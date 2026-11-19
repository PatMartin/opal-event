"""``/api/v1/config`` snapshots (v0.1.0): save (managed/response destinations), atomic
load under mandatory If-Match (428/412), non-mutating validate, module/format guards,
and atomic recovery — a failed load preserves the prior configuration."""

from __future__ import annotations


def _save(client, destination="response"):
    resp = client.post("/api/v1/config/save", json={"destination": destination})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _load(client, snapshot=None, etag=None, body="unset"):
    headers = {} if etag is None else {"If-Match": etag}
    payload = {} if body == "unset" else body
    if body == "unset" and snapshot is not None:
        payload = {"snapshot": snapshot}
    return client.post("/api/v1/config/load", json=payload, headers=headers)


class TestConfigSave:
    def test_save_response_destination(self, app_client):
        data = _save(app_client)
        assert data["managed"] is False
        snap = data["snapshot"]
        assert snap["format_version"] == "1.0.0" and snap["module"] == "opal-event" and snap["created_at"]
        assert set(snap["resources"]) == {"profiles", "personas", "rules", "actions"}

    def test_save_managed_then_load_omitted_snapshot(self, app_client):
        assert _save(app_client, "managed")["managed"] is True
        etag = app_client.get("/api/v1/setting/").headers["etag"]
        resp = _load(app_client, etag=etag)
        assert resp.status_code == 200 and resp.json()["applied"] is True

    def test_load_managed_missing_is_404(self, app_client):
        etag = app_client.get("/api/v1/setting/").headers["etag"]
        assert _load(app_client, etag=etag).status_code == 404


class TestConfigLoad:
    def test_load_requires_if_match_and_rejects_stale(self, app_client):
        snap = _save(app_client)["snapshot"]
        assert _load(app_client, snapshot=snap).status_code == 428
        assert _load(app_client, snapshot=snap, etag="definitely-stale").status_code == 412

    def test_explicit_null_snapshot_invalid(self, app_client):
        etag = app_client.get("/api/v1/setting/").headers["etag"]
        assert _load(app_client, etag=etag, body={"snapshot": None}).status_code == 422

    def test_save_edit_load_restores_atomically(self, app_client):
        snap = _save(app_client)["snapshot"]
        # mutate: add a rule and override a setting
        etag = app_client.get("/api/v1/rule/").headers["etag"]
        rule = {"id": "r1", "enabled": True, "topic": "t", "conditions": [], "actions": []}
        assert app_client.post("/api/v1/rule/", json=rule, headers={"If-Match": etag}).status_code == 201
        etag = app_client.get("/api/v1/setting/").headers["etag"]
        assert (
            app_client.put("/api/v1/setting/execution.queue_size", json={"value": 321}, headers={"If-Match": etag}).status_code
            == 200
        )

        etag = app_client.get("/api/v1/setting/").headers["etag"]
        resp = _load(app_client, snapshot=snap, etag=etag)
        assert resp.status_code == 200 and resp.json()["applied"] is True
        assert app_client.get("/api/v1/rule/r1").status_code == 404
        # the fixture YAML persists queue_size=10, so the snapshot restores that override
        rec = app_client.get("/api/v1/setting/execution.queue_size").json()
        assert rec["value"] == 10 and rec["has_override"] is True

    def test_failed_load_preserves_prior_config(self, app_client):
        etag = app_client.get("/api/v1/rule/").headers["etag"]
        rule = {"id": "keep", "enabled": True, "topic": "t", "conditions": [], "actions": []}
        assert app_client.post("/api/v1/rule/", json=rule, headers={"If-Match": etag}).status_code == 201

        bad = _save(app_client)["snapshot"]
        bad["module"] = "opal-other"
        etag = app_client.get("/api/v1/setting/").headers["etag"]
        assert _load(app_client, snapshot=bad, etag=etag).status_code == 422
        assert app_client.get("/api/v1/rule/keep").status_code == 200

        bad2 = _save(app_client)["snapshot"]
        bad2["resources"]["mystery"] = []
        etag = app_client.get("/api/v1/setting/").headers["etag"]
        assert _load(app_client, snapshot=bad2, etag=etag).status_code == 422
        assert app_client.get("/api/v1/rule/keep").status_code == 200

    def test_unknown_setting_key_rejected(self, app_client):
        snap = _save(app_client)["snapshot"]
        snap["settings"]["nope.key"] = 1
        etag = app_client.get("/api/v1/setting/").headers["etag"]
        assert _load(app_client, snapshot=snap, etag=etag).status_code == 422

    def test_load_profiles_round_trip(self, app_client):
        etag = app_client.get("/api/v1/profile/").headers["etag"]
        app_client.post("/api/v1/profile/", json={"name": "p1"}, headers={"If-Match": etag})
        snap = _save(app_client)["snapshot"]
        assert snap["resources"]["profiles"][0]["name"] == "p1"
        etag = app_client.get("/api/v1/setting/").headers["etag"]
        assert _load(app_client, snapshot=snap, etag=etag).status_code == 200
        assert app_client.get("/api/v1/profile/1").json()["name"] == "p1"


class TestConfigValidate:
    def test_validate_live_config_when_snapshot_omitted(self, app_client):
        resp = app_client.post("/api/v1/config/validate", json={})
        assert resp.status_code == 200 and resp.json()["valid"] is True

    def test_validate_candidate_reports_issues(self, app_client):
        snap = _save(app_client)["snapshot"]
        snap["resources"]["rules"] = [{"id": "r", "enabled": True, "topic": "t", "conditions": [], "actions": ["ghost"]}]
        resp = app_client.post("/api/v1/config/validate", json={"snapshot": snap})
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert any(w["code"] == "dangling_reference" for w in body["warnings"])

    def test_validate_does_not_mutate(self, app_client):
        rev_before = app_client.get("/api/v1/setting/").headers["etag"]
        bad = _save(app_client)["snapshot"]
        bad["module"] = "opal-other"
        resp = app_client.post("/api/v1/config/validate", json={"snapshot": bad})
        assert resp.status_code == 200 and resp.json()["valid"] is False
        assert resp.json()["errors"][0]["code"] == "wrong_module"
        assert app_client.get("/api/v1/setting/").headers["etag"] == rev_before
