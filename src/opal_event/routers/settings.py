"""``/api/v1/setting``: portable, non-secret live-adjustable settings (execution, persistence
subset, max_event_bytes). v0.1.0: the collection is a flat ``OpalSettingsMap`` (GET only —
the collection PATCH is retired); individual overrides use GET/PUT/DELETE ``/setting/{key}``
with the kernel ``SettingRead`` record (effective/configured/default + provenance). Network
bindings, CURVE keys, bearer tokens, database path, and rules/actions are intentionally
excluded -- see ``PortableSettings`` in ``opal_event.yaml``."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Response

from opal_event.auth import require_auth
from opal_event.config import ConcurrentUpdateError
from opal_event.deps import get_config
from opal_event.errors import NotFoundError, PreconditionFailedError, PreconditionRequiredError
from opal_event.models import (
    ExecutionSettings,
    PortablePersistenceSettings,
    SettingRead,
    SettingUpdate,
    ZeroMqSettings,
)

router = APIRouter(dependencies=[Depends(require_auth)])


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


def _key_parts(key: str) -> tuple[str, str]:
    """Dotted settings-map key -> (raw YAML section, field)."""
    return ("zeromq", "max_event_bytes") if key == "max_event_bytes" else key.split(".", 1)


def _defaults_map() -> dict:
    """Default value per portable key, taken from field defaults (Settings() isn't bare-
    instantiable — persistence is required)."""
    m = {f"execution.{k}": f.default for k, f in ExecutionSettings.model_fields.items()}
    m |= {f"persistence.{k}": PortablePersistenceSettings.model_fields[k].default for k in _PERSISTENCE_KEYS}
    m["max_event_bytes"] = ZeroMqSettings.model_fields["max_event_bytes"].default
    return m


def _portable_map(settings) -> dict:
    """Flat dotted-key view of the portable settings (kernel ``OpalSettingsMap``):
    ``execution.<field>``, ``persistence.<field>``, and ``max_event_bytes``."""
    m = {f"execution.{k}": v for k, v in settings.execution.model_dump(mode="json").items()}
    m |= {f"persistence.{k}": getattr(settings.persistence, k) for k in _PERSISTENCE_KEYS}
    m["max_event_bytes"] = settings.zeromq.max_event_bytes
    return m


@router.get("/api/v1/setting/")
def get_settings(response: Response, config=Depends(get_config)):
    """Kernel ``OpalSettingsMap``: complete flat map of registered keys to effective values."""
    response.headers["ETag"] = config.revision
    return _portable_map(config.settings)


def _setting_read(key: str, settings, raw: dict) -> SettingRead:
    defaults = _defaults_map()
    if key not in defaults:
        raise NotFoundError(f"unknown setting '{key}'")
    value = _portable_map(settings)[key]
    section, field = _key_parts(key)
    present = isinstance(raw.get(section), dict) and field in raw[section]
    configured = raw[section][field] if present else defaults[key]
    # The YAML file is rewritten as a normalized full dump on every write, so key presence alone
    # cannot mark a user override; an override is a persisted value that differs from the
    # registered default (a default-valued persisted write reports as default, by design).
    override = present and configured != defaults[key]
    return SettingRead(
        key=key,
        value=value,
        configured_value=configured,
        default=defaults[key],
        has_override=override,
        is_default=not override,
        source="persisted" if override else "default",
        restart_required=False,
        pending_restart=False,
    )


def _set_key(config, key: str, value, if_match: str):
    _setting_read(key, config.settings, config.raw_at_load)  # 404 on unknown key before any mutation
    section, field = _key_parts(key)
    try:
        return config.update(
            lambda raw: {**raw, section: {**raw.get(section, {}), field: value}},
            expected_revision=if_match,
        )
    except ConcurrentUpdateError as exc:
        raise PreconditionFailedError(str(exc)) from exc


@router.get("/api/v1/setting/{key}", response_model=SettingRead)
def get_setting(key: str, config=Depends(get_config)):
    return _setting_read(key, config.settings, config.raw_at_load)


@router.put("/api/v1/setting/{key}", response_model=SettingRead)
def put_setting(key: str, body: SettingUpdate, config=Depends(get_config), if_match: str | None = Header(default=None)):
    if if_match is None:
        raise PreconditionRequiredError("If-Match header (from GET /api/v1/setting/) is required for this update")
    return _setting_read(key, _set_key(config, key, body.value, if_match), config.raw_at_load)


@router.delete("/api/v1/setting/{key}", status_code=204)
def delete_setting(key: str, config=Depends(get_config), if_match: str | None = Header(default=None)):
    """Delete the persistent override for one registered key (kernel ``opal_setting_delete``):
    204 even when no override exists; the effective value falls back to the registered default."""
    if if_match is None:
        raise PreconditionRequiredError("If-Match header (from GET /api/v1/setting/) is required for this update")
    section, field = _key_parts(key)
    _setting_read(key, config.settings, config.raw_at_load)  # 404 unknown key

    def mutate(raw: dict) -> dict:
        if isinstance(raw.get(section), dict):
            raw[section] = {k: v for k, v in raw[section].items() if k != field}
        return raw

    try:
        config.update(mutate, expected_revision=if_match)
    except ConcurrentUpdateError as exc:
        raise PreconditionFailedError(str(exc)) from exc
    return Response(status_code=204)
