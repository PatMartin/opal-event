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
