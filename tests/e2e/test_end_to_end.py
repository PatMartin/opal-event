import time


def wait_until(predicate, timeout=3.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class TestEndToEndPipeline:
    def test_invalid_event_increments_stats_counter(self, app_client):
        stats_url = "/api/v1/stats/"
        invalid_events_before = app_client.get(stats_url).json()["invalid_events"]

        app_client.app.state.service.broker.publish("invalid.topic", {"topic": "mismatched.topic", "data": {}})

        assert wait_until(lambda: app_client.get(stats_url).json()["invalid_events"] == invalid_events_before + 1)

    def test_publish_triggers_rule_and_action_execution(self, app_client):
        resp = app_client.post(
            "/api/v1/events/", json={"topic": "vision.object.detected", "data": {"object": "person", "camera": "front_door"}}
        )
        assert resp.status_code == 202
        event_id = resp.json()["id"]

        assert wait_until(lambda: app_client.get("/api/v1/events/").json()["items"])
        events = app_client.get("/api/v1/events/").json()["items"]
        assert any(e["id"] == event_id for e in events)

        assert wait_until(lambda: app_client.get("/api/v1/executions/").json()["items"])
        executions = app_client.get("/api/v1/executions/").json()["items"]
        matching = [e for e in executions if e["event_id"] == event_id]
        assert matching
        assert matching[0]["rule_id"] == "front_door_alert"
        assert matching[0]["action_id"] == "echo_action"

        stats = app_client.get("/api/v1/stats/").json()
        assert stats["observed_events"] >= 1
        assert stats["matched_events"] >= 1

    def test_non_matching_topic_does_not_trigger_actions(self, app_client):
        app_client.post("/api/v1/events/", json={"topic": "other.topic", "data": {}})
        assert wait_until(lambda: app_client.get("/api/v1/events/").json()["items"])
        executions_before = app_client.get("/api/v1/executions/").json()["items"]
        assert not any(e["event_id"] for e in executions_before if e.get("rule_id") == "front_door_alert")

    def test_rule_lifecycle_crud(self, app_client):
        body = {"id": "new_rule", "enabled": True, "topic": "intent.*", "conditions": [], "actions": ["echo_action"]}
        resp = app_client.post("/api/v1/rules/", json=body)
        assert resp.status_code == 201
        assert resp.headers["location"] == "/api/v1/rules/new_rule"

        resp = app_client.get("/api/v1/rules/new_rule")
        assert resp.status_code == 200
        etag = resp.headers["etag"]

        resp = app_client.patch("/api/v1/rules/new_rule", json={"enabled": False}, headers={"If-Match": etag})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        etag = resp.headers.get("etag") or app_client.get("/api/v1/rules/new_rule").headers["etag"]

        resp = app_client.delete("/api/v1/rules/new_rule", headers={"If-Match": etag})
        assert resp.status_code == 204

        resp = app_client.get("/api/v1/rules/new_rule")
        assert resp.status_code == 404

    def test_clear_events_operation(self, app_client):
        resp = app_client.post("/api/v1/events/", json={"topic": "vision.object.detected", "data": {"object": "person"}})
        assert resp.status_code == 202
        assert wait_until(lambda: app_client.get("/api/v1/events/").json()["items"])
        assert len(app_client.get("/api/v1/events/").json()["items"]) > 0

        # DELETE /api/v1/events/ should clear all events from all topics
        clear_resp = app_client.delete("/api/v1/events/")
        assert clear_resp.status_code == 204

        # Events list should now be empty
        events_resp = app_client.get("/api/v1/events/").json()
        assert events_resp["items"] == []
        topics_resp = app_client.get("/api/v1/topics/").json()
        assert topics_resp["items"] == []

    def test_delete_event_operation(self, app_client):
        resp = app_client.post("/api/v1/events/", json={"topic": "vision.object.detected", "data": {"object": "person"}})
        assert resp.status_code == 202
        event_id = resp.json()["id"]
        assert wait_until(lambda: app_client.get(f"/api/v1/events/{event_id}").status_code == 200)

        # DELETE /api/v1/events/{event_id} should delete the specific event and return 204
        delete_resp = app_client.delete(f"/api/v1/events/{event_id}")
        assert delete_resp.status_code == 204

        # Getting the deleted event should return 404
        get_resp = app_client.get(f"/api/v1/events/{event_id}")
        assert get_resp.status_code == 404

        # Deleting a non-existent event should return 404
        delete_missing_resp = app_client.delete(f"/api/v1/events/{event_id}")
        assert delete_missing_resp.status_code == 404

    def test_manual_action_execution(self, app_client):
        resp = app_client.post(
            "/api/v1/actions/python_action/execute", json={"event": {"topic": "manual.test", "data": {"object": "cat"}}}
        )
        assert resp.status_code == 202
        execution_id = resp.json()["id"]
        assert wait_until(lambda: app_client.get(f"/api/v1/executions/{execution_id}").json()["status"] == "succeeded")
        execution = app_client.get(f"/api/v1/executions/{execution_id}").json()
        assert execution["result"] == {"received_topic": "manual.test", "object": "cat"}

    def test_backup_export_validate_roundtrip(self, app_client):
        exported = app_client.get("/api/v1/backup/export").json()
        resp = app_client.post("/api/v1/backup/validate", json=exported)
        assert resp.status_code == 200
        assert resp.json()["valid"] is True

    def test_backup_import_stale_if_match_is_409_not_412(self, app_client):
        # Unlike settings/rules/actions, opal_event.1.2.0.yaml does not add 412/428 responses to
        # backup/import; a stale revision intentionally still yields the original 409 Conflict.
        exported = app_client.get("/api/v1/backup/export").json()
        resp = app_client.post("/api/v1/backup/import", json={"backup": exported}, headers={"If-Match": "definitely-stale"})
        assert resp.status_code == 409

    def test_backup_import_missing_if_match_is_422_not_428(self, app_client):
        exported = app_client.get("/api/v1/backup/export").json()
        resp = app_client.post("/api/v1/backup/import", json={"backup": exported})
        assert resp.status_code == 422  # FastAPI's standard required-header validation, unchanged from v1.1
