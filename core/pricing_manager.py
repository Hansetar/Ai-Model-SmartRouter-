"""
core/pricing_manager.py
=======================
动态价格与余额管理。

- 价格同步：异步定时任务从 litellm model_prices.json 拉取主流模型最新单价。
- 余额适配器：策略模式适配不同大厂 API。结果在内存缓存。
- 无接口的模型，通过本地扣账估算。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

from .config import config
from .exchange_rate import exchange_rate_manager
from .script_engine import execute_balance_script, execute_price_script


# ---------------------------------------------------------------------- #
# 余额检查策略
# ---------------------------------------------------------------------- #
class BalanceChecker:
    """余额检查器基类。"""

    def check(self, model: Dict[str, Any]) -> Optional[float]:
        raise NotImplementedError


class OpenAIBalanceChecker(BalanceChecker):
    """OpenAI 余额查询（通过 billing API）。"""

    def check(self, model: Dict[str, Any]) -> Optional[float]:
        api_key = model.get("api_key", "")
        if not api_key:
            return None
        try:
            resp = httpx.get(
                "https://api.openai.com/v1/organization/costs?start_time=2024-01-01",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                # OpenAI billing API 返回的是费用而非余额，这里返回 None
                # 实际余额需要通过 dashboard 获取
                return None
        except Exception as exc:
            print(f"[Balance] OpenAI check failed: {exc}", file=sys.stderr)
        return None


class DeepSeekBalanceChecker(BalanceChecker):
    """DeepSeek 余额查询。

    调用 https://api.deepseek.com/user/balance 获取账户余额。
    API 返回格式: {"is_available": bool, "balance_infos": [{"currency": "CNY", "total_balance": "9.01", ...}]}
    """

    def check(self, model: Dict[str, Any]) -> Optional[float]:
        api_key = model.get("api_key", "")
        if not api_key:
            return None
        try:
            resp = httpx.get(
                "https://api.deepseek.com/user/balance",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                balance_infos = data.get("balance_infos", [])
                if balance_infos:
                    total = sum(
                        float(b.get("total_balance", 0)) for b in balance_infos
                    )
                    return round(total, 6)
            else:
                print(f"[Balance] DeepSeek API returned status {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        except Exception as exc:
            print(f"[Balance] DeepSeek check failed: {exc}", file=sys.stderr)
        return None


class ZhipuBalanceChecker(BalanceChecker):
    """智谱 GLM 余额查询。

    智谱 API Key 格式为 {id}.{secret}，需要生成 JWT token 进行认证。
    注意：智谱目前未提供余额查询 API，此检查器始终返回 None，
    建议通过手动设定余额（balance_manual）或自定义余额脚本管理。
    """

    def check(self, model: Dict[str, Any]) -> Optional[float]:
        # 智谱目前未提供余额查询 API，直接返回 None
        # 用户可通过以下方式管理余额：
        # 1. 在提供方/模型中设置 balance_manual（手动余额）
        # 2. 编写自定义余额脚本（如通过爬虫等方式获取）
        return None

    @staticmethod
    def generate_token(api_key: str) -> Optional[str]:
        """将智谱 API Key ({id}.{secret}) 转换为 JWT token。

        智谱 JWT 规范：
        - 时间戳使用毫秒级（time.time() * 1000）
        - header 包含 sign_type: SIGN
        - 可用于调用智谱 chat/completions 等接口
        """
        try:
            import hmac
            import base64
            import json as _json
            import time as _time

            parts = api_key.split(".")
            if len(parts) != 2:
                return None
            api_key_id, api_key_secret = parts[0], parts[1]

            # 智谱使用毫秒级时间戳
            now_ms = int(round(_time.time() * 1000))
            payload = {
                "api_key": api_key_id,
                "exp": now_ms + 3600 * 1000,
                "timestamp": now_ms,
            }

            def b64url_encode(data: bytes) -> str:
                return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")

            header = {"alg": "HS256", "sign_type": "SIGN"}
            header_b64 = b64url_encode(_json.dumps(header, separators=(",", ":")).encode("utf-8"))
            payload_b64 = b64url_encode(_json.dumps(payload, separators=(",", ":")).encode("utf-8"))
            signing_input = f"{header_b64}.{payload_b64}"

            sig = hmac.new(
                api_key_secret.encode("utf-8"),
                signing_input.encode("utf-8"),
                "sha256",
            )
            signature_b64 = b64url_encode(sig.digest())

            return f"{header_b64}.{payload_b64}.{signature_b64}"
        except Exception as exc:
            print(f"[Balance] Zhipu JWT generation failed: {exc}", file=sys.stderr)
            return None


class SiliconFlowBalanceChecker(BalanceChecker):
    """SiliconFlow 余额查询。"""

    def check(self, model: Dict[str, Any]) -> Optional[float]:
        api_key = model.get("api_key", "")
        if not api_key:
            return None
        try:
            resp = httpx.get(
                "https://api.siliconflow.cn/v1/user/info",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                return round(float(data.get("totalBalance", 0)), 6)
        except Exception as exc:
            print(f"[Balance] SiliconFlow check failed: {exc}", file=sys.stderr)
        return None


class AliyunBalanceChecker(BalanceChecker):
    """阿里云 DashScope 余额查询。"""

    def check(self, model: Dict[str, Any]) -> Optional[float]:
        api_key = model.get("api_key", "")
        if not api_key:
            return None
        try:
            resp = httpx.get(
                "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10.0,
            )
            # DashScope 没有直接的余额查询 API，返回 None
            return None
        except Exception as exc:
            print(f"[Balance] Aliyun check failed: {exc}", file=sys.stderr)
        return None


class LocalEstimateChecker(BalanceChecker):
    """本地扣账估算（无接口的模型）。"""

    def check(self, model: Dict[str, Any]) -> Optional[float]:
        from .database import db

        metrics = db.get_metrics(model["name"])
        if metrics and metrics.get("last_balance") is not None:
            return metrics["last_balance"]
        return None


class BalanceCheckerFactory:
    """余额检查器工厂（策略模式）。

    支持的 api_type:
    - openai: OpenAI 及兼容接口（gpt-4o-mini, deepseek-chat 等）
    - deepseek: DeepSeek 专用余额查询
    - zhipu: 智谱 GLM
    - siliconflow: SiliconFlow
    - aliyun: 阿里云 DashScope
    - local: 本地估算
    """

    _checkers: Dict[str, BalanceChecker] = {
        "openai": OpenAIBalanceChecker(),
        "deepseek": DeepSeekBalanceChecker(),
        "zhipu": ZhipuBalanceChecker(),
        "siliconflow": SiliconFlowBalanceChecker(),
        "aliyun": AliyunBalanceChecker(),
        "local": LocalEstimateChecker(),
    }

    @staticmethod
    def get_checker(api_type: str) -> BalanceChecker:
        return BalanceCheckerFactory._checkers.get(
            api_type, LocalEstimateChecker()
        )

    @staticmethod
    def register_checker(api_type: str, checker: BalanceChecker) -> None:
        """注册自定义余额检查器。"""
        BalanceCheckerFactory._checkers[api_type] = checker


# ---------------------------------------------------------------------- #
# 价格余额管理器
# ---------------------------------------------------------------------- #
class PricingManager:
    """价格与余额管理器。"""

    # litellm 价格数据 URL
    LITELLM_PRICES_URL = (
        "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
    )

    def __init__(self) -> None:
        self._balance_cache: Dict[str, tuple] = {}  # name -> (timestamp, balance)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        # 启动价格同步后台线程
        self._sync_thread = threading.Thread(
            target=self._bg_price_sync, daemon=True, name="price-sync"
        )
        self._sync_thread.start()

    # ------------------------------------------------------------------ #
    # 模型可用性
    # ------------------------------------------------------------------ #
    @staticmethod
    def is_within_active_hours(active_hours) -> bool:
        """判断当前时间是否在模型的生效时间段内。

        :param active_hours: 生效时间段，支持以下格式：
                             - None 或空字符串/空列表：全天生效
                             - 单个字符串 "HH:MM-HH:MM"：一个时间段
                             - 列表 ["HH:MM-HH:MM", ...]：多个时间段，任一匹配即生效
                             跨午夜自动识别，如 "21:00-09:00" 表示晚21点到次日上午9点。
        :return: True 表示当前在生效时间内，False 表示不在。
        """
        if not active_hours:
            return True  # 未设置则全天生效

        # 统一转为列表处理
        if isinstance(active_hours, str):
            periods = [active_hours]
        elif isinstance(active_hours, (list, tuple)):
            periods = active_hours
        else:
            return True

        if not periods:
            return True

        # 优先使用 TZ 环境变量指定的时区，回退到系统本地时间
        tz_name = os.environ.get("TZ", "")
        if tz_name:
            try:
                from datetime import datetime, timezone, timedelta
                tz_offset_map = {
                    "Asia/Shanghai": timedelta(hours=8),
                    "Asia/Tokyo": timedelta(hours=9),
                    "America/New_York": timedelta(hours=-5),
                    "America/Los_Angeles": timedelta(hours=-8),
                    "Europe/London": timedelta(hours=0),
                    "Europe/Berlin": timedelta(hours=1),
                    "UTC": timedelta(hours=0),
                }
                tz_offset = tz_offset_map.get(tz_name)
                if tz_offset is None:
                    # 尝试从 TZ 环境变量解析（如 "Asia/Shanghai" 不在映射中时回退）
                    tz_offset = timedelta(hours=8)  # 默认东八区
                now_dt = datetime.now(timezone(tz_offset))
                current_minutes = now_dt.hour * 60 + now_dt.minute
            except Exception:
                now = time.localtime()
                current_minutes = now.tm_hour * 60 + now.tm_min
        else:
            now = time.localtime()
            current_minutes = now.tm_hour * 60 + now.tm_min

        for period in periods:
            if not period or not isinstance(period, str):
                continue
            try:
                parts = period.split("-")
                if len(parts) != 2:
                    continue
                start_h, start_m = map(int, parts[0].strip().split(":"))
                end_h, end_m = map(int, parts[1].strip().split(":"))
                start_minutes = start_h * 60 + start_m
                end_minutes = end_h * 60 + end_m
                if start_minutes <= end_minutes:
                    # 同日时间段，如 "09:00-18:00"
                    if start_minutes <= current_minutes < end_minutes:
                        return True
                else:
                    # 跨午夜时间段，如 "21:00-09:00"（晚21点到次日上午9点）
                    if current_minutes >= start_minutes or current_minutes < end_minutes:
                        return True
            except Exception:
                continue

        return False  # 所有时间段都不匹配

    def get_available_models(self) -> List[Dict[str, Any]]:
        """返回所有可用模型（排除禁用模型和不在生效时间段的模型），附带实时余额和统一货币价格。"""
        models = config.get_models()
        target_currency = config.currency
        # 过滤禁用的模型
        models = [m for m in models if m.get("enabled", True)]
        # 过滤不在生效时间段的模型
        models = [m for m in models if self.is_within_active_hours(m.get("active_hours"))]
        for m in models:
            # 手动设定余额优先；balance_frozen=True 时不再动态更新余额
            if m.get("balance_manual") is not None:
                m["balance"] = m["balance_manual"]
            elif m.get("balance_frozen"):
                # 余额冻结：使用上次缓存的余额，不再查询 API
                m["balance"] = self._get_cached_balance(m)
            else:
                m["balance"] = self._get_balance(m)

            # 价格获取：优先使用脚本返回的价格，其次使用模型配置中的价格
            script_price = self.get_model_price_from_script(m)
            if script_price is not None:
                price_input = script_price.get("price_input", 0)
                price_output = script_price.get("price_output", 0)
                model_currency = script_price.get("price_currency", target_currency)
                model_unit = script_price.get("price_unit", "1M")
                # 将脚本价格写回原始字段，确保路由器读到正确值
                m["price_input"] = price_input
                m["price_output"] = price_output
                m["price_currency"] = model_currency
                m["price_unit"] = model_unit
            else:
                price_input = m.get("price_input", 0)
                price_output = m.get("price_output", 0)
                model_currency = m.get("price_currency", target_currency)
                model_unit = m.get("price_unit", "1M")

            # 价格货币转换：将原始价格转为用户选择的货币
            if model_currency != target_currency:
                m["price_input_display"] = round(
                    exchange_rate_manager.convert(price_input, model_currency, target_currency), 8
                )
                m["price_output_display"] = round(
                    exchange_rate_manager.convert(price_output, model_currency, target_currency), 8
                )
                m["price_currency_display"] = target_currency
            else:
                m["price_input_display"] = price_input
                m["price_output_display"] = price_output
                m["price_currency_display"] = target_currency
            m["price_unit_display"] = model_unit
        return models

    # ------------------------------------------------------------------ #
    # 余额查询（带缓存）
    # ------------------------------------------------------------------ #
    def _get_cached_balance(self, model: Dict[str, Any]) -> Optional[float]:
        """获取缓存的余额，不触发 API 查询。

        优先从内存缓存获取，缓存未命中时从数据库获取 last_balance 作为兜底。
        用于管理面板等需要快速响应的场景，避免同步网络请求阻塞。
        """
        name = model.get("name", "")
        if not name:
            return None
        with self._lock:
            cached = self._balance_cache.get(name)
            if cached:
                return cached[1]
        # 缓存未命中时，从数据库获取 last_balance 作为兜底（冷启动恢复）
        try:
            metrics = db.get_metrics(name)
            if metrics and metrics.get("last_balance") is not None:
                balance = metrics["last_balance"]
                with self._lock:
                    self._balance_cache[name] = (time.time(), balance)
                return balance
        except Exception:
            pass
        return None

    def _get_balance(self, model: Dict[str, Any]) -> Optional[float]:
        name = model["name"]
        with self._lock:
            cached = self._balance_cache.get(name)
            if cached and (time.time() - cached[0] < config.balance_cache_ttl_seconds):
                return cached[1]

        balance = None
        balance_currency = None  # 脚本返回的货币单位

        # 优先级1: 模型级余额脚本
        model_script = model.get("balance_script", "")
        if model_script and model_script.strip():
            script_result = execute_balance_script(
                script=model_script,
                api_key=model.get("api_key", ""),
                base_url=model.get("base_url", ""),
                model_name=name,
            )
            if script_result is not None:
                balance = script_result["balance"]
                balance_currency = script_result.get("balance_currency")

        # 优先级2: Provider 级余额脚本
        if balance is None:
            provider = model.get("_provider")
            if provider and provider.get("balance_script", "").strip():
                # 使用模型自身的 api_key（可能覆盖了 Provider 的），或 Provider 的
                api_key = model.get("api_key", "") or provider.get("api_key", "")
                base_url = model.get("base_url", "") or provider.get("base_url", "")
                script_result = execute_balance_script(
                    script=provider["balance_script"],
                    api_key=api_key,
                    base_url=base_url,
                    model_name=name,
                )
                if script_result is not None:
                    balance = script_result["balance"]
                    balance_currency = script_result.get("balance_currency")

        # 优先级3: Provider 级手动余额（同一 Provider 共享）
        if balance is None:
            provider = model.get("_provider")
            if provider and provider.get("balance_manual") is not None:
                balance = provider["balance_manual"]
                # 手动余额使用 Provider 的 balance_currency
                balance_currency = provider.get("balance_currency")

        # 优先级4: 内置策略余额查询
        if balance is None:
            # 优先使用 provider_type 选择余额检查器（如 deepseek），
            # 回退到 api_type（如 openai），避免 DeepSeek 供应商因 api_type=openai
            # 而错误使用 OpenAIBalanceChecker
            provider = model.get("_provider")
            checker_type = "local"
            if provider and provider.get("provider_type"):
                checker_type = provider["provider_type"]
            elif model.get("api_type"):
                checker_type = model["api_type"]
            checker = BalanceCheckerFactory.get_checker(checker_type)
            try:
                balance = checker.check(model)
                # 内置策略的余额货币推断
                if balance is not None:
                    balance_currency = self._infer_balance_currency(model, checker_type)
            except Exception as exc:
                print(f"[Pricing] balance check error for {name}: {exc}", file=sys.stderr)

        # 本地估算兜底
        if balance is None:
            from .database import db

            metrics = db.get_metrics(name)
            balance = metrics["last_balance"] if metrics else None

        # 余额货币转换：如果脚本/内置策略返回的货币与显示货币不同，进行换算
        if balance is not None and balance_currency is not None:
            target_currency = config.currency
            if balance_currency != target_currency:
                balance = round(
                    exchange_rate_manager.convert(balance, balance_currency, target_currency), 6
                )

        with self._lock:
            self._balance_cache[name] = (time.time(), balance)
        return balance

    def refresh_balance(self, model_name: str) -> Optional[float]:
        """强制刷新某模型余额。"""
        model = config.get_model(model_name)
        if not model:
            return None
        with self._lock:
            self._balance_cache.pop(model_name, None)
        return self._get_balance(model)

    @staticmethod
    def _infer_balance_currency(model: Dict[str, Any], api_type: str) -> str:
        """推断内置策略返回的余额货币单位。

        根据提供方类型推断余额的货币单位：
        - deepseek: CNY
        - siliconflow: CNY
        - zhipu: CNY
        - openai: USD
        - 其他: 使用模型的 price_currency 或当前显示货币
        """
        _CURRENCY_MAP = {
            "deepseek": "CNY",
            "siliconflow": "CNY",
            "zhipu": "CNY",
            "aliyun": "CNY",
            "openai": "USD",
        }
        return _CURRENCY_MAP.get(api_type) or model.get("price_currency") or config.currency

    @staticmethod
    def _infer_price_unit(model: Dict[str, Any], api_type: str) -> str:
        """推断单价脚本未指定 price_unit 时的默认单位。

        根据提供方类型推断单价的计量单位：
        - openai: 按实际 API 返回，通常为 1M
        - 其他: 使用模型的 price_unit 或默认 1M
        """
        return model.get("price_unit") or "1M"

    @staticmethod
    def _infer_price_currency(model: Dict[str, Any], api_type: str) -> str:
        """推断单价脚本未指定 price_currency 时的默认货币。

        根据提供方类型推断单价的货币单位：
        - deepseek: CNY
        - siliconflow: CNY
        - zhipu: CNY
        - openai: USD
        - 其他: 使用模型的 price_currency 或当前显示货币
        """
        _CURRENCY_MAP = {
            "deepseek": "CNY",
            "siliconflow": "CNY",
            "zhipu": "CNY",
            "aliyun": "CNY",
            "openai": "USD",
        }
        return _CURRENCY_MAP.get(api_type) or model.get("price_currency") or config.currency

    # ------------------------------------------------------------------ #
    # 本地扣账
    # ------------------------------------------------------------------ #
    def deduct(self, model_name: str, cost: float) -> None:
        """调用结束后本地扣账。

        对于手动设定余额的模型，同步更新 balance_manual 值，
        确保消耗的 token 费用从手动余额中扣除。
        """
        from .database import db

        with self._lock:
            cached = self._balance_cache.get(model_name)
            if cached and cached[1] is not None:
                new_balance = max(0.0, cached[1] - cost)
                self._balance_cache[model_name] = (time.time(), new_balance)
                db.update_balance(model_name, new_balance)

        # 手动设定余额模式：同步更新 config 中的 balance_manual
        models = config.get("models", [])
        for m in models:
            if m.get("name") == model_name and m.get("balance_manual") is not None:
                old_balance = float(m["balance_manual"])
                new_manual = max(0.0, old_balance - cost)
                m["balance_manual"] = new_manual
                config.set("models", models)
                # 更新缓存以保持一致
                with self._lock:
                    self._balance_cache[model_name] = (time.time(), new_manual)
                try:
                    config.save()
                except Exception:
                    pass
                break

    # ------------------------------------------------------------------ #
    # 价格同步（后台线程）
    # ------------------------------------------------------------------ #
    def _bg_price_sync(self) -> None:
        """定时同步价格。"""
        while not self._stop_event.is_set():
            try:
                self._sync_prices()
            except Exception as exc:
                print(f"[Pricing] sync failed: {exc}", file=sys.stderr)
            # 等待下一次同步
            self._stop_event.wait(config.price_sync_interval_hours * 3600)

    def _sync_prices(self, provider_name: str = None, model_name: str = None) -> None:
        """从 litellm 拉取最新单价并合并到配置。

        有提供方/模型级单价脚本的模型不会被 litellm 覆盖。

        :param provider_name: 仅更新指定提供方的模型，None 表示全部
        :param model_name: 仅更新指定模型，None 表示全部
        """
        try:
            resp = httpx.get(self.LITELLM_PRICES_URL, timeout=30.0)
            if resp.status_code != 200:
                print(
                    f"[Pricing] fetch prices failed: HTTP {resp.status_code}",
                    file=sys.stderr,
                )
                return

            remote_prices = resp.json()
            updated = 0
            # 使用 get_models() 获取带 _provider 的数据，用于检查脚本
            enriched_models = config.get_models()
            enriched_map = {m.get("name"): m for m in enriched_models}
            models = config.get("models", [])

            for model in models:
                # 过滤：仅更新指定提供方
                if provider_name and model.get("provider") != provider_name:
                    continue
                # 过滤：仅更新指定模型
                if model_name and model.get("name") != model_name:
                    continue

                litellm_name = model.get("litellm_name", "")
                if not litellm_name:
                    continue
                # 跳过冻结价格的模型
                if model.get("price_frozen"):
                    continue
                # 跳过有单价脚本的模型（脚本优先级高于 litellm）
                enriched = enriched_map.get(model.get("name"))
                if enriched and self.get_model_price_from_script(enriched) is not None:
                    continue

                # 查找 litellm 中的价格数据
                price_data = remote_prices.get(litellm_name)
                if not price_data:
                    # 尝试不带前缀
                    parts = litellm_name.split("/", 1)
                    if len(parts) == 2:
                        price_data = remote_prices.get(parts[1])

                if price_data:
                    new_input = float(price_data.get("input_cost_per_token", 0)) * 1_000_000
                    new_output = float(price_data.get("output_cost_per_token", 0)) * 1_000_000
                    if new_input > 0 or new_output > 0:
                        model["price_input"] = round(new_input, 8)
                        model["price_output"] = round(new_output, 8)
                        # litellm 价格统一为 USD，单位为 1M
                        model["price_currency"] = "USD"
                        model["price_unit"] = "1M"
                        updated += 1

            if updated > 0:
                config.save()
                print(f"[Pricing] updated prices for {updated} models", file=sys.stderr)
            else:
                print("[Pricing] no price updates found", file=sys.stderr)

        except Exception as exc:
            print(f"[Pricing] sync error: {exc}", file=sys.stderr)

    def sync_prices_now(self, provider_name: str = None, model_name: str = None) -> int:
        """手动触发价格同步，返回更新的模型数量。

        同步逻辑：
        1. 先执行提供方/模型级单价脚本，用脚本返回的价格更新模型配置
        2. 再从 litellm 同步（仅对无脚本且未冻结的模型）

        :param provider_name: 仅更新指定提供方的模型，None 表示全部
        :param model_name: 仅更新指定模型，None 表示全部
        """
        # 使用 get_models() 获取带 _provider 的完整模型数据
        enriched_models = config.get_models()
        # 原始模型数据用于写回
        raw_models = config.get("models", [])
        providers = {p["name"]: p for p in config.get_providers()}
        updated = 0

        # 构建 name -> raw_model 映射
        raw_model_map = {m.get("name"): m for m in raw_models}

        # 第一步：执行提供方/模型级单价脚本
        for model in enriched_models:
            if provider_name and model.get("provider") != provider_name:
                continue
            if model_name and model.get("name") != model_name:
                continue

            script_result = self.get_model_price_from_script(model)
            if script_result is not None:
                model_name_key = model.get("name")
                raw_model = raw_model_map.get(model_name_key)
                if not raw_model:
                    continue

                new_input = script_result.get("price_input")
                new_output = script_result.get("price_output")
                new_currency = script_result.get("price_currency")
                new_unit = script_result.get("price_unit")

                changed = False
                if new_input is not None and raw_model.get("price_input") != new_input:
                    raw_model["price_input"] = new_input
                    changed = True
                if new_output is not None and raw_model.get("price_output") != new_output:
                    raw_model["price_output"] = new_output
                    changed = True
                if new_currency and raw_model.get("price_currency") != new_currency:
                    raw_model["price_currency"] = new_currency
                    changed = True
                if new_unit and raw_model.get("price_unit") != new_unit:
                    raw_model["price_unit"] = new_unit
                    changed = True

                if changed:
                    updated += 1

        # 第二步：从 litellm 同步（仅对无脚本且未冻结的模型）
        try:
            resp = httpx.get(self.LITELLM_PRICES_URL, timeout=30.0)
            if resp.status_code == 200:
                remote_prices = resp.json()

                for model in raw_models:
                    if provider_name and model.get("provider") != provider_name:
                        continue
                    if model_name and model.get("name") != model_name:
                        continue

                    # 有脚本的模型已由脚本更新，跳过 litellm
                    enriched = next((m for m in enriched_models if m.get("name") == model.get("name")), None)
                    if enriched and self.get_model_price_from_script(enriched) is not None:
                        continue

                    litellm_name = model.get("litellm_name", "")
                    if not litellm_name:
                        continue
                    if model.get("price_frozen"):
                        continue

                    price_data = remote_prices.get(litellm_name)
                    if not price_data:
                        parts = litellm_name.split("/", 1)
                        if len(parts) == 2:
                            price_data = remote_prices.get(parts[1])

                    if price_data:
                        new_input = float(price_data.get("input_cost_per_token", 0)) * 1_000_000
                        new_output = float(price_data.get("output_cost_per_token", 0)) * 1_000_000
                        if new_input > 0 or new_output > 0:
                            model["price_input"] = round(new_input, 8)
                            model["price_output"] = round(new_output, 8)
                            model["price_currency"] = "USD"
                            model["price_unit"] = "1M"
                            updated += 1

        except Exception as exc:
            print(f"[Pricing] litellm sync error: {exc}", file=sys.stderr)

        if updated > 0:
            config.set("models", raw_models)
            config.save()
        return updated

    def get_model_price_from_script(self, model: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """通过脚本获取模型单价。

        优先级：模型级脚本 > Provider 级脚本 > None

        :return: {"price_input": float, "price_output": float,
                  "price_currency": str, "price_unit": str} 或 None
        """
        api_type = model.get("api_type", "local")
        result = None

        # 优先级1: 模型级单价脚本
        model_script = model.get("price_script", "")
        if model_script and model_script.strip():
            result = execute_price_script(
                script=model_script,
                api_key=model.get("api_key", ""),
                base_url=model.get("base_url", ""),
                model_name=model.get("name", ""),
            )

        # 优先级2: Provider 级单价脚本
        if result is None:
            provider = model.get("_provider")
            if provider and provider.get("price_script", "").strip():
                api_key = model.get("api_key", "") or provider.get("api_key", "")
                base_url = model.get("base_url", "") or provider.get("base_url", "")
                result = execute_price_script(
                    script=provider["price_script"],
                    api_key=api_key,
                    base_url=base_url,
                    model_name=model.get("name", ""),
                )

        if result is None:
            return None

        # 推断未指定的字段
        if result.get("price_currency") is None:
            result["price_currency"] = self._infer_price_currency(model, api_type)
        if result.get("price_unit") is None:
            result["price_unit"] = self._infer_price_unit(model, api_type)

        return result

    def shutdown(self) -> None:
        self._stop_event.set()


# 全局单例
pricing_manager = PricingManager()
