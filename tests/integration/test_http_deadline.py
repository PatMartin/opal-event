"""Elapsed HTTP action deadline (step 6): timeout_seconds bounds connect + headers +
streamed body (monotonic), not just each socket recv. A slow-drip body must expire;
the worker is released promptly and the next action still runs."""

import http.server
import threading
import time

import pytest

from opal_event.actions.http import run_http_action
from opal_event.models import AllowedHttpDestination, HttpAction


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/drip":
            # Progresses forever — under the old per-recv timeout this never expired.
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            try:
                while True:
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.05)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def _action(port, path="/", timeout=2):
    return HttpAction(
        id="a1",
        type="http",
        method="GET",
        url=f"http://127.0.0.1:{port}{path}",
        body_mode="none",
        timeout_seconds=timeout,
    )


def _allowlist(port):
    return [AllowedHttpDestination(host="127.0.0.1", ports=[port], schemes=["http"])]


def test_normal_endpoint_succeeds(server):
    out = run_http_action(_action(server.server_port, "/"), {}, 10, 65536, _allowlist(server.server_port))
    assert out.status == "completed" and out.http_status == 200
    assert out.result == {"ok": True}


def test_slow_drip_expires_and_releases(server):
    start = time.monotonic()
    out = run_http_action(_action(server.server_port, "/drip", timeout=1), {}, 1, 65536, _allowlist(server.server_port))
    elapsed = time.monotonic() - start
    assert out.status == "timed_out" and out.error_code == "timeout"
    # Prompt release: the call must end near the deadline, not after server-side work.
    assert elapsed < 10
    # The executor is free: a follow-up call runs normally.
    out2 = run_http_action(_action(server.server_port, "/"), {}, 5, 65536, _allowlist(server.server_port))
    assert out2.status == "completed"


def test_response_size_limit_preserved(server):
    out = run_http_action(_action(server.server_port, "/drip", timeout=10), {}, 10, 4, _allowlist(server.server_port))
    assert out.status == "completed"
    assert out.result.endswith("...[truncated]")
