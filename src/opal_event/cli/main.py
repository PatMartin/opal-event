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
settings_app = typer.Typer(help="Portable, live-adjustable service settings.", no_args_is_help=True)
backup_app = typer.Typer(help="Export, validate, and import portable configuration.", no_args_is_help=True)
for _sub, _name in (
    (rules_app, "rules"),
    (actions_app, "actions"),
    (executions_app, "executions"),
    (settings_app, "settings"),
    (backup_app, "backup"),
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
) -> None:
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


def _call(ctx: typer.Context, op: str, **kwargs: Any) -> httpx.Response:
    state = _state(ctx)
    if state.context_headers:
        kwargs["headers"] = {**state.context_headers, **(kwargs.get("headers") or {})}
    try:
        return state.client.call(op, **kwargs)
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


def _current_revision(ctx: typer.Context) -> str:
    resp = _call(ctx, "get_api_v1_settings")
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
    resp = _call(ctx, "post_api_v1_events", json=body)
    _emit(ctx, resp.json())


@event_app.command("list")
def list_events(
    ctx: typer.Context,
    cursor: str = typer.Option(None, "--cursor"),
    limit: int = typer.Option(50, "--limit"),
    topic: str = typer.Option(None, "--topic"),
    source: str = typer.Option(None, "--source"),
    event_from: str = typer.Option(None, "--from"),
    event_to: str = typer.Option(None, "--to"),
) -> None:
    """List stored events."""
    raw_params = dict(cursor=cursor, limit=limit, topic=topic, source=source, **{"from": event_from, "to": event_to})
    params = {k: v for k, v in raw_params.items() if v is not None}
    resp = _call(ctx, "get_api_v1_events", params=params)
    data = resp.json()
    _emit(ctx, data["items"], columns=["id", "topic", "source", "timestamp"])


@event_app.command("get")
def get_event(ctx: typer.Context, event_id: str = typer.Argument(...)) -> None:
    """Get a stored event by ID."""
    _emit(ctx, _call(ctx, "get_api_v1_events_id", path_params={"event_id": event_id}).json())


@event_app.command("delete")
def delete_event_cmd(ctx: typer.Context, event_id: str = typer.Argument(..., help="Event ID to delete")) -> None:
    """Delete a stored event by ID."""
    _call(ctx, "delete_api_v1_events_id", path_params={"event_id": event_id})
    if _state(ctx).json_output:
        typer.echo(json.dumps({"deleted": event_id}))
    else:
        typer.echo(f"Deleted event '{event_id}'.")


@event_app.command("clear")
def clear_events_cmd(ctx: typer.Context) -> None:
    """Clear all events from all topics."""
    _call(ctx, "delete_api_v1_events")
    if _state(ctx).json_output:
        typer.echo(json.dumps({"cleared": True}))
    else:
        typer.echo("Cleared all events.")


@event_app.command("topics")
def topics(ctx: typer.Context) -> None:
    """List observed topics (incomplete when persistence is off)."""
    _emit(ctx, _call(ctx, "get_api_v1_topics").json()["items"])


@event_app.command("health")
def health(ctx: typer.Context) -> None:
    """Get readiness and health."""
    _emit(ctx, _call(ctx, "get_api_v1_health").json())


@event_app.command("health-live")
def health_live(ctx: typer.Context) -> None:
    """Check liveness (no auth required)."""
    _emit(ctx, _call(ctx, "get_api_v1_health_live").json())


@event_app.command("health-ready")
def health_ready(ctx: typer.Context) -> None:
    """Check readiness (no auth required)."""
    _emit(ctx, _call(ctx, "get_api_v1_health_ready").json())


@event_app.command("stats")
def stats(ctx: typer.Context) -> None:
    """Get event and execution counters."""
    _emit(ctx, _call(ctx, "get_api_v1_stats").json())


# -- `opal event rules` --------------------------------------------------------------------------


@rules_app.command("list")
def rules_list(ctx: typer.Context) -> None:
    """List rules (complete bounded catalog; no pagination)."""
    data = _call(ctx, "get_api_v1_rules").json()
    _emit(ctx, data["items"], columns=["id", "enabled", "topic", "actions"])


