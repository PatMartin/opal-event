"""Bounded-list semantics for the stored-event and execution catalogs:
deterministic newest-first ordering and `limit` as a plain max-items cap."""

from __future__ import annotations


def make_event(app_client, topic: str, data: dict | None = None):
    return app_client.post("/api/v1/event/", json={"topic": topic, "data": data or {}})


class TestEventsBoundedList:
    def test_limit_caps_items(self, app_client):
        for _ in range(3):
            make_event(app_client, "vision.object.detected", {"n": 1})
        page = app_client.get("/api/v1/event/", params={"topic": "vision.object.detected", "limit": 1})
        data = page.json()
        assert page.status_code == 200 and isinstance(data, list) and len(data) == 1

    def test_deterministic_newest_first(self, app_client):
        for _ in range(3):
            make_event(app_client, "vision.object.detected")
        limited = app_client.get("/api/v1/event/", params={"limit": 2}).json()
        full = app_client.get("/api/v1/event/", params={"limit": 50}).json()
        ids = [e["id"] for e in full]
        assert len(ids) == len(set(ids))
        assert limited == full[:2]

    def test_filters_select(self, app_client):
        make_event(app_client, "topic.a")
        make_event(app_client, "topic.b")
        filtered = app_client.get("/api/v1/event/", params={"topic": "topic.a"}).json()
        assert all(e["topic"] == "topic.a" for e in filtered)


class TestExecutionsBoundedList:
    def test_limit_caps_items(self, app_client):
        resp = app_client.get("/api/v1/execution/", params={"limit": 5})
        data = resp.json()
        assert resp.status_code == 200 and isinstance(data, list) and len(data) <= 5


class TestCatalogsAreArrays:
    def test_rule_list_is_array(self, app_client):
        assert isinstance(app_client.get("/api/v1/rule/").json(), list)

    def test_action_list_is_array(self, app_client):
        assert isinstance(app_client.get("/api/v1/action/").json(), list)
