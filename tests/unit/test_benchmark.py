"""Lightweight tests for benchmark.py scenario isolation and accounting — no real service.
A fake httpx client + fake ZMQ publisher exercise configure_rules and run_scenario offline."""

from __future__ import annotations

import benchmark


class _Resp:
    def __init__(self, status=200, data=None, headers=None):
        self.status_code = status
        self._data = data
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return self._data


class FakeClient:
    def __init__(self, rules=None, stats_seq=None, publish_statuses=None):
        self._rules = list(rules or [])
        self._stats_seq = iter(stats_seq or [])
        self._publish_statuses = list(publish_statuses or [])
        self.deleted = []
        self.created = []

    def _stats(self):
        try:
            return next(self._stats_seq)
        except StopIteration:
            return self._last_stats

    def get(self, url, timeout=None, **kw):
        if url.endswith("/api/v1/rules/"):
            return _Resp(200, {"items": self._rules})
        if url.endswith("/api/v1/stats/"):
            self._last_stats = self._stats()
            return _Resp(200, self._last_stats)
        return _Resp(200, {}, {"etag": '"r1"'})

    def post(self, url, json=None, timeout=None, **kw):
        if url.endswith("/api/v1/rules/"):
            self.created.append(json["id"])
            return _Resp(201)
        if self._publish_statuses:
            return _Resp(self._publish_statuses.pop(0))
        return _Resp(202)

    def delete(self, url, headers=None, timeout=None, **kw):
        self.deleted.append(url.rsplit("/", 1)[1])
        return _Resp(204)


class FakePub:
    def __init__(self, fail=False):
        self.sent = 0
        self._fail = fail

    def send_multipart(self, frames):
        if self._fail:
            import zmq

            raise zmq.ZMQError("send failed")
        self.sent += 1


BASE_STATS = {
    "observed_events": 0,
    "invalid_events": 0,
    "matched_events": 0,
    "executions_succeeded": 0,
    "executions_failed": 0,
    "executions_dropped": 0,
    "queue_depth": 0,
    "active_workers": 0,
    "rejected_publications": 0,
}


def _stats(**kw):
    return {**BASE_STATS, **kw}


class TestConfigureRulesIsolation:
    def test_prior_bench_rules_removed_before_create(self):
        client = FakeClient(rules=[{"id": "bench_rule_old_0"}, {"id": "user_rule"}, {"id": "bench_rule_old_1"}])
        benchmark.configure_rules(client, "http://x", 3, "tag")
        assert sorted(client.deleted) == ["bench_rule_old_0", "bench_rule_old_1"]
        assert "user_rule" not in client.deleted
        assert len(client.created) == 3

    def test_exact_rule_count_created(self):
        client = FakeClient()
        benchmark.configure_rules(client, "http://x", 25, "t")
        assert client.created == [f"bench_rule_t_{i}" for i in range(25)]


class TestRunScenarioAccounting:
    def test_counts_and_status_ok(self):
        # 600/min x 1s = 10 sends; stats sequence: before, drain-hit, exec progression
        seq = [
            _stats(),  # before
            _stats(observed_events=10, matched_events=10, executions_succeeded=10),  # drain+exec done
            _stats(observed_events=10, matched_events=10, executions_succeeded=10),
        ]
        client = FakeClient(stats_seq=seq)
        row = benchmark.run_scenario(client, FakePub(), "http://x", "t", mode="zmq", rate_per_min=600, duration_s=1, rule_count=1)
        assert row["sent"] == 10 and row["failed"] == 0
        assert row["observed"] == 600 and row["matched"] == 600 and row["exec_done"] == 600
        assert row["status"] == "ok" and row["drain_s"] == 0.0

    def test_failed_rest_publishes_counted(self, monkeypatch):
        monkeypatch.setattr(benchmark, "DRAIN_TIMEOUT_S", 0.3)  # stats never reach target
        client = FakeClient(
            stats_seq=[_stats() for _ in range(3)],
            publish_statuses=[202, 500, 202, 500, 202],
        )
        row = benchmark.run_scenario(
            client, FakePub(), "http://x", "t", mode="rest", rate_per_min=300, duration_s=1, rule_count=1
        )
        assert row["sent"] == 3 and row["failed"] == 2

    def test_drain_timeout_reports_incomplete(self, monkeypatch):
        monkeypatch.setattr(benchmark, "DRAIN_TIMEOUT_S", 0.3)
        client = FakeClient(stats_seq=[_stats()])  # observed never catches up
        row = benchmark.run_scenario(client, FakePub(), "http://x", "t", mode="zmq", rate_per_min=600, duration_s=1, rule_count=1)
        assert row["status"] == "incomplete"
        assert row["drain_s"] == f">{0.3}"
