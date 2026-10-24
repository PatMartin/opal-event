# Opal Event v1 — ZeroMQ Wire Protocol

**Status:** v1 companion specification to `opal_event.yaml` (OpenAPI 3.1). REST covers management and REST event ingress; ZeroMQ is a separate transport, not an HTTP API.

## Architecture

- `opal-event-svc` binds an **XSUB** inbound endpoint (default `tcp://127.0.0.1:5555`) and an **XPUB** outbound endpoint (default `tcp://127.0.0.1:5556`). Producers use PUB and connect to XSUB; consumers use SUB and connect to XPUB. Names in the YAML configuration are `zeromq.publishers` (inbound producers) and `zeromq.subscribers` (outbound subscribers).
- Proxy forwards multipart messages and propagates downstream subscription filters to upstream publishers. The internal observer must receive all events independently (for example, a capture path feeding an internal SUB/processor). It may not block the forwarding path.
- REST POST `/v1/events` publishes through a service-owned PUB connector to the inbound XSUB endpoint. A service-owned internal all-topics observer normalizes, validates, logs and evaluates rule matches. Delivery to the observer is also best-effort.
- Connecting does not imply all subscriptions have propagated. PUB/SUB suffers **slow-joiner** loss. There are no producer or consumer acknowledgments, durable queues, automatic replay, or delivery ordering guarantees across publishers.

## Message framing

Exactly **two** multipart frames:

1. UTF-8 topic, ASCII restricted: `^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)*$`, at most 255 characters. The topic MUST match the JSON envelope `topic` exactly.
2. UTF-8 JSON object envelope, minimal required keys `topic` (string), `data` (any JSON value). Optional `id` (UUID), `source` (nonempty string), `timestamp` (RFC3339 date-time).

**Total payload cap:** frame-1 bytes + frame-2 bytes <= **1,048,576 bytes** (1,024 KiB). This excludes transport framing overhead. Producers SHALL enforce this limit; the internal observer rejects larger or malformed messages for processing and increments `invalid_events`. The pure XSUB/XPUB broker does not necessarily reject or intercept malformed events before forwarding, so external subscribers MUST validate inputs themselves. Never transmit raw images in these events; send stable references or URIs if needed.

```text
Frame 1: vision.object.detected
Frame 2: {"topic":"vision.object.detected","source":"opal-look","data":{"object":"person","camera":"front_door","confidence":0.94}}
```

### Topic behavior

- ZeroMQ SUB filters are **byte prefixes**, not application wildcard expressions. A raw subscription for `vision.` matches all descendant `vision.*` topics and excludes `vision` itself. A subscription for `vision` also matches `visionary` and is not the same as an application rule.
- The **rule engine** implements two kinds of matches independently of SUB filters: exact (`vision.object.detected`) and hierarchical prefix with a terminal `.*` (`vision.*`). Thus `vision.*` matches `vision.object.detected`, but does not match bare `vision` or `visionary`.
- Topics do not need to be registered. Use lower-case dot-separated namespaces. Suggested conventions: `intent.recognized`, `vision.object.detected`, `service.health.changed`.

## Metadata normalization and REST distinctions

- For REST ingress, `id` defaults to a new UUID, `source` defaults to `opal-event-rest`, and `timestamp` defaults to UTC publication time; the **normalized event is what REST publishes** on ZeroMQ.
- Direct ZeroMQ producers may omit optional metadata. The transparent proxy forwards them unchanged. An internal observer assigns defaults to its **own stored/processed copy** (`source=unknown` when absent), not to forwarded frames. Two different observers may therefore assign different generated IDs for the same minimally framed message. For event correlation between services, producers SHOULD supply a stable UUID and timestamp.
- With an ID absent, deduplication is not reliable. There is **no deduplication guarantee** in v1. If the producer chooses an ID, it must use it consistently across retries. Events that fail validation are ignored by rules and excluded from event history; the proxy can still forward their original frames.
- REST responds `202 Accepted` on successful handoff to the local publishing mechanism, **not** on subscriber acknowledgement, persistence, rule matching, or action completion.

## Subscription and publishing examples (PyZMQ)

Publisher:

