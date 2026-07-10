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

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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
    # 工具函数
    # ------------------------------------------------------------------ #
    def _hash_prompt(prompt: str) -> str:
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

    def _extract_prompt(body: Dict[str, Any]) -> str:
        messages = body.get("messages", [])
        if not messages:
            return ""
        return messages[-1].get("content", "")

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
        """构建上游请求头，空 API Key 时不发送 Authorization。"""
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        api_key = selected.get("api_key", "")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _log_fallback(
        original_model: str,
        fallback_model: str,
        attempt: int,
        failed_models: List[str],
        error: str = "",
        prompt_preview: str = "",
    ) -> None:
        """记录后备链切换事件到专用日志。"""
        fallback_logger.log_fallback(
            original_model=original_model,
            fallback_model=fallback_model,
            attempt=attempt,
            failed_models=failed_models,
            error=error,
            prompt_preview=prompt_preview,
        )

    async def _check_upstream_available(
        selected: Dict[str, Any],
        body: Dict[str, Any],
    ) -> tuple[bool, Dict[str, Any]]:
        """预检上游模型是否可用（用于流式模式的后备链判断）。

        发送一个轻量的非流式请求，检查上游是否返回成功。
        返回 (is_available, error_info)。
        """
        check_body = dict(body)
        check_body["stream"] = False  # 强制非流式预检
        # stream_options 必须与 stream=true 一起使用，预检时移除
        check_body.pop("stream_options", None)
        check_body["model"] = selected.get("upstream_model_name") or selected["name"]
        headers = _build_upstream_headers(selected)
        try:
            url = _build_upstream_url(selected)
        except ValueError as e:
            return False, {"status_code": 400, "message": str(e)}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=check_body, headers=headers)
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
    ) -> StreamingResponse | JSONResponse:
        """执行上游请求，根据 stream 参数决定流式透传或非流式响应。

        失败时抛出 UpstreamError 而非直接返回错误响应，以便外层实现后备链重试。

        模型名处理规则：
        - 发送给上游：使用 upstream_model_name（如未配置则用 name）
        - 返回给客户端：使用 selected["name"]（配置中的显示名）
          - auto 模式下客户端应看到实际路由到的模型名，而非 "auto"
          - 直连模式下客户端应看到自己请求的模型名
        """
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
                async with httpx.AsyncClient(timeout=120.0) as client:
                    resp = await client.post(url, json=body, headers=headers)
                    if resp.status_code >= 400:
                        success = False
                        latency_ms = int((time.perf_counter() - t0) * 1000)
                        asyncio.create_task(
                            _after_call(
                                request_id=request_id, prompt=prompt, diff=diff,
                                est_tokens=est_tokens, selected=selected,
                                latency_ms=latency_ms, success=False,
                                collected_content="", usage_info={},
                                task_type=task_type,
                                route_source=effective_route_source,
                                requested_model_name=requested_model_name,
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
                                request_id=request_id, prompt=prompt, diff=diff,
                                est_tokens=est_tokens, selected=selected,
                                latency_ms=latency_ms, success=True,
                                collected_content="".join(collected_content),
                                usage_info=usage_info, task_type=task_type,
                                route_source=effective_route_source,
                                requested_model_name=requested_model_name,
                            )
                        )
                        return JSONResponse(content=resp_data)
            except UpstreamError:
                raise  # 重新抛出，让外层处理后备链
            except Exception as exc:  # noqa: BLE001
                latency_ms = int((time.perf_counter() - t0) * 1000)
                asyncio.create_task(
                    _after_call(
                        request_id=request_id, prompt=prompt, diff=diff,
                        est_tokens=est_tokens, selected=selected,
                        latency_ms=latency_ms, success=False,
                        collected_content="", usage_info={},
                        task_type=task_type,
                        route_source=effective_route_source,
                        requested_model_name=requested_model_name,
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
                    request_id=request_id, prompt=prompt, diff=diff,
                    est_tokens=est_tokens, selected=selected,
                    latency_ms=latency_ms, success=True,
                    collected_content="".join(collected_content),
                    usage_info=usage_info, task_type=task_type,
                    route_source=effective_route_source,
                    requested_model_name=requested_model_name,
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
                async with httpx.AsyncClient(timeout=120.0) as client:
                    async with client.stream(
                        "POST", url, json=body, headers=headers
                    ) as resp:
                        if resp.status_code >= 400:
                            success = False
                            error_body = await resp.aread()
                            latency_ms = int((time.perf_counter() - t0) * 1000)
                            asyncio.create_task(
                                _after_call(
                                    request_id=request_id, prompt=prompt, diff=diff,
                                    est_tokens=est_tokens, selected=selected,
                                    latency_ms=latency_ms, success=False,
                                    collected_content="", usage_info={},
                                    task_type=task_type,
                                    route_source=effective_route_source,
                                    requested_model_name=requested_model_name,
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
                        request_id=request_id, prompt=prompt, diff=diff,
                        est_tokens=est_tokens, selected=selected,
                        latency_ms=latency_ms, success=False,
                        collected_content="", usage_info={},
                        task_type=task_type,
                        route_source=effective_route_source,
                        requested_model_name=requested_model_name,
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
                        )
                    )

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"X-Request-Id": request_id}
        )

    async def _after_call(
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
        )
        # 本地扣账
        if cost > 0:
            pricing_manager.deduct(selected["name"], cost)
        # 训练样本（自动持久化到数据库）
        app.state.predictor.add_sample(prompt, actual_diff, actual_out_tokens, task_type=task_type, source="auto", model_name=selected["name"])
        # 自适应学习：从成功请求中学习关键词
        if task_type and success:
            task_type_detector.learn_keywords(task_type, prompt, success)

    # ------------------------------------------------------------------ #
    # 核心代理接口
    # ------------------------------------------------------------------ #
    @app.post("/v1/chat/completions")
    async def proxy(request: Request, _auth=Depends(check_api_key)):
        body = await request.json()

        # 模型名映射：将请求中的模型名映射为实际模型名
        requested_model = body.get("model", "")
        is_auto_route = (requested_model == "auto" or requested_model == "")
        resolved_model = config.resolve_model_name(requested_model) if not is_auto_route else ""
        if resolved_model != requested_model and not is_auto_route:
            body["model"] = resolved_model

        prompt = _extract_prompt(body)
        request_id = str(uuid.uuid4())

        # 1. 缓存命中检查（关键词定向优先级高于缓存）
        prompt_hash = _hash_prompt(prompt)
        # 先检测关键词——如果用户明确指定了模型，忽略缓存
        keyword_model_early = detect_model_keyword(prompt, pricing_manager.get_available_models())
        if not keyword_model_early:
            cached_model = router.get_cached_route(prompt_hash)
            if cached_model:
                task_type = detect_task_type(prompt)
                return await _do_upstream(
                    cached_model, body, request_id, prompt, 3, 500,
                    task_type=task_type, requested_model_name=requested_model,
                    route_source="cache",
                )

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
                route_source = "keyword"
        if not selected and (is_auto_route or requested_model_name == "auto"):
            # 智能路由模式（双机制：预测推荐 + 评分）
            selected = app.state.router.select_model(diff, est_tok, est_tok, task_type=task_type, predictor_recommendation=predictor_rec)
            route_source = "auto"
        elif not selected and requested_model_name:
            selected = config.get_model(requested_model_name)
            if not selected:
                raise OpenAIError(
                    404,
                    f"The model `{requested_model_name}` does not exist",
                    "invalid_request_error",
                )
            route_source = "direct"

        if not selected:
            selected = app.state.router.select_model(diff, est_tok, est_tok, task_type=task_type, predictor_recommendation=predictor_rec)
            route_source = "auto"
        if not selected:
            # 使用 fallback 模型
            selected = config.get_fallback_model()
            route_source = "fallback"
            if not selected:
                raise OpenAIError(503, "No model available", "server_error")

        # 5. 代理请求（含后备链重试）
        # 非流式和流式请求都支持后备链重试
        is_stream = body.get("stream", False)

        # 流式模式：先预检上游连接，失败则触发后备链
        if is_stream:
            failed_models_stream: List[str] = []
            last_error_stream: Optional[UpstreamError] = None
            max_retries_stream = len(config.get_models())

            for attempt in range(max_retries_stream + 1):
                if attempt == 0:
                    current_model = selected
                    current_route_source = route_source
                    is_retry = False
                else:
                    # 先尝试严格匹配的后备链，再尝试宽松匹配（允许降级）
                    fallback_chain = app.state.router.select_fallback_chain(
                        diff, est_tok, est_tok,
                        failed_models=failed_models_stream,
                        task_type=task_type,
                        strict_capability=True,
                    )
                    if not fallback_chain:
                        # 严格匹配无结果，放宽能力要求（兜底）
                        fallback_chain = app.state.router.select_fallback_chain(
                            diff, est_tok, est_tok,
                            failed_models=failed_models_stream,
                            task_type=task_type,
                            strict_capability=False,
                        )
                    if not fallback_chain:
                        # 最终兜底：使用 fallback_model
                        fb = config.get_fallback_model()
                        if fb and fb["name"] not in failed_models_stream:
                            fallback_chain = [fb]
                    if not fallback_chain:
                        break
                    current_model = fallback_chain[0]
                    current_route_source = "fallback"
                    is_retry = True
                    _log_fallback(
                        original_model=selected["name"],
                        fallback_model=current_model["name"],
                        attempt=attempt,
                        failed_models=failed_models_stream,
                        error=last_error_stream.message if last_error_stream else "",
                        prompt_preview=prompt[:100],
                    )

                # 预检：先发一个轻量请求检查上游是否可用
                upstream_ok, upstream_error = await _check_upstream_available(
                    current_model, body
                )
                if upstream_ok:
                    # 上游可用，开始流式传输
                    return await _do_upstream(
                        current_model, body, request_id, prompt, diff, est_tok,
                        task_type=task_type, requested_model_name=requested_model,
                        route_source=current_route_source,
                        is_fallback_retry=is_retry,
                    )
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
        max_retries = len(config.get_models())  # 最多尝试所有模型

        for attempt in range(max_retries + 1):
            if attempt == 0:
                # 首次尝试：使用选中的模型
                current_model = selected
                current_route_source = route_source
                is_retry = False
            else:
                # 后备重试：从后备链中获取下一个模型
                # 先尝试严格匹配，再尝试宽松匹配（允许降级）
                fallback_chain = app.state.router.select_fallback_chain(
                    diff, est_tok, est_tok,
                    failed_models=failed_models,
                    task_type=task_type,
                    strict_capability=True,
                )
                if not fallback_chain:
                    # 严格匹配无结果，放宽能力要求（兜底）
                    fallback_chain = app.state.router.select_fallback_chain(
                        diff, est_tok, est_tok,
                        failed_models=failed_models,
                        task_type=task_type,
                        strict_capability=False,
                    )
                if not fallback_chain:
                    # 最终兜底：使用 fallback_model
                    fb = config.get_fallback_model()
                    if fb and fb["name"] not in failed_models:
                        fallback_chain = [fb]
                if not fallback_chain:
                    break  # 没有更多后备模型
                current_model = fallback_chain[0]
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
                )

            try:
                return await _do_upstream(
                    current_model, body, request_id, prompt, diff, est_tok,
                    task_type=task_type, requested_model_name=requested_model,
                    route_source=current_route_source,
                    is_fallback_retry=is_retry,
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
        # 只暴露启用的模型
        models = [m for m in models if m.get("enabled", True)]
        aliases = config.model_aliases
        now = int(time.time())
        data = [
            {
                "id": m["name"],
                "object": "model",
                "created": now,
                "owned_by": m.get("api_type", "unknown"),
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
                balance = pricing_manager._get_balance(m)
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
            balance = pricing_manager._get_balance(m)
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
        return data

    @app.get("/admin/api/models")
    async def api_models(_admin=Depends(require_admin)):
        """管理面板模型列表：显示所有模型（包括禁用的），附带余额和价格。"""
        models = config.get_models()
        target_currency = config.currency
        for m in models:
            # 余额处理（与 pricing_manager 相同逻辑）
            if m.get("balance_manual") is not None:
                m["balance"] = m["balance_manual"]
            elif m.get("balance_frozen"):
                m["balance"] = pricing_manager._get_cached_balance(m)
            else:
                m["balance"] = pricing_manager._get_balance(m)
            # 价格货币转换
            model_currency = m.get("price_currency", "USD")
            if model_currency != target_currency:
                m["price_input_display"] = round(
                    exchange_rate_manager.convert(m.get("price_input", 0), model_currency, target_currency), 8)
                m["price_output_display"] = round(
                    exchange_rate_manager.convert(m.get("price_output", 0), model_currency, target_currency), 8)
                m["price_currency_display"] = target_currency
            else:
                m["price_input_display"] = m.get("price_input", 0)
                m["price_output_display"] = m.get("price_output", 0)
                m["price_currency_display"] = target_currency
            # 脱敏 API Key
            if m.get("api_key"):
                key = m["api_key"]
                m["api_key"] = key[:8] + "***" if len(key) > 8 else "***"
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
                # 前端传回的脱敏 key，恢复原值
                original = existing_keys.get(m["name"], "")
                if original:
                    m["api_key"] = original
            # 处理 capability_manual：如果自动模式，删除 capability 让后端自动计算
            if not m.get("capability_manual", False):
                m.pop("capability", None)
            # 清理前端辅助字段，不写入 config.yaml
            m.pop("capability_manual", None)
            m.pop("balance", None)
            m.pop("price_input_display", None)
            m.pop("price_output_display", None)
            m.pop("price_currency_display", None)
        config.set("models", models_data)
        try:
            config.save()
        except Exception:
            pass
        return {"status": "ok"}

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
        balance = pricing_manager.refresh_balance(model_name)
        return {"model": model_name, "balance": balance}

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

    @app.post("/admin/api/sync-prices")
    async def api_sync_prices(_admin=Depends(require_admin)):
        """手动触发价格同步。"""
        updated = pricing_manager.sync_prices_now()
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
        success = exchange_rate_manager.sync_now()
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
    # 健康检查
    # ------------------------------------------------------------------ #
    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "predictor_ready": app.state.predictor.is_ready,
            "queue_size": app.state.predictor.queue_size,
        }

    # ------------------------------------------------------------------ #
    # 静态面板托管
    # ------------------------------------------------------------------ #
    web_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
    if web_dist.exists():
        # 自定义 StaticFiles 子类，添加 no-cache 头防止浏览器缓存旧页面
        class NoCacheStaticFiles(StaticFiles):
            async def __call__(self, scope, receive, send):
                async def send_with_no_cache(message):
                    if message["type"] == "http.response.start":
                        headers = dict(message.get("headers", []))
                        headers[b"cache-control"] = b"no-cache, no-store, must-revalidate"
                        headers[b"pragma"] = b"no-cache"
                        headers[b"expires"] = b"0"
                        message["headers"] = list(headers.items())
                    await send(message)
                await super().__call__(scope, receive, send_with_no_cache)

        app.mount("/admin", NoCacheStaticFiles(directory=str(web_dist), html=True), name="admin")

    return app


# 模块级 app 实例（供 uvicorn 直接引用）
app = create_app()
