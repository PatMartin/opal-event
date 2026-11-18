"""REST admission observability (step 5): a REST-admitted event is rule-routed once
through the bounded entry point, its broker echo can never re-dispatch, and every
admitted id carries a traceable disposition (executions by event_id incl. queue
saturation drops, events.matched flag, stats counters)."""
import json
import time
import uuid

import zmq
from fastapi.testclient import TestClient

from opal_event.app import create_app
from opal_event.zeromq_broker import PublisherQueueFullError
from tests.conftest import base_config, write_config


def _cfg(tmp_path, **overrides):
    return base_config(
        tmp_path,
        actions=[{
            "id": "count_action", "type": "shell", "enabled": True, "mode": "exec",
            "executable": "cmd", "args": ["/c", f"echo 1 >> {tmp_path / 'c.txt'}"],
            "stdin": "none", "timeout_seconds": 5,
        }],
        rules=[{
            "id": "count_rule", "enabled": True, "topic": "vision.*", "conditions": [],
            "actions": ["count_action"],
        }],
        **overrides,
    )


def _client(tmp_path, cfg):
    return TestClient(create_app(str(write_config(tmp_path, cfg))))


def _wait_execs(client, event_id, expected, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = client.get("/api/v1/execution/", params={"event_id": event_id}).json()
        if len(rows) >= expected:
            return rows
        time.sleep(0.25)
    return client.get("/api/v1/execution/", params={"event_id": event_id}).json()


def test_rest_publish_dispatches_once_echo_inert(tmp_path):
    """The event is routed at admission; its broker echo must not dispatch again."""
    client = _client(tmp_path, _cfg(tmp_path))
    with client:
        receipt = client.post("/api/v1/event/", json={"topic": "vision.door", "data": {}}).json()
        rows = _wait_execs(client, receipt["id"], 1)
        time.sleep(2.0)  # let the echo come back — must stay inert
        rows = _wait_execs(client, receipt["id"], 1)
        assert len(rows) == 1
        assert rows[0]["trigger"] == "rule" and rows[0]["rule_id"] == "count_rule"
        ev = client.get(f"/api/v1/event/{receipt['id']}").json()
        assert ev["matched"] is True


def test_zero_match_disposition(tmp_path):
    client = _client(tmp_path, _cfg(tmp_path))
    with client:
        receipt = client.post("/api/v1/event/", json={"topic": "audio.nothing", "data": {}}).json()
        time.sleep(0.5)
        ev = client.get(f"/api/v1/event/{receipt['id']}").json()
        assert ev["matched"] is False
        assert client.get("/api/v1/execution/", params={"event_id": receipt["id"]}).json() == []


def test_fanout_saturation_still_admitted(tmp_path):
    """A saturated publisher queue loses the fan-out copy but the admission contract —
    rules routed, event persisted — is already fulfilled (best-effort ZMQ)."""
    cfg = _cfg(tmp_path)
    app = create_app(str(write_config(tmp_path, cfg)))
    client = TestClient(app)
    with client:
        svc = app.state.service
        original = svc.publish
        svc.publish = lambda e: (_ for _ in ()).throw(PublisherQueueFullError("full"))
        try:
            receipt = client.post("/api/v1/event/", json={"topic": "vision.door", "data": {}}).json()
        finally:
            svc.publish = original
        assert _wait_execs(client, receipt["id"], 1)  # routed despite failed fan-out
        stats = client.get("/api/v1/stats/").json()
        assert stats.get("fanout_dropped", 0) >= 1


def test_execution_queue_saturation_is_dropped_status(tmp_path):
    """Burst past the bounded execution queue: overflow admissions land as 'dropped'
    execution rows — traceable per admitted event id."""
    cfg = _cfg(tmp_path)
    cfg["execution"]["queue_size"] = 1
    cfg["execution"]["max_workers"] = 1
    cfg["execution"]["default_timeout_seconds"] = 5
    cfg["actions"][0]["executable"] = "cmd"
    cfg["actions"][0]["args"] = ["/c", "ping -n 4 127.0.0.1 >nul"]  # slow so queue stays full
    cfg["actions"][0]["timeout_seconds"] = 10
    client = _client(tmp_path, cfg)
    with client:
        receipts = [
            client.post("/api/v1/event/", json={"topic": "vision.burst", "data": {}}).json()
            for _ in range(6)
        ]
        deadline = time.time() + 20
        statuses = []
        while time.time() < deadline:
            statuses = [
                e["status"]
                for r in receipts
                for e in client.get("/api/v1/execution/", params={"event_id": r["id"]}).json()
            ]
            if statuses and all(s in ("succeeded", "failed", "dropped", "timed_out") for s in statuses):
                break
            time.sleep(0.3)
        assert len(statuses) == 6
        assert "dropped" in statuses  # queue saturation produced a dropped disposition


def test_raw_zmq_producer_still_dispatched(tmp_path):
    """Raw producers keep the observer admission path unchanged."""
    cfg = _cfg(tmp_path)
    client = _client(tmp_path, cfg)
    with client:
        s = zmq.Context.instance().socket(zmq.PUB)
        s.connect(cfg["zeromq"]["publishers"])
        time.sleep(0.5)
        eid = str(uuid.uuid4())
        s.send_multipart([b"vision.door", json.dumps({
            "id": eid, "topic": "vision.door", "source": "zmq", "type": "test",
            "timestamp": "2026-01-01T00:00:00+00:00", "data": {},
        }).encode()])
        rows = _wait_execs(client, eid, 1)
        assert len(rows) == 1 and rows[0]["rule_id"] == "count_rule"
        s.close(0)
