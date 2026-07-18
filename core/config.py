"""
core/config.py
===============
核心配置文件解析模块。

负责加载 config.yaml，并提供全局单例访问。支持环境变量覆盖敏感字段（API Key）。
本模块不依赖任何网络框架，可被双模式适配层共享复用。
"""

from __future__ import annotations

import copy
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """配置异常基类。"""


# ---------------------------------------------------------------------- #
# 参数量(B) -> 能力等级 换算表（1-100 量化精度）
# ---------------------------------------------------------------------- #
# 基于业界主流模型的参数量与能力对应关系：
#   <1B:  10 (极小模型，仅简单对话)
#   1-7B: 25 (小模型，日常对话/简单任务)
#   7-14B: 50 (中小模型，通用任务)
#   14-70B: 75 (中大模型，复杂推理/代码)
#   >70B:  95 (超大模型，高难度任务)
def params_b_to_capability(params_b: float) -> int:
    """将模型参数量(B)换算为能力等级(1-100)。

    :param params_b: 模型参数量，单位十亿(B)
    :return: 能力等级 1-100
    """
    if params_b <= 0:
        return 10
    if params_b < 1:
        return 10
    if params_b < 7:
        return 25
    if params_b < 14:
        return 50
    if params_b < 70:
        return 75
    return 95


# 所有支持的请求类型
ALL_TASK_TYPES = ["coding", "math", "reasoning", "creative", "chat", "translation", "analysis"]


