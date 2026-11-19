"""``/api/v1/persona/`` kernel parity (v0.1.0 kernel persona_collection/item/validate):
enabled filtering, strong ETag + mandatory If-Match (428/412), the anchored persona_id
pattern, the validate probe, and publish Idempotency-Key semantics (same key + identical
body replays the original receipt; a different body is 409)."""

from __future__ import annotations

import pytest


@pytest.fixture
def etag(app_client):
    return app_client.get("/api/v1/persona/").headers["etag"]


def _create(client, body, etag):
    return client.post("/api/v1/persona/", json=body, headers={"If-Match": etag})


class TestPersonaKernelParity:
    def test_enabled_filter(self, app_client, etag):
        assert _create(app_client, {"persona_id": "amy", "name": "Amy"}, etag).status_code == 201
        etag = app_client.get("/api/v1/persona/").headers["etag"]
        assert _create(app_client, {"persona_id": "vox", "name": "Vox", "enabled": False}, etag).status_code == 201
        assert [p["persona_id"] for p in app_client.get("/api/v1/persona/").json()] == ["amy", "vox"]
        assert [p["persona_id"] for p in app_client.get("/api/v1/persona/?enabled=true").json()] == ["amy"]
        assert [p["persona_id"] for p in app_client.get("/api/v1/persona/?enabled=false").json()] == ["vox"]

    def test_if_match_preconditions(self, app_client, etag):
        assert app_client.post("/api/v1/persona/", json={"persona_id": "amy", "name": "Amy"}).status_code == 428
        assert _create(app_client, {"persona_id": "amy", "name": "Amy"}, "bogus").status_code == 412
        assert _create(app_client, {"persona_id": "amy", "name": "Amy"}, etag).status_code == 201
        assert app_client.get("/api/v1/persona/amy").headers["etag"]
        assert app_client.patch("/api/v1/persona/amy", json={"name": "A"}, headers={"If-Match": "bogus"}).status_code == 412
        assert app_client.delete("/api/v1/persona/amy").status_code == 428

    def test_persona_id_pattern_anchored(self, app_client, etag):
        for bad in ("-amy", "amy bot", "amy\nbot", "amy!", ""):
            assert _create(app_client, {"persona_id": bad, "name": "x"}, etag).status_code == 422, bad
        for good in ("amy", "a.b-c_d", "9lives"):
            assert _create(app_client, {"persona_id": good, "name": "x"}, etag).status_code == 201, good
            etag = app_client.get("/api/v1/persona/").headers["etag"]

    def test_validate_probe(self, app_client, etag):
        assert _create(app_client, {"persona_id": "amy", "name": "Amy", "voice_id": "en-us-x"}, etag).status_code == 201
        resp = app_client.post("/api/v1/persona/amy/validate")
        assert resp.status_code == 200 and resp.json()["valid"] is True
        assert app_client.post("/api/v1/persona/ghost/validate").status_code == 404

    def test_duplicate_and_conflict(self, app_client, etag):
        assert _create(app_client, {"persona_id": "amy", "name": "Amy"}, etag).status_code == 201
        etag = app_client.get("/api/v1/persona/").headers["etag"]
        assert _create(app_client, {"persona_id": "amy", "name": "Other"}, etag).status_code == 409


class TestPublishIdempotency:
    def test_replay_returns_original_receipt(self, app_client):
        body = {"topic": "t.idem", "data": {"n": 1}}
        headers = {"Idempotency-Key": "key-1"}
        r1 = app_client.post("/api/v1/event/", json=body, headers=headers)
        assert r1.status_code == 202
        r2 = app_client.post("/api/v1/event/", json=body, headers=headers)
        assert r2.status_code == 202
        assert r2.json()["id"] == r1.json()["id"]
        # the event was admitted exactly once
        assert len([e for e in app_client.get("/api/v1/event/?topic=t.idem").json() if e["id"] == r1.json()["id"]]) == 1

    def test_same_key_different_body_is_409(self, app_client):
        headers = {"Idempotency-Key": "key-2"}
        assert app_client.post("/api/v1/event/", json={"topic": "t.idem2", "data": {}}, headers=headers).status_code == 202
        assert app_client.post("/api/v1/event/", json={"topic": "t.other", "data": {}}, headers=headers).status_code == 409

    def test_key_validation(self, app_client):
        assert (
            app_client.post(
                "/api/v1/event/", json={"topic": "t.k", "data": {}}, headers={"Idempotency-Key": "x" * 200}
            ).status_code
            == 422
        )
