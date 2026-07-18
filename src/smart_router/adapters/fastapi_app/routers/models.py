"""Models router - list available models."""

from __future__ import annotations

from fastapi import APIRouter

from ....core.config import get_settings

router = APIRouter()


@router.get("/models")
async def list_models():
    """List all available models (OpenAI-compatible).

    Includes "auto" as a special model that triggers smart routing,
    which automatically selects the best model for each request.
    """
    settings = get_settings()
    models = [
        {
            "id": "auto",
            "object": "model",
            "created": 0,
            "owned_by": "smart-router",
            "modalities": ["text", "image", "audio", "video", "file"],
        }
    ]
    for m in settings.models:
        if m.enabled:
            models.append({
                "id": m.name,
                "object": "model",
                "created": 0,
                "owned_by": m.provider or "unknown",
                "modalities": m.modalities,
            })
    return {"object": "list", "data": models}
