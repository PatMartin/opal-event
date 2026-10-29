"""OPAL Suite Standard v1.2 correlation headers: validation, echo-back, and defaulting."""

from __future__ import annotations

import re
import time


def wait_until(predicate, timeout=3.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class TestOpalContextHeaders:
    def test_response_echoes_generated_transaction_and_request_ids_when_absent(self, app_client):
        resp = app_client.get("/api/v1/health/live")
        assert resp.status_code == 200
        assert re.match(r"^[!-~]{1,128}$", resp.headers["x-opal-transaction-id"])
        assert re.match(r"^[!-~]{1,128}$", resp.headers["x-opal-request-id"])
        assert "x-opal-session-id" not in resp.headers
        assert "x-opal-causation-id" not in resp.headers

    def test_response_echoes_supplied_session_and_transaction_ids(self, app_client):
        resp = app_client.get(
            "/api/v1/health/live",
            headers={"X-Opal-Session-Id": "sess-1", "X-Opal-Transaction-Id": "txn-1", "X-Opal-Causation-Id": "cause-1"},
        )
        assert resp.headers["x-opal-session-id"] == "sess-1"
        assert resp.headers["x-opal-transaction-id"] == "txn-1"
        assert resp.headers["x-opal-causation-id"] == "cause-1"

    def test_request_id_is_always_freshly_generated_not_echoed_from_client(self, app_client):
        resp = app_client.get("/api/v1/health/live", headers={"X-Opal-Request-Id": "client-supplied-value"})
        assert resp.headers["x-opal-request-id"] != "client-supplied-value"

    def test_malformed_header_value_yields_422_with_opal_error_shape(self, app_client):
        resp = app_client.get("/api/v1/health/live", headers={"X-Opal-Session-Id": "bad value with spaces"})
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"]["code"] == "invalid_header"
        assert "request_id" in body["error"]
        assert "transaction_id" in body["error"]
        # still echoed on headers even for a rejected request
        assert re.match(r"^[!-~]{1,128}$", resp.headers["x-opal-transaction-id"])

    def test_oversized_header_value_rejected(self, app_client):
        resp = app_client.get("/api/v1/health/live", headers={"X-Opal-Session-Id": "x" * 129})
        assert resp.status_code == 422

    def test_context_echoed_on_error_responses_too(self, app_client):
        resp = app_client.get("/api/v1/rules/missing-rule", headers={"X-Opal-Session-Id": "sess-err"})
        assert resp.status_code == 404
        assert resp.headers["x-opal-session-id"] == "sess-err"
        assert "x-opal-transaction-id" in resp.headers

    def test_problem_error_request_id_matches_context_header(self, app_client):
        resp = app_client.get("/api/v1/rules/missing-rule")
        body = resp.json()
        assert body["error"]["request_id"] == resp.headers["x-opal-request-id"]

    def test_metadata_propagates_from_rest_publication_through_zeromq_observation(self, app_client, monkeypatch):
        observed = []
        service = app_client.app.state.service
        original_on_event = service.broker._on_event

        def capture(ev):
            observed.append(ev)
            return original_on_event(ev)

        monkeypatch.setattr(service.broker, "_on_event", capture)

        resp = app_client.post(
            "/api/v1/events/",
            json={"topic": "metadata.observed.test", "data": {"foo": "bar"}},
            headers={
                "X-Opal-Session-Id": "sess-propagate-1",
                "X-Opal-Transaction-Id": "txn-propagate-2",
                "X-Opal-Causation-Id": "cause-propagate-3",
            },
        )
        assert resp.status_code == 202

        assert wait_until(lambda: any(ev.get("topic") == "metadata.observed.test" for ev in observed), timeout=3)
        matching = [ev for ev in observed if ev.get("topic") == "metadata.observed.test"]
        assert len(matching) == 1
        ev = matching[0]

        # Valid session, transaction, and causation metadata preserved in normalized observed event
        assert ev["session_id"] == "sess-propagate-1"
        assert ev["transaction_id"] == "txn-propagate-2"
        assert ev["causation_id"] == "cause-propagate-3"
        assert ev["opal_session_id"] == "sess-propagate-1"
        assert ev["opal_transaction_id"] == "txn-propagate-2"
        assert ev["opal_causation_id"] == "cause-propagate-3"
        assert ev["topic"] == "metadata.observed.test"
        assert ev["data"] == {"foo": "bar"}

    def test_metadata_partial_headers_propagation(self, app_client, monkeypatch):
        observed = []
        service = app_client.app.state.service
        original_on_event = service.broker._on_event

        def capture(ev):
            observed.append(ev)
            return original_on_event(ev)

        monkeypatch.setattr(service.broker, "_on_event", capture)

        # Only session_id supplied; transaction_id is assigned by middleware; causation_id absent
        resp = app_client.post(
            "/api/v1/events/",
            json={"topic": "metadata.partial.test", "data": {"a": 1}},
            headers={"X-Opal-Session-Id": "sess-only-123"},
        )
        assert resp.status_code == 202

        assert wait_until(lambda: any(ev.get("topic") == "metadata.partial.test" for ev in observed), timeout=3)
        ev = [ev for ev in observed if ev.get("topic") == "metadata.partial.test"][0]

        assert ev["session_id"] == "sess-only-123"
        assert "transaction_id" in ev and ev["transaction_id"]
        assert "causation_id" not in ev
