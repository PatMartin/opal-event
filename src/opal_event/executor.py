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
        self._queue_size = queue_size
        self._queue: queue.Queue[Job] = queue.Queue()
        self._workers: list[threading.Thread] = []
        self._worker_events: dict[threading.Thread, threading.Event] = {}
        self._retired_workers: list[threading.Thread] = []
        self._worker_counter = 0
        self._max_workers = max_workers
        self._started = False
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def _start_worker(self) -> threading.Thread:
        self._worker_counter += 1
        stop_event = threading.Event()
        t = threading.Thread(
            target=self._worker_loop,
            args=(stop_event,),
            name=f"opal-event-worker-{self._worker_counter}",
            daemon=True,
        )
        self._workers.append(t)
        self._worker_events[t] = stop_event
        t.start()
        return t

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._stop.clear()
            for _ in range(self._max_workers):
                self._start_worker()
            self._started = True

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            all_threads = list(self._workers) + list(self._retired_workers)
        for t in all_threads:
            t.join(timeout=5)
        with self._lock:
            self._workers.clear()
            self._worker_events.clear()
            self._retired_workers.clear()
            self._started = False

    def resize(self, queue_size: int, max_workers: int) -> None:
        """Applies live ``execution.queue_size``/``execution.max_workers`` changes. Synchronized
        admission control governs queue capacity on the stable worker queue, and the worker pool
        adjusts dynamically without restarting existing workers."""
        workers_to_retire: list[threading.Event] = []
        with self._lock:
            self._queue_size = queue_size
            self._max_workers = max_workers
            if self._started:
                self._retired_workers = [w for w in self._retired_workers if w.is_alive()]
                current_count = len(self._workers)
                if max_workers > current_count:
                    for _ in range(max_workers - current_count):
                        self._start_worker()
                elif max_workers < current_count:
                    surplus = current_count - max_workers
                    for _ in range(surplus):
                        worker = self._workers.pop()
                        stop_event = self._worker_events.pop(worker, None)
                        if stop_event is not None:
                            workers_to_retire.append(stop_event)
                        self._retired_workers.append(worker)

        for stop_event in workers_to_retire:
            stop_event.set()

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def queue_size(self) -> int:
        with self._lock:
            return self._queue_size

    @property
    def max_workers(self) -> int:
        with self._lock:
            return self._max_workers

    @property
    def worker_count(self) -> int:
        with self._lock:
            return len(self._workers)

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
        with self._lock:
            if self._queue.qsize() >= self._queue_size:
                admitted = False
            else:
                self._queue.put_nowait(job)
                admitted = True

        if not admitted:
            self.db.update_execution(
                execution_id,
                status="dropped",
                error_code="queue_full",
                error_message="bounded execution queue is full",
            )
            record.status = "dropped"
            record.error_code = "queue_full"
            record.error_message = "bounded execution queue is full"
            self.metrics.incr("executions_dropped")
            return record

        return record

    def _worker_loop(self, stop_event: threading.Event) -> None:
        while not self._stop.is_set() and not stop_event.is_set():
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
        try:
            outcome = run_action(job.action, job.event, job.execution_settings, job.security_settings)
        except Exception as exc:
            logger.exception("unexpected exception during execution of action %s", job.action.id)
            duration_ms = int((time.monotonic() - start) * 1000)
            finished_at = _now()
            self.db.update_execution(
                job.execution_id,
                status="failed",
                finished_at=finished_at,
                duration_ms=duration_ms,
                error_code="unexpected_error",
                error_message=str(exc),
            )
            self.metrics.incr("executions_failed")
            return

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
