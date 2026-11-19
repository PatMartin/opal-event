"""``/api/v1/config``: kernel ``config save|load|validate`` (v0.1.0). Portable
``OpalConfigSnapshot`` envelopes carry flat setting-key overrides plus the declared
resource sections (profiles/personas/rules/actions); history, sessions, runtime state,
and secrets are never included. Loads are atomic full replacements under a mandatory
``If-Match`` revision; a failed validation or write leaves the live config intact."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Body, Depends, Header, Response
from pydantic import TypeAdapter, ValidationError

from opal_event.auth import require_auth
from opal_event.config import ConcurrentUpdateError
from opal_event.deps import get_config
from opal_event.errors import (
    NotFoundError,
    OperationFailedError,
    PreconditionFailedError,
    PreconditionRequiredError,
    UnprocessableEntityError,
)
from opal_event.models import (
    SNAPSHOT_FORMAT_VERSION,
    SNAPSHOT_MODULE,
    SNAPSHOT_RESOURCE_SECTIONS,
    Action,
    ConfigLoadRequest,
    ConfigLoadResult,
    ConfigSaveRequest,
    ConfigSaveResult,
    ConfigSnapshot,
    ConfigValidation,
    PersonaRead,
    Profile,
    Rule,
    ValidationIssue,
    _utcnow,
)
from opal_event.routers.settings import _defaults_map, _key_parts

router = APIRouter(dependencies=[Depends(require_auth)])

MANAGED_SNAPSHOT_NAME = "opal-event.snapshot.json"

_RESOURCE_MODELS = {"profiles": Profile, "personas": PersonaRead, "rules": Rule, "actions": Action}


def _managed_path(config) -> Path:
    """The module's single default managed snapshot, beside the SQLite database."""
    return Path(config.settings.persistence.database).parent / MANAGED_SNAPSHOT_NAME


def _overrides_map(config) -> dict:
    """Registered setting keys whose persisted value differs from the default."""
    raw = config.raw_at_load
    defaults = _defaults_map()
    out = {}
    for key, default in defaults.items():
        section, field = _key_parts(key)
        present = isinstance(raw.get(section), dict) and field in raw[section]
        if present and raw[section][field] != default:
            out[key] = raw[section][field]
    return out


def _build_snapshot(config) -> ConfigSnapshot:
    """Snapshot of the live persistent desired configuration (for save and for
    validating the live configuration on a bare ``config validate`` call)."""
    s = config.settings
    return ConfigSnapshot(
        created_at=_utcnow(),
        settings=_overrides_map(config),
        resources={
            "profiles": [p.model_dump(mode="json") for p in s.profiles],
            "personas": [p.model_dump(mode="json") for p in s.personas],
            "rules": [r.model_dump(mode="json") for r in s.rules],
            "actions": [a.model_dump(mode="json") for a in s.actions],
        },
    )


