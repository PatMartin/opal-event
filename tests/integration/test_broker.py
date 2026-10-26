import json
import threading
import time

import pytest
import zmq

from opal_event.models import CurveSettings, ZeroMqSettings
from opal_event.zeromq_broker import Broker, PublisherQueueFullError, parse_and_normalize
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

    def test_publish_preserves_correlation_metadata(self):
        settings = ZeroMqSettings(publishers=f"tcp://127.0.0.1:{free_port()}", subscribers=f"tcp://127.0.0.1:{free_port()}")
        received = []
        broker = Broker(settings, on_event=received.append)
        try:
            broker.start()
            assert broker.running
            broker.publish(
                "intent.recognized",
                {
                    "topic": "intent.recognized",
                    "data": {"a": 1},
                    "session_id": "sess-zmq-1",
                    "transaction_id": "txn-zmq-2",
                    "causation_id": "cause-zmq-3",
                },
            )
            assert wait_until(lambda: len(received) == 1)
            event = received[0]
            assert event["session_id"] == "sess-zmq-1"
            assert event["transaction_id"] == "txn-zmq-2"
            assert event["causation_id"] == "cause-zmq-3"
            assert event["opal_session_id"] == "sess-zmq-1"
            assert event["opal_transaction_id"] == "txn-zmq-2"
            assert event["opal_causation_id"] == "cause-zmq-3"
        finally:
            broker.stop()

    def test_stop_sets_running_false(self):
        settings = ZeroMqSettings(publishers=f"tcp://127.0.0.1:{free_port()}", subscribers=f"tcp://127.0.0.1:{free_port()}")
        broker = Broker(settings, on_event=lambda e: None)
        broker.start()
        assert broker.running
        broker.stop()
        assert not broker.running

    @pytest.mark.parametrize("invalid_frame_count", [1, 3])
    def test_observer_rejects_invalid_frame_count_and_continues(self, invalid_frame_count, caplog):
        settings = ZeroMqSettings(
            publishers=f"tcp://127.0.0.1:{free_port()}",
            subscribers=f"tcp://127.0.0.1:{free_port()}",
        )
        received = []
        broker = Broker(settings, on_event=received.append)
        context = zmq.Context()
        publisher = context.socket(zmq.PUB)
        try:
            broker.start()
            publisher.connect(settings.publishers)
            time.sleep(0.2)

            topic = b"intent.recognized"
            payload = json.dumps({"topic": "intent.recognized", "data": {}}).encode()
            invalid_frames = [topic] if invalid_frame_count == 1 else [topic, payload, b"extra"]
            publisher.send_multipart(invalid_frames)
            publisher.send_multipart([topic, payload])

            assert wait_until(lambda: len(received) >= 2)
            assert received[0]["_invalid"] is True
            assert f"received {invalid_frame_count}" in received[0]["error"]
            assert received[1]["topic"] == "intent.recognized"
            assert any("rejected invalid event on observer path" in record.message for record in caplog.records)
        finally:
            publisher.close(linger=0)
            context.term()
            broker.stop()

    def test_observer_rejects_protocol_violations_and_recovers(self):
        """Wire-protocol violations (bad topic syntax, bad metadata) are rejected; the observer
        keeps processing subsequent valid messages."""
        settings = ZeroMqSettings(
            publishers=f"tcp://127.0.0.1:{free_port()}",
            subscribers=f"tcp://127.0.0.1:{free_port()}",
        )
        received = []
        broker = Broker(settings, on_event=received.append)
        context = zmq.Context()
        publisher = context.socket(zmq.PUB)
        try:
            broker.start()
            publisher.connect(settings.publishers)
            time.sleep(0.2)

            publisher.send_multipart([b"Bad Topic", json.dumps({"topic": "Bad Topic", "data": {}}).encode()])
            publisher.send_multipart([b"a.b", json.dumps({"topic": "a.b", "data": {}, "source": ""}).encode()])
            publisher.send_multipart([b"good.topic", json.dumps({"topic": "good.topic", "data": {"ok": 1}}).encode()])

            assert wait_until(lambda: len(received) == 3)
            assert received[0]["_invalid"] is True
            assert "pattern" in received[0]["error"]
            assert received[1]["_invalid"] is True
            assert "source" in received[1]["error"]
            assert "_invalid" not in received[2]
            assert received[2]["topic"] == "good.topic"
            assert broker.running  # observer thread survived the rejections
        finally:
            publisher.close(linger=0)
            context.term()
            broker.stop()

    def test_update_max_event_bytes_at_runtime(self):
        settings = ZeroMqSettings(
            publishers=f"tcp://127.0.0.1:{free_port()}",
            subscribers=f"tcp://127.0.0.1:{free_port()}",
            max_event_bytes=10000,
        )
        received = []
        broker = Broker(settings, on_event=received.append)
        context = zmq.Context()
        publisher = context.socket(zmq.PUB)
        try:
            broker.start()
            publisher.connect(settings.publishers)
            time.sleep(0.2)

            # Send a 500-byte event - initially accepted
            topic = b"test.topic"
            payload_500 = json.dumps({"topic": "test.topic", "data": {"pad": "x" * 400}}).encode("utf-8")
            publisher.send_multipart([topic, payload_500])
            assert wait_until(lambda: len(received) == 1)
            assert "_invalid" not in received[0]

            # Update max_event_bytes to 200 (lower than 500)
            broker.update_max_event_bytes(200)

            # Send the same 500-byte event - now rejected by the observer
            publisher.send_multipart([topic, payload_500])
            assert wait_until(lambda: len(received) == 2)
            assert received[1].get("_invalid") is True
            assert "max_event_bytes" in received[1].get("error", "")

            # Send a small 100-byte event - accepted under the new 200-byte limit
            payload_small = json.dumps({"topic": "test.topic", "data": {"ok": 1}}).encode("utf-8")
            publisher.send_multipart([topic, payload_small])
            assert wait_until(lambda: len(received) == 3)
            assert "_invalid" not in received[2]
            assert received[2]["topic"] == "test.topic"
        finally:
            publisher.close(linger=0)
            context.term()
            broker.stop()

    def test_concurrent_publish_from_ten_threads(self):
        settings = ZeroMqSettings(
            publishers=f"tcp://127.0.0.1:{free_port()}",
            subscribers=f"tcp://127.0.0.1:{free_port()}",
        )
        received = []
        lock = threading.Lock()

        def on_event(ev):
            with lock:
                received.append(ev)

        broker = Broker(settings, on_event=on_event)
        try:
            broker.start()
            assert broker.running

            errors = []
            threads = []

            def worker(thread_idx: int):
                try:
                    for i in range(10):
                        broker.publish(
                            "thread.test",
                            {
                                "topic": "thread.test",
                                "source": f"worker-{thread_idx}",
                                "data": {"idx": i},
                            },
                        )
                except Exception as exc:
                    errors.append(exc)

            for t_id in range(10):
                t = threading.Thread(target=worker, args=(t_id,))
                threads.append(t)
                t.start()

            for t in threads:
                t.join(timeout=5)

            assert not errors
            # 10 threads * 10 messages = 100 messages total
            assert wait_until(lambda: len(received) == 100, timeout=5)
            with lock:
                assert len(received) == 100
                assert all(ev.get("topic") == "thread.test" for ev in received)
                sources = {ev.get("source") for ev in received}
                assert sources == {f"worker-{i}" for i in range(10)}
        finally:
            broker.stop()

    def test_publisher_queue_full_rejection_and_recovery(self, monkeypatch):
        settings = ZeroMqSettings(
            publishers=f"tcp://127.0.0.1:{free_port()}",
            subscribers=f"tcp://127.0.0.1:{free_port()}",
            publisher_queue_size=2,
        )
        received = []
        rejected_callbacks = []
        broker = Broker(
            settings,
            on_event=received.append,
            on_reject=lambda: rejected_callbacks.append(True),
        )

        unblock_event = threading.Event()
        send_started = threading.Event()
        original_send = zmq.Socket.send_multipart

        def blocked_send(sock_self, frames, *args, **kwargs):
            send_started.set()
            unblock_event.wait(timeout=5)
            return original_send(sock_self, frames, *args, **kwargs)

        monkeypatch.setattr(zmq.Socket, "send_multipart", blocked_send)

        try:
            broker.start()
            assert broker.running
            assert broker.queue_size == 2
            assert broker.rejected_publications == 0

            # 1. Publish first event; publisher thread consumes it from queue and blocks in send_multipart
            broker.publish("queue.test", {"topic": "queue.test", "data": {"num": 1}})
            assert send_started.wait(timeout=2)

            # 2. Fill the remaining queue capacity (2 slots)
            broker.publish("queue.test", {"topic": "queue.test", "data": {"num": 2}})
            broker.publish("queue.test", {"topic": "queue.test", "data": {"num": 3}})
            assert broker.queue_depth == 2

            # 3. New publication must be rejected and raise PublisherQueueFullError
            with pytest.raises(PublisherQueueFullError):
                broker.publish("queue.test", {"topic": "queue.test", "data": {"num": 4}})

            assert broker.rejected_publications == 1
            assert len(rejected_callbacks) == 1

            # Another attempt while full is also rejected without discarding queued items
            with pytest.raises(PublisherQueueFullError):
                broker.publish("queue.test", {"topic": "queue.test", "data": {"num": 5}})

            assert broker.rejected_publications == 2
            assert len(rejected_callbacks) == 2
            assert broker.queue_depth == 2

            # 4. Unblock the publisher thread; all 3 accepted events should be sent and observed
            unblock_event.set()
            assert wait_until(lambda: len(received) == 3, timeout=3)
            received_nums = [e["data"]["num"] for e in received]
            assert received_nums == [1, 2, 3]

            # 5. Recovery: once queue is drained, new publications are accepted again
            assert wait_until(lambda: broker.queue_depth == 0, timeout=2)
            broker.publish("queue.test", {"topic": "queue.test", "data": {"num": 6}})
            assert wait_until(lambda: len(received) == 4, timeout=2)
            assert received[-1]["data"]["num"] == 6
            assert broker.rejected_publications == 2
        finally:
            unblock_event.set()
            broker.stop()


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

    def test_accepts_full_valid_metadata(self):
        import json

        envelope = {
            "topic": "vision.object.detected",
            "data": {"object": "person"},
            "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
            "source": "opal-look",
            "timestamp": "2026-10-26T10:00:00Z",
        }
        normalized = parse_and_normalize(b"vision.object.detected", json.dumps(envelope).encode(), max_event_bytes=1_000_000)
        assert normalized["id"] == envelope["id"]
        assert normalized["source"] == "opal-look"
        assert normalized["timestamp"] == envelope["timestamp"]

    def test_preserves_opal_correlation_metadata(self):
        import json
        import uuid

        sid = str(uuid.uuid4())
        envelope = {"topic": "a.b", "data": {}, "session_id": sid}
        normalized = parse_and_normalize(b"a.b", json.dumps(envelope).encode(), max_event_bytes=1_000_000)
        assert normalized["session_id"] == sid

    def test_preserves_null_data(self):
        normalized = parse_and_normalize(b"a.b", b'{"topic":"a.b","data":null}', max_event_bytes=1_000_000)
        assert normalized["data"] is None

    @pytest.mark.parametrize(
        "topic_bytes",
        [b"Bad.Topic", b"a..b", b"a.-b", b"1a.b", b"A", b"a b.c", b"a" * 256],
        ids=["uppercase", "empty-segment", "dash-segment", "leading-digit", "single-upper", "space", "oversized"],
    )
    def test_rejects_invalid_wire_topics(self, topic_bytes):
        import json

        from opal_event.zeromq_broker import InvalidEventError

        payload = json.dumps({"topic": topic_bytes.decode("ascii"), "data": {}}).encode()
        with pytest.raises(InvalidEventError):
            parse_and_normalize(topic_bytes, payload, max_event_bytes=1_000_000)

    def test_rejects_non_utf8_topic(self):
        import json

        from opal_event.zeromq_broker import InvalidEventError

        with pytest.raises(InvalidEventError):
            parse_and_normalize(b"\xff\xfe", json.dumps({"topic": "x", "data": {}}).encode(), max_event_bytes=1_000_000)

    @pytest.mark.parametrize(
        "envelope",
        [
            "not-json{{{",
            json.dumps(["not", "an", "object"]),
            json.dumps({"data": {}}),  # missing topic
            json.dumps({"topic": "a.b"}),  # missing data
            json.dumps({"topic": 5, "data": {}}),  # non-string envelope topic
        ],
        ids=["malformed-json", "non-object", "missing-topic", "missing-data", "non-string-topic"],
    )
    def test_rejects_malformed_envelopes(self, envelope):
        from opal_event.zeromq_broker import InvalidEventError

        payload = envelope if isinstance(envelope, bytes) else envelope.encode()
        with pytest.raises(InvalidEventError):
            parse_and_normalize(b"a.b", payload, max_event_bytes=1_000_000)

    @pytest.mark.parametrize(
        "patch",
        [
            {"id": "not-a-uuid"},
            {"id": 123},
            {"source": ""},
            {"source": 42},
            {"timestamp": "yesterday"},
            {"timestamp": "2026-10-26"},  # date-only is not a date-time
            {"timestamp": 1700000000},
        ],
        ids=["bad-uuid", "non-string-id", "empty-source", "non-string-source", "bad-ts", "date-only", "numeric-ts"],
    )
    def test_rejects_malformed_metadata(self, patch):
        import json

        from opal_event.zeromq_broker import InvalidEventError

        envelope = {"topic": "a.b", "data": {}, **patch}
        with pytest.raises(InvalidEventError):
            parse_and_normalize(b"a.b", json.dumps(envelope).encode(), max_event_bytes=1_000_000)

    def test_permits_unknown_extra_fields(self):
        import json

        envelope = {"topic": "a.b", "data": {}, "custom_field": {"nested": [1, 2]}, "vendor.x": "y"}
        normalized = parse_and_normalize(b"a.b", json.dumps(envelope).encode(), max_event_bytes=1_000_000)
        assert normalized["topic"] == "a.b"
