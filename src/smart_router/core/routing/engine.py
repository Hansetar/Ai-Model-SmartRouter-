"""Main routing engine - orchestrates dual-path routing with fusion and fallback."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..config import get_settings
from .scoring import ScoringRouter
from .ml_router import MLRouter
from .fusion import FusionLayer, FusionResult
from .task_detector import TaskTypeDetector, detect_task_type
from .model_keyword import detect_model_keyword

logger = logging.getLogger(__name__)


class RoutingEngine:
    """Intelligent routing engine with dual-path ML/RL + scoring.

    Architecture:
    ┌─────────────┐     ┌─────────────┐
    │  ML/RL Path │     │ Scoring Path│
    │ (MLRouter)  │     │(ScoringRouter)│
    └──────┬──────┘     └──────┬──────┘
           │                    │
           └──────┬─────────────┘
                  │
          ┌───────▼───────┐
          │  Fusion Layer  │
          │ (4 strategies) │
          └───────┬───────┘
                  │
          ┌───────▼───────┐
          │  Fallback      │
          │  (default model)│
          └───────────────┘
    """

    def __init__(self) -> None:
        self._scoring = ScoringRouter()
        self._ml = MLRouter()
        self._fusion = FusionLayer()
        self._task_detector = TaskTypeDetector()

    def select_model(
        self,
        prompt: str,
        difficulty: Optional[int] = None,
        est_in_tokens: int = 500,
        est_out_tokens: int = 500,
        task_type: Optional[str] = None,
        exclude: Optional[List[str]] = None,
        content_types: Optional[List[str]] = None,
        requested_model: Optional[str] = None,
        tenant_id: Optional[str] = None,
        is_global_key: bool = False,
    ) -> FusionResult:
        """Main entry point: select the best model for a request.

        Flow:
        1. Check if user specified a model via keyword -> direct route
        2. Detect task type if not provided
        3. Predict difficulty if not provided
        4. Run both routing paths in parallel
        5. Fuse results
        6. Fallback if needed
        """
        settings = get_settings()
        exclude = exclude or []

        # Step 1: User-specified model via keyword
        if requested_model:
            resolved = settings.resolve_model_name(requested_model)
            model = settings.get_enriched_model(resolved)
            if model and model.get("enabled", True):
                return FusionResult(
                    model=model,
                    model_name=resolved,
                    strategy_used="user_specified",
                    candidates_count=1,
                )

        # Check keyword-based model selection
        keyword_model = detect_model_keyword(prompt, [
            {"name": m.name, "capability": m.effective_capability,
             "price_input": m.price_input, "price_output": m.price_output,
             "params_b": m.params_b}
            for m in settings.models if m.enabled
        ])
        if keyword_model:
            model = settings.get_enriched_model(keyword_model)
            if model:
                return FusionResult(
                    model=model,
                    model_name=keyword_model,
                    strategy_used="keyword_match",
                    candidates_count=1,
                )

        # Step 2: Detect task type
        if not task_type:
            task_type = detect_task_type(prompt)

        # Step 3: Predict difficulty
        if difficulty is None:
            difficulty, est_tokens = self._ml.predict(prompt)
            est_in_tokens = est_tokens
            est_out_tokens = est_tokens

        # Map tokens to difficulty using config ranges
        token_difficulty = settings.tokens_to_difficulty(est_in_tokens + est_out_tokens)
        difficulty = max(difficulty, token_difficulty)

        # Step 4: Run both paths
        scoring_candidates = self._scoring.rank_models(
            difficulty=difficulty,
            est_in_tokens=est_in_tokens,
            est_out_tokens=est_out_tokens,
            task_type=task_type,
            exclude=exclude,
            content_types=content_types,
            tenant_id=tenant_id,
            is_global_key=is_global_key,
        )

        ml_candidates = self._ml.rank_models(
            prompt=prompt,
            difficulty=difficulty,
            est_tokens=est_in_tokens,
            task_type=task_type,
            exclude=exclude,
            tenant_id=tenant_id,
            is_global_key=is_global_key,
        )

        # Step 5: Fuse
        result = self._fusion.fuse(
            scoring_candidates=scoring_candidates,
            ml_candidates=ml_candidates,
            difficulty=difficulty,
            task_type=task_type,
        )

        # Step 6: Fallback
        if not result.model:
            fallback_name = settings.fallback_model or settings.default_model
            if fallback_name:
                model = settings.get_enriched_model(fallback_name)
                if model:
                    result.model = model
                    result.model_name = fallback_name
                    result.strategy_used = "fallback"

        # Add metadata
        result.debug_info.update({
            "difficulty": difficulty,
            "est_in_tokens": est_in_tokens,
            "est_out_tokens": est_out_tokens,
            "task_type": task_type,
            "scoring_candidates_count": len(scoring_candidates),
            "ml_candidates_count": len(ml_candidates),
        })

        return result

    def select_fallback_chain(
        self,
        difficulty: int,
        est_in_tokens: int,
        est_out_tokens: int,
        failed_models: List[str],
        task_type: Optional[str] = None,
        content_types: Optional[List[str]] = None,
        tenant_id: Optional[str] = None,
        is_global_key: bool = False,
    ) -> List[Dict[str, Any]]:
        """Get fallback model chain after primary model failure.

        Returns all available models sorted by score, excluding failed ones.
        """
        settings = get_settings()

        candidates = self._scoring.rank_models(
            difficulty=difficulty,
            est_in_tokens=est_in_tokens,
            est_out_tokens=est_out_tokens,
            task_type=task_type,
            exclude=failed_models,
            content_types=content_types,
            tenant_id=tenant_id,
            is_global_key=is_global_key,
        )

        return [c["model"] for c in candidates]

    def record_feedback(
        self,
        model_name: str,
        task_type: Optional[str],
        success: bool,
        satisfaction: Optional[str] = None,
    ) -> None:
        """Record feedback for RL policy update."""
        reward = 1.0 if success else -1.0
        if satisfaction == "positive":
            reward = 2.0
        elif satisfaction == "negative":
            reward = -2.0

        self._ml.update_rl_policy(task_type, model_name, reward)

        # Also update task detector weights
        if task_type and satisfaction:
            self._task_detector.learn_from_feedback(task_type, satisfaction)

    @property
    def ml_router(self) -> MLRouter:
        return self._ml

    @property
    def scoring_router(self) -> ScoringRouter:
        return self._scoring

    def get_status(self) -> Dict[str, Any]:
        """Get routing engine status."""
        return {
            "ml": self._ml.get_status(),
            "task_detector": self._task_detector.get_status(),
        }


# Global singleton
_engine: Optional[RoutingEngine] = None


def get_routing_engine() -> RoutingEngine:
    """Get or create the global routing engine instance."""
    global _engine
    if _engine is None:
        _engine = RoutingEngine()
    return _engine
