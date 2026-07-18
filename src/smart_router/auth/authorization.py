"""Authorization module - 6-point check for model access control.

Checks:
1. API key valid and bound to tenant
2. Tenant has permission to use the model (whitelist/blacklist)
3. Model is enabled and currently active (is_active_now)
4. Tenant balance > 0 or unlimited
5. Provider balance > 0 and model balance > 0 (if set)
6. Tenant daily quota not exceeded
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from ..core.config import get_settings, ModelConfig, ProviderConfig, TenantConfig
from .api_key import ApiKeyIdentity, resolve_tenant_from_request

logger = logging.getLogger(__name__)


@dataclass
class AuthorizationResult:
    """Result of authorization check."""

    authorized: bool
    error_code: int = 0  # HTTP status code
    error_detail: str = ""  # Human-readable error
    tenant_id: Optional[str] = None
    is_global_key: bool = False
    # Which check failed (for debugging)
    failed_check: Optional[str] = None


def authorize_model_access(
    api_key: str,
    model_name: str,
    tenant_id_header: Optional[str] = None,
) -> AuthorizationResult:
    """Perform full 6-point authorization check for model access.

    This is the main entry point for both direct model calls and
    smart routing validation.

    Args:
        api_key: Bearer token from Authorization header
        model_name: The model being requested
        tenant_id_header: X-Tenant-ID header (fallback for legacy compat)

    Returns:
        AuthorizationResult with authorized=True or detailed error info
    """
    settings = get_settings()

    # ── Check 1: API key valid and bound to tenant ──
    identity = resolve_tenant_from_request(api_key, tenant_id_header)
    if not identity.is_valid:
        return AuthorizationResult(
            authorized=False,
            error_code=401,
            error_detail=identity.error or "Invalid API key",
            failed_check="api_key",
        )

    # Global key bypasses tenant-level checks (2, 4, 6) but still checks model availability (3, 5)
    if identity.is_global_key and not identity.tenant_id:
        return _authorize_global_key(settings, model_name, identity)

    # Tenant-bound access - run all 6 checks
    tenant_id = identity.tenant_id
    tenant = settings.get_tenant(tenant_id) if tenant_id else None

    # ── Check 2: Tenant has permission to use the model ──
    if tenant:
        model_perm = _check_model_permission(tenant, model_name, settings)
        if not model_perm.authorized:
            model_perm.tenant_id = tenant_id
            model_perm.is_global_key = identity.is_global_key
            return model_perm

    # ── Check 3: Model is enabled and currently active ──
    model = settings.get_model(model_name)
    if not model:
        # Check if it's an alias
        resolved = settings.resolve_model_name(model_name)
        if resolved != model_name:
            model = settings.get_model(resolved)
            if model:
                model_name = resolved
        if not model:
            return AuthorizationResult(
                authorized=False,
                error_code=404,
                error_detail=f"Model '{model_name}' does not exist",
                tenant_id=tenant_id,
                is_global_key=identity.is_global_key,
                failed_check="model_exists",
            )

    if not model.enabled:
        return AuthorizationResult(
            authorized=False,
            error_code=403,
            error_detail=f"Model '{model_name}' is not enabled",
            tenant_id=tenant_id,
            is_global_key=identity.is_global_key,
            failed_check="model_enabled",
        )

    if not model.is_active_now:
        return AuthorizationResult(
            authorized=False,
            error_code=403,
            error_detail=f"Model '{model_name}' is not currently active",
            tenant_id=tenant_id,
            is_global_key=identity.is_global_key,
            failed_check="model_active",
        )

    # ── Check 4: Tenant balance > 0 or unlimited ──
    if tenant:
        balance_check = _check_tenant_balance(tenant)
        if not balance_check.authorized:
            balance_check.tenant_id = tenant_id
            balance_check.is_global_key = identity.is_global_key
            return balance_check

    # ── Check 5: Provider balance > 0 and model balance > 0 ──
    balance_check = _check_provider_model_balance(model, settings)
    if not balance_check.authorized:
        balance_check.tenant_id = tenant_id
        balance_check.is_global_key = identity.is_global_key
        return balance_check

    # ── Check 6: Tenant daily quota not exceeded ──
    if tenant:
        quota_check = _check_tenant_quota(tenant)
        if not quota_check.authorized:
            quota_check.tenant_id = tenant_id
            quota_check.is_global_key = identity.is_global_key
            return quota_check

    return AuthorizationResult(
        authorized=True,
        tenant_id=tenant_id,
        is_global_key=identity.is_global_key,
    )


def _authorize_global_key(
    settings,
    model_name: str,
    identity: ApiKeyIdentity,
) -> AuthorizationResult:
    """Authorize access using global API key (super-admin channel).

    Global key bypasses tenant-level checks but still validates:
    - Model exists
    - Model is enabled and active
    - Provider/model balance > 0
    """
    model = settings.get_model(model_name)
    if not model:
        resolved = settings.resolve_model_name(model_name)
        if resolved != model_name:
            model = settings.get_model(resolved)
        if not model:
            return AuthorizationResult(
                authorized=False,
                error_code=404,
                error_detail=f"Model '{model_name}' does not exist",
                is_global_key=True,
                failed_check="model_exists",
            )

    if not model.enabled:
        return AuthorizationResult(
            authorized=False,
            error_code=403,
            error_detail=f"Model '{model_name}' is not enabled",
            is_global_key=True,
            failed_check="model_enabled",
        )

    if not model.is_active_now:
        return AuthorizationResult(
            authorized=False,
            error_code=403,
            error_detail=f"Model '{model_name}' is not currently active",
            is_global_key=True,
            failed_check="model_active",
        )

    # Check provider/model balance
    balance_check = _check_provider_model_balance(model, settings)
    balance_check.is_global_key = True
    return balance_check


def _check_model_permission(
    tenant: TenantConfig,
    model_name: str,
    settings,
) -> AuthorizationResult:
    """Check 2: Tenant has permission to use the model.

    Whitelist mode (default): allowed_models empty = no models allowed (deny all)
    Blacklist mode: blocked_models lists denied models

    When both are set, models outside both lists are treated as blacklisted.
    """
    mode = getattr(tenant, 'model_filter_mode', 'whitelist')

    if mode == 'blacklist':
        # Blacklist mode: block models in blocked_models
        if tenant.blocked_models and model_name in tenant.blocked_models:
            return AuthorizationResult(
                authorized=False,
                error_code=403,
                error_detail=f"No permission to use model '{model_name}'",
                failed_check="model_permission",
            )
        # If both whitelist and blacklist are set, models outside both lists are blacklisted
        if tenant.allowed_models and model_name not in tenant.allowed_models:
            return AuthorizationResult(
                authorized=False,
                error_code=404,
                error_detail=f"Model '{model_name}' does not exist",
                failed_check="model_permission",
            )
    else:
        # Whitelist mode: empty allowed_models = no models allowed (deny all)
        if not tenant.allowed_models:
            return AuthorizationResult(
                authorized=False,
                error_code=404,
                error_detail=f"Model '{model_name}' does not exist",
                failed_check="model_permission",
            )
        if model_name not in tenant.allowed_models:
            # Return "does not exist" to not expose model existence
            return AuthorizationResult(
                authorized=False,
                error_code=404,
                error_detail=f"Model '{model_name}' does not exist",
                failed_check="model_permission",
            )

    return AuthorizationResult(authorized=True)


def _check_tenant_balance(tenant: TenantConfig) -> AuthorizationResult:
    """Check 4: Tenant balance > 0 or unlimited.

    Checks the TenantBalance from the database.
    If tenant has unlimited balance, this check passes.
    """
    try:
        from ..core.storage import get_session, TenantBalance
        with get_session() as session:
            balance_record = session.get(TenantBalance, tenant.tenant_id)
            if balance_record:
                if getattr(balance_record, 'unlimited', False):
                    return AuthorizationResult(authorized=True)
                if balance_record.balance <= 0:
                    return AuthorizationResult(
                        authorized=False,
                        error_code=402,
                        error_detail="Account is in arrears, please recharge",
                        failed_check="tenant_balance",
                    )
    except Exception as e:
        logger.warning("Failed to check tenant balance: %s", e)
        # If we can't check, allow through (fail-open for balance check)
        # This prevents blocking all requests if DB is down

    return AuthorizationResult(authorized=True)


def _check_provider_model_balance(
    model: ModelConfig,
    settings,
) -> AuthorizationResult:
    """Check 5: Provider balance > 0 and model balance > 0.

    If provider balance is 0 or model balance is 0, the model is unavailable.
    This doesn't block the entire request (unlike tenant balance),
    it just means this specific model can't be used.
    """
    # Check model-level balance
    if model.balance_manual is not None and model.balance_manual <= 0:
        return AuthorizationResult(
            authorized=False,
            error_code=403,
            error_detail=f"Model '{model.name}' balance is depleted",
            failed_check="model_balance",
        )

    # Check provider-level balance
    if model.provider:
        provider = settings.get_provider(model.provider)
        if provider and provider.balance_manual is not None and provider.balance_manual <= 0:
            return AuthorizationResult(
                authorized=False,
                error_code=403,
                error_detail=f"Provider '{model.provider}' balance is depleted",
                failed_check="provider_balance",
            )

    return AuthorizationResult(authorized=True)


def _check_tenant_quota(tenant: TenantConfig) -> AuthorizationResult:
    """Check 6: Tenant daily quota not exceeded.

    Checks quota_daily_tokens and quota_daily_requests.
    0 = unlimited.
    """
    if tenant.quota_daily_requests <= 0 and tenant.quota_daily_tokens <= 0:
        return AuthorizationResult(authorized=True)

    try:
        from ..core.storage import get_session, TenantUsage
        from sqlalchemy import select, func
        import datetime

        today = datetime.date.today()
        today_str = today.strftime("%Y-%m-%d")

        with get_session() as session:
            usage = session.execute(
                select(TenantUsage)
                .where(TenantUsage.tenant_id == tenant.tenant_id)
                .where(TenantUsage.date == today_str)
            ).scalar_one_or_none()

            if not usage:
                return AuthorizationResult(authorized=True)

            if tenant.quota_daily_requests > 0 and usage.total_requests >= tenant.quota_daily_requests:
                return AuthorizationResult(
                    authorized=False,
                    error_code=429,
                    error_detail="Daily request quota exceeded",
                    failed_check="tenant_quota",
                )

            if tenant.quota_daily_tokens > 0 and usage.total_tokens >= tenant.quota_daily_tokens:
                return AuthorizationResult(
                    authorized=False,
                    error_code=429,
                    error_detail="Daily token quota exceeded",
                    failed_check="tenant_quota",
                )

    except Exception as e:
        logger.warning("Failed to check tenant quota: %s", e)

    return AuthorizationResult(authorized=True)


def is_model_available_for_tenant(
    model_name: str,
    tenant_id: Optional[str],
    is_global_key: bool = False,
) -> bool:
    """Quick check if a model is available for a tenant (for routing candidate filtering).

    This is a lighter-weight check used by the routing engine to filter candidates.
    It checks: model exists, enabled, active, tenant permission, and balance > 0.
    Does NOT check quota (that's checked at call time).
    """
    settings = get_settings()

    model = settings.get_model(model_name)
    if not model:
        return False
    if not model.enabled:
        return False
    if not model.is_active_now:
        return False

    # Check provider/model balance
    if model.balance_manual is not None and model.balance_manual <= 0:
        return False
    if model.provider:
        provider = settings.get_provider(model.provider)
        if provider and provider.balance_manual is not None and provider.balance_manual <= 0:
            return False

    # Global key bypasses tenant checks
    if is_global_key:
        return True

    # Check tenant permission
    if tenant_id:
        tenant = settings.get_tenant(tenant_id)
        if tenant:
            mode = getattr(tenant, 'model_filter_mode', 'whitelist')
            if mode == 'blacklist':
                if tenant.blocked_models and model_name in tenant.blocked_models:
                    return False
                if tenant.allowed_models and model_name not in tenant.allowed_models:
                    return False
            else:
                # Whitelist: empty = deny all
                if not tenant.allowed_models:
                    return False
                if model_name not in tenant.allowed_models:
                    return False

            # Check tenant balance
            try:
                from ..core.storage import get_session, TenantBalance
                with get_session() as session:
                    balance_record = session.get(TenantBalance, tenant.tenant_id)
                    if balance_record:
                        if not getattr(balance_record, 'unlimited', False) and balance_record.balance <= 0:
                            return False
            except Exception:
                pass  # Fail-open for balance check in routing

    return True
