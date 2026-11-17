# CLAUDE.md

Jarvis is a self-hosted voice/home assistant. The brain is Hermes Agent, run as the `jarvis` container with the plugins under `hermes/plugins/`; `aura-adapter/` is the one origin the apps talk to; `aura-core/` the house service (devices, voice identity, automations, the HA-shaped REST); `jarvis-web/` (SvelteKit) the console; `android-app/` (Kotlin) the phone and the car; `aura-desktop/` the desktop agent and `jarvis-desktop-app/` (Tauri, ADR-0051) the one-binary app that carries it; `aura-schema/` the one settings schema; `aura-voice/` and `deploy/extract/` the voice and the page extractor; `bench/` the benchmark. One repo, no workspace tool; each Python tree has its own `tests/`. `android-app/`, `jarvis-web/` and `bench/` have their own `CLAUDE.md`. Check `docs/` (index `docs/README.md`), `DEVIATIONS.md` and `aura-core/docs/` before inferring behaviour from code. Tasks live in Sampo (`sampo state`); the current brief is `docs/design/v2/PROMPT.md`, its ledgers `DECISIONS.md` and `GAPS.md` at the root.

## Environment

Nothing is installed system-wide (no pip, no `python3-venv`, no sudo). Python deps live in the repo-local `.venv` (gitignored), and every shell is fresh: `make` finds it itself (a lane borrows the main checkout's); prefix the rest: `. .venv/bin/activate && `. Recreate a missing `.venv` (`python3 -m venv .venv` fails here, no ensurepip):

```bash
python3 -m venv --without-pip .venv
curl -sSf https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py && .venv/bin/python /tmp/get-pip.py
.venv/bin/python -m pip install pytest pytest-asyncio pytest-timeout "ruff==0.16.3" \
  shellcheck-py actionlint-py hadolint-bin \
  -r aura-core/requirements.txt -r testing/requirements.txt -e aura-desktop -e aura-schema
bash scripts/install-age.sh .venv/bin   # else test_backup.py skips 27 of 31
```

In a lane, run `make lint-python` before returning a commit.

Local Python 3.11; CI 3.12. The console needs `cd jarvis-web && npm ci` once.

## Commands

- `make test` — the offline gate: ruff + every Python suite + both evals. `make help` lists the rest; `make test-web` and `make test-android` cover the console and the phone's mirror.
- One suite with CI's flags: `cd aura-core && python3 -m pytest tests -q --timeout=120 --timeout-method=signal` (two voice tests hang forever on 3.12 without it); one test: `-k name`. Hermes side: `cd aura-adapter && python3 -m pytest tests -q`; `python3 -m pytest hermes/plugins/<plugin>/tests hermes/plugins/tests -q` (shared matrix tests in `hermes/plugins/tests/`); `pytest deploy/tests`. Run `make lint-python` (mypy) before a push: it catches what ruff and pytest miss.
- CI's gates beyond `make test`: `make lint-web`, `make lint-python`, `make dup`, `make gate-hud`, `make token-lint`, `make schema-check`, `make tokens-check`, `make docs-check`. `make ci-jobs`: Python jobs in CI's images. `/gate` runs them the way CI does; `/claims` reconciles `docs/verification.md`; `/pr` opens the merge request.
- Generated files — edit the source, run the target, commit both (each carries a `@generated` header; CI fails a stale copy): `deploy/config.template.yaml` and the settings contract from `aura-schema/aura_schema/templates/config.template.yaml.in` (`make schema`); every token file from `design/tokens.json` (`make tokens`); `docs/reference/*` and `CAPABILITIES.md` (`make docs`).
- Stacks: `make up` / `make down` run the house stack (`aura-core/docker-compose.yml`). The Hermes stack (`deploy/docker-compose.yml`) runs from `/opt/jarvis-host`; never start it from a checkout or a worktree (its compose project is production). This checkout is a dev box and the production host: restarting house containers is fine; Hermes containers change only through a deploy (`scripts/deploy-here.sh` here, `scripts/deploy.sh` from CI).

## How a request flows

- **Apps → adapter → Hermes.** The console, phone and desktop talk only to `jarvis-adapter` (:8643, bearer key; `/ws`, `/api/audio/*`, `/api/status`, `/api/pair`, `/v1/*`). It relays the HUD socket to `hermes serve` on loopback 9119 (`prompt.submit`, `command.dispatch` for `/goal`, `hud.panel.*`, approval frames) and proxies `/v1/chat/completions` to the API server on 8642. The wire contract is `docs/hermes-hud-protocol.md`, pinned by both app suites through `tests/contracts/*.json`.
- **Voice.** App records → `POST /api/audio/transcribe` (faster-whisper; the house's speaker gate issues a two-minute `speaker_ticket` that `prompt.submit` carries) → streamed reply → `POST /api/audio/speak-pcm` (CosyVoice on GPU, sentence by sentence; Piper only when the host sets `TTS_PROVIDER=piper`, `docs/runbooks/voice.md`; offline, the car says its fixed lines from cached clips). `hud_say` is how the model speaks on purpose.
- **Tools and approvals.** Each `hermes/plugins/<name>/` gives the model a house capability. `hermes/plugins/_shared/_matrix.py` is the approval matrix (ask / free / denied for a guest), enforced by `jarvis-guard` through `_shared/_approval_hook.py` and pinned row by row in `hermes/plugins/tests/test_matrix.py`. A tool with no row is asked about every time, so a new tool needs a row. `hermes/home/` seeds the persona; `hermes/skills/` the procedures. Approvals on production are `manual`.
- **The house.** `jarvis-core` (:8080) owns entities, automations, MQTT, voice identity and the phone/desktop device channel; Hermes reaches it through the `homeassistant` toolset with `HASS_TOKEN`.
- **Config.** `aura-schema` is the one settings schema; plugins read settings only through it (`aura-schema/tests/test_plugin_env_conformance.py`). The entrypoint renders `config.yaml`/SOUL/USER/MEMORY into the `jarvis-hermes-home` volume on first start only; `CONFIG_LEAVES` in `deploy/entrypoint.sh` is how a new key reaches a rendered volume.

## Conventions that differ from the defaults

- Ruff runs a deliberately defect-only ruleset (`ruff.toml`). Do not add style rules, run a formatter, or fix quote style, import order or line length in passing.
- Python: `from __future__ import annotations`, PEP 604/585 types, double quotes, 100 columns, `_LOGGER = logging.getLogger(__name__)`. Svelte/TS: tabs, single quotes, semicolons. Match the file you are in.
- Comments say *why* and name the failure they prevent; docstrings say what a guarantee does **not** cover.
- Security tests assert the decision, not stored state (T-0777).
- Logic that exists in two languages (Kotlin ↔ `android-app/tools/*.py`, server ↔ console) is bound by a shared table in `tests/contracts/*.json` that both suites read, never by a "keep in step" comment.
- `MUTANT` and `DELIBERATELY BROKEN` in any source or yaml file fail `make check-markers` (CI's `test:lint`).
- No real name, place, host, address, phone, device address, home path or key in a tracked file: `make private-values` fails on one. Tests use made-up values (`192.0.2.x`, `example.com`, `.test`); a machine's own values go in the untracked `.private-values`.
- `aura-core/tests/test_packaging.py` pins `docker-compose.yml` ↔ `configuration.yaml` ↔ `.env.example`: change one, change the others.
- Never type a colour, size, font, radius, shadow or duration in app code: `design/tokens.json` is the only place a value lives. `design/token-lint.baseline.json` is a ratchet; never raise it.

## Git

- Work on `claude/jarvis-overhaul`; its merge request's pipeline deploys, and merges land on `main`. CI is GitLab (`.gitlab-ci.yml` + `.gitlab/ci/*.yml`, `docs/ci.md`); a `claude/*` branch with no merge request gets no pipeline. Run `/gate` before pushing; CI's runner is this host, so heavy local work during a pipeline fails its frame-budget jobs.
- Commit subjects are sentence-style, no conventional-commit prefix, and describe the behaviour change. Bodies are long: what was wrong, how it was reproduced, what the change does *not* fix, suite counts last. Keep the `Claude-Session` trailer.
- A change that moves a claim in `docs/verification.md` or a judgement call in `DEVIATIONS.md` updates it in the same change, with the command that demonstrates it.

## Gotchas

- The only precious local state is `.env`, `aura-core/.env` and `/opt/jarvis-host/.env` (real tokens, gitignored); never overwrite them from `.env.example`. Never `git clean -xfd`: it deletes ~200 MB of untracked models (`aura-core/wyoming/`, `aura-core/config/models/`).
- The Jarvis Code sandbox must never get host or LAN networking (`.claude/rules/jarvis-code-sandbox.md` loads when you touch it). Every port of the deployed stack binds the address in `TAILSCALE_IP` (the host's LAN address; the name is kept) or loopback.
- `jarvis-adapter` shares the `jarvis` container's network namespace: after any recreate of `jarvis`, recreate the adapter too (a restart orphans it). After a reboot `deploy/boot-recover.sh` recreates the LAN-bound ports.
- `pkill -f`/`pgrep -f` match their own shell (exit 144); select by executable.
