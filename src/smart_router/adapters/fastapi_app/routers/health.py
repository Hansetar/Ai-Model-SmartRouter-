"""Health router - model health status endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ....auth.jwt_auth import verify_token
from ....health import get_health_checker

router = APIRouter()


async def require_admin(request) -> dict:
    """Admin auth dependency (reused from admin router)."""
    from .admin import require_admin as _require_admin
    return await _require_admin(request)


@router.get("/health/models")
async def get_models_health(_admin=Depends(require_admin)):
    """Get health status of all models."""
    checker = get_health_checker()
    return {"models": checker.get_all_status()}


@router.post("/health/models/{model_name:path}/check")
async def trigger_health_check(model_name: str, _admin=Depends(require_admin)):
    """Trigger an immediate health check for a model."""
    from ....core.config import get_settings
    settings = get_settings()
    model = settings.get_model(model_name)
    if not model:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Model not found")

    checker = get_health_checker()
    import asyncio
    await checker._check_model(model)

    return {"status": checker.get_status(model_name)}
