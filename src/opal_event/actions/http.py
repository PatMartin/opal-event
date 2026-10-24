"""HTTP action runner: fixed scheme/host/port validated against the allowlist (DNS re-checked on
every call), redirects disabled. Only path/query/body/headers may be templated."""

from __future__ import annotations

import json
from typing import Any

import httpx

from opal_event.actions import ExecutionOutcome
from opal_event.models import AllowedHttpDestination, HttpAction
from opal_event.security import HttpDestinationError, resolve_and_check
from opal_event.templating import TemplateError, render_value


def run_http_action(
    action: HttpAction,
    event: dict[str, Any],
    timeout: int,
    max_output_bytes: int,
    allowlist: list[AllowedHttpDestination],
) -> ExecutionOutcome:
    try:
        url = resolve_and_check(action.url, allowlist)
    except HttpDestinationError as exc:
        return ExecutionOutcome(status="failed", error_code="destination_not_allowed", error_message=str(exc))

    try:
        headers = {k: render_value(v, event) for k, v in (action.headers or {}).items()}
        for k, v in headers.items():
            if not isinstance(v, str):
                return ExecutionOutcome(
                    status="failed",
                    error_code="invalid_header_value",
                    error_message=f"rendered header '{k}' must be a string, got {type(v).__name__}",
                )
        if action.body_mode == "template":
            body = render_value(action.body, event)
        elif action.body_mode == "event_json":
            body = event
        else:
            body = None
    except TemplateError as exc:
        return ExecutionOutcome(status="failed", error_code="template_error", error_message=str(exc))

    try:
        with httpx.Client(follow_redirects=False, timeout=timeout, trust_env=False) as client:
            request = client.build_request(
                action.method,
                url,
                headers=headers or None,
                json=body if action.body_mode != "none" else None,
            )
            response = client.send(request, stream=True)
            try:
                chunks: list[bytes] = []
                total_bytes = 0
                truncated = False
                for chunk in response.iter_bytes():
                    needed = max_output_bytes - total_bytes
                    if len(chunk) > needed:
                        chunks.append(chunk[:needed])
                        total_bytes += needed
                        truncated = True
                        break
                    chunks.append(chunk)
                    total_bytes += len(chunk)
            finally:
                response.close()
    except httpx.TimeoutException:
        return ExecutionOutcome(status="timed_out", error_code="timeout", error_message=f"exceeded {timeout}s timeout")
    except httpx.HTTPError as exc:
        return ExecutionOutcome(status="failed", error_code="http_error", error_message=str(exc))

    raw_bytes = b"".join(chunks)
    text = raw_bytes.decode("utf-8", errors="replace")
    if truncated:
        text += "...[truncated]"
        result = text
    else:
        try:
            result = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            result = text
    status = "succeeded" if 200 <= response.status_code < 300 else "failed"
    error_code = None if status == "succeeded" else "http_status_error"
    error_message = None if status == "succeeded" else f"HTTP {response.status_code}"
    return ExecutionOutcome(
        status=status,
        http_status=response.status_code,
        result=result,
        error_code=error_code,
        error_message=error_message,
    )
