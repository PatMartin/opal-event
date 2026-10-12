"""Bounded, concurrent action execution pool.

Matched rule actions and manual executions are submitted here. A fixed-size worker pool (
``execution.max_workers``) pulls from a bounded queue (``execution.queue_size``); when the queue is
full, the job is recorded as ``dropped`` with ``error_code=queue_full`` and never retried. Workers
run independently with no ordering guarantee across jobs. Each job executes the *action snapshot* it
was queued with (``action_revision``), so concurrent action edits never change an in-flight job.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from opal_event.actions import run_action
from opal_event.db import Database
from opal_event.metrics import Metrics
from opal_event.models import Action, ExecutionSettings, SecuritySettings

logger = logging.getLogger("opal_event.executor")


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class Job:
    execution_id: str
    action: Action
    event: dict[str, Any]
    event_id: str | None
    rule_id: str | None
    trigger: str
    action_revision: str
    execution_settings: ExecutionSettings
    security_settings: SecuritySettings


@dataclass
class ExecutionRecord:
    """In-memory mirror of the row just inserted so callers get the receipt without a re-read."""

    id: str
    event_id: str | None
    rule_id: str | None
    action_id: str
    status: str
    trigger: str
    created_at: str
    action_revision: str
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    exit_code: int | None = None
    http_status: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    result: Any = None
    error_code: str | None = None
    error_message: str | None = None


class Executor:
    def __init__(self, db: Database, metrics: Metrics, queue_size: int, max_workers: int):
        self.db = db
        self.metrics = metrics
        self._queue: queue.Queue[Job] = queue.Queue(maxsize=queue_size)
        self._workers: list[threading.Thread] = []
        self._max_workers = max_workers
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        for i in range(self._max_workers):
            t = threading.Thread(target=self._worker_loop, name=f"opal-event-worker-{i}", daemon=True)
            t.start()
            self._workers.append(t)

    def stop(self) -> None:
        self._stop.set()
        for t in self._workers:
            t.join(timeout=5)

    def resize(self, queue_size: int, max_workers: int) -> None:
        """Applies live ``execution.queue_size``/``execution.max_workers`` changes. The queue itself
        cannot be resized in place, so in-flight items are preserved and a fresh bounded queue of
        the new size takes over for subsequent submissions."""
        with self._lock:
            old_queue = self._queue
            new_queue = queue.Queue(maxsize=queue_size)
            while True:
                try:
                    new_queue.put_nowait(old_queue.get_nowait())
                except queue.Empty:
                    break
            self._queue = new_queue
            if max_workers != self._max_workers:
                self.stop()
                self._stop = threading.Event()
                self._workers = []
                self._max_workers = max_workers
                self.start()

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    def submit(
        self,
        action: Action,
        event: dict[str, Any],
        *,
        event_id: str | None,
        rule_id: str | None,
        trigger: str,
        execution_settings: ExecutionSettings,
        security_settings: SecuritySettings,
    ) -> ExecutionRecord:
        execution_id = str(uuid.uuid4())
        created_at = _now()
        action_revision = sha256(action.model_dump_json().encode("utf-8")).hexdigest()[:16]
        job = Job(
            execution_id=execution_id,
            action=action,
            event=event,
            event_id=event_id,
            rule_id=rule_id,
            trigger=trigger,
            action_revision=action_revision,
            execution_settings=execution_settings,
            security_settings=security_settings,
        )
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            record = ExecutionRecord(
                id=execution_id,
                event_id=event_id,
                rule_id=rule_id,
                action_id=action.id,
                status="dropped",
                trigger=trigger,
                created_at=created_at,
                action_revision=action_revision,
                error_code="queue_full",
                error_message="bounded execution queue is full",
            )
            self.db.insert_execution(_record_to_dict(record))
            self.metrics.incr("executions_dropped")
            return record

        record = ExecutionRecord(
            id=execution_id,
            event_id=event_id,
            rule_id=rule_id,
            action_id=action.id,
            status="queued",
            trigger=trigger,
            created_at=created_at,
            action_revision=action_revision,
        )
        self.db.insert_execution(_record_to_dict(record))
        return record

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            self.metrics.incr("active_workers")
            try:
                self._run_job(job)
            finally:
                self.metrics.decr("active_workers")
                self._queue.task_done()

    def _run_job(self, job: Job) -> None:
        started_at = _now()
        self.db.update_execution(job.execution_id, status="running", started_at=started_at)
        start = time.monotonic()
        outcome = run_action(job.action, job.event, job.execution_settings, job.security_settings)
        duration_ms = int((time.monotonic() - start) * 1000)
        finished_at = _now()
        self.db.update_execution(
            job.execution_id,
            status=outcome.status,
            finished_at=finished_at,
            duration_ms=duration_ms,
            exit_code=outcome.exit_code,
            http_status=outcome.http_status,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            result=outcome.result,
            error_code=outcome.error_code,
            error_message=outcome.error_message,
        )
        if outcome.status == "succeeded":
            self.metrics.incr("executions_succeeded")
        else:
            self.metrics.incr("executions_failed")


def _record_to_dict(record: ExecutionRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "event_id": record.event_id,
        "rule_id": record.rule_id,
        "action_id": record.action_id,
        "status": record.status,
        "trigger": record.trigger,
        "created_at": record.created_at,
        "action_revision": record.action_revision,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "duration_ms": record.duration_ms,
        "exit_code": record.exit_code,
        "http_status": record.http_status,
        "stdout": record.stdout,
        "stderr": record.stderr,
        "result": record.result,
        "error_code": record.error_code,
        "error_message": record.error_message,
    }
