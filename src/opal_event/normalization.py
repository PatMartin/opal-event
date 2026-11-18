"""Event normalization: canonicalizes event IDs, timestamps, sources, and data payloads."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from opal_event.models import EventInput
from opal_event.opal_context import extract_event_metadata

RFC3339_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


def is_uuid_string(value: Any) -> bool:
    """True iff ``value`` is a string parseable as a UUID (wire `id` field format)."""
    if not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def is_rfc3339_datetime(value: Any) -> bool:
    """True iff ``value`` is a string matching the RFC3339 date-time wire format."""
    if not isinstance(value, str) or not RFC3339_RE.match(value):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def parse_chain_depth(value: Any) -> int | None:
    """Parses optional ``opal_chain_depth``/``X-Opal-Chain-Depth`` metadata.

    ``None`` -> absent (treated as a fresh chain root). Otherwise must be an integer
    ``>= 0``; anything else raises ``ValueError`` (REST maps to 422, the observer drops
    the event as invalid)."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("chain depth must be a non-negative integer")
    try:
        depth = int(value)
    except (TypeError, ValueError):
        raise ValueError("chain depth must be a non-negative integer") from None
    if depth < 0:
        raise ValueError("chain depth must be a non-negative integer")
    return depth


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

    res = {
        "id": raw_id or str(uuid.uuid4()),
        "topic": raw_topic,
        "source": raw_source or default_source,
        "timestamp": raw_timestamp or utc_now_iso(),
        "data": data,
    }
    if isinstance(event, Mapping):
        res.update(extract_event_metadata(event))
        if "opal_chain_depth" in event:
            # Producer-declared suite hop count (advisory); validated/enforced downstream.
            res["opal_chain_depth"] = event["opal_chain_depth"]
    return res
