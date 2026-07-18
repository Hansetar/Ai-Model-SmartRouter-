"""
adapters/standalone_app.py
==========================
独立运行模式：基于 FastAPI 的 HTTP 代理网关，兼容 OpenAI 接口格式。

提供：
- POST /v1/chat/completions   智能路由代理（流式透传）
- GET  /v1/models             模型列表
- POST /v1/feedback           显式反馈上报
- POST /admin/api/login       管理面板登录
- GET  /admin/api/*           控制面板后端 API（需 JWT 认证）
- /admin/                     控制面板静态页面
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------- #
# 并发控制信号量（全局，在 create_app 中初始化）
# ---------------------------------------------------------------------- #
_concurrency_semaphore: Optional[asyncio.Semaphore] = None

# OpenAI 兼容的错误响应格式
class OpenAIError(HTTPException):
    """OpenAI 兼容的错误响应。"""

    def __init__(self, status_code: int, message: str, error_type: str = "invalid_request_error") -> None:
        self.detail = {
            "error": {
                "message": message,
                "type": error_type,
                "param": None,
                "code": None,
            }
        }
        super().__init__(status_code=status_code, detail=self.detail)


class UpstreamError(Exception):
    """上游模型调用失败异常，用于触发后备链重试。"""

    def __init__(self, model_name: str, status_code: int, message: str) -> None:
        self.model_name = model_name
        self.status_code = status_code
        self.message = message
        super().__init__(f"Model {model_name} failed ({status_code}): {message}")

from core import config, db, feedback_analyzer, predictor, pricing_manager, router
from core.router import detect_task_type, detect_model_keyword, task_type_detector
from core.exchange_rate import exchange_rate_manager
from core.fallback_logger import fallback_logger
from core.notifier import notifier

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------- #
# API 响应内存缓存
# ---------------------------------------------------------------------- #
_api_cache: Dict[str, tuple] = {}  # key -> (data, expire_time)


def _cache_get(key: str) -> Optional[Any]:
    """获取缓存数据，过期返回 None。"""
    entry = _api_cache.get(key)
    if entry is None:
        return None
    data, expire = entry
    if time.time() > expire:
        del _api_cache[key]
        return None
    return data


def _cache_set(key: str, data: Any, ttl: float = 10.0) -> None:
    """设置缓存数据，默认 TTL 10秒。"""
    _api_cache[key] = (data, time.time() + ttl)
    # 清理过期缓存（简单策略：超过 100 条时清理）
    if len(_api_cache) > 100:
        now = time.time()
        expired = [k for k, (_, e) in _api_cache.items() if now > e]
        for k in expired:
            del _api_cache[k]


from core.auth import (
    create_access_token,
    is_api_key_configured,
    verify_api_key,
    verify_password,
    verify_token,
)


# ---------------------------------------------------------------------- #
# 认证依赖
# ---------------------------------------------------------------------- #
async def _extract_admin_token(request: Request) -> Optional[str]:
    """从请求中提取 JWT Token。"""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


async def require_admin(request: Request) -> dict:
    """管理面板 JWT 认证依赖。"""
    token = await _extract_admin_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="未提供认证令牌")
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="认证令牌无效或已过期")
    return payload


async def check_api_key(request: Request) -> None:
    """检查 /v1 接口的 API Key 认证。

    对于流式请求，如果认证失败，以 SSE 格式返回错误，
    因为客户端期望收到 text/event-stream 响应。
    """
    if not is_api_key_configured():
        return  # 未配置 API Key，放行

    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        key = auth[7:]
    else:
        key = auth

    if not verify_api_key(key):
        raise OpenAIError(401, "Invalid API key", "authentication_error")


def create_app(predictor_instance=None) -> FastAPI:
    """创建 FastAPI 应用。

    :param predictor_instance: 可选，注入预测器实例（测试用）
    """
    app = FastAPI(
        title="OpenClaw SmartRouter",
        description="双模式智能路由插件 - 独立 API 代理网关",
        version="1.0.0",
    )

    # Gzip 压缩中间件：大幅减少传输体积（JS/CSS/JSON 通常可压缩 60-80%）
    from starlette.middleware.gzip import GZipMiddleware
    app.add_middleware(GZipMiddleware, minimum_size=500)

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 慢查询监控中间件：记录超过阈值的请求，帮助发现性能问题
    _SLOW_QUERY_THRESHOLD_MS = 5000  # 5秒
    _slow_query_log: list = []  # 保留最近50条慢查询记录
    _MAX_SLOW_QUERIES = 50

    @app.middleware("http")
    async def slow_query_middleware(request: Request, call_next):
        start_time = time.time()
        response = await call_next(request)
        elapsed_ms = int((time.time() - start_time) * 1000)

        if elapsed_ms > _SLOW_QUERY_THRESHOLD_MS:
            logger.warning(
                "慢查询告警: %s %s 耗时 %dms (阈值 %dms)",
                request.method, request.url.path, elapsed_ms, _SLOW_QUERY_THRESHOLD_MS
            )
            record = {
                "timestamp": time.time(),
                "method": request.method,
                "path": request.url.path,
                "elapsed_ms": elapsed_ms,
                "status_code": response.status_code if response else 0,
            }
            _slow_query_log.append(record)
            # 保留最近50条
            if len(_slow_query_log) > _MAX_SLOW_QUERIES:
                _slow_query_log.pop(0)

        return response

    # API 调用日志中间件：记录所有 /v1/ 请求
    @app.middleware("http")
    async def api_log_middleware(request: Request, call_next):
        # 只记录 /v1/ 路径的 API 请求
        if not request.url.path.startswith("/v1/"):
            return await call_next(request)

        start_time = time.time()
        response = None
        error_msg = None

        try:
            response = await call_next(request)
        except Exception as exc:
            error_msg = str(exc)[:500]
            raise

        finally:
            try:
                latency_ms = int((time.time() - start_time) * 1000)
                status_code = response.status_code if response else 500

                # 从 request.state 获取路由信息（由 proxy 函数设置）
                routed_model = getattr(request.state, "_api_routed_model", None)
                route_source = getattr(request.state, "_api_route_source", None)
                requested_model = getattr(request.state, "_api_requested_model", None)
                prompt_tokens = getattr(request.state, "_api_prompt_tokens", 0)
                completion_tokens = getattr(request.state, "_api_completion_tokens", 0)
                cost = getattr(request.state, "_api_cost", 0.0)
                cost_currency = getattr(request.state, "_api_cost_currency", config.currency)
                prompt_preview = getattr(request.state, "_api_prompt_preview", None)

                # 获取客户端 IP
                client_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                if not client_ip:
                    client_ip = request.client.host if request.client else None

                db.log_api_call(
                    request_id=getattr(request.state, "_api_request_id", ""),
                    method=request.method,
                    path=request.url.path,
                    requested_model=requested_model,
                    routed_model=routed_model,
                    route_source=route_source,
                    status_code=status_code,
                    error_message=error_msg,
                    latency_ms=latency_ms,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost=cost,
                    cost_currency=cost_currency,
                    prompt_preview=prompt_preview,
                    client_ip=client_ip,
                )
            except Exception:
                pass  # 日志写入不能影响主流程

        return response

    # 全局异常处理器：确保所有错误响应都符合 OpenAI 格式
    @app.exception_handler(HTTPException)
    async def openai_error_handler(request: Request, exc: HTTPException):
        """将 HTTPException 转为 OpenAI 兼容的错误格式。"""
        # 如果 detail 已经是 OpenAI 格式（含 error 键），直接返回
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        # 否则包装为 OpenAI 格式
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "message": str(exc.detail),
                    "type": "invalid_request_error",
                    "param": None,
                    "code": None,
                }
            },
        )

    @app.exception_handler(Exception)
    async def generic_error_handler(request: Request, exc: Exception):
        """未捕获异常的兜底处理。"""
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "message": f"Internal server error: {str(exc)}",
                    "type": "internal_error",
                    "param": None,
                    "code": None,
                }
            },
        )

    # ------------------------------------------------------------------ #
    # 全局对象
    # ------------------------------------------------------------------ #
    app.state.predictor = predictor_instance or predictor
    app.state.router = router
    app.state.pricing = pricing_manager
    app.state.feedback = feedback_analyzer
    app.state.db = db
    app.state.fallback_logger = fallback_logger

    # ------------------------------------------------------------------ #
    # 全局 httpx 异步客户端（连接池复用，避免每次请求创建新客户端）
    # ------------------------------------------------------------------ #
    _http_client: Optional[httpx.AsyncClient] = None

    # 并发控制信号量（默认 50 并发，可通过环境变量调整）
    max_concurrency = int(os.environ.get("SMARTROUTER_MAX_CONCURRENCY", "50"))
    global _concurrency_semaphore
    _concurrency_semaphore = asyncio.Semaphore(max_concurrency)

    async def _get_http_client() -> httpx.AsyncClient:
        """获取或创建全局 httpx 异步客户端（懒初始化）。"""
        nonlocal _http_client
        if _http_client is None or _http_client.is_closed:
            _http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=10.0),
                limits=httpx.Limits(
                    max_connections=100,
                    max_keepalive_connections=20,
                    keepalive_expiry=60,
                ),
            )
        return _http_client

    # ------------------------------------------------------------------ #
    # 定时任务：自动应用超过24h未确认的 pending_modalities
    # ------------------------------------------------------------------ #
    _pending_modality_task: asyncio.Task | None = None

    async def _auto_apply_pending_modalities_loop():
        """每小时检查一次，将超过24h未确认的 pending_modalities 自动应用。"""
        while True:
            try:
                await asyncio.sleep(3600)  # 每小时检查一次
                import time as _time
                AUTO_CONFIRM_SECONDS = 24 * 3600
                now_ts = _time.time()
                raw_models = config.get("models", [])
                auto_applied = []
                for m in raw_models:
                    pending = m.get("pending_modalities")
                    detected_at = m.get("pending_modalities_detected_at")
                    if pending is not None and detected_at is not None:
                        if now_ts - detected_at >= AUTO_CONFIRM_SECONDS:
                            m["modalities"] = pending
                            m["pending_modalities"] = None
                            m["pending_modalities_detected_at"] = None
                            auto_applied.append(m.get("name"))
                if auto_applied:
                    config.set("models", raw_models)
                    try:
                        config.save()
                    except Exception:
                        pass
                    logger.info("自动应用 pending_modalities: %s", auto_applied)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("自动应用 pending_modalities 出错: %s", e)

    @app.on_event("startup")
    async def _start_background_tasks():
        nonlocal _pending_modality_task
        _pending_modality_task = asyncio.create_task(_auto_apply_pending_modalities_loop())

    @app.on_event("shutdown")
    async def _shutdown_http_client():
        """应用关闭时清理 httpx 客户端和定时任务。"""
        nonlocal _http_client, _pending_modality_task
        if _pending_modality_task:
            _pending_modality_task.cancel()
            _pending_modality_task = None
        if _http_client and not _http_client.is_closed:
            await _http_client.aclose()
            _http_client = None

    # ------------------------------------------------------------------ #
    # 工具函数
    # ------------------------------------------------------------------ #
    def _hash_prompt(prompt: str) -> str:
        if not isinstance(prompt, str):
            prompt = str(prompt) if prompt is not None else ""
        return hashlib.md5(prompt.encode("utf-8")).hexdigest()

    def _get_unit_divisor(price_unit: str) -> float:
        """将 price_unit 字符串转换为除数。"""
        unit = str(price_unit).strip().upper()
        if unit in ("1", "PER_TOKEN", ""):
            return 1
        if unit in ("1K", "K"):
            return 1_000
        if unit in ("1M", "M"):
            return 1_000_000
        if unit in ("1B", "B"):
            return 1_000_000_000
        try:
            return float(unit)
        except (ValueError, TypeError):
            return 1_000_000

    def _extract_text_from_content(content: Any) -> str:
        """从 content 字段提取纯文本，兼容多模态格式。

        OpenAI 多模态格式中 content 可以是：
        - 字符串: "Hello"
        - 列表: [{"type": "text", "text": "Hello"}, {"type": "image_url", ...}]
        """
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, str):
                    text_parts.append(item)
                elif isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
            return " ".join(text_parts)
        return str(content) if content is not None else ""

    def _detect_content_types(body: Dict[str, Any]) -> List[str]:
        """检测请求中包含的内容类型（text, image, audio, video）。

        扫描所有 messages 中的 content 字段，返回涉及的内容类型列表。
        用于路由时过滤不支持多模态的模型。
        """
        types: set = set()
        messages = body.get("messages", [])
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str):
                types.add("text")
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        item_type = item.get("type", "")
                        if item_type == "text":
                            types.add("text")
                        elif item_type == "image_url":
                            types.add("image")
                        elif item_type == "image_file":
                            types.add("image")
                        elif item_type == "input_image":
                            types.add("image")
                        elif item_type == "audio_url":
                            types.add("audio")
                        elif item_type == "input_audio":
                            types.add("audio")
                        elif item_type == "video_url":
                            types.add("video")
                        elif item_type == "video_file":
                            types.add("video")
                        elif item_type == "input_video":
                            types.add("video")
                        elif item_type == "file_url":
                            types.add("file")
        if not types:
            types.add("text")
        return list(types)

    def _extract_prompt(body: Dict[str, Any]) -> str:
        messages = body.get("messages", [])
        if not messages:
            return ""
        content = messages[-1].get("content", "")
        return _extract_text_from_content(content)

    def _build_upstream_url(selected: Dict[str, Any]) -> str:
        """构建上游请求 URL。

        所有模型统一使用 OpenAI 兼容接口格式：
        {base_url}/chat/completions
        """
        base_url = selected.get("base_url", "").rstrip("/")
        if not base_url:
            # 根据 api_type 推断默认 base_url
            api_type = selected.get("api_type", "openai")
            default_urls = {
                "openai": "https://api.openai.com/v1",
                "deepseek": "https://api.deepseek.com/v1",
                "zhipu": "https://open.bigmodel.cn/api/paas/v4",
                "siliconflow": "https://api.siliconflow.cn/v1",
                "aliyun": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            }
            base_url = default_urls.get(api_type, "")
            if not base_url:
                raise ValueError(f"模型 {selected['name']} 缺少 base_url 配置")

        # 确保 URL 以 /chat/completions 结尾
        if not base_url.endswith("/chat/completions"):
            url = f"{base_url}/chat/completions"
        else:
            url = base_url

        # 去重斜杠
        url = url.replace("//chat", "/chat")
        return url

    def _collect_sse_text(text: str) -> tuple[str, Dict[str, Any]]:
        """从 SSE 文本中收集完整内容，返回 (content, usage)。"""
        import json as _json

        collected: List[str] = []
        usage_info: Dict[str, Any] = {}
        for line in text.splitlines():
            if not line or not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload.strip() == "[DONE]":
                break
            try:
                data = _json.loads(payload)
                delta = (
                    data.get("choices", [{}])[0]
                    .get("delta", {})
                    .get("content", "")
                )
                if delta:
                    collected.append(delta)
                if "usage" in data:
                    usage_info.update(data["usage"])
            except Exception:  # noqa: BLE001
                pass
        return "".join(collected), usage_info

    def _build_non_stream_response(
        model_name: str, content: str, usage_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """组装 OpenAI 兼容的非流式 chat/completions 响应。"""
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_name,
            "system_fingerprint": f"fp_{uuid.uuid4().hex[:12]}",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": usage_info.get("prompt_tokens", 0),
                "completion_tokens": usage_info.get("completion_tokens", 0),
                "total_tokens": usage_info.get("total_tokens", 0),
            },
        }

    def _build_upstream_headers(selected: Dict[str, Any]) -> Dict[str, str]:
        """构建上游请求头。

        严格使用模型/Provider 配置的 api_key，绝不使用用户的认证 key。
        用户的 api_key 仅用于 /v1 接口的身份认证（check_api_key），
        路由做中转代理时必须使用自身配置的 key 访问上游模型。

        智谱(zhipu) API Key 格式为 {id}.{secret}，需要生成 JWT token。
        """
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        # 优先级：模型自身 api_key > Provider api_key > 环境变量覆盖
        # get_models() 已完成合并，selected["api_key"] 即为最终的上游 key
        api_key = selected.get("api_key", "")
        api_type = selected.get("api_type", "openai")
        if api_key:
            if api_type == "zhipu":
                # 智谱需要将 {id}.{secret} 格式的 key 转为 JWT token
                from core.pricing_manager import ZhipuBalanceChecker
                jwt_token = ZhipuBalanceChecker.generate_token(api_key)
                if jwt_token:
                    headers["Authorization"] = f"Bearer {jwt_token}"
                else:
                    # JWT 生成失败，回退到原始 key（可能认证失败）
                    headers["Authorization"] = f"Bearer {api_key}"
            else:
                headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _log_fallback(
        original_model: str,
        fallback_model: str,
        attempt: int,
        failed_models: List[str],
        error: str = "",
        prompt_preview: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """记录后备链切换事件到专用日志。"""
        fallback_logger.log_fallback(
            original_model=original_model,
            fallback_model=fallback_model,
            attempt=attempt,
            failed_models=failed_models,
            error=error,
            prompt_preview=prompt_preview,
            extra=extra,
        )

    async def _check_upstream_available(
        selected: Dict[str, Any],
        body: Dict[str, Any],
    ) -> tuple[bool, Dict[str, Any]]:
        """预检上游模型是否可用（用于流式模式的后备链判断）。

        优化：发送一个极简请求（只含1条消息、max_tokens=1），
        而非完整请求，大幅减少预检开销。
        返回 (is_available, error_info)。
        """
        # 构建极简预检请求体，只验证连通性和认证
        check_body = {
            "model": selected.get("upstream_model_name") or selected["name"],
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
            "stream": False,
        }
        headers = _build_upstream_headers(selected)
        try:
            url = _build_upstream_url(selected)
        except ValueError as e:
            return False, {"status_code": 400, "message": str(e)}

        try:
            client = await _get_http_client()
            resp = await client.post(url, json=check_body, headers=headers, timeout=15.0)
            if resp.status_code >= 400:
                error_msg = ""
                try:
                    error_data = resp.json()
                    error_msg = error_data.get("error", {}).get("message", str(error_data)) if isinstance(error_data, dict) else str(error_data)
                except Exception:
                    error_msg = resp.text[:500]
                return False, {"status_code": resp.status_code, "message": error_msg}
            return True, {}
        except Exception as exc:  # noqa: BLE001
            return False, {"status_code": 502, "message": str(exc)}

    async def _do_upstream(
        request: Request,
        selected: Dict[str, Any],
        body: Dict[str, Any],
        request_id: str,
        prompt: str,
        diff: int,
        est_tokens: int,
        task_type: Optional[str] = None,
        requested_model_name: str = "",
        route_source: Optional[str] = None,
        is_fallback_retry: bool = False,
        route_chain: Optional[List[str]] = None,
        content_types: Optional[List[str]] = None,
    ) -> StreamingResponse | JSONResponse:
        """执行上游请求，根据 stream 参数决定流式透传或非流式响应。

        失败时抛出 UpstreamError 而非直接返回错误响应，以便外层实现后备链重试。

        模型名处理规则：
        - 发送给上游：使用 upstream_model_name（如未配置则用 name）
        - 返回给客户端：使用 selected["name"]（配置中的显示名）
          - auto 模式下客户端应看到实际路由到的模型名，而非 "auto"
          - 直连模式下客户端应看到自己请求的模型名
        """
        # ===== 统一保险检查：确保模型未被禁用且在生效时间段内 =====
        model_name = selected.get("name", "unknown")
        if not selected.get("enabled", True):
            is_auto = (not requested_model_name or requested_model_name == "auto")
            if is_auto:
                raise UpstreamError(
                    model_name=model_name,
                    status_code=403,
                    message=f"Model {model_name} is disabled, skipping to fallback",
                )
            else:
                raise OpenAIError(403, f"Model `{requested_model_name}` is disabled", "invalid_request_error")
        if not pricing_manager.is_within_active_hours(selected.get("active_hours")):
            is_auto = (not requested_model_name or requested_model_name == "auto")
            if is_auto:
                raise UpstreamError(
                    model_name=model_name,
                    status_code=403,
                    message=f"Model {model_name} is outside active hours, skipping to fallback",
                )
            else:
                raise OpenAIError(403, f"Model `{requested_model_name}` is not available at this time (outside active hours)", "invalid_request_error")
        # ===== 保险检查结束 =====

        body = dict(body)
        # 发送给上游的模型名：优先 upstream_model_name
        upstream_model = selected.get("upstream_model_name") or selected["name"]
        body["model"] = upstream_model
        headers = _build_upstream_headers(selected)
        try:
            url = _build_upstream_url(selected)
        except ValueError as e:
            raise OpenAIError(400, str(e))

        effective_route_source = "fallback" if is_fallback_retry else route_source

        # 返回给客户端的模型名：
        # - auto 模式（model="auto" 或 model=""）：显示实际路由到的模型名 selected["name"]
        # - 直连模式（model=具体名称）：显示用户请求的模型名
        if requested_model_name and requested_model_name != "auto":
            display_model = requested_model_name
        else:
            display_model = selected["name"]

        is_stream = body.get("stream", False)
        # 流式模式：如果上游要求 stream_options 但请求中没有，自动添加
        if is_stream and "stream_options" not in body:
            body["stream_options"] = {"include_usage": True}
        # 非流式模式：移除 stream_options（某些 API 不允许 stream=false 时带 stream_options）
        if not is_stream:
            body.pop("stream_options", None)
        t0 = time.perf_counter()

        # ---- 非流式模式 ----
        if not is_stream:
            collected_content: List[str] = []
            usage_info: Dict[str, Any] = {}
            success = True
            try:
                client = await _get_http_client()
                resp = await client.post(url, json=body, headers=headers)
                if resp.status_code >= 400:
                    success = False
                    latency_ms = int((time.perf_counter() - t0) * 1000)
                    asyncio.create_task(
                        _after_call(
                            request=request,
                            request_id=request_id, prompt=prompt, diff=diff,
                            est_tokens=est_tokens, selected=selected,
                            latency_ms=latency_ms, success=False,
                            collected_content="", usage_info={},
                            task_type=task_type,
                            route_source=effective_route_source,
                            requested_model_name=requested_model_name,
                            route_chain=route_chain,
                        content_types=content_types,
                        )
                    )
                    # 抛出异常以触发后备链重试
                    error_msg = ""
                    try:
                        error_data = resp.json()
                        error_msg = error_data.get("error", {}).get("message", str(error_data)) if isinstance(error_data, dict) else str(error_data)
                    except Exception:
                        error_msg = resp.text[:500]
                    raise UpstreamError(
                        model_name=selected["name"],
                        status_code=resp.status_code,
                        message=error_msg,
                    )
                # 上游可能返回流式 SSE（即使我们请求非流式），需要兼容
                content_type = resp.headers.get("content-type", "")
                if "text/event-stream" in content_type:
                    # 上游强制流式，收集完整响应后组装为非流式 JSON
                    full_content, usage_info = _collect_sse_text(resp.text)
                    collected_content = [full_content]
                else:
                    # 标准非流式 JSON 响应
                    resp_data = resp.json()
                    # 替换 model 字段为客户端应看到的模型名
                    resp_data["model"] = display_model
                    # 提取 content 和 usage
                    choices = resp_data.get("choices", [])
                    if choices:
                        msg = choices[0].get("message", {})
                        c = msg.get("content", "")
                        if c:
                            collected_content = [c]
                    usage_info = resp_data.get("usage", {})
                    latency_ms = int((time.perf_counter() - t0) * 1000)
                    asyncio.create_task(
                        _after_call(
                            request=request,
                            request_id=request_id, prompt=prompt, diff=diff,
                            est_tokens=est_tokens, selected=selected,
                            latency_ms=latency_ms, success=True,
                            collected_content="".join(collected_content),
                            usage_info=usage_info, task_type=task_type,
                            route_source=effective_route_source,
                            requested_model_name=requested_model_name,
                            route_chain=route_chain,
                        content_types=content_types,
                        )
                    )
                    return JSONResponse(content=resp_data)
            except UpstreamError:
                raise  # 重新抛出，让外层处理后备链
            except Exception as exc:  # noqa: BLE001
                latency_ms = int((time.perf_counter() - t0) * 1000)
                asyncio.create_task(
                    _after_call(
                        request=request,
                        request_id=request_id, prompt=prompt, diff=diff,
                        est_tokens=est_tokens, selected=selected,
                        latency_ms=latency_ms, success=False,
                        collected_content="", usage_info={},
                        task_type=task_type,
                        route_source=effective_route_source,
                        requested_model_name=requested_model_name,
                        route_chain=route_chain,
                    content_types=content_types,
                    )
                )
                # 抛出异常以触发后备链重试
                raise UpstreamError(
                    model_name=selected["name"],
                    status_code=502,
                    message=str(exc),
                )

            # SSE 收集后组装非流式响应
            latency_ms = int((time.perf_counter() - t0) * 1000)
            asyncio.create_task(
                _after_call(
                    request=request,
                    request_id=request_id, prompt=prompt, diff=diff,
                    est_tokens=est_tokens, selected=selected,
                    latency_ms=latency_ms, success=True,
                    collected_content="".join(collected_content),
                    usage_info=usage_info, task_type=task_type,
                    route_source=effective_route_source,
                    requested_model_name=requested_model_name,
                route_chain=route_chain,
                content_types=content_types,
                )
            )
            # 组装 OpenAI 兼容的非流式响应
            non_stream_resp = _build_non_stream_response(
                display_model, "".join(collected_content), usage_info
            )
            return JSONResponse(content=non_stream_resp)

        # ---- 流式模式 ----
        # 流式模式：转发上游 SSE，同时将 chunk 中的 model 字段替换为 display_model
        async def stream():
            collected_content: List[str] = []
            usage_info: Dict[str, Any] = {}
            success = True
            try:
                client = await _get_http_client()
                async with client.stream(
                    "POST", url, json=body, headers=headers
                ) as resp:
                    if resp.status_code >= 400:
                        success = False
                        error_body = await resp.aread()
                        latency_ms = int((time.perf_counter() - t0) * 1000)
                        asyncio.create_task(
                            _after_call(
                                request=request,
                                request_id=request_id, prompt=prompt, diff=diff,
                                est_tokens=est_tokens, selected=selected,
                                latency_ms=latency_ms, success=False,
                                collected_content="", usage_info={},
                                task_type=task_type,
                                route_source=effective_route_source,
                                requested_model_name=requested_model_name,
                                route_chain=route_chain,
                            content_types=content_types,
                            )
                        )
                        error_msg = ""
                        try:
                            error_text = error_body.decode("utf-8", errors="ignore")
                            import json as _json
                            try:
                                error_data = _json.loads(error_text)
                                error_msg = error_data.get("error", {}).get("message", str(error_data)) if isinstance(error_data, dict) else str(error_data)
                            except Exception:
                                error_msg = error_text[:500]
                        except Exception:
                            error_msg = "upstream error"
                        import json as _json
                        error_resp = {"error": {"message": error_msg, "type": "upstream_error", "param": None, "code": None}}
                        yield f"data: {_json.dumps(error_resp)}\n\n".encode("utf-8")
                        yield b"data: [DONE]\n\n"
                        return
                    async for line in resp.aiter_lines():
                        # 按行处理 SSE 数据，替换 model 字段并收集 content
                        if not line.strip():
                            # 空行（SSE 分隔符），直接透传
                            yield b"\n"
                            continue
                        if line.startswith("data: "):
                            import json as _json
                            payload = line[6:].strip()
                            if payload == "[DONE]":
                                yield b"data: [DONE]\n\n"
                                continue
                            try:
                                data = _json.loads(payload)
                                # 替换 model 字段为客户端应看到的模型名
                                if "model" in data:
                                    data["model"] = display_model
                                # 收集 content 用于难度评估
                                delta = (
                                    data.get("choices", [{}])[0]
                                    .get("delta", {})
                                    .get("content", "")
                                )
                                if delta:
                                    collected_content.append(delta)
                                if "usage" in data:
                                    usage_info.update(data["usage"])
                                # 重新编码为 SSE 格式
                                modified_line = f"data: {_json.dumps(data, ensure_ascii=False)}\n\n"
                                yield modified_line.encode("utf-8")
                                continue
                            except Exception:  # noqa: BLE001
                                pass
                        # 非 SSE 行或 JSON 解析失败，原样透传
                        yield (line + "\n").encode("utf-8")
            except Exception as exc:  # noqa: BLE001
                success = False
                latency_ms = int((time.perf_counter() - t0) * 1000)
                asyncio.create_task(
                    _after_call(
                        request=request,
                        request_id=request_id, prompt=prompt, diff=diff,
                        est_tokens=est_tokens, selected=selected,
                        latency_ms=latency_ms, success=False,
                        collected_content="", usage_info={},
                        task_type=task_type,
                        route_source=effective_route_source,
                        requested_model_name=requested_model_name,
                        route_chain=route_chain,
                    content_types=content_types,
                    )
                )
                import json as _json
                error_msg = _json.dumps({
                    "error": {"message": str(exc), "type": "upstream_error", "param": None, "code": None}
                })
                yield f"data: {error_msg}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"
                return
            finally:
                if success:
                    latency_ms = int((time.perf_counter() - t0) * 1000)
                    asyncio.create_task(
                        _after_call(
                            request=request,
                            request_id=request_id,
                            prompt=prompt,
                            diff=diff,
                            est_tokens=est_tokens,
                            selected=selected,
                            latency_ms=latency_ms,
                            success=success,
                            collected_content="".join(collected_content),
                            usage_info=usage_info,
                            task_type=task_type,
                            route_source=effective_route_source,
                            requested_model_name=requested_model_name,
                            route_chain=route_chain,
                        content_types=content_types,
                        )
                    )

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"X-Request-Id": request_id}
        )

    async def _after_call(
        request: Request,
        request_id: str,
        prompt: str,
        diff: int,
        est_tokens: int,
        selected: Dict[str, Any],
        latency_ms: int,
        success: bool,
        collected_content: str,
        usage_info: Dict[str, Any],
        task_type: Optional[str] = None,
        route_source: Optional[str] = None,
        requested_model_name: str = "",
        route_chain: Optional[List[str]] = None,
        content_types: Optional[List[str]] = None,
    ) -> None:
        """请求结束后的异步处理：记录日志、训练、扣账。"""
        # 真实 Token 数
        actual_in_tokens = usage_info.get("prompt_tokens", est_tokens)
        actual_out_tokens = usage_info.get("completion_tokens") or len(collected_content) // 4
        # 成本（使用实际 token 数计算，统一转为用户选择的货币）
        # price_input/price_output 的单位由 price_unit 决定（默认 1M=每百万token）
        target_currency = config.currency
        model_currency = selected.get("price_currency", "USD")
        price_unit = selected.get("price_unit", "1M")
        unit_divisor = _get_unit_divisor(price_unit)
        raw_cost = (
            actual_in_tokens * float(selected.get("price_input", 0))
            + actual_out_tokens * float(selected.get("price_output", 0))
        ) / unit_divisor
        cost = exchange_rate_manager.convert(raw_cost, model_currency, target_currency)
        # 启发式评估难度（综合 token 消耗和费用）
        actual_diff = feedback_analyzer.estimate_difficulty(
            {"choices": [{"message": {"content": collected_content}}]},
            cost=cost, completion_tokens=actual_out_tokens,
        )
        # 记录日志（含路由来源、prompt预览、请求模型名、token数）
        prompt_hash = _hash_prompt(prompt)
        prompt_preview = prompt[:200] if prompt else ""
        db.log_request(
            prompt_hash=prompt_hash,
            predicted_difficulty=diff,
            actual_difficulty=actual_diff,
            routed_model=selected["name"],
            cost=cost,
            latency_ms=latency_ms,
            success=success,
            task_type=task_type,
            cost_currency=target_currency,
            route_source=route_source,
            prompt_preview=prompt_preview,
            requested_model=requested_model_name or None,
            prompt_tokens=actual_in_tokens,
            completion_tokens=actual_out_tokens,
            route_chain=",".join(route_chain) if route_chain else None,
            content_types=",".join(content_types) if content_types else None,
        )
        # 本地扣账
        if cost > 0:
            pricing_manager.deduct(selected["name"], cost)
        # 训练样本（自动持久化到数据库）
        app.state.predictor.add_sample(prompt, actual_diff, actual_out_tokens, task_type=task_type, source="auto", model_name=selected["name"])
        # 自适应学习：从成功请求中学习关键词
        if task_type and success:
            task_type_detector.learn_keywords(task_type, prompt, success)
        # 更新 request.state 供 API 日志中间件读取
        try:
            request.state._api_prompt_tokens = actual_in_tokens
            request.state._api_completion_tokens = actual_out_tokens
            request.state._api_cost = cost
            request.state._api_cost_currency = target_currency
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 核心代理接口
    # ------------------------------------------------------------------ #
    @app.post("/v1/chat/completions")
    async def proxy(request: Request, _auth=Depends(check_api_key)):
        # 并发控制：使用信号量限制同时处理的上游请求数
        if _concurrency_semaphore is None:
            raise OpenAIError(503, "Service not ready", "server_error")
        # 非阻塞尝试获取信号量，失败则返回 429
        acquired = False
        try:
            acquired = _concurrency_semaphore._value > 0
            if not acquired:
                raise OpenAIError(429, "Too many concurrent requests, please retry later", "rate_limit_error")
            await asyncio.wait_for(_concurrency_semaphore.acquire(), timeout=0.1)
            acquired = True
        except asyncio.TimeoutError:
            raise OpenAIError(429, "Too many concurrent requests, please retry later", "rate_limit_error")
        try:
            return await _proxy_impl(request)
        finally:
            if acquired:
                _concurrency_semaphore.release()

    async def _proxy_impl(request: Request) -> StreamingResponse | JSONResponse:
        """代理请求的核心实现（从 proxy 中提取，便于并发控制包装）。"""
        body = await request.json()

        # 模型名映射：将请求中的模型名映射为实际模型名
        requested_model = body.get("model", "")
        is_auto_route = (requested_model == "auto" or requested_model == "")
        resolved_model = config.resolve_model_name(requested_model) if not is_auto_route else ""
        if resolved_model != requested_model and not is_auto_route:
            body["model"] = resolved_model

        prompt = _extract_prompt(body)
        content_types = _detect_content_types(body)
        has_multimodal = any(t != "text" for t in content_types)
        request_id = str(uuid.uuid4())
        # 设置 request.state 供 API 日志中间件读取
        request.state._api_request_id = request_id
        request.state._api_requested_model = requested_model or "auto"
        request.state._api_routed_model = None
        request.state._api_route_source = None
        request.state._api_prompt_tokens = 0
        request.state._api_completion_tokens = 0
        request.state._api_cost = 0.0
        request.state._api_cost_currency = config.currency
        request.state._api_prompt_preview = prompt[:200] if prompt else None

        # 1. 缓存命中检查（关键词定向优先级高于缓存）
        # 多模态请求不使用缓存（因为 prompt hash 不包含非文本内容）
        prompt_hash = _hash_prompt(prompt) if not has_multimodal else ""
        # 先检测关键词——如果用户明确指定了模型，忽略缓存
        keyword_model_early = detect_model_keyword(prompt, pricing_manager.get_available_models())
        if not keyword_model_early:
            cached_model = router.get_cached_route(prompt_hash) if prompt_hash else None
            if cached_model:
                # 检查缓存命中的模型是否仍在生效时间段内
                if not pricing_manager.is_within_active_hours(cached_model.get("active_hours")):
                    cached_model = None
                elif not cached_model.get("enabled", True):
                    cached_model = None
                # 检查缓存命中的模型是否支持请求的内容类型
                elif has_multimodal and not router._supports_content_types(cached_model, content_types):
                    cached_model = None
            if cached_model:
                task_type = detect_task_type(prompt)
                try:
                    return await _do_upstream(
                        request, cached_model, body, request_id, prompt, 3, 500,
                        task_type=task_type, requested_model_name=requested_model,
                        route_source="cache",
                        route_chain=[cached_model["name"]],
                    content_types=content_types,
                    )
                except UpstreamError:
                    # 缓存命中的模型被保险检查拒绝，继续走正常路由
                    pass

        # 2. 同步拦截预测
        diff, est_tok = app.state.predictor.predict(prompt)

        # 2.5 获取预测模型推荐
        predictor_rec = None
        try:
            predictor_rec_result = app.state.predictor.predict_with_model(prompt)
            if predictor_rec_result and predictor_rec_result.get("expected_model"):
                predictor_rec = predictor_rec_result["expected_model"]
        except Exception:
            pass

        # 3. 推断请求类型
        task_type = detect_task_type(prompt)

        # 3.5 关键词定向模型选择（使用早期检测结果，避免重复计算）
        keyword_model = keyword_model_early

        # 4. 智能路由
        # model="auto" 或 model="" 表示智能路由模式
        # model=具体名称 表示直连指定模型
        # 关键词定向优先级最高：用户在 prompt 中明确指定模型
        requested_model_name = body.get("model", "")
        selected = None
        route_source = "auto"
        if keyword_model:
            selected = config.get_model(keyword_model)
            if selected:
                # 检查模型是否在生效时间段内
                if not pricing_manager.is_within_active_hours(selected.get("active_hours")):
                    selected = None
                # 检查模型是否支持请求的内容类型
                elif has_multimodal and not app.state.router._supports_content_types(selected, content_types):
                    selected = None
                else:
                    route_source = "keyword"
        if not selected and (is_auto_route or requested_model_name == "auto"):
            # 智能路由模式（双机制：预测推荐 + 评分）
            selected = app.state.router.select_model(diff, est_tok, est_tok, task_type=task_type, predictor_recommendation=predictor_rec, content_types=content_types)
            route_source = "auto"
        elif not selected and requested_model_name:
            selected = config.get_model(requested_model_name)
            if not selected:
                raise OpenAIError(
                    404,
                    f"The model `{requested_model_name}` does not exist",
                    "invalid_request_error",
                )
            # 检查模型是否在生效时间段内
            if not pricing_manager.is_within_active_hours(selected.get("active_hours")):
                raise OpenAIError(
                    403,
                    f"The model `{requested_model_name}` is not available at this time (outside active hours)",
                    "invalid_request_error",
                )
            # 检查模型是否支持请求的内容类型
            if has_multimodal and not app.state.router._supports_content_types(selected, content_types):
                raise OpenAIError(
                    400,
                    f"The model `{requested_model_name}` does not support the requested content types: {', '.join(t for t in content_types if t != 'text')}. "
                    f"Please use a model that supports multimodal input.",
                    "invalid_request_error",
                )
            route_source = "direct"

        if not selected:
            selected = app.state.router.select_model(diff, est_tok, est_tok, task_type=task_type, predictor_recommendation=predictor_rec, content_types=content_types)
            route_source = "auto"
        if not selected:
            # 使用 fallback 模型
            selected = config.get_fallback_model()
            # 检查 fallback 模型是否在生效时间段内
            if selected and not pricing_manager.is_within_active_hours(selected.get("active_hours")):
                selected = None
            if selected:
                route_source = "fallback"
            if not selected:
                raise OpenAIError(503, "No model available (all models are outside their active hours or disabled)", "server_error")

        # 记录路由信息到 request.state（供 API 日志中间件读取）
        request.state._api_routed_model = selected["name"]
        request.state._api_route_source = route_source

        # 5. 代理请求（含后备链重试）
        # 非流式和流式请求都支持后备链重试
        is_stream = body.get("stream", False)

        # 预先计算完整的后备链（严格匹配 → 宽松匹配 → 兜底），避免重复计算权重
        strict_chain = app.state.router.select_fallback_chain(
            diff, est_tok, est_tok,
            failed_models=[selected["name"]],
            task_type=task_type,
            strict_capability=True,
            content_types=content_types,
        )
        loose_chain = app.state.router.select_fallback_chain(
            diff, est_tok, est_tok,
            failed_models=[selected["name"]],
            task_type=task_type,
            strict_capability=False,
            content_types=content_types,
        )
        # 宽松链中去除严格链已有的模型（避免重复）
        strict_names = {m["name"] for m in strict_chain}
        loose_only = [m for m in loose_chain if m["name"] not in strict_names]
        # 兜底模型
        fb_model = config.get_fallback_model()
        fb_list = []
        if fb_model and fb_model["name"] != selected["name"] and fb_model["name"] not in strict_names and pricing_manager.is_within_active_hours(fb_model.get("active_hours")):
            fb_list = [fb_model]
        # 完整后备链：严格匹配 → 宽松降级 → 兜底
        full_fallback_chain = strict_chain + loose_only + fb_list
        # 记录路由链路信息
        route_chain_info = [selected["name"]] + [m["name"] for m in full_fallback_chain]

        # 流式模式：先预检上游连接，失败则触发后备链
        if is_stream:
            failed_models_stream: List[str] = []
            last_error_stream: Optional[UpstreamError] = None

            for attempt in range(len(full_fallback_chain) + 1):
                if attempt == 0:
                    current_model = selected
                    current_route_source = route_source
                    is_retry = False
                else:
                    # 从预计算的后备链中取下一个模型
                    if attempt - 1 >= len(full_fallback_chain):
                        break
                    current_model = full_fallback_chain[attempt - 1]
                    current_route_source = "fallback"
                    is_retry = True
                    _log_fallback(
                        original_model=selected["name"],
                        fallback_model=current_model["name"],
                        attempt=attempt,
                        failed_models=failed_models_stream,
                        error=last_error_stream.message if last_error_stream else "",
                        prompt_preview=prompt[:100],
                        extra={"route_chain": route_chain_info},
                    )

                # 预检：先发一个轻量请求检查上游是否可用
                upstream_ok, upstream_error = await _check_upstream_available(
                    current_model, body
                )
                if upstream_ok:
                    # 上游可用，开始流式传输
                    try:
                        return await _do_upstream(
                            request,
                            current_model, body, request_id, prompt, diff, est_tok,
                            task_type=task_type, requested_model_name=requested_model,
                            route_source=current_route_source,
                            is_fallback_retry=is_retry,
                            route_chain=route_chain_info,
                        content_types=content_types,
                        )
                    except UpstreamError as e:
                        # 保险检查拒绝的模型也触发后备链重试
                        failed_models_stream.append(e.model_name)
                        last_error_stream = e
                        app.state.router._degrade_reliability(e.model_name)
                        continue
                else:
                    # 上游不可用，记录失败并尝试下一个模型
                    failed_models_stream.append(current_model["name"])
                    last_error_stream = UpstreamError(
                        model_name=current_model["name"],
                        status_code=upstream_error.get("status_code", 502),
                        message=upstream_error.get("message", "upstream check failed"),
                    )
                    app.state.router._degrade_reliability(current_model["name"])
                    continue

            # 所有后备模型都失败
            fallback_logger.log_fallback_exhausted(
                original_model=selected["name"],
                failed_models=failed_models_stream,
                error=last_error_stream.message if last_error_stream else "",
                prompt_preview=prompt[:100],
            )
            raise OpenAIError(
                502,
                f"All models failed. Tried: {', '.join(failed_models_stream)}. Last error: {last_error_stream.message if last_error_stream else 'unknown'}",
                "server_error",
            )

        # 非流式模式：实现后备链重试
        failed_models: List[str] = []
        last_error: Optional[UpstreamError] = None

        for attempt in range(len(full_fallback_chain) + 1):
            if attempt == 0:
                # 首次尝试：使用选中的模型
                current_model = selected
                current_route_source = route_source
                is_retry = False
            else:
                # 从预计算的后备链中取下一个模型
                if attempt - 1 >= len(full_fallback_chain):
                    break
                current_model = full_fallback_chain[attempt - 1]
                current_route_source = "fallback"
                is_retry = True
                # 记录后备链日志
                _log_fallback(
                    original_model=selected["name"],
                    fallback_model=current_model["name"],
                    attempt=attempt,
                    failed_models=failed_models,
                    error=last_error.message if last_error else "",
                    prompt_preview=prompt[:100],
                    extra={"route_chain": route_chain_info},
                )

            try:
                return await _do_upstream(
                    request,
                    current_model, body, request_id, prompt, diff, est_tok,
                    task_type=task_type, requested_model_name=requested_model,
                    route_source=current_route_source,
                    is_fallback_retry=is_retry,
                    route_chain=route_chain_info,
                content_types=content_types,
                )
            except UpstreamError as e:
                failed_models.append(e.model_name)
                last_error = e
                # 降低失败模型的可靠性
                app.state.router._degrade_reliability(e.model_name)
                continue

        # 所有后备模型都失败
        fallback_logger.log_fallback_exhausted(
            original_model=selected["name"],
            failed_models=failed_models,
            error=last_error.message if last_error else "",
            prompt_preview=prompt[:100],
        )
        raise OpenAIError(
            502,
            f"All models failed. Tried: {', '.join(failed_models)}. Last error: {last_error.message if last_error else 'unknown'}",
            "server_error",
        )

    @app.get("/v1/models")
    async def list_models(_auth=Depends(check_api_key)):
        """兼容 OpenAI /v1/models 接口。

        同时暴露模型别名和智能路由模式标识，使 OpenClaw 等客户端可以用 gpt-4 等名称连接。
        model="auto" 表示智能路由模式，由系统自动选择最优模型。
        """
        models = config.get_models()
        # 只暴露启用的且在生效时间段内的模型
        models = [m for m in models if m.get("enabled", True)]
        models = [m for m in models if pricing_manager.is_within_active_hours(m.get("active_hours"))]
        aliases = config.model_aliases
        now = int(time.time())
        data = [
            {
                "id": m["name"],
                "object": "model",
                "created": now,
                "owned_by": m.get("api_type", "unknown"),
                "modalities": m.get("modalities", ["text"]),
            }
            for m in models
        ]
        # 添加智能路由模式标识
        data.insert(0, {
            "id": "auto",
            "object": "model",
            "created": now,
            "owned_by": "smart-router",
        })
        # 添加别名模型（指向实际模型）
        existing_ids = {d["id"] for d in data}
        for alias, target in aliases.items():
            if alias not in existing_ids:
                target_model = config.get_model(target)
                data.append({
                    "id": alias,
                    "object": "model",
                    "created": now,
                    "owned_by": target_model.get("api_type", "unknown") if target_model else "unknown",
                })
        return {"object": "list", "data": data}

    # ------------------------------------------------------------------ #
    # 标准余额查询接口（兼容 OpenAI 格式）
    # ------------------------------------------------------------------ #
    @app.get("/v1/balance")
    async def get_balance(_auth=Depends(check_api_key)):
        """标准 Token 余额查询接口。

        返回所有启用模型的余额信息，格式兼容 OpenAI 风格，
        便于 OpenClaw 等客户端理解和使用。

        响应格式：
        {
            "object": "balance_list",
            "data": [
                {
                    "model": "model-name",
                    "balance": 100.0,
                    "currency": "CNY",
                    "balance_source": "manual|api|local_estimate",
                    "is_frozen": false
                }
            ]
        }
        """
        models = config.get_models()
        target_currency = config.currency
        result = []
        loop = asyncio.get_event_loop()
        for m in models:
            if not m.get("enabled", True):
                continue
            # 余额来源判断
            if m.get("balance_manual") is not None:
                balance = float(m["balance_manual"])
                source = "manual"
            elif m.get("balance_frozen"):
                balance = pricing_manager._get_cached_balance(m)
                source = "cached"
            else:
                balance = await loop.run_in_executor(None, pricing_manager._get_balance, m)
                source = "api"
            # 余额货币转换
            balance_currency = m.get("balance_currency", m.get("price_currency", "USD"))
            if balance_currency != target_currency and balance is not None:
                balance = exchange_rate_manager.convert(balance, balance_currency, target_currency)
                balance_currency = target_currency
            result.append({
                "model": m["name"],
                "balance": round(balance, 6) if balance is not None else None,
                "currency": balance_currency,
                "balance_source": source,
                "is_frozen": m.get("balance_frozen", False),
            })
        return {
            "object": "balance_list",
            "data": result,
        }

    @app.get("/v1/balance/{model_name}")
    async def get_model_balance(model_name: str, _auth=Depends(check_api_key)):
        """查询单个模型的 Token 余额。"""
        resolved = config.resolve_model_name(model_name)
        m = config.get_model(resolved)
        if not m or not m.get("enabled", True):
            raise OpenAIError(404, f"The model `{model_name}` does not exist")
        target_currency = config.currency
        if m.get("balance_manual") is not None:
            balance = float(m["balance_manual"])
            source = "manual"
        elif m.get("balance_frozen"):
            balance = pricing_manager._get_cached_balance(m)
            source = "cached"
        else:
            loop = asyncio.get_event_loop()
            balance = await loop.run_in_executor(None, pricing_manager._get_balance, m)
            source = "api"
        balance_currency = m.get("balance_currency", m.get("price_currency", "USD"))
        if balance_currency != target_currency and balance is not None:
            balance = exchange_rate_manager.convert(balance, balance_currency, target_currency)
            balance_currency = target_currency
        return {
            "object": "balance",
            "model": m["name"],
            "balance": round(balance, 6) if balance is not None else None,
            "currency": balance_currency,
            "balance_source": source,
            "is_frozen": m.get("balance_frozen", False),
        }

    # ------------------------------------------------------------------ #
    # 反馈接口
    # ------------------------------------------------------------------ #
    @app.post("/v1/feedback")
    async def submit_feedback(request: Request, _auth=Depends(check_api_key)):
        body = await request.json()
        request_id = body.get("request_id")
        sentiment = body.get("sentiment")  # positive / negative
        context = body.get("context_snapshot", "")
        if sentiment not in ("positive", "negative"):
            raise HTTPException(status_code=400, detail="sentiment 必须为 positive/negative")

        # 尝试取出原 prompt 与预测难度用于纠偏
        prompt = body.get("prompt", "")
        predicted_diff = body.get("predicted_difficulty")
        task_type = body.get("task_type")
        feedback_analyzer.record_explicit(
            request_id=request_id,
            sentiment=sentiment,
            context_snapshot=context,
            predictor=app.state.predictor,
            prompt=prompt,
            predicted_difficulty=predicted_diff,
        )
        # 自适应学习：从反馈中调整 task_type 权重
        if task_type:
            task_type_detector.learn_from_feedback(task_type, sentiment)
        return {"status": "ok", "sentiment": sentiment}

    # ------------------------------------------------------------------ #
    # 管理面板登录
    # ------------------------------------------------------------------ #
    @app.post("/admin/api/login")
    async def admin_login(request: Request):
        body = await request.json()
        password = body.get("password", "")
        if not verify_password(password):
            raise HTTPException(status_code=401, detail="密码错误")
        token = create_access_token("admin")
        return {"token": token, "token_type": "bearer"}

    @app.post("/admin/api/change-password")
    async def admin_change_password(request: Request, _admin=Depends(require_admin)):
        body = await request.json()
        new_password = body.get("new_password", "")
        if len(new_password) < 4:
            raise HTTPException(status_code=400, detail="密码长度至少4位")
        # Web 设置的密码优先级最高：同时更新 config.yaml、环境变量和 .env 文件
        config.set("admin_password", new_password)
        try:
            config.save()
        except Exception:
            pass
        os.environ["SMARTROUTER_ADMIN_PASSWORD"] = new_password
        config.sync_env_to_dotenv("SMARTROUTER_ADMIN_PASSWORD", new_password)
        return {"status": "ok"}

    @app.post("/admin/api/change-ssh-key")
    async def admin_change_ssh_key(request: Request, _admin=Depends(require_admin)):
        body = await request.json()
        new_key = body.get("ssh_key", "")
        # Web 设置的 SSH Key 优先级最高：同时更新 config.yaml、环境变量和 .env 文件
        config.set("ssh_key", new_key)
        try:
            config.save()
        except Exception:
            pass
        os.environ["SMARTROUTER_SSH_KEY"] = new_key
        config.sync_env_to_dotenv("SMARTROUTER_SSH_KEY", new_key)
        return {"status": "ok"}

    # ------------------------------------------------------------------ #
    # 控制面板后端 API（需认证）
    # ------------------------------------------------------------------ #
    @app.get("/admin/api/dashboard")
    async def api_dashboard(period: str = "today", _admin=Depends(require_admin)):
        """仪表盘统计。period: today/week/month/all"""
        # 缓存10秒，避免频繁刷新时重复查询数据库
        cache_key = f"dashboard:{period}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        now = time.time()
        if period == "today":
            since = now - 86400
        elif period == "week":
            since = now - 7 * 86400
        elif period == "month":
            since = now - 30 * 86400
        elif period == "year":
            since = now - 365 * 86400
        else:
            since = None  # all
        data = db.get_dashboard_stats(since)
        # 汇率转换：将 saved_cost_by_currency 转为当前显示货币
        target_currency = config.currency
        total_saved = 0.0
        for item in data.get("saved_cost_by_currency", []):
            cur = item.get("cost_currency", "USD")
            val = float(item.get("s", 0))
            total_saved += float(exchange_rate_manager.convert(val, cur, target_currency))
        data["saved_cost"] = round(total_saved, 6)
        data["cost_currency"] = target_currency
        # model_token_stats 中的 total_cost 也做汇率转换
        for m in data.get("model_token_stats", []):
            if m.get("total_cost") and m.get("cost_currency") and m["cost_currency"] != target_currency:
                m["total_cost"] = round(float(exchange_rate_manager.convert(
                    m["total_cost"], m["cost_currency"], target_currency)), 6)
                m["cost_currency"] = target_currency
        _cache_set(cache_key, data, ttl=10.0)
        return data

    @app.get("/admin/api/models")
    async def api_models(_admin=Depends(require_admin)):
        """管理面板模型列表：显示所有模型（包括禁用的），附带余额和价格。

        性能优化：余额和价格查询仅使用缓存/配置值，不触发实时网络请求，
        避免同步 HTTP 请求阻塞事件循环导致面板白屏。
        实时余额由后台定时任务或 /v1/balance 接口单独更新。
        """
        models = config.get_models()
        target_currency = config.currency
        # 批量获取所有模型指标（避免逐个查询数据库）
        try:
            all_metrics = {m["model_name"]: m for m in db.get_all_metrics()}
        except Exception:
            all_metrics = {}
        for m in models:
            # 余额处理：优先使用手动设定值，其次内存缓存，最后批量指标
            bm = m.get("balance_manual")
            if bm is not None and bm != "":
                m["balance"] = bm
            else:
                # 仅查内存缓存（不查数据库，避免 77 次 DB 查询）
                with pricing_manager._lock:
                    cached = pricing_manager._balance_cache.get(m.get("name", ""))
                    m["balance"] = cached[1] if cached else None
                # 内存缓存未命中时，从批量指标获取
                if m.get("balance") is None:
                    metrics = all_metrics.get(m.get("name", ""))
                    if metrics and metrics.get("last_balance") is not None:
                        m["balance"] = metrics["last_balance"]

            # 价格获取：仅使用配置中的值，不执行价格脚本
            # （价格脚本可能包含同步网络请求，会阻塞事件循环）
            price_input = m.get("price_input", 0)
            price_output = m.get("price_output", 0)
            model_currency = m.get("price_currency", target_currency)
            model_unit = m.get("price_unit", "1M")

            # 价格货币转换
            if model_currency != target_currency:
                m["price_input_display"] = round(
                    exchange_rate_manager.convert(price_input, model_currency, target_currency), 8)
                m["price_output_display"] = round(
                    exchange_rate_manager.convert(price_output, model_currency, target_currency), 8)
                m["price_currency_display"] = target_currency
            else:
                m["price_input_display"] = price_input
                m["price_output_display"] = price_output
                m["price_currency_display"] = target_currency
            m["price_unit_display"] = model_unit

            # 脱敏 API Key
            if m.get("api_key"):
                key = m["api_key"]
                m["api_key"] = key[:8] + "***" if len(key) > 8 else "***"
            # 脱敏 Provider 中的 API Key（防止泄露）
            if m.get("_provider") and m["_provider"].get("api_key"):
                pkey = m["_provider"]["api_key"]
                m["_provider"]["api_key"] = pkey[:8] + "***" if len(pkey) > 8 else "***"
            # 判断当前是否在生效时间段内
            m["is_active_now"] = pricing_manager.is_within_active_hours(m.get("active_hours"))
        return {"models": models}

    @app.post("/admin/api/models")
    async def api_update_models(request: Request, _admin=Depends(require_admin)):
        body = await request.json()
        models_data = body.get("models", [])
        # 保存前，合并已有的 api_key（前端传来的可能是脱敏的）
        existing_models = config.get("models", [])
        existing_keys = {m["name"]: m.get("api_key", "") for m in existing_models}
        for m in models_data:
            key = m.get("api_key", "")
            if key.endswith("***"):
                # 前端传回的脱敏 key
                if m.get("_api_key_inherited"):
                    # 继承自提供方的 key，不写入模型配置（让运行时从 provider 继承）
                    m.pop("api_key", None)
                else:
                    # 模型自有的 key，恢复原值
                    original = existing_keys.get(m["name"], "")
                    if original:
                        m["api_key"] = original
                    else:
                        # 原始配置中也没有 key，说明是继承的，移除
                        m.pop("api_key", None)
            # 处理 capability_manual：如果自动模式，删除 capability 让后端自动计算
            if not m.get("capability_manual", False):
                m.pop("capability", None)
            # 清理前端辅助字段，不写入 config.yaml
            m.pop("capability_manual", None)
            m.pop("balance", None)
            m.pop("price_input_display", None)
            m.pop("price_output_display", None)
            m.pop("price_currency_display", None)
            m.pop("is_active_now", None)
            m.pop("active_hours_list", None)
            m.pop("_provider", None)
            m.pop("_api_key_inherited", None)
            m.pop("owned_by", None)
        config.set("models", models_data)
        try:
            config.save()
        except Exception:
            pass
        return {"status": "ok"}

    # ------------------------------------------------------------------ #
    # 模型批量操作 API（必须在 {model_name:path} 路由之前注册）
    # ------------------------------------------------------------------ #
    async def _detect_single_modality(model_cfg: Dict[str, Any]) -> Dict[str, Any]:
        """检测单个模型的模态支持（内部函数，供并发调用）。"""
        model_name = model_cfg.get("name", "unknown")
        detected = {"text"}
        method = "probe"

        base_url = model_cfg.get("base_url", "").rstrip("/")
        api_key = model_cfg.get("api_key", "")
        api_type = model_cfg.get("api_type", "openai")
        upstream_model = model_cfg.get("upstream_model_name") or model_name

        if not base_url or not base_url.startswith("http"):
            return {"modalities": ["text"], "method": "skip", "detail": "缺少或无效 base_url"}
        if api_key and api_key.startswith("YOUR_"):
            return {"modalities": ["text"], "method": "skip", "detail": "API Key 为占位符"}

        headers = {"Content-Type": "application/json"}
        if api_key:
            if api_type == "zhipu":
                from core.pricing_manager import ZhipuBalanceChecker
                jwt_token = ZhipuBalanceChecker.generate_token(api_key)
                headers["Authorization"] = f"Bearer {jwt_token}" if jwt_token else f"Bearer {api_key}"
            else:
                headers["Authorization"] = f"Bearer {api_key}"

        client = await _get_http_client()

        try:
            models_url = f"{base_url}/models/{upstream_model}"
            resp = await client.get(models_url, headers=headers, timeout=8.0)
            if resp.status_code == 200:
                model_info = resp.json()
                model_modalities = model_info.get("modalities") or model_info.get("capabilities", {}).get("modalities")
                if model_modalities and isinstance(model_modalities, list):
                    for mod in model_modalities:
                        mod_lower = mod.lower() if isinstance(mod, str) else str(mod).lower()
                        if mod_lower in ("text", "image", "audio", "video", "file"):
                            detected.add(mod_lower)
                        elif "image" in mod_lower or "vision" in mod_lower:
                            detected.add("image")
                        elif "audio" in mod_lower or "speech" in mod_lower:
                            detected.add("audio")
                        elif "video" in mod_lower:
                            detected.add("video")
                    method = "query"
        except Exception:
            pass

        # 名称推断：基于模型名称关键词推断模态支持
        if method == "probe":
            name_lower = upstream_model.lower()
            # 多模态模型名称模式
            _NAME_MODALITY_HINTS = {
                "image": ["vision", "gpt-4o", "gpt-4-turbo", "gpt-4v", "claude-3", "gemini", "qwen-vl",
                          "qwen2-vl", "glm-4v", "step-1v", "yi-vision", "internvl", "cogvlm",
                          "llava", "minicpm-v", "pixtral"],
                "audio": ["whisper", "tts", "speech", "audio", "gpt-4o-audio", "glm-4-voice",
                          "qwen-audio", "qwen2-audio"],
                "video": ["video", "gpt-4o-video"],
            }
            for modality, keywords in _NAME_MODALITY_HINTS.items():
                for kw in keywords:
                    if kw in name_lower:
                        detected.add(modality)
                        method = "name_infer"
                        break
            # 名称推断后仍可继续探测以补充更多模态

        if method == "probe" or method == "name_infer":
            chat_url = f"{base_url}/chat/completions"

            async def probe_image():
                try:
                    image_body = {
                        "model": upstream_model,
                        "messages": [{"role": "user", "content": [
                            {"type": "text", "text": "Describe"},
                            {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="}}
                        ]}],
                        "max_tokens": 1, "stream": False,
                    }
                    resp = await client.post(chat_url, json=image_body, headers=headers, timeout=10.0)
                    if resp.status_code < 400:
                        detected.add("image")
                except Exception:
                    pass

            async def probe_audio():
                try:
                    audio_body = {
                        "model": upstream_model,
                        "messages": [{"role": "user", "content": [
                            {"type": "text", "text": "Listen"},
                            {"type": "input_audio", "input_audio": {"data": "dGVzdA==", "format": "wav"}}
                        ]}],
                        "max_tokens": 1, "stream": False,
                    }
                    resp = await client.post(chat_url, json=audio_body, headers=headers, timeout=10.0)
                    if resp.status_code < 400:
                        detected.add("audio")
                except Exception:
                    pass

            await asyncio.gather(probe_image(), probe_audio())

        final_modalities = sorted(detected, key=lambda x: ["text", "image", "audio", "video", "file"].index(x) if x in ["text", "image", "audio", "video", "file"] else 99)
        return {"modalities": final_modalities, "method": method}

    async def _detect_single_balance(model_cfg: Dict[str, Any]) -> Dict[str, Any]:
        """检测单个模型余额。"""
        model_name = model_cfg.get("name", "unknown")
        try:
            loop = asyncio.get_event_loop()
            balance = await loop.run_in_executor(None, pricing_manager.refresh_balance, model_name)
            return {"balance": balance, "method": "query"}
        except Exception as e:
            return {"balance": None, "method": "error", "detail": str(e)[:100]}

    async def _detect_single_price(model_cfg: Dict[str, Any]) -> Dict[str, Any]:
        """检测单个模型价格。"""
        model_name = model_cfg.get("name", "unknown")
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, pricing_manager.get_model_price_from_script, model_cfg)
            if result:
                return {"price_input": result.get("price_input"), "price_output": result.get("price_output"), "method": "query"}
            return {"method": "skip", "detail": "无价格脚本"}
        except Exception as e:
            return {"method": "error", "detail": str(e)[:100]}

    @app.post("/admin/api/models/detect-modalities")
    async def api_detect_modalities(request: Request, _admin=Depends(require_admin)):
        """自动检测模型支持的模态（并发执行）。

        检测结果默认写入 pending_modalities，需确认后才生效。
        save=True 时写入 pending_modalities 而非直接写入 modalities。
        """
        body = await request.json()
        model_names = body.get("model_names", [])
        save = body.get("save", False)

        if not model_names:
            raise HTTPException(status_code=400, detail="model_names 不能为空")

        models = config.get_models()
        raw_models = config.get("models", [])

        model_cfg_map = {}
        for m in models:
            name = m.get("name")
            if name and name in model_names:
                model_cfg_map[name] = m

        sem = asyncio.Semaphore(10)

        async def detect_with_sem(name):
            cfg = model_cfg_map.get(name)
            if not cfg:
                return name, {"modalities": ["text"], "method": "not_found", "detail": "模型未找到"}
            async with sem:
                result = await _detect_single_modality(cfg)
                return name, result

        tasks = [detect_with_sem(name) for name in model_names]
        task_results = await asyncio.gather(*tasks, return_exceptions=True)

        import time as _time
        now_ts = _time.time()
        results = {}
        for tr in task_results:
            if isinstance(tr, Exception):
                continue
            name, result = tr
            results[name] = result
            if save:
                for m in raw_models:
                    if m.get("name") == name:
                        # 写入 pending_modalities 而非直接写入 modalities
                        m["pending_modalities"] = result["modalities"]
                        m["pending_modalities_detected_at"] = now_ts
                        break

        if save:
            config.set("models", raw_models)
            try:
                config.save()
            except Exception:
                pass

        return {"results": results}

    @app.post("/admin/api/models/detect-modalities/stream")
    async def api_detect_modalities_stream(request: Request, _admin=Depends(require_admin)):
        """流式检测模型模态，通过 SSE 逐个返回进度。

        模态检测结果写入 pending_modalities，需确认后才生效。
        """
        body = await request.json()
        model_names = body.get("model_names", [])
        save = body.get("save", False)
        action = body.get("action", "modalities")

        if not model_names:
            raise HTTPException(status_code=400, detail="model_names 不能为空")

        models = config.get_models()
        raw_models = config.get("models", [])

        model_cfg_map = {}
        for m in models:
            name = m.get("name")
            if name and name in model_names:
                model_cfg_map[name] = m

        import time as _time

        async def event_stream():
            total = len(model_names)
            completed = 0
            now_ts = _time.time()
            for model_name in model_names:
                cfg = model_cfg_map.get(model_name)
                if not cfg:
                    completed += 1
                    result = {"modalities": ["text"], "method": "not_found", "detail": "模型未找到"}
                    yield f"data: {json.dumps({'model': model_name, 'result': result, 'progress': completed, 'total': total, 'action': action})}\n\n"
                    continue

                if action == "balance":
                    result = await _detect_single_balance(cfg)
                elif action == "price":
                    result = await _detect_single_price(cfg)
                else:
                    result = await _detect_single_modality(cfg)

                completed += 1

                if save:
                    for m in raw_models:
                        if m.get("name") == model_name:
                            if action == "modalities" and "modalities" in result:
                                # 写入 pending_modalities 而非直接写入 modalities
                                m["pending_modalities"] = result["modalities"]
                                m["pending_modalities_detected_at"] = now_ts
                            elif action == "balance" and "balance" in result:
                                m["balance_manual"] = result["balance"]
                            elif action == "price" and result.get("method") == "query":
                                if result.get("price_input") is not None:
                                    m["price_input"] = result["price_input"]
                                if result.get("price_output") is not None:
                                    m["price_output"] = result["price_output"]
                            break

                yield f"data: {json.dumps({'model': model_name, 'result': result, 'progress': completed, 'total': total, 'action': action}, ensure_ascii=False)}\n\n"

            if save:
                config.set("models", raw_models)
                try:
                    config.save()
                except Exception:
                    pass

            yield f"data: {json.dumps({'done': True, 'total': total, 'action': action})}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/admin/api/models/confirm-modalities")
    async def api_confirm_modalities(request: Request, _admin=Depends(require_admin)):
        """确认待生效的模态检测结果，将其应用到 modalities 字段。

        也可用于拒绝（discard=True），清除 pending_modalities。
        """
        body = await request.json()
        model_names = body.get("model_names", [])
        discard = body.get("discard", False)

        if not model_names:
            raise HTTPException(status_code=400, detail="model_names 不能为空")

        raw_models = config.get("models", [])
        updated = []

        for m in raw_models:
            name = m.get("name")
            if name and name in model_names:
                pending = m.get("pending_modalities")
                if pending is not None:
                    if discard:
                        # 拒绝：清除 pending 状态
                        m["pending_modalities"] = None
                        m["pending_modalities_detected_at"] = None
                    else:
                        # 确认：将 pending 应用到 modalities
                        m["modalities"] = pending
                        m["pending_modalities"] = None
                        m["pending_modalities_detected_at"] = None
                    updated.append(name)

        if updated:
            config.set("models", raw_models)
            try:
                config.save()
            except Exception:
                pass

        return {"updated": updated, "action": "discard" if discard else "confirm"}

    @app.post("/admin/api/models/apply-pending-modalities")
    async def api_apply_pending_modalities(_admin=Depends(require_admin)):
        """将超过24h未确认的 pending_modalities 自动应用到 modalities。

        可由定时任务或手动触发。
        """
        import time as _time
        AUTO_CONFIRM_SECONDS = 24 * 3600  # 24小时
        now_ts = _time.time()

        raw_models = config.get("models", [])
        auto_applied = []

        for m in raw_models:
            pending = m.get("pending_modalities")
            detected_at = m.get("pending_modalities_detected_at")
            if pending is not None and detected_at is not None:
                if now_ts - detected_at >= AUTO_CONFIRM_SECONDS:
                    m["modalities"] = pending
                    m["pending_modalities"] = None
                    m["pending_modalities_detected_at"] = None
                    auto_applied.append(m.get("name"))

        if auto_applied:
            config.set("models", raw_models)
            try:
                config.save()
            except Exception:
                pass

        return {"auto_applied": auto_applied, "count": len(auto_applied)}

    @app.post("/admin/api/models/batch-update")
    async def api_batch_update_models(request: Request, _admin=Depends(require_admin)):
        """批量更新模型配置字段。"""
        body = await request.json()
        model_names = body.get("model_names", [])
        updates = body.get("updates", {})

        if not model_names:
            raise HTTPException(status_code=400, detail="model_names 不能为空")
        if not updates:
            raise HTTPException(status_code=400, detail="updates 不能为空")

        models = config.get("models", [])
        updated = 0
        for m in models:
            if m.get("name") in model_names:
                for key, value in updates.items():
                    m[key] = value
                updated += 1

        if updated > 0:
            config.set("models", models)
            try:
                config.save()
            except Exception:
                pass

        return {"status": "ok", "updated": updated}

    @app.post("/admin/api/models/{model_name:path}/clone")
    async def api_clone_model(model_name: str, request: Request, _admin=Depends(require_admin)):
        """克隆模型配置，生成一个新模型。"""
        body = await request.json()
        new_name = body.get("new_name", f"{model_name}-copy")
        # 从原始 YAML 数据中查找模型（避免 enrich 后的冗余字段）
        models_data = config.get("models", [])
        existing = None
        for m in models_data:
            if m.get("name") == model_name:
                existing = m
                break
        if not existing:
            raise HTTPException(status_code=404, detail="模型不存在")
        # 检查新名称是否已存在
        for m in models_data:
            if m.get("name") == new_name:
                raise HTTPException(status_code=400, detail=f"模型 {new_name} 已存在")
        # 克隆配置（深拷贝原始数据，仅修改名称）
        new_model = copy.deepcopy(existing)
        new_model["name"] = new_name
        models_data.append(new_model)
        config.set("models", models_data)
        try:
            config.save()
        except Exception:
            pass
        return {"status": "ok", "name": new_name}

    @app.get("/admin/api/models/{model_name:path}/config")
    async def api_get_model_config(model_name: str, _admin=Depends(require_admin)):
        """获取单个模型的原始 YAML 配置。"""
        models = config.get("models", [])
        for m in models:
            if m.get("name") == model_name:
                return {"model": m}
        raise HTTPException(status_code=404, detail="模型不存在")

    @app.put("/admin/api/models/{model_name:path}/config")
    async def api_update_model_config(model_name: str, request: Request, _admin=Depends(require_admin)):
        """直接更新单个模型的 YAML 配置。"""
        body = await request.json()
        model_config = body.get("model", {})
        if not model_config:
            raise HTTPException(status_code=400, detail="model 配置不能为空")
        models = config.get("models", [])
        found = False
        for i, m in enumerate(models):
            if m.get("name") == model_name:
                # 保留 name 不变
                model_config["name"] = model_name
                models[i] = model_config
                found = True
                break
        if not found:
            raise HTTPException(status_code=404, detail="模型不存在")
        config.set("models", models)
        try:
            config.save()
        except Exception:
            pass
        return {"status": "ok"}

    @app.post("/admin/api/models/{model_name:path}/test")
    async def api_test_model(model_name: str, _admin=Depends(require_admin)):
        loop = asyncio.get_event_loop()
        balance = await loop.run_in_executor(None, pricing_manager.refresh_balance, model_name)
        return {"model": model_name, "balance": balance}

    # ------------------------------------------------------------------ #
    # Provider（提供方）管理 API
    # ------------------------------------------------------------------ #
    @app.get("/admin/api/providers")
    async def api_providers(_admin=Depends(require_admin)):
        """获取所有提供方列表。"""
        providers = config.get_providers()
        # 只读取一次模型列表，避免 N 个 provider 重复读取
        models = config.get("models", [])
        for p in providers:
            if p.get("api_key"):
                key = p["api_key"]
                p["api_key"] = key[:8] + "***" if len(key) > 8 else "***"
            p["model_count"] = sum(1 for m in models if m.get("provider") == p["name"])
        return {"providers": providers}

    @app.post("/admin/api/providers")
    async def api_update_providers(request: Request, _admin=Depends(require_admin)):
        """更新提供方列表。"""
        body = await request.json()
        providers_data = body.get("providers", [])
        # 保存前，合并已有的 api_key（前端传来的可能是脱敏的）
        existing_providers = config.get("providers", [])
        existing_keys = {p["name"]: p.get("api_key", "") for p in existing_providers}
        for p in providers_data:
            key = p.get("api_key", "")
            if key.endswith("***"):
                original = existing_keys.get(p["name"], "")
                if original:
                    p["api_key"] = original
            # 清理前端辅助字段
            p.pop("model_count", None)
        config.set("providers", providers_data)
        try:
            config.save()
        except Exception:
            pass
        return {"status": "ok"}

    @app.post("/admin/api/providers/{provider_name}/test-balance")
    async def api_test_provider_balance(provider_name: str, _admin=Depends(require_admin)):
        """测试提供方的余额获取（通过脚本或内置策略）。"""
        provider = config.get_provider(provider_name)
        if not provider:
            raise HTTPException(status_code=404, detail="提供方不存在")
        # 尝试通过脚本获取余额
        from core.script_engine import execute_balance_script
        balance = None
        balance_currency = None
        if provider.get("balance_script", "").strip():
            loop = asyncio.get_event_loop()
            script_result = await loop.run_in_executor(
                None, execute_balance_script,
                provider["balance_script"], provider.get("api_key", ""), provider.get("base_url", ""), ""
            )
            if script_result is not None:
                balance = script_result["balance"]
                balance_currency = script_result.get("balance_currency")
        if balance is None and provider.get("balance_manual") is not None:
            balance = provider["balance_manual"]
            balance_currency = provider.get("balance_currency")
        if balance is None:
            # 尝试内置策略
            api_type = provider.get("api_type", "local")
            checker = BalanceCheckerFactory.get_checker(api_type)
            try:
                loop = asyncio.get_event_loop()
                balance = await loop.run_in_executor(None, checker.check, provider)
                if balance is not None:
                    balance_currency = pricing_manager._infer_balance_currency(provider, api_type)
            except Exception:
                pass
        # 推断货币单位
        if balance is not None and balance_currency is None:
            api_type = provider.get("api_type", "local")
            balance_currency = pricing_manager._infer_balance_currency(provider, api_type)
        # 低余额通知
        if balance is not None and balance <= 0:
            notifier.notify(
                event=f"balance_exhausted:{provider_name}",
                severity="critical",
                title=f"供应商余额耗尽: {provider_name}",
                message=f"供应商 {provider_name} 余额已耗尽 ({balance} {balance_currency or ''})，请及时充值。",
            )
        elif balance is not None and balance < 1.0:
            notifier.notify(
                event=f"balance_low:{provider_name}",
                severity="warning",
                title=f"供应商余额不足: {provider_name}",
                message=f"供应商 {provider_name} 余额仅剩 {balance} {balance_currency or ''}，建议尽快充值。",
            )
        return {"provider": provider_name, "balance": balance, "balance_currency": balance_currency}

    @app.post("/admin/api/providers/{provider_name}/test-price")
    async def api_test_provider_price(provider_name: str, request: Request, _admin=Depends(require_admin)):
        """测试提供方的单价获取（通过脚本）。"""
        provider = config.get_provider(provider_name)
        if not provider:
            raise HTTPException(status_code=404, detail="提供方不存在")
        body = await request.json()
        model_name = body.get("model_name", "")
        from core.script_engine import execute_price_script
        result = None
        if provider.get("price_script", "").strip():
            result = execute_price_script(
                script=provider["price_script"],
                api_key=provider.get("api_key", ""),
                base_url=provider.get("base_url", ""),
                model_name=model_name,
            )
        # 推断未指定的单位字段
        if result is not None:
            api_type = provider.get("api_type", "local")
            if result.get("price_currency") is None:
                result["price_currency"] = pricing_manager._infer_price_currency(provider, api_type)
            if result.get("price_unit") is None:
                result["price_unit"] = pricing_manager._infer_price_unit(provider, api_type)
        return {"provider": provider_name, "model_name": model_name, "price": result}

    @app.post("/admin/api/script/test")
    async def api_test_script(request: Request, _admin=Depends(require_admin)):
        """测试脚本语法。"""
        body = await request.json()
        script = body.get("script", "")
        script_type = body.get("script_type", "balance")
        from core.script_engine import test_script
        result = test_script(script, script_type)
        return result

    @app.post("/admin/api/script/execute")
    async def api_execute_script(request: Request, _admin=Depends(require_admin)):
        """执行脚本并返回结果（用于调试）。"""
        body = await request.json()
        script = body.get("script", "")
        script_type = body.get("script_type", "balance")
        api_key = body.get("api_key", "")
        base_url = body.get("base_url", "")
        model_name = body.get("model_name", "")
        loop = asyncio.get_event_loop()
        if script_type == "balance":
            from core.script_engine import execute_balance_script
            result = await loop.run_in_executor(None, execute_balance_script, script, api_key, base_url, model_name)
            # 推断余额货币单位
            balance_currency = None
            if result is not None:
                balance_currency = result.get("balance_currency")
                if balance_currency is None:
                    # 尝试从提供方推断
                    providers = config.get_providers()
                    for p in providers:
                        if p.get("api_key", "") == api_key or p.get("base_url", "") == base_url:
                            balance_currency = pricing_manager._infer_balance_currency(p, p.get("api_type", "local"))
                            break
                    if balance_currency is None:
                        balance_currency = config.currency
            return {"result": result, "type": "balance", "balance_currency": balance_currency}
        else:
            from core.script_engine import execute_price_script
            result = await loop.run_in_executor(None, execute_price_script, script, api_key, base_url, model_name)
            # 推断单价单位
            if result is not None:
                providers = config.get_providers()
                api_type = "local"
                for p in providers:
                    if p.get("api_key", "") == api_key or p.get("base_url", "") == base_url:
                        api_type = p.get("api_type", "local")
                        break
                if result.get("price_currency") is None:
                    result["price_currency"] = pricing_manager._infer_price_currency({"api_type": api_type}, api_type)
                if result.get("price_unit") is None:
                    result["price_unit"] = pricing_manager._infer_price_unit({"api_type": api_type}, api_type)
            return {"result": result, "type": "price"}

    @app.get("/admin/api/script/help")
    async def api_script_help(_admin=Depends(require_admin)):
        """获取脚本编写说明。"""
        from core.script_engine import BALANCE_SCRIPT_HELP, PRICE_SCRIPT_HELP
        return {
            "balance_script_help": BALANCE_SCRIPT_HELP,
            "price_script_help": PRICE_SCRIPT_HELP,
        }

    @app.get("/admin/api/metrics")
    async def api_metrics(_admin=Depends(require_admin)):
        """模型聚合指标接口。"""
        return {"metrics": db.get_all_metrics()}

    @app.get("/admin/api/feedback/negative")
    async def api_negative_feedback(_admin=Depends(require_admin)):
        return {"items": db.get_negative_feedback_conversations()}

    @app.get("/admin/api/predictor/status")
    async def api_predictor_status(_admin=Depends(require_admin)):
        status = app.state.predictor.get_status()
        status["expected_model_info"] = "使用 /admin/api/predictor/test 获取预测期望模型"
        return status

    @app.post("/admin/api/predictor/test")
    async def api_predictor_test(request: Request, _admin=Depends(require_admin)):
        """管理面板专用：预测 prompt 的难度、任务类型、推荐模型及候选列表。"""
        body = await request.json()
        prompt = body.get("prompt", "")
        if not prompt:
            raise HTTPException(status_code=400, detail="prompt 不能为空")
        requested_model = body.get("model", "")
        if requested_model:
            resolved = config.resolve_model_name(requested_model)
        else:
            resolved = ""
        diff, est_tokens = app.state.predictor.predict(prompt)
        task_type = detect_task_type(prompt)
        if resolved:
            selected = config.get_model(resolved)
            route_source = "specified"
        else:
            selected = app.state.router.select_model(diff, est_tokens, est_tokens, task_type=task_type)
            route_source = "auto"
        if not selected:
            selected = config.get_fallback_model()
            route_source = "fallback"
        candidate_models = []
        try:
            candidates = app.state.router.select_model_candidates(diff, est_tokens, est_tokens, task_type=task_type, top_k=3)
            candidate_models = [
                {"name": c["model"]["name"], "combined_score": round(c["combined_score"], 4), "cost": round(c["cost"], 6)}
                for c in candidates
            ]
        except Exception:
            pass
        return {
            "predicted_difficulty": diff,
            "estimated_tokens": est_tokens,
            "task_type": task_type,
            "recommended_model": selected["name"] if selected else None,
            "candidate_models": candidate_models,
            "model_capability": selected.get("capability") if selected else None,
            "model_task_types": selected.get("task_types", []) if selected else [],
            "route_source": route_source,
        }

    @app.post("/admin/api/predictor/reset")
    async def api_predictor_reset(_admin=Depends(require_admin)):
        """重置预测引擎：清空训练数据和模型权重，重新初始化。"""
        app.state.predictor.reset()
        # 清空训练样本
        with db._connect() as conn:
            conn.execute("DELETE FROM training_samples")
            conn.commit()
        return {"status": "ok", "message": "预测引擎已重置"}

    @app.get("/admin/api/task-type/stats")
    async def api_task_type_stats(_admin=Depends(require_admin)):
        """任务类型统计。"""
        return {
            "stats": db.get_task_type_stats(),
            "detector": task_type_detector.get_status(),
            "recent": db.get_recent_task_types(limit=50),
        }

    @app.get("/admin/api/config")
    async def api_get_config(_admin=Depends(require_admin)):
        data = dict(config.data)
        # 脱敏敏感字段
        if "admin_password" in data:
            data["admin_password"] = "***"
        if "api_key" in data and data["api_key"]:
            data["api_key"] = "***"
        if "ssh_key" in data and data["ssh_key"]:
            data["ssh_key"] = "***"
        return data

    @app.post("/admin/api/config")
    async def api_update_config(request: Request, _admin=Depends(require_admin)):
        body = await request.json()
        for k, v in body.items():
            if k == "models":  # models 单独接口
                continue
            if k == "admin_password" and (not v or v == "***"):
                continue  # 不更新脱敏的密码
            if k == "api_key" and (not v or v == "***"):
                continue  # 不更新脱敏的 key
            if k == "ssh_key" and v == "***":
                continue  # 不更新脱敏的 SSH Key
            config.set(k, v)
            # Web 设置的敏感字段优先级最高：同步到环境变量和 .env 文件
            if k == "admin_password":
                os.environ["SMARTROUTER_ADMIN_PASSWORD"] = v
                config.sync_env_to_dotenv("SMARTROUTER_ADMIN_PASSWORD", v)
            elif k == "api_key":
                os.environ["SMARTROUTER_API_KEY"] = v
                config.sync_env_to_dotenv("SMARTROUTER_API_KEY", v)
            elif k == "ssh_key":
                os.environ["SMARTROUTER_SSH_KEY"] = v
                config.sync_env_to_dotenv("SMARTROUTER_SSH_KEY", v)
        try:
            config.save()
        except Exception:
            # 即使文件写入失败，环境变量仍可生效（运行时有效，重启后失效）
            pass
        return {"status": "ok"}

    # ------------------------------------------------------------------ #
    # 配置子路由 API（/admin/api/config/xxx）
    # 前端使用 /config/xxx 前缀调用，此处提供对应路由
    # ------------------------------------------------------------------ #
    @app.post("/admin/api/config/reload")
    async def api_config_reload(_admin=Depends(require_admin)):
        """重新加载配置文件。"""
        config.reload()
        return {"status": "ok"}

    @app.put("/admin/api/config/basic")
    async def api_config_basic(request: Request, _admin=Depends(require_admin)):
        """更新基本设置。"""
        body = await request.json()
        for k, v in body.items():
            if k in ("models", "providers", "admin_password", "api_key", "ssh_key"):
                continue
            config.set(k, v)
        try:
            config.save()
        except Exception as e:
            logger.error("保存基本设置失败: %s", e)
        return {"status": "ok"}

    @app.put("/admin/api/config/route-weights")
    async def api_config_route_weights(request: Request, _admin=Depends(require_admin)):
        """更新路由权重配置。"""
        body = await request.json()
        config.set("route_weights", body)
        try:
            config.save()
        except Exception as e:
            logger.error("保存路由权重失败: %s", e)
        return {"status": "ok"}

    @app.put("/admin/api/config/rl")
    async def api_config_rl(request: Request, _admin=Depends(require_admin)):
        """更新强化学习配置。"""
        body = await request.json()
        config.set("rl_config", body)
        try:
            config.save()
        except Exception as e:
            logger.error("保存RL配置失败: %s", e)
        return {"status": "ok"}

    @app.put("/admin/api/config/health-check")
    async def api_config_health_check(request: Request, _admin=Depends(require_admin)):
        """更新健康检查配置。"""
        body = await request.json()
        config.set("health_check", body)
        try:
            config.save()
        except Exception as e:
            logger.error("保存健康检查配置失败: %s", e)
        return {"status": "ok"}

    @app.put("/admin/api/config/storage")
    async def api_config_storage(request: Request, _admin=Depends(require_admin)):
        """更新存储配置。"""
        body = await request.json()
        config.set("storage", body)
        try:
            config.save()
        except Exception as e:
            logger.error("保存存储配置失败: %s", e)
        return {"status": "ok"}

    @app.put("/admin/api/config/web")
    async def api_config_web(request: Request, _admin=Depends(require_admin)):
        """更新 Web 配置。"""
        body = await request.json()
        config.set("web", body)
        try:
            config.save()
        except Exception as e:
            logger.error("保存Web配置失败: %s", e)
        return {"status": "ok"}

    @app.put("/admin/api/config/exchange-rates")
    async def api_config_exchange_rates(request: Request, _admin=Depends(require_admin)):
        """更新汇率配置。"""
        body = await request.json()
        rates = body.get("exchange_rates", body)
        config.set("exchange_rates", rates)
        try:
            config.save()
        except Exception as e:
            logger.error("保存汇率配置失败: %s", e)
        return {"status": "ok"}

    @app.put("/admin/api/config/model-aliases")
    async def api_config_model_aliases(request: Request, _admin=Depends(require_admin)):
        """更新模型别名配置。"""
        body = await request.json()
        config.set("model_aliases", body)
        try:
            config.save()
        except Exception as e:
            logger.error("保存模型别名失败: %s", e)
        return {"status": "ok"}

    @app.post("/admin/api/config/difficulty-ranges")
    async def api_config_difficulty_ranges(request: Request, _admin=Depends(require_admin)):
        """更新难度范围配置。"""
        body = await request.json()
        ranges = body.get("difficulty_ranges")
        if not ranges or not isinstance(ranges, list):
            raise HTTPException(status_code=400, detail="difficulty_ranges 必须为非空数组")
        for r in ranges:
            if not isinstance(r, dict):
                raise HTTPException(status_code=400, detail="每条范围必须为对象")
            if "min_tokens" not in r or "max_tokens" not in r or "difficulty" not in r:
                raise HTTPException(status_code=400, detail="每条范围必须包含 min_tokens, max_tokens, difficulty")
            if not (1 <= int(r["difficulty"]) <= 100):
                raise HTTPException(status_code=400, detail="difficulty 必须为 1-100")
        config.difficulty_ranges = ranges
        try:
            config.save()
        except Exception as e:
            logger.error("保存难度范围失败: %s", e)
        return {"status": "ok", "difficulty_ranges": ranges}

    @app.put("/admin/api/config/notifications")
    async def api_config_notifications(request: Request, _admin=Depends(require_admin)):
        """更新通知配置。"""
        body = await request.json()
        config.set("notifications", body)
        try:
            config.save()
        except Exception as e:
            logger.error("保存通知配置失败: %s", e)
        return {"status": "ok"}

    @app.post("/admin/api/config/notifications/test")
    async def api_test_notification(request: Request, _admin=Depends(require_admin)):
        """测试通知渠道。"""
        body = await request.json()
        channel = body.get("channel", {})
        ch_type = channel.get("type", "")
        title = "SmartRouter 通知测试"
        message = "这是一条测试通知，如果您收到此消息，说明通知渠道配置正确。"
        success = False
        try:
            if ch_type == "webhook":
                from core.notifier import _send_webhook
                success = _send_webhook(channel["url"], {"title": title, "message": message})
            elif ch_type == "dingtalk":
                from core.notifier import _send_dingtalk
                success = _send_dingtalk(channel["url"], title, message)
            elif ch_type == "wecom":
                from core.notifier import _send_wecom
                success = _send_wecom(channel["url"], message)
            elif ch_type == "feishu":
                from core.notifier import _send_feishu
                success = _send_feishu(channel["url"], title, message)
            elif ch_type == "telegram":
                from core.notifier import _send_telegram
                success = _send_telegram(channel["bot_token"], channel["chat_id"], f"*{title}*\n{message}")
            elif ch_type == "slack":
                from core.notifier import _send_slack
                success = _send_slack(channel["url"], f"*{title}*\n{message}")
            elif ch_type == "email":
                from core.notifier import _send_email
                success = _send_email(
                    channel.get("smtp_host", ""), int(channel.get("smtp_port", 587)),
                    channel.get("smtp_user", ""), channel.get("smtp_pass", ""),
                    channel.get("from", ""), channel.get("to", "").split(","),
                    title, message,
                )
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": success}

    @app.post("/admin/api/sync-prices")
    async def api_sync_prices(request: Request, _admin=Depends(require_admin)):
        """手动触发价格同步，支持按提供方或模型过滤。

        Body 参数（可选）:
        - provider_name: 仅更新指定提供方的模型
        - model_name: 仅更新指定模型
        - 不传参数则更新全部
        """
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        provider_name = body.get("provider_name") if body else None
        model_name = body.get("model_name") if body else None
        loop = asyncio.get_event_loop()
        updated = await loop.run_in_executor(None, pricing_manager.sync_prices_now, provider_name, model_name)
        if updated < 0:
            raise HTTPException(status_code=500, detail="价格同步失败")
        return {"status": "ok", "updated_models": updated}

    @app.get("/admin/api/exchange-rate/status")
    async def api_exchange_rate_status(_admin=Depends(require_admin)):
        """获取汇率状态。"""
        return exchange_rate_manager.get_status()

    @app.post("/admin/api/exchange-rate/sync")
    async def api_exchange_rate_sync(_admin=Depends(require_admin)):
        """手动触发汇率同步。"""
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(None, exchange_rate_manager.sync_now)
        if not success:
            raise HTTPException(status_code=500, detail="汇率同步失败")
        return {"status": "ok"}

    @app.post("/admin/api/reload-config")
    async def api_reload_config(_admin=Depends(require_admin)):
        """手动重新加载配置文件。"""
        config.reload()
        return {"status": "ok"}

    # ------------------------------------------------------------------ #
    # 难度范围配置 API
    # ------------------------------------------------------------------ #
    @app.get("/admin/api/difficulty-ranges")
    async def api_get_difficulty_ranges(_admin=Depends(require_admin)):
        """获取 Token 消耗范围到难度的映射配置。"""
        return {"difficulty_ranges": config.difficulty_ranges}

    @app.post("/admin/api/difficulty-ranges")
    async def api_set_difficulty_ranges(request: Request, _admin=Depends(require_admin)):
        """更新 Token 消耗范围到难度的映射配置。"""
        body = await request.json()
        ranges = body.get("difficulty_ranges")
        if not ranges or not isinstance(ranges, list):
            raise HTTPException(status_code=400, detail="difficulty_ranges 必须为非空数组")
        for r in ranges:
            if not isinstance(r, dict):
                raise HTTPException(status_code=400, detail="每条范围必须为对象")
            if "min_tokens" not in r or "max_tokens" not in r or "difficulty" not in r:
                raise HTTPException(status_code=400, detail="每条范围必须包含 min_tokens, max_tokens, difficulty")
            if not (1 <= int(r["difficulty"]) <= 100):
                raise HTTPException(status_code=400, detail="difficulty 必须为 1-100")
        config.difficulty_ranges = ranges
        try:
            config.save()
        except Exception:
            pass
        return {"status": "ok", "difficulty_ranges": ranges}

    # ------------------------------------------------------------------ #
    # 训练集管理 API
    # ------------------------------------------------------------------ #
    @app.get("/admin/api/training-samples")
    async def api_list_training_samples(
        source: Optional[str] = None,
        task_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        _admin=Depends(require_admin),
    ):
        """获取训练样本列表。"""
        samples = db.get_training_samples(
            source=source, task_type=task_type, limit=limit, offset=offset
        )
        total = db.count_training_samples(source=source)
        sources = db.get_training_sample_sources()
        return {
            "samples": samples,
            "total": total,
            "limit": limit,
            "offset": offset,
            "sources": sources,
        }

    @app.post("/admin/api/training-samples")
    async def api_add_training_sample(request: Request, _admin=Depends(require_admin)):
        """添加训练样本。"""
        body = await request.json()
        prompt = body.get("prompt", "")
        difficulty = body.get("difficulty")
        est_tokens = body.get("est_tokens", 500)
        task_type = body.get("task_type")
        model_name = body.get("model_name")
        source = body.get("source", "manual")
        if not prompt:
            raise HTTPException(status_code=400, detail="prompt 不能为空")
        if difficulty is None or not (1 <= int(difficulty) <= 100):
            raise HTTPException(status_code=400, detail="difficulty 必须为 1-100 的整数")
        sample_id = db.add_training_sample(
            prompt=prompt,
            difficulty=int(difficulty),
            est_tokens=int(est_tokens),
            task_type=task_type,
            model_name=model_name,
            source=source,
            new_mark_ttl=float(config.new_mark_ttl_seconds),
        )
        return {"status": "ok", "id": sample_id}

    @app.put("/admin/api/training-samples/{sample_id}")
    async def api_update_training_sample(
        sample_id: int, request: Request, _admin=Depends(require_admin)
    ):
        """更新训练样本，调整后自动排入训练队列。"""
        existing = db.get_training_sample(sample_id)
        if not existing:
            raise HTTPException(status_code=404, detail="样本不存在")
        body = await request.json()
        ok = db.update_training_sample(
            sample_id=sample_id,
            prompt=body.get("prompt"),
            difficulty=body.get("difficulty"),
            est_tokens=body.get("est_tokens"),
            task_type=body.get("task_type"),
            model_name=body.get("model_name"),
        )
        # 调整后自动排入训练队列
        if ok:
            updated = db.get_training_sample(sample_id)
            if updated:
                app.state.predictor.add_sample(
                    prompt=updated["prompt"],
                    actual_difficulty=updated["difficulty"],
                    actual_tokens=updated.get("est_tokens", 500),
                    task_type=updated.get("task_type"),
                    source="manual_adjust",
                    model_name=updated.get("model_name"),
                )
        return {"status": "ok" if ok else "no_change"}

    @app.delete("/admin/api/training-samples/{sample_id}")
    async def api_delete_training_sample(
        sample_id: int, _admin=Depends(require_admin)
    ):
        """删除训练样本。"""
        ok = db.delete_training_sample(sample_id)
        if not ok:
            raise HTTPException(status_code=404, detail="样本不存在")
        return {"status": "ok"}

    @app.post("/admin/api/training-samples/batch-delete")
    async def api_batch_delete_training_samples(request: Request, _admin=Depends(require_admin)):
        """批量删除训练样本。"""
        body = await request.json()
        ids = body.get("ids", [])
        if not ids:
            raise HTTPException(status_code=400, detail="ids 不能为空")
        deleted = 0
        for sid in ids:
            if db.delete_training_sample(int(sid)):
                deleted += 1
        return {"status": "ok", "deleted": deleted}

    @app.post("/admin/api/training-samples/batch-update")
    async def api_batch_update_training_samples(request: Request, _admin=Depends(require_admin)):
        """批量更新训练样本。"""
        body = await request.json()
        updates = body.get("updates", [])
        if not updates:
            raise HTTPException(status_code=400, detail="updates 不能为空")
        updated = 0
        for u in updates:
            sid = u.get("id")
            if not sid:
                continue
            ok = db.update_training_sample(
                sample_id=int(sid),
                difficulty=u.get("difficulty"),
                est_tokens=u.get("est_tokens"),
                task_type=u.get("task_type"),
                model_name=u.get("model_name"),
            )
            if ok:
                updated += 1
                # 自动排入训练队列
                sample = db.get_training_sample(int(sid))
                if sample:
                    app.state.predictor.add_sample(
                        prompt=sample["prompt"],
                        actual_difficulty=sample["difficulty"],
                        actual_tokens=sample.get("est_tokens", 500),
                        task_type=sample.get("task_type"),
                        model_name=sample.get("model_name"),
                        source="batch_update",
                    )
        return {"status": "ok", "updated": updated}

    @app.post("/admin/api/training-samples/batch")
    async def api_batch_import_training_samples(
        request: Request, _admin=Depends(require_admin)
    ):
        """批量导入训练样本。"""
        body = await request.json()
        samples = body.get("samples", [])
        if not samples:
            raise HTTPException(status_code=400, detail="samples 不能为空")
        source = body.get("source", "batch_import")
        imported = 0
        errors = []
        for i, s in enumerate(samples):
            prompt = s.get("prompt", "")
            difficulty = s.get("difficulty")
            if not prompt or difficulty is None:
                errors.append({"index": i, "error": "prompt 或 difficulty 缺失"})
                continue
            if not (1 <= int(difficulty) <= 100):
                errors.append({"index": i, "error": "difficulty 必须为 1-100"})
                continue
            db.add_training_sample(
                prompt=prompt,
                difficulty=int(difficulty),
                est_tokens=int(s.get("est_tokens", 500)),
                task_type=s.get("task_type"),
                model_name=s.get("model_name"),
                source=source,
                new_mark_ttl=float(config.new_mark_ttl_seconds),
            )
            imported += 1
        return {"status": "ok", "imported": imported, "errors": errors}

    @app.post("/admin/api/training-samples/retrain")
    async def api_retrain_from_samples(_admin=Depends(require_admin)):
        """从训练集重新训练预测模型。"""
        samples = db.get_training_samples(limit=10000)
        if not samples:
            raise HTTPException(status_code=400, detail="训练集为空，无法重训")
        count = 0
        for s in samples:
            app.state.predictor.add_sample(
                prompt=s["prompt"],
                actual_difficulty=s["difficulty"],
                actual_tokens=s.get("est_tokens", 500),
                task_type=s.get("task_type"),
                model_name=s.get("model_name"),
            )
            count += 1
        return {"status": "ok", "queued_samples": count}

    # ------------------------------------------------------------------ #
    # 准备模型接口（自动路由预判）
    # ------------------------------------------------------------------ #
    @app.post("/v1/prepare-model")
    async def prepare_model(request: Request, _auth=Depends(check_api_key)):
        """根据 prompt 预测难度和任务类型，返回推荐模型信息。

        客户端可在发送正式请求前调用此接口，获取路由决策，
        以便在 UI 中展示将使用的模型，或提前准备上下文。
        """
        body = await request.json()
        prompt = body.get("prompt", "")
        if not prompt:
            raise OpenAIError(400, "prompt 不能为空")

        # 模型名映射
        requested_model = body.get("model", "")
        if requested_model:
            resolved = config.resolve_model_name(requested_model)
        else:
            resolved = ""

        # 预测难度和 Token 数
        diff, est_tokens = app.state.predictor.predict(prompt)

        # 推断任务类型
        task_type = detect_task_type(prompt)

        # 智能路由选择模型
        if resolved:
            selected = config.get_model(resolved)
            if not selected:
                raise OpenAIError(
                    404,
                    f"The model `{resolved}` does not exist",
                    "invalid_request_error",
                )
            route_source = "specified"
        else:
            selected = app.state.router.select_model(
                diff, est_tokens, est_tokens, task_type=task_type
            )
            route_source = "auto"

        if not selected:
            selected = config.get_fallback_model()
            route_source = "fallback"

        # 获取候选模型列表
        candidate_models = []
        try:
            candidates = app.state.router.select_model_candidates(
                diff, est_tokens, est_tokens, task_type=task_type, top_k=3
            )
            candidate_models = [
                {"name": c["model"]["name"], "combined_score": round(c["combined_score"], 4), "cost": round(c["cost"], 6)}
                for c in candidates
            ]
        except Exception:
            pass

        return {
            "predicted_difficulty": diff,
            "estimated_tokens": est_tokens,
            "task_type": task_type,
            "recommended_model": selected["name"] if selected else None,
            "candidate_models": candidate_models,
            "model_capability": selected.get("capability") if selected else None,
            "model_task_types": selected.get("task_types", []) if selected else [],
            "route_source": route_source,
            "model_info": {
                "name": selected["name"],
                "api_type": selected.get("api_type", ""),
                "capability": selected.get("capability", 0),
                "price_input": selected.get("price_input", 0),
                "price_output": selected.get("price_output", 0),
                "price_currency": selected.get("price_currency", "USD"),
                "task_types": selected.get("task_types", []),
            } if selected else None,
        }

    # ------------------------------------------------------------------ #
    # 路由日志管理 API
    # ------------------------------------------------------------------ #
    @app.get("/admin/api/route-logs")
    async def api_route_logs(
        limit: int = 100,
        offset: int = 0,
        model: Optional[str] = None,
        route_source: Optional[str] = None,
        _admin=Depends(require_admin),
    ):
        """获取路由日志列表。"""
        logs = db.get_route_logs(
            limit=limit, offset=offset, model=model, route_source=route_source
        )
        total = db.count_route_logs(model=model)
        stats = db.get_route_log_stats()
        # 清除过期新增标记
        db.clear_expired_new_marks()
        return {
            "logs": logs,
            "total": total,
            "limit": limit,
            "offset": offset,
            "stats": stats,
        }

    @app.delete("/admin/api/route-logs")
    async def api_clear_route_logs(
        max_age_days: Optional[int] = None,
        _admin=Depends(require_admin),
    ):
        """清除路由日志。max_age_days=0 或不传表示清除全部。"""
        if max_age_days is not None and max_age_days > 0:
            deleted = db.clear_old_logs(max_age_days=max_age_days)
            return {"status": "ok", "deleted": deleted, "mode": "age_based", "max_age_days": max_age_days}
        else:
            deleted = db.clear_all_logs()
            return {"status": "ok", "deleted": deleted, "mode": "all"}

    @app.post("/admin/api/reset-stats")
    async def api_reset_stats(_admin=Depends(require_admin)):
        """重置所有累计统计数据（清除所有日志和指标）。"""
        logs_deleted = db.clear_all_logs()
        # 重置模型指标
        try:
            from core.database import Database
            with Database.get_instance()._connect() as conn:
                conn.execute("UPDATE model_metrics SET total_calls=0, success_calls=0, positive_feedback=0, negative_feedback=0, success_rate=0.9, satisfaction_rate=0.9")
                conn.commit()
        except Exception:
            pass
        # 重置任务类型统计
        try:
            from core.database import Database
            with Database.get_instance()._connect() as conn:
                conn.execute("UPDATE task_type_stats SET total_count=0, positive_count=0, negative_count=0")
                conn.commit()
        except Exception:
            pass
        return {"status": "ok", "logs_deleted": logs_deleted}

    @app.get("/admin/api/route-logs/stats")
    async def api_route_log_stats(_admin=Depends(require_admin)):
        """获取路由日志统计。"""
        return db.get_route_log_stats()

    # ------------------------------------------------------------------ #
    # API 调用日志 API
    # ------------------------------------------------------------------ #
    @app.get("/admin/api/api-logs")
    async def api_api_logs(
        limit: int = 100,
        offset: int = 0,
        model: Optional[str] = None,
        status_code: Optional[int] = None,
        _admin=Depends(require_admin),
    ):
        """获取 API 调用日志列表。"""
        logs = db.get_api_logs(limit=limit, offset=offset, model=model, status_code=status_code)
        total = db.count_api_logs(model=model, status_code=status_code)
        stats = db.get_api_log_stats()
        return {
            "logs": logs,
            "total": total,
            "limit": limit,
            "offset": offset,
            "stats": stats,
        }

    @app.delete("/admin/api/api-logs")
    async def api_clear_api_logs(
        max_age_days: Optional[int] = None,
        _admin=Depends(require_admin),
    ):
        """清除 API 调用日志。"""
        if max_age_days and max_age_days > 0:
            deleted = db.clear_old_api_logs(max_age_days)
        else:
            deleted = db.clear_all_api_logs()
        return {"deleted": deleted}

    @app.get("/admin/api/api-logs/stats")
    async def api_api_log_stats(_admin=Depends(require_admin)):
        """获取 API 调用日志统计。"""
        return db.get_api_log_stats()

    # ------------------------------------------------------------------ #
    # 后备链日志 API
    # ------------------------------------------------------------------ #
    @app.get("/admin/api/fallback-logs")
    async def api_fallback_logs(
        event_type: Optional[str] = None,
        model: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        _admin=Depends(require_admin),
    ):
        """获取后备链日志列表。"""
        logs = fallback_logger.get_fallback_logs(
            event_type=event_type, model=model, limit=limit, offset=offset
        )
        total = fallback_logger.count_fallback_logs(event_type=event_type)
        stats = fallback_logger.get_fallback_stats()
        return {
            "logs": logs,
            "total": total,
            "limit": limit,
            "offset": offset,
            "stats": stats,
        }

    @app.get("/admin/api/fallback-logs/stats")
    async def api_fallback_log_stats(_admin=Depends(require_admin)):
        """获取后备链统计信息。"""
        return fallback_logger.get_fallback_stats()

    # ------------------------------------------------------------------ #
    # 样本管理日志 API
    # ------------------------------------------------------------------ #
    @app.get("/admin/api/sample-logs")
    async def api_sample_logs(
        event_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        _admin=Depends(require_admin),
    ):
        """获取样本管理日志列表。"""
        logs = fallback_logger.get_sample_logs(
            event_type=event_type, limit=limit, offset=offset
        )
        total = fallback_logger.count_sample_logs(event_type=event_type)
        return {
            "logs": logs,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    # ------------------------------------------------------------------ #
    # 批量并发接口：一次请求同时发起多个 chat completions 调用
    # ------------------------------------------------------------------ #
    @app.post("/v1/chat/completions/batch")
    async def batch_proxy(request: Request, _auth=Depends(check_api_key)):
        """批量并发调用接口。

        请求体格式：
        {
            "requests": [
                {"model": "auto", "messages": [...], "stream": false},
                {"model": "deepseek-v4-flash", "messages": [...], "stream": false},
                ...
            ]
        }

        响应格式：
        {
            "object": "batch",
            "data": [
                {"index": 0, "status": 200, "body": {...}},
                {"index": 1, "status": 500, "error": "..."},
                ...
            ]
        }

        限制：单次最多 10 个请求，且不支持流式（批量请求统一非流式返回）。
        """
        body = await request.json()
        requests_list = body.get("requests", [])
        if not requests_list:
            raise OpenAIError(400, "requests field is required and must be non-empty", "invalid_request_error")
        if len(requests_list) > 10:
            raise OpenAIError(400, "Maximum 10 requests per batch", "invalid_request_error")

        async def _handle_single(index: int, req_body: Dict[str, Any]) -> Dict[str, Any]:
            """处理单个批量请求。"""
            try:
                # 强制非流式
                req_body["stream"] = False
                req_body.pop("stream_options", None)

                # 模型名映射
                requested_model = req_body.get("model", "")
                is_auto_route = (requested_model == "auto" or requested_model == "")
                resolved_model = config.resolve_model_name(requested_model) if not is_auto_route else ""
                if resolved_model != requested_model and not is_auto_route:
                    req_body["model"] = resolved_model

                prompt = _extract_prompt(req_body)
                request_id = f"batch-{uuid.uuid4().hex[:8]}-{index}"

                # 智能路由
                diff, est_tok = app.state.predictor.predict(prompt)
                task_type = detect_task_type(prompt)
                keyword_model = detect_model_keyword(prompt, pricing_manager.get_available_models())

                selected = None
                route_source = "auto"
                if keyword_model:
                    selected = config.get_model(keyword_model)
                    if selected and pricing_manager.is_within_active_hours(selected.get("active_hours")):
                        route_source = "keyword"
                    else:
                        selected = None
                if not selected and is_auto_route:
                    selected = app.state.router.select_model(diff, est_tok, est_tok, task_type=task_type)
                    route_source = "auto"
                elif not selected and requested_model:
                    selected = config.get_model(requested_model)
                    route_source = "direct"
                if not selected:
                    selected = config.get_fallback_model()
                    route_source = "fallback"
                if not selected:
                    return {"index": index, "status": 503, "error": "No model available"}

                # 构建上游请求
                upstream_model = selected.get("upstream_model_name") or selected["name"]
                req_body["model"] = upstream_model
                headers = _build_upstream_headers(selected)
                try:
                    url = _build_upstream_url(selected)
                except ValueError as e:
                    return {"index": index, "status": 400, "error": str(e)}

                # 发送请求
                client = await _get_http_client()
                t0 = time.perf_counter()
                resp = await client.post(url, json=req_body, headers=headers)
                latency_ms = int((time.perf_counter() - t0) * 1000)

                if resp.status_code >= 400:
                    error_msg = ""
                    try:
                        error_data = resp.json()
                        error_msg = error_data.get("error", {}).get("message", str(error_data)) if isinstance(error_data, dict) else str(error_data)
                    except Exception:
                        error_msg = resp.text[:500]
                    return {"index": index, "status": resp.status_code, "error": error_msg}

                # 处理响应
                content_type = resp.headers.get("content-type", "")
                if "text/event-stream" in content_type:
                    full_content, usage_info = _collect_sse_text(resp.text)
                    resp_data = _build_non_stream_response(selected["name"], full_content, usage_info)
                else:
                    resp_data = resp.json()
                    resp_data["model"] = selected["name"]

                # 异步记录日志
                usage_info = resp_data.get("usage", {})
                asyncio.create_task(
                    _after_call(
                        request=request,
                        request_id=request_id, prompt=prompt, diff=diff,
                        est_tokens=est_tok, selected=selected,
                        latency_ms=latency_ms, success=True,
                        collected_content=resp_data.get("choices", [{}])[0].get("message", {}).get("content", ""),
                        usage_info=usage_info, task_type=task_type,
                        route_source=route_source,
                        requested_model_name=requested_model or "auto",
                        route_chain=[selected["name"]],
                    content_types=content_types,
                    )
                )

                return {"index": index, "status": 200, "body": resp_data}
            except Exception as exc:  # noqa: BLE001
                return {"index": index, "status": 500, "error": str(exc)}

        # 并发执行所有请求
        tasks = [_handle_single(i, req) for i, req in enumerate(requests_list)]
        results = await asyncio.gather(*tasks)

        return {
            "object": "batch",
            "data": list(results),
        }

    # ------------------------------------------------------------------ #
    # 健康检查
    # ------------------------------------------------------------------ #
    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "predictor_ready": app.state.predictor.is_ready,
            "queue_size": app.state.predictor.queue_size,
            "concurrency": {
                "max": max_concurrency,
                "available": _concurrency_semaphore._value if _concurrency_semaphore else 0,
            },
        }

    # ------------------------------------------------------------------ #
    # 慢查询记录 API
    # ------------------------------------------------------------------ #
    @app.get("/admin/api/slow-queries")
    async def get_slow_queries(_admin=Depends(require_admin)):
        """获取最近的慢查询记录（超过5秒的请求）"""
        return {"slow_queries": _slow_query_log, "threshold_ms": _SLOW_QUERY_THRESHOLD_MS}

    # ------------------------------------------------------------------ #
    # 大屏展示 API（无需认证，适合投屏展示）
    # ------------------------------------------------------------------ #
    @app.get("/admin/api/bigscreen")
    async def bigscreen_data():
        """大屏展示数据接口：汇总关键指标，适合投屏/大屏展示"""
        # 获取仪表盘数据（今日统计）
        now = time.time()
        since = now - 86400  # 最近24小时
        stats = db.get_dashboard_stats(since)

        # 获取模型列表（仅基本信息）
        models_info = []
        for m in config.get_models():
            models_info.append({
                "name": m.get("name", ""),
                "provider": m.get("provider", ""),
                "enabled": m.get("enabled", True),
            })

        # 获取并发信息
        concurrency_info = {
            "max": max_concurrency,
            "available": _concurrency_semaphore._value if _concurrency_semaphore else 0,
            "in_use": max_concurrency - (_concurrency_semaphore._value if _concurrency_semaphore else 0),
        }

        # 获取预测器状态
        predictor_info = {
            "is_ready": app.state.predictor.is_ready,
            "queue_size": app.state.predictor.queue_size,
        }

        # 获取最近慢查询
        recent_slow = _slow_query_log[-5:] if _slow_query_log else []

        # 汇率转换：将 saved_cost_by_currency 转为当前显示货币
        target_currency = config.currency
        total_saved = 0.0
        for item in stats.get("saved_cost_by_currency", []):
            cur = item.get("cost_currency", "USD")
            val = float(item.get("s", 0))
            total_saved += float(exchange_rate_manager.convert(val, cur, target_currency))
        stats["saved_cost"] = round(total_saved, 6)
        stats["cost_currency"] = target_currency

        return {
            "timestamp": time.time(),
            "stats": stats,
            "models": models_info,
            "model_count": len(models_info),
            "enabled_count": sum(1 for m in models_info if m["enabled"]),
            "concurrency": concurrency_info,
            "predictor": predictor_info,
            "recent_slow_queries": recent_slow,
            "slow_query_threshold_ms": _SLOW_QUERY_THRESHOLD_MS,
        }

    # ------------------------------------------------------------------ #
    # 静态面板托管
    # ------------------------------------------------------------------ #
    web_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
    if web_dist.exists():
        # 智能缓存策略：
        # - HTML 文件（index.html）：不缓存，确保用户始终获取最新版本
        # - JS/CSS 等静态资源：缓存 1 小时，大幅减少重复加载时间
        #   （这些文件名不含 hash，但版本更新时 HTML 引用不变，所以缓存时间不宜过长）
        class SmartCacheStaticFiles(StaticFiles):
            async def __call__(self, scope, receive, send):
                # 从 scope 中获取请求路径
                path = scope.get("path", "") if isinstance(scope, dict) else ""
                is_html = path.endswith("/") or path.endswith(".html") or path == ""

                async def send_with_cache(message):
                    if message["type"] == "http.response.start":
                        headers = dict(message.get("headers", []))
                        if is_html:
                            # HTML 不缓存
                            headers[b"cache-control"] = b"no-cache, no-store, must-revalidate"
                            headers[b"pragma"] = b"no-cache"
                            headers[b"expires"] = b"0"
                        else:
                            # 静态资源缓存 1 小时
                            headers[b"cache-control"] = b"public, max-age=3600"
                        message["headers"] = list(headers.items())
                    await send(message)
                await super().__call__(scope, receive, send_with_cache)

        app.mount("/admin", SmartCacheStaticFiles(directory=str(web_dist), html=True), name="admin")

    return app


# 模块级 app 实例（供 uvicorn 直接引用）
app = create_app()
