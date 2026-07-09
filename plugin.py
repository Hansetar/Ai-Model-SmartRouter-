"""
plugin.py
=========
OpenClaw 插件模式注册入口。

将 openclaw-smart-router 文件夹放入 OpenClaw 的 plugins/ 目录后，
在 OpenClaw 主配置中启用 plugins: ["openclaw-smart-router"]，
宿主启动时会自动调用此模块的 setup() 完成注册。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.openclaw_plugin import OpenClawSmartRouterPlugin, setup  # noqa: F401

__all__ = ["OpenClawSmartRouterPlugin", "setup"]
