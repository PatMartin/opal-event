# OPAL Event Cookbook

*The nervous system recipes: twenty things OPAL Event does alone, and twenty more it
does holding hands with Listen, Look, Say, Think, Device, and friends.*

Every snippet lives in its own fenced `bash` block, so documentation renderers that
offer a copy control give you a **one-click copy** of the whole command. Comments
above each recipe tell you what it does and why. Where a recipe says `<id>` or
`<exec-id>`, paste the value a previous command printed.

**Setup for every recipe:** start the service once, then open a second terminal in
this checkout.

```bash
# Terminal 1 — the service (REST :8080, ZMQ producer :5555, consumer :5556)
opal-event-svc --config config/opal_event.yaml

# …or let the CLI manage it in the background:
opal event service start
```

```bash
# Terminal 2 — prove it's alive before anything else
opal event info health --ready
```

> **Ports assumed below:** Event `8080`, Listen `8000`, Say `8001`, Look `8002`,
> Think `8083` (its default `8080` collides with Event — move it with
> `OPAL_THINK_PORT`), Device `8084`. Each CLI honors its own URL env var
> (`OPAL_EVENT_API_URL`, `OPAL_LISTEN_API_URL`, `OPAL_SAY_API_URL`,
> `OPAL_LOOK_URL`, `OPAL_THINK_API_URL`, `OPAL_DEVICE_URL`).

---

## Contents

