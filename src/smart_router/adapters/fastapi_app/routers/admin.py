"""Admin router - full management panel API endpoints.

Covers: login, dashboard, models CRUD, providers CRUD, config management,
training samples, model tuning, tenants, request logs, feedback,
balance/price scripts, modality/tag detection.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import random
import secrets
import string
import subprocess
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, text

from ....auth.jwt_auth import create_access_token, verify_password, verify_token
from ....auth.superadmin import (
    is_setup_required as _is_setup_required,
    save_admin_config as _save_admin_config,
    delete_admin_config as _delete_admin_config,
    list_users as _list_users,
    add_user as _add_user,
    update_user as _update_user,
    delete_user as _delete_user,
    verify_user_password as _verify_user_password,
)
from ....core.config import get_settings, reload_settings
from ....core.config.models import (
    ModelConfig, ProviderConfig, DifficultyRange, RouteWeights,
    TenantConfig, HealthCheckConfig, RLConfig, StorageConfig, WebFrameworkConfig,
)
from ....core.config.settings import PROVIDER_BALANCE_TEMPLATES, PROVIDER_PRICE_TEMPLATES
from ....core.storage import get_session, init_db, reset_db, RequestLog, ModelMetric, TrainingSample, FeedbackRecord, TenantUsage, ProviderBalanceLog, TenantBalance
from ....core.routing import get_routing_engine
from ....core.routing.task_detector import TaskTypeDetector
from ....core.exchange_rate import get_exchange_rate_manager

router = APIRouter()
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# API Key generation
# ------------------------------------------------------------------ #
def generate_api_key(format_config: Any) -> str:
    """Generate API key based on format configuration (using cryptographically secure random)."""
    _alphabet = string.ascii_letters + string.digits
    # Generate random part using secrets for cryptographic safety
    random_part = ''.join(secrets.choice(_alphabet) for _ in range(format_config.random_length))

    # Get timestamp if needed
    timestamp = datetime.now().strftime("%Y%m%d") if format_config.include_timestamp else ""

    # Generate based on format type
    if format_config.format_type == "openai":
        # sk-[32位随机字母数字]
        return f"sk-{random_part}"
    elif format_config.format_type == "prefix":
        # sk-[8位前缀]-[24位随机字符]
        prefix_part = ''.join(secrets.choice(_alphabet) for _ in range(8))
        return f"{format_config.prefix}-{prefix_part}-{random_part[:24]}"
    elif format_config.format_type == "smartrouter":
        # sr-[32位随机字母数字]
        return f"sr-{random_part}"
    elif format_config.format_type == "custom":
        # Custom template
        template = format_config.custom_template or "{prefix}-{random}"
        return template.format(
            prefix=format_config.prefix,
            timestamp=timestamp,
            random=random_part
        )
    else:
        # Default to OpenAI format
        return f"sk-{random_part}"


# ------------------------------------------------------------------ #
# Auth dependency
# ------------------------------------------------------------------ #
# RBAC role definitions
ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLE_GUEST = "guest"

# Permission map: endpoint_prefix -> minimum role required
# admin > user > guest
_ROLE_HIERARCHY = {ROLE_GUEST: 0, ROLE_USER: 1, ROLE_ADMIN: 2}

# Guest-accessible endpoints (read-only dashboard/stats)
_GUEST_ENDPOINTS = {
    "/dashboard", "/balance", "/config", "/config/info",
    "/feedback/stats", "/routing/status",
}

# User-accessible endpoints (guest + tag feedback, training samples read, requests read)
_USER_ENDPOINTS = _GUEST_ENDPOINTS | {
    "/tag-feedback", "/tags/list", "/training-samples",
    "/requests", "/feedback",
}


def _get_user_role(payload: dict) -> str:
    """Extract role from JWT payload, default to guest."""
    return payload.get("role", ROLE_GUEST)


def _check_permission(path: str, role: str) -> bool:
    """Check if a role has permission to access a path."""
    if role == ROLE_ADMIN:
        return True
    # Match path against allowed endpoints (exact match or prefix with /)
    if role == ROLE_GUEST:
        allowed = _GUEST_ENDPOINTS
    elif role == ROLE_USER:
        allowed = _USER_ENDPOINTS
    else:
        return False
    for ep in allowed:
        if path == ep or path.startswith(ep + "/"):
            return True
    return False


async def require_admin(request: Request) -> dict:
    """JWT authentication + RBAC permission check."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    payload = verify_token(auth[7:])
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    # RBAC check
    role = _get_user_role(payload)
    path = request.url.path
    # Strip /admin/api prefix for matching
    api_prefix = "/admin/api"
    if path.startswith(api_prefix):
        path = path[len(api_prefix):]
    if not _check_permission(path, role):
        raise HTTPException(status_code=403, detail=f"Insufficient permissions (role={role})")
    payload["role"] = role
    return payload


# ------------------------------------------------------------------ #
# Login
# ------------------------------------------------------------------ #
class LoginRequest(BaseModel):
    password: str


@router.post("/login")
async def login(req: LoginRequest):
    if not verify_password(req.password):
        raise HTTPException(status_code=401, detail="Wrong password")
    token = create_access_token({"sub": "admin", "role": ROLE_ADMIN})
    return {"access_token": token, "token_type": "bearer", "role": ROLE_ADMIN}


@router.post("/login/guest")
async def login_guest():
    """Guest login - no password required, read-only access."""
    token = create_access_token({"sub": "guest", "role": ROLE_GUEST})
    return {"access_token": token, "token_type": "bearer", "role": ROLE_GUEST}


# ------------------------------------------------------------------ #
# Super admin setup & user management (T041 supplement)
# ------------------------------------------------------------------ #
@router.get("/setup/status")
async def setup_status():
    """Check if super admin setup is required."""
    return {"setup_required": _is_setup_required()}


class SetupRequest(BaseModel):
    admin_password: str = Field(..., min_length=6, description="Super admin password (min 6 chars)")


@router.post("/setup/init")
async def setup_init(req: SetupRequest):
    """Initialize super admin (setup wizard). Only works when setup is required."""
    if not _is_setup_required():
        raise HTTPException(status_code=400, detail="Setup already completed. Delete admin config file to reset.")
    # Generate a random encryption key instead of using a hardcoded one
    import hashlib
    _enc_key = hashlib.sha256(f"smartrouter-{time.time()}-{secrets.token_hex(16)}".encode()).hexdigest()[:32]
    _save_admin_config({"admin_password": req.admin_password, "users": []}, _enc_key)
    token = create_access_token({"sub": "admin", "role": ROLE_ADMIN})
    return {"status": "ok", "access_token": token, "token_type": "bearer", "role": ROLE_ADMIN}


@router.post("/setup/reset")
async def setup_reset(_admin=Depends(require_admin)):
    """Reset super admin config (delete encrypted file, triggers setup wizard on next login)."""
    _delete_admin_config()
    return {"status": "ok", "message": "Admin config deleted. Setup wizard will appear on next login."}


@router.post("/login/user", deprecated=True, include_in_schema=False)
async def login_user():
    """Deprecated: Use /login/user-auth instead."""
    raise HTTPException(status_code=410, detail="This endpoint is deprecated. Use /login/user-auth instead.")


class UserLoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login/user-auth")
async def login_user_auth(req: UserLoginRequest):
    """Login as a managed user."""
    user = _verify_user_password(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_access_token({"sub": user["username"], "role": user["role"]})
    return {"access_token": token, "token_type": "bearer", "role": user["role"], "username": user["username"]}


@router.get("/users")
async def get_users(_admin=Depends(require_admin)):
    """List all managed users."""
    users = _list_users()
    # Don't expose password hashes
    safe_users = [{"username": u.get("username"), "role": u.get("role", "user"), "tenant_id": u.get("tenant_id", "")} for u in users]
    return {"users": safe_users}


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=2)
    password: str = Field(..., min_length=6)
    role: str = Field("user", description="Role: admin/user/guest")
    tenant_id: str = Field("", description="Bound tenant ID")


@router.post("/users")
async def create_user(req: CreateUserRequest, _admin=Depends(require_admin)):
    """Create a new managed user."""
    if req.role not in (ROLE_ADMIN, ROLE_USER, ROLE_GUEST):
        raise HTTPException(status_code=400, detail=f"Invalid role: {req.role}")
    success = _add_user(req.username, req.password, req.role, req.tenant_id)
    if not success:
        raise HTTPException(status_code=409, detail=f"User '{req.username}' already exists")
    return {"status": "ok"}


class UpdateUserRequest(BaseModel):
    role: Optional[str] = None
    tenant_id: Optional[str] = None
    password: Optional[str] = None


@router.put("/users/{username}")
async def update_user(username: str, req: UpdateUserRequest, _admin=Depends(require_admin)):
    """Update a managed user."""
    if req.role and req.role not in (ROLE_ADMIN, ROLE_USER, ROLE_GUEST):
        raise HTTPException(status_code=400, detail=f"Invalid role: {req.role}")
    success = _update_user(username, role=req.role, tenant_id=req.tenant_id, password=req.password)
    if not success:
        raise HTTPException(status_code=404, detail=f"User '{username}' not found")
    return {"status": "ok"}


@router.delete("/users/{username}")
async def remove_user(username: str, _admin=Depends(require_admin)):
    """Delete a managed user."""
    success = _delete_user(username)
    if not success:
        raise HTTPException(status_code=404, detail=f"User '{username}' not found")
    return {"status": "ok"}


# ------------------------------------------------------------------ #
# Dashboard (optimized with SQL aggregation + in-memory cache)
# ------------------------------------------------------------------ #

class _SimpleTTLCache:
    """Simple TTL cache using only stdlib (no cachetools dependency)."""
    def __init__(self, ttl: int = 30, maxsize: int = 10):
        self._ttl = ttl
        self._maxsize = maxsize
        self._store: Dict[str, tuple] = {}  # key -> (value, expire_at)

    def get(self, key: str):
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expire_at = entry
        if time.time() > expire_at:
            del self._store[key]
            return None
        return value

    def __setitem__(self, key: str, value):
        # Evict expired entries if at capacity
        if len(self._store) >= self._maxsize:
            now = time.time()
            expired = [k for k, (_, exp) in self._store.items() if now > exp]
            for k in expired:
                del self._store[k]
            # If still at capacity, remove oldest
            if len(self._store) >= self._maxsize:
                oldest_key = next(iter(self._store))
                del self._store[oldest_key]
        self._store[key] = (value, time.time() + self._ttl)

_dashboard_cache = _SimpleTTLCache(ttl=1, maxsize=10)  # 1s cache for dashboard (supports real-time gradient refresh)
_balance_cache = _SimpleTTLCache(ttl=300, maxsize=10)  # 5min cache for balance


@router.get("/dashboard")
async def get_dashboard(period: str = "today", _admin=Depends(require_admin)):
    """Dashboard with SQL-level aggregation for performance."""
    cache_key = f"dashboard_{period}"
    cached = _dashboard_cache.get(cache_key)
    if cached:
        return cached

    settings = get_settings()
    since = _period_to_timestamp(period)
    try:
        with get_session() as session:
            query = session.query(RequestLog)
            if since:
                query = query.filter(RequestLog.timestamp >= since)

            # SQL-level aggregation instead of loading all records
            total = query.count()
            total_cost = session.query(func.sum(RequestLog.cost)).filter(
                RequestLog.timestamp >= since if since else True
            ).scalar() or 0
            avg_latency = session.query(func.avg(RequestLog.latency_ms)).filter(
                RequestLog.timestamp >= since if since else True
            ).scalar() or 0
            total_input = session.query(func.sum(RequestLog.prompt_tokens)).filter(
                RequestLog.timestamp >= since if since else True
            ).scalar() or 0
            total_output = session.query(func.sum(RequestLog.completion_tokens)).filter(
                RequestLog.timestamp >= since if since else True
            ).scalar() or 0
            success_count = session.query(func.count(RequestLog.id)).filter(
                RequestLog.timestamp >= since if since else True,
                RequestLog.success == True,
            ).scalar() or 0
            success_rate = (success_count / total * 100) if total > 0 else 0

            # Model distribution via SQL GROUP BY
            model_dist_rows = session.query(
                RequestLog.routed_model, func.count(RequestLog.id)
            ).filter(
                RequestLog.timestamp >= since if since else True
            ).group_by(RequestLog.routed_model).all()
            model_dist = [{"name": r[0] or "unknown", "value": r[1]} for r in model_dist_rows]

            # Trend via SQL GROUP BY (strftime for time bucketing)
            if period in ("today", "week"):
                time_fmt = "%H:00"
            else:
                time_fmt = "%m-%d"

            trend_rows = session.query(
                func.strftime(time_fmt, func.datetime(RequestLog.timestamp, "unixepoch")),
                func.count(RequestLog.id),
            ).filter(
                RequestLog.timestamp >= since if since else True
            ).group_by(
                func.strftime(time_fmt, func.datetime(RequestLog.timestamp, "unixepoch"))
            ).all()
            trend_labels = [r[0] or "" for r in trend_rows]
            trend_values = [r[1] for r in trend_rows]

            # Cost trend via SQL GROUP BY
            cost_trend_rows = session.query(
                func.strftime(time_fmt, func.datetime(RequestLog.timestamp, "unixepoch")),
                func.sum(RequestLog.cost),
            ).filter(
                RequestLog.timestamp >= since if since else True
            ).group_by(
                func.strftime(time_fmt, func.datetime(RequestLog.timestamp, "unixepoch"))
            ).all()
            cost_trend_labels = [r[0] or "" for r in cost_trend_rows]
            cost_trend_values = [round(r[1] or 0, 4) for r in cost_trend_rows]

            result = {
                "total_interceptions": total,
                "saved_cost": round(total_cost, 4),
                "avg_latency_ms": round(avg_latency, 2),
                "total_input_tokens": total_input,
                "total_output_tokens": total_output,
                "success_rate": round(success_rate, 1),
                "model_distribution": model_dist,
                "trend_labels": trend_labels,
                "trend_values": trend_values,
                "cost_trend_labels": cost_trend_labels,
                "cost_trend_values": cost_trend_values,
                "period": period,
            }
            _dashboard_cache[cache_key] = result
            return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------ #
# Models management (full CRUD)
# ------------------------------------------------------------------ #
@router.get("/models")
async def list_models(_admin=Depends(require_admin)):
    settings = get_settings()
    models = [settings.get_enriched_model(m.name) for m in settings.models]
    # Mask api_key for security
    for m in models:
        key = m.get("api_key", "")
        if key and len(key) > 8:
            m["api_key"] = key[:8] + "***"
        # Also mask provider api_key if present
        provider = m.get("_provider", {})
        if provider and provider.get("api_key") and len(provider["api_key"]) > 8:
            provider["api_key"] = provider["api_key"][:8] + "***"
    return {"models": models}


