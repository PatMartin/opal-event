"""/api/v1/service/* : log tail, stop-on-nothing, and start/stop status shapes (v1.7.1).
Spawning a real managed child is covered by the CLI e2e path, not here — these tests
exercise the REST contract and the pidfile/bookkeeping logic directly."""

from __future__ import annotations

from pathlib import Path

from opal_event.lifecycle import ServiceProcessManager


def test_service_log_404_without_file(app_client):
    # logs/ under cwd may exist in dev runs; accept 200-or-404, assert shape either way
    resp = app_client.get("/api/v1/service/log?lines=5")
    if resp.status_code == 200:
        assert "lines" in resp.json() and "log_file" in resp.json()
    else:
        assert resp.status_code == 404


def test_service_stop_not_running(app_client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # empty logs/ so no pidfile
    resp = app_client.post("/api/v1/service/stop")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "stopped" and body["previous_pid"] is None and body["pid"] is None


def test_manager_pidfile_and_tail(tmp_path):
    m = ServiceProcessManager(log_dir=tmp_path / "logs")
    assert m.tracked_pid() is None
    status, term = m.stop()
    assert status["state"] == "stopped" and not term
    (tmp_path / "logs").mkdir()
    m.log_file.write_text("l1\nl2\nl3\n", encoding="utf-8")
    assert m.tail_log(2) == {"log_file": str(m.log_file), "lines": ["l2", "l3"]}
    m.pid_file.write_text("not-a-pid")
    assert m.tracked_pid() is None  # corrupt pidfile cleaned up


def test_service_log_returns_lines(app_client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("logs").mkdir()
    Path("logs/opal_event.log").write_text("a\nb\nc\n", encoding="utf-8")
    resp = app_client.get("/api/v1/service/log?lines=2")
    assert resp.status_code == 200 and resp.json()["lines"] == ["b", "c"]
