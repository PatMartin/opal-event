"""Event normalization: canonicalizes event IDs, timestamps, sources, and data payloads."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from opal_event.models import EventInput


def utc_now_iso() -> str:
    """Returns the current UTC time as an ISO-8601 string ending in 'Z'."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def normalize_event(
    event: EventInput | Mapping[str, Any],
    *,
    default_source: str = "opal-event-rest",
    topic: str | None = None,
) -> dict[str, Any]:
    """Normalizes an EventInput model or raw dictionary into a canonical event dictionary.

    Fills in missing defaults for id (UUID v4), timestamp (current UTC ISO string),
    and source (defaulting to ``default_source``).
    """
    if isinstance(event, Mapping):
        raw_id = event.get("id")
        raw_topic = topic if topic is not None else event.get("topic")
        raw_source = event.get("source")
        raw_timestamp = event.get("timestamp")
        data = event.get("data")
    else:
        raw_id = getattr(event, "id", None)
        raw_topic = topic if topic is not None else getattr(event, "topic", None)
        raw_source = getattr(event, "source", None)
        raw_timestamp = getattr(event, "timestamp", None)
        data = getattr(event, "data", None)

    return {
        "id": raw_id or str(uuid.uuid4()),
        "topic": raw_topic,
        "source": raw_source or default_source,
        "timestamp": raw_timestamp or utc_now_iso(),
        "data": data,
    }
