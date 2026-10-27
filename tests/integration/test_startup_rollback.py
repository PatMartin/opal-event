"""Fault-injection tests: a failed EventService.start() must release every resource it opened —
executor workers, ZeroMQ sockets/context, broker threads, and the SQLite connection — so a fresh
service can start on the same configuration afterwards."""

from __future__ import annotations

import socket
import sqlite3
import threading

import pytest
import zmq

from opal_event.config import ConfigStore
from opal_event.service import EventService
from tests.conftest import base_config, write_config


def _svc(tmp_path):
    cfg_path = write_config(tmp_path, base_config(tmp_path))
    return EventService(ConfigStore(str(cfg_path))), cfg_path


def _squat(endpoint: str) -> socket.socket:
    """Hold the TCP port of a tcp://127.0.0.1:N endpoint so a ZMQ bind on it fails."""
    port = int(endpoint.rsplit(":", 1)[1])
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    s.bind(("127.0.0.1", port))
    s.listen()
    return s


def _assert_no_leaks(service: EventService) -> None:
    broker = service.broker
    for sock in (broker._xsub, broker._xpub, broker._observer_sub):
        assert sock is None or sock.closed, "leaked ZeroMQ socket"
    assert broker._proxy_thread is None or not broker._proxy_thread.is_alive()
    assert broker._observer_thread is None or not broker._observer_thread.is_alive()
    assert broker._publisher_thread is None or not broker._publisher_thread.is_alive()
    assert not any(w.is_alive() for w in service.executor._workers)
    svc_threads = [t.name for t in threading.enumerate() if t.name.startswith("opal-event-") and t.is_alive()]
    assert svc_threads == [], f"leaked threads: {svc_threads}"
    with pytest.raises(sqlite3.ProgrammingError):
        service.db.get_event("anything")


class TestStartupRollback:
    def test_first_bind_failure_releases_everything(self, tmp_path):
        service, _ = _svc(tmp_path)
        squatter = _squat(service.broker._settings.publishers)
        try:
            with pytest.raises(zmq.ZMQError):
                service.start()
            _assert_no_leaks(service)
        finally:
            squatter.close()

    def test_second_bind_failure_releases_bound_first_socket(self, tmp_path):
        service, _ = _svc(tmp_path)
        squatter = _squat(service.broker._settings.subscribers)
        try:
            with pytest.raises(zmq.ZMQError):
                service.start()
            _assert_no_leaks(service)
        finally:
            squatter.close()

    def test_executor_start_failure_releases_broker_and_db(self, tmp_path, monkeypatch):
        service, _ = _svc(tmp_path)
        monkeypatch.setattr(type(service.executor), "start", lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
        with pytest.raises(RuntimeError, match="boom"):
            service.start()
        _assert_no_leaks(service)

    def test_rollback_is_idempotent(self, tmp_path):
        service, _ = _svc(tmp_path)
        squatter = _squat(service.broker._settings.publishers)
        try:
            with pytest.raises(zmq.ZMQError):
                service.start()
            service.stop()
            service.stop()
        finally:
            squatter.close()

    def test_successful_startup_after_failed_attempt(self, tmp_path):
        service, cfg_path = _svc(tmp_path)
        squatter = _squat(service.broker._settings.publishers)
        with pytest.raises(zmq.ZMQError):
            service.start()
        squatter.close()

        # Ports and the DB were released, so a fresh service can bind the same config.
        service2 = EventService(ConfigStore(str(cfg_path)))
        try:
            service2.start()
            assert service2.is_ready()
        finally:
            service2.stop()
        assert not service2.broker.running
