"""Stateless rule matching: topic patterns (exact or ``prefix.*``) ANDed with condition checks.

Per zeromq_protocol.md and opal_event.yaml: ``vision.*`` matches ``vision.foo`` and deeper
descendants but not bare ``vision`` nor ``visionary``. Conditions compare strictly typed JSON
values; a missing path evaluates false except for ``exists`` with ``value=false``.
"""

from __future__ import annotations

from typing import Any

from opal_event.models import Condition, Rule


def topic_matches(pattern: str, topic: str) -> bool:
    if pattern.endswith(".*"):
        prefix = pattern[:-2]
        return topic.startswith(prefix + ".")
    return pattern == topic


def _resolve_path(data: Any, path: str) -> tuple[Any, bool]:
    """Resolves a dotted path against the normalized event. Returns (value, found)."""
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return None, False
            current = current[part]
        elif isinstance(current, list):
            if not part.lstrip("-").isdigit():
                return None, False
            index = int(part)
            if index < 0 or index >= len(current):
                return None, False
            current = current[index]
        else:
            return None, False
    return current, True


def _same_type_compare(op: str, value: Any, expected: Any) -> bool:
    # Numeric comparisons require actual numbers (bool is intentionally excluded: JSON bool != number).
    if isinstance(value, bool) or isinstance(expected, bool):
        return False
    if not isinstance(value, int | float) or not isinstance(expected, int | float):
        return False
    if op == "gt":
        return value > expected
    if op == "gte":
        return value >= expected
    if op == "lt":
        return value < expected
    if op == "lte":
        return value <= expected
    raise AssertionError(op)


def evaluate_condition(condition: Condition, event: dict[str, Any]) -> bool:
    value, found = _resolve_path(event, condition.path)
    op = condition.operator
    if op == "exists":
        return found == bool(condition.value)
    if not found:
        return False
    if op == "eq":
        return value == condition.value
    if op == "ne":
        return value != condition.value
    if op in ("gt", "gte", "lt", "lte"):
        return _same_type_compare(op, value, condition.value)
    if op == "in":
        return isinstance(condition.value, list) and value in condition.value
    if op == "not_in":
        return isinstance(condition.value, list) and value not in condition.value
    if op == "contains":
        if isinstance(value, str):
            return isinstance(condition.value, str) and condition.value in value
        if isinstance(value, list):
            return condition.value in value
        return False
    raise ValueError(f"unknown operator: {op}")


def evaluate_rule(rule: Rule, event: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    """Evaluates a rule's topic pattern and ANDed conditions. Always runs every condition check
    (for diagnostics in ``RuleTestResult.checks``) even once the overall match is already false."""
    checks: list[dict[str, Any]] = []
    topic_ok = topic_matches(rule.topic, event.get("topic", ""))
    checks.append({"kind": "topic", "pattern": rule.topic, "matched": topic_ok})
    conditions_ok = True
    for condition in rule.conditions:
        result = evaluate_condition(condition, event)
        checks.append(
            {
                "kind": "condition",
                "path": condition.path,
                "operator": condition.operator,
                "matched": result,
            }
        )
        conditions_ok = conditions_ok and result
    matched = rule.enabled and topic_ok and conditions_ok
    return matched, checks
