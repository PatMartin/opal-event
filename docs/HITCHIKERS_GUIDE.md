# The Hitchhiker's Guide to opal-event

> **DON'T PANIC.** Everything below works with *only* `opal-event` installed —
> no message broker infra, no Docker, no databases to babysit. Just the service,
> the CLI, Python, and `pyzmq` (which opal-event already pulls in).
>
> Start the service once and leave it running:
>
> ```bash
> opal-event-svc            # config/opal_event.yaml → REST :8080, ZMQ :5555/:5556
> ```
>
> In a second terminal, everything below works.

---

## 1. Hello, Galaxy — tail the whole event bus

The ZeroMQ broker is a transparent XSUB/XPUB forwarder. Attach a subscriber in
five lines and watch every event the service sees:

```bash
python -c "
import zmq
s = zmq.Context().socket(zmq.SUB)
s.connect('tcp://127.0.0.1:5556')     # consumer side
s.subscribe('')                        # empty prefix = EVERYTHING
print('listening for transmissions…')
while True:
    topic, payload = s.recv_multipart()
    print(topic.decode(), '→', payload.decode())
"
```

Then in another terminal:

```bash
opal event publish hitchhiker.arrival --data-json '{"who": "Ford Prefect", "towel": true}'
```

You'll see the two-frame message fly by: topic frame + JSON envelope. This is the
single most useful debugging move in the whole guide.

---

## 2. The Vogon Alarm — a rule that makes noise

Rules watch topics and fire actions. Let's wire `alarm.vogon` to a beep.

Create the action (writes a subprocess that actually makes noise):

```bash
# Windows
opal event actions create --json '{
  "id": "honk", "type": "shell", "enabled": true, "mode": "exec",
  "executable": "powershell",
  "args": ["-c", "[console]::beep(880,300); [console]::beep(660,300)"],
  "stdin": "none", "timeout_seconds": 5}'

# macOS: replace executable/args with "afplay" / ["/System/Library/Sounds/Glass.aiff"]
# Linux: "sh" / ["-c", "echo -e '\\a'"]
```

> **Re-running examples?** `create` rejects a duplicate `id` with `409 conflict` —
> either pick a new id, `opal event actions delete honk` first, or use
> `actions update honk --json '{...}'` to change it in place. Same for rules.

Now the rule:

```bash
opal event rules create --json '{
  "id": "vogon_siren", "enabled": true,
  "topic": "alarm.vogon",
  "conditions": [],
  "actions": ["honk"]}'
```

Test it dry first — `rules test` evaluates without executing:

```bash
opal event rules test vogon_siren --event-json '{"topic": "alarm.vogon", "data": {}}'
```

Fire it for real and listen to your machine panic:

```bash
opal event publish alarm.vogon --data-json '{"captain": "Prostetnic Vogon Jeltz"}'
opal event executions list          # status: succeeded
```

---

## 3. Ford Prefect's Field Journal — event-sourced notes

The service persists every event to SQLite (when `persistence.event_mode` allows).
Publish your field notes, then page through the history:

```bash
opal event publish journal.entry --data-json '{"day": 1, "note": "Earthman is adapting well"}'
opal event publish journal.entry --data-json '{"day": 2, "note": "taught him about towels"}'
opal event events list --limit 10
opal event topics                  # journal.entry appears
opal event events get <event_id>   # full envelope, metadata and all
```

Congratulations, you built a durable write-ahead log with three commands.

---

## 4. The Infinite Improbability Drive — condition routing

Rules AND their conditions. Route events by payload value:

```bash
opal event actions create --json '{
  "id": "log_improbable", "type": "python", "enabled": true,
  "module": "opal_event.plugins.example", "function": "handle_event",
  "timeout_seconds": 5}'

opal event rules create --json '{
  "id": "improbability_gate", "enabled": true, "topic": "dice.roll",
  "conditions": [{"path": "data.p_improbability", "operator": "gte", "value": 1000}],
  "actions": ["log_improbable"]}'
```

```bash
opal event publish dice.roll --data-json '{"p_improbability": 999}'   # nothing fires
opal event publish dice.roll --data-json '{"p_improbability": 1042}'  # action runs
opal event executions list           # exactly one execution
```

The plugin returns `{"received_topic": ..., "object": data.object}` — check its
stdout in the execution detail:

```bash
opal event executions get <execution_id>
```

---

## 5. Zaphod's Two-Headed Fan-Out — one rule, two actions

Actions on a matched rule dispatch independently. Two heads, two outputs:

```bash
opal event rules create --json '{
  "id": "two_heads", "enabled": true, "topic": "zaphod.quote",
  "conditions": [],
  "actions": ["honk", "log_improbable"]}'

opal event publish zaphod.quote --data-json '{"line": "Hey, you sass that hoopy Ford Prefect?"}'
opal event executions list --limit 5     # TWO execution records, one event
```

