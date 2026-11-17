---
name: gate
description: Run this repo's offline test gate the way CI runs it — bootstrap .venv if needed, ruff, every Python suite with CI's timeout flags, both evals, the Android mirrors and CI's static checks; `web` adds the aura-web suite. Use before pushing, after any non-trivial change, or when asked to run the tests.
---

# /gate — the offline gate, as CI runs it

Arguments: `$ARGUMENTS`. Empty runs everything offline. `web` adds the aura-web suite. A suite name (`lint`, `core`, `desktop`, `browser`, `services`, `evals`, `contract`, `android`, `static`) runs only that one.

A `claude/*` branch gets no CI, so this is the only gate. Nothing below needs hardware, models or the network, except a first `npm ci`.

## 0. Environment

Every shell is fresh: start each command with `. .venv/bin/activate`. If `.venv/bin/ruff` is missing, recreate the venv with the commands under "Environment" in CLAUDE.md. `aura-schema` must be installed editable in the venv (`pip install -e aura-schema`): the plugins, the adapter and `make docs` import it. For `web`, run `cd aura-web && npm ci` if `node_modules/` is missing.

## 1. Run, in this order, each in its own subshell

CI's `--timeout=120 --timeout-method=signal` is not belt-and-braces: two jarvis-core voice tests hang forever on 3.12 without it, and a hang must surface as one named failure, not a stalled run.

```bash
python3 -m ruff check .                                                                        # lint
( cd aura-core    && python3 -m pytest tests -q --timeout=120 --timeout-method=signal )      # core
( cd aura-desktop && python3 -m pytest tests -q --timeout=120 --timeout-method=signal )      # desktop
make test-desktop-e2e                                                                          # desktop agent against a real house; fails, not skips, without its harness
( cd evals && python3 -m pytest test_routing.py test_resolution.py -q --timeout=120 --timeout-method=signal )      # evals
python3 -m pytest hermes/plugins deploy/tests -q --timeout=120 --timeout-method=signal                             # plugins + deploy
python3 -m pytest bench/tests aura-adapter/tests aura-schema/tests -q --timeout=120 --timeout-method=signal    # bench, adapter, schema
make test-android                                                                              # android mirrors
make lint-python lint-web lint-android dup schema-check docs-check                             # the v2 quality gates (Phase 2)
```

Static checks — CI's `static` job, minus the contract suite already run above. The marker pattern is written with brackets so this file could never trip the scan it describes.

```bash
find . -name '*.sh' -not -path './node_modules/*' -not -path './.git/*' -not -path './.venv/*' -print0 | xargs -0 -n1 bash -n
python3 -m compileall -q hermes aura-adapter deploy scripts aura-core/aura aura-desktop evals android-app/tools
! grep -rnIiE '\bM[U]TANT\b|\bDELIBERATELY BR[O]KEN\b' \
    --include='*.py' --include='*.kt' --include='*.kts' --include='*.ts' --include='*.js' --include='*.svelte' \
    --include='*.sh' --include='*.yml' --include='*.yaml' \
    --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=build --exclude-dir=.svelte-kit \
    --exclude-dir=__pycache__ --exclude-dir=.venv --exclude-dir=.claude . | grep -v '^\./\.gitlab/ci/ci\.yml:'
for f in aura-core/docker-compose.yml deploy/docker-compose.yml; do docker compose -f "$f" config -q; done   # deploy/ needs its .env: use --env-file deploy/.env.example
```

`web` (only when asked — it is slow and needs `node_modules`; Playwright additionally needs `npx playwright install chromium` once):

```bash
( cd aura-web && npm run build && node scripts/offline-asset.mjs --check && npm test )   # the check is every HUD job's first step in CI: stale, and all of them fail before a test runs (T-1129)
node scripts/verify/hud-size.mjs --apk=/nonexistent   # CI's hud-size job; a wanted growth is re-recorded with --record and a note
( cd aura-web && npx playwright test )
```

Unlike `make test-web`, do not swallow a Playwright failure.

Before a push (committed work only; needs Docker and the network, several minutes):

```bash
make ci-jobs    # each Python test job's own lines in its own image on a clean clone; `make ci-job JOB=test:adapter` for one
```

The suites above run in the venv on this machine, which has every suite's packages, the host's
tools and a Hermes install beside the checkout. The runner has none of those: a job can be green
in every command above and red there (T-1442). `make ci-jobs` is what the runner sees.

## 2. Report

- A table, one row per suite: passed / failed / skipped, runtime — the shape of "Suite sizes" in `docs/verification.md`, so the numbers can be compared and, via `/claims`, re-measured into it.
- Everything that was **not** run, and why (no Docker daemon, no `node_modules`, no chromium).
- A failing test by name with its assertion text. Do not summarise it away, and do not re-run until green and call it flaky without saying so.
- Never weaken a test, add a skip, or leave a marker to get through. If a failure is already present on the base branch, show that (run the one test against the base commit) and say so.
