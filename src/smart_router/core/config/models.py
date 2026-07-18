"""Pydantic models for configuration - type-safe, validated, serializable."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class ActiveHours(BaseModel):
    """Time range when a model is active, e.g. '18:00-9:00'."""

    range: str = Field(..., pattern=r"^\d{1,2}:\d{2}-\d{1,2}:\d{2}$")

    def is_active_now(self) -> bool:
        """Check if current time falls within this active hours range."""
        import datetime

        now = datetime.datetime.now().time()
        parts = self.range.split("-")
        start = datetime.datetime.strptime(parts[0].strip(), "%H:%M").time()
        end = datetime.datetime.strptime(parts[1].strip(), "%H:%M").time()

        if start <= end:
            return start <= now <= end
        else:
            # Crosses midnight, e.g. 22:00-06:00
            return now >= start or now <= end


class ScheduleRule(BaseModel):
    """A single schedule rule defining when a model is active."""

    # Time ranges (can be multiple, can cross midnight)
    time_ranges: List[str] = Field(default_factory=list, description="Time ranges like '09:00-18:00', '22:00-06:00'")

    # Day-of-week filter (1=Monday, 7=Sunday, empty=all days)
    days_of_week: List[int] = Field(default_factory=list, description="Active days of week (1=Mon,7=Sun), empty=all")

    # Day-of-month filter (1-31, empty=all days)
    days_of_month: List[int] = Field(default_factory=list, description="Active days of month (1-31), empty=all")

    # Date range (inclusive, format YYYY-MM-DD)
    start_date: str = Field("", description="Start date (YYYY-MM-DD), empty=no limit")
    end_date: str = Field("", description="End date (YYYY-MM-DD), empty=no limit")

    # Holiday behavior
    include_holidays: bool = Field(True, description="Whether this rule applies on holidays")

    def is_active_now(self, holidays: Optional[List[str]] = None) -> bool:
        """Check if this schedule rule is currently active."""
        import datetime

        now = datetime.datetime.now()
        today_str = now.strftime("%Y-%m-%d")

        # Check date range
        if self.start_date and today_str < self.start_date:
            return False
        if self.end_date and today_str > self.end_date:
            return False

        # Check holiday
        is_holiday = holidays and today_str in holidays
        if is_holiday and not self.include_holidays:
            return False

        # Check day of week
        if self.days_of_week:
            # Python: Monday=0, we use Monday=1
            dow = now.isoweekday()
            if dow not in self.days_of_week:
                return False

        # Check day of month
        if self.days_of_month:
            if now.day not in self.days_of_month:
                return False

        # Check time ranges
        if not self.time_ranges:
            return True  # No time restriction = all day

        current_time = now.time()
        for tr in self.time_ranges:
            parts = tr.split("-")
            start = datetime.datetime.strptime(parts[0].strip(), "%H:%M").time()
            end = datetime.datetime.strptime(parts[1].strip(), "%H:%M").time()
            if start <= end:
                if start <= current_time <= end:
                    return True
            else:
                # Crosses midnight
                if current_time >= start or current_time <= end:
                    return True

        return False


class DifficultyRange(BaseModel):
    """Token consumption range to difficulty level mapping."""

    min_tokens: int = Field(..., ge=0, description="Lower bound (inclusive)")
    max_tokens: int = Field(..., gt=0, description="Upper bound (exclusive)")
    difficulty: int = Field(..., ge=1, le=100, description="Difficulty level 1-100")

    @model_validator(mode="after")
    def validate_range(self) -> "DifficultyRange":
        if self.min_tokens >= self.max_tokens:
            raise ValueError(
                f"min_tokens ({self.min_tokens}) must be less than max_tokens ({self.max_tokens})"
            )
        return self


class RouteWeights(BaseModel):
    """Routing algorithm weight configuration."""

    predictor_weight: float = Field(0.5, ge=0, le=1, description="ML/RL predictor weight")
    score_weight: float = Field(0.5, ge=0, le=1, description="Scoring weight")
    model_preferences: Dict[str, float] = Field(
        default_factory=dict, description="Per-model preference weights"
    )
    # Dual-path fusion weights
    fusion_weighted: float = Field(0.4, ge=0, le=1, description="Weighted fusion weight")
    fusion_voting: float = Field(0.3, ge=0, le=1, description="Voting fusion weight")
    fusion_primary_backup: float = Field(0.2, ge=0, le=1, description="Primary-backup weight")
    fusion_cascade: float = Field(0.1, ge=0, le=1, description="Cascade fusion weight")


class ModelConfig(BaseModel):
    """Individual model configuration."""

    model_config = {"extra": "ignore"}

    name: str = Field(..., min_length=1, description="Unique model identifier")
    provider: str = Field("", description="Provider name for config inheritance")
    api_key: str = Field("", repr=False, description="API key (env var override recommended)")
    api_type: str = Field("openai", description="API type: openai, deepseek, anthropic, etc.")
    base_url: str = Field("", description="API base URL")
    litellm_name: str = Field("", description="LiteLLM model name for price sync")

    # Model capabilities
    params_b: float = Field(0, ge=0, description="Parameter count in billions")
    capability: Optional[int] = Field(None, ge=1, le=100, description="Manual capability override")
    task_types: List[str] = Field(default_factory=list, description="Supported task types")
    modalities: List[str] = Field(default_factory=lambda: ["text"], description="Supported modalities")
    pending_modalities: Optional[List[str]] = Field(None, description="Pending modality detection results, awaiting confirmation")
    pending_modalities_detected_at: Optional[float] = Field(None, description="Timestamp when pending_modalities were detected")
    capability_tags: List[str] = Field(default_factory=list, description="Capability tags: coding, writing, translation, math, etc.")

    # Pricing
    price_input: float = Field(0, ge=0, description="Input price per unit")
    price_output: float = Field(0, ge=0, description="Output price per unit")
    price_currency: str = Field("CNY", description="Price currency")
    price_unit: str = Field("1M", description="Price unit: 1, 1K, 1M, 1B")
    price_frozen: bool = Field(False, description="Freeze price from auto-sync")
    price_script: str = Field("", description="Custom price script")

    # Balance
    balance_manual: Optional[float] = Field(None, description="Manual balance override")
    balance_currency: str = Field("CNY", description="Balance currency")
    balance_frozen: bool = Field(False, description="Freeze balance from auto-sync")
    balance_script: str = Field("", description="Custom balance script")
    balance_source: str = Field("inherit", description="Balance source: inherit/script/manual. inherit=use provider balance")
    balance_deduction_mode: str = Field("inherit", description="Deduction mode: inherit/realtime/periodic")

    # Availability
    enabled: bool = Field(True, description="Whether model is enabled")
    active_hours: Optional[List[str]] = Field(None, description="Active hours ranges (legacy format)")
    schedule_rules: List[ScheduleRule] = Field(default_factory=list, description="Schedule rules for model activation (new format)")

    @property
    def effective_capability(self) -> int:
        """Get effective capability: manual override or auto-calculated from params_b."""
        if self.capability is not None:
            return self.capability
        return self._params_b_to_capability(self.params_b)

    @staticmethod
    def _params_b_to_capability(params_b: float) -> int:
        """Convert parameter count (B) to capability level (1-100)."""
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

    @property
    def is_free(self) -> bool:
        """Check if this model is free (zero input and output price)."""
        return self.price_input == 0 and self.price_output == 0

    @property
    def is_active_now(self) -> bool:
        """Check if model is currently within active hours."""
        # New schedule rules take precedence
        if self.schedule_rules:
            # Get holidays from settings if available
            holidays = self._get_holidays()
            for rule in self.schedule_rules:
                if rule.is_active_now(holidays):
                    return True
            return False
        # Legacy active_hours format
        if not self.active_hours:
            return True
        for hours_str in self.active_hours:
            ah = ActiveHours(range=hours_str)
            if ah.is_active_now():
                return True
        return False

    def _get_holidays(self) -> Optional[List[str]]:
        """Get holiday list from settings."""
        try:
            from smart_router.core.config.settings import get_settings
            settings = get_settings()
            return getattr(settings, 'holidays', None)
        except Exception:
            return None


class ProviderConfig(BaseModel):
    """Provider configuration for API key and URL inheritance."""

    model_config = {"extra": "ignore"}

    name: str = Field(..., min_length=1)
    display_name: str = Field("")
    api_key: str = Field("", repr=False)
    api_type: str = Field("openai")
    base_url: str = Field("")
    enabled: bool = Field(True, description="Whether provider is enabled")
    balance_script: str = Field("")
    price_script: str = Field("")
    balance_manual: Optional[float] = Field(None)
    balance_currency: str = Field("CNY")
    balance_updated_at: Optional[float] = Field(None, description="Last balance update timestamp")
    balance_source: str = Field("auto", description="Balance source: auto/script/manual. auto=script first, fallback manual")
    balance_deduction_mode: str = Field("realtime", description="Deduction mode: realtime/periodic. realtime=deduct after each request")
    provider_type: str = Field("openai", description="Provider type: openai/deepseek/openrouter/anthropic/custom")


class ExchangeRateEntry(BaseModel):
    """Single exchange rate entry."""

    from_currency: str
    to_currency: str
    rate: float = Field(..., gt=0)


class TenantConfig(BaseModel):
    """Multi-tenant configuration."""

    model_config = {"extra": "ignore"}

    tenant_id: str = Field(..., min_length=1)
    name: str = Field(...)
    api_key: str = Field("", repr=False)
    email: str = Field("", description="Tenant contact email for notifications")
    enabled: bool = Field(True)
    quota_daily_tokens: int = Field(0, description="Daily token quota, 0=unlimited")
    quota_daily_requests: int = Field(0, description="Daily request quota, 0=unlimited")
    allowed_models: List[str] = Field(default_factory=list, description="Allowed models (whitelist), empty=all")
    blocked_models: List[str] = Field(default_factory=list, description="Blocked models (blacklist), empty=none blocked")
    model_filter_mode: str = Field("whitelist", description="Model filter mode: whitelist or blacklist")
    route_weights_override: Optional[RouteWeights] = Field(
        None, description="Per-tenant route weight override"
    )
    # Balance notification thresholds
    balance_threshold_type: str = Field("fixed", description="Threshold type: fixed or percentage")
    balance_threshold_value: float = Field(10, description="Threshold value (amount for fixed, percentage for percentage)")
    balance_notify_enabled: bool = Field(True, description="Enable balance low notifications for this tenant")


class NotificationChannel(BaseModel):
    """Single notification channel configuration."""

    type: str = Field(..., description="Channel type: webhook/dingtalk/wecom/feishu/telegram/slack/email")
    name: str = Field("", description="Channel display name")
    url: str = Field("", description="Webhook URL (for webhook/dingtalk/wecom/feishu/slack)")
    bot_token: str = Field("", description="Bot token (for telegram)")
    chat_id: str = Field("", description="Chat ID (for telegram)")
    smtp_host: str = Field("", description="SMTP server host (for email)")
    smtp_port: int = Field(587, description="SMTP server port (for email)")
    smtp_user: str = Field("", description="SMTP username (for email)")
    smtp_pass: str = Field("", repr=False, description="SMTP password (for email)")
    from_address: str = Field("", alias="from", description="Sender email address")
    to: str = Field("", description="Recipient email(s), comma-separated")

    model_config = {"extra": "ignore", "populate_by_name": True}


class NotificationConfig(BaseModel):
    """Notification system configuration."""

    enabled: bool = Field(False, description="Enable/disable notifications")
    min_severity: str = Field("warning", description="Minimum severity to notify: info/warning/critical")
    channels: List[NotificationChannel] = Field(default_factory=list, description="Notification channels")


class ApiKeyFormatConfig(BaseModel):
    """API Key generation format configuration."""

    format_type: str = Field("openai", description="Format type: openai/prefix/smartrouter/custom")
    prefix: str = Field("sk", description="Key prefix (e.g., sk, sr)")
    random_length: int = Field(32, ge=16, le=64, description="Random part length")
    include_timestamp: bool = Field(False, description="Include timestamp in key")
    custom_template: str = Field("", description="Custom template with variables: {prefix}, {timestamp}, {random}")


class PermissionConfig(BaseModel):
    """Permission configuration for a role or user."""

    # Dashboard permissions
    dashboard_view: str = Field("none", description="Dashboard view permission: none/self/all")
    # Balance permissions
    balance_view: str = Field("none", description="Balance view permission: none/self/all")
    balance_edit: bool = Field(False, description="Can edit balance")
    # Model permissions
    models_view: bool = Field(False, description="Can view models")
    models_edit: bool = Field(False, description="Can edit models")
    # Provider permissions
    providers_view: bool = Field(False, description="Can view providers")
    providers_edit: bool = Field(False, description="Can edit providers")
    # Request logs permissions
    request_logs_view: str = Field("none", description="Request logs view: none/self/all")
    # Config permissions
    config_view: bool = Field(False, description="Can view config")
    config_edit: bool = Field(False, description="Can edit config")
    # User management permissions
    users_view: bool = Field(False, description="Can view users")
    users_edit: bool = Field(False, description="Can edit users")
    # Tenant management permissions
    tenants_view: bool = Field(False, description="Can view tenants")
    tenants_edit: bool = Field(False, description="Can edit tenants")


class RolePermissions(BaseModel):
    """Role-based permissions configuration."""

    admin: PermissionConfig = Field(default_factory=lambda: PermissionConfig(
        dashboard_view="all", balance_view="all", balance_edit=True,
        models_view=True, models_edit=True,
        providers_view=True, providers_edit=True,
        request_logs_view="all",
        config_view=True, config_edit=True,
        users_view=True, users_edit=True,
        tenants_view=True, tenants_edit=True
    ))
    user: PermissionConfig = Field(default_factory=lambda: PermissionConfig(
        dashboard_view="self", balance_view="self", balance_edit=False,
        models_view=True, models_edit=False,
        providers_view=False, providers_edit=False,
        request_logs_view="self",
        config_view=False, config_edit=False,
        users_view=False, users_edit=False,
        tenants_view=False, tenants_edit=False
    ))
    guest: PermissionConfig = Field(default_factory=lambda: PermissionConfig(
        dashboard_view="all", balance_view="none", balance_edit=False,
        models_view=True, models_edit=False,
        providers_view=False, providers_edit=False,
        request_logs_view="none",
        config_view=True, config_edit=False,
        users_view=False, users_edit=False,
        tenants_view=False, tenants_edit=False
    ))


class HealthCheckConfig(BaseModel):
    """Model health check configuration."""

    enabled: bool = Field(True)
    interval_seconds: int = Field(300, ge=30, description="Check interval")
    timeout_seconds: int = Field(10, ge=1, description="Request timeout")
    failure_threshold: int = Field(3, ge=1, description="Consecutive failures to mark unhealthy")
    recovery_threshold: int = Field(2, ge=1, description="Consecutive successes to mark healthy")
    test_prompt: str = Field("Hello", description="Test prompt for health check")


class RLConfig(BaseModel):
    """Reinforcement learning configuration."""

    enabled: bool = Field(True)
    online_learning_rate: float = Field(0.01, gt=0, le=1)
    batch_retrain_interval_hours: int = Field(24, ge=1)
    min_samples_for_retrain: int = Field(100, ge=10)
    exploration_rate: float = Field(0.1, ge=0, le=1)
    discount_factor: float = Field(0.95, ge=0, le=1)


class StorageConfig(BaseModel):
    """Database/storage configuration."""

    backend: str = Field("sqlite", description="Storage backend: sqlite, postgresql, mysql")
    url: str = Field("", description="Database URL (for postgresql/mysql)")
    redis_url: str = Field("", description="Redis URL for caching layer")

    @property
    def effective_url(self) -> str:
        """Get effective database URL."""
        if self.url:
            return self.url
        if self.backend == "sqlite":
            return "sqlite:///data/smart_router.db"
        return ""


class WebFrameworkConfig(BaseModel):
    """Web framework selection configuration."""

    backend: str = Field("fastapi", description="Web framework: fastapi, litestar")
    host: str = Field("0.0.0.0")
    port: int = Field(8000, ge=1, le=65535)
    log_level: str = Field("info")
    cors_origins: List[str] = Field(default_factory=lambda: ["*"])
    concurrency_limit: int = Field(100, ge=1)
