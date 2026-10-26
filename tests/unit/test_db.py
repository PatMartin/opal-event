from opal_event.db import Database


def make_event(event_id: str, topic: str = "vision.object.detected", source: str = "test") -> dict:
    return {"id": event_id, "topic": topic, "source": source, "timestamp": "2026-01-01T00:00:00Z", "data": {"n": event_id}}


class TestDatabaseEvents:
    def test_insert_and_get_event(self, tmp_path):
        db = Database(tmp_path / "t.db")
        db.insert_event(make_event("e1"))
        event = db.get_event("e1")
        assert event is not None
        assert event["topic"] == "vision.object.detected"
        assert event["data"] == {"n": "e1"}

    def test_get_missing_event_returns_none(self, tmp_path):
        db = Database(tmp_path / "t.db")
        assert db.get_event("missing") is None

    def test_list_events_pagination(self, tmp_path):
        db = Database(tmp_path / "t.db")
        for i in range(5):
            db.insert_event(make_event(f"e{i}"))
        page1, cursor1 = db.list_events(limit=2)
        assert len(page1) == 2
        assert cursor1 is not None
        page2, cursor2 = db.list_events(cursor=cursor1, limit=2)
        assert len(page2) == 2
        ids = {e["id"] for e in page1} | {e["id"] for e in page2}
        assert len(ids) == 4

    def test_list_events_filters_by_topic(self, tmp_path):
        db = Database(tmp_path / "t.db")
        db.insert_event(make_event("e1", topic="vision.object.detected"))
        db.insert_event(make_event("e2", topic="intent.recognized"))
        items, _ = db.list_events(topic="intent.recognized")
        assert [e["id"] for e in items] == ["e2"]

    def test_list_topics_distinct(self, tmp_path):
        db = Database(tmp_path / "t.db")
        db.insert_event(make_event("e1", topic="a.b"))
        db.insert_event(make_event("e2", topic="a.b"))
        db.insert_event(make_event("e3", topic="c.d"))
        assert db.list_topics() == ["a.b", "c.d"]

    def test_clear_events(self, tmp_path):
        db = Database(tmp_path / "t.db")
        db.insert_event(make_event("e1", topic="a.b"))
        db.insert_event(make_event("e2", topic="c.d"))
        assert len(db.list_topics()) == 2
        events, _ = db.list_events()
        assert len(events) == 2

        db.clear_events()

        events_after, _ = db.list_events()
        assert len(events_after) == 0
        assert db.list_topics() == []

    def test_delete_event(self, tmp_path):
        db = Database(tmp_path / "t.db")
        db.insert_event(make_event("e1", topic="a.b"))
        db.insert_event(make_event("e2", topic="c.d"))
        assert db.delete_event("e1") is True
        assert db.get_event("e1") is None
        assert db.get_event("e2") is not None
        assert db.delete_event("e1") is False
        assert db.delete_event("non_existent") is False


class TestDatabaseExecutions:
    def test_insert_update_get_execution(self, tmp_path):
        db = Database(tmp_path / "t.db")
        db.insert_execution(
            {"id": "x1", "action_id": "a1", "status": "queued", "trigger": "manual", "created_at": "2026-01-01T00:00:00Z"}
        )
        db.update_execution("x1", status="succeeded", result={"ok": True})
        execution = db.get_execution("x1")
        assert execution["status"] == "succeeded"
        assert execution["result"] == {"ok": True}

    def test_list_executions_filters_by_status(self, tmp_path):
        db = Database(tmp_path / "t.db")
        db.insert_execution({"id": "x1", "action_id": "a1", "status": "succeeded", "trigger": "manual", "created_at": "t"})
        db.insert_execution({"id": "x2", "action_id": "a1", "status": "failed", "trigger": "manual", "created_at": "t"})
        items, _ = db.list_executions(status="failed")
        assert [e["id"] for e in items] == ["x2"]

    def test_cleanup_retention_removes_old_rows(self, tmp_path):
        db = Database(tmp_path / "t.db")
        db.insert_event({"id": "old", "topic": "a", "source": "s", "timestamp": "2000-01-01T00:00:00Z", "data": {}})
        db.insert_execution(
            {"id": "old", "action_id": "a1", "status": "succeeded", "trigger": "manual", "created_at": "2000-01-01T00:00:00Z"}
        )
        db.cleanup_retention(retention_days=7)
        assert db.get_event("old") is None
        assert db.get_execution("old") is None


class TestReconcileInterrupted:
    def _row(self, eid, status, **kw):
        row = {
            "id": eid,
            "action_id": "a1",
            "status": status,
            "trigger": "manual",
            "created_at": "2026-01-01T00:00:00Z",
            "action_revision": "rev123",
        }
        row.update(kw)
        return row

    def test_queued_becomes_dropped(self, tmp_path):
        db = Database(tmp_path / "t.db")
        db.insert_execution(self._row("q1", "queued"))
        assert db.reconcile_interrupted_executions() == 1
        row = db.get_execution("q1")
        assert row["status"] == "dropped"
        assert row["error_code"] == "service_restart"
        assert row["started_at"] is None and row["finished_at"] is not None
        assert row["created_at"] == "2026-01-01T00:00:00Z" and row["action_revision"] == "rev123"

    def test_running_becomes_failed(self, tmp_path):
        db = Database(tmp_path / "t.db")
        db.insert_execution(self._row("r1", "running", started_at="2026-01-01T00:00:01Z"))
        assert db.reconcile_interrupted_executions() == 1
        row = db.get_execution("r1")
        assert row["status"] == "failed"
        assert row["error_code"] == "service_restart"
        assert row["started_at"] == "2026-01-01T00:00:01Z" and row["finished_at"] is not None

    def test_terminal_rows_untouched(self, tmp_path):
        db = Database(tmp_path / "t.db")
        for i, s in enumerate(("succeeded", "failed", "timed_out", "dropped")):
            db.insert_execution(self._row(f"e{i}", s, finished_at="2026-01-02T00:00:00Z"))
        assert db.reconcile_interrupted_executions() == 0
        for i, s in enumerate(("succeeded", "failed", "timed_out", "dropped")):
            row = db.get_execution(f"e{i}")
            assert row["status"] == s and row["finished_at"] == "2026-01-02T00:00:00Z"

    def test_idempotent_repeat(self, tmp_path):
        db = Database(tmp_path / "t.db")
        db.insert_execution(self._row("q1", "queued"))
        db.insert_execution(self._row("r1", "running"))
        assert db.reconcile_interrupted_executions() == 2
        assert db.reconcile_interrupted_executions() == 0
        assert db.get_execution("q1")["status"] == "dropped"
        assert db.get_execution("r1")["status"] == "failed"

    def test_empty_history(self, tmp_path):
        db = Database(tmp_path / "t.db")
        assert db.reconcile_interrupted_executions() == 0
