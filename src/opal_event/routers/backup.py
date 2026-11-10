"""``/api/v1/backup``: portable config export/validate/import plus service-managed
``.tar.gz`` archives (list/create/download/restore/delete). Host-bound endpoints,
secrets, and the SQLite path are intentionally excluded from every bundle."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Header, Response
from fastapi.responses import FileResponse

from opal_event.auth import require_auth
from opal_event.backups import backup_dir, backup_path, create_backup, list_backups, read_bundle
from opal_event.config import ConcurrentUpdateError
from opal_event.deps import get_config
from opal_event.errors import (
    ConflictError,
    OperationFailedError,
    PreconditionFailedError,
    PreconditionRequiredError,
)
from opal_event.models import (
    BackupCreateResponse,
    BackupImportResult,
    BackupInfo,
    ConfigBackup,
    PortablePersistenceSettings,
    PortableSettings,
    ValidationReport,
    _utcnow,
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


def _bundle(settings) -> ConfigBackup:
    return ConfigBackup(
        format_version=1,
        exported_at=_utcnow(),
        settings=_portable_view(settings),
        actions=settings.actions,
        rules=settings.rules,
        personas=settings.personas,
        profiles=settings.profiles,
    )


def _archive_dir(config) -> object:
    return backup_dir(config.settings.persistence.database)


def _validate_backup(backup: ConfigBackup) -> ValidationReport:
    errors: list[str] = []
    warnings: list[str] = []
    action_ids = [a.id for a in backup.actions]
    seen_action_ids: set[str] = set()
    for action_id in action_ids:
        if action_id in seen_action_ids:
            errors.append(f"duplicate action id: {action_id}")
        seen_action_ids.add(action_id)

    rule_ids = [r.id for r in backup.rules]
    seen_rule_ids: set[str] = set()
    for rule_id in rule_ids:
        if rule_id in seen_rule_ids:
            errors.append(f"duplicate rule id: {rule_id}")
        seen_rule_ids.add(rule_id)

    for rule in backup.rules:
        for action_id in rule.actions:
            if action_id not in seen_action_ids:
                warnings.append(f"rule '{rule.id}' references unknown action '{action_id}'")

    seen_persona_ids: set[str] = set()
    for persona in backup.personas:
        if persona.persona_id in seen_persona_ids:
            errors.append(f"duplicate persona id: {persona.persona_id}")
        seen_persona_ids.add(persona.persona_id)

    seen_profile_ids: set[int] = set()
    seen_profile_names: set[str] = set()
    for profile in backup.profiles:
        if profile.id in seen_profile_ids:
            errors.append(f"duplicate profile id: {profile.id}")
        seen_profile_ids.add(profile.id)
        if profile.name in seen_profile_names:
            errors.append(f"duplicate profile name: {profile.name}")
        seen_profile_names.add(profile.name)

    return ValidationReport(valid=not errors, errors=errors, warnings=warnings)


def _apply_bundle(backup: ConfigBackup):
    """The mutate shared by import and archive restore: replace the portable sections."""

    def mutate(raw: dict) -> dict:
        raw = dict(raw)
        portable = backup.settings
        raw["execution"] = portable.execution.model_dump(mode="json")
        raw["persistence"] = {**raw.get("persistence", {}), **portable.persistence.model_dump(mode="json")}
        raw["zeromq"] = {**raw.get("zeromq", {}), "max_event_bytes": portable.max_event_bytes}
        raw["actions"] = [a.model_dump(mode="json") for a in backup.actions]
        raw["rules"] = [r.model_dump(mode="json") for r in backup.rules]
        raw["personas"] = [p.model_dump(mode="json") for p in backup.personas]
        raw["profiles"] = [p.model_dump(mode="json") for p in backup.profiles]
        return raw

    return mutate


# -- logical config bundle -----------------------------------------------------


@router.get("/api/v1/backup/export", response_model=ConfigBackup)
def export_backup(config=Depends(get_config)):
    return _bundle(config.settings)


@router.post("/api/v1/backup/validate", response_model=ValidationReport)
def validate_backup(backup: ConfigBackup):
    return _validate_backup(backup)


@router.post("/api/v1/backup/import", response_model=BackupImportResult)
def import_backup(
    config=Depends(get_config),
    backup: ConfigBackup | None = Body(default=None),
    if_match: str | None = Header(default=None),
):
    """Atomically import a config bundle — the bare ``OpalConfigExport`` body (v1.7.1;
    the ``{"backup": ...}`` wrapper is gone). An empty body re-validates and applies
    the on-disk ``./config/`` YAML, equivalent to a filesystem reload. Strong
    ``If-Match`` is mandatory: 428 missing, 412 stale."""
    if if_match is None:
        raise PreconditionRequiredError()
    if backup is None:
        # Re-validate + apply ./config/ under the caller's precondition.
        try:
            config.update(lambda raw: raw, expected_revision=if_match)
        except ConcurrentUpdateError as exc:
            raise PreconditionFailedError(str(exc)) from exc
        return BackupImportResult(revision=config.revision, pending_restart=config.pending_restart)
    report = _validate_backup(backup)
    if not report.valid:
        raise ConflictError(f"backup failed validation: {'; '.join(report.errors)}")
    try:
        config.update(_apply_bundle(backup), expected_revision=if_match)
    except ConcurrentUpdateError as exc:
        raise PreconditionFailedError(str(exc)) from exc
    return BackupImportResult(revision=config.revision, pending_restart=config.pending_restart)


# -- service-managed archives ----------------------------------------------------


@router.get("/api/v1/backup/list", response_model=list[BackupInfo])
def list_backup_archives(config=Depends(get_config)):
    return list_backups(_archive_dir(config))


@router.post("/api/v1/backup/create", response_model=BackupCreateResponse, status_code=201)
def create_backup_archive(response: Response, config=Depends(get_config)):
    try:
        filename = create_backup(_archive_dir(config), _bundle(config.settings).model_dump(mode="json"))
    except OSError as exc:
        raise OperationFailedError(f"failed to create backup: {exc}") from exc
    response.headers["Location"] = f"/api/v1/backup/download/{filename}"
    return BackupCreateResponse(filename=filename, message="Backup created successfully.")


@router.get("/api/v1/backup/download/{filename}")
def download_backup(filename: str, config=Depends(get_config)):
    return FileResponse(backup_path(_archive_dir(config), filename), media_type="application/octet-stream", filename=filename)


@router.post("/api/v1/backup/restore/{filename}", response_model=BackupImportResult)
def restore_backup(filename: str, config=Depends(get_config), if_match: str | None = Header(default=None)):
    """Validate an archive's bundle then apply it atomically, like ``/backup/import``."""
    if if_match is None:
        raise PreconditionRequiredError()
    bundle = read_bundle(_archive_dir(config), filename)
    try:
        backup = ConfigBackup.model_validate(bundle)
    except Exception as exc:
        raise ConflictError(f"backup '{filename}' is not a valid config bundle: {exc}") from exc
    report = _validate_backup(backup)
    if not report.valid:
        raise ConflictError(f"backup '{filename}' failed validation: {'; '.join(report.errors)}")
    try:
        config.update(_apply_bundle(backup), expected_revision=if_match)
    except ConcurrentUpdateError as exc:
        raise PreconditionFailedError(str(exc)) from exc
    return BackupImportResult(revision=config.revision, pending_restart=config.pending_restart)


# Registered after the literal paths so /backup/list|create|download|restore|export|... win.
@router.delete("/api/v1/backup/{filename}", status_code=204)
def delete_backup(filename: str, config=Depends(get_config)):
    backup_path(_archive_dir(config), filename).unlink()
    return Response(status_code=204)
