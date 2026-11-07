# opal-event Cookbook

Twenty recipes, all runnable with nothing but `opal-event` installed — the
service, the CLI, Python, and `pyzmq` (already a dependency).

**Setup for every recipe:** start the service once and open a second terminal.

```bash
opal-event-svc            # config/opal_event.yaml → REST :8080, ZMQ :5555/:5556
```

**On re-runs:** `create` rejects a duplicate `id` with `409 conflict` — pick a
new id, `delete` the old object first, or `update` it in place.

---

## 1. Tail the entire event bus

Everything the broker forwards is visible to any subscriber — this is the
single most useful debugging trick in the box.

```bash
python -c "
import zmq
s = zmq.Context().socket(zmq.SUB)
s.connect('tcp://127.0.0.1:5556')     # consumer side
s.subscribe('')                        # empty prefix = ALL topics
print('listening…')
while True:
    topic, payload = s.recv_multipart()
    print(topic.decode(), '→', payload.decode())
"
```

```bash
opal event publish demo.arrival --data-json '{"who": "me", "via": "rest"}'
```

You'll see the two-frame message: topic frame + JSON envelope.

---

## 2. Make your machine beep when an event fires

A `shell` action runs any executable — including a beeper.

```bash
# Windows
opal event actions create --json '{
  "id": "honk", "type": "shell", "enabled": true, "mode": "exec",
  "executable": "powershell",
  "args": ["-c", "[console]::beep(880,300); [console]::beep(660,300)"],
  "stdin": "none", "timeout_seconds": 5}'

# macOS: "afplay" / ["/System/Library/Sounds/Glass.aiff"]
# Linux: "sh" / ["-c", "echo -e '\\a'"]

opal event rules create --json '{
  "id": "siren", "enabled": true, "topic": "alarm.test",
  "conditions": [], "actions": ["honk"]}'

opal event rules test siren --event-json '{"topic": "alarm.test", "data": {}}'   # dry-run
opal event publish alarm.test --data-json '{"severity": "high"}'                 # beep
opal event executions list                                                     # succeeded
```

---

## 3. Keep a durable journal with the event log

Events persist to SQLite — you get a write-ahead log for free.

```bash
opal event publish journal.entry --data-json '{"day": 1, "note": "started"}'
opal event publish journal.entry --data-json '{"day": 2, "note": "progress"}'
opal event list --limit 10
opal event topics                  # journal.entry shows up
opal event get <event_id>   # full envelope with metadata
```

---

## 4. Route events by payload value

Rule conditions AND together — gate an action on a threshold.

```bash
opal event actions create --json '{
  "id": "log_match", "type": "python", "enabled": true,
  "module": "opal_event.plugins.example", "function": "handle_event",
  "timeout_seconds": 5}'

opal event rules create --json '{
  "id": "high_roller", "enabled": true, "topic": "dice.roll",
  "conditions": [{"path": "data.score", "operator": "gte", "value": 1000}],
  "actions": ["log_match"]}'

opal event publish dice.roll --data-json '{"score": 999}'   # nothing fires
opal event publish dice.roll --data-json '{"score": 1042}'  # action runs
opal event executions get <execution_id>                    # plugin stdout
```

---

## 5. Fan out one event to two actions

All actions on a matched rule dispatch independently — separate workers,
separate timeouts, separate records.

```bash
opal event rules create --json '{
  "id": "fanout", "enabled": true, "topic": "demo.quote",
  "conditions": [], "actions": ["honk", "log_match"]}'

opal event publish demo.quote --data-json '{"line": "hello twice"}'
opal event executions list --limit 5     # TWO executions from ONE event
```

---

## 6. Prove that unmatched events still get stored

Best-effort means observe-and-store, not fire-and-forget.

```bash
opal event stats                              # note observed_events / matched_events
opal event publish unhandled.topic --data-json '{}'
opal event stats                              # observed +1, matched unchanged
opal event list | head                 # still persisted to history
```

---

## 7. Disable a noisy rule without restarting

Rules hot-reload — silence a rule in place, re-enable it later.

