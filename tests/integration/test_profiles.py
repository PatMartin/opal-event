"""``/api/v1/profile/`` catalog: CRUD, strong-ETag preconditions, the one-active invariant,
and activation applying the stored portable-settings preset to live config (v1.7.1)."""

from __future__ import annotations

import pytest


@pytest.fixture
def etag(app_client):
    return app_client.get("/api/v1/profile/").headers["etag"]


def _create(client, body, etag):
    return client.post("/api/v1/profile/", json=body, headers={"If-Match": etag})


class TestProfileCrud:
    def test_list_starts_empty_with_etag(self, app_client):
        resp = app_client.get("/api/v1/profile/")
        assert resp.status_code == 200
        assert resp.json() == []
        assert resp.headers["etag"]

    def test_create_requires_if_match(self, app_client):
        assert app_client.post("/api/v1/profile/", json={"name": "p"}).status_code == 428

    def test_create_stale_if_match(self, app_client):
        resp = app_client.post("/api/v1/profile/", json={"name": "p"}, headers={"If-Match": "bogus"})
        assert resp.status_code == 412

    def test_create_assigns_integer_id_and_location(self, app_client, etag):
        resp = _create(app_client, {"name": "studio", "description": "hi"}, etag)
        assert resp.status_code == 201
        profile = resp.json()
        assert profile["id"] == 1 and profile["is_active"] is False
        assert resp.headers["location"] == "/api/v1/profile/1"
        assert profile["created_at"] and profile["updated_at"]
        # next id increments
        resp2 = _create(app_client, {"name": "draft"}, app_client.get("/api/v1/profile/").headers["etag"])
        assert resp2.json()["id"] == 2

    def test_create_explicit_id_and_conflicts(self, app_client, etag):
        assert _create(app_client, {"id": 7, "name": "a"}, etag).status_code == 201
        etag = app_client.get("/api/v1/profile/").headers["etag"]
        assert _create(app_client, {"id": 7, "name": "b"}, etag).status_code == 409  # id clash
        assert _create(app_client, {"name": "a"}, etag).status_code == 409  # name clash

    def test_get_patch_delete(self, app_client, etag):
        assert _create(app_client, {"name": "p"}, etag).status_code == 201
        assert app_client.get("/api/v1/profile/1").json()["name"] == "p"
        assert app_client.get("/api/v1/profile/99").status_code == 404

        etag = app_client.get("/api/v1/profile/1").headers["etag"]
        resp = app_client.patch("/api/v1/profile/1", json={"description": "d"}, headers={"If-Match": etag})
        assert resp.status_code == 200 and resp.json()["description"] == "d"
        assert app_client.patch("/api/v1/profile/1", json={}).status_code == 422
        assert app_client.patch("/api/v1/profile/1", json={"name": "x"}).status_code == 428

        etag = app_client.get("/api/v1/profile/1").headers["etag"]
        assert app_client.delete("/api/v1/profile/1", headers={"If-Match": etag}).status_code == 204
        assert app_client.get("/api/v1/profile/1").status_code == 404
        etag = app_client.get("/api/v1/profile/").headers["etag"]
        assert app_client.delete("/api/v1/profile/1", headers={"If-Match": etag}).status_code == 404


class TestProfileActivation:
    def test_activate_applies_preset_and_single_active(self, app_client, etag):
        preset = {"execution": {"max_workers": 9, "queue_size": 42}}
        assert _create(app_client, {"name": "fast", "settings": preset}, etag).status_code == 201
        etag = app_client.get("/api/v1/profile/").headers["etag"]
        assert _create(app_client, {"name": "lean", "settings": {"execution": {"max_workers": 1}}}, etag).status_code == 201

        etag = app_client.get("/api/v1/profile/").headers["etag"]
        resp = app_client.post("/api/v1/profile/1/activate", headers={"If-Match": etag})
        assert resp.status_code == 200 and resp.json()["is_active"] is True

        profiles = app_client.get("/api/v1/profile/").json()
        assert [p["is_active"] for p in profiles] == [True, False]
        settings = app_client.get("/api/v1/setting/").json()
        assert settings["execution"]["max_workers"] == 9
        assert settings["execution"]["queue_size"] == 42

        etag = app_client.get("/api/v1/profile/").headers["etag"]
        app_client.post("/api/v1/profile/2/activate", headers={"If-Match": etag})
        profiles = app_client.get("/api/v1/profile/").json()
        assert [p["is_active"] for p in profiles] == [False, True]
        assert app_client.get("/api/v1/setting/").json()["execution"]["max_workers"] == 1

    def test_patch_is_active_true_activates(self, app_client, etag):
        assert _create(app_client, {"name": "a", "settings": {"max_event_bytes": 1234}}, etag).status_code == 201
        assert (
            _create(app_client, {"name": "b", "is_active": True}, app_client.get("/api/v1/profile/").headers["etag"]).status_code
            == 201
        )
        # creating with is_active already activated b and applied its (absent) preset
        profiles = app_client.get("/api/v1/profile/").json()
        assert [p["is_active"] for p in profiles] == [False, True]

        etag = app_client.get("/api/v1/profile/1").headers["etag"]
        resp = app_client.patch("/api/v1/profile/1", json={"is_active": True}, headers={"If-Match": etag})
        assert resp.json()["is_active"] is True
        assert app_client.get("/api/v1/setting/").json()["max_event_bytes"] == 1234
        profiles = app_client.get("/api/v1/profile/").json()
        assert [p["is_active"] for p in profiles] == [True, False]

    def test_activate_requires_if_match(self, app_client, etag):
        assert _create(app_client, {"name": "p"}, etag).status_code == 201
        assert app_client.post("/api/v1/profile/1/activate").status_code == 428
        assert app_client.post("/api/v1/profile/1/activate", headers={"If-Match": "bogus"}).status_code == 412
        assert (
            app_client.post(
                "/api/v1/profile/9/activate", headers={"If-Match": app_client.get("/api/v1/profile/").headers["etag"]}
            ).status_code
            == 404
        )
