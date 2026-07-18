"""Feedback router - user feedback collection."""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ....core.config import get_settings
from ....core.routing import get_routing_engine
from ....core.storage import get_session, FeedbackRecord

router = APIRouter()


class FeedbackRequest(BaseModel):
    """Feedback submission request."""

    request_id: str = Field(..., description="Request ID (prompt hash)")
    feedback_type: str = Field("explicit", description="explicit or implicit")
    sentiment: str = Field(..., description="positive or negative")
    context_snapshot: str = Field("", description="Optional context")


@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest):
    """Submit feedback for a routed request."""
    if req.sentiment not in ("positive", "negative"):
        raise HTTPException(status_code=400, detail="sentiment must be 'positive' or 'negative'")

    try:
        with get_session() as session:
            # Find the request log
            from ....core.storage import RequestLog
            from sqlalchemy import select
            log = session.query(RequestLog).filter_by(prompt_hash=req.request_id).order_by(RequestLog.id.desc()).first()

            model_name = log.routed_model if log else None
            task_type = log.task_type if log else None

            # Record feedback
            feedback = FeedbackRecord(
                request_id=req.request_id,
                feedback_type=req.feedback_type,
                sentiment=req.sentiment,
                context_snapshot=req.context_snapshot,
                timestamp=time.time(),
            )
            session.add(feedback)
            session.commit()

        # Update RL policy
        engine = get_routing_engine()
        if model_name:
            engine.record_feedback(model_name, task_type, True, req.sentiment)

        return {"status": "ok"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
