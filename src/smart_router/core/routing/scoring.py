"""Scoring router - multi-dimensional weighted scoring (Path B).

Score = AdjustedCost / (Reliability * Satisfaction + epsilon)
Free models get Cost=0, highest priority.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..config import get_settings, ModelConfig

logger = logging.getLogger(__name__)


class ScoringRouter:
    """Multi-dimensional scoring router.

    Evaluates models based on:
    - Estimated cost (currency-converted)
    - Task type match
    - Historical reliability (success rate)
    - User satisfaction rate
    - Balance penalty
    - User preference weights
    """

    def select_model(
        self,
        difficulty: int,
        est_in_tokens: int,
        est_out_tokens: int,
        task_type: Optional[str] = None,
        exclude: Optional[List[str]] = None,
        content_types: Optional[List[str]] = None,
        tenant_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        is_global_key: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Select the best model using scoring.

        Returns the model config dict with the lowest combined score,
        or None if no suitable model found.
        """
        settings = get_settings()
        exclude = exclude or []
        candidates = self._evaluate_candidates(
            settings, difficulty, est_in_tokens, est_out_tokens,
            task_type, exclude, content_types, tenant_id, tags, is_global_key,
        )

        if not candidates:
            return None

        candidates.sort(key=lambda x: x["combined_score"])
        return candidates[0]["model"]

    def select_model_candidates(
        self,
        difficulty: int,
        est_in_tokens: int,
        est_out_tokens: int,
        task_type: Optional[str] = None,
        exclude: Optional[List[str]] = None,
        content_types: Optional[List[str]] = None,
        top_k: int = 3,
        tenant_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        is_global_key: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return top-K candidates sorted by score."""
        settings = get_settings()
        exclude = exclude or []
        candidates = self._evaluate_candidates(
            settings, difficulty, est_in_tokens, est_out_tokens,
            task_type, exclude, content_types, tenant_id, tags, is_global_key,
        )

        candidates.sort(key=lambda x: x["combined_score"])
        return [c["model"] for c in candidates[:top_k]]

    def rank_models(
        self,
        difficulty: int,
        est_in_tokens: int,
        est_out_tokens: int,
        task_type: Optional[str] = None,
        exclude: Optional[List[str]] = None,
        content_types: Optional[List[str]] = None,
        tenant_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        is_global_key: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return all candidates ranked by score (for fusion layer)."""
        settings = get_settings()
        exclude = exclude or []
        candidates = self._evaluate_candidates(
            settings, difficulty, est_in_tokens, est_out_tokens,
            task_type, exclude, content_types, tenant_id, tags, is_global_key,
        )
        candidates.sort(key=lambda x: x["combined_score"])
        return candidates

    def _evaluate_candidates(
        self,
        settings,
        difficulty: int,
        est_in_tokens: int,
        est_out_tokens: int,
        task_type: Optional[str],
        exclude: List[str],
        content_types: Optional[List[str]],
        tenant_id: Optional[str],
        tags: Optional[List[str]],
        is_global_key: bool = False,
    ) -> List[Dict[str, Any]]:
        """Evaluate all available models and return scored candidates."""
        from ..storage import get_session, ModelMetric
        from sqlalchemy import select

        route_weights = settings.route_weights
        model_preferences = route_weights.model_preferences
        candidates = []

        for model in settings.models:
            name = model.name
            if name in exclude:
                continue
            if not model.enabled:
                continue
            if not model.is_active_now:
                continue

            # Filter by capability
            if model.effective_capability < difficulty:
                continue

            # Filter by content types / modalities
            if content_types and not self._supports_content_types(model, content_types):
                continue

            # Filter by tenant allowed/blocked models
            if tenant_id and not is_global_key:
                tenant = settings.get_tenant(tenant_id)
                if tenant:
                    mode = getattr(tenant, 'model_filter_mode', 'whitelist')
                    if mode == 'blacklist':
                        # Blacklist: block models in blocked_models list
                        if tenant.blocked_models and name in tenant.blocked_models:
                            continue
                        # If both whitelist and blacklist are set, models outside both lists are blacklisted
                        if tenant.allowed_models and name not in tenant.allowed_models:
                            continue
                    else:
                        # Whitelist: empty allowed_models = no models allowed (deny all)
                        if not tenant.allowed_models:
                            continue
                        if name not in tenant.allowed_models:
                            continue

                    # Check tenant balance: if tenant is in arrears, skip all models
                    try:
                        from ..storage import get_session as _get_session, TenantBalance as _TenantBalance
                        with _get_session() as _session:
                            _balance_record = _session.get(_TenantBalance, tenant.tenant_id)
                            if _balance_record and not getattr(_balance_record, 'unlimited', False) and _balance_record.balance <= 0:
                                continue  # Tenant in arrears, skip this model
                    except Exception:
                        pass  # Fail-open for balance check in routing

            # Get metrics from database
            reliability = 0.9
            satisfaction = 0.9
            try:
                with get_session() as session:
                    metric = session.get(ModelMetric, name)
                    if metric:
                        reliability = metric.success_rate
                        satisfaction = metric.satisfaction_rate
            except Exception:
                pass

            # Balance penalty - check provider balance
            balance_penalty = 0.0
            if not model.is_free:
                # Check model-level balance
                if model.balance_manual is not None and model.balance_manual <= 0:
                    balance_penalty = 1000.0
                # Check provider-level balance
                if model.provider:
                    provider = settings.get_provider(model.provider)
                    if provider and provider.balance_manual is not None and provider.balance_manual <= 0:
                        balance_penalty = 1000.0
                    elif provider and provider.balance_manual is not None and provider.balance_manual < 1.0:
                        balance_penalty = 500.0

            # Calculate cost
            cost = self._calculate_cost(model, est_in_tokens, est_out_tokens, settings)

            # Task type match
            task_match = self._calc_task_match(model, task_type)

            # Free model bonus (lower score = better)
            free_bonus = 0.0
            if model.is_free:
                free_bonus = 0.5  # Significant priority for free models

            # Difficulty matching: penalize over-qualified models for simple tasks
            difficulty_penalty = 0.0
            if difficulty <= 20 and model.effective_capability >= 75:
                # Simple task, high-capability model - penalize
                difficulty_penalty = 0.3
            elif difficulty <= 40 and model.effective_capability >= 90:
                difficulty_penalty = 0.15

            # Tag matching: boost models with matching capability tags
            tag_match = self._calc_tag_match(model, tags)

            # Score calculation
            adjusted_cost = cost / task_match if task_match > 0 else cost
            score_value = adjusted_cost / (reliability * satisfaction + 0.01)

            # Preference weight
            preference_weight = float(model_preferences.get(name, 0))

            # Combined score (lower is better)
            combined_score = (
                score_value
                - preference_weight
                + balance_penalty
                - free_bonus
                + difficulty_penalty
                - tag_match
            )

            candidates.append({
                "model": settings.get_enriched_model(name),
                "cost": cost,
                "adjusted_cost": adjusted_cost,
                "score": score_value,
                "reliability": reliability,
                "satisfaction": satisfaction,
                "task_match": task_match,
                "tag_match": tag_match,
                "preference_weight": preference_weight,
                "combined_score": combined_score,
            })

        return candidates

    @staticmethod
    def _calculate_cost(
        model: ModelConfig,
        est_in_tokens: int,
        est_out_tokens: int,
        settings,
    ) -> float:
        """Calculate estimated cost in target currency."""
        from ..exchange_rate import get_exchange_rate_manager

        unit_divisor = ScoringRouter._get_unit_divisor(model.price_unit)
        raw_cost = (est_in_tokens * model.price_input + est_out_tokens * model.price_output) / unit_divisor

        if model.price_currency == settings.currency:
            return raw_cost

        erm = get_exchange_rate_manager()
        return erm.convert(raw_cost, model.price_currency, settings.currency)

    @staticmethod
    def _get_unit_divisor(price_unit: str) -> float:
        """Convert price_unit string to divisor."""
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

    @staticmethod
    def _calc_task_match(model: ModelConfig, task_type: Optional[str]) -> float:
        """Calculate task type match score (0.5-1.5)."""
        if not task_type:
            return 1.0
        if not model.task_types:
            return 1.0
        if task_type in model.task_types:
            return 1.5
        return 0.5

    @staticmethod
    def _supports_content_types(model: ModelConfig, content_types: List[str]) -> bool:
        """Check if model supports all requested content types."""
        non_text = [t for t in content_types if t != "text"]
        if not non_text:
            return True
        if not model.modalities:
            return False
        return all(ct in model.modalities for ct in non_text)

    @staticmethod
    def _calc_tag_match(model: ModelConfig, tags: Optional[List[str]]) -> float:
        """Calculate tag match score (0.0-0.5).

        Returns a bonus value: more matching tags = higher bonus (lower combined score).
        """
        if not tags:
            return 0.0
        if not model.capability_tags:
            return 0.0
        matching = set(tags) & set(model.capability_tags)
        if not matching:
            return 0.0
        # 0.1 per matching tag, max 0.5
        return min(0.5, len(matching) * 0.1)
