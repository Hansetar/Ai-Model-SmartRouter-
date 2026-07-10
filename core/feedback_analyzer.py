"""
core/feedback_analyzer.py
=========================
用户反馈与语义闭环。

构建"预测 -> 调用 -> 反馈 -> 修正预测"的飞轮效应。
1. 显式反馈：前端注入按钮，用户点击 👍/👎
2. 隐式反馈：用户下一句话语义分析（正则匹配 + 兜底模型分类）
3. 纠偏训练：用户点"不认可"时，将该 Prompt 真实难度 +1，重新送入训练队列
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

from .database import db


# 正向信号关键词
_POSITIVE_PATTERNS = [
    r"谢谢",
    r"感谢",
    r"对的",
    r"正确",
    r"很好",
    r"不错",
    r"完美",
    r"thanks",
    r"thank you",
    r"good",
    r"great",
    r"perfect",
    r"correct",
    r"right",
    r"excellent",
    r"awesome",
]

# 负向信号关键词
_NEGATIVE_PATTERNS = [
    r"不对",
    r"错误",
    r"不行",
    r"不好",
    r"重新",
    r"再试",
    r"不是",
    r"没解决",
    r"wrong",
    r"incorrect",
    r"bad",
    r"no",
    r"not right",
    r"try again",
    r"redo",
    r"doesn'?t work",
    r"不是这样",
    r"答非所问",
]


class FeedbackAnalyzer:
    """反馈分析器。"""

    def __init__(self) -> None:
        self._pos_re = [re.compile(p, re.IGNORECASE) for p in _POSITIVE_PATTERNS]
        self._neg_re = [re.compile(p, re.IGNORECASE) for p in _NEGATIVE_PATTERNS]

    # ------------------------------------------------------------------ #
    # 显式反馈
    # ------------------------------------------------------------------ #
    def record_explicit(
        self,
        request_id: str,
        sentiment: str,
        context_snapshot: str = "",
        predictor=None,
        prompt: Optional[str] = None,
        predicted_difficulty: Optional[int] = None,
    ) -> None:
        """记录显式反馈，并触发纠偏训练。"""
        db.record_feedback(
            request_id=request_id,
            feedback_type="explicit",
            sentiment=sentiment,
            context_snapshot=context_snapshot,
        )

        # 纠偏训练：用户点"不认可"，说明难度被低估
        if (
            sentiment == "negative"
            and predictor is not None
            and prompt
            and predicted_difficulty is not None
        ):
            corrected_diff = min(100, predicted_difficulty + 10)
            # 真实 Token 数未知，用预测值兜底
            _, est_tokens = predictor.predict(prompt)
            predictor.add_sample(prompt, corrected_diff, est_tokens)

    # ------------------------------------------------------------------ #
    # 隐式反馈
    # ------------------------------------------------------------------ #
    def analyze_implicit(self, user_message: str) -> Tuple[str, float]:
        """分析用户下一句话的隐式情感。

        :return: (sentiment, confidence)
        """
        # 1. 正则匹配
        for pattern in self._pos_re:
            if pattern.search(user_message):
                return "positive", 0.9
        for pattern in self._neg_re:
            if pattern.search(user_message):
                return "negative", 0.9

        # 2. 无法匹配，返回 neutral
        # 真实场景应送入最便宜的模型分类，这里降级为 neutral
        return "neutral", 0.5

    def record_implicit(
        self,
        request_id: str,
        user_message: str,
        context_snapshot: str = "",
    ) -> str:
        """记录隐式反馈。"""
        sentiment, _ = self.analyze_implicit(user_message)
        if sentiment != "neutral":
            db.record_feedback(
                request_id=request_id,
                feedback_type="implicit",
                sentiment=sentiment,
                context_snapshot=context_snapshot or user_message[:500],
            )
        return sentiment

    # ------------------------------------------------------------------ #
    # 难度启发式评估（用于 after_llm_call 钩子）
    # ------------------------------------------------------------------ #
    @staticmethod
    def estimate_difficulty(response: dict, cost: float = 0.0, completion_tokens: int = 0) -> int:
        """根据响应、实际 token 消耗和费用综合评估真实难度（1-100）。

        核心逻辑：消耗大量 token / 花费高 = 高难度任务；消耗很少 = 低难度。
        优先使用配置中的 difficulty_ranges 映射，回退到连续映射。
        """
        from .config import config

        # 优先使用 token 消耗和费用判断
        if completion_tokens > 0 or cost > 0:
            # 使用配置中的 Token 消耗范围映射
            token_diff = config.tokens_to_difficulty(completion_tokens)
            # 基于 cost 的难度映射
            if cost > 0:
                cost_diff = min(100, max(1, int(cost * 10000)))
            else:
                cost_diff = 1
            # 取两者较大值
            return max(token_diff, cost_diff)

        # 降级：根据响应内容长度启发式评估
        content = ""
        if isinstance(response, dict):
            choices = response.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
        if not content:
            return 50
        length = len(content)
        has_code = "```" in content
        # 使用内容长度估算 token 数（约 4 字符 = 1 token），再映射到难度
        est_tokens = max(1, length // 4)
        base_diff = config.tokens_to_difficulty(est_tokens)
        if has_code:
            base_diff = min(100, base_diff + 20)
        return base_diff


# 全局单例
feedback_analyzer = FeedbackAnalyzer()
