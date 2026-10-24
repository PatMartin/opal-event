import time

from opal_event.db import Database
from opal_event.executor import Executor
from opal_event.metrics import Metrics
from opal_event.models import ExecutionSettings, SecuritySettings, ShellAction


def wait_until(predicate, timeout=2.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class TestExecutor:
    def test_submit_runs_action_and_records_execution(self, tmp_path):
        db = Database(tmp_path / "t.db")
        metrics = Metrics()
        executor = Executor(db, metrics, queue_size=5, max_workers=2)
        executor.start()
        try:
            action = ShellAction(id="echo", type="shell", mode="exec", executable="/bin/echo", args=["hi"])
            record = executor.submit(
                action,
                {"topic": "t", "data": {}},
                event_id=None,
                rule_id=None,
                trigger="manual",
                execution_settings=ExecutionSettings(),
                security_settings=SecuritySettings(),
            )
            assert record.status == "queued"
            assert wait_until(lambda: db.get_execution(record.id)["status"] == "succeeded")
            assert metrics.get("executions_succeeded") == 1
        finally:
            executor.stop()

    def test_overflow_drops_without_retry(self, tmp_path):
        db = Database(tmp_path / "t.db")
        metrics = Metrics()
        executor = Executor(db, metrics, queue_size=1, max_workers=1)
        executor.start()
        try:
            slow = ShellAction(id="sleep", type="shell", mode="exec", executable="/bin/sleep", args=["1"])
            exec_settings = ExecutionSettings()
            sec_settings = SecuritySettings()

            def submit():
                return executor.submit(
                    slow,
                    {"topic": "t", "data": {}},
                    event_id=None,
                    rule_id=None,
                    trigger="manual",
                    execution_settings=exec_settings,
                    security_settings=sec_settings,
                )

            r1 = submit()
            time.sleep(0.1)
            r2 = submit()
            r3 = submit()
            statuses = {r1.status, r2.status, r3.status}
            assert "dropped" in statuses
            dropped = [r for r in (r1, r2, r3) if r.status == "dropped"][0]
            assert dropped.error_code == "queue_full"
        finally:
            executor.stop()

    def test_timeout_action_marked_timed_out(self, tmp_path):
        db = Database(tmp_path / "t.db")
        metrics = Metrics()
        executor = Executor(db, metrics, queue_size=5, max_workers=1)
        executor.start()
        try:
            slow = ShellAction(id="sleep", type="shell", mode="exec", executable="/bin/sleep", args=["5"], timeout_seconds=1)
            record = executor.submit(
                slow,
                {"topic": "t", "data": {}},
                event_id=None,
                rule_id=None,
                trigger="manual",
                execution_settings=ExecutionSettings(),
                security_settings=SecuritySettings(),
            )
            assert wait_until(lambda: db.get_execution(record.id)["status"] == "timed_out", timeout=4)
        finally:
            executor.stop()

    def test_fast_job_does_not_remain_queued_due_to_worker_race(self, tmp_path):
        db = Database(tmp_path / "t.db")
        metrics = Metrics()
        # Fast jobs executed by active worker must transition to succeeded and never be overwritten to queued.
        executor = Executor(db, metrics, queue_size=10, max_workers=2)
        executor.start()
        try:
            action = ShellAction(id="echo", type="shell", mode="exec", executable="/bin/true")
            exec_settings = ExecutionSettings()
            sec_settings = SecuritySettings()

            records = [
                executor.submit(
                    action,
                    {"topic": "t", "data": {}},
                    event_id=None,
                    rule_id=None,
                    trigger="manual",
                    execution_settings=exec_settings,
                    security_settings=sec_settings,
                )
                for _ in range(5)
            ]

            for record in records:
                assert wait_until(lambda r=record: db.get_execution(r.id)["status"] == "succeeded")
                exec_row = db.get_execution(record.id)
                assert exec_row is not None
                assert exec_row["status"] == "succeeded"
        finally:
            executor.stop()

    def test_unexpected_action_exception_marks_failed_and_worker_continues(self, tmp_path, monkeypatch, caplog):
        db = Database(tmp_path / "t.db")
        metrics = Metrics()
        # Single worker so the second job must be processed by the exact same worker thread
        executor = Executor(db, metrics, queue_size=5, max_workers=1)
        executor.start()
        try:
            worker_thread = executor._workers[0]
            action_fail = ShellAction(id="exploding_action", type="shell", mode="exec", executable="/bin/echo", args=["boom"])
            action_ok = ShellAction(id="ok_action", type="shell", mode="exec", executable="/bin/echo", args=["hello"])

            import opal_event.executor as executor_module

            original_run_action = executor_module.run_action

            def mock_run_action(action, event, exec_settings, sec_settings):
                if action.id == "exploding_action":
                    raise RuntimeError("simulated unexpected crash in runner")
                return original_run_action(action, event, exec_settings, sec_settings)

            monkeypatch.setattr(executor_module, "run_action", mock_run_action)

            exec_settings = ExecutionSettings()
            sec_settings = SecuritySettings()

            record_fail = executor.submit(
                action_fail,
                {"topic": "t", "data": {}},
                event_id=None,
                rule_id=None,
                trigger="manual",
                execution_settings=exec_settings,
                security_settings=sec_settings,
            )

            record_ok = executor.submit(
                action_ok,
                {"topic": "t", "data": {}},
                event_id=None,
                rule_id=None,
                trigger="manual",
                execution_settings=exec_settings,
                security_settings=sec_settings,
            )

            assert wait_until(lambda: db.get_execution(record_fail.id)["status"] == "failed")
            assert wait_until(lambda: db.get_execution(record_ok.id)["status"] == "succeeded")

            row_fail = db.get_execution(record_fail.id)
            assert row_fail is not None
            assert row_fail["status"] == "failed"
            assert row_fail["error_code"] == "unexpected_error"
            assert "simulated unexpected crash in runner" in row_fail["error_message"]
            assert row_fail["started_at"] is not None
            assert row_fail["finished_at"] is not None
            assert row_fail["duration_ms"] is not None

            row_ok = db.get_execution(record_ok.id)
            assert row_ok is not None
            assert row_ok["status"] == "succeeded"
            assert row_ok["started_at"] is not None
            assert row_ok["finished_at"] is not None
            assert row_ok["duration_ms"] is not None

            # Verify worker thread did not terminate
            assert worker_thread.is_alive()
            # Verify queue accounting
            assert executor.queue_depth == 0
            assert metrics.get("active_workers") == 0
            assert metrics.get("executions_failed") == 1
            assert metrics.get("executions_succeeded") == 1

            # Verify traceback was logged
            assert any(
                record.levelname == "ERROR"
                and "unexpected exception during execution of action exploding_action" in record.message
                and record.exc_info is not None
                for record in caplog.records
            )
        finally:
            executor.stop()


