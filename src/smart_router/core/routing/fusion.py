"""Fusion layer - multi-strategy result fusion for dual-path routing.

Four fusion strategies, applied in order:
1. Weighted fusion: merge rankings by configurable weights
2. Voting: select models with majority agreement
3. Primary-backup: ML/RL primary, scoring backup
4. Cascade: scoring coarse-filter, ML/RL fine-rank

Each strategy produces a candidate. The first strategy that produces
a valid result wins. If all fail, the fallback model is used.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class FusionResult:
    """Result from the fusion layer."""

    model: Optional[Dict[str, Any]] = None
    model_name: str = ""
    strategy_used: str = ""
    candidates_count: int = 0
    scoring_rank: List[str] = field(default_factory=list)
    ml_rank: List[str] = field(default_factory=list)
    debug_info: Dict[str, Any] = field(default_factory=dict)


class FusionLayer:
    """Multi-strategy fusion for dual-path routing results.

    Applies four strategies in priority order:
    1. Weighted fusion
    2. Voting
    3. Primary-backup
    4. Cascade

    The first strategy to produce a valid result wins.
    """

    def fuse(
        self,
        scoring_candidates: List[Dict[str, Any]],
        ml_candidates: List[Dict[str, Any]],
        difficulty: int,
        task_type: Optional[str] = None,
    ) -> FusionResult:
        """Fuse results from both routing paths.

        :param scoring_candidates: Ranked candidates from ScoringRouter
        :param ml_candidates: Ranked candidates from MLRouter
        :param difficulty: Predicted difficulty
        :param task_type: Detected task type
        :return: FusionResult with the selected model
        """
        settings = get_settings()
        weights = settings.route_weights

        result = FusionResult(
            scoring_rank=[c.get("model", {}).get("name", "") for c in scoring_candidates[:5]],
            ml_rank=[c.get("model", {}).get("name", "") for c in ml_candidates[:5]],
        )

        # Strategy 1: Weighted fusion
        model = self._weighted_fusion(scoring_candidates, ml_candidates, weights)
        if model:
            result.model = model
            result.model_name = model.get("name", "")
            result.strategy_used = "weighted_fusion"
            result.candidates_count = len(scoring_candidates) + len(ml_candidates)
            return result

        # Strategy 2: Voting
        model = self._voting_fusion(scoring_candidates, ml_candidates)
        if model:
            result.model = model
            result.model_name = model.get("name", "")
            result.strategy_used = "voting"
            result.candidates_count = len(scoring_candidates) + len(ml_candidates)
            return result

        # Strategy 3: Primary-backup (ML primary, scoring backup)
        model = self._primary_backup_fusion(ml_candidates, scoring_candidates)
        if model:
            result.model = model
            result.model_name = model.get("name", "")
            result.strategy_used = "primary_backup"
            result.candidates_count = len(scoring_candidates) + len(ml_candidates)
            return result

        # Strategy 4: Cascade (scoring filter, ML rank)
        model = self._cascade_fusion(scoring_candidates, ml_candidates)
        if model:
            result.model = model
            result.model_name = model.get("name", "")
            result.strategy_used = "cascade"
            result.candidates_count = len(scoring_candidates) + len(ml_candidates)
            return result

        # All strategies failed - use fallback
        result.strategy_used = "fallback"
        return result

    def _weighted_fusion(
        self,
        scoring_candidates: List[Dict[str, Any]],
        ml_candidates: List[Dict[str, Any]],
        weights,
    ) -> Optional[Dict[str, Any]]:
        """Weighted fusion: combine scores from both paths.

        Each model gets a combined score:
        combined = w_scoring * scoring_score + w_ml * ml_score
        where scores are normalized ranks (0=best, 1=worst).
        """
        if not scoring_candidates and not ml_candidates:
            return None

        w_scoring = weights.fusion_weighted
        w_ml = 1.0 - w_scoring  # Complement

        # Build rank maps
        scoring_ranks = {}
        for i, c in enumerate(scoring_candidates):
            name = c.get("model", {}).get("name", "")
            if name:
                scoring_ranks[name] = i

        ml_ranks = {}
        for i, c in enumerate(ml_candidates):
            name = c.get("model", {}).get("name", "")
            if name:
                ml_ranks[name] = i

        # All model names
        all_names = set(scoring_ranks.keys()) | set(ml_ranks.keys())
        if not all_names:
            return None

        max_scoring = max(len(scoring_ranks), 1)
        max_ml = max(len(ml_ranks), 1)

        # Calculate combined scores
        best_name = None
        best_score = float("inf")

        for name in all_names:
            s_rank = scoring_ranks.get(name, max_scoring) / max_scoring
            m_rank = ml_ranks.get(name, max_ml) / max_ml

            # If model only in one path, penalize slightly
            if name not in scoring_ranks:
                s_rank = 1.0
            if name not in ml_ranks:
                m_rank = 1.0

            combined = w_scoring * s_rank + w_ml * m_rank
            if combined < best_score:
                best_score = combined
                best_name = name

        if not best_name:
            return None

        # Find the model dict
        for c in scoring_candidates + ml_candidates:
            if c.get("model", {}).get("name") == best_name:
                return c["model"]

        return None

    def _voting_fusion(
        self,
        scoring_candidates: List[Dict[str, Any]],
        ml_candidates: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Voting fusion: select models that appear in both top-K lists."""
        top_k = 3

        scoring_top = set()
        for c in scoring_candidates[:top_k]:
            name = c.get("model", {}).get("name", "")
            if name:
                scoring_top.add(name)

        ml_top = set()
        for c in ml_candidates[:top_k]:
            name = c.get("model", {}).get("name", "")
            if name:
                ml_top.add(name)

        # Intersection
        common = scoring_top & ml_top
        if common:
            # Pick the one with best scoring rank
            for c in scoring_candidates:
                name = c.get("model", {}).get("name", "")
                if name in common:
                    return c["model"]

        # No intersection - try majority (at least 2 out of 3 agree)
        from collections import Counter
        all_top = list(scoring_top) + list(ml_top)
        counts = Counter(all_top)
        for name, count in counts.most_common():
            if count >= 2:
                for c in scoring_candidates + ml_candidates:
                    if c.get("model", {}).get("name") == name:
                        return c["model"]

        return None

    def _primary_backup_fusion(
        self,
        ml_candidates: List[Dict[str, Any]],
        scoring_candidates: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Primary-backup: ML/RL is primary, scoring is backup."""
        # Try ML first
        if ml_candidates:
            model = ml_candidates[0].get("model")
            if model:
                return model

        # Fallback to scoring
        if scoring_candidates:
            model = scoring_candidates[0].get("model")
            if model:
                return model

        return None

    def _cascade_fusion(
        self,
        scoring_candidates: List[Dict[str, Any]],
        ml_candidates: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Cascade: scoring coarse-filters, ML fine-ranks within candidates."""
        # Scoring filters top-5
        scoring_top_names = set()
        for c in scoring_candidates[:5]:
            name = c.get("model", {}).get("name", "")
            if name:
                scoring_top_names.add(name)

        # ML re-ranks within scoring's top candidates
        for c in ml_candidates:
            name = c.get("model", {}).get("name", "")
            if name in scoring_top_names:
                return c["model"]

        # If ML has no overlap, use scoring's top
        if scoring_candidates:
            return scoring_candidates[0].get("model")

        return None
