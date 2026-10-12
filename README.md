# Opal Event

Event bus, lightweight rule engine, and bounded action-execution service for the OPAL stack, with a
REST API (`opal-event-svc`), CLI (`opal` / `opal-event`), and a React SPA. The REST/CLI/SPA surface
implements [`opal_event.1.2.0.yaml`](opal_event.1.2.0.yaml) (OpenAPI 3.1, "OPAL Suite Standard v1.2")
exactly; the ZeroMQ wire transport implements `specs/zeromq_protocol.md` exactly. Both documents are
authoritative — see them for the full, normative request/response and wire-protocol details. This
README explains how the implementation realizes that contract and how to run it.

> **Versioning:** [`opal_event.1.1.0.yaml`](opal_event.1.1.0.yaml) is kept alongside the active
> [`opal_event.1.2.0.yaml`](opal_event.1.2.0.yaml) purely as a historical/diff reference; the
> implementation targets 1.2.0. See [Migrating from 1.1.0](#migrating-from-110) below for exactly
> what changed and why none of it touches the ZeroMQ wire protocol.

## Architecture

```
                 ┌───────────────┐        PUB         ┌───────────────┐
 External        │  PUB clients   │ ───────────────▶  │     XSUB       │
 producers       └───────────────┘                    │  (publishers   │
                                                        │   endpoint)    │
                                                        └───────┬───────┘
                                                                │ zmq.proxy (forwards data;
                                                                │ propagates subscriptions)
                                                        ┌───────▼───────┐        SUB          ┌───────────────┐
                                                        │     XPUB       │ ─────────────────▶  │ External SUB   │
                                                        │ (subscribers   │                     │ consumers      │
                                                        │   endpoint)    │                     └───────────────┘
                                                        └───────┬───────┘
                                                                │ SUB (subscribe to everything)
                                                        ┌───────▼───────────────────────┐
                                                        │   Internal observer            │
                                                        │   - validate/normalize          │
                                                        │   - persist (event_mode)        │
                                                        │   - match rules                 │
                                                        │   - submit matched actions       │
                                                        └───────┬────────────────────────┘
                                                                │
                           ┌────────────────────────────────────┼─────────────────────────┐
                           │                                     ▼                         │
                  ┌────────▼────────┐                 ┌──────────────────┐        ┌────────▼────────┐
                  │  SQLite (events,  │                 │ Bounded executor  │        │  REST API         │
                  │  executions)      │                 │ pool (N workers,  │        │  (FastAPI)        │
                  └───────────────────┘                 │ bounded queue)    │        │  - CLI / SPA       │
                                                         └──────────────────┘        │    consume this    │
                                                                                      └───────────────────┘
```

* **REST ingress** (`POST /api/v1/events/`) does **not** call the rule engine directly. It normalizes
  the event and publishes it through a service-owned PUB connector into the same XSUB endpoint
  external producers use. The single internal observer then treats REST-originated and
  directly-published ZeroMQ events identically — this matches zeromq_protocol.md exactly ("REST
  POST /api/v1/events publishes through a service-owned PUB connector to the inbound XSUB endpoint").
* The **XSUB/XPUB proxy** is a pure, best-effort forwarder: it does not validate, store, or
  rate-limit. Only the internal observer's own copy is validated/stored/matched.
* The **executor** is a fixed-size worker pool reading from a bounded queue. Overflow is dropped
  (recorded as an `Execution` with `status=dropped`, `error_code=queue_full`) — never retried.
* **SQLite** stores observed events (per `persistence.event_mode`) and all execution records
  (independent of `event_mode`), with periodic retention cleanup.
* **Configuration** is one YAML file, hot-reloaded on save (via a `watchfiles` filesystem watcher)
  or via REST. Network bindings, REST auth, CURVE keys, and the database path require a restart;
  rules/actions/execution/persistence settings apply live.

## Technology stack

| Layer | Technology |
|---|---|
| REST | Python, FastAPI, Pydantic v2, Uvicorn |
| Messaging | PyZMQ (XSUB/XPUB broker + CURVE) |
| Persistence | SQLite (stdlib `sqlite3`) |
| CLI | Typer |
| SPA | React + TypeScript + Vite |
| Configuration | YAML (`watchfiles` for hot reload) |
| Packaging | `pyproject.toml` (setuptools) |

## Project layout

```
opal-event/
├── opal_event.1.2.0.yaml        # authoritative OpenAPI 3.1 contract (do not modify)
├── opal_event.1.1.0.yaml        # previous contract version, kept for historical reference
├── specs/zeromq_protocol.md     # authoritative ZeroMQ wire protocol (do not modify)
├── config/opal_event.example.yaml
├── examples/                    # sample event/rule/action JSON
├── scripts/announce.sh          # sample allowlisted shell action script
├── src/opal_event/
│   ├── models.py                 # pydantic schemas mirroring the OpenAPI contract
│   ├── config.py                 # YAML load/validate/persist/hot-reload (ConfigStore)
│   ├── db.py                     # SQLite events/executions persistence
│   ├── rules.py                  # topic + condition matching
│   ├── templating.py             # {{data.path}} / {{event}} rendering
│   ├── security.py                # HTTP destination allowlist, subprocess env hardening
│   ├── actions/                  # shell / http / python action runners
│   ├── executor.py               # bounded worker pool
│   ├── zeromq_broker.py          # XSUB/XPUB proxy + internal observer + CURVE
│   ├── metrics.py                # in-memory counters for GET /api/v1/stats/
│   ├── service.py                # wires config+db+executor+broker together
│   ├── app.py / routers/         # FastAPI app and one router module per tag
│   ├── cli/                      # Typer CLI (`opal event ...`)
│   ├── plugins/                  # Python plugin runner + example plugin
│   └── __main__.py                # `opal-event-svc` entrypoint
├── spa/                           # React/TS/Vite SPA
└── tests/{unit,integration,contract,security,e2e}/
```

## Installation

Requires Python ≥ 3.11 and Node.js ≥ 18 (for the SPA only).

### Linux / macOS

```bash
cd opal-event
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp config/opal_event.example.yaml config/opal_event.yaml
mkdir -p var
```

### Windows (PowerShell)

```powershell
cd opal-event
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

Copy-Item config\opal_event.example.yaml config\opal_event.yaml
New-Item -ItemType Directory -Force -Path var
```

### SPA dependencies (both platforms)

```bash
cd spa
npm install
```

## Development

Run the REST/ZeroMQ service:

```bash
opal-event-svc --config config/opal_event.yaml --log-level info
```

Run the SPA dev server (proxies `/api` to the REST server):

```bash
cd spa
OPAL_EVENT_API_URL=http://127.0.0.1:8080 npm run dev     # Linux/macOS
$env:OPAL_EVENT_API_URL="http://127.0.0.1:8080"; npm run dev   # Windows PowerShell
```

Open http://127.0.0.1:5173.

Use the CLI against a running service:

```bash
export OPAL_EVENT_API_URL=http://127.0.0.1:8080     # Linux/macOS
$env:OPAL_EVENT_API_URL="http://127.0.0.1:8080"      # Windows PowerShell

opal event health
opal event publish vision.object.detected --data-json '{"object":"person","camera":"front_door"}'
opal event rules list
opal event actions execute announce_person --event-json '{"topic":"manual.test","data":{"camera":"front_door"}}'
```

Run tests / lint:

```bash
pytest
ruff check src tests
cd spa && npx tsc -b   # SPA typecheck
```

## Production startup

### Linux (systemd example)

```ini
# /etc/systemd/system/opal-event.service
[Unit]
Description=Opal Event service
After=network.target

[Service]
Type=simple
User=opal
WorkingDirectory=/opt/opal-event
Environment=OPAL_EVENT_API_TOKEN=change-me
ExecStart=/opt/opal-event/.venv/bin/opal-event-svc --config /opt/opal-event/config/opal_event.yaml --log-level info
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
cd spa && npm run build   # outputs spa/dist; serve it with any static file server / reverse proxy
```

### Windows (service via NSSM, or run directly)

```powershell
# Direct foreground run:
.venv\Scripts\opal-event-svc.exe --config config\opal_event.yaml --log-level info

# Or register as a Windows service with NSSM:
nssm install OpalEvent "C:\opal-event\.venv\Scripts\opal-event-svc.exe" --config C:\opal-event\config\opal_event.yaml
nssm start OpalEvent
```

Serve `spa/dist` (after `npm run build`) with any static web server or reverse proxy (e.g. IIS,
nginx, Caddy) that forwards `/api` to the REST server's host:port.

## Configuration

A single YAML file (`config/opal_event.yaml`) holds the full `Settings` schema: `server`, `zeromq`,
`security`, `execution`, `persistence`, `actions`, `rules`. See
[`config/opal_event.example.yaml`](config/opal_event.example.yaml) for a complete annotated example.

* **Restart-only fields:** `server.*`, `zeromq.publishers`/`subscribers`/`curve`,
  `security.rest_auth`, `persistence.database`. Changing these on disk is saved but only activated
  after a service restart; `GET /api/v1/health/` reports `pending_restart: true` until then.
* **Hot-reloadable fields:** `execution.*`, `persistence.event_mode`/`retention_days`, `actions`,
  `rules`. Edit the file directly (a filesystem watcher picks it up) or use the REST API
  (`PATCH /api/v1/settings/`, the rules/actions CRUD endpoints) — both paths validate atomically and
  leave the last-good configuration active on any validation failure.
* **Policy:** binding any interface to a non-loopback address requires `security.rest_auth.enabled:
  true` (enforced at startup) and should also enable `zeromq.curve.enabled: true`.

### Portable settings vs. full settings

`GET/PATCH /api/v1/settings/` exposes only the **portable** subset (`execution`,
`persistence.event_mode`/`retention_days`/`execution_history_enabled`, `max_event_bytes`) — never
secrets, network bindings, or the database path. `GET/POST /api/v1/backup/*` additionally carries
`actions` and `rules`, still excluding secrets/bindings/history.

> **Contract note:** `ZeroMqSettings.max_event_bytes` is annotated `const: 1048576` in
> `opal_event.1.2.0.yaml`, while `PortableSettings.max_event_bytes` is documented as PATCHable up to
> that same value. A literal `const` would make the documented PATCH behavior non-functional, so this
> implementation treats `1048576` as the enforced **ceiling** rather than a fixed literal — the only
> reasonable reading that keeps the PATCH operation meaningful, per the contract's own narrative.

## ZeroMQ

See [`specs/zeromq_protocol.md`](specs/zeromq_protocol.md) for the full wire protocol (framing,
topic semantics, CURVE, security, limitations). Summary:

* Producers: `PUB` socket, `connect()` to `zeromq.publishers` (default `tcp://127.0.0.1:5555`).
* Consumers: `SUB` socket, `connect()` to `zeromq.subscribers` (default `tcp://127.0.0.1:5556`).
* Two-frame multipart messages: `[topic_bytes, json_envelope_bytes]`, envelope `{"topic", "data",
  "id"?, "source"?, "timestamp"?}`, combined size ≤ 1 MiB.
* Best-effort, no acknowledgments, no delivery ordering guarantees, no replay.
* CURVE: set `zeromq.curve.enabled: true` with `server_public_key_file`/`server_secret_key_file`
  (a `zmq.auth`-format certificate pair — generate with
  `python -c "import zmq.auth; zmq.auth.create_certificates('config/curve', 'server')"`) and
  `authorized_client_keys_file` (a directory of `*.key` public-key certificate files, one per
  authorized external client, per `zmq.auth` conventions). The service's own internal connectors
  (REST publisher, internal observer) use an ephemeral keypair that is always implicitly authorized.

## REST API

Full contract: [`opal_event.1.2.0.yaml`](opal_event.1.2.0.yaml). All routes are under `/api/v1`, use
trailing slashes on collection roots, bearer auth (`Authorization: Bearer <token>`) when
`security.rest_auth.enabled` is true, and the shared OPAL error shapes: `Problem` for
400/401/403/404/409/413/429/500/503, FastAPI's standard `HTTPValidationError` for request-body 422s,
and the nested `OpalError` (`{"error": {...}}`) shape for strong-ETag preconditions (412/428) and
malformed OPAL context headers (422) -- see [Migrating from 1.1.0](#migrating-from-110).

| Resource | Operations |
|---|---|
| Events | `POST/GET /api/v1/events/`, `GET /api/v1/events/{id}` (cursor-paginated history) |
| Topics | `GET /api/v1/topics/` (full catalog, strong ETag) |
| Rules | `GET/POST /api/v1/rules/`, `GET/PATCH/DELETE /api/v1/rules/{id}`, `POST /api/v1/rules/{id}/test` (full catalog, strong ETag, PATCH/DELETE require `If-Match`) |
| Actions | `GET/POST /api/v1/actions/`, `GET/PATCH/DELETE /api/v1/actions/{id}`, `POST /api/v1/actions/{id}/execute` (full catalog, strong ETag, PATCH/DELETE require `If-Match`) |
| Executions | `GET /api/v1/executions/`, `GET /api/v1/executions/{id}` (cursor-paginated history, `has_more`) |
| Operations | `GET /api/v1/health/`, `GET /api/v1/stats/`, `GET /api/v1/health/live`, `GET /api/v1/health/ready` |
| Settings | `GET/PATCH /api/v1/settings/` (strong ETag, PATCH requires `If-Match`) |
| Backup | `GET /api/v1/backup/export`, `POST /api/v1/backup/validate`, `POST /api/v1/backup/import` |

Every request/response additionally carries the four OPAL correlation headers
(`X-Opal-Session-Id`, `X-Opal-Transaction-Id`, `X-Opal-Request-Id`, `X-Opal-Causation-Id`); see
[Migrating from 1.1.0](#migrating-from-110).

Interactive docs: `http://127.0.0.1:8080/docs` (Swagger UI) once the service is running.

## CLI

`opal event ...` (or the standalone `opal-event ...` entrypoint) exposes every applicable REST
operation with hyphenated options and snake_case Python identifiers, e.g.:

```bash
opal event publish <topic> --data-json '<json>' [--id] [--source] [--timestamp]
opal event list [--cursor] [--limit] [--topic] [--source] [--from] [--to]
opal event get <event_id>
opal event topics
opal event health | stats | health-live | health-ready

opal event rules list                         # full catalog; no cursor/limit (v1.2 bounded catalog)
opal event rules create|get [--json|--file]
opal event rules update|delete <rule_id> [--if-match]   # If-Match auto-fetched via GET when omitted
opal event rules test <rule_id> --event-json '<json>'

opal event actions list                       # full catalog; no cursor/limit (v1.2 bounded catalog)
opal event actions create|get [--json|--file]
opal event actions update|delete <action_id> [--if-match]  # If-Match auto-fetched via GET when omitted
opal event actions execute <action_id> --event-json '<json>'

opal event executions list [--status] [--rule-id] [--action-id] [--event-id]  # default/max limit 100/500
opal event executions get <execution_id>

opal event settings get
opal event settings update [--json|--file] [--if-match]

opal event backup export [--output]
opal event backup validate [--json|--file]
opal event backup import [--json|--file] [--if-match]
```

Global options: `--api-url` (env `OPAL_EVENT_API_URL`, default `http://127.0.0.1:8080`), `--token`
(env `OPAL_EVENT_API_TOKEN`), `--json` (raw JSON output instead of tables), `--timeout`, and the
optional OPAL correlation-ID passthroughs `--opal-session-id`, `--opal-transaction-id`,
`--opal-causation-id` (env `OPAL_SESSION_ID`/`OPAL_TRANSACTION_ID`/`OPAL_CAUSATION_ID`).

Action/rule/backup create and update commands accept a full JSON payload via `--json '<text>'`,
`--file <path>`, or stdin — this is the simplest reliable way to expose the three polymorphic action
types (shell/http/python) and nested rule conditions without re-implementing every field as a flag.

## SPA

`spa/` is a React + TypeScript + Vite single-page app with tabs for **Health**, **Events**,
**Rules**, **Actions**, **Executions**, and **Settings**. It talks only to the REST API (no business
logic is duplicated client-side): publishing events, creating/enabling/disabling/deleting rules and
actions, running rule dry-tests and manual action executions, browsing event/execution history, and
editing portable settings. A bearer token field in the sidebar is stored in `localStorage` and sent
as `Authorization: Bearer <token>` when set. Optional `X-Opal-Session-Id`/`-Transaction-Id`/
`-Causation-Id` fields are also available in the sidebar and, when set, are sent on every request;
rule/action enable/disable and delete actions transparently fetch the resource's current strong
ETag first and send it as `If-Match` (v1.2 requires this; see [Migrating from 1.1.0](#migrating-from-110)).

```bash
cd spa
npm run dev      # development server with /api proxy
npm run build    # production build to spa/dist
npm run typecheck
```

## Security notes

* **HTTP actions**: destination is checked against `security.http_action_allowlist`
  (host+scheme+port), then DNS-resolved and re-checked against blocked ranges (link-local/metadata,
  IPv6 loopback/link-local) on every call; redirects are disabled.
* **Shell actions**: always `argv`, never a concatenated shell string (`shell=False`); templated
  event values become individual argv entries or JSON on stdin, never interpreted command source.
  Interpreter modes (`sh`/`bash`/`cmd`/`powershell`) require a statically registered `script_path`.
  Subprocesses run in their own session/process-group so a timeout can kill the whole tree.
* **Python plugin actions**: run in a separate, unprivileged child interpreter
  (`opal_event.plugins.runner`); the registered `module.function(event) -> JSON value` contract
  receives the event via stdin JSON and must return via stdout JSON — this is process isolation, not
  a security sandbox against a malicious plugin.
* **CURVE** authenticates and encrypts the ZeroMQ transport but is not a per-topic publisher ACL —
  v1 has no fine-grained publish permissions, matching zeromq_protocol.md.
* **REST bearer auth** is required by this implementation whenever `server.host` is non-loopback
  (enforced at startup).

## Migrating from 1.1.0

opal_event.1.2.0.yaml adds the "OPAL Suite Standard v1.2": cross-cutting correlation headers and
stricter concurrency control. **None of it touches `ZeroMqSettings`, `CurveSettings`, `Event`/
`EventInput`, `Rule`/`Action`, or `Execution`** -- those schemas, and the two-frame wire format in
`specs/zeromq_protocol.md`, are byte-for-byte unchanged from 1.1.0. The migration is entirely a REST-surface tightening:

1. **Correlation headers.** `X-Opal-Session-Id`, `X-Opal-Transaction-Id`, `X-Opal-Request-Id`,
   `X-Opal-Causation-Id` are optional, opaque, 1-128 printable-ASCII tokens accepted on every
   request and echoed on every response (success or error). The service assigns a
   `X-Opal-Transaction-Id` when absent and always generates a fresh `X-Opal-Request-Id` for the
   current hop; malformed values are rejected with a 422 in the nested `OpalError` shape
   (`{"error": {"code", "message", "request_id", "transaction_id", "session_id", "causation_id"}}`),
   handled by `opal_event.opal_context.OpalContextMiddleware`. When REST publishes an event, the
   current request's session/transaction/causation IDs are added as additional, optional keys
   (`opal_session_id`, `opal_transaction_id`, `opal_causation_id`) on the outbound two-frame ZeroMQ
   envelope -- an additive-only extension zeromq_protocol.md's "optional id/source/timestamp"
   wording already permits; the stored/REST `Event` shape is untouched (it forbids extra properties).
2. **Rules and Actions became bounded configuration catalogs.** `GET /api/v1/rules/` and
   `GET /api/v1/actions/` no longer accept `cursor`/`limit` and always return the complete
   collection (`next_cursor` stays `null`), with a strong `ETag` header. An internal cap (10,000
   entries; not part of the portable/REST schema) guards against unbounded growth per the
   contract's "may reject catalog sizes over an advertised deployment cap" note.
3. **Strong-ETag preconditions.** Rule/Action `PATCH`/`DELETE` now require `If-Match`: `428` if
   omitted, `412` if it no longer matches the current revision (nested `OpalError` shape).
   `PATCH /api/v1/settings/` switched from an unconditional-409-on-mismatch model to the same
   428/412 scheme. `POST /api/v1/backup/import` is **unchanged** (still a required header at the
   HTTP-validation level and `409` on a stale revision) because opal_event.1.2.0.yaml's `backup/import`
   operation does not declare 412/428 responses -- this implementation follows the literal,
   per-operation contract rather than the free-text summary where they disagree.
4. **Executions gained `has_more`** (default limit 100, max 500; was 50/200). Events list pagination
   is unchanged (schema wasn't updated to add `has_more` there, so it isn't returned). Both lists'
   cursors are now scoped to their exact filter set and expire after one hour; a cursor reused with
   different filters, or an expired/garbled one, returns `422` (`HTTPValidationError` shape, matching
   each endpoint's already-declared 422 response).
5. `GET /api/v1/topics/` gained a strong `ETag` header (same config-revision value as rules/actions,
   even though topics are a derived read-only projection with no corresponding `If-Match` write path
   -- the header's declared semantics are reused verbatim here since the contract offers no
   topic-specific alternative).

Two schemas from the shared OPAL component library, `OpalHealthProbe` and `OpalCursorPageMetadata`,
are defined in opal_event.1.2.0.yaml but never referenced by any path -- they are intentionally
unused (dead boilerplate from the suite-wide schema set, not wired into this service's operations).

## Known limitations

* CURVE authorization is directory-based (`zmq.auth` certificate files), per standard pyzmq
  conventions; the contract does not specify an exact file format for `authorized_client_keys_file`.
* No durable queues, delivery acknowledgments, exactly-once semantics, replay, rule chaining, or
  action retries — all explicitly out of scope per `zeromq_protocol.md`.
* SQLite is a single-writer store; this is appropriate for the bounded worker-pool write volume this
  service targets, not for very high-throughput deployments.
