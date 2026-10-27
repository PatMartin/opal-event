"""Opt-in event throughput benchmark for opal-event.

Provisions an ISOLATED throwaway service (ephemeral 127.0.0.1 ports, temp SQLite DB), exercises
publication over REST and raw ZeroMQ at sustained rates, measures each pipeline stage separately,
and prints a human-readable summary (--json for machine-readable output).

    .venv/bin/python benchmark.py                        # defaults
    .venv/bin/python benchmark.py --duration 15 --json bench.json
    .venv/bin/python benchmark.py --rates 100,1000 --rule-counts 1,25

What is measured, per scenario:
  publish_rate   events/min actually accepted by the publisher path (send loop wall time)
  publish_lat    mean per-request acceptance latency, ms (REST only; ZMQ send is fire-and-forget)
  observed       events/min reaching the observer copy (stats.observed_events delta)
  matched        events/min matching >=1 enabled rule (stats.matched_events delta)
  exec_done      executions/min reaching a terminal state (succeeded+failed+dropped delta)
  drain_s        seconds for the observer to catch up after the last event was sent (lag)
  queue_peak     max executor queue_depth sampled during the run
  dropped        executions dropped (queue_full / shutdown) during the run
  invalid        invalid_events delta

Interpretation notes:
  - publish_rate vs observed separates publisher acceptance from observer processing; a large gap
    or a growing drain_s means the observer is the bottleneck, not ingress.
  - Rule-count scenarios isolate rule-evaluation cost; a no-match rule (condition false) still
    forces evaluation, so matched vs observed shows match overhead independent of execution cost.
  - This is a single-machine smoke benchmark, not a load test: results depend on host speed.
  - NOT run in CI; run it manually when you need numbers.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import yaml
import zmq

ROOT = Path(__file__).resolve().parent
READY_TIMEOUT_S = 30
DRAIN_TIMEOUT_S = 60


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_config(tmp: Path) -> Path:
    cfg = {
        "version": 1,
        "server": {"host": "127.0.0.1", "port": free_port()},
        "zeromq": {
            "publishers": f"tcp://127.0.0.1:{free_port()}",
            "subscribers": f"tcp://127.0.0.1:{free_port()}",
            "max_event_bytes": 1048576,
            "curve": {"enabled": False},
        },
        "security": {
            "rest_auth": {"enabled": False, "token_env": "OPAL_EVENT_API_TOKEN"},
            "http_action_allowlist": [{"host": "127.0.0.1", "ports": [9000], "schemes": ["http"]}],
        },
        "execution": {
            "max_workers": 4,
            "queue_size": 10000,
            "default_timeout_seconds": 10,
            "overflow": "drop_and_log",
            "retry_enabled": False,
            "max_output_bytes": 4096,
        },
        "persistence": {
            "database": str(tmp / "bench.db"),
            "event_mode": "all",
            "retention_days": 365,
            "execution_history_enabled": True,
        },
        "actions": [
            {
                "id": "bench_noop",
                "type": "python",
                "enabled": True,
                "module": "opal_event.plugins.example",
                "function": "handle_event",
                "timeout_seconds": 10,
            }
        ],
        "rules": [],
    }
    path = tmp / "opal_event.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return path


def wait_ready(base_url: str) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base_url}/api/v1/health/ready", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise TimeoutError("service did not become ready")


def stats(client: httpx.Client, base_url: str) -> dict:
    return client.get(f"{base_url}/api/v1/stats/", timeout=10).json()


def _delete_rule(client: httpx.Client, base_url: str, rule_id: str) -> None:
    etag = client.get(f"{base_url}/api/v1/rules/{rule_id}", timeout=10).headers.get("etag", "")
    client.delete(f"{base_url}/api/v1/rules/{rule_id}", headers={"If-Match": etag}, timeout=10)


def configure_rules(client: httpx.Client, base_url: str, rule_count: int, scenario_tag: str) -> None:
    """Replace any prior benchmark rules with exactly ``rule_count`` fresh rules:
    K-1 that always evaluate but never match + 1 that matches -> bench_noop."""
    for rid in [r["id"] for r in client.get(f"{base_url}/api/v1/rules/", timeout=10).json()["items"]]:
        if rid.startswith("bench_rule_"):
            _delete_rule(client, base_url, rid)
    for i in range(rule_count):
        matching = i == rule_count - 1
        body = {
            "id": f"bench_rule_{scenario_tag}_{i}",
            "enabled": True,
            "topic": "bench.*",
            "conditions": [
                # matching rule: seq>=0 (always true); others: eq -1 (evaluates but never matches)
                {"path": "data.seq", "operator": "gte" if matching else "eq", "value": 0 if matching else -1}
            ],
            "actions": ["bench_noop"] if matching else [],
        }
        r = client.post(f"{base_url}/api/v1/rules/", json=body, timeout=10)
        assert r.status_code == 201, f"rule create -> {r.status_code}: {r.text[:200]}"


def run_scenario(client, pub, base_url, name, *, mode, rate_per_min, duration_s, rule_count) -> dict:
    tag = re.sub(r"[^a-z0-9]", "", name.lower().replace("bench-", ""))
    configure_rules(client, base_url, rule_count, tag)
    before = stats(client, base_url)
    target = int(rate_per_min / 60 * duration_s)
    interval = 60 / rate_per_min
    lat_ms: list[float] = []
    queue_peak = 0

    t_send0 = time.monotonic()
    sent = failed = 0
    for i in range(target):
        deadline = t_send0 + (i + 1) * interval
        envelope = {"topic": "bench.throughput", "source": f"bench-{mode}", "data": {"seq": i}}
        if mode == "rest":
            t0 = time.monotonic()
            r = client.post(f"{base_url}/api/v1/events/", json=envelope, timeout=30)
            lat_ms.append((time.monotonic() - t0) * 1000)
            if r.status_code == 202:
                sent += 1
            else:
                failed += 1
        else:
            try:
                pub.send_multipart([b"bench.throughput", json.dumps(envelope).encode()])
                sent += 1
            except zmq.ZMQError:
                failed += 1
        # sample executor queue depth opportunistically (cheap GET)
        if i % 50 == 0:
            with contextlib.suppress(httpx.HTTPError):
                queue_peak = max(queue_peak, stats(client, base_url)["queue_depth"])
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
    send_s = time.monotonic() - t_send0

    # Observer drain: poll stats until observed_events catches up to sent (or timeout).
    expected = before["observed_events"] + sent
    t_drain0 = time.monotonic()
    drain_s = None
    while time.monotonic() - t_drain0 < DRAIN_TIMEOUT_S:
        now = stats(client, base_url)
        queue_peak = max(queue_peak, now["queue_depth"])
        if now["observed_events"] >= expected:
            drain_s = time.monotonic() - t_drain0
            break
        time.sleep(0.25)
    after = stats(client, base_url)

    # Then wait (bounded) for the executions THIS scenario triggered to reach terminal state, so
    # the next scenario's before-snapshot is never polluted by still-running leftovers.
    expected_exec = after["matched_events"] - before["matched_events"]
    exec_done = (after["executions_succeeded"] + after["executions_failed"] + after["executions_dropped"]) - (
        before["executions_succeeded"] + before["executions_failed"] + before["executions_dropped"]
    )
    exec_complete = True
    t_exec0 = time.monotonic()
    while exec_done < expected_exec and time.monotonic() - t_exec0 < DRAIN_TIMEOUT_S:
        time.sleep(0.25)
        after = stats(client, base_url)
        exec_done = (after["executions_succeeded"] + after["executions_failed"] + after["executions_dropped"]) - (
            before["executions_succeeded"] + before["executions_failed"] + before["executions_dropped"]
        )
    exec_complete = exec_done >= expected_exec
    mins = duration_s / 60 or 1
    row = {
        "scenario": name,
        "mode": mode,
        "rate_target": rate_per_min,
        "rules": rule_count,
        "sent": sent,
        "failed": failed,
        "status": "ok" if (drain_s is not None and exec_complete) else "incomplete",
        "publish_rate": round(sent / (send_s / 60 or 1)),
        "publish_lat_ms": round(sum(lat_ms) / len(lat_ms), 2) if lat_ms else None,
        "observed": round((after["observed_events"] - before["observed_events"]) / mins),
        "matched": round((after["matched_events"] - before["matched_events"]) / mins),
        "exec_done": round(exec_done / mins),
        "drain_s": round(drain_s, 2) if drain_s is not None else f">{DRAIN_TIMEOUT_S}",
        "queue_peak": queue_peak,
        "dropped": after["executions_dropped"] - before["executions_dropped"],
        "invalid": after["invalid_events"] - before["invalid_events"],
    }
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--duration", type=float, default=10.0, help="seconds per scenario (default 10)")
    ap.add_argument("--rates", default="100,1000,10000", help="comma-separated events/min targets")
    ap.add_argument("--rule-counts", default="1", help="comma-separated rule counts for scaling scenarios")
    ap.add_argument("--json", dest="json_path", help="also write results to this JSON file")
    args = ap.parse_args()
    rates = [int(x) for x in args.rates.split(",")]
    rule_counts = [int(x) for x in args.rule_counts.split(",")]

    tmp = Path(tempfile.mkdtemp(prefix="opal_bench_"))
    cfg_path = make_config(tmp)
    cfg = yaml.safe_load(cfg_path.read_text())
    base_url = f"http://127.0.0.1:{cfg['server']['port']}"
    pub_ep = cfg["zeromq"]["publishers"]

    svc = subprocess.Popen([sys.executable, "-m", "opal_event", "--config", str(cfg_path), "--log-level", "error"], cwd=ROOT)
    try:
        wait_ready(base_url)
        ctx = zmq.Context()
        pub = ctx.socket(zmq.PUB)
        pub.connect(pub_ep)
        time.sleep(0.5)  # PUB/SUB slow-joiner — the spec-documented bound, not a hidden race fix
        client = httpx.Client(timeout=30)
        rows = []
        try:
            rows.append(
                run_scenario(
                    client,
                    pub,
                    base_url,
                    f"rest-{rates[0]}/min",
                    mode="rest",
                    rate_per_min=rates[0],
                    duration_s=args.duration,
                    rule_count=1,
                )
            )
            for rc in rule_counts:
                for rate in rates:
                    rows.append(
                        run_scenario(
                            client,
                            pub,
                            base_url,
                            f"zmq-{rate}/min rules={rc}",
                            mode="zmq",
                            rate_per_min=rate,
                            duration_s=args.duration,
                            rule_count=rc,
                        )
                    )
        finally:
            client.close()
            pub.close(linger=0)
            ctx.term()

        cols = (
            "scenario",
            "sent",
            "fail",
            "pub/min",
            "lat_ms",
            "obs/min",
            "match/m",
            "exec/m",
            "drain_s",
            "qpeak",
            "drop",
            "inv",
            "status",
        )
        vals = (
            "scenario",
            "sent",
            "failed",
            "publish_rate",
            "publish_lat_ms",
            "observed",
            "matched",
            "exec_done",
            "drain_s",
            "queue_peak",
            "dropped",
            "invalid",
            "status",
        )
        widths = (22, 6, 5, 9, 8, 9, 9, 8, 9, 6, 5, 5, 11)
        hdr = "".join(f"{c:<{w}}" for c, w in zip(cols, widths, strict=True))
        print("\n" + hdr + "\n" + "-" * len(hdr))
        for r in rows:
            print("".join(f"{str(r[k]):<{w}}" for k, w in zip(vals, widths, strict=True)))
        if args.json_path:
            Path(args.json_path).write_text(json.dumps(rows, indent=2))
            print(f"\nJSON written to {args.json_path}")
        return 0
    finally:
        svc.terminate()
        try:
            svc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            svc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
