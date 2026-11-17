# Jarvis — top-level developer tasks.
# `make help` lists targets. `make test` runs everything runnable in CI/dev
# (no hardware, no models, no network). Hardware gates live in `make smoke`
# and docs/verification.md.

SHELL := /bin/bash
COMPOSE := docker compose
# The profiles the deployed configuration.yaml needs: `mqtt:` and `sensors:`
# are switched on (M57) and `web:` searches through SearXNG, and neither
# mosquitto nor searxng starts without its profile — the core then logs a
# broker it cannot reach at every boot and the live rig's sensor scenario is
# refused before it starts (26 Aug). `?=` so an operator's .env or shell wins.
export COMPOSE_PROFILES ?= mqtt,search

# The repo's own Python goes first on PATH when it exists, so a recipe's `python3` (and every
# tool a recipe or its scripts start: ruff, mypy, pytest) works in a shell that never ran
# `. .venv/bin/activate`. `make docs-check` died on "No module named 'yaml'" twice that way
# (T-1387). A worktree has no .venv, so it borrows the main checkout's, found through git's
# common dir. With no .venv anywhere (CI) PATH is untouched. JARVIS_VENV overrides the guess.
JARVIS_VENV ?= $(firstword $(wildcard $(CURDIR)/.venv) \
  $(wildcard $(abspath $(dir $(abspath $(shell git rev-parse --git-common-dir 2>/dev/null))))/.venv))
ifneq ($(wildcard $(JARVIS_VENV)/bin/python3),)
export PATH := $(JARVIS_VENV)/bin:$(PATH)
endif

.DEFAULT_GOAL := help

.PHONY: help
help: ## show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-20s\033[0m %s\n",$$1,$$2}'

# --- tests ------------------------------------------------------------------
# The timeout flags are CI's (.gitlab-ci.yml) and they belong here too: without them this
# target can HANG rather than fail — a run on 2026-09-07 sat at 0 % CPU for eleven minutes
# and would have sat there all night. CLAUDE.md documented the flags under "one suite, with
# CI's flags" while the gate everyone actually runs went without them. (Comments live here,
# outside the recipe: inside it, make echoes each one as if it were a command.)
.PHONY: test-core
test-core: ## jarvis-core: the assistant itself (the big one)
	cd aura-core && python3 -m pytest tests -q --timeout=120 --timeout-method=signal

.PHONY: test-desktop
test-desktop: ## jarvis-desktop
	cd aura-desktop && python3 -m pytest tests -q

# In no gate until T-1440, and one of its tests was red for seven weeks with nobody told. The
# variable makes a harness that cannot be imported an error: without it the suite skips, and a
# gate that skipped everything would be green having run nothing.
.PHONY: test-desktop-e2e
test-desktop-e2e: ## the desktop agent against a real house: boots both, ~12 s, no display or network needed
	cd aura-desktop && AURA_E2E_REQUIRED=1 python3 -m pytest tests_e2e -q --timeout=120 --timeout-method=signal

.PHONY: test-contract
test-contract: ## the pipeline file, checked the way gitlab-runner runs it (deploy/tests/test_pipeline.py), and the scripts library
	python3 -m pytest deploy/tests scripts/media/tests scripts/document/tests scripts/image/tests scripts/audio/tests -q --timeout=120

.PHONY: test-tools
test-tools: ## the repository's own tooling (scorecard arithmetic, the toolbelt tape measure)
	python3 -m pytest testing/tools evals/intelligence -q --timeout=120

.PHONY: test-python
test-python: test-core test-desktop test-desktop-e2e test-contract test-tools test-hermes eval-routing eval-resolution ## every python suite

.PHONY: lint
lint: ## ruff, defect-only ruleset (see ruff.toml)
	python3 -m ruff check .

.PHONY: lint-fix
lint-fix: ## the same, applying what it can fix
	python3 -m ruff check . --fix

