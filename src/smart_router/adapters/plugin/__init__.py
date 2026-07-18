"""OpenClaw plugin adapter - embed SmartRouter as a plugin."""

from __future__ import annotations

from typing import Any, Dict, Optional

from ...core.config import get_settings
from ...core.routing import get_routing_engine
from ...core.storage import init_db


class OpenClawSmartRouterPlugin:
    """OpenClaw plugin wrapper for SmartRouter.

    Usage:
        Place the smart-router folder in OpenClaw's plugins/ directory,
        then enable it in the main config: plugins: ["smart-router"]
    """

    name = "openclaw-smart-router"
    version = "2.0.0"

    def __init__(self) -> None:
        self._engine = None

    def setup(self, host_config: Optional[Dict[str, Any]] = None) -> None:
        """Initialize the plugin."""
        settings = get_settings()
        init_db(settings.storage.effective_url)
        self._engine = get_routing_engine()

    def route(self, prompt: str, **kwargs) -> Dict[str, Any]:
        """Route a request to the best model."""
        if not self._engine:
            self.setup()

        result = self._engine.select_model(prompt=prompt, **kwargs)
        return {
            "model": result.model,
            "model_name": result.model_name,
            "strategy": result.strategy_used,
            "debug_info": result.debug_info,
        }


def setup(host_config: Optional[Dict[str, Any]] = None) -> OpenClawSmartRouterPlugin:
    """Plugin setup entry point."""
    plugin = OpenClawSmartRouterPlugin()
    plugin.setup(host_config)
    return plugin
