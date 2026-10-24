"""``/api/v1/rules``: CRUD over YAML-persisted rules plus a dry-run ``/test`` endpoint.

Rules became a bounded configuration catalog in opal_event.1.2.0.yaml: the list endpoint returns the
complete collection (no cursor pagination) with a strong ETag, and PATCH/DELETE now require a
matching ``If-Match`` (428 if omitted, 412 if stale) instead of the previous unconditional writes.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Response

from opal_event.auth import require_auth
from opal_event.config import ConcurrentUpdateError
from opal_event.deps import get_config
from opal_event.errors import ConflictError, NotFoundError, PreconditionFailedError, PreconditionRequiredError
from opal_event.models import EventInput, ListRules, Rule, RulePatch, RuleTestInput, RuleTestResult
from opal_event.normalization import normalize_event
from opal_event.rules import evaluate_rule

router = APIRouter(dependencies=[Depends(require_auth)])

# "An implementation may reject catalog sizes over an advertised deployment cap, rather than
# silently truncate" (opal_event.1.2.0.yaml). Generous default; not part of the portable/REST
# schema, purely an internal safety net.
RULES_MAX_COUNT = 10_000


def _normalize_event_input(event_input: EventInput) -> dict[str, Any]:
    return normalize_event(event_input)


@router.get("/api/v1/rules/", response_model=ListRules)
def list_rules(response: Response, config=Depends(get_config)):
    response.headers["ETag"] = config.revision
    return ListRules(items=config.settings.rules, next_cursor=None)


@router.post("/api/v1/rules/", response_model=Rule, status_code=201)
def create_rule(rule: Rule, response: Response, config=Depends(get_config)):
    def mutate(raw: dict) -> dict:
        existing = raw.get("rules", [])
        if any(r["id"] == rule.id for r in existing):
            raise ConflictError(f"rule '{rule.id}' already exists")
        if len(existing) >= RULES_MAX_COUNT:
            raise ConflictError(f"rule catalog is full (max {RULES_MAX_COUNT})")
        raw = dict(raw)
        raw["rules"] = [*existing, rule.model_dump(mode="json")]
        return raw

    new_settings = config.update(mutate)
    response.headers["Location"] = f"/api/v1/rules/{rule.id}"
    return next(r for r in new_settings.rules if r.id == rule.id)


@router.get("/api/v1/rules/{rule_id}", response_model=Rule)
def get_rule(rule_id: str, response: Response, config=Depends(get_config)):
    response.headers["ETag"] = config.revision
    for rule in config.settings.rules:
        if rule.id == rule_id:
            return rule
    raise NotFoundError(f"rule '{rule_id}' not found")


@router.delete("/api/v1/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: str, config=Depends(get_config), if_match: str | None = Header(default=None)):
    if if_match is None:
        raise PreconditionRequiredError()

    def mutate(raw: dict) -> dict:
        existing = raw.get("rules", [])
        if not any(r["id"] == rule_id for r in existing):
            raise NotFoundError(f"rule '{rule_id}' not found")
        raw = dict(raw)
        raw["rules"] = [r for r in existing if r["id"] != rule_id]
        return raw

    try:
        config.update(mutate, expected_revision=if_match)
    except ConcurrentUpdateError as exc:
        raise PreconditionFailedError(str(exc)) from exc
    return Response(status_code=204)


@router.patch("/api/v1/rules/{rule_id}", response_model=Rule)
def patch_rule(rule_id: str, patch: RulePatch, config=Depends(get_config), if_match: str | None = Header(default=None)):
    if if_match is None:
        raise PreconditionRequiredError()

    def mutate(raw: dict) -> dict:
        existing = raw.get("rules", [])
        index = next((i for i, r in enumerate(existing) if r["id"] == rule_id), None)
        if index is None:
            raise NotFoundError(f"rule '{rule_id}' not found")
        merged = {**existing[index], **patch.model_dump(mode="json", exclude_unset=True)}
        raw = dict(raw)
        raw["rules"] = [*existing[:index], merged, *existing[index + 1 :]]
        return raw

    try:
        new_settings = config.update(mutate, expected_revision=if_match)
    except ConcurrentUpdateError as exc:
        raise PreconditionFailedError(str(exc)) from exc
    return next(r for r in new_settings.rules if r.id == rule_id)


@router.post("/api/v1/rules/{rule_id}/test", response_model=RuleTestResult)
def test_rule(rule_id: str, test_input: RuleTestInput, config=Depends(get_config)):
    rule = next((r for r in config.settings.rules if r.id == rule_id), None)
    if rule is None:
        raise NotFoundError(f"rule '{rule_id}' not found")
    event = _normalize_event_input(test_input.event)
    matched, checks = evaluate_rule(rule, event)
    return RuleTestResult(matched=matched, action_ids=list(rule.actions) if matched else [], checks=checks)
