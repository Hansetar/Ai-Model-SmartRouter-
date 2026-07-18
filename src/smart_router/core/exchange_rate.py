"""Exchange rate manager - currency conversion with merge-sync support."""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Optional, Tuple

from .config import get_settings

logger = logging.getLogger(__name__)


class ExchangeRateManager:
    """Exchange rate manager with auto-sync from external API.

    Sync strategy (merge mode):
    - Manual rates are preserved as effective values
    - Fetched rates are stored as reference values
    - When manual rate differs from fetched rate, manual takes priority
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_sync = 0.0
        self._fetched_rates: Dict[str, float] = {}  # Reference rates from external API

    def convert(self, amount: float, from_currency: str, to_currency: str) -> float:
        """Convert amount from one currency to another."""
        if from_currency == to_currency or amount == 0:
            return amount

        settings = get_settings()
        rates = settings.exchange_rates

        # Direct rate
        key = f"{from_currency}_{to_currency}"
        if key in rates:
            return amount * rates[key]

        # Reverse rate
        reverse_key = f"{to_currency}_{from_currency}"
        if reverse_key in rates:
            rate = rates[reverse_key]
            if rate > 0:
                return amount / rate

        # Cross rate via USD
        from_usd = rates.get(f"{from_currency}_USD")
        usd_to = rates.get(f"USD_{to_currency}")

        if from_usd and usd_to:
            usd_amount = amount * from_usd
            return usd_amount * usd_to

        # Cross rate via CNY
        from_cny = rates.get(f"{from_currency}_CNY")
        cny_to = rates.get(f"CNY_{to_currency}")

        if from_cny and cny_to:
            cny_amount = amount * from_cny
            return cny_amount * cny_to

        logger.warning("No exchange rate for %s -> %s", from_currency, to_currency)
        return amount

    def fetch_rates(self) -> Dict[str, float]:
        """Fetch exchange rates from external API without overwriting manual rates.

        Returns the fetched rates dict for display purposes.
        """
        try:
            import httpx
            resp = httpx.get(
                "https://open.er-api.com/v6/latest/USD",
                timeout=10.0,
            )
            if resp.status_code != 200:
                logger.warning("Exchange rate fetch failed: HTTP %d", resp.status_code)
                return {}

            data = resp.json()
            if data.get("result") != "success":
                return {}

            usd_rates = data.get("rates", {})
            fetched: Dict[str, float] = {}

            # Build cross rates
            for curr, rate in usd_rates.items():
                fetched[f"USD_{curr}"] = rate
                if rate > 0:
                    fetched[f"{curr}_USD"] = 1.0 / rate

            # Build CNY cross rates
            cny_to_usd = usd_rates.get("CNY", 1.0)
            if cny_to_usd > 0:
                for curr, rate in usd_rates.items():
                    cny_rate = rate / cny_to_usd
                    fetched[f"CNY_{curr}"] = cny_rate
                    if cny_rate > 0:
                        fetched[f"{curr}_CNY"] = 1.0 / cny_rate

            with self._lock:
                self._fetched_rates = fetched
                self._last_sync = time.time()

            logger.info("Exchange rates fetched: %d rates", len(fetched))
            return fetched

        except Exception as e:
            logger.warning("Exchange rate fetch failed: %s", e)
            return {}

    def sync_rates(self, manual_overrides: Optional[Dict[str, float]] = None) -> bool:
        """Sync exchange rates from external API with merge mode.

        Strategy:
        1. Fetch rates from external API
        2. If manual_overrides provided, those take priority
        3. For keys not in manual_overrides, use fetched values
        4. Save merged result to config
        """
        fetched = self.fetch_rates()
        if not fetched:
            return False

        settings = get_settings()
        current_rates = dict(settings.exchange_rates)  # existing manual rates

        # Mark which keys are manually set (exist in current config before sync)
        manual_keys = set(current_rates.keys())

        # Merge: fetched rates as base, manual rates override
        merged = dict(fetched)
        if manual_overrides:
            merged.update(manual_overrides)
        else:
            # Preserve existing manual rates
            for key in manual_keys:
                if key in current_rates:
                    merged[key] = current_rates[key]

        settings.exchange_rates = merged
        settings.save_to_yaml()

        logger.info(
            "Exchange rates synced (merge): %d total, %d manual, %d fetched",
            len(merged), len(manual_keys), len(fetched),
        )
        return True

    def get_rates_with_reference(self) -> Dict[str, Tuple[float, Optional[float]]]:
        """Get all rates with reference values.

        Returns dict of key -> (effective_rate, reference_rate_or_None).
        Reference rate is the externally fetched rate; if it differs from
        the effective rate, the effective rate is manual.
        """
        settings = get_settings()
        current = settings.exchange_rates
        with self._lock:
            fetched = dict(self._fetched_rates)

        result: Dict[str, Tuple[float, Optional[float]]] = {}
        all_keys = set(current.keys()) | set(fetched.keys())
        for key in all_keys:
            effective = current.get(key, 0.0)
            ref = fetched.get(key)
            result[key] = (effective, ref)
        return result

    @property
    def last_sync_time(self) -> float:
        return self._last_sync


# Global singleton
_exchange_rate_manager: Optional[ExchangeRateManager] = None


def get_exchange_rate_manager() -> ExchangeRateManager:
    """Get or create the global exchange rate manager."""
    global _exchange_rate_manager
    if _exchange_rate_manager is None:
        _exchange_rate_manager = ExchangeRateManager()
    return _exchange_rate_manager
