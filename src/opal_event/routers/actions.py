"""``/api/v1/actions``: CRUD over YAML-persisted action definitions plus manual execution.

Actions became a bounded configuration catalog in opal_event.1.2.0.yaml: the list endpoint returns
the complete collection (no cursor pagination) with a strong ETag, and PATCH/DELETE now require a
matching ``If-Match`` (428 if omitted, 412 if stale) instead of the previous unconditional writes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, Header, Response

from opal_event.auth import require_auth
from opal_event.config import ConcurrentUpdateError
from opal_event.deps import get_config, get_service
from opal_event.errors import ConflictError, NotFoundError, PreconditionFailedError, PreconditionRequiredError
from opal_event.models import (
    Action,
    Execution,
    ExecutionRequest,
    HttpActionPatch,
    ListActions,
    PythonActionPatch,
    ShellActionPatch,
)
from opal_event.service import EventService
from opal_event.validation import ValidationProblem, validate_model, validate_type

router = APIRouter(dependencies=[Depends(require_auth)])

_PATCH_MODELS = {
    "shell": ShellActionPatch,
    "http": HttpActionPatch,
    "python": PythonActionPatch,
}

# "An implementation may reject catalog sizes over an advertised deployment cap, rather than
# silently truncate" (opal_event.1.2.0.yaml). Generous default; not part of the portable/REST
# schema, purely an internal safety net.
ACTIONS_MAX_COUNT = 10_000


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@router.get("/api/v1/actions/", response_model=ListActions)
def list_actions(response: Response, config=Depends(get_config)):
    response.headers["ETag"] = config.revision
    return ListActions(items=config.settings.actions, next_cursor=None)


@router.post("/api/v1/actions/", response_model=Action, status_code=201)
def create_action(action: Action, response: Response, config=Depends(get_config)):
    def mutate(raw: dict) -> dict:
        existing = raw.get("actions", [])
        if any(a["id"] == action.id for a in existing):
            raise ConflictError(f"action '{action.id}' already exists")
        if len(existing) >= ACTIONS_MAX_COUNT:
            raise ConflictError(f"action catalog is full (max {ACTIONS_MAX_COUNT})")
        raw = dict(raw)
        raw["actions"] = [*existing, action.model_dump(mode="json")]
        return raw

    new_settings = config.update(mutate)
    response.headers["Location"] = f"/api/v1/actions/{action.id}"
    return next(a for a in new_settings.actions if a.id == action.id)


@router.get("/api/v1/actions/{action_id}", response_model=Action)
def get_action(action_id: str, response: Response, config=Depends(get_config)):
    response.headers["ETag"] = config.revision
    for action in config.settings.actions:
        if action.id == action_id:
            return action
    raise NotFoundError(f"action '{action_id}' not found")


@router.delete("/api/v1/actions/{action_id}", status_code=204)
def delete_action(action_id: str, config=Depends(get_config), if_match: str | None = Header(default=None)):
    if if_match is None:
        raise PreconditionRequiredError()

    def mutate(raw: dict) -> dict:
        existing_actions = raw.get("actions", [])
        if not any(a["id"] == action_id for a in existing_actions):
            raise NotFoundError(f"action '{action_id}' not found")
        referencing = [r["id"] for r in raw.get("rules", []) if action_id in r.get("actions", [])]
        if referencing:
            raise ConflictError(f"action '{action_id}' is referenced by rule(s): {', '.join(referencing)}")
        raw = dict(raw)
        raw["actions"] = [a for a in existing_actions if a["id"] != action_id]
        return raw

    try:
        config.update(mutate, expected_revision=if_match)
    except ConcurrentUpdateError as exc:
        raise PreconditionFailedError(str(exc)) from exc
    return Response(status_code=204)


@router.patch("/api/v1/actions/{action_id}", response_model=Action)
def patch_action(
    action_id: str,
    patch_body: Annotated[dict[str, Any], Body()],
    config=Depends(get_config),
    if_match: str | None = Header(default=None),
):
    if if_match is None:
        raise PreconditionRequiredError()

    def mutate(raw: dict) -> dict:
        existing = raw.get("actions", [])
        index = next((i for i, a in enumerate(existing) if a["id"] == action_id), None)
        if index is None:
            raise NotFoundError(f"action '{action_id}' not found")
        current = existing[index]
        patch_model_cls = _PATCH_MODELS.get(current["type"])
        if patch_model_cls is None:
            raise ValidationProblem.from_message(["body", "type"], f"unknown action type: {current['type']}", "value_error")
        patch = validate_model(patch_model_cls, patch_body)
        merged = {**current, **patch.model_dump(mode="json", exclude_unset=True)}
        validate_type(Action, merged)  # re-validate the merged action against its full schema
        raw = dict(raw)
        raw["actions"] = [*existing[:index], merged, *existing[index + 1 :]]
        return raw

    try:
        new_settings = config.update(mutate, expected_revision=if_match)
    except ConcurrentUpdateError as exc:
        raise PreconditionFailedError(str(exc)) from exc
    return next(a for a in new_settings.actions if a.id == action_id)


@router.post("/api/v1/actions/{action_id}/execute", response_model=Execution, status_code=202)
def execute_action(
    action_id: str,
    body: ExecutionRequest,
    service: Annotated[EventService, Depends(get_service)],
    config=Depends(get_config),
):
    action = next((a for a in config.settings.actions if a.id == action_id), None)
    if action is None:
        raise NotFoundError(f"action '{action_id}' not found")
    if not action.enabled:
        raise ValidationProblem.from_message(["body", "action_id"], f"action '{action_id}' is disabled", "value_error")

    event_input = body.event
    event = {
        "id": event_input.id or str(uuid4()),
        "topic": event_input.topic,
        "source": event_input.source or "opal-event-rest",
        "timestamp": event_input.timestamp or _now(),
        "data": event_input.data,
    }
    settings = config.settings
    record = service.executor.submit(
        action,
        event,
        event_id=event["id"],
        rule_id=None,
        trigger="manual",
        execution_settings=settings.execution,
        security_settings=settings.security,
    )
    return Execution(
        id=record.id,
        event_id=record.event_id,
        rule_id=record.rule_id,
        action_id=record.action_id,
        status=record.status,
        trigger=record.trigger,
        created_at=record.created_at,
        action_revision=record.action_revision,
        error_code=record.error_code,
        error_message=record.error_message,
    )
