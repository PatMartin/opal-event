"""CLI ``service`` commands: REST-first with local pidfile fallback when the API is unreachable."""

from __future__ import annotations

import httpx
from typer.testing import CliRunner

from opal_event.cli import main as cli_main
from opal_event.cli.client import ApiClient

runner = CliRunner()


def _dead_client(base_url, token=None, timeout=30.0):
    """ApiClient whose transport always raises ConnectError, simulating a stopped service."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    return ApiClient(base_url, token, timeout, http=httpx.Client(base_url=base_url, transport=httpx.MockTransport(handler)))


def test_service_start_cold_starts_via_local_manager(monkeypatch):
    """When the API is unreachable, `service start` must spawn the managed server locally."""
    monkeypatch.setattr(cli_main, "ApiClient", _dead_client)
    launched = {}

    class FakeManager:
        def __init__(self, config_path=None):
            launched["config"] = config_path

        def launch(self, append=False):
            launched["append"] = append

            class Proc:
                pid = 4242

            return Proc()

    monkeypatch.setattr(cli_main, "_manager", FakeManager)

    result = runner.invoke(cli_main.event_app, ["service", "start"])

    assert result.exit_code == 0, result.output
    assert launched["append"] is False
    assert "started in background" in result.output


def test_service_log_falls_back_to_local_file(monkeypatch):
    """When the API is unreachable, `service log` reads the local managed log."""
    monkeypatch.setattr(cli_main, "ApiClient", _dead_client)

    class FakeManager:
        def __init__(self, config_path=None):
            pass

        def tail_log(self, lines):
            return {"lines": [f"line-{i}" for i in range(lines)]}

    monkeypatch.setattr(cli_main, "_manager", FakeManager)

    result = runner.invoke(cli_main.event_app, ["service", "log", "-n", "3"])

    assert result.exit_code == 0, result.output
    assert "line-0" in result.output
    assert "line-2" in result.output
    assert "line-3" not in result.output


def test_service_stop_reports_local_state_when_api_unreachable(monkeypatch):
    """When the API is unreachable, `service stop` stops the tracked pid locally."""
    monkeypatch.setattr(cli_main, "ApiClient", _dead_client)

    class FakeManager:
        def __init__(self, config_path=None):
            pass

        def stop(self):
            return {"previous_pid": 777}, True

    monkeypatch.setattr(cli_main, "_manager", FakeManager)

    result = runner.invoke(cli_main.event_app, ["service", "stop"])

    assert result.exit_code == 0, result.output
    assert "pid 777" in result.output
