"""``/api/v1/executions``: read-only inspection of the action execution history."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from opal_event.auth import require_auth
from opal_event.deps import get_service
from opal_event.errors import NotFoundError
from opal_event.models import Execution, ListExecutions
from opal_event.pagination import decode_scoped_cursor, encode_scoped_cursor
from opal_event.service import EventService

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/api/v1/executions/", response_model=ListExecutions)
def list_executions(
    service: Annotated[EventService, Depends(get_service)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    status: str | None = None,
    rule_id: str | None = None,
    action_id: str | None = None,
    event_id: str | None = None,
):
    filters = {"status": status, "rule_id": rule_id, "action_id": action_id, "event_id": event_id}
    inner_cursor = decode_scoped_cursor(cursor, filters) if cursor else None
    items, next_inner_cursor = service.db.list_executions(
        cursor=inner_cursor, limit=limit, status=status, rule_id=rule_id, action_id=action_id, event_id=event_id
    )
    next_cursor = encode_scoped_cursor(next_inner_cursor, filters) if next_inner_cursor else None
    return ListExecutions(
        items=[Execution.model_validate(item) for item in items], next_cursor=next_cursor, has_more=next_cursor is not None
    )


@router.get("/api/v1/executions/{execution_id}", response_model=Execution)
def get_execution(execution_id: str, service: Annotated[EventService, Depends(get_service)]):
    execution = service.db.get_execution(execution_id)
    if execution is None:
        raise NotFoundError(f"execution '{execution_id}' not found")
    return Execution.model_validate(execution)
