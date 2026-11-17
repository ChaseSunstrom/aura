# The HUD protocol — how the apps talk to JARVIS

Verified against Hermes v0.21.0 (v2026.8.31) on 2026-09-01/02; the deploy smoke passes on v0.21.5 (v2026.9.24), 2026-10-06. Both apps (web console,
Android) implement exactly this; the adapter (`aura-adapter/`) is the only origin they use.

## Endpoints (all on the adapter, e.g. `http://10.20.30.59:8643`)

| Purpose | Endpoint | Auth |
|---|---|---|
| HUD socket (sessions, prompts, commands, events, approvals) | `ws(s)://<adapter>/ws` | bearer key as `Authorization: Bearer <key>` header, **or** WebSocket subprotocol `jarvis-key.<key>` (browsers), **or** `?key=<key>` |
| Status: model loaded / warming up, backend health | `GET /api/status` | bearer |
| Speech to text | `POST /api/audio/transcribe` `{"data_url":"data:audio/webm;base64,…","mime_type":"audio/webm","transcript"?:"…"}` → `{"ok":true,"transcript":"…","provider":"local"}` (empty transcript = silence; a `transcript` sent with the audio is the phone's own words, taken instead of Whisper with the speaker gate still hearing the audio, `provider:"phone"`; `"speaker_gate":false` skips the gate for a file a tool transcribes: `speaker` null, no `speaker_ticket`, Hermes' own Whisper, no `voice_ms` figure and no `transcribe_ms` deadline check — honoured only from a loopback peer, ignored (gated as usual, flag stripped) from any other; `recording_transcribe` sends it) | bearer |
| Text to speech (one shot) | `POST /api/audio/speak` `{"text":"…"}` → `{"ok":true,"data_url":"data:audio/mpeg;base64,…"}` (Piper WAV re-encoded to MP3 by the backend; verified) | bearer |
| Text to speech (streamed, per reply) | `ws(s)://<adapter>/api/audio/speak-stream` | bearer (same three ways) |
| OpenAI-compatible chat (non-HUD clients, tests) | `POST /v1/chat/completions` (SSE when `stream:true`) | bearer |
| Sessions over REST | `GET /api/sessions?limit=`, `GET /api/sessions/{id}/messages`, `POST /api/sessions/{id}/chat/stream` | bearer |
| Scheduled jobs | `GET /api/jobs`, `POST /api/jobs/{id}/run`, `/pause`, `/resume` | bearer |
| Pairing payload for the phone's QR | `GET /api/pair` → `{"url","key","model","kind":"hermes-adapter"}`; with a device key, no `key` and the `"device"` it is (`{id,name,kind,created,last_seen,house_token}`) | bearer |
| Mint a device its own key (T-0569; the console's pairing claim) | `POST /api/pair` `{"name","kind","house_token"?}` → the payload above with the new device's `key` and `"device"` | master key only |
| Device keys: list, revoke | `GET /api/devices/keys` → `{"keys":[device…]}`; `DELETE /api/devices/keys/{id}` → `{"ok":true,"id"}` (404 when unknown; closes that key's open sockets with `4401`) | master key only |
| The console's Board (T-0733) | `GET /api/board` → `{"at","sources":{"hermes","models","gpus"},"hermes","models","gpus"}`; each source `{"status":"ok"\|"stale"\|"unavailable","age_s","why"}`. `hermes` is the aura-board plugin's file (`$HERMES_HOME/board/board.json`: the last 24 h of turns by model, hour, kind and outcome, latency, tool calls a minute; counts only, never content), stale past 15 min; `models` is llama-swap's `/running` cut to `{model,state}`; `gpus` the manager's cards. A section the plugin could not read is `null` with its reason under `hermes.errors` | bearer |
| Liveness | `GET /health` | none |

Failure modes: `401` (bad or revoked key), `502` (adapter cannot reach Hermes), WS close `4401` (bad key, or the device key was revoked while the socket was open), WS close `1013` "upstream not ready" (`/ws` only: Hermes did not connect and send its first frame within 6 s, or hung up before it; the apps reconnect, and give up on a socket with no `gateway.ready` after 10 s themselves).
The adapter forwards `adapter.error` events (`{"type":"adapter.error","payload":{"error":"backend unavailable"}}`)
when `hermes serve` is down.

## The HUD socket: JSON-RPC 2.0, newline-free JSON text frames

Client → server request: `{"jsonrpc":"2.0","id":<int>,"method":"<name>","params":{…}}`
Server → client: responses `{"jsonrpc":"2.0","id":<int>,"result":{…}}` / `{"…","error":{"code","message"}}`,
and events `{"jsonrpc":"2.0","method":"event","params":{"type":"<type>","session_id":"…","seq":<int>,"payload":{…}}}`.

The first frame after connect is the event `gateway.ready`.

**Events a handler emits arrive before that handler's reply.** The gateway pins every frame a
request produces to the one transport and writes them in order, so `hud.layout.apply` from
`@<sid> arrange` (and any other event a slash command emits synchronously) is on the socket
*ahead of* the `command.dispatch` response. A client that reads only for its own request id and
discards the rest loses those events; buffer every `method: "event"` frame you see while waiting
for a reply.

### Methods the apps use

| Method | Params | Result / notes |
|---|---|---|
| `session.create` | `{}` | `{session_id}` — one per conversation; keep it for `/resume` |
| `session.list` | `{}` | saved transcripts |
| `session.resume` | `{session_id}` | re-attach; history replays. A session is stored at its first message: one never spoken in resumes only while its socket lives, then answers 4007 (the console starts a new one quietly unless that conversation was asked for, T-0886) |
| `session.history` | `{session_id}` | messages |
| `session.status` | `{session_id}` | running/idle, model |
| `session.interrupt` | `{session_id}` | stop the current turn (cooperative; `stopping` → idle) |
| `session.steer` | `{session_id, text}` | inject guidance at the next tool boundary |
| `subagent.interrupt` | `{subagent_id}` | stop one delegated child; `{found, subagent_id}`. Hermes' own (`tui_gateway/methods_session.py`), relayed like any method; `subagent.steer {subagent_id, text}` and `delegation.status` sit beside it, unused by the console so far |
| `image.attach_bytes` | `{session_id, content_base64, filename?, ext?}` | queue an image (type sniffed from its bytes, ≤ 25 MiB) for the **next** `prompt.submit` on that session; result `{attached:true, path, count, text, bytes}`, errors 4015 missing, 4016 unsupported type, 4017 bad base64, 4018 too large. There is no detach, so the console attaches only at the moment it submits the words the photo goes with (T-0497); the adapter relays the next prompt on that session even when it would answer it itself (below), and a `prompt.submit` that fails after the attach leaves the photo for the one after |
| `prompt.submit` | `{session_id, text, surface?, speaker_ticket?, voice?}` | start a turn; result `{status:"streaming"}`. `surface` is what this screen can show (`tests/contracts/surface-capabilities.json`; the adapter records it per session and strips it — the artifacts plugin's prompt line and `artifact_send` read it back; a deploy's own probe turns, `scripts/smoke.sh` and `scripts/verify/golden-turn.mjs`, send `{"name": probe_surface}` from `tests/contracts/rig-session.json`, and the habit counter skips a session that reported it); `speaker_ticket` is the voice gate's word from `/api/audio/transcribe`, recorded and stripped the same way; `voice: true` says the words were spoken whatever the gate did (the ticket says who, `voice` says how): the adapter runs the turn at `VOICE_REASONING_EFFORT` and strips it, a typed turn omits it (`tests/contracts/voice-flag.json`) |
| `command.dispatch` | `{session_id, name, arg}` | slash command **without** the leading `/`. Result `type` decides what to do next (below) |
| `commands.catalog` | `{}` | the slash commands the backend knows |
| `approval.respond` | `{session_id, request_id, choice}` | `choice` ∈ `once` \| `session` \| `always` \| `deny` — the allowed set arrives as `choices` in the `approval.request` payload (smart-denied requests offer only `once`/`deny`); `all: true` resolves every pending request the same way The reply carries no text: the console's and the phone's "Ask why" answer `deny` and then say their question as the next `prompt.submit` (T-0694, T-1054). |
| `clarify.respond` | `{session_id, request_id, answer, question_id?}` | answer a clarifying question; `question_id` names one question of a batch (without it, the adapter takes the question it showed) |
| `config.get` / `config.set` | `{key}` / `{key, value}` | e.g. `goals.max_turns`, `tts.piper.voice`; `{key: "model", value, session_id}` switches one conversation's model (the chat's model select, the path `/model` takes) and answers `{value, warning, confirm_required, scope: "session"}`, then a `session.info` carrying the new `model` (mid-turn, Hermes holds the switch until the turn ends) |

### `command.dispatch` result types

* `{"type":"send","notice":"⊙ Goal set (20-turn budget): …","message":"<text>"}` — show `notice`, then the
  client **must** call `prompt.submit` with `message` (this is how `/goal <text>` starts its first turn; the
  TUI does the same).
* `{"type":"exec","output":"…"}` — display `output` (e.g. `/goal status` → `No active goal…` or the status line, `/goal pause`, `/goal clear` → `✓ Goal cleared.`).
* `{"type":"plugin","output":"…"}` — a plugin command's reply (the `jgoal` command); display `output`.
* `{"type":"prefill","message":"…"}` — put `message` in the composer, do not send.
* `{"type":"error", …}` or a JSON-RPC error — show it.

### Goal commands (all via `command.dispatch`)

| name / arg | Effect |
|---|---|
| `goal` / `<text>` | set (replaces) the standing goal, first turn starts (type `send`) |
| `goal` / `status` · `pause` · `resume` · `clear` | control (result type `exec`) |
| `jgoal` / `@<session_id> draft <text>` | **JARVIS plugin command** (plugin commands do not receive the session, so the apps prefix the arg with `@<session_id> `): expand a one-liner into a completion contract (outcome / verification / constraints / boundaries / stop_when) via the goal judge, then set it |
| `jgoal` / `@<sid> show` · `@<sid> wait <pid>` · `@<sid> unwait` · `@<sid> contract <field>: <text>` | plugin: show the contract, park/unpark |
| `jgoal` / `@<sid> gate add <shell command>` · `@<sid> gate list` · `@<sid> gate remove <n>` · `@<sid> gate clear` | plugin: quality gates that must exit 0 before "done" |
| `jgoal` / `@<sid> subgoal <text>` · `@<sid> subgoal list` · `@<sid> subgoal remove <n>` · `@<sid> subgoal clear` | plugin: extra acceptance criteria mid-loop (`/subgoal` itself is a reserved built-in name, so it lives under `jgoal`) |

Deviation from the Hermes docs, verified on v0.21.0: the TUI-gateway `command.dispatch` handler for
`goal` implements only `status | pause | resume | clear | <text>`; `draft`, `show`, `gate …`, `wait`
and `/subgoal` exist on the CLI/messaging gateway but are **not** routed here (`goal gate list`
would set a goal literally named "gate list"; `subgoal` returns error 4018). The in-repo
`aura-goals` plugin registers `jgoal` as a plugin command (`subgoal` is a reserved built-in name and
cannot be registered by a plugin), which `command.dispatch` does route (result type `plugin`), and
drives the same `GoalManager` the loop uses.

### Events the HUD renders

| `type` | `payload` | HUD |
|---|---|---|
| `session.info` | `{model, provider, approval_mode, tools, …}` | header: model in use, approval mode |
| `message.delta` | `{text}` | streaming reply text (append) |
| `message.interim` | `{text, already_streamed}` | mid-turn assistant note |
| `message.complete` | `{text, …}` | final text for the turn |
| `reasoning.delta` / `reasoning.available` | `{text}` | collapsible "thinking" |
| `tool.start` | `{tool_id, name, context, args}` | tool card: running |
| `tool.progress` | `{tool_id, …}` | tool card: progress line |
| `tool.complete` | `{tool_id, name, args, duration_s, result?, summary?, error?}` (`result` is the tool's JSON parsed when it parses; older emitters sent `duration`) | tool card: done / failed; the steps projection also reads a result's `success:false`, `ok:false` or non-zero `exit_code` as failed. A card still running at `message.complete` is closed as failed (`stopped` after an interrupt), so a Stop cannot leave the core in its tool state; a `tool.complete` that arrives later still updates it |
| `approval.request` | `{request_id, tool, command/args, reason, options}` | approval banner → `approval.respond` |
| `clarify.request` | `{request_id, question, choices, options, questions?}` | question card → `clarify.respond`; a batch carries `questions` (`{qid, question, choices}`) and its first question flat, and each answer without a `question_id` is followed by the next question (`tests/contracts/clarify.json` holds both shapes; the adapter, console and phone suites read it) |
| `status.update` with `payload.kind == "goal"` | `{kind:"goal", text}` where text is one of `⊙ Goal set (N-turn budget): …`, `↻ Continuing toward goal (n/N): <judge reason>`, `⏳ Goal (parked …)`, `⏸ Goal paused — n/N turns used …`, `✓ Goal achieved: <reason>`, `Goal cleared` | goal panel: state + the judge's reason, verbatim |
| `status.update` (other kinds) | `{kind, text}` | status line |
| `session.title`, `session.usage` | | title, token counts |
| `subagent.start` / `subagent.tool` / `subagent.complete` | `{subagent_id, goal, parent_id?, depth, child_session_id?, tool_count, status, summary, …}` | delegation rows: the dock, C2, the orb's companions (below) |
| `hud.projection` | `{id, type, data, origin:"agent"}` | a generative visual out of the core (`Projected`, below) |
| `process.*` | background processes | "running in background" rows |
| `settings.changed` | `{key}` | a Hermes setting was written (the aura-settings plugin, to every live session): the settings feed re-reads, and the console offers Undo for five seconds and re-reads Recent changes (`jsettings history`); a write from the API server or cron is not announced, a console sees it on reconnect |
| `settings.proposals` | `{waiting}` | the settings plugin's waiting changes were made, applied or discarded (to every live session): the Approvals screen reads `jsettings proposals` again. The payload is the count only, never a key, a value or an id (a proposed secret sits in that file in clear); a missed event is covered by the read on open and reconnect |
| `memory.proposals` | `{open}` | what waits to be remembered changed: a proposal was made, kept or dropped (the aura-hud plugin, to every live session): the Approvals screen reads `jmemory proposals` again. The payload is the count only, never a proposal's words. A change made in a routine's own process is heard within about five seconds: once a console has listed its panels or read `jmemory`, the gateway looks at the queue file every 5 s and announces when the open proposals differ (T-1506). A console not connected then reads the list on open and reconnect |
| `error` | `{message}` | toast |
| `adapter.error` | `{error}` | "backend unavailable" state (offline screen). It ends with the next `gateway.ready`, and its text goes with it: an app that kept the text read it as an `error` event once the backend answered and sat at alert until the next turn (`tests/contracts/reactor.json` `derivation.sequences`, T-1483) |

Parse the goal state from `status.update` text with the leading glyph: `⊙` set, `↻` continuing
(`(n/N)` gives turn/budget, the text after `: ` is the judge's reason), `⏳` parked, `⏸` paused,
`✓` achieved. Always show the reason text.

### Subagents (Hermes' own events, on the delegating session)

`delegate_task` relays `subagent.start`, `subagent.tool`, `subagent.thinking` and `subagent.complete`
on the session that delegated (`tui_gateway/server.py`, the `subagent.` branch); the child's own
`subagent.text` stays on the child's session. Of the payload, the console keeps:

| field | on | kept as |
|---|---|---|
| `subagent_id` (older emitters: `id`, `task_id`) | all | the row's id. A `complete` without one marks nothing, so one finisher cannot finish a batch |
| `goal` | start | the row's label |
| `parent_id` | start | `parent` — present only when the delegating agent is itself a subagent |
| `depth` | start | `depth` — 0 is JARVIS' own child, 1 that child's child |
| `child_session_id` | start | `session` — the child's own Hermes session |
| `tool_count` | start, tool, complete | `tools`; a `subagent.tool` without one counts one |
| `status` | complete | `completed` → done, `interrupted` → interrupted, `failed` / `error` / `timeout` → failed |
| `summary` | complete | `summary`, and the dock's detail line |

`subagent.thinking`, `model`, `toolsets`, `duration_seconds` and the token, file and cost
rollups on `complete` are not kept. The orb draws a companion for each running child of
JARVIS and that child's own children as its satellites (`hud/data/companions.ts`,
`companionsFrom` / `satellitesFrom`); C2 lists the tree with a Stop on each running row,
which is `subagent.interrupt`.

### Cold start

`GET /api/status` → `model.state` is llama-swap's state for the configured model:
`ready` | `starting` (render **"warming up model"**, expect the first token in up to ~3 min) |
`stopping` | `unloaded` | `other-model` | `unreachable`. Poll every 5 s while a turn is pending
and no token has arrived; every 30 s otherwise.

The same report carries `status: {word, reason}`: the one word every surface shows, decided by
the adapter (`status_word` in `aura_adapter/status.py`) and printed as `word: reason` by the
console (the Ops header), the phone (the orb's caption while the model loads, Settings and the
system check) and the desktop console (the model pane). No surface derives it. The words are
`ready`, `degraded` and `down`; the first of these that holds gives the reason: the API server
or the backend out of reach (`down: backend unreachable`), the kill switch
(`degraded: kill switch engaged`), llama-swap out of reach (`degraded: model server unreachable`),
the model starting or loading (`degraded: model warming`). A model unloaded at rest is `ready`.
When the adapter itself does not answer, each surface shows the fixed `down: no answer`. An
adapter older than the field sends none, and each surface keeps the wording it had. The table
all four suites read is `tests/contracts/status-words.json`. The report does not probe the
house (jarvis-core), so `ready` says nothing about devices.

The adapter answers a closed grammar of `prompt.submit` texts itself, whatever the model's
state (`aura-adapter/aura_adapter/fast_intent.py`; warm too since T-0186): the time, the
date, and — from a session whose surface is `android` — bare play/pause/next/previous for the
phone's music. The reply is the frames a turn already ends with (the `{status:"streaming"}`
result, then `message.complete`, plus `hud.phone.media` for music); while that state is any
but `ready` or `unreachable` its text ends "The main model is still waking up.". Hermes never
sees that prompt. The one exception is the first prompt after an `image.attach_bytes` on that
session: it is always relayed, so the photo goes with it. Lights and timers are not in the
grammar: their tools are not FREE in the approval matrix, so they wait for the model.

When llama-swap answered earlier in the adapter's life and now refuses or times out (the model
host is down, not loading), the adapter answers **every** `prompt.submit` the same way: the
grammar with "My main model is unreachable right now." after it, anything else with one line
saying it cannot reach the model. A llama-swap never seen answering (a model not behind it)
is relayed as before. The console's model readout goes to warn and its panel to `offline`.

