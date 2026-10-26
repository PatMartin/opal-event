"""Health/readiness reporting: executor liveness and rejected config reloads."""

from __future__ import annotations

import time

from opal_event.zeromq_broker import Broker


def _wait_until(predicate, timeout=3.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class TestHealthReporting:
    def test_healthy_service(self, app_client):
        health = app_client.get("/api/v1/health/")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        ready = app_client.get("/api/v1/health/ready")
        assert ready.status_code == 200
        assert ready.json()["details"]["executor"] == "ok"

    def test_dead_worker_degrades_health_and_fails_readiness(self, app_client):
        service = app_client.app.state.service
        worker = service.executor._workers[0]
        service.executor._worker_events[worker].set()  # ask that one worker to exit
        assert _wait_until(lambda: not worker.is_alive())
        assert service.executor.healthy is False

        assert app_client.get("/api/v1/health/").json()["status"] == "degraded"
        assert app_client.get("/api/v1/health/ready").status_code == 503

    def test_rejected_reload_degrades_health_but_readiness_survives(self, app_client):
        """A rejected config edit must degrade health but NOT readiness — last-good still runs."""
        service = app_client.app.state.service
        config_path = service.config.path
        original = config_path.read_text(encoding="utf-8")

        config_path.write_text("bogus: [unclosed\n", encoding="utf-8")
        assert service.config.reload_from_disk() is False
        assert service.config.reload_failed is True

        assert app_client.get("/api/v1/health/").json()["status"] == "degraded"
        assert app_client.get("/api/v1/health/ready").status_code == 200  # last-good operational

        # Recovery: a subsequent valid reload clears the failure indication.
        recovered = original.replace("queue_size: 10", "queue_size: 8")
        assert recovered != original
        config_path.write_text(recovered, encoding="utf-8")
        assert service.config.reload_from_disk() is True
        assert service.config.reload_failed is False
        assert app_client.get("/api/v1/health/").json()["status"] == "ok"

    def test_database_failure_degrades_and_fails_readiness(self, app_client):
        service = app_client.app.state.service
        service.db.close()
        assert app_client.get("/api/v1/health/").json()["status"] == "degraded"
        assert app_client.get("/api/v1/health/ready").status_code == 503

    def test_broker_failure_degrades_and_fails_readiness(self, app_client, monkeypatch):
        monkeypatch.setattr(Broker, "running", property(lambda self: False))
        assert app_client.get("/api/v1/health/").json()["status"] == "degraded"
        assert app_client.get("/api/v1/health/ready").status_code == 503
