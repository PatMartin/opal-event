"""``/api/v1/profile``: kernel-mounted profile catalog (v0.1.0, kernel v3 namespaced
profiles).

A profile is a portable ``OpalProfile`` document: opaque string ``id``, required
``shared`` and ``modules`` sections (empty objects valid). opal-event's own compartment
is a closed empty schema — activation is a local selection only and changes no live
settings; foreign module compartments are stored and returned verbatim. Profiles are
YAML-persisted like rules/actions/personas: GET returns the complete bounded collection
with a strong ETag, and every mutation requires ``If-Match`` (428 if omitted, 412 if
stale). Patches may only target the ``modules.opal-event`` compartment — foreign
compartment edits are rejected with 422 and stored contents preserved.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Response

from opal_event.auth import require_auth
from opal_event.config import ConcurrentUpdateError
from opal_event.deps import get_config
from opal_event.errors import ConflictError, NotFoundError, PreconditionFailedError, PreconditionRequiredError
from opal_event.models import _PROFILE_COMPARTMENT, Profile, ProfileCreate, ProfilePatch, _utcnow

router = APIRouter(dependencies=[Depends(require_auth)])

# Same deployment-cap convention as rules/actions/personas: generous internal ceiling.
PROFILES_MAX_COUNT = 10_000


def _mark_active(profiles: list[dict], index: int) -> list[dict]:
    """Copy of `profiles` with only entry `index` carrying ``is_active: true``."""
    return [{**p, "is_active": i == index} for i, p in enumerate(profiles)]


def _allocate_id(existing: list[dict]) -> str:
    """Next free opaque id: continues the integer sequence for ids allocated before the
    v3 string-id migration, so stored catalogs keep a stable ordering."""
    numeric = [int(p["id"]) for p in existing if str(p.get("id", "")).isdigit()]
    return str(max(numeric, default=0) + 1)


@router.get("/api/v1/profile/", response_model=list[Profile])
def list_profiles(response: Response, config=Depends(get_config)):
    response.headers["ETag"] = config.revision
    return config.settings.profiles


@router.post("/api/v1/profile/", response_model=Profile, status_code=201)
def create_profile(
    body: ProfileCreate, response: Response, config=Depends(get_config), if_match: str | None = Header(default=None)
):
    if if_match is None:
        raise PreconditionRequiredError()

    now = _utcnow()
    profile_id: str | None = None

    def mutate(raw: dict) -> dict:
        nonlocal profile_id
        existing = raw.get("profiles", [])
        if any(p["name"] == body.name for p in existing):
            raise ConflictError(f"profile name '{body.name}' already exists")
        if body.id is not None and any(p["id"] == body.id for p in existing):
            raise ConflictError(f"profile id '{body.id}' is already in use")
        if len(existing) >= PROFILES_MAX_COUNT:
            raise ConflictError(f"profile catalog is full (max {PROFILES_MAX_COUNT})")
        profile_id = body.id if body.id is not None else _allocate_id(existing)
        profile = Profile(
            id=profile_id,
            name=body.name,
            description=body.description,
            is_active=body.is_active,
            shared=body.shared,
            modules=body.modules,
            created_at=now,
            updated_at=now,
        )
        raw = dict(raw)
        profiles = [*existing, profile.model_dump(mode="json")]
        if body.is_active:
            profiles = _mark_active(profiles, len(profiles) - 1)
        raw["profiles"] = profiles
        return raw

    try:
        new_settings = config.update(mutate, expected_revision=if_match)
    except ConcurrentUpdateError as exc:
        raise PreconditionFailedError(str(exc)) from exc
    response.headers["Location"] = f"/api/v1/profile/{profile_id}"
    return next(p for p in new_settings.profiles if p.id == profile_id)


@router.get("/api/v1/profile/{profile_id}", response_model=Profile)
def get_profile(profile_id: str, response: Response, config=Depends(get_config)):
    response.headers["ETag"] = config.revision
    for profile in config.settings.profiles:
        if profile.id == profile_id:
            return profile
    raise NotFoundError(f"profile '{profile_id}' not found")


@router.patch("/api/v1/profile/{profile_id}", response_model=Profile)
def patch_profile(profile_id: str, patch: ProfilePatch, config=Depends(get_config), if_match: str | None = Header(default=None)):
    if if_match is None:
        raise PreconditionRequiredError()

    def mutate(raw: dict) -> dict:
        existing = raw.get("profiles", [])
        index = next((i for i, p in enumerate(existing) if p["id"] == profile_id), None)
        if index is None:
            raise NotFoundError(f"profile '{profile_id}' not found")
        fields = patch.model_dump(mode="json", exclude_unset=True)
        if "name" in fields and any(p["name"] == fields["name"] and i != index for i, p in enumerate(existing)):
            raise ConflictError(f"profile name '{fields['name']}' already exists")
        stored = existing[index]
        merged = {**stored}
        for key in ("name", "description", "is_active", "shared"):
            if key in fields:
                merged[key] = fields[key]
        if "modules" in fields:
            # Own-compartment field-wise merge; foreign compartments are never touched
            # (the patch schema rejects them before we get here). Explicit null removes
            # an override field; the compartment itself must remain an object.
            modules = dict(stored.get("modules", {}))
            own = dict(modules.get(_PROFILE_COMPARTMENT) or {})
            for field, value in (fields["modules"].get(_PROFILE_COMPARTMENT) or {}).items():
                if value is None:
                    own.pop(field, None)
                else:
                    own[field] = value
            modules[_PROFILE_COMPARTMENT] = own
            merged["modules"] = modules
        merged["updated_at"] = _utcnow()
        raw = dict(raw)
        profiles = [*existing[:index], merged, *existing[index + 1 :]]
        if fields.get("is_active"):
            profiles = _mark_active(profiles, index)
        raw["profiles"] = profiles
        return raw

    try:
        new_settings = config.update(mutate, expected_revision=if_match)
    except ConcurrentUpdateError as exc:
        raise PreconditionFailedError(str(exc)) from exc
    return next(p for p in new_settings.profiles if p.id == profile_id)


@router.delete("/api/v1/profile/{profile_id}", status_code=204)
def delete_profile(profile_id: str, config=Depends(get_config), if_match: str | None = Header(default=None)):
    if if_match is None:
        raise PreconditionRequiredError()

    def mutate(raw: dict) -> dict:
        existing = raw.get("profiles", [])
        if not any(p["id"] == profile_id for p in existing):
            raise NotFoundError(f"profile '{profile_id}' not found")
        raw = dict(raw)
        raw["profiles"] = [p for p in existing if p["id"] != profile_id]
        return raw

    try:
        config.update(mutate, expected_revision=if_match)
    except ConcurrentUpdateError as exc:
        raise PreconditionFailedError(str(exc)) from exc
    return Response(status_code=204)


@router.post("/api/v1/profile/{profile_id}/activate", response_model=Profile)
def activate_profile(profile_id: str, config=Depends(get_config), if_match: str | None = Header(default=None)):
    """Mark `profile_id` active (deactivating all others). v3: activation is a local
    selection only — opal-event's compartment declares no defaults to apply."""
    if if_match is None:
        raise PreconditionRequiredError()

    def mutate(raw: dict) -> dict:
        existing = raw.get("profiles", [])
        index = next((i for i, p in enumerate(existing) if p["id"] == profile_id), None)
        if index is None:
            raise NotFoundError(f"profile '{profile_id}' not found")
        raw = dict(raw)
        profiles = [*existing[:index], {**existing[index], "updated_at": _utcnow()}, *existing[index + 1 :]]
        raw["profiles"] = _mark_active(profiles, index)
        return raw

    try:
        new_settings = config.update(mutate, expected_revision=if_match)
    except ConcurrentUpdateError as exc:
        raise PreconditionFailedError(str(exc)) from exc
    return next(p for p in new_settings.profiles if p.id == profile_id)
