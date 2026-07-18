"""Authentication package - JWT + API key + multi-tenant + super admin + authorization."""

from .jwt_auth import create_access_token, verify_token, verify_password
from .api_key import verify_api_key, is_api_key_configured, resolve_api_key, resolve_tenant_from_request, ApiKeyIdentity
from .authorization import authorize_model_access, is_model_available_for_tenant, AuthorizationResult
from .superadmin import (
    is_setup_required,
    get_super_admin_password,
    get_super_admin_role,
    save_admin_config,
    load_admin_config,
    delete_admin_config,
    list_users,
    add_user,
    update_user,
    delete_user,
    verify_user_password,
)

__all__ = [
    "create_access_token",
    "verify_token",
    "verify_password",
    "verify_api_key",
    "is_api_key_configured",
    "resolve_api_key",
    "resolve_tenant_from_request",
    "ApiKeyIdentity",
    "authorize_model_access",
    "is_model_available_for_tenant",
    "AuthorizationResult",
    "is_setup_required",
    "get_super_admin_password",
    "get_super_admin_role",
    "save_admin_config",
    "load_admin_config",
    "delete_admin_config",
    "list_users",
    "add_user",
    "update_user",
    "delete_user",
    "verify_user_password",
]
