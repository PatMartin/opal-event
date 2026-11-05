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

# Operations an execute-scoped token may perform — THINK's minimal surface.
# Fail-closed: anything not listed is denied for the execute scope.
_EXECUTE_ALLOWED: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/v1/actions/"),
        ("GET", "/api/v1/actions/{action_id}"),
        ("POST", "/api/v1/actions/{action_id}/execute"),
        ("GET", "/api/v1/executions/"),
        ("GET", "/api/v1/executions/{execution_id}"),
    }
)


def _route_path(request: Request) -> str | None:
    route = request.scope.get("route")
    return getattr(route, "path", None)


# Discovery metadata an execute-scoped caller may see — the self-description
# surface THINK consumes, never operational detail (paths, URLs, headers,
# modules, args) or secret references.
_DISCOVERY_FIELDS = ("id", "type", "enabled", "description", "input_schema", "keywords", "suggested_prompts")


def is_execute_scope(request: Request) -> bool:
    return getattr(request.state, "scope", None) == "execute"


def discovery_projection(action) -> dict:
    """Execute-scope view of an action definition: discovery metadata only."""
    return {field: getattr(action, field, None) for field in _DISCOVERY_FIELDS}


def require_auth(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme)) -> None:
    config: ConfigStore = request.app.state.config
    auth_settings = config.settings.security.rest_auth
    if not auth_settings.enabled:
        request.state.scope = "admin"
        return
    expected = os.environ.get(auth_settings.token_env)
    if not expected:
        raise HTTPException(status_code=503, detail="REST auth is enabled but token_env is unset")
    scope: str | None = None
    if credentials is not None:
        if hmac.compare_digest(credentials.credentials, expected):
            scope = "admin"
        elif execute_env := auth_settings.execute_token_env:
            execute_token = os.environ.get(execute_env)
            if execute_token and hmac.compare_digest(credentials.credentials, execute_token):
                scope = "execute"
    if scope is None:
        raise HTTPException(status_code=401, detail="missing/invalid bearer token")
    request.state.scope = scope
    if scope == "execute" and (request.method, _route_path(request)) not in _EXECUTE_ALLOWED:
        raise HTTPException(status_code=403, detail="execute-scoped token may not perform this operation")
