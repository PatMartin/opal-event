"""``/api/v1/health``, ``/api/v1/health/live``, ``/api/v1/health/ready``, ``/api/v1/stats``."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from opal_event.auth import require_auth
from opal_event.deps import get_config, get_service
from opal_event.errors import ServiceUnavailableError
from opal_event.models import Health, HealthProbe, Stats
from opal_event.service import EventService

router = APIRouter()


def _database_ready(service: EventService) -> bool:
    try:
        service.db.list_topics()
        return True
    except Exception:
        return False


@router.get("/api/v1/health/", response_model=Health, dependencies=[Depends(require_auth)])
def get_health(service: Annotated[EventService, Depends(get_service)], config=Depends(get_config)):
    broker_running = service.broker.running
    database_ready = _database_ready(service)
    status = "ok" if broker_running and database_ready else "degraded"
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


@router.get("/api/v1/health/live", response_model=HealthProbe)
def health_live():
    return HealthProbe(status="ok")


@router.get("/api/v1/health/ready", response_model=HealthProbe)
def health_ready(service: Annotated[EventService, Depends(get_service)]):
    broker_ok = service.broker.running
    db_ok = _database_ready(service)
    if not (broker_ok and db_ok):
        raise ServiceUnavailableError("broker or persistence subsystem is not ready")
    return HealthProbe(status="ok", details={"broker": "ok", "database": "ok"})
