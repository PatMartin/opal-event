"""HTTP client used by the CLI. All CLI functionality goes through the REST API."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx

DEFAULT_API_URL = "http://127.0.0.1:8080"

# operationId -> (method, path template). Must match opal_event.yaml (verified by contract tests).
OPERATIONS: dict[str, tuple[str, str]] = {
    "post_api_v1_events": ("POST", "/api/v1/events/"),
    "get_api_v1_events": ("GET", "/api/v1/events/"),
    "delete_api_v1_events": ("DELETE", "/api/v1/events/"),
    "get_api_v1_events_id": ("GET", "/api/v1/events/{event_id}"),
    "delete_api_v1_events_id": ("DELETE", "/api/v1/events/{event_id}"),
    "get_api_v1_topics": ("GET", "/api/v1/topics/"),
    "get_api_v1_rules": ("GET", "/api/v1/rules/"),
    "post_api_v1_rules": ("POST", "/api/v1/rules/"),
    "get_api_v1_rules_id": ("GET", "/api/v1/rules/{rule_id}"),
    "delete_api_v1_rules_id": ("DELETE", "/api/v1/rules/{rule_id}"),
    "patch_api_v1_rules_rule_id": ("PATCH", "/api/v1/rules/{rule_id}"),
    "post_api_v1_rules_id_test": ("POST", "/api/v1/rules/{rule_id}/test"),
    "get_api_v1_actions": ("GET", "/api/v1/actions/"),
    "post_api_v1_actions": ("POST", "/api/v1/actions/"),
    "get_api_v1_actions_id": ("GET", "/api/v1/actions/{action_id}"),
    "delete_api_v1_actions_id": ("DELETE", "/api/v1/actions/{action_id}"),
    "patch_api_v1_actions_action_id": ("PATCH", "/api/v1/actions/{action_id}"),
    "post_api_v1_actions_id_execute": ("POST", "/api/v1/actions/{action_id}/execute"),
    "get_api_v1_executions": ("GET", "/api/v1/executions/"),
    "get_api_v1_executions_id": ("GET", "/api/v1/executions/{execution_id}"),
    "get_api_v1_health": ("GET", "/api/v1/health/"),
    "get_api_v1_stats": ("GET", "/api/v1/stats/"),
    "get_api_v1_settings": ("GET", "/api/v1/settings/"),
    "patch_api_v1_settings": ("PATCH", "/api/v1/settings/"),
    "get_api_v1_health_live": ("GET", "/api/v1/health/live"),
    "get_api_v1_health_ready": ("GET", "/api/v1/health/ready"),
    "get_api_v1_backup_export": ("GET", "/api/v1/backup/export"),
    "post_api_v1_backup_validate": ("POST", "/api/v1/backup/validate"),
    "post_api_v1_backup_import": ("POST", "/api/v1/backup/import"),
}


def api_url_from_env() -> str:
    return os.environ.get("OPAL_EVENT_API_URL", DEFAULT_API_URL)


def token_from_env() -> str | None:
    return os.environ.get("OPAL_EVENT_API_TOKEN")


class ApiCallError(Exception):
    def __init__(self, status: int, body: Any):
        self.status = status
        self.body = body
        super().__init__(self.describe())

    def describe(self) -> str:
        body = self.body
        if isinstance(body, dict) and "code" in body:
            return f"HTTP {self.status} {body['code']}: {body.get('message', '')}"
        if isinstance(body, dict) and isinstance(body.get("detail"), list):
            parts = [f"{'.'.join(map(str, d.get('loc', [])))}: {d.get('msg')}" for d in body["detail"]]
            return f"HTTP {self.status} validation error: " + "; ".join(parts)
        return f"HTTP {self.status}: {body}"


class ApiClient:
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 30.0, http: httpx.Client | None = None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.http = http or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, headers=headers)

    @staticmethod
    def _request_args(op: str, path_params: dict[str, Any] | None) -> tuple[str, str]:
        method, template = OPERATIONS[op]
        params = {k: quote(str(v), safe="") for k, v in (path_params or {}).items()}
        return method, template.format(**params)

    @staticmethod
    def _raise_for(resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            resp.read()
            try:
                body = resp.json()
            except ValueError:
                body = resp.text
            raise ApiCallError(resp.status_code, body)

    def call(
        self,
        op: str,
        path_params: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        method, path = self._request_args(op, path_params)
        resp = self.http.request(method, path, params=params, json=json, headers=headers)
        self._raise_for(resp)
        return resp

    def close(self) -> None:
        self.http.close()
