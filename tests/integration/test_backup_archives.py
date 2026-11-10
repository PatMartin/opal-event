"""Managed backup archives (/api/v1/backup/list|create|download|restore|delete) and the
v1.7.1 import contract: bare ConfigBackup body, 428/412 If-Match, empty body -> ./config/."""

from __future__ import annotations


class TestBackupArchives:
    def test_list_empty(self, app_client):
        resp = app_client.get("/api/v1/backup/list")
        assert resp.status_code == 200 and resp.json() == []

    def test_create_list_download_delete_cycle(self, app_client):
        resp = app_client.post("/api/v1/backup/create")
        assert resp.status_code == 201
        filename = resp.json()["filename"]
        assert filename.startswith("opal-event-") and filename.endswith(".tar.gz")
        assert resp.headers["location"] == f"/api/v1/backup/download/{filename}"

        listing = app_client.get("/api/v1/backup/list").json()
        assert any(b["filename"] == filename for b in listing)
        entry = next(b for b in listing if b["filename"] == filename)
        assert entry["size_bytes"] > 0 and entry["created_at"]

        dl = app_client.get(f"/api/v1/backup/download/{filename}")
        assert dl.status_code == 200 and dl.content[:2] == b"\x1f\x8b"  # gzip magic

        assert app_client.delete(f"/api/v1/backup/{filename}").status_code == 204
        assert app_client.get(f"/api/v1/backup/download/{filename}").status_code == 404
        assert app_client.delete("/api/v1/backup/nope.tar.gz").status_code == 404
        assert app_client.get("/api/v1/backup/download/..%2Fevil").status_code in (404, 422)

    def test_restore_round_trip(self, app_client):
        filename = app_client.post("/api/v1/backup/create").json()["filename"]
        # mutate: add a rule + bump a setting, then restore should revert both
        etag = app_client.get("/api/v1/rule/").headers["etag"]
        rule = {"id": "r1", "enabled": True, "topic": "t", "conditions": [], "actions": []}
        app_client.post("/api/v1/rule/", json=rule, headers={"If-Match": etag})

        etag = app_client.get("/api/v1/setting/").headers["etag"]
        resp = app_client.post(f"/api/v1/backup/restore/{filename}", headers={"If-Match": etag})
        assert resp.status_code == 200 and "revision" in resp.json()
        assert app_client.get("/api/v1/rule/r1").status_code == 404

    def test_restore_requires_if_match_and_validates(self, app_client):
        filename = app_client.post("/api/v1/backup/create").json()["filename"]
        assert app_client.post(f"/api/v1/backup/restore/{filename}").status_code == 428
        assert app_client.post(f"/api/v1/backup/restore/{filename}", headers={"If-Match": "bogus"}).status_code == 412
        assert (
            app_client.post(
                "/api/v1/backup/restore/nope.tar.gz", headers={"If-Match": app_client.get("/api/v1/setting/").headers["etag"]}
            ).status_code
            == 404
        )


class TestImportContract:
    def test_import_bare_bundle_428_412(self, app_client):
        backup = app_client.get("/api/v1/backup/export").json()
        assert app_client.post("/api/v1/backup/import", json=backup).status_code == 428
        assert app_client.post("/api/v1/backup/import", json=backup, headers={"If-Match": "bogus"}).status_code == 412
        etag = app_client.get("/api/v1/setting/").headers["etag"]
        resp = app_client.post("/api/v1/backup/import", json=backup, headers={"If-Match": etag})
        assert resp.status_code == 200 and "revision" in resp.json()

    def test_legacy_wrapper_rejected(self, app_client):
        backup = app_client.get("/api/v1/backup/export").json()
        etag = app_client.get("/api/v1/setting/").headers["etag"]
        assert app_client.post("/api/v1/backup/import", json={"backup": backup}, headers={"If-Match": etag}).status_code == 422

    def test_import_empty_body_reloads_config(self, app_client):
        etag = app_client.get("/api/v1/setting/").headers["etag"]
        resp = app_client.post("/api/v1/backup/import", headers={"If-Match": etag})
        assert resp.status_code == 200
        assert "revision" in resp.json() and "pending_restart" in resp.json()

    def test_export_includes_profiles(self, app_client):
        etag = app_client.get("/api/v1/profile/").headers["etag"]
        app_client.post("/api/v1/profile/", json={"name": "p1"}, headers={"If-Match": etag})
        backup = app_client.get("/api/v1/backup/export").json()
        assert backup["profiles"][0]["name"] == "p1" and backup["exported_at"]
        # round-trip: import the exported bundle wholesale
        etag = app_client.get("/api/v1/setting/").headers["etag"]
        assert app_client.post("/api/v1/backup/import", json=backup, headers={"If-Match": etag}).status_code == 200
        assert app_client.get("/api/v1/profile/1").json()["name"] == "p1"
