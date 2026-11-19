"""HTTP client used by the CLI. All CLI functionality goes through the REST API."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx

DEFAULT_API_URL = "http://127.0.0.1:8080"

# operationId -> (method, path template). Must match opal_event_v0.1.0.yaml (verified by contract tests).
OPERATIONS: dict[str, tuple[str, str]] = {
    "publish_event": ("POST", "/api/v1/event/"),
    "list_events": ("GET", "/api/v1/event/"),
    "clear_events": ("DELETE", "/api/v1/event/"),
    "show_event": ("GET", "/api/v1/event/{event_id}"),
    "remove_event": ("DELETE", "/api/v1/event/{event_id}"),
    "list_topics": ("GET", "/api/v1/topic/"),
    "list_rules": ("GET", "/api/v1/rule/"),
    "create_rule": ("POST", "/api/v1/rule/"),
    "show_rule": ("GET", "/api/v1/rule/{rule_id}"),
    "remove_rule": ("DELETE", "/api/v1/rule/{rule_id}"),
    "update_rule": ("PATCH", "/api/v1/rule/{rule_id}"),
    "test_rule": ("POST", "/api/v1/rule/{rule_id}/test"),
    "list_actions": ("GET", "/api/v1/action/"),
    "create_action": ("POST", "/api/v1/action/"),
    "show_action": ("GET", "/api/v1/action/{action_id}"),
    "remove_action": ("DELETE", "/api/v1/action/{action_id}"),
    "update_action": ("PATCH", "/api/v1/action/{action_id}"),
    "execute_action": ("POST", "/api/v1/action/{action_id}/execute"),
    "list_executions": ("GET", "/api/v1/execution/"),
    "show_execution": ("GET", "/api/v1/execution/{execution_id}"),
    "opal_health_get": ("GET", "/api/v1/health/"),
    "opal_setting_list": ("GET", "/api/v1/setting/"),
    "opal_setting_get": ("GET", "/api/v1/setting/{key}"),
    "opal_setting_put": ("PUT", "/api/v1/setting/{key}"),
    "opal_setting_delete": ("DELETE", "/api/v1/setting/{key}"),
    "opal_health_live_get": ("GET", "/api/v1/health/live"),
    "opal_health_ready_get": ("GET", "/api/v1/health/ready"),
    "opal_config_save": ("POST", "/api/v1/config/save"),
    "opal_config_load": ("POST", "/api/v1/config/load"),
    "opal_config_validate": ("POST", "/api/v1/config/validate"),
    "opal_persona_list": ("GET", "/api/v1/persona/"),
    "opal_persona_create": ("POST", "/api/v1/persona/"),
    "opal_persona_validate": ("POST", "/api/v1/persona/{persona_id}/validate"),
    "opal_persona_get": ("GET", "/api/v1/persona/{persona_id}"),
    "opal_persona_patch": ("PATCH", "/api/v1/persona/{persona_id}"),
    "opal_persona_delete": ("DELETE", "/api/v1/persona/{persona_id}"),
    "opal_profile_list": ("GET", "/api/v1/profile/"),
    "opal_profile_create": ("POST", "/api/v1/profile/"),
    "opal_profile_get": ("GET", "/api/v1/profile/{profile_id}"),
    "opal_profile_patch": ("PATCH", "/api/v1/profile/{profile_id}"),
    "opal_profile_delete": ("DELETE", "/api/v1/profile/{profile_id}"),
    "opal_profile_activate": ("POST", "/api/v1/profile/{profile_id}/activate"),
    "opal_service_start": ("POST", "/api/v1/service/start"),
    "opal_service_stop": ("POST", "/api/v1/service/stop"),
    "opal_service_log": ("GET", "/api/v1/service/log"),
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
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict) and "code" in err:  # OpalError envelope
            return f"HTTP {self.status} {err['code']}: {err.get('message', '')}"
        if isinstance(body, dict) and "code" in body:  # legacy flat shape
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
