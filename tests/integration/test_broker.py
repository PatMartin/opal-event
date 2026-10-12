import time

from opal_event.models import CurveSettings, ZeroMqSettings
from opal_event.zeromq_broker import Broker, parse_and_normalize
from tests.conftest import free_port


def wait_until(predicate, timeout=2.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class TestBrokerPubSub:
    def test_publish_is_observed_and_normalized(self):
        settings = ZeroMqSettings(publishers=f"tcp://127.0.0.1:{free_port()}", subscribers=f"tcp://127.0.0.1:{free_port()}")
        received = []
        broker = Broker(settings, on_event=received.append)
        try:
            broker.start()
            assert broker.running
            broker.publish("intent.recognized", {"topic": "intent.recognized", "data": {"a": 1}})
            assert wait_until(lambda: len(received) == 1)
            event = received[0]
            assert event["topic"] == "intent.recognized"
            assert event["source"] == "unknown"  # no source supplied -> observer default
            assert "id" in event and "timestamp" in event
        finally:
            broker.stop()

    def test_stop_sets_running_false(self):
        settings = ZeroMqSettings(publishers=f"tcp://127.0.0.1:{free_port()}", subscribers=f"tcp://127.0.0.1:{free_port()}")
        broker = Broker(settings, on_event=lambda e: None)
        broker.start()
        assert broker.running
        broker.stop()
        assert not broker.running


class TestBrokerCurve:
    def test_publish_over_curve(self, tmp_path):
        import zmq.auth

        zmq.auth.create_certificates(str(tmp_path), "server")
        (tmp_path / "authorized").mkdir()
        curve = CurveSettings(
            enabled=True,
            server_public_key_file=str(tmp_path / "server.key"),
            server_secret_key_file=str(tmp_path / "server.key_secret"),
            authorized_client_keys_file=str(tmp_path / "authorized"),
        )
        settings = ZeroMqSettings(
            publishers=f"tcp://127.0.0.1:{free_port()}", subscribers=f"tcp://127.0.0.1:{free_port()}", curve=curve
        )
        received = []
        broker = Broker(settings, on_event=received.append)
        try:
            broker.start()
            broker.publish("vision.object.detected", {"topic": "vision.object.detected", "data": {}})
            assert wait_until(lambda: len(received) == 1)
        finally:
            broker.stop()


class TestParseAndNormalize:
    def test_rejects_oversized_event(self):
        payload = b'{"topic":"a","data":"' + b"x" * 2000 + b'"}'
        try:
            parse_and_normalize(b"a", payload, max_event_bytes=100)
            raised = False
        except Exception:
            raised = True
        assert raised

    def test_rejects_topic_envelope_mismatch(self):
        import json

        payload = json.dumps({"topic": "b", "data": {}}).encode()
        try:
            parse_and_normalize(b"a", payload, max_event_bytes=1_000_000)
            raised = False
        except Exception:
            raised = True
        assert raised

    def test_fills_defaults(self):
        import json

        payload = json.dumps({"topic": "a.b", "data": {"x": 1}}).encode()
        normalized = parse_and_normalize(b"a.b", payload, max_event_bytes=1_000_000)
        assert normalized["source"] == "unknown"
        assert normalized["topic"] == "a.b"
