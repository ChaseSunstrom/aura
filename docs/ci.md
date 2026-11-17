# CI on GitLab (and on GitHub)

## GitHub Actions: the offline gate without a runner or a secret

`.github/workflows/` mirrors the gates below on GitHub-hosted runners (`ubuntu-24.04`), reading no
secret and holding `contents: read`. Every action is pinned by commit SHA with its version in a comment.

| workflow | runs | GitLab job(s) |
|---|---|---|
| `python.yml` | the suites (plugins, desktop, cosyvoice, adapter, config-render, aura-core, tools and both evals) and mypy, vulture, pylint R0801, pip-audit | `test:*`, `py-*` |
| `web.yml` | `npm run check`, eslint, knip, tsc; OSV advisories; `make dup`; the build; vitest; the Playwright axe, keyboard, contrast and preference specs on chromium | `web-lint`, `web-deps`, `dup-all`, `web:build`, `test:web`, `hud-a11y` |
| `android.yml` | `make test-android` (the Kotlin mirrors); ktlint, detekt and `lintDebug`; the JVM unit tests (app and car modules) | `test:android`, `kotlin-lint`, the unit steps of `apk:build` |
| `static.yml` | ruff, compileall, `bash -n`, the markers check; the schema, docs, token and icon drift checks; the safety drills | `test:lint`, `schema-sync`, `docs-sync`, `docs-links`, `tokens-sync`, `safety-drills` |

**Enable them on a fork:** push the repository to GitHub and open the Actions tab; nothing else
needs setting (no secret, no runner, no variable). The runs start on a push to `main` or
`claude/jarvis-overhaul`, on every pull request and by hand (`workflow_dispatch`).