Each action ran in its own worker slot, independently timed out, independently
audited. Neither knows the other exists — very on-brand for Zaphod.

---

## 6. The S.E.P. Field — watch events vanish politely

An event matching no rule is still observed and (with `event_mode=all`) stored —
but dispatches nothing. Make the effect visible:

```bash
opal event stats                              # note observed_events / matched_events
opal event publish somebody.elses.problem --data-json '{}'
opal event stats                              # observed +1, matched unchanged
opal event events list | head                 # …but it IS in history
```

The `Somebody Else's Problem` field renders events invisible to actions while
keeping them perfectly visible to your event log.

---

## 7. Kill the Dolphins — live rule surgery, no restart

Rules hot-reload. Silence a noisy rule without restarting anything:

```bash
opal event rules update vogon_siren --json '{"enabled": false}'
opal event publish alarm.vogon --data-json '{}'   # silence. blessed silence.
opal event rules update vogon_siren --json '{"enabled": true}'
```

`rules update` auto-fetches the `If-Match` revision for you — optimistic
concurrency without the paperwork.

---

## 8. Marvin's Complaint Line — the execution audit trail

Every action run — dispatched, manual, or dropped — leaves an execution record
with stdout, exit code, timing, and error classification:

```bash
opal event publish marvin.complaint --data-json '{"brain_size": "planet-sized"}'
opal event executions list --status succeeded
opal event executions list --status dropped     # the queue-overflow graveyard
opal event executions get <id>                  # stdout, stderr, exit_code, timings
```

Marvin has a brain the size of a planet and they ask him to grep the execution
log. He could do it with his eyes shut. Both of them.

---

## 9. Hotline to Mission Control — the HTTP action

The http action POSTs templated JSON to allowlisted destinations only. The
example config allowlists `http://127.0.0.1:9000` — so run Mission Control there:

```bash
python -c "
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        print('MISSION CONTROL:', body.decode()); self.send_response(200); self.end_headers()
HTTPServer(('127.0.0.1', 9000), H).serve_forever()
" &
```

```bash
opal event actions create --json '{
  "id": "notify_hq", "type": "http", "enabled": true, "method": "POST",
  "url": "http://127.0.0.1:9000/notify", "body_mode": "template",
  "body": {"ship": "{{data.ship}}", "status": "improbable"},
  "timeout_seconds": 10}'

opal event rules create --json '{
  "id": "ship_spotted", "enabled": true, "topic": "radar.contact",
  "conditions": [{"path": "data.ship", "operator": "exists", "value": true}],
  "actions": ["notify_hq"]}'

opal event publish radar.contact --data-json '{"ship": "Heart of Gold"}'
# Mission Control terminal prints: {"ship": "Heart of Gold", "status": "improbable"}
```

`{{data.ship}}` substituted into the JSON body — templating without template
injection (allowlist is enforced *after* DNS resolution, redirects are refused).

---

## 10. The Big Red Button — manual execution

Run a registered action on demand with a synthetic event — for testing, for
re-runs, for pressing buttons labeled DO NOT PRESS:

```bash
opal event actions execute log_improbable \
  --event-json '{"topic": "manual.test", "data": {"object": "towel"}}'
opal event executions list --limit 5            # trigger=manual, auditable
```

---

## 11. The 1 MiB Towel — payload bounds

`max_event_bytes` is a hard contract boundary. Exceed it and get a crisp 413:

```bash
python -c "print('{\"padding\": \"' + 'x'*1100000 + '\"}')" > /tmp/big.json
opal event publish towel.oversized --data-json "$(cat /tmp/big.json)"
# Error: [413] payload_too_large — the towel is simply too large for the envelope
opal event stats     # invalid_events untouched; it never even got in the door
```

The same bound applies on the wire (ZMQ frame sum) and at the REST edge.

---

## 12. Deep Thought's Memory Palace — settings as a service

Portable settings are queryable AND writable live, key by key:

```bash
opal event settings get                            # the whole portable map + ETag
opal event settings get-key execution.queue_size   # one key, default, is_default
opal event settings set-key execution.queue_size --value 200
opal event settings get-key execution.queue_size   # is_default: false
opal event settings reset-key execution.queue_size # back to schema default 100
```

Every mutation is ETag-guarded — two writers racing means one gets a 412 and a
story to tell.

---

## 13. Vogsphere Customs — backup, validate, import

Export the whole portable configuration (secrets and history never travel):

```bash
opal event backup export --output snapshot.json
python -c "import json; d=json.load(open('snapshot.json')); print(sorted(d))"
# ['actions', 'exported_at', 'format_version', 'rules', 'settings', 'version']
```

Edit it, dry-run it, commit it:

```bash
opal event backup validate --file snapshot.json     # schema + semantic checks
opal event backup import --file snapshot.json       # atomic apply, If-Match guarded
```

Validation failures exit 1 — perfect for pre-commit hooks and CI gates.

---