```python
import json
import zmq

ctx = zmq.Context.instance()
pub = ctx.socket(zmq.PUB)
pub.connect("tcp://127.0.0.1:5555")
event = {"topic": "intent.recognized", "source": "opal-listen", "data": {"intent": "lights.turn_on"}}
# Wait for connection/subscription propagation as appropriate; this sleep alone is not a guarantee.
pub.send_multipart([event["topic"].encode("utf-8"), json.dumps(event).encode("utf-8")])
```

Subscriber:

```python
import json
import zmq

ctx = zmq.Context.instance()
sub = ctx.socket(zmq.SUB)
sub.connect("tcp://127.0.0.1:5556")
sub.setsockopt(zmq.SUBSCRIBE, b"intent.")
while True:
    topic, payload = sub.recv_multipart()
    event = json.loads(payload)
    if event.get("topic") != topic.decode("utf-8"):
        continue
    print(event)
```

## Security and deployment

- Bind defaults: localhost only. LAN deployment: configure explicit interface/IP and require REST bearer authorization and **ZeroMQ CURVE** on external-facing sockets. Configuration references secrets by protected file paths or environment variables, not inline literal secrets.
- CURVE authenticates peers cryptographically only when peer public keys are explicitly checked/authorized; merely enabling CURVE encryption is **not** a publisher allowlist. Configure trusted client public keys for both publisher and subscriber sides. CURVE is not an application permission system; v1 has no fine-grained per-topic publisher ACL.
- CURVE server keys, trust lists, REST bearer changes, bound addresses/ports require service restart; rule/action YAML changes apply live.
- Untrusted publisher payloads must never become unescaped shell code, unapproved HTTP hostnames/ports, filesystem paths for executables, or Python module names.
- HTTP actions use configured host/port allowlist, DNS and address checks, and no redirects. Disallow link-local metadata and other unapproved addresses.
- Shell actions can opt into `sh`, `bash`, `cmd`, or `powershell` script execution; all scripts are registered statically. Pass event values as independent argv entries or JSON on stdin; never interpolate event text into executable script source.
- Python plugin function contract: `def handle_event(event: dict) -> JSON-serializable value`. The trusted runner imports a registered module in a child Python interpreter, loads event JSON from stdin, writes a single JSON result to stdout, and reports non-zero exit/timeout as a failure. Plugin prints/debug output must go to stderr to avoid corrupting the output protocol.
- Run subprocess workers unprivileged, with controlled environment, working directory, PATH, output capture limits, execution timeouts, and process tree cleanup. Subprocess isolation is **not** a strong security sandbox for malicious plugins.

## Execution and retention

- Observer matches enabled rules; each rule's conditions use AND semantics. All matched action IDs schedule **independently** in a configurable pool (default 4 workers), no completion order guarantee.
- When the bounded queue (suggested size 100) is full, record `dropped` with `queue_full`, increment metrics, and continue. No automatic retries for failure or drop.
- Persist observed event history according to YAML `event_mode`: `all`, `matched`, `none`; separate execution history remains enabled. Default seven-day retention; cleanup is periodic. `GET /v1/events` is for inspection and not for replay.
- Shell exit code, HTTP status and response summary, Python result/error, runtime, captured output, action config revision are recorded on execution. Request and output values may be redacted/truncated according to configuration.

## Configuration and reload semantics

- Single YAML config can be replaced through REST (optimistic revision via `If-Match`) or edited on disk. File watcher validates before atomic in-memory switch; invalid edits leave last-good effective configuration running and health degrades with a reload error.
- Network/CURVE/security/database-path changes are written to disk but not made active until restart; REST `GET /v1/config` exposes `pending_restart` and fields. Rule/action updates take effect immediately; in-flight jobs continue using the snapshot on which they were queued.
- Example config: [`opal_event.example.yaml`](opal_event.example.yaml).

## Limitations explicitly outside v1

Durable queues, replay, message acknowledgments, transactional action execution, event aggregations, chained rules, complex Boolean rule expressions, exactly-once semantics, broker-side event transformations, and arbitrary network-reachable action destinations.


## REST alignment note
The ZeroMQ multipart frames are unchanged by the OPAL operational reconciliation. REST examples should use `/api/v1/events/`, not the earlier `/v1/events` route.
