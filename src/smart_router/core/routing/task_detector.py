"""Task type detection - static rules + adaptive learning."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional

# Static task type patterns
_TASK_PATTERNS: Dict[str, List[str]] = {
    "coding": [
        r"代码", r"编程", r"函数", r"bug", r"debug", r"实现", r"写一个",
        r"code", r"program", r"function", r"implement", r"script",
        r"python", r"javascript", r"java", r"rust", r"cpp", r"html", r"css",
        r"sql", r"api", r"算法", r"algorithm",
    ],
    "math": [
        r"计算", r"数学", r"方程", r"积分", r"概率", r"统计",
        r"calculate", r"math", r"equation", r"integral", r"probability",
        r"证明", r"prove", r"定理", r"theorem",
    ],
    "reasoning": [
        r"分析", r"推理", r"为什么", r"原因", r"逻辑", r"论证",
        r"analyze", r"reason", r"why", r"logic", r"explain",
        r"比较", r"compare", r"评估", r"evaluate",
    ],
    "creative": [
        r"写", r"创作", r"故事", r"诗", r"小说", r"文案",
        r"write", r"create", r"story", r"poem", r"creative",
        r"想象", r"imagine", r"设计", r"design",
    ],
    "translation": [
        r"翻译", r"translate", r"译", r"中译英", r"英译中",
    ],
    "analysis": [
        r"总结", r"摘要", r"归纳", r"提取", r"整理",
        r"summarize", r"summary", r"extract", r"organize",
        r"报告", r"report", r"数据.*分析",
    ],
}

# Pre-compiled patterns
_COMPILED_PATTERNS: Dict[str, List[re.Pattern]] = {
    task: [re.compile(p, re.IGNORECASE) for p in patterns]
    for task, patterns in _TASK_PATTERNS.items()
}

# Tag detection patterns (keyword -> tag mapping)
_TAG_PATTERNS: Dict[str, List[str]] = {
    "coding": [
        r"代码", r"编程", r"函数", r"bug", r"debug", r"实现", r"写一个",
        r"code", r"program", r"function", r"implement", r"script",
        r"python", r"javascript", r"java", r"rust", r"cpp", r"html", r"css",
        r"sql", r"api", r"算法", r"algorithm", r"git", r"deploy",
    ],
    "translation": [
        r"翻译", r"translate", r"译", r"中译英", r"英译中",
        r"translate.*to", r"into.*language",
    ],
    "math": [
        r"计算", r"数学", r"方程", r"积分", r"概率", r"统计",
        r"calculate", r"math", r"equation", r"integral", r"probability",
        r"证明", r"prove", r"定理", r"theorem",
    ],
    "writing": [
        r"写", r"创作", r"故事", r"诗", r"小说", r"文案", r"文章",
        r"write", r"create", r"story", r"poem", r"creative", r"essay",
        r"想象", r"imagine", r"设计", r"design", r"blog", r"letter",
    ],
    "chat": [
        r"你好", r"聊天", r"闲聊", r"讨论", r"说说",
        r"hello", r"hi", r"chat", r"talk", r"discuss", r"tell me",
        r"怎么样", r"如何", r"what do you think",
    ],
}

# Pre-compiled tag patterns
_COMPILED_TAG_PATTERNS: Dict[str, List[re.Pattern]] = {
    tag: [re.compile(p, re.IGNORECASE) for p in patterns]
    for tag, patterns in _TAG_PATTERNS.items()
}

logger = logging.getLogger(__name__)


class TaskTypeDetector:
    """Adaptive task type detector.

    Combines static regex rules with feedback-based learning.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._weights: Dict[str, float] = {}
        self._learned_patterns: Dict[str, List[re.Pattern]] = {}
        self._total_samples = 0
        self._recent_detections: List[Dict[str, Any]] = []

    def detect(self, prompt: str) -> Optional[str]:
        """Detect task type from prompt."""
        if not prompt:
            return None

        matches: List[Dict[str, Any]] = []

        # Static rules
        priority_order = ["coding", "math", "reasoning", "translation", "analysis", "creative"]
        for task in priority_order:
            patterns = _COMPILED_PATTERNS.get(task, [])
            for p in patterns:
                if p.search(prompt):
                    weight = self._weights.get(task, 1.0)
                    matches.append({"task_type": task, "source": "rule", "weight": weight})
                    break

        # Learned patterns
        with self._lock:
            for task, patterns in self._learned_patterns.items():
                for p in patterns:
                    if p.search(prompt):
                        weight = self._weights.get(task, 1.0) * 0.8
                        matches.append({"task_type": task, "source": "learned", "weight": weight})
                        break

        if not matches:
            return None

        matches.sort(key=lambda x: x["weight"], reverse=True)
        result = matches[0]["task_type"]

        # Record detection
        with self._lock:
            self._recent_detections.append({
                "prompt_preview": prompt[:50],
                "detected_type": result,
                "weight": matches[0]["weight"],
                "source": matches[0]["source"],
                "timestamp": time.time(),
            })
            if len(self._recent_detections) > 200:
                self._recent_detections = self._recent_detections[-200:]

        return result

    def learn_from_feedback(self, task_type: str, sentiment: str) -> None:
        """Adjust weights based on feedback."""
        if not task_type:
            return
        with self._lock:
            current = self._weights.get(task_type, 1.0)
            if sentiment == "positive":
                self._weights[task_type] = min(1.5, current + 0.05)
            elif sentiment == "negative":
                self._weights[task_type] = max(0.3, current - 0.1)

    def get_status(self) -> Dict[str, Any]:
        """Get detector status."""
        with self._lock:
            return {
                "weights": dict(self._weights),
                "learned_pattern_counts": {k: len(v) for k, v in self._learned_patterns.items()},
                "total_samples": self._total_samples,
                "recent_detections": list(self._recent_detections[-20:]),
            }

    # ------------------------------------------------------------------ #
    # Modality detection
    # ------------------------------------------------------------------ #

    @staticmethod
    def detect_modalities(messages: list) -> List[str]:
        """Detect modalities from OpenAI-format messages.

        Checks content for type fields (text/image_url/input_audio),
        base64 image data, and image URLs.

        Args:
            messages: OpenAI-format messages list.

        Returns:
            List of detected modalities, e.g. ["text", "image", "audio"].
        """
        modalities: set = set()

        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                modalities.add("text")
                # Check for base64 image data in text
                if "data:image/" in content:
                    modalities.add("image")
                # Check for image URLs
                if re.search(r"https?://\S+\.(jpg|jpeg|png|gif|webp|bmp|svg)", content, re.IGNORECASE):
                    modalities.add("image")
            elif isinstance(content, list):
                # Multi-part content
                for part in content:
                    if isinstance(part, dict):
                        part_type = part.get("type", "")
                        if part_type == "text":
                            modalities.add("text")
                        elif part_type == "image_url":
                            modalities.add("image")
                        elif part_type == "input_audio":
                            modalities.add("audio")
                        elif part_type == "image":
                            modalities.add("image")
                        # Check image_url details for base64
                        image_url = part.get("image_url", {})
                        if isinstance(image_url, dict):
                            url = image_url.get("url", "")
                            if isinstance(url, str) and url.startswith("data:image/"):
                                modalities.add("image")

        return sorted(modalities) if modalities else ["text"]

    # ------------------------------------------------------------------ #
    # Tag detection
    # ------------------------------------------------------------------ #

    @staticmethod
    def detect_tags(prompt: str) -> List[str]:
        """Detect capability tags from prompt using rule-based keyword matching.

        Predefined rules: coding keywords -> coding, translation -> translation,
        math -> math, writing -> writing, chat -> chat.

        Args:
            prompt: The user prompt text.

        Returns:
            List of matched tags.
        """
        if not prompt:
            return []

        tags: List[str] = []
        for tag, patterns in _COMPILED_TAG_PATTERNS.items():
            for p in patterns:
                if p.search(prompt):
                    tags.append(tag)
                    break

        return tags

    @staticmethod
    def predict_tags(prompt: str) -> List[str]:
        """Predict capability tags using ML embedding + simple classification.

        Falls back to rule-based detect_tags if ML is unavailable.

        Args:
            prompt: The user prompt text.

        Returns:
            List of predicted tags.
        """
        if not prompt:
            return []

        # Try ONNX-based tag prediction
        try:
            import numpy as np

            from ..config import get_settings

            settings = get_settings()
            # Use the ML router's embedder for feature extraction
            from .ml_router import MLRouter, _HAS_ONNX

            if not _HAS_ONNX:
                return TaskTypeDetector.detect_tags(prompt)

            # Create a temporary MLRouter to access the embedder
            # (or reuse the global one via routing engine)
            from . import get_routing_engine
            engine = get_routing_engine()
            ml_router = engine.ml_router

            if ml_router._embedder is None:
                return TaskTypeDetector.detect_tags(prompt)

            # Get embedding
            embedding = ml_router._get_embedding(prompt)

            # Simple classification: compute similarity to tag centroids
            # This is a lightweight approach - for production, use a trained classifier
            tag_keywords = {
                "coding": "code program function implement python javascript algorithm",
                "translation": "translate translation 翻译 译",
                "math": "math calculate equation integral probability 计算 数学",
                "writing": "write create story poem creative essay 写 创作",
                "chat": "hello chat talk discuss 你好 聊天",
            }

            best_tags = []
            for tag, keywords in tag_keywords.items():
                keyword_embedding = ml_router._get_embedding(keywords)
                similarity = float(np.dot(embedding[0], keyword_embedding[0]) /
                                   (np.linalg.norm(embedding[0]) * np.linalg.norm(keyword_embedding[0]) + 1e-9))
                if similarity > 0.3:
                    best_tags.append((tag, similarity))

            best_tags.sort(key=lambda x: x[1], reverse=True)
            return [tag for tag, _ in best_tags[:3]]

        except Exception as e:
            logger.debug("ML tag prediction failed, falling back to rules: %s", e)
            return TaskTypeDetector.detect_tags(prompt)


# Global detector
_task_detector = TaskTypeDetector()


def detect_task_type(prompt: str) -> Optional[str]:
    """Detect task type from prompt (convenience function)."""
    return _task_detector.detect(prompt)
