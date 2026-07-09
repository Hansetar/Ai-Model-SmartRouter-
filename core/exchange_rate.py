"""
core/exchange_rate.py
=====================
汇率实时同步模块。

- 从公开 API 获取 USD/CNY 汇率
- 定时后台更新
- 提供货币转换工具函数
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Dict, Optional

import httpx

from .config import config


class ExchangeRateManager:
    """汇率管理器：定时同步 + 货币转换。"""

    # 免费汇率 API（无需 API Key）
    EXCHANGE_RATE_API = "https://open.er-api.com/v6/latest/USD"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._last_sync: float = 0
        # 启动后台同步线程
        self._sync_thread = threading.Thread(
            target=self._bg_sync, daemon=True, name="exchange-rate-sync"
        )
        self._sync_thread.start()

    # ------------------------------------------------------------------ #
    # 后台同步
    # ------------------------------------------------------------------ #
    def _bg_sync(self) -> None:
        """定时同步汇率。"""
        # 启动时立即同步一次
        self.sync_now()
        while not self._stop_event.is_set():
            interval = config.exchange_rate_sync_interval_hours * 3600
            self._stop_event.wait(interval)
            if not self._stop_event.is_set():
                self.sync_now()

    def sync_now(self) -> bool:
        """立即同步汇率，返回是否成功。"""
        try:
            resp = httpx.get(self.EXCHANGE_RATE_API, timeout=15.0)
            if resp.status_code != 200:
                print(
                    f"[ExchangeRate] sync failed: HTTP {resp.status_code}",
                    file=sys.stderr,
                )
                return False

            data = resp.json()
            rates = data.get("rates", {})
            usd_cny = rates.get("CNY")
            if not usd_cny:
                print("[ExchangeRate] CNY rate not found in response", file=sys.stderr)
                return False

            usd_cny = float(usd_cny)
            cny_usd = round(1.0 / usd_cny, 6) if usd_cny > 0 else 0

            new_rates = {
                "USD_CNY": round(usd_cny, 4),
                "CNY_USD": cny_usd,
            }

            # 补充其他常见货币对
            for currency, rate in rates.items():
                if currency in ("CNY", "USD"):
                    continue
                new_rates[f"USD_{currency}"] = round(float(rate), 6)
                if float(rate) > 0:
                    new_rates[f"{currency}_USD"] = round(1.0 / float(rate), 6)
                # 交叉汇率：通过 USD 中转
                if usd_cny > 0:
                    new_rates[f"{currency}_CNY"] = round(float(rate) * cny_usd / 1.0, 6)
                    new_rates[f"CNY_{currency}"] = round(1.0 / (float(rate) * cny_usd), 6) if float(rate) * cny_usd > 0 else 0

            with self._lock:
                config.exchange_rates = new_rates
                config.save()

            self._last_sync = time.time()
            print(f"[ExchangeRate] synced: USD_CNY={usd_cny}", file=sys.stderr)
            return True

        except Exception as exc:
            print(f"[ExchangeRate] sync error: {exc}", file=sys.stderr)
            return False

    # ------------------------------------------------------------------ #
    # 货币转换
    # ------------------------------------------------------------------ #
    def convert(self, amount: float, from_currency: str, to_currency: str) -> float:
        """将金额从一种货币转换为另一种。

        :param amount: 金额
        :param from_currency: 源货币（如 USD）
        :param to_currency: 目标货币（如 CNY）
        :return: 转换后的金额
        """
        if from_currency == to_currency:
            return amount
        if amount == 0:
            return 0.0

        rates = config.exchange_rates
        key = f"{from_currency}_{to_currency}"
        rate = rates.get(key)
        if rate:
            return round(amount * rate, 8)

        # 尝试通过 USD 中转
        if from_currency != "USD" and to_currency != "USD":
            # from -> USD -> to
            rate1 = rates.get(f"{from_currency}_USD")
            rate2 = rates.get(f"USD_{to_currency}")
            if rate1 and rate2:
                return round(amount * rate1 * rate2, 8)

        # 无法转换，返回原值
        return amount

    def get_rate(self, from_currency: str, to_currency: str) -> Optional[float]:
        """获取汇率。"""
        if from_currency == to_currency:
            return 1.0
        rates = config.exchange_rates
        return rates.get(f"{from_currency}_{to_currency}")

    def get_status(self) -> Dict[str, any]:
        """获取汇率管理器状态。"""
        return {
            "last_sync": self._last_sync,
            "last_sync_ago_seconds": int(time.time() - self._last_sync) if self._last_sync else None,
            "sync_interval_hours": config.exchange_rate_sync_interval_hours,
            "rates_count": len(config.exchange_rates),
            "usd_cny": config.exchange_rates.get("USD_CNY"),
            "cny_usd": config.exchange_rates.get("CNY_USD"),
        }

    def shutdown(self) -> None:
        self._stop_event.set()


# 全局单例
exchange_rate_manager = ExchangeRateManager()