class Config:
    """全局配置单例。

    使用方式::

        from core.config import config
        models = config.get("models", [])
    """

    _instance: Optional["Config"] = None

    def __init__(self, config_path: Optional[str] = None) -> None:
        self._config_path: Path = (
            Path(config_path)
            if config_path
            else Path(__file__).resolve().parent.parent / "config.yaml"
        )
        self._data: Dict[str, Any] = {}
        self.reload()

    # ------------------------------------------------------------------ #
    # 单例控制
    # ------------------------------------------------------------------ #
    @classmethod
    def get_instance(cls, config_path: Optional[str] = None) -> "Config":
        if cls._instance is None:
            cls._instance = cls(config_path)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    # ------------------------------------------------------------------ #
    # 加载与持久化
    # ------------------------------------------------------------------ #
    # 备份目录
    _BACKUP_DIR_NAME = "config_backups"
    _MAX_BACKUPS = 10

    def _backup_dir(self) -> Path:
        return self._config_path.parent / self._BACKUP_DIR_NAME

    def _ensure_backup_dir(self) -> Path:
        d = self._backup_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def backup(self, reason: str = "manual") -> Optional[Path]:
        """创建配置文件备份。

        :param reason: 备份原因标记，用于文件名
        :return: 备份文件路径，失败返回 None
        """
        if not self._config_path.exists():
            return None
        try:
            backup_dir = self._ensure_backup_dir()
            ts = time.strftime("%Y%m%d_%H%M%S")
            backup_name = f"config_{ts}_{reason}.yaml"
            backup_path = backup_dir / backup_name
            shutil.copy2(self._config_path, backup_path)
            # 清理旧备份，只保留最近 _MAX_BACKUPS 个
            backups = sorted(backup_dir.glob("config_*.yaml"), reverse=True)
            for old in backups[self._MAX_BACKUPS:]:
                old.unlink(missing_ok=True)
            logger.info("配置备份已创建: %s (原因: %s)", backup_path.name, reason)
            return backup_path
        except Exception as e:
            logger.warning("配置备份失败: %s", e)
            return None

    def _find_latest_backup(self) -> Optional[Path]:
        """查找最新的有效备份文件。"""
        backup_dir = self._backup_dir()
        if not backup_dir.exists():
            return None
        backups = sorted(backup_dir.glob("config_*.yaml"), reverse=True)
        for bp in backups:
            try:
                with open(bp, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data and isinstance(data, dict):
                    return bp
            except Exception:
                continue
        return None

    def _repair_config(self, raw_data: Any) -> Dict[str, Any]:
        """尝试修复配置数据中的常见错误。

        :param raw_data: yaml.safe_load 的原始结果
        :return: 修复后的配置字典
        """
        # 情况1: 完全为空 -> 返回最小有效配置
        if raw_data is None:
            logger.warning("配置文件为空，使用最小默认配置")
            return {"models": [], "providers": [], "admin_password": "admin"}

        # 情况2: 不是字典 -> 尝试包装或回退
        if not isinstance(raw_data, dict):
            logger.warning("配置文件格式异常（非字典），使用最小默认配置")
            return {"models": [], "providers": [], "admin_password": "admin"}

        data = dict(raw_data)

        # 情况3: models 不是列表 -> 修复
        if "models" not in data:
            data["models"] = []
        elif not isinstance(data["models"], list):
            logger.warning("models 字段不是列表，已重置为空列表")
            data["models"] = []

        # 情况4: providers 不是列表 -> 修复
        if "providers" not in data:
            data["providers"] = []
        elif not isinstance(data["providers"], list):
            logger.warning("providers 字段不是列表，已重置为空列表")
            data["providers"] = []

        # 情况5: 修复模型配置中缺少 name 字段的条目
        valid_models = []
        for i, m in enumerate(data["models"]):
            if not isinstance(m, dict):
                logger.warning("models[%d] 不是字典，已跳过", i)
                continue
            if "name" not in m or not m["name"]:
                logger.warning("models[%d] 缺少 name 字段，已跳过", i)
                continue
            valid_models.append(m)
        data["models"] = valid_models

        # 情况6: 修复 provider 配置中缺少 name 字段的条目
        valid_providers = []
        for i, p in enumerate(data["providers"]):
            if not isinstance(p, dict):
                logger.warning("providers[%d] 不是字典，已跳过", i)
                continue
            if "name" not in p or not p["name"]:
                logger.warning("providers[%d] 缺少 name 字段，已跳过", i)
                continue
            valid_providers.append(p)
        data["providers"] = valid_providers

        # 情况7: 确保关键字段存在
        if "admin_password" not in data:
            data["admin_password"] = "admin"
        if "currency" not in data:
            data["currency"] = "CNY"

        return data

    def reload(self) -> None:
        """重新从磁盘读取 config.yaml，带错误自动修复和备份恢复。

        加载策略：
        1. 尝试读取并解析 config.yaml
        2. 如果 YAML 语法错误 -> 尝试从最新备份恢复
        3. 如果解析成功但数据异常 -> 自动修复
        4. 如果文件不存在 -> 尝试从备份恢复，否则创建最小配置
        """
        # 文件不存在时的处理
        if not self._config_path.exists():
            logger.error("配置文件不存在: %s", self._config_path)
            backup = self._find_latest_backup()
            if backup:
                logger.info("从备份恢复配置: %s", backup.name)
                shutil.copy2(backup, self._config_path)
                # 读取恢复后的文件
                with open(self._config_path, "r", encoding="utf-8") as f:
                    raw_data = yaml.safe_load(f)
                self._data = self._repair_config(raw_data)
            else:
                logger.warning("无可用备份，创建最小默认配置")
                self._data = {"models": [], "providers": [], "admin_password": "admin", "currency": "CNY"}
                self.save()
            return

        # 尝试读取和解析
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                raw_data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            logger.error("配置文件 YAML 语法错误: %s", e)
            # 先备份损坏的文件
            try:
                corrupt_backup = self._config_path.parent / f"config_corrupt_{time.strftime('%Y%m%d_%H%M%S')}.yaml"
                shutil.copy2(self._config_path, corrupt_backup)
                logger.info("损坏的配置已备份到: %s", corrupt_backup.name)
            except Exception:
                pass
            # 尝试从备份恢复
            backup = self._find_latest_backup()
            if backup:
                logger.info("从备份恢复配置: %s", backup.name)
                shutil.copy2(backup, self._config_path)
                with open(self._config_path, "r", encoding="utf-8") as f:
                    raw_data = yaml.safe_load(f)
            else:
                logger.warning("无可用备份，使用最小默认配置")
                self._data = {"models": [], "providers": [], "admin_password": "admin", "currency": "CNY"}
                return

        # 自动修复配置数据
        self._data = self._repair_config(raw_data)

    def save(self) -> None:
        """将内存中的配置写回 config.yaml，保存前自动备份。"""
        # 保存前先备份当前文件
        if self._config_path.exists():
            self.backup(reason="auto_before_save")
        try:
            with open(self._config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(self._data, f, allow_unicode=True, sort_keys=False)
        except OSError as e:
            raise ConfigError(f"配置文件写入失败: {e}") from e

    def sync_env_to_dotenv(self, env_key: str, value: str) -> None:
        """将环境变量值同步写入 .env 文件，确保容器重启后 Web 修改仍然生效。

        :param env_key: 环境变量名，如 SMARTROUTER_ADMIN_PASSWORD
        :param value: 要写入的值
        """
        dotenv_path = self._config_path.parent / ".env"
        try:
            lines: List[str] = []
            if dotenv_path.exists():
                with open(dotenv_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            found = False
            new_lines: List[str] = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith(f"{env_key}="):
                    new_lines.append(f"{env_key}={value}\n")
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                # 追加到文件末尾（确保前面有空行分隔）
                if new_lines and not new_lines[-1].endswith("\n"):
                    new_lines.append("\n")
                new_lines.append(f"{env_key}={value}\n")
            with open(dotenv_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
        except OSError:
            pass  # .env 文件写入失败不影响运行时生效

    # ------------------------------------------------------------------ #
    # 访问接口
    # ------------------------------------------------------------------ #
    # 环境变量覆盖映射
    _ENV_OVERRIDES = {
        "admin_password": "SMARTROUTER_ADMIN_PASSWORD",
        "api_key": "SMARTROUTER_API_KEY",
        "ssh_key": "SMARTROUTER_SSH_KEY",
    }

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值。config.yaml 中的值优先于环境变量。

        优先级（从高到低）：
        1. config.yaml 中的值（Web 修改会写入 config.yaml，确保在线修改持久生效）
        2. 环境变量（仅在 config.yaml 中没有对应 key 时作为兜底）
        3. default 参数
        """
        # 优先使用 config.yaml 中的值
        if key in self._data:
            return self._data[key]
        # 回退到环境变量
        env_key = self._ENV_OVERRIDES.get(key)
        if env_key and env_key in os.environ:
            return os.environ[env_key]
        return default

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    @property
    def data(self) -> Dict[str, Any]:
        return self._data

    # ------------------------------------------------------------------ #
    # 业务相关便捷方法
    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    # Provider（提供方）管理
    # ------------------------------------------------------------------ #
    def get_providers(self) -> List[Dict[str, Any]]:
        """返回提供方列表，自动补全默认值。"""
        providers = self._data.get("providers", [])
        enriched: List[Dict[str, Any]] = []
        for p in providers:
            # 防御性检查：跳过无效 provider 配置
            if not isinstance(p, dict) or not p.get("name"):
                continue
            item = dict(p)
            # 环境变量覆盖 Provider API Key
            env_key = f"SMARTROUTER_PROVIDER_API_KEY_{p['name'].upper().replace('-', '_').replace('.', '_')}"
            if env_key in os.environ:
                item["api_key"] = os.environ[env_key]
            # 默认值
            if "display_name" not in item:
                item["display_name"] = item.get("name", "")
            if "api_type" not in item:
                item["api_type"] = "openai"
            if "base_url" not in item:
                item["base_url"] = ""
            if "api_key" not in item:
                item["api_key"] = ""
            if "balance_script" not in item:
                item["balance_script"] = ""
            if "price_script" not in item:
                item["price_script"] = ""
            if "balance_manual" not in item:
                item["balance_manual"] = None
            if "balance_currency" not in item:
                item["balance_currency"] = "CNY"
            enriched.append(item)
        return enriched

    def get_provider(self, name: str) -> Optional[Dict[str, Any]]:
        """按名称获取提供方配置。"""
        for p in self.get_providers():
            if p["name"] == name:
                return p
        return None

    def get_models(self) -> List[Dict[str, Any]]:
        """返回模型列表，合并 Provider 配置，并对 API Key 做环境变量覆盖，自动计算 capability。

        优先级（从高到低）：
        1. 模型自身的字段（如 api_key、base_url）
        2. 关联 Provider 的字段
        3. 环境变量覆盖
        4. 默认值
        """
        providers_map = {p["name"]: p for p in self.get_providers()}
        models = self._data.get("models", [])
        enriched: List[Dict[str, Any]] = []
        for m in models:
            # 防御性检查：跳过无效模型配置
            if not isinstance(m, dict) or not m.get("name"):
                continue
            item = dict(m)
            # 合并 Provider 配置：模型自身字段优先，Provider 字段作为兜底
            # 继承规则：模型字段为空/空字符串时，使用 Provider 的值
            provider_name = m.get("provider", "")
            provider = providers_map.get(provider_name) if provider_name else None
            if provider:
                # Provider 字段作为兜底（模型未设置或为空时使用 Provider 的值）
                if not item.get("base_url") and provider.get("base_url"):
                    item["base_url"] = provider["base_url"]
                if not item.get("api_key") and provider.get("api_key"):
                    item["api_key"] = provider["api_key"]
                if not item.get("api_type") and provider.get("api_type"):
                    item["api_type"] = provider["api_type"]
                # 标记模型是否在配置中显式设置了 api_key（用于前端区分"继承"和"自有"）
                item["_api_key_inherited"] = not m.get("api_key")
                # 记录 Provider 信息供前端和余额查询使用
                item["_provider"] = {
                    "name": provider["name"],
                    "display_name": provider.get("display_name", provider["name"]),
                    "api_key": provider.get("api_key", ""),
                    "base_url": provider.get("base_url", ""),
                    "api_type": provider.get("api_type", "openai"),
                    "provider_type": provider.get("provider_type", ""),
                    "balance_script": provider.get("balance_script", ""),
                    "price_script": provider.get("price_script", ""),
                    "balance_manual": provider.get("balance_manual"),
                    "balance_currency": provider.get("balance_currency", "CNY"),
                }
            # 环境变量覆盖 API Key（模型级）
            env_key = f"SMARTROUTER_API_KEY_{m['name'].upper().replace('-', '_').replace('.', '_')}"
            if env_key in os.environ:
                item["api_key"] = os.environ[env_key]
            # 自动计算 capability：如果用户手动设置了 capability 则优先使用，
            # 否则从 params_b 自动换算
            if "capability" not in m or m.get("capability") is None:
                params_b = m.get("params_b", 0)
                item["capability"] = params_b_to_capability(params_b) if params_b else 50
            # 确保 task_types 存在
            if "task_types" not in item or item["task_types"] is None:
                item["task_types"] = []
            # 确保 modalities 存在（模型支持的模态列表，如 ["text", "image", "audio", "video"]）
            if "modalities" not in item or item["modalities"] is None:
                item["modalities"] = []
            # 确保 pending_modalities 存在（待确认的模态检测结果）
            if "pending_modalities" not in item or item["pending_modalities"] is None:
                item["pending_modalities"] = None
            # 确保 pending_modalities_detected_at 存在（待确认模态的检测时间戳）
            if "pending_modalities_detected_at" not in item or item["pending_modalities_detected_at"] is None:
                item["pending_modalities_detected_at"] = None
            # 默认 price_unit 为 1M（每百万token）
            if "price_unit" not in item:
                item["price_unit"] = "1M"
            # 默认不冻结价格
            if "price_frozen" not in item:
                item["price_frozen"] = False
            # 默认不手动设定余额
            if "balance_manual" not in item:
                item["balance_manual"] = None
            # 默认余额货币与 price_currency 相同
            if "balance_currency" not in item:
                item["balance_currency"] = item.get("price_currency", "USD")
            # 默认不冻结余额
            if "balance_frozen" not in item:
                item["balance_frozen"] = False
            # 默认启用模型
            if "enabled" not in item:
                item["enabled"] = True
            # 默认全天生效（null/空字符串/空列表表示全天）
            if "active_hours" not in item:
                item["active_hours"] = None
            # 默认无模型级脚本
            if "balance_script" not in item:
                item["balance_script"] = ""
            if "price_script" not in item:
                item["price_script"] = ""
            enriched.append(item)
        return enriched

    def get_model(self, name: str) -> Optional[Dict[str, Any]]:
        for m in self.get_models():
            if m["name"] == name:
                return m
        return None

    def get_default_model(self) -> Optional[Dict[str, Any]]:
        """冷启动降级使用的默认中等模型。"""
        default_name = self._data.get("default_model")
        if default_name:
            return self.get_model(default_name)
        # 兜底：取第一个 capability>=50 的模型
        for m in self.get_models():
            if m.get("capability", 0) >= 50:
                return m
        models = self.get_models()
        return models[0] if models else None

    @property
    def cache_ttl_seconds(self) -> int:
        return int(self._data.get("cache_ttl_seconds", 300))

    @property
    def balance_cache_ttl_seconds(self) -> int:
        return int(self._data.get("balance_cache_seconds", 300))

    @property
    def price_sync_interval_hours(self) -> int:
        return int(self._data.get("price_sync_interval_hours", 6))

    @property
    def exchange_rate_sync_interval_hours(self) -> int:
        return int(self._data.get("exchange_rate_sync_interval_hours", 12))

    @property
    def currency(self) -> str:
        """用户选择的显示货币单位（CNY/USD）。"""
        return self._data.get("currency", "CNY")

    @currency.setter
    def currency(self, value: str) -> None:
        self._data["currency"] = value

    @property
    def exchange_rates(self) -> Dict[str, float]:
        """汇率表，如 {"USD_CNY": 7.25, "CNY_USD": 0.1379}。"""
        return self._data.get("exchange_rates", {})

    @exchange_rates.setter
    def exchange_rates(self, value: Dict[str, float]) -> None:
        self._data["exchange_rates"] = value

    @property
    def model_aliases(self) -> Dict[str, str]:
        """模型名映射表，如 {"gpt-4": "deepseek-chat"}。"""
        return self._data.get("model_aliases", {})

    @model_aliases.setter
    def model_aliases(self, value: Dict[str, str]) -> None:
        self._data["model_aliases"] = value

    def resolve_model_name(self, name: str) -> str:
        """将请求中的模型名映射为实际模型名。

        优先查 model_aliases，找不到则原样返回。
        """
        aliases = self.model_aliases
        return aliases.get(name, name)

    @property
    def fallback_model_name(self) -> str:
        """无法路由时的兜底模型名。"""
        return self._data.get("fallback_model", "") or self._data.get("default_model", "")

    def get_fallback_model(self) -> Optional[Dict[str, Any]]:
        """获取兜底模型配置。优先 fallback_model，其次 default_model。"""
        fb_name = self.fallback_model_name
        if fb_name:
            model = self.get_model(fb_name)
            if model:
                return model
        # 兜底：取第一个 capability>=50 的模型
        for m in self.get_models():
            if m.get("capability", 0) >= 50:
                return m
        models = self.get_models()
        return models[0] if models else None

    @property
    def log_retention_days(self) -> int:
        """日志保存天数，0 表示永久保存。"""
        return int(self._data.get("log_retention_days", 0))

    @log_retention_days.setter
    def log_retention_days(self, value: int) -> None:
        self._data["log_retention_days"] = value

    @property
    def new_mark_ttl_seconds(self) -> int:
        """训练样本新增标记持续时间（秒）。"""
        return int(self._data.get("new_mark_ttl_seconds", 3600))

    @new_mark_ttl_seconds.setter
    def new_mark_ttl_seconds(self, value: int) -> None:
        self._data["new_mark_ttl_seconds"] = value

    @property
    def sample_max_capacity(self) -> int:
        """非自动新增样本的最大保存容量。0 表示无上限。"""
        return int(self._data.get("sample_max_capacity", 0))

    @sample_max_capacity.setter
    def sample_max_capacity(self, value: int) -> None:
        self._data["sample_max_capacity"] = value

    @property
    def route_weights(self) -> Dict[str, Any]:
        """路由权重配置。

        predictor_weight: 预测模型推荐权重 (0-1)
        score_weight: 评分选模型权重 (0-1)
        model_preferences: 用户模型调用偏好 {model_name: weight}
        """
        defaults = {
            "predictor_weight": 0.5,
            "score_weight": 0.5,
            "model_preferences": {},
        }
        rw = self._data.get("route_weights", {})
        return {**defaults, **rw}

    @route_weights.setter
    def route_weights(self, value: Dict[str, Any]) -> None:
        self._data["route_weights"] = value


    @property
    def difficulty_ranges(self) -> Dict[str, Any]:
        """Token 消耗范围到难度的映射配置。

        配置格式：
        difficulty_ranges:
          - min_tokens: 0
            max_tokens: 50
            difficulty: 10
          - min_tokens: 50
            max_tokens: 300
            difficulty: 30
          ...
        """
        defaults = [
            {"min_tokens": 0, "max_tokens": 50, "difficulty": 10},
            {"min_tokens": 50, "max_tokens": 300, "difficulty": 30},
            {"min_tokens": 300, "max_tokens": 800, "difficulty": 50},
            {"min_tokens": 800, "max_tokens": 2000, "difficulty": 75},
            {"min_tokens": 2000, "max_tokens": 999999, "difficulty": 95},
        ]
        return self._data.get("difficulty_ranges", defaults)

    @difficulty_ranges.setter
    def difficulty_ranges(self, value: Any) -> None:
        self._data["difficulty_ranges"] = value

    def tokens_to_difficulty(self, tokens: int) -> int:
        """根据 Token 消耗量映射到难度等级（1-100）。

        支持阶梯式精确匹配和范围间隙线性插值：
        1. Token 值落在某个 [min_tokens, max_tokens) 区间内 → 直接返回对应 difficulty
        2. Token 值落在两个相邻范围的间隙 → 线性插值计算平滑难度
        3. Token 值低于最小范围 → 返回第一个范围的 difficulty
        4. Token 值高于最大范围 → 返回最后一个范围的 difficulty

        :param tokens: Token 消耗量
        :return: 难度等级 1-100
        """
        ranges = self.difficulty_ranges
        if not ranges:
            return 50

        # 1. 精确匹配
        for r in ranges:
            if r.get("min_tokens", 0) <= tokens < r.get("max_tokens", 999999):
                return int(r.get("difficulty", 50))

        # 2. 间隙插值：按 min_tokens 排序后查找间隙
        sorted_ranges = sorted(ranges, key=lambda r: r.get("min_tokens", 0))

        # 低于最小范围
        if tokens < sorted_ranges[0].get("min_tokens", 0):
            return int(sorted_ranges[0].get("difficulty", 50))

        # 高于最大范围
        if tokens >= sorted_ranges[-1].get("max_tokens", 999999):
            return int(sorted_ranges[-1].get("difficulty", 50))

        # 只有一个范围
        if len(sorted_ranges) == 1:
            return int(sorted_ranges[0].get("difficulty", 50))

        # 查找间隙并插值
        for i in range(len(sorted_ranges) - 1):
            lower = sorted_ranges[i]
            upper = sorted_ranges[i + 1]
            lower_max = lower.get("max_tokens", 999999)
            upper_min = upper.get("min_tokens", 0)
            if lower_max <= tokens < upper_min:
                # 线性插值
                gap = upper_min - lower_max
                if gap <= 0:
                    return int(upper.get("difficulty", 50))
                ratio = (tokens - lower_max) / gap
                lower_diff = float(lower.get("difficulty", 50))
                upper_diff = float(upper.get("difficulty", 50))
                difficulty = lower_diff + ratio * (upper_diff - lower_diff)
                return max(1, min(100, int(round(difficulty))))

        return 50


# 全局单例
config = Config.get_instance()
