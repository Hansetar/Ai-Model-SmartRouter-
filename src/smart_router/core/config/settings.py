"""Global settings - Pydantic Settings + YAML + env vars.

Priority (highest to lowest):
1. Environment variables (SMARTROUTER_*)
2. .env file
3. config.yaml
4. Defaults
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import (
    ApiKeyFormatConfig,
    DifficultyRange,
    HealthCheckConfig,
    ModelConfig,
    NotificationConfig,
    PermissionConfig,
    ProviderConfig,
    RLConfig,
    RolePermissions,
    RouteWeights,
    StorageConfig,
    TenantConfig,
    WebFrameworkConfig,
)

logger = logging.getLogger(__name__)

# Default config path
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent.parent.parent / "config.yaml"


# ------------------------------------------------------------------ #
# Provider balance query script templates
# ------------------------------------------------------------------ #
_OPENAI_BALANCE_SCRIPT = """\
import httpx
import json
import os
api_key = os.environ.get('PROVIDER_API_KEY', '')
if not api_key:
    print(json.dumps({'error': 'No API key configured'}))
    exit(1)
try:
    resp = httpx.get(
        'https://api.openai.com/v1/organization/usage',
        headers={'Authorization': f'Bearer {api_key}'},
        timeout=10
    )
    if resp.status_code == 200:
        data = resp.json()
        print(json.dumps({'balance': data.get('total', 0), 'currency': 'USD'}))
    else:
        print(json.dumps({'error': f'API returned {resp.status_code}'}))
except Exception as e:
    print(json.dumps({'error': str(e)}))
"""

_DEEPSEEK_BALANCE_SCRIPT = """\
import httpx
import json
import os
api_key = os.environ.get('PROVIDER_API_KEY', '')
if not api_key:
    print(json.dumps({'error': 'No API key configured'}))
    exit(1)
try:
    resp = httpx.get(
        'https://api.deepseek.com/user/balance',
        headers={'Authorization': f'Bearer {api_key}'},
        timeout=10
    )
    if resp.status_code == 200:
        data = resp.json()
        balance_infos = data.get('balance_infos', [])
        # 汇总所有余额条目（可能存在多个货币账户）
        total = sum(float(b.get('total_balance', 0)) for b in balance_infos)
        print(json.dumps({'balance': round(total, 6), 'balance_currency': 'CNY'}))
    else:
        print(json.dumps({'error': f'API returned {resp.status_code}'}))
except Exception as e:
    print(json.dumps({'error': str(e)}))
"""

_OPENROUTER_BALANCE_SCRIPT = """\
import httpx
import json
import os
api_key = os.environ.get('PROVIDER_API_KEY', '')
if not api_key:
    print(json.dumps({'error': 'No API key configured'}))
    exit(1)
try:
    resp = httpx.get(
        'https://openrouter.ai/api/v1/auth/key',
        headers={'Authorization': f'Bearer {api_key}'},
        timeout=10
    )
    if resp.status_code == 200:
        data = resp.json()
        limit = data.get('data', {}).get('limit', 0)
        usage = data.get('data', {}).get('usage', 0)
        balance = float(limit) - float(usage) if limit else 0
        print(json.dumps({'balance': balance, 'currency': 'USD'}))
    else:
        print(json.dumps({'error': f'API returned {resp.status_code}'}))
except Exception as e:
    print(json.dumps({'error': str(e)}))
