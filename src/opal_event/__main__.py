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
    app = create_app(args.config)

    from opal_event.config import ConfigStore

    cfg = ConfigStore(args.config)
    uvicorn.run(app, host=cfg.settings.server.host, port=cfg.settings.server.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    sys.exit(main())
