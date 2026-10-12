"""In-memory, thread-safe counters for ``GET /api/v1/stats``. Counters reset on service restart;
there is no persisted/cumulative history beyond what the events/executions tables already store."""

from __future__ import annotations

import threading


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {
            "observed_events": 0,
            "matched_events": 0,
            "executions_succeeded": 0,
            "executions_failed": 0,
            "executions_dropped": 0,
            "active_workers": 0,
        }

    def incr(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount

    def decr(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) - amount

    def get(self, name: str) -> int:
        with self._lock:
            return self._counters.get(name, 0)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counters)
