"""`opal event ...` command line. Every command calls the Opal Event REST API (see OPERATIONS)."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import typer
import yaml

from opal_event.cli.client import ApiCallError, ApiClient, api_url_from_env, token_from_env


@dataclass
class CliState:
    client: ApiClient
    json_output: bool
    context_headers: dict[str, str]


event_app = typer.Typer(
    help="OPAL Event: event bus, rules, and action execution.", no_args_is_help=True, pretty_exceptions_enable=False
)
rules_app = typer.Typer(help="Manage rules that match events to actions.", no_args_is_help=True)
actions_app = typer.Typer(help="Manage registered actions (shell/http/python).", no_args_is_help=True)
executions_app = typer.Typer(help="Inspect action execution history.", no_args_is_help=True)
info_app = typer.Typer(help="Service identity, health, and runtime metrics.", no_args_is_help=True)
service_app = typer.Typer(help="Run the local opal-event-svc process.", no_args_is_help=True)
personas_app = typer.Typer(help="Manage registered personas (named automation identities).", no_args_is_help=True)
profiles_app = typer.Typer(help="Manage named presets of portable operating defaults.", no_args_is_help=True)
history_app = typer.Typer(help="Stored-event history: list, inspect, and remove retained events.", no_args_is_help=True)
# Canonical x-opal-cli bindings: `config setting <verb>` plus the declared flat `setting` aliases.
config_app = typer.Typer(help="Configuration resources.", no_args_is_help=True)
config_setting_app = typer.Typer(help="Service-wide portable settings.", no_args_is_help=True)
config_app.add_typer(config_setting_app, name="setting")
for _sub, _name in (
    (rules_app, "rules"),
    (actions_app, "actions"),
    (executions_app, "executions"),
    (history_app, "history"),
    (info_app, "info"),
    (config_app, "config"),
    (profiles_app, "profiles"),
    (personas_app, "personas"),
    (service_app, "service"),
):
    event_app.add_typer(_sub, name=_name)


@event_app.callback()
def event_callback(
    ctx: typer.Context,
    api_url: str = typer.Option(None, "--api-url", help="REST API base URL [env: OPAL_EVENT_API_URL]"),
    token: str = typer.Option(None, "--token", help="Bearer token [env: OPAL_EVENT_API_TOKEN]"),
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON responses"),
    timeout: float = typer.Option(30.0, "--timeout", help="HTTP timeout in seconds"),
    opal_session_id: str = typer.Option(
        None, "--opal-session-id", help="X-Opal-Session-Id to send with every request [env: OPAL_SESSION_ID]"
    ),
    opal_transaction_id: str = typer.Option(
        None, "--opal-transaction-id", help="X-Opal-Transaction-Id to send with every request [env: OPAL_TRANSACTION_ID]"
    ),
    opal_causation_id: str = typer.Option(
        None, "--opal-causation-id", help="X-Opal-Causation-Id to send with every request [env: OPAL_CAUSATION_ID]"
    ),
    env_file: str = typer.Option(None, "--env-file", help="Load this dotenv file instead of ./.env"),
    no_env_file: bool = typer.Option(False, "--no-env-file", help="Disable dotenv loading and .env initialization"),
) -> None:
    from opal_event.config import dotenv_baseline
    from opal_event.dotenv import DotenvError, prepare

    try:
        prepare(env_file=env_file, no_env_file=no_env_file, baseline=dotenv_baseline(None))
    except DotenvError as exc:
        raise typer.BadParameter(str(exc)) from exc
    context_headers = {
        "X-Opal-Session-Id": opal_session_id or os.environ.get("OPAL_SESSION_ID"),
        "X-Opal-Transaction-Id": opal_transaction_id or os.environ.get("OPAL_TRANSACTION_ID"),
        "X-Opal-Causation-Id": opal_causation_id or os.environ.get("OPAL_CAUSATION_ID"),
    }
    context_headers = {k: v for k, v in context_headers.items() if v}
    ctx.obj = CliState(ApiClient(api_url or api_url_from_env(), token or token_from_env(), timeout), json_output, context_headers)


# -- helpers -----------------------------------------------------------------------------------


def _state(ctx: typer.Context) -> CliState:
    return ctx.find_root().obj if not isinstance(ctx.obj, CliState) else ctx.obj


def _api(ctx: typer.Context, op: str, **kwargs: Any) -> httpx.Response:
    """Raw client call: merges context headers but lets ApiCallError/httpx.HTTPError propagate."""
    state = _state(ctx)
    if state.context_headers:
        kwargs["headers"] = {**state.context_headers, **(kwargs.get("headers") or {})}
    return state.client.call(op, **kwargs)


def _call(ctx: typer.Context, op: str, **kwargs: Any) -> httpx.Response:
    try:
        return _api(ctx, op, **kwargs)
    except ApiCallError as exc:
        typer.echo(f"Error: {exc.describe()}", err=True)
        raise typer.Exit(1) from None
    except httpx.HTTPError as exc:
        typer.echo(f"Error: cannot reach Opal Event API: {exc}", err=True)
        raise typer.Exit(2) from None


def _emit(ctx: typer.Context, data: Any, columns: list[str] | None = None) -> None:
    if columns and isinstance(data, list) and not _state(ctx).json_output:
        if not data:
            typer.echo("(none)")
            return
        rows = [[_cell(item.get(c)) for c in columns] for item in data]
        widths = [max(len(c), *(len(r[i]) for r in rows)) for i, c in enumerate(columns)]
        typer.echo("  ".join(c.upper().ljust(w) for c, w in zip(columns, widths, strict=True)))
        for row in rows:
            typer.echo("  ".join(v.ljust(w) for v, w in zip(row, widths, strict=True)))
        return
    typer.echo(json.dumps(data, indent=2))


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, dict | list):
        return json.dumps(value)
    return str(value)


def _read_payload(json_str: str | None, file: str | None) -> dict[str, Any]:
    """Reads a JSON request body from ``--json``, ``--file``, or stdin (in that priority order)."""
    if json_str is not None:
        text = json_str
    elif file is not None:
        text = Path(file).read_text(encoding="utf-8")
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        raise typer.BadParameter("provide --json, --file, or pipe JSON on stdin")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"invalid JSON: {exc}") from None


def _event_input(event_json: str) -> dict[str, Any]:
    try:
        return json.loads(event_json)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"invalid JSON: {exc}") from None


_NAME = typer.Option(..., "--name", help="Dotted portable setting key")
_VALUE = typer.Option(..., "--value", help="JSON value (or literal string) to set")
_IFM = typer.Option(None, "--if-match", help="Revision precondition (default: auto-fetched)")


def _current_revision(ctx: typer.Context) -> str:
    resp = _call(ctx, "opal_setting_list")
    return resp.headers["etag"]


def _resource_etag(ctx: typer.Context, get_op: str, path_params: dict[str, str]) -> str:
    """Fetches the current strong ETag for a single rule/action resource (used to auto-populate
    ``If-Match`` on update/delete when the caller doesn't supply one explicitly)."""
    resp = _call(ctx, get_op, path_params=path_params)
    return resp.headers["etag"]


# -- `opal event` top-level commands ------------------------------------------------------------


@event_app.command("publish")
def publish(
    ctx: typer.Context,
    topic: str = typer.Argument(..., help="Dotted event topic, e.g. intent.recognized"),
    data_json: str = typer.Option(..., "--data-json", help="JSON value for the event 'data' field"),
    id: str = typer.Option(None, "--id", help="Explicit event UUID (default: server-generated)"),
    source: str = typer.Option(None, "--source", help="Event source (default: opal-event-rest)"),
    timestamp: str = typer.Option(None, "--timestamp", help="RFC3339 timestamp (default: now)"),
) -> None:
    """Publish an event (normalizes missing metadata, enqueues publication; 202 != delivered)."""
    body = {"topic": topic, "data": json.loads(data_json)}
    if id:
        body["id"] = id
    if source:
        body["source"] = source
    if timestamp:
        body["timestamp"] = timestamp
    resp = _call(ctx, "publish_event", json=body)
    _emit(ctx, resp.json())


def _list_events(
    ctx: typer.Context, limit: int, topic: str | None, source: str | None, event_from: str | None, event_to: str | None
) -> None:
    raw_params = dict(limit=limit, topic=topic, source=source, **{"from": event_from, "to": event_to})
    params = {k: v for k, v in raw_params.items() if v is not None}
    data = _call(ctx, "list_events", params=params).json()
    _emit(ctx, data, columns=["id", "topic", "source", "timestamp"])


def _show_event(ctx: typer.Context, event_id: str) -> None:
    _emit(ctx, _call(ctx, "show_event", path_params={"event_id": event_id}).json())


def _remove_event(ctx: typer.Context, event_id: str) -> None:
    _call(ctx, "remove_event", path_params={"event_id": event_id})
    if _state(ctx).json_output:
        typer.echo(json.dumps({"deleted": event_id}))
    else:
        typer.echo(f"Deleted event '{event_id}'.")


def _clear_events(ctx: typer.Context) -> None:
    _call(ctx, "clear_events")
    if _state(ctx).json_output:
        typer.echo(json.dumps({"cleared": True}))
    else:
        typer.echo("Cleared all events.")


def _topics(ctx: typer.Context) -> None:
    _emit(ctx, _call(ctx, "list_topics").json())


@event_app.command("list")
def list_events(
    ctx: typer.Context,
    limit: int = typer.Option(50, "--limit"),
    topic: str = typer.Option(None, "--topic"),
    source: str = typer.Option(None, "--source"),
    event_from: str = typer.Option(None, "--from"),
    event_to: str = typer.Option(None, "--to"),
) -> None:
    """List stored events (alias of `history list`)."""
    _list_events(ctx, limit, topic, source, event_from, event_to)


@event_app.command("get")
def get_event(ctx: typer.Context, event_id: str = typer.Argument(...)) -> None:
    """Get a stored event by ID (alias of `history show`)."""
    _show_event(ctx, event_id)


@event_app.command("delete")
def delete_event_cmd(ctx: typer.Context, event_id: str = typer.Argument(..., help="Event ID to delete")) -> None:
    """Delete a stored event by ID (alias of `history remove`)."""
    _remove_event(ctx, event_id)


@event_app.command("clear")
def clear_events_cmd(ctx: typer.Context) -> None:
    """Clear all events from all topics (alias of `history clear`)."""
    _clear_events(ctx)


@event_app.command("topics")
def topics(ctx: typer.Context) -> None:
    """List observed topics, incomplete when persistence is off (alias of `history topics`)."""
    _topics(ctx)


@history_app.command("list")
def history_list(
    ctx: typer.Context,
    limit: int = typer.Option(50, "--limit"),
    topic: str = typer.Option(None, "--topic"),
    source: str = typer.Option(None, "--source"),
    event_from: str = typer.Option(None, "--from"),
    event_to: str = typer.Option(None, "--to"),
) -> None:
    """List stored events."""
    _list_events(ctx, limit, topic, source, event_from, event_to)


@history_app.command("show")
def history_show(ctx: typer.Context, event_id: str = typer.Argument(...)) -> None:
    """Show one stored event."""
    _show_event(ctx, event_id)


@history_app.command("remove")
def history_remove(ctx: typer.Context, event_id: str = typer.Argument(..., help="Event ID to delete")) -> None:
    """Delete one stored event by ID."""
    _remove_event(ctx, event_id)


@history_app.command("clear")
def history_clear(ctx: typer.Context) -> None:
    """Delete every stored event in every topic."""
    _clear_events(ctx)


@history_app.command("topics")
def history_topics(ctx: typer.Context) -> None:
    """List observed topics (incomplete when persistence is off)."""
    _topics(ctx)


@info_app.command("health")
def info_health(
    ctx: typer.Context,
    live: bool = typer.Option(False, "--live", help="Liveness probe only (GET /api/v1/health/live; no auth required)"),
    ready: bool = typer.Option(False, "--ready", help="Readiness probe only (GET /api/v1/health/ready; no auth required)"),
) -> None:
    """Get service health, or run the liveness (--live) or readiness (--ready) probe."""
    if live and ready:
        raise typer.BadParameter("--live and --ready are mutually exclusive")
    op = "opal_health_live_get" if live else "opal_health_ready_get" if ready else "opal_health_get"
    _emit(ctx, _call(ctx, op).json())


# -- `opal event rules` --------------------------------------------------------------------------


@rules_app.command("list")
def rules_list(ctx: typer.Context) -> None:
    """List rules (complete bounded catalog; no pagination)."""
    data = _call(ctx, "list_rules").json()
    _emit(ctx, data, columns=["id", "enabled", "topic", "actions"])


@rules_app.command("create")
def rules_create(
    ctx: typer.Context,
    json_str: str = typer.Option(None, "--json", help="Full Rule JSON body"),
    file: str = typer.Option(None, "--file", help="Path to a file containing the Rule JSON body"),
) -> None:
    """Create a rule. Requires a matching If-Match (428 if omitted server-side; auto-fetched here)."""
    body = _read_payload(json_str, file)
    revision = _current_revision(ctx)
    _emit(ctx, _call(ctx, "create_rule", json=body, headers={"If-Match": revision}).json())


@rules_app.command("get")
def rules_get(ctx: typer.Context, rule_id: str = typer.Argument(...)) -> None:
    """Get a rule."""
    _emit(ctx, _call(ctx, "show_rule", path_params={"rule_id": rule_id}).json())


@rules_app.command("delete")
def rules_delete(
    ctx: typer.Context,
    rule_id: str = typer.Argument(...),
    if_match: str = typer.Option(None, "--if-match", help="Strong ETag precondition (default: auto-fetched)"),
) -> None:
    """Delete a rule. Requires a matching If-Match (428 if omitted server-side; auto-fetched here)."""
    revision = if_match or _resource_etag(ctx, "show_rule", {"rule_id": rule_id})
    _call(ctx, "remove_rule", path_params={"rule_id": rule_id}, headers={"If-Match": revision})
    typer.echo(f"deleted rule {rule_id}")


@rules_app.command("update")
def rules_update(
    ctx: typer.Context,
    rule_id: str = typer.Argument(...),
    json_str: str = typer.Option(None, "--json", help="Partial RulePatch JSON body"),
    file: str = typer.Option(None, "--file", help="Path to a file containing the RulePatch JSON body"),
    if_match: str = typer.Option(None, "--if-match", help="Strong ETag precondition (default: auto-fetched)"),
) -> None:
    """Partially update a rule. Requires a matching If-Match (428 if omitted server-side; auto-fetched here)."""
    body = _read_payload(json_str, file)
    revision = if_match or _resource_etag(ctx, "show_rule", {"rule_id": rule_id})
    _emit(
        ctx,
        _call(ctx, "update_rule", path_params={"rule_id": rule_id}, json=body, headers={"If-Match": revision}).json(),
    )


@rules_app.command("test")
def rules_test(
    ctx: typer.Context, rule_id: str = typer.Argument(...), event_json: str = typer.Option(..., "--event-json")
) -> None:
    """Evaluate a rule against a sample event without executing any action."""
    body = {"event": _event_input(event_json)}
    _emit(ctx, _call(ctx, "test_rule", path_params={"rule_id": rule_id}, json=body).json())


# -- `opal event actions` ------------------------------------------------------------------------


@actions_app.command("list")
def actions_list(ctx: typer.Context) -> None:
    """List actions (complete bounded catalog; no pagination)."""
    data = _call(ctx, "list_actions").json()
    _emit(ctx, data, columns=["id", "type", "enabled"])


@actions_app.command("create")
def actions_create(
    ctx: typer.Context,
    json_str: str = typer.Option(None, "--json", help="Full Action JSON body (shell/http/python)"),
    file: str = typer.Option(None, "--file", help="Path to a file containing the Action JSON body"),
) -> None:
    """Create an action. Requires a matching If-Match (428 if omitted server-side; auto-fetched here)."""
    body = _read_payload(json_str, file)
    revision = _current_revision(ctx)
    _emit(ctx, _call(ctx, "create_action", json=body, headers={"If-Match": revision}).json())


@actions_app.command("get")
def actions_get(ctx: typer.Context, action_id: str = typer.Argument(...)) -> None:
    """Get an action."""
    _emit(ctx, _call(ctx, "show_action", path_params={"action_id": action_id}).json())


@actions_app.command("delete")
def actions_delete(
    ctx: typer.Context,
    action_id: str = typer.Argument(...),
    if_match: str = typer.Option(None, "--if-match", help="Strong ETag precondition (default: auto-fetched)"),
) -> None:
    """Delete an unused action (409 if referenced by a rule; 428/412 if If-Match is missing/stale)."""
    revision = if_match or _resource_etag(ctx, "show_action", {"action_id": action_id})
    _call(ctx, "remove_action", path_params={"action_id": action_id}, headers={"If-Match": revision})
    typer.echo(f"deleted action {action_id}")


@actions_app.command("update")
def actions_update(
    ctx: typer.Context,
    action_id: str = typer.Argument(...),
    json_str: str = typer.Option(None, "--json", help="Partial ActionPatch JSON body"),
    file: str = typer.Option(None, "--file", help="Path to a file containing the ActionPatch JSON body"),
    if_match: str = typer.Option(None, "--if-match", help="Strong ETag precondition (default: auto-fetched)"),
) -> None:
    """Partially update an action. Requires a matching If-Match (428 if omitted; auto-fetched here)."""
    body = _read_payload(json_str, file)
    revision = if_match or _resource_etag(ctx, "show_action", {"action_id": action_id})
    _emit(
        ctx,
        _call(
            ctx,
            "update_action",
            path_params={"action_id": action_id},
            json=body,
            headers={"If-Match": revision},
        ).json(),
    )


@actions_app.command("execute")
def actions_execute(
    ctx: typer.Context,
    action_id: str = typer.Argument(...),
    event_json: str = typer.Option(..., "--event-json"),
    idempotency_key: str = typer.Option(
        None, "--idempotency-key", help="Deduplicate retries: same key+payload returns the original receipt"
    ),
) -> None:
    """Manually execute a registered, enabled action."""
    body = {"event": _event_input(event_json)}
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
    _emit(
        ctx,
        _call(ctx, "execute_action", path_params={"action_id": action_id}, json=body, headers=headers).json(),
    )


# -- `opal event executions` ---------------------------------------------------------------------


@executions_app.command("list")
def executions_list(
    ctx: typer.Context,
    limit: int = typer.Option(100, "--limit"),
    status: str = typer.Option(None, "--status"),
    rule_id: str = typer.Option(None, "--rule-id"),
    action_id: str = typer.Option(None, "--action-id"),
    event_id: str = typer.Option(None, "--event-id"),
) -> None:
    """List executions."""
    raw_params = dict(limit=limit, status=status, rule_id=rule_id, action_id=action_id, event_id=event_id)
    params = {k: v for k, v in raw_params.items() if v is not None}
    data = _call(ctx, "list_executions", params=params).json()
    _emit(ctx, data, columns=["id", "action_id", "status", "trigger", "created_at"])


@executions_app.command("get")
def executions_get(ctx: typer.Context, execution_id: str = typer.Argument(...)) -> None:
    """Get an execution result."""
    _emit(ctx, _call(ctx, "show_execution", path_params={"execution_id": execution_id}).json())


# -- `opal event config save|load|validate` -------------------------------------------------------


def _read_snapshot_file(path: str) -> dict:
    """Read a local YAML or JSON snapshot file (kernel ``yaml_or_json_file`` binding)."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: cannot read snapshot file {path}: {exc}", err=True)
        raise typer.Exit(2) from None
    if not isinstance(data, dict):
        typer.echo(f"Error: snapshot file {path} is not a mapping", err=True)
        raise typer.Exit(2)
    return data


@config_app.command("save")
def config_save_cmd(
    ctx: typer.Context,
    out: str = typer.Option(None, "--out", help="Write the snapshot to FILE instead of the managed store"),
) -> None:
    """Capture the persistent configuration as a portable snapshot."""
    if out is None:
        data = _call(ctx, "opal_config_save", json={"destination": "managed"}).json()
        typer.echo("managed snapshot saved")
        _emit(ctx, data["snapshot"])
    else:
        data = _call(ctx, "opal_config_save", json={"destination": "response"}).json()
        Path(out).write_text(json.dumps(data["snapshot"], indent=2), encoding="utf-8")
        typer.echo(f"snapshot written to {out}", err=True)


@config_app.command("load")
def config_load_cmd(
    ctx: typer.Context,
    in_file: str = typer.Option(None, "--in", help="YAML or JSON snapshot file (omit to load the managed snapshot)"),
    if_match: str = _IFM,
) -> None:
    """Atomically replace the persistent portable configuration with a snapshot."""
    body = {"snapshot": _read_snapshot_file(in_file)} if in_file else {}
    revision = if_match or _current_revision(ctx)
    _emit(ctx, _call(ctx, "opal_config_load", json=body, headers={"If-Match": revision}).json())


@config_app.command("validate")
def config_validate_cmd(
    ctx: typer.Context,
    in_file: str = typer.Option(None, "--in", help="YAML or JSON snapshot file (omit to validate live configuration)"),
) -> None:
    """Validate a candidate snapshot, or the live configuration, without side effects."""
    body = {"snapshot": _read_snapshot_file(in_file)} if in_file else {}
    data = _call(ctx, "opal_config_validate", json=body).json()
    _emit(ctx, data)
    if not data["valid"]:
        raise typer.Exit(1)


# -- `opal event personas` ---------------------------------------------------------------------


@personas_app.command("list")
def personas_list(
    ctx: typer.Context,
    enabled: bool = typer.Option(None, "--enabled/--disabled", help="Filter by enabled state"),
) -> None:
    """List registered personas (complete bounded catalog; no pagination)."""
    params = {} if enabled is None else {"enabled": enabled}
    _emit(ctx, _call(ctx, "opal_persona_list", params=params).json(), columns=["persona_id", "name", "enabled", "voice_id"])


@personas_app.command("create")
def personas_create(
    ctx: typer.Context,
    json_str: str = typer.Option(None, "--json", help="Full PersonaCreate JSON body"),
    file: str = typer.Option(None, "--file", help="Path to a file containing the PersonaCreate JSON body"),
    if_match: str = typer.Option(None, "--if-match", help="Revision precondition (default: auto-fetched)"),
) -> None:
    """Register a persona. Requires a matching If-Match (428 if omitted server-side; auto-fetched here)."""
    body = _read_payload(json_str, file)
    revision = if_match or _current_revision(ctx)
    _emit(ctx, _call(ctx, "opal_persona_create", json=body, headers={"If-Match": revision}).json())


@personas_app.command("get")
def personas_get(ctx: typer.Context, persona_id: str = typer.Argument(...)) -> None:
    """Get a persona."""
    _emit(ctx, _call(ctx, "opal_persona_get", path_params={"persona_id": persona_id}).json())


@personas_app.command("update")
def personas_update(
    ctx: typer.Context,
    persona_id: str = typer.Argument(...),
    json_str: str = typer.Option(None, "--json", help="Partial PersonaUpdate JSON body"),
    file: str = typer.Option(None, "--file", help="Path to a file containing the PersonaUpdate JSON body"),
    if_match: str = typer.Option(None, "--if-match", help="Strong ETag precondition (default: auto-fetched)"),
) -> None:
    """Partially update a persona. Requires a matching If-Match (428 if omitted; auto-fetched here)."""
    body = _read_payload(json_str, file)
    revision = if_match or _resource_etag(ctx, "opal_persona_get", {"persona_id": persona_id})
    _emit(
        ctx,
        _call(
            ctx, "opal_persona_patch", path_params={"persona_id": persona_id}, json=body, headers={"If-Match": revision}
        ).json(),
    )


@personas_app.command("delete")
def personas_delete(
    ctx: typer.Context,
    persona_id: str = typer.Argument(...),
    if_match: str = typer.Option(None, "--if-match", help="Strong ETag precondition (default: auto-fetched)"),
) -> None:
    """Delete a persona. Requires a matching If-Match (428 if omitted server-side; auto-fetched here)."""
    revision = if_match or _resource_etag(ctx, "opal_persona_get", {"persona_id": persona_id})
    _call(ctx, "opal_persona_delete", path_params={"persona_id": persona_id}, headers={"If-Match": revision})
    typer.echo(f"deleted persona {persona_id}")


@personas_app.command("validate")
def personas_validate(ctx: typer.Context, persona_id: str = typer.Argument(...)) -> None:
    """Check that a persona is registered, enabled, and its bindings can be resolved."""
    data = _call(ctx, "opal_persona_validate", path_params={"persona_id": persona_id}).json()
    _emit(ctx, data)
    if not data["valid"]:
        raise typer.Exit(1)


# -- `opal event service` ----------------------------------------------------------------------


PID_FILE = Path("logs") / "opal_event.pid"
LOG_FILE = Path("logs") / "opal_event.log"


def _manager(config_path: str | None = None):
    """Local fallback handle mirroring the REST ``/api/v1/service/*`` manager."""
    from opal_event.lifecycle import ServiceProcessManager

    return ServiceProcessManager(config_path=config_path)


@service_app.command("start")
def service_start(
    ctx: typer.Context,
    config: str = typer.Option(None, "--config", help="YAML configuration file [env: OPAL_EVENT_CONFIG]"),
    append: bool = typer.Option(False, "--append", help="Append to the service log instead of truncating it"),
) -> None:
    """Start the managed server in the background (restarts one already running).

    Calls POST /api/v1/service/start first so remote management works; when the API
    is unreachable it falls back to the same pidfile/log bookkeeping locally, which
    is what lets `service start` cold-start a stopped service."""
    import os

    config_path = config or os.environ.get("OPAL_EVENT_CONFIG", "config/opal_event.yaml")
    try:
        _emit(ctx, _api(ctx, "opal_service_start", json={"append": append}).json())
        return
    except ApiCallError as exc:
        typer.echo(f"Error: {exc.describe()}", err=True)
        raise typer.Exit(1) from None
    except httpx.HTTPError:
        pass  # API unreachable: spawn the managed server locally
    proc = _manager(config_path).launch(append=append)
    typer.echo(f"opal-event-svc started in background (pid {proc.pid}); logs: {LOG_FILE}")


@service_app.command("stop")
def service_stop(ctx: typer.Context) -> None:
    """Stop the managed server if it is running (not an error when already stopped)."""
    try:
        _emit(ctx, _api(ctx, "opal_service_stop").json())
        return
    except ApiCallError as exc:
        typer.echo(f"Error: {exc.describe()}", err=True)
        raise typer.Exit(1) from None
    except httpx.HTTPError:
        pass  # API unreachable: stop the tracked pid locally
    status, _ = _manager().stop()
    if status["previous_pid"] is None:
        typer.echo("no managed server running")
    else:
        typer.echo(f"server stopped (pid {status['previous_pid']})")


@service_app.command("log")
def service_log_cmd(ctx: typer.Context, lines: int = typer.Option(10, "--lines", "-n", min=1, max=1000)) -> None:
    """Tail the service log file (default: last 10 lines)."""
    try:
        data = _api(ctx, "opal_service_log", params={"lines": lines}).json()
        if _state(ctx).json_output:
            _emit(ctx, data)
        else:
            for line in data["lines"]:
                typer.echo(line)
        return
    except ApiCallError as exc:
        typer.echo(f"Error: {exc.describe()}", err=True)
        raise typer.Exit(1) from None
    except httpx.HTTPError:
        pass  # API unreachable: read the local log file
    tail = _manager().tail_log(lines)
    if tail is None:
        typer.echo(f"No log file at {LOG_FILE}; start the service via `service start` first", err=True)
        raise typer.Exit(1)
    for line in tail["lines"]:
        typer.echo(line)


@service_app.command("view")
def service_view(ctx: typer.Context) -> None:
    """Print the last 100 lines of the service log (alias of `service log -n 100`)."""
    try:
        data = _api(ctx, "opal_service_log", params={"lines": 100}).json()
        typer.echo("\n".join(data["lines"]) + ("\n" if data["lines"] else ""), nl=False)
        return
    except (ApiCallError, httpx.HTTPError):
        pass
    tail = _manager().tail_log(100)
    if tail is None:
        typer.echo(f"No log file at {LOG_FILE}; start the service via `service start` first", err=True)
        raise typer.Exit(1)
    typer.echo("\n".join(tail["lines"]) + ("\n" if tail["lines"] else ""), nl=False)


@event_app.command("log")
def service_log(
    lines: int = typer.Option(20, "--lines", help="Print the last N log lines before following"),
    follow: bool = typer.Option(True, "--follow/--no-follow", help="Keep tailing new output (Ctrl+C to exit)"),
) -> None:
    """Tail the opal-event-svc log until interrupted."""
    import time

    if not LOG_FILE.exists():
        typer.echo(f"No log file at {LOG_FILE}; start the service via `service start` first", err=True)
        raise typer.Exit(1)

    with LOG_FILE.open("r", encoding="utf-8", errors="replace") as fh:
        tail = fh.readlines()[-lines:] if lines > 0 else []
        for line in tail:
            typer.echo(line, nl=False)
        if not follow:
            return
        try:
            while True:
                chunk = fh.read()
                if chunk:
                    typer.echo(chunk, nl=False)
                else:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            return


@event_app.command("tree")
def command_tree(
    ascii: bool = typer.Option(False, "--ascii", help="Plain ASCII output instead of a color-coded tree"),
) -> None:
    """Print this CLI's command/argument tree, generated from the live Typer app."""
    if ascii:
        typer.echo(_render_command_tree())
        return
    from rich.console import Console

    Console().print(_render_rich_command_tree())


# -- `opal event config setting` (x-opal-cli canonical bindings, v0.1.0) ---------------------------
# Kernel-declared spellings only: `config setting list|show|set|remove` with `--name` bound to the
# {key} path parameter. The retired collection PATCH, `config setting update|patch`, and the flat
# `setting`/`settings` groups are removed.


def _settings_show_all(ctx: typer.Context) -> None:
    _emit(ctx, _call(ctx, "opal_setting_list").json())


def _settings_show_key(ctx: typer.Context, key: str) -> None:
    _emit(ctx, _call(ctx, "opal_setting_get", path_params={"key": key}).json())


def _settings_set_key(ctx: typer.Context, key: str, value: str, if_match: str | None) -> None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    revision = if_match or _current_revision(ctx)
    _emit(
        ctx,
        _call(ctx, "opal_setting_put", path_params={"key": key}, json={"value": parsed}, headers={"If-Match": revision}).json(),
    )


def _settings_remove_key(ctx: typer.Context, key: str, if_match: str | None) -> None:
    revision = if_match or _current_revision(ctx)
    _call(ctx, "opal_setting_delete", path_params={"key": key}, headers={"If-Match": revision})
    typer.echo(f"removed setting override '{key}'")


@config_setting_app.command("list")
def config_setting_list(ctx: typer.Context) -> None:
    """List the portable settings map."""
    _settings_show_all(ctx)


@config_setting_app.command("show")
def config_setting_show(ctx: typer.Context, key: str = _NAME) -> None:
    """Show one setting."""
    _settings_show_key(ctx, key)


@config_setting_app.command("set")
def config_setting_set(ctx: typer.Context, key: str = _NAME, value: str = _VALUE, if_match: str = _IFM) -> None:
    """Create or replace one setting value."""
    _settings_set_key(ctx, key, value, if_match)


@config_setting_app.command("remove")
def config_setting_remove(ctx: typer.Context, key: str = _NAME, if_match: str = _IFM) -> None:
    """Delete the persistent override for one setting key (restores the default)."""
    _settings_remove_key(ctx, key, if_match)


# -- `opal event profiles` ---------------------------------------------------------------------


def _profile_etag(ctx: typer.Context, profile_id: int) -> str:
    resp = _call(ctx, "opal_profile_get", path_params={"profile_id": profile_id})
    return resp.headers["etag"]


@profiles_app.command("list")
def profiles_list(ctx: typer.Context) -> None:
    """List profiles (complete bounded catalog; no pagination)."""
    _emit(ctx, _call(ctx, "opal_profile_list").json(), columns=["id", "name", "is_active"])


@profiles_app.command("create")
def profiles_create(
    ctx: typer.Context,
    name: str = typer.Argument(...),
    description: str = typer.Option(None, "--description"),
    active: bool = typer.Option(False, "--active", help="Activate this profile on create"),
    json_str: str = typer.Option(None, "--json", help="Full ProfileCreate JSON body"),
    file: str = typer.Option(None, "--file", help="Path to a file containing the ProfileCreate body"),
    if_match: str = _IFM,
) -> None:
    """Create a profile. Requires a matching If-Match (428 if omitted; auto-fetched here)."""
    body: dict[str, Any] = {"name": name}
    if description is not None:
        body["description"] = description
    if active:
        body["is_active"] = True
    if json_str is not None or file is not None:
        body = _read_payload(json_str, file)
    revision = if_match or _current_revision(ctx)
    _emit(ctx, _call(ctx, "opal_profile_create", json=body, headers={"If-Match": revision}).json())


@profiles_app.command("get")
def profiles_get(ctx: typer.Context, profile_id: int = typer.Argument(...)) -> None:
    """Show one profile."""
    _emit(ctx, _call(ctx, "opal_profile_get", path_params={"profile_id": profile_id}).json())


@profiles_app.command("update")
def profiles_update(
    ctx: typer.Context,
    profile_id: int = typer.Argument(...),
    json_str: str = typer.Option(None, "--json", help="Partial ProfilePatch JSON body"),
    file: str = typer.Option(None, "--file", help="Path to a file containing the patch body"),
    if_match: str = _IFM,
) -> None:
    """Partially update a profile (is_active: true activates and applies the preset)."""
    body = _read_payload(json_str, file)
    revision = if_match or _profile_etag(ctx, profile_id)
    _emit(
        ctx,
        _call(
            ctx, "opal_profile_patch", path_params={"profile_id": profile_id}, json=body, headers={"If-Match": revision}
        ).json(),
    )


@profiles_app.command("delete")
def profiles_delete(ctx: typer.Context, profile_id: int = typer.Argument(...), if_match: str = _IFM) -> None:
    """Delete a profile. Requires a matching If-Match (428 if omitted; auto-fetched here)."""
    revision = if_match or _profile_etag(ctx, profile_id)
    _call(ctx, "opal_profile_delete", path_params={"profile_id": profile_id}, headers={"If-Match": revision})
    typer.echo(f"deleted profile {profile_id}")


@profiles_app.command("activate")
def profiles_activate(ctx: typer.Context, profile_id: int = typer.Argument(...), if_match: str = _IFM) -> None:
    """Activate a profile: apply its stored preset to live config and deactivate others."""
    revision = if_match or _profile_etag(ctx, profile_id)
    _emit(ctx, _call(ctx, "opal_profile_activate", path_params={"profile_id": profile_id}, headers={"If-Match": revision}).json())


# -- `opal` root -----------------------------------------------------------------------------

app = typer.Typer(help="OPAL command line.", no_args_is_help=True, pretty_exceptions_enable=False)
app.add_typer(event_app, name="event")
app.add_typer(service_app, name="service")

try:  # Share the `opal` root with sibling OPAL CLIs when they are installed alongside this one.
    from opal_talk.cli.main import talk_app  # type: ignore[import-not-found]

    app.add_typer(talk_app, name="talk")
except Exception:  # noqa: BLE001
    pass

try:
    from opal_listener.cli.main import listen_app  # type: ignore[import-not-found]

    app.add_typer(listen_app, name="listen")
except Exception:  # noqa: BLE001
    pass


def _param_label(param) -> tuple[str, str] | None:
    """A param's ``(text, style)`` — yellow when required, dim when bracketed — or None to skip it.

    Matches opal-listen's tree renderer: ``name``/``[name]`` for arguments,
    ``--flag``/``[--flag]`` for options; hidden, ``expose_value=False`` (shell-completion
    plumbing), and ``--help`` params are skipped."""
    if getattr(param, "hidden", False) or getattr(param, "expose_value", True) is False:
        return None
    kind = getattr(param, "param_type_name", "")
    required = getattr(param, "required", False)
    style = "yellow" if required else "dim"
    if kind == "argument":
        return (param.name if required else f"[{param.name}]", style)
    if kind != "option":
        return None
    opts = [*getattr(param, "opts", ()), *getattr(param, "secondary_opts", ())]
    flag = next((o for o in opts if o.startswith("--")), opts[0] if opts else param.name)
    if flag == "--help":
        return None
    return (flag if required else f"[{flag}]", style)


def _usage_labels(cmd) -> list[tuple[str, str]]:
    return [label for p in getattr(cmd, "params", ()) if (label := _param_label(p))]


def _visible_commands(group) -> list[tuple[str, Any]]:
    return [(n, c) for n, c in getattr(group, "commands", {}).items() if not getattr(c, "hidden", False)]


def _is_group(cmd) -> bool:
    return bool(getattr(cmd, "commands", None))


def _styled_label(name, cmd, name_style):
    from rich.text import Text

    text = Text(name, style=name_style)
    for label, style in _usage_labels(cmd):
        text.append(f" {label}", style=style)
    return text


def _plain_label(name, cmd) -> str:
    return " ".join([name, *(label for label, _ in _usage_labels(cmd))])


def _add_commands(node, group) -> None:
    for name, cmd in _visible_commands(group):
        child = node.add(_styled_label(name, cmd, "bold cyan" if _is_group(cmd) else "cyan"), guide_style="blue")
        if _is_group(cmd):
            _add_commands(child, cmd)


def _ascii_lines(group, prefix) -> list[str]:
    visible = _visible_commands(group)
    lines: list[str] = []
    for i, (name, cmd) in enumerate(visible):
        last = i == len(visible) - 1
        lines.append(f"{prefix}{'`-- ' if last else '|-- '}{_plain_label(name, cmd)}")
        if _is_group(cmd):
            lines.extend(_ascii_lines(cmd, prefix + ("    " if last else "|   ")))
    return lines


def _render_command_tree() -> str:
    """ASCII tree of ``event_app``, opal-listen style: params are usage labels appended to
    each command's line (``name``/``[name]``, ``--flag``/``[--flag]``), ``|--`` guides."""
    from typer.main import get_command

    root = get_command(event_app)
    return "\n".join([_plain_label("opal event", root), *_ascii_lines(root, "")])


def _render_rich_command_tree():
    """Color-coded ``rich`` tree of ``event_app``: bold green root, blue guides, bold cyan
    command groups, cyan leaf commands, yellow required params, dim optional params."""
    from rich.tree import Tree
    from typer.main import get_command

    root = get_command(event_app)
    tree = Tree(_styled_label("opal event", root, "bold green"), guide_style="blue")
    _add_commands(tree, root)
    return tree


if __name__ == "__main__":
    app()
