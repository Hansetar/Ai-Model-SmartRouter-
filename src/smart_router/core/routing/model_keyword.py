"""Model keyword detection - detect user's model preference from prompt."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Keyword patterns for model selection
_MODEL_KEYWORD_PATTERNS: List[Dict[str, Any]] = [
    # Direct model name matching
    {"pattern": r"(?:使用?|用|选|指定|调用|换|切)(?:deepseek|deep.?seek)", "model_hint": "deepseek", "type": "name_prefix"},
    {"pattern": r"(?:使用?|用|选|指定|调用|换|切)(?:glm|zhipu|智谱)", "model_hint": "glm", "type": "name_prefix"},
    {"pattern": r"(?:使用?|用|选|指定|调用|换|切)(?:gpt|openai)", "model_hint": "gpt", "type": "name_prefix"},
    {"pattern": r"(?:使用?|用|选|指定|调用|换|切)(?:claude|anthropic)", "model_hint": "claude", "type": "name_prefix"},
    {"pattern": r"(?:使用?|用|选|指定|调用|换|切)(?:qwen|通义|千问)", "model_hint": "qwen", "type": "name_prefix"},
    {"pattern": r"(?:使用?|用|选|指定|调用|换|切)(?:llama)", "model_hint": "llama", "type": "name_prefix"},
    # Semantic descriptions
    {"pattern": r"(?:用|使用?|选|需要?|想要?)(?:最强|最好|最大|最智能|最厉害|最强力)(?:的)?(?:模型|大模型|AI)", "model_hint": "strongest", "type": "semantic"},
    {"pattern": r"(?:用|使用?|选|需要?|想要?)(?:最便宜|最省钱|免费|最经济)(?:的)?(?:模型|大模型|AI)", "model_hint": "cheapest", "type": "semantic"},
    {"pattern": r"(?:用|使用?|选|需要?|想要?)(?:最快|最迅速|最快速|低延迟)(?:的)?(?:模型|大模型|AI)", "model_hint": "fastest", "type": "semantic"},
    # English semantic
    {"pattern": r"(?:use|using|select|choose|with)\s+(?:the\s+)?(?:strongest|best|most\s+powerful|largest)\s+(?:model|AI|LLM)", "model_hint": "strongest", "type": "semantic"},
    {"pattern": r"(?:use|using|select|choose|with)\s+(?:the\s+)?(?:cheapest|free|most\s+affordable)\s+(?:model|AI|LLM)", "model_hint": "cheapest", "type": "semantic"},
    {"pattern": r"(?:use|using|select|choose|with)\s+(?:the\s+)?(?:fastest|quickest|lowest\s+latency)\s+(?:model|AI|LLM)", "model_hint": "fastest", "type": "semantic"},
]

_COMPILED_MODEL_KEYWORD_PATTERNS = [
    {**item, "compiled": re.compile(item["pattern"], re.IGNORECASE)}
    for item in _MODEL_KEYWORD_PATTERNS
]


def detect_model_keyword(
    prompt: str,
    available_models: Optional[List[Dict[str, Any]]] = None,
) -> Optional[str]:
    """Detect if user specified a model preference via keywords.

    :param prompt: User's prompt
    :param available_models: Available model list for matching
    :return: Matched model name, or None
    """
    if not prompt:
        return None

    available_models = available_models or []

    for item in _COMPILED_MODEL_KEYWORD_PATTERNS:
        match = item["compiled"].search(prompt)
        if not match:
            continue

        model_hint = item["model_hint"]
        match_type = item["type"]

        if match_type == "name_prefix":
            # Fuzzy match by prefix
            for m in available_models:
                name = m.get("name", "").lower()
                if name.startswith(model_hint.lower()) or model_hint.lower() in name:
                    return m["name"]

        elif match_type == "semantic":
            if model_hint == "strongest":
                best = None
                best_cap = 0
                for m in available_models:
                    cap = m.get("capability", 0)
                    if cap > best_cap:
                        best_cap = cap
                        best = m
                if best:
                    return best["name"]

            elif model_hint == "cheapest":
                cheapest = None
                min_price = float("inf")
                for m in available_models:
                    pi = float(m.get("price_input", 0))
                    po = float(m.get("price_output", 0))
                    total = pi + po
                    if total == 0:
                        return m["name"]
                    if total < min_price:
                        min_price = total
                        cheapest = m
                if cheapest:
                    return cheapest["name"]

            elif model_hint == "fastest":
                fastest = None
                min_params = float("inf")
                for m in available_models:
                    params = float(m.get("params_b", 999))
                    if params < min_params:
                        min_params = params
                        fastest = m
                if fastest:
                    return fastest["name"]

    return None
