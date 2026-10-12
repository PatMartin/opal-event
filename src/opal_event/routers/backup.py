"""``/api/v1/backup``: export/validate/import of the portable configuration subset (settings,
actions, rules). Host-bound endpoints, secrets, and the SQLite path are intentionally excluded."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header

from opal_event.auth import require_auth
from opal_event.config import ConcurrentUpdateError
from opal_event.deps import get_config
from opal_event.errors import ConflictError
from opal_event.models import (
    BackupImportRequest,
    BackupImportResult,
    ConfigBackup,
    PortablePersistenceSettings,
    PortableSettings,
    ValidationReport,
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

    return ValidationReport(valid=not errors, errors=errors, warnings=warnings)


@router.get("/api/v1/backup/export", response_model=ConfigBackup)
def export_backup(config=Depends(get_config)):
    settings = config.settings
    return ConfigBackup(
        format_version=1,
        settings=_portable_view(settings),
        actions=settings.actions,
        rules=settings.rules,
    )


@router.post("/api/v1/backup/validate", response_model=ValidationReport)
def validate_backup(backup: ConfigBackup):
    return _validate_backup(backup)


@router.post("/api/v1/backup/import", response_model=BackupImportResult)
def import_backup(request: BackupImportRequest, config=Depends(get_config), if_match: str = Header(...)):
    # Unlike settings/rules/actions PATCH+DELETE, opal_event.1.2.0.yaml does not add 412/428
    # responses to this operation (only 400/401/403/409/422/500 remain declared here), so a
    # missing/stale If-Match intentionally still yields FastAPI's standard required-header 422 /
    # a 409 Conflict respectively, matching this endpoint's literal, unmodified response set.
    report = _validate_backup(request.backup)
    if not report.valid:
        raise ConflictError(f"backup failed validation: {'; '.join(report.errors)}")

    def mutate(raw: dict) -> dict:
        raw = dict(raw)
        portable = request.backup.settings
        raw["execution"] = portable.execution.model_dump(mode="json")
        raw["persistence"] = {**raw.get("persistence", {}), **portable.persistence.model_dump(mode="json")}
        raw["zeromq"] = {**raw.get("zeromq", {}), "max_event_bytes": portable.max_event_bytes}
        raw["actions"] = [a.model_dump(mode="json") for a in request.backup.actions]
        raw["rules"] = [r.model_dump(mode="json") for r in request.backup.rules]
        return raw

    try:
        config.update(mutate, expected_revision=if_match)
    except ConcurrentUpdateError as exc:
        raise ConflictError(str(exc)) from exc
    return BackupImportResult(revision=config.revision, pending_restart=config.pending_restart)