- [Introduction](#introduction)
- [CLI Command Tree](#cli-command-tree)
- [Recipes](#recipes)
  - [Local recipes — 20+ one-liners with nothing but opal-event](#local-recipes--20-one-liners-with-nothing-but-opal-event)
  - [Combined — 20 recipes across two or more OPAL services](#combined--20-recipes-across-two-or-more-opal-services)

## Introduction

[OPAL](https://github.com/PatMartin/opal) is a suite of small, single-responsibility
local services. Each one owns exactly one faculty; none reaches inside another.

| Service | Faculty | What it does |
| --- | --- | --- |
| **Listen** (`opal listen`) | Hearing | Wakewords, speech-to-text, intent and slot recognition. Publishes `opal.listen.intent.match` onto the event bus when `event_publication_enabled` is on. |
| **Look** (`opal look`) | Sight | Engines, models, single-image predict, and ephemeral RTSP streams whose per-stream SSE carries detections. |
| **Say** (`opal say`) | Voice | Text-to-speech over host engines (Piper, Kokoro). Returns audio bytes; `--play` renders locally. |
| **Think** (`opal think`) | Reasoning | Bounded async tasks on configured models. Can discover and invoke Event actions over REST (`event.base_url`). |
| **Device** (`opal-device`) | Touch | Device registry, groups, federation, its own `/event/` history + SSE. |
| **Event** (`opal event`) | **Nervous system** | This module. Moves events between services, matches rules, runs actions. |

OPAL Event is the bridge between *noticing* and *doing*:

```text
opal-look sees a person ───────┐
opal-listen hears an intent ───┼──▶ OPAL EVENT ──▶ rule match ──▶ action
opal-device reports a change ──┘        │                            │
   sensors & scripts publish ───────────┘                            │
        ├── ZeroMQ bus (XSUB :5555 / XPUB :5556)                     ├── shell command
        ├── REST ingress  POST /api/v1/event/                        ├── HTTP webhook (allowlisted)
        ├── SQLite history (events + executions)                     └── Python plugin
        └── CLI / SPA / Swagger on :8080
```

Three building blocks, three verbs:

- **Event** — "something happened": a `topic` plus JSON `data` (optional `id`, `source`, `timestamp`).
- **Rule** — "when this happens": exact topic or `prefix.*` wildcard, AND-ed field conditions.
- **Action** — "do this": `shell` (argv exec), `http` (allowlisted webhook), or `python` (importable plugin).

Best effort, honestly: `202 Accepted` means the event was validated and handed to the
bus — not that every subscriber saw it or that an action succeeded. The **executions**
log is where you check what actually ran.

## CLI Command Tree

Each checkout installs its own `opal` entry point that mounts the sibling CLIs it can
import, plus a standalone alias (`opal-event`, `opal-say`, `opal-look`, `opal-think`,
`opal-device`, `opal-yolo`). In a shared venv the tree below is the union; in a single
checkout you get that repo's slice. `opal <suite> tree` prints the live tree anywhere
it exists.

```text
opal
├── service                     (the opal orchestrator checkout)
│   └── list | show | update | start | stop | restart | status | logs
│
├── listen                      hearing — opal-listen (svc: opal-listen-svc, :8000)
│   ├── wakeword                list | get | add | update | remove
│   ├── intent                  list | get | add | update | remove
│   │                           phrases | add-phrase | remove-phrase
│   ├── slot                    list | get | add | update | remove
│   │                           values | add-value | remove-value
│   │                           aliases | add-alias | remove-alias
│   ├── profile                 list | get | add | update | activate | remove
│   ├── persona                 list | get | add | update | remove | validate
│   ├── setting                 get | unset | …
│   ├── test                    text | audio | listen | listen-debug | regression
│   ├── recognition             test
│   ├── pipeline                start | stop | status
│   ├── failed                  list | get | add | update | …   (failed utterances)
│   ├── regression              run | …
│   ├── reaction                path | list | get | add | update | remove
│   │                           enable | disable | test | log   (local reactions.yaml)
│   ├── config                  setting … | validate | backup | backups | restore
│   │                           download | delete-backup | export | import
│   ├── service                 doctor | devices | info | ping | live | ready
│   │                           start | stop | log | perf last|summary|history
│   ├── event                   publish        (write straight onto the ZMQ bus)
│   ├── gui | tree
│
├── look                        sight — opal-look (svc: opal serve, :8002)
│   ├── engine                  list | show | discover | capabilities
│   ├── model                   list | show | load | unload
│   ├── predict                 image
│   ├── hardware                list | show
│   ├── stream                  start | list | show | stop | watch
│   │                           watch-video | snapshot | metrics
│   ├── info                    health | live | ready | version | ping
│   ├── config                  engine update | profile … | setting …
│   ├── backup                  list | create | download | restore | remove
│   │                           export | import | validate
│   ├── service                 start | stop | log
│   └── tree
│
├── think                       reasoning — opal-think (svc: opal-think-svc, :8080→move)
│   ├── ask | chat              aliases for `task create`
│   ├── task                    create | list | show | watch | cancel
│   ├── session                 add | list | show | remove
│   ├── model                   list | show
│   ├── config                  setting | persona | profile  (CRUD + activate)
│   ├── info                    health | live | ready | version | ping
│   ├── backup                  list | create | download | restore | remove
│   │                           export | validate | import
│   ├── service                 start | stop | log
│   ├── gui | tree
│
├── say                         voice — opal-say (svc: opal-say-svc, :8001)
│   ├── generate                TEXT [--play] [-o FILE] [--stream]
│   │                           [--voice | --persona | --profile …]
│   ├── engine                  list | get | update | validate
│   ├── voice                   list | add | get | update | remove | validate | test
│   ├── persona                 list | add | get | update | validate | remove
│   ├── profile                 list | add | get | update | remove | activate
│   ├── info                    version | health | live | ready | ping
│   ├── config                  setting …
│   ├── backup                  list | create | download | restore | delete
│   │                           export | import | validate
│   ├── service                 start | stop | log
│   └── tree
│
├── event                       nervous system — THIS MODULE
│   │                           (svc: opal-event-svc, REST :8080, ZMQ :5555/:5556)
│   ├── publish TOPIC --data-json JSON   [--id] [--source] [--timestamp]
│   ├── list | get | delete | clear | topics        (stored event history)
│   ├── history                 list | show | remove | clear | topics  (same data)
│   ├── info                    get | ping | health [--live|--ready] | stats
│   ├── rules                   list | create | get | update | delete | test
│   ├── actions                 list | create | get | update | delete | execute
│   ├── executions              list | get
│   ├── settings                get | update | get-key | set-key | reset-key
│   ├── setting                 get | get-key | set-key | reset-key  (flat aliases)
│   ├── config                  setting list | show | set | remove | update
│   ├── profiles                list | create | get | update | delete | activate
│   ├── personas                list | create | get | update | delete | validate
│   ├── backup                  export | validate | import
│   ├── service                 start | stop | log | view
│   ├── log [--lines N] [--follow/--no-follow]
│   └── tree [--ascii]
│
└── talk                        conversation — opal-talk (mounts when installed)

opal-device                     devices/federation — standalone CLI (:8080 default)
├── info | health | catalog | discover | watch      (watch = /event/stream SSE)
├── device     list | add | show | update | remove | capabilities | status | test
│   ├── label                   list | set | remove
│   └── tag                     list | add | remove
├── group      list | add | show | update | remove
│   └── member                  list | add | remove
├── peer       status | sync | discover  (+ CRUD)
├── profile    … | activate
├── setting    list | show | set | update | remove
├── default    list | set | remove
├── sharing    list | add | allow | deny | remove
├── plugin     list | show
├── event      list                    (its own local event history)
├── federation inventory | registry
├── backup     list | create | export | download | restore | import | validate | remove
└── service    start | stop | log

opal-yolo / yolo-look           remote vision engine — opal-look-yolo
├── serve | predict | predict-frame | tree
├── info      show | health
├── engines   list | discover | show | capabilities
├── models    list | show | load | unload
└── hardware  list | show
```

**Global flags for `opal event`** (place right after `event`, before the subcommand):
`--api-url`, `--token`, `--json`, `--timeout`, `--opal-session-id`,
`--opal-transaction-id`, `--opal-causation-id`, `--env-file`, `--no-env-file`.

## Recipes

### Local recipes — 20+ one-liners with nothing but opal-event

The chain below builds on itself: service → publish → inspect → action → rule →
fire → audit → tune → snapshot → stop. Each step needs only `opal-event` installed.

**1. Bring the managed service up in the background** — pidfile + log bookkeeping, returns immediately.

```bash
opal event service start
```

**2. Gate on real readiness** — `--ready` checks broker *and* database, exiting non-zero until both are up; perfect for scripts.

```bash
opal event info health --ready
```

**3. Publish your first event** — `202 Accepted` means validated and handed to the bus.

```bash
opal event publish demo.hello --data-json '{"msg": "the nervous system is awake"}' --source quickstart
```

**4. See it in history** — `persistence.event_mode: all` stores everything observed.

```bash
opal event list --limit 5
```

**5. Pull the full envelope** — id, source, timestamp, data: the whole normalized event.

```bash
opal event get <id>
```

**6. Ask which topics have traffic** — the topic catalog is derived from observed events.

```bash
opal event topics
```

**7. Backfill with an explicit id and timestamp** — caller-supplied metadata is honored; history filters understand full RFC3339.

```bash
opal event publish demo.backfill --id "42" --source backfill --timestamp "2020-01-01T11:00:00Z" --data-json '{"batch": "january"}'
```

**8. Register a harmless action** — `shell`/`exec` mode runs an argv array (no shell string); `{{data.name}}` templates from the event.

```bash
opal event actions create --json '{"id":"greeter","type":"shell","enabled":true,"mode":"exec","executable":"/bin/echo","args":["Hello, {{data.name}}!"],"stdin":"none","timeout_seconds":10}'
```

**9. Wire a topic to that action** — conditions AND together; `exists` gates on presence.

```bash
opal event rules create --json '{"id":"greeting_rule","enabled":true,"topic":"demo.greeting","conditions":[{"path":"data.name","operator":"exists","value":true}],"actions":["greeter"]}'
```

**10. Dry-run the rule first** — `rules test` evaluates the match without executing anything.

```bash
opal event rules test greeting_rule --event-json '{"topic":"demo.greeting","data":{"name":"Ada"}}'
```

**11. Fire for real** — the match schedules `greeter` on a worker.

```bash
opal event publish demo.greeting --data-json '{"name":"Ada"}' --source demo
```

**12. Read the audit trail** — every dispatch writes a record: status, timing, stdout, exit code.

```bash
opal event executions list --rule-id greeting_rule
```

**13. Inspect one execution** — the detail view shows the rendered argv and captured output.

```bash
opal event executions get <exec-id>
```

**14. Invoke an action by hand** — `execute` needs no matching rule; great for testing and re-runs (`trigger=manual` in the record).

```bash
opal event actions execute greeter --event-json '{"topic":"demo.manual","data":{"name":"Grace"}}'
```

**15. Silence a noisy rule without deleting it** — hot reload, CLI auto-fetches the `If-Match` ETag.

```bash
opal event rules update greeting_rule --json '{"enabled": false}'
```

**16. Read one portable setting** — shows the value, the schema default, and whether you're on the default.

```bash
opal event settings get-key execution.queue_size
```

**17. Tune it live** — revision-guarded; a stale precondition returns `412`.

```bash
opal event settings set-key execution.queue_size --value 200
```

**18. Snapshot the whole portable config** — export excludes secrets and history; `validate` exits 1 on a bad bundle, handy in CI.

```bash
opal event backup export --output snapshot.json && opal event backup validate --file snapshot.json
```

**19. Swap operating defaults with a profile** — a profile is a named preset of portable settings; activating it applies the preset atomically and deactivates the others.

```bash
opal event profiles create "Night" --json '{"name":"Night","description":"Small queue, short timeouts","settings":{"execution.queue_size":50}}'
opal event profiles activate 1
```

**20. Register an automation identity** — personas attribute work to a consistent character (and can bind an OPAL Say `voice_id` for spoken output).

```bash
opal event personas create --json '{"persona_id":"nightwatch","name":"Night Watch","enabled":true}'
opal event personas validate nightwatch
```

**21. Ask the counters** — `observed_events`, `matched_events`, `invalid_events`, and friends.

```bash
opal event info stats
```

**22. Tail the entire bus** — the single most useful debugging trick: subscribe to everything.

```bash
python -c "import zmq;s=zmq.Context().socket(zmq.SUB);s.connect('tcp://127.0.0.1:5556');s.subscribe('');print('listening…');[print(s.recv_multipart()) for _ in iter(int,1)]"
```

**23. Publish straight onto the wire** — REST is one door; producers can also PUB to `:5555` (two frames: topic, JSON envelope).

```bash
python -c "import zmq,json,time;p=zmq.Context().socket(zmq.PUB);p.connect('tcp://127.0.0.1:5555');time.sleep(0.3);p.send_multipart([b'demo.direct',json.dumps({'data':{'route':'wire'}}).encode()]);time.sleep(0.2)"
```

**24. Tail the service log without leaving the CLI** — prints the last N lines then follows until Ctrl+C.

```bash
opal event log --lines 30 --no-follow
```

**25. Print the live command tree** — generated from the installed Typer app, so it always matches your version.

```bash
opal event tree
```

**26. Wipe event history (carefully)** — deletes *all* stored events; rules keep running.

```bash
opal event clear
```

### Combined — 20 recipes across two or more OPAL services

Each of these uses `opal event` **plus at least one sibling**. Where an action runs a
sibling CLI, that sibling's console script must be on the service's `PATH` — the
standalone aliases (`opal-say`, `opal-look`, `opal-think`, `opal-device`) are safer
than the shared `opal` root, since each checkout owns its own `opal`. For `http`
actions crossing services, remember the shipped config only allows
`http://127.0.0.1:9000` — extend `security.http_action_allowlist` in
`config/opal_event.yaml` and restart, or prefer `shell` actions, which aren't
allowlisted at all.

**1. The voice doorbell** — Listen hears an intent, Event routes it, Say speaks it. Register a shell action that calls Say's CLI with the event's `text` field.

```bash
opal event actions create --json '{"id":"speak","type":"shell","enabled":true,"mode":"exec","executable":"opal-say","args":["generate","{{data.text}}","--play"],"stdin":"none","timeout_seconds":30}'
opal event rules create --json '{"id":"announce_intents","enabled":true,"topic":"opal.listen.intent.match","conditions":[{"path":"data.intent","operator":"eq","value":"announce"}],"actions":["speak"]}'
opal event publish opal.listen.intent.match --source opal-listen --data-json '{"intent":"announce","text":"Dinner is ready."}'
```

**2. Publish onto the bus from the Listen CLI** — `opal listen event publish` writes a proper envelope (topic frame + JSON frame) straight to the ZMQ endpoint, so it lands in the same history REST-published events do.

```bash
opal listen event publish demo.from.listen '{"heard": "kitchen light"}'
opal event list --topic demo.from.listen
```

**3. Type a command, hear the confirmation** — dry-run intent matching without a microphone (`test text`), then let a rule confirm it out loud.

```bash
opal listen test text "computer, turn on the kitchen light"
opal event publish opal.listen.intent.match --source opal-listen --data-json '{"intent":"light_turn_on","room":"kitchen","text":"Kitchen light on."}'
opal event executions list --rule-id announce_intents
```

**4. Eyes to mouth** — run a single-image prediction in Look, republish the result as a bus event, let the shipped `front_door_alert` rule (or your own) fire the speaker.

```bash
opal look model load synthetic-det --engine synthetic
opal look predict image --engine synthetic --model synthetic-det --source porch.jpg
opal event publish vision.object.detected --source opal-look --data-json '{"object":"person","camera":"front_door","confidence":0.94}'
opal event executions list --limit 5
```

**5. Republish a live Look stream onto the bus** — Look streams emit SSE, not bus topics. This tiny bridge turns `stream watch` detections into `vision.stream.detection` events any rule can consume.

```bash
opal look stream watch str-42 | python -c "import sys,json,subprocess;[subprocess.run(['opal','event','publish','vision.stream.detection','--data-json',l[5:]]) for l in sys.stdin if l.startswith('data:')]"
```

**6. Snapshot on demand** — an event becomes a camera shutter: the action grabs a frame from a running Look stream named in the event data.

```bash
opal event actions create --json '{"id":"snap","type":"shell","enabled":true,"mode":"exec","executable":"opal-look","args":["stream","snapshot","{{data.stream}}","--out","{{data.out}}"],"stdin":"none","timeout_seconds":15}'
opal event publish camera.snapshot.request --data-json '{"stream":"str-42","out":"doorcam.jpg"}'
```

**7. Kick off a Think task when an event fires** — the action hands `data.prompt` to Think and detaches so the event bus never blocks on the model.

```bash
opal event actions create --json '{"id":"think_it","type":"shell","enabled":true,"mode":"exec","executable":"opal-think","args":["task","create","--prompt","{{data.prompt}}","--detach"],"stdin":"none","timeout_seconds":15}'
opal event rules create --json '{"id":"ask_the_brain","enabled":true,"topic":"think.request","conditions":[],"actions":["think_it"]}'
opal event publish think.request --data-json '{"prompt":"Summarize the last deploy in one sentence."}'
opal think task list --limit 5
```

**8. Let Think call Event back** — point Think at the bus (`event.base_url`); it discovers `GET /api/v1/action/` and can invoke your registered actions. Three services in one circuit: Think → Event → Say. (Uses the `speak` action from recipe 1.)

```bash
opal think config setting set event.base_url '"http://127.0.0.1:8080"'
opal think ask "Use the 'speak' action to say 'the circuit is complete'." 
opal event executions list --action-id speak
```

**9. An HTTP action straight into Think's REST API** — no CLI hop: extend the allowlist to include `127.0.0.1:8083`, restart, then template the task body. Types survive when `{{…}}` is the whole string.

```bash
opal event actions create --json '{"id":"think_http","type":"http","enabled":true,"method":"POST","url":"http://127.0.0.1:8083/api/v1/task/","body_mode":"template","body":{"prompt":"{{data.prompt}}","max_steps":4},"timeout_seconds":15}'
opal event actions execute think_http --event-json '{"topic":"think.http","data":{"prompt":"Draft a haiku about event buses."}}'
```

**10. Person at the front door** — the flagship chain: Look (or a simulation) detects, the preconfigured `front_door_alert` rule matches `vision.*` + `object == person`, and Say announces it — add `speak` to the rule's action list to hear it.

```bash
opal event rules test front_door_alert --event-json '{"topic":"vision.object.detected","data":{"object":"person","camera":"front_door","confidence":0.94}}'
opal event publish vision.object.detected --source opal-look --data-json '{"object":"person","camera":"front_door","confidence":0.94}'
opal event executions list --rule-id front_door_alert
```

**11. Fan out one alarm to three outputs** — a single `alarm.panic` event triggers the local beeper, a spoken warning through Say, and the HTTP notifier — three independent executions from one publish.

```bash
opal event rules create --json '{"id":"panic","enabled":true,"topic":"alarm.panic","conditions":[],"actions":["honk","speak","send_notification"]}'
opal event publish alarm.panic --data-json '{"text":"All hands on deck.","camera":"garage"}'
opal event executions list --rule-id panic   # three records, one event
```

**12. The daily digest** — Event keeps the audit, Think summarizes it, Say reads it over the intercom. Ears, memory, and mouth in one pipe.

```bash
opal event --json list --limit 50 > /tmp/today.json
opal think ask "Summarize these events in two sentences: $(cat /tmp/today.json)"
opal event publish digest.ready --data-json '{"text":"Here is the daily event summary."}'
```

**13. Failed-execution watchdog** — poll the failure list, republish each new failure as `ops.exec.failed`, and let a notification rule alert on it. The bus audits itself.

```bash
opal event --json executions list --status failed | python -c "import sys,json,subprocess;[subprocess.run(['opal','event','publish','ops.exec.failed','--data-json',json.dumps(e)]) for e in json.load(sys.stdin)]"
opal event list --topic ops.exec.failed
```

**14. Device events join the bus** — opal-device keeps its own `/event/stream` SSE. Bridge it once and every device change becomes routable through Event rules.

```bash
opal-device watch | python -c "import sys,json,subprocess;[subprocess.run(['opal','event','publish','device.changed','--data-json',l[5:]]) for l in sys.stdin if l.startswith('data:')]"
opal event list --topic device.changed
```

**15. The whole-suite backup sweep** — every service speaks the same kernel backup verbs; archive them side by side before a config spree.

```bash
opal event backup export --output backup-event.json
opal say backup export --output backup-say.json
opal look backup export > backup-look.json        # look/think/device print JSON to stdout
opal think backup export > backup-think.json
opal-device backup export > backup-device.json
```

**16. Fleet health gate** — chain readiness probes; only publish `suite.online` when every service answers. Drop this in a boot script.

```bash
opal event info health --ready && opal say info ready && opal look info ready && opal think info ready && opal event publish suite.online --data-json '{"at":"boot"}'
```

**17. Quiet hours, event-driven** — a scheduled `house.quiet.start` event flips Say to its hushed profile and disables the noisy rule; `house.quiet.end` puts everything back. The bus manages itself.

```bash
opal event actions create --json '{"id":"hush","type":"shell","enabled":true,"mode":"exec","executable":"opal-say","args":["profile","activate","2"],"stdin":"none","timeout_seconds":10}'
opal event rules create --json '{"id":"quiet_hours","enabled":true,"topic":"house.quiet.start","conditions":[],"actions":["hush"]}'
opal event publish house.quiet.start --data-json '{}'
opal event rules update panic --json '{"enabled": false}'
```

**18. Trace one transaction across the chain** — the correlation headers are first-class citizens: publish with a session id, and the same value rides the action's outbound calls.

```bash
opal event --opal-session-id demo-ses-1 --opal-transaction-id txn-42 publish demo.traced --data-json '{"hop":1}'
opal event executions list --event-id <id>
```

**19. A persona speaks for the bus** — a persona is a named automation identity that can bind an OPAL Say `voice_id`, so attributed work sounds like one consistent character. Register `herald` on Event's side, then let Say resolve the same name.

```bash
opal event personas create --json '{"persona_id":"herald","name":"Herald","enabled":true,"voice_id":"en-us-amy-low","description":"Bus announcer"}'
opal event personas validate herald
opal say generate "This is Herald, reporting from the event bus." --persona herald --play
```

**20. The regression finish line** — run Listen's test suite, publish the verdict, and let a rule archive a config snapshot on success. Your CI's last mile, spoken.

```bash
opal listen test regression
opal event publish ci.regression.done --source opal-listen --data-json '{"suite":"intents","passed":true,"text":"All intent tests green."}'
opal event executions list --rule-id announce_intents
```

---

### Quick map

| You want | Command |
|---|---|
| Publish / browse events | `publish`, `list`, `get`, `topics` |
| Rules | `rules list/create/get/update/delete/test` |
| Actions | `actions list/create/get/update/delete/execute` |
| Executions | `executions list/get` |
| Settings | `settings get/update/get-key/set-key/reset-key` |
| Profiles / personas | `profiles …`, `personas …` |
| Backup | `backup export/validate/import` |
| Health / metrics | `info health [--live\|--ready]`, `info stats`, `info ping` |
| Service | `service start/stop/log/view`, `log`, `tree` |
| The wire | PUB → `:5555`, SUB ← `:5556` (topic frame + JSON frame) |
