"""Python plugin action runner: spawns a child interpreter running
``opal_event.plugins.runner <module> <function>``, feeds JSON event on stdin, and reads the single
JSON result from stdout."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from opal_event.actions import ExecutionOutcome, IncompleteCaptureError, communicate_bounded, decode_bounded
from opal_event.models import PythonAction
from opal_event.security import subprocess_environment


def run_python_action(action: PythonAction, event: dict[str, Any], timeout: int, max_output_bytes: int) -> ExecutionOutcome:
    argv = [sys.executable, "-m", "opal_event.plugins.runner", action.module, action.function]
    popen_kwargs: dict[str, Any] = dict(
        stdin=subprocess.PIPE,
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
        out, err = communicate_bounded(proc, json.dumps(event).encode("utf-8"), timeout, max_output_bytes)
    except subprocess.TimeoutExpired:
        return ExecutionOutcome(status="timed_out", error_code="timeout", error_message=f"exceeded {timeout}s timeout")
    except IncompleteCaptureError as exc:
        return ExecutionOutcome(status="failed", error_code="output_capture", error_message=str(exc))

    stderr = decode_bounded(err)
    if proc.returncode != 0:
        return ExecutionOutcome(
            status="failed",
            exit_code=proc.returncode,
            stderr=stderr,
            error_code="plugin_error",
            error_message=stderr or f"plugin exited with code {proc.returncode}",
        )
    if out.overflowed:
        # The bounded buffer kept only a prefix — parsing it would silently accept truncated JSON.
        return ExecutionOutcome(
            status="failed",
            exit_code=proc.returncode,
            stderr=stderr,
            error_code="output_limit",
            error_message=f"plugin stdout exceeded max_output_bytes ({max_output_bytes}); result discarded",
        )
    try:
        result = json.loads(bytes(out.data).decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        return ExecutionOutcome(
            status="failed",
            exit_code=proc.returncode,
            stderr=stderr,
            error_code="invalid_result",
            error_message=f"plugin stdout was not valid JSON: {exc}",
        )
    return ExecutionOutcome(status="succeeded", exit_code=proc.returncode, result=result, stderr=stderr)