# --- Python quality gates (.gitlab/ci/quality-python.yml) ----------------------
# The four blocking checks exactly as the py-mypy / py-dead / py-dup CI jobs run them, plus
# ruff. Package roots, not repo dirs (`mypy aura-desktop` would pull in tests_e2e/); the
# plugins go through scripts/mypy-plugins.sh because a bare `mypy hermes/plugins` FATALs on the
# hyphenated dir names and reports zero. pip-audit (py-deps) is CI-only: its resolver needs
# ensurepip, which the dev host lacks. PY_LINT_SCOPE (pylint's) is defined with dup-python.
PY_MYPY_TREES := aura-adapter/aura_adapter aura-core/aura aura-desktop/aura_desktop bench aura-schema/aura_schema aura-voice/aura_voice
PY_DEAD_PATHS := aura-core/aura aura-adapter/aura_adapter aura-desktop/aura_desktop bench hermes/plugins scripts deploy aura-voice/aura_voice

.PHONY: lint-python
lint-python: ## defect-only Python gates: ruff + mypy (per tree + plugins) + vulture + pylint R0801 — as CI
	python3 -m ruff check .
	python3 -m mypy --config-file mypy.ini $(PY_MYPY_TREES)
	bash scripts/mypy-plugins.sh
	python3 -m vulture --min-confidence 80 $(PY_DEAD_PATHS) vulture-allowlist.py
	python3 scripts/verify/vulture-allowlist-check.py vulture-allowlist.py $(PY_DEAD_PATHS)
	python3 -m pylint --rcfile=pylintrc $(PY_LINT_SCOPE)

.PHONY: test-web
test-web: ## build + unit + e2e for the HUD (the web smoke went with pipeline.ts, 2026-09-18)
	cd aura-web && npm run build && npm test
	cd aura-web && npm run test:e2e

# The console's screenshots (T-0611): every v2 screen and its states at 390, 768 and
# 1440 wide, into docs/screens/v2/console/ — recorded, not asserted. Builds first
# because the e2e server serves aura-web/build. Review the PNG diff before committing.
.PHONY: sweep
sweep: ## re-take the console's screenshots: every screen and state at 390/768/1440 into docs/screens/v2/console/
	cd aura-web && npm run build
	cd aura-web && V2_SCREENS=1 E2E_PORT=$${E2E_PORT:-8311} npx playwright test e2e/v2-sweep.spec.ts

# The README's clips (T-0865): the console's own build plays a scripted session with a made-up
# house and is filmed a frame at a time (aura-web/e2e/film.ts), so a clip is smooth on a host
# that draws WebGL in software; that is also why it is slow, a second or two a frame. One clip:
# README_CLIP=car. The table of clips is tests/contracts/readme-clips.json; look at each clip
# before committing it. Recorded, not asserted: without README_CLIPS the spec skips.
.PHONY: readme-previews
readme-previews: ## re-cut the README's looping previews (docs/media) from the films in site/media, each under the table's byte budget; needs ffmpeg
	node scripts/media/readme-previews.mjs

.PHONY: readme-clips
readme-clips: ## re-film the README's clips into docs/media (previews) and site/media (MP4s); needs ffmpeg, takes about an hour
	cd aura-web && npm run build
	cd aura-web && README_CLIPS=$${README_CLIPS:-$${TMPDIR:-/tmp}/aura-readme-clips} E2E_PORT=$${E2E_PORT:-8312} npx playwright test e2e/readme-clips.spec.ts

# The Pages site (T-0866): site/build.mjs fills site/index.html from the README and the clip
# table, bundles the console's own galaxy core for the first screen and copies the console's
# tokens, theme and fonts beside it. The types are checked first: the bundler does not. The
# output is a plain folder (SITE_OUT, default _site) that .github/workflows/pages.yml uploads;
# to look at it, serve it from any static server. Needs aura-web/node_modules (`npm ci`).
.PHONY: site
site: ## build the Pages site into _site (the folder the pages workflow deploys)
	# The site's tsconfig extends the console's, which extends .svelte-kit/tsconfig.json: a file
	# `svelte-kit sync` writes and a fresh checkout (CI's) does not have (TS5083, then TS5095).
	cd aura-web && npx svelte-kit sync && npx tsc -p ../site/tsconfig.json
	node site/build.mjs --out $${SITE_OUT:-_site}

