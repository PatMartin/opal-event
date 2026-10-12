"""Trusted child-process entrypoint for Python plugin actions.

Invoked as ``python -m opal_event.plugins.runner <module> <function>``. Reads the normalized event
as JSON from stdin, calls ``function(event) -> JSON-serializable value``, and writes exactly one
JSON line to stdout. Any plugin print/debug output must go to stderr: stdout is reserved for the
single JSON result so it is never corrupted by incidental prints.
"""

from __future__ import annotations

import importlib
import json
import sys


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: python -m opal_event.plugins.runner <module> <function>", file=sys.stderr)
        return 2
    module_name, function_name = sys.argv[1], sys.argv[2]
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"invalid JSON on stdin: {exc}", file=sys.stderr)
        return 1
    try:
        module = importlib.import_module(module_name)
        function = getattr(module, function_name)
    except (ImportError, AttributeError) as exc:
        print(f"failed to load {module_name}.{function_name}: {exc}", file=sys.stderr)
        return 1
    try:
        result = function(event)
    except Exception as exc:  # noqa: BLE001 - plugin errors must not crash the runner silently
        print(f"plugin raised: {exc}", file=sys.stderr)
        return 1
    try:
        json.dump(result, sys.stdout)
    except TypeError as exc:
        print(f"plugin result is not JSON-serializable: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
