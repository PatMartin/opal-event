"""Cursor helpers.

- ``paginate``: plain offset-based pagination, retained only for any future in-memory bounded
  collection that still wants it (currently unused by rules/actions, which became full catalogs
  in opal_event.1.2.0.yaml).
- ``encode_scoped_cursor``/``decode_scoped_cursor``: wrap an opaque inner cursor (e.g. the
  SQLite seq-based cursor from :mod:`opal_event.db`) with a filter-set fingerprint and an explicit
  expiry, per the v1.2 suite standard: "Cursors are scoped to the exact filter set and invalidate
  on mismatch (422); cursor values expire explicitly." Used by the events and executions list
  endpoints, which remain cursor-paginated "growing histories".
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import TypeVar

from opal_event.validation import ValidationProblem

T = TypeVar("T")

CURSOR_TTL_SECONDS = 3600


def encode_offset_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii")


def decode_offset_cursor(cursor: str) -> int:
    try:
        return int(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("ascii"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid cursor") from exc


def paginate(items: list[T], cursor: str | None, limit: int) -> tuple[list[T], str | None]:
    offset = decode_offset_cursor(cursor) if cursor else 0
    page = items[offset : offset + limit]
    next_cursor = encode_offset_cursor(offset + limit) if offset + limit < len(items) else None
    return page, next_cursor


def _filters_fingerprint(filters: dict) -> str:
    normalized = json.dumps(filters, sort_keys=True, default=str)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def encode_scoped_cursor(inner_cursor: str, filters: dict) -> str:
    fingerprint = _filters_fingerprint(filters)
    expires_at = int(time.time()) + CURSOR_TTL_SECONDS
    payload = f"{fingerprint}:{expires_at}:{inner_cursor}"
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def decode_scoped_cursor(cursor: str, filters: dict, cursor_field: str = "cursor") -> str:
    """Validates and unwraps a scoped cursor. Raises :class:`ValidationProblem` (422,
    ``HTTPValidationError`` shape, matching this endpoint's already-declared 422 response) on an
    invalid, filter-mismatched, or expired cursor."""
    try:
        payload = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        fingerprint, expires_at_str, inner_cursor = payload.split(":", 2)
        expires_at = int(expires_at_str)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationProblem.from_message(["query", cursor_field], "invalid cursor", "value_error") from exc
    if expires_at < int(time.time()):
        raise ValidationProblem.from_message(["query", cursor_field], "cursor has expired", "value_error")
    if fingerprint != _filters_fingerprint(filters):
        raise ValidationProblem.from_message(
            ["query", cursor_field], "cursor does not match the current filter set", "value_error"
        )
    return inner_cursor
