"""Speech-request plugin for the Event → Say smoke test and examples.

Registered as module ``opal_event.plugins.say_request``, function ``request_speech``.
Reads ``event["data"]``: ``url`` (a Say ``POST /api/v1/generate`` endpoint), ``body``
(the GenerateRequest JSON), and ``output`` (path to write the returned WAV to).
Benign by construction: a fixed POST plus a file write, no arbitrary execution.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any


def request_speech(event: dict[str, Any]) -> Any:
    data = event.get("data") or {}
    url = data.get("url")
    output = data.get("output")
    body = data.get("body") or {}
    if not url or not output:
        raise ValueError("event.data must carry 'url' and 'output'")
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        audio = resp.read()
    if not audio[:4] == b"RIFF":
        raise ValueError("response is not a WAV/RIFF payload")
    with open(output, "wb") as fh:
        fh.write(audio)
    return {"output": output, "bytes": len(audio)}
