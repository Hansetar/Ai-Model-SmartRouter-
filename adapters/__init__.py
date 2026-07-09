"""
adapters 包初始化。

导出双模式适配器：
- standalone_app: 独立 FastAPI 网关
- openclaw_plugin: OpenClaw 进程内插件
"""

from .standalone_app import create_app, app
from .openclaw_plugin import OpenClawSmartRouterPlugin, setup

__all__ = ["create_app", "app", "OpenClawSmartRouterPlugin", "setup"]
