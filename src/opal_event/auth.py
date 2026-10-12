"""Bearer-token REST authentication, enabled per ``security.rest_auth`` in the YAML config.

Per policy: on loopback, auth can be disabled by configuration; when bound to a non-loopback
interface, auth must be enabled (enforced at startup in :mod:`opal_event.server`, not here).
"""

from __future__ import annotations

import hmac
import os

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from opal_event.config import ConfigStore

_bearer_scheme = HTTPBearer(auto_error=False)


def require_auth(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme)) -> None:
    config: ConfigStore = request.app.state.config
    auth_settings = config.settings.security.rest_auth
    if not auth_settings.enabled:
        return
    expected = os.environ.get(auth_settings.token_env)
    if not expected:
        raise HTTPException(status_code=503, detail="REST auth is enabled but token_env is unset")
    if credentials is None or not hmac.compare_digest(credentials.credentials, expected):
        raise HTTPException(status_code=401, detail="missing/invalid bearer token")