# The deployed console, the way a person meets it: every tab at desktop and phone width, failing on
# a skeleton still up after 5 s, a page/console error, a failed request, a mic toast with no mic, or
# overlap at 390 px (T-0321); a side column stopping short of the window's foot at 1440 (T-0325);
# the phone on the 2D reactor, the owner's own. CI runs it after every deploy (deploy:console).
# The house's names live in the host's .env, never in the repo (T-0841): the console is where the
# apps' pairing QR points (AURA_PUBLIC_URL, less its /hermes), and CI passes LIVE_URL itself.
HOST_ENV ?= /opt/jarvis-host/.env
LIVE_URL ?= $(shell sed -n 's#^\(AURA\|JARVIS\)_PUBLIC_URL=\(https\?://[^/]*\).*#\2/#p' $(HOST_ENV) 2>/dev/null)
# The console gate (T-0387) needs the deployed key to sign in. CI passes AURA_API_KEY; on the
# host it comes from the deploy's .env. It is exported, never written into the echoed recipe.
console-live: export AURA_API_KEY ?= $(shell grep -sE '^(AURA|JARVIS)_API_KEY=' /opt/jarvis-host/.env | head -1 | cut -d= -f2- | tr -d '"')
.PHONY: console-live
console-live: ## the deployed console loads: the core and every launcher screen at 1440 and 390 px, none still loading after 5 s, no error
	node scripts/verify/console-live.mjs $(LIVE_URL) --width 1440 --check all --wait 5000
	node scripts/verify/console-live.mjs $(LIVE_URL) --width 390 --check all --wait 5000

golden-turn: export AURA_API_KEY ?= $(shell grep -sE '^(AURA|JARVIS)_API_KEY=' /opt/jarvis-host/.env | head -1 | cut -d= -f2- | tr -d '"')
# The adapter's port is published on the host's LAN address only, not loopback.
golden-turn: export AURA_BASE ?= http://$(shell sed -n 's/^TAILSCALE_IP=//p' $(HOST_ENV) 2>/dev/null | tr -d '\r'):8643
.PHONY: golden-turn
golden-turn: ## one real turn on the deployed console, the car's screen drawn, the phone if adb sees one; each surface's result goes to the board (/api/verified)
	node scripts/verify/golden-turn.mjs $(LIVE_URL)

# The console's a11y + performance gates (.gitlab/ci/quality-hud.yml), the web half:
# axe/keyboard/contrast/preferences over every v2 screen, the shell's frame-time
# trace, and the bundle + APK size budgets. E2E_PORT because 8199 is a live HUD's
# port on the box that runs Jarvis (playwright.config.ts). The Android half runs
# with `make test-android`'s Gradle neighbour (`:app:testDebugUnitTest`).
.PHONY: gate-hud
gate-hud: ## console gates: axe + keyboard + rendered contrast + prefs, frame-time trace, size budgets
	cd aura-web && npm run build
	# The phone's offline page inlines the built CSS: any console style change stales it (CI's hud jobs check it).
	cd aura-web && node scripts/offline-asset.mjs --check
	cd aura-web && E2E_PORT=$${E2E_PORT:-8311} npx playwright test e2e/a11y-axe.spec.ts e2e/a11y-keyboard.spec.ts e2e/a11y-contrast.spec.ts e2e/a11y-preferences.spec.ts
	python3 scripts/verify/hud-contrast.py
	cd aura-web && E2E_PORT=$${E2E_PORT:-8311} npx playwright test e2e/hud-frametime.spec.ts
	node scripts/verify/hud-size.mjs

.PHONY: size-web
size-web: ## console size budget in a lane: build, then the bundle gate (no APK here; gate-hud measures all)
	cd aura-web && npm run build
	node scripts/verify/hud-size.mjs --apk=/nonexistent

.PHONY: goldens
goldens: ## re-record every screenshot golden (the phone's Roborazzi; the console's pictures are `make sweep`); review the PNG diff before committing
	cd android-app && JAVA_HOME=$$HOME/.local/jdk ANDROID_HOME=$$HOME/Android/Sdk PATH=$$HOME/.local/jdk/bin:$$HOME/.local/gradle/bin:$$PATH \
		gradle :app:recordRoborazziDebug --no-daemon -q
	git status --short -- android-app/app/src/test/screenshots

