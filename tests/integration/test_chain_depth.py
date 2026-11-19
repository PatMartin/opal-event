"""Suite chain-depth limit: producers declare derived hops via ``X-Opal-Chain-Depth``
(REST) / ``opal_chain_depth`` (bus envelope); chain root is ``transaction_id`` and the
parent hop is ``causation_id``. Depth >= ``security.max_chain_depth`` (default 16) is
refused — a 422 on REST admission, a skipped dispatch (event still persisted for audit)
on the observer path. The Think fixture covers derived ``execute`` calls carrying the
correlation context Think propagates.
"""

import json
import time
import uuid

import zmq
from fastapi.testclient import TestClient

from opal_event.app import create_app
from tests.conftest import base_config, write_config


def _topic() -> str:
    return "test.chain.z" + uuid.uuid4().hex[:8]


def _count_cfg(tmp_path):
    out = tmp_path / "count.txt"
    cfg = base_config(
        tmp_path,
        actions=[
            {
                "id": "count_action",
                "type": "shell",
                "enabled": True,
                "mode": "exec",
                "executable": "cmd",
                "args": ["/c", f"echo 1 >> {out}"],
                "stdin": "none",
                "timeout_seconds": 5,
            }
        ],
        rules=[
            {
                "id": "count_rule",
                "enabled": True,
                "topic": "test.chain.*",
                "conditions": [],
                "actions": ["count_action"],
            }
        ],
    )
    return cfg, out


def _client(tmp_path, cfg=None):
    cfg = cfg or base_config(tmp_path)
    app = create_app(str(write_config(tmp_path, cfg)))
    return TestClient(app), cfg


def _execs(client, action_id="count_action"):
    return [e for e in client.get("/api/v1/execution/").json() if e["action_id"] == action_id]


def test_rest_publish_under_limit_admitted(tmp_path):
    cfg, out = _count_cfg(tmp_path)
    client, _ = _client(tmp_path, cfg)
    with client:
        r = client.post("/api/v1/event/", json={"topic": _topic(), "data": {}}, headers={"X-Opal-Chain-Depth": "15"})
        assert r.status_code == 202
        deadline = time.time() + 10
        while not _execs(client) and time.time() < deadline:
            time.sleep(0.25)
        assert len(_execs(client)) == 1


def test_rest_publish_at_limit_rejected(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        for depth in ("16", "99"):
            r = client.post("/api/v1/event/", json={"topic": _topic(), "data": {}}, headers={"X-Opal-Chain-Depth": depth})
            assert r.status_code == 422
            assert r.json()["error"]["code"] == "chain_depth_exceeded"


def test_rest_publish_invalid_depth_rejected(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        for bad in ("abc", "-1", "1.5"):
            r = client.post("/api/v1/event/", json={"topic": _topic(), "data": {}}, headers={"X-Opal-Chain-Depth": bad})
            assert r.status_code == 422
            assert r.json()["error"]["code"] == "invalid_header"


def test_rest_publish_no_depth_is_root(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        assert client.post("/api/v1/event/", json={"topic": _topic(), "data": {}}).status_code == 202


def test_execute_at_limit_rejected_and_think_boundary(tmp_path):
    cfg, _ = _count_cfg(tmp_path)
    client, _ = _client(tmp_path, cfg)
    with client:
        for depth in ("16", "40"):
            r = client.post(
                "/api/v1/action/count_action/execute",
                json={"event": {"topic": _topic(), "data": {}}},
                headers={"X-Opal-Chain-Depth": depth},
            )
            assert r.status_code == 422
            assert r.json()["error"]["code"] == "chain_depth_exceeded"
        assert _execs(client) == []
        # Derived call inside the limit — correlation fields as Think sends them — is admitted.
        r = client.post(
            "/api/v1/action/count_action/execute",
            json={"event": {"topic": _topic(), "data": {}}},
            headers={
                "X-Opal-Chain-Depth": "15",
                "X-Opal-Transaction-Id": str(uuid.uuid4()),
                "X-Opal-Causation-Id": str(uuid.uuid4()),
            },
        )
        assert r.status_code == 202
        r = client.post(
            "/api/v1/action/count_action/execute",
            json={"event": {"topic": _topic(), "data": {}}},
            headers={"X-Opal-Chain-Depth": "bogus"},
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "invalid_header"


def test_zmq_envelope_depth_enforced_and_audited(tmp_path):
    cfg, out = _count_cfg(tmp_path)
    client, _ = _client(tmp_path, cfg)
    with client:
        s = zmq.Context.instance().socket(zmq.PUB)
        s.connect(cfg["zeromq"]["publishers"])
        time.sleep(0.5)
        topic = _topic()
        stats0 = client.get("/api/v1/health/").json()["details"]["stats"]

        def send(depth):
            s.send_multipart(
                [
                    topic.encode(),
                    json.dumps(
                        {
                            "id": str(uuid.uuid4()),
                            "topic": topic,
                            "source": "zmq",
                            "type": "test",
                            "timestamp": "2026-01-01T00:00:00+00:00",
                            "data": {},
                            "opal_chain_depth": depth,
                        }
                    ).encode(),
                ]
            )
            time.sleep(1.2)

        send(16)  # at limit -> dispatch refused, event persisted
        send("bad")  # invalid metadata -> dropped as malformed
        send(15)  # under limit -> dispatches

        stats = client.get("/api/v1/health/").json()["details"]["stats"]
        assert stats.get("chain_depth_rejections", 0) - stats0.get("chain_depth_rejections", 0) == 1
        assert stats["invalid_events"] - stats0["invalid_events"] == 1
        events = [e for e in client.get("/api/v1/event/").json() if e["topic"] == topic]
        assert len(events) == 2  # blocked + good persisted; malformed dropped pre-persistence
        s.close(0)


def test_custom_limit_config(tmp_path):
    cfg = base_config(tmp_path)
    cfg["security"]["max_chain_depth"] = 2
    client, _ = _client(tmp_path, cfg)
    with client:
        r = client.post("/api/v1/event/", json={"topic": _topic(), "data": {}}, headers={"X-Opal-Chain-Depth": "2"})
        assert r.status_code == 422
        r = client.post("/api/v1/event/", json={"topic": _topic(), "data": {}}, headers={"X-Opal-Chain-Depth": "1"})
        assert r.status_code == 202