```bash
opal event rules update siren --json '{"enabled": false}'
opal event publish alarm.test --data-json '{}'   # nothing happens
opal event rules update siren --json '{"enabled": true}'
```

The CLI auto-fetches `If-Match` — optimistic concurrency, zero paperwork.

---

## 8. Mine the execution audit trail

Every dispatch — matched, manual, or dropped — writes a record with stdout,
exit code, timing, and an error classification.

```bash
opal event publish demo.audit --data-json '{"note": "watch me"}'
opal event executions list --status succeeded
opal event executions list --status dropped     # overflow/validation graveyard
opal event executions get <id>                  # stdout, stderr, exit_code, timings
```

---

## 9. Webhook to a local listener

The `http` action POSTs templated JSON to allowlisted destinations. The shipped
config allowlists `http://127.0.0.1:9000` — run a receiver there:

```bash
python -c "
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        print('RECEIVER:', body.decode()); self.send_response(200); self.end_headers()
HTTPServer(('127.0.0.1', 9000), H).serve_forever()
" &

opal event actions create --json '{
  "id": "notify", "type": "http", "enabled": true, "method": "POST",
  "url": "http://127.0.0.1:9000/notify", "body_mode": "template",
  "body": {"ship": "{{data.ship}}", "status": "spotted"},
  "timeout_seconds": 10}'

opal event rules create --json '{
  "id": "contact", "enabled": true, "topic": "radar.contact",
  "conditions": [{"path": "data.ship", "operator": "exists", "value": true}],
  "actions": ["notify"]}'

opal event publish radar.contact --data-json '{"ship": "Heart of Gold"}'
# RECEIVER: {"ship": "Heart of Gold", "status": "spotted"}
```

`{{data.ship}}` substitutes into the body — the allowlist is enforced after
DNS resolution and redirects are refused, so templating can't smuggle traffic
to a different host.

---

## 10. Trigger an action manually

`actions execute` runs a registered action on a synthetic event — for testing
and re-runs.

```bash
opal event actions execute log_match \
  --event-json '{"topic": "manual.test", "data": {"object": "widget"}}'
opal event executions list --limit 5            # trigger=manual, auditable
```

---

## 11. Hit the payload ceiling

`max_event_bytes` is a hard bound at both edges — try to exceed it:

```bash
python -c "print('{\"padding\": \"' + 'x'*1100000 + '\"}')" > /tmp/big.json
opal event publish oversized.event --data-json "$(cat /tmp/big.json)"
# Error: HTTP 413 payload_too_large
opal event stats     # invalid_events untouched — it never got in
```

---

## 12. Read and write settings key by key

```bash
opal event settings get                            # whole portable map + ETag
opal event settings get-key execution.queue_size   # value + default + is_default
opal event settings set-key execution.queue_size --value 200
opal event settings reset-key execution.queue_size # back to schema default
```

Every write is revision-guarded; a stale `If-Match` returns 412.

---

## 13. Round-trip your configuration

Export excludes secrets and history; validation exits 1 on failure — handy in
pre-commit hooks and CI.

```bash
opal event backup export --output snapshot.json
opal event backup validate --file snapshot.json
opal event backup import --file snapshot.json    # atomic, If-Match guarded
```

---

## 14. Write your own Python plugin

Plugins get the event as JSON on stdin and return JSON on stdout. Any importable
module works:

```bash
cat > myplugin.py <<'PLUGIN'
def handle_event(event):
    d = event.get("data") or {}
    return {"echo": d.get("phrase", "")}
PLUGIN
PYTHONPATH="$PWD" opal-event-svc   # restart so the module is importable
```

```bash
opal event actions create --json '{
  "id": "echo", "type": "python", "enabled": true,
  "module": "myplugin", "function": "handle_event", "timeout_seconds": 5}'

opal event actions execute echo \
  --event-json '{"topic": "test.echo", "data": {"phrase": "it works"}}'
opal event executions get <id>    # stdout holds {"echo": "it works"}
```

---

## 15. Publish with an explicit ID and timestamp

