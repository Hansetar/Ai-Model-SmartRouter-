"""
main.py
=======
独立模式启动入口。

启动 FastAPI 网关，监听 8000 端口，提供：
- /v1/chat/completions  智能路由代理
- /v1/models            模型列表
- /v1/feedback          反馈上报
- /admin/               控制面板
- /health               健康检查

用法：
    python main.py
    uvicorn main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn
from adapters.standalone_app import create_app


def main() -> None:
    host = os.environ.get("SMARTROUTER_HOST", "0.0.0.0")
    port = int(os.environ.get("SMARTROUTER_PORT", "8000"))
    log_level = os.environ.get("SMARTROUTER_LOG_LEVEL", "info")

    app = create_app()

    print("=" * 60)
    print("  OpenClaw SmartRouter - 独立模式启动")
    print(f"  监听: http://{host}:{port}")
    print(f"  代理: http://{host}:{port}/v1/chat/completions")
    print(f"  面板: http://{host}:{port}/admin")
    print(f"  健康: http://{host}:{port}/health")
    print("=" * 60)

    uvicorn.run(app, host=host, port=port, log_level=log_level)


if __name__ == "__main__":
    main()
