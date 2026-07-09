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
import sys
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

from .config import config
from .exchange_rate import exchange_rate_manager


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
    """DeepSeek 余额查询。"""

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
        except Exception as exc:
            print(f"[Balance] DeepSeek check failed: {exc}", file=sys.stderr)
        return None


class ZhipuBalanceChecker(BalanceChecker):
    """智谱 GLM 余额查询。"""

    def check(self, model: Dict[str, Any]) -> Optional[float]:
        api_key = model.get("api_key", "")
        if not api_key:
            return None
        try:
            # 智谱通过查询账户信息获取余额
            resp = httpx.get(
                "https://open.bigmodel.cn/api/paas/v4/account/balance",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                return round(float(data.get("total_balance", 0)), 6)
        except Exception as exc:
            print(f"[Balance] Zhipu check failed: {exc}", file=sys.stderr)
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
    def get_available_models(self) -> List[Dict[str, Any]]:
        """返回所有可用模型（排除禁用模型），附带实时余额和统一货币价格。"""
        models = config.get_models()
        target_currency = config.currency
        # 过滤禁用的模型
        models = [m for m in models if m.get("enabled", True)]
        for m in models:
            # 手动设定余额优先；balance_frozen=True 时不再动态更新余额
            if m.get("balance_manual") is not None:
                m["balance"] = m["balance_manual"]
            elif m.get("balance_frozen"):
                # 余额冻结：使用上次缓存的余额，不再查询 API
                m["balance"] = self._get_cached_balance(m)
            else:
                m["balance"] = self._get_balance(m)
            # 价格货币转换：将模型原始价格转为用户选择的货币
            model_currency = m.get("price_currency", "USD")
            if model_currency != target_currency:
                m["price_input_display"] = round(
                    exchange_rate_manager.convert(
                        m.get("price_input", 0), model_currency, target_currency
                    ), 8
                )
                m["price_output_display"] = round(
                    exchange_rate_manager.convert(
                        m.get("price_output", 0), model_currency, target_currency
                    ), 8
                )
                m["price_currency_display"] = target_currency
            else:
                m["price_input_display"] = m.get("price_input", 0)
                m["price_output_display"] = m.get("price_output", 0)
                m["price_currency_display"] = target_currency
        return models

    # ------------------------------------------------------------------ #
    # 余额查询（带缓存）
    # ------------------------------------------------------------------ #
    def _get_cached_balance(self, model: Dict[str, Any]) -> Optional[float]:
        """获取缓存的余额，不触发 API 查询。用于 balance_frozen 模式。"""
        name = model["name"]
        with self._lock:
            cached = self._balance_cache.get(name)
            if cached:
                return cached[1]
        # 没有缓存时回退到查询
        return self._get_balance(model)

    def _get_balance(self, model: Dict[str, Any]) -> Optional[float]:
        name = model["name"]
        with self._lock:
            cached = self._balance_cache.get(name)
            if cached and (time.time() - cached[0] < config.balance_cache_ttl_seconds):
                return cached[1]

        # 调用对应策略
        api_type = model.get("api_type", "local")
        checker = BalanceCheckerFactory.get_checker(api_type)
        balance = None
        try:
            balance = checker.check(model)
        except Exception as exc:
            print(f"[Pricing] balance check error for {name}: {exc}", file=sys.stderr)

        # 本地估算兜底
        if balance is None:
            from .database import db

            metrics = db.get_metrics(name)
            balance = metrics["last_balance"] if metrics else None

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

    # ------------------------------------------------------------------ #
    # 本地扣账
    # ------------------------------------------------------------------ #
    def deduct(self, model_name: str, cost: float) -> None:
        """调用结束后本地扣账。"""
        from .database import db

        with self._lock:
            cached = self._balance_cache.get(model_name)
            if cached and cached[1] is not None:
                new_balance = max(0.0, cached[1] - cost)
                self._balance_cache[model_name] = (time.time(), new_balance)
                db.update_balance(model_name, new_balance)

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

    def _sync_prices(self) -> None:
        """从 litellm 拉取最新单价并合并到配置。"""
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
            models = config.get("models", [])

            for model in models:
                litellm_name = model.get("litellm_name", "")
                if not litellm_name:
                    continue
                # 跳过冻结价格的模型
                if model.get("price_frozen"):
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
                        # litellm 价格统一为 USD
                        model["price_currency"] = "USD"
                        updated += 1

            if updated > 0:
                config.save()
                print(f"[Pricing] updated prices for {updated} models", file=sys.stderr)
            else:
                print("[Pricing] no price updates found", file=sys.stderr)

        except Exception as exc:
            print(f"[Pricing] sync error: {exc}", file=sys.stderr)

    def sync_prices_now(self) -> int:
        """手动触发价格同步，返回更新的模型数量。"""
        try:
            resp = httpx.get(self.LITELLM_PRICES_URL, timeout=30.0)
            if resp.status_code != 200:
                return -1

            remote_prices = resp.json()
            updated = 0
            models = config.get("models", [])

            for model in models:
                litellm_name = model.get("litellm_name", "")
                if not litellm_name:
                    continue
                # 跳过冻结价格的模型
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
                        # litellm 价格统一为 USD
                        model["price_currency"] = "USD"
                        updated += 1

            if updated > 0:
                config.save()
            return updated

        except Exception as exc:
            print(f"[Pricing] manual sync error: {exc}", file=sys.stderr)
            return -1

    def shutdown(self) -> None:
        self._stop_event.set()


# 全局单例
pricing_manager = PricingManager()
