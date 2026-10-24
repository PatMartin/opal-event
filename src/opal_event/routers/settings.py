"""``/api/v1/settings``: portable, non-secret live-adjustable settings (execution, persistence
subset, max_event_bytes). Network bindings, CURVE keys, bearer tokens, database path, and
rules/actions are intentionally excluded -- see ``PortableSettings`` in ``opal_event.yaml``."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Response

from opal_event.auth import require_auth
from opal_event.config import ConcurrentUpdateError
from opal_event.deps import get_config
from opal_event.errors import PreconditionFailedError, PreconditionRequiredError
from opal_event.models import PortablePersistenceSettings, PortableSettings, PortableSettingsPatch

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


@router.get("/api/v1/settings/", response_model=PortableSettings)
def get_settings(response: Response, config=Depends(get_config)):
    response.headers["ETag"] = config.revision
    return _portable_view(config.settings)


@router.patch("/api/v1/settings/", response_model=PortableSettings)
def patch_settings(patch: PortableSettingsPatch, config=Depends(get_config), if_match: str | None = Header(default=None)):
    if if_match is None:
        raise PreconditionRequiredError("If-Match header (from GET /api/v1/settings/) is required for this update")

    def mutate(raw: dict) -> dict:
        raw = dict(raw)
        fields = patch.model_dump(mode="json", exclude_unset=True)
        if "execution" in fields:
            raw["execution"] = {**raw.get("execution", {}), **fields["execution"]}
        if "persistence" in fields:
            raw["persistence"] = {**raw.get("persistence", {}), **fields["persistence"]}
        if "max_event_bytes" in fields:
            raw["zeromq"] = {**raw.get("zeromq", {}), "max_event_bytes": fields["max_event_bytes"]}
        return raw

    try:
        new_settings = config.update(mutate, expected_revision=if_match)
    except ConcurrentUpdateError as exc:
        raise PreconditionFailedError(str(exc)) from exc
    return _portable_view(new_settings)