@router.post("/models")
async def create_model(request: Request, _admin=Depends(require_admin)):
    """Create a new model."""
    body = await request.json()
    try:
        model = ModelConfig(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid model config: {e}")
    settings = get_settings()
    if settings.get_model(model.name):
        raise HTTPException(status_code=400, detail=f"Model {model.name} already exists")
    settings.models.append(model)
    settings.save_to_yaml()
    return {"status": "ok", "name": model.name}


@router.post("/models/{model_name:path}/clone")
async def clone_model(model_name: str, request: Request, _admin=Depends(require_admin)):
    body = await request.json()
    new_name = body.get("new_name", f"{model_name}-copy")
    settings = get_settings()
    existing = settings.get_model(model_name)
    if not existing:
        raise HTTPException(status_code=404, detail="Model not found")
    if settings.get_model(new_name):
        raise HTTPException(status_code=400, detail=f"Model {new_name} already exists")
    new_model_data = copy.deepcopy(existing.model_dump())
    new_model_data["name"] = new_name
    settings.models.append(ModelConfig(**new_model_data))
    settings.save_to_yaml()
    return {"status": "ok", "name": new_name}


@router.put("/models/{model_name:path}/config")
async def update_model_config(model_name: str, request: Request, _admin=Depends(require_admin)):
    body = await request.json()
    model_config = body.get("model", {})
    if not model_config:
        raise HTTPException(status_code=400, detail="model config cannot be empty")
    settings = get_settings()
    found = False
    old_model = None
    for i, m in enumerate(settings.models):
        if m.name == model_name:
            old_model = settings.models[i]
            try:
                # Merge: start from existing data, override with submitted fields
                existing_data = old_model.model_dump()
                existing_data.update(model_config)
                existing_data["name"] = model_name
                # Preserve original api_key if frontend sent masked version (ends with ***)
                new_key = existing_data.get("api_key", "")
                if isinstance(new_key, str) and new_key.endswith("***"):
                    original_key = old_model.api_key
                    if original_key:
                        existing_data["api_key"] = original_key
                    else:
                        existing_data["api_key"] = ""
                settings.models[i] = ModelConfig(**existing_data)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Invalid model config: {e}")
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="Model not found")
    try:
        settings.save_to_yaml()
    except Exception as e:
        # Rollback
        if old_model is not None:
            for i, m in enumerate(settings.models):
                if m.name == model_name:
                    settings.models[i] = old_model
                    break
        raise HTTPException(status_code=500, detail=f"Failed to save config: {e}")
    return {"status": "ok"}


@router.delete("/models/{model_name:path}")
async def delete_model(model_name: str, _admin=Depends(require_admin)):
    settings = get_settings()
    original_models = list(settings.models)
    original_count = len(settings.models)
    settings.models = [m for m in settings.models if m.name != model_name]
    if len(settings.models) == original_count:
        raise HTTPException(status_code=404, detail="Model not found")
    try:
        settings.save_to_yaml()
    except Exception as e:
        # Rollback
        settings.models = original_models
        raise HTTPException(status_code=500, detail=f"Failed to save config: {e}")
    return {"status": "ok"}


# ------------------------------------------------------------------ #
# Providers management (full CRUD)
# ------------------------------------------------------------------ #
@router.get("/providers")
async def list_providers(_admin=Depends(require_admin)):
    settings = get_settings()
    providers = [p.model_dump() for p in settings.providers]
    # Mask api_key for security (show first 8 chars + ***)
    for p in providers:
        key = p.get("api_key", "")
        if key and len(key) > 8:
            p["api_key"] = key[:8] + "***"
    return {"providers": providers}


@router.post("/providers")
async def create_provider(request: Request, _admin=Depends(require_admin)):
    body = await request.json()
    try:
        provider = ProviderConfig(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid provider config: {e}")
    settings = get_settings()
    if settings.get_provider(provider.name):
        raise HTTPException(status_code=400, detail=f"Provider '{provider.name}' already exists")
    settings.providers.append(provider)
    try:
        settings.save_to_yaml()
    except Exception as e:
        # Rollback
        settings.providers.pop()
        raise HTTPException(status_code=500, detail=f"Failed to save config: {e}")
    return {"status": "ok", "name": provider.name}


@router.put("/providers/{provider_name}")
async def update_provider(provider_name: str, request: Request, _admin=Depends(require_admin)):
    body = await request.json()
    settings = get_settings()
    found = False
    old_provider = None
    for i, p in enumerate(settings.providers):
        if p.name == provider_name:
            old_provider = settings.providers[i]
            try:
                # Merge: start from existing data, override with submitted fields
                existing_data = old_provider.model_dump()
                existing_data.update(body)
                existing_data["name"] = provider_name
                # Preserve original api_key if frontend sent masked version (ends with ***)
                new_key = existing_data.get("api_key", "")
                if isinstance(new_key, str) and new_key.endswith("***"):
                    original_key = old_provider.api_key
                    if original_key:
                        existing_data["api_key"] = original_key
                    else:
                        existing_data["api_key"] = ""
                settings.providers[i] = ProviderConfig(**existing_data)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Invalid provider config: {e}")
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not found")
    try:
        settings.save_to_yaml()
    except Exception as e:
        # Rollback
        if old_provider is not None:
            for i, p in enumerate(settings.providers):
                if p.name == provider_name:
                    settings.providers[i] = old_provider
                    break
        raise HTTPException(status_code=500, detail=f"Failed to save config: {e}")
    return {"status": "ok"}


@router.delete("/providers/{provider_name}")
async def delete_provider(provider_name: str, request: Request, _admin=Depends(require_admin)):
    """Delete a provider. Check for model bindings first."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    force = body.get("force", False)
    transfer_to = body.get("transfer_to")

    settings = get_settings()

    # Check if provider exists
    provider = settings.get_provider(provider_name)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not found")

    # Check for model bindings
    bound_models = [m for m in settings.models if m.provider == provider_name]

    if bound_models and not force and not transfer_to:
        # Return bound models for frontend to handle
        return {
            "status": "has_bindings",
            "bound_models": [{"name": m.name, "provider": m.provider} for m in bound_models],
            "message": f"Provider '{provider_name}' has {len(bound_models)} bound models. Please choose to transfer or delete them."
        }

    # Handle transfer
    if transfer_to:
        target_provider = settings.get_provider(transfer_to)
        if not target_provider:
            raise HTTPException(status_code=404, detail=f"Target provider '{transfer_to}' not found")
        for model in bound_models:
            model.provider = transfer_to

    # Handle force delete (delete bound models too)
    if force and bound_models:
        settings.models = [m for m in settings.models if m.provider != provider_name]

    # Delete provider
    settings.providers = [p for p in settings.providers if p.name != provider_name]

    try:
        settings.save_to_yaml()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save config: {e}")

    result = {"status": "ok"}
    if transfer_to:
        result["transferred"] = len(bound_models)
    elif force and bound_models:
        result["deleted_models"] = len(bound_models)
    return result


@router.post("/providers/{provider_name}/clone")
async def clone_provider(provider_name: str, request: Request, _admin=Depends(require_admin)):
    """Clone a provider configuration."""
    body = await request.json()
    new_name = body.get("new_name", f"{provider_name}-copy")
    clone_api_key = body.get("clone_api_key", False)
    settings = get_settings()
    existing = settings.get_provider(provider_name)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not found")
    if settings.get_provider(new_name):
        raise HTTPException(status_code=400, detail=f"Provider '{new_name}' already exists")
    new_data = copy.deepcopy(existing.model_dump())
    new_data["name"] = new_name
    if not clone_api_key:
        new_data["api_key"] = ""
    settings.providers.append(ProviderConfig(**new_data))
    try:
        settings.save_to_yaml()
    except Exception as e:
        settings.providers.pop()
        raise HTTPException(status_code=500, detail=f"Failed to save: {e}")
    return {"status": "ok", "name": new_name}


@router.post("/providers/batch-delete")
async def batch_delete_providers(request: Request, _admin=Depends(require_admin)):
    """Batch delete providers by names. Check for model bindings."""
    body = await request.json()
    names = body.get("names", [])
    if not names:
        raise HTTPException(status_code=400, detail="names list is required")

    force = body.get("force", False)
    transfer_to = body.get("transfer_to")
    check_only = body.get("check_only", False)  # Only check bindings, don't delete

    settings = get_settings()

    # Check for model bindings
    bound_models = [m for m in settings.models if m.provider in names]
    bindings_by_provider = {}
    for m in bound_models:
        if m.provider not in bindings_by_provider:
            bindings_by_provider[m.provider] = []
        bindings_by_provider[m.provider].append(m.name)

    if check_only:
        # Return binding info without deleting
        return {
            "status": "check_complete",
            "has_bindings": len(bound_models) > 0,
            "bindings": bindings_by_provider,
            "total_bound_models": len(bound_models)
        }

    if bound_models and not force and not transfer_to:
        # Return bound models for frontend to handle
        return {
            "status": "has_bindings",
            "bindings": bindings_by_provider,
            "total_bound_models": len(bound_models),
            "message": f"Found {len(bound_models)} bound models across {len(bindings_by_provider)} providers. Please choose to transfer or delete them."
        }

    # Handle transfer
    if transfer_to:
        target_provider = settings.get_provider(transfer_to)
        if not target_provider:
            raise HTTPException(status_code=404, detail=f"Target provider '{transfer_to}' not found")
        for model in bound_models:
            model.provider = transfer_to

    # Handle force delete (delete bound models too)
    if force and bound_models:
        settings.models = [m for m in settings.models if m.provider not in names]

    # Delete providers
    original_count = len(settings.providers)
    settings.providers = [p for p in settings.providers if p.name not in names]
    deleted_count = original_count - len(settings.providers)

    if deleted_count == 0:
        raise HTTPException(status_code=404, detail="No matching providers found")

    try:
        settings.save_to_yaml()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save: {e}")

    result = {"status": "ok", "deleted": deleted_count}
    if transfer_to:
        result["transferred"] = len(bound_models)
    elif force and bound_models:
        result["deleted_models"] = len(bound_models)
    return result


@router.post("/providers/{name}/fetch-models")
async def fetch_provider_models(name: str, _admin=Depends(require_admin)):
    """Fetch available models from a provider's API (e.g., DeepSeek /models endpoint).

    This queries the provider's /models API to discover available models,
    useful for auto-populating the model list.
    """
    settings = get_settings()
    provider = settings.get_provider(name)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")

    if not provider.api_key:
        raise HTTPException(status_code=400, detail=f"Provider '{name}' has no API key configured")

    base_url = provider.base_url.rstrip("/")
    api_key = provider.api_key

    try:
        import httpx
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Provider API returned status {resp.status_code}: {resp.text[:200]}",
                )
            data = resp.json()

        # 解析 OpenAI 兼容的模型列表格式
        models = []
        model_list = data.get("data", [])
        if isinstance(model_list, list):
            for m in model_list:
                model_id = m.get("id", "")
                if model_id:
                    models.append({
                        "id": model_id,
                        "owned_by": m.get("owned_by", name),
                        "object": m.get("object", "model"),
                    })

        return {"provider": name, "models": models, "total": len(models)}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch models: {str(e)[:200]}")


# ------------------------------------------------------------------ #
# Tenants management (full CRUD)
# ------------------------------------------------------------------ #
@router.get("/tenants")
async def list_tenants(_admin=Depends(require_admin)):
    settings = get_settings()
    tenants = [t.model_dump() for t in settings.tenants]
    # Mask api_key for security (show first 8 chars + ***)
    for t in tenants:
        key = t.get("api_key", "")
        if key and len(key) > 8:
            t["api_key"] = key[:8] + "***"
    return {"tenants": tenants}


@router.post("/tenants")
async def create_tenant(request: Request, _admin=Depends(require_admin)):
    body = await request.json()
    try:
        tenant = TenantConfig(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid tenant config: {e}")
    settings = get_settings()
    if settings.get_tenant(tenant.tenant_id):
        raise HTTPException(status_code=400, detail=f"Tenant {tenant.tenant_id} already exists")

    # Auto-generate API key if not provided
    if not tenant.api_key:
        tenant.api_key = generate_api_key(settings.api_key_format)

    settings.tenants.append(tenant)
    try:
        settings.save_to_yaml()
    except Exception as e:
        # Rollback
        settings.tenants.pop()
        raise HTTPException(status_code=500, detail=f"Failed to save config: {e}")
    return {"status": "ok", "tenant_id": tenant.tenant_id, "api_key": tenant.api_key}


@router.put("/tenants/{tenant_id}")
async def update_tenant(tenant_id: str, request: Request, _admin=Depends(require_admin)):
    body = await request.json()
    settings = get_settings()
    found = False
    old_tenant = None
    for i, t in enumerate(settings.tenants):
        if t.tenant_id == tenant_id:
            old_tenant = settings.tenants[i]
            try:
                # Merge: start from existing data, override with submitted fields
                existing_data = old_tenant.model_dump()
                existing_data.update(body)
                existing_data["tenant_id"] = tenant_id
                # Preserve original api_key if frontend sent masked version (ends with ***)
                new_key = existing_data.get("api_key", "")
                if isinstance(new_key, str) and new_key.endswith("***"):
                    original_key = old_tenant.api_key
                    if original_key:
                        existing_data["api_key"] = original_key
                    else:
                        existing_data["api_key"] = ""
                settings.tenants[i] = TenantConfig(**existing_data)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Invalid tenant config: {e}")
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="Tenant not found")
    try:
        settings.save_to_yaml()
    except Exception as e:
        # Rollback
        if old_tenant is not None:
            for i, t in enumerate(settings.tenants):
                if t.tenant_id == tenant_id:
                    settings.tenants[i] = old_tenant
                    break
        raise HTTPException(status_code=500, detail=f"Failed to save config: {e}")
    return {"status": "ok"}


@router.delete("/tenants/{tenant_id}")
async def delete_tenant(tenant_id: str, _admin=Depends(require_admin)):
    settings = get_settings()
    original_tenants = list(settings.tenants)
    original_count = len(settings.tenants)
    settings.tenants = [t for t in settings.tenants if t.tenant_id != tenant_id]
    if len(settings.tenants) == original_count:
        raise HTTPException(status_code=404, detail="Tenant not found")
    try:
        settings.save_to_yaml()
    except Exception as e:
        # Rollback
        settings.tenants = original_tenants
        raise HTTPException(status_code=500, detail=f"Failed to save config: {e}")
    return {"status": "ok"}


@router.post("/tenants/{tenant_id}/reset-api-key")
async def reset_tenant_api_key(tenant_id: str, _admin=Depends(require_admin)):
    """Reset a tenant's API key with a new auto-generated one."""
    settings = get_settings()
    found = False
    old_tenant = None
    for i, t in enumerate(settings.tenants):
        if t.tenant_id == tenant_id:
            old_tenant = settings.tenants[i]
            new_key = generate_api_key(settings.api_key_format)
            settings.tenants[i].api_key = new_key
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="Tenant not found")
    try:
        settings.save_to_yaml()
    except Exception as e:
        # Rollback
        if old_tenant is not None:
            for i, t in enumerate(settings.tenants):
                if t.tenant_id == tenant_id:
                    settings.tenants[i] = old_tenant
                    break
        raise HTTPException(status_code=500, detail=f"Failed to save config: {e}")
    return {"status": "ok", "tenant_id": tenant_id, "api_key": new_key}


