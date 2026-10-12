"""FastAPI dependency accessors for the shared runtime objects stored on ``app.state``."""

from __future__ import annotations

from fastapi import Request

from opal_event.config import ConfigStore
from opal_event.service import EventService


def get_service(request: Request) -> EventService:
    return request.app.state.service


def get_config(request: Request) -> ConfigStore:
    return request.app.state.config
