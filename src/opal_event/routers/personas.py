"""``/api/v1/persona``: CRUD over YAML-persisted persona registrations (v1.6.0).

A persona is a named automation identity that rules and actions can attribute work to; an
optional ``voice_id`` binds it to an OPAL Say voice for spoken notifications. Personas are a
bounded configuration catalog like rules/actions: complete collection on GET with a strong
ETag, and every mutation requires ``If-Match`` (428 if omitted, 412 if stale).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Response

from opal_event.auth import require_auth
from opal_event.config import ConcurrentUpdateError
from opal_event.deps import get_config
from opal_event.errors import ConflictError, NotFoundError, PreconditionFailedError, PreconditionRequiredError
from opal_event.models import PersonaCreate, PersonaRead, PersonaUpdate, ValidationReport, _utcnow

router = APIRouter(dependencies=[Depends(require_auth)])

# Same deployment-cap convention as rules/actions: generous internal ceiling, not contractual.
PERSONAS_MAX_COUNT = 10_000


@router.get("/api/v1/persona/", response_model=list[PersonaRead])
def list_personas(
    response: Response,
    config=Depends(get_config),
    enabled: bool | None = Query(default=None),
):
    response.headers["ETag"] = config.revision
    personas = config.settings.personas
    if enabled is not None:
        personas = [p for p in personas if p.enabled == enabled]
    return personas


@router.post("/api/v1/persona/", response_model=PersonaRead, status_code=201)
def create_persona(
    body: PersonaCreate, response: Response, config=Depends(get_config), if_match: str | None = Header(default=None)
):
    if if_match is None:
        raise PreconditionRequiredError()

    now = _utcnow()
    persona = PersonaRead(
        persona_id=body.persona_id,
        name=body.name,
        description=body.description,
        voice_id=body.voice_id,
        enabled=body.enabled,
        created_at=now,
        updated_at=now,
    )

    def mutate(raw: dict) -> dict:
        existing = raw.get("personas", [])
        if any(p["persona_id"] == body.persona_id for p in existing):
            raise ConflictError(f"persona '{body.persona_id}' is already registered")
        if len(existing) >= PERSONAS_MAX_COUNT:
            raise ConflictError(f"persona catalog is full (max {PERSONAS_MAX_COUNT})")
        raw = dict(raw)
        raw["personas"] = [*existing, persona.model_dump(mode="json")]
        return raw

    try:
        config.update(mutate, expected_revision=if_match)
    except ConcurrentUpdateError as exc:
        raise PreconditionFailedError(str(exc)) from exc
    response.headers["Location"] = f"/api/v1/persona/{persona.persona_id}"
    return persona


@router.post("/api/v1/persona/{persona_id}/validate", response_model=ValidationReport)
def validate_persona(persona_id: str, config=Depends(get_config)):
    persona = next((p for p in config.settings.personas if p.persona_id == persona_id), None)
    if persona is None:
        raise NotFoundError(f"persona '{persona_id}' not found")
    errors: list[str] = []
    warnings: list[str] = []
    if not persona.enabled:
        errors.append(f"persona '{persona_id}' is disabled")
    if persona.voice_id:
        # voice_id names an OPAL Say binding this service does not resolve locally.
        warnings.append(f"voice binding '{persona.voice_id}' is resolved by OPAL Say, not verified locally")
    return ValidationReport(valid=not errors, errors=errors, warnings=warnings)


@router.get("/api/v1/persona/{persona_id}", response_model=PersonaRead)
def get_persona(persona_id: str, response: Response, config=Depends(get_config)):
    response.headers["ETag"] = config.revision
    for persona in config.settings.personas:
        if persona.persona_id == persona_id:
            return persona
    raise NotFoundError(f"persona '{persona_id}' not found")


@router.patch("/api/v1/persona/{persona_id}", response_model=PersonaRead)
def patch_persona(
    persona_id: str,
    patch: PersonaUpdate,
    config=Depends(get_config),
    if_match: str | None = Header(default=None),
):
    if if_match is None:
        raise PreconditionRequiredError()

    def mutate(raw: dict) -> dict:
        existing = raw.get("personas", [])
        index = next((i for i, p in enumerate(existing) if p["persona_id"] == persona_id), None)
        if index is None:
            raise NotFoundError(f"persona '{persona_id}' not found")
        merged = {**existing[index], **patch.model_dump(mode="json", exclude_unset=True), "updated_at": _utcnow()}
        raw = dict(raw)
        raw["personas"] = [*existing[:index], merged, *existing[index + 1 :]]
        return raw

    try:
        new_settings = config.update(mutate, expected_revision=if_match)
    except ConcurrentUpdateError as exc:
        raise PreconditionFailedError(str(exc)) from exc
    return next(p for p in new_settings.personas if p.persona_id == persona_id)


@router.delete("/api/v1/persona/{persona_id}", status_code=204)
def delete_persona(persona_id: str, config=Depends(get_config), if_match: str | None = Header(default=None)):
    if if_match is None:
        raise PreconditionRequiredError()

    def mutate(raw: dict) -> dict:
        existing = raw.get("personas", [])
        if not any(p["persona_id"] == persona_id for p in existing):
            raise NotFoundError(f"persona '{persona_id}' not found")
        raw = dict(raw)
        raw["personas"] = [p for p in existing if p["persona_id"] != persona_id]
        return raw

    try:
        config.update(mutate, expected_revision=if_match)
    except ConcurrentUpdateError as exc:
        raise PreconditionFailedError(str(exc)) from exc
    return Response(status_code=204)
