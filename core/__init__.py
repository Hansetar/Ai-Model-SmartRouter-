"""
core 包初始化。

导出核心组件，便于双模式适配层统一引用。
"""

from .config import config, Config, ConfigError
from .database import db, Database
from .predictor import OnlinePredictor, predictor
from .pricing_manager import pricing_manager, PricingManager, BalanceCheckerFactory
from .router import router, SmartRouter
from .feedback_analyzer import feedback_analyzer, FeedbackAnalyzer
from .exchange_rate import exchange_rate_manager, ExchangeRateManager
from .fallback_logger import fallback_logger, FallbackLogger
from .auth import (
    create_access_token,
    verify_token,
    verify_password,
    verify_api_key,
    is_api_key_configured,
)

__all__ = [
    "config",
    "Config",
    "ConfigError",
    "db",
    "Database",
    "OnlinePredictor",
    "predictor",
    "pricing_manager",
    "PricingManager",
    "BalanceCheckerFactory",
    "router",
    "SmartRouter",
    "feedback_analyzer",
    "FeedbackAnalyzer",
    "exchange_rate_manager",
    "ExchangeRateManager",
    "fallback_logger",
    "FallbackLogger",
    "create_access_token",
    "verify_token",
    "verify_password",
    "verify_api_key",
    "is_api_key_configured",
]

__version__ = "1.0.0"
