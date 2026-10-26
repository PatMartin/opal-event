"""Bounded output capture for shell/python action runners.

Uses ``sys.executable -c`` as the shell target so tests are platform-independent.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

import opal_event.actions.python_plugin as pp
from opal_event.actions import communicate_bounded
from opal_event.actions.python_plugin import run_python_action
from opal_event.actions.shell import run_shell_action
from opal_event.models import PythonAction, ShellAction
from opal_event.security import subprocess_environment


def _shell(code: str) -> ShellAction:
    return ShellAction(id="t", type="shell", mode="exec", executable=sys.executable, args=["-u", "-c", code], stdin="none")


class TestShellBoundedCapture:
    def test_output_below_limit(self):
        outcome = run_shell_action(_shell("print('hi')"), {"topic": "t", "data": {}}, timeout=10, max_output_bytes=65536)
        assert outcome.status == "succeeded"
        assert outcome.stdout.strip() == "hi"
        assert outcome.stderr == ""

    def test_output_greatly_exceeding_limit(self):
        code = "import sys; sys.stdout.write('x' * 200000)"
        outcome = run_shell_action(_shell(code), {"topic": "t", "data": {}}, timeout=10, max_output_bytes=1024)
        assert outcome.status == "succeeded"
        assert outcome.stdout.startswith("x" * 1024)
        assert outcome.stdout.endswith("...[truncated]")

    def test_simultaneous_stdout_and_stderr_both_bounded(self):
        code = "import sys; sys.stdout.write('o' * 50000); sys.stdout.flush(); sys.stderr.write('e' * 50000); sys.stderr.flush()"
        outcome = run_shell_action(_shell(code), {"topic": "t", "data": {}}, timeout=10, max_output_bytes=512)
        assert outcome.status == "succeeded"
        assert outcome.stdout.startswith("o" * 512) and outcome.stdout.endswith("...[truncated]")
        assert outcome.stderr.startswith("e" * 512) and outcome.stderr.endswith("...[truncated]")

    def test_zero_byte_limit(self):
        loud = run_shell_action(_shell("print('loud')"), {"topic": "t", "data": {}}, timeout=10, max_output_bytes=0)
        assert loud.status == "succeeded"
        assert loud.stdout == "...[truncated]"
        quiet = run_shell_action(_shell("pass"), {"topic": "t", "data": {}}, timeout=10, max_output_bytes=0)
        assert quiet.status == "succeeded"
        assert quiet.stdout == ""

    def test_timeout_while_flooding_output(self):
        code = "import sys\nwhile True: sys.stdout.write('y' * 65536)"
        outcome = run_shell_action(_shell(code), {"topic": "t", "data": {}}, timeout=1, max_output_bytes=64)
        assert outcome.status == "timed_out"
        assert outcome.error_code == "timeout"


class TestPluginBoundedCapture:
    @pytest.fixture
    def plugin(self, tmp_path, monkeypatch):
        (tmp_path / "bigplug.py").write_text(
            "def small(event):\n"
            "    return {'ok': True, 'n': event['data']['n']}\n"
            "def huge(event):\n"
            "    return {'blob': 'x' * 200000}\n"
            "def flood(event):\n"
            "    import sys\n"
            "    while True:\n"
            "        sys.stderr.write('z' * 65536)\n"
        )
        real_env = subprocess_environment
        monkeypatch.setattr(pp, "subprocess_environment", lambda: real_env(extra={"PYTHONPATH": str(tmp_path)}))

    def _action(self, function: str) -> PythonAction:
        return PythonAction(id="p", type="python", module="bigplug", function=function)

    def test_small_json_result_preserved(self, plugin):
        outcome = run_python_action(self._action("small"), {"topic": "t", "data": {"n": 7}}, timeout=15, max_output_bytes=65536)
        assert outcome.status == "succeeded"
        assert outcome.result == {"ok": True, "n": 7}

    def test_oversized_json_rejected_not_parsed(self, plugin):
        outcome = run_python_action(self._action("huge"), {"topic": "t", "data": {}}, timeout=15, max_output_bytes=1024)
        assert outcome.status == "failed"
        assert outcome.error_code == "output_limit"
        assert outcome.result is None


class TestProcessCleanup:
    def test_killed_on_timeout_and_no_leaked_reader_threads(self):
        kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if sys.platform != "win32":
            kwargs["start_new_session"] = True
        else:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen([sys.executable, "-u", "-c", "import time; print('a'); time.sleep(60)"], **kwargs)
        with pytest.raises(subprocess.TimeoutExpired):
            communicate_bounded(proc, None, timeout=1, max_output_bytes=65536)
        assert proc.poll() is not None  # tree is dead before the exception propagates
