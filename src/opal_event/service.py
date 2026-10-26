"""Wires configuration, persistence, the rule engine, the executor pool, and the ZeroMQ broker
into one cohesive runtime object (``EventService``), owned by the FastAPI app's lifespan.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from opal_event.config import ConfigStore
from opal_event.db import Database
from opal_event.executor import Executor
from opal_event.metrics import Metrics
from opal_event.models import Action, Settings
from opal_event.rules import evaluate_rule
from opal_event.zeromq_broker import Broker

logger = logging.getLogger("opal_event.service")

RETENTION_INTERVAL_SECONDS = 3600


class EventService:
    def __init__(self, config: ConfigStore):
        self.config = config
        self.metrics = Metrics()
        self.db = Database(config.settings.persistence.database)
        self.executor = Executor(
            self.db,
            self.metrics,
            queue_size=config.settings.execution.queue_size,
            max_workers=config.settings.execution.max_workers,
        )
        self.broker = Broker(
            config.settings.zeromq,
            on_event=self._handle_observed_event,
            on_reject=lambda: self.metrics.incr("rejected_publications"),
        )
        self._retention_stop = threading.Event()
        self._retention_thread: threading.Thread | None = None
        config.set_reload_callback(self._on_config_reload)

    def start(self) -> None:
        # Reconcile executions interrupted by a previous abrupt shutdown before accepting any new
        # work (queued -> dropped, running -> failed, both error_code=service_restart).
        reconciled = self.db.reconcile_interrupted_executions()
        if reconciled:
            logger.info("reconciled %d interrupted execution(s) left by previous run", reconciled)
        try:
            removed = self.db.cleanup_retention(self.config.settings.persistence.retention_days)
            if removed:
                logger.info("startup retention cleanup removed %d row(s)", removed)
        except Exception:
            logger.exception("startup retention cleanup failed")
        self.executor.start()
        self.broker.start()
        self._retention_thread = threading.Thread(target=self._retention_loop, daemon=True, name="opal-event-retention")
        self._retention_thread.start()

    def stop(self) -> None:
        self._retention_stop.set()
        if self._retention_thread is not None:
            self._retention_thread.join(timeout=2)
        self.broker.stop()
        # Order matters: executor.stop() blocks until every worker has finished writing its final
        # execution record, so closing SQLite is only safe after it returns.
        self.executor.stop()
        self.db.close()

    # -- observed events (both direct ZeroMQ producers and REST-ingress, uniformly) ----------------

    def _handle_observed_event(self, event: dict[str, Any]) -> None:
        if event.get("_invalid"):
            self.metrics.incr("invalid_events")
            logger.warning("invalid event dropped by observer: %s", event.get("error"))
            return
        settings = self.config.settings
        self.metrics.incr("observed_events")

        actions_by_id: dict[str, Action] = {a.id: a for a in settings.actions}
        matched_any = False
        for rule in settings.rules:
            if not rule.enabled:
                continue
            matched, _checks = evaluate_rule(rule, event)
            if not matched:
                continue
            matched_any = True
            for action_id in rule.actions:
                action = actions_by_id.get(action_id)
                if action is None or not action.enabled:
                    logger.warning("rule %s references missing/disabled action %s", rule.id, action_id)
                    continue
                self.executor.submit(
                    action,
                    event,
                    event_id=event.get("id"),
                    rule_id=rule.id,
                    trigger="rule",
                    execution_settings=settings.execution,
                    security_settings=settings.security,
                )

        if matched_any:
            self.metrics.incr("matched_events")

        mode = settings.persistence.event_mode
        if mode == "all" or (mode == "matched" and matched_any):
            self.db.insert_event(event)

    # -- publish entrypoint used by REST POST /api/v1/events/ --------------------------------------

    def publish(self, envelope: dict[str, Any]) -> None:
        self.broker.publish(envelope["topic"], envelope)

    # -- live config reload side-effects -----------------------------------------------------------

    def _on_config_reload(self, old: Settings, new: Settings) -> None:
        if old.execution.queue_size != new.execution.queue_size or old.execution.max_workers != new.execution.max_workers:
            self.executor.resize(new.execution.queue_size, new.execution.max_workers)
        if old.zeromq.max_event_bytes != new.zeromq.max_event_bytes:
            self.broker.update_max_event_bytes(new.zeromq.max_event_bytes)
        if old.zeromq.publisher_queue_size != new.zeromq.publisher_queue_size:
            self.broker.update_publisher_queue_size(new.zeromq.publisher_queue_size)

    # -- retention cleanup ---------------------------------------------------------------------------

    def _retention_loop(self) -> None:
        while not self._retention_stop.wait(timeout=RETENTION_INTERVAL_SECONDS):
            try:
                self.db.cleanup_retention(self.config.settings.persistence.retention_days)
            except Exception:
                logger.exception("retention cleanup failed")

    # -- readiness -------------------------------------------------------------------------------

    def is_ready(self) -> bool:
        return self.broker.running and self.executor.healthy

    async def watch_config_file(self) -> None:
        """Watches the YAML config file on disk and hot-reloads on change. Runs as an asyncio task
        for the lifetime of the app; invalid edits are logged and ignored, keeping last-good config
        active (see :meth:`ConfigStore.reload_from_disk`)."""
        import watchfiles

        async for _changes in watchfiles.awatch(str(self.config.path)):
            await asyncio.get_event_loop().run_in_executor(None, self.config.reload_from_disk)