## 14. The Babel Fish — write a real Python plugin

Python plugins get the event as JSON on stdin and return JSON on stdout. Drop a
file next to your config and register it — the module only needs to be
importable by the service interpreter:

```bash
cat > babel.py <<'EOF'
def handle_event(event):
    d = event.get("data") or {}
    return {"translated": f"[in your own language]: {d.get('phrase', '')}"}
EOF
PYTHONPATH="$PWD" opal-event-svc   # restart with your plugin dir on the path
```

```bash
opal event actions create --json '{
  "id": "translate", "type": "python", "enabled": true,
  "module": "babel", "function": "handle_event", "timeout_seconds": 5}'

opal event actions execute translate \
  --event-json '{"topic": "fish.speak", "data": {"phrase": "so long, and thanks"}}'
opal event executions get <id>    # stdout holds the JSON result
```

---

## 15. Time Traveler's Receipts — explicit IDs and timestamps

The envelope honors caller-provided `id` and `timestamp` — and history filters
understand real RFC3339 with offsets:

```bash
opal event publish flashback --id "42" --timestamp "1979-10-12T11:00:00Z" \
  --source "the-guide" --data-json '{"era": "BBC Radio 4"}'
opal event events list --from "1979-01-01T00:00:00Z" --to "1980-01-01T00:00:00Z"
```

Canonical UTC-microsecond comparisons underneath — offset and fractional-second
formats all land correctly. Your event from 1979 stays findable.

---

## 16. Slartibartfast's Quality Gate — the suffix wildcard

`vision.*` matches `vision.front_door` and `vision.front_door.left`, but NOT
`visionary` and NOT bare `vision`. Prove it:

```bash
opal event rules create --json '{
  "id": "fjord_inspector", "enabled": true, "topic": "coastline.*",
  "conditions": [{"path": "data.crinkle", "operator": "gt", "value": 7}],
  "actions": ["log_improbable"]}'

opal event publish coastline.norway --data-json '{"crinkle": 9}'    # matches
opal event publish coastlineX --data-json '{"crinkle": 9}'          # not even close
opal event publish coastline --data-json '{"crinkle": 9}'           # bare parent: no
```

Award yourself a fjord. You earned it.

---

## 17. The Pan Galactic Pub — publish straight onto the wire

REST is one door; the broker accepts producers directly at `:5555` — two frames,
topic + JSON:

```bash
python -c "
import zmq, json, time
p = zmq.Context().socket(zmq.PUB)
p.connect('tcp://127.0.0.1:5555')
time.sleep(0.3)   # slow-joiner: give the handshake a beat
p.send_multipart([b'pub.gargle_blaster', json.dumps({'data': {'slices': 1}}).encode()])
time.sleep(0.2)
"
opal event events list --topic pub.gargle_blaster   # stored like any other event
```

The internal observer validates wire events independently — publish garbage
(`source` > 128 chars, say) and watch `invalid_events` tick up in `stats` while
the broker stays up. Best-effort means exactly that.

---

## 18. The Total Perspective Vortex — replay an event

Pull a stored event and re-fire it through the rules engine:

```bash
opal event events list --topic alarm.vogon --limit 1   # find it
opal event events get <event_id>                        # copy the data field
opal event publish alarm.vogon --data-json '{"captain": "Prostetnic Vogon Jeltz", "replay": true}'
```

New envelope, new execution, same payload — history is a source, not a museum.

---

## 19. Mostly Harmless — the drop graveyard

Overflow and validation failures don't disappear — they become `dropped`
executions with an `error_code`. Force one: give an action an `input_schema`
that rejects, then publish:

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
# error_code: input_validation_failed — Marvin approves of the rejection
```

---

## 20. Share and Enjoy — the complete circuit in 60 seconds

The whole lifecycle, end to end, in one paste:

```bash
opal event rules test improbability_gate \
  --event-json '{"topic": "dice.roll", "data": {"p_improbability": 5000}}'  # dry-run
opal event publish dice.roll --data-json '{"p_improbability": 5000}'      # live fire
opal event executions list                                                # audit
opal event stats                                                          # counters
opal event events list --topic dice.roll                                  # storage
opal event backup export --output before_the_guide_vanishes.json          # insurance
```

Dry-run → publish → dispatch → audit → persist → export. Six commands, one
mostly-harmless event bus.

---

### Appendix: where each piece lives

| You want | Command |
|---|---|
| Service | `opal-event-svc [--config PATH]` |
| Publish / browse events | `opal event publish`, `events list/get`, `topics` |
| Rules | `rules list/create/get/test/update/delete` |
| Actions | `actions list/create/get/update/delete/execute` |
| Executions | `executions list/get` |
| Settings | `settings get/update/get-key/set-key/reset-key` |
| Backup | `backup export/validate/import` |
| Wire | PUB → `:5555`, SUB ← `:5556` (topic frame + JSON frame) |

So long, and thanks for all the events.
