"""``/api/v1/action``: CRUD over YAML-persisted action definitions plus manual execution.

Actions became a bounded configuration catalog in opal_event.1.2.0.yaml: the list endpoint returns
the complete collection (no cursor pagination) with a strong ETag, and PATCH/DELETE now require a
matching ``If-Match`` (428 if omitted, 412 if stale) instead of the previous unconditional writes.
"""

from __future__ import annotations

from typing import Annotated, Any

import jsonschema
from fastapi import APIRouter, Body, Depends, Header, Request, Response
from fastapi.responses import JSONResponse

from opal_event.auth import discovery_projection, is_execute_scope, require_auth
from opal_event.config import ConcurrentUpdateError
from opal_event.deps import get_config, get_service
from opal_event.errors import ConflictError, NotFoundError, PreconditionFailedError, PreconditionRequiredError
from opal_event.models import (
    Action,
    Execution,
    ExecutionRequest,
    HttpActionPatch,
    ActionList,
    PythonActionPatch,
    ShellActionPatch,
)
from opal_event.normalization import normalize_event
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


@router.get("/api/v1/action/", response_model=ActionList)
def list_actions(request: Request, response: Response, config=Depends(get_config)):
    response.headers["ETag"] = config.revision
    if is_execute_scope(request):
        return JSONResponse([discovery_projection(a) for a in config.settings.actions])
    return config.settings.actions


@router.post("/api/v1/action/", response_model=Action, status_code=201)
def create_action(action: Action, response: Response, config=Depends(get_config), if_match: str | None = Header(default=None)):
    if if_match is None:
        raise PreconditionRequiredError()

    def mutate(raw: dict) -> dict:
        existing = raw.get("actions", [])
        if any(a["id"] == action.id for a in existing):
            raise ConflictError(f"action '{action.id}' already exists")
        if len(existing) >= ACTIONS_MAX_COUNT:
            raise ConflictError(f"action catalog is full (max {ACTIONS_MAX_COUNT})")
        raw = dict(raw)
        raw["actions"] = [*existing, action.model_dump(mode="json")]
        return raw

    try:
        new_settings = config.update(mutate, expected_revision=if_match)
    except ConcurrentUpdateError as exc:
        raise PreconditionFailedError(str(exc)) from exc
    response.headers["Location"] = f"/api/v1/action/{action.id}"
    return next(a for a in new_settings.actions if a.id == action.id)


@router.get("/api/v1/action/{action_id}", response_model=Action)
def get_action(action_id: str, request: Request, response: Response, config=Depends(get_config)):
    response.headers["ETag"] = config.revision
    for action in config.settings.actions:
        if action.id == action_id:
            if is_execute_scope(request):
                return JSONResponse(discovery_projection(action))
            return action
    raise NotFoundError(f"action '{action_id}' not found")


@router.delete("/api/v1/action/{action_id}", status_code=204)
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


@router.patch("/api/v1/action/{action_id}", response_model=Action)
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


@router.post("/api/v1/action/{action_id}/execute", response_model=Execution, status_code=202)
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

    event = normalize_event(body.event)
    if action.input_schema:
        try:
            jsonschema.validate(event.get("data"), action.input_schema)
        except jsonschema.ValidationError as exc:
            raise ValidationProblem.from_message(
                ["body", "event", "data"],
                f"event.data does not satisfy action '{action_id}' input_schema: {exc.message}",
                "value_error",
            ) from exc
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
