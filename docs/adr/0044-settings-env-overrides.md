# ADR-0044: Env-backed settings are written to an override file the container exports at start

- **Status:** Accepted (2026-09-18)
- **Comes from:** the completion brief row 2.4 (settings: every leaf shows its source and can be changed), research of 2026-09-18
- **Builds on:** [ADR-0017](0017-one-settings-schema.md) (one settings schema), [ADR-0035](0035-settings-three-tiers.md) (three tiers, live-apply where the runtime allows), [ADR-0026](0026-approval-matrix-audit-log.md) (the approval matrix)

## Context

The schema has 177 leaves. 164 are env-backed (`Setting.env`); 36 carry a Hermes config key the
`jarvis-settings` plugin could write to `config.yaml` (`jsettings set` over the HUD socket), 23 of
them env-backed as well, and 14 of them the agent tool's contract keys. The
console drew every leaf but wrote an env leaf to jarvis-core's env store, which refused 147 of
them ("not a variable .env.example names") and applied the rest to the wrong container. No row
said where its value came from — `list` had no `source` — and a `set` from the console (actor
`command`) never met the approval matrix's `settings_set` ASK row, which only sees the agent's
tool.

What the container can and cannot do was measured, not assumed:

- The host `.env` is not mounted anywhere. The gateway cannot edit it.
- `config.yaml` is read by Hermes alone. The adapter, the compose file and the phone/desktop
  apps do not read it, so "make everything a Hermes key" leaves the adapter's leaves
  (`adapter.*`, `network.*`) unreachable.
- The compose file resolves some names on the host — `${TAILSCALE_IP}` in a port bind,
  `${AURA_TAG}` in an image, `HERMES_API_KEY` renamed to `API_SERVER_KEY` on its way into the
  gateway, `LLM_MODEL` renamed to `AURA_MODEL` on its way into the adapter — and nothing inside
  a container can re-read those.
- The entrypoint runs as root on every start (an s6 cont-init step) and acts on
  `SELFEXT_REPO_PATH` (`cp -a`, `git init`, `chown -R hermes`) before the gateway exists.
- The gateway's uid can write anywhere under `/opt/data`, which is inside the approval matrix's
  free write roots.

## Decision

**An env-backed leaf is written to `$HERMES_HOME/.storage/environment.json`** — a JSON object of
`{"<ENV_NAME>": "<value>"}`, mode 0640, owner the gateway's uid and group, in a 0711 directory (the adapter's uid is in that group). On every
start the entrypoint exports each entry over the container environment before `config.yaml` is
rendered (so the render, the pinned `CONFIG_LEAVES` and every later step see it) and writes it
into s6's `container_environment`, which is where the supervised gateway service takes its
environment from. The adapter reads the same file at its own load through the same schema helper.
**A value set this way applies after a restart**, the way jarvis-core's own env store applies its
writes: the row says `applies: "restart"` and the console says so.

**A leaf with both an env name and a Hermes key is written to both.** The Hermes key is what the
gateway reads now (`applies: "now"` unless the schema marks it `restart_needed`); the override is
what the next render and the pinned leaves read from, so the two cannot disagree after a restart.

**The schema decides what is fixed outside the container.** `aura_schema.HOST_OWNED_ENV` names
the env leaves an in-container override cannot change: compose-resolved names, the phone and
desktop's own settings, and the two names the entrypoint acts on as root. A test derives that set
from `deploy/docker-compose.yml`, `deploy/.env.example` and `deploy/entrypoint.sh` and fails when
the tuple drifts. Those rows answer `applies: "compose"`, `settable: false`, and a `set` is refused
with the env name and the instruction (edit `.env`, recreate). The same helper,
`load_overrides`, filters the file for the entrypoint, the adapter and the plugin, so a name that
is not an overridable JARVIS setting — `LD_PRELOAD`, a path root chowns — never reaches an
environment whatever wrote the file; with the schema unimportable the entrypoint applies nothing.

**The override file and the proposals file are sealed** in the approval matrix, beside
`config.yaml`: a model that could write the file could change what JARVIS is without the
`settings_set` gate.

**A `set` from the command path that the matrix would ask about does not write.** It becomes a
proposal `{id, key, before, after, source, applies, ts}` in `.storage/settings-proposals.json`
(0600), and `jsettings apply <id>` — the person's yes, the same act as answering the agent's
approval prompt — validates again, refuses under the kill switch, writes and audits with the
proposal id. `discard` drops it; `proposals` lists what waits. The agent's tool path is unchanged:
its `pre_tool_call` hook asks, and an approved call writes at once.

**Every row carries its source.** `jsettings list` and `get` serve every leaf with `source` ∈
`default | config | env | override`, `settable`, `applies`, and a secret's value masked. The
agent's `settings_list` keeps to the 14 contract keys: 177 rows in a tool result is the context
cost the matrix's own comment on `settings_find` warns of.

## Consequences

- **Good.** Every leaf the console draws has one honest write path and one honest answer to
  "where does this come from" and "when does it apply". The 147 refusals are gone; the leaves that
  cannot change from inside — 36 of them, the compose file's — say so instead of pretending.
- **Good.** One filter, one path constant, one `applies` rule, all in the schema; the entrypoint,
  the adapter and the plugin cannot disagree about which names are overridable.
- **Bad.** Secrets set this way live in clear in the override file, beside the same secrets in the
  host `.env`, and now on the backed-up volume. The file is 0640 and sealed against the model; it
  is not encrypted. Rotating a secret means rotating it in both places.
- **Bad, bounded.** The adapter runs as uid 10001 and the gateway as 10000, so the file is
  written 0640 and the adapter's image puts its user in the gateway's group (gid 10000); a file the
  adapter still cannot read (an older volume, a hand-made 0600) makes it log one warning and run on
  its environment alone rather than fail to start.
- **Not covered.** Compose-level keys — ports, binds, image tags, values the compose file renames
  on the way into a service, values handed to containers that do not read the file (the console,
  SearXNG, CosyVoice, the backup), and the phone/desktop's own settings. Those leaves keep
  `restart_needed`, answer `applies: "compose"`, and the UI says "compose file". A `both` leaf
  whose env name is host-owned (`model.default` / `LLM_MODEL`) writes the Hermes key only.
- **Not covered.** A live apply for env leaves. The gateway process reads its environment once;
  "restart" is the truth and the row says it.
- **Not covered.** The console's own UI for sources, proposals and the "compose file" wording —
  another package, coded against the command contract in `docs/hermes-hud-protocol.md`.
