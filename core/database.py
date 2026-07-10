"""
core/database.py
================
SQLite ORM 层。

负责持久化调用记录、模型聚合指标、用户反馈。所有写操作均通过队列异步执行，
保证主请求路径零阻塞。表结构对应设计书第六节。
"""

from __future__ import annotations

import queue
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import config


# ---------------------------------------------------------------------- #
# 建表 SQL
# ---------------------------------------------------------------------- #
_CREATE_REQUEST_LOGS = """
CREATE TABLE IF NOT EXISTS request_logs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp           REAL    NOT NULL,
    prompt_hash         TEXT    NOT NULL,
    predicted_difficulty INTEGER,
    actual_difficulty   INTEGER,
    routed_model        TEXT,
    cost                REAL,
    cost_currency       TEXT    DEFAULT 'CNY',
    latency_ms          INTEGER,
    success             INTEGER,
    task_type           TEXT,
    route_source        TEXT,
    prompt_preview      TEXT,
    requested_model     TEXT,
    prompt_tokens       INTEGER DEFAULT 0,
    completion_tokens   INTEGER DEFAULT 0
);
"""

_SCHEMA = _CREATE_REQUEST_LOGS + """
CREATE TABLE IF NOT EXISTS model_metrics (
    model_name          TEXT PRIMARY KEY,
    success_rate        REAL DEFAULT 0.9,
    satisfaction_rate   REAL DEFAULT 0.9,
    total_calls         INTEGER DEFAULT 0,
    success_calls       INTEGER DEFAULT 0,
    positive_feedback   INTEGER DEFAULT 0,
    negative_feedback   INTEGER DEFAULT 0,
    last_balance        REAL,
    last_sync_time      REAL
);

CREATE TABLE IF NOT EXISTS feedback_records (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id          TEXT,
    feedback_type       TEXT,   -- explicit / implicit
    sentiment           TEXT,   -- positive / negative
    context_snapshot    TEXT,
    timestamp           REAL
);

CREATE TABLE IF NOT EXISTS task_type_stats (
    task_type           TEXT PRIMARY KEY,
    total_count         INTEGER DEFAULT 0,
    positive_count      INTEGER DEFAULT 0,
    negative_count      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS training_samples (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt              TEXT    NOT NULL,
    difficulty          INTEGER NOT NULL,
    est_tokens          INTEGER DEFAULT 500,
    task_type           TEXT,
    model_name          TEXT,
    source              TEXT    DEFAULT 'auto',
    is_new              INTEGER DEFAULT 1,
    new_mark_ttl        REAL    DEFAULT 3600,
    created_at          REAL,
    updated_at          REAL
);

CREATE INDEX IF NOT EXISTS idx_request_logs_ts ON request_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_request_logs_model ON request_logs(routed_model);
CREATE INDEX IF NOT EXISTS idx_request_logs_task ON request_logs(task_type);
CREATE INDEX IF NOT EXISTS idx_training_samples_source ON training_samples(source);
"""