.PHONY: test-android
test-android: ## the Kotlin logic mirrors (pure python, no SDK)
	@fail=0; for t in android-app/tools/*.py; do echo "--- $$t"; python3 "$$t" || fail=1; done; exit $$fail

.PHONY: lint-android
lint-android: ## defect-only Android lint: ktlint + detekt + lintDebug (V2_PLAN D29)
	cd android-app && bash tools/lint.sh

.PHONY: lint-web
lint-web: ## defect-only web gates: svelte-check + eslint + knip (console), tsc + unit tests (the desktop app's pages) — .gitlab/ci/quality-web.yml
	cd aura-web && npm run check && npx eslint . && npx knip
	# The app's ten unit tests ride here because no other local gate enters this tree: a
	# reworded line failed desktop:shell seven minutes into two pipelines (T-1045).
	cd aura-desktop-app && npm run typecheck && npx vitest run

# --- duplication (jscpd, every source tree) -----------------------------------
# The nine source roots, scanned exactly as the `dup-all` CI job does. The
# ignore globs (generated token files and icon drawables, fixtures, screenshots) live in
# `.jscpd.json`; test source is deliberately kept in the denominator so removing
# a test does not flatter the ratio. jscpd is pinned so the % is reproducible.
JSCPD_PATHS := aura-web/src aura-desktop-app/ui aura-desktop-app/src-tauri/src android-app/app/src aura-core/aura hermes/plugins aura-adapter bench aura-desktop/aura_desktop aura-schema

.PHONY: dup
dup: ## fail if copy-paste duplication exceeds 1% across every source tree (.jscpd.json)
	npx --yes jscpd@4.3.0 --config .jscpd.json $(JSCPD_PATHS)

# The Python half on its own, held tighter (V2_TASKS Phase 2: < 0.5 %), plus pylint's
# cross-file clone check on the scope the `py-dup` CI job scans. `scripts` is in the jscpd
# scan because the probes were one of the clone sites; it is not in pylint's (tooling).
PY_DUP_PATHS := aura-core/aura hermes/plugins aura-adapter bench aura-desktop/aura_desktop aura-schema scripts
PY_LINT_SCOPE := aura-core/aura aura-adapter/aura_adapter hermes/plugins aura-schema/aura_schema

.PHONY: dup-python
dup-python: ## Python duplication only: pylint R0801 at zero blocks + jscpd python < 0.5 %
	python3 -m pylint --rcfile=pylintrc $(PY_LINT_SCOPE)
	npx --yes jscpd@4.3.0 --config .jscpd.json --format python --threshold 0.5 $(PY_DUP_PATHS)

# --- the SSRF address parser (V2_PLAN D28) -----------------------------------
# One source, the desktop agent's; jarvis-core's copy is written by this target and never by
# hand (aura-core/tests/test_ssrf.py fails when it is stale or edited). The two trees deploy
# separately and neither may import the other, so the copy is generated the way the token files
# are, and the jscpd markers keep it out of the duplication ratio for the same reason.
SSRF_PARSE_SRC := aura-desktop/aura_desktop/actions/ssrf_parse.py
SSRF_PARSE_GEN := aura-core/aura/helpers/ssrf_parse.py

.PHONY: ssrf-sync
ssrf-sync: ## regenerate jarvis-core's copy of the SSRF address parser from the desktop source (D28)
	{ echo '# @generated from $(SSRF_PARSE_SRC) by `make ssrf-sync`; edit that file, not this one.'; \
	  echo '# jscpd:ignore-start'; cat $(SSRF_PARSE_SRC); echo '# jscpd:ignore-end'; } > $(SSRF_PARSE_GEN)

.PHONY: no-cloud-web
no-cloud-web: ## fail if a cloud web search/extract provider is configured anywhere (V2 §4, D19)
	bash scripts/verify/no-cloud-web-providers.sh

.PHONY: test
test: lint no-cloud-web test-python ## everything runnable without hardware or models
	@echo "OFFLINE TEST SUITE PASSED"

# --- speech -------------------------------------------------------------------
# The adapter speaks by jarvis-core's rules but cannot install jarvis-core (its image builds
# from the repo root, jarvis-core's from its own directory), so it carries a copy; the
# adapter's suite fails on a stale one (test_the_adapter_speaks_by_the_house_rules_word_for_word).
SPEECH_SRC := aura-core/aura/voice/speech_text.py
SPEECH_COPY := aura-adapter/aura_adapter/speech_text.py
.PHONY: speech-copy
speech-copy: ## copy jarvis-core's speech rules into the adapter
	{ printf '# @generated from %s by `make speech-copy` -- DO NOT EDIT; edit that file.\n\n' $(SPEECH_SRC); cat $(SPEECH_SRC); } > $(SPEECH_COPY)

# --- design system ------------------------------------------------------------
.PHONY: tokens
tokens: ## regenerate every surface's tokens from design/tokens.json
	python3 design/build.py

.PHONY: tokens-check
tokens-check: ## fail if a generated token or theme file is stale, or a theme pack is invalid (CI: tokens-sync)
	python3 design/build.py --check
	python3 scripts/verify/theme_check.py

.PHONY: icons
icons: ## regenerate every icon (web, phone, desktop, Tauri bundle) from the v2 core's geometry
	python3 scripts/gen-icons.py

.PHONY: icons-check
icons-check: ## fail, naming each stale file, if a committed icon is not the geometry's
	python3 scripts/gen-icons.py --check

.PHONY: fonts
fonts: ## re-vendor the design system's font subsets from their pinned upstream commits (needs fonttools, brotli)
	python3 design/fonts.py

.PHONY: token-lint
token-lint: ## fail on a hard-coded colour/spacing/type/motion value in app code (ratchet: design/token-lint.baseline.json)
	python3 scripts/verify/token_lint.py
	python3 scripts/verify/theme_literal_lint.py
	python3 scripts/verify/persona_name_lint.py

.PHONY: ds-boundary
ds-boundary: ## fail when a console screen outside src/lib/ds imports a non-ds component or types a font or colour (ratchet: scripts/verify/ds_boundary.baseline.json)
	python3 scripts/verify/ds_boundary.py

# --- the HUD layout case table (D30) ------------------------------------------
# hermes/plugins/hud-layout/layout.py is the authority for the split tree; the window
# manager's TypeScript replays these cases, so the two cannot drift without a red gate.
.PHONY: hud-cases
hud-cases: ## regenerate the split-tree case table the plugin and the window manager share
	python3 scripts/gen-hud-layout-cases.py

.PHONY: hud-cases-check
hud-cases-check: ## fail if the committed case table is not what hud-layout/layout.py produces
	python3 scripts/gen-hud-layout-cases.py --check

# --- settings schema (D17) ----------------------------------------------------
.PHONY: schema
schema: ## regenerate config.template.yaml, the jsettings contract and settings.md from aura-schema
	PYTHONPATH=aura-schema python3 -m aura_schema.generate

.PHONY: schema-check
schema-check: ## fail if a schema-generated file is stale (CI: make schema && git diff --exit-code)
	PYTHONPATH=aura-schema python3 -m aura_schema.generate --check

# --- docs (V2_PLAN §3.4) ------------------------------------------------------
# The reference pages and CAPABILITIES.md are derived from the tree (config template,
# plugin register() calls, adapter + console routes, bench/tasks) and committed, so a
# reader on GitLab sees them without a build. `docs-check` is the CI contract: regenerate
# and fail on drift, then fail on a broken relative link or anchor anywhere under docs/
# or the root markdown. settings.md is `make schema`'s (D17); `docs` only links it.
# Only the generated pages are diffed: docs/reference/ also holds authored pages
# (ANDROID_TASKER_PARITY.md) and schema's settings.md, and an edit
# to one of those in the same change must not read as generator drift.
# This list and the one in CI's docs-sync job are bound to the generator's own table by
# deploy/tests/test_pipeline.py: a page the generator writes and a list forgets fails there.
DOCS_GENERATED := docs/reference/tools.md docs/reference/plugins.md docs/reference/mcp-servers.md \
  docs/reference/slash-commands.md docs/reference/api-endpoints.md docs/reference/hud-protocol.md \
  docs/reference/paired-access.md docs/reference/approval-matrix.md docs/reference/skills.md CAPABILITIES.md \
  aura-web/src/lib/hud/data/capabilities.json tests/contracts/capabilities.json

.PHONY: docs
docs: ## regenerate docs/reference/* (tools, plugins, MCP, commands, API, HUD index) and CAPABILITIES.md
	python3 scripts/gen-docs.py

.PHONY: capabilities
capabilities: ## regenerate CAPABILITIES.md only (one row per JCB task under bench/tasks)
	python3 scripts/gen-docs.py --only capabilities

.PHONY: docs-check
docs-check: docs ## fail if a generated docs page is stale, a relative link is broken, a UI file is missing from the Glass inventory, or always-loaded session context is over budget
	git diff --exit-code -- $(DOCS_GENERATED)
	python3 scripts/check-links.py
	python3 scripts/verify/glass-inventory.py
	python3 scripts/verify/context_budget.py

# --- evals ------------------------------------------------------------------
.PHONY: eval-routing
eval-routing: ## routing table + its two mirrors (offline)
	cd evals && python3 -m pytest test_routing.py -q

.PHONY: eval-resolution
eval-resolution: ## does "the kitchen lamp" find the kitchen lamp (offline)
	cd evals && python3 -m pytest test_resolution.py -q

.PHONY: eval-resolution-report
eval-resolution-report: ## the same, case by case, for tuning the matcher
	cd evals && python3 test_resolution.py

.PHONY: eval-persona
eval-persona: ## persona eval against hermes/home/SOUL.md (needs a model; BACKEND=ollama)
	cd evals && python3 persona_eval.py --backend $(or $(BACKEND),ollama)

# --- running things ---------------------------------------------------------
.PHONY: up
up: ## start the house stack (aura-core/docker-compose.yml); the Hermes stack is deployed, not started here
	@if [ -f .git ] && [ -z "$$AURA_ALLOW_WORKTREE_COMPOSE" ]; then echo "make up: refusing from a git worktree — it would re-create the house containers from this checkout" >&2; exit 3; fi
	@python3 scripts/check-house-folder.py
	cd aura-core && $(COMPOSE) up -d --build
	@echo "house up. The Hermes stack (deploy/docker-compose.yml) runs from the host's rsync'd copy: 'make deploy', or the pipeline; see docs/runbooks/operations.md"

.PHONY: drill
drill: ## cold-start drill: stop the house stack, start it, print each service's seconds to healthy; fails on one past its budget or out of order. Bare, it only prints the plan; DRILL=--yes takes the house down and runs it
	@if [ -f .git ] && [ -z "$$AURA_ALLOW_WORKTREE_COMPOSE" ]; then echo "make drill: refusing from a git worktree" >&2; exit 3; fi
	python3 deploy/cold_start_drill.py $(or $(DRILL),--dry) -f aura-core/docker-compose.yml

.PHONY: down
down: ## stop the house stack
	cd aura-core && $(COMPOSE) down

.PHONY: smoke
smoke: ## boot a throwaway jarvis-core and drive its real APIs
	bash scripts/e2e-smoke.sh

.PHONY: pipeline-smoke
pipeline-smoke: ## full stt->tts audio round trip (needs AURA_TOKEN + Wyoming)
	python3 scripts/pipeline-smoke.py

.PHONY: firewall
firewall: ## apply the ufw policy (root, real server). DRY_RUN=1 to preview
	sudo -E bash scripts/apply-firewall.sh

.PHONY: egress-audit
egress-audit: ## sandbox isolation + the egress allowlist (add --live against a running stack)
	bash scripts/egress-audit.sh

.PHONY: offline-selftest
offline-selftest: ## each model service's image starts and answers with no network (on the production host)
	python3 deploy/offline_selftest.py

.PHONY: verify
verify: ## the full gate: offline suite, then the hardware-backed checks
	$(MAKE) test
	-$(MAKE) smoke
	-$(MAKE) egress-audit
	-$(MAKE) eval-persona
	@echo "See docs/verification.md for the on-device (Pixel, head unit) gates."

# ---------------------------------------------------------------------------
# Hermes rebuild targets (the Hermes rebuild brief, Phase H). These are what
# the GitLab pipeline runs; `make test` below still runs the POC suites that
# remain in the tree.
# The registry path is the host's too (T-0841); CI builds with $CI_REGISTRY_IMAGE.
AURA_REGISTRY ?= $(shell sed -n 's/^\(AURA\|JARVIS\)_REGISTRY=//p' $(HOST_ENV) | head -1 2>/dev/null)
AURA_TAG ?= $(shell git rev-parse --short HEAD)

test-hermes: ## plugins, adapter, schema, config render, pipeline file (the pipeline's test stage, offline)
	python3 -m ruff check hermes aura-adapter aura-schema deploy scripts
	cd hermes && python3 -m pytest plugins -q --timeout=120 --timeout-method=signal
	cd aura-adapter && python3 -m pytest tests -q --timeout=120 --timeout-method=signal
	cd aura-schema && python3 -m pytest tests -q --timeout=120 --timeout-method=signal
	python3 -m pytest deploy/tests -q --timeout=120 --timeout-method=signal
	$(MAKE) --no-print-directory check-shell check-markers private-values

build: ## the three images, tagged $(AURA_REGISTRY)/<svc>:$(AURA_TAG)
	$(if $(AURA_REGISTRY),,$(error AURA_REGISTRY is unset: run on the host, or pass AURA_REGISTRY=<registry>/<namespace>))
	# HEAD, not the working tree: the jarvis image's self-extension checkout is the commit
	# it was built from (deploy/Dockerfile ADDs the tar), the same as build:jarvis bakes.
	git archive --format=tar --prefix=repo/ -o deploy/seed-repo.tar HEAD
	docker build -f deploy/Dockerfile -t $(AURA_REGISTRY)/jarvis:$(AURA_TAG) .
	docker build -f aura-adapter/Dockerfile -t $(AURA_REGISTRY)/jarvis-adapter:$(AURA_TAG) .
	docker build -t $(AURA_REGISTRY)/jarvis-web:$(AURA_TAG) aura-web

push: build ## push the three images to the GitLab registry
	docker push $(AURA_REGISTRY)/jarvis:$(AURA_TAG)
	docker push $(AURA_REGISTRY)/jarvis-adapter:$(AURA_TAG)
	docker push $(AURA_REGISTRY)/jarvis-web:$(AURA_TAG)

integration: ## bring deploy/docker-compose.yml up locally (deploy/.env) and run scripts/smoke.sh
	cd deploy && docker compose up -d --remove-orphans
	@set -a && . deploy/.env && set +a && \
	  for i in $$(seq 1 60); do curl -fsS -m 5 http://$$TAILSCALE_IP:8643/health >/dev/null 2>&1 && break; sleep 5; done && \
	  AURA_BASE=http://$$TAILSCALE_IP:8643 HERMES_API_BASE=http://$$TAILSCALE_IP:8642 bash scripts/smoke.sh

deploy: ## deploy $(AURA_TAG) to $$AURA_HOST over the tailnet (health-gated, auto-rollback)
	AURA_TAG=$(AURA_TAG) bash scripts/deploy.sh

rollback: ## restore the previous tag on the JARVIS host
	bash scripts/deploy.sh --rollback

.PHONY: check-shell
check-shell: ## bash -n over every deploy/ops shell script (CI's test:lint calls this too)
	bash -n $(SHELL_SCRIPTS)

# A job red in the pipeline and green here is green here for a reason this machine hides: the
# venv holds every suite's packages, the host has tools the slim image lacks, and Hermes sits
# beside the checkout (T-1442). These run a job's own lines, read from the pipeline file, in a
# fresh container of the job's image on a clean clone of HEAD. Committed work only.
.PHONY: ci-job
ci-job: ## one pipeline job's own lines in its own image, on a clean clone: make ci-job JOB=test:adapter
	@python3 scripts/ci-job.py $(JOB)

.PHONY: ci-jobs
ci-jobs: ## the pipeline's Python test jobs, each in its own image (minutes, needs Docker and the network)
	@python3 scripts/ci-job.py --python-tests

.PHONY: ci-jobs-all
ci-jobs-all: ## those and the Node and Hermes-image test jobs the tool can run (longer: each installs its own packages)
	@python3 scripts/ci-job.py --all-tests

.PHONY: ci-watch
ci-watch: ## watch the pushed head's pipeline from this host, no token: ends when it deploys, fails or after 80 minutes
	@python3 scripts/ci-watch.py --sha "$$(git rev-parse HEAD)"

.PHONY: check-markers
check-markers: ## no MUTANT / DELIBERATELY BROKEN left in a source or yaml file (a mutation test's leftovers)
	@git grep -nIiwE 'MUTANT|DELIBERATELY BROKEN' -- '*.py' '*.kt' '*.kts' '*.ts' '*.js' '*.svelte' '*.sh' '*.yml' '*.yaml'; test $$? -eq 1  # 1 = no hit; 0 = a marker, 128 = git failed

.PHONY: private-values
private-values: ## no private value in a tracked file: addresses, hosts, emails, phones, device addresses, home paths, keys
	@python3 scripts/verify/private_values.py

SHELL_SCRIPTS := deploy/entrypoint.sh deploy/backup.sh deploy/boot-recover.sh scripts/deploy.sh scripts/deploy-here.sh scripts/smoke.sh scripts/setup-docker-registry.sh scripts/setup-runner.sh
