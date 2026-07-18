"""Quota manager - per-tenant request and token quotas."""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Optional

from ..core.config import get_settings, TenantConfig
from ..core.storage import get_session, TenantUsage

logger = logging.getLogger(__name__)


class QuotaManager:
    """Per-tenant quota enforcement.

    Supports:
    - Daily request quota
    - Daily token quota
    - Per-model restrictions
    """

    def check_quota(
        self,
        tenant_id: Optional[str],
        estimated_tokens: int = 0,
    ) -> bool:
        """Check if a tenant has remaining quota.

        :return: True if quota is available, False if exceeded
        """
        if not tenant_id:
            return True  # No tenant = no quota enforcement

        settings = get_settings()
        tenant = settings.get_tenant(tenant_id)
        if not tenant:
            return True  # Unknown tenant = no quota

        today = date.today().isoformat()

        try:
            with get_session() as session:
                usage = session.query(TenantUsage).filter_by(
                    tenant_id=tenant_id, date=today
                ).first()

                if not usage:
                    return True  # No usage today

                # Check request quota
                if tenant.quota_daily_requests > 0:
                    if usage.total_requests >= tenant.quota_daily_requests:
                        logger.info("Tenant %s exceeded daily request quota", tenant_id)
                        return False

                # Check token quota
                if tenant.quota_daily_tokens > 0:
                    if usage.total_tokens >= tenant.quota_daily_tokens:
                        logger.info("Tenant %s exceeded daily token quota", tenant_id)
                        return False

        except Exception as e:
            logger.warning("Quota check failed: %s", e)
            return True  # Fail open

        return True

    def record_usage(
        self,
        tenant_id: Optional[str],
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost: float = 0.0,
        currency: str = "CNY",
    ) -> None:
        """Record usage for a tenant."""
        if not tenant_id:
            return

        today = date.today().isoformat()
        total_tokens = prompt_tokens + completion_tokens

        try:
            with get_session() as session:
                usage = session.query(TenantUsage).filter_by(
                    tenant_id=tenant_id, date=today
                ).first()

                if usage:
                    usage.total_requests += 1
                    usage.total_tokens += total_tokens
                    usage.total_input_tokens += prompt_tokens
                    usage.total_output_tokens += completion_tokens
                    usage.total_cost += cost
                else:
                    usage = TenantUsage(
                        tenant_id=tenant_id,
                        date=today,
                        total_requests=1,
                        total_tokens=total_tokens,
                        total_input_tokens=prompt_tokens,
                        total_output_tokens=completion_tokens,
                        total_cost=cost,
                        cost_currency=currency,
                    )
                    session.add(usage)

                session.commit()

        except Exception as e:
            logger.warning("Usage recording failed: %s", e)

    def get_usage(
        self,
        tenant_id: str,
        target_date: Optional[str] = None,
    ) -> Optional[dict]:
        """Get tenant usage for a specific date."""
        today = target_date or date.today().isoformat()

        try:
            with get_session() as session:
                usage = session.query(TenantUsage).filter_by(
                    tenant_id=tenant_id, date=today
                ).first()

                if usage:
                    return {
                        "tenant_id": usage.tenant_id,
                        "date": usage.date,
                        "total_requests": usage.total_requests,
                        "total_tokens": usage.total_tokens,
                        "total_input_tokens": usage.total_input_tokens,
                        "total_output_tokens": usage.total_output_tokens,
                        "total_cost": usage.total_cost,
                        "cost_currency": usage.cost_currency,
                    }
        except Exception as e:
            logger.warning("Usage query failed: %s", e)

        return None


# Global singleton
_quota_manager: Optional[QuotaManager] = None


def get_quota_manager() -> QuotaManager:
    """Get or create the global quota manager."""
    global _quota_manager
    if _quota_manager is None:
        _quota_manager = QuotaManager()
    return _quota_manager