One workflow is not a mirror. `pages.yml` publishes the project's site: it runs `make site`
(`site/build.mjs`, [the site's page](../site/README.md)) and deploys the folder with GitHub's
own Pages actions, on a push to `main` that touches what the site is built from, or by hand. It
needs the repository's Pages source set to "GitHub Actions". Its build job holds
`contents: read`; only its deploy job holds `pages: write` and the run's own identity token,
and it reads no secret. GitLab has no job for it: the site's behaviour is held there by
`e2e/site.spec.ts` in `hud-behaviour`, which builds the same folder.

**GitLab-only**, because they need this host, the private registry or an owner secret:
`deploy:production` and `deploy:console`; the frame-budget HUD jobs (`hud-frametime`, `hud-overlap`,
`hud-behaviour`, `hud-gallery`, `hud-size`, `android-macrobench`); `bench:mr` and `bench:nightly`;
the image builds (`build:*`) and `integration:smoke`; `test:plugin-discovery` (the Hermes image);
`test:hermes-imports` (every Hermes-private import a plugin makes, tried in the image `build:jarvis` pushed; the smoke needs it);
`apk:build`'s signing, goldens and `apk:release`; the signed `desktop:*` jobs; `android-deps` (needs
`NVD_API_KEY`). `deploy/tests/test_github_workflows.py` fails if a GitLab job is neither mirrored
command for command nor named in its `GITLAB_ONLY` table, or a workflow runs a command GitLab and the Makefile do not.
The house's own suite, the tooling tests and the offline evals run on both sides: `test:core` and
`test:tools` in GitLab, `core` and `tools-and-evals` in `python.yml`. Until T-1459 GitLab had no job
for them, so the pipeline that deploys never ran the house's tests ("`test:core` and `test:tools`" below).

The desktop job on both sides (`test:desktop`, and `desktop` in `python.yml`) also runs the agent's
end-to-end suite, `aura-desktop/tests_e2e`: a real house and a real agent process, about 12 s, no
display and no network. It runs with `AURA_E2E_REQUIRED=1`, which makes a harness that cannot be
imported an error; without the variable the suite skips, and the job would be green having run
nothing. Locally it is `make test-desktop-e2e`, part of `make test`
(`test_the_desktop_end_to_end_suite_is_a_gate_and_cannot_skip_its_way_green`).

## GitLab

The repository's pipeline lives in `.gitlab-ci.yml` (when a pipeline runs, the
stages, the form's switches) and one included file per former GitHub workflow
under `.gitlab/ci/`:

| file | what it proves | when |
|---|---|---|
| `.gitlab-ci.yml` itself | the fast lane (`test:lint`, the python suites, `test:web`, `test:android`, the executable Android specs), the image builds, `integration:smoke`, `deploy:production` and `deploy:console` | every pipeline |
| `android-apk.yml` | the APK builds, its unit tests, lint and screenshot goldens pass; releases | when `android-app` changes; a `vX.Y.Z` tag |
| `desktop-dist.yml` | the wheel and sdist build and install clean; the desktop app (Tauri) is tested, built for Linux and cross-built for Windows, signed and published with its update feed | when `aura-desktop` or `aura-desktop-app` change |
| `bench.yml` | the JARVIS Capability Benchmark: `bench:mr` (a fast tagged subset, fails on a > 2-point regression) and `bench:nightly` (the full corpus, artifacts + trend) | merge requests; a schedule |
| `quality-python.yml` | the defect-only Python gates: `py-mypy`, `py-dead` (vulture), `py-dup` (pylint R0801), `py-deps` (pip-audit over the aura-core, testing and aura-adapter requirements; on the 3.12 image because the dev host has no ensurepip), and `schema-sync` (`make schema`, drift fails, then the aura-schema suite). Tools pinned (mypy 2.3.1, vulture 2.16, pylint 4.0.8, pip-audit 2.10.1); the local mirror is `.pre-commit-config.yaml`'s Python block, minus pip-audit | every pipeline |
| `quality-android.yml` | `kotlin-lint` — ktlint + detekt + `lintDebug`, defect-only (ADR-0029); `android-deps` (OWASP, skips without an NVD key) | every pipeline |
| `quality-web.yml` | the defect-only web gates: `web-lint` (aura-web `npm run check` as the type gate, eslint 9 under `eslint.config.js`, knip under `knip.jsonc`; aura-desktop-app ts-prune via `npm run prune`), `web-deps` (`npm audit --omit=dev` in both trees), `dup-all` (`make dup`: jscpd at most 1 % over the nine source roots, `.jscpd.json`). Locally: `make lint-web`, `make dup` | every pipeline |
| `quality-docs.yml` | `docs-sync` — `make docs`, then `git diff --exit-code` on the seven generated pages (`docs/reference/*` minus the authored ones, and `CAPABILITIES.md`); `docs-links` — `scripts/check-links.py` over every relative link and anchor in `docs/` and the root markdown, every proof the claims register cites, and every folder a documented command enters (a `cd`, a `for` loop of folders, a `pip install -e` or `-r` path), in those pages and in each tree's README, `CLAUDE.md` and `docs/`. Locally: `make docs-check` | every pipeline |
| `quality-hud.yml` | the Glass HUD gates (Phase 2b), on today's UI as a ratchet: `hud-a11y` (axe over every route, the 8-panel scene and every gallery entry — serious/critical block, `aura-web/e2e/a11y-baseline.json` holds today's findings and only shrinks; keyboard reach + focus ring on every panel verb; contrast of every text block against the *rendered* backdrop with the reactor at its brightest; reduced-motion and reduced-transparency emulation), `hud-frametime` (a Chromium trace over the 8-panel scene through open/update/drag/resize/mode, three passes, best pass gated — p95 and max frame CPU time (thread time of the main-thread tasks inside each `AnimationFrame`, so a loaded runner does not fail the HUD) against `scripts/verify/hud-frametime-budget.json`: 16.7 / 50 ms, the spec's own numbers; the profile is recorded, and on the runners it is SwiftShader — no GPU; wall time reported beside it; CLS 0 during a drag; ≤ 8 blur layers), `hud-size` (bundle gzip and the APK against `scripts/verify/hud-size-budget.json`), and `android-macrobench` (the 8-panel frame-timing macrobenchmark; runs only on a runner tagged `android-device` with `ANDROID_DEVICE_RUNNER=true` — BLOCKED until one exists). The Compose/ATF accessibility checks run inside `apk:build`'s unit tests. Locally: `make gate-hud` | every pipeline (the macrobenchmark: device runner only) |

`deploy/tests/test_pipeline.py` (`make test-contract`) checks the
files against how gitlab-runner really runs them; CI's `static` job runs it
first.

## One pipeline per change

A push to a branch with an open merge request gets the merge-request pipeline
and nothing else. A push to any branch without one gets a branch pipeline (the
workflow's last rule, `if: $CI_COMMIT_BRANCH`); so does the web form. A new
commit on the same ref cancels the running pipeline.

`deploy:production` runs on a green pipeline of `main`, and of
`claude/jarvis-overhaul` both as a branch pipeline and as the merge-request
pipeline its open merge request gets (a rule on
`$CI_MERGE_REQUEST_SOURCE_BRANCH_NAME`; the branch rule alone matched nothing
while the MR was open). After it, `deploy:console` opens the deployed console in
headless Chromium (`make console-live`): the core and every launcher screen at
1440 and 390 px, failing on a screen that does not hydrate or open, one still
loading after 5 s, a page or console error, a failed request, a mic alert with no
mic, or clipped text (rebuilt on the v2 shell in T-0838). The same job then runs the golden turn
(`scripts/verify/golden-turn.mjs`, T-1141) and sends each surface's result to the board;
that step never fails the job. The console check fails the pipeline and keeps
the screenshots as an artifact; it does not roll back (`make rollback` does).
Before the merge, `hud-behaviour` runs the same script against the e2e
server's build (`e2e/console-live-selectors.spec.ts`, T-0842: 1440 px, `--check
loaded`), so a renamed testid or a screen that never mounts fails the merge
request and not the deploy.

## Each thing once (T-0275)

Pipeline 553 took 1,916 s over 43 jobs. What changed, none of it a gate weakened:

- A `desktop-v*` tag runs only the desktop jobs; every other job opens its rules with
  `if: $CI_COMMIT_TAG =~ /^desktop-v/` / `when: never`. A new job needs that rule too
  (`test_ci_does_each_thing_once_and_a_desktop_tag_runs_only_the_desktop_release`).
- The image builds have `needs: []` and start with the tests. The tests still gate the
  deploy: `integration:smoke` needs every test-stage job (optional ones exist only on some
  refs), `deploy:production` needs the smoke, and the pinned test fails if a new test job is
  missing from that list.
- `integration:smoke` asks only the adapter's erase door (smoke step 7, T-1412): the job runs
  `scripts/smoke.sh` in its own shell, which has no `HASS_URL`, and its PASS line says the
  house was not asked. That is deliberate: CI builds no house (its stack points at the one
  already running on the host), so asking it would prove nothing about the tree under test.
  The house's door is asked by the deploy's smoke, which runs the step inside the Hermes
  container; rebuild the house (`make up`) before a deploy that changes it.
- `lintDebug` runs in `kotlin-lint` only (`apk:release` needs it); svelte-check runs in
  `web-lint` only (`test:web` runs vitest, not `npm test`).
- Python jobs share a pip cache (`PIP_CACHE_DIR` under `.cache/pip`, key `pip-py312`).
- `android-deps` does not start without `NVD_API_KEY`.
- `hud-overlap` and `hud-gallery` left the `hud-web` resource group; `hud-a11y`,
  `hud-behaviour` and `hud-frametime` stay in it (frame budgets and timing assertions).
- `deploy:production`, `bench:mr`, `bench:nightly` and `integration:smoke` share the
  `jarvis-models` resource group: the benchmark and the smoke ask production's llama-swap, and a
  deploy that changes `jarvis-models` recreates it (pipeline 646: four cases timed out in the
  cold start, T-0420; the smoke joined in T-0421). Inside one pipeline nothing waits more: the
  deploy and the benchmark already need the smoke. Whichever starts
  first makes the other wait, across pipelines too: a deploy waits about 20 minutes behind
  `bench:mr`, up to three hours behind a nightly run. A deploy ends only after its host smoke has
  a reply from the model, so a benchmark behind it starts on a warm one. The group's process mode
  is GitLab's default (`unordered`): a freed group goes to any waiting job, not the oldest.

## Runners

| tag | needed by | what the runner must offer |
|---|---|---|
| *(none)* | everything else | a Docker executor that takes untagged jobs — the runner on the GitLab container. For part of 2026-09-23 every job was sent to this host's runner instead (`default: tags: [jarvis]`) while that runner failed with the GitLab container's disk full; reverted, because this host's runner mounts the Docker socket on the production host and the test jobs' PyPI/npm installs do not belong beside it. `apk:release` moved to `jarvis` on 2026-11-08: with that disk full again, its artifact download only warned and pipeline 681 published a truncated APK (it now also refuses one that fails `unzip -t`) |
| `android-device` | `android-macrobench` | a runner with a phone or emulator on `adb` (a Pixel for the 120 Hz numbers of GLASS_HUD §8.3), the Android SDK reachable, and `ANDROID_DEVICE_RUNNER=true` set on the pipeline — the job is `never` without both, so it cannot sit pending on the shared runners |

**When every `jarvis`-tagged job goes `stuck_pending_no_matching_runners` while untagged
jobs still run**: the runner container is up but cannot reach GitLab. `docker exec gitlab-runner
gitlab-runner verify` says why. Two causes so far: on 2026-09-05 it could not resolve the old
tailnet name after a reboot (the container had copied the LAN router's resolver before
tailscaled installed MagicDNS), and on 2026-10-27 the house left Tailscale and the runner's
`url` in `~/.gitlab-runner/config.toml` still named the old tailnet host. The fix for the
second is to set `url` (and `clone_url`, if present) to `https://gitlab.home.example` and
`docker restart gitlab-runner`. `scripts/setup-runner.sh` no longer pins a resolver. Nothing on
GitLab's side needs touching, and the failed jobs simply need a new pipeline. Since T-0424
`deploy/health_guard.py` runs the same `verify` every five minutes and puts a house notice up
after two failures in a row, so the next outage is not found two days late.

Register a runner: **Settings → CI/CD → Runners → New project runner**, tick
*Run untagged jobs* for the general one, give the others their tag, then
`gitlab-runner register` with the token it shows.

## Schedules

**Build → Pipeline schedules**: `bench:nightly` (the full benchmark, `.gitlab/ci/bench.yml`)
runs only on a scheduled pipeline; the image builds and the release jobs are `never` on one. The nightly `verify:all` and the weekly `compose:smoke` were retired with the
milestone scripts and the compose smoke.

## The form's switches

| variable | default | effect |
|---|---|---|
| `BUILD_TYPE` | `debug` | the APK a hand-run pipeline builds; pushes, merge requests and tags always build `release` |

## CI/CD variables (Settings → CI/CD → Variables)

| name | for |
|---|---|
| `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD` | signing the release APK with your own key; without them the committed CI key signs (see `android-app/README.md`) |
| `GITLAB_API_TOKEN` | a project access token with the `api` scope (masked); with it a red python suite posts its failing tests as a note on the merge request. Without it the same rows are in the job log and the JUnit report on the merge request. |
| `CI_LLM_API_KEY` | the host llama-swap's inference key (the value of `LLM_API_KEY` in `/opt/jarvis-host/.env`); the smoke and bench stacks send it to the model on `host.docker.internal:9000`, which refuses a call without it. Owner step: create the masked variable; until it exists those jobs get 401 from the model. |

## Releases

Every push to `main`/`dev` that changes the app publishes the release APK to
the project's **generic package registry** (`jarvis-android/<versionCode>/`)
and creates a release tagged `v1.0.<n>+<n>` pointing at it — the `+<code>` is
what a client reads to learn the version without downloading the APK. A person
pushing a `vX.Y.Z` tag gets the same for that tag. GitLab releases have no
*prerelease* flag; the automatic ones say "Test build" in their description.
(The in-app updater still reads GitHub's release feed — pointing it at GitLab's
is separate work.)

## Reading a pipeline without the UI

The project is public, so the API answers pipeline and job *lists* without a
token. Job **logs** answer 401 until **Settings → CI/CD → General pipelines →
Public pipelines** is ticked (the first pipeline, 27 Aug 2026, had two red
jobs whose traces nobody without a token could read); a project access token
with `read_api` in `PRIVATE-TOKEN:` is the alternative.

```bash
API=https://gitlab.home.example/api/v4/projects/3
curl -s "$API/pipelines?sha=<sha>"                    # the pipelines for a commit
curl -s "$API/pipelines/<id>/jobs" | python3 -m json.tool   # jobs, with status and stage
curl -s "$API/jobs/<job id>/trace" | tail -n 60       # a job's log
```

A red `python` job's log ends with the `FAILED`/`ERROR` rows pytest named
(`ERROR:`-prefixed) and the last line of pytest's own summary; the JUnit report
is on the merge request's *Tests* tab.

### Whether a push deployed, from the deploy host

On the host that runs the tagged runner and the stack, Docker shows most of a
pipeline without a token: the runner daemon's result lines, the job containers
it starts, and the image tag of the deployed stack, which a deploy sets to the
commit's first eight characters.

```bash
make ci-watch                                   # the pushed HEAD: until it deploys, fails or 80 minutes
python3 scripts/ci-watch.py --once              # one look: results in the last hour, containers, the tag
python3 scripts/ci-watch.py --from-tag <tag>    # any new tag, for a watch started after the deploy began
```

It prints a line when what it sees changes (counts and the tag, never a log
line: each of the runner's carries the project's path). A failed job is told
by the three numbers its line carries, `exit 22 after 18 s (pipeline 910)`:
the exit code, the seconds the job ran and the pipeline's number. That is often
the diagnosis: a job that exits 22 (what `curl -f` answers an HTTP error with)
after eighteen seconds died at a transfer in its first step, and no test
failed. The job's name and the step are in its trace, which needs the token.
It ends 0 when the
stack runs the commit's tag, is healthy and the deploy job's container is gone;
1 when a job failed on this runner and none has run here for ten minutes; 2 at
its limit, with the reason. It asks Docker only, so it cannot match its own
process, which is how a hand-written wait once sat for 2.4 hours.

It does not see the second runner. A failure there shows as a pipeline that
goes quiet with nothing failed here, and the watch ends at its limit; a gap
with no job container in the middle of a pipeline is that runner working, not
the end. A failed job the pipeline allows is counted and does not end the watch.

### A job red in the pipeline and green here

`make test` is not the runner's environment, and three differences have each
kept a job red while every local run was green (T-1442):

- A job installs only its own `before_script` lines. The local venv holds every
  suite's packages, so the adapter's first async test passed here and failed in
  `test:adapter`, whose install line had no `pytest-asyncio`
  (`deploy/tests/test_pipeline.py` now fails a job that runs an async test
  without such a plugin).
- The Python jobs have no Hermes. `hermes/conftest.py` puts an install found
  beside the Hermes home on the path, so Hermes' own redactor runs here and
  the small fallback in `_shared/_audit.py` runs there. A test of masking uses
  a shape both know.
- The slim image has no `ffmpeg` and none of the host's other tools. A test
  that fakes a tool's work fakes the look for its binary too.

To see what the runner sees, run the job's own lines in its own image on a
clean clone, as root, with nothing else installed. `scripts/ci-job.py` reads
the lines from the pipeline file and the files it includes (`extends`, the
`default` block and the pipeline's plain variables resolved), so nothing is
copied by hand (T-1444):

```bash
make ci-job JOB=test:adapter                    # one job; exit 1 names the failed tests
make ci-jobs                                    # every untagged Python test-stage job, in turn
make ci-jobs-all                                # those, and the jobs in the Node and Hermes images
python3 scripts/ci-job.py --print test:plugins  # the lines it would run; starts nothing
python3 scripts/ci-job.py --list                # the jobs `make ci-jobs` runs; add --all-tests for the longer set
```

`make ci-jobs-all` adds the test-stage jobs the tool can run in other images,
found by the same rule: the console's build, unit tests, lint, dependency
audit and duplication check in the Node image, and plugin discovery in the
Hermes image. Each Node job installs the console's packages from the network
first, so the longer set is for a change to the console or to a plugin's
manifest, not for every push.

It runs the commit at `HEAD` (the runner clones commits; uncommitted changes
are counted in its first line and left out) in a fresh container per job, which
gets the clone read-only, its script, and nothing else of the host's. It needs
Docker and the network, and `make ci-jobs` takes several minutes, so it is a
check before a push, not part of `make test`. A job that carries a runner tag,
starts a service or needs another job's artifacts is refused with the reason
(the deploy, the smoke, the browser jobs): those are still read from the
pipeline. A green run is the job's script passing in its image, not the
pipeline passing. It refuses to start (exit 2) while a pipeline's jobs run on
this host, which it sees as containers named `runner-…` in `docker ps`: the
runner shares these cores, and the load fails its frame-budget jobs.
`--during-pipeline` starts it anyway. It cannot see a job on the second
runner.

A green job is not every test run: on 2026-11-15 four Python jobs skipped 66
tests in their images, 40 of them tests the pipeline could run and did not
(a Hermes install, ffmpeg, three packages, `jq`, a non-root user).
[research/ci-skips-2026-11-15.md](research/ci-skips-2026-11-15.md) lists every
reason with its count and how the run was made. The four holes are closed:
the missing packages are installed (T-1451), `test:hermes` runs the tests
that need a Hermes install, `test:unprivileged` runs the six that skip as
root (each proves a file mode keeps another user out) once more as `nobody`,
failing on a `SKIPPED` row that still names root, and `test:media` installs
Debian's ffmpeg (ffprobe comes with it) and runs the twelve screen-recording
tests and the media conversion's test against the real tool, failing on a
row that names either. The install is most of that job's minute (63 s
here), which is why it is its own job. It proves Debian's ffmpeg 5.1, not
the build in the image production runs.

### A skip nobody recorded

A job that runs pytest fails on a skip reason that is not recorded for it.
The `.skip-guard` template sets three variables that load a pytest plugin
(`scripts/ci/skip_guard.py`) into every pytest run of the job; the plugin
looks up each skip by the job's name and the reason pytest prints in
`scripts/ci/allowed-skips.json`. An unrecorded one turns a passing run into
exit 1 and is printed after pytest's own summary:

```
===== skip guard: 1 skip reason(s) not recorded for test:plugins =====
UNRECORDED SKIP [3] the frobnicator is not on this machine
    plugins/x/tests/test_y.py::test_z
```

When a job goes red this way, the test did not run. In order of preference:

1. Give the job what the test needs (a package on its install line, as
   T-1451 did).
2. Run the file in a job that has it (`test:hermes`, `test:unprivileged`,
   `test:media`), and record the reason for the first job as `"kind":
   "image"` with `"runs_in"` naming the second.
3. Only when no machine should run it (a cell of a shared table that does not
   apply, an opt-in live proof), record it as `"kind": "design"`.

Each entry says why. `deploy/tests/test_pipeline.py` rejects any other kind,
an entry without a why, and a job with a pytest line that does not carry the
variables. The plugin is off wherever the variables are not set, so a desk
and `make test` are untouched; `scripts/ci-job.py <job>` sets them as the
runner does, which is how to see a job's verdict before a push.

What it does not see: a deselected or uncollected test, the Node and Kotlin
suites, the GitHub mirror (its runners skip a different set), a job that
reaches pytest only through a script, and an entry no test meets any more.

### `test:core` and `test:tools`: the house's suite in the pipeline that deploys

`test:core` runs `make test-core` (`aura-core/tests`, 4,154 tests) and
`test:tools` runs `make test-tools`, both offline evals and `bench/tests`, in
the pipeline's Python image with the skip guard on. `integration:smoke` needs
both, so a red house test stops the deploy. Until T-1459 they ran on a desk
(`make test`) and on GitHub only, whose results nobody on the deploy host
reads. Measured with `scripts/ci-job.py test:core test:tools` on 2026-11-15:
about five minutes for `test:core` (4.3 of them the tests, one core) and
under a minute for `test:tools`. Neither has a tag, so either runner takes
them.

The first runs in the image were red three times, and each is what the jobs
are for:

- The image installs `aura-core/requirements.txt` as the house's own image
  does, floating, and got fastapi 0.163 where the desk's `.venv` still had
  0.133. That release keeps an included router as one nested entry in
  `app.routes`. `test_paired_access.py` walked `app.routes`: its allowlist test
  failed, and its neighbour (every route a client calls is on the paired
  allowlist) scanned an empty table and passed for nothing. Both now read the
  app's OpenAPI document, and the scan fails when it sees too few routes.
- `evals/test_resolution.py` collected every case into a test that is about
  half of them and skipped the rest, 19 skips a run. The guard asked; the test
  now takes only its own cases.
- Three tests (`test_llm_local_only.py`, `test_vision_openai.py`) named a
  real public host and let the network resolve it. The model server's
  local-only guard lets a name that does not resolve through, on purpose (a
  compose service still starting), so in a run where the container's DNS
  failed for a moment they reported that a public model server was accepted.
  `aura-core/tests/conftest.py` now answers the guard's lookups for the whole
  suite: a house test must not need a resolver to mean something. Whether the
  guard should look again once the name resolves is T-1461.

What the jobs do not prove: the image the house runs (`aura-core/Dockerfile`,
with its system packages and models), anything that needs a broker, a
microphone or a GPU, and a desk whose `.venv` is older than the image; when
the two disagree, "A job red in the pipeline and green here" above applies.

### Which versions a suite runs against (T-1460)

A green suite says something about production only when it ran against what
production runs. Until T-1460 the adapter's did not: its image installs
`aura-adapter/requirements.txt`, every line an exact pin, while `test:adapter`
took the same packages unpinned from the shared template. On 2026-11-15 the
job was testing fastapi 0.163 and production ran 0.133, and the house's suite
had just shown what one release in between can do (T-1459: a route listing
changed shape and a security test passed for nothing).

- **The adapter**: `test:adapter`, and GitHub's `adapter` job, install the
  image's requirements file before the suite. The suite carries the check
  itself: `aura-adapter/tests/test_image_versions.py` compares every pin in
  that file with what is installed and fails by name, with both versions and
  the command that fixes it. It runs wherever the suite runs, so a new job or
  a stale `.venv` cannot drift quietly. `deploy/tests/test_pipeline.py` pins
  the install lines.
- **The house**: `aura-core/requirements.txt` is a set of ranges on purpose
  (its comments say why the floor is where it is), and its image takes the
  newest release each range allows on the day it is built. `test:core`
  resolves the same file the same way, so the pipeline runs the suite at the
  top of the range. A desk has one `.venv` for every tree, and the adapter's
  suite passes only at the adapter's pins (a venv made from the commands in
  `CLAUDE.md` resolves the ranges at their top and needs
  `pip install -r aura-adapter/requirements.txt` once; the failing test says
  so). For the web framework that is the bottom of the house's range, so
  `make test` runs the house's suite there. Both ends are run; neither is the
  running container's exact set.
- **The plugins** run for real only in `test:hermes`, on the Hermes image
  production runs (below). The other Python jobs take what the template
  installs.

What this does not prove: the packages a pin pulls in but the file does not
name (starlette, anyio), the Python version (the image and the jobs are 3.12,
a desk is 3.11), and the house's exact running set, which no job reproduces.

### `test:hermes`: the tests that need Hermes, on the release production runs

Seventeen tests and the backup drill skip where Hermes is not installed, which
is every Python job. `test:hermes` runs their five files and all six safety
drills in the upstream Hermes image at `$HERMES_TAG`, the image
`deploy/Dockerfile` builds on, with pytest installed beside the venv. Its skip
report is kept and a `SKIPPED` row that names Hermes fails the job, so it
cannot go green by skipping; a skipped drill fails it too.

```bash
python3 scripts/ci-job.py test:hermes       # the job, in its image, about 15 s
```

A desk runs the same files against whatever Hermes it has (`HERMES_INSTALL_DIR`,
else `$HERMES_HOME/hermes-agent`), which need not be that release: on
2026-11-15 this host's was v2026.8.31, and a guard test that passed there
failed in the pinned image on a private name the newer release had moved. For
anything that depends on Hermes's internals the job is the proof, not
`make test`.

A run of the plugin suite says so itself (T-1456). When the install it imported
stands on another release than `HERMES_TAG`, the run ends with one line, also
under `-q`:

```text
Hermes found at ~/.hermes/hermes-agent is v2026.8.31; production is built on v2026.9.24 (HERMES_TAG). …
```

The release is read from the checkout's git files (`hermes/conftest.py`), so
nothing is run in the install and nothing written to it. An install that is not
a git checkout reads as "an unknown release"; the pipeline's image has no git
folder and is the pin by construction, so its jobs stay silent. The line goes
away when the desk's install is moved to the pinned tag, which is a change
outside this repository.

`desktop:agent:windows` freezes the agent under Wine and has ended at the
runner's clock without saying where (T-1145).
`aura-desktop-app/tools/build-agent.sh` now gives `pip install` and
PyInstaller a limit each (`PIP_LIMIT_S` 600 s, `FREEZE_LIMIT_S` 300 s; by hand
they take about 135 s and 24 s), tries a step
that hits it once more, and writes every try's seconds and exit to
`agent-bin/build-times.txt`, an artifact kept from a failed run too. A slow or
red run of that job is read there first.

## What did not survive the move from GitHub

- The macOS and Windows wheel installs need runners the project has to
  register; until then only the Linux install runs.
- Dependabot (`.github/dependabot.yml`) has no GitLab equivalent in the tree;
  Renovate is the usual replacement if wanted.
- Per-line annotations on a check run are replaced by JUnit reports (pytest,
  Playwright, Gradle) and `ERROR:` lines in the log.
- The step summary page is replaced by the same text printed last in the job
  log and kept as an artifact (`summary.md`, `lint-summary.md`).

## Only what a merge request touched (T-0277)

- On a **merge request**, `web:build`, `test:web`, `web-lint` and the HUD jobs (`hud-a11y`, `hud-frametime`, `hud-overlap`, `hud-behaviour`, `hud-gallery`, `hud-size`) run only when `aura-web/`, `design/`, `tests/web/`, `tests/contracts/`, `hermes/plugins/hud*/`, `hermes/plugins/aura-hud/`, `scripts/verify/hud-*` or their CI file changed (`web-lint` also for `aura-desktop-app/`); `bench:mr` only when `bench/` or `hermes/plugins/` changed. On main, tags, schedules and the web form every one of them runs.
- A `needs:` entry on a job the merge request filtered out fails the pipeline at creation, so `integration:smoke` lists these jobs `optional: true`. A new path-filtered job must be optional there (`test_path_filtered_jobs_never_break_the_merge_request_pipeline` checks that every non-optional need of `integration:smoke` is created on any merge request).
- The console is built once for the test stage: `web:build` leaves `aura-web/build/` as an artifact and the HUD jobs and `hud-size` take it (`needs: web:build, artifacts`). `test:web` runs vitest only; the web image (`build:web`) stays its own Docker build.
- A failed Playwright test is tried once more on CI (`retries` in `aura-web/playwright.config.ts`, T-1508) and never locally. A test that passes on its second try is listed as **flaky** at the end of the job's log and in its html report; one that fails twice fails the job. It was added after two merge request pipelines in a row (929, 931) did not deploy because the headless browser crashed on the untagged runner under tests that pass here. A flaky line is a fault to read, not noise: the same test flaky twice gets a task. Target sizes are compared in layout units (`layoutPx` in `e2e/v2.ts`): Chromium lays out in 1/64 px, so a 44 px target can read 43.9998.
- Caches: the Android toolchain and Gradle caches have fixed keys (`android-toolchain-1`, `android-gradle-1`) and are saved even when the job fails (apk:build, kotlin-lint, android-deps, android-macrobench): keyed on files and saved on success only, every dependency bump ran cold into Google's 429 and kept nothing (T-0983); bump the number to start clean; the Cargo registry on `src-tauri/Cargo.lock` (`desktop:shell`; `target/` is deliberately not cached); Tauri's bundler tools (AppRun, linuxdeploy) under `.cache/tauri/` (`tauri-bundler-tools-1`; the job points `XDG_CACHE_HOME` into the project so a GitHub 504 fails only a cold run, T-0810).
- A failed apk:build still hands over its JVM test results (junit in the pipeline view, for the app and the five car modules), their HTML reports and the app's Roborazzi screen differences (T-1057). Its log has a `step: <name>` line before each gradle step, so the last one names where the build stopped.
