"""
core/router.py
==============
智能决策路由。

综合预测难度、预估 Token、实时价格、API 余量、历史可靠性、用户满意度、任务类型匹配，
按公式 Score = AdjustedCost / (Reliability * Satisfaction + 0.01) 选取最优模型。
免费模型 Cost=0，优先级最高。
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, List, Optional

from .config import config
from .database import db
from .pricing_manager import pricing_manager
from .exchange_rate import exchange_rate_manager


# ---------------------------------------------------------------------- #
# 请求类型推断（静态规则 + 自适应学习）
# ---------------------------------------------------------------------- #
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

# 预编译正则
_COMPILED_PATTERNS: Dict[str, List[re.Pattern]] = {
    task: [re.compile(p, re.IGNORECASE) for p in patterns]
    for task, patterns in _TASK_PATTERNS.items()
}


class TaskTypeDetector:
    """自适应任务类型检测器。

    基于静态正则规则 + 历史反馈学习，自动调整任务类型推断。

    学习机制：
    1. 静态规则优先：使用预定义的关键词正则匹配
    2. 反馈纠偏：负面反馈降低对应 task_type 的置信度，正面反馈提升
    3. 历史统计：基于历史请求中各 task_type 的成功率，调整推断权重
    4. 关键词学习：从成功请求中提取高频词，自动扩充匹配规则
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # 各 task_type 的置信度权重（1.0 为默认）
        self._weights: Dict[str, float] = {}
        # 从历史学习到的额外关键词
        self._learned_patterns: Dict[str, List[re.Pattern]] = {}
        # 训练样本计数
        self._total_samples = 0
        # 缓存最近检测结果
        self._recent_detections: List[Dict[str, Any]] = []
        # 从数据库加载历史统计
        self._load_from_db()

    def _load_from_db(self) -> None:
        """从数据库加载历史 task_type 统计，初始化权重。"""
        try:
            stats = db.get_task_type_stats()
            for s in stats:
                tt = s.get("task_type", "")
                total = s.get("total_count", 0)
                pos = s.get("positive_count", 0)
                if total > 0 and tt:
                    # 成功率越高，权重越高（0.5 ~ 1.5）
                    success_rate = pos / total
                    self._weights[tt] = 0.5 + success_rate
                    self._total_samples += total
        except Exception:  # noqa: BLE001
            pass

    def detect(self, prompt: str) -> Optional[str]:
        """从 prompt 推断请求类型。

        :param prompt: 用户输入的 prompt
        :return: 推断的任务类型，无法判断时返回 None（视为通用 chat）
        """
        if not prompt:
            return None

        # 收集所有匹配结果及其权重
        matches: List[Dict[str, Any]] = []

        # 1. 静态规则匹配
        priority_order = ["coding", "math", "reasoning", "translation", "analysis", "creative"]
        for task in priority_order:
            patterns = _COMPILED_PATTERNS.get(task, [])
            for p in patterns:
                if p.search(prompt):
                    weight = self._weights.get(task, 1.0)
                    matches.append({"task_type": task, "source": "rule", "weight": weight})
                    break  # 每种类型只匹配一次

        # 2. 学习到的关键词匹配
        with self._lock:
            for task, patterns in self._learned_patterns.items():
                for p in patterns:
                    if p.search(prompt):
                        weight = self._weights.get(task, 1.0) * 0.8  # 学习规则权重略低
                        matches.append({"task_type": task, "source": "learned", "weight": weight})
                        break

        if not matches:
            return None

        # 按权重排序，选权重最高的
        matches.sort(key=lambda x: x["weight"], reverse=True)
        result = matches[0]["task_type"]

        # 记录最近检测结果
        with self._lock:
            self._recent_detections.append({
                "prompt_preview": prompt[:50],
                "detected_type": result,
                "weight": matches[0]["weight"],
                "source": matches[0]["source"],
                "timestamp": time.time(),
            })
            # 只保留最近 200 条
            if len(self._recent_detections) > 200:
                self._recent_detections = self._recent_detections[-200:]

        return result

    def learn_from_feedback(self, task_type: str, sentiment: str) -> None:
        """从用户反馈中学习，调整 task_type 权重。

        :param task_type: 请求的任务类型
        :param sentiment: positive / negative
        """
        if not task_type:
            return
        with self._lock:
            current = self._weights.get(task_type, 1.0)
            if sentiment == "positive":
                # 正面反馈：提升权重（上限 1.5）
                self._weights[task_type] = min(1.5, current + 0.05)
            elif sentiment == "negative":
                # 负面反馈：降低权重（下限 0.3）
                self._weights[task_type] = max(0.3, current - 0.1)

    def learn_keywords(self, task_type: str, prompt: str, success: bool) -> None:
        """从成功请求中学习关键词模式。

        当某个 prompt 成功完成且检测到 task_type 时，
        提取 prompt 中的关键词，如果该词在历史中多次出现，
        则将其加入该 task_type 的学习规则。

        :param task_type: 任务类型
        :param prompt: 原始 prompt
        :param success: 请求是否成功
        """
        if not task_type or not prompt or not success:
            return

        # 简单关键词提取：取 2-4 字的中文词和英文单词
        import re as _re

        # 中文 2-4 字词
        cn_words = _re.findall(r'[\u4e00-\u9fff]{2,4}', prompt)
        # 英文单词（3+ 字母）
        en_words = _re.findall(r'[a-zA-Z]{3,}', prompt.lower())

        keywords = cn_words + en_words
        if not keywords:
            return

        with self._lock:
            if task_type not in self._learned_patterns:
                self._learned_patterns[task_type] = []

            existing_texts = {p.pattern for p in self._learned_patterns[task_type]}
            # 只添加不在静态规则中的新关键词
            static_texts = set()
            for patterns in _TASK_PATTERNS.values():
                static_texts.update(patterns)

            for kw in keywords:
                if kw not in existing_texts and kw not in static_texts and len(kw) >= 2:
                    try:
                        self._learned_patterns[task_type].append(
                            re.compile(_re.escape(kw), re.IGNORECASE)
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    # 每种类型最多学习 50 个关键词
                    if len(self._learned_patterns[task_type]) > 50:
                        self._learned_patterns[task_type] = self._learned_patterns[task_type][-50:]

    def get_status(self) -> Dict[str, Any]:
        """获取检测器状态信息。"""
        with self._lock:
            return {
                "weights": dict(self._weights),
                "learned_pattern_counts": {
                    k: len(v) for k, v in self._learned_patterns.items()
                },
                "total_samples": self._total_samples,
                "recent_detections": list(self._recent_detections[-20:]),
            }


# 全局检测器实例
task_type_detector = TaskTypeDetector()


# ---------------------------------------------------------------------- #
# 关键词定向模型选择（智能语义分析）
# ---------------------------------------------------------------------- #
# 用户在 prompt 中使用特定关键词来指定想用的模型
# 支持中英文模型名、别名、以及语义化描述
_MODEL_KEYWORD_PATTERNS: List[Dict[str, Any]] = [
    # 直接模型名匹配（支持常见变体）
    {"pattern": r"(?:使用?|用|选|指定|调用|换|切)(?:deepseek|deep.?seek)", "model_hint": "deepseek", "type": "name_prefix"},
    {"pattern": r"(?:使用?|用|选|指定|调用|换|切)(?:glm|zhipu|智谱)", "model_hint": "glm", "type": "name_prefix"},
    {"pattern": r"(?:使用?|用|选|指定|调用|换|切)(?:gpt|openai)", "model_hint": "gpt", "type": "name_prefix"},
    {"pattern": r"(?:使用?|用|选|指定|调用|换|切)(?:claude|anthropic)", "model_hint": "claude", "type": "name_prefix"},
    {"pattern": r"(?:使用?|用|选|指定|调用|换|切)(?:qwen|通义|千问)", "model_hint": "qwen", "type": "name_prefix"},
    {"pattern": r"(?:使用?|用|选|指定|调用|换|切)(?:llama)", "model_hint": "llama", "type": "name_prefix"},
    # 语义化描述匹配
    {"pattern": r"(?:用|使用?|选|需要?|想要?)(?:最强|最好|最大|最智能|最厉害|最强力)(?:的)?(?:模型|大模型|AI)", "model_hint": "strongest", "type": "semantic"},
    {"pattern": r"(?:用|使用?|选|需要?|想要?)(?:最便宜|最省钱|免费|最经济|最便宜)(?:的)?(?:模型|大模型|AI)", "model_hint": "cheapest", "type": "semantic"},
    {"pattern": r"(?:用|使用?|选|需要?|想要?)(?:最快|最迅速|最快速|低延迟)(?:的)?(?:模型|大模型|AI)", "model_hint": "fastest", "type": "semantic"},
    # 英文语义
    {"pattern": r"(?:use|using|select|choose|with)\s+(?:the\s+)?(?:strongest|best|most\s+powerful|largest)\s+(?:model|AI|LLM)", "model_hint": "strongest", "type": "semantic"},
    {"pattern": r"(?:use|using|select|choose|with)\s+(?:the\s+)?(?:cheapest|free|most\s+affordable)\s+(?:model|AI|LLM)", "model_hint": "cheapest", "type": "semantic"},
    {"pattern": r"(?:use|using|select|choose|with)\s+(?:the\s+)?(?:fastest|quickest|lowest\s+latency)\s+(?:model|AI|LLM)", "model_hint": "fastest", "type": "semantic"},
]

_COMPILED_MODEL_KEYWORD_PATTERNS = [
    {**item, "compiled": re.compile(item["pattern"], re.IGNORECASE)}
    for item in _MODEL_KEYWORD_PATTERNS
]


def detect_model_keyword(prompt: str, available_models: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    """从 prompt 中检测用户是否通过关键词指定了想用的模型。

    智能语义分析：
    1. 直接模型名匹配：如"用deepseek回答"、"使用glm"
    2. 语义化描述匹配：如"用最强的模型"、"用最便宜的"
    3. 模糊匹配：根据关键词前缀匹配可用模型名

    :param prompt: 用户输入的 prompt
    :param available_models: 可用模型列表（用于模糊匹配）
    :return: 匹配到的模型名，未匹配返回 None
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
            # 模糊匹配：根据前缀匹配可用模型名
            for m in available_models:
                name = m.get("name", "").lower()
                if name.startswith(model_hint.lower()) or model_hint.lower() in name:
                    return m["name"]
            # 也检查 upstream_model_name
            for m in available_models:
                upstream = m.get("upstream_model_name", "").lower()
                if upstream.startswith(model_hint.lower()) or model_hint.lower() in upstream:
                    return m["name"]

        elif match_type == "semantic":
            if model_hint == "strongest":
                # 选能力最高的模型
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
                # 选最便宜的模型（免费优先）
                cheapest = None
                min_price = float("inf")
                for m in available_models:
                    pi = float(m.get("price_input", 0))
                    po = float(m.get("price_output", 0))
                    total_price = pi + po
                    if total_price == 0:
                        return m["name"]  # 免费模型直接返回
                    if total_price < min_price:
                        min_price = total_price
                        cheapest = m
                if cheapest:
                    return cheapest["name"]

            elif model_hint == "fastest":
                # 选参数量最小的模型（通常最快）
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


def detect_task_type(prompt: str) -> Optional[str]:
    """从 prompt 推断请求类型（兼容旧接口）。

    :param prompt: 用户输入的 prompt
    :return: 推断的任务类型，无法判断时返回 None（视为通用 chat）
    """
    return task_type_detector.detect(prompt)


class SmartRouter:
    """智能决策路由器。"""

    def __init__(self) -> None:
        self._pricing = pricing_manager
        self._db = db

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def select_model(
        self,
        difficulty: int,
        est_in_tokens: int,
        est_out_tokens: int,
        exclude: Optional[List[str]] = None,
        task_type: Optional[str] = None,
        predictor_recommendation: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """选取花费最少且能胜任的模型。

        双机制路由：
        1. 预测模型推荐：predictor 根据特征推荐一个模型
        2. 评分选模型：综合 cost/reliability/satisfaction/task_match 评分

        最终排序 = predictor_weight * 预测推荐分 + score_weight * 评分分 + 用户偏好加成

        :param difficulty: 预测难度 1-5
        :param est_in_tokens: 预估输入 Token
        :param est_out_tokens: 预估输出 Token
        :param exclude: 排除的模型名列表（重试场景）
        :param task_type: 请求类型
        :param predictor_recommendation: 预测模型推荐的模型名
        :return: 选中的模型 dict，无候选返回 None
        """
        exclude = exclude or []
        available = self._pricing.get_available_models()
        route_weights = config.route_weights
        predictor_weight = float(route_weights.get("predictor_weight", 0.5))
        score_weight = float(route_weights.get("score_weight", 0.5))
        model_preferences = route_weights.get("model_preferences", {})

        candidates: List[Dict[str, Any]] = []

        for model in available:
            name = model["name"]
            if name in exclude:
                continue

            # 1. 过滤能力不足
            if model.get("capability", 0) < difficulty:
                continue

            # 2. 过滤余额耗尽的付费模型
            price_input = float(model.get("price_input", 0))
            price_output = float(model.get("price_output", 0))
            if price_input > 0 or price_output > 0:
                balance = model.get("balance")
                if balance is not None and balance <= 0:
                    continue

            # 3. 计算预估花费（免费模型为 0，统一转为用户选择的货币）
            # price_input/price_output 的单位由 price_unit 决定（默认 1M=每百万token）
            target_currency = config.currency
            model_currency = model.get("price_currency", "USD")
            price_unit = model.get("price_unit", "1M")
            # 将 price_unit 转换为每 token 的除数
            unit_divisor = self._get_unit_divisor(price_unit)
            raw_cost = (est_in_tokens * price_input + est_out_tokens * price_output) / unit_divisor
            cost = exchange_rate_manager.convert(raw_cost, model_currency, target_currency)

            # 4. 任务类型匹配度
            task_match = self._calc_task_match(model, task_type)

            # 5. 历史可靠性与满意度
            metrics = self._db.get_metrics(name)
            reliability = metrics["success_rate"] if metrics else 0.9
            satisfaction = metrics["satisfaction_rate"] if metrics else 0.9

            # 6. 评分选模型分（越低越好，归一化到 0-1）
            adjusted_cost = cost / task_match if task_match > 0 else cost
            score_value = adjusted_cost / (reliability * satisfaction + 0.01)

            # 7. 预测模型推荐分（推荐模型得 1.0，其他得 0.0）
            predictor_score = 1.0 if predictor_recommendation and name == predictor_recommendation else 0.0

            # 8. 用户偏好加成
            preference_weight = float(model_preferences.get(name, 0))

            # 9. 综合评分（越低越好）
            # predictor_score 越高越好（1.0=推荐），score_value 越低越好
            # 归一化：将 predictor_score 反转（1.0 -> 0, 0.0 -> 1），使综合分越低越好
            combined_score = (
                predictor_weight * (1.0 - predictor_score)  # 预测推荐：推荐模型得 0，其他得 predictor_weight
                + score_weight * score_value                 # 评分：越低越好
                - preference_weight                          # 偏好：减去偏好权重，使偏好模型得分更低
            )

            candidates.append(
                {
                    "model": model,
                    "cost": cost,
                    "adjusted_cost": adjusted_cost,
                    "score": score_value,
                    "reliability": reliability,
                    "satisfaction": satisfaction,
                    "task_match": task_match,
                    "predictor_score": predictor_score,
                    "preference_weight": preference_weight,
                    "combined_score": combined_score,
                }
            )

        if not candidates:
            # 降级：返回默认模型
            default = config.get_default_model()
            return default

        # 按综合评分排序：combined_score 最低的优先
        candidates.sort(key=lambda x: x["combined_score"])
        selected = candidates[0]
        return selected["model"]

    def select_model_candidates(
        self,
        difficulty: int,
        est_in_tokens: int,
        est_out_tokens: int,
        exclude: Optional[List[str]] = None,
        task_type: Optional[str] = None,
        predictor_recommendation: Optional[str] = None,
        top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """返回按综合评分排序的候选模型列表（用于预测推荐展示）。

        参数与 select_model 相同，top_k 指定返回前几名。
        """
        exclude = exclude or []
        available = self._pricing.get_available_models()
        route_weights = config.route_weights
        predictor_weight = float(route_weights.get("predictor_weight", 0.5))
        score_weight = float(route_weights.get("score_weight", 0.5))
        model_preferences = route_weights.get("model_preferences", {})

        candidates: List[Dict[str, Any]] = []

        for model in available:
            name = model["name"]
            if name in exclude:
                continue
            if model.get("capability", 0) < difficulty:
                continue
            price_input = float(model.get("price_input", 0))
            price_output = float(model.get("price_output", 0))
            if price_input > 0 or price_output > 0:
                balance = model.get("balance")
                if balance is not None and balance <= 0:
                    continue

            target_currency = config.currency
            model_currency = model.get("price_currency", "USD")
            price_unit = model.get("price_unit", "1M")
            unit_divisor = self._get_unit_divisor(price_unit)
            raw_cost = (est_in_tokens * price_input + est_out_tokens * price_output) / unit_divisor
            cost = exchange_rate_manager.convert(raw_cost, model_currency, target_currency)

            task_match = self._calc_task_match(model, task_type)
            metrics = self._db.get_metrics(name)
            reliability = metrics["success_rate"] if metrics else 0.9
            satisfaction = metrics["satisfaction_rate"] if metrics else 0.9

            adjusted_cost = cost / task_match if task_match > 0 else cost
            score_value = adjusted_cost / (reliability * satisfaction + 0.01)
            predictor_score = 1.0 if predictor_recommendation and name == predictor_recommendation else 0.0
            preference_weight = float(model_preferences.get(name, 0))

            combined_score = (
                predictor_weight * (1.0 - predictor_score)
                + score_weight * score_value
                - preference_weight
            )

            candidates.append({
                "model": model,
                "cost": cost,
                "combined_score": combined_score,
                "predictor_score": predictor_score,
                "task_match": task_match,
            })

        candidates.sort(key=lambda x: x["combined_score"])
        return candidates[:top_k]

    @staticmethod
    def _get_unit_divisor(price_unit: str) -> float:
        """将 price_unit 字符串转换为除数。

        支持的单位:
        - "1" 或 "per_token": 每 token
        - "1K": 每千 token
        - "1M": 每百万 token（默认）
        - "1B": 每十亿 token
        """
        unit = str(price_unit).strip().upper()
        if unit in ("1", "PER_TOKEN", ""):
            return 1
        if unit in ("1K", "K"):
            return 1_000
        if unit in ("1M", "M"):
            return 1_000_000
        if unit in ("1B", "B"):
            return 1_000_000_000
        # 尝试解析数字
        try:
            return float(unit)
        except (ValueError, TypeError):
            return 1_000_000  # 默认按 1M

    def _calc_task_match(self, model: Dict[str, Any], task_type: Optional[str]) -> float:
        """计算模型与请求类型的匹配度。

        :return: 匹配度 0.5-1.5
            - 模型明确支持该类型: 1.5 (加分)
            - 模型未声明 task_types 或请求类型未知: 1.0 (中性)
            - 模型声明了 task_types 但不包含该类型: 0.5 (减分，但不完全排除)
        """
        if not task_type:
            return 1.0  # 未知类型，中性

        model_tasks = model.get("task_types", [])
        if not model_tasks:
            return 1.0  # 模型未声明类型，中性

        if task_type in model_tasks:
            return 1.5  # 匹配，加分

        return 0.5  # 不匹配，减分

    # ------------------------------------------------------------------ #
    # 重试与容灾
    # ------------------------------------------------------------------ #
    def select_fallback(
        self,
        difficulty: int,
        est_in_tokens: int,
        est_out_tokens: int,
        failed_model: str,
        task_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """主模型失败后的降级路由。"""
        # 标记该模型可靠性降低
        self._degrade_reliability(failed_model)
        return self.select_model(
            difficulty, est_in_tokens, est_out_tokens,
            exclude=[failed_model], task_type=task_type,
        )

    def select_fallback_chain(
        self,
        difficulty: int,
        est_in_tokens: int,
        est_out_tokens: int,
        failed_models: List[str],
        task_type: Optional[str] = None,
        strict_capability: bool = True,
    ) -> List[Dict[str, Any]]:
        """获取按价格排序的后备模型链：免费 -> 便宜 -> 贵。

        返回排除已失败模型后，按价格从低到高排序的所有可用模型列表。
        调用方可依次尝试，直到成功为止。

        :param difficulty: 预测难度 1-5
        :param est_in_tokens: 预估输入 Token
        :param est_out_tokens: 预估输出 Token
        :param failed_models: 已失败的模型名列表
        :param task_type: 请求类型
        :param strict_capability: 是否严格过滤能力不足的模型。
            True=过滤 capability<difficulty 的模型（首次路由），
            False=不过滤，允许降级使用能力较低的模型（兜底场景）
        :return: 按价格排序的可用模型列表
        """
        available = self._pricing.get_available_models()
        candidates: List[Dict[str, Any]] = []

        for model in available:
            name = model["name"]
            if name in failed_models:
                continue

            # 严格模式下过滤能力不足的模型
            if strict_capability and model.get("capability", 0) < difficulty:
                continue

            # 过滤余额耗尽的付费模型
            price_input = float(model.get("price_input", 0))
            price_output = float(model.get("price_output", 0))
            if price_input > 0 or price_output > 0:
                balance = model.get("balance")
                if balance is not None and balance <= 0:
                    continue

            # 计算预估花费（price 单位由 price_unit 决定）
            target_currency = config.currency
            model_currency = model.get("price_currency", "USD")
            price_unit = model.get("price_unit", "1M")
            unit_divisor = self._get_unit_divisor(price_unit)
            raw_cost = (est_in_tokens * price_input + est_out_tokens * price_output) / unit_divisor
            cost = exchange_rate_manager.convert(raw_cost, model_currency, target_currency)

            # 任务类型匹配度
            task_match = self._calc_task_match(model, task_type)

            # 历史可靠性
            metrics = self._db.get_metrics(name)
            reliability = metrics["success_rate"] if metrics else 0.9

            # 能力差距惩罚：能力低于难度的模型排在后面
            capability = model.get("capability", 0)
            capability_penalty = max(0, difficulty - capability) * 1000 if capability < difficulty else 0

            candidates.append({
                "model": model,
                "cost": cost,
                "task_match": task_match,
                "reliability": reliability,
                "capability_penalty": capability_penalty,
            })

        # 排序：免费模型优先，然后按 cost 从低到高
        # 兜底模式下，能力不足的模型排在后面（通过 penalty）
        candidates.sort(key=lambda x: (x["capability_penalty"], x["cost"], -x["reliability"]))

        return [c["model"] for c in candidates]

    def _degrade_reliability(self, model_name: str) -> None:
        """降低模型可靠性评分。"""
        try:
            metrics = self._db.get_metrics(model_name)
            if metrics:
                new_rate = max(0.1, metrics["success_rate"] * 0.8)
                from .database import Database

                with Database.get_instance()._connect() as conn:
                    conn.execute(
                        "UPDATE model_metrics SET success_rate=? WHERE model_name=?",
                        (new_rate, model_name),
                    )
                    conn.commit()
        except Exception as exc:  # noqa: BLE001
            import sys

            print(f"[Router] degrade reliability failed: {exc}", file=sys.stderr)

    # ------------------------------------------------------------------ #
    # 缓存（Prompt MD5 短时间复用）
    # ------------------------------------------------------------------ #
    def get_cached_route(self, prompt_hash: str) -> Optional[Dict[str, Any]]:
        """5 分钟内相同 Prompt 直接复用上次成功的模型。"""
        try:
            from .database import Database

            with Database.get_instance()._connect() as conn:
                cur = conn.execute(
                    "SELECT routed_model, timestamp FROM request_logs "
                    "WHERE prompt_hash=? AND success=1 "
                    "ORDER BY id DESC LIMIT 1",
                    (prompt_hash,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                if time.time() - row["timestamp"] > config.cache_ttl_seconds:
                    return None
                model = config.get_model(row["routed_model"])
                return model
        except Exception:  # noqa: BLE001
            return None


# 全局单例
router = SmartRouter()
