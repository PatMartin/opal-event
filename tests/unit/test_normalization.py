import json
import uuid
from datetime import datetime

import pytest
from pydantic import ValidationError

from opal_event.models import EventInput
from opal_event.normalization import is_rfc3339_datetime, is_uuid_string, normalize_event, utc_now_iso
from opal_event.zeromq_broker import parse_and_normalize


class TestNormalizeEvent:
    def test_preserves_explicit_fields_from_event_input(self):
        input_data = EventInput(
            id="11111111-2222-3333-4444-555555555555",
            topic="test.topic",
            source="custom-source",
            timestamp="2026-10-24T12:00:00Z",
            data={"key": "value"},
        )
        normalized = normalize_event(input_data)
        assert normalized == {
            "id": "11111111-2222-3333-4444-555555555555",
            "topic": "test.topic",
            "source": "custom-source",
            "timestamp": "2026-10-24T12:00:00Z",
            "data": {"key": "value"},
        }

    def test_fills_defaults_for_minimal_event_input(self):
        input_data = EventInput(topic="telemetry.metric", data=None)
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


class TestEventInputWireValidation:
    """REST EventInput must enforce the same id/timestamp/data rules as the ZeroMQ observer."""

    @staticmethod
    def _parse(**overrides):
        body = {"topic": "test.input", "data": {}}
        body.update(overrides)
        return EventInput.model_validate(body)

    def test_valid_uuid_id_accepted(self):
        e = self._parse(id=str(uuid.uuid4()))
        assert e.id is not None

    @pytest.mark.parametrize("bad_id", ["not-a-uuid", "12345", "g47e1c2d-8f3a-4b5c-9d6e-7f8a9b0c1d2e", ""])
    def test_invalid_uuid_rejected(self, bad_id):
        with pytest.raises(ValidationError):
            self._parse(id=bad_id)

    @pytest.mark.parametrize("ts", ["2026-01-10T12:00:00Z", "2026-01-10T12:00:00.123456Z", "2026-01-10T14:00:00+02:00"])
    def test_valid_rfc3339_timestamp_accepted(self, ts):
        e = self._parse(timestamp=ts)
        assert e.timestamp == ts

    @pytest.mark.parametrize("bad_ts", ["2026-01-10 12:00:00", "2026-01-10", "not-a-date", "2026-13-99T99:99:99Z", ""])
    def test_invalid_timestamp_rejected(self, bad_ts):
        with pytest.raises(ValidationError):
            self._parse(timestamp=bad_ts)

    def test_explicit_null_data_allowed(self):
        e = self._parse(data=None)
        assert e.data is None

    def test_missing_data_rejected(self):
        with pytest.raises(ValidationError):
            EventInput.model_validate({"topic": "test.input"})

    def test_omitted_metadata_generates_valid_wire_fields(self):
        normalized = normalize_event(self._parse())
        assert is_uuid_string(normalized["id"])
        assert is_rfc3339_datetime(normalized["timestamp"])
        assert normalized["source"] == "opal-event-rest"

    def test_normalized_rest_event_passes_observer_validation(self):
        """The full REST path output must satisfy parse_and_normalize's wire checks."""
        normalized = normalize_event(self._parse(id=str(uuid.uuid4()), source="rest_api", timestamp="2026-01-10T12:00:00Z"))
        frame1 = normalized["topic"].encode("utf-8")
        frame2 = json.dumps(normalized).encode("utf-8")
        parsed = parse_and_normalize(frame1, frame2, max_event_bytes=1048576)
        assert parsed["id"] == normalized["id"]
        assert parsed["data"] == normalized["data"]

    def test_null_data_normalized_passes_observer_validation(self):
        normalized = normalize_event(self._parse(data=None))
        frame1 = normalized["topic"].encode("utf-8")
        frame2 = json.dumps(normalized).encode("utf-8")
        parsed = parse_and_normalize(frame1, frame2, max_event_bytes=1048576)
        assert parsed["data"] is None
