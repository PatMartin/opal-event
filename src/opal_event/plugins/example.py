"""Sample Python plugin action used by the example configuration and tests.

Registered as module ``opal_event.plugins.example``, function ``handle_event``.
"""

from __future__ import annotations

from typing import Any


def handle_event(event: dict[str, Any]) -> Any:
    data = event.get("data") or {}
    return {"received_topic": event.get("topic"), "object": data.get("object")}
