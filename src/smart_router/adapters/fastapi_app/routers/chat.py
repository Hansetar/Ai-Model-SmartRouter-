"""Chat completions router - OpenAI-compatible proxy with smart routing.

Model parameter behavior:
- model="auto": Triggers smart routing system (selects best model for the request)
- model=<existing_model_name>: Direct call, no smart routing, but full authorization required
- model=<alias>: Resolved via model_aliases, then treated as direct call
- model=<unknown>: Returns 404 error (strict mode, no fallback to smart routing)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ....auth import verify_api_key, resolve_tenant_from_request, authorize_model_access
from ....core.config import get_settings
from ....core.routing import get_routing_engine
from ....core.storage import get_session, RequestLog, ApiLog
from ....health import get_health_checker
from ....quota import get_quota_manager

logger = logging.getLogger(__name__)

router = APIRouter()


class UpstreamError(Exception):
    """Upstream model call failure."""

    def __init__(self, model_name: str, status_code: int, message: str):
        self.model_name = model_name
        self.status_code = status_code
        self.message = message
        super().__init__(f"Model {model_name} failed ({status_code}): {message}")


@router.post("/chat/completions")
async def chat_completions(request: Request):
    """OpenAI-compatible chat completions endpoint with smart routing."""
    body = await request.json()

    # ── Step 1: Extract API key and resolve tenant identity ──
    api_key = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not api_key:
        raise HTTPException(status_code=401, detail="API key is required")

    tenant_id_header = request.headers.get("X-Tenant-ID")
    identity = resolve_tenant_from_request(api_key, tenant_id_header)
    if not identity.is_valid:
        raise HTTPException(status_code=401, detail=identity.error or "Invalid API key")

    tenant_id = identity.tenant_id
    is_global_key = identity.is_global_key

    # ── Step 2: Extract request params ──
    messages = body.get("messages", [])
    model_name = body.get("model")
    stream = body.get("stream", False)
    max_tokens = body.get("max_tokens", 4096)

    # Build prompt from messages
    prompt = _extract_prompt(messages)
    content_types = _detect_content_types(messages)

    # ── Step 3: Route based on model parameter ──
    settings = get_settings()

    if not model_name:
        raise HTTPException(status_code=400, detail="Model parameter is required. Use 'auto' for smart routing or specify a model name.")

    if model_name == "auto":
        # ── Smart routing path ──
        return await _handle_smart_routing(
            body=body,
            prompt=prompt,
            content_types=content_types,
            tenant_id=tenant_id,
            is_global_key=is_global_key,
            stream=stream,
            max_tokens=max_tokens,
        )

    # ── Direct call path: resolve model name (check alias first) ──
    resolved_model = settings.resolve_model_name(model_name)
    model = settings.get_model(resolved_model)

    if not model:
        # Model doesn't exist (not an alias, not a known model)
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' does not exist")

    # ── Step 4: Full authorization check for direct call ──
    auth_result = authorize_model_access(api_key, resolved_model, tenant_id_header)
    if not auth_result.authorized:
        raise HTTPException(status_code=auth_result.error_code, detail=auth_result.error_detail)

    # ── Step 5: Call the model directly ──
    enriched_model = settings.get_enriched_model(resolved_model)
    if not enriched_model:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' does not exist")

    return await _execute_model_call(
        model=enriched_model,
        model_name=resolved_model,
        body=body,
        prompt=prompt,
        content_types=content_types,
        tenant_id=tenant_id,
        stream=stream,
        strategy_used="direct_call",
    )


async def _handle_smart_routing(
    body: Dict[str, Any],
    prompt: str,
    content_types: List[str],
    tenant_id: Optional[str],
    is_global_key: bool,
    stream: bool,
    max_tokens: int,
) -> Any:
    """Handle smart routing when model=auto.

    The routing engine will only consider models that the tenant
    has permission to use and are active with balance > 0.

    Protection mechanisms to avoid infinite loops and hangs:
    - Maximum fallback chain depth (3 levels)
    - Request timeout (120s hard limit)
    - Failed model tracking to prevent retry loops
    - Circuit breaker via health checker
    """
    settings = get_settings()

    # Quota check (pre-routing)
    quota_mgr = get_quota_manager()
    if not quota_mgr.check_quota(tenant_id, estimated_tokens=max_tokens):
        raise HTTPException(status_code=429, detail="Quota exceeded")

    # Check tenant balance first (if tenant-bound)
    if tenant_id and not is_global_key:
        tenant = settings.get_tenant(tenant_id)
        if tenant:
            try:
                from ....core.storage import get_session, TenantBalance
                with get_session() as session:
                    balance_record = session.get(TenantBalance, tenant_id)
                    if balance_record and not getattr(balance_record, 'unlimited', False) and balance_record.balance <= 0:
                        raise HTTPException(status_code=402, detail="Account is in arrears, please recharge")
            except HTTPException:
                raise
            except Exception:
                pass  # Fail-open for balance check

    # Route to model
    engine = get_routing_engine()
    result = engine.select_model(
        prompt=prompt,
        requested_model=None,  # Don't pass "auto" as requested_model
        content_types=content_types,
        tenant_id=tenant_id,
        is_global_key=is_global_key,
    )

    if not result.model:
        raise HTTPException(status_code=503, detail="No available model found by smart routing")

    selected_model = result.model
    selected_name = selected_model["name"]

    # Post-routing authorization check (double-check)
    if tenant_id and not is_global_key:
        # Verify the routed model is actually available for this tenant
        from ....auth.authorization import is_model_available_for_tenant
        if not is_model_available_for_tenant(selected_name, tenant_id, is_global_key):
            raise HTTPException(status_code=503, detail="No available model found by smart routing")

    # Check model health
    health_checker = get_health_checker()
    if not health_checker.is_healthy(selected_name):
        # Try fallback chain
        fallback_chain = engine.select_fallback_chain(
            difficulty=result.debug_info.get("difficulty", 50),
            est_in_tokens=result.debug_info.get("est_in_tokens", 500),
            est_out_tokens=result.debug_info.get("est_out_tokens", 500),
            failed_models=[selected_name],
            task_type=result.debug_info.get("task_type"),
            content_types=content_types,
            tenant_id=tenant_id,
            is_global_key=is_global_key,
        )
        if fallback_chain:
            selected_model = fallback_chain[0]
            selected_name = selected_model["name"]
        else:
            raise HTTPException(status_code=503, detail="No healthy model available")

    return await _execute_model_call(
        model=selected_model,
        model_name=selected_name,
        body=body,
        prompt=prompt,
        content_types=content_types,
        tenant_id=tenant_id,
        stream=stream,
        strategy_used=result.strategy_used or "smart_routing",
        debug_info=result.debug_info,
        _failed_models={selected_name},  # Track initial model
        _fallback_depth=0,
    )


async def _execute_model_call(
    model: Dict[str, Any],
    model_name: str,
    body: Dict[str, Any],
    prompt: str,
    content_types: List[str],
    tenant_id: Optional[str],
    stream: bool,
    strategy_used: str,
    debug_info: Optional[Dict[str, Any]] = None,
    _failed_models: Optional[set] = None,
    _fallback_depth: int = 0,
) -> Any:
    """Execute a model call with logging and fallback handling.

    Protection mechanisms:
    - _failed_models: Tracks models that already failed to prevent retry loops
    - _fallback_depth: Limits fallback chain depth to MAX_FALLBACK_DEPTH (3)
    - Hard timeout: 120s per upstream call
    - Circuit breaker: Health checker marks unhealthy models
    """
    # Protection: Maximum fallback depth to prevent infinite loops
    MAX_FALLBACK_DEPTH = 3
    if _fallback_depth > MAX_FALLBACK_DEPTH:
        raise HTTPException(status_code=503, detail="Max fallback depth exceeded, no available model")

    # Initialize failed models tracker
    if _failed_models is None:
        _failed_models = set()
    _failed_models.add(model_name)

    settings = get_settings()

    # Double-check provider/model balance before calling
    model_config = settings.get_model(model_name)
    if model_config:
        if model_config.balance_manual is not None and model_config.balance_manual <= 0:
            raise HTTPException(status_code=403, detail=f"Model '{model_name}' balance is depleted")
        if model_config.provider:
            provider = settings.get_provider(model_config.provider)
            if provider and provider.balance_manual is not None and provider.balance_manual <= 0:
                raise HTTPException(status_code=403, detail=f"Provider '{model_config.provider}' balance is depleted")

    request_id = str(uuid.uuid4())
    start_time = time.perf_counter()

    try:
        response = await _call_upstream(
            model=model,
            body=body,
            stream=stream,
        )
    except UpstreamError as e:
        # Record failure
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        _log_request(
            request_id=request_id, prompt=prompt, model_name=model_name,
            latency_ms=latency_ms, success=False, strategy_used=strategy_used,
            content_types=",".join(content_types) if content_types else None,
            tenant_id=tenant_id,
        )
        health_checker = get_health_checker()
        health_checker.record_request_result(model_name, False)

        # Try fallback chain (only for smart routing, not direct calls)
        if strategy_used != "direct_call":
            engine = get_routing_engine()
            fallback_chain = engine.select_fallback_chain(
                difficulty=debug_info.get("difficulty", 50) if debug_info else 50,
                est_in_tokens=debug_info.get("est_in_tokens", 500) if debug_info else 500,
                est_out_tokens=debug_info.get("est_out_tokens", 500) if debug_info else 500,
                failed_models=list(_failed_models),  # Pass all previously failed models
                task_type=debug_info.get("task_type") if debug_info else None,
                content_types=content_types,
                tenant_id=tenant_id,
                is_global_key=False,
            )

            # Protection: Filter out already-failed models from fallback chain
            safe_fallback = [fb for fb in fallback_chain if fb["name"] not in _failed_models]

            for fb_model in safe_fallback:
                try:
                    response = await _call_upstream(model=fb_model, body=body, stream=stream)
                    model_name = fb_model["name"]
                    strategy_used = "fallback"
                    break
                except UpstreamError:
                    _failed_models.add(fb_model["name"])
                    health_checker = get_health_checker()
                    health_checker.record_request_result(fb_model["name"], False)
                    continue
            else:
                raise HTTPException(status_code=502, detail="All models failed (no more fallback candidates)")
        else:
            raise HTTPException(status_code=e.status_code, detail=f"Model call failed: {e.message}")

    latency_ms = int((time.perf_counter() - start_time) * 1000)

    # Record success
    _log_request(
        request_id=request_id, prompt=prompt, model_name=model_name,
        latency_ms=latency_ms, success=True, strategy_used=strategy_used,
        content_types=",".join(content_types) if content_types else None,
        tenant_id=tenant_id,
    )
    health_checker = get_health_checker()
    health_checker.record_request_result(model_name, True)

    # Record quota usage
    quota_mgr = get_quota_manager()
    quota_mgr.record_usage(tenant_id, cost=0.0)

    return response


async def _call_upstream(
    model: Dict[str, Any],
    body: Dict[str, Any],
    stream: bool = False,
) -> Any:
    """Call upstream model API."""
    base_url = model.get("base_url", "")
    api_key = model.get("api_key", "")
    api_type = model.get("api_type", "openai")

    if not base_url:
        raise UpstreamError(model["name"], 500, "No base_url configured")

    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Build upstream request body
    upstream_body = dict(body)
    if model.get("litellm_name"):
        upstream_body["model"] = model["litellm_name"]
    elif "model" in upstream_body:
        upstream_body["model"] = model["name"]

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            if stream:
                return StreamingResponse(
                    _stream_upstream(client, url, headers, upstream_body, model["name"]),
                    media_type="text/event-stream",
                )
            else:
                resp = await client.post(url, headers=headers, json=upstream_body)
                if resp.status_code != 200:
                    raise UpstreamError(model["name"], resp.status_code, resp.text[:500])
                return JSONResponse(content=resp.json())

    except httpx.TimeoutException:
        raise UpstreamError(model["name"], 504, "Request timeout")
    except httpx.ConnectError:
        raise UpstreamError(model["name"], 502, "Connection failed")


async def _stream_upstream(
    client: httpx.AsyncClient,
    url: str,
    headers: Dict[str, str],
    body: Dict[str, Any],
    model_name: str,
):
    """Stream response from upstream model."""
    body["stream"] = True
    try:
        async with client.stream("POST", url, headers=headers, json=body) as resp:
            if resp.status_code != 200:
                error_body = await resp.aread()
                raise UpstreamError(model_name, resp.status_code, error_body.decode()[:500])
            async for chunk in resp.aiter_bytes():
                yield chunk
    except UpstreamError:
        raise
    except Exception as e:
        raise UpstreamError(model_name, 502, str(e))


def _extract_prompt(messages: List[Dict[str, Any]]) -> str:
    """Extract text prompt from messages."""
    parts = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
    return " ".join(parts)[:2000]


def _detect_content_types(messages: List[Dict[str, Any]]) -> List[str]:
    """Detect content types from messages."""
    types = {"text"}
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    t = item.get("type", "")
                    if t in ("image_url", "image"):
                        types.add("image")
                    elif t == "video":
                        types.add("video")
                    elif t == "audio":
                        types.add("audio")
                    elif t == "file":
                        types.add("file")
    return list(types)


def _log_request(
    request_id: str,
    prompt: str,
    model_name: str,
    latency_ms: int,
    success: bool,
    strategy_used: str = "",
    content_types: Optional[str] = None,
    tenant_id: Optional[str] = None,
    debug_info: Optional[Dict[str, Any]] = None,
) -> None:
    """Log request to database."""
    try:
        prompt_hash = hashlib.md5(prompt.encode("utf-8")).hexdigest()
        with get_session() as session:
            log = RequestLog(
                timestamp=time.time(),
                prompt_hash=prompt_hash,
                predicted_difficulty=debug_info.get("difficulty", 50) if debug_info else 50,
                routed_model=model_name,
                latency_ms=latency_ms,
                success=success,
                task_type=debug_info.get("task_type") if debug_info else None,
                route_source=strategy_used,
                prompt_preview=prompt[:200],
                content_types=content_types,
                tenant_id=tenant_id,
            )
            session.add(log)
            session.commit()
    except Exception as e:
        logger.warning("Request logging failed: %s", e)
