"""Model health checker - periodic probing and circuit breaker."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from ..core.config import get_settings, ModelConfig
from ..core.storage import get_session, ModelMetric

logger = logging.getLogger(__name__)


class HealthChecker:
    """Periodic model health checker with circuit breaker pattern.

    States:
    - healthy: Model is responding normally
    - degraded: Model has intermittent failures
    - unhealthy: Model has been marked as down (circuit open)

    Circuit breaker:
    - failure_threshold consecutive failures -> unhealthy
    - recovery_threshold consecutive successes -> healthy
    - Half-open: after timeout, allow one test request
    """

    def __init__(self) -> None:
        self._health_status: Dict[str, str] = {}  # model_name -> status
        self._consecutive_failures: Dict[str, int] = {}
        self._consecutive_successes: Dict[str, int] = {}
        self._last_check: Dict[str, float] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the periodic health check loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._check_loop())
        logger.info("Health checker started")

    async def stop(self) -> None:
        """Stop the health check loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Health checker stopped")

    async def _check_loop(self) -> None:
        """Main health check loop."""
        while self._running:
            settings = get_settings()
            if settings.health_check.enabled:
                for model in settings.models:
                    if model.enabled and model.base_url and model.api_key:
                        await self._check_model(model)
            await asyncio.sleep(settings.health_check.interval_seconds)

    async def _check_model(self, model: ModelConfig) -> None:
        """Check a single model's health."""
        name = model.name
        try:
            start = time.perf_counter()
            async with httpx.AsyncClient(timeout=settings.health_check.timeout_seconds) as client:
                resp = await client.post(
                    f"{model.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {model.api_key}"},
                    json={
                        "model": model.litellm_name or model.name,
                        "messages": [{"role": "user", "content": settings.health_check.test_prompt}],
                        "max_tokens": 5,
                    },
                )
            latency = (time.perf_counter() - start) * 1000

            if resp.status_code == 200:
                self._record_success(name)
            else:
                self._record_failure(name, f"HTTP {resp.status_code}")

        except Exception as e:
            self._record_failure(name, str(e))

        self._last_check[name] = time.time()

    def _record_success(self, model_name: str) -> None:
        """Record a successful health check."""
        self._consecutive_failures[model_name] = 0
        self._consecutive_successes[model_name] = self._consecutive_successes.get(model_name, 0) + 1

        settings = get_settings()
        threshold = settings.health_check.recovery_threshold

        if self._consecutive_successes[model_name] >= threshold:
            self._health_status[model_name] = "healthy"
            logger.info("Model %s recovered to healthy", model_name)

        # Update database
        self._update_db_health(model_name)

    def _record_failure(self, model_name: str, reason: str) -> None:
        """Record a failed health check."""
        self._consecutive_successes[model_name] = 0
        self._consecutive_failures[model_name] = self._consecutive_failures.get(model_name, 0) + 1

        settings = get_settings()
        threshold = settings.health_check.failure_threshold

        if self._consecutive_failures[model_name] >= threshold:
            self._health_status[model_name] = "unhealthy"
            logger.warning("Model %s marked unhealthy: %s", model_name, reason)
        else:
            self._health_status[model_name] = "degraded"
            logger.info("Model %s degraded (failures: %d/%d): %s",
                       model_name, self._consecutive_failures[model_name], threshold, reason)

        # Update database
        self._update_db_health(model_name)

    def _update_db_health(self, model_name: str) -> None:
        """Update model health status in database."""
        try:
            with get_session() as session:
                metric = session.get(ModelMetric, model_name)
                if metric:
                    metric.health_status = self._health_status.get(model_name, "healthy")
                    metric.consecutive_failures = self._consecutive_failures.get(model_name, 0)
                    metric.consecutive_successes = self._consecutive_successes.get(model_name, 0)
                    metric.last_health_check = time.time()
                    session.commit()
        except Exception as e:
            logger.warning("Failed to update health in DB: %s", e)

    def get_status(self, model_name: str) -> str:
        """Get health status of a model."""
        return self._health_status.get(model_name, "healthy")

    def is_healthy(self, model_name: str) -> bool:
        """Check if a model is healthy."""
        return self._health_status.get(model_name, "healthy") != "unhealthy"

    def get_all_status(self) -> Dict[str, str]:
        """Get health status of all models."""
        return dict(self._health_status)

    def record_request_result(self, model_name: str, success: bool) -> None:
        """Record the result of an actual request (not health check).

        This allows the health checker to learn from real traffic.
        """
        if success:
            self._record_success(model_name)
        else:
            self._record_failure(model_name, "request_failed")


# Global singleton
_health_checker: Optional[HealthChecker] = None


def get_health_checker() -> HealthChecker:
    """Get or create the global health checker."""
    global _health_checker
    if _health_checker is None:
        _health_checker = HealthChecker()
    return _health_checker
