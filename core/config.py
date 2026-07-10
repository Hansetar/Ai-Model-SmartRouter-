"""
core/config.py
===============
核心配置文件解析模块。

负责加载 config.yaml，并提供全局单例访问。支持环境变量覆盖敏感字段（API Key）。
本模块不依赖任何网络框架，可被双模式适配层共享复用。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


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
    def reload(self) -> None:
        """重新从磁盘读取 config.yaml。"""
        if not self._config_path.exists():
            raise ConfigError(f"配置文件不存在: {self._config_path}")
        with open(self._config_path, "r", encoding="utf-8") as f:
            self._data = yaml.safe_load(f) or {}

    def save(self) -> None:
        """将内存中的配置写回 config.yaml。"""
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
    def get_models(self) -> List[Dict[str, Any]]:
        """返回模型列表，并对 API Key 做环境变量覆盖，自动计算 capability。"""
        models = self._data.get("models", [])
        enriched: List[Dict[str, Any]] = []
        for m in models:
            item = dict(m)
            # 环境变量覆盖 API Key
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