@rules_app.command("create")
def rules_create(
    ctx: typer.Context,
    json_str: str = typer.Option(None, "--json", help="Full Rule JSON body"),
    file: str = typer.Option(None, "--file", help="Path to a file containing the Rule JSON body"),
) -> None:
    """Create a rule."""
    body = _read_payload(json_str, file)
    _emit(ctx, _call(ctx, "post_api_v1_rules", json=body).json())


@rules_app.command("get")
def rules_get(ctx: typer.Context, rule_id: str = typer.Argument(...)) -> None:
    """Get a rule."""
    _emit(ctx, _call(ctx, "get_api_v1_rules_id", path_params={"rule_id": rule_id}).json())


@rules_app.command("delete")
def rules_delete(
    ctx: typer.Context,
    rule_id: str = typer.Argument(...),
    if_match: str = typer.Option(None, "--if-match", help="Strong ETag precondition (default: auto-fetched)"),
) -> None:
    """Delete a rule. Requires a matching If-Match (428 if omitted server-side; auto-fetched here)."""
    revision = if_match or _resource_etag(ctx, "get_api_v1_rules_id", {"rule_id": rule_id})
    _call(ctx, "delete_api_v1_rules_id", path_params={"rule_id": rule_id}, headers={"If-Match": revision})
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
    revision = if_match or _resource_etag(ctx, "get_api_v1_rules_id", {"rule_id": rule_id})
    _emit(
        ctx,
        _call(
            ctx, "patch_api_v1_rules_rule_id", path_params={"rule_id": rule_id}, json=body, headers={"If-Match": revision}
        ).json(),
    )


@rules_app.command("test")
def rules_test(
    ctx: typer.Context, rule_id: str = typer.Argument(...), event_json: str = typer.Option(..., "--event-json")
) -> None:
    """Evaluate a rule against a sample event without executing any action."""
    body = {"event": _event_input(event_json)}
    _emit(ctx, _call(ctx, "post_api_v1_rules_id_test", path_params={"rule_id": rule_id}, json=body).json())


# -- `opal event actions` ------------------------------------------------------------------------


@actions_app.command("list")
def actions_list(ctx: typer.Context) -> None:
    """List actions (complete bounded catalog; no pagination)."""
    data = _call(ctx, "get_api_v1_actions").json()
    _emit(ctx, data["items"], columns=["id", "type", "enabled"])


@actions_app.command("create")
def actions_create(
    ctx: typer.Context,
    json_str: str = typer.Option(None, "--json", help="Full Action JSON body (shell/http/python)"),
    file: str = typer.Option(None, "--file", help="Path to a file containing the Action JSON body"),
) -> None:
    """Create an action."""
    body = _read_payload(json_str, file)
    _emit(ctx, _call(ctx, "post_api_v1_actions", json=body).json())


@actions_app.command("get")
def actions_get(ctx: typer.Context, action_id: str = typer.Argument(...)) -> None:
    """Get an action."""
    _emit(ctx, _call(ctx, "get_api_v1_actions_id", path_params={"action_id": action_id}).json())


@actions_app.command("delete")
def actions_delete(
    ctx: typer.Context,
    action_id: str = typer.Argument(...),
    if_match: str = typer.Option(None, "--if-match", help="Strong ETag precondition (default: auto-fetched)"),
) -> None:
    """Delete an unused action (409 if referenced by a rule; 428/412 if If-Match is missing/stale)."""
    revision = if_match or _resource_etag(ctx, "get_api_v1_actions_id", {"action_id": action_id})
    _call(ctx, "delete_api_v1_actions_id", path_params={"action_id": action_id}, headers={"If-Match": revision})
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
    revision = if_match or _resource_etag(ctx, "get_api_v1_actions_id", {"action_id": action_id})
    _emit(
        ctx,
        _call(
            ctx,
            "patch_api_v1_actions_action_id",
            path_params={"action_id": action_id},
            json=body,
            headers={"If-Match": revision},
        ).json(),
    )


@actions_app.command("execute")
def actions_execute(
    ctx: typer.Context, action_id: str = typer.Argument(...), event_json: str = typer.Option(..., "--event-json")
) -> None:
    """Manually execute a registered, enabled action."""
    body = {"event": _event_input(event_json)}
    _emit(ctx, _call(ctx, "post_api_v1_actions_id_execute", path_params={"action_id": action_id}, json=body).json())


