"""Per-key settings endpoints (opal_event_v1.6.0 kernel ``opal_setting_*`` ops)."""

import pytest


@pytest.fixture
def etag(app_client):
    return app_client.get("/api/v1/setting/").headers["etag"]


class TestSettingKeyEndpoints:
    def test_get_known_key(self, app_client):
        r = app_client.get("/api/v1/setting/execution.queue_size")
        assert r.status_code == 200
        body = r.json()
        assert body["key"] == "execution.queue_size"
        assert body["value"] == 10  # test fixture config overrides the model default
        assert body["default"] == 100 and body["is_default"] is False

    def test_get_unknown_key_404(self, app_client):
        assert app_client.get("/api/v1/setting/nope.nothing").status_code == 404

    def test_put_requires_if_match(self, app_client):
        r = app_client.put("/api/v1/setting/execution.queue_size", json={"value": 150})
        assert r.status_code == 428

    def test_put_stale_revision_412(self, app_client):
        r = app_client.put("/api/v1/setting/execution.queue_size", json={"value": 150}, headers={"If-Match": '"stale"'})
        assert r.status_code == 412

    def test_put_then_get(self, app_client, etag):
        r = app_client.put("/api/v1/setting/execution.queue_size", json={"value": 150}, headers={"If-Match": etag})
        assert r.status_code == 200 and r.json()["value"] == 150 and r.json()["is_default"] is False

    def test_delete_resets_default(self, app_client, etag):
        e2 = app_client.put("/api/v1/setting/execution.queue_size", json={"value": 150}, headers={"If-Match": etag})
        assert e2.status_code == 200
        fresh = app_client.get("/api/v1/setting/").headers["etag"]
        r = app_client.delete("/api/v1/setting/execution.queue_size", headers={"If-Match": fresh})
        assert r.status_code == 204
        after = app_client.get("/api/v1/setting/execution.queue_size")
        assert after.json()["value"] == 100 and after.json()["is_default"] is True

    def test_put_max_event_bytes(self, app_client, etag):
        r = app_client.put("/api/v1/setting/max_event_bytes", json={"value": 1048576}, headers={"If-Match": etag})
        assert r.status_code == 200
