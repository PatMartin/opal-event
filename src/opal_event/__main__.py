"""``opal-event-svc`` entrypoint: runs the FastAPI app with Uvicorn using the configured host/port."""

from __future__ import annotations

import argparse
import logging
import os
import sys

import uvicorn

from opal_event.app import create_app

DEFAULT_CONFIG_PATH = os.environ.get("OPAL_EVENT_CONFIG", "config/opal_event.yaml")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opal-event-svc", description="Run the Opal Event REST/ZeroMQ service")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="path to the YAML configuration file")
    parser.add_argument("--log-level", default="info", choices=["critical", "error", "warning", "info", "debug"])
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper())

    # Managed-server protocol (v1.7.1 kernel `service` tag): when launched by
    # POST /api/v1/service/start with a restart pending, hold off binding until the
    # parent pids have exited so the port frees cleanly.
    wait_pids = os.environ.get("OPAL_EVENT_WAIT_FOR_PID")
    if wait_pids:
        from opal_event.lifecycle import wait_for_pids

        wait_for_pids([p for p in wait_pids.split(",") if p.strip()])

    app = create_app(args.config)

    from opal_event.config import ConfigStore

    cfg = ConfigStore(args.config)

    # Record the real server pid for `service stop` — the spawning CLI's Popen pid can
    # differ (venv python.exe is a shim that re-execs the interpreter). The managed
    # lifecycle spawner overrides the location via OPAL_EVENT_PID_FILE.
    from pathlib import Path

    pid_file = Path(os.environ.get("OPAL_EVENT_PID_FILE") or Path("logs") / "opal_event.pid")
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    try:
        uvicorn.run(app, host=cfg.settings.server.host, port=cfg.settings.server.port, log_level=args.log_level)
    finally:
        pid_file.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