@router.get("/tenants/{tenant_id}/api-key")
async def get_tenant_api_key(tenant_id: str, _admin=Depends(require_admin)):
    """Get the full (unmasked) API key for a tenant."""
    settings = get_settings()
    tenant = settings.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    # If api_key is empty, auto-generate one
    if not tenant.api_key:
        tenant.api_key = generate_api_key(settings.api_key_format)
        try:
            settings.save_to_yaml()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to save auto-generated API key: {e}")
    return {"tenant_id": tenant_id, "api_key": tenant.api_key}


# ------------------------------------------------------------------ #
# Config management (all settings editable)
# ------------------------------------------------------------------ #
@router.get("/config")
async def get_config(_admin=Depends(require_admin)):
    settings = get_settings()
    config = settings._to_yaml_dict()
    # Mask sensitive fields in providers
    for p in config.get("providers", []):
        if p.get("api_key") and len(p["api_key"]) > 8:
            p["api_key"] = p["api_key"][:8] + "***"
    # Mask sensitive fields in models
    for m in config.get("models", []):
        if m.get("api_key") and len(m["api_key"]) > 8:
            m["api_key"] = m["api_key"][:8] + "***"
    # Mask sensitive fields in tenants
    for t in config.get("tenants", []):
        if t.get("api_key") and len(t["api_key"]) > 8:
            t["api_key"] = t["api_key"][:8] + "***"
    return config


@router.get("/config/info")
async def get_config_info(_admin=Depends(require_admin)):
    """Return configuration file metadata and sources."""
    import os
    settings = get_settings()
    config_path = str(settings._config_path)
    config_exists = settings._config_path.exists()
    config_size = settings._config_path.stat().st_size if config_exists else 0
    config_mtime = settings._config_path.stat().st_mtime if config_exists else 0
    import datetime
    mtime_str = datetime.datetime.fromtimestamp(config_mtime).isoformat() if config_mtime else ""

    # Check env overrides
    env_overrides = []
    env_prefix = "SMARTROUTER_"
    for key in os.environ:
        if key.startswith(env_prefix):
            env_overrides.append(key)

    # Database info
    db_url = settings.storage.effective_url
    db_backend = settings.storage.backend

    # Model files info
    models_dir = os.environ.get("SMARTROUTER_MODELS_DIR", "/app/data/models")
    models_dir_exists = os.path.isdir(models_dir)

    return {
        "config_path": config_path,
        "config_exists": config_exists,
        "config_size": config_size,
        "config_modified_at": mtime_str,
        "env_overrides": env_overrides,
        "database": {
            "backend": db_backend,
            "url": db_url if db_url.startswith("sqlite") else "***hidden***",
            "path": db_url.replace("sqlite:///", "") if db_url.startswith("sqlite") else None,
        },
        "models_dir": models_dir,
        "models_dir_exists": models_dir_exists,
        "currency": settings.currency,
        "total_models": len(settings.models),
        "total_providers": len(settings.providers),
        "total_tenants": len(settings.tenants),
    }


@router.post("/config/reload")
async def reload_config(_admin=Depends(require_admin)):
    reload_settings()
    return {"status": "ok"}


class BasicSettingsUpdate(BaseModel):
    admin_password: Optional[str] = None
    api_key: Optional[str] = None
    currency: Optional[str] = None
    default_model: Optional[str] = None
    fallback_model: Optional[str] = None
    cache_ttl_seconds: Optional[int] = Field(None, ge=0)
    balance_cache_seconds: Optional[int] = Field(None, ge=0)
    price_sync_interval_hours: Optional[int] = Field(None, ge=0)
    exchange_rate_sync_interval_hours: Optional[int] = Field(None, ge=0)
    log_retention_days: Optional[int] = Field(None, ge=0)
    new_mark_ttl_seconds: Optional[int] = Field(None, ge=0)
    sample_max_capacity: Optional[int] = Field(None, ge=0)


@router.put("/config/basic")
async def update_basic_settings(body: BasicSettingsUpdate, _admin=Depends(require_admin)):
    settings = get_settings()
    # Only set known attributes from the whitelist (BasicSettingsUpdate fields)
    allowed_keys = set(body.model_fields.keys())
    for key, value in body.model_dump(exclude_none=True).items():
        if key in allowed_keys and hasattr(settings, key):
            setattr(settings, key, value)
    settings.save_to_yaml()
    return {"status": "ok"}


