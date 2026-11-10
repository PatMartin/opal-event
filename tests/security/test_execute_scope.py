"""Security tests: execute-scoped bearer tokens may only list/show actions,
execute actions, and read executions — administration is denied with 403."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from opal_event.app import create_app
from tests.conftest import base_config, write_config

_SECURITY = {
    "rest_auth": {
        "enabled": True,
        "token_env": "OPAL_EVENT_API_TOKEN",
        "execute_token_env": "OPAL_EVENT_EXECUTE_TOKEN",
    },
    "http_action_allowlist": [],
}
_ADMIN = {"Authorization": "Bearer admin-token"}
_EXEC = {"Authorization": "Bearer exec-token"}


@pytest.fixture
def scoped_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("OPAL_EVENT_API_TOKEN", "admin-token")
    monkeypatch.setenv("OPAL_EVENT_EXECUTE_TOKEN", "exec-token")
    path = write_config(tmp_path, base_config(tmp_path, security=_SECURITY))
    return TestClient(create_app(str(path)))


class TestExecuteScopeAllowed:
    def test_list_and_show_actions(self, scoped_client) -> None:
        with scoped_client as c:
            assert c.get("/api/v1/action/", headers=_EXEC).status_code == 200
            assert c.get("/api/v1/action/echo_action", headers=_EXEC).status_code == 200

    def test_list_topics(self, scoped_client) -> None:
        # actions:read — "Discover registered actions and topics." (v1.7.1 x-opal-authorization)
        with scoped_client as c:
            assert c.get("/api/v1/topic/", headers=_EXEC).status_code == 200

    def test_execute_action(self, scoped_client) -> None:
        with scoped_client as c:
            resp = c.post(
                "/api/v1/action/echo_action/execute",
                json={"event": {"topic": "t", "data": {"camera": "front"}}},
                headers=_EXEC,
            )
            assert resp.status_code == 202, resp.text

    def test_read_executions(self, scoped_client) -> None:
        with scoped_client as c:
            assert c.get("/api/v1/execution/", headers=_EXEC).status_code == 200
            resp = c.post(
                "/api/v1/action/echo_action/execute",
                json={"event": {"topic": "t", "data": {}}},
                headers=_EXEC,
            )
            execution_id = resp.json()["id"]
            assert c.get(f"/api/v1/execution/{execution_id}", headers=_EXEC).status_code == 200


class TestExecuteScopeDenied:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("post", "/api/v1/action/"),
            ("patch", "/api/v1/action/echo_action"),
            ("delete", "/api/v1/action/echo_action"),
            ("get", "/api/v1/rule/"),
            ("post", "/api/v1/rule/"),
            ("post", "/api/v1/rule/front_door_alert/test"),
            ("get", "/api/v1/setting/"),
            ("patch", "/api/v1/setting/"),
            ("get", "/api/v1/backup/export"),
            ("post", "/api/v1/backup/import"),
            ("post", "/api/v1/backup/validate"),
            ("post", "/api/v1/event/"),
            ("get", "/api/v1/event/"),
            ("delete", "/api/v1/event/"),
            ("get", "/api/v1/stats/"),
        ],
    )
    def test_admin_operations_forbidden(self, scoped_client, method, path) -> None:
        with scoped_client as c:
            resp = c.request(method, path, headers=_EXEC, json={})
            assert resp.status_code == 403, f"{method.upper()} {path} -> {resp.status_code}"


class TestAdminTokenUnchanged:
    def test_admin_still_administers(self, scoped_client) -> None:
        with scoped_client as c:
            assert c.get("/api/v1/rule/", headers=_ADMIN).status_code == 200
            etag = c.get("/api/v1/action/", headers=_ADMIN).headers["etag"]
            created = c.post(
                "/api/v1/action/",
                json={"id": "adm", "type": "shell", "enabled": True, "mode": "exec", "executable": "/bin/echo"},
                headers={**_ADMIN, "If-Match": etag},
            )
            assert created.status_code in (200, 201)
            assert c.get("/api/v1/setting/", headers=_ADMIN).status_code == 200

    def test_admin_can_also_use_execute_surface(self, scoped_client) -> None:
        with scoped_client as c:
            assert c.get("/api/v1/action/", headers=_ADMIN).status_code == 200


class TestScopeMisconfiguration:
    def test_no_execute_env_configured_rejects_execute_token(self, tmp_path, monkeypatch) -> None:
        """Without execute_token_env configured there is only the admin credential."""
        monkeypatch.setenv("OPAL_EVENT_API_TOKEN", "admin-token")
        monkeypatch.setenv("OPAL_EVENT_EXECUTE_TOKEN", "exec-token")
        cfg = {
            "rest_auth": {"enabled": True, "token_env": "OPAL_EVENT_API_TOKEN"},
            "http_action_allowlist": [],
        }
        path = write_config(tmp_path, base_config(tmp_path, security=cfg))
        with TestClient(create_app(str(path))) as c:
            assert c.get("/api/v1/action/", headers=_EXEC).status_code == 401

    def test_execute_env_set_but_var_missing(self, scoped_client, monkeypatch) -> None:
        """Configured execute_token_env with unset env var -> execute tokens cannot match."""
        monkeypatch.delenv("OPAL_EVENT_EXECUTE_TOKEN", raising=False)
        with scoped_client as c:
            assert c.get("/api/v1/action/", headers=_EXEC).status_code == 401
            assert c.get("/api/v1/action/", headers=_ADMIN).status_code == 200

    def test_health_endpoints_remain_open(self, scoped_client) -> None:
        with scoped_client as c:
            assert c.get("/api/v1/health/live").status_code == 200


class TestDiscoveryProjection:
    """Execute scope sees discovery metadata only — no operational detail."""

    def test_list_projection_hides_operational_fields(self, scoped_client) -> None:
        with scoped_client as c:
            # admin registers one of each action type with rich detail
            for action in (
                {"id": "sh", "type": "shell", "enabled": True, "mode": "exec", "executable": "/opt/x"},
                {
                    "id": "ht",
                    "type": "http",
                    "enabled": True,
                    "method": "POST",
                    "url": "https://internal.local/x",
                    "body_mode": "none",
                    "headers": {"x-api-key": "s"},
                },
                {"id": "py", "type": "python", "enabled": True, "module": "opal_plugins.m", "function": "run"},
            ):
                action["description"] = "d"
                etag = c.get("/api/v1/action/", headers=_ADMIN).headers["etag"]
                assert c.post("/api/v1/action/", json=action, headers={**_ADMIN, "If-Match": etag}).status_code in (200, 201)
            items = {a["id"]: a for a in c.get("/api/v1/action/", headers=_EXEC).json()}
            for entry in items.values():
                assert set(entry) == {"id", "type", "enabled", "description", "input_schema", "keywords", "suggested_prompts"}
            admin_items = {a["id"]: a for a in c.get("/api/v1/action/", headers=_ADMIN).json()}
            assert admin_items["sh"]["executable"] == "/opt/x"  # admin still sees full definitions
            assert admin_items["ht"]["headers"] == {"x-api-key": "s"}

    def test_show_projection_hides_operational_fields(self, scoped_client) -> None:
        with scoped_client as c:
            shown = c.get("/api/v1/action/echo_action", headers=_EXEC)
            assert shown.status_code == 200
            body = shown.json()
            assert "executable" not in body and "args" not in body and "stdin" not in body
            assert body["id"] == "echo_action" and body["type"] == "shell"


class TestExecuteScopeEdgeCases:
    def test_disabled_action_execute_rejected(self, scoped_client, tmp_path, monkeypatch) -> None:
        with scoped_client as c:
            etag = c.get("/api/v1/action/", headers=_ADMIN).headers["etag"]
            assert c.post(
                "/api/v1/action/",
                json={"id": "off", "type": "shell", "enabled": False, "mode": "exec", "executable": "/x"},
                headers={**_ADMIN, "If-Match": etag},
            ).status_code in (200, 201)
            resp = c.post("/api/v1/action/off/execute", json={"event": {"topic": "t", "data": {}}}, headers=_EXEC)
            assert resp.status_code == 422  # allowed route, denied by action state

    def test_missing_and_invalid_bearer(self, scoped_client) -> None:
        with scoped_client as c:
            assert c.get("/api/v1/action/").status_code == 401
            assert c.get("/api/v1/action/", headers={"Authorization": "Bearer wrong"}).status_code == 401

    def test_execute_cannot_escalate_via_projection(self, scoped_client) -> None:
        """No execute-scope route returns fields that could carry secret material."""
        with scoped_client as c:
            for path in ("/api/v1/action/", "/api/v1/action/echo_action"):
                body = c.get(path, headers=_EXEC).text
                for hint in ("executable", "script_path", "url", "headers", "module", "token", "secret"):
                    assert f'"{hint}"' not in body, f"{hint} leaked in {path}"
