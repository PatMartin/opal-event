"""SQLite persistence for observed events and action execution history.

Two tables: ``events`` (subject to ``persistence.event_mode``) and ``executions`` (always recorded).
Both support keyset ("cursor") pagination ordered newest-first, matching the REST list endpoints.
Retention cleanup deletes rows older than ``persistence.retention_days`` for both tables.
"""

from __future__ import annotations

import base64
import json
import logging
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("opal_event.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    topic TEXT NOT NULL,
    source TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    ts_us INTEGER,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_topic ON events(topic);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_ts_us ON events(ts_us);

CREATE TABLE IF NOT EXISTS executions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    event_id TEXT,
    rule_id TEXT,
    action_id TEXT NOT NULL,
    status TEXT NOT NULL,
    trigger_kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_us INTEGER,
    started_at TEXT,
    finished_at TEXT,
    duration_ms INTEGER,
    exit_code INTEGER,
    http_status INTEGER,
    stdout TEXT,
    stderr TEXT,
    result TEXT,
    error_code TEXT,
    error_message TEXT,
    action_revision TEXT
);
CREATE INDEX IF NOT EXISTS idx_executions_status ON executions(status);
CREATE INDEX IF NOT EXISTS idx_executions_rule ON executions(rule_id);
CREATE INDEX IF NOT EXISTS idx_executions_action ON executions(action_id);
CREATE INDEX IF NOT EXISTS idx_executions_event ON executions(event_id);
CREATE INDEX IF NOT EXISTS idx_executions_created_us ON executions(created_us);
"""


def _ts_to_us(value: str) -> int | None:
    """Parses an RFC3339 timestamp into integer UTC microseconds since the Unix epoch.
    Accepts ``Z``, ``±HH:MM`` offsets, variable-precision fractions, and legacy naive values
    (interpreted as UTC). Returns ``None`` for unparseable input."""
    try:
        dt = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1_000_000)


def _encode_cursor(seq: int) -> str:
    return base64.urlsafe_b64encode(str(seq).encode("ascii")).decode("ascii")


def _decode_cursor(cursor: str) -> int:
    try:
        return int(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("ascii"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid cursor") from exc


class Database:
    """Thread-safe SQLite access. Uses one connection guarded by a lock (SQLite is fine for this
    write volume; a bounded worker pool and a single REST process are the only writers)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """Idempotent schema migration: adds the canonical ``ts_us``/``created_us`` integer
        microsecond columns (already in fresh SCHEMA) and backfills them from the stored
        RFC3339 strings. Rows with unparseable timestamps stay NULL — never silently deleted
        (retention and time filters simply do not match them)."""
        for table, col, text_col in (("events", "ts_us", "timestamp"), ("executions", "created_us", "created_at")):
            cols = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} INTEGER")
                self._conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_{col} ON {table}({col})")
            skipped = 0
            for row in self._conn.execute(f"SELECT seq, {text_col} FROM {table} WHERE {col} IS NULL").fetchall():
                us = _ts_to_us(row[text_col])
                if us is None:
                    skipped += 1
                    continue
                self._conn.execute(f"UPDATE {table} SET {col} = ? WHERE seq = ?", (us, row["seq"]))
            if skipped:
                logger.warning("migration left %d unparseable timestamp(s) NULL in %s", skipped, table)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def ping(self) -> None:
        """Constant-cost connectivity probe for readiness checks: a bare ``SELECT 1`` — no table
        scan, no event-history work. Raises if the connection is closed or the DB is unreachable."""
        with self._lock:
            self._conn.execute("SELECT 1").fetchone()

    # -- events --------------------------------------------------------------------

    def insert_event(self, event: dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO events (id, topic, source, timestamp, ts_us, data) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event["id"],
                    event["topic"],
                    event["source"],
                    event["timestamp"],
                    _ts_to_us(event["timestamp"]),
                    json.dumps(event["data"]),
                ),
            )

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return self._row_to_event(row) if row else None

    def list_events(
        self,
        cursor: str | None = None,
        limit: int = 50,
        topic: str | None = None,
        source: str | None = None,
        time_from: str | None = None,
        time_to: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        clauses = []
        params: list[Any] = []
        if cursor:
            clauses.append("seq < ?")
            params.append(_decode_cursor(cursor))
        if topic:
            clauses.append("topic = ?")
            params.append(topic)
        if source:
            clauses.append("source = ?")
            params.append(source)
        if time_from:
            us = _ts_to_us(time_from)
            if us is not None:
                clauses.append("ts_us >= ?")
                params.append(us)
        if time_to:
            us = _ts_to_us(time_to)
            if us is not None:
                clauses.append("ts_us <= ?")
                params.append(us)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM events {where} ORDER BY seq DESC LIMIT ?"
        params.append(limit + 1)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return self._paginate(rows, limit, self._row_to_event)

    def list_topics(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute("SELECT DISTINCT topic FROM events ORDER BY topic ASC").fetchall()
        return [row["topic"] for row in rows]

    def clear_events(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM events")

    def delete_event(self, event_id: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
            return cursor.rowcount > 0

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "topic": row["topic"],
            "source": row["source"],
            "timestamp": row["timestamp"],
            "data": json.loads(row["data"]),
        }

    # -- executions ------------------------------------------------------------------

    def insert_execution(self, execution: dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO executions
                (id, event_id, rule_id, action_id, status, trigger_kind, created_at, created_us, started_at, finished_at,
                 duration_ms, exit_code, http_status, stdout, stderr, result, error_code, error_message, action_revision)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    execution["id"],
                    execution.get("event_id"),
                    execution.get("rule_id"),
                    execution["action_id"],
                    execution["status"],
                    execution["trigger"],
                    execution["created_at"],
                    _ts_to_us(execution["created_at"]),
                    execution.get("started_at"),
                    execution.get("finished_at"),
                    execution.get("duration_ms"),
                    execution.get("exit_code"),
                    execution.get("http_status"),
                    execution.get("stdout"),
                    execution.get("stderr"),
                    json.dumps(execution.get("result")) if "result" in execution else None,
                    execution.get("error_code"),
                    execution.get("error_message"),
                    execution.get("action_revision"),
                ),
            )

    def update_execution(self, execution_id: str, **fields: Any) -> None:
        if not fields:
            return
        columns = {"trigger": "trigger_kind"}
        set_clause = ", ".join(f"{columns.get(k, k)} = ?" for k in fields)
        values = [json.dumps(v) if k == "result" else v for k, v in fields.items()]
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE executions SET {set_clause} WHERE id = ?", (*values, execution_id))

    def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM executions WHERE id = ?", (execution_id,)).fetchone()
        return self._row_to_execution(row) if row else None

    def list_executions(
        self,
        cursor: str | None = None,
        limit: int = 50,
        status: str | None = None,
        rule_id: str | None = None,
        action_id: str | None = None,
        event_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        clauses = []
        params: list[Any] = []
        if cursor:
            clauses.append("seq < ?")
            params.append(_decode_cursor(cursor))
        if status:
            clauses.append("status = ?")
            params.append(status)
        if rule_id:
            clauses.append("rule_id = ?")
            params.append(rule_id)
        if action_id:
            clauses.append("action_id = ?")
            params.append(action_id)
        if event_id:
            clauses.append("event_id = ?")
            params.append(event_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM executions {where} ORDER BY seq DESC LIMIT ?"
        params.append(limit + 1)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return self._paginate(rows, limit, self._row_to_execution)

    @staticmethod
    def _row_to_execution(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "event_id": row["event_id"],
            "rule_id": row["rule_id"],
            "action_id": row["action_id"],
            "status": row["status"],
            "trigger": row["trigger_kind"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "duration_ms": row["duration_ms"],
            "exit_code": row["exit_code"],
            "http_status": row["http_status"],
            "stdout": row["stdout"],
            "stderr": row["stderr"],
            "result": json.loads(row["result"]) if row["result"] is not None else None,
            "error_code": row["error_code"],
            "error_message": row["error_message"],
            "action_revision": row["action_revision"],
        }

    # -- shared helpers --------------------------------------------------------------

    @staticmethod
    def _paginate(rows: list[sqlite3.Row], limit: int, to_dict) -> tuple[list[dict[str, Any]], str | None]:
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = _encode_cursor(page[-1]["seq"]) if has_more and page else None
        return [to_dict(row) for row in page], next_cursor

    def reconcile_interrupted_executions(self) -> int:
        """Closes out executions left ``queued``/``running`` by an abrupt shutdown.

        ``queued`` -> ``dropped`` (the job never ran and its queue is gone) and
        ``running`` -> ``failed`` (the job was in flight and will never report a result). Both get
        ``error_code='service_restart'`` and a ``finished_at`` timestamp; every other column —
        created_at, started_at, action_revision, trigger, linkage — is preserved untouched.
        Idempotent: only non-terminal rows match, so a second call is a no-op. Never reruns or
        retries the interrupted actions. Returns the number of rows reconciled.
        """
        now = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"  # same ISO-8601 Z format the executor writes
        with self._lock, self._conn:
            queued = self._conn.execute(
                f"""UPDATE executions
                    SET status='dropped', finished_at={now},
                        error_code='service_restart',
                        error_message='service restarted before the job started'
                    WHERE status='queued'"""
            ).rowcount
            running = self._conn.execute(
                f"""UPDATE executions
                    SET status='failed', finished_at={now},
                        error_code='service_restart',
                        error_message='service restarted while the job was running'
                    WHERE status='running'"""
            ).rowcount
            return queued + running

    # -- retention --------------------------------------------------------------------

    def cleanup_retention(self, retention_days: int) -> int:
        """Deletes events/executions strictly older than ``retention_days``; returns rows removed.

        Comparison is integer UTC microseconds — exact at microsecond precision, indexable, and
        correct across ``Z``/offset/fraction formats (``ts_us``/``created_us`` are canonicalized
        at insert/migration time). Rows exactly on the cutoff are kept (strict ``<``); rows with
        unparseable legacy timestamps have NULL canonical columns and are never deleted.
        """
        cutoff_us = int((datetime.now(UTC) - timedelta(days=retention_days)).timestamp() * 1_000_000)
        with self._lock, self._conn:
            events = self._conn.execute("DELETE FROM events WHERE ts_us < ?", (cutoff_us,)).rowcount
            executions = self._conn.execute("DELETE FROM executions WHERE created_us < ?", (cutoff_us,)).rowcount
            return events + executions
