"""``/api/v1/health/``, ``/api/v1/health/live``, ``/api/v1/health/ready``.

v0.1.0 retires ``GET /api/v1/info/``, ``GET /api/v1/info/ping`` and ``GET /api/v1/stats/``:
identity, liveness, readiness and counters are consolidated into the kernel
``OpalHealthReport`` returned by ``GET /api/v1/health/``, with the former Stats payload
served under ``details.stats`` (module ``x-opal-health-details`` declaration)."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from opal_event.auth import require_auth
from opal_event.deps import get_config, get_service
from opal_event.models import HealthReport, OpalHealthProbe, Stats, _utcnow
from opal_event.service import EventService

logger = logging.getLogger("opal_event.routers.operations")

SERVICE_NAME = "opal-event-svc"
SERVICE_VERSION = "0.1.0"
API_VERSION = "v1"

router = APIRouter()


def _stats(service: EventService) -> Stats:
    snapshot = service.metrics.snapshot()
    return Stats(
        observed_events=snapshot["observed_events"],
        invalid_events=snapshot["invalid_events"],
        matched_events=snapshot["matched_events"],
        executions_completed=snapshot["executions_completed"],
        executions_failed=snapshot["executions_failed"],
        executions_dropped=snapshot["executions_dropped"],
        queue_depth=service.executor.queue_depth,
        active_workers=snapshot["active_workers"],
        rejected_publications=snapshot.get("rejected_publications", 0),
        chain_depth_rejections=snapshot.get("chain_depth_rejections", 0),
        fanout_dropped=snapshot.get("fanout_dropped", 0),
    )


@router.get("/api/v1/health/", response_model=HealthReport, dependencies=[Depends(require_auth)])
def get_health(service: Annotated[EventService, Depends(get_service)], config=Depends(get_config)):
    broker_running = service.broker.running
    database_ready = service.is_database_ready()
    executor_healthy = service.executor.healthy
    reload_ok = not config.reload_failed
    ready = service.is_ready()
    status = "ok" if (broker_running and database_ready and executor_healthy and reload_ok) else "degraded"
    if status == "degraded":
        logger.warning(
            "health degraded: broker_running=%s database_ready=%s executor_healthy=%s config_reload_ok=%s",
            broker_running,
            database_ready,
            executor_healthy,
            reload_ok,
        )
    pending_fields = config.pending_restart_fields
    active = next((p for p in config.settings.profiles if p.is_active), None)
    return HealthReport(
        name=SERVICE_NAME,
        version=SERVICE_VERSION,
        api_version=API_VERSION,
        status=status,
        live=True,
        ready=ready,
        details={
            "broker_running": broker_running,
            "database_ready": database_ready,
            "executor_healthy": executor_healthy,
            "stats": _stats(service).model_dump(mode="json"),
        },
        active_profile_id=str(active.id) if active is not None else None,
        checked_at=_utcnow(),
        uptime_seconds=service.uptime_seconds,
        pending_restart=bool(pending_fields),
        pending_restart_fields=pending_fields,
    )


@router.get("/api/v1/health/live", response_model=OpalHealthProbe)
def health_live(service: Annotated[EventService, Depends(get_service)]):
    return OpalHealthProbe(status="ok", ready=service.is_ready())


@router.get("/api/v1/health/ready", response_model=OpalHealthProbe)
def health_ready(service: Annotated[EventService, Depends(get_service)], response: Response):
    # One authoritative readiness check shared with REST publication — a 503 here means
    # POST /api/v1/event/ would also be refused, and vice versa. Per openapi/opal_event_v0.1.0.yaml the
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
