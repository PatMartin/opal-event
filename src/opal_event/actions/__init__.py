"""Action execution dispatch: shell, HTTP, and Python plugin runners.

Each runner returns an :class:`ExecutionOutcome` consumed by the executor to build/update an
``Execution`` record. No runner retries internally; the bounded worker pool in
:mod:`opal_event.executor` is solely responsible for scheduling and drop-on-overflow behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from opal_event.models import Action, ExecutionSettings, HttpAction, PythonAction, SecuritySettings, ShellAction


@dataclass
class ExecutionOutcome:
    status: str  # succeeded | failed | timed_out
    exit_code: int | None = None
    http_status: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    result: Any = None
    error_code: str | None = None
    error_message: str | None = None


def truncate_output(text: str | None, max_bytes: int) -> str | None:
    if text is None:
        return None
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore") + "...[truncated]"


def run_action(
    action: Action,
    event: dict[str, Any],
    execution_settings: ExecutionSettings,
    security_settings: SecuritySettings,
) -> ExecutionOutcome:
    timeout = action.timeout_seconds or execution_settings.default_timeout_seconds
    max_output_bytes = execution_settings.max_output_bytes
    if isinstance(action, ShellAction):
        from opal_event.actions.shell import run_shell_action

        return run_shell_action(action, event, timeout, max_output_bytes)
    if isinstance(action, HttpAction):
        from opal_event.actions.http import run_http_action

        return run_http_action(action, event, timeout, max_output_bytes, security_settings.http_action_allowlist)
    if isinstance(action, PythonAction):
        from opal_event.actions.python_plugin import run_python_action

        return run_python_action(action, event, timeout, max_output_bytes)
    raise TypeError(f"unsupported action type: {type(action)!r}")
