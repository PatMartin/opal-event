#!/usr/bin/env python3
"""Full regression test for a RUNNING opal-event service.

Covers: health/stats, event publish + history + delete, topics, rules CRUD + If-Match
preconditions + dry-test, actions CRUD + manual execute, executions, settings GET/PATCH,
backup export/validate/import, OPAL correlation headers, all three error shapes, scoped
cursors, the REST->ZeroMQ->observer->rules->executor->DB pipeline, and the raw ZeroMQ
publish path.

Usage:
    python regression_test.py

Environment (all optional):
    OPAL_EVENT_API_URL          default http://127.0.0.1:8080
    OPAL_EVENT_API_TOKEN        bearer token (only needed if rest_auth is enabled)
    OPAL_EVENT_ZMQ_PUBLISHERS   default tcp://127.0.0.1:5555

Every network operation is capped at 30 seconds; a timeout is a FAIL. Each test also has
a 30-second overall budget (polling loops bail out early). Results are printed as
PASS/FAIL/SKIP lines; exit code is 0 only when everything passes (skips are allowed).

WARNING: this suite MUTATES the target service. It creates/deletes rules and actions, runs
actions, re-imports the exported backup, and deletes some of its own events. Every config
write makes the service rewrite its YAML file (comments are not preserved). Resources are
namespaced with a per-run token and removed at the end; test events stay in history. Do not
point it at a shared or production instance.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
import time
import uuid
from urllib.parse import urlparse

try:
    import httpx
except ImportError:
    sys.exit("regression_test.py requires httpx (run it with the project venv python)")

API = os.environ.get("OPAL_EVENT_API_URL", "http://127.0.0.1:8080").rstrip("/")
ZMQ_PUB_ADDR = os.environ.get("OPAL_EVENT_ZMQ_PUBLISHERS", "tcp://127.0.0.1:5555")
TOKEN = os.environ.get("OPAL_EVENT_API_TOKEN")

REQ_TIMEOUT = 30.0  # hard cap per HTTP request / socket op
TEST_BUDGET = 30.0  # hard cap per test, including poll loops
POLL_BUDGET = 15.0  # cap for "wait until state converges" loops inside tests

RUN = "r" + uuid.uuid4().hex[:8]  # letter-first: topic/rule/action id patterns require it
TOPIC_BASE = f"regression.{RUN}"
RULE_ID = f"regression_rule_{RUN}"
ACTION_ID = f"regression_action_{RUN}"
E2E_RULE_ID = f"regression_e2e_rule_{RUN}"
E2E_ACTION_ID = f"regression_e2e_action_{RUN}"

_results: list[tuple[str, str, str, float]] = []
_state: dict = {}


class SkipTest(Exception):
    pass


class TimeoutFail(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def poll_until(fn, description, budget=POLL_BUDGET):
    """Calls fn() until it returns truthy or the budget expires. Returns the truthy value."""
    deadline = time.monotonic() + budget
    last = None
    while time.monotonic() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(0.4)
    raise TimeoutFail(f"timeout after {budget:.0f}s waiting for: {description}")


def make_client() -> httpx.Client:
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    return httpx.Client(base_url=API, timeout=REQ_TIMEOUT, headers=headers)


CLIENT = make_client()


def run(name, fn):
    start = time.monotonic()
    try:
        fn()
        status, detail = "PASS", ""
    except SkipTest as exc:
        status, detail = "SKIP", str(exc)
    except httpx.TimeoutException:
        status, detail = "FAIL", f"request timed out (> {REQ_TIMEOUT:.0f}s)"
    except httpx.ConnectError as exc:
        status, detail = "FAIL", f"connection error: {exc}"
    except TimeoutFail as exc:
        status, detail = "FAIL", str(exc)
    except AssertionError as exc:
        status, detail = "FAIL", str(exc)
    except Exception as exc:  # noqa: BLE001 - report, don't die
        status, detail = "FAIL", f"{type(exc).__name__}: {exc}"
    elapsed = time.monotonic() - start
    if status == "PASS" and elapsed > TEST_BUDGET:
        status, detail = "FAIL", f"exceeded {TEST_BUDGET:.0f}s budget ({elapsed:.1f}s)"
    _results.append((name, status, detail, elapsed))
    line = f"{status:<4} {name}  ({elapsed:5.1f}s)"
    if detail:
        line += f" -- {detail[:200]}"
    print(line, flush=True)


# ---------------------------------------------------------------- tests


def t_connectivity():
    r = CLIENT.get("/api/v1/health/live")
    check(r.status_code == 200, f"GET /health/live -> {r.status_code}; is opal-event-svc running?")


def t_health_ready():
    r = CLIENT.get("/api/v1/health/ready")
    check(r.status_code == 200, f"ready -> {r.status_code}")
    check(r.json()["status"] == "ok", f"ready body {r.json()}")


def t_health_fields():
    r = CLIENT.get("/api/v1/health/")
    check(r.status_code == 200, f"health -> {r.status_code}")
    body = r.json()
    for key in ("status", "broker_running", "database_ready", "pending_restart"):
        check(key in body, f"health missing '{key}'")
    check(body["status"] == "ok" and body["broker_running"] and body["database_ready"], f"degraded: {body}")


def t_stats_fields():
    r = CLIENT.get("/api/v1/stats/")
    check(r.status_code == 200, f"stats -> {r.status_code}")
    for key in ("observed_events", "invalid_events", "matched_events", "executions_succeeded",
                "executions_failed", "executions_dropped", "queue_depth", "active_workers"):
        check(key in r.json(), f"stats missing '{key}'")


def t_opal_headers_echoed():
    headers = {
        "X-Opal-Session-Id": f"sess-{RUN}",
        "X-Opal-Transaction-Id": f"txn-{RUN}",
        "X-Opal-Causation-Id": f"cause-{RUN}",
    }
    r = CLIENT.get("/api/v1/health/", headers=headers)
    check(r.status_code == 200, f"health -> {r.status_code}")
    check(r.headers.get("x-opal-session-id") == headers["X-Opal-Session-Id"], "session id not echoed")
    check(r.headers.get("x-opal-transaction-id") == headers["X-Opal-Transaction-Id"], "transaction id not echoed")
    check(r.headers.get("x-opal-causation-id") == headers["X-Opal-Causation-Id"], "causation id not echoed")
    check(r.headers.get("x-opal-request-id"), "missing X-Opal-Request-Id")


def t_opal_header_malformed():
    r = CLIENT.get("/api/v1/health/", headers={"X-Opal-Session-Id": "has space"})
    check(r.status_code == 422, f"expected 422, got {r.status_code}")
    body = r.json()
    check("error" in body and body["error"]["code"] == "invalid_header", f"wrong shape: {body}")
    check(r.headers.get("x-opal-request-id"), "no request id on rejected request")


def t_publish():
    r = CLIENT.post("/api/v1/events/", json={"topic": f"{TOPIC_BASE}.one", "data": {"n": 1}})
    check(r.status_code == 202, f"publish -> {r.status_code} {r.text[:200]}")
    body = r.json()
    check(body["status"] == "accepted" and body["id"], f"bad receipt: {body}")
    _state["event_id"] = body["id"]


def t_publish_validation_shape():
    r = CLIENT.post("/api/v1/events/", json={"topic": "BAD TOPIC!!", "data": {}})
    check(r.status_code == 422, f"expected 422, got {r.status_code}")
    check("detail" in r.json(), f"expected HTTPValidationError shape: {r.text[:200]}")


def t_event_stored():
    eid = _state["event_id"]
    def found():
        r = CLIENT.get(f"/api/v1/events/{eid}")
        return r if r.status_code == 200 else None
    r = poll_until(found, f"event {eid} persisted")
    ev = r.json()
    check(ev["topic"] == f"{TOPIC_BASE}.one" and ev["data"] == {"n": 1}, f"event mismatch: {ev}")
    check(ev["id"] == eid and ev["source"] and ev["timestamp"], f"missing fields: {ev}")


def t_event_not_found_shape():
    r = CLIENT.get(f"/api/v1/events/{uuid.uuid4()}")
    check(r.status_code == 404, f"expected 404, got {r.status_code}")
    body = r.json()
    check("code" in body and "message" in body and "request_id" in body, f"expected Problem shape: {body}")


def t_events_list():
    r = CLIENT.get("/api/v1/events/", params={"topic": f"{TOPIC_BASE}.one"})
    check(r.status_code == 200, f"list -> {r.status_code}")
    body = r.json()
    check("items" in body and "next_cursor" in body, f"missing list keys: {body}")
    check(any(i["id"] == _state["event_id"] for i in body["items"]), "published event not in list")


def t_second_publish_for_cursor():
    r = CLIENT.post("/api/v1/events/", json={"topic": f"{TOPIC_BASE}.two", "data": {"n": 2}})
    check(r.status_code == 202, f"publish2 -> {r.status_code}")
    _state["event_id_2"] = r.json()["id"]
    poll_until(lambda: CLIENT.get(f"/api/v1/events/{_state['event_id_2']}").status_code == 200,
               "second event persisted")


def t_cursor_scoping():
    r = CLIENT.get("/api/v1/events/", params={"limit": 1})
    check(r.status_code == 200, f"page1 -> {r.status_code}")
    cursor = r.json().get("next_cursor")
    if not cursor:
        raise SkipTest("fewer than 2 events in store; cannot exercise cursor")
    r2 = CLIENT.get("/api/v1/events/", params={"limit": 1, "cursor": cursor})
    check(r2.status_code == 200, f"page2 same filters -> {r2.status_code}")
    r3 = CLIENT.get("/api/v1/events/", params={"limit": 1, "cursor": cursor, "topic": "different.filter"})
    check(r3.status_code == 422, f"cursor reused across filters -> {r3.status_code} (want 422)")
    r4 = CLIENT.get("/api/v1/events/", params={"limit": 1, "cursor": "!!!garbage!!!"})
    check(r4.status_code == 422, f"garbage cursor -> {r4.status_code} (want 422)")


def t_topics_list():
    def found():
        r = CLIENT.get("/api/v1/topics/")
        if r.status_code != 200:
            return None
        return r if f"{TOPIC_BASE}.one" in r.json()["items"] else None
    r = poll_until(found, "topic visible in /topics/")
    check(r.headers.get("etag"), "topics list missing ETag")


def t_rules_catalog():
    r = CLIENT.get("/api/v1/rules/")
    check(r.status_code == 200, f"rules list -> {r.status_code}")
    body = r.json()
    check("items" in body and isinstance(body["items"], list), "rules items missing")
    check(body.get("next_cursor") is None, "rules must be a full catalog (next_cursor null)")
    check(r.headers.get("etag"), "rules list missing ETag")
    _state["rules_etag"] = r.headers["etag"]


def t_rule_create_and_conflict():
    rule = {
        "id": RULE_ID, "enabled": True, "topic": f"{TOPIC_BASE}.*",
        "conditions": [{"path": "data.kind", "operator": "eq", "value": "hit"}],
        "actions": [],
    }
    r = CLIENT.post("/api/v1/rules/", json=rule)
    check(r.status_code == 201, f"create rule -> {r.status_code} {r.text[:200]}")
    r2 = CLIENT.post("/api/v1/rules/", json=rule)
    check(r2.status_code == 409, f"duplicate rule -> {r2.status_code} (want 409)")


def t_rule_get():
    r = CLIENT.get(f"/api/v1/rules/{RULE_ID}")
    check(r.status_code == 200, f"get rule -> {r.status_code}")
    check(r.headers.get("etag"), "rule get missing ETag")


def t_rule_patch_preconditions():
    url = f"/api/v1/rules/{RULE_ID}"
    r = CLIENT.patch(url, json={"enabled": False})
    check(r.status_code == 428 and "error" in r.json(), f"no If-Match -> {r.status_code} (want 428 OpalError)")
    r = CLIENT.patch(url, json={"enabled": False}, headers={"If-Match": "stale-etag"})
    check(r.status_code == 412 and "error" in r.json(), f"stale If-Match -> {r.status_code} (want 412 OpalError)")
    etag = CLIENT.get(url).headers["etag"]
    r = CLIENT.patch(url, json={"enabled": False}, headers={"If-Match": etag})
    check(r.status_code == 200, f"valid patch -> {r.status_code}")
    check(r.json()["enabled"] is False, "patch did not apply")


def t_rule_dry_test():
    match = CLIENT.post(f"/api/v1/rules/{RULE_ID}/test",
                        json={"event": {"topic": f"{TOPIC_BASE}.x", "data": {"kind": "hit"}}})
    check(match.status_code == 200, f"rule test -> {match.status_code}")
    # rule was disabled by the previous PATCH test -> matched must be False
    check(match.json()["matched"] is False, f"disabled rule should not match: {match.json()}")
    etag = CLIENT.get(f"/api/v1/rules/{RULE_ID}").headers["etag"]
    r = CLIENT.patch(f"/api/v1/rules/{RULE_ID}", json={"enabled": True}, headers={"If-Match": etag})
    check(r.status_code == 200 and r.json()["enabled"] is True, f"re-enable rule -> {r.status_code}")
    match2 = CLIENT.post(f"/api/v1/rules/{RULE_ID}/test",
                         json={"event": {"topic": f"{TOPIC_BASE}.x", "data": {"kind": "hit"}}})
    check(match2.json()["matched"] is True, f"enabled rule should match: {match2.json()}")
    miss = CLIENT.post(f"/api/v1/rules/{RULE_ID}/test",
                       json={"event": {"topic": f"{TOPIC_BASE}.x", "data": {"kind": "miss"}}})
    check(miss.json()["matched"] is False, f"non-matching condition should not match: {miss.json()}")


def t_rule_delete():
    url = f"/api/v1/rules/{RULE_ID}"
    r = CLIENT.delete(url)
    check(r.status_code == 428, f"delete w/o If-Match -> {r.status_code} (want 428)")
    etag = CLIENT.get(url).headers["etag"]
    r = CLIENT.delete(url, headers={"If-Match": etag})
    check(r.status_code == 204, f"delete w/ If-Match -> {r.status_code}")
    check(CLIENT.get(url).status_code == 404, "deleted rule still readable")


def t_action_create_and_conflict():
    action = {
        "id": ACTION_ID, "type": "python", "enabled": True,
        "module": "opal_event.plugins.example", "function": "handle_event",
        "timeout_seconds": 10,
    }
    r = CLIENT.post("/api/v1/actions/", json=action)
    check(r.status_code == 201, f"create action -> {r.status_code} {r.text[:200]}")
    r2 = CLIENT.post("/api/v1/actions/", json=action)
    check(r2.status_code == 409, f"duplicate action -> {r2.status_code} (want 409)")


def t_action_patch_preconditions():
    url = f"/api/v1/actions/{ACTION_ID}"
    r = CLIENT.patch(url, json={"enabled": False})
    check(r.status_code == 428, f"patch w/o If-Match -> {r.status_code} (want 428)")
    r = CLIENT.patch(url, json={"enabled": False}, headers={"If-Match": "stale"})
    check(r.status_code == 412, f"stale If-Match -> {r.status_code} (want 412)")


def t_action_execute():
    r = CLIENT.post(f"/api/v1/actions/{ACTION_ID}/execute",
                    json={"event": {"topic": f"{TOPIC_BASE}.manual", "data": {"k": "v"}}})
    check(r.status_code == 202, f"execute -> {r.status_code} {r.text[:200]}")
    body = r.json()
    check(body["status"] == "queued", f"expected 'queued' receipt, got: {body['status']} {body.get('error_code')}")
    check(body["trigger"] == "manual" and body["action_id"] == ACTION_ID, f"bad execution: {body}")
    _state["exec_id"] = body["id"]


def t_execution_reaches_terminal():
    eid = _state["exec_id"]
    def done():
        r = CLIENT.get(f"/api/v1/executions/{eid}")
        if r.status_code != 200:
            return None
        return r if r.json()["status"] in ("succeeded", "failed", "timed_out", "dropped") else None
    r = poll_until(done, f"execution {eid} terminal")
    ex = r.json()
    check(ex["status"] == "succeeded", f"python action should succeed: {ex['status']} {ex.get('error_message')}")
    check(ex["started_at"] and ex["finished_at"] and ex["duration_ms"] is not None, "missing timing fields")
    check(ex.get("action_revision"), "missing action_revision")


def t_executions_list():
    r = CLIENT.get("/api/v1/executions/", params={"event_id": "does-not-exist"})
    check(r.status_code == 200, f"executions list -> {r.status_code}")
    body = r.json()
    check("items" in body and "has_more" in body and isinstance(body["has_more"], bool), f"list shape: {body}")
    r2 = CLIENT.get("/api/v1/executions/", params={"action_id": ACTION_ID})
    check(any(i["id"] == _state["exec_id"] for i in r2.json()["items"]), "execution not filterable by action_id")


def t_action_delete():
    url = f"/api/v1/actions/{ACTION_ID}"
    r = CLIENT.delete(url)
    check(r.status_code == 428, f"delete w/o If-Match -> {r.status_code} (want 428)")
    etag = CLIENT.get(url).headers["etag"]
    r = CLIENT.delete(url, headers={"If-Match": etag})
    check(r.status_code == 204, f"delete w/ If-Match -> {r.status_code}")


def t_settings_get_and_patch():
    r = CLIENT.get("/api/v1/settings/")
    check(r.status_code == 200, f"settings -> {r.status_code}")
    body = r.json()
    check(set(body) <= {"execution", "persistence", "max_event_bytes"}, f"non-portable keys leaked: {set(body)}")
    etag = r.headers.get("etag")
    check(etag, "settings missing ETag")
    r2 = CLIENT.patch("/api/v1/settings/", json={"max_event_bytes": body["max_event_bytes"]})
    check(r2.status_code == 428, f"settings PATCH w/o If-Match -> {r2.status_code} (want 428)")
    r3 = CLIENT.patch("/api/v1/settings/", json={"max_event_bytes": body["max_event_bytes"]},
                      headers={"If-Match": etag})
    check(r3.status_code == 200, f"settings PATCH -> {r3.status_code}")


def t_backup_export_validate_import():
    r = CLIENT.get("/api/v1/backup/export")
    check(r.status_code == 200, f"export -> {r.status_code}")
    backup = r.json()
    check(backup.get("format_version") == 1 and "actions" in backup and "rules" in backup,
          f"bad backup shape: {list(backup)}")
    v = CLIENT.post("/api/v1/backup/validate", json=backup)
    check(v.status_code == 200 and v.json()["valid"] is True, f"validate -> {v.status_code} {v.text[:200]}")
    r2 = CLIENT.post("/api/v1/backup/import", json={"backup": backup})
    check(r2.status_code == 422, f"import w/o If-Match -> {r2.status_code} (want 422 required header)")
    etag = CLIENT.get("/api/v1/settings/").headers["etag"]
    r3 = CLIENT.post("/api/v1/backup/import", json={"backup": backup}, headers={"If-Match": etag})
    check(r3.status_code == 200, f"import -> {r3.status_code} {r3.text[:200]}")
    check("revision" in r3.json() and "pending_restart" in r3.json(), f"bad import result: {r3.text[:200]}")


def t_e2e_pipeline():
    """Own rule+action so the test does not depend on the example config."""
    action = {"id": E2E_ACTION_ID, "type": "python", "enabled": True,
              "module": "opal_event.plugins.example", "function": "handle_event", "timeout_seconds": 10}
    rule = {"id": E2E_RULE_ID, "enabled": True, "topic": f"{TOPIC_BASE}.e2e.*",
            "conditions": [{"path": "data.person", "operator": "eq", "value": True}],
            "actions": [E2E_ACTION_ID]}
    check(CLIENT.post("/api/v1/actions/", json=action).status_code == 201, "e2e action create failed")
    check(CLIENT.post("/api/v1/rules/", json=rule).status_code == 201, "e2e rule create failed")

    r = CLIENT.post("/api/v1/events/", json={"topic": f"{TOPIC_BASE}.e2e.go", "data": {"person": True}})
    check(r.status_code == 202, f"e2e publish -> {r.status_code}")
    eid = r.json()["id"]

    def done():
        lr = CLIENT.get("/api/v1/executions/", params={"event_id": eid})
        if lr.status_code != 200 or not lr.json()["items"]:
            return None
        ex = lr.json()["items"][0]
        return ex if ex["status"] in ("succeeded", "failed", "timed_out", "dropped") else None

    ex = poll_until(done, "rule->action execution terminal")
    check(ex["status"] == "succeeded", f"pipeline execution: {ex['status']} {ex.get('error_message')}")
    check(ex["rule_id"] == E2E_RULE_ID and ex["trigger"] == "rule", f"wrong linkage: {ex}")

    # Non-matching event: no execution may appear. The observer evaluates rules and synchronously
    # records any execution *before* persisting the event, so once the event is stored the
    # decision is final -- no timing guesswork (only possible when event_mode == "all").
    r2 = CLIENT.post("/api/v1/events/", json={"topic": f"{TOPIC_BASE}.e2e.go", "data": {"person": False}})
    check(r2.status_code == 202, "non-match publish failed")
    eid2 = r2.json()["id"]
    if CLIENT.get("/api/v1/settings/").json()["persistence"]["event_mode"] == "all":
        poll_until(lambda: CLIENT.get(f"/api/v1/events/{eid2}").status_code == 200, "non-matching event persisted")
    else:
        time.sleep(2)  # no persistence signal for unmatched events; best-effort wait
    lr = CLIENT.get("/api/v1/executions/", params={"event_id": eid2})
    check(lr.json()["items"] == [], f"non-matching event spawned executions: {lr.json()['items']}")

    for kind, rid in (("rules", E2E_RULE_ID), ("actions", E2E_ACTION_ID)):  # rule first: it references the action
        etag = CLIENT.get(f"/api/v1/{kind}/{rid}").headers["etag"]
        rd = CLIENT.delete(f"/api/v1/{kind}/{rid}", headers={"If-Match": etag})
        check(rd.status_code == 204, f"e2e cleanup DELETE {kind}/{rid} -> {rd.status_code}")


def _zmq_send(frames: list[bytes]) -> None:
    """Publishes one multipart message to the publishers endpoint through a fresh PUB socket."""
    try:
        import zmq  # noqa: PLC0415
    except ImportError as err:
        raise SkipTest("pyzmq not installed") from err
    parts = urlparse(ZMQ_PUB_ADDR)
    check(parts.scheme == "tcp" and parts.hostname and parts.port,
          f"cannot parse OPAL_EVENT_ZMQ_PUBLISHERS={ZMQ_PUB_ADDR!r} (expected tcp://host:port)")
    # quick reachability probe so a dead endpoint fails fast, not after the poll budget
    try:
        socket.create_connection((parts.hostname, parts.port), timeout=5).close()
    except OSError as err:
        raise SkipTest(f"ZMQ publisher endpoint {ZMQ_PUB_ADDR} unreachable") from err
    ctx = zmq.Context()
    try:
        pub = ctx.socket(zmq.PUB)
        pub.connect(ZMQ_PUB_ADDR)
        time.sleep(0.5)  # slow-joiner
        pub.send_multipart(frames)
        time.sleep(0.3)  # let the frame flush before linger=0 teardown
    finally:
        ctx.destroy(linger=0)  # closes any open sockets; plain term() would block forever on one


def t_zmq_publish():
    topic = f"{TOPIC_BASE}.zmq"
    marker = f"zmq-src-{RUN}"
    _zmq_send([topic.encode(), json.dumps({"topic": topic, "data": {"via": "raw-pub"}, "source": marker}).encode()])

    def found():
        r = CLIENT.get("/api/v1/events/", params={"topic": topic, "limit": 1})  # topic is indexed; source isn't
        return r.json()["items"] if r.status_code == 200 else None

    items = poll_until(found, "raw ZMQ publish observed + stored")
    check(items[0]["source"] == marker and items[0]["data"] == {"via": "raw-pub"}, f"stored event mismatch: {items[0]}")


def t_zmq_invalid_frame():
    before = CLIENT.get("/api/v1/stats/").json()["invalid_events"]
    # frame topic != envelope topic -> observer must reject it (proxy still forwards fine)
    _zmq_send([f"{TOPIC_BASE}.frame".encode(), json.dumps({"topic": "mismatched.topic", "data": {}}).encode()])

    def bumped():
        r = CLIENT.get("/api/v1/stats/")
        return r if r.status_code == 200 and r.json()["invalid_events"] > before else None

    poll_until(bumped, "invalid_events counter increment")


def t_event_delete():
    eid = _state["event_id_2"]
    r = CLIENT.delete(f"/api/v1/events/{eid}")
    check(r.status_code == 204, f"delete event -> {r.status_code}")
    check(CLIENT.get(f"/api/v1/events/{eid}").status_code == 404, "deleted event still readable")


TESTS = [
    ("connectivity /health/live", t_connectivity),
    ("health/ready", t_health_ready),
    ("health fields", t_health_fields),
    ("stats fields", t_stats_fields),
    ("opal headers echoed", t_opal_headers_echoed),
    ("opal header malformed 422", t_opal_header_malformed),
    ("publish event 202", t_publish),
    ("publish validation 422 shape", t_publish_validation_shape),
    ("event stored + roundtrip", t_event_stored),
    ("event 404 Problem shape", t_event_not_found_shape),
    ("events list", t_events_list),
    ("publish second event", t_second_publish_for_cursor),
    ("cursor scoping/422", t_cursor_scoping),
    ("topics list + ETag", t_topics_list),
    ("rules catalog + ETag", t_rules_catalog),
    ("rule create/409", t_rule_create_and_conflict),
    ("rule get + ETag", t_rule_get),
    ("rule patch 428/412/200", t_rule_patch_preconditions),
    ("rule dry-test", t_rule_dry_test),
    ("rule delete 428/204", t_rule_delete),
    ("action create/409", t_action_create_and_conflict),
    ("action patch 428/412", t_action_patch_preconditions),
    ("action execute 202", t_action_execute),
    ("execution terminal state", t_execution_reaches_terminal),
    ("executions list + filters", t_executions_list),
    ("action delete 428/204", t_action_delete),
    ("settings get + patch", t_settings_get_and_patch),
    ("backup export/validate/import", t_backup_export_validate_import),
    ("e2e rule->action pipeline", t_e2e_pipeline),
    ("zmq raw publish", t_zmq_publish),
    ("zmq invalid frame rejected", t_zmq_invalid_frame),
    ("event delete 204/404", t_event_delete),
]


def _cleanup() -> None:
    """Best-effort removal of any rules/actions this run left behind (e.g. after a mid-test failure)."""
    for kind in ("rules", "actions"):  # rules first: an action referenced by a rule can't be deleted (409)
        with contextlib.suppress(httpx.HTTPError, KeyError, ValueError):
            for item in CLIENT.get(f"/api/v1/{kind}/").json()["items"]:
                if RUN in item["id"]:
                    etag = CLIENT.get(f"/api/v1/{kind}/{item['id']}").headers.get("etag", "")
                    CLIENT.delete(f"/api/v1/{kind}/{item['id']}", headers={"If-Match": etag})


def main() -> int:
    print(f"regression_test.py -- target {API}  (per-request timeout {REQ_TIMEOUT:.0f}s, per-test budget {TEST_BUDGET:.0f}s)")
    print("NOTE: mutates the target service (rules, actions, events; config YAML is rewritten)")
    print("-" * 78)
    # fast-fail probe: nothing else makes sense if the service is down
    try:
        CLIENT.get("/api/v1/health/live", timeout=10)
    except httpx.HTTPError as exc:
        print(f"FAIL connectivity -- cannot reach {API}: {exc}")
        print("start the service first: opal-event-svc --config config/opal_event.yaml")
        CLIENT.close()
        return 1

    try:
        for name, fn in TESTS:
            run(name, fn)
    finally:
        _cleanup()
        CLIENT.close()

    passed = sum(1 for _, s, _, _ in _results if s == "PASS")
    failed = sum(1 for _, s, _, _ in _results if s == "FAIL")
    skipped = sum(1 for _, s, _, _ in _results if s == "SKIP")
    print("-" * 78)
    print(f"{passed} passed, {failed} failed, {skipped} skipped  (of {len(_results)})")
    if failed:
        print("FAILING:")
        for name, s, detail, _ in _results:
            if s == "FAIL":
                print(f"  {name} -- {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
