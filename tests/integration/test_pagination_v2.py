"""Cursor pagination semantics introduced in opal_event.1.2.0.yaml: cursors are scoped to the exact
filter set and expire explicitly; executions additionally gain a ``has_more`` field."""

from __future__ import annotations

import time

from opal_event.pagination import CURSOR_TTL_SECONDS, decode_scoped_cursor, encode_scoped_cursor
from opal_event.validation import ValidationProblem


def make_event(app_client, topic: str, data: dict | None = None):
    return app_client.post("/api/v1/events/", json={"topic": topic, "data": data or {}})


class TestEventsCursorScoping:
    def test_cursor_from_one_filter_set_rejected_for_a_different_filter_set(self, app_client):
        for _ in range(3):
            make_event(app_client, "vision.object.detected", {"n": 1})
        page = app_client.get("/api/v1/events/", params={"topic": "vision.object.detected", "limit": 1})
        cursor = page.json()["next_cursor"]
        assert cursor is not None

        mismatched = app_client.get("/api/v1/events/", params={"topic": "other.topic", "cursor": cursor})
        assert mismatched.status_code == 422

    def test_cursor_reused_with_same_filter_set_succeeds(self, app_client):
        for _ in range(3):
            make_event(app_client, "vision.object.detected", {"n": 1})
        page1 = app_client.get("/api/v1/events/", params={"topic": "vision.object.detected", "limit": 1})
        cursor = page1.json()["next_cursor"]
        page2 = app_client.get("/api/v1/events/", params={"topic": "vision.object.detected", "cursor": cursor})
        assert page2.status_code == 200

    def test_garbage_cursor_rejected(self, app_client):
        resp = app_client.get("/api/v1/events/", params={"cursor": "not-a-valid-cursor"})
        assert resp.status_code == 422

    def test_expired_cursor_rejected(self):
        cursor = encode_scoped_cursor("inner", {"topic": None})
        # Simulate the TTL having elapsed by crafting an already-expired cursor directly.
        import base64

        payload = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        fingerprint, _expires_at, inner = payload.split(":", 2)
        expired_payload = f"{fingerprint}:{int(time.time()) - 1}:{inner}"
        expired_cursor = base64.urlsafe_b64encode(expired_payload.encode("utf-8")).decode("ascii")
        try:
            decode_scoped_cursor(expired_cursor, {"topic": None})
            raised = False
        except ValidationProblem:
            raised = True
        assert raised

    def test_cursor_ttl_is_positive_and_bounded(self):
        assert 0 < CURSOR_TTL_SECONDS <= 24 * 3600


class TestExecutionsHasMore:
    def test_has_more_true_when_additional_pages_exist(self, app_client):
        resp = app_client.post("/api/v1/actions/python_action/execute", json={"event": {"topic": "t", "data": {}}})
        assert resp.status_code == 202
        resp2 = app_client.post("/api/v1/actions/python_action/execute", json={"event": {"topic": "t", "data": {}}})
        assert resp2.status_code == 202
        data = app_client.get("/api/v1/executions/", params={"limit": 1}).json()
        assert "has_more" in data
        assert data["has_more"] is True
        assert data["next_cursor"] is not None

    def test_has_more_false_at_end_of_results(self, app_client):
        data = app_client.get("/api/v1/executions/", params={"limit": 500}).json()
        assert data["has_more"] is False
        assert data["next_cursor"] is None

    def test_executions_cursor_scoped_to_filters(self, app_client):
        app_client.post("/api/v1/actions/python_action/execute", json={"event": {"topic": "t", "data": {}}})
        app_client.post("/api/v1/actions/python_action/execute", json={"event": {"topic": "t", "data": {}}})
        page = app_client.get("/api/v1/executions/", params={"action_id": "python_action", "limit": 1})
        cursor = page.json()["next_cursor"]
        if cursor is not None:
            mismatched = app_client.get("/api/v1/executions/", params={"action_id": "echo_action", "cursor": cursor})
            assert mismatched.status_code == 422
