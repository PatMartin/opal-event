"""`opal event publish` + `opal event history ...`: publishing is separate from the
stored-event catalog commands; legacy top-level event verbs keep working."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx
import pytest
from typer.testing import CliRunner

from opal_event.cli import main as cli_main
from opal_event.cli.client import ApiClient

runner = CliRunner()

EVENT = {"id": "evt_1", "topic": "vision.object.detected", "source": "test", "timestamp": "2025-01-01T00:00:00Z"}

RESPONSES = {
    "/api/v1/event/": (200, [EVENT]),
    "/api/v1/event/evt_1": (200, EVENT),
    "/api/v1/topic/": (200, {"items": ["vision.object.detected"]}),
}


@dataclass
class FakeApi:
    seen: list[tuple[str, str]] = field(default_factory=list)


@pytest.fixture
def api(monkeypatch) -> FakeApi:
    fake = FakeApi()

    def handler(request: httpx.Request) -> httpx.Response:
        fake.seen.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(202, json={"id": "evt_1", "status": "accepted", "published_at": "2025-01-01T00:00:00Z"})
        if request.method == "DELETE":
            return httpx.Response(204)
        status, body = RESPONSES[request.url.path]
        return httpx.Response(status, json=body)

    def make_client(base_url, token=None, timeout=30.0):
        return ApiClient(base_url, token, timeout, http=httpx.Client(base_url=base_url, transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(cli_main, "ApiClient", make_client)
    return fake


def test_publish_posts_event(api):
    result = runner.invoke(cli_main.event_app, ["publish", "vision.object.detected", "--data-json", '{"n":1}'])
    assert result.exit_code == 0, result.output
    assert api.seen == [("POST", "/api/v1/event/")]


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["history", "list"], ("GET", "/api/v1/event/")),
        (["history", "show", "evt_1"], ("GET", "/api/v1/event/evt_1")),
        (["history", "remove", "evt_1"], ("DELETE", "/api/v1/event/evt_1")),
        (["history", "clear"], ("DELETE", "/api/v1/event/")),
        (["history", "topics"], ("GET", "/api/v1/topic/")),
    ],
)
def test_history_group_dispatches(api, args, expected):
    result = runner.invoke(cli_main.event_app, args)
    assert result.exit_code == 0, result.output
    assert api.seen == [expected]


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["list"], ("GET", "/api/v1/event/")),
        (["get", "evt_1"], ("GET", "/api/v1/event/evt_1")),
        (["delete", "evt_1"], ("DELETE", "/api/v1/event/evt_1")),
        (["clear"], ("DELETE", "/api/v1/event/")),
        (["topics"], ("GET", "/api/v1/topic/")),
    ],
)
def test_legacy_event_verbs_still_work(api, args, expected):
    result = runner.invoke(cli_main.event_app, args)
    assert result.exit_code == 0, result.output
    assert api.seen == [expected]


def test_remove_deletes_one_event_not_the_collection(api):
    result = runner.invoke(cli_main.event_app, ["history", "remove", "evt_1"])
    assert result.exit_code == 0
    assert api.seen == [("DELETE", "/api/v1/event/evt_1")]  # item, not collection
