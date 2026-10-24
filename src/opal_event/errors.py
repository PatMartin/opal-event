"""Structured ``Problem`` error responses and the exception types that carry them.

``422`` validation errors keep FastAPI's default ``HTTPValidationError`` shape (``{"detail": [...]}
``), which already matches the shared OPAL contract shape with no changes needed. Every other error
status in the contract (400/401/403/404/409/413/429/500/503) uses the ``Problem`` schema instead.

The v1.2 suite standard introduces a third shape, the nested ``OpalError`` (``{"error": {...}}``),
used exclusively for strong-ETag precondition failures (412 stale / 428 missing) and OPAL-context
header validation failures (422) -- see :mod:`opal_event.opal_context` for the header side.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from opal_event.opal_context import get_opal_context


class ProblemError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class BadRequestError(ProblemError):
    def __init__(self, message: str = "Malformed JSON or invalid query"):
        super().__init__(400, "bad_request", message)


class ForbiddenError(ProblemError):
    def __init__(self, message: str = "Forbidden by server policy"):
        super().__init__(403, "forbidden", message)


class NotFoundError(ProblemError):
    def __init__(self, message: str = "Resource not found"):
        super().__init__(404, "not_found", message)


class ConflictError(ProblemError):
    def __init__(self, message: str = "Concurrent update or in-use resource"):
        super().__init__(409, "conflict", message)


class PayloadTooLargeError(ProblemError):
    def __init__(self, message: str = "Event too large"):
        super().__init__(413, "payload_too_large", message)


class RateLimitError(ProblemError):
    def __init__(self, message: str = "Rate or queue admission limit"):
        super().__init__(429, "rate_limited", message)


class ServiceUnavailableError(ProblemError):
    def __init__(self, message: str = "Broker, persistence, or execution subsystem unavailable"):
        super().__init__(503, "unavailable", message)


async def problem_exception_handler(request: Request, exc: ProblemError) -> JSONResponse:
    context = get_opal_context(request)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code,
            "message": exc.message,
            "request_id": context.request_id,
        },
    )


class OpalContextualError(Exception):
    """Base for errors rendered with the nested ``OpalError`` shape: strong-ETag preconditions
    (412/428) and OPAL-context header validation (422). See :class:`opal_event.models.OpalError`."""

    def __init__(self, status_code: int, code: str, message: str, details: object = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


class PreconditionRequiredError(OpalContextualError):
    """428: a mutating request to a strong-ETag resource omitted the required ``If-Match`` header."""

    def __init__(self, message: str = "If-Match header is required for this update"):
        super().__init__(428, "precondition_required", message)


class PreconditionFailedError(OpalContextualError):
    """412: ``If-Match`` was supplied but no longer matches the resource's current revision."""

    def __init__(self, message: str = "If-Match revision is stale"):
        super().__init__(412, "precondition_failed", message)


async def opal_contextual_error_handler(request: Request, exc: OpalContextualError) -> JSONResponse:
    context = get_opal_context(request)
    body = {
        "error": {
            "code": exc.code,
            "message": exc.message,
            "request_id": context.request_id,
            "transaction_id": context.transaction_id,
            "session_id": context.session_id,
            "causation_id": context.causation_id,
        }
    }
    if exc.details is not None:
        body["error"]["details"] = exc.details
    return JSONResponse(status_code=exc.status_code, content=body)
