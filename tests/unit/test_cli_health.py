"""CLI ``health`` command: default summary plus mutually exclusive ``--live`` / ``--ready`` probes."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx
import pytest
from typer.testing import CliRunner

from opal_event.cli import main as cli_main
from opal_event.cli.client import ApiClient

runner = CliRunner()

RESPONSES = {
    "/api/v1/health/": (200, {"status": "ok", "broker_running": True, "database_ready": True, "pending_restart": False}),
    "/api/v1/health/live": (200, {"status": "ok", "details": None}),
    "/api/v1/health/ready": (200, {"status": "ok", "details": {"broker": "ok", "database": "ok"}}),
}


@dataclass
class FakeApi:
    responses: dict[str, tuple[int, dict]] = field(default_factory=lambda: dict(RESPONSES))
    seen: list[tuple[str, str]] = field(default_factory=list)


@pytest.fixture
def api(monkeypatch) -> FakeApi:
    """Routes the CLI's real ApiClient through an httpx mock transport, recording each request."""
    fake = FakeApi()

    def handler(request: httpx.Request) -> httpx.Response:
        fake.seen.append((request.method, request.url.path))
        status, body = fake.responses[request.url.path]
        return httpx.Response(status, json=body)

    def make_client(base_url, token=None, timeout=30.0):
        return ApiClient(base_url, token, timeout, http=httpx.Client(base_url=base_url, transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(cli_main, "ApiClient", make_client)
    return fake


@pytest.mark.parametrize(
    ("args", "path"),
    [
        ([], "/api/v1/health/"),
        (["--live"], "/api/v1/health/live"),
        (["--ready"], "/api/v1/health/ready"),
    ],
)
def test_health_calls_expected_endpoint(api, args, path):
    result = runner.invoke(cli_main.event_app, ["health", *args])
    assert result.exit_code == 0, result.output
    assert api.seen == [("GET", path)]
    assert json.loads(result.output) == RESPONSES[path][1]


def test_live_and_ready_are_mutually_exclusive(api):
    result = runner.invoke(cli_main.event_app, ["health", "--live", "--ready"])
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output
    assert api.seen == []


def test_ready_exits_nonzero_when_not_ready(api):
    api.responses["/api/v1/health/ready"] = (503, {"code": "unavailable", "message": "broker down"})
    result = runner.invoke(cli_main.event_app, ["health", "--ready"])
    assert result.exit_code == 1
    assert "503" in result.output


@pytest.mark.parametrize("old_name", ["health-live", "health-ready"])
def test_old_probe_commands_are_removed(api, old_name):
    result = runner.invoke(cli_main.event_app, [old_name])
    assert result.exit_code == 2
    assert "No such command" in result.output


class TestCommandTree:
    def test_tree_renders_commands_and_params(self):
        result = runner.invoke(cli_main.app, ["event", "tree"])
        assert result.exit_code == 0
        out = result.output
        assert out.startswith("opal event")
        # commands and groups are present
        for line in ("|-- publish", "|-- rules", "`-- personas"):
            assert line in out
        # positionals render as <name>, required options bare, optional bracketed
        assert "|-- <topic>" in out
        assert "|-- --data-json <str>" in out
        assert "|-- [ --timeout <float> =30.0 ]" in out
        # --help and typer's completion boilerplate are skipped
        assert "--help" not in out
        assert "--install-completion" not in out

    def test_tree_tracks_app_changes(self):
        # rendered from the live Typer app: adding a command changes the output
        import typer

        tree_before = cli_main._render_command_tree()
        marker_app = typer.Typer()

        @marker_app.command("zz_marker")
        def _marker() -> None:  # pragma: no cover - registered then removed
            pass

        cli_main.event_app.add_typer(marker_app, name="zz_marker")
        try:
            tree_after = cli_main._render_command_tree()
            assert "zz_marker" in tree_after.splitlines()[-2] + tree_after.splitlines()[-1]
            assert tree_before != tree_after
        finally:
            cli_main.event_app.registered_groups = [
                g for g in cli_main.event_app.registered_groups if g.name != "zz_marker"
            ]
