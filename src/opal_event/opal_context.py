"""OPAL Suite Standard v1.2 cross-cutting request/response correlation headers.

``X-Opal-Session-Id``, ``X-Opal-Transaction-Id``, ``X-Opal-Request-Id``, ``X-Opal-Causation-Id`` are
optional, opaque, 1..128-character printable-ASCII tokens (no whitespace/control characters) that
every request may carry and every response (success or error) must echo back where known. They are
context-propagation only -- never an authentication or authorization mechanism.

- ``transaction_id``: a single logical end-to-end operation; this service assigns one if absent.
- ``request_id``: identifies *this* inbound HTTP exchange; always freshly generated for this hop
  (a client-supplied value is accepted for diagnostics but is not echoed back as this hop's ID).
- ``session_id`` / ``causation_id``: passed through unchanged when supplied; omitted when absent
  (this service does not itself originate a session, nor require one).

Malformed values are rejected with the nested ``OpalError`` 422 shape (not the flat ``Problem``
shape used for business-logic errors, and not the ``HTTPValidationError`` shape used for body
validation) since this is a distinct, upstream-of-routing failure category introduced by the suite
standard. See opal_event.yaml's ``OpalError``/``OpalError422`` schemas and the ``x-opal-context-standard``
extension block for the normative description this module implements.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_ID_PATTERN = re.compile(r"^[!-~]{1,128}$")

_HEADER_NAMES: dict[str, str] = {
    "session_id": "X-Opal-Session-Id",
    "transaction_id": "X-Opal-Transaction-Id",
    "request_id": "X-Opal-Request-Id",
    "causation_id": "X-Opal-Causation-Id",
}


@dataclass
class OpalContext:
    session_id: str | None
    transaction_id: str
    request_id: str
    causation_id: str | None


def _opal_error_body(code: str, message: str, context: OpalContext, details: object = None) -> dict:
    body: dict = {
        "error": {
            "code": code,
            "message": message,
            "request_id": context.request_id,
            "transaction_id": context.transaction_id,
            "session_id": context.session_id,
            "causation_id": context.causation_id,
        }
    }
    if details is not None:
        body["error"]["details"] = details
    return body


def _apply_context_headers(response: Response, context: OpalContext) -> None:
    response.headers["X-Opal-Transaction-Id"] = context.transaction_id
    response.headers["X-Opal-Request-Id"] = context.request_id
    if context.session_id is not None:
        response.headers["X-Opal-Session-Id"] = context.session_id
    if context.causation_id is not None:
        response.headers["X-Opal-Causation-Id"] = context.causation_id


class OpalContextMiddleware(BaseHTTPMiddleware):
    """Validates/derives the OPAL correlation context for every request and echoes it on every
    response. Installed as the outermost middleware so it also sees responses produced by FastAPI's
    own exception handlers (Problem/HTTPValidationError/OpalError alike)."""

    async def dispatch(self, request: Request, call_next):
        raw = {key: request.headers.get(name) for key, name in _HEADER_NAMES.items()}
        invalid = [name for key, name in _HEADER_NAMES.items() if raw[key] is not None and not _ID_PATTERN.match(raw[key])]
        if invalid:
            # Even on a rejected request we must still echo whatever context we *can* trust: a
            # malformed value is dropped (not echoed), valid sibling values and a fresh transaction/
            # request ID still are, matching "all response types ... return session ID if known".
            fallback = OpalContext(
                session_id=raw["session_id"] if raw["session_id"] and _ID_PATTERN.match(raw["session_id"]) else None,
                transaction_id=raw["transaction_id"]
                if raw["transaction_id"] and _ID_PATTERN.match(raw["transaction_id"])
                else uuid.uuid4().hex,
                request_id=uuid.uuid4().hex,
                causation_id=raw["causation_id"] if raw["causation_id"] and _ID_PATTERN.match(raw["causation_id"]) else None,
            )
            details = [
                {"header": name, "reason": "must be 1-128 printable ASCII characters with no whitespace/control characters"}
                for name in invalid
            ]
            body = _opal_error_body("invalid_header", "One or more OPAL context headers are malformed", fallback, details)
            response = JSONResponse(status_code=422, content=body)
            _apply_context_headers(response, fallback)
            return response

        context = OpalContext(
            session_id=raw["session_id"],
            transaction_id=raw["transaction_id"] or uuid.uuid4().hex,
            request_id=uuid.uuid4().hex,
            causation_id=raw["causation_id"],
        )
        request.state.opal_context = context
        response = await call_next(request)
        _apply_context_headers(response, context)
        return response


def get_opal_context(request: Request) -> OpalContext:
    """Fetches the context stashed by :class:`OpalContextMiddleware`, with a safe fallback for any
    code path invoked outside a request where the middleware did not run (e.g. direct unit tests)."""
    context = getattr(request.state, "opal_context", None)
    if context is None:
        context = OpalContext(session_id=None, transaction_id=uuid.uuid4().hex, request_id=uuid.uuid4().hex, causation_id=None)
    return context
