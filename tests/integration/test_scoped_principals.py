"""Scoped-principal authorization (step 4): producers restricted to configured topic
prefixes; an execute principal (the Think stand-in) restricted to named actions and to
receipts it owns. Two synthetic principals, verified over the real REST surface.
"""

import time

import pytest
from fastapi.testclient import TestClient

from opal_event.app import create_app
from tests.conftest import base_config, write_config

ADMIN = "admintoken123"
PRODUCER = "producertoken123"
THINK = "thinktoken123"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OPAL_EVENT_API_TOKEN", ADMIN)
    monkeypatch.setenv("PRODUCER_TOKEN", PRODUCER)
    monkeypatch.setenv("THINK_TOKEN", THINK)
    cfg = base_config(
        tmp_path,
        actions=[
            {
                "id": "count_action",
                "type": "shell",
                "enabled": True,
                "mode": "exec",
                "executable": "cmd",
                "args": ["/c", f"echo 1 >> {tmp_path / 'c.txt'}"],
                "stdin": "none",
                "timeout_seconds": 5,
            },
            {
                "id": "other_action",
                "type": "shell",
                "enabled": True,
                "mode": "exec",
                "executable": "cmd",
                "args": ["/c", f"echo 1 >> {tmp_path / 'o.txt'}"],
                "stdin": "none",
                "timeout_seconds": 5,
            },
        ],
        rules=[],
    )
    cfg["security"]["rest_auth"] = {
        "enabled": True,
        "token_env": "OPAL_EVENT_API_TOKEN",
        "execute_token_env": None,
        "principals": [
            {"name": "producer", "token_env": "PRODUCER_TOKEN", "scope": "publish", "topic_prefixes": ["vision."]},
            {
                "name": "think",
                "token_env": "THINK_TOKEN",
                "scope": "execute",
                "actions": ["count_action"],
                "own_receipts_only": True,
            },
        ],
    }
    app = create_app(str(write_config(tmp_path, cfg)))
    with TestClient(app) as c:
        yield c


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def test_producer_publishes_allowed_prefix(client):
    r = client.post("/api/v1/event/", json={"topic": "vision.front", "data": {}}, headers=_auth(PRODUCER))
    assert r.status_code == 202


def test_producer_denied_other_prefix(client):
    r = client.post("/api/v1/event/", json={"topic": "audio.back", "data": {}}, headers=_auth(PRODUCER))
    assert r.status_code == 403


def test_producer_cannot_admin(client):
    for method, url in (("GET", "/api/v1/action/"), ("GET", "/api/v1/execution/"), ("GET", "/api/v1/setting/")):
        assert client.request(method, url, headers=_auth(PRODUCER)).status_code == 403
    assert client.get("/api/v1/health/", headers=_auth(PRODUCER)).status_code == 200


def test_think_discovers_only_named_actions(client):
    r = client.get("/api/v1/action/", headers=_auth(THINK))
    assert r.status_code == 200
    ids = [a["id"] for a in r.json()]
    assert ids == ["count_action"]
    assert client.get("/api/v1/action/other_action", headers=_auth(THINK)).status_code == 404
    assert client.get("/api/v1/action/count_action", headers=_auth(THINK)).status_code == 200


def test_think_executes_named_action_only(client):
    ok = client.post("/api/v1/action/count_action/execute", json={"event": {"topic": "t.z", "data": {}}}, headers=_auth(THINK))
    assert ok.status_code == 202
    denied = client.post(
        "/api/v1/action/other_action/execute", json={"event": {"topic": "t.z", "data": {}}}, headers=_auth(THINK)
    )
    assert denied.status_code == 404


def test_think_sees_only_own_receipts(client):
    mine = client.post(
        "/api/v1/action/count_action/execute", json={"event": {"topic": "t.z", "data": {}}}, headers=_auth(THINK)
    ).json()
    theirs = client.post(
        "/api/v1/action/count_action/execute", json={"event": {"topic": "t.z", "data": {}}}, headers=_auth(ADMIN)
    ).json()
    time.sleep(0.5)
    assert client.get(f"/api/v1/execution/{mine['id']}", headers=_auth(THINK)).status_code == 200
    assert client.get(f"/api/v1/execution/{theirs['id']}", headers=_auth(THINK)).status_code == 404
    listed = client.get("/api/v1/execution/", headers=_auth(THINK)).json()
    assert {e["id"] for e in listed} == {mine["id"]}
    # Admin still sees everything, including the owner marking.
    all_rows = client.get("/api/v1/execution/", headers=_auth(ADMIN)).json()
    owners = {e["id"]: e["owner"] for e in all_rows}
    assert owners[mine["id"]] == "think" and owners[theirs["id"]] is None


def test_admin_full_surface(client):
    assert client.get("/api/v1/setting/", headers=_auth(ADMIN)).status_code == 200
    assert client.get("/api/v1/action/", headers=_auth(ADMIN)).status_code == 200
    assert client.post("/api/v1/event/", json={"topic": "any.topic", "data": {}}, headers=_auth(ADMIN)).status_code == 202


def test_no_token_rejected_even_from_loopback(client):
    assert client.get("/api/v1/action/").status_code == 401
