"""Idempotent action admission (Step 2): durable dedup for rule redelivery and manual retries.

A matching retry returns the same execution receipt without re-running the action; a reused
key carrying different work is a 409 conflict. Harmless side effect: a shell action appending
one line to a counter file per run.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from opal_event.app import create_app
from tests.conftest import base_config, write_config

IS_WINDOWS = os.name == "nt"


def _count_action(count_file: Path, action_id: str = "count_action") -> dict:
    target = str(count_file).replace("\\", "/")
    if IS_WINDOWS:
        return {
            "id": action_id,
            "type": "shell",
            "enabled": True,
            "mode": "exec",
            "executable": "cmd",
            "args": ["/c", "echo", "hit", ">>", target],
            "stdin": "none",
            "timeout_seconds": 5,
        }
    return {
        "id": action_id,
        "type": "shell",
        "enabled": True,
        "mode": "exec",
        "executable": "/bin/sh",
        "args": ["-c", f"echo hit >> {target}"],
        "stdin": "none",
        "timeout_seconds": 5,
    }


@pytest.fixture
def client(tmp_path):
    count_file = tmp_path / "count.txt"
    cfg = base_config(tmp_path, actions=[_count_action(count_file)], rules=[])
    app = create_app(str(write_config(tmp_path, cfg)))
    with TestClient(app) as c:
        c.count_file = count_file
        yield c


def _count(path: Path) -> int:
    return len(path.read_text().splitlines()) if path.exists() else 0


def _wait_terminal(client: TestClient, execution_id: str, timeout: float = 10.0, token: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/v1/execution/{execution_id}", headers=headers)
        if r.status_code == 200 and r.json()["status"] in ("succeeded", "failed", "dropped", "timed_out"):
            return r.json()
        time.sleep(0.05)
    raise AssertionError(f"execution {execution_id} never reached a terminal state")


def _wait_count(path: Path, expected: int, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline and _count(path) < expected:
        time.sleep(0.05)
    assert _count(path) == expected, f"expected {expected} counted runs, got {_count(path)}"


def _wait_executions(client: TestClient, action_id: str, expected: int, timeout: float = 10.0) -> list[dict]:
    """Poll until ``expected`` terminal-state execution rows exist for the action — the
    authoritative admission count (immune to concurrent file-append collisions)."""
    deadline = time.time() + timeout
    rows: list[dict] = []
    while time.time() < deadline:
        rows = [
            e
            for e in client.get("/api/v1/execution/", params={"action_id": action_id}).json()
            if e["status"] in ("succeeded", "failed", "dropped", "timed_out")
        ]
        if len(rows) >= expected:
            return rows
        time.sleep(0.05)
    raise AssertionError(f"expected {expected} executions for {action_id}, got {len(rows)}")


def _execute(client: TestClient, body: dict, key: str | None = None, token: str | None = None) -> object:
    headers = {}
    if key is not None:
        headers["Idempotency-Key"] = key
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.post("/api/v1/action/count_action/execute", json={"event": body}, headers=headers)


BODY = {"topic": "manual.count", "data": {"n": 1}}


class TestManualIdempotency:
    def test_retry_same_key_returns_same_receipt(self, client):
        r1 = _execute(client, BODY, key="k-1")
        assert r1.status_code == 202, r1.text
        first = _wait_terminal(client, r1.json()["id"])
        assert first["status"] == "succeeded", first

        r2 = _execute(client, BODY, key="k-1")
        assert r2.status_code == 202, r2.text
        assert r2.json()["id"] == first["id"]
        time.sleep(0.5)
        assert _count(client.count_file) == 1

    def test_same_key_different_payload_conflicts(self, client):
        r1 = _execute(client, BODY, key="k-2")
        assert r1.status_code == 202, r1.text
        _wait_terminal(client, r1.json()["id"])

        r2 = _execute(client, {"topic": "manual.count", "data": {"n": 2}}, key="k-2")
        assert r2.status_code == 409, r2.text
        assert _count(client.count_file) == 1

    def test_different_keys_run_separately(self, client):
        r1 = _execute(client, BODY, key="k-a")
        r2 = _execute(client, BODY, key="k-b")
        assert r1.json()["id"] != r2.json()["id"]
        _wait_terminal(client, r1.json()["id"])
        _wait_terminal(client, r2.json()["id"])
        assert _count(client.count_file) == 2

    def test_no_key_runs_every_time(self, client):
        r1 = _execute(client, BODY)
        r2 = _execute(client, BODY)
        assert r1.json()["id"] != r2.json()["id"]
        _wait_terminal(client, r1.json()["id"])
        _wait_terminal(client, r2.json()["id"])
        # Execution rows are authoritative; the count file can lose a line to Windows
        # `>>` append sharing between concurrent shell spawns.
        assert len(_wait_executions(client, "count_action", 2)) == 2

    def test_concurrent_same_key_single_run(self, client):
        results, ids = [], []
        barrier = threading.Barrier(8)

        def fire():
            barrier.wait()
            r = _execute(client, BODY, key="k-conc")
            results.append(r.status_code)
            if r.status_code == 202:
                ids.append(r.json()["id"])

        threads = [threading.Thread(target=fire) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(15)

        assert results.count(202) == 8, results
        assert len(set(ids)) == 1, f"concurrent retries produced {len(set(ids))} executions"
        _wait_count(client.count_file, 1)


class TestRuleRedelivery:
    @pytest.fixture
    def rule_client(self, tmp_path):
        count_file = tmp_path / "count.txt"
        action = _count_action(count_file)
        cfg = base_config(
            tmp_path,
            actions=[action],
            rules=[
                {"id": "rule_a", "enabled": True, "topic": "count.*", "conditions": [], "actions": ["count_action"]},
                {"id": "rule_b", "enabled": True, "topic": "count.*", "conditions": [], "actions": ["count_action"]},
            ],
        )
        app = create_app(str(write_config(tmp_path, cfg)))
        with TestClient(app) as c:
            c.count_file = count_file
            yield c

    def _publish(self, client, event_id: str):
        r = client.post("/api/v1/event/", json={"id": event_id, "topic": "count.hit", "data": {"n": 1}})
        assert r.status_code == 202, r.text

    def test_two_rules_same_action_both_run_once(self, rule_client):
        """Distinct rule ids = distinct admission identities: both must execute."""
        self._publish(rule_client, str(uuid.uuid4()))
        rows = _wait_executions(rule_client, "count_action", 2)
        assert {r["rule_id"] for r in rows} == {"rule_a", "rule_b"}
        assert all(r["status"] == "succeeded" for r in rows)

    def test_redelivered_event_does_not_rerun(self, rule_client):
        event_id = str(uuid.uuid4())
        self._publish(rule_client, event_id)
        _wait_executions(rule_client, "count_action", 2)

        # Simulate at-least-once redelivery of the same event through the bus.
        self._publish(rule_client, event_id)
        time.sleep(1.0)
        rows = rule_client.get("/api/v1/execution/", params={"action_id": "count_action"}).json()
        assert len(rows) == 2, f"redelivery created a new admission: {len(rows)} executions"
        assert _count(rule_client.count_file) <= 2, "redelivery re-ran a deduplicated rule/action pair"

        # A genuinely new event still fires.
        self._publish(rule_client, str(uuid.uuid4()))
        _wait_executions(rule_client, "count_action", 4)

    def test_records_survive_restart(self, tmp_path):
        count_file = tmp_path / "count.txt"
        cfg = base_config(tmp_path, actions=[_count_action(count_file)], rules=[])
        path = write_config(tmp_path, cfg)
        with TestClient(create_app(str(path))) as c1:
            r1 = _execute(c1, BODY, key="k-durable")
            assert r1.status_code == 202
            _wait_terminal(c1, r1.json()["id"])
        # Second process lifetime on the same DB — durable dedup must still hold.
        with TestClient(create_app(str(path))) as c2:
            r2 = _execute(c2, BODY, key="k-durable")
            assert r2.status_code == 202
            assert r2.json()["id"] == r1.json()["id"]
            assert _count(count_file) == 1


class TestCallerScoping:
    @pytest.fixture
    def auth_client(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPAL_EVENT_API_TOKEN", "admin-secret")
        monkeypatch.setenv("OPAL_EVENT_EXECUTE_TOKEN", "exec-secret")
        count_file = tmp_path / "count.txt"
        cfg = base_config(tmp_path, actions=[_count_action(count_file)], rules=[])
        cfg["security"]["rest_auth"] = {
            "enabled": True,
            "token_env": "OPAL_EVENT_API_TOKEN",
            "execute_token_env": "OPAL_EVENT_EXECUTE_TOKEN",
        }
        app = create_app(str(write_config(tmp_path, cfg)))
        with TestClient(app) as c:
            c.count_file = count_file
            yield c

    def test_same_key_different_callers_run_independently(self, auth_client):
        r_admin = _execute(auth_client, BODY, key="shared-key", token="admin-secret")
        r_exec = _execute(auth_client, BODY, key="shared-key", token="exec-secret")
        assert r_admin.status_code == 202, r_admin.text
        assert r_exec.status_code == 202, r_exec.text
        assert r_admin.json()["id"] != r_exec.json()["id"], "caller scoping leaked across principals"
        _wait_terminal(auth_client, r_admin.json()["id"], token="admin-secret")
        _wait_terminal(auth_client, r_exec.json()["id"], token="admin-secret")
        assert _count(auth_client.count_file) == 2
