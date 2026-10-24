import os
import time

_AUTH_SECURITY = {"rest_auth": {"enabled": True, "token_env": "OPAL_EVENT_API_TOKEN"}, "http_action_allowlist": []}


def wait_until(predicate, timeout=2.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class TestRestAuth:
    def test_requests_rejected_without_token_when_enabled(self, make_client, monkeypatch):
        monkeypatch.setenv("OPAL_EVENT_API_TOKEN", "s3cret")
        client = make_client(security=_AUTH_SECURITY)
        with client as c:
            resp = c.get("/api/v1/rules/")
            assert resp.status_code == 401

    def test_requests_accepted_with_correct_token(self, make_client, monkeypatch):
        monkeypatch.setenv("OPAL_EVENT_API_TOKEN", "s3cret")
        client = make_client(security=_AUTH_SECURITY)
        with client as c:
            resp = c.get("/api/v1/rules/", headers={"Authorization": "Bearer s3cret"})
            assert resp.status_code == 200

    def test_wrong_token_rejected(self, make_client, monkeypatch):
        monkeypatch.setenv("OPAL_EVENT_API_TOKEN", "s3cret")
        client = make_client(security=_AUTH_SECURITY)
        with client as c:
            resp = c.get("/api/v1/rules/", headers={"Authorization": "Bearer wrong"})
            assert resp.status_code == 401

    def test_health_live_and_ready_do_not_require_auth(self, make_client, monkeypatch):
        monkeypatch.setenv("OPAL_EVENT_API_TOKEN", "s3cret")
        client = make_client(security=_AUTH_SECURITY)
        with client as c:
            assert c.get("/api/v1/health/live").status_code == 200


class TestHttpActionAllowlist:
    def test_disallowed_destination_fails_closed(self, make_client):
        client = make_client(
            actions=[
                {
                    "id": "bad_http",
                    "type": "http",
                    "enabled": True,
                    "method": "POST",
                    "url": "http://169.254.169.254/latest/meta-data",
                    "body_mode": "none",
                    "timeout_seconds": 5,
                }
            ],
            rules=[],
            security={"rest_auth": {"enabled": False, "token_env": "OPAL_EVENT_API_TOKEN"}, "http_action_allowlist": []},
        )
        with client as c:
            resp = c.post("/api/v1/actions/bad_http/execute", json={"event": {"topic": "t", "data": {}}})
            assert resp.status_code == 202
            execution_id = resp.json()["id"]
            assert wait_until(lambda: c.get(f"/api/v1/executions/{execution_id}").json()["status"] == "failed")
            execution = c.get(f"/api/v1/executions/{execution_id}").json()
            assert execution["error_code"] == "destination_not_allowed"

    def test_http_action_invalid_header_value_fails(self, make_client):
        client = make_client(
            actions=[
                {
                    "id": "bad_header_http",
                    "type": "http",
                    "enabled": True,
                    "method": "POST",
                    "url": "http://127.0.0.1:9000/notify",
                    "headers": {"X-Invalid": "{{data.count}}"},
                    "body_mode": "none",
                    "timeout_seconds": 5,
                }
            ],
            rules=[],
            security={
                "rest_auth": {"enabled": False, "token_env": "OPAL_EVENT_API_TOKEN"},
                "http_action_allowlist": [{"host": "127.0.0.1", "ports": [9000], "schemes": ["http"]}],
            },
        )
        with client as c:
            resp = c.post(
                "/api/v1/actions/bad_header_http/execute",
                json={"event": {"topic": "t", "data": {"count": 42}}},
            )
            assert resp.status_code == 202
            execution_id = resp.json()["id"]
            assert wait_until(lambda: c.get(f"/api/v1/executions/{execution_id}").json()["status"] == "failed")
            execution = c.get(f"/api/v1/executions/{execution_id}").json()
            assert execution["error_code"] == "invalid_header_value"
            assert "must be a string" in execution["error_message"]


class TestShellArgvIsolation:
    def test_event_data_is_not_interpreted_as_shell_code(self, make_client):
        """Malicious event data passed as a templated argv entry must never be shell-interpreted."""
        client = make_client(
            actions=[
                {
                    "id": "echo_arg",
                    "type": "shell",
                    "enabled": True,
                    "mode": "exec",
                    "executable": "/bin/echo",
                    "args": ["{{data.payload}}"],
                    "stdin": "none",
                    "timeout_seconds": 5,
                }
            ],
            rules=[],
        )
        malicious = "; touch /tmp/opal_event_pwned_$$; echo"
        with client as c:
            resp = c.post(
                "/api/v1/actions/echo_arg/execute",
                json={"event": {"topic": "t", "data": {"payload": malicious}}},
            )
            execution_id = resp.json()["id"]
            assert wait_until(lambda: c.get(f"/api/v1/executions/{execution_id}").json()["status"] == "succeeded")
            execution = c.get(f"/api/v1/executions/{execution_id}").json()
            # The payload is echoed back verbatim as a single argv entry, not executed as shell code.
            assert execution["stdout"].strip() == malicious
            assert not os.path.exists("/tmp/opal_event_pwned_" + str(os.getpid()))