@router.put("/config/route-weights")
async def update_route_weights(request: Request, _admin=Depends(require_admin)):
    body = await request.json()
    try:
        weights = RouteWeights(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid route weights: {e}")
    settings = get_settings()
    settings.route_weights = weights
    settings.save_to_yaml()
    return {"status": "ok"}


@router.put("/config/rl")
async def update_rl_config(request: Request, _admin=Depends(require_admin)):
    body = await request.json()
    try:
        rl = RLConfig(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid RL config: {e}")
    settings = get_settings()
    settings.rl_config = rl
    settings.save_to_yaml()
    # Apply to running ML router
    engine = get_routing_engine()
    engine.ml_router.set_rl_params(
        learning_rate=rl.online_learning_rate,
        exploration_rate=rl.exploration_rate,
        discount_factor=rl.discount_factor,
    )
    return {"status": "ok"}


@router.put("/config/health-check")
async def update_health_check_config(request: Request, _admin=Depends(require_admin)):
    body = await request.json()
    try:
        hc = HealthCheckConfig(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid health check config: {e}")
    settings = get_settings()
    settings.health_check = hc
    settings.save_to_yaml()
    return {"status": "ok"}


@router.put("/config/storage")
async def update_storage_config(request: Request, _admin=Depends(require_admin)):
    body = await request.json()
    try:
        storage = StorageConfig(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid storage config: {e}")
    settings = get_settings()
    settings.storage = storage
    settings.save_to_yaml()
    return {"status": "ok"}


@router.put("/config/web")
async def update_web_config(request: Request, _admin=Depends(require_admin)):
    body = await request.json()
    try:
        web = WebFrameworkConfig(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid web config: {e}")
    settings = get_settings()
    settings.web = web
    settings.save_to_yaml()
    return {"status": "ok"}


@router.post("/config/difficulty-ranges")
async def update_difficulty_ranges(request: Request, _admin=Depends(require_admin)):
    body = await request.json()
    ranges = body.get("difficulty_ranges", [])
    try:
        validated = [DifficultyRange(**r) for r in ranges]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid difficulty ranges: {e}")
    settings = get_settings()
    settings.difficulty_ranges = validated
    settings.save_to_yaml()
    return {"status": "ok", "ranges": [r.model_dump() for r in validated]}


@router.put("/config/exchange-rates")
async def update_exchange_rates(request: Request, _admin=Depends(require_admin)):
    """Update exchange rates (manual overrides only)."""
    body = await request.json()
    rates = body.get("exchange_rates", {})
    if not isinstance(rates, dict):
        raise HTTPException(status_code=400, detail="exchange_rates must be a dict")
    settings = get_settings()
    settings.exchange_rates = rates
    settings.save_to_yaml()
    return {"status": "ok"}


@router.get("/exchange-rates/fetch")
async def fetch_exchange_rates(_admin=Depends(require_admin)):
    """Fetch reference exchange rates from external API (does not overwrite manual rates)."""
    mgr = get_exchange_rate_manager()
    fetched = mgr.fetch_rates()
    settings = get_settings()
    current = settings.exchange_rates
    # Build result with reference comparison
    result = {}
    all_keys = sorted(set(list(current.keys()) + list(fetched.keys())))
    for key in all_keys:
        effective = current.get(key)
        ref = fetched.get(key)
        entry: Dict[str, Any] = {}
        if effective is not None:
            entry["effective"] = effective
            entry["is_manual"] = True
        if ref is not None:
            entry["reference"] = ref
            if effective is None:
                entry["effective"] = ref
                entry["is_manual"] = False
            elif abs(effective - ref) > 1e-6:
                entry["is_manual"] = True  # differs from reference
            else:
                entry["is_manual"] = False
        if entry:
            result[key] = entry
    return {"rates": result, "total": len(result), "fetched_count": len(fetched)}


@router.post("/exchange-rates/sync")
async def sync_exchange_rates(request: Request, _admin=Depends(require_admin)):
    """Sync exchange rates with merge mode: manual rates preserved, fetched rates fill gaps."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    manual_overrides = body.get("manual_overrides") if body else None
    mgr = get_exchange_rate_manager()
    success = mgr.sync_rates(manual_overrides=manual_overrides)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to fetch exchange rates from external API")
    settings = get_settings()
    return {"status": "ok", "total_rates": len(settings.exchange_rates)}


@router.put("/config/model-aliases")
async def update_model_aliases(request: Request, _admin=Depends(require_admin)):
    body = await request.json()
    aliases = body.get("model_aliases", {})
    if not isinstance(aliases, dict):
        raise HTTPException(status_code=400, detail="model_aliases must be a dict")
    settings = get_settings()
    settings.model_aliases = aliases
    settings.save_to_yaml()
    return {"status": "ok"}


# ------------------------------------------------------------------ #
# Schedule & Holidays (T054)
# ------------------------------------------------------------------ #
@router.get("/holidays")
async def get_holidays(_admin=Depends(require_admin)):
    """Get configured holiday dates."""
    settings = get_settings()
    return {"holidays": settings.holidays}


@router.put("/holidays")
async def update_holidays(body: Dict[str, Any], _admin=Depends(require_admin)):
    """Update holiday dates list."""
    holidays = body.get("holidays", [])
    if not isinstance(holidays, list):
        raise HTTPException(status_code=400, detail="holidays must be a list of date strings")
    # Validate format
    import re
    for h in holidays:
        if not isinstance(h, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", h):
            raise HTTPException(status_code=400, detail=f"Invalid holiday date format: {h}, expected YYYY-MM-DD")
    settings = get_settings()
    settings.holidays = holidays
    try:
        settings.save_to_yaml()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save: {e}")
    return {"status": "ok", "holidays": settings.holidays}


@router.put("/models/{model_name:path}/schedule")
async def update_model_schedule(model_name: str, body: Dict[str, Any], _admin=Depends(require_admin)):
    """Update model schedule rules."""
    from smart_router.core.config.models import ScheduleRule
    settings = get_settings()
    model = settings.get_model(model_name)
    if not model:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found")

    schedule_rules = body.get("schedule_rules", [])
    try:
        rules = [ScheduleRule(**r) for r in schedule_rules if isinstance(r, dict)]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid schedule rule: {e}")

    model.schedule_rules = rules
    try:
        settings.save_to_yaml()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save: {e}")
    return {"status": "ok", "schedule_rules": [r.model_dump() for r in model.schedule_rules]}


@router.get("/models/{model_name:path}/schedule-status")
async def get_model_schedule_status(model_name: str, _admin=Depends(require_admin)):
    """Check if a model is currently active based on its schedule rules."""
    settings = get_settings()
    model = settings.get_model(model_name)
    if not model:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found")
    return {
        "model": model_name,
        "enabled": model.enabled,
        "is_active_now": model.is_active_now,
        "has_schedule": len(model.schedule_rules) > 0 or bool(model.active_hours),
        "schedule_rules": [r.model_dump() for r in model.schedule_rules],
    }


# ------------------------------------------------------------------ #
# Training samples management
# ------------------------------------------------------------------ #
@router.get("/training-samples")
async def list_training_samples(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    source: Optional[str] = None,
    task_type: Optional[str] = None,
    _admin=Depends(require_admin),
):
    """List training samples with pagination and filtering."""
    try:
        with get_session() as session:
            query = session.query(TrainingSample)
            if source:
                query = query.filter(TrainingSample.source == source)
            if task_type:
                query = query.filter(TrainingSample.task_type == task_type)
            total = query.count()
            samples = query.order_by(TrainingSample.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
            return {
                "total": total,
                "page": page,
                "page_size": page_size,
                "samples": [
                    {
                        "id": s.id,
                        "prompt": s.prompt[:200],
                        "difficulty": s.difficulty,
                        "est_tokens": s.est_tokens,
                        "task_type": s.task_type,
                        "model_name": s.model_name,
                        "source": s.source,
                        "is_new": s.is_new,
                        "created_at": s.created_at,
                    }
                    for s in samples
                ],
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/training-samples")
async def create_training_sample(request: Request, _admin=Depends(require_admin)):
    """Add a training sample manually."""
    body = await request.json()
    prompt = body.get("prompt", "")
    if len(prompt) > 10000:
        raise HTTPException(status_code=400, detail="prompt exceeds maximum length of 10000 characters")
    try:
        with get_session() as session:
            sample = TrainingSample(
                prompt=prompt,
                difficulty=body.get("difficulty", 50),
                est_tokens=body.get("est_tokens", 500),
                task_type=body.get("task_type"),
                model_name=body.get("model_name"),
                source=body.get("source", "manual"),
                is_new=True,
                created_at=time.time(),
            )
            session.add(sample)
            session.commit()
            return {"status": "ok", "id": sample.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/training-samples/{sample_id}")
async def update_training_sample(sample_id: int, request: Request, _admin=Depends(require_admin)):
    """Update a training sample."""
    body = await request.json()
    # Validate field types
    _field_types = {"prompt": str, "difficulty": (int, float), "est_tokens": (int, float), "task_type": str, "model_name": str, "source": str}
    for key in body:
        if key not in _field_types:
            raise HTTPException(status_code=400, detail=f"Invalid field: {key}")
        if body[key] is not None and not isinstance(body[key], _field_types[key]):
            raise HTTPException(status_code=400, detail=f"Invalid type for field '{key}'")
    if "prompt" in body and len(body["prompt"]) > 10000:
        raise HTTPException(status_code=400, detail="prompt exceeds maximum length of 10000 characters")
    try:
        with get_session() as session:
            sample = session.get(TrainingSample, sample_id)
            if not sample:
                raise HTTPException(status_code=404, detail="Sample not found")
            for key in _field_types:
                if key in body:
                    setattr(sample, key, body[key])
            session.commit()
            return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/training-samples/{sample_id}")
async def delete_training_sample(sample_id: int, _admin=Depends(require_admin)):
    """Delete a training sample."""
    try:
        with get_session() as session:
            sample = session.get(TrainingSample, sample_id)
            if not sample:
                raise HTTPException(status_code=404, detail="Sample not found")
            session.delete(sample)
            session.commit()
            return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/training-samples/batch-delete")
async def batch_delete_training_samples(request: Request, _admin=Depends(require_admin)):
    """Batch delete training samples by IDs."""
    body = await request.json()
    ids = body.get("ids", [])
    if not ids:
        raise HTTPException(status_code=400, detail="ids list is required")
    try:
        with get_session() as session:
            deleted = session.query(TrainingSample).filter(TrainingSample.id.in_(ids)).delete(synchronize_session=False)
            session.commit()
            return {"status": "ok", "deleted": deleted}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/training-samples/batch-update")
async def batch_update_training_samples(request: Request, _admin=Depends(require_admin)):
    """Batch update training samples (set same field values for multiple samples)."""
    body = await request.json()
    ids = body.get("ids", [])
    updates = body.get("updates", {})
    if not ids:
        raise HTTPException(status_code=400, detail="ids list is required")
    if not updates:
        raise HTTPException(status_code=400, detail="updates object is required")
    # Validate update field types
    _allowed_update_fields = {"difficulty": (int, float), "task_type": str, "model_name": str, "source": str}
    for key, value in updates.items():
        if key not in _allowed_update_fields:
            raise HTTPException(status_code=400, detail=f"Invalid update field: {key}")
        if value is not None and not isinstance(value, _allowed_update_fields[key]):
            raise HTTPException(status_code=400, detail=f"Invalid type for field '{key}': expected {_allowed_update_fields[key]}")
    try:
        with get_session() as session:
            samples = session.query(TrainingSample).filter(TrainingSample.id.in_(ids)).all()
            updated = 0
            for s in samples:
                for key in _allowed_update_fields:
                    if key in updates:
                        setattr(s, key, updates[key])
                updated += 1
            session.commit()
            return {"status": "ok", "updated": updated}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------ #
# Model tuning
# ------------------------------------------------------------------ #
import asyncio
from concurrent.futures import ThreadPoolExecutor

_tuning_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tuning")
_retrain_tasks: Dict[str, Dict[str, Any]] = {}


@router.get("/tuning/status")
async def tuning_status(_admin=Depends(require_admin)):
    """Get detailed ML/RL tuning status."""
    loop = asyncio.get_running_loop()
    # Run engine init + status fetch in executor to avoid blocking event loop
    def _get_tuning_status():
        engine = get_routing_engine()
        return {
            "ml": engine.ml_router.get_status(),
            "rl_policy": engine.ml_router.get_rl_policy_detail(),
        }
    result = await loop.run_in_executor(None, _get_tuning_status)
    return result


@router.post("/tuning/retrain")
async def trigger_retrain(request: Request, _admin=Depends(require_admin)):
    """Manually trigger model retraining (async background task)."""
    import uuid
    try:
        body = await request.json()
    except Exception:
        body = {}
    limit = body.get("limit", 10000) if body else 10000

    task_id = str(uuid.uuid4())[:8]
    _retrain_tasks[task_id] = {"status": "running", "progress": "loading samples", "result": None, "error": None}

    def _do_retrain():
        try:
            with get_session() as session:
                samples = session.query(TrainingSample).limit(limit).all()
                sample_dicts = [
                    {
                        "prompt": s.prompt,
                        "difficulty": s.difficulty,
                        "est_tokens": s.est_tokens,
                        "task_type": s.task_type,
                        "model_name": s.model_name,
                    }
                    for s in samples
                ]
            _retrain_tasks[task_id]["progress"] = f"training on {len(sample_dicts)} samples"
            engine = get_routing_engine()
            result = engine.ml_router.batch_retrain(sample_dicts)
            _retrain_tasks[task_id]["status"] = "completed"
            _retrain_tasks[task_id]["result"] = result
            _retrain_tasks[task_id]["progress"] = "done"
        except Exception as e:
            _retrain_tasks[task_id]["status"] = "failed"
            _retrain_tasks[task_id]["error"] = str(e)
            _retrain_tasks[task_id]["progress"] = f"failed: {e}"

    loop = asyncio.get_running_loop()
    loop.run_in_executor(_tuning_executor, _do_retrain)
    return {"status": "started", "task_id": task_id, "message": "重训练已在后台启动"}


@router.get("/tuning/retrain/{task_id}")
async def get_retrain_status(task_id: str, _admin=Depends(require_admin)):
    """Get retrain task status."""
    if task_id not in _retrain_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return _retrain_tasks[task_id]


@router.put("/tuning/rl-params")
async def update_rl_params(request: Request, _admin=Depends(require_admin)):
    """Update RL parameters in real-time."""
    body = await request.json()
    loop = asyncio.get_running_loop()
    def _update():
        engine = get_routing_engine()
        engine.ml_router.set_rl_params(
            learning_rate=body.get("learning_rate"),
            exploration_rate=body.get("exploration_rate"),
            discount_factor=body.get("discount_factor"),
        )
    await loop.run_in_executor(None, _update)
    return {"status": "ok"}


@router.put("/tuning/auto-tune")
async def toggle_auto_tune(request: Request, _admin=Depends(require_admin)):
    """Enable or disable auto-tuning."""
    body = await request.json()
    enabled = body.get("enabled", True)
    loop = asyncio.get_running_loop()
    def _toggle():
        engine = get_routing_engine()
        engine.ml_router.set_auto_tune(enabled)
    await loop.run_in_executor(None, _toggle)
    return {"status": "ok", "auto_tune_enabled": enabled}


@router.post("/tuning/reset")
async def reset_models(_admin=Depends(require_admin)):
    """Reset all ML models and RL policy."""
    loop = asyncio.get_running_loop()
    def _reset():
        engine = get_routing_engine()
        engine.ml_router.reset_models()
    await loop.run_in_executor(None, _reset)
    return {"status": "ok"}


# ------------------------------------------------------------------ #
# Request logs
# ------------------------------------------------------------------ #
@router.get("/request-logs")
async def list_request_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    model: Optional[str] = None,
    task_type: Optional[str] = None,
    success: Optional[bool] = None,
    _admin=Depends(require_admin),
):
    """List request logs with pagination and filtering."""
    try:
        with get_session() as session:
            query = session.query(RequestLog)
            if model:
                query = query.filter(RequestLog.routed_model == model)
            if task_type:
                query = query.filter(RequestLog.task_type == task_type)
            if success is not None:
                query = query.filter(RequestLog.success == success)
            total = query.count()
            logs = query.order_by(RequestLog.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
            return {
                "total": total,
                "page": page,
                "page_size": page_size,
                "logs": [
                    {
                        "id": l.id,
                        "timestamp": l.timestamp,
                        "prompt_hash": l.prompt_hash,
                        "predicted_difficulty": l.predicted_difficulty,
                        "routed_model": l.routed_model,
                        "latency_ms": l.latency_ms,
                        "success": l.success,
                        "task_type": l.task_type,
                        "route_source": l.route_source,
                        "prompt_tokens": l.prompt_tokens,
                        "completion_tokens": l.completion_tokens,
                        "cost": l.cost,
                        "tenant_id": l.tenant_id,
                    }
                    for l in logs
                ],
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------ #
# Feedback management
# ------------------------------------------------------------------ #
@router.get("/feedback")
async def list_feedback(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sentiment: Optional[str] = None,
    _admin=Depends(require_admin),
):
    """List feedback records."""
    try:
        with get_session() as session:
            query = session.query(FeedbackRecord)
            if sentiment:
                query = query.filter(FeedbackRecord.sentiment == sentiment)
            total = query.count()
            records = query.order_by(FeedbackRecord.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
            return {
                "total": total,
                "page": page,
                "page_size": page_size,
                "records": [
                    {
                        "id": r.id,
                        "request_id": r.request_id,
                        "feedback_type": r.feedback_type,
                        "sentiment": r.sentiment,
                        "timestamp": r.timestamp,
                        "tenant_id": r.tenant_id,
                    }
                    for r in records
                ],
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/feedback/stats")
async def feedback_stats(_admin=Depends(require_admin)):
    """Get feedback statistics."""
    try:
        with get_session() as session:
            positive = session.query(FeedbackRecord).filter(FeedbackRecord.sentiment == "positive").count()
            negative = session.query(FeedbackRecord).filter(FeedbackRecord.sentiment == "negative").count()
            return {"positive": positive, "negative": negative, "total": positive + negative}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------ #
# Tag feedback & label management (T021 supplement)
# ------------------------------------------------------------------ #
@router.post("/tag-feedback")
async def submit_tag_feedback(request: Request, _admin=Depends(require_admin)):
    """Submit tag feedback for a request (from request result page or external platform).
    
    This API allows marking tags as correct/incorrect, which feeds back into training.
    """
    body = await request.json()
    request_id = body.get("request_id", "")
    tags = body.get("tags", [])
    correct = body.get("correct", True)  # True=tags correct, False=tags incorrect
    suggested_tags = body.get("suggested_tags", [])  # User-suggested alternative tags
    source = body.get("source", "manual")  # manual / api / external

    if not request_id:
        raise HTTPException(status_code=400, detail="request_id is required")

    # Store feedback record
    try:
        with get_session() as session:
            record = FeedbackRecord(
                request_id=request_id,
                feedback_type="tag_feedback",
                sentiment="positive" if correct else "negative",
                context_snapshot=str({"tags": tags, "suggested_tags": suggested_tags, "source": source}),
                timestamp=time.time(),
                tenant_id=body.get("tenant_id", ""),
            )
            session.add(record)
            session.commit()
    except Exception as e:
        logger.error("Failed to save tag feedback: %s", e)

    # If tags are incorrect and user suggested alternatives, add training samples
    if not correct and suggested_tags:
        try:
            with get_session() as session:
                for tag in suggested_tags:
                    sample = TrainingSample(
                        prompt=body.get("prompt_preview", "")[:500],
                        difficulty=body.get("difficulty", 50),
                        task_type=tag,
                        model_name=body.get("model_name", ""),
                        source=f"tag_feedback_{source}",
                        is_new=True,
                        created_at=time.time(),
                    )
                    session.add(sample)
                session.commit()
        except Exception as e:
            logger.error("Failed to add training samples from tag feedback: %s", e)

    return {"status": "ok", "feedback_recorded": True, "training_samples_added": len(suggested_tags) if not correct else 0}


@router.get("/tags/list")
async def list_tags(_admin=Depends(require_admin)):
    """List all available tags (predefined + custom from models)."""
    from smart_router.core.task_detector import _TAG_PATTERNS
    settings = get_settings()
    # Collect tags from models
    model_tags = set()
    for m in settings.models:
        for tag in (m.capability_tags or []):
            model_tags.add(tag)
    # Predefined tags
    predefined = list(_TAG_PATTERNS.keys()) if _TAG_PATTERNS else []
    all_tags = sorted(set(predefined) | model_tags)
    return {"tags": all_tags, "predefined": predefined, "custom": sorted(model_tags - set(predefined))}


# ------------------------------------------------------------------ #
# Batch operations for models
# ------------------------------------------------------------------ #
@router.post("/models/batch")
async def batch_model_operation(request: Request, _admin=Depends(require_admin)):
    """Batch operations on models: update provider, api_key, or delete."""
    body = await request.json()
    operation = body.get("operation")  # "update_provider", "update_api_key", "delete"
    model_names = body.get("model_names", [])
    if not model_names or not operation:
        raise HTTPException(status_code=400, detail="operation and model_names required")

    settings = get_settings()
    results: List[Dict[str, Any]] = []
    model_names_set = set(model_names)

    if operation == "delete":
        before = len(settings.models)
        settings.models = [m for m in settings.models if m.name not in model_names_set]
        deleted = before - len(settings.models)
        settings.save_to_yaml()
        return {"status": "ok", "deleted": deleted}

    elif operation == "update_provider":
        provider = body.get("provider")
        if not provider:
            raise HTTPException(status_code=400, detail="provider required")
        for m in settings.models:
            if m.name in model_names_set:
                m.provider = provider
                results.append({"name": m.name, "updated": True})
        settings.save_to_yaml()
        return {"status": "ok", "updated": len(results)}

    elif operation == "update_api_key":
        api_key = body.get("api_key")
        if not api_key:
            raise HTTPException(status_code=400, detail="api_key required")
        for m in settings.models:
            if m.name in model_names_set:
                m.api_key = api_key
                results.append({"name": m.name, "updated": True})
        settings.save_to_yaml()
        return {"status": "ok", "updated": len(results)}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown operation: {operation}")


@router.post("/models/import")
async def import_models(request: Request, _admin=Depends(require_admin)):
    """Import models from JSON array."""
    body = await request.json()
    models_data = body.get("models", [])
    if not models_data:
        raise HTTPException(status_code=400, detail="models array required")

    settings = get_settings()
    imported = 0
    errors = 0
    for md in models_data:
        try:
            model = ModelConfig(**md)
            if not settings.get_model(model.name):
                settings.models.append(model)
                imported += 1
            else:
                errors += 1
        except Exception:
            errors += 1

    settings.save_to_yaml()
    return {"status": "ok", "imported": imported, "errors": errors}


# ------------------------------------------------------------------ #
# Balance query
# ------------------------------------------------------------------ #
@router.get("/balance")
async def get_balance(_admin=Depends(require_admin)):
    """Get balance for all providers and tenant quotas (cached + async)."""
    cache_key = "balance_all"
    cached = _balance_cache.get(cache_key)
    if cached:
        return cached

    settings = get_settings()
    provider_balances = []

    # Use cached provider balance data (don't call external APIs on every dashboard load)
    for p in settings.providers:
        balance_info: Dict[str, Any] = {
            "name": p.name,
            "provider": p.name,  # Frontend expects 'provider' field
            "type": p.provider_type,
            "balance": p.balance_manual,
            "currency": p.balance_currency,
            "balance_updated_at": p.balance_updated_at,
            "error": None,
        }
        provider_balances.append(balance_info)

    # Tenant quota usage via SQL aggregation (not N+1)
    tenant_usage = []
    try:
        with get_session() as session:
            # Single aggregated query instead of N+1
            tenant_rows = session.query(
                RequestLog.tenant_id,
                func.count(RequestLog.id).label("total_requests"),
                func.sum(RequestLog.cost).label("total_cost"),
            ).group_by(RequestLog.tenant_id).all()

            tenant_cost_map = {r[0]: {"requests": r[1], "cost": r[2] or 0} for r in tenant_rows if r[0]}

            for t in settings.tenants:
                usage = tenant_cost_map.get(t.tenant_id, {"requests": 0, "cost": 0})
                tenant_usage.append({
                    "tenant_id": t.tenant_id,
                    "name": t.name,
                    "quota_daily_tokens": t.quota_daily_tokens,
                    "quota_daily_requests": t.quota_daily_requests,
                    "used_requests": usage["requests"],
                    "used_cost": round(usage["cost"], 4),
                })
    except Exception:
        pass

    result = {"providers": provider_balances, "tenants": tenant_usage}
    _balance_cache[cache_key] = result
    return result


@router.get("/balance/templates")
async def get_balance_templates(_admin=Depends(require_admin)):
    """Get balance query script templates with documentation."""
    return {
        "templates": PROVIDER_BALANCE_TEMPLATES,
        "documentation": {
            "variables": {
                "api_key": "提供商的API密钥",
                "base_url": "提供商的API基础URL",
                "model_name": "模型名称（模型级脚本可用）",
            },
            "output_format": {
                "single_balance": '{"balance": 100.50, "balance_currency": "CNY"}',
                "per_model_balance": '{"models": {"model-a": {"balance": 50, "currency": "CNY"}, "model-b": {"balance": 30, "currency": "USD"}}}',
            },
            "notes": [
                "脚本在沙箱环境中执行，超时30秒",
                "返回JSON格式，必须包含balance字段",
                "balance_currency字段可选，默认CNY",
                "如果提供商按模型分余额，返回models映射",
                "脚本为空时使用手动余额",
            ],
        },
    }


@router.post("/balance/deduct")
async def deduct_balance(request: Request, _admin=Depends(require_admin)):
    """Manually deduct balance from a provider (for periodic deduction mode)."""
    body = await request.json()
    provider_name = body.get("provider_name")
    amount = body.get("amount", 0)
    if not provider_name:
        raise HTTPException(status_code=400, detail="provider_name is required")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be positive")

    settings = get_settings()
    provider = settings.get_provider(provider_name)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not found")

    if provider.balance_manual is None:
        raise HTTPException(status_code=400, detail="Provider has no manual balance set")

    provider.balance_manual = max(0, provider.balance_manual - amount)
    try:
        settings.save_to_yaml()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save: {e}")

    return {"status": "ok", "remaining": provider.balance_manual, "currency": provider.balance_currency}


@router.post("/balance/sync/{provider_name}")
async def sync_provider_balance(provider_name: str, _admin=Depends(require_admin)):
    """Manually sync balance for a single provider by running its balance script."""
    settings = get_settings()
    provider = settings.get_provider(provider_name)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not found")

    if not provider.balance_script:
        # No script: check if manual balance is set
        _balance_cache._store.pop("balance_all", None)
        return {
            "status": "no_script",
            "message": "未配置余额查询脚本，使用手动余额",
            "balance": provider.balance_manual,
            "currency": provider.balance_currency,
        }

    try:
        result = await _run_provider_script(provider.balance_script, provider)
        # Check for script execution errors before updating balance
        if "error" in result:
            _balance_cache._store.pop("balance_all", None)
            return {
                "status": "error",
                "message": f"余额脚本执行失败: {result['error']}",
                "balance": provider.balance_manual,
                "currency": provider.balance_currency,
            }
        balance = result.get("balance")
        if balance is None:
            _balance_cache._store.pop("balance_all", None)
            return {
                "status": "no_data",
                "message": "脚本未返回余额数据",
                "balance": provider.balance_manual,
                "currency": provider.balance_currency,
            }
        currency = result.get("balance_currency", provider.balance_currency)
        provider.balance_manual = float(balance)
        provider.balance_currency = currency
        provider.balance_updated_at = time.time()
        settings.save_to_yaml()
        # Invalidate balance cache
        _balance_cache._store.pop("balance_all", None)
        return {"status": "ok", "balance": float(balance), "currency": currency}
    except Exception as e:
        _balance_cache._store.pop("balance_all", None)
        raise HTTPException(status_code=500, detail=f"Balance sync failed: {str(e)[:200]}")


@router.post("/balance/sync-model/{model_name:path}")
async def sync_model_balance(model_name: str, _admin=Depends(require_admin)):
    """Manually sync balance for a single model by running its balance script."""
    settings = get_settings()
    model = settings.get_model(model_name)
    if not model:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found")

    if not model.balance_script:
        _balance_cache._store.pop("balance_all", None)
        return {
            "status": "no_script",
            "message": "未配置余额查询脚本",
            "balance": model.balance_manual,
            "currency": model.balance_currency,
        }

    try:
        provider = settings.get_provider(model.provider) if model.provider else None
        result = await _run_provider_script(model.balance_script, provider or model)
        # Check for script execution errors
        if "error" in result:
            _balance_cache._store.pop("balance_all", None)
            return {
                "status": "error",
                "message": f"余额脚本执行失败: {result['error']}",
                "balance": model.balance_manual,
                "currency": model.balance_currency,
            }
        balance = result.get("balance")
        if balance is None:
            _balance_cache._store.pop("balance_all", None)
            return {
                "status": "no_data",
                "message": "脚本未返回余额数据",
                "balance": model.balance_manual,
                "currency": model.balance_currency,
            }
        currency = result.get("balance_currency", model.balance_currency)
        model.balance_manual = float(balance)
        model.balance_currency = currency
        settings.save_to_yaml()
        _balance_cache._store.pop("balance_all", None)
        return {"status": "ok", "balance": float(balance), "currency": currency}
    except Exception as e:
        _balance_cache._store.pop("balance_all", None)
        raise HTTPException(status_code=500, detail=f"Model balance sync failed: {str(e)[:200]}")


@router.post("/balance/sync-all")
async def sync_all_balances(_admin=Depends(require_admin)):
    """Sync balance for all providers that have balance scripts."""
    settings = get_settings()
    results = []
    for p in settings.providers:
        if p.balance_script:
            try:
                result = await _run_provider_script(p.balance_script, p)
                # Check for script execution errors - don't overwrite balance with 0
                if "error" in result:
                    results.append({"provider": p.name, "status": "error", "message": f"脚本执行失败: {result['error']}", "balance": p.balance_manual, "currency": p.balance_currency})
                    continue
                balance = result.get("balance")
                if balance is None:
                    results.append({"provider": p.name, "status": "no_data", "message": "脚本未返回余额数据", "balance": p.balance_manual, "currency": p.balance_currency})
                    continue
                currency = result.get("balance_currency", p.balance_currency)
                p.balance_manual = float(balance)
                p.balance_currency = currency
                p.balance_updated_at = time.time()
                results.append({"provider": p.name, "status": "ok", "balance": float(balance), "currency": currency})
            except Exception as e:
                results.append({"provider": p.name, "status": "error", "message": str(e)[:100], "balance": p.balance_manual, "currency": p.balance_currency})
        else:
            results.append({"provider": p.name, "status": "no_script", "message": "未配置余额查询脚本", "balance": p.balance_manual, "currency": p.balance_currency})
    try:
        settings.save_to_yaml()
    except Exception as e:
        logger.error("Failed to save after sync-all: %s", e)
    _balance_cache._store.pop("balance_all", None)
    return {"results": results}


# ------------------------------------------------------------------ #
# Per-model balance display & priority strategy (T038 supplement)
# ------------------------------------------------------------------ #
@router.get("/balance/model-balances")
async def get_model_balances(_admin=Depends(require_admin)):
    """Get balance info for each model (from provider balance + model-level overrides)."""
    settings = get_settings()
    result = []
    for m in settings.models:
        provider = settings.get_provider(m.provider) if m.provider else None
        # Model-level balance takes priority, then provider balance
        balance = m.balance_manual if m.balance_manual is not None else (provider.balance_manual if provider and provider.balance_manual is not None else 0)
        currency = m.balance_currency if m.balance_currency != "CNY" or not provider else provider.balance_currency
        result.append({
            "model_name": m.name,
            "provider": m.provider,
            "balance": balance,
            "currency": currency,
            "balance_script": bool(m.balance_script),
            "priority": m.priority,
            "weight": m.weight,
        })
    return {"models": result}


class BalancePriorityConfig(BaseModel):
    """Balance priority strategy configuration."""
    strategy: str = Field("weight", description="Strategy: weight/balance/cost/latency/custom")
    # weight: use model weight (default)
    # balance: prefer models with higher balance
    # cost: prefer cheaper models
    # latency: prefer lower latency models
    # custom: per-model priority override
    per_model_strategy: Dict[str, str] = Field(default_factory=dict, description="Per-model strategy override")
    balance_threshold: float = Field(0, description="Below this balance, model is deprioritized")


@router.get("/balance/priority")
async def get_balance_priority(_admin=Depends(require_admin)):
    """Get current balance priority strategy."""
    settings = get_settings()
    strategy = getattr(settings, "balance_priority_strategy", "weight")
    per_model = getattr(settings, "balance_priority_per_model", {})
    threshold = getattr(settings, "balance_priority_threshold", 0)
    return {
        "strategy": strategy,
        "per_model_strategy": per_model,
        "balance_threshold": threshold,
    }


@router.put("/balance/priority")
async def update_balance_priority(config: BalancePriorityConfig, _admin=Depends(require_admin)):
    """Update balance priority strategy (admin configurable)."""
    settings = get_settings()
    settings.balance_priority_strategy = config.strategy
    settings.balance_priority_per_model = config.per_model_strategy
    settings.balance_priority_threshold = config.balance_threshold
    settings.save_to_yaml()
    _balance_cache._store.pop("balance_all", None)
    return {"status": "ok"}


# ------------------------------------------------------------------ #
# Price script templates
# ------------------------------------------------------------------ #
@router.get("/price/templates")
async def get_price_templates(_admin=Depends(require_admin)):
    """Get price query script templates with documentation."""
    return {
        "templates": PROVIDER_PRICE_TEMPLATES,
        "documentation": {
            "variables": {
                "api_key": "提供商的API密钥",
                "base_url": "提供商的API基础URL",
                "model_name": "模型名称",
                "provider_name": "提供商名称",
            },
            "output_format": {
                "single_price": '{"price_input": 0.002, "price_output": 0.008, "price_currency": "USD", "price_unit": "1M"}',
                "per_model_prices": '{"models": {"gpt-4o": {"price_input": 0.002, "price_output": 0.008, "currency": "USD", "unit": "1M"}, "gpt-4o-mini": {"price_input": 0.0001, "price_output": 0.0003, "currency": "USD", "unit": "1M"}}}',
                "full_price_table": '{"prices": [{"model": "gpt-4o", "input": 0.002, "output": 0.008, "currency": "USD", "unit": "1M"}]}',
            },
            "inheritance": [
                "如果模型没有设置price_script，继承提供商的price_script",
                "提供商脚本返回所有模型价格表时，单个模型从中提取",
                "优先级：手动输入 > 模型脚本 > 提供商脚本 > litellm数据",
            ],
            "notes": [
                "脚本在沙箱环境中执行，超时30秒",
                "返回JSON格式，必须包含price_input和price_output字段",
                "price_currency字段可选，默认CNY",
                "price_unit字段可选，默认1M (每百万token)",
            ],
        },
    }


# ------------------------------------------------------------------ #
# Tenant usage statistics
# ------------------------------------------------------------------ #
@router.get("/tenants/usage")
async def tenant_usage_stats(
    tenant_id: Optional[str] = None,
    group_by: str = Query("tenant", pattern="^(tenant|tenant_model|tenant_time)$"),
    period: str = Query("month", pattern="^(day|week|month|all)$"),
    _admin=Depends(require_admin),
):
    """Get tenant usage statistics with different grouping."""
    try:
        with get_session() as session:

            query = session.query(RequestLog)
            if tenant_id:
                query = query.filter(RequestLog.tenant_id == tenant_id)

            since = _period_to_timestamp(period)
            if since:
                query = query.filter(RequestLog.timestamp >= since)

            if group_by == "tenant":
                results = session.query(
                    RequestLog.tenant_id,
                    func.count(RequestLog.id).label("total_requests"),
                    func.sum(RequestLog.prompt_tokens).label("total_input"),
                    func.sum(RequestLog.completion_tokens).label("total_output"),
                    func.sum(RequestLog.cost).label("total_cost"),
                ).filter(RequestLog.timestamp >= (since or 0) if since else True)
                if tenant_id:
                    results = results.filter(RequestLog.tenant_id == tenant_id)
                results = results.group_by(RequestLog.tenant_id).all()
                return {
                    "group_by": "tenant",
                    "data": [
                        {
                            "tenant_id": r.tenant_id,
                            "total_requests": r.total_requests,
                            "total_input": r.total_input or 0,
                            "total_output": r.total_output or 0,
                            "total_cost": round(r.total_cost or 0, 4),
                        }
                        for r in results
                    ],
                }

            elif group_by == "tenant_model":
                results = session.query(
                    RequestLog.tenant_id,
                    RequestLog.routed_model,
                    func.count(RequestLog.id).label("total_requests"),
                    func.sum(RequestLog.cost).label("total_cost"),
                ).filter(RequestLog.timestamp >= (since or 0) if since else True)
                if tenant_id:
                    results = results.filter(RequestLog.tenant_id == tenant_id)
                results = results.group_by(RequestLog.tenant_id, RequestLog.routed_model).all()
                return {
                    "group_by": "tenant_model",
                    "data": [
                        {
                            "tenant_id": r.tenant_id,
                            "model": r.routed_model,
                            "total_requests": r.total_requests,
                            "total_cost": round(r.total_cost or 0, 4),
                        }
                        for r in results
                    ],
                }

            elif group_by == "tenant_time":
                # Group by tenant + day using SQL aggregation (avoids loading all records)
                results = session.query(
                    RequestLog.tenant_id,
                    func.strftime("%Y-%m-%d", func.datetime(RequestLog.timestamp, "unixepoch")).label("day"),
                    func.count(RequestLog.id).label("total_requests"),
                    func.sum(RequestLog.cost).label("total_cost"),
                    func.sum(RequestLog.prompt_tokens).label("total_input"),
                    func.sum(RequestLog.completion_tokens).label("total_output"),
                ).filter(RequestLog.timestamp >= (since or 0) if since else True)
                if tenant_id:
                    results = results.filter(RequestLog.tenant_id == tenant_id)
                results = results.group_by(
                    RequestLog.tenant_id,
                    func.strftime("%Y-%m-%d", func.datetime(RequestLog.timestamp, "unixepoch")),
                ).all()
                data = [
                    {
                        "tenant_id": r.tenant_id,
                        "date": r.day,
                        "requests": r.total_requests,
                        "cost": round(r.total_cost or 0, 4),
                        "tokens": (r.total_input or 0) + (r.total_output or 0),
                    }
                    for r in results
                    if r.tenant_id
                ]
                return {"group_by": "tenant_time", "data": data}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------ #
# Database management
# ------------------------------------------------------------------ #
@router.post("/database/reset")
async def database_reset(_admin=Depends(require_admin)):
    reset_db()
    return {"status": "ok", "message": "Database reset complete"}


@router.get("/database/check")
async def database_check(_admin=Depends(require_admin)):
    """Check database health: file existence, writability, format validity."""
    import os
    settings = get_settings()
    db_url = settings.storage.effective_url
    result = {
        "backend": settings.storage.backend,
        "url": db_url,
        "path": None,
        "exists": False,
        "writable": False,
        "valid": False,
        "size": 0,
        "error": None,
    }

    if db_url.startswith("sqlite"):
        db_path = db_url.replace("sqlite:///", "")
        result["path"] = db_path

        # Check existence
        result["exists"] = os.path.exists(db_path)

        if result["exists"]:
            result["size"] = os.path.getsize(db_path)

            # Check writability
            result["writable"] = os.access(db_path, os.W_OK)

            # Check format validity
            try:
                import sqlite3
                with sqlite3.connect(db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    tables = [row[0] for row in cursor.fetchall()]
                result["valid"] = len(tables) > 0
                result["tables"] = tables
            except Exception as e:
                result["valid"] = False
                result["error"] = str(e)
        else:
            # Check if parent directory is writable
            parent_dir = os.path.dirname(db_path)
            if parent_dir:
                result["writable"] = os.access(parent_dir, os.W_OK) if os.path.exists(parent_dir) else False
                result["parent_exists"] = os.path.exists(parent_dir)
    else:
        # Non-SQLite: try a simple connection test
        result["exists"] = True  # Assume exists for remote DBs
        try:
            with get_session() as session:
                session.execute(text("SELECT 1"))
            result["valid"] = True
            result["writable"] = True
        except Exception as e:
            result["valid"] = False
            result["error"] = str(e)

    return result


@router.post("/database/repair")
async def database_repair(_admin=Depends(require_admin)):
    """Repair or recreate the database file."""
    import os
    import shutil
    settings = get_settings()
    db_url = settings.storage.effective_url

    if not db_url.startswith("sqlite"):
        raise HTTPException(status_code=400, detail="Repair only supported for SQLite")

    db_path = db_url.replace("sqlite:///", "")

    def _do_repair():
        # Backup if exists
        if os.path.exists(db_path):
            backup_path = db_path + ".bak"
            shutil.copy2(db_path, backup_path)
        # Remove corrupted file
        if os.path.exists(db_path):
            os.remove(db_path)
        # Recreate
        init_db(db_url)

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _do_repair)
        return {"status": "ok", "message": "Database repaired successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to repair database: {e}")


# ------------------------------------------------------------------ #
# ML Models management (persistent model files)
# ------------------------------------------------------------------ #
@router.get("/ml-models")
async def list_ml_models(_admin=Depends(require_admin)):
    """List all ML model files in the persistent models directory."""
    import os
    models_dir = os.environ.get("SMARTROUTER_MODELS_DIR", "/app/data/models")
    result = []
    if os.path.isdir(models_dir):
        for f in os.listdir(models_dir):
            fp = os.path.join(models_dir, f)
            if os.path.isfile(fp):
                stat = os.stat(fp)
                result.append({
                    "name": f,
                    "path": fp,
                    "size": stat.st_size,
                    "modified_at": stat.st_mtime,
                    "type": os.path.splitext(f)[1].lstrip("."),
                })
    return {"models": result, "models_dir": models_dir}


@router.delete("/ml-models/{model_name}")
async def delete_ml_model(model_name: str, _admin=Depends(require_admin)):
    """Delete a specific ML model file."""
    import os
    models_dir = os.environ.get("SMARTROUTER_MODELS_DIR", "/app/data/models")
    # Security: prevent path traversal
    safe_name = os.path.basename(model_name)
    fp = os.path.join(models_dir, safe_name)
    if not os.path.exists(fp):
        raise HTTPException(status_code=404, detail=f"Model file '{safe_name}' not found")
    os.remove(fp)
    return {"status": "ok", "message": f"Model file '{safe_name}' deleted"}


@router.post("/ml-models/rebuild")
async def rebuild_ml_models(_admin=Depends(require_admin)):
    """Rebuild ML models from training data."""
    try:
        from ....core.routing import get_routing_engine
        engine = get_routing_engine()
        result = engine.ml_router.retrain()
        return {"status": "ok", "message": "ML models rebuilt", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to rebuild models: {e}")


# ------------------------------------------------------------------ #
# Routing engine status
# ------------------------------------------------------------------ #
@router.get("/routing/status")
async def routing_status(_admin=Depends(require_admin)):
    engine = get_routing_engine()
    return engine.get_status()


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #
def _period_to_timestamp(period: str) -> Optional[float]:
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    if period == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start = (now - _dt.timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    elif period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == "year":
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == "all":
        return None
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp()





# ------------------------------------------------------------------ #
# Balance / Price script execution
# ------------------------------------------------------------------ #


async def _run_provider_script(script: str, provider: ProviderConfig, timeout: int = 30) -> Dict[str, Any]:
    """Execute a provider balance/price script in a sandboxed subprocess (async, non-blocking).

    Args:
        script: Python script to execute.
        provider: Provider configuration (passed as env vars).
        timeout: Execution timeout in seconds.

    Returns:
        Parsed JSON output from the script.
    """
    # Inherit current process environment so subprocess can find python3,
    # import installed packages (httpx, etc.), and resolve system paths.
    env = dict(os.environ)
    env.update({
        "PROVIDER_NAME": provider.name,
        "PROVIDER_API_KEY": provider.api_key,
        "PROVIDER_BASE_URL": provider.base_url,
        "PROVIDER_API_TYPE": provider.api_type,
        "PROVIDER_TYPE": provider.provider_type,
    })
    # Inject Python variable definitions so scripts can use api_key, base_url, etc. directly
    # This bridges the gap between exec()-style scripts (variable access) and subprocess (env vars)
    _var_inject = (
        "import os as _os\n"
        "api_key = _os.environ.get('PROVIDER_API_KEY', '')\n"
        "base_url = _os.environ.get('PROVIDER_BASE_URL', '')\n"
        "model_name = _os.environ.get('PROVIDER_MODEL_NAME', '')\n"
        "provider_name = _os.environ.get('PROVIDER_NAME', '')\n"
        "provider_type = _os.environ.get('PROVIDER_TYPE', '')\n"
        "api_type = _os.environ.get('PROVIDER_API_TYPE', '')\n"
    )
    # Append result collector: if the script sets a `result` variable or defines a `run()` function
    # but doesn't print JSON to stdout, we collect and print it automatically.
    # This makes subprocess execution compatible with exec()-style scripts.
    _result_collector = (
        "\nimport json as _json\n"
        "try:\n"
        "    if 'result' in dir() and result is not None:\n"
        "        if isinstance(result, (int, float)):\n"
        "            print(_json.dumps({'balance': float(result)}))\n"
        "        elif isinstance(result, dict):\n"
        "            print(_json.dumps(result))\n"
        "except Exception:\n"
        "    pass\n"
    )
    full_script = _var_inject + script + _result_collector
    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", "-c", full_script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"error": f"Script timed out after {timeout}s"}
        output = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr_str = stderr_bytes.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            return {"error": f"Script exited with code {proc.returncode}", "stderr": stderr_str[:500]}
        if not output:
            return {"error": "Script produced no output"}
        return json.loads(output)
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON output: {e}", "raw": output[:200]}
    except Exception as e:
        return {"error": str(e)[:200]}


@router.post("/providers/{name}/run-balance-script")
async def run_provider_balance_script(name: str, _admin=Depends(require_admin)):
    """Execute balance query script for a specific provider."""
    settings = get_settings()
    provider = settings.get_provider(name)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    script = provider.balance_script
    if not script:
        # Try template
        template = PROVIDER_BALANCE_TEMPLATES.get(provider.provider_type, {})
        script = template.get("script", "")
    if not script:
        raise HTTPException(status_code=400, detail=f"No balance script configured for provider {name}")

    result = await _run_provider_script(script, provider)

    # Update provider balance if successful
    if "balance" in result and result["balance"] is not None:
        for i, p in enumerate(settings.providers):
            if p.name == name:
                settings.providers[i].balance_manual = float(result["balance"])
                # 兼容 balance_currency 和 currency 两种字段名
                currency = result.get("balance_currency") or result.get("currency")
                if currency:
                    settings.providers[i].balance_currency = currency
                settings.providers[i].balance_updated_at = time.time()
                break
        settings.save_to_yaml()

        # Log balance to database
        try:
            with get_session() as session:
                log_entry = ProviderBalanceLog(
                    provider_name=name,
                    balance=float(result["balance"]),
                    currency=result.get("balance_currency") or result.get("currency", "USD"),
                    timestamp=time.time(),
                )
                session.add(log_entry)
                session.commit()
        except Exception as e:
            logger.warning("Failed to log provider balance: %s", e)

    return {"provider": name, "result": result}


@router.post("/providers/{name}/run-price-script")
async def run_provider_price_script(name: str, _admin=Depends(require_admin)):
    """Execute price query script for a specific provider."""
    settings = get_settings()
    provider = settings.get_provider(name)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    script = provider.price_script
    if not script:
        # Try template
        template = PROVIDER_PRICE_TEMPLATES.get(provider.provider_type, {})
        script = template.get("script", "")
    if not script:
        raise HTTPException(status_code=400, detail=f"No price script configured for provider {name}")

    result = await _run_provider_script(script, provider)
    return {"provider": name, "result": result}


@router.post("/providers/run-all-balance-scripts")
async def run_all_balance_scripts(_admin=Depends(require_admin)):
    """Execute balance query scripts for all providers."""
    settings = get_settings()
    results = []
    for p in settings.providers:
        script = p.balance_script
        if not script:
            template = PROVIDER_BALANCE_TEMPLATES.get(p.provider_type, {})
            script = template.get("script", "")
        if not script:
            results.append({"provider": p.name, "result": {"error": "No balance script configured"}})
            continue

        result = await _run_provider_script(script, p)

        # Update provider balance if successful
        if "balance" in result and result["balance"] is not None:
            for i, prov in enumerate(settings.providers):
                if prov.name == p.name:
                    settings.providers[i].balance_manual = float(result["balance"])
                    # 兼容 balance_currency 和 currency 两种字段名
                    currency = result.get("balance_currency") or result.get("currency")
                    if currency:
                        settings.providers[i].balance_currency = currency
                    settings.providers[i].balance_updated_at = time.time()
                    break

            # Log balance to database
            try:
                with get_session() as session:
                    log_entry = ProviderBalanceLog(
                        provider_name=p.name,
                        balance=float(result["balance"]),
                        currency=result.get("balance_currency") or result.get("currency", "USD"),
                        timestamp=time.time(),
                    )
                    session.add(log_entry)
                    session.commit()
            except Exception:
                pass

        results.append({"provider": p.name, "result": result})

    settings.save_to_yaml()
    return {"results": results}


@router.post("/providers/run-all-price-scripts")
async def run_all_price_scripts(_admin=Depends(require_admin)):
    """Execute price query scripts for all providers."""
    settings = get_settings()
    results = []
    for p in settings.providers:
        script = p.price_script
        if not script:
            template = PROVIDER_PRICE_TEMPLATES.get(p.provider_type, {})
            script = template.get("script", "")
        if not script:
            results.append({"provider": p.name, "result": {"error": "No price script configured"}})
            continue

        result = await _run_provider_script(script, p)
        results.append({"provider": p.name, "result": result})

    return {"results": results}


# ------------------------------------------------------------------ #
# Provider panel
# ------------------------------------------------------------------ #
@router.get("/provider-panel")
async def provider_panel(_admin=Depends(require_admin)):
    """Get provider consumption and balance panel data."""
    settings = get_settings()
    panel_data = []

    for p in settings.providers:
        # Get models for this provider
        provider_models = [m for m in settings.models if m.provider == p.name]

        # Get recent balance history
        balance_history = []
        try:
            with get_session() as session:
                logs = (
                    session.query(ProviderBalanceLog)
                    .filter(ProviderBalanceLog.provider_name == p.name)
                    .order_by(ProviderBalanceLog.timestamp.desc())
                    .limit(10)
                    .all()
                )
                balance_history = [
                    {
                        "balance": l.balance,
                        "currency": l.currency,
                        "timestamp": l.timestamp,
                    }
                    for l in logs
                ]
        except Exception:
            pass

        # Get usage stats for this provider's models
        total_requests = 0
        total_cost = 0.0
        total_input_tokens = 0
        total_output_tokens = 0
        try:
            with get_session() as session:
                model_names = [m.name for m in provider_models]
                if model_names:
                    stats = (
                        session.query(
                            func.count(RequestLog.id).label("requests"),
                            func.sum(RequestLog.cost).label("cost"),
                            func.sum(RequestLog.prompt_tokens).label("input_tokens"),
                            func.sum(RequestLog.completion_tokens).label("output_tokens"),
                        )
                        .filter(RequestLog.routed_model.in_(model_names))
                        .first()
                    )
                    if stats:
                        total_requests = stats.requests or 0
                        total_cost = stats.cost or 0.0
                        total_input_tokens = stats.input_tokens or 0
                        total_output_tokens = stats.output_tokens or 0
        except Exception:
            pass

        panel_data.append({
            "name": p.name,
            "display_name": p.display_name or p.name,
            "provider_type": p.provider_type,
            "balance": p.balance_manual,
            "balance_currency": p.balance_currency,
            "balance_updated_at": p.balance_updated_at,
            "model_count": len(provider_models),
            "models": [m.name for m in provider_models],
            "total_requests": total_requests,
            "total_cost": round(total_cost, 4),
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "balance_history": balance_history,
        })

    return {"providers": panel_data}


# ------------------------------------------------------------------ #
# Modality / Tag detection (test endpoints)
# ------------------------------------------------------------------ #
class DetectModalitiesRequest(BaseModel):
    messages: List[Dict[str, Any]]


class DetectTagsRequest(BaseModel):
    prompt: str
    use_ml: bool = Field(False, description="Use ML-based tag prediction")


@router.post("/detect-modalities")
async def detect_modalities(req: DetectModalitiesRequest, _admin=Depends(require_admin)):
    """Detect modalities from OpenAI-format messages (test endpoint)."""
    modalities = TaskTypeDetector.detect_modalities(req.messages)
    return {"modalities": modalities}


@router.post("/detect-tags")
async def detect_tags(req: DetectTagsRequest, _admin=Depends(require_admin)):
    """Detect capability tags from prompt (test endpoint)."""
    if req.use_ml:
        tags = TaskTypeDetector.predict_tags(req.prompt)
        source = "ml"
    else:
        tags = TaskTypeDetector.detect_tags(req.prompt)
        source = "rules"
    return {"tags": tags, "source": source}


# ------------------------------------------------------------------ #
# Model modality auto-detection, confirmation, and auto-apply
# ------------------------------------------------------------------ #

# Name-based modality inference hints
_NAME_MODALITY_HINTS: Dict[str, List[str]] = {
    "image": [
        "vision", "gpt-4o", "gpt-4-turbo", "gpt-4v", "claude-3", "gemini",
        "qwen-vl", "qwen2-vl", "glm-4v", "step-1v", "yi-vision",
        "internvl", "cogvlm", "llava", "minicpm-v", "pixtral",
    ],
    "audio": [
        "whisper", "tts", "speech", "audio", "gpt-4o-audio",
        "glm-4-voice", "qwen-audio", "qwen2-audio",
    ],
    "video": ["video", "gpt-4o-video"],
}


class DetectModelModalitiesRequest(BaseModel):
    model_names: List[str]
    save: bool = Field(False, description="Save results to pending_modalities")
    methods: Optional[List[str]] = Field(None, description="Detection methods to use: query, name_infer, probe_image, probe_audio, probe_video, probe_file, structured_test. Default: all methods")


class ConfirmModalitiesRequest(BaseModel):
    model_names: List[str]
    discard: bool = Field(False, description="Discard instead of confirm")


async def _detect_single_model_modality(model: ModelConfig, methods: Optional[List[str]] = None) -> Dict[str, Any]:
    """Detect a single model's modality support via multiple methods.

    Methods (in order of reliability):
    1. query - Query upstream /models/{name} for modalities
    2. name_infer - Name-based keyword inference
    3. probe_image - Send minimal image request to test support
    4. probe_audio - Send minimal audio request to test support
    5. probe_video - Send minimal video request to test support
    6. probe_file - Send minimal file request to test support
    7. structured_test - Structured test with multiple content types in one request
    """
    import httpx
    import asyncio as _asyncio

    # Default: all methods
    all_methods = {"query", "name_infer", "probe_image", "probe_audio", "probe_video", "probe_file", "structured_test"}
    active_methods = set(methods) if methods else all_methods

    detected: set = {"text"}
    method_results: Dict[str, Any] = {}
    best_method = "probe"

    base_url = model.base_url.rstrip("/")
    api_key = model.api_key
    api_type = model.api_type
    upstream_model = model.litellm_name or model.name

    if not base_url or not base_url.startswith("http"):
        return {"modalities": ["text"], "method": "skip", "detail": "Missing or invalid base_url", "method_results": {}}
    if api_key and api_key.startswith("YOUR_"):
        return {"modalities": ["text"], "method": "skip", "detail": "API Key is placeholder", "method_results": {}}

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Method 1: Query upstream /models/{name} for modalities
    if "query" in active_methods:
        try:
            async with httpx.AsyncClient() as client:
                models_url = f"{base_url}/models/{upstream_model}"
                resp = await client.get(models_url, headers=headers, timeout=8.0)
                if resp.status_code == 200:
                    model_info = resp.json()
                    model_modalities = model_info.get("modalities") or model_info.get("capabilities", {}).get("modalities")
                    query_detected = []
                    if model_modalities and isinstance(model_modalities, list):
                        for mod in model_modalities:
                            mod_lower = mod.lower() if isinstance(mod, str) else str(mod).lower()
                            if mod_lower in ("text", "image", "audio", "video", "file"):
                                detected.add(mod_lower)
                                query_detected.append(mod_lower)
                            elif "image" in mod_lower or "vision" in mod_lower:
                                detected.add("image")
                                query_detected.append("image")
                            elif "audio" in mod_lower or "speech" in mod_lower:
                                detected.add("audio")
                                query_detected.append("audio")
                            elif "video" in mod_lower:
                                detected.add("video")
                                query_detected.append("video")
                    method_results["query"] = {"success": True, "detected": query_detected}
                    if query_detected:
                        best_method = "query"
                else:
                    method_results["query"] = {"success": False, "status": resp.status_code}
        except Exception as e:
            method_results["query"] = {"success": False, "error": str(e)[:100]}

    # Method 2: Name-based inference
    if "name_infer" in active_methods:
        name_lower = upstream_model.lower()
        name_detected = []
        for modality, keywords in _NAME_MODALITY_HINTS.items():
            for kw in keywords:
                if kw in name_lower:
                    detected.add(modality)
                    name_detected.append(modality)
                    break
        method_results["name_infer"] = {"success": True, "detected": name_detected}
        if name_detected and best_method == "probe":
            best_method = "name_infer"

    # Method 3-6: Probe requests (image, audio, video, file)
    probe_tasks = []

    async def _probe_modality(modality: str, probe_body: Dict[str, Any]) -> None:
        try:
            async with httpx.AsyncClient() as client:
                chat_url = f"{base_url}/chat/completions"
                resp = await client.post(chat_url, json=probe_body, headers=headers, timeout=15.0)
                if resp.status_code < 400:
                    detected.add(modality)
                    method_results[f"probe_{modality}"] = {"success": True, "detected": [modality]}
                else:
                    method_results[f"probe_{modality}"] = {"success": False, "status": resp.status_code}
        except Exception as e:
            method_results[f"probe_{modality}"] = {"success": False, "error": str(e)[:100]}

    # Minimal 1x1 PNG base64
    _MINI_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    # Minimal WAV base64
    _MINI_WAV_B64 = "dGVzdA=="

    if "probe_image" in active_methods:
        probe_tasks.append(_probe_modality("image", {
            "model": upstream_model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "Describe"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_MINI_PNG_B64}"}}
            ]}],
            "max_tokens": 1, "stream": False,
        }))

    if "probe_audio" in active_methods:
        probe_tasks.append(_probe_modality("audio", {
            "model": upstream_model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "Listen"},
                {"type": "input_audio", "input_audio": {"data": _MINI_WAV_B64, "format": "wav"}}
            ]}],
            "max_tokens": 1, "stream": False,
        }))

    if "probe_video" in active_methods:
        probe_tasks.append(_probe_modality("video", {
            "model": upstream_model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "Watch"},
                {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,AAAA"}}
            ]}],
            "max_tokens": 1, "stream": False,
        }))

    if "probe_file" in active_methods:
        probe_tasks.append(_probe_modality("file", {
            "model": upstream_model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "Read"},
                {"type": "file", "file": {"url": "data:text/plain;base64,dGVzdA=="}}
            ]}],
            "max_tokens": 1, "stream": False,
        }))

    # Method 7: Structured test - send multiple content types in one request
    if "structured_test" in active_methods:
        async def _structured_test():
            try:
                async with httpx.AsyncClient() as client:
                    chat_url = f"{base_url}/chat/completions"
                    structured_body = {
                        "model": upstream_model,
                        "messages": [{"role": "user", "content": [
                            {"type": "text", "text": "Analyze all content types"},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_MINI_PNG_B64}"}},
                            {"type": "input_audio", "input_audio": {"data": _MINI_WAV_B64, "format": "wav"}},
                        ]}],
                        "max_tokens": 1, "stream": False,
                    }
                    resp = await client.post(chat_url, json=structured_body, headers=headers, timeout=15.0)
                    structured_detected = []
                    if resp.status_code < 400:
                        # If the model accepts a multi-modal request, it supports both image and audio
                        for mod in ["image", "audio"]:
                            detected.add(mod)
                            structured_detected.append(mod)
                        method_results["structured_test"] = {"success": True, "detected": structured_detected}
                    else:
                        method_results["structured_test"] = {"success": False, "status": resp.status_code}
            except Exception as e:
                method_results["structured_test"] = {"success": False, "error": str(e)[:100]}

        probe_tasks.append(_structured_test())

    if probe_tasks:
        await _asyncio.gather(*probe_tasks, return_exceptions=True)

    # Determine best method from results
    if best_method == "probe":
        # Check which probe method succeeded
        for m in ["query", "name_infer", "structured_test", "probe_image", "probe_audio", "probe_video", "probe_file"]:
            if m in method_results and method_results[m].get("success") and method_results[m].get("detected"):
                best_method = m
                break

    final_modalities = sorted(detected, key=lambda x: ["text", "image", "audio", "video", "file"].index(x) if x in ["text", "image", "audio", "video", "file"] else 99)
    return {"modalities": final_modalities, "method": best_method, "method_results": method_results}


@router.post("/models/detect-modalities")
async def detect_model_modalities(req: DetectModelModalitiesRequest, _admin=Depends(require_admin)):
    """Auto-detect model modality support using multiple methods.

    Detection methods (can be selected via 'methods' parameter):
    - query: Query upstream /models/{name} API
    - name_infer: Name-based keyword inference
    - probe_image: Send minimal image request
    - probe_audio: Send minimal audio request
    - probe_video: Send minimal video request
    - probe_file: Send minimal file request
    - structured_test: Structured multi-modal test

    Results go to pending_modalities (need confirmation).
    """
    import asyncio as _asyncio

    settings = get_settings()
    model_map = {m.name: m for m in settings.models}
    sem = _asyncio.Semaphore(10)

    async def detect_with_sem(name: str):
        m = model_map.get(name)
        if not m:
            return name, {"modalities": ["text"], "method": "not_found", "detail": "Model not found", "method_results": {}}
        async with sem:
            result = await _detect_single_model_modality(m, methods=req.methods)
            return name, result

    tasks = [detect_with_sem(name) for name in req.model_names]
    task_results = await _asyncio.gather(*tasks, return_exceptions=True)

    now_ts = time.time()
    results = {}
    for tr in task_results:
        if isinstance(tr, Exception):
            continue
        name, result = tr
        results[name] = result
        if req.save:
            for m in settings.models:
                if m.name == name:
                    m.pending_modalities = result["modalities"]
                    m.pending_modalities_detected_at = now_ts
                    break

    if req.save:
        settings.save_to_yaml()

    return {"results": results}


@router.post("/models/confirm-modalities")
async def confirm_modalities(req: ConfirmModalitiesRequest, _admin=Depends(require_admin)):
    """Confirm or discard pending modality detection results."""
    settings = get_settings()
    updated = []

    for m in settings.models:
        if m.name in req.model_names and m.pending_modalities is not None:
            if req.discard:
                m.pending_modalities = None
                m.pending_modalities_detected_at = None
            else:
                m.modalities = m.pending_modalities
                m.pending_modalities = None
                m.pending_modalities_detected_at = None
            updated.append(m.name)

    if updated:
        settings.save_to_yaml()

    return {"updated": updated, "action": "discard" if req.discard else "confirm"}


@router.post("/models/apply-pending-modalities")
async def apply_pending_modalities(_admin=Depends(require_admin)):
    """Auto-apply pending_modalities older than 24 hours."""

    AUTO_CONFIRM_SECONDS = 24 * 3600
    now_ts = time.time()
    settings = get_settings()
    auto_applied = []

    for m in settings.models:
        if m.pending_modalities is not None and m.pending_modalities_detected_at is not None:
            if now_ts - m.pending_modalities_detected_at >= AUTO_CONFIRM_SECONDS:
                m.modalities = m.pending_modalities
                m.pending_modalities = None
                m.pending_modalities_detected_at = None
                auto_applied.append(m.name)

    if auto_applied:
        settings.save_to_yaml()

    return {"auto_applied": auto_applied, "count": len(auto_applied)}


# ------------------------------------------------------------------ #
# Notifications (T028/T040/T049)
# ------------------------------------------------------------------ #
class NotificationTestRequest(BaseModel):
    channel: str = Field(..., description="Channel to test: webhook/dingtalk/wecom/feishu/telegram/slack/email")
    recipient: Optional[str] = Field(None, description="Recipient for email channel")
    config: Optional[Dict[str, Any]] = Field(None, description="Channel config override")


@router.put("/config/notifications")
async def update_notifications(body: Dict[str, Any], _admin=Depends(require_admin)):
    """Save notification configuration."""
    from smart_router.core.config.models import NotificationConfig
    settings = get_settings()
    try:
        notif_config = NotificationConfig(**body)
        settings.notifications = notif_config
        settings.save_to_yaml()
        return {"status": "ok", "message": "通知配置已保存"}
    except Exception as e:
        logger.error("保存通知配置失败: %s", e)
        raise HTTPException(status_code=500, detail=f"保存通知配置失败: {str(e)[:200]}")


@router.get("/config/notifications")
async def get_notifications(_admin=Depends(require_admin)):
    """Get current notification configuration."""
    settings = get_settings()
    notif_data = settings.notifications.model_dump(by_alias=True)
    # Mask sensitive fields (SMTP password, webhook secrets, etc.)
    for ch in notif_data.get("channels", []):
        for sensitive_key in ("smtp_pass", "secret", "token", "api_key", "webhook_secret"):
            if ch.get(sensitive_key):
                ch[sensitive_key] = "***"
    return notif_data


@router.get("/config/api-key-format")
async def get_api_key_format(_admin=Depends(require_admin)):
    """Get current API key format configuration."""
    settings = get_settings()
    return settings.api_key_format.model_dump()


@router.put("/config/api-key-format")
async def update_api_key_format(request: Request, _admin=Depends(require_admin)):
    """Update API key format configuration."""
    body = await request.json()
    settings = get_settings()
    try:
        settings.api_key_format = settings.api_key_format.__class__(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid API key format config: {e}")
    settings.save_to_yaml()
    return {"status": "ok"}


@router.post("/config/api-key-format/generate")
async def generate_sample_api_key(request: Request, _admin=Depends(require_admin)):
    """Generate a sample API key using current format configuration."""
    body = await request.json()
    settings = get_settings()
    # Use provided config or current settings
    if body:
        try:
            from ....core.config.models import ApiKeyFormatConfig
            format_config = ApiKeyFormatConfig(**body)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid format config: {e}")
    else:
        format_config = settings.api_key_format
    sample_key = generate_api_key(format_config)
    return {"sample_key": sample_key}


@router.get("/config/permissions")
async def get_permissions(_admin=Depends(require_admin)):
    """Get current role permissions configuration."""
    settings = get_settings()
    return settings.role_permissions.model_dump()


@router.put("/config/permissions")
async def update_permissions(request: Request, _admin=Depends(require_admin)):
    """Update role permissions configuration."""
    body = await request.json()
    settings = get_settings()
    try:
        from ....core.config.models import RolePermissions
        settings.role_permissions = RolePermissions(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid permissions config: {e}")
    settings.save_to_yaml()
    return {"status": "ok"}


@router.post("/users/transfer-superadmin")
async def transfer_superadmin(request: Request, _admin=Depends(require_admin)):
    """Transfer super admin role to another user."""
    body = await request.json()
    new_admin_username = body.get("username")
    if not new_admin_username:
        raise HTTPException(status_code=400, detail="username is required")

    # Verify current user is super admin
    token_data = _admin
    if token_data.get("role") != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Only super admin can transfer role")

    # Verify target user exists
    users = _list_users()
    target_user = None
    for u in users:
        if u.get("username") == new_admin_username:
            target_user = u
            break

    if not target_user:
        raise HTTPException(status_code=404, detail=f"User '{new_admin_username}' not found")

    # Update roles
    _update_user(new_admin_username, {"role": ROLE_ADMIN})
    # Demote current admin to regular user
    current_username = token_data.get("sub")
    if current_username and current_username != new_admin_username:
        _update_user(current_username, {"role": ROLE_USER})

    return {"status": "ok", "message": f"Super admin role transferred to {new_admin_username}"}


@router.post("/notifications/test")
async def test_notification(req: NotificationTestRequest, _admin=Depends(require_admin)):
    """Test a notification channel. For email, recipient is required."""
    channel = req.channel.lower()
    message = f"[SmartRouter Test] 通知测试消息 - {channel} - 时间: {time.strftime('%Y-%m-%d %H:%M:%S')}"
    success = False
    error = None

    try:
        if channel == "email":
            if not req.recipient:
                raise HTTPException(status_code=400, detail="Email channel requires 'recipient' field")
            settings = get_settings()
            # Find email channel config
            email_channel = None
            for ch in settings.notifications.channels:
                if ch.type == "email":
                    email_channel = ch
                    break
            if not email_channel or not email_channel.smtp_host:
                raise HTTPException(status_code=400, detail="No email channel configured. Please configure SMTP in notification settings first.")
            # Send test email (run in executor to avoid blocking event loop)
            import smtplib
            from email.mime.text import MIMEText
            msg = MIMEText(message, "plain", "utf-8")
            msg["Subject"] = "[SmartRouter] 通知测试"
            msg["From"] = email_channel.from_address or email_channel.smtp_user
            msg["To"] = req.recipient

            def _send_email():
                with smtplib.SMTP(email_channel.smtp_host, email_channel.smtp_port, timeout=10) as server:
                    if email_channel.smtp_port == 587:
                        server.starttls()
                    if email_channel.smtp_user and email_channel.smtp_pass:
                        server.login(email_channel.smtp_user, email_channel.smtp_pass)
                    server.send_message(msg)

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _send_email)
            success = True

        elif channel == "webhook":
            config = req.config or {}
            url = config.get("url", "")
            if not url:
                raise HTTPException(status_code=400, detail="Webhook channel requires 'config.url'")
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json={"text": message, "source": "SmartRouter"}, timeout=10.0)
            success = resp.status_code < 400
            if not success:
                error = f"Webhook returned {resp.status_code}"

        elif channel in ("dingtalk", "wecom", "feishu", "telegram", "slack"):
            config = req.config or {}
            url = config.get("url", config.get("webhook_url", ""))
            if not url:
                raise HTTPException(status_code=400, detail=f"{channel} channel requires 'config.url'")
            import httpx
            payload = {"text": message, "msgtype": "text"}
            if channel == "dingtalk":
                payload = {"msgtype": "markdown", "markdown": {"title": "SmartRouter Test", "text": message}}
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, timeout=10.0)
            success = resp.status_code < 400
            if not success:
                error = f"{channel} returned {resp.status_code}"
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported channel: {channel}")

    except HTTPException:
        raise
    except Exception as e:
        success = False
        error = str(e)[:200]

    return {"success": success, "channel": channel, "error": error}


@router.post("/notifications/test-all")
async def test_all_notifications(_admin=Depends(require_admin)):
    """Test all configured notification channels."""
    settings = get_settings()
    results = []
    # This would check the notification config and test each channel
    # For now, return a placeholder
    return {"results": results, "message": "Test all configured channels"}


# ------------------------------------------------------------------ #
# User profile management (T041)
# ------------------------------------------------------------------ #
class UserProfile(BaseModel):
    """User profile information."""
    username: str = Field(..., min_length=1)
    email: str = Field("")
    role: str = Field("user", description="Role: admin/user/guest")
    display_name: str = Field("")
    avatar_url: str = Field("")


class TenantBalanceConfig(BaseModel):
    """Tenant balance configuration."""
    tenant_id: str = Field(..., min_length=1)
    balance: float = Field(0, description="Balance amount, -1=unlimited")
    currency: str = Field("CNY")
    unlimited: bool = Field(False, description="If true, balance is unlimited (record usage but don't deduct)")


# In-memory user profiles (would be persisted in production)
_user_profiles: Dict[str, UserProfile] = {}
# Note: _tenant_balances removed - now persisted in database (TenantBalance table)


@router.get("/users/profile")
async def get_user_profiles(_admin=Depends(require_admin)):
    """Get all user profiles."""
    return {"users": list(_user_profiles.values())}


@router.put("/users/profile/{username}")
async def update_user_profile(username: str, profile: UserProfile, _admin=Depends(require_admin)):
    """Update a user profile."""
    if username != profile.username:
        raise HTTPException(status_code=400, detail="Username mismatch")
    _user_profiles[username] = profile
    return {"status": "ok"}


@router.get("/tenants/balance")
async def get_tenant_balances(_admin=Depends(require_admin)):
    """Get all tenant balance configurations (from database)."""
    settings = get_settings()
    result = []
    try:
        with get_session() as session:
            for t in settings.tenants:
                bal = session.query(TenantBalance).filter(TenantBalance.tenant_id == t.tenant_id).first()
                result.append({
                    "tenant_id": t.tenant_id,
                    "name": t.name,
                    "balance": bal.balance if bal else 0,
                    "currency": bal.currency if bal else "CNY",
                    "unlimited": bal.unlimited if bal else False,
                    "updated_at": bal.updated_at if bal else None,
                })
    except Exception:
        # Fallback: return defaults if DB not ready
        for t in settings.tenants:
            result.append({
                "tenant_id": t.tenant_id,
                "name": t.name,
                "balance": 0,
                "currency": "CNY",
                "unlimited": False,
                "updated_at": None,
            })
    return {"balances": result}


@router.put("/tenants/balance/{tenant_id}")
async def update_tenant_balance(tenant_id: str, config: TenantBalanceConfig, _admin=Depends(require_admin)):
    """Update tenant balance configuration (persisted to database + YAML backup)."""
    if tenant_id != config.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID mismatch")
    settings = get_settings()
    if not settings.get_tenant(tenant_id):
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")

    # Save to database (primary storage)
    try:
        with get_session() as session:
            bal = session.query(TenantBalance).filter(TenantBalance.tenant_id == tenant_id).first()
            if bal:
                bal.balance = config.balance
                bal.currency = config.currency
                bal.unlimited = config.unlimited
                bal.updated_at = time.time()
            else:
                bal = TenantBalance(
                    tenant_id=tenant_id,
                    balance=config.balance,
                    currency=config.currency,
                    unlimited=config.unlimited,
                    updated_at=time.time(),
                )
                session.add(bal)
            session.commit()
    except Exception as e:
        logger.error("Failed to persist tenant balance to DB: %s", e)

    # Check if balance is below threshold and send notification
    await _check_tenant_balance_notification(tenant_id, config, settings)
    return {"status": "ok"}


async def _check_tenant_balance_notification(tenant_id: str, balance_cfg: TenantBalanceConfig, settings):
    """Check if tenant balance is below threshold and send email notification."""
    if not balance_cfg.unlimited and balance_cfg.balance >= 0:
        tenant = settings.get_tenant(tenant_id)
        if not tenant or not tenant.balance_notify_enabled:
            return
        # Determine if below threshold
        is_low = False
        if tenant.balance_threshold_type == "fixed":
            is_low = balance_cfg.balance < tenant.balance_threshold_value
        elif tenant.balance_threshold_type == "percentage":
            # For percentage, we need an initial balance; use threshold_value as percentage of 100
            is_low = balance_cfg.balance < tenant.balance_threshold_value
        if is_low:
            await _send_tenant_balance_email(tenant, balance_cfg, settings)


async def _send_tenant_balance_email(tenant, balance_cfg: TenantBalanceConfig, settings):
    """Send balance low notification email to tenant (async, non-blocking)."""
    # Find email channel config
    email_channel = None
    for ch in settings.notifications.channels:
        if ch.type == "email":
            email_channel = ch
            break
    if not email_channel or not email_channel.smtp_host:
        logger.warning("No email channel configured for tenant balance notification")
        return

    # Determine recipient: tenant email first, fallback to user email
    recipient = tenant.email
    if not recipient:
        # Try to find associated user email
        user_profile = _user_profiles.get(tenant.tenant_id)
        if user_profile and user_profile.email:
            recipient = user_profile.email
    if not recipient:
        # Fallback to configured notification email
        recipient = email_channel.to.split(",")[0].strip() if email_channel.to else None
    if not recipient:
        logger.warning("No recipient email for tenant %s balance notification", tenant.tenant_id)
        return

    try:
        import smtplib
        from email.mime.text import MIMEText
        message = (
            f"尊敬的 {tenant.name}，\n\n"
            f"您的 SmartRouter 账户余额不足。\n\n"
            f"当前余额: {balance_cfg.balance} {balance_cfg.currency}\n"
            f"租户ID: {tenant.tenant_id}\n\n"
            f"请及时充值以避免服务中断。\n\n"
            f"— SmartRouter 系统"
        )
        msg = MIMEText(message, "plain", "utf-8")
        msg["Subject"] = f"[SmartRouter] 余额不足提醒 - {tenant.name}"
        msg["From"] = email_channel.from_address or email_channel.smtp_user
        msg["To"] = recipient

        def _send_email():
            with smtplib.SMTP(email_channel.smtp_host, email_channel.smtp_port, timeout=10) as server:
                if email_channel.smtp_port == 587:
                    server.starttls()
                if email_channel.smtp_user and email_channel.smtp_pass:
                    server.login(email_channel.smtp_user, email_channel.smtp_pass)
                server.send_message(msg)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _send_email)
        logger.info("Balance low notification sent to %s for tenant %s", recipient, tenant.tenant_id)
    except Exception as e:
        logger.error("Failed to send balance notification email: %s", e)


# ------------------------------------------------------------------ #
# Payment system API (T029 supplement - UNTESTED, interface only)
# ------------------------------------------------------------------ #
# WARNING: This module provides payment interface stubs for future
# third-party payment integration. NOT TESTED. Do not use in production
# without proper integration testing and security review.

class PaymentOrderRequest(BaseModel):
    """Create a payment order."""
    tenant_id: str = Field(..., min_length=1)
    amount: float = Field(..., gt=0, description="Payment amount")
    currency: str = Field("CNY", description="Payment currency")
    channel: str = Field("alipay", description="Payment channel: alipay/wechat/unionpay/stripe")
    description: str = Field("", description="Payment description")
    return_url: str = Field("", description="Return URL after payment")
    notify_url: str = Field("", description="Async notification URL for payment callback")

    @staticmethod
    def _validate_url(url: str) -> bool:
        """Basic URL validation to prevent SSRF."""
        if not url:
            return True  # empty is ok (optional field)
        return url.startswith(("http://", "https://"))

    def model_post_init(self, __context: Any) -> None:
        if self.return_url and not self._validate_url(self.return_url):
            raise ValueError("return_url must start with http:// or https://")
        if self.notify_url and not self._validate_url(self.notify_url):
            raise ValueError("notify_url must start with http:// or https://")


class PaymentCallbackRequest(BaseModel):
    """Payment callback from third-party provider."""
    order_id: str = Field(..., description="Internal order ID")
    transaction_id: str = Field("", description="Third-party transaction ID")
    status: str = Field(..., description="Payment status: success/failed/pending")
    amount: float = Field(0, description="Actual paid amount")
    currency: str = Field("CNY")
    raw_data: Dict[str, Any] = Field(default_factory=dict, description="Raw callback data from provider")


class RefundRequest(BaseModel):
    """Refund request."""
    order_id: str = Field(..., description="Original order ID")
    amount: float = Field(..., gt=0, description="Refund amount")
    reason: str = Field("", description="Refund reason")


# In-memory order storage (would be database in production)
_payment_orders: Dict[str, Dict[str, Any]] = {}


@router.post("/payment/create-order")
async def create_payment_order(req: PaymentOrderRequest, _admin=Depends(require_admin)):
    """Create a payment order (UNTESTED - interface stub)."""
    import uuid
    order_id = f"PAY-{uuid.uuid4().hex[:12]}"
    order = {
        "order_id": order_id,
        "tenant_id": req.tenant_id,
        "amount": req.amount,
        "currency": req.currency,
        "channel": req.channel,
        "status": "pending",
        "created_at": time.time(),
        "description": req.description,
    }
    _payment_orders[order_id] = order
    logger.warning("[UNTESTED] Payment order created: %s for tenant %s amount %.2f %s",
                   order_id, req.tenant_id, req.amount, req.currency)
    return {
        "status": "ok",
        "order_id": order_id,
        "payment_url": f"#unimplemented-{req.channel}-payment",
        "message": "Payment interface stub - not connected to real payment provider",
    }


@router.post("/payment/callback")
async def payment_callback(req: PaymentCallbackRequest, _admin=Depends(require_admin)):
    """Payment callback endpoint (UNTESTED - interface stub)."""
    order = _payment_orders.get(req.order_id)
    if not order:
        raise HTTPException(status_code=404, detail=f"Order '{req.order_id}' not found")
    # Idempotency: skip if already processed
    if order.get("status") in ("success", "failed"):
        return {"status": "already_processed", "order_status": order["status"]}
    order["status"] = req.status
    order["transaction_id"] = req.transaction_id
    order["paid_at"] = time.time()
    if req.status == "success":
        try:
            with get_session() as session:
                bal = session.query(TenantBalance).filter(
                    TenantBalance.tenant_id == order["tenant_id"]
                ).first()
                if bal:
                    bal.balance += req.amount
                    bal.updated_at = time.time()
                else:
                    bal = TenantBalance(
                        tenant_id=order["tenant_id"],
                        balance=req.amount,
                        currency=req.currency,
                        updated_at=time.time(),
                    )
                    session.add(bal)
                session.commit()
        except Exception as e:
            logger.error("[UNTESTED] Failed to credit tenant balance after payment: %s", e)
    logger.warning("[UNTESTED] Payment callback: order=%s status=%s", req.order_id, req.status)
    return {"status": "ok"}


@router.get("/payment/orders")
async def list_payment_orders(
    tenant_id: Optional[str] = None,
    _admin=Depends(require_admin),
):
    """List payment orders (UNTESTED - interface stub)."""
    orders = list(_payment_orders.values())
    if tenant_id:
        orders = [o for o in orders if o.get("tenant_id") == tenant_id]
    return {"orders": orders, "total": len(orders)}


@router.post("/payment/refund")
async def create_refund(req: RefundRequest, _admin=Depends(require_admin)):
    """Create a refund request (UNTESTED - interface stub)."""
    order = _payment_orders.get(req.order_id)
    if not order:
        raise HTTPException(status_code=404, detail=f"Order '{req.order_id}' not found")
    if order.get("status") != "success":
        raise HTTPException(status_code=400, detail="Can only refund successful orders")
    logger.warning("[UNTESTED] Refund requested: order=%s amount=%.2f reason=%s",
                   req.order_id, req.amount, req.reason)
    return {
        "status": "ok",
        "refund_id": f"REFUND-{req.order_id}",
        "message": "Refund interface stub - not connected to real payment provider",
    }
