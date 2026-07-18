"""CLI entry point for smart-router."""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="smart-router",
        description="SmartRouter - Intelligent LLM Model Routing Engine",
    )
    parser.add_argument("--host", default=None, help="Host to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="Port to bind (default: 8000)")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--log-level", default=None, help="Log level (debug/info/warning/error)")
    parser.add_argument("--reset-db", action="store_true", help="Reset database on startup")

    args = parser.parse_args()

    # Import after parsing to avoid slow startup for --help
    from .core.config import get_settings
    from .core.storage import init_db, reset_db

    settings = get_settings(args.config)

    if args.reset_db:
        reset_db(settings.storage.effective_url)
        print("Database reset complete.")

    init_db(settings.storage.effective_url)

    host = args.host or settings.web.host
    port = args.port or settings.web.port
    log_level = args.log_level or settings.web.log_level

    # Select web framework
    if settings.web.backend == "litestar":
        _run_litestar(host, port, log_level, args.config)
    else:
        _run_fastapi(host, port, log_level, args.config)


def _run_fastapi(host: str, port: int, log_level: str, config_path: str | None) -> None:
    """Run with FastAPI."""
    import uvicorn
    from .adapters.fastapi_app import create_app

    app = create_app(config_path)

    print("=" * 60)
    print("  SmartRouter v2.0 - FastAPI Mode")
    print(f"  Listening: http://{host}:{port}")
    print(f"  Proxy:     http://{host}:{port}/v1/chat/completions")
    print(f"  Admin:     http://{host}:{port}/admin")
    print(f"  Docs:      http://{host}:{port}/docs")
    print("=" * 60)

    uvicorn.run(app, host=host, port=port, log_level=log_level)


def _run_litestar(host: str, port: int, log_level: str, config_path: str | None) -> None:
    """Run with Litestar (optional)."""
    try:
        import uvicorn
        from .adapters.litestar_app import create_app

        app = create_app(config_path)
        uvicorn.run(app, host=host, port=port, log_level=log_level)
    except ImportError:
        print("Litestar not installed. Install with: pip install smart-router[litestar]")
        sys.exit(1)


if __name__ == "__main__":
    main()
