"""
main.py
=======
SmartRouter v2.0 启动入口。

启动 FastAPI 网关，提供：
- /v1/chat/completions  智能路由代理（双线路 ML/RL + 评分）
- /v1/models            模型列表
- /v1/feedback          反馈上报
- /admin/               控制面板
- /admin/api/*          管理 API
- /health               健康检查

用法：
    python main.py
    python main.py --port 8080
    python main.py --config /path/to/config.yaml
    python main.py --reset-db
"""

from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根目录和 src 在 sys.path 中
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
for p in [str(ROOT), str(SRC)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from smart_router.cli import main

if __name__ == "__main__":
    main()
