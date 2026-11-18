"""``/api/v1/execution``: read-only inspection of the action execution history."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from opal_event.auth import require_auth
from opal_event.deps import get_service
from opal_event.errors import NotFoundError
from opal_event.models import Execution, ExecutionList
from opal_event.service import EventService

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/api/v1/execution/", response_model=ExecutionList)
def list_executions(
    request: Request,
    service: Annotated[EventService, Depends(get_service)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    status: str | None = None,
    rule_id: str | None = None,
    action_id: str | None = None,
    event_id: str | None = None,
):
    # own_receipts_only principals see only executions they admitted (owner = their name).
    owner = None
    if (p := getattr(request.state, "principal", None)) is not None and p.own_receipts_only:
        owner = p.name
    items = service.db.list_executions(
        limit=limit, status=status, rule_id=rule_id, action_id=action_id, event_id=event_id, owner=owner
    )
    return [Execution.model_validate(item) for item in items]


@router.get("/api/v1/execution/{execution_id}", response_model=Execution)
def get_execution(execution_id: str, request: Request, service: Annotated[EventService, Depends(get_service)]):
    execution = service.db.get_execution(execution_id)
    if execution is None:
        raise NotFoundError(f"execution '{execution_id}' not found")
    p = getattr(request.state, "principal", None)
    if p is not None and p.own_receipts_only and execution.get("owner") != p.name:
        raise NotFoundError(f"execution '{execution_id}' not found")
    return Execution.model_validate(execution)
