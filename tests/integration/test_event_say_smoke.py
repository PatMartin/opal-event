"""Event → Say smoke test (step 7): real Event broker routes a synthetic ``say.request``
event through an Event rule to the ``say_request`` plugin action, which POSTs a
GenerateRequest to a speech endpoint and saves the WAV. The trace is verified
event-id → succeeded execution receipt → valid WAV (RIFF + positive frame count).

Two variants:
- ``test_event_to_say_fake_engine`` — self-contained; a stub HTTP endpoint emits a
  generated WAV. Coverage: event→rule→action→WAV plumbing; engine synthesis itself faked.
- ``test_event_to_say_live`` — requires a running opal-say service with a real engine
  (Piper or Kokoro): set ``OPAL_SAY_URL`` (e.g. http://127.0.0.1:8002),
  ``OPAL_SAY_VOICE``, ``OPAL_SAY_ENGINE``.

Prereqs for the live variant: `uvicorn "opal_say.__main__:..."`/`opal-say-svc` on a free
port with the piper or kokoro extra installed and a voice + profile configured.
"""
import math
import os
import struct
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from opal_event.app import create_app
from tests.conftest import base_config, write_config

RATE = 22050


def _make_wav(path: Path, seconds: float = 0.25) -> None:
    n = int(RATE * seconds)
    frames = b"".join(
        struct.pack("<h", int(20000 * math.sin(2 * math.pi * 440 * i / RATE))) for i in range(n)
    )
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames)


class _SayStub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        if self.path != "/api/v1/generate":
            self.send_response(404)
            self.end_headers()
            return
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        tmp = Path(self.server.out_dir) / "gen.wav"
        _make_wav(tmp)
        payload = tmp.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _service(tmp_path, say_url, wav_out):
    cfg = base_config(
        tmp_path,
        actions=[{
            "id": "speak", "type": "python", "enabled": True,
            "module": "opal_event.plugins.say_request", "function": "request_speech",
            "timeout_seconds": 30,
        }],
        rules=[{
            "id": "say_rule", "enabled": True, "topic": "say.request", "conditions": [],
            "actions": ["speak"],
        }],
    )
    app = create_app(str(write_config(tmp_path, cfg)))
    client = TestClient(app)
    return client, {
        "url": f"{say_url}/api/v1/generate",
        "output": str(wav_out),
        "body": {"text": "smoke test", "voice_id": os.environ.get("OPAL_SAY_VOICE") or "v1",
                 "engine_id": os.environ.get("OPAL_SAY_ENGINE") or "fake",
                 "output_format": "wav"},
    }


def _wait_terminal(client, execution_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = client.get(f"/api/v1/execution/{execution_id}").json()
        if row["status"] in ("succeeded", "failed", "timed_out", "dropped"):
            return row
        time.sleep(0.5)
    raise AssertionError(f"execution {execution_id} did not reach a terminal state")


def _run_flow(client, data, wav_out):
    receipt = client.post("/api/v1/event/", json={"topic": "say.request", "data": data}).json()
    deadline = time.time() + 15
    rows = []
    while time.time() < deadline:
        rows = client.get("/api/v1/execution/", params={"event_id": receipt["id"]}).json()
        if rows:
            break
        time.sleep(0.25)
    assert rows, "no execution admitted for the event"
    final = _wait_terminal(client, rows[0]["id"])
    assert final["status"] == "succeeded", final
    # WAV validation: RIFF container + positive frame count.
    with wave.open(str(wav_out), "rb") as w:
        assert w.getnframes() > 0
        assert w.getsampwidth() == 2
    return final


def test_event_to_say_fake_engine(tmp_path):
    """Self-contained: stub speech endpoint; verifies event→rule→action→WAV plumbing."""
    tmp = tmp_path / "stub"
    tmp.mkdir()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _SayStub)
    httpd.out_dir = str(tmp)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        say_url = f"http://127.0.0.1:{httpd.server_port}"
        wav_out = tmp_path / "smoke.wav"
        client, data = _service(tmp_path, say_url, wav_out)
        with client:
            _run_flow(client, data, wav_out)
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.mark.skipif(not os.environ.get("OPAL_SAY_URL"), reason="set OPAL_SAY_URL to a live opal-say service")
def test_event_to_say_live(tmp_path):
    """Real engine coverage: requires a running opal-say (Piper/Kokoro) on OPAL_SAY_URL."""
    wav_out = tmp_path / "smoke_live.wav"
    client, data = _service(tmp_path, os.environ["OPAL_SAY_URL"], wav_out)
    with client:
        _run_flow(client, data, wav_out)