# -- `opal event executions` ---------------------------------------------------------------------


@executions_app.command("list")
def executions_list(
    ctx: typer.Context,
    cursor: str = typer.Option(None, "--cursor"),
    limit: int = typer.Option(100, "--limit"),
    status: str = typer.Option(None, "--status"),
    rule_id: str = typer.Option(None, "--rule-id"),
    action_id: str = typer.Option(None, "--action-id"),
    event_id: str = typer.Option(None, "--event-id"),
) -> None:
    """List executions."""
    raw_params = dict(cursor=cursor, limit=limit, status=status, rule_id=rule_id, action_id=action_id, event_id=event_id)
    params = {k: v for k, v in raw_params.items() if v is not None}
    data = _call(ctx, "get_api_v1_executions", params=params).json()
    _emit(ctx, data["items"], columns=["id", "action_id", "status", "trigger", "created_at"])


@executions_app.command("get")
def executions_get(ctx: typer.Context, execution_id: str = typer.Argument(...)) -> None:
    """Get an execution result."""
    _emit(ctx, _call(ctx, "get_api_v1_executions_id", path_params={"execution_id": execution_id}).json())


# -- `opal event settings` -----------------------------------------------------------------------


@settings_app.command("get")
def settings_get(ctx: typer.Context) -> None:
    """Get portable settings."""
    _emit(ctx, _call(ctx, "get_api_v1_settings").json())


@settings_app.command("update")
def settings_update(
    ctx: typer.Context,
    json_str: str = typer.Option(None, "--json", help="Partial PortableSettingsPatch JSON body"),
    file: str = typer.Option(None, "--file", help="Path to a file containing the PortableSettingsPatch JSON body"),
    if_match: str = typer.Option(None, "--if-match", help="Revision precondition (default: auto-fetched)"),
) -> None:
    """Update portable settings (execution/persistence subset, max_event_bytes)."""
    body = _read_payload(json_str, file)
    revision = if_match or _current_revision(ctx)
    _emit(ctx, _call(ctx, "patch_api_v1_settings", json=body, headers={"If-Match": revision}).json())


# -- `opal event backup` -------------------------------------------------------------------------


@backup_app.command("export")
def backup_export(
    ctx: typer.Context, output: str = typer.Option(None, "--output", help="Write to a file instead of stdout")
) -> None:
    """Export portable configuration (settings, actions, rules)."""
    data = _call(ctx, "get_api_v1_backup_export").json()
    if output:
        Path(output).write_text(json.dumps(data, indent=2), encoding="utf-8")
        typer.echo(f"exported configuration to {output}", err=True)
    else:
        typer.echo(json.dumps(data, indent=2))


@backup_app.command("validate")
def backup_validate(
    ctx: typer.Context,
    json_str: str = typer.Option(None, "--json", help="Full ConfigBackup JSON body"),
    file: str = typer.Option(None, "--file", help="Path to a file containing the ConfigBackup JSON body"),
) -> None:
    """Validate a portable backup without side effects."""
    body = _read_payload(json_str, file)
    data = _call(ctx, "post_api_v1_backup_validate", json=body).json()
    _emit(ctx, data)
    if not data["valid"]:
        raise typer.Exit(1)


@backup_app.command("import")
def backup_import(
    ctx: typer.Context,
    json_str: str = typer.Option(None, "--json", help="Full BackupImportRequest JSON body ({'backup': {...}})"),
    file: str = typer.Option(None, "--file", help="Path to a file containing the BackupImportRequest JSON body"),
    if_match: str = typer.Option(None, "--if-match", help="Revision precondition (default: auto-fetched)"),
) -> None:
    """Import a portable backup atomically (replaces portable rules/actions/settings)."""
    body = _read_payload(json_str, file)
    revision = if_match or _current_revision(ctx)
    _emit(ctx, _call(ctx, "post_api_v1_backup_import", json=body, headers={"If-Match": revision}).json())


# -- `opal` root -----------------------------------------------------------------------------

app = typer.Typer(help="OPAL command line.", no_args_is_help=True, pretty_exceptions_enable=False)
app.add_typer(event_app, name="event")

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


if __name__ == "__main__":
    app()
