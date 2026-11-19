"""Strong-ETag optimistic concurrency (opal_event.1.2.0.yaml): rules/actions PATCH+DELETE and
settings PATCH require a matching ``If-Match``, returning 428 when omitted and 412 when stale."""

from __future__ import annotations


class TestRulesConcurrency:
    def test_list_and_get_return_etag(self, app_client):
        list_resp = app_client.get("/api/v1/rule/")
        assert list_resp.status_code == 200
        assert list_resp.headers["etag"]
        get_resp = app_client.get("/api/v1/rule/front_door_alert")
        assert get_resp.headers["etag"] == list_resp.headers["etag"]

    def test_list_is_a_full_catalog(self, app_client):
        data = app_client.get("/api/v1/rule/").json()
        assert isinstance(data, list)

    def test_patch_without_if_match_is_428(self, app_client):
        resp = app_client.patch("/api/v1/rule/front_door_alert", json={"enabled": False})
        assert resp.status_code == 428
        assert resp.json()["error"]["code"] == "precondition_required"

    def test_patch_with_stale_if_match_is_412(self, app_client):
        resp = app_client.patch("/api/v1/rule/front_door_alert", json={"enabled": False}, headers={"If-Match": "stale"})
        assert resp.status_code == 412
        assert resp.json()["error"]["code"] == "precondition_failed"

    def test_patch_with_correct_if_match_succeeds(self, app_client):
        etag = app_client.get("/api/v1/rule/front_door_alert").headers["etag"]
        resp = app_client.patch("/api/v1/rule/front_door_alert", json={"enabled": False}, headers={"If-Match": etag})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    def test_delete_without_if_match_is_428(self, app_client):
        resp = app_client.delete("/api/v1/rule/front_door_alert")
        assert resp.status_code == 428

    def test_delete_with_stale_if_match_is_412(self, app_client):
        resp = app_client.delete("/api/v1/rule/front_door_alert", headers={"If-Match": "stale"})
        assert resp.status_code == 412

    def test_delete_with_correct_if_match_succeeds(self, app_client):
        etag = app_client.get("/api/v1/rule/front_door_alert").headers["etag"]
        resp = app_client.delete("/api/v1/rule/front_door_alert", headers={"If-Match": etag})
        assert resp.status_code == 204


class TestActionsConcurrency:
    def test_list_and_get_return_etag(self, app_client):
        list_resp = app_client.get("/api/v1/action/")
        assert list_resp.headers["etag"]
        get_resp = app_client.get("/api/v1/action/echo_action")
        assert get_resp.headers["etag"] == list_resp.headers["etag"]

    def test_list_is_a_full_catalog(self, app_client):
        data = app_client.get("/api/v1/action/").json()
        assert isinstance(data, list)

    def test_patch_without_if_match_is_428(self, app_client):
        resp = app_client.patch("/api/v1/action/echo_action", json={"enabled": False})
        assert resp.status_code == 428

    def test_patch_with_stale_if_match_is_412(self, app_client):
        resp = app_client.patch("/api/v1/action/echo_action", json={"enabled": False}, headers={"If-Match": "stale"})
        assert resp.status_code == 412

    def test_delete_requires_if_match_before_in_use_check(self, app_client):
        # echo_action is referenced by front_door_alert; without If-Match we must still get 428
        # (precondition checked), not a 409 in-use conflict.
        resp = app_client.delete("/api/v1/action/echo_action")
        assert resp.status_code == 428

    def test_delete_in_use_action_with_valid_if_match_is_409(self, app_client):
        etag = app_client.get("/api/v1/action/echo_action").headers["etag"]
        resp = app_client.delete("/api/v1/action/echo_action", headers={"If-Match": etag})
        assert resp.status_code == 409


class TestSettingsConcurrency:
    """v0.1.0: the collection PATCH is retired; per-key PUT/DELETE carry the If-Match contract."""

    def test_collection_patch_is_retired(self, app_client):
        resp = app_client.patch("/api/v1/setting/", json={"max_event_bytes": 500000})
        assert resp.status_code == 405

    def test_put_without_if_match_is_428(self, app_client):
        resp = app_client.put("/api/v1/setting/max_event_bytes", json={"value": 500000})
        assert resp.status_code == 428
        assert resp.json()["error"]["code"] == "precondition_required"

    def test_put_with_stale_if_match_is_412(self, app_client):
        resp = app_client.put("/api/v1/setting/max_event_bytes", json={"value": 500000}, headers={"If-Match": "stale"})
        assert resp.status_code == 412
        assert resp.json()["error"]["code"] == "precondition_failed"

    def test_put_with_correct_if_match_succeeds(self, app_client):
        etag = app_client.get("/api/v1/setting/").headers["etag"]
        resp = app_client.put("/api/v1/setting/max_event_bytes", json={"value": 500000}, headers={"If-Match": etag})
        assert resp.status_code == 200
        assert resp.json()["value"] == 500000

    def test_put_one_key_preserves_siblings(self, app_client):
        get_resp = app_client.get("/api/v1/setting/")
        assert get_resp.status_code == 200
        etag = get_resp.headers["etag"]
        original = get_resp.json()
        original_queue_size = original["execution.queue_size"]
        new_workers = original["execution.max_workers"] + 1

        put_resp = app_client.put(
            "/api/v1/setting/execution.max_workers",
            json={"value": new_workers},
            headers={"If-Match": etag},
        )
        assert put_resp.status_code == 200
        assert put_resp.json()["value"] == new_workers

        refetched = app_client.get("/api/v1/setting/").json()
        assert refetched["execution.max_workers"] == new_workers
        assert refetched["execution.queue_size"] == original_queue_size
        assert refetched["execution.default_timeout_seconds"] == original["execution.default_timeout_seconds"]

    def test_put_max_event_bytes_updates_broker_observer_limit(self, app_client):
        # Verify that updating max_event_bytes via settings PUT updates the runtime broker observer
        broker = app_client.app.state.service.broker
        initial_broker_limit = broker.max_event_bytes

        etag = app_client.get("/api/v1/setting/").headers["etag"]
        new_limit = 250000
        put_resp = app_client.put(
            "/api/v1/setting/max_event_bytes",
            json={"value": new_limit},
            headers={"If-Match": etag},
        )
        assert put_resp.status_code == 200
        assert put_resp.json()["value"] == new_limit
        assert broker.max_event_bytes == new_limit
        assert broker.max_event_bytes != initial_broker_limit


class TestTopicsEtag:
    def test_topics_list_returns_etag(self, app_client):
        resp = app_client.get("/api/v1/topic/")
        assert resp.status_code == 200
        assert resp.headers["etag"]
