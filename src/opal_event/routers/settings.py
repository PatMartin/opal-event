"""``/api/v1/setting``: portable, non-secret live-adjustable settings (execution, persistence
subset, max_event_bytes). Network bindings, CURVE keys, bearer tokens, database path, and
rules/actions are intentionally excluded -- see ``PortableSettings`` in ``opal_event.yaml``."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Response

from opal_event.auth import require_auth
from opal_event.config import ConcurrentUpdateError
from opal_event.deps import get_config
from opal_event.errors import NotFoundError, PreconditionFailedError, PreconditionRequiredError
from opal_event.models import (
    ExecutionSettings,
    PortablePersistenceSettings,
    PortableSettings,
    PortableSettingsPatch,
    SettingRead,
    SettingUpdate,
    ZeroMqSettings,
)

router = APIRouter(dependencies=[Depends(require_auth)])


def _portable_view(settings) -> PortableSettings:
    return PortableSettings(
        execution=settings.execution,
        persistence=PortablePersistenceSettings(
            event_mode=settings.persistence.event_mode,
            retention_days=settings.persistence.retention_days,
            execution_history_enabled=settings.persistence.execution_history_enabled,
        ),
        max_event_bytes=settings.zeromq.max_event_bytes,
    )


@router.get("/api/v1/setting/", response_model=PortableSettings)
def get_settings(response: Response, config=Depends(get_config)):
    response.headers["ETag"] = config.revision
    return _portable_view(config.settings)


@router.patch("/api/v1/setting/", response_model=PortableSettings)
def patch_settings(patch: PortableSettingsPatch, config=Depends(get_config), if_match: str | None = Header(default=None)):
    if if_match is None:
        raise PreconditionRequiredError("If-Match header (from GET /api/v1/setting/) is required for this update")

    def mutate(raw: dict) -> dict:
        return _apply_portable_patch(raw, patch.model_dump(mode="json", exclude_unset=True))

    try:
        new_settings = config.update(mutate, expected_revision=if_match)
    except ConcurrentUpdateError as exc:
        raise PreconditionFailedError(str(exc)) from exc
    return _portable_view(new_settings)


def _apply_portable_patch(raw: dict, fields: dict) -> dict:
    """Merge a ``PortableSettingsPatch``'s set fields into the raw config dict. Shared with
    profile activation (``routers/profiles.py``), which applies a stored preset identically."""
    raw = dict(raw)
    if "execution" in fields:
        raw["execution"] = {**raw.get("execution", {}), **fields["execution"]}
    if "persistence" in fields:
        raw["persistence"] = {**raw.get("persistence", {}), **fields["persistence"]}
    if "max_event_bytes" in fields:
        raw["zeromq"] = {**raw.get("zeromq", {}), "max_event_bytes": fields["max_event_bytes"]}
    return raw


_PERSISTENCE_KEYS = ("event_mode", "retention_days", "execution_history_enabled")


def _defaults_map() -> dict:
    """Default value per portable key, taken from field defaults (Settings() isn't bare-
    instantiable — persistence is required)."""
    m = {f"execution.{k}": f.default for k, f in ExecutionSettings.model_fields.items()}
    m |= {f"persistence.{k}": PortablePersistenceSettings.model_fields[k].default for k in _PERSISTENCE_KEYS}
    m["max_event_bytes"] = ZeroMqSettings.model_fields["max_event_bytes"].default
    return m


def _portable_map(settings) -> dict:
    """Flat dotted-key view of the portable settings (kernel ``OpalSettingsMap`` space):
    ``execution.<field>``, ``persistence.<field>``, and ``max_event_bytes``."""
    m = {f"execution.{k}": v for k, v in settings.execution.model_dump(mode="json").items()}
    m |= {f"persistence.{k}": getattr(settings.persistence, k) for k in _PERSISTENCE_KEYS}
    m["max_event_bytes"] = settings.zeromq.max_event_bytes
    return m


def _setting_read(key: str, settings) -> SettingRead:
    defaults = _defaults_map()
    if key not in defaults:
        raise NotFoundError(f"unknown setting '{key}'")
    value = _portable_map(settings)[key]
    return SettingRead(key=key, value=value, default=defaults[key], is_default=value == defaults[key])


def _set_key(config, key: str, value, if_match: str):
    _setting_read(key, config.settings)  # 404 on unknown key before any mutation
    section, field = ("zeromq", "max_event_bytes") if key == "max_event_bytes" else key.split(".", 1)
    try:
        return config.update(
            lambda raw: {**raw, section: {**raw.get(section, {}), field: value}},
            expected_revision=if_match,
        )
    except ConcurrentUpdateError as exc:
        raise PreconditionFailedError(str(exc)) from exc


@router.get("/api/v1/setting/{key}", response_model=SettingRead)
def get_setting(key: str, config=Depends(get_config)):
    return _setting_read(key, config.settings)


@router.put("/api/v1/setting/{key}", response_model=SettingRead)
def put_setting(key: str, body: SettingUpdate, config=Depends(get_config), if_match: str | None = Header(default=None)):
    if if_match is None:
        raise PreconditionRequiredError("If-Match header (from GET /api/v1/setting/) is required for this update")
    return _setting_read(key, _set_key(config, key, body.value, if_match))


@router.delete("/api/v1/setting/{key}", status_code=204)
def delete_setting(key: str, config=Depends(get_config), if_match: str | None = Header(default=None)):
    """Resets a single key to its schema default (kernel ``opal_setting_delete``, 204)."""
    if if_match is None:
        raise PreconditionRequiredError("If-Match header (from GET /api/v1/setting/) is required for this update")
    read = _setting_read(key, config.settings)  # 404 unknown key before touching defaults
    _set_key(config, key, read.default, if_match)
    return Response(status_code=204)
