# Security & privacy model

Jarvis puts a tool-rich LLM with dangerous actuators (unlock, messaging,
shell, code-exec) next to untrusted input (web results, camera frames, MQTT
payloads, screen text, documents). The whole design assumes the model can be
tricked, and keeps the model out of the loop for anything that can hurt you.

This is the repo-level model. `aura-core/docs/security.md` is the detailed
one for the assistant itself — tokens, the API surface, what lands on disk.

## Threat model

* **Prompt injection** via web results, fetched pages, camera frames, MQTT
  payloads, notification text, or the user parroting an injected string.
  Mitigation: everything from outside is *fenced* as data before the model
  sees it (`aura-core/aura/integrations/web/fence.py`), the persona says
  untrusted text is never instructions, and — the part that actually holds —
  no dispatcher can be reached from fenced content without a fresh human
  approval. Wording is not the control; the tier is.
* **A compromised or hallucinating model** calling `execute_command` or
  applying a diff. Mitigation: the approval decision is made in code, outside
  the model, in two independent places (below). Persona wording cannot reach
  it. See `ToolRegistry.requires_approval` in `aura-core/aura/tools.py`,
  with adversarial tests (the orchestrator's second gate was retired with it, T-0276).
* **Blast radius if a gate ever failed.** The sandbox has no network, no host
  mounts, no Docker socket, no capabilities, no secrets, a read-only rootfs
  and tight resource limits. The only writable crossover is
  `./jarvis-workspace`, which holds nothing sensitive.

## Tiers

Policy is enforced on the device, outside the model. A server may only ever
**raise** a tier, never lower one.

| tier | examples | gate |
|---|---|---|
| 1 direct | lights, covers, climate, `get_user_context`, `web_search`, `web_fetch`, `list_settings`, tier-1 `*.tool.yaml` | none (safe / idempotent) |
| 2 background | `run_background_task`, `delegate_to_agents`, `code_task` | none, but runs outside the turn and reports back |
| 3 approval | unlock, notify, `execute_command`, `apply_code_task`, `write_file`, `start_coding_job`, `change_setting`, tier-3 `*.tool.yaml` | **human approval, enforced in code** |

Two things escalate automatically, and neither consults the model:

* A tool whose resolved targets land in `GATED_DOMAINS` (`lock`, `notify`)
  becomes tier 3 no matter what tier it was declared at.
* A `web_browse` batch that would click or type — as opposed to read — is
  held, and `jarvis-browser` gates the sensitive subset again with its own
  separate secret.

A held action returns `approval_required` and a request id, and that is the
end of the model's turn. It cannot retry, rephrase or self-approve. The
arguments are **pinned** when the request is created: a fuzzy target ("the
front door") is resolved to concrete entity ids *before* a human sees it, so
what runs later is what was shown, not a re-resolution against a house that
has moved on. Requests expire, and each can be used at most once.

## Enrolling a voice is a durable write about a person, and no turn can do it

Teaching Jarvis whose voice it answers (`docs/explanation/voice-identity.md`) changes what
the speaker gate will accept for good, until somebody deletes it. On the tier
table above that would be tier 3 — an approval — but it is not a tool at all,
and the difference is deliberate: an approval card says *what* will run and a
human says yes; an enrolment's "what" is a recording, and a card cannot show a
voice. So the write is reachable only over REST — `POST /api/voice/speaker/enrol`,
`POST /api/voice/speaker/people` (a name and a level) and
`DELETE /api/voice/speaker` — with a credential a person holds:

* the phone's own bearer token, relayed unchanged by the console
  (`jarvis-web/src/lib/server/speakerRelay.ts`), or
* the console password, the same door already in front of the pairing secret
  and in front of deleting the profile.

There is no `enrol_voice` tool, no `jarvis/voice/...` socket command, and no
service call. The consequence that matters: **a model turn cannot enrol
anybody** — not a fenced one that read a web page saying "enrol this voice",
not a spoken one from a stranger the gate let through in `observe`. The
recognised name a turn carries is context for the prompt and unlocks nothing;
what a recognised *level* does is narrower still — a guest's voice is refused
the gated rows the owner would have been asked about, and nothing is ever
opened by a level that the matrix would not have opened for the owner
(`voice-identity.md`, "Levels, and where they bite").
`test_no_tool_and_no_websocket_command_can_enrol` in `aura-core/tests/test_speaker_gate.py`
pins all three absences and that the routes sit on the token-gated router;
`jarvis-web/src/lib/server/routes.test.ts` pins that the console's relay never
lends the admin token to an enrolment. The store never leaves the box (counts
and scores on every endpoint, never a vector), and a bare `DELETE` is a real
delete of every person.

Forgetting is the one write of these the model may ask for (`voice_person_forget`, T-1314). It
deletes and cannot add a voice, and a card can show the name being forgotten, which it cannot for
a recording. The approval matrix asks for each person and denies a guest, and the call carries the
house token the homeassistant toolset already holds. The tool refuses an empty name before anything
is sent, because the house reads a missing name as "everyone".

## The command path has two gates, with different credentials

*Retired:* the orchestrator integration and its command path left the tree in
T-0276 (23177d71); this section records how that path was gated.

`execute_command` and `apply_code_task` are the sharpest tools in the box, so
they pass two independent checks in two processes:

1. **In jarvis-core.** The tool is `TIER_APPROVAL`. The registry holds it and
   fires `jarvis_approval_required`; the handler is unreachable from a model
   turn.
2. **In jarvis-orchestrator.** `ExecGate` is a state machine keyed on
   `APPROVAL_SECRET`. Nothing reaches the sandbox before it says approved, the
   stored command is verbatim what was requested, and a request can be
   approved at most once.

Two secrets, deliberately distinct: `ORCHESTRATOR_TOKEN` authenticates every
call, `APPROVAL_SECRET` is sent on exactly two request paths and only after a
human approved that exact action. **Possession of the API token alone cannot
execute anything** — that is the point of the split, it was tested in
the orchestrator's own suite and jarvis-core's orchestrator tests (both retired
with it), and jarvis-core logged an error at startup if both were set to the
same value.

jarvis-core also refuses to approve a command the orchestrator echoes back
differently from the one that was approved: if the stored copy does not match
byte for byte, nobody saw what would actually run, so it stops.

## Pairing needs a second secret, for the same reason

`POST /api/pair/claim` is the **only unauthenticated write in the API**, and it
has to be: the phone has no credential yet, which is the entire problem being
solved. It exchanges a short-lived code for a real token.

The obvious design — put the token in the QR — is worse than typing it by hand.
A QR on a screen is photographable from across a room, ends up in whatever
screenshot or shared window captured it, and a token in one stays valid
indefinitely. So the QR carries a **code**: 192 bits from
`secrets.token_urlsafe(24)`, five minutes, single use, removed before the token
is minted so two devices racing the same photograph produce exactly one token.
Failed claims are counted **per caller** — a global counter would let anybody
who can reach the endpoint lock the household out of pairing — and a claim
carrying an `Origin` header is refused outright, because browsers always send
one on a cross-origin POST and phones never do.

Minting a code needs `AURA_PAIRING_SECRET` **as well as** the API token, and
that is not belt and braces. jarvis-web's relay attaches the server-held admin
token to whatever connects, and its origin guard deliberately admits a client
that sends no `Origin`, because that is what a non-browser client looks like.
So a script with nothing but transient reach to the console's port is already
an authenticated API client — and with minting gated on the API token alone it
could mint a code, claim it, and walk away with a permanent token. Reach for as
long as the script runs, converted into access forever.

The same split as the orchestrator's, then: the relay never holds this secret.
It is typed into the pairing panel and forwarded. Unset means pairing is off
and every surface says so.

**Un-pairing is the half that makes pairing safe to offer.** Revoking a token
closes every socket authenticated with it, and tells them why, rather than
waiting for a reconnect that may be days away. The console's list is built from
the auth manager rather than from any pairing record: a token store that failed
to load would otherwise render as "no devices" over a live full-privilege
credential, with no way to see it and nothing to revoke.

## A question is shown to a person, so it says where its words came from

The tier system answers "may this run without a human". It cannot answer
"should the human believe what is on the screen", and those are different
questions for exactly one tool.

A held **action** displays pinned entity ids — resolved server-side when the
request was raised — which injected text cannot forge. A held **question**
(`ask_user`) displays the model's own sentence, so a turn that has read a
hostile page can put *"confirm your bank password"* in front of somebody in
Jarvis's voice, on the same consent surface they trust for everything else.

Every approval request therefore carries `tainted`, read from the same
`UntrustedTurns` store that already raises the tier for device commands. The
console draws a warning; the phone has no field for provenance, so it goes in
the words. Nothing is refused — a turn that read a page and needs to ask which
of three results was meant is the legitimate case, and an attacker who is
refused simply rephrases. Marking is the control that survives that.

The raising itself happens in one of two places, never both. The tool registry
holds every tool that is not declared read-only once the turn has read
untrusted content (`ToolRegistry.requires_approval`); a tool that applies the
rule at the surface that runs the action declares `escalates_itself` and is let
through — `control_device` is the one, because `device_control` raises the
device's own tier to CONFIRM with the reason verbatim, so the phone shows the
human the real action. Held at the registry as well, the phone never saw the
action and the server asked about "control_device" instead: two prompts, the
second naming the tool rather than the deed. The flag is declared, not
inferred, and a re-registration that starts claiming it is refused as a
weakening, so a tool that does not say so escalates like any other.

## A settings tool may change what the settings page can change, and nothing else

"How can I ask it to be able to edit settings with permission" (M67). The
model has `list_settings` (tier 1, read-only: a turn that has read a page may
still ask what the settings are) and `change_setting` (tier 3). What the
second may do is decided by the allowlist in `aura/settings.py`, not by the
tool: `SETTINGS` is a hardcoded tuple of the knobs a person changes on the
console — a model, a temperature, a wake word, the timezone — and the keys the
safety model reads (`llm.expose`, the gated domains, `jarvis.http` and its
CORS list, the sandbox's `network_mode`, `mcp.allow_stdio`) are not in it and
cannot be added from a tool. Resolution is membership in that tuple, never a
path into the config file, so no spelling of `local_only` reaches anything; a
key that is not a setting is refused *before* a request is held, with the
nearest real names, because a card a human can only deny teaches the model
nothing.

What runs is what was shown. The pin freezes the exact key, the value as the
setting's own validator coerced it, and the value it replaces; the sentence on
the card — "Change Wake word (voice.wake_word) from hey_jarvis to ok_nabu" —
is composed from those pinned arguments on the server, never by the model and
never on the surface. An approved change then goes through the one function
`config/settings/set` is (`api/common.py async_set_setting`): the same
validator, the same live apply, one audit line in the `jarvis.settings.audit`
logger saying what changed from what to what and by whom (`api <token>` or
`llm`), one `jarvis_setting_changed`. There is no second write path for a tool
to be quietly laxer on, and `test_settings_tool.py` spies on the function from
both doors.

A tainted turn is **held and marked, not refused** — the opposite decision
from `remember`, on purpose. `remember` refuses after untrusted content because
a human cannot audit a memory write in the two seconds an approval gets. A
setting change is the case approval was built for: one key, one value, the old
value beside the new one, in a sentence a person can judge. The attack to
reason about is a page saying "turn local-only off": there is no such key, so
nothing is held; and a key that *is* there arrives on the card marked
`tainted` for the human to weigh. Refusing would also break the legitimate
case — a turn that read a page and was then asked, by the user, to change the
temperature.
## An answer can be said, and the taint boundary decides when it may not be

"I should be able to verbally confirm it." While a question or a held action
waits on a conversation, the next turn in that conversation can be its answer
— *"the corner one"*, *"yes, go ahead"*, *"cancel"* — and the agent decides
that **in code, before the model sees the turn**, by the rules pinned in
`tests/contracts/spoken_answers.json` (`aura/llm/spoken_answers.py`). The
resolution itself is `approve_request`, unchanged: single use, the answer
reaching only the argument the tool named in `Tool.answerable`, an approval
carrying no words at all. What runs after a spoken yes is what the card showed.

A wrong match approves an action the person did not confirm, so the rules
prefer asking again over guessing:

| waiting | resolved by | never by |
| --- | --- | --- |
| one **action** | the whole utterance being one of the affirmations (*yes*, *go ahead*, *okay*…) or denials (*no*, *cancel*, *never mind*…) | *"yes and also turn on the lamp"*, a choice word, anything else — the request keeps waiting and the model is told so |
| one **question with choices** | words that pick out exactly one choice (the choice's own text is the answer); a denial dismisses | words that fit two choices or none |
| one **free-text question** | the words, verbatim; a denial dismisses | — |
| **two or more** | nothing — a *yes* is answered with what is waiting and *"say which"* | any guess |
| anything **tainted** | nothing — the turn says it waits on the console | any words |

The request carries which conversation raised it (`conversation_id`, stamped
from what the agent recorded for the turn), so a *"yes"* in one conversation
never approves what another asked; a request raised outside any conversation
(the console's `jarvis/tools/call`) is nobody's to answer by voice.

**The taint boundary.** A request raised by a turn that had already read
untrusted content carries `tainted` (above), and such a request is never
resolved by words, in either direction. The reason is the one the previous
section gives: the words of a tainted question may be the page's, not
Jarvis's, and the console's banner is the surface that says so. A spoken
*"the corner one"* to a question an injected page composed would answer the
page. So the turn replies that the request is waiting on the console, where
the provenance is on screen, and leaves it exactly as it was. The rule is a
single check in `spoken_answers.decide`, and the contract's cases pin it for
an action, a question with choices and a free-text question alike.

## Everything that comes back is untrusted too

Search results, fetched pages, crawled pages, specialist-agent prose,
generated diffs and command stdout are all wrapped in
`<untrusted_web_content>` markers with a notice before the model sees them.
Content cannot close its own fence — the markers are escaped inside the body.
"Another model wrote it" is not a trust boundary: a delegated agent that read
a poisoned page must not be able to smuggle an instruction back.

The markers are the half the model sees. The half that holds is
`mark_untrusted_result`, which taints the *turn*: once a turn has read
somebody else's words, no dispatcher can be reached from it without a human.
Any tool that returns bytes from a machine that is not this one must call it —
including YAML- and console-defined tools, which fetch an arbitrary HTTP
endpoint. A `note` on the result asking the model to treat the body as data is
worth saying and is **not** a control, because a hostile endpoint's reply is
exactly the text that talks a model out of following a note. Error paths count:
a 500 whose body is the remote server's words is as much an injection vector
as a 200, and is cheaper to return.

## The relay is a door, and it now asks who you are

`jarvis-web` holds the admin token so the browser never does, and relays the
console's WebSocket to jarvis-core at `/ws`. Authorisation for that socket was
a same-origin check — correct as far as it goes, because only a browser sends
`Origin` and only a browser can be tricked into a cross-origin request.

The gap was what happened when there was no `Origin` at all. `isOriginAllowed`
returns true for a missing one, so **anything that was not a browser — the
phone, curl, any script that could reach port 8199 — was handed the admin
token's full control of the house without presenting anything**, while
jarvis-core next door required a bearer token for exactly the same power. The
firewall was the only thing in the way, which makes "LAN" a trust boundary the
rest of this document does not otherwise grant.

Now the socket asks:

| client | brings | relay does |
| --- | --- | --- |
| browser console | same-origin `Origin` and a console session (cookie), no token | injects the admin token, swallows the handshake |
| Jarvis app, scripts | `Authorization: Bearer <jarvis-core token>` | passes the token through; **jarvis-core** decides |
| anything else | neither | `401`, before a socket exists |
| a page on another origin | a foreign `Origin` | `403`, as before |

Pass-through matters as much as the refusal. The relay does not vouch for a
client it cannot authenticate; it forwards the credential and lets jarvis-core
remain the single authority on tokens. A revoked token stops working
everywhere at once, which would not be true if the relay kept minting access
of its own.

It is also what lets one URL do everything: with the handshake identical on
both, the app can be pointed at the console and reach voice, management and
TTS through it, instead of needing to know which of two servers is which.

### Origin names the page, not the person (T-0387)

Since the house left Tailscale (2026-10-27) port 8199 is on the LAN, and an
`Origin` header is a string any LAN client can type. With a console password
set, the console lends its server-held keys (the adapter key on `/hermes/*`,
the admin token on `/ws` and `/api/*`) only to a browser holding a console
session. `admit` in `jarvis-web/src/lib/server/consoleAuth.ts` is the one
rule: `/api/*` and `/hermes/*` answer `401` to a request with neither a live
session nor the adapter key as its bearer, and both socket relays refuse the
same, and refuse everything if the check is not registered. Five paths are
exempt, matched on whole segments: `/api/console` (the sign-in),
`/api/pair/claim` (a single-use 192-bit code), `/api/voice/speaker/**` (each
route relays the phone's own token, or the admin token only for a signed-in
console, `speakerAuth` in `speakerRelay.ts`), `/api/config` (the phone's server
discovery: the backend URL and which settings are present, never a value) and
`/api/models/<name>` (a model download that passes the phone's own token
upstream and attaches nothing of the server's).

The phone and the car have no password to type. Their web view's page load
carries the adapter key they were paired with, and a keyed `text/html` load is
answered with a 30-day session cookie that the view's own fetches and sockets
then carry. A browser with no session meets the lock screen as its sign-in,
password only. Sessions are kept in `/app/.storage/console-sessions.json`
(0600), so a restart signs nobody out.

What it does not cover:

* No password set leaves the console open: that is the owner's "Later" on the
  setup sheet, and it keeps a first run working. A deploy that lost the
  password is caught by `scripts/console-gate-check.sh` in the deploy smoke,
  which fails when an unsigned request is answered.
* A keyed page load turns the adapter key into a 30-day session. That gives
  nothing the key did not already give: the key opens the agent itself.
* The cookie is `Secure` only when Caddy says the browser came over https
  (`X-Forwarded-Proto`). adapter-node calls every request https when `ORIGIN`
  is unset, which marked it `Secure` on plain `http://<lan>:8199`, where
  Chromium drops it and nobody stays signed in.

### The phone's tunnel carries the console and nothing else (T-0394)

The phone app runs its own link home (a scanned WireGuard tunnel run
inside the app), and its web view reaches the console through that link's
HTTP proxy on loopback. Any app on the phone can open a loopback port, and on
a WireGuard link every request went to the proxy, which dialled whatever it
was asked: any app could reach any home host and port through Jarvis's tunnel.

Now the web view sends the proxy only the console's host (a reverse bypass rule
of that one host), and the proxy carries only the console's `host:port`
(`ProxyAllow` in `android-app/tailnet/tailnet.go`, set by `ConsolePage` on each
load); anything else is answered `403` before anything is dialled.
`TestProxyRefusesWhatTheAppDidNotAllow` and `ConsoleProxyTargetTest` pin it.

What it does not cover: another app on the phone can still reach the console
itself through the proxy, which then answers it as it answers the LAN (the
console gate above). The forwarders the app's own sockets use each have one
fixed target that asks for its own key.

## Isolation matrix

| service | network | user | rootfs | mounts | caps |
|---|---|---|---|---|---|
| jarvis-core | host (:8080), LAN/WG via ufw | non-root | rw (image) | `aura-core/config` | drop ALL |
| jarvis-web | host (:8199), LAN/WG via ufw | node | rw (image) | `./.storage/jarvis-web` | default |
| jarvis-browser | host (:8210), loopback via ufw | non-root | rw (image) | none | drop ALL (`seccomp:unconfined` for Chromium) |
| jarvis-orchestrator | host (:8188), core host only via ufw | 10002 | read-only | `./jarvis-workspace` | drop ALL |
| jarvis-sandbox | **none** | 10001 | read-only | `./jarvis-workspace` | drop ALL |

`network_mode: host` is used so the `127.0.0.1` defaults reach jarvis-core
(8080) and Ollama (11434) on the same host, and so ufw — not Docker's DNAT,
which can bypass ufw — is the single authority on who may reach a port.

**`jarvis-sandbox` must never get host or LAN networking.** It executes
LLM-proposed commands; `network_mode: none` and the workspace file-queue are
its containment. This is asserted by `scripts/egress-audit.sh`, by the compose
config, and by `aura-core/tests/test_packaging.py`, which fails if the
commented service in `aura-core/docker-compose.yml` ever loses it.
`no-new-privileges` on both isolated services; sandbox `mem_limit 1g`,
`pids_limit 128`, `cpus 2`, tmpfs `/tmp`.

## Outbound audit → zero cloud at runtime

Every host the stack can reach outside the house, what each is sent, its
off switch and a verdict, from the code and a live capture of the running
containers, is [`privacy/egress.md`](../privacy/egress.md) (T-0767). In short:
web searches through the stack's own SearXNG, pages someone asks to read, the
weather (with the house's position rounded to 0.1°, T-0765), and a short list
of scheduled fetches that carry nothing about the house but its address.

There is no cloud fallback anywhere. If SearXNG is down, `web_search` fails
and says so; it does not quietly ask somebody's search API instead. The one
fallback it has is another SearXNG — the stack's own, when `SEARXNG_URL` names
an instance elsewhere that cannot search — and the result says which one
answered (`aura-core/docs/search.md`).

Run `scripts/egress-audit.sh` against the live stack: it proves the sandbox
has only `lo` and cannot reach the LAN gateway or the internet, and flags any
orchestrator egress you did not intend.

### The house's own model server: checked at startup and again before a request

While `local_only` holds (the default for `llm:` and for `vision:`), a model
server url that resolves to a public address is refused at startup. A name
that does not resolve yet is let through there on purpose: on a first boot a
compose service may not have started, and a house that will not start because
DNS was slow is the worse bug. Until T-1461 that was also a way round the
rule: a public host name and a moment without DNS at boot, and every
conversation or camera frame went to it afterwards.

`LocalOnlyGate` (`aura-core/aura/integrations/llm/__init__.py`) is the second
look. It sits on the model's HTTP client and holds every request to the model
server's host until the name has resolved to a local address; a public answer
refuses the request and says what to change, and a camera look is refused
before the camera is read. An answer stands for a minute, then the name is
looked up again, so a record that changes later is caught at the next look. A
name already shown to be local keeps that answer through a moment without DNS.
The gate takes the host from the HTTP client's own URL parser, the one that
names the host on each request, so the two cannot spell it differently (a
punycode name, T-1463).

The reranker is under the same rule (T-1462). It is sent the user's query with
memory texts (`memory: rerank_url`) or with a research run's search snippets
(`research: rerank_url`), to a url of its own through a client of its own, so
the model server's gate never saw it. `local_only_gate` gives each rerank host
one gate for the life of the process, and the reranker asks it in the one
method that sends: a url that resolves public, a public address, or a name
that does not resolve is sent nothing, the search keeps the order it had, and
the log says why once. There is one rule for the house, so it follows
`llm: local_only` (on unless that says false), whether or not the house runs
a chat model of its own. An address is judged once and never looked up again.

So is memory's embedder (T-1464). `memory: embedding_url` is sent every memory
text when it is written and every query when something is recalled, and the
files index embeds through the same client. The same per-host gate is hooked
onto the HTTP client the embedder sends with, so nothing that uses that client
can skip the look; a refused embed is a transport error to the index, which
answers by keyword as it does when the embedder is down. With no
`embedding_url`, memory embeds through the chat client, which the model
server's gate already holds.

A model server's own answer cannot send the request elsewhere either
(T-1466). The chat client and the embedder's client follow redirects, and a
307 keeps the method and the body, so a local server that answered with one
had the same prompt or memory text sent to whatever host its `Location` named.
The gate now looks at each redirect its host answers with, before the client
builds the next request: the same host is followed, another host has to be
shown local by the same lookup, and anything else (a public name, a public
address, a name that does not resolve, no host at all) is refused with the
transport error callers already handle. A host a redirect was let through to
is held to the same rule for its own redirects, and one server's redirects
may lead to eight other hosts at most. The check is installed for every gated
host, including an address or a `localhost` that needs no lookup of its own.

What it does not cover: the HTTP client resolves the name again itself, so a
record that flips between the two lookups (rebinding) has up to a minute; a
redirect answered by a host no gate is about is not looked at (the chat
client is also the one the house's own tools reach the internet with); the
model catalogue's own client, which reads model lists and sends no text, is
not gated; and `local_only: false` removes the gate, which is what that
setting is for. On production the chat model is Hermes's, not the
house's (`llm: enabled: false`), so this is the camera model's, the
reranker's and the embedder's guard there.

## Firewall

`scripts/apply-firewall.sh` (ufw) — deny incoming, allow `wg0`, allow the LAN
to the jarvis-core/Ollama/Wyoming/HUD ports, keep photon, jarvis-browser and
SearXNG on loopback, allow only the jarvis-core host to the orchestrator, and
(post-setup) deny outgoing except the SearXNG path and `wg0`. `DRY_RUN=1`
previews. Transport is LAN/WireGuard only, no port forward.

## Retention

`aura-core/config/configuration.yaml` sets `recorder: purge_keep_days` (10
by default) with `auto_purge`, and excludes high-frequency sensor noise and
`call_service` events from the database entirely. No raw audio is stored by
the voice pipeline — transcripts and in-memory traces only.

`jarvis.db` is the sensitive artefact and it is easy to underestimate: it
knows when you get up, when the house is empty, and when the front door
opened. Treat a backup of `/config` as a copy of your keys — encrypt it, and
keep `purge_keep_days` no longer than you actually need.

## Updates

Pin image digests in a deploy overlay; stage changes; keep the previous digest
for a documented one-command rollback (`docker compose up -d` with the old
digest). OpenCode is pinned by `OPENCODE_VERSION` in its Dockerfile.
