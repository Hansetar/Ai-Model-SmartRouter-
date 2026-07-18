"""API key authentication for proxy endpoints.

Supports:
- Tenant API key: bound to a specific tenant, used for /v1/ API calls
- Global API key: super-admin channel, bypasses tenant restrictions
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from ..core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class ApiKeyIdentity:
    """Resolved identity from an API key."""

    is_valid: bool
    is_global_key: bool = False
    tenant_id: Optional[str] = None
    tenant_name: Optional[str] = None
    error: Optional[str] = None


def verify_api_key(api_key: str) -> bool:
    """Verify API key for proxy access (backward-compatible boolean check).

    Checks both tenant API keys and global API key.
    """
    identity = resolve_api_key(api_key)
    return identity.is_valid


def resolve_api_key(api_key: str) -> ApiKeyIdentity:
    """Resolve an API key to its identity (tenant or global admin).

    Priority:
    1. Check tenant API keys (api_key -> tenant lookup)
    2. Check global API key (super-admin channel)

    Returns ApiKeyIdentity with resolved information.
    """
    if not api_key:
        return ApiKeyIdentity(is_valid=False, error="API key is required")

    settings = get_settings()

    # 1. Check tenant API keys first
    for tenant in settings.tenants:
        if tenant.api_key and tenant.api_key == api_key:
            if not tenant.enabled:
                return ApiKeyIdentity(
                    is_valid=False,
                    tenant_id=tenant.tenant_id,
                    tenant_name=tenant.name,
                    error="Tenant is disabled",
                )
            return ApiKeyIdentity(
                is_valid=True,
                is_global_key=False,
                tenant_id=tenant.tenant_id,
                tenant_name=tenant.name,
            )

    # 2. Check global API key (super-admin channel)
    if settings.api_key and api_key == settings.api_key:
        # Check if global key is enabled
        if not getattr(settings, 'global_api_key_enabled', False):
            return ApiKeyIdentity(
                is_valid=False,
                is_global_key=True,
                error="Global API key is disabled",
            )
        # Check expiration
        expires_at = getattr(settings, 'global_api_key_expires_at', None)
        if expires_at is not None:
            # expires_at == 0 means permanent (never expires)
            if expires_at != 0 and time.time() > expires_at:
                return ApiKeyIdentity(
                    is_valid=False,
                    is_global_key=True,
                    error="Global API key has expired",
                )
        return ApiKeyIdentity(
            is_valid=True,
            is_global_key=True,
        )

    return ApiKeyIdentity(is_valid=False, error="Invalid API key")


def resolve_tenant_from_request(api_key: str, tenant_id_header: Optional[str] = None) -> ApiKeyIdentity:
    """Resolve tenant identity from API key (priority) or X-Tenant-ID header (fallback).

    This is the primary entry point for chat completions and other /v1/ endpoints.

    Args:
        api_key: The Bearer token from Authorization header
        tenant_id_header: The X-Tenant-ID header value (optional, for backward compat)

    Returns:
        ApiKeyIdentity with resolved tenant information
    """
    if not api_key:
        return ApiKeyIdentity(is_valid=False, error="API key is required")

    settings = get_settings()

    # Priority 1: Resolve tenant from API key
    for tenant in settings.tenants:
        if tenant.api_key and tenant.api_key == api_key:
            if not tenant.enabled:
                return ApiKeyIdentity(
                    is_valid=False,
                    tenant_id=tenant.tenant_id,
                    tenant_name=tenant.name,
                    error="Tenant is disabled",
                )
            return ApiKeyIdentity(
                is_valid=True,
                is_global_key=False,
                tenant_id=tenant.tenant_id,
                tenant_name=tenant.name,
            )

    # Priority 2: Check global API key
    if settings.api_key and api_key == settings.api_key:
        if not getattr(settings, 'global_api_key_enabled', False):
            return ApiKeyIdentity(
                is_valid=False,
                is_global_key=True,
                error="Global API key is disabled",
            )
        # Check expiration
        expires_at = getattr(settings, 'global_api_key_expires_at', None)
        if expires_at is not None and expires_at != 0 and time.time() > expires_at:
            return ApiKeyIdentity(
                is_valid=False,
                is_global_key=True,
                error="Global API key has expired",
            )
        # Global key: try to resolve tenant from X-Tenant-ID header
        if tenant_id_header:
            tenant = settings.get_tenant(tenant_id_header)
            if tenant:
                return ApiKeyIdentity(
                    is_valid=True,
                    is_global_key=True,
                    tenant_id=tenant.tenant_id,
                    tenant_name=tenant.name,
                )
        # Global key without tenant binding - still valid, no tenant restriction
        return ApiKeyIdentity(
            is_valid=True,
            is_global_key=True,
        )

    # Priority 3: Fallback to X-Tenant-ID header (backward compat)
    # This path is reached when the api_key doesn't match any tenant or global key
    # but a tenant_id_header is provided - this is the legacy behavior
    if tenant_id_header:
        tenant = settings.get_tenant(tenant_id_header)
        if tenant:
            # Legacy mode: X-Tenant-ID with any valid api_key
            # Only allow if the global api_key matches (for backward compat)
            if settings.api_key and api_key == settings.api_key:
                return ApiKeyIdentity(
                    is_valid=True,
                    is_global_key=True,
                    tenant_id=tenant.tenant_id,
                    tenant_name=tenant.name,
                )
            return ApiKeyIdentity(
                is_valid=False,
                error="Invalid API key for tenant access",
            )

    return ApiKeyIdentity(is_valid=False, error="Invalid API key")


def is_api_key_configured() -> bool:
    """Check if API key authentication is enabled."""
    settings = get_settings()
    return bool(settings.api_key) or bool(settings.tenants)
