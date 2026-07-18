"""FastAPI application factory - modular, pluggable."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ...core.config import get_settings
from ...core.storage import init_db
from ...health import get_health_checker

logger = logging.getLogger(__name__)

# Background task references
_bg_tasks: list[asyncio.Task] = []


def create_app(config_path: Optional[str] = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    # Initialize settings with fault tolerance
    _init_errors: list[str] = []
    try:
        settings = get_settings(config_path)
    except Exception as e:
        logger.error("Failed to load settings: %s", e)
        _init_errors.append(f"Settings load failed: {e}")
        settings = get_settings()  # Use defaults

    try:
        init_db(settings.storage.effective_url)
    except Exception as e:
        logger.error("Failed to initialize database: %s", e)
        _init_errors.append(f"Database init failed: {e}")

    app = FastAPI(
        title="SmartRouter API",
        description="Intelligent LLM Model Routing Engine",
        version="2.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Store init errors for diagnostics
    app.state.init_errors = _init_errors

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.web.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Concurrency semaphore
    from .middleware.concurrency import ConcurrencyMiddleware
    app.add_middleware(ConcurrencyMiddleware, max_concurrent=settings.web.concurrency_limit)

    # Register routers
    from .routers import chat, models, feedback, admin, health as health_router

    app.include_router(chat.router, prefix="/v1", tags=["Chat"])
    app.include_router(models.router, prefix="/v1", tags=["Models"])
    app.include_router(feedback.router, prefix="/v1", tags=["Feedback"])
    app.include_router(admin.router, prefix="/admin/api", tags=["Admin"])
    app.include_router(health_router.router, prefix="/admin/api", tags=["Health"])

    # Static files for admin panel
    static_dir = Path(__file__).resolve().parent.parent.parent.parent.parent / "web" / "dist"
    if static_dir.exists():
        app.mount("/admin", StaticFiles(directory=str(static_dir), html=True), name="admin")

    # Root path redirect to admin panel
    @app.get("/")
    async def root_redirect():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/admin/")

    # Health check endpoint
    @app.get("/health")
    async def health_check():
        return {"status": "ok", "version": "2.0.0"}

    # Diagnostics endpoint - shows init errors and warnings
    @app.get("/admin/api/diagnostics")
    async def diagnostics():
        errors = getattr(app.state, "init_errors", [])
        return {
            "status": "degraded" if errors else "ok",
            "init_errors": errors,
            "models_dir_exists": Path("models").exists(),
            "models_dir_files": len(list(Path("models").glob("*"))) if Path("models").exists() else 0,
            "config_exists": Path("config.yaml").exists(),
        }

    # Startup/shutdown events
    @app.on_event("startup")
    async def startup():
        # Start health checker
        try:
            checker = get_health_checker()
            await checker.start()
        except Exception as e:
            logger.warning("Failed to start health checker: %s", e)
            _init_errors.append(f"Health checker start failed: {e}")

        # Start auto-tune background task
        try:
            task = asyncio.create_task(_auto_tune_loop())
            _bg_tasks.append(task)
        except Exception as e:
            logger.warning("Failed to start auto-tune loop: %s", e)

        if _init_errors:
            logger.warning("SmartRouter started with %d warnings: %s", len(_init_errors), _init_errors)
        else:
            logger.info("SmartRouter started")

    @app.on_event("shutdown")
    async def shutdown():
        # Cancel background tasks
        for task in _bg_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        _bg_tasks.clear()

        try:
            checker = get_health_checker()
            await checker.stop()
        except Exception as e:
            logger.warning("Failed to stop health checker: %s", e)

        # Save ML models on shutdown
        try:
            from ...core.routing import get_routing_engine
            engine = get_routing_engine()
            engine.ml_router.save_models()
        except Exception as e:
            logger.warning("Failed to save ML models on shutdown: %s", e)

        logger.info("SmartRouter stopped")

    return app


async def _auto_tune_loop() -> None:
    """Background loop for auto-tuning: periodic retrain + RL decay."""
    while True:
        try:
            await asyncio.sleep(300)  # Check every 5 minutes
            from ...core.routing import get_routing_engine
            engine = get_routing_engine()
            result = engine.ml_router.check_auto_retrain()
            if result:
                logger.info("Auto-tune retrain result: %s", result)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("Auto-tune loop error: %s", e)