The adapter logs two lines per relayed turn, `turn start session=… id=… surface=…` and
`turn end session=… outcome=… elapsed_ms=…` (`aura-adapter/aura_adapter/turn_log.py`), so
`docker logs jarvis-adapter | grep <session>` finds a turn a surface reported failing; the same
session id is in Hermes' log.

### Approvals and questions under Hermes v2026.9.24

Hermes now asks a client with a server->client JSON-RPC request (`{"id": "srq-…", "method":
"approval" | "clarify", …}`) and only on a socket that sent `client.capabilities
{server_requests: true}`; on any other it withdraws the approval at once. The adapter sends the
capability for every app socket and translates (`aura-adapter/aura_adapter/server_requests.py`):
the requests arrive at the apps as the `approval.request` / `clarify.request` events above, their
`approval.respond` / `clarify.respond` go back as the response Hermes waits for (a batch question
through `clarify.lock`), `request.cancel` arrives as `approval.cancelled` / `clarify.cancelled`,
and any other request kind is refused at once with -32601.

**Sensitive approvals (T-0945).** A sensitive call (locks and alarms, the vault, destructive
commands, settings and capability changes; `hermes/plugins/_shared/_matrix.py`) is not put to
Hermes' approval queue, because Hermes takes a yes for a queued approval from any socket, its
legacy `approval.respond`, `/v1/runs/…/approval` and `/approve` on a chat platform. Once a
paired phone has registered a signing key (`POST /api/approval/key {public_key}`, base64 DER
P-256, the phone's own device key, first one wins, and one phone a house: a second phone is answered 423 until the first is revoked in Devices; two keys registered before T-1030 both keep signing), the approval gate asks the adapter from
inside the tool call (`POST /api/approval/proof`, master key) and waits up to two minutes. On a volume whose `plugins.hook_callback_timeout` is still Hermes' 30 s the wait is 20 s, and the gate logs that once with the setting's name. The
adapter shows the ask on every app socket as an `approval.request` whose `request_id` starts
`proof-`, with `confirm: "phone"`, `challenge` (`"{request_id}\n{pattern_key}\n{sha256 hex of the JSON array [command, description]}"`; an app signs it as given) and only
`once` / `deny`; it says yes only for an `approval.respond` whose `signature` is base64 DER
ECDSA/SHA-256 over the challenge by a registered key. Any other allow is answered with error
-32000 and the ask stays open; `deny` needs no signature; every app then gets
`approval.resolved {request_id, choice}` (`once`, `deny` or `timeout`). The field names are
`phone_confirmation` in `tests/contracts/approval-event.json`. Not covered: a house with no
phone key registered, or a gate that cannot reach the adapter, where the ask is Hermes' ordinary
one-time ask; and the master key, which can pair a phone of its own.

**On the phone** (`android-app` `ui/ApprovalProof.kt`): a P-256 Android Keystore key (a strong
biometric on every use, invalidated by a new enrolment) is registered on each session until the
adapter answers 200; a 403 (master key or a non-phone device) 409 (this phone's key is not the one on its record) or 423 (another phone
signs for the house: revoke it in Devices) is said once and tried again after the next pairing. The card, the island and the
consent screen sign the challenge behind BiometricPrompt, a refused signature (-32000) raises
the ask again with "The fingerprint was not accepted", and `approval.resolved` (a `proof-` ask
carries no `session_id`) takes the card, screen and notification down. The car's voice never
allows one: it says to confirm on the phone and the ask goes to the phone's consent screen.

### The approval card (T-0606)

An approval is one clear ask. Hermes' `request_tool_approval` has no slot but the reason string, so
`aura-guard` writes the card as the **last five lines of the reason** and the apps parse them
(`tests/contracts/approval-event.json` `card` is the table; Python reads it in
`hermes/plugins/tests/test_matrix.py`, the console in `aura-web/src/lib/hud/approvalCard.test.ts`):

```
What: <one plain ask, ends with ?>
Why: <the owner's words, or what started the turn>
Impact: <what changes>
Undoable: yes|no
Home: yes|no
```

A reason without the five lines (an older guard, Hermes' own shell gate) is shown as plain text.
Anything before the lines is unchanged: the first-message and outside-read prefixes come first,
then the rule sentence with the arguments ("Show me first" reveals that sentence and answers
nothing). `Undoable: no` means hold-to-confirm. Every field is one line, so an argument cannot
forge a line. Every tool the matrix asks about has an entry (`_messages._CARD`); a tool without
one reads as not undoable.

A change that replaces a value the guard can read adds three lines **directly before** the five
(T-1317, T-1329; the contract's `change` is the table, read by each plugin's tests and
`approvalCard.test.ts`). The tools that carry them: `settings_set` and `settings_undo` (the
setting), `chat_model_set` (the model this chat runs on, from the gateway's live table),
`device_name` (the name the house's network sweep holds, one read of 3 s), `model_settings` (the
launch options the call changes, one 3 s read of the adapter's model list) and `goal_pause` /
`goal_resume` (the standing goal's run state). Each is registered the way the settings plugin
registers its own (`_approval_hook.CHANGE_DESCRIBERS`). A read that fails, times out or finds
nothing writes no lines, and the ask goes on without them. Every other tool an action row names
is listed in `tests/contracts/console-actions.json` `no_prior_value` with its reason (it adds or
removes something, or its row is free and never put to the person), and
`hermes/plugins/tests/test_action_parity.py` fails for a tool in neither:

```
Setting: <the key>
Now: <the value now>
Becomes: <the value asked for>
```

Each value is worded (`on`, `off`, `not set`), put on one line and cut at 72 characters with a
mark. A secret's two values are both `(secret)`, and its value is in no other part of the text
or the audit line. The console draws the two values as rows on the card, only for those
tools (another tool's arguments are outside text), and draws neither for a secret or a mask;
the phone shows the lines with the rest of the text before the card. An app that does not know
the lines shows them as text. They change neither who may answer nor how: a settings change is
still confirmed with the phone's fingerprint.

Rule chips map onto Hermes' choices: **ask every time** = `once`, **allow for this task** =
`session`, **allow when I'm home** = `always`, offered only while `Home: yes`. A home request's
`rule_key` ends `:at-home`, so an `always` given at home is stored apart and never covers a request
made away; presence comes from the paired phone's zone through `GET /api/config/companion/list`
(`presence.zone`), and unknown is away. An app clears the card on `approval.resolved` / `approval.cancelled`
(the shell lane draws it on the device in use; fan-out to the other sockets is not changed here).

A standing answer runs out (T-0570). Every card's `rule_key` carries a token after its base
(`<base>:g<6 hex>`, then `:after-outside` and `:at-home`), and an `always` lasts
`approvals.always_ttl_days` (default 30 days). After that, or once revoked, the token changes, the
key Hermes remembered never comes up again, and the next call asks. Only a human's `always` starts
the clock: smart mode's `smart_<verdict>` answers never do. An `always` given before the token
existed asks once more. The owner lists and revokes them through `command.dispatch`:

```
command.dispatch {name:"guard", arg:"@<sid> grants"}            → {type:"plugin", output:"<JSON list>"}
command.dispatch {name:"guard", arg:"@<sid> revoke <base_key>"} → {type:"plugin", output:"revoked: <base_key>"}
```

Each row is `{tool, base_key, scopes, granted_at, expires_at}` (epoch seconds), oldest first;
render it as text. A revoke moves every scope of that rule at once. A session whose speaker is a
guest or a member gets a one-line refusal from either verb.

### Voice (relay path)

1. Push-to-talk: record (Opus/WebM in the browser, PCM-WAV on Android), `POST /api/audio/transcribe`
   with a base64 data URL. Empty transcript → say "I didn't catch that" locally, do not submit.
2. `prompt.submit` the transcript on the current session.
3. Speak the reply as it streams: open `/api/audio/speak-stream`, send text deltas, play the
   audio frames; or, simpler and used first, wait for `message.complete` and `POST /api/audio/speak`
   (play the `data_url`, an MP3).
4. Barge-in: on any new push-to-talk, stop playback, `session.interrupt` if a turn is running.
   An open mic's utterance only quiets playback. Once its transcript is accepted it is sent
   as a `prompt.submit` on the running turn, which Hermes folds into that turn (its
   `display.busy_input_mode`, default redirect); only a stop said on its own ("stop",
   "cancel that", "never mind") sends `session.interrupt` first (a cough, the speaker's own
   echo or a passing remark must not end the turn). While a reply is live -- playing, between its sentences, the turn
   still running, or 0.7 s after the last clip -- an open mic starts an utterance only on a
   voice clearly over the speaker's echo (`aura-web/src/lib/hud/bargeIn.ts`).
   An open-mic transcript that is only the wake phrase ("Hey, Jarvis.") is not submitted: the
   console shows listening for 8 s, until the next transcript is submitted or the mic turns
   off (`isWakeOnly` in `aura-web/src/lib/hermes/voice.ts`). Push-to-talk submits it as said.
5. "keep working on this until …" phrases are goals: the app dispatches `goal` with the sentence
   instead of `prompt.submit` when the transcript starts with a goal trigger
   (`keep working on`, `keep going until`, `don't stop until`, `get this done`, `set a goal`).

### A second utterance while a turn runs, per surface (T-1140)

What each surface sends when the person says or types something new with a turn still running.
"Folds" means `prompt.submit` on the running session with no `session.interrupt` before it, which
Hermes redirects into the turn (above). Read from the senders' code; the surfaces differ.

| Surface | Second utterance | Decided in |
|---|---|---|
| Console, typed | Folds. No check for a running turn. | `VoiceController.submit` (`aura-web/src/lib/hud/voice.svelte.ts`) → `HermesLink.submit` (`aura-web/src/lib/hermes/link.ts`) |
| Console, push-to-talk | Interrupts first: a new press stops playback and sends `session.interrupt` if `turn.running`, then submits. | `VoiceController.start` (`voice.svelte.ts`) |
| Console, open mic | Folds. The onset only quiets playback; once the transcript is accepted it is submitted, and `session.interrupt` goes first only when the whole transcript is a stop phrase (`isStop`, `aura-web/src/lib/hermes/voice.ts`). A wake-phrase-only transcript is not submitted. | `VoiceController.start` and the transcribe handler (`voice.svelte.ts`) |
| Car | Interrupts and replaces: a new `ask` cancels the running turn (`session.interrupt` when its job is still active) and then submits. The car has no open mic: the head unit's microphone is requested per utterance. | `CarTurn.ask`, `CarTurn.cancel` (`android-app/app/src/main/kotlin/ai/jarvis/app/car/voice/CarTurn.kt`); `HermesCarJarvis.interrupt` (`.../car/voice/HermesCarJarvis.kt`) |
| Phone, open mic (the native conversation: assistant overlay, `HermesConversation`) | Not heard while the turn works: the mic's end-of-speech is acted on only in the LISTENING phase, and a turn in flight is THINKING. Over the spoken reply, a voice clearly over the echo interrupts at once (`session.interrupt` if `turnRunning`) and starts listening, before any transcript exists and with no stop-phrase test. A sentence typed on that screen also interrupts first. This differs from the console on purpose: the overlay has no transcript while the reply plays, so level is its only way to be cut in on, and the level is judged by the echo gate `BargeIn` (`.../assist/hermes/BargeIn.kt`): the reply's first moments teach it the speaker's echo in the microphone, and only a level well over that echo, held, counts as the person. Not covered: a cough or a slammed door loud and long enough passes the gate and ends the turn, and a person no louder than the speaker is not heard. The overlay is still reachable (the assist gesture), though the phone's home is the console page. | `HermesConversation.onMicLevel`, `isListening`, `bargeIn`, `sendTyped` (`android-app/app/src/main/kotlin/ai/jarvis/app/assist/hermes/HermesConversation.kt`) |
| Phone, console page | The console's rows above: the phone's home is the console in a WebView (`android-app/CLAUDE.md`), so its open mic is the console's. | as the console |
| Desktop agent | Folds, typed only: the terminal console sends `prompt.submit` and never `session.interrupt`; a routine that fires does the same. The agent has no microphone path. | `Console._submit` and `_flush` (`aura-desktop/aura_desktop/console/app.py`); `_Link.fire` (`aura-desktop/aura_desktop/routines.py`) |

Not covered: what Hermes does with the folded `prompt.submit` (`display.busy_input_mode`, a Hermes
setting this table did not read, so "folds" is the protocol's claim above, not re-proved here);
what the adapter does with a prompt on its way (not read); the goal loop's own turns; and
`session.steer`, which no surface's send path calls (`HermesLink.steer` and `HermesClient.steer`
exist, with no caller outside tests).

## Panels — the surface JARVIS pulls things up on

A **panel** is a typed card the HUD shows, updates and dismisses. The agent summons panels through
the `aura-hud` plugin's tools (`hud_show`, `hud_update`, `hud_dismiss`, `hud_list`); the person
summons them by voice ("show me the GPUs") or by clicking; events (an approval, a goal change, a
finished job) summon their own. Every app renders the same surface from the same events.

### Events (session-scoped, on the HUD socket, `method: "event"`)

```json
{"jsonrpc":"2.0","method":"event","params":{"type":"hud.panel.show","session_id":"<sid>","payload":{
  "id":"p_3f9a","kind":"telemetry","title":"GPUs","payload":{...},"ttl_s":null,"origin":"agent",
  "created_at":"2026-09-03T00:41:12Z","rev":1}}}
{"jsonrpc":"2.0","method":"event","params":{"type":"hud.panel.update","session_id":"<sid>","payload":{"id":"p_3f9a","rev":2,"payload":{...},"title":"GPUs (live)"}}}
{"jsonrpc":"2.0","method":"event","params":{"type":"hud.panel.dismiss","session_id":"<sid>","payload":{"id":"p_3f9a"}}}
{"jsonrpc":"2.0","method":"event","params":{"type":"hud.panel.dismiss","session_id":"<sid>","payload":{"id":"*"}}}
```

`origin` is `agent` (a tool call), `user` (voice/click), or `system` (approval, goal, job).
`rev` is monotonic per panel; a client ignores an update with a lower `rev` than it holds.
`ttl_s`, when set, dismisses the panel by itself after that many seconds (the client animates
it out; the plugin also emits the dismiss so late joiners agree).

**The owner's other screens** (T-0785). A panel the owner's session shows (`hud.panel.show`,
its `update` and `dismiss`) and a `hud.projection` go to the asking session and to every other
live HUD session that is the owner's (the adapter's speaker record per session: a session
nobody has spoken at is the key holder's, the owner's), the same frame with each session's own
`session_id`. A phone or car voice turn runs in a session of its
own, and the phone's home and the car page are the console on sessions of theirs: sent only to
the asker, what Jarvis launched there never reached the screen being looked at. A member's or
a guest's frames stay in their own session, and their screens never get the owner's (a panel
may be the owner's mail). A tool that draws its own answer (`lists` on `show`, `weather_forecast`)
sends its projection the same way (T-1471), and a list that was shown is sent again, under the
same `id`, to the sessions that were shown it when it changes. `hud_say`
stays with the asker. `hud_dismiss "*"` sends the wildcard to the asker only; the other screens
get one dismiss per panel the asker held, so their own panels stay. The other screens hold no
copy: one that reloads or joins later lists its own session's panels only. The console's stage
draws a routed panel at rest, and a routed projection in its answer scene, which it keeps at
rest for five minutes after the newest frame (a redraw of the same `id` starts that again;
Escape or the console's next turn sends it back; T-1472); the car page draws
the newest launched panel (title and first line) or visual as its one card, under a call or a
request waiting on the driver and above a notice and the music; kept panels are not launches.

**Kept panels** (`hud_show keep: true`, T-0434). When the person asks to keep or pin a panel,
it is shown on every live HUD session, not just this one, and every new session gets it on
connect. Its record carries `"kept": true` and `ttl_s: null`. Kept panels live in
`$HERMES_HOME/plugin-data/jarvis-hud/kept.json`, at most 12; the 13th is refused until one is
dismissed. The dashboard and the cron gateway are two processes: each re-reads the file when
its text changes (polled every 5 s), so a cron job's `hud_update` on a kept id reaches the
screens. `hud_dismiss "*"` clears the session's panels and then shows the kept ones again;
dismissing a kept panel by id forgets it everywhere. A guest's turn (the adapter's speaker
record) cannot keep a panel, cannot show, update or dismiss a kept id, and `hud_list` hides
the kept panels from it; an unreadable or half-written file leaves the panels up. Not covered: a client that is offline
when a kept panel is dismissed keeps drawing it until its next connect; two processes writing
the file in the same instant can lose one write (last writer wins).

### The car (`place_search`, `car_navigate`, 2026-09-25)

```json
{"jsonrpc":"2.0","method":"event","params":{"type":"hud.car.navigate","session_id":"<sid>","payload":{
  "lat":41.8812,"lon":-87.6278,"label":"Blue Door Hardware","app":""}}}
```

A car turn's prompt says where the car is (`HermesCarJarvis`); `place_search` finds places near
it from the house's own place index (`GET /api/maps/places`, T-0765): a name or address, or a
kind of place (`kind`, an OSM tag such as `amenity=cafe`) within 5 km, nearest first. A house
Photon (`PHOTON_URL`) is asked next; the public Photon and Nominatim (`PHOTON_PUBLIC`) and an
Overpass (`OVERPASS_URL`) only when the owner switches them on. `car_navigate` sends this event to the session; the phone opens the destination in
`app` (`organic`, `google`, `here`; empty is the person's first installed) in the car's app pane
(`CarHud`). A new destination is a changed route. Both tools are free
(`_shared/_matrix.py`); pinned by `aura-hud/tests/test_places.py`.

### Kinds and payloads (`tests/contracts/hud-panels.json` is the source both suites read)

| kind | payload | rendered as |
|---|---|---|
| `text` | `{"markdown": str}` | a card of prose (markdown, no raw HTML) |
| `table` | `{"columns": [str], "rows": [[cell]]}` | a table, first column emphasised |
| `image` | `{"url": str, "alt": str, "caption"?: str}` | an image, click to enlarge |
| `chart` | `{"series": [{"name": str, "points": [[x, y]]}], "unit"?: str}` | the existing line chart |
| `camera` | `{"camera": str, "url": str, "taken_at": iso}` | a still with its camera's name and time |
| `code` | `{"language": str, "text": str, "path"?: str}` | monospace, syntax-aware where the console already is |
| `terminal` | `{"command": str, "output": str, "exit_code"?: int, "live"?: bool}` | a terminal card; `live` keeps the cursor |
| `web` | `{"url": str, "title"?: str}` | a sandboxed frame (allow-listed origins), else a link card |
| `device` | `{"entity_id": str, "name": str, "state": str, "attributes": {}}` | a house device card with its control |
| `telemetry` | `{"gpus": [...]}` (the gpu_status result) or `{"metrics": [{"name","value","unit"}]}` | gauges |
| `goal` | the `status.update kind=goal` text, phase, turn, max_turns | the goal card |
| `approval` | the `approval.request` payload | the approval door (answered through `approval.respond`) |
| `clarify` | the clarify payload | the question card |
| `notice` | `{"level": "info"|"warn"|"error", "text": str}` | a toast-sized card |
| `video` | `{"url": str, "mime": str, "title"?: str, "provenance"?: {"tool", "source_url"}}` | a player: muted until unmuted, `preload=metadata`, unloaded off-screen; the provenance is its caption |
| `audio` | `{"url": str, "mime": str, "title"?: str, "provenance"?: {"tool", "source_url"}}` | a player (`preload=metadata`), the provenance as its caption |
| `document` | `{"url": str, "mime": str, "title"?: str, "provenance"?: {"tool", "source_url"}}` | a PDF embedded; anything else a link that says Open, or Download for the types the adapter serves as an attachment (`download_mimes`) |

The three media kinds carry a published artifact (`aura-artifacts`; the adapter's
`GET /api/artifacts/<id>`, relayed by the console under `/hermes/api/artifacts/<id>` with byte
ranges) by `url` + `mime`: an artifact id is a digest with no extension, so the mime is required
rather than sniffed. The console and the phone play them; the desktop console says what the
file is and where it is. They are appended to `kinds`, never reordered — the Android mirror
compares the list element-wise.

### Projections (`hud_project`, T-0612; `tests/contracts/projections.json` is the source)

`hud_project {type, data, id?}` draws a typed visual: one of the contract's 19 types (`scene`
is the default "make me a visual", `custom_html` the escape hatch). The plugin validates `data`
against the contract (unknown fields, types, bounds, item counts, a 128 KB cap) and answers a
refusal in the same words the console uses; a valid one is emitted as

```json
{"jsonrpc":"2.0","method":"event","params":{"type":"hud.projection","session_id":"<sid>","payload":{
  "id":"j_3f9a…","type":"bar","data":{"title":"Tables at eight","bars":[…]},"origin":"agent"}}}
```

The id is the model's (1 to 64 of `A-Z a-z 0-9 _ . : -`) or minted; the same id replaces that
projection. `origin:"agent"` marks every string as model output: the console draws strings as
text, never as HTML, and validates again before drawing (a malformed payload is the error state,
not a crash). The tool is free in the matrix, for a guest too: it shows, it does not act.
`custom_html` runs only in `<iframe sandbox="allow-scripts">` loading `/projection-frame`, whose
own CSP is `default-src 'none'` with inline script and style only (no fetch, socket, form or
network image), with no same-origin; it runs only after the owner presses Run (a busy loop in
a sandboxed frame shares the console's thread), is capped in size and in time counted from Run,
and is removed when the projection goes back into the core. The console's side is `$lib/ds/gen/README.md`.

### Commands

`command.dispatch {name:"hud", arg:"@<sid> list"}` → `{type:"plugin", output:"<JSON list of panels>"}`
for a client that joins late; `arg:"@<sid> dismiss <id|*>"` for a click or a voice "clear the screen".
The plugin keeps the panel state per session in memory and replays it to `list`.

`arg:"@<sid> routines view|enable <id> on|off|remove <id>|forget-person <name>|forget-place <label>"`
is the console's Routines panel; `routines add <json>` and `routines update <json>` (T-0549) hand
one JSON object to the `routine_add` / `routine_update` handlers, so a routine built in the form
is stored exactly as a spoken one. They take only `when`, `do`, `name`, `once`,
`cooldown_minutes` (and `routine`, `enabled` for an update); any other key
(`allow_without_asking`, `house_action`, `trial_days`) is refused, because only a spoken ask
may set it. Every write
emits `hud.phone.routines` so the phone re-arms its triggers. Like a routine tool's, the frame
goes to the session that made the change and to the owner's other live sessions (T-1473): the
phone listens on sessions of its own, not the desk console's. A phone with no live session
pulls the copy when it connects.

### Motion

Summon: scale 0.96→1, opacity 0→1, 200 ms, spring-ish ease-out; dismiss: 160 ms ease-in to 0.98
and 0; reorder: 240 ms layout transition. `prefers-reduced-motion` collapses all of them to
opacity-only 80 ms. A panel is keyboard-dismissible (Escape on the focused panel), and the
surface never steals focus from the composer unless the panel is an approval or a clarify.

## Layout — JARVIS arranges the HUD (the `hud-layout` plugin)

The panels above are *content*; the **layout** is where the window manager puts them
(GLASS_HUD.md §9: a stable id, a type, four modes — Tiled, Floating, Focus, Ambient). JARVIS
arranges the layout through the `hud-layout` plugin's tools; the person arranges it by hand,
and by hand always wins. The layout document is the settings schema's `LayoutDocument`
(`hud.layouts`: surface → activity → `{mode, tree, panels}`), so the plugin, the settings panel
and every renderer validate one shape; `tests/contracts/hud-layout.json` fixes the event and tool
shapes and reaches the document schema by `$ref` into `hermes-settings.json#/hud`.

### Events (session-scoped, on the HUD socket, `method: "event"`)

```json
{"jsonrpc":"2.0","method":"event","params":{"type":"hud.layout.apply","session_id":"<sid>","payload":{
  "op":"arrange","surface":"web","activity":"goal",
  "layout":{"mode":"tiled","tree":{"split":"row","ratio":0.55,"children":["chat",{"split":"column","ratio":0.5,"children":["goals","timeline"]}]},
            "panels":[{"id":"chat","type":"chat","pinned":false},{"id":"goals","type":"goals","pinned":false},{"id":"timeline","type":"timeline","pinned":false}]},
  "animate":true,"reversible":true,"reason":"preset goal-start","rev":1,"undo_depth":1}}}
{"jsonrpc":"2.0","method":"event","params":{"type":"hud.layout.highlight","session_id":"<sid>","payload":{"panel":"approvals","surface":"web","duration_ms":1200,"reason":"approval needed"}}}
```

`op` is one of `open`, `close`, `focus`, `move`, `resize`, `arrange`, `pin`, `set_mode`, `undo`
(`user` is reserved for the HUD's own report, below). Every frame carries the **whole** layout
after the change, not a delta: a client renders it with the §8 springs (`animate`), and `rev` is
monotonic per session so a stale frame is dropped. `surface` names the surface the tree was made
for (default `web`); a phone renders a tree by its leaf order (§9.6), the console as panes (§9.7).
A `highlight` is a 1.2 s rim pulse in the reactor's light on the named panel — never a scroll or a
modal — and is the one thing JARVIS may still do while the layout is locked.

### Tools (toolset `hud_layout`)

| tool | does | arguments |
|---|---|---|
| `hud_list_panels` | mode, activity, panels in tiling order, the focused one, lock state, undo depth | `surface?` |
| `hud_open` | add a panel: split beside a target (Tiled), at a rect (Floating), into the rail (Focus) | `id`, `type`, `beside?`, `edge?` (left/right/top/bottom), `share?`, `rect?`, `pinned?` |
| `hud_close` | remove a panel; its split collapses. A pinned panel refuses unless `force` | `id`, `force?` |
| `hud_focus` | Focus mode with the panel large, the rest as the rail | `id` |
| `hud_move` | re-seat beside a target on an edge (Tiled) or to a rect (Floating) | `id`, `target?`, `edge?`, `share?`, `rect?` |
| `hud_resize` | the panel's share of its parent split, or a new rect | `id`, `share?`, `rect?` |
| `hud_arrange` | the whole layout for an activity: a preset name or a layout tree | `preset?`, `layout?`, `activity?`, `force?`, `remember?` |
| `hud_highlight` | the rim pulse | `id` |
| `hud_pin` | pin/unpin (a pinned panel survives rearrangements) | `id`, `pinned?` |
| `hud_set_mode` | switch mode keeping every panel (Tiled re-tiles orphans, Ambient hides all) | `mode` |
| `hud_undo` | re-apply the layout JARVIS's last change replaced | — |

Every tool takes `surface?` and `reason?` (the clause the HUD shows as *why*), returns
`{ok, delivered, …}` with the applied layout, and is refused with
`{"ok": false, "refused": "lock_layout", "error": "Lock layout is on: …"}` while
`hud.lock_layout` is true — `hud_list_panels` and `hud_highlight` excepted. A failed
change (unknown panel, bad edge, a tree the schema rejects) answers `{"ok": false, "error": …}`
and emits nothing.

**Presets** are the brief's choreography as trees (all in the contract): `goal-start` (chat |
goals / timeline), `image-open` (chat | image), `approval-focus` (Focus on approvals),
`model-warming` (chat | model / timeline), `voice-ambient` (Ambient, the voice strip),
`browser-task` (chat | browser | timeline) and `working` (§9.2, the default). Each names its
activity (`goal`, `image`, `approval`, `model`, `voice`, `browser`, `default`).

### The three rules

1. **Lock layout.** `hud.lock_layout` (the settings schema; the HUD's toggle, `alt-k`) is read
   before every change. While on, every JARVIS change is refused with the text above; the HUD
   shows the toggle as the reason.
2. **The person's arrangement wins, and is remembered.** When the person arranges by hand, the
   HUD reports it: `command.dispatch {name:"hud_layout", arg:"@<sid> user <surface> <activity> <json layout>"}`
   (the `hud.layout.user` shape: `surface`, `activity`, `layout`). The plugin saves it under
   `hud.layouts[surface][activity]` through the schema and clears the session's undo stack.
   From then on `hud_arrange` for that activity applies *the person's* layout and answers
   `kept: "user"` ("kept your arrangement for goal"). An undo — the person's *Undo layout*, or
   "undo that" through `hud_undo` — marks the activity **declined**: `hud_arrange` for it then
   changes nothing and answers `kept: "declined"`. Both yield to `force` (only when the person
   asks) and to `hud_layout forget`.
3. **Reversible.** Every JARVIS change pushes the layout it replaced on a per-session stack
   (32 deep); `hud_undo` / `@<sid> undo` re-applies it as `op: "undo"` (`reversible: false`).

JARVIS's own per-activity preferences — a tree it built and stored with `hud_arrange … remember:true`
— live in Hermes's plugin state (`<HERMES_HOME>/plugin-data/…/state.json`, the `ctx.state` API),
never in `hud.layouts`, so a saved layout of the person's is never overwritten by a preference.
The skill `hermes/skills/hud-layout/SKILL.md` teaches the choreography and the rules to the model.

### Commands (`command.dispatch {name:"hud_layout", arg:"@<sid> …"}`, result `{type:"plugin", output:"<JSON>"}`)

| arg | does |
|---|---|
| `@<sid> get` | `{locked, activity, layouts{surface}, undo_depth, user_layouts, preferences, declined, presets}` for the HUD's state line |
| `@<sid> arrange <preset>` | the person chose a preset from the layouts menu: applied over their saved one (it is their choice), still refused by the lock |
| `@<sid> undo` | *Undo layout* (`alt-z`): the last JARVIS change, recorded as a decline |
| `@<sid> lock` / `unlock` | writes `hud.lock_layout` through the schema (audited to `logs/settings.jsonl`) |
| `@<sid> user <surface> <activity> <json>` | the manual-arrangement report (rule 2) |
| `@<sid> forget [activity]` | clears JARVIS's preferences and declines, for one activity or all |
| `@<sid> presets` | the preset trees |

## Settings — reading and changing Hermes's own configuration (the `aura-settings` plugin)

The gateway's `config.set` knows only its own keys (`model`, `fast`, `yolo` and `reasoning` per
session with a `session_id`; `prompt`, `personality`, `skin` and the TUI's display toggles), and
everything else answers `4002 unknown config key`, so the apps and the agent change settings through the
`aura-settings` plugin: a slash command for the apps and approval-gated tools for the agent.
Every leaf of the one settings schema is served (175 today: every row with an `env` name or a
`hermes_key`). A leaf with a Hermes key is written to `config.yaml` through Hermes's own config
writer; a leaf with an env name is written to the override file `.storage/environment.json` in the
Hermes home, which the entrypoint exports over the container environment on the next start and the
adapter reads at its own load ([ADR-0044](adr/0044-settings-env-overrides.md)); a leaf with both is
written to both so the next render agrees. Every write is audit-logged to `logs/settings.jsonl`
(who, key, before, after, when — secrets masked).

```
command.dispatch {name:"jsettings", arg:"@<sid> get goals.max_turns"}
  → {type:"plugin", output:"{\"key\":\"goals.max_turns\",\"value\":20,\"type\":\"int\",\"restart_needed\":false,\"doc\":\"Turns a standing goal may take before it parks\",\"source\":\"config\",\"settable\":true,\"applies\":\"now\"}"}
command.dispatch {name:"jsettings", arg:"@<sid> get stt.model"}
  → {type:"plugin", output:"{\"key\":\"stt.model\",\"value\":\"base\",\"type\":\"string\",\"restart_needed\":true,\"doc\":\"…\",\"source\":\"env\",\"settable\":true,\"applies\":\"restart\"}"}
command.dispatch {name:"jsettings", arg:"@<sid> get adapter.port"}
  → {type:"plugin", output:"{\"key\":\"adapter.port\",\"value\":8643,\"type\":\"int\",\"restart_needed\":true,\"doc\":\"…\",\"source\":\"default\",\"settable\":false,\"applies\":\"compose\"}"}
command.dispatch {name:"jsettings", arg:"@<sid> list"}
  → {type:"plugin", output:"[{\"key\":…,\"value\":…,\"type\":…,\"restart_needed\":…,\"doc\":…,\"source\":…,\"settable\":…,\"applies\":…}, …]"}
```

Every row (`get` and each element of `list`) carries the same eight fields:

| field | meaning |
|---|---|
| `key`, `type`, `restart_needed`, `doc` | as in the contract catalogue (`type` is `string`/`int`/`float`/`bool`/`enum`/`list`/`object`) |
| `value` | the current value, typed; a secret's is `"[redacted]"` when set and `""` when not |
| `source` | `config` (the user set the Hermes key in config.yaml), `override` (the override file — what the next start will use), `env` (the container environment), `default` |
| `settable` | whether this plugin can write it: every Hermes key and every env leaf the container reads; `false` for a leaf the compose file fixes on the host |
| `applies` | `now` (Hermes reads the key live), `restart` (read at the next container start), `compose` (fixed outside the container: a port bind, an image tag, a value the compose file renames on its way into a service, or a phone/desktop setting — edit the host `.env` and recreate; the UI says "compose file") |

`list` serves the 14 contract keys first, in contract order, then every other leaf in the schema's
order. A leaf with both an env name and a Hermes key answers `source` for the Hermes key (what the
gateway reads now); the override and the env are what the next start renders from.

### Writing: `set`, and the propose/apply step

A `set` from the apps (the command path) does not write when the approval matrix would ASK for
`settings_set` on the key — which today is every key: the `settings_set` row is ASK, so the console
cannot walk around the gate that fronts the agent's tool. It becomes a **proposal** the person
applies or discards; `apply` is the human's yes, the same act as answering the agent's approval
prompt. The proposal waits in `.storage/settings-proposals.json` (0600) across restarts.

```
command.dispatch {name:"jsettings", arg:"@<sid> set goals.max_turns 30"}
  → {type:"plugin", output:"{\"status\":\"proposed\",\"proposal\":{\"id\":\"9db33f66538c\",\"key\":\"goals.max_turns\",\"before\":20,\"after\":30,\"source\":\"config\",\"applies\":\"now\",\"ts\":\"2026-09-18T12:00:00Z\"}}"}
command.dispatch {name:"jsettings", arg:"@<sid> proposals"}
  → {type:"plugin", output:"[{\"id\":\"9db33f66538c\",\"key\":\"goals.max_turns\",\"before\":20,\"after\":30,\"source\":\"config\",\"applies\":\"now\",\"ts\":\"2026-09-18T12:00:00Z\"}]"}
command.dispatch {name:"jsettings", arg:"@<sid> apply 9db33f66538c"}
  → {type:"plugin", output:"{\"key\":\"goals.max_turns\",\"value\":30,\"previous\":20,\"restart_needed\":false,\"applies\":\"now\",\"source\":\"config\",\"proposal\":\"9db33f66538c\"}"}
command.dispatch {name:"jsettings", arg:"@<sid> discard 13b421e15f40"}
  → {type:"plugin", output:"{\"status\":\"discarded\",\"id\":\"13b421e15f40\"}"}
```

- A proposal is `{id, key, before, after, source, applies, ts}`: `before` is the current value and
  `source` where it comes from at proposal time, `after` the validated value that will be written,
  `ts` UTC. A secret's `before`/`after` are masked on the wire; the file holds the real value.
- `apply <id>` validates again, refuses while the kill switch is engaged, writes, audits with the
  actor `command`, the session and a `proposal` field, and answers the write shape:
  `{key, value, previous, restart_needed, applies, source, proposal}` — `source` is `config` when
  a Hermes key was written, `override` when only the override file was.
- `discard <id>` drops it: `{"status":"discarded","id":…}`. `proposals` lists what is waiting.
- A `set` the matrix would let through freely (no such row today) writes at once and answers the
  write shape without `proposal`. While the kill switch is engaged a `set` answers
  `{"error":"refused: …"}` and nothing is proposed.

### What changed, and the way back: `history`, `undo`

```
command.dispatch {name:"jsettings", arg:"@<sid> history 2"}
  → {type:"plugin", output:"[{\"ts\":\"2026-11-13T12:00:05Z\",\"actor\":\"tool\",\"key\":\"goals.max_turns\",\"before\":30,\"after\":40,\"undoable\":true},{\"ts\":\"2026-11-13T12:00:00Z\",\"actor\":\"tool\",\"key\":\"goals.max_turns\",\"before\":20,\"after\":30,\"undoable\":false}]"}
command.dispatch {name:"jsettings", arg:"@<sid> undo goals.max_turns"}
  → {type:"plugin", output:"{\"key\":\"goals.max_turns\",\"value\":30,\"previous\":40,\"restart_needed\":false,\"applies\":\"now\",\"source\":\"config\"}"}
```

- `history [n]` reads the audit log newest first: 20 rows by default, 50 at most; a count that is
  not a positive number reads as the default, and a line that is not a change is skipped. A row is
  `{ts, actor, key, before, after, undoable}`: `actor` is `tool` (the model's `settings_set` or
  `settings_undo`), `command` (an app) or `hud_layout`; a secret's values are the mask the audit
  stored; `undoable` is true only on the newest row of a key the plugin can write that is not a
  secret. It does not say the undo will be accepted.
- `undo [key]` puts back the value the newest change replaced (the newest change to `key` when one
  is named) through the same path as `set`, as the person: it answers the write shape, or
  `{"status":"proposed","proposal":…}` when the matrix would ask (a security setting), or
  `{"error":…}` (`nothing to undo`, a secret, the kill switch). One level: the undo is itself a
  change, so a second undo is the redo. The write broadcasts `settings.changed` before the answer.
- Neither covers a change made outside the plugin (the house's own configuration, a hand edit of
  `config.yaml`): it was never audited.

Values are JSON (`30`, `true`, `"smart"`, `["web","browser"]`, a layout document), validated by
the schema's own `coerce` in strict mode: a bounds violation, a value of the wrong type or an enum
outside its choices answers `{"error":"goals.max_turns must be between 1 and 200, got 999"}` and
nothing is proposed or written. An unknown key answers `{"error":"unknown setting 'x'; …"}`; a leaf
the compose file fixes answers `{"error":"adapter.port is fixed outside the container (ADAPTER_PORT
in the host .env / compose file); edit .env and recreate the stack"}`.

The allow-list the agent's tools describe is not kept by hand: it is the `settable` rows of the one
settings schema (`aura-schema`, V2 D17), generated by `make schema` into
`tests/contracts/hermes-settings.json` — the `keys` table, each with its type, range or choices,
`restart_needed`, its UI `group` and `widget`, and the `settings` catalogue of every leaf with its
`env`/`hermes_key`, `secret`, `tier` and now `applies`. The `command` block there names the eight
verbs. The human-readable list, with each key's meaning and default, is
[`docs/reference/settings.md`](reference/settings.md) (generated from the same schema).

The agent gets tools: `settings_list` (the 14 contract keys, not the whole catalogue — a context
cost), `settings_find`, `settings_get` and `settings_set` (any leaf) — the last behind the approval
hook (`pre_tool_call` → approve with the key and both values in the message, a secret masked), so
"Jarvis, raise the goal budget to 30" works when the person says yes. The tool path writes at once
once approved; it never proposes.

## Memory proposals: what waits to be remembered (the `aura-hud` plugin's `jmemory`)

A routine, or a turn that has read someone else's words, does not add to memory: it calls
`memory_propose`, and the sentence waits in a queue (`state/memory-proposals.json`, T-1500). The
apps read and empty that queue here; `tests/contracts/memory-proposals.json` is the table the
plugin's tests and the console's read.

```
command.dispatch {name:"jmemory", arg:"@<sid> proposals"}
  → {type:"plugin", output:"{\"ok\":true,\"open\":[{\"id\":\"p3\",\"text\":\"…\",\"store\":\"fact\",\"by\":\"cron\",\"read\":[\"session_search\"],\"at\":\"2026-11-17 03:31 UTC\",\"worded_as_instruction\":false}]}"}
command.dispatch {name:"jmemory", arg:"@<sid> drop p3"}        (ids, or `all`)
  → {type:"plugin", output:"{\"ok\":true,\"dropped\":[\"p3\"],\"open\":0}"}
```

- A row is the fact whole (one line, at most 200 characters, never clipped), the `store` it is
  for (`fact` or `memory`, the profile), `by` (`conversation`, `cron`, or the platform that
  started the turn), `read` (up to four tools that turn had read outside text with) and
  `worded_as_instruction` (the sentence reads as an order, not as a fact).
- `drop` stores nothing and is answered at once; a dropped row can still be kept by asking for
  30 days. While the kill switch is on it answers `{"ok":false,"error":…}` and drops nothing.
- There is no keep. A keep is the assistant's `memory_proposals {action:"keep", ids:[…]}` call,
  which the matrix asks about every time on a card that shows each fact whole
  ([the approval card](#the-approval-card-t-0606)). The console's Keep therefore sends a prompt
  (`prompt.submit`) with one fixed sentence naming the id, and the yes is said on that card.
- What the command answers goes to the app's screen and never to the model, so it marks no
  turn as having read outside text. Each change broadcasts `memory.proposals {open}`; one a
  routine's own run made is broadcast by the gateway's 5 s look at the queue file (T-1506).
- The console's Keep all names at most `keep_batch` ids (8, the table's row, the plugin's
  KEEP_BATCH) in one sentence, so one card shows each; its Drop all is one `drop` naming the
  ids it shows, never `all`, so a proposal that arrived since is not dropped unseen. The count
  on All apps is read with `proposals` on connect and then taken from each event (T-1505).
- Does not cover: who is at the app (the command channel is the paired apps', as for
  `jsettings`); a model that does not make the call a Keep's sentence names (nothing is kept,
  and the card on the screen can be asked again once the turn is over).

## Integrations — browsing and installing what already exists (the `aura-integrations` plugin)

Hermes already ships three catalogues — community **plugins** (`hermes plugins`), **skills** from
skills.sh and ClawHub (`hermes skills`), and **MCP servers** from the Nous-approved catalog
(`hermes mcp`). The `aura-integrations` plugin gives the apps and the agent one door to all three:
a slash command for the apps and approval-gated tools for the agent. Reads are free; anything that
changes an install passes the approval gate and writes an audit line.

The three catalogues are the `kind`: `plugin`, `skill`, `mcp` (search and list also take `all`).

```
command.dispatch {name:"jintegrations", arg:"@<sid> search skill weather"}
  → {type:"plugin", output:"{\"ok\":true,\"kind\":\"skill\",\"action\":\"search\",\"count\":25,\"rows\":[{\"id\":\"skills-sh/steipete/clawdis/weather\",\"name\":\"weather\",\"description\":\"…\",\"source\":\"skills.sh\",\"trust\":\"community\",\"installed\":false,\"version\":null}, …]}"}
command.dispatch {name:"jintegrations", arg:"@<sid> list plugin"}
  → {type:"plugin", output:"{\"ok\":true,\"kind\":\"plugin\",\"action\":\"list\",\"count\":9,\"rows\":[{\"id\":\"aura-hud\",\"name\":\"aura-hud\",\"source\":\"user\",\"trust\":\"unaudited\",\"installed\":true,\"version\":\"0.1.0\",\"status\":\"enabled\",\"enabled\":true}, …]}"}
command.dispatch {name:"jintegrations", arg:"@<sid> inspect mcp filesystem"}
  → {type:"plugin", output:"{\"ok\":true,\"kind\":\"mcp\",\"id\":\"filesystem\",\"action\":\"inspect\",\"row\":{…},\"detail\":\"…\"}"}
command.dispatch {name:"jintegrations", arg:"@<sid> install skill quincy-weather"}
  → {type:"plugin", output:"{\"ok\":true,\"kind\":\"skill\",\"id\":\"quincy-weather\",\"action\":\"install\",\"exit_code\":0,\"output\":\"…\"}"}
```

Command shapes (the console calls each via `command.dispatch`, prefixing `@<sid>`):

| verb | shape |
|---|---|
| `search` | `@<sid> search <kind> <query>` (kind may be `all`) |
| `list` | `@<sid> list <kind>` (kind may be `all`; a bare `list` means `all`) |
| `inspect` | `@<sid> inspect <kind> <id>` |
| `install` / `update` / `remove` | `@<sid> install\|update\|remove <kind> <id>` |
| `enable` / `disable` | `@<sid> enable\|disable <kind> <id>` |

Every row carries the same fields, so one renderer draws all three catalogues:
`id`, `name`, `description`, `source`, `trust`, `installed`, `version` (a skill also carries `enabled`,
a plugin its `status`). `id` is what a later action takes — a skill's `identifier`, a plugin or MCP
`name`. `trust` is what can honestly be said about the item's provenance: a plugin is `bundled` or
`unaudited` (indexed ≠ audited), a skill carries its hub trust (`community`/`official`/…), an MCP
catalog entry is `nous-approved`. Unknown kinds, missing ids and catalogue failures answer
`{"ok": false, "error": "…"}` in the output.

**The agent** gets the same as tools under the `integrations` toolset: `integration_search`,
`integration_inspect`, `integration_list` (read-only) and `integration_install`,
`integration_update`, `integration_remove`, `integration_enable` (gated). The four gated tools pass
the `pre_tool_call` approval hook, whose message names the **kind, id, source/hub, trust level** and
**what the fetch will do** ("install skill 'quincy-weather' (source: clawhub; trust: community). This
will download a SKILL.md and its files…"), so "Jarvis, install the weather skill" only lands after
the person says yes. A plugin installs **disabled**; enable it with `integration_enable` as a
separate, also-gated step. `update` is plugin/skill only (MCP servers are not versioned here); a
skill or MCP server is enabled/disabled by installing/removing it or through its own config, not by
`integration_enable`.

Both paths write one JSON line per change to `<HERMES_HOME>/logs/integrations.jsonl`
(`{"ts","actor":"command"|"tool","session","action","kind","id"}`) — the command path is a person
clicking (no approval, but audited), the tool path is the agent (gated and audited). Reads write
nothing. Installs land in the Hermes home volume, so they survive a redeploy. The kinds, actions,
row fields and command shapes are fixed in `tests/contracts/hermes-integrations.json`.
