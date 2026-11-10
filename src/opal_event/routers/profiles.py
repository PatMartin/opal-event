"""``/api/v1/profile``: kernel-mounted profile catalog (v1.7.1).

A profile is a named preset of the service's portable operating defaults (a
``PortableSettingsPatch``). Profiles are YAML-persisted like rules/actions/personas:
GET returns the complete bounded collection with a strong ETag, and every mutation
requires ``If-Match`` (428 if omitted, 412 if stale). Exactly one profile may be active
at a time; activation applies the stored preset to the live configuration in the same
atomic write, so `is_active` always describes the last-applied preset. Deactivation
does not revert previously applied settings — profiles are write-only presets.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Response

from opal_event.auth import require_auth
from opal_event.config import ConcurrentUpdateError
from opal_event.deps import get_config
from opal_event.errors import ConflictError, NotFoundError, PreconditionFailedError, PreconditionRequiredError
from opal_event.models import Profile, ProfileCreate, ProfilePatch, _utcnow
from opal_event.routers.settings import _apply_portable_patch

router = APIRouter(dependencies=[Depends(require_auth)])

# Same deployment-cap convention as rules/actions/personas: generous internal ceiling.
PROFILES_MAX_COUNT = 10_000


def _mark_active(profiles: list[dict], index: int) -> list[dict]:
    """Copy of `profiles` with only entry `index` carrying ``is_active: true``."""
    return [{**p, "is_active": i == index} for i, p in enumerate(profiles)]


def _apply_preset(raw: dict, profile: dict) -> dict:
    """Apply an activated profile's stored settings preset to the raw config dict."""
    settings = profile.get("settings")
    if settings:
        # Stored presets may carry explicit nulls for unset fields — only real values apply.
        raw = _apply_portable_patch(raw, {k: v for k, v in settings.items() if v is not None})
    return raw


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
    profile_id: int | None = None

    def mutate(raw: dict) -> dict:
        nonlocal profile_id
        existing = raw.get("profiles", [])
        if any(p["name"] == body.name for p in existing):
            raise ConflictError(f"profile name '{body.name}' already exists")
        if body.id is not None and any(p["id"] == body.id for p in existing):
            raise ConflictError(f"profile id {body.id} is already in use")
        if len(existing) >= PROFILES_MAX_COUNT:
            raise ConflictError(f"profile catalog is full (max {PROFILES_MAX_COUNT})")
        profile_id = body.id if body.id is not None else max((p["id"] for p in existing), default=0) + 1
        profile = Profile(
            id=profile_id,
            name=body.name,
            description=body.description,
            is_active=body.is_active,
            settings=body.settings,
            created_at=now,
            updated_at=now,
        )
        raw = dict(raw)
        profiles = [*existing, profile.model_dump(mode="json")]
        if body.is_active:
            profiles = _mark_active(profiles, len(profiles) - 1)
            raw = _apply_preset(raw, profile.model_dump(mode="json"))
        raw["profiles"] = profiles
        return raw

    try:
        new_settings = config.update(mutate, expected_revision=if_match)
    except ConcurrentUpdateError as exc:
        raise PreconditionFailedError(str(exc)) from exc
    response.headers["Location"] = f"/api/v1/profile/{profile_id}"
    return next(p for p in new_settings.profiles if p.id == profile_id)


@router.get("/api/v1/profile/{profile_id}", response_model=Profile)
def get_profile(profile_id: int, response: Response, config=Depends(get_config)):
    response.headers["ETag"] = config.revision
    for profile in config.settings.profiles:
        if profile.id == profile_id:
            return profile
    raise NotFoundError(f"profile '{profile_id}' not found")


@router.patch("/api/v1/profile/{profile_id}", response_model=Profile)
def patch_profile(profile_id: int, patch: ProfilePatch, config=Depends(get_config), if_match: str | None = Header(default=None)):
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
        merged = {**existing[index], **fields, "updated_at": _utcnow()}
        raw = dict(raw)
        profiles = [*existing[:index], merged, *existing[index + 1 :]]
        if fields.get("is_active"):
            profiles = _mark_active(profiles, index)
            raw = _apply_preset(raw, merged)
        raw["profiles"] = profiles
        return raw

    try:
        new_settings = config.update(mutate, expected_revision=if_match)
    except ConcurrentUpdateError as exc:
        raise PreconditionFailedError(str(exc)) from exc
    return next(p for p in new_settings.profiles if p.id == profile_id)


@router.delete("/api/v1/profile/{profile_id}", status_code=204)
def delete_profile(profile_id: int, config=Depends(get_config), if_match: str | None = Header(default=None)):
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
def activate_profile(profile_id: int, config=Depends(get_config), if_match: str | None = Header(default=None)):
    """Mark `profile_id` active (deactivating all others) and apply its stored preset to
    the live configuration in the same atomic write."""
    if if_match is None:
        raise PreconditionRequiredError()

    def mutate(raw: dict) -> dict:
        existing = raw.get("profiles", [])
        index = next((i for i, p in enumerate(existing) if p["id"] == profile_id), None)
        if index is None:
            raise NotFoundError(f"profile '{profile_id}' not found")
        raw = dict(raw)
        activated = {**existing[index], "updated_at": _utcnow()}
        profiles = [*existing[:index], activated, *existing[index + 1 :]]
        raw["profiles"] = _mark_active(profiles, index)
        return _apply_preset(raw, activated)

    try:
        new_settings = config.update(mutate, expected_revision=if_match)
    except ConcurrentUpdateError as exc:
        raise PreconditionFailedError(str(exc)) from exc
    return next(p for p in new_settings.profiles if p.id == profile_id)
