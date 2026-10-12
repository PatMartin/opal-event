"""Strong-ETag optimistic concurrency (opal_event.1.2.0.yaml): rules/actions PATCH+DELETE and
settings PATCH require a matching ``If-Match``, returning 428 when omitted and 412 when stale."""

from __future__ import annotations


class TestRulesConcurrency:
    def test_list_and_get_return_etag(self, app_client):
        list_resp = app_client.get("/api/v1/rules/")
        assert list_resp.status_code == 200
        assert list_resp.headers["etag"]
        get_resp = app_client.get("/api/v1/rules/front_door_alert")
        assert get_resp.headers["etag"] == list_resp.headers["etag"]

    def test_list_is_a_full_catalog_with_no_cursor(self, app_client):
        data = app_client.get("/api/v1/rules/").json()
        assert data["next_cursor"] is None
        assert isinstance(data["items"], list)
        # cursor/limit query params are no longer part of the contract; passing them is harmless no-ops.
        resp = app_client.get("/api/v1/rules/", params={"cursor": "ignored", "limit": 1})
        assert len(resp.json()["items"]) == len(data["items"])

    def test_patch_without_if_match_is_428(self, app_client):
        resp = app_client.patch("/api/v1/rules/front_door_alert", json={"enabled": False})
        assert resp.status_code == 428
        assert resp.json()["error"]["code"] == "precondition_required"

    def test_patch_with_stale_if_match_is_412(self, app_client):
        resp = app_client.patch("/api/v1/rules/front_door_alert", json={"enabled": False}, headers={"If-Match": "stale"})
        assert resp.status_code == 412
        assert resp.json()["error"]["code"] == "precondition_failed"

    def test_patch_with_correct_if_match_succeeds(self, app_client):
        etag = app_client.get("/api/v1/rules/front_door_alert").headers["etag"]
        resp = app_client.patch("/api/v1/rules/front_door_alert", json={"enabled": False}, headers={"If-Match": etag})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    def test_delete_without_if_match_is_428(self, app_client):
        resp = app_client.delete("/api/v1/rules/front_door_alert")
        assert resp.status_code == 428

    def test_delete_with_stale_if_match_is_412(self, app_client):
        resp = app_client.delete("/api/v1/rules/front_door_alert", headers={"If-Match": "stale"})
        assert resp.status_code == 412

    def test_delete_with_correct_if_match_succeeds(self, app_client):
        etag = app_client.get("/api/v1/rules/front_door_alert").headers["etag"]
        resp = app_client.delete("/api/v1/rules/front_door_alert", headers={"If-Match": etag})
        assert resp.status_code == 204


class TestActionsConcurrency:
    def test_list_and_get_return_etag(self, app_client):
        list_resp = app_client.get("/api/v1/actions/")
        assert list_resp.headers["etag"]
        get_resp = app_client.get("/api/v1/actions/echo_action")
        assert get_resp.headers["etag"] == list_resp.headers["etag"]

    def test_list_is_a_full_catalog_with_no_cursor(self, app_client):
        data = app_client.get("/api/v1/actions/").json()
        assert data["next_cursor"] is None

    def test_patch_without_if_match_is_428(self, app_client):
        resp = app_client.patch("/api/v1/actions/echo_action", json={"enabled": False})
        assert resp.status_code == 428

    def test_patch_with_stale_if_match_is_412(self, app_client):
        resp = app_client.patch("/api/v1/actions/echo_action", json={"enabled": False}, headers={"If-Match": "stale"})
        assert resp.status_code == 412

    def test_delete_requires_if_match_before_in_use_check(self, app_client):
        # echo_action is referenced by front_door_alert; without If-Match we must still get 428
        # (precondition checked), not a 409 in-use conflict.
        resp = app_client.delete("/api/v1/actions/echo_action")
        assert resp.status_code == 428

    def test_delete_in_use_action_with_valid_if_match_is_409(self, app_client):
        etag = app_client.get("/api/v1/actions/echo_action").headers["etag"]
        resp = app_client.delete("/api/v1/actions/echo_action", headers={"If-Match": etag})
        assert resp.status_code == 409


class TestSettingsConcurrency:
    def test_patch_without_if_match_is_428(self, app_client):
        resp = app_client.patch("/api/v1/settings/", json={"max_event_bytes": 500000})
        assert resp.status_code == 428
        assert resp.json()["error"]["code"] == "precondition_required"

    def test_patch_with_stale_if_match_is_412(self, app_client):
        resp = app_client.patch("/api/v1/settings/", json={"max_event_bytes": 500000}, headers={"If-Match": "stale"})
        assert resp.status_code == 412
        assert resp.json()["error"]["code"] == "precondition_failed"

    def test_patch_with_correct_if_match_succeeds(self, app_client):
        etag = app_client.get("/api/v1/settings/").headers["etag"]
        resp = app_client.patch("/api/v1/settings/", json={"max_event_bytes": 500000}, headers={"If-Match": etag})
        assert resp.status_code == 200
        assert resp.json()["max_event_bytes"] == 500000


class TestTopicsEtag:
    def test_topics_list_returns_etag(self, app_client):
        resp = app_client.get("/api/v1/topics/")
        assert resp.status_code == 200
        assert resp.headers["etag"]
