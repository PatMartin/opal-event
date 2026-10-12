"""``{{data.path}}`` / ``{{event}}`` templating for action inputs.

Whole JSON values preserve type when a value is the *entire* string (e.g. ``"{{data.count}}"`` with
no surrounding text yields the original int/float/bool/dict/list/null). Interpolated strings (a
placeholder embedded in surrounding text) stringify the resolved value. A missing path raises
:class:`TemplateError`; there is no expression evaluation or control flow, only path lookups.
"""

from __future__ import annotations

import json
import re
from typing import Any

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][\w.]*)\s*\}\}")


class TemplateError(Exception):
    """Raised when a template references a path that is absent from the event."""


def _resolve(expr: str, event: dict[str, Any]) -> Any:
    if expr == "event":
        return event
    if not expr.startswith("data"):
        raise TemplateError(f"unsupported template path: {expr}")
    current: Any = event
    for part in expr.split("."):
        if isinstance(current, dict):
            if part not in current:
                raise TemplateError(f"template path not found: {expr}")
            current = current[part]
        elif isinstance(current, list) and part.lstrip("-").isdigit():
            index = int(part)
            if index < 0 or index >= len(current):
                raise TemplateError(f"template path not found: {expr}")
            current = current[index]
        else:
            raise TemplateError(f"template path not found: {expr}")
    return current


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return json.dumps(value)


def render_value(template: Any, event: dict[str, Any]) -> Any:
    """Renders a single template value (str, or any JSON structure containing template strings)."""
    if isinstance(template, str):
        whole_match = _PLACEHOLDER.fullmatch(template.strip())
        if whole_match:
            return _resolve(whole_match.group(1), event)
        return _PLACEHOLDER.sub(lambda m: _stringify(_resolve(m.group(1), event)), template)
    if isinstance(template, dict):
        return {key: render_value(value, event) for key, value in template.items()}
    if isinstance(template, list):
        return [render_value(item, event) for item in template]
    return template
