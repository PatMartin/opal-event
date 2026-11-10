"""HTTP client used by the CLI. All CLI functionality goes through the REST API."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx

DEFAULT_API_URL = "http://127.0.0.1:8080"

# operationId -> (method, path template). Must match opal_event_v1.7.1.yaml (verified by contract tests).
OPERATIONS: dict[str, tuple[str, str]] = {
    "post_api_v1_events": ("POST", "/api/v1/event/"),
    "get_api_v1_events": ("GET", "/api/v1/event/"),
    "delete_api_v1_events": ("DELETE", "/api/v1/event/"),
    "get_api_v1_events_id": ("GET", "/api/v1/event/{event_id}"),
    "delete_api_v1_events_id": ("DELETE", "/api/v1/event/{event_id}"),
    "get_api_v1_topics": ("GET", "/api/v1/topic/"),
    "get_api_v1_rules": ("GET", "/api/v1/rule/"),
    "post_api_v1_rules": ("POST", "/api/v1/rule/"),
    "get_api_v1_rules_id": ("GET", "/api/v1/rule/{rule_id}"),
    "delete_api_v1_rules_id": ("DELETE", "/api/v1/rule/{rule_id}"),
    "patch_api_v1_rules_rule_id": ("PATCH", "/api/v1/rule/{rule_id}"),
    "post_api_v1_rules_id_test": ("POST", "/api/v1/rule/{rule_id}/test"),
    "get_api_v1_actions": ("GET", "/api/v1/action/"),
    "post_api_v1_actions": ("POST", "/api/v1/action/"),
    "get_api_v1_actions_id": ("GET", "/api/v1/action/{action_id}"),
    "delete_api_v1_actions_id": ("DELETE", "/api/v1/action/{action_id}"),
    "patch_api_v1_actions_action_id": ("PATCH", "/api/v1/action/{action_id}"),
    "post_api_v1_actions_id_execute": ("POST", "/api/v1/action/{action_id}/execute"),
    "get_api_v1_executions": ("GET", "/api/v1/execution/"),
    "get_api_v1_executions_id": ("GET", "/api/v1/execution/{execution_id}"),
    "opal_health_get": ("GET", "/api/v1/health/"),
    "get_api_v1_stats": ("GET", "/api/v1/stats/"),
    "opal_setting_list": ("GET", "/api/v1/setting/"),
    "opal_setting_patch": ("PATCH", "/api/v1/setting/"),
    "opal_setting_get": ("GET", "/api/v1/setting/{key}"),
    "opal_setting_put": ("PUT", "/api/v1/setting/{key}"),
    "opal_setting_delete": ("DELETE", "/api/v1/setting/{key}"),
    "opal_info_get": ("GET", "/api/v1/info/"),
    "opal_info_ping": ("GET", "/api/v1/info/ping"),
    "opal_health_live_get": ("GET", "/api/v1/health/live"),
    "opal_health_ready_get": ("GET", "/api/v1/health/ready"),
    "opal_backup_export": ("GET", "/api/v1/backup/export"),
    "opal_backup_validate_post": ("POST", "/api/v1/backup/validate"),
    "opal_backup_import": ("POST", "/api/v1/backup/import"),
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
    "opal_backup_list": ("GET", "/api/v1/backup/list"),
    "opal_backup_create": ("POST", "/api/v1/backup/create"),
    "opal_backup_download": ("GET", "/api/v1/backup/download/{filename}"),
    "opal_backup_restore": ("POST", "/api/v1/backup/restore/{filename}"),
    "opal_backup_delete": ("DELETE", "/api/v1/backup/{filename}"),
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
