"""``/api/v1/health``, ``/api/v1/health/live``, ``/api/v1/health/ready``, ``/api/v1/stats``."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from opal_event.auth import require_auth
from opal_event.deps import get_config, get_service
from opal_event.models import Health, OpalHealthProbe, Stats
from opal_event.service import EventService

logger = logging.getLogger("opal_event.routers.operations")

router = APIRouter()


@router.get("/api/v1/health/", response_model=Health, dependencies=[Depends(require_auth)])
def get_health(service: Annotated[EventService, Depends(get_service)], config=Depends(get_config)):
    broker_running = service.broker.running
    database_ready = service.is_database_ready()
    executor_healthy = service.executor.healthy
    reload_ok = not config.reload_failed
    status = "ok" if (broker_running and database_ready and executor_healthy and reload_ok) else "degraded"
    if status == "degraded":
        logger.warning(
            "health degraded: broker_running=%s database_ready=%s executor_healthy=%s config_reload_ok=%s",
            broker_running,
            database_ready,
            executor_healthy,
            reload_ok,
        )
    return Health(
        status=status,
        broker_running=broker_running,
        database_ready=database_ready,
        pending_restart=config.pending_restart,
    )


@router.get("/api/v1/stats/", response_model=Stats, dependencies=[Depends(require_auth)])
def get_stats(service: Annotated[EventService, Depends(get_service)]):
    snapshot = service.metrics.snapshot()
    return Stats(
        observed_events=snapshot["observed_events"],
        invalid_events=snapshot["invalid_events"],
        matched_events=snapshot["matched_events"],
        executions_succeeded=snapshot["executions_succeeded"],
        executions_failed=snapshot["executions_failed"],
        executions_dropped=snapshot["executions_dropped"],
        queue_depth=service.executor.queue_depth,
        active_workers=snapshot["active_workers"],
        rejected_publications=snapshot.get("rejected_publications", 0),
    )


@router.get("/api/v1/health/live", response_model=OpalHealthProbe)
def health_live(service: Annotated[EventService, Depends(get_service)]):
    return OpalHealthProbe(status="ok", ready=service.is_ready())


@router.get("/api/v1/health/ready", response_model=OpalHealthProbe)
def health_ready(service: Annotated[EventService, Depends(get_service)], response: Response):
    # One authoritative readiness check shared with REST publication — a 503 here means
    # POST /api/v1/events/ would also be refused, and vice versa. Per opal_event.1.4.0.yaml the
    # 503 body is the OpalHealthProbe itself (status "unavailable", ready false), not an error.
    if not service.is_ready():
        response.status_code = 503
        details = {
            "broker": "ok" if service.broker.running else "unavailable",
            "database": "ok" if service.is_database_ready() else "unavailable",
            "executor": "ok" if service.executor.healthy else "unavailable",
        }
        return OpalHealthProbe(status="unavailable", ready=False, details=details)
    return OpalHealthProbe(status="ok", ready=True, details={"broker": "ok", "database": "ok", "executor": "ok"})
