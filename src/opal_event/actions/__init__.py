"""Action execution dispatch: shell, HTTP, and Python plugin runners.

Each runner returns an :class:`ExecutionOutcome` consumed by the executor to build/update an
``Execution`` record. No runner retries internally; the bounded worker pool in
:mod:`opal_event.executor` is solely responsible for scheduling and drop-on-overflow behavior.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import suppress
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


class BoundedBuffer:
    """Thread-feedable capture buffer capped at ``limit`` bytes.

    Overflow policy: the first ``limit`` bytes are retained and the rest is *counted* (``overflow``)
    while the stream keeps being drained — the child never deadlocks on a full pipe and memory use
    stays bounded regardless of how much the process emits.
    """

    def __init__(self, limit: int):
        self._limit = max(0, limit)
        self.data = bytearray()
        self.overflow = 0

    @property
    def overflowed(self) -> bool:
        return self.overflow > 0

    def feed(self, chunk: bytes) -> None:
        room = self._limit - len(self.data)
        if room > 0:
            self.data += chunk[:room]
        if len(chunk) > room:
            self.overflow += len(chunk) - max(room, 0)


def decode_bounded(buffer: BoundedBuffer) -> str:
    """Decodes a captured buffer to text; appends the truncation marker iff bytes were dropped."""
    text = bytes(buffer.data).decode("utf-8", errors="replace")
    return text + "...[truncated]" if buffer.overflowed else text


def _drain_stream(stream, buffer: BoundedBuffer) -> None:
    try:
        while chunk := stream.read(65536):
            buffer.feed(chunk)
    except (OSError, ValueError):
        pass  # read aborted during incomplete-capture cleanup — same outcome as EOF
    finally:
        with suppress(OSError, ValueError):
            stream.close()


def _feed_stdin(stream, data: bytes) -> None:
    with suppress(OSError):  # child may exit or never read; EPIPE/BrokenPipeError is expected
        stream.write(data)
    with suppress(OSError):
        stream.close()


def _release_pipe_reader(stream) -> None:
    """Unblock a reader thread stuck in ``read()`` on this pipe — without blocking ourselves.

    Closing a pipe's read end from another thread can hang when a descendant still holds the
    write end (observed on Windows: the close waits for the pending read). Windows therefore
    aborts the pending read via CancelIoEx; POSIX closes the fd directly (the read returns
    EBADF/EOF). Callers must tolerate ValueError/OSError for already-closed wrappers.
    """
    if sys.platform == "win32":
        import ctypes
        import msvcrt

        ctypes.windll.kernel32.CancelIoEx(msvcrt.get_osfhandle(stream.fileno()), None)
    else:
        os.close(stream.fileno())


class IncompleteCaptureError(RuntimeError):
    """A reader thread could not reach EOF on a child output stream — a descendant inherited the
    pipe and never closed it, so the captured output is an arbitrary prefix, not a bounded
    prefix. Raised instead of silently returning possibly-truncated output as a success."""


def communicate_bounded(
    proc: subprocess.Popen,
    stdin_data: bytes | None,
    timeout: int,
    max_output_bytes: int,
) -> tuple[BoundedBuffer, BoundedBuffer]:
    """``Popen.communicate`` replacement enforcing ``max_output_bytes`` *during* capture.

    POSIX drains stdout/stderr nonblocking via a ``selectors`` loop in the caller thread — there
    is no second thread that can be stuck in ``read()`` on a descendant-held pipe, so cleanup is
    deterministic. Windows uses reader threads plus CancelIoEx to abort pending reads. The stdin
    payload is written on its own daemon thread so a child that never reads cannot stall the
    timeout. One monotonic deadline governs execution plus a small bounded cleanup allowance:
    the caller always returns by ``timeout`` + ``_CLEANUP_ALLOWANCE``.

    Classifications: child alive past ``timeout`` -> ``subprocess.TimeoutExpired``; child exited
    but a pipe is still held by a descendant -> ``IncompleteCaptureError`` (output is incomplete,
    never silently treated as success).
    """
    stdout = BoundedBuffer(max_output_bytes)
    stderr = BoundedBuffer(max_output_bytes)
    if stdin_data is not None:
        threading.Thread(target=_feed_stdin, args=(proc.stdin, stdin_data), daemon=True, name="opal-event-in").start()
    elif proc.stdin is not None:
        proc.stdin.close()
    if sys.platform == "win32":
        _capture_windows(proc, stdout, stderr, timeout)
    else:
        _capture_posix(proc, stdout, stderr, timeout)
    return stdout, stderr


# Bounded post-exit drain: after the child exits, pipes still held by descendants get this much
# grace before capture is declared incomplete. After a timeout kill, _CLEANUP_ALLOWANCE bounds
# the final drain — total wall time is always <= timeout + _CLEANUP_ALLOWANCE.
_POST_EXIT_DRAIN = 5.0
_CLEANUP_ALLOWANCE = 5.0


def _capture_posix(proc, stdout: BoundedBuffer, stderr: BoundedBuffer, timeout: int) -> None:
    import selectors

    deadline = time.monotonic() + timeout
    end = deadline + _CLEANUP_ALLOWANCE
    sel = selectors.DefaultSelector()
    for stream, buf in ((proc.stdout, stdout), (proc.stderr, stderr)):
        if stream is not None:
            os.set_blocking(stream.fileno(), False)
            sel.register(stream.fileno(), selectors.EVENT_READ, buf)
    marked = timed_out = False
    exit_grace_end = float("inf")
    while sel.get_map():
        now = time.monotonic()
        if proc.poll() is not None and exit_grace_end == float("inf"):
            exit_grace_end = now + _POST_EXIT_DRAIN
        limit = min(end, exit_grace_end)
        if now >= limit:
            break
        if now >= deadline and not marked:
            marked = True
            if proc.poll() is None:
                kill_process_tree(proc)
                timed_out = True
        for key, _ in sel.select(timeout=max(0.05, min(0.25, limit - now))):
            with suppress(BlockingIOError):
                chunk = os.read(key.fd, 65536)
                if chunk:
                    key.data.feed(chunk)
                else:
                    sel.unregister(key.fd)
    leftover = bool(sel.get_map())  # pipes still open when the loop gave up
    sel.close()
    for stream in (proc.stdout, proc.stderr, proc.stdin):
        if stream is not None:
            with suppress(OSError, ValueError):
                stream.close()  # safe: this thread owns the reads; nothing else holds the lock
    if proc.poll() is None:  # survived the tree kill — last-resort direct kill, still bounded
        with suppress(OSError):
            proc.kill()
        with suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=2)
    if timed_out:
        raise subprocess.TimeoutExpired(proc.args, timeout)
    if leftover:
        # child exited but pipes never reached EOF within the post-exit drain window —
        # a descendant still holds a write end open; the capture is INCOMPLETE.
        kill_process_tree(proc)
        raise IncompleteCaptureError(
            "child exited but a descendant still holds an output pipe open; captured output is incomplete"
        )


def _capture_windows(proc, stdout: BoundedBuffer, stderr: BoundedBuffer, timeout: int) -> None:
    """Windows capture: buffered streams + reader threads; pending reads are aborted via
    CancelIoEx during incomplete-capture cleanup (closing a pipe handle with a pending read
    would itself block). Bounded: joins are time-capped end-to-end."""
    readers = [
        threading.Thread(target=_drain_stream, args=(proc.stdout, stdout), daemon=True, name="opal-event-out"),
        threading.Thread(target=_drain_stream, args=(proc.stderr, stderr), daemon=True, name="opal-event-err"),
    ]
    for t in readers:
        t.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_process_tree(proc)
        with suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=_CLEANUP_ALLOWANCE)
        for t in readers:
            t.join(timeout=2)
        raise
    for t in readers:
        t.join(timeout=_POST_EXIT_DRAIN)
    if not any(t.is_alive() for t in readers):
        return
    kill_process_tree(proc)
    for stream in (proc.stdout, proc.stderr, proc.stdin):
        if stream is not None:
            with suppress(OSError, ValueError):
                _release_pipe_reader(stream)
    for t in readers:
        t.join(timeout=2)
    for stream in (proc.stdout, proc.stderr, proc.stdin):
        if stream is not None:
            with suppress(Exception):
                stream.close()
    raise IncompleteCaptureError("child exited but a descendant still holds an output pipe open; captured output is incomplete")


def kill_process_tree(proc: subprocess.Popen) -> None:
    """Kills the child and anything it spawned (session group on POSIX, job tree via taskkill)."""
    if sys.platform != "win32":
        with suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    else:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], check=False, capture_output=True, timeout=10)


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
