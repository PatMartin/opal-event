"""Shared pytest fixtures: isolated YAML config per test (unique DB + ZeroMQ ports) and a ready
FastAPI TestClient wired to the full :class:`~opal_event.service.EventService` stack."""

from __future__ import annotations

import itertools
import socket
import warnings
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from opal_event.app import create_app

warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient`")

_port_counter = itertools.count(19000)


def free_port() -> int:
    while True:
        port = next(_port_counter)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port


def base_config(tmp_path: Path, **overrides) -> dict:
    cfg = {
        "version": 1,
        "server": {"host": "127.0.0.1", "port": free_port()},
        "zeromq": {
            "publishers": f"tcp://127.0.0.1:{free_port()}",
            "subscribers": f"tcp://127.0.0.1:{free_port()}",
            "max_event_bytes": 1048576,
            "curve": {
                "enabled": False,
                "server_public_key_file": None,
                "server_secret_key_file": None,
                "authorized_client_keys_file": None,
            },
        },
        "security": {
            "rest_auth": {"enabled": False, "token_env": "OPAL_EVENT_API_TOKEN"},
            "http_action_allowlist": [{"host": "127.0.0.1", "ports": [9000], "schemes": ["http"]}],
        },
        "execution": {
            "max_workers": 2,
            "queue_size": 10,
            "default_timeout_seconds": 5,
            "overflow": "drop_and_log",
            "retry_enabled": False,
            "max_output_bytes": 65536,
        },
        "persistence": {
            "database": str(tmp_path / "opal_event_test.db"),
            "event_mode": "all",
            "retention_days": 7,
            "execution_history_enabled": True,
        },
        "actions": [
            {
                "id": "echo_action",
                "type": "shell",
                "enabled": True,
                "mode": "exec",
                "executable": "/bin/echo",
                "args": ["{{data.camera}}"],
                "stdin": "none",
                "timeout_seconds": 5,
            },
            {
                "id": "python_action",
                "type": "python",
                "enabled": True,
                "module": "opal_event.plugins.example",
                "function": "handle_event",
                "timeout_seconds": 5,
            },
        ],
        "rules": [
            {
                "id": "front_door_alert",
                "enabled": True,
                "topic": "vision.*",
                "conditions": [{"path": "data.object", "operator": "eq", "value": "person"}],
                "actions": ["echo_action"],
            }
        ],
    }
    cfg.update(overrides)
    return cfg


def write_config(tmp_path: Path, cfg: dict) -> Path:
    path = tmp_path / "opal_event.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def config_path(tmp_path: Path):
    return write_config(tmp_path, base_config(tmp_path))


@pytest.fixture
def app_client(config_path: Path):
    app = create_app(str(config_path))
    with TestClient(app) as client:
        yield client


@pytest.fixture
def make_client(tmp_path: Path):
    """Factory fixture for tests that need custom config overrides."""

    def _make(**overrides):
        path = write_config(tmp_path, base_config(tmp_path, **overrides))
        app = create_app(str(path))
        return TestClient(app)

    return _make
