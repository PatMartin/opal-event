"""``/api/v1/service``: managed server-process lifecycle (v1.7.1 kernel ``service`` tag).

All three routes delegate to :class:`opal_event.lifecycle.ServiceProcessManager`. When the
pidfile-tracked process *is* this serving process, start/stop report the status and then
self-terminate after the response is sent — ``start`` on self is therefore a restart.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Query

from opal_event.auth import require_auth
from opal_event.deps import get_config
from opal_event.errors import NotFoundError
from opal_event.lifecycle import ServiceProcessManager
from opal_event.models import ServiceLogTail, ServiceStartOptions, ServiceStatus

router = APIRouter(dependencies=[Depends(require_auth)])


def _manager(config) -> ServiceProcessManager:
    return ServiceProcessManager(config)


@router.post("/api/v1/service/start", response_model=ServiceStatus)
def service_start(
    background_tasks: BackgroundTasks,
    config=Depends(get_config),
    body: Annotated[ServiceStartOptions | None, Body()] = None,
):
    manager = _manager(config)
    status, terminate_self = manager.start(append=body.append if body else False)
    if terminate_self:
        background_tasks.add_task(manager.terminate_self)
    return status


@router.post("/api/v1/service/stop", response_model=ServiceStatus)
def service_stop(background_tasks: BackgroundTasks, config=Depends(get_config)):
    manager = _manager(config)
    status, terminate_self = manager.stop()
    if terminate_self:
        background_tasks.add_task(manager.terminate_self)
    return status


@router.get("/api/v1/service/log", response_model=ServiceLogTail)
def service_log(config=Depends(get_config), lines: Annotated[int, Query(ge=1, le=1000)] = 10):
    manager = _manager(config)
    tail = manager.tail_log(lines)
    if tail is None:
        raise NotFoundError(f"no service log at {manager.log_file}")
    return tail
