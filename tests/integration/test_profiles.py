"""``/api/v1/profile/`` catalog (v0.1.0 kernel v3): namespaced profile documents,
strong-ETag preconditions, the one-active local selection, own-compartment-only
patches, foreign compartment preservation, and v2 stored-profile migration."""

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

    def test_create_normalizes_sections_and_allocates_string_id(self, app_client, etag):
        resp = _create(app_client, {"name": "studio", "description": "hi"}, etag)
        assert resp.status_code == 201
        profile = resp.json()
        assert profile["id"] == "1" and profile["is_active"] is False
        assert profile["shared"] == {} and profile["modules"] == {}
        assert resp.headers["location"] == "/api/v1/profile/1"
        assert profile["created_at"] and profile["updated_at"]
        resp2 = _create(app_client, {"name": "draft"}, app_client.get("/api/v1/profile/").headers["etag"])
        assert resp2.json()["id"] == "2"

    def test_create_explicit_id_and_conflicts(self, app_client, etag):
        assert _create(app_client, {"id": "studio", "name": "a"}, etag).status_code == 201
        etag = app_client.get("/api/v1/profile/").headers["etag"]
        assert _create(app_client, {"id": "studio", "name": "b"}, etag).status_code == 409  # id clash
        assert _create(app_client, {"name": "a"}, etag).status_code == 409  # name clash

    def test_shared_and_own_compartment_are_closed(self, app_client, etag):
        assert _create(app_client, {"name": "p", "shared": {"x": 1}}, etag).status_code == 422
        assert _create(app_client, {"name": "p", "modules": {"opal-event": {"x": 1}}}, etag).status_code == 422
        # foreign compartments are accepted and preserved verbatim
        resp = _create(app_client, {"name": "p", "modules": {"opal-say": {"speed": 1.1}}}, etag)
        assert resp.status_code == 201 and resp.json()["modules"]["opal-say"] == {"speed": 1.1}

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


class TestProfilePatchCompartments:
    def test_foreign_compartment_patch_rejected_and_preserved(self, app_client, etag):
        resp = _create(app_client, {"name": "p", "modules": {"opal-say": {"speed": 1.1, "voice_id": "v"}}}, etag)
        assert resp.status_code == 201
        etag = app_client.get("/api/v1/profile/1").headers["etag"]
        assert (
            app_client.patch(
                "/api/v1/profile/1", json={"modules": {"opal-say": {"speed": 2.0}}}, headers={"If-Match": etag}
            ).status_code
            == 422
        )
        # stored foreign contents are untouched
        assert app_client.get("/api/v1/profile/1").json()["modules"]["opal-say"] == {"speed": 1.1, "voice_id": "v"}

    def test_own_compartment_patch_accepted(self, app_client, etag):
        assert _create(app_client, {"name": "p", "modules": {"opal-say": {"speed": 1.1}}}, etag).status_code == 201
        etag = app_client.get("/api/v1/profile/1").headers["etag"]
        # the opal-event compartment is a closed empty schema: an empty patch merges to
        # no change, foreign compartments are preserved
        resp = app_client.patch("/api/v1/profile/1", json={"modules": {"opal-event": {}}}, headers={"If-Match": etag})
        assert resp.status_code == 200
        assert resp.json()["modules"]["opal-say"] == {"speed": 1.1}


class TestProfileActivation:
    def test_activate_is_local_selection_only(self, app_client, etag):
        assert _create(app_client, {"name": "fast"}, etag).status_code == 201
        etag = app_client.get("/api/v1/profile/").headers["etag"]
        assert _create(app_client, {"name": "lean"}, etag).status_code == 201
        settings_before = app_client.get("/api/v1/setting/").json()

        etag = app_client.get("/api/v1/profile/").headers["etag"]
        resp = app_client.post("/api/v1/profile/1/activate", headers={"If-Match": etag})
        assert resp.status_code == 200 and resp.json()["is_active"] is True
        profiles = app_client.get("/api/v1/profile/").json()
        assert [p["is_active"] for p in profiles] == [True, False]
        # v3: the opal-event compartment is empty; activation applies no live settings
        assert app_client.get("/api/v1/setting/").json() == settings_before

        etag = app_client.get("/api/v1/profile/").headers["etag"]
        app_client.post("/api/v1/profile/2/activate", headers={"If-Match": etag})
        assert [p["is_active"] for p in app_client.get("/api/v1/profile/").json()] == [False, True]

    def test_patch_is_active_true_activates(self, app_client, etag):
        assert _create(app_client, {"name": "a"}, etag).status_code == 201
        assert (
            _create(app_client, {"name": "b", "is_active": True}, app_client.get("/api/v1/profile/").headers["etag"]).status_code
            == 201
        )
        assert [p["is_active"] for p in app_client.get("/api/v1/profile/").json()] == [False, True]

        etag = app_client.get("/api/v1/profile/1").headers["etag"]
        resp = app_client.patch("/api/v1/profile/1", json={"is_active": True}, headers={"If-Match": etag})
        assert resp.json()["is_active"] is True
        assert [p["is_active"] for p in app_client.get("/api/v1/profile/").json()] == [True, False]

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


class TestStoredProfileMigration:
    def test_v2_stored_profile_loads(self, make_client):
        """A v1.7.1 stored profile (integer id, flat settings preset) upgrades on load:
        id stringifies, shared/modules normalize to empty objects, the preset is dropped."""
        v2 = [{"id": 1, "name": "legacy", "is_active": True, "settings": {"execution": {"max_workers": 9}}}]
        with make_client(profiles=v2) as client:
            profiles = client.get("/api/v1/profile/").json()
            assert profiles[0]["id"] == "1"
            assert profiles[0]["name"] == "legacy" and profiles[0]["is_active"] is True
            assert profiles[0]["shared"] == {} and profiles[0]["modules"] == {}
            assert "settings" not in profiles[0]

    def test_v2_foreign_modules_preserved(self, make_client):
        """Foreign compartments survive load/patch/save round-trips verbatim."""
        mods = {"opal-say": {"speed": 1.25}}
        with make_client(profiles=[{"id": "x", "name": "p", "is_active": False, "shared": {}, "modules": mods}]) as client:
            snap = client.post("/api/v1/config/save", json={"destination": "response"}).json()["snapshot"]
            assert snap["resources"]["profiles"][0]["modules"] == mods
