"""Python plugin action runner: spawns a child interpreter running
``opal_event.plugins.runner <module> <function>``, feeds JSON event on stdin, and reads the single
JSON result from stdout."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from contextlib import suppress
from typing import Any

from opal_event.actions import ExecutionOutcome, truncate_output
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
        stdout_bytes, stderr_bytes = proc.communicate(input=json.dumps(event).encode("utf-8"), timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        proc.wait(timeout=5)
        return ExecutionOutcome(status="timed_out", error_code="timeout", error_message=f"exceeded {timeout}s timeout")

    stderr = truncate_output(stderr_bytes.decode("utf-8", errors="replace"), max_output_bytes)
    if proc.returncode != 0:
        return ExecutionOutcome(
            status="failed",
            exit_code=proc.returncode,
            stderr=stderr,
            error_code="plugin_error",
            error_message=stderr or f"plugin exited with code {proc.returncode}",
        )
    try:
        result = json.loads(stdout_bytes.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        return ExecutionOutcome(
            status="failed",
            exit_code=proc.returncode,
            stderr=stderr,
            error_code="invalid_result",
            error_message=f"plugin stdout was not valid JSON: {exc}",
        )
    return ExecutionOutcome(status="succeeded", exit_code=proc.returncode, result=result, stderr=stderr)


def _kill_tree(proc: subprocess.Popen) -> None:
    if sys.platform != "win32":
        with suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    else:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], check=False, capture_output=True)
