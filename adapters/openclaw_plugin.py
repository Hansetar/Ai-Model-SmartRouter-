"""
adapters/openclaw_plugin.py
===========================
OpenClaw 插件模式适配。

作为 OpenClaw 组件启动，通过进程内钩子拦截，无 HTTP 开销。
- before_llm_call: 预测 + 路由，返回重定向配置
- after_llm_call:  获取真实结果进行训练
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any, Dict, Optional

from core import config, db, feedback_analyzer, predictor, pricing_manager, router
from core.router import detect_task_type, task_type_detector


class OpenClawSmartRouterPlugin:
    """OpenClaw SmartRouter 插件。

    通过 OpenClaw 宿主的生命周期钩子接入，进程内调用，零 HTTP 开销。
    """

    def __init__(self, openclaw_app: Any) -> None:
        self.app = openclaw_app
        self.predictor = predictor
        self.router = router
        self.pricing = pricing_manager
        self.feedback = feedback_analyzer
        self.db = db

        # 将面板挂载到 OpenClaw 的路由下（如果宿主支持）
        try:
            from fastapi.staticfiles import StaticFiles
            from pathlib import Path

            web_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
            if web_dist.exists() and hasattr(openclaw_app, "mount"):
                openclaw_app.mount(
                    "/smart-router",
                    StaticFiles(directory=str(web_dist), html=True),
                    name="smart-router-admin",
                )
        except Exception as exc:  # noqa: BLE001
            import sys

            print(f"[Plugin] mount admin panel failed: {exc}", file=sys.stderr)

    # ------------------------------------------------------------------ #
    # 钩子：请求前
    # ------------------------------------------------------------------ #
    async def before_llm_call(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """OpenClaw 发起请求前的钩子。

        :param request_data: 包含 messages 的请求字典
        :return: 重定向配置，交由宿主执行网络请求
        """
        messages = request_data.get("messages", [])
        prompt = messages[-1].get("content", "") if messages else ""
        request_data["prompt"] = prompt  # 供 after_llm_call 使用

        # 1. 缓存命中
        prompt_hash = hashlib.md5(prompt.encode("utf-8")).hexdigest()
        cached_model = self.router.get_cached_route(prompt_hash)
        if cached_model:
            request_data["_cached"] = True
            return {
                "model": cached_model["name"],
                "api_key": cached_model.get("api_key", ""),
                "base_url": cached_model.get("base_url", ""),
            }

        # 2. 同步拦截预测
        diff, est_tok = self.predictor.predict(prompt)
        request_data["_predicted_difficulty"] = diff
        request_data["_est_tokens"] = est_tok

        # 3. 推断请求类型
        task_type = detect_task_type(prompt)
        request_data["_task_type"] = task_type

        # 4. 智能路由
        selected = self.router.select_model(diff, est_tok, est_tok, task_type=task_type)
        if not selected:
            # 降级到默认模型
            default = config.get_default_model()
            if default:
                selected = default
            else:
                return {}

        request_data["_selected_model"] = selected["name"]
        return {
            "model": selected["name"],
            "api_key": selected.get("api_key", ""),
            "base_url": selected.get("base_url", ""),
        }

    # ------------------------------------------------------------------ #
    # 钩子：请求后
    # ------------------------------------------------------------------ #
    async def after_llm_call(
        self, request_id: str, request_data: Dict[str, Any], response: Dict[str, Any]
    ) -> None:
        """请求结束后的钩子，获取真实结果进行训练。"""
        prompt = request_data.get("prompt", "")
        selected_name = request_data.get("_selected_model", "")
        diff = request_data.get("_predicted_difficulty", 3)
        est_tok = request_data.get("_est_tokens", 500)
        task_type = request_data.get("_task_type")

        if not selected_name:
            return

        # 真实 Token 数
        usage = response.get("usage", {})
        actual_tokens = usage.get("completion_tokens", 0)
        if not actual_tokens:
            # 启发式：按字符数估算
            content = ""
            choices = response.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
            actual_tokens = max(1, len(content) // 4)

        # 启发式评估难度
        actual_diff = self.feedback.estimate_difficulty(response)

        # 成本
        selected = config.get_model(selected_name)
        cost = 0.0
        if selected:
            cost = (
                est_tok * float(selected.get("price_input", 0))
                + actual_tokens * float(selected.get("price_output", 0))
            )
            # 本地扣账
            if cost > 0:
                self.pricing.deduct(selected_name, cost)

        # 记录日志
        prompt_hash = hashlib.md5(prompt.encode("utf-8")).hexdigest()
        latency_ms = int(response.get("_latency_ms", 0))
        success = response.get("_success", True)
        self.db.log_request(
            prompt_hash=prompt_hash,
            predicted_difficulty=diff,
            actual_difficulty=actual_diff,
            routed_model=selected_name,
            cost=cost,
            latency_ms=latency_ms,
            success=success,
            task_type=task_type,
        )

        # 训练样本
        self.predictor.add_sample(prompt, actual_diff, actual_tokens)

        # 自适应学习：从成功请求中学习关键词
        if task_type and success:
            task_type_detector.learn_keywords(task_type, prompt, success)

    # ------------------------------------------------------------------ #
    # 反馈接口（供 OpenClaw 前端调用）
    # ------------------------------------------------------------------ #
    async def handle_feedback(
        self,
        request_id: str,
        sentiment: str,
        context_snapshot: str = "",
        prompt: Optional[str] = None,
        predicted_difficulty: Optional[int] = None,
        task_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.feedback.record_explicit(
            request_id=request_id,
            sentiment=sentiment,
            context_snapshot=context_snapshot,
            predictor=self.predictor,
            prompt=prompt,
            predicted_difficulty=predicted_difficulty,
        )
        # 自适应学习：从反馈中调整 task_type 权重
        if task_type:
            task_type_detector.learn_from_feedback(task_type, sentiment)
        return {"status": "ok", "sentiment": sentiment}

    # ------------------------------------------------------------------ #
    # 隐式反馈分析
    # ------------------------------------------------------------------ #
    async def analyze_implicit_feedback(
        self, request_id: str, user_message: str
    ) -> Dict[str, Any]:
        sentiment = self.feedback.record_implicit(request_id, user_message)
        return {"sentiment": sentiment}


def setup(openclaw_app: Any) -> OpenClawSmartRouterPlugin:
    """OpenClaw 插件注册入口。

    在 OpenClaw 主配置中启用 plugins: ["openclaw-smart-router"] 后，
    宿主会调用此函数完成注册。
    """
    plugin = OpenClawSmartRouterPlugin(openclaw_app)
    openclaw_app.register_hook("before_llm_call", plugin.before_llm_call)
    openclaw_app.register_hook("after_llm_call", plugin.after_llm_call)
    return plugin