"""

_ANTHROPIC_BALANCE_SCRIPT = """\
import json
import os
# Anthropic does not provide a public balance API
# Use manual balance or custom integration
print(json.dumps({'error': 'Anthropic does not provide balance API', 'balance': None}))
"""

_PRICE_FALLBACK_SCRIPT = """\
import json
# Use litellm or manual pricing for accuracy
print(json.dumps({'error': 'Use litellm for price sync', 'prices': {}}))
"""

PROVIDER_BALANCE_TEMPLATES: Dict[str, Dict[str, str]] = {
    "openai": {
        "name": "OpenAI",
        "script": _OPENAI_BALANCE_SCRIPT,
        "description": "查询OpenAI账户余额",
    },
    "deepseek": {
        "name": "DeepSeek",
        "script": _DEEPSEEK_BALANCE_SCRIPT,
        "description": "查询DeepSeek账户余额",
    },
    "openrouter": {
        "name": "OpenRouter",
        "script": _OPENROUTER_BALANCE_SCRIPT,
        "description": "查询OpenRouter账户余额",
    },
    "anthropic": {
        "name": "Anthropic",
        "script": _ANTHROPIC_BALANCE_SCRIPT,
        "description": "Anthropic暂不支持余额查询API",
    },
}

PROVIDER_PRICE_TEMPLATES: Dict[str, Dict[str, str]] = {
    "openai": {
        "name": "OpenAI",
        "script": _PRICE_FALLBACK_SCRIPT,
        "description": "OpenAI价格查询（建议使用litellm同步）",
    },
    "deepseek": {
        "name": "DeepSeek",
        "script": _PRICE_FALLBACK_SCRIPT,
        "description": "DeepSeek价格查询（建议使用litellm同步）",
    },
}


class Settings(BaseSettings):
    """Global application settings.

    Loads from environment variables (SMARTROUTER_* prefix), .env file,
    and config.yaml. Provides type-safe access to all configuration.
    """

    model_config = SettingsConfigDict(
        env_prefix="SMARTROUTER_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # Core settings
    # ------------------------------------------------------------------ #
    admin_password: str = Field("admin", repr=False)
    api_key: str = Field("admin123", repr=False)
    ssh_key: str = Field("", repr=False)
    currency: str = Field("CNY")
    default_model: str = Field("")
    fallback_model: str = Field("")

    # Global API key (super-admin channel)
    global_api_key_enabled: bool = Field(False, description="Enable/disable global API key for super-admin access")
    global_api_key_expires_at: Optional[float] = Field(None, description="Global API key expiration timestamp (Unix epoch). None=not set, 0=permanent, >0=expires at this time")
    global_api_key_duration_days: int = Field(7, ge=1, description="Default duration in days when creating/renewing global API key")

    # Cache
    cache_ttl_seconds: int = Field(600, ge=0)
    balance_cache_seconds: int = Field(300, ge=0)

    # Sync intervals
    price_sync_interval_hours: int = Field(6, ge=1)
    exchange_rate_sync_interval_hours: int = Field(12, ge=1)

    # Log retention
    log_retention_days: int = Field(0, ge=0, description="0=keep forever")

    # Training
    new_mark_ttl_seconds: int = Field(3600, ge=0)
    sample_max_capacity: int = Field(0, ge=0, description="0=unlimited")

    # ------------------------------------------------------------------ #
    # Complex config (loaded from YAML, not env vars)
    # ------------------------------------------------------------------ #
    models: List[ModelConfig] = Field(default_factory=list)
    providers: List[ProviderConfig] = Field(default_factory=list)
    difficulty_ranges: List[DifficultyRange] = Field(
        default_factory=lambda: [
            DifficultyRange(min_tokens=0, max_tokens=50, difficulty=10),
            DifficultyRange(min_tokens=50, max_tokens=300, difficulty=20),
            DifficultyRange(min_tokens=300, max_tokens=800, difficulty=40),
            DifficultyRange(min_tokens=800, max_tokens=2000, difficulty=80),
            DifficultyRange(min_tokens=2000, max_tokens=999999, difficulty=99),
        ]
    )
    exchange_rates: Dict[str, float] = Field(default_factory=dict)
    model_aliases: Dict[str, str] = Field(default_factory=dict)
    route_weights: RouteWeights = Field(default_factory=RouteWeights)

    # ------------------------------------------------------------------ #
    # New v2 features
    # ------------------------------------------------------------------ #
    tenants: List[TenantConfig] = Field(default_factory=list)
    health_check: HealthCheckConfig = Field(default_factory=HealthCheckConfig)
    rl_config: RLConfig = Field(default_factory=RLConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    web: WebFrameworkConfig = Field(default_factory=WebFrameworkConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)
    api_key_format: ApiKeyFormatConfig = Field(default_factory=ApiKeyFormatConfig)
    role_permissions: RolePermissions = Field(default_factory=RolePermissions)
    holidays: List[str] = Field(default_factory=list, description="Holiday dates (YYYY-MM-DD format) for schedule rules")
    balance_priority_strategy: str = Field("weight", description="Balance priority strategy: weight/balance/cost/latency/custom")
    balance_priority_per_model: Dict[str, str] = Field(default_factory=dict, description="Per-model strategy override")
    balance_priority_threshold: float = Field(0, description="Below this balance, model is deprioritized")

    # ------------------------------------------------------------------ #
    # Internal state
    # ------------------------------------------------------------------ #
    _config_path: Path = _DEFAULT_CONFIG_PATH
    _yaml_data: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # YAML loading / saving
    # ------------------------------------------------------------------ #
    def load_from_yaml(self, config_path: Optional[str] = None) -> None:
        """Load configuration from YAML file with auto-repair and backup recovery."""
        if config_path:
            self._config_path = Path(config_path)

        if not self._config_path.exists():
            logger.warning("Config file not found: %s", self._config_path)
            backup = self._find_latest_backup()
            if backup:
                logger.info("Restoring from backup: %s", backup.name)
                shutil.copy2(backup, self._config_path)
            else:
                logger.warning("No backup available, using defaults")
                return

        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                raw_data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            logger.error("YAML syntax error: %s", e)
            backup = self._find_latest_backup()
            if backup:
                shutil.copy2(backup, self._config_path)
                with open(self._config_path, "r", encoding="utf-8") as f:
                    raw_data = yaml.safe_load(f)
            else:
                logger.warning("No backup, using defaults")
                return

        if not isinstance(raw_data, dict):
            logger.warning("Config is not a dict, using defaults")
            return

        self._yaml_data = raw_data
        self._apply_yaml_data(raw_data)

    def _apply_yaml_data(self, data: Dict[str, Any]) -> None:
        """Apply YAML data to settings fields."""
        # Simple fields
        for key in [
            "admin_password", "api_key", "ssh_key", "currency",
            "default_model", "fallback_model", "cache_ttl_seconds",
            "balance_cache_seconds", "price_sync_interval_hours",
            "exchange_rate_sync_interval_hours", "log_retention_days",
            "new_mark_ttl_seconds", "sample_max_capacity",
            "global_api_key_enabled", "global_api_key_expires_at",
            "global_api_key_duration_days",
        ]:
            if key in data and data[key] is not None:
                setattr(self, key, data[key])

        # Models
        if "models" in data and isinstance(data["models"], list):
            self.models = []
            for m in data["models"]:
                if isinstance(m, dict) and m.get("name"):
                    self.models.append(ModelConfig(**m))

        # Providers
        if "providers" in data and isinstance(data["providers"], list):
            self.providers = []
            for p in data["providers"]:
                if isinstance(p, dict) and p.get("name"):
                    self.providers.append(ProviderConfig(**p))

        # Difficulty ranges
        if "difficulty_ranges" in data and isinstance(data["difficulty_ranges"], list):
            self.difficulty_ranges = [
                DifficultyRange(**r) for r in data["difficulty_ranges"]
                if isinstance(r, dict)
            ]

        # Exchange rates
        if "exchange_rates" in data and isinstance(data["exchange_rates"], dict):
            self.exchange_rates = data["exchange_rates"]

        # Model aliases
        if "model_aliases" in data and isinstance(data["model_aliases"], dict):
            self.model_aliases = data["model_aliases"]

        # Route weights
        if "route_weights" in data and isinstance(data["route_weights"], dict):
            self.route_weights = RouteWeights(**data["route_weights"])

        # Tenants
        if "tenants" in data and isinstance(data["tenants"], list):
            self.tenants = [TenantConfig(**t) for t in data["tenants"] if isinstance(t, dict)]

        # Health check
        if "health_check" in data and isinstance(data["health_check"], dict):
            self.health_check = HealthCheckConfig(**data["health_check"])

        # RL config
        if "rl_config" in data and isinstance(data["rl_config"], dict):
            self.rl_config = RLConfig(**data["rl_config"])

        # Storage
        if "storage" in data and isinstance(data["storage"], dict):
            self.storage = StorageConfig(**data["storage"])

        # Web framework
        if "web" in data and isinstance(data["web"], dict):
            self.web = WebFrameworkConfig(**data["web"])

        # Notifications
        if "notifications" in data and isinstance(data["notifications"], dict):
            self.notifications = NotificationConfig(**data["notifications"])

        # Holidays
        if "holidays" in data and isinstance(data["holidays"], list):
            self.holidays = data["holidays"]

        # API key format
        if "api_key_format" in data and isinstance(data["api_key_format"], dict):
            self.api_key_format = ApiKeyFormatConfig(**data["api_key_format"])

        # Role permissions
        if "role_permissions" in data and isinstance(data["role_permissions"], dict):
            self.role_permissions = RolePermissions(**data["role_permissions"])

    def save_to_yaml(self, config_path: Optional[str] = None) -> None:
        """Save current settings to YAML file with auto-backup."""
        path = Path(config_path) if config_path else self._config_path

        # Backup before save
        if path.exists():
            self._backup(reason="auto_before_save")

        data = self._to_yaml_dict()
        try:
            # Ensure parent directory exists
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
            logger.info("Config saved to %s", path)
        except OSError as e:
            logger.error("Failed to write config to %s: %s", path, e)
            raise ConfigError(f"Failed to write config: {e}") from e
        except Exception as e:
            logger.error("Unexpected error saving config: %s", e)
            raise ConfigError(f"Unexpected error saving config: {e}") from e

    def _to_yaml_dict(self) -> Dict[str, Any]:
        """Convert settings to YAML-compatible dict."""
        data = dict(self._yaml_data)  # Preserve unknown keys

        # Update known fields
        data["admin_password"] = self.admin_password
        data["api_key"] = self.api_key
        data["currency"] = self.currency
        data["default_model"] = self.default_model
        data["fallback_model"] = self.fallback_model
        data["cache_ttl_seconds"] = self.cache_ttl_seconds
        data["balance_cache_seconds"] = self.balance_cache_seconds
        data["price_sync_interval_hours"] = self.price_sync_interval_hours
        data["exchange_rate_sync_interval_hours"] = self.exchange_rate_sync_interval_hours
        data["log_retention_days"] = self.log_retention_days
        data["new_mark_ttl_seconds"] = self.new_mark_ttl_seconds
        data["sample_max_capacity"] = self.sample_max_capacity
        data["global_api_key_enabled"] = self.global_api_key_enabled
        data["global_api_key_expires_at"] = self.global_api_key_expires_at
        data["global_api_key_duration_days"] = self.global_api_key_duration_days
        data["models"] = [m.model_dump() for m in self.models]
        data["providers"] = [p.model_dump() for p in self.providers]
        data["difficulty_ranges"] = [r.model_dump() for r in self.difficulty_ranges]
        data["exchange_rates"] = self.exchange_rates
        data["model_aliases"] = self.model_aliases
        data["route_weights"] = self.route_weights.model_dump()
        data["tenants"] = [t.model_dump() for t in self.tenants]
        data["health_check"] = self.health_check.model_dump()
        data["rl_config"] = self.rl_config.model_dump()
        data["storage"] = self.storage.model_dump()
        data["web"] = self.web.model_dump()
        data["notifications"] = self.notifications.model_dump(by_alias=True)
        data["holidays"] = self.holidays
        data["api_key_format"] = self.api_key_format.model_dump()
        data["role_permissions"] = self.role_permissions.model_dump()

        return data

    # ------------------------------------------------------------------ #
    # Backup management
    # ------------------------------------------------------------------ #
    _MAX_BACKUPS = 10

    def _backup_dir(self) -> Path:
        return self._config_path.parent / "config_backups"

    def _backup(self, reason: str = "manual") -> Optional[Path]:
        """Create a config backup."""
        if not self._config_path.exists():
            return None
        try:
            backup_dir = self._backup_dir()
            backup_dir.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            backup_path = backup_dir / f"config_{ts}_{reason}.yaml"
            shutil.copy2(self._config_path, backup_path)
            # Cleanup old backups
            backups = sorted(backup_dir.glob("config_*.yaml"), reverse=True)
            for old in backups[self._MAX_BACKUPS:]:
                old.unlink(missing_ok=True)
            return backup_path
        except Exception as e:
            logger.warning("Backup failed: %s", e)
            return None

    def _find_latest_backup(self) -> Optional[Path]:
        """Find the latest valid backup file."""
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

    # ------------------------------------------------------------------ #
    # Business helpers
    # ------------------------------------------------------------------ #
    def get_model(self, name: str) -> Optional[ModelConfig]:
        """Get model by name."""
        for m in self.models:
            if m.name == name:
                return m
        return None

    def get_provider(self, name: str) -> Optional[ProviderConfig]:
        """Get provider by name."""
        for p in self.providers:
            if p.name == name:
                return p
        return None

    def get_enriched_model(self, name: str) -> Optional[Dict[str, Any]]:
        """Get model with provider fields merged (for backward compat)."""
        model = self.get_model(name)
        if not model:
            return None
        result = model.model_dump()
        # Merge provider defaults
        if model.provider:
            provider = self.get_provider(model.provider)
            if provider:
                if not result.get("base_url") and provider.base_url:
                    result["base_url"] = provider.base_url
                if not result.get("api_key") and provider.api_key:
                    result["api_key"] = provider.api_key
                if not result.get("api_type") and provider.api_type:
                    result["api_type"] = provider.api_type
                result["_provider"] = provider.model_dump()
        # Add computed fields
        result["capability"] = model.effective_capability
        return result

    def resolve_model_name(self, name: str) -> str:
        """Resolve model alias to actual model name."""
        return self.model_aliases.get(name, name)

    def tokens_to_difficulty(self, tokens: int) -> int:
        """Map token consumption to difficulty level (1-100).

        Supports:
        1. Exact range match: min_tokens <= tokens < max_tokens -> difficulty
        2. Gap interpolation: linear interpolation between adjacent ranges
        3. Boundary handling: below min / above max
        """
        ranges = self.difficulty_ranges
        if not ranges:
            return 50

        # 1. Exact match
        for r in ranges:
            if r.min_tokens <= tokens < r.max_tokens:
                return r.difficulty

        # 2. Gap interpolation
        sorted_ranges = sorted(ranges, key=lambda r: r.min_tokens)

        # Below minimum
        if tokens < sorted_ranges[0].min_tokens:
            return sorted_ranges[0].difficulty

        # Above maximum
        if tokens >= sorted_ranges[-1].max_tokens:
            return sorted_ranges[-1].difficulty

        # Single range
        if len(sorted_ranges) == 1:
            return sorted_ranges[0].difficulty

        # Find gap and interpolate
        for i in range(len(sorted_ranges) - 1):
            lower = sorted_ranges[i]
            upper = sorted_ranges[i + 1]
            if lower.max_tokens <= tokens < upper.min_tokens:
                gap = upper.min_tokens - lower.max_tokens
                if gap <= 0:
                    return upper.difficulty
                ratio = (tokens - lower.max_tokens) / gap
                difficulty = lower.difficulty + ratio * (upper.difficulty - lower.difficulty)
                return max(1, min(100, int(round(difficulty))))

        return 50

    def get_tenant(self, tenant_id: str) -> Optional[TenantConfig]:
        """Get tenant by ID."""
        for t in self.tenants:
            if t.tenant_id == tenant_id:
                return t
        return None


class ConfigError(Exception):
    """Configuration error."""


# ------------------------------------------------------------------ #
# Global singleton
# ------------------------------------------------------------------ #
_settings_instance: Optional[Settings] = None


def get_settings(config_path: Optional[str] = None) -> Settings:
    """Get or create the global settings instance."""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
        _settings_instance.load_from_yaml(config_path)
    return _settings_instance


def reload_settings(config_path: Optional[str] = None) -> Settings:
    """Force reload settings from YAML."""
    global _settings_instance
    _settings_instance = Settings()
    _settings_instance.load_from_yaml(config_path)
    return _settings_instance