def _write_managed(path: Path, snapshot: ConfigSnapshot) -> None:
    """Atomic create-or-replace of the managed snapshot; a failed write preserves
    the previous snapshot (temp file + fsync + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(snapshot.model_dump(mode="json"), indent=2)
    fd, tmp = tempfile.mkstemp(prefix=".opal-event-snapshot-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise OperationFailedError(f"failed to write managed snapshot: {exc}") from exc


def _read_managed(path: Path) -> ConfigSnapshot:
    if not path.is_file():
        raise NotFoundError("no managed snapshot exists; save one first or supply a candidate snapshot")
    try:
        return ConfigSnapshot.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise OperationFailedError(f"managed snapshot is unreadable: {exc}") from exc


def _validate_snapshot(snapshot: ConfigSnapshot) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    if snapshot.module != SNAPSHOT_MODULE:
        errors.append(
            ValidationIssue(
                code="wrong_module", path="module", message=f"snapshot is for module '{snapshot.module}', not '{SNAPSHOT_MODULE}'"
            )
        )
    if snapshot.format_version != SNAPSHOT_FORMAT_VERSION:
        errors.append(
            ValidationIssue(
                code="unsupported_format",
                path="format_version",
                message=f"format_version '{snapshot.format_version}' is not accepted (want '{SNAPSHOT_FORMAT_VERSION}')",
            )
        )
    defaults = _defaults_map()
    for key in snapshot.settings:
        if key not in defaults:
            errors.append(ValidationIssue(code="unknown_setting", path=f"settings.{key}", message=f"unknown setting key '{key}'"))
    for section in snapshot.resources:
        if section not in SNAPSHOT_RESOURCE_SECTIONS:
            errors.append(
                ValidationIssue(
                    code="unknown_section", path=f"resources.{section}", message=f"unknown resource section '{section}'"
                )
            )
    parsed: dict[str, list] = {}
    for section, model in _RESOURCE_MODELS.items():
        items = snapshot.resources.get(section, [])
        if not isinstance(items, list):
            errors.append(
                ValidationIssue(code="invalid_section", path=f"resources.{section}", message="section must be an array")
            )
            continue
        parsed[section] = []
        adapter = TypeAdapter(model)
        for i, item in enumerate(items):
            try:
                parsed[section].append(adapter.validate_python(item))
            except ValidationError as exc:
                first = exc.errors()[0] if exc.errors() else {}
                errors.append(
                    ValidationIssue(
                        code="invalid_resource",
                        path=f"resources.{section}[{i}]",
                        message=f"{first.get('loc', '')}: {first.get('msg', 'validation failed')}",
                    )
                )
    if errors:
        return errors, warnings

    # Referential checks on the parsed candidate (same policy the old backup validator used).
    action_ids: set[str] = set()
    for i, action in enumerate(parsed["actions"]):
        if action.id in action_ids:
            errors.append(
                ValidationIssue(
                    code="duplicate_id", path=f"resources.actions[{i}].id", message=f"duplicate action id: {action.id}"
                )
            )
        action_ids.add(action.id)
    rule_ids: set[str] = set()
    for i, rule in enumerate(parsed["rules"]):
        if rule.id in rule_ids:
            errors.append(
                ValidationIssue(code="duplicate_id", path=f"resources.rules[{i}].id", message=f"duplicate rule id: {rule.id}")
            )
        rule_ids.add(rule.id)
        for action_id in rule.actions:
            if action_id not in action_ids:
                warnings.append(
                    ValidationIssue(
                        code="dangling_reference",
                        path=f"resources.rules[{i}]",
                        message=f"rule '{rule.id}' references unknown action '{action_id}'",
                    )
                )
    persona_ids: set[str] = set()
    for i, persona in enumerate(parsed["personas"]):
        if persona.persona_id in persona_ids:
            errors.append(
                ValidationIssue(
                    code="duplicate_id",
                    path=f"resources.personas[{i}].persona_id",
                    message=f"duplicate persona id: {persona.persona_id}",
                )
            )
        persona_ids.add(persona.persona_id)
    profile_ids: set[int] = set()
    profile_names: set[str] = set()
    for i, profile in enumerate(parsed["profiles"]):
        if profile.id in profile_ids:
            errors.append(
                ValidationIssue(
                    code="duplicate_id", path=f"resources.profiles[{i}].id", message=f"duplicate profile id: {profile.id}"
                )
            )
        if profile.name in profile_names:
            errors.append(
                ValidationIssue(
                    code="duplicate_id", path=f"resources.profiles[{i}].name", message=f"duplicate profile name: {profile.name}"
                )
            )
        profile_ids.add(profile.id)
        profile_names.add(profile.name)
    return errors, warnings


def _apply_snapshot(snapshot: ConfigSnapshot):
    """Atomic full replace: registered settings revert to defaults unless overridden by the
    snapshot; omitted resource sections become empty. ``config.update`` re-validates the
    resulting ``Settings`` before persisting, so a bad candidate never touches the file."""
    defaults = _defaults_map()

    def mutate(raw: dict) -> dict:
        raw = dict(raw)
        for key, default in defaults.items():
            section, field = _key_parts(key)
            sect = dict(raw.get(section) or {})
            sect[field] = snapshot.settings.get(key, default)
            raw[section] = sect
        for section in SNAPSHOT_RESOURCE_SECTIONS:
            raw[section] = list(snapshot.resources.get(section, []))
        return raw

    return mutate


def _explicit_null(body: ConfigLoadRequest | None) -> bool:
    return body is not None and "snapshot" in body.model_fields_set and body.snapshot is None


@router.post("/api/v1/config/save", response_model=ConfigSaveResult)
def config_save(response: Response, body: ConfigSaveRequest | None = Body(default=None), config=Depends(get_config)):
    """Kernel ``opal_config_save``. ``destination: managed`` atomically creates or replaces the
    default managed snapshot; ``response`` only returns the snapshot (CLI ``--out``). Save does
    not mutate live config; the response ETag carries the captured live revision."""
    destination = (body or ConfigSaveRequest()).destination
    snapshot = _build_snapshot(config)
    managed = destination == "managed"
    if managed:
        _write_managed(_managed_path(config), snapshot)
    response.headers["ETag"] = config.revision
    return ConfigSaveResult(snapshot=snapshot, managed=managed)


@router.post("/api/v1/config/load", response_model=ConfigLoadResult)
def config_load(
    response: Response,
    body: ConfigLoadRequest | None = Body(default=None),
    config=Depends(get_config),
    if_match: str | None = Header(default=None),
):
    """Kernel ``opal_config_load``: atomic replace of the complete portable configuration.
    Omit ``snapshot`` (or send ``{}``) to load the managed snapshot — 404 when none exists;
    an explicit ``null`` snapshot is invalid. Strong ``If-Match`` is mandatory (428/412)."""
    if if_match is None:
        raise PreconditionRequiredError("If-Match header is required for this update")
    if _explicit_null(body):
        raise UnprocessableEntityError(
            "explicit null snapshot is invalid; omit the field or send {} to load the managed snapshot"
        )
    snapshot = body.snapshot if body and body.snapshot is not None else _read_managed(_managed_path(config))
    errors, warnings = _validate_snapshot(snapshot)
    if errors:
        raise UnprocessableEntityError(
            "snapshot failed validation",
            {"errors": [e.model_dump(mode="json") for e in errors], "warnings": [w.model_dump(mode="json") for w in warnings]},
        )
    try:
        config.update(_apply_snapshot(snapshot), expected_revision=if_match)
    except ConcurrentUpdateError as exc:
        raise PreconditionFailedError(str(exc)) from exc
    response.headers["ETag"] = config.revision
    return ConfigLoadResult(applied=True, pending_restart_fields=config.pending_restart_fields, warnings=warnings)


@router.post("/api/v1/config/validate", response_model=ConfigValidation)
def config_validate(body: ConfigLoadRequest | None = Body(default=None), config=Depends(get_config)):
    """Kernel ``opal_config_validate``: non-mutating. Omit ``snapshot`` to validate the current
    live configuration; the managed snapshot is never read implicitly."""
    if _explicit_null(body):
        raise UnprocessableEntityError("explicit null snapshot is invalid; omit the field to validate the live configuration")
    snapshot = body.snapshot if body and body.snapshot is not None else _build_snapshot(config)
    errors, warnings = _validate_snapshot(snapshot)
    return ConfigValidation(valid=not errors, errors=errors, warnings=warnings)
