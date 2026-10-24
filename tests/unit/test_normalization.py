import uuid
from datetime import datetime

from opal_event.models import EventInput
from opal_event.normalization import normalize_event, utc_now_iso


class TestNormalizeEvent:
    def test_preserves_explicit_fields_from_event_input(self):
        input_data = EventInput(
            id="explicit-id-123",
            topic="test.topic",
            source="custom-source",
            timestamp="2026-10-24T12:00:00Z",
            data={"key": "value"},
        )
        normalized = normalize_event(input_data)
        assert normalized == {
            "id": "explicit-id-123",
            "topic": "test.topic",
            "source": "custom-source",
            "timestamp": "2026-10-24T12:00:00Z",
            "data": {"key": "value"},
        }

    def test_fills_defaults_for_minimal_event_input(self):
        input_data = EventInput(topic="telemetry.metric")
        normalized = normalize_event(input_data)

        # ID generated as valid UUIDv4
        parsed_uuid = uuid.UUID(normalized["id"])
        assert str(parsed_uuid) == normalized["id"]

        assert normalized["topic"] == "telemetry.metric"
        assert normalized["source"] == "opal-event-rest"
        assert normalized["data"] is None

        # Timestamp is valid ISO ending in Z
        assert normalized["timestamp"].endswith("Z")
        dt = datetime.fromisoformat(normalized["timestamp"].replace("Z", "+00:00"))
        assert dt is not None

    def test_normalizes_dictionary_with_default_source(self):
        envelope = {"topic": "system.alert", "data": [1, 2, 3]}
        normalized = normalize_event(envelope)

        assert uuid.UUID(normalized["id"])
        assert normalized["topic"] == "system.alert"
        assert normalized["source"] == "opal-event-rest"
        assert normalized["data"] == [1, 2, 3]
        assert normalized["timestamp"].endswith("Z")

    def test_normalizes_dictionary_with_custom_default_source(self):
        envelope = {"topic": "device.status", "data": {"status": "ok"}}
        normalized = normalize_event(envelope, default_source="unknown")

        assert uuid.UUID(normalized["id"])
        assert normalized["topic"] == "device.status"
        assert normalized["source"] == "unknown"
        assert normalized["data"] == {"status": "ok"}

    def test_preserves_explicit_source_over_default_source(self):
        envelope = {
            "id": "abc",
            "topic": "device.status",
            "source": "sensor-1",
            "timestamp": "2026-01-01T00:00:00Z",
            "data": {},
        }
        normalized = normalize_event(envelope, default_source="unknown")
        assert normalized["source"] == "sensor-1"
        assert normalized["id"] == "abc"
        assert normalized["timestamp"] == "2026-01-01T00:00:00Z"

    def test_topic_override_precedence(self):
        envelope = {"topic": "original.topic", "data": {}}
        normalized = normalize_event(envelope, topic="overridden.topic")
        assert normalized["topic"] == "overridden.topic"

    def test_generates_unique_ids(self):
        e1 = normalize_event({"topic": "t", "data": {}})
        e2 = normalize_event({"topic": "t", "data": {}})
        assert e1["id"] != e2["id"]

    def test_handles_falsy_empty_strings(self):
        raw = {"id": "", "topic": "test.topic", "source": "", "timestamp": "", "data": None}
        normalized = normalize_event(raw)
        assert uuid.UUID(normalized["id"])
        assert normalized["source"] == "opal-event-rest"
        assert normalized["timestamp"].endswith("Z")
        assert normalized["data"] is None

    def test_utc_now_iso_format(self):
        ts = utc_now_iso()
        assert ts.endswith("Z")
        assert not ts.endswith("+00:00")
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert dt is not None

    def test_preserves_valid_correlation_metadata(self):
        envelope = {
            "topic": "test.meta",
            "data": {},
            "session_id": "sess-123",
            "transaction_id": "txn-456",
            "causation_id": "cause-789",
        }
        normalized = normalize_event(envelope)
        assert normalized["session_id"] == "sess-123"
        assert normalized["transaction_id"] == "txn-456"
        assert normalized["causation_id"] == "cause-789"
        assert normalized["opal_session_id"] == "sess-123"
        assert normalized["opal_transaction_id"] == "txn-456"
        assert normalized["opal_causation_id"] == "cause-789"

    def test_preserves_opal_prefixed_metadata(self):
        envelope = {
            "topic": "test.meta",
            "data": {},
            "opal_session_id": "sess-abc",
            "opal_transaction_id": "txn-def",
            "opal_causation_id": "cause-ghi",
        }
        normalized = normalize_event(envelope)
        assert normalized["session_id"] == "sess-abc"
        assert normalized["transaction_id"] == "txn-def"
        assert normalized["causation_id"] == "cause-ghi"

    def test_ignores_malformed_metadata(self):
        envelope = {
            "topic": "test.meta",
            "data": {},
            "session_id": "invalid with spaces",
            "transaction_id": "x" * 129,
            "causation_id": 12345,
        }
        normalized = normalize_event(envelope)
        assert "session_id" not in normalized
        assert "transaction_id" not in normalized
        assert "causation_id" not in normalized

