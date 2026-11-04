"""Contract tests: action metadata fields + declared input_schema validation.

v1.4.1 self-describing actions: description/input_schema/keywords/suggested_prompts
must round-trip through create/show/patch/list, and a declared input_schema is
enforced against event.data at execute time — before any side effects. Legacy
actions without schemas retain prior behavior.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from opal_event.models import Action

_ADAPTER = TypeAdapter(Action)

_META = {
    "description": "Announce a person detected by a camera.",
    "keywords": ["camera", "announce"],
    "suggested_prompts": ["announce visitors"],
}
_SCHEMA = {"type": "object", "properties": {"camera": {"type": "string"}}, "required": ["camera"]}


def _shell_action(**extra) -> dict:
    return {
        "id": "schema_action",
        "type": "shell",
        "enabled": True,
        "mode": "exec",
        "executable": "/bin/echo",
        "input_schema": _SCHEMA,
        **_META,
        **extra,
    }


# -- model layer ----------------------------------------------------------------


@pytest.mark.parametrize("atype", ["shell", "http", "python"])
def test_metadata_fields_round_trip(atype) -> None:
    base = {"id": "a", "type": atype, "enabled": True, **_META, "input_schema": _SCHEMA}
    if atype == "shell":
        base.update(mode="exec", executable="/x")
    elif atype == "http":
        base.update(method="POST", url="http://x/y", body_mode="none")
    else:
        base.update(module="mod.fn", function="run")
    action = _ADAPTER.validate_python(base)
    for field, value in _META.items():
        assert getattr(action, field) == value
    assert action.input_schema == _SCHEMA


def test_legacy_action_fields_optional() -> None:
    action = _ADAPTER.validate_python({"id": "a", "type": "shell", "enabled": True, "mode": "exec", "executable": "/x"})
    assert action.description is None and action.input_schema is None


@pytest.mark.parametrize(
    "bad",
    [
        {"properties": {"x": {"$ref": "https://evil.example/x.json"}}},
        {"$ref": "http://x/y"},
        "not-a-dict",
        {"type": 123},
    ],
)
def test_malformed_or_remote_schema_rejected(bad) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _ADAPTER.validate_python(
            {"id": "a", "type": "shell", "enabled": True, "mode": "exec", "executable": "/x", "input_schema": bad}
        )


# -- REST layer -------------------------------------------------------------------


def test_create_show_patch_metadata(app_client: TestClient) -> None:
    created = app_client.post("/api/v1/actions/", json=_shell_action())
    assert created.status_code in (200, 201), created.text
    shown = app_client.get("/api/v1/actions/schema_action")
    etag = shown.headers.get("etag")
    body = shown.json()
    for field, value in _META.items():
        assert body[field] == value
    assert body["input_schema"] == _SCHEMA

    patched = app_client.patch(
        "/api/v1/actions/schema_action",
        json={"description": "Updated purpose", "keywords": ["new"]},
        headers={"If-Match": etag} if etag else {},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["description"] == "Updated purpose"
    assert patched.json()["keywords"] == ["new"]


def test_execute_validates_declared_schema(app_client: TestClient) -> None:
    assert app_client.post("/api/v1/actions/", json=_shell_action()).status_code in (200, 201)
    missing = app_client.post("/api/v1/actions/schema_action/execute", json={"event": {"topic": "t", "data": {}}})
    assert missing.status_code == 422, missing.text
    wrong_type = app_client.post(
        "/api/v1/actions/schema_action/execute", json={"event": {"topic": "t", "data": {"camera": 42}}}
    )
    assert wrong_type.status_code == 422
    ok = app_client.post("/api/v1/actions/schema_action/execute", json={"event": {"topic": "t", "data": {"camera": "front"}}})
    assert ok.status_code == 202


def test_execute_legacy_action_unchanged(app_client: TestClient) -> None:
    # echo_action from base config has no input_schema -> executes regardless of data shape
    response = app_client.post("/api/v1/actions/echo_action/execute", json={"event": {"topic": "t", "data": {"anything": True}}})
    assert response.status_code == 202


def test_execute_rejects_before_side_effect(app_client: TestClient) -> None:
    assert app_client.post("/api/v1/actions/", json=_shell_action()).status_code in (200, 201)
    before = app_client.get("/api/v1/executions/").json() if app_client.get("/api/v1/executions/").status_code == 200 else {}
    rejected = app_client.post("/api/v1/actions/schema_action/execute", json={"event": {"topic": "t", "data": {}}})
    assert rejected.status_code == 422
    after = app_client.get("/api/v1/executions/")
    if after.status_code == 200:
        assert after.json() == before or len(after.json().get("items", after.json())) == len(
            before.get("items", before) if isinstance(before, dict) else before
        )