Caller-supplied `id`/`timestamp` are honored; history filters understand full
RFC3339 with offsets and fractional seconds:

```bash
opal event publish flashback --id "42" --timestamp "2020-01-01T11:00:00Z" \
  --source "backfill" --data-json '{"batch": "january"}'
opal event list --from "2020-01-01T00:00:00Z" --to "2020-12-31T23:59:59Z"
```

Canonical UTC-microsecond comparisons underneath — every format lands right.

---

## 16. Learn the wildcard the hard way (in a dry-run)

`vision.*` matches `vision.front` and deeper — NOT `visionary`, NOT bare
`vision`. `rules test` proves it without executing anything:

```bash
opal event rules create --json '{
  "id": "fjords", "enabled": true, "topic": "coastline.*",
  "conditions": [{"path": "data.crinkle", "operator": "gt", "value": 7}],
  "actions": ["log_match"]}'

opal event rules test fjords --event-json '{"topic": "coastline.norway", "data": {"crinkle": 9}}'   # matched
opal event rules test fjords --event-json '{"topic": "coastlineX", "data": {"crinkle": 9}}'         # not matched
opal event rules test fjords --event-json '{"topic": "coastline", "data": {"crinkle": 9}}'          # not matched
```

---

## 17. Publish straight onto the ZeroMQ wire

REST is one door; producers can also connect directly to `:5555` — two frames,
topic + JSON:

```bash
python -c "
import zmq, json, time
p = zmq.Context().socket(zmq.PUB)
p.connect('tcp://127.0.0.1:5555')   # producer side
time.sleep(0.3)                     # slow-joiner: let the handshake land
p.send_multipart([b'demo.direct', json.dumps({'data': {'route': 'wire'}}).encode()])
time.sleep(0.2)
"
opal event list --topic demo.direct   # persisted like any other event
```

The internal observer validates independently — publish `source` > 128 chars
and watch `invalid_events` climb in `stats` while the broker keeps running.

---

## 18. Replay an event from history

History is a source, not a museum:

```bash
opal event list --topic alarm.test --limit 1   # find the event
opal event get <event_id>                       # copy its data field
opal event publish alarm.test --data-json '{"severity": "high", "replay": true}'
```

New envelope, new execution, same payload.

---

## 19. Generate a dropped execution on purpose

Rule-triggered dispatch validates `event.data` against the action's
`input_schema` first — failures become `dropped` records with a clear
`error_code`, never a silent skip:

```bash
opal event actions create --json '{
  "id": "picky", "type": "python", "enabled": true,
  "module": "opal_event.plugins.example", "function": "handle_event",
  "input_schema": {"type": "object", "required": ["sarcasm"]},
  "timeout_seconds": 5}'

opal event rules create --json '{
  "id": "sarcasm_gate", "enabled": true, "topic": "tone.check",
  "conditions": [], "actions": ["picky"]}'

opal event publish tone.check --data-json '{"enthusiasm": "high"}'
opal event executions list --status dropped
# error_code: input_validation_failed
```

---

## 20. The whole circuit in sixty seconds

```bash
opal event rules test high_roller \
  --event-json '{"topic": "dice.roll", "data": {"score": 5000}}'          # dry-run
opal event publish dice.roll --data-json '{"score": 5000}'              # live fire
opal event executions list                                                # audit
opal event stats                                                          # counters
opal event list --topic dice.roll                                  # storage
opal event backup export --output checkpoint.json                         # insurance
```

Dry-run → publish → dispatch → audit → persist → export. Six commands,
complete lifecycle.

---

### Quick command map

| You want | Command |
|---|---|
| Publish / browse events | `opal event publish`, `events list/get`, `topics` |
| Rules | `rules list/create/get/test/update/delete` |
| Actions | `actions list/create/get/update/delete/execute` |
| Executions | `executions list/get` |
| Settings | `settings get/update/get-key/set-key/reset-key` |
| Backup | `backup export/validate/import` |
| Health / metrics | `health [--live|--ready]`, `stats` |
| Wire | PUB → `:5555`, SUB ← `:5556` (topic frame + JSON frame) |