class Database:
    """SQLite 数据访问层 + 异步写入队列。"""

    _instance: Optional["Database"] = None
    _lock = threading.Lock()

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path: str = db_path or str(
            Path(__file__).resolve().parent.parent / "data" / "smart_router.db"
        )
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._write_queue: queue.Queue[Tuple[str, tuple]] = queue.Queue()
        self._writer_thread = threading.Thread(
            target=self._bg_writer, daemon=True, name="db-writer"
        )
        self._writer_thread.start()

    @classmethod
    def get_instance(cls, db_path: Optional[str] = None) -> "Database":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(db_path)
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        """重置单例（测试用）。"""
        with cls._lock:
            cls._instance = None

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            # 先迁移旧表
            self._migrate(conn)
            # 再执行建表（IF NOT EXISTS 对已存在的表无影响）
            conn.executescript(_SCHEMA)
            conn.commit()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """处理数据库迁移。"""
        try:
            # 检查 request_logs 是否有 task_type 列
            cur = conn.execute("PRAGMA table_info(request_logs)")
            columns = [row["name"] for row in cur.fetchall()]
            if columns and "task_type" not in columns:
                conn.execute("ALTER TABLE request_logs ADD COLUMN task_type TEXT")
                conn.commit()
            if columns and "cost_currency" not in columns:
                conn.execute("ALTER TABLE request_logs ADD COLUMN cost_currency TEXT DEFAULT 'CNY'")
                conn.commit()
            if columns and "route_source" not in columns:
                conn.execute("ALTER TABLE request_logs ADD COLUMN route_source TEXT")
                conn.commit()
            if columns and "prompt_preview" not in columns:
                conn.execute("ALTER TABLE request_logs ADD COLUMN prompt_preview TEXT")
                conn.commit()
            if columns and "requested_model" not in columns:
                conn.execute("ALTER TABLE request_logs ADD COLUMN requested_model TEXT")
                conn.commit()
            if columns and "prompt_tokens" not in columns:
                conn.execute("ALTER TABLE request_logs ADD COLUMN prompt_tokens INTEGER DEFAULT 0")
                conn.commit()
            if columns and "completion_tokens" not in columns:
                conn.execute("ALTER TABLE request_logs ADD COLUMN completion_tokens INTEGER DEFAULT 0")
                conn.commit()
            # 训练样本表新增标记字段
            cur2 = conn.execute("PRAGMA table_info(training_samples)")
            ts_columns = [row["name"] for row in cur2.fetchall()]
            if ts_columns and "is_new" not in ts_columns:
                conn.execute("ALTER TABLE training_samples ADD COLUMN is_new INTEGER DEFAULT 1")
                conn.commit()
            if ts_columns and "new_mark_ttl" not in ts_columns:
                conn.execute("ALTER TABLE training_samples ADD COLUMN new_mark_ttl REAL DEFAULT 3600")
                conn.commit()
            if ts_columns and "model_name" not in ts_columns:
                conn.execute("ALTER TABLE training_samples ADD COLUMN model_name TEXT")
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            import sys
            print(f"[Database] migration failed: {exc}", file=sys.stderr)

    def _bg_writer(self) -> None:
        """后台线程：批量消费写队列。"""
        while True:
            try:
                sql, params = self._write_queue.get(timeout=0.1)
                with self._connect() as conn:
                    conn.execute(sql, params)
                    conn.commit()
            except queue.Empty:
                continue
            except Exception as exc:  # noqa: BLE001
                # 写失败不能影响主流程，仅记录到 stderr
                import sys

                print(f"[Database] async write failed: {exc}", file=sys.stderr)

    # ------------------------------------------------------------------ #
    # 写接口（全部异步）
    # ------------------------------------------------------------------ #
    def log_request(
        self,
        prompt_hash: str,
        predicted_difficulty: int,
        actual_difficulty: Optional[int],
        routed_model: str,
        cost: float,
        latency_ms: int,
        success: bool,
        task_type: Optional[str] = None,
        cost_currency: str = "CNY",
        route_source: Optional[str] = None,
        prompt_preview: Optional[str] = None,
        requested_model: Optional[str] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        sql = (
            "INSERT INTO request_logs "
            "(timestamp, prompt_hash, predicted_difficulty, actual_difficulty, "
            " routed_model, cost, cost_currency, latency_ms, success, task_type, "
            " route_source, prompt_preview, requested_model, prompt_tokens, completion_tokens) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            time.time(),
            prompt_hash,
            predicted_difficulty,
            actual_difficulty,
            routed_model,
            cost,
            cost_currency,
            latency_ms,
            1 if success else 0,
            task_type,
            route_source,
            prompt_preview,
            requested_model,
            prompt_tokens,
            completion_tokens,
        )
        self._write_queue.put((sql, params))

        # 同步更新模型聚合指标（轻量计算）
        self._update_model_metric(routed_model, success)

        # 更新 task_type 统计
        if task_type:
            self._update_task_type_stat(task_type, success)

    def _update_model_metric(self, model_name: str, success: bool) -> None:
        """更新模型成功率。"""
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    "SELECT total_calls, success_calls FROM model_metrics WHERE model_name=?",
                    (model_name,),
                )
                row = cur.fetchone()
                if row is None:
                    total, succ = 0, 0
                else:
                    total, succ = row["total_calls"], row["success_calls"]
                total += 1
                succ += 1 if success else 0
                rate = succ / total if total else 0.9
                conn.execute(
                    "INSERT INTO model_metrics(model_name, success_rate, total_calls, success_calls) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(model_name) DO UPDATE SET "
                    "success_rate=excluded.success_rate, "
                    "total_calls=excluded.total_calls, "
                    "success_calls=excluded.success_calls",
                    (model_name, rate, total, succ),
                )
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            import sys

            print(f"[Database] update metric failed: {exc}", file=sys.stderr)

    def _update_task_type_stat(self, task_type: str, success: bool) -> None:
        """更新任务类型统计。"""
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    "SELECT total_count, positive_count, negative_count FROM task_type_stats WHERE task_type=?",
                    (task_type,),
                )
                row = cur.fetchone()
                if row is None:
                    total, pos, neg = 0, 0, 0
                else:
                    total, pos, neg = row["total_count"], row["positive_count"], row["negative_count"]
                total += 1
                if success:
                    pos += 1
                else:
                    neg += 1
                conn.execute(
                    "INSERT INTO task_type_stats(task_type, total_count, positive_count, negative_count) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(task_type) DO UPDATE SET "
                    "total_count=excluded.total_count, "
                    "positive_count=excluded.positive_count, "
                    "negative_count=excluded.negative_count",
                    (task_type, total, pos, neg),
                )
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            import sys

            print(f"[Database] update task_type stat failed: {exc}", file=sys.stderr)

    def record_feedback(
        self,
        request_id: str,
        feedback_type: str,
        sentiment: str,
        context_snapshot: str = "",
    ) -> None:
        sql = (
            "INSERT INTO feedback_records "
            "(request_id, feedback_type, sentiment, context_snapshot, timestamp) "
            "VALUES (?, ?, ?, ?, ?)"
        )
        self._write_queue.put(
            (sql, (request_id, feedback_type, sentiment, context_snapshot, time.time()))
        )
        # 反向更新模型满意度
        try:
            with self._connect() as conn:
                # 找到该 request_id 对应的模型
                cur = conn.execute(
                    "SELECT routed_model FROM request_logs WHERE prompt_hash=? ORDER BY id DESC LIMIT 1",
                    (request_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return
                model_name = row["routed_model"]
                cur = conn.execute(
                    "SELECT positive_feedback, negative_feedback FROM model_metrics WHERE model_name=?",
                    (model_name,),
                )
                mrow = cur.fetchone()
                pos = (mrow["positive_feedback"] if mrow else 0) + (
                    1 if sentiment == "positive" else 0
                )
                neg = (mrow["negative_feedback"] if mrow else 0) + (
                    1 if sentiment == "negative" else 0
                )
                total = pos + neg
                sat = pos / total if total else 0.9
                conn.execute(
                    "INSERT INTO model_metrics(model_name, satisfaction_rate, positive_feedback, negative_feedback) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(model_name) DO UPDATE SET "
                    "satisfaction_rate=excluded.satisfaction_rate, "
                    "positive_feedback=excluded.positive_feedback, "
                    "negative_feedback=excluded.negative_feedback",
                    (model_name, sat, pos, neg),
                )
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            import sys

            print(f"[Database] update satisfaction failed: {exc}", file=sys.stderr)

    def update_balance(self, model_name: str, balance: float) -> None:
        sql = (
            "INSERT INTO model_metrics(model_name, last_balance, last_sync_time) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(model_name) DO UPDATE SET "
            "last_balance=excluded.last_balance, last_sync_time=excluded.last_sync_time"
        )
        self._write_queue.put((sql, (model_name, balance, time.time())))

    # ------------------------------------------------------------------ #
    # 读接口（同步，仅用于面板）
    # ------------------------------------------------------------------ #
    def get_metrics(self, model_name: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM model_metrics WHERE model_name=?", (model_name,)
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def get_all_metrics(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM model_metrics")
            return [dict(r) for r in cur.fetchall()]

    def get_dashboard_stats(self, since: Optional[float] = None) -> Dict[str, Any]:
        with self._connect() as conn:
            # 构建 WHERE 子句：since 为 None 时不限制时间范围
            where = "WHERE timestamp>=?" if since is not None else ""
            params = (since,) if since is not None else ()

            # 今日拦截次数
            total = conn.execute(
                f"SELECT COUNT(*) AS c FROM request_logs {where}", params
            ).fetchone()["c"]
            # 累计花费（按货币分组，避免不同货币直接相加）
            saved_by_currency = conn.execute(
                f"SELECT cost_currency, COALESCE(SUM(cost),0) AS s FROM request_logs {where} GROUP BY cost_currency",
                params
            ).fetchall()
            # 平均延迟
            avg_lat = conn.execute(
                f"SELECT COALESCE(AVG(latency_ms),0) AS a FROM request_logs {where}", params
            ).fetchone()["a"]
            # Token 统计
            token_stats = conn.execute(
                f"SELECT COALESCE(SUM(prompt_tokens),0) AS total_input, "
                f"COALESCE(SUM(completion_tokens),0) AS total_output "
                f"FROM request_logs {where}", params
            ).fetchone()
            # 各模型调用占比
            model_dist = conn.execute(
                f"SELECT routed_model AS name, COUNT(*) AS count "
                f"FROM request_logs {where} GROUP BY routed_model", params
            ).fetchall()
            # 每日请求量与成本趋势
            daily = conn.execute(
                f"SELECT date(timestamp,'unixepoch') AS day, "
                f"COUNT(*) AS req, COALESCE(SUM(cost),0) AS cost, "
                f"COALESCE(SUM(prompt_tokens),0) AS input_tokens, "
                f"COALESCE(SUM(completion_tokens),0) AS output_tokens "
                f"FROM request_logs {where} GROUP BY day ORDER BY day", params
            ).fetchall()
            # 反馈统计
            fb_where = "WHERE timestamp>=?" if since is not None else ""
            fb_params = (since,) if since is not None else ()
            fb = conn.execute(
                f"SELECT sentiment, COUNT(*) AS c FROM feedback_records {fb_where} "
                f"GROUP BY sentiment", fb_params
            ).fetchall()
            # 任务类型分布
            task_where = "WHERE task_type IS NOT NULL" if since is None else "WHERE timestamp>=? AND task_type IS NOT NULL"
            task_params = (since,) if since is not None else ()
            task_dist = conn.execute(
                f"SELECT task_type, COUNT(*) AS count FROM request_logs "
                f"{task_where} GROUP BY task_type", task_params
            ).fetchall()
            # 任务类型统计详情
            task_stats = conn.execute("SELECT * FROM task_type_stats").fetchall()
            # 各模型 token 和花费统计
            model_token_stats = conn.execute(
                f"SELECT routed_model AS model_name, "
                f"COUNT(*) AS total_calls, "
                f"COALESCE(SUM(prompt_tokens),0) AS total_input_tokens, "
                f"COALESCE(SUM(completion_tokens),0) AS total_output_tokens, "
                f"COALESCE(SUM(cost),0) AS total_cost, "
                f"cost_currency "
                f"FROM request_logs {where} "
                f"GROUP BY routed_model, cost_currency", params
            ).fetchall()
        return {
            "total_interceptions": total,
            "saved_cost_by_currency": [dict(r) for r in saved_by_currency],
            "avg_latency_ms": round(float(avg_lat), 2),
            "total_input_tokens": int(token_stats["total_input"] or 0),
            "total_output_tokens": int(token_stats["total_output"] or 0),
            "model_distribution": [dict(r) for r in model_dist],
            "daily_trend": [dict(r) for r in daily],
            "feedback_summary": {r["sentiment"]: r["c"] for r in fb},
            "task_type_distribution": [dict(r) for r in task_dist],
            "task_type_stats": [dict(r) for r in task_stats],
            "model_token_stats": [dict(r) for r in model_token_stats],
        }

    def get_negative_feedback_conversations(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT f.id, f.request_id, f.context_snapshot, f.timestamp, r.routed_model "
                "FROM feedback_records f LEFT JOIN request_logs r "
                "ON r.prompt_hash=f.request_id "
                "WHERE f.sentiment='negative' ORDER BY f.timestamp DESC LIMIT ?",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_task_type_stats(self) -> List[Dict[str, Any]]:
        """获取所有任务类型的统计。"""
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM task_type_stats")
            return [dict(r) for r in cur.fetchall()]

    def get_recent_task_types(self, limit: int = 100) -> List[Dict[str, Any]]:
        """获取最近的请求及其 task_type。"""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT task_type, routed_model, predicted_difficulty, actual_difficulty, "
                "cost, latency_ms, success, timestamp "
                "FROM request_logs WHERE task_type IS NOT NULL "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------ #
    # 训练集 CRUD
    # ------------------------------------------------------------------ #
    def get_training_samples(
        self,
        source: Optional[str] = None,
        task_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """获取训练样本列表，支持按来源和任务类型过滤。"""
        with self._connect() as conn:
            conditions = []
            params: list = []
            if source:
                conditions.append("source=?")
                params.append(source)
            if task_type:
                conditions.append("task_type=?")
                params.append(task_type)
            where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
            cur = conn.execute(
                f"SELECT * FROM training_samples{where} ORDER BY id DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            )
            return [dict(r) for r in cur.fetchall()]

    def get_training_sample(self, sample_id: int) -> Optional[Dict[str, Any]]:
        """获取单个训练样本。"""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM training_samples WHERE id=?", (sample_id,)
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def add_training_sample(
        self,
        prompt: str,
        difficulty: int,
        est_tokens: int = 500,
        task_type: Optional[str] = None,
        model_name: Optional[str] = None,
        source: str = "manual",
        new_mark_ttl: float = 3600,
    ) -> int:
        """添加训练样本，返回插入的 id。新增样本默认标记为 is_new=1。"""
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO training_samples (prompt, difficulty, est_tokens, task_type, model_name, source, is_new, new_mark_ttl, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (prompt, difficulty, est_tokens, task_type, model_name, source, new_mark_ttl, now, now),
            )
            conn.commit()
            sample_id = cur.lastrowid
        # 滚动保存：检查容量限制
        self._enforce_sample_capacity()
        return sample_id

    def _enforce_sample_capacity(self) -> None:
        """对非自动新增样本执行滚动保存，超出容量时删除最旧的。

        规则：
        - 只对 source != 'auto' 的样本执行容量控制（手动/批量导入的样本）
        - auto 样本不纳入容量控制，由日志保留策略管理
        - max_capacity <= 0 表示无上限
        """
        try:
            from .config import config
            max_capacity = config.sample_max_capacity
            if max_capacity <= 0:
                return  # 无上限
            with self._connect() as conn:
                # 统计非 auto 样本数量
                cur = conn.execute(
                    "SELECT COUNT(*) AS c FROM training_samples WHERE source != 'auto'"
                )
                count = cur.fetchone()["c"]
                if count > max_capacity:
                    # 删除最旧的非 auto 样本，保留到 max_capacity
                    delete_count = count - max_capacity
                    conn.execute(
                        "DELETE FROM training_samples WHERE id IN ("
                        "  SELECT id FROM training_samples WHERE source != 'auto' "
                        "  ORDER BY created_at ASC LIMIT ?"
                        ")",
                        (delete_count,),
                    )
                    conn.commit()
        except Exception as exc:  # noqa: BLE001
            import sys
            print(f"[Database] enforce sample capacity failed: {exc}", file=sys.stderr)

    def update_training_sample(
        self,
        sample_id: int,
        prompt: Optional[str] = None,
        difficulty: Optional[int] = None,
        est_tokens: Optional[int] = None,
        task_type: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> bool:
        """更新训练样本，只更新非 None 的字段。"""
        sets: List[str] = []
        params: list = []
        if prompt is not None:
            sets.append("prompt=?")
            params.append(prompt)
        if difficulty is not None:
            sets.append("difficulty=?")
            params.append(difficulty)
        if est_tokens is not None:
            sets.append("est_tokens=?")
            params.append(est_tokens)
        if task_type is not None:
            sets.append("task_type=?")
            params.append(task_type)
        if model_name is not None:
            sets.append("model_name=?")
            params.append(model_name)
        if not sets:
            return False
        sets.append("updated_at=?")
        params.append(time.time())
        params.append(sample_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE training_samples SET {', '.join(sets)} WHERE id=?",
                params,
            )
            conn.commit()
            return True

    def delete_training_sample(self, sample_id: int) -> bool:
        """删除训练样本。"""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM training_samples WHERE id=?", (sample_id,))
            conn.commit()
            return cur.rowcount > 0

    def count_training_samples(self, source: Optional[str] = None) -> int:
        """统计训练样本数量。"""
        with self._connect() as conn:
            if source:
                cur = conn.execute(
                    "SELECT COUNT(*) AS c FROM training_samples WHERE source=?",
                    (source,),
                )
            else:
                cur = conn.execute("SELECT COUNT(*) AS c FROM training_samples")
            return cur.fetchone()["c"]

    def get_training_sample_sources(self) -> List[Dict[str, Any]]:
        """获取训练样本按来源分组的统计。"""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT source, COUNT(*) AS count FROM training_samples GROUP BY source"
            )
            return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------ #
    # 训练样本新增标记
    # ------------------------------------------------------------------ #
    def clear_expired_new_marks(self) -> int:
        """清除过期的训练样本新增标记，返回清除数量。"""
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE training_samples SET is_new=0 WHERE is_new=1 AND created_at IS NOT NULL AND (created_at + new_mark_ttl) < ?",
                (now,),
            )
            conn.commit()
            return cur.rowcount

    # ------------------------------------------------------------------ #
    # 路由日志管理
    # ------------------------------------------------------------------ #
    def get_route_logs(
        self,
        limit: int = 100,
        offset: int = 0,
        model: Optional[str] = None,
        route_source: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """获取路由日志列表。"""
        with self._connect() as conn:
            conditions = []
            params: list = []
            if model:
                conditions.append("routed_model=?")
                params.append(model)
            if route_source:
                conditions.append("route_source=?")
                params.append(route_source)
            where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
            cur = conn.execute(
                f"SELECT * FROM request_logs{where} ORDER BY id DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            )
            return [dict(r) for r in cur.fetchall()]

    def count_route_logs(self, model: Optional[str] = None) -> int:
        """统计路由日志数量。"""
        with self._connect() as conn:
            if model:
                cur = conn.execute(
                    "SELECT COUNT(*) AS c FROM request_logs WHERE routed_model=?",
                    (model,),
                )
            else:
                cur = conn.execute("SELECT COUNT(*) AS c FROM request_logs")
            return cur.fetchone()["c"]

    def clear_old_logs(self, max_age_days: int = 30) -> int:
        """清除超过指定天数的日志，返回删除数量。0 表示不自动清除。"""
        if max_age_days <= 0:
            return 0
        cutoff = time.time() - (max_age_days * 86400)
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM request_logs WHERE timestamp < ?", (cutoff,)
            )
            conn.commit()
            return cur.rowcount

    def clear_all_logs(self) -> int:
        """清除所有日志，返回删除数量。"""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM request_logs")
            conn.commit()
            return cur.rowcount

    def get_route_log_stats(self) -> Dict[str, Any]:
        """获取路由日志统计信息。"""
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) AS c FROM request_logs").fetchone()["c"]
            # 按路由来源统计
            source_stats = conn.execute(
                "SELECT route_source, COUNT(*) AS count FROM request_logs WHERE route_source IS NOT NULL GROUP BY route_source"
            ).fetchall()
            # 按模型统计
            model_stats = conn.execute(
                "SELECT routed_model, COUNT(*) AS count, AVG(latency_ms) AS avg_latency, AVG(cost) AS avg_cost FROM request_logs GROUP BY routed_model ORDER BY count DESC"
            ).fetchall()
            # 最早和最晚日志时间
            time_range = conn.execute(
                "SELECT MIN(timestamp) AS earliest, MAX(timestamp) AS latest FROM request_logs"
            ).fetchone()
        return {
            "total_logs": total,
            "source_distribution": [dict(r) for r in source_stats],
            "model_distribution": [dict(r) for r in model_stats],
            "earliest_log": time_range["earliest"],
            "latest_log": time_range["latest"],
        }


# 全局单例
db = Database.get_instance()
