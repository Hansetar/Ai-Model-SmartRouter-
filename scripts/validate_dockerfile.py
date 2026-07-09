#!/usr/bin/env python3
"""
scripts/validate_dockerfile.py
==============================
验证 Dockerfile 引用的所有文件/目录是否存在，确保构建不会因缺失文件失败。

运行：
    python scripts/validate_dockerfile.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def check_exists(rel_path: str, must_exist: bool = True) -> bool:
    """检查相对路径文件是否存在。"""
    full = ROOT / rel_path
    exists = full.exists()
    status = "OK" if exists else "MISSING"
    print(f"  [{status}] {rel_path}")
    return exists if must_exist else True


def main() -> int:
    print("=" * 60)
    print("Dockerfile 引用文件验证")
    print("=" * 60)

    print("\n[1] 基础文件：")
    all_ok = True
    for f in [
        "Dockerfile",
        "docker-compose.yml",
        ".dockerignore",
        "requirements.txt",
        "config.yaml",
        "main.py",
        "plugin.py",
        "README.md",
    ]:
        if not check_exists(f):
            all_ok = False

    print("\n[2] core/ 内核模块：")
    for f in [
        "core/__init__.py",
        "core/config.py",
        "core/database.py",
        "core/predictor.py",
        "core/router.py",
        "core/pricing_manager.py",
        "core/feedback_analyzer.py",
    ]:
        if not check_exists(f):
            all_ok = False

    print("\n[3] adapters/ 适配层：")
    for f in [
        "adapters/__init__.py",
        "adapters/standalone_app.py",
        "adapters/openclaw_plugin.py",
    ]:
        if not check_exists(f):
            all_ok = False

    print("\n[4] web/ 前端面板：")
    for f in ["web/dist/index.html"]:
        if not check_exists(f):
            all_ok = False

    print("\n[5] scripts/ 脚本：")
    for f in [
        "scripts/inject_feedback.js",
        "scripts/download_minilm.py",
    ]:
        if not check_exists(f):
            all_ok = False

    print("\n[6] models/ 目录：")
    if not check_exists("models/README.md"):
        all_ok = False

    print("\n[7] tests/ 测试：")
    for f in ["tests/__init__.py", "tests/test_core.py"]:
        if not check_exists(f):
            all_ok = False

    print("\n[8] data/ 目录：")
    if not check_exists("data/.gitkeep"):
        all_ok = False

    print("\n" + "=" * 60)
    if all_ok:
        print("✓ 所有文件验证通过，Dockerfile 可正常构建")
        return 0
    else:
        print("✗ 部分文件缺失，请检查后再构建")
        return 1


if __name__ == "__main__":
    sys.exit(main())
