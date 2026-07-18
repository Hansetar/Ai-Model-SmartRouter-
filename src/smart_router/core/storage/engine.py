"""Database engine management - pluggable backends with auto-init and migration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

logger = logging.getLogger(__name__)

_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker] = None


def get_engine(db_url: Optional[str] = None) -> Engine:
    """Get or create the database engine.

    Supports:
    - sqlite: sqlite:///path/to/db.db (default)
    - postgresql: postgresql://user:pass@host:port/db
    - mysql: mysql://user:pass@host:port/db
    """
    global _engine
    if _engine is not None:
        return _engine

    if not db_url:
        db_url = "sqlite:///data/smart_router.db"

    # SQLite-specific configuration
    connect_args = {}
    if db_url.startswith("sqlite"):
        # Ensure parent directory exists
        path = db_url.replace("sqlite:///", "")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        connect_args["check_same_thread"] = False

    _engine = create_engine(
        db_url,
        connect_args=connect_args,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=False,
    )

    # SQLite WAL mode for better concurrent read performance
    if db_url.startswith("sqlite"):

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return _engine


def get_session_factory(db_url: Optional[str] = None) -> sessionmaker:
    """Get or create the session factory."""
    global _session_factory
    if _session_factory is not None:
        return _session_factory

    engine = get_engine(db_url)
    _session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return _session_factory


def get_session(db_url: Optional[str] = None) -> Session:
    """Create a new database session."""
    factory = get_session_factory(db_url)
    return factory()


def init_db(db_url: Optional[str] = None) -> None:
    """Initialize database: create all tables if they don't exist.

    This is safe to call multiple times - it uses CREATE IF NOT EXISTS.
    """
    engine = get_engine(db_url)
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized: %s", db_url or "sqlite:///data/smart_router.db")

    # Run Alembic migrations if available
    _run_migrations_if_available(engine)


def reset_db(db_url: Optional[str] = None) -> None:
    """Reset database: drop all tables and recreate.

    WARNING: This destroys all data!
    """
    engine = get_engine(db_url)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    logger.warning("Database reset: all tables dropped and recreated")


def _run_migrations_if_available(engine: Engine) -> None:
    """Run Alembic migrations if alembic is configured."""
    try:
        from alembic.config import Config as AlembicConfig
        from alembic import command

        alembic_cfg_path = Path(__file__).resolve().parent.parent.parent.parent.parent / "alembic.ini"
        if alembic_cfg_path.exists():
            alembic_cfg = AlembicConfig(str(alembic_cfg_path))
            command.upgrade(alembic_cfg, "head")
            logger.info("Alembic migrations applied")
    except ImportError:
        logger.debug("Alembic not installed, skipping migrations")
    except Exception as e:
        logger.warning("Alembic migration failed: %s", e)
