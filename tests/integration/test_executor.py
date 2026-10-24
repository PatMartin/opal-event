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

    def test_decrease_queue_size_while_jobs_pending_preserves_accepted_and_rejects_excess(self, tmp_path, monkeypatch):
        db = Database(tmp_path / "t.db")
        metrics = Metrics()
        # Single worker, initial queue_size = 4
        executor = Executor(db, metrics, queue_size=4, max_workers=1)

        import threading

        worker_busy = threading.Event()
        unblock_worker = threading.Event()

        import opal_event.executor as executor_module

        original_run_action = executor_module.run_action

        def mock_run_action(action, event, exec_settings, sec_settings):
            if action.id == "blocking_action":
                worker_busy.set()
                unblock_worker.wait(timeout=5)
            return original_run_action(action, event, exec_settings, sec_settings)

        monkeypatch.setattr(executor_module, "run_action", mock_run_action)

        executor.start()
        try:
            blocking_action = ShellAction(id="blocking_action", type="shell", mode="exec", executable="/bin/true")
            normal_action = ShellAction(id="normal_action", type="shell", mode="exec", executable="/bin/true")
            exec_settings = ExecutionSettings()
            sec_settings = SecuritySettings()

            # Submit job 0: single worker picks it up and blocks in run_action
            r0 = executor.submit(
                blocking_action,
                {"topic": "t", "data": {"n": 0}},
                event_id=None,
                rule_id=None,
                trigger="manual",
                execution_settings=exec_settings,
                security_settings=sec_settings,
            )
            assert worker_busy.wait(timeout=2)

            # Submit 3 normal jobs into queue (queue capacity 4, current depth 3)
            r1 = executor.submit(
                normal_action,
                {"topic": "t", "data": {"n": 1}},
                event_id=None,
                rule_id=None,
                trigger="manual",
                execution_settings=exec_settings,
                security_settings=sec_settings,
            )
            r2 = executor.submit(
                normal_action,
                {"topic": "t", "data": {"n": 2}},
                event_id=None,
                rule_id=None,
                trigger="manual",
                execution_settings=exec_settings,
                security_settings=sec_settings,
            )
            r3 = executor.submit(
                normal_action,
                {"topic": "t", "data": {"n": 3}},
                event_id=None,
                rule_id=None,
                trigger="manual",
                execution_settings=exec_settings,
                security_settings=sec_settings,
            )

            assert r1.status == "queued"
            assert r2.status == "queued"
            assert r3.status == "queued"
            assert executor.queue_depth == 3

            # Stable queue verification: capture the underlying queue object
            stable_queue = executor._queue

            # Decrease queue_size to 2 while 3 jobs are pending (> 2)
            executor.resize(queue_size=2, max_workers=1)

            # Queue object must be identical (stable worker queue)
            assert executor._queue is stable_queue
            assert executor.queue_size == 2

            # Accepted jobs must remain in queue untouched
            assert executor.queue_depth == 3
            assert db.get_execution(r1.id)["status"] == "queued"
            assert db.get_execution(r2.id)["status"] == "queued"
            assert db.get_execution(r3.id)["status"] == "queued"

            # New submission must be rejected (3 >= 2) with drop-newest and execution record
            r4 = executor.submit(
                normal_action,
                {"topic": "t", "data": {"n": 4}},
                event_id=None,
                rule_id=None,
                trigger="manual",
                execution_settings=exec_settings,
                security_settings=sec_settings,
            )
            assert r4.status == "dropped"
            assert r4.error_code == "queue_full"
            assert db.get_execution(r4.id)["status"] == "dropped"
            assert metrics.get("executions_dropped") == 1

            # Still, jobs 1, 2, 3 remain intact in queue
            assert executor.queue_depth == 3

            # Unblock the worker so it drains the pending jobs
            unblock_worker.set()

            # Wait until jobs 0, 1, 2 finish and queue depth drops below new capacity (2)
            assert wait_until(lambda: executor.queue_depth <= 1, timeout=3)

            # Now submit a new job: queue_depth < 2, so it must be admitted!
            r5 = executor.submit(
                normal_action,
                {"topic": "t", "data": {"n": 5}},
                event_id=None,
                rule_id=None,
                trigger="manual",
                execution_settings=exec_settings,
                security_settings=sec_settings,
            )
            assert r5.status == "queued"

            # All admitted jobs (r0, r1, r2, r3, r5) must succeed
            for r in (r0, r1, r2, r3, r5):
                assert wait_until(lambda rec=r: db.get_execution(rec.id)["status"] == "succeeded", timeout=3)

            assert metrics.get("executions_succeeded") == 5
            assert metrics.get("executions_dropped") == 1
        finally:
            unblock_worker.set()
            executor.stop()

    def test_increase_queue_size_while_jobs_pending_allows_more_admissions(self, tmp_path, monkeypatch):
        db = Database(tmp_path / "t.db")
        metrics = Metrics()
        # Single worker, initial queue_size = 2
        executor = Executor(db, metrics, queue_size=2, max_workers=1)

        import threading

        worker_busy = threading.Event()
        unblock_worker = threading.Event()

        import opal_event.executor as executor_module

        original_run_action = executor_module.run_action

        def mock_run_action(action, event, exec_settings, sec_settings):
            if action.id == "blocking_action":
                worker_busy.set()
                unblock_worker.wait(timeout=5)
            return original_run_action(action, event, exec_settings, sec_settings)

        monkeypatch.setattr(executor_module, "run_action", mock_run_action)

        executor.start()
        try:
            blocking_action = ShellAction(id="blocking_action", type="shell", mode="exec", executable="/bin/true")
            normal_action = ShellAction(id="normal_action", type="shell", mode="exec", executable="/bin/true")
            exec_settings = ExecutionSettings()
            sec_settings = SecuritySettings()

            # Submit job 0: single worker picks it up and blocks
            r0 = executor.submit(
                blocking_action,
                {"topic": "t", "data": {"n": 0}},
                event_id=None,
                rule_id=None,
                trigger="manual",
                execution_settings=exec_settings,
                security_settings=sec_settings,
            )
            assert worker_busy.wait(timeout=2)

            # Fill the queue (2 slots)
            r1 = executor.submit(
                normal_action,
                {"topic": "t", "data": {"n": 1}},
                event_id=None,
                rule_id=None,
                trigger="manual",
                execution_settings=exec_settings,
                security_settings=sec_settings,
            )
            r2 = executor.submit(
                normal_action,
                {"topic": "t", "data": {"n": 2}},
                event_id=None,
                rule_id=None,
                trigger="manual",
                execution_settings=exec_settings,
                security_settings=sec_settings,
            )
            assert r1.status == "queued"
            assert r2.status == "queued"
            assert executor.queue_depth == 2

            # Queue is full under limit 2: excess submission rejected
            r3 = executor.submit(
                normal_action,
                {"topic": "t", "data": {"n": 3}},
                event_id=None,
                rule_id=None,
                trigger="manual",
                execution_settings=exec_settings,
                security_settings=sec_settings,
            )
            assert r3.status == "dropped"
            assert r3.error_code == "queue_full"
            assert metrics.get("executions_dropped") == 1

            stable_queue = executor._queue

            # Increase queue_size to 4 while jobs 1 and 2 are pending
            executor.resize(queue_size=4, max_workers=1)

            # Queue object must be stable
            assert executor._queue is stable_queue
            assert executor.queue_size == 4
            # Pending jobs preserved
            assert executor.queue_depth == 2

            # Now additional submissions can be admitted into the expanded queue
            r4 = executor.submit(
                normal_action,
                {"topic": "t", "data": {"n": 4}},
                event_id=None,
                rule_id=None,
                trigger="manual",
                execution_settings=exec_settings,
                security_settings=sec_settings,
            )
            r5 = executor.submit(
                normal_action,
                {"topic": "t", "data": {"n": 5}},
                event_id=None,
                rule_id=None,
                trigger="manual",
                execution_settings=exec_settings,
                security_settings=sec_settings,
            )
            assert r4.status == "queued"
            assert r5.status == "queued"
            assert executor.queue_depth == 4

            # Further submission beyond 4 is rejected
            r6 = executor.submit(
                normal_action,
                {"topic": "t", "data": {"n": 6}},
                event_id=None,
                rule_id=None,
                trigger="manual",
                execution_settings=exec_settings,
                security_settings=sec_settings,
            )
            assert r6.status == "dropped"
            assert r6.error_code == "queue_full"
            assert metrics.get("executions_dropped") == 2

            # Unblock worker and verify all accepted jobs finish
            unblock_worker.set()
            for r in (r0, r1, r2, r4, r5):
                assert wait_until(lambda rec=r: db.get_execution(rec.id)["status"] == "succeeded", timeout=3)

            assert metrics.get("executions_succeeded") == 5
            assert metrics.get("executions_dropped") == 2
        finally:
            unblock_worker.set()
            executor.stop()

    def test_resize_max_workers_increase_during_long_running_job(self, tmp_path, monkeypatch):
        db = Database(tmp_path / "t.db")
        metrics = Metrics()
        # Start with 1 worker
        executor = Executor(db, metrics, queue_size=10, max_workers=1)

        import threading

        job0_started = threading.Event()
        unblock_job0 = threading.Event()
        job1_started = threading.Event()
        unblock_job1 = threading.Event()

        import opal_event.executor as executor_module

        original_run_action = executor_module.run_action

        def mock_run_action(action, event, exec_settings, sec_settings):
            if action.id == "long_0":
                job0_started.set()
                unblock_job0.wait(timeout=5)
            elif action.id == "long_1":
                job1_started.set()
                unblock_job1.wait(timeout=5)
            return original_run_action(action, event, exec_settings, sec_settings)

        monkeypatch.setattr(executor_module, "run_action", mock_run_action)

        executor.start()
        try:
            act0 = ShellAction(id="long_0", type="shell", mode="exec", executable="/bin/true")
            act1 = ShellAction(id="long_1", type="shell", mode="exec", executable="/bin/true")
            exec_settings = ExecutionSettings()
            sec_settings = SecuritySettings()

            # Submit job 0: worker 1 runs it and blocks
            r0 = executor.submit(
                act0,
                {"topic": "t", "data": {}},
                event_id=None,
                rule_id=None,
                trigger="manual",
                execution_settings=exec_settings,
                security_settings=sec_settings,
            )
            assert job0_started.wait(timeout=2)
            assert metrics.get("active_workers") == 1

            # Submit job 1: sits in queue because only 1 worker exists and it is busy
            r1 = executor.submit(
                act1,
                {"topic": "t", "data": {}},
                event_id=None,
                rule_id=None,
                trigger="manual",
                execution_settings=exec_settings,
                security_settings=sec_settings,
            )
            assert executor.queue_depth == 1
            assert not job1_started.is_set()

            # Increase max_workers from 1 to 2
            executor.resize(queue_size=10, max_workers=2)
            assert executor.max_workers == 2
            assert executor.worker_count == 2

            # The new worker picks up job 1 and starts running it concurrently with job 0!
            assert job1_started.wait(timeout=2)
            assert wait_until(lambda: metrics.get("active_workers") == 2, timeout=2)
            assert executor.queue_depth == 0

            # Unblock both jobs
            unblock_job0.set()
            unblock_job1.set()

            assert wait_until(lambda: metrics.get("active_workers") == 0, timeout=3)
            assert wait_until(lambda: db.get_execution(r0.id)["status"] == "succeeded", timeout=3)
            assert wait_until(lambda: db.get_execution(r1.id)["status"] == "succeeded", timeout=3)
            assert metrics.get("executions_succeeded") == 2
        finally:
            unblock_job0.set()
            unblock_job1.set()
            executor.stop()

    def test_resize_max_workers_decrease_during_long_running_jobs(self, tmp_path, monkeypatch):
        db = Database(tmp_path / "t.db")
        metrics = Metrics()
        # Start with 3 workers
        executor = Executor(db, metrics, queue_size=10, max_workers=3)

        import threading

        jobs_started = threading.Barrier(4)  # 3 worker threads + test thread
        unblock_jobs = threading.Event()

        import opal_event.executor as executor_module

        original_run_action = executor_module.run_action

        def mock_run_action(action, event, exec_settings, sec_settings):
            if action.id == "long_job":
                jobs_started.wait(timeout=5)
                unblock_jobs.wait(timeout=5)
            return original_run_action(action, event, exec_settings, sec_settings)

        monkeypatch.setattr(executor_module, "run_action", mock_run_action)

        executor.start()
        try:
            long_action = ShellAction(id="long_job", type="shell", mode="exec", executable="/bin/true")
            fast_action = ShellAction(id="fast_job", type="shell", mode="exec", executable="/bin/true")
            exec_settings = ExecutionSettings()
            sec_settings = SecuritySettings()

            # Submit 3 long-running jobs (all 3 workers become active)
            r0 = executor.submit(
                long_action,
                {"topic": "t", "data": {"n": 0}},
                event_id=None,
                rule_id=None,
                trigger="manual",
                execution_settings=exec_settings,
                security_settings=sec_settings,
            )
            r1 = executor.submit(
                long_action,
                {"topic": "t", "data": {"n": 1}},
                event_id=None,
                rule_id=None,
                trigger="manual",
                execution_settings=exec_settings,
                security_settings=sec_settings,
            )
            r2 = executor.submit(
                long_action,
                {"topic": "t", "data": {"n": 2}},
                event_id=None,
                rule_id=None,
                trigger="manual",
                execution_settings=exec_settings,
                security_settings=sec_settings,
            )

            # Wait until all 3 workers reach the barrier inside run_action
            jobs_started.wait(timeout=3)
            assert wait_until(lambda: metrics.get("active_workers") == 3)

            # Submit 2 fast jobs to the queue: they wait pending
            r3 = executor.submit(
                fast_action,
                {"topic": "t", "data": {"n": 3}},
                event_id=None,
                rule_id=None,
                trigger="manual",
                execution_settings=exec_settings,
                security_settings=sec_settings,
            )
            r4 = executor.submit(
                fast_action,
                {"topic": "t", "data": {"n": 4}},
                event_id=None,
                rule_id=None,
                trigger="manual",
                execution_settings=exec_settings,
                security_settings=sec_settings,
            )
            assert executor.queue_depth == 2

            # Now decrease max_workers from 3 to 1 while 3 jobs are running!
            executor.resize(queue_size=10, max_workers=1)
            assert executor.max_workers == 1
            assert executor.worker_count == 1

            # In-flight jobs are still running (active_workers remains 3)
            assert metrics.get("active_workers") == 3

            # Unblock the 3 running jobs
            unblock_jobs.set()

            # The 3 running jobs must complete successfully
            for r in (r0, r1, r2):
                assert wait_until(lambda rec=r: db.get_execution(rec.id)["status"] == "succeeded", timeout=3)

            # The 2 surplus workers retire without taking jobs 3 and 4; the single remaining worker processes them
            for r in (r3, r4):
                assert wait_until(lambda rec=r: db.get_execution(rec.id)["status"] == "succeeded", timeout=3)

            assert wait_until(lambda: metrics.get("active_workers") == 0, timeout=3)
            assert metrics.get("executions_succeeded") == 5
            assert executor.queue_depth == 0
        finally:
            unblock_jobs.set()
            executor.stop()


