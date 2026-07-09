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
            corrected_diff = min(5, predicted_difficulty + 1)
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
        """根据响应、实际 token 消耗和费用综合评估真实难度。

        核心逻辑：消耗大量 token / 花费高 = 高难度任务；消耗很少 = 低难度。
        - 费用 > 0.01 元 或 completion_tokens > 2000 -> 难度 5
        - 费用 > 0.001 元 或 completion_tokens > 800 -> 难度 4
        - 费用 > 0.0001 元 或 completion_tokens > 300 -> 难度 3
        - 费用 > 0 或 completion_tokens > 50 -> 难度 2
        - 其他 -> 难度 1
        """
        # 优先使用 token 消耗和费用判断
        if completion_tokens > 0 or cost > 0:
            if cost > 0.01 or completion_tokens > 2000:
                return 5
            if cost > 0.001 or completion_tokens > 800:
                return 4
            if cost > 0.0001 or completion_tokens > 300:
                return 3
            if cost > 0 or completion_tokens > 50:
                return 2
            return 1

        # 降级：根据响应内容长度启发式评估
        content = ""
        if isinstance(response, dict):
            choices = response.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
        if not content:
            return 3
        length = len(content)
        has_code = "```" in content
        if length > 2000 or (has_code and length > 800):
            return 5
        if length > 800 or has_code:
            return 4
        if length > 300:
            return 3
        if length > 100:
            return 2
        return 1


# 全局单例
feedback_analyzer = FeedbackAnalyzer()
