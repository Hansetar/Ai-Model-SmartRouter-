"""
core/fallback_logger.py
=======================
后备链与样本管理专用日志模块。

提供结构化日志记录，所有日志写入 SQLite 的 fallback_logs 表，
同时输出到 stderr。支持按事件类型、模型名、时间范围查询。
"""

from __future__ import annotations

import queue
import sqlite3
import sys
import threading
import time
import json as _json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import config


# ---------------------------------------------------------------------- #
# 建表 SQL
# ---------------------------------------------------------------------- #
_CREATE_FALLBACK_LOGS = """
CREATE TABLE IF NOT EXISTS fallback_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       REAL    NOT NULL,
    event_type      TEXT    NOT NULL,
    original_model  TEXT,
    fallback_model  TEXT,
    attempt         INTEGER,
    failed_models   TEXT,
    error_message   TEXT,
    prompt_preview  TEXT,
    extra           TEXT
);
"""

_CREATE_SAMPLE_LOGS = """
CREATE TABLE IF NOT EXISTS sample_management_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       REAL    NOT NULL,
    event_type      TEXT    NOT NULL,
    sample_id       INTEGER,
    source          TEXT,
    prompt_preview  TEXT,
    difficulty      INTEGER,
    task_type       TEXT,
    extra           TEXT
);
"""

_CREATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_fallback_logs_ts ON fallback_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_fallback_logs_event ON fallback_logs(event_type);
CREATE INDEX IF NOT EXISTS idx_fallback_logs_model ON fallback_logs(original_model);
CREATE INDEX IF NOT EXISTS idx_sample_logs_ts ON sample_management_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_sample_logs_event ON sample_management_logs(event_type);
"""


class FallbackLogger:
    """后备链与样本管理专用日志记录器。

    所有写操作通过队列异步执行，保证主请求路径零阻塞。
    """

    _instance: Optional["FallbackLogger"] = None
    _lock = threading.Lock()

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path: str = db_path or str(
            Path(__file__).resolve().parent.parent / "data" / "smart_router.db"
        )
        self._init_schema()
        self._write_queue: queue.Queue[Tuple[str, tuple]] = queue.Queue()
        self._writer_thread = threading.Thread(
            target=self._bg_writer, daemon=True, name="fallback-logger"
        )
        self._writer_thread.start()

    @classmethod
    def get_instance(cls, db_path: Optional[str] = None) -> "FallbackLogger":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(db_path)
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._instance = None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_CREATE_FALLBACK_LOGS)
            conn.executescript(_CREATE_SAMPLE_LOGS)
            conn.executescript(_CREATE_INDEXES)
            conn.commit()

    def _bg_writer(self) -> None:
        """后台线程：消费写队列。"""
        while True:
            try:
                sql, params = self._write_queue.get(timeout=0.1)
                with self._connect() as conn:
                    conn.execute(sql, params)
                    conn.commit()
            except queue.Empty:
                continue
            except Exception as exc:  # noqa: BLE001
                print(f"[FallbackLogger] async write failed: {exc}", file=sys.stderr)

    # ------------------------------------------------------------------ #
    # 后备链日志
    # ------------------------------------------------------------------ #
    def log_fallback(
        self,
        original_model: str,
        fallback_model: str,
        attempt: int,
        failed_models: List[str],
        error: str = "",
        prompt_preview: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """记录后备链切换事件。"""
        now = time.time()
        sql = (
            "INSERT INTO fallback_logs "
            "(timestamp, event_type, original_model, fallback_model, attempt, "
            " failed_models, error_message, prompt_preview, extra) "
            "VALUES (?, 'fallback_switch', ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            now,
            original_model,
            fallback_model,
            attempt,
            _json.dumps(failed_models),
            error[:500] if error else "",
            prompt_preview[:200] if prompt_preview else "",
            _json.dumps(extra) if extra else None,
        )
        self._write_queue.put((sql, params))
        # 同时输出到 stderr
        print(
            f"[FallbackLogger] {original_model} -> {fallback_model} "
            f"(attempt={attempt}, failed={failed_models}, error={error[:100]})",
            file=sys.stderr,
        )

    def log_fallback_exhausted(
        self,
        original_model: str,
        failed_models: List[str],
        error: str = "",
        prompt_preview: str = "",
    ) -> None:
        """记录后备链耗尽事件（所有模型都失败）。"""
        now = time.time()
        sql = (
            "INSERT INTO fallback_logs "
            "(timestamp, event_type, original_model, fallback_model, attempt, "
            " failed_models, error_message, prompt_preview, extra) "
            "VALUES (?, 'fallback_exhausted', ?, NULL, NULL, ?, ?, ?, NULL)"
        )
        params = (
            now,
            original_model,
            _json.dumps(failed_models),
            error[:500] if error else "",
            prompt_preview[:200] if prompt_preview else "",
        )
        self._write_queue.put((sql, params))
        print(
            f"[FallbackLogger] EXHAUSTED: all models failed for {original_model} "
            f"(failed={failed_models})",
            file=sys.stderr,
        )

    # ------------------------------------------------------------------ #
    # 样本管理日志
    # ------------------------------------------------------------------ #
    def log_sample_auto_collected(
        self,
        prompt_preview: str = "",
        difficulty: int = 0,
        task_type: Optional[str] = None,
    ) -> None:
        """记录自动采集样本事件。"""
        now = time.time()
        sql = (
            "INSERT INTO sample_management_logs "
            "(timestamp, event_type, sample_id, source, prompt_preview, difficulty, task_type, extra) "
            "VALUES (?, 'auto_collected', NULL, 'auto', ?, ?, ?, NULL)"
        )
        params = (now, prompt_preview[:200] if prompt_preview else "", difficulty, task_type)
        self._write_queue.put((sql, params))

    def log_sample_adjusted(
        self,
        sample_id: int,
        source: str = "manual_adjust",
        prompt_preview: str = "",
        difficulty: int = 0,
        task_type: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """记录样本调整事件。"""
        now = time.time()
        sql = (
            "INSERT INTO sample_management_logs "
            "(timestamp, event_type, sample_id, source, prompt_preview, difficulty, task_type, extra) "
            "VALUES (?, 'adjusted', ?, ?, ?, ?, ?, ?)"
        )
        params = (
            now, sample_id, source,
            prompt_preview[:200] if prompt_preview else "",
            difficulty, task_type,
            _json.dumps(extra) if extra else None,
        )
        self._write_queue.put((sql, params))
        print(
            f"[FallbackLogger] sample #{sample_id} adjusted (diff={difficulty}, type={task_type})",
            file=sys.stderr,
        )

    def log_sample_capacity_enforced(
        self,
        max_capacity: int,
        deleted_count: int,
    ) -> None:
        """记录样本容量控制事件。"""
        now = time.time()
        sql = (
            "INSERT INTO sample_management_logs "
            "(timestamp, event_type, sample_id, source, prompt_preview, difficulty, task_type, extra) "
            "VALUES (?, 'capacity_enforced', NULL, NULL, NULL, NULL, NULL, ?)"
        )
        params = (now, _json.dumps({"max_capacity": max_capacity, "deleted_count": deleted_count}))
        self._write_queue.put((sql, params))

    # ------------------------------------------------------------------ #
    # 查询接口
    # ------------------------------------------------------------------ #
    def get_fallback_logs(
        self,
        event_type: Optional[str] = None,
        model: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """获取后备链日志。"""
        with self._connect() as conn:
            conditions = []
            params: list = []
            if event_type:
                conditions.append("event_type=?")
                params.append(event_type)
            if model:
                conditions.append("(original_model=? OR fallback_model=?)")
                params.extend([model, model])
            where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
            cur = conn.execute(
                f"SELECT * FROM fallback_logs{where} ORDER BY id DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            )
            return [dict(r) for r in cur.fetchall()]

    def count_fallback_logs(self, event_type: Optional[str] = None) -> int:
        """统计后备链日志数量。"""
        with self._connect() as conn:
            if event_type:
                cur = conn.execute(
                    "SELECT COUNT(*) AS c FROM fallback_logs WHERE event_type=?",
                    (event_type,),
                )
            else:
                cur = conn.execute("SELECT COUNT(*) AS c FROM fallback_logs")
            return cur.fetchone()["c"]

    def get_sample_logs(
        self,
        event_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """获取样本管理日志。"""
        with self._connect() as conn:
            conditions = []
            params: list = []
            if event_type:
                conditions.append("event_type=?")
                params.append(event_type)
            where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
            cur = conn.execute(
                f"SELECT * FROM sample_management_logs{where} ORDER BY id DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            )
            return [dict(r) for r in cur.fetchall()]

    def count_sample_logs(self, event_type: Optional[str] = None) -> int:
        """统计样本管理日志数量。"""
        with self._connect() as conn:
            if event_type:
                cur = conn.execute(
                    "SELECT COUNT(*) AS c FROM sample_management_logs WHERE event_type=?",
                    (event_type,),
                )
            else:
                cur = conn.execute("SELECT COUNT(*) AS c FROM sample_management_logs")
            return cur.fetchone()["c"]

    def get_fallback_stats(self) -> Dict[str, Any]:
        """获取后备链统计信息。"""
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) AS c FROM fallback_logs").fetchone()["c"]
            # 按事件类型统计
            event_stats = conn.execute(
                "SELECT event_type, COUNT(*) AS count FROM fallback_logs GROUP BY event_type"
            ).fetchall()
            # 按原始模型统计
            model_stats = conn.execute(
                "SELECT original_model, COUNT(*) AS count FROM fallback_logs "
                "GROUP BY original_model ORDER BY count DESC"
            ).fetchall()
            # 按后备模型统计
            fallback_stats = conn.execute(
                "SELECT fallback_model, COUNT(*) AS count FROM fallback_logs "
                "WHERE fallback_model IS NOT NULL GROUP BY fallback_model ORDER BY count DESC"
            ).fetchall()
            # 最近的后备切换
            recent = conn.execute(
                "SELECT * FROM fallback_logs ORDER BY id DESC LIMIT 20"
            ).fetchall()
        return {
            "total_logs": total,
            "event_distribution": [dict(r) for r in event_stats],
            "model_distribution": [dict(r) for r in model_stats],
            "fallback_distribution": [dict(r) for r in fallback_stats],
            "recent": [dict(r) for r in recent],
        }


# 全局单例
fallback_logger = FallbackLogger.get_instance()
