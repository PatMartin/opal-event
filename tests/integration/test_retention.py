"""Retention cleanup at service startup and on the hourly loop."""

from __future__ import annotations

from opal_event.config import ConfigStore
from opal_event.db import Database
from opal_event.service import EventService
from tests.conftest import base_config, write_config


def test_startup_runs_retention_cleanup(tmp_path):
    cfg = base_config(tmp_path)
    db_path = tmp_path / "svc.db"
    cfg["persistence"]["database"] = str(db_path)
    cfg_path = write_config(tmp_path, cfg)

    # Simulate rows left behind by a previous run (retention_days default is 7).
    pre = Database(db_path)
    pre.insert_event({"id": "expired", "topic": "a.b", "source": "t", "timestamp": "2020-01-01T00:00:00Z", "data": {}})
    pre.insert_execution(
        {"id": "expired-x", "action_id": "a1", "status": "succeeded", "trigger": "manual", "created_at": "2020-01-01T00:00:00Z"}
    )
    pre.insert_event({"id": "fresh", "topic": "a.b", "source": "t", "timestamp": "2999-01-01T00:00:00Z", "data": {}})
    pre.close()

    service = EventService(ConfigStore(str(cfg_path)))
    try:
        service.start()
        assert service.db.get_event("expired") is None
        assert service.db.get_execution("expired-x") is None
        assert service.db.get_event("fresh") is not None
    finally:
        service.stop()
