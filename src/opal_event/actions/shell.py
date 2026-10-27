"""Shell action runner: static executable/argv or an allowlisted interpreter script.

Executed with ``shell=False`` (argv list, never a concatenated shell string). Event data only ever
reaches the child as templated argv entries or as the full normalized event on stdin, never as part
of an interpreted command string. The process runs in its own session/group so a timeout can kill
the whole tree.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from opal_event.actions import ExecutionOutcome, IncompleteCaptureError, communicate_bounded, decode_bounded
from opal_event.models import ShellAction
from opal_event.security import subprocess_environment
from opal_event.templating import TemplateError, render_value

_INTERPRETERS: dict[str, list[str]] = {
    "sh": ["sh"],
    "bash": ["bash"],
    "cmd": ["cmd", "/c"],
    "powershell": ["powershell", "-NoProfile", "-NonInteractive", "-File"],
}


def _build_argv(action: ShellAction, rendered_args: list[str]) -> list[str]:
    if action.mode == "exec":
        return [action.executable, *rendered_args]
    return [*_INTERPRETERS[action.mode], action.script_path, *rendered_args]


def run_shell_action(action: ShellAction, event: dict[str, Any], timeout: int, max_output_bytes: int) -> ExecutionOutcome:
    try:
        rendered_args = [render_value(arg, event) for arg in action.args]
        rendered_args = [arg if isinstance(arg, str) else json.dumps(arg) for arg in rendered_args]
    except TemplateError as exc:
        return ExecutionOutcome(status="failed", error_code="template_error", error_message=str(exc))

    argv = _build_argv(action, rendered_args)
    stdin_data = json.dumps(event).encode("utf-8") if action.stdin == "event_json" else None

    popen_kwargs: dict[str, Any] = dict(
        stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=subprocess_environment(),
    )
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True
    else:
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    try:
        proc = subprocess.Popen(argv, **popen_kwargs)
    except OSError as exc:
        return ExecutionOutcome(status="failed", error_code="spawn_error", error_message=str(exc))

    try:
        out, err = communicate_bounded(proc, stdin_data, timeout, max_output_bytes)
    except subprocess.TimeoutExpired:
        return ExecutionOutcome(status="timed_out", error_code="timeout", error_message=f"exceeded {timeout}s timeout")
    except IncompleteCaptureError as exc:
        return ExecutionOutcome(status="failed", error_code="output_capture", error_message=str(exc))

    stdout = decode_bounded(out)
    stderr = decode_bounded(err)
    status = "succeeded" if proc.returncode == 0 else "failed"
    error_code = None if status == "succeeded" else "nonzero_exit"
    return ExecutionOutcome(status=status, exit_code=proc.returncode, stdout=stdout, stderr=stderr, error_code=error_code)
