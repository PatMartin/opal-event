"""SQLite persistence for observed events and action execution history.

Two tables: ``events`` (subject to ``persistence.event_mode``) and ``executions`` (always recorded).
Both support keyset ("cursor") pagination ordered newest-first, matching the REST list endpoints.
Retention cleanup deletes rows older than ``persistence.retention_days`` for both tables.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    topic TEXT NOT NULL,
    source TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_topic ON events(topic);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);

CREATE TABLE IF NOT EXISTS executions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    event_id TEXT,
    rule_id TEXT,
    action_id TEXT NOT NULL,
    status TEXT NOT NULL,
    trigger_kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
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
"""


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

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- events --------------------------------------------------------------------

    def insert_event(self, event: dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO events (id, topic, source, timestamp, data) VALUES (?, ?, ?, ?, ?)",
                (event["id"], event["topic"], event["source"], event["timestamp"], json.dumps(event["data"])),
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
            clauses.append("timestamp >= ?")
            params.append(time_from)
        if time_to:
            clauses.append("timestamp <= ?")
            params.append(time_to)
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
                (id, event_id, rule_id, action_id, status, trigger_kind, created_at, started_at, finished_at,
                 duration_ms, exit_code, http_status, stdout, stderr, result, error_code, error_message, action_revision)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    execution["id"],
                    execution.get("event_id"),
                    execution.get("rule_id"),
                    execution["action_id"],
                    execution["status"],
                    execution["trigger"],
                    execution["created_at"],
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

        Stored timestamps are ISO-8601 ``YYYY-MM-DDTHH:MM:SS[.ffffff]Z`` while SQLite's
        ``datetime('now')`` yields space-separated ``YYYY-MM-DD HH:MM:SS`` — a raw string
        comparison misorders every same-date prefix ('T' > ' '). Both sides are therefore
        compared through ``julianday`` on space-normalized strings, giving a real UTC
        chronological comparison. Rows exactly on the cutoff are kept (strict ``<``).
        """
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).strftime("%Y-%m-%d %H:%M:%S.%f")
        with self._lock, self._conn:
            events = self._conn.execute(
                "DELETE FROM events WHERE julianday(replace(replace(timestamp,'T',' '),'Z','')) < julianday(?)",
                (cutoff,),
            ).rowcount
            executions = self._conn.execute(
                "DELETE FROM executions WHERE julianday(replace(replace(created_at,'T',' '),'Z','')) < julianday(?)",
                (cutoff,),
            ).rowcount
            return events + executions
