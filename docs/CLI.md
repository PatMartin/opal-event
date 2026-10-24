# `opal event` CLI Reference

The `opal event` command (and its standalone alias `opal-event`) is the terminal interface to the
OPAL Event service (`opal-event-svc`). It provides command-line control over event publication,
event and topic inspection, rules and actions catalogs, execution history, live-adjustable portable
settings, configuration backups, service health, and operational diagnostics.

The CLI is a thin REST client. **Every command calls the Opal Event REST API** through the operations
declared in the authoritative OpenAPI 3.1 specification (`opal_event.1.2.0.yaml`). It never accesses
the SQLite database or ZeroMQ sockets directly. The server must therefore be running, either locally or
on a remote host.

---

## Table of contents

1. [Installation and invocation](#1-installation-and-invocation)
2. [Connecting to the server](#2-connecting-to-the-server)
3. [Conventions](#3-conventions)
4. [Command tree](#4-command-tree)
5. [`publish`: event publication](#5-publish-event-publication)
6. [`list`, `get`, `delete`, & `clear`: event history](#6-list-get-delete--clear-event-history)
7. [`topics`: topic catalog](#7-topics-topic-catalog)
8. [`rules`: rule management](#8-rules-rule-management)
9. [`actions`: action management](#9-actions-action-management)
10. [`executions`: execution history](#10-executions-execution-history)
11. [`settings`: portable service settings](#11-settings-portable-service-settings)
12. [`backup`: configuration export, validation, and import](#12-backup-configuration-export-validation-and-import)
13. [Health, readiness, and metrics](#13-health-readiness-and-metrics)
14. [Walkthroughs](#14-walkthroughs)
15. [Scripting and automation](#15-scripting-and-automation)
16. [Troubleshooting](#16-troubleshooting)
17. [Appendix: CLI ↔ REST mapping](#17-appendix-cli--rest-mapping)

---

## 1. Installation and invocation

Installing `opal-event` installs both the unified `opal` command (with the `event` subcommand group)
and the standalone `opal-event` shortcut:

```bash
cd /gh/opal/opal-event
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

You can invoke the CLI in three equivalent ways:

```bash
# 1. Through the unified OPAL entrypoint
opal event --help

# 2. Through the standalone opal-event console script
opal-event --help

# 3. As a Python module
python -m opal_event.cli.main event --help
```

Start the service before running CLI commands:

```bash
opal-event-svc --config config/opal_event.yaml
```

### Getting help

`--help` is available at every level of the command tree:

```bash
opal --help                       # top level (shows 'event' and sibling suites)
opal event --help                 # global options and event command groups
opal event rules --help           # commands in the rules group
opal event rules create --help    # options for creating a rule
```

Running a subcommand group without a command also displays its help:

```bash
opal event rules                  # same as: opal event rules --help
```

### Shell completion

The CLI is built with Typer, which supports native shell completion:

```bash
opal-event --install-completion bash    # or zsh / fish; restart shell afterwards
opal-event --show-completion bash       # print completion script to stdout
```

---

## 2. Connecting to the server

### Global options

Global options belong to the `event` group (or the root of `opal-event`). When invoking `opal event`,
place global options **after `event` and before the subcommand or group**:

```
opal event [--api-url URL] [--token TOKEN] [--json] [--timeout SECONDS]
           [--opal-session-id ID] [--opal-transaction-id ID] [--opal-causation-id ID]
           <command | group> [ARGS] [OPTIONS]
```

Or with `opal-event`:

```
opal-event [--api-url URL] [--token TOKEN] [--json] [--timeout SECONDS]
           [--opal-session-id ID] [--opal-transaction-id ID] [--opal-causation-id ID]
           <command | group> [ARGS] [OPTIONS]
```

| Option | Default | Meaning |
|---|---|---|
| `--api-url URL` | `$OPAL_EVENT_API_URL`, otherwise `http://127.0.0.1:8080` | Base URL of the Opal Event REST API |
| `--token TOKEN` | `$OPAL_EVENT_API_TOKEN`, otherwise none | Bearer token sent in `Authorization: ****** |
| `--json` | off | Output raw API JSON instead of formatted tables |
| `--timeout FLOAT` | `30.0` | HTTP request timeout in seconds |
| `--opal-session-id ID` | `$OPAL_SESSION_ID`, otherwise none | Correlation header `X-Opal-Session-Id` sent with every request |
| `--opal-transaction-id ID` | `$OPAL_TRANSACTION_ID`, otherwise none | Correlation header `X-Opal-Transaction-Id` sent with every request |
| `--opal-causation-id ID` | `$OPAL_CAUSATION_ID`, otherwise none | Correlation header `X-Opal-Causation-Id` sent with every request |

```bash
# Correct: global options placed right after 'event'
opal event --json rules list
opal event --api-url http://192.168.1.50:8080 --timeout 10 health

# Wrong: global options placed after the command are rejected as unknown options
opal event rules list --json
```

### Environment variables

Export environment variables in your shell so you do not need to specify connection flags on every call:

| Environment variable | Equivalent CLI option | Purpose |
|---|---|---|
| `OPAL_EVENT_API_URL` | `--api-url` | REST API base URL (default: `http://127.0.0.1:8080`) |
| `OPAL_EVENT_API_TOKEN` | `--token` | REST bearer token when `security.rest_auth.enabled: true` |
| `OPAL_SESSION_ID` | `--opal-session-id` | Default session correlation identifier |
| `OPAL_TRANSACTION_ID` | `--opal-transaction-id` | Default transaction correlation identifier |
| `OPAL_CAUSATION_ID` | `--opal-causation-id` | Default causation correlation identifier |

```bash
export OPAL_EVENT_API_URL=http://192.168.1.50:8080
export OPAL_EVENT_API_TOKEN=secret-token-123
opal event health                 # connects to 192.168.1.50 with bearer token

# Override for a single command:
OPAL_EVENT_API_URL=http://pi5.local:8080 opal event stats

# Explicit CLI flags override environment variables:
opal event --api-url http://127.0.0.1:8080 health
```

### Checking the connection

Use the health probes to verify connectivity:

```bash
opal event health-live            # verifies process and database reachability
opal event health-ready           # verifies broker and configuration readiness
opal event health                 # summary status (status: ok | degraded)
```

If the service cannot be reached, the CLI prints an error and exits with code `2`:

```
Error: cannot reach Opal Event API: [Errno 111] Connection refused
```

---

## 3. Conventions

### Input payloads (`--json`, `--file`, stdin)

Commands that create or modify rules, actions, settings, or backups accept structured JSON payloads.
These commands support three input methods, checked in this priority order:

1. `--json '<raw JSON text>'`: Inline JSON string.
2. `--file <path>`: Path to a file containing JSON text.
3. Standard input (stdin): Pipe JSON into the command when stdin is not a TTY.

```bash
# 1. Inline JSON
opal event rules create --json '{"id":"r1","topic":"vision.*","conditions":[],"actions":["act1"]}'

# 2. File payload
opal event rules create --file examples/sample_rule.json

# 3. Piped stdin
cat examples/sample_rule.json | opal event rules create
jq '.enabled = false' rule.json | opal event rules update r1
```

If no payload is supplied and stdin is a TTY, the CLI fails immediately with:
`Error: Invalid value: provide --json, --file, or pipe JSON on stdin`.

### Strong ETags and optimistic concurrency

Per the OPAL v1.2 standard, mutating operations (`PATCH /api/v1/rules/{id}`, `DELETE /api/v1/rules/{id}`,
`PATCH /api/v1/actions/{id}`, `DELETE /api/v1/actions/{id}`, `PATCH /api/v1/settings/`, and
`POST /api/v1/backup/import`) require an `If-Match` precondition header containing the current strong
`ETag` revision.

The CLI automates this for you:
- When `--if-match` is omitted on `rules update`, `rules delete`, `actions update`, `actions delete`,
  `settings update`, or `backup import`, **the CLI automatically fetches the resource's current ETag**
  via a `GET` request immediately before sending the mutation.
- You can provide an explicit `--if-match` if you want strict optimistic concurrency control:

```bash
# Auto-fetches current ETag:
opal event rules update front_door_alert --json '{"enabled": false}'

# Explicit ETag precondition:
opal event rules update front_door_alert --json '{"enabled": false}' --if-match '"1-a1b2c3d4"'
```

If the precondition fails on the server, the CLI outputs an error:
- `HTTP 412`: The resource was modified concurrently since the ETag was retrieved.
- `HTTP 428`: Missing precondition header (if an explicit empty value was forced).

### Correlation tracking (OPAL Context Headers)

The OPAL v1.2 specification defines four correlation headers accepted on every request and echoed on
every response:

| Header | Meaning | CLI Option / Source |
|---|---|---|
| `X-Opal-Session-Id` | High-level user or conversational session ID | `--opal-session-id` / `$OPAL_SESSION_ID` |
| `X-Opal-Transaction-Id` | Multi-hop transaction or workflow ID | `--opal-transaction-id` / `$OPAL_TRANSACTION_ID` (generated by server if omitted) |
| `X-Opal-Causation-Id` | Identifier of the message or event that caused this request | `--opal-causation-id` / `$OPAL_CAUSATION_ID` |
| `X-Opal-Request-Id` | Unique ID for the immediate HTTP request hop | Generated fresh by the server for each request |

When passed to the CLI, these headers are included in outgoing requests, allowing end-to-end trace
correlation across the OPAL ecosystem (e.g. tracing an intent from `opal-listen` through `opal-event`
to an external webhook).

### Output formats

By default, collection queries print clean human-readable tables:

| Command | Default table columns |
|---|---|
| `list` | `ID`, `TOPIC`, `SOURCE`, `TIMESTAMP` |
| `rules list` | `ID`, `ENABLED`, `TOPIC`, `ACTIONS` |
| `actions list` | `ID`, `TYPE`, `ENABLED` |
| `executions list` | `ID`, `ACTION_ID`, `STATUS`, `TRIGGER`, `CREATED_AT` |

In table view:
- Empty/null values display as `-`.
- Boolean values display as `yes` or `no`.
- Lists and dictionaries display as compact JSON.
- An empty result set outputs `(none)`.

Single-resource queries (`get`, `health`, `stats`, `publish`, `rules test`, `actions execute`, etc.)
display formatted, indented JSON.

Passing `--json` forces all commands (including table-formatted lists) to output raw, pretty-printed
JSON suitable for processing with `jq`.

```bash
# Table output
opal event rules list

# Raw JSON output
opal event --json rules list
```

### Exit codes

| Exit code | Meaning |
|---|---|
| `0` | Success |
| `1` | API error (`4xx` or `5xx`), validation failure, or `backup validate` failed |
| `2` | CLI usage error (invalid arguments/flags, bad JSON syntax) or API unreachable |

---

## 4. Command tree

```text
opal
└── event (alias: opal-event)
    │   [Global Options & Flags]
    │   ├── --api-url <url>                     REST API base URL [env: OPAL_EVENT_API_URL]
    │   ├── --token <token>                     Bearer token [env: OPAL_EVENT_API_TOKEN]
    │   ├── --json                              Output raw JSON (flag)
    │   ├── --timeout <seconds>                 HTTP timeout [default: 30.0]
    │   ├── --opal-session-id <id>              X-Opal-Session-Id header [env: OPAL_SESSION_ID]
    │   ├── --opal-transaction-id <id>          X-Opal-Transaction-Id header [env: OPAL_TRANSACTION_ID]
    │   └── --opal-causation-id <id>            X-Opal-Causation-Id header [env: OPAL_CAUSATION_ID]
    │
    ├── publish <topic>                         Publish event to the bus
    │   ├── --data-json <json>                  (required) Event data JSON
    │   ├── [--id <uuid>]                       Explicit event UUID
    │   ├── [--source <src>]                    Event source [default: opal-event-rest]
    │   └── [--timestamp <rfc3339>]             Timestamp [default: now]
    │
    ├── list                                    List stored events
    │   ├── [--cursor <token>]                  Pagination cursor
    │   ├── [--limit <int>]                     Max items [default: 50]
    │   ├── [--topic <topic>]                   Topic exact filter
    │   ├── [--source <src>]                    Source filter
    │   ├── [--from <rfc3339>]                  Created >= timestamp
    │   └── [--to <rfc3339>]                    Created <= timestamp
    │
    ├── get <event_id>                          Get stored event by ID
    ├── delete <event_id>                       Delete stored event by ID
    ├── clear                                   Clear all stored events from all topics
    ├── topics                                  List observed topics
    ├── health                                  Readiness and health summary
    ├── health-live                             Liveness probe (no auth)
    ├── health-ready                            Readiness probe (no auth)
    ├── stats                                   Runtime metrics and counters
    │
    ├── rules
    │   ├── list                                List rules (bounded catalog)
    │   ├── get <rule_id>                       Get rule by ID
    │   ├── create                              Create new rule
    │   │   ├── [--json <json>]                 Full Rule JSON body
    │   │   └── [--file <path>]                 File path containing JSON
    │   ├── update <rule_id>                    Partially update rule
    │   │   ├── [--json <json>]                 Partial RulePatch JSON body
    │   │   ├── [--file <path>]                 File path containing JSON
    │   │   └── [--if-match <etag>]             Precondition ETag (auto-fetched if omitted)
    │   ├── delete <rule_id>                    Delete rule by ID
    │   │   └── [--if-match <etag>]             Precondition ETag (auto-fetched if omitted)
    │   └── test <rule_id>                      Dry-run evaluate rule against event
    │       └── --event-json <json>             (required) Sample event JSON
    │
    ├── actions
    │   ├── list                                List actions (bounded catalog)
    │   ├── get <action_id>                     Get action by ID
    │   ├── create                              Create new action
    │   │   ├── [--json <json>]                 Full Action JSON body
    │   │   └── [--file <path>]                 File path containing JSON
    │   ├── update <action_id>                  Partially update action
    │   │   ├── [--json <json>]                 Partial ActionPatch JSON body
    │   │   ├── [--file <path>]                 File path containing JSON
    │   │   └── [--if-match <etag>]             Precondition ETag (auto-fetched if omitted)
    │   ├── delete <action_id>                  Delete action by ID
    │   │   └── [--if-match <etag>]             Precondition ETag (auto-fetched if omitted)
    │   └── execute <action_id>                 Manually execute action
    │       └── --event-json <json>             (required) Sample event JSON
    │
    ├── executions
    │   ├── list                                List action executions
    │   │   ├── [--cursor <token>]              Pagination cursor
    │   │   ├── [--limit <int>]                 Max items [default: 100]
    │   │   ├── [--status <status>]             Filter by execution status
    │   │   ├── [--rule-id <rule_id>]           Filter by rule ID
    │   │   ├── [--action-id <action_id>]       Filter by action ID
    │   │   └── [--event-id <event_id>]         Filter by triggering event ID
    │   └── get <execution_id>                  Get execution details by ID
    │
    ├── settings
    │   ├── get                                 Get portable service settings
    │   └── update                              Update portable settings
    │       ├── [--json <json>]                 Partial settings JSON body
    │       ├── [--file <path>]                 File path containing JSON
    │       └── [--if-match <etag>]             Revision ETag (auto-fetched if omitted)
    │
    └── backup
        ├── export                              Export configuration bundle
        │   └── [--output <path>]               Write to file instead of stdout
        ├── validate                            Validate configuration bundle
        │   ├── [--json <json>]                 Full ConfigBackup JSON body
        │   └── [--file <path>]                 File path containing JSON
        └── import                              Import configuration bundle atomically
            ├── [--json <json>]                 BackupImportRequest JSON body
            ├── [--file <path>]                 File path containing JSON
            └── [--if-match <etag>]             Revision ETag (auto-fetched if omitted)
```

---

## 5. `publish`: event publication

`opal event publish` sends an event to the REST ingress endpoint (`POST /api/v1/events/`). The server
normalizes any missing metadata (generating a UUID `id`, defaulting `source` to `opal-event-rest`,
and setting `timestamp` to current UTC time), publishes the event onto the internal ZeroMQ event bus
via a service-owned PUB socket, and responds with `202 Accepted`.

> **Note:** A `202 Accepted` response indicates the event was successfully validated and handed off to the
> publishing subsystem. It does **not** guarantee downstream subscriber receipt or action execution completion.

```
opal event publish [OPTIONS] TOPIC
```

| Argument / Option | Default | Description |
|---|---|---|
| `TOPIC` | *(required)* | Dotted event topic (e.g. `vision.object.detected`) matching `^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)*$` |
| `--data-json TEXT` | *(required)* | JSON-encoded string for the event's `data` payload |
| `--id TEXT` | Server-generated UUID | Explicit event UUID |
| `--source TEXT` | `opal-event-rest` | Logical source identifier (1–128 characters) |
| `--timestamp TEXT` | Current UTC RFC3339 | Explicit ISO 8601 / RFC 3339 timestamp |

### Examples

```bash
# Basic publish
opal event publish vision.object.detected --data-json '{"object":"person","camera":"front_door"}'

# Output (202 Accepted receipt):
# {
#   "id": "e4f3a120-8b1e-4c7b-99f2-2a781b0f56d9",
#   "status": "accepted",
#   "published_at": "2026-10-12T15:40:02.123456Z"
# }

# Publish with custom ID and explicit source
opal event publish sensor.temperature.reading \
  --data-json '{"room":"living_room","celsius":21.5}' \
  --source "sensor-node-01" \
  --id "11111111-2222-3333-4444-555555555555"

# Publish with correlation IDs
opal event --opal-session-id "user-sess-42" \
           --opal-transaction-id "txn-99" \
           publish user.action.clicked --data-json '{"button":"panic"}'
```

---

## 6. `list`, `get`, `delete`, & `clear`: event history

When `persistence.event_mode` is set to `all` or `matched`, observed events are stored in the SQLite
database and queryable via the CLI.

### `list`: list stored events

```
opal event list [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--cursor TEXT` | `None` | Pagination cursor from a previous page's `next_cursor` |
| `--limit INT` | `50` | Maximum items to return (1–100) |
| `--topic TEXT` | `None` | Filter by exact topic |
| `--source TEXT` | `None` | Filter by source identifier |
| `--from TEXT` | `None` | Filter events created at or after this RFC 3339 timestamp |
| `--to TEXT` | `None` | Filter events created at or before this RFC 3339 timestamp |

```bash
# List events in table format
opal event list

# Filter by topic and limit
opal event list --topic vision.object.detected --limit 10

# Filter by time range
opal event list --from "2026-10-12T00:00:00Z" --to "2026-10-12T12:00:00Z"

# Get raw JSON with cursor pagination
opal event --json list --limit 2
```

### `get`: get an event by ID

```
opal event get EVENT_ID
```

```bash
opal event get e4f3a120-8b1e-4c7b-99f2-2a781b0f56d9
```

Output:
```json
{
  "id": "e4f3a120-8b1e-4c7b-99f2-2a781b0f56d9",
  "topic": "vision.object.detected",
  "source": "opal-event-rest",
  "timestamp": "2026-10-12T15:40:02.123456Z",
  "data": {
    "camera": "front_door",
    "object": "person"
  }
}
```

### `delete`: delete an individual stored event

Deletes a single stored event by its unique ID. Returns an error if the event does not exist.

```
opal event delete <EVENT_ID>
```

```bash
opal event delete 7b8d9c2e-4f1a-4e3b-9a8c-1d2e3f4a5b6c
# Output:
# Deleted event '7b8d9c2e-4f1a-4e3b-9a8c-1d2e3f4a5b6c'.
```

With `--json`:
```bash
opal event delete 7b8d9c2e-4f1a-4e3b-9a8c-1d2e3f4a5b6c --json
# Output:
# {
#   "deleted": "7b8d9c2e-4f1a-4e3b-9a8c-1d2e3f4a5b6c"
# }
```

### `clear`: clear all stored events

Deletes all stored events from all topics in the database.

```
opal event clear
```

```bash
opal event clear
# Output:
# Cleared all events.
```

---

## 7. `topics`: topic catalog

`opal event topics` lists all topics currently observed in stored events or defined across rules.

```bash
opal event topics
```

Output:
```json
[
  "intent.recognized",
  "sensor.temperature.reading",
  "vision.object.detected"
]
```

> **Note:** If `persistence.event_mode` is set to `none`, the topic list will reflect topics defined
> in configured rules, as unpersisted ephemeral events are not recorded in the database.

---

## 8. `rules`: rule management

Rules match incoming events by topic pattern and condition expressions, dispatching one or more actions
when all conditions evaluate to true.

### `rules list`

Lists all rules in the bounded configuration catalog (no pagination):

```bash
opal event rules list
```

Example table output:
```
ID                ENABLED  TOPIC     ACTIONS
front_door_alert  yes      vision.*  ["announce_person", "send_notification"]
temp_alert        no       sensor.*  ["log_alert"]
```

### `rules get`

Retrieves a single rule by ID:

```bash
opal event rules get front_door_alert
```

Output:
```json
{
  "id": "front_door_alert",
  "enabled": true,
  "topic": "vision.*",
  "conditions": [
    {
      "path": "data.object",
      "operator": "eq",
      "value": "person"
    }
  ],
  "actions": [
    "announce_person",
    "send_notification"
  ]
}
```

### `rules create`

Registers a new rule. The rule payload must be valid JSON:

```
opal event rules create [--json TEXT | --file PATH]
```

```bash
# Using inline JSON:
opal event rules create --json '{
  "id": "living_room_temp",
  "enabled": true,
  "topic": "sensor.temperature.reading",
  "conditions": [
    {"path": "data.room", "operator": "eq", "value": "living_room"},
    {"path": "data.celsius", "operator": "gt", "value": 25.0}
  ],
  "actions": ["send_notification"]
}'

# From a file:
opal event rules create --file examples/sample_rule.json
```

#### Rule schema specification

- `id`: Alphanumeric string matching `^[a-zA-Z][a-zA-Z0-9_-]*$`.
- `enabled`: Boolean (default: `true`).
- `topic`: Topic pattern matching either:
  - Exact topic: e.g. `vision.object.detected`
  - Prefix wildcard: e.g. `vision.*` (matches `vision.object`, `vision.object.detected`; does **not** match bare `vision` or `visionary`)
- `conditions`: List of condition objects evaluated with logical **AND**:
  - `path`: Dotted JSON path into the event (e.g. `data.object`, `data.sensors.0.temp`).
  - `operator`: One of `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `not_in`, `exists`, `contains`.
  - `value`: Target value compared against the resolved path.
- `actions`: List of registered action IDs to execute upon matching.

#### Condition operators

| Operator | Evaluates to true when | Notes |
|---|---|---|
| `eq` | `path_value == value` | Strict type matching |
| `ne` | `path_value != value` | Strict type matching |
| `gt` | `path_value > value` | Numbers only (integers/floats; booleans excluded) |
| `gte` | `path_value >= value` | Numbers only |
| `lt` | `path_value < value` | Numbers only |
| `lte` | `path_value <= value` | Numbers only |
| `in` | `path_value in value` | `value` must be a JSON array |
| `not_in` | `path_value not in value` | `value` must be a JSON array |
| `exists` | `found == bool(value)` | Checks path presence; `value: false` tests absence |
| `contains` | `value in path_value` | Substring match if string; element match if list |

### `rules update`

Partially updates an existing rule. Supports `--if-match` (auto-fetched if omitted):

```
opal event rules update RULE_ID [--json TEXT | --file PATH] [--if-match TEXT]
```

```bash
# Disable a rule
opal event rules update front_door_alert --json '{"enabled": false}'

# Update conditions and actions
opal event rules update front_door_alert --json '{
  "conditions": [{"path": "data.object", "operator": "in", "value": ["person", "vehicle"]}],
  "actions": ["announce_person"]
}'
```

### `rules delete`

Deletes a rule by ID. Supports `--if-match` (auto-fetched if omitted):

```
opal event rules delete RULE_ID [--if-match TEXT]
```

```bash
opal event rules delete living_room_temp
# Output: deleted rule living_room_temp
```

### `rules test`

Evaluates a rule against a sample event **without executing any actions** (dry-run mode). This is
invaluable for debugging condition logic before enabling rules in production.

```
opal event rules test RULE_ID --event-json TEXT
```

```bash
opal event rules test front_door_alert --event-json '{
  "topic": "vision.object.detected",
  "data": {
    "object": "person",
    "camera": "front_door"
  }
}'
```

Output:
```json
{
  "matched": true,
  "action_ids": [
    "announce_person",
    "send_notification"
  ],
  "checks": [
    {
      "kind": "topic",
      "pattern": "vision.*",
      "matched": true
    },
    {
      "kind": "condition",
      "path": "data.object",
      "operator": "eq",
      "matched": true
    }
  ]
}
```

If a condition fails, `matched` will be `false` and the failing check will report `"matched": false`.

---

## 9. `actions`: action management

Actions define executable tasks triggered by rules or manual invocations. OPAL Event supports three
polymorphic action types: **shell**, **http**, and **python**.

### `actions list`

Lists all actions in the catalog:

```bash
opal event actions list
```

Example table output:
```
ID                TYPE    ENABLED
announce_person   shell   yes
send_notification http    yes
process_detection python  yes
```

### `actions get`

Retrieves an action's full configuration:

```bash
opal event actions get announce_person
```

Output:
```json
{
  "id": "announce_person",
  "type": "shell",
  "enabled": true,
  "mode": "exec",
  "executable": "/bin/echo",
  "args": ["person detected at {{data.camera}}"],
  "stdin": "none",
  "timeout_seconds": 10
}
```

### `actions create`

Creates a new action from inline JSON or a file:

```
opal event actions create [--json TEXT | --file PATH]
```

#### Action Types & Schemas

#### 1. Shell Actions (`type: "shell"`)

Shell actions run local executables or scripts in an isolated process group. Commands are executed
directly via `argv` lists (`shell=False`), preventing shell injection attacks.

```json
{
  "id": "run_backup_script",
  "type": "shell",
  "enabled": true,
  "mode": "bash",
  "script_path": "/opt/scripts/backup.sh",
  "args": ["--target", "{{data.volume}}"],
  "stdin": "event_json",
  "timeout_seconds": 30
}
```

- `mode`:
  - `"exec"`: Direct binary execution (`executable` required).
  - `"sh"`, `"bash"`, `"cmd"`, `"powershell"`: Interpreter script execution (`script_path` required).
- `executable`: Absolute path to binary (for `exec` mode).
- `script_path`: Absolute path to script (for interpreter modes).
- `args`: List of argument strings. Supports template expressions `{{data.path}}` and `{{event}}`.
- `stdin`:
  - `"event_json"`: Feeds full normalized event JSON to standard input.
  - `"none"`: Detaches stdin (`DEVNULL`).
- `timeout_seconds`: Execution timeout (1–3600 seconds; defaults to service `default_timeout_seconds`).

#### 2. HTTP Actions (`type: "http"`)

HTTP actions send outbound HTTP requests to allowlisted endpoints. Every destination URL is verified
against `security.http_action_allowlist` (matching host, port, and scheme) and DNS-resolved to block
SSRF attacks against link-local and metadata addresses. Redirects are disabled.

```json
{
  "id": "notify_webhook",
  "type": "http",
  "enabled": true,
  "method": "POST",
  "url": "http://127.0.0.1:9000/alerts",
  "headers": {
    "Content-Type": "application/json",
    "X-Alert-Source": "{{event.source}}"
  },
  "body_mode": "template",
  "body": {
    "camera": "{{data.camera}}",
    "alert_time": "{{event.timestamp}}"
  },
  "timeout_seconds": 15
}
```

- `method`: `GET`, `POST`, `PUT`, `PATCH`, or `DELETE`.
- `url`: Target endpoint URL.
- `headers`: Optional key-value header mapping (values can use templates).
- `body_mode`:
  - `"template"`: Body is rendered using `{{data.path}}` templates.
  - `"event_json"`: Sends the entire raw event JSON as request body.
  - `"none"`: No body sent.
- `body`: JSON structure used when `body_mode` is `"template"`.

#### 3. Python Actions (`type: "python"`)

Python actions execute registered Python functions in an isolated child Python process
(`opal_event.plugins.runner`). The target function must accept an event dict and return a JSON-serializable
dict or value:

```python
# In my_module/handler.py:
def handle_event(event: dict) -> dict:
    return {"processed": True, "count": len(event.get("data", {}))}
```

Action definition:
```json
{
  "id": "run_python_handler",
  "type": "python",
  "enabled": true,
  "module": "my_module.handler",
  "function": "handle_event",
  "timeout_seconds": 10
}
```

- `module`: Importable Python module path (`^[a-zA-Z_]\w*(\.[a-zA-Z_]\w*)*$`).
- `function`: Target function name (`^[a-zA-Z_]\w*$`).

### Templating in Actions

Action arguments, headers, and bodies support placeholder templating:
- `{{event}}`: Expands to the entire event dictionary.
- `{{data.field}}`: Expands to a specific field in the event `data` payload.
- Dotted paths navigate nested dictionaries and list indices (e.g. `{{data.cameras.0.id}}`).
- **Type preservation:** When a template string contains *only* a placeholder (e.g. `"{{data.count}}"`),
  the resolved value preserves its original JSON type (integer, float, boolean, list, object).
- **String interpolation:** When placeholders are embedded in surrounding text (e.g. `"prefix_{{data.id}}"`),
  values are converted to strings.

### `actions update`

Partially updates an existing action. Supports `--if-match` (auto-fetched if omitted):

```
opal event actions update ACTION_ID [--json TEXT | --file PATH] [--if-match TEXT]
```

```bash
# Update timeout and enable flag
opal event actions update announce_person --json '{"timeout_seconds": 20}'

# Update HTTP action URL
opal event actions update send_notification --json '{"url": "http://127.0.0.1:9000/v2/notify"}'
```

### `actions delete`

Deletes an action by ID. Supports `--if-match` (auto-fetched if omitted):

```
opal event actions delete ACTION_ID [--if-match TEXT]
```

```bash
opal event actions delete announce_person
# Output: deleted action announce_person
```

> **Note:** If an action is currently referenced by any rule, deletion is rejected with `HTTP 409 Conflict`.
> You must remove the action reference from all rules before deleting it.

### `actions execute`

Manually triggers an action with an event payload. The action is executed immediately through the
bounded executor pool, and the resulting execution record is returned:

```
opal event actions execute ACTION_ID --event-json TEXT
```

```bash
opal event actions execute announce_person --event-json '{
  "topic": "manual.test",
  "data": {"camera": "front_door"}
}'
```

Output:
```json
{
  "id": "c8b417e9-3891-4cf1-831e-450f381289de",
  "event_id": null,
  "rule_id": null,
  "action_id": "announce_person",
  "status": "succeeded",
  "trigger": "manual",
  "created_at": "2026-10-12T15:45:00.100000Z",
  "started_at": "2026-10-12T15:45:00.102000Z",
  "finished_at": "2026-10-12T15:45:00.115000Z",
  "duration_ms": 13,
  "exit_code": 0,
  "http_status": null,
  "stdout": "person detected at front_door\n",
  "stderr": "",
  "result": null,
  "error_code": null,
  "error_message": null,
  "action_revision": "\"1-a1b2c3d4\""
}
```

---

## 10. `executions`: execution history

Whenever an action is triggered (by a matching rule or manual execution), an `Execution` record is
created in SQLite to track its progress, lifecycle timestamps, outputs, and errors.

### `executions list`

Lists execution history with optional filtering and pagination:

```
opal event executions list [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--cursor TEXT` | `None` | Pagination cursor from a previous page's `next_cursor` |
| `--limit INT` | `100` | Maximum items to return (1–500, default: 100) |
| `--status TEXT` | `None` | Filter by status (`queued`, `running`, `succeeded`, `failed`, `timed_out`, `dropped`) |
| `--rule-id TEXT` | `None` | Filter by rule ID |
| `--action-id TEXT` | `None` | Filter by action ID |
| `--event-id TEXT` | `None` | Filter by triggering event ID |

```bash
# List recent executions in table format
opal event executions list

# Filter by failed executions
opal event executions list --status failed

# Filter by action ID
opal event executions list --action-id announce_person --limit 20
```

Example table output:
```
ID                                    ACTION_ID        STATUS     TRIGGER  CREATED_AT
c8b417e9-3891-4cf1-831e-450f381289de  announce_person  succeeded  manual   2026-10-12T15:45:00.100000Z
a1111111-2222-3333-4444-555555555555  send_notification failed    rule     2026-10-12T15:46:12.450000Z
```

### `executions get`

Retrieves full details for a single execution:

```
opal event executions get EXECUTION_ID
```

```bash
opal event executions get a1111111-2222-3333-4444-555555555555
```

Output:
```json
{
  "id": "a1111111-2222-3333-4444-555555555555",
  "event_id": "e4f3a120-8b1e-4c7b-99f2-2a781b0f56d9",
  "rule_id": "front_door_alert",
  "action_id": "send_notification",
  "status": "failed",
  "trigger": "rule",
  "created_at": "2026-10-12T15:46:12.450000Z",
  "started_at": "2026-10-12T15:46:12.455000Z",
  "finished_at": "2026-10-12T15:46:12.490000Z",
  "duration_ms": 35,
  "exit_code": null,
  "http_status": 500,
  "stdout": null,
  "stderr": null,
  "result": {"error": "internal database failure"},
  "error_code": "http_status_error",
  "error_message": "HTTP 500",
  "action_revision": "\"1-a1b2c3d4\""
}
```

### Execution Statuses

| Status | Meaning |
|---|---|
| `queued` | Enqueued in the bounded executor queue waiting for an available worker |
| `running` | Currently executing inside an active worker thread/subprocess |
| `succeeded` | Completed successfully (shell exit 0, HTTP 2xx, or Python return) |
| `failed` | Execution failed (non-zero exit code, HTTP 4xx/5xx, plugin exception, or template error) |
| `timed_out` | Killed after exceeding the configured timeout |
| `dropped` | Worker queue was full when submitted (`error_code: queue_full`); never executed |

---

## 11. `settings`: portable service settings

The Opal Event service distinguishes between **restart-only system settings** (such as server host/port,
ZeroMQ network bindings, and database file paths) and **portable runtime settings** that can be modified
live without restarting the service.

### `settings get`

Inspects the current portable settings:

```bash
opal event settings get
```

Output:
```json
{
  "execution": {
    "max_workers": 4,
    "queue_size": 100,
    "default_timeout_seconds": 30,
    "overflow": "drop_and_log",
    "retry_enabled": false,
    "max_output_bytes": 65536
  },
  "persistence": {
    "event_mode": "all",
    "retention_days": 7,
    "execution_history_enabled": true
  },
  "max_event_bytes": 1048576
}
```

### `settings update`

Updates portable settings. Mutated fields apply immediately to the running service and are written
to disk. Supports `--if-match` (auto-fetched if omitted):

```
opal event settings update [--json TEXT | --file PATH] [--if-match TEXT]
```

```bash
# Adjust worker count and timeout
opal event settings update --json '{
  "execution": {
    "max_workers": 8,
    "default_timeout_seconds": 45
  }
}'

# Change persistence mode and retention
opal event settings update --json '{
  "persistence": {
    "event_mode": "matched",
    "retention_days": 14
  }
}'
```

#### Portable Settings Reference

| Field | Range / Values | Description |
|---|---|---|
| `execution.max_workers` | 1–128 | Number of worker threads executing actions |
| `execution.queue_size` | 1–100,000 | Depth of the in-memory action execution queue |
| `execution.default_timeout_seconds` | 1–3,600 | Fallback timeout when an action specifies none |
| `execution.overflow` | `"drop_and_log"` | Policy when queue is full (drops action and logs record) |
| `execution.retry_enabled` | `false` | Action retries are not supported in v1 |
| `execution.max_output_bytes` | 0–10,485,760 | Maximum stdout/stderr bytes captured per execution |
| `persistence.event_mode` | `"all"`, `"matched"`, `"none"` | Which events to persist to SQLite |
| `persistence.retention_days` | 1–3,650 | Number of days before expired events are purged |
| `persistence.execution_history_enabled`| `true` | Execution logging is always active |
| `max_event_bytes` | 1–1,048,576 | Maximum payload cap for ZeroMQ and REST events |

---

## 12. `backup`: configuration export, validation, and import

The `backup` group manages complete, portable configuration bundles containing all rules, actions,
and portable settings. Sensitive local configuration (secrets, database paths, network bindings) is
excluded, making backup bundles safe to commit to version control or migrate across environments.

### `backup export`

Exports the entire portable configuration to stdout or a file:

```
opal event backup export [--output PATH]
```

```bash
# Print to stdout
opal event backup export

# Save to a file
opal event backup export --output backup.json
# Output: exported configuration to backup.json
```

Exported backup bundle format:
```json
{
  "format_version": 1,
  "settings": { ... },
  "actions": [ ... ],
  "rules": [ ... ]
}
```

### `backup validate`

Validates a backup bundle against the schema and referential integrity constraints without applying
any changes:

```
opal event backup validate [--json TEXT | --file PATH]
```

```bash
opal event backup validate --file backup.json
```

Output on success:
```json
{
  "valid": true,
  "errors": [],
  "warnings": []
}
```

If validation fails (for example, if a rule references a non-existent action ID), the command outputs
the error list and exits with code `1`:

```json
{
  "valid": false,
  "errors": [
    "Rule 'front_door_alert' references unknown action 'missing_action'"
  ],
  "warnings": []
}
```

### `backup import`

Atomically replaces the active rules, actions, and portable settings with the contents of the backup
bundle. Supports `--if-match` (auto-fetched if omitted):

```
opal event backup import [--json TEXT | --file PATH] [--if-match TEXT]
```

```bash
opal event backup import --file backup.json
```

Output:
```json
{
  "revision": "\"2-b2c3d4e5\"",
  "pending_restart": false
}
```

---

## 13. Health, readiness, and metrics

OPAL Event provides standard endpoints for container orchestration, monitoring, and debugging.

### `health`

Returns overall system health:

```bash
opal event health
```

Output:
```json
{
  "status": "ok",
  "broker_running": true,
  "database_ready": true,
  "pending_restart": false
}
```

- `status`: `"ok"` or `"degraded"`.
- `broker_running`: Indicates the ZeroMQ XSUB/XPUB proxy is healthy.
- `database_ready`: Indicates SQLite WAL connection is functional.
- `pending_restart`: `true` if changes were made to restart-only YAML settings on disk.

### `health-live` (Liveness Probe)

Fast liveness check intended for Kubernetes or systemd (`GET /api/v1/health/live`). Requires no
authentication.

```bash
opal event health-live
```

Output:
```json
{
  "status": "ok",
  "details": {
    "process": "alive",
    "database": "reachable"
  }
}
```

### `health-ready` (Readiness Probe)

Readiness probe intended for load balancers (`GET /api/v1/health/ready`). Checks whether the broker
is running and configurations are loaded. Requires no authentication.

```bash
opal event health-ready
```

Output:
```json
{
  "status": "ok",
  "details": {
    "broker": "running",
    "config": "valid"
  }
}
```

### `stats` (Runtime Metrics)

Returns in-memory operational metrics and queue counters:

```bash
opal event stats
```

Output:
```json
{
  "observed_events": 1420,
  "invalid_events": 3,
  "matched_events": 350,
  "executions_succeeded": 348,
  "executions_failed": 2,
  "executions_dropped": 0,
  "queue_depth": 0,
  "active_workers": 0
}
```

- `observed_events`: Total events received by the internal observer.
- `invalid_events`: Events rejected by the internal observer as invalid.
- `matched_events`: Events that matched at least one enabled rule.
- `executions_succeeded`: Completed actions with successful outcomes.
- `executions_failed`: Actions that returned errors or non-zero status.
- `executions_dropped`: Actions dropped due to queue overflow.
- `queue_depth`: Number of actions currently waiting in the executor queue.
- `active_workers`: Number of worker threads currently executing an action.

---

## 14. Walkthroughs

### 14.1 Complete CRUD and execution tour

This walkthrough registers an action, creates a rule, tests the rule logic in dry-run mode, triggers
an execution via event publication, inspects the result, and cleans up.

```bash
# 1. Verify service health
opal event health

# 2. Create a shell action
opal event actions create --json '{
  "id": "say_hello",
  "type": "shell",
  "enabled": true,
  "mode": "exec",
  "executable": "/bin/echo",
  "args": ["Hello, {{data.name}}!"],
  "stdin": "none"
}'

# 3. Create a rule referencing the action
opal event rules create --json '{
  "id": "greet_user",
  "enabled": true,
  "topic": "user.greet",
  "conditions": [
    {"path": "data.name", "operator": "exists", "value": true}
  ],
  "actions": ["say_hello"]
}'

# 4. Dry-run test the rule
opal event rules test greet_user --event-json '{"topic":"user.greet","data":{"name":"Alice"}}'

# 5. Publish an event to trigger the rule
opal event publish user.greet --data-json '{"name":"Alice"}'

# 6. Verify execution outcome
sleep 1
opal event executions list --rule-id greet_user --limit 1

# 7. Manually execute the action directly
opal event actions execute say_hello --event-json '{"topic":"manual","data":{"name":"Bob"}}'

# 8. Clean up (delete rule first, action, then clear test events)
opal event rules delete greet_user
opal event actions delete say_hello
opal event clear
```

### 14.2 Webhook alert configuration

Set up an automated webhook notification when high temperature is detected:

```bash
# 1. Register HTTP webhook action
opal event actions create --json '{
  "id": "alert_webhook",
  "type": "http",
  "enabled": true,
  "method": "POST",
  "url": "http://127.0.0.1:9000/alerts",
  "headers": {"Content-Type": "application/json"},
  "body_mode": "template",
  "body": {
    "alert": "high_temperature",
    "sensor": "{{data.sensor_id}}",
    "temperature": "{{data.temp_c}}"
  }
}'

# 2. Register rule matching high temperature readings
opal event rules create --json '{
  "id": "high_temp_rule",
  "enabled": true,
  "topic": "sensor.temperature",
  "conditions": [
    {"path": "data.temp_c", "operator": "gte", "value": 30.0}
  ],
  "actions": ["alert_webhook"]
}'

# 3. Test with a normal temperature reading (should not match)
opal event rules test high_temp_rule --event-json '{"topic":"sensor.temperature","data":{"temp_c":22.0,"sensor_id":"s1"}}'
# "matched": false

# 4. Test with an alert temperature reading (matches)
opal event rules test high_temp_rule --event-json '{"topic":"sensor.temperature","data":{"temp_c":32.5,"sensor_id":"s1"}}'
# "matched": true
```

---

## 15. Scripting and automation

### JSON processing with `jq`

Always pass `--json` to `opal event` when writing shell scripts.

```bash
# Extract all rule IDs
opal event --json rules list | jq -r '.[].id'

# List IDs of all failed executions in the last hour
opal event --json executions list --status failed | jq -r '.[].id'

# Count observed events
opal event --json stats | jq .observed_events

# Find all actions referenced by enabled rules
opal event --json rules list | jq -r '.[] | select(.enabled) | .actions[]' | sort -u
```

### CI/CD configuration guard

Validate backup bundles in CI before deploying changes to production:

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "Validating backup configuration..."
opal event backup validate --file config/backup.json

echo "Deploying to production..."
opal event --api-url http://prod-event.local:8080 backup import --file config/backup.json

echo "Checking health..."
opal event --api-url http://prod-event.local:8080 health-ready
echo "Deployment successful!"
```

### Batch operations

```bash
# Disable all rules matching a pattern
for rule_id in $(opal event --json rules list | jq -r '.[].id' | grep '^temp_'); do
  echo "Disabling rule: $rule_id"
  opal event rules update "$rule_id" --json '{"enabled": false}'
done
```

---

## 16. Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| `Error: cannot reach Opal Event API: ...` | The service is not running or the URL is incorrect. Start `opal-event-svc` or check `--api-url` / `$OPAL_EVENT_API_URL`. |
| `No such option: --json` | Global options must precede subcommands: use `opal event --json rules list`. |
| `HTTP 401: unauthorized` | The service has REST authentication enabled. Pass `--token <token>` or set `$OPAL_EVENT_API_TOKEN`. |
| `HTTP 404: not_found` | Resource does not exist. Check `rules list` or `actions list` for valid IDs. |
| `HTTP 409: conflict` on action delete | Action is still referenced by an active rule. Remove the action from all rules before deleting it. |
| `HTTP 412: precondition_failed` | The resource was modified concurrently since the ETag was read. Retry the operation. |
| `HTTP 422: validation_error` | Payload failed schema validation. Check error detail for failing property and type constraints. |
| `HTTP 422: invalid_header` | A context correlation header (`X-Opal-*`) contained invalid characters (must be 1–128 printable ASCII characters). |
| `destination_not_allowed` in HTTP execution | Target URL is not permit-listed in `security.http_action_allowlist` or resolves to a blocked link-local/private address. |
| `template_error` in action execution | The action template referenced a path that does not exist in the event. Verify event structure. |
| Action execution `status: dropped` | Worker pool queue is full (`queue_depth` exceeded `queue_size`). Increase `max_workers` or `queue_size` in settings. |
| `timeout` in execution | Action ran longer than configured `timeout_seconds`. Increase timeout or optimize script/endpoint. |

---

## 17. Appendix: CLI ↔ REST mapping

All REST endpoints reside under `/api/v1`. Collection endpoints use trailing slashes. All 29 OpenAPI
operations declared in `opal_event.1.2.0.yaml` are mapped below:

| CLI command | HTTP method | API endpoint | If-Match / ETag |
|---|---|---|---|
| `opal event publish` | `POST` | `/api/v1/events/` | None |
| `opal event list` | `GET` | `/api/v1/events/` | None |
| `opal event clear` | `DELETE` | `/api/v1/events/` | None |
| `opal event get` | `GET` | `/api/v1/events/{event_id}` | None |
| `opal event delete` | `DELETE` | `/api/v1/events/{event_id}` | None |
| `opal event topics` | `GET` | `/api/v1/topics/` | Response carries strong `ETag` |
| `opal event rules list` | `GET` | `/api/v1/rules/` | Response carries strong `ETag` |
| `opal event rules create` | `POST` | `/api/v1/rules/` | None |
| `opal event rules get` | `GET` | `/api/v1/rules/{rule_id}` | Response carries strong `ETag` |
| `opal event rules update` | `PATCH` | `/api/v1/rules/{rule_id}` | Required (`If-Match`; auto-fetched) |
| `opal event rules delete` | `DELETE` | `/api/v1/rules/{rule_id}` | Required (`If-Match`; auto-fetched) |
| `opal event rules test` | `POST` | `/api/v1/rules/{rule_id}/test` | None |
| `opal event actions list` | `GET` | `/api/v1/actions/` | Response carries strong `ETag` |
| `opal event actions create` | `POST` | `/api/v1/actions/` | None |
| `opal event actions get` | `GET` | `/api/v1/actions/{action_id}` | Response carries strong `ETag` |
| `opal event actions update` | `PATCH` | `/api/v1/actions/{action_id}` | Required (`If-Match`; auto-fetched) |
| `opal event actions delete` | `DELETE` | `/api/v1/actions/{action_id}` | Required (`If-Match`; auto-fetched) |
| `opal event actions execute` | `POST` | `/api/v1/actions/{action_id}/execute` | None |
| `opal event executions list` | `GET` | `/api/v1/executions/` | None |
| `opal event executions get` | `GET` | `/api/v1/executions/{execution_id}` | None |
| `opal event settings get` | `GET` | `/api/v1/settings/` | Response carries strong `ETag` |
| `opal event settings update` | `PATCH` | `/api/v1/settings/` | Required (`If-Match`; auto-fetched) |
| `opal event backup export` | `GET` | `/api/v1/backup/export` | None |
| `opal event backup validate` | `POST` | `/api/v1/backup/validate` | None |
| `opal event backup import` | `POST` | `/api/v1/backup/import` | Required (`If-Match`; auto-fetched) |
| `opal event health` | `GET` | `/api/v1/health/` | None |
| `opal event stats` | `GET` | `/api/v1/stats/` | None |
| `opal event health-live` | `GET` | `/api/v1/health/live` | None (no auth required) |
| `opal event health-ready` | `GET` | `/api/v1/health/ready` | None (no auth required) |
