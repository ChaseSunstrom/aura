# How to run the JARVIS Capability Benchmark

The benchmark (JCB) drives the deployed stack the way a person would and scores what comes
back. It is the number the V2 goal is measured on, so how it is run matters as much as what
it reports. For what the corpus contains and how a task is written, see the
[benchmark reference](../reference/README.md) and `bench/schema.py`; for why the baseline is
pinned rather than re-measured, see [ADR-0015](../adr/0015-jcb-baselined-first.md).

## Before you run

**Run against a deployed image, not a local tree.** The harness talks to the running stack
over the network. If the stack is a tag older than the code you want to measure, you are
measuring the old tag — `docker compose ps` tells you which, and it has caught people out
more than once. Check the image, not the branch.

**Run on a quiet box.** The tasks have per-task timeouts in the 120–480 s range, and the
host is 4 vCPU. A pipeline, a Gradle build or a second benchmark running alongside will time
tasks out and score capability failures that are really contention. Raising a timeout so a
task passes is gaming the yardstick; waiting for a quiet box is not.

**Only one benchmark at a time.** The API server caps concurrent runs
(`gateway.api_server.max_concurrent_runs`, default 10) and answers 429 when full. The harness
waits a 429 out rather than scoring it, but two benchmarks still distort each other's latency
and token numbers.

## The addresses and the key

The defaults in `bench/runner.py` are loopback, and the API server's port is **not published
to the host** — `127.0.0.1:8642` will not answer. Use the host's LAN address (`TAILSCALE_IP` in its `.env`):

```bash
export BENCH_API_BASE=http://192.0.2.10:8642     # Hermes API server (the runs route)
export BENCH_WS_BASE=http://192.0.2.10:8643      # jarvis-adapter (the gateway route)
```

The API server's bearer key is generated on first start and kept in the container's
`~/.hermes/.env` as `API_SERVER_KEY`. It is not in any host `.env`, and `AURA_TOKEN` will
not authenticate this route (it answers 401). Read it from the container rather than copying
it anywhere:

```bash
export BENCH_API_KEY="$(docker exec jarvis printenv API_SERVER_KEY)"
```

Confirm before starting a run that would otherwise fail 86 times:

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $BENCH_API_KEY" "$BENCH_API_BASE/v1/models"   # expect 200
```

Fixtures are staged and `plugin_state` checkers run **inside the jarvis container**, and the
runner only knows that if you tell it. With `BENCH_EXEC` unset the exec prefix is empty and every
`write_file` and every `hermes cron list` runs on the box you launched from, where `/opt/data`
does not exist: staging fails closed and a checker that needs the container's cron table finds
an empty one. Nothing errors at start; it shows up as tasks failing for reasons that look like
the agent's. A run was lost to this on the 3rd and another on the 6th.

```bash
export BENCH_EXEC="docker exec jarvis"      # where fixtures and checkers run
export BENCH_WORK_ROOT=/opt/data/work BENCH_FILE_ROOT=/opt/data    # the defaults, made explicit
export BENCH_WS_KEY="$(docker exec jarvis-adapter printenv AURA_API_KEY)"   # the gateway route
```

Twenty-four tasks send the agent to the **fixture server** (`bench/fixtures/README.md`), a
separate process that nothing starts for you. It binds `0.0.0.0:8899`; the container reaches it
as `host.docker.internal:8899` through the `extra_hosts` line in `deploy/docker-compose.yml`.
With it down, the browser category goes to zero with verdicts that read as the agent's
("Chrome exited early", five minutes hunting the port). `bench run` now probes it before the
first task and stops if it does not answer.

```bash
python3 bench/fixtures/server.py --port 8899 --state /tmp/jcb-fixtures &   # keep it running
export BENCH_FIXTURE_BASE=http://127.0.0.1:8899   # how the harness reaches the same server
docker exec jarvis curl -s http://host.docker.internal:8899/health          # expect {"status": "ok", …}
```

`0.0.0.0` also puts the fixtures on the LAN. On the production host, bind the address the
container knows as `host.docker.internal` (the bridge gateway) instead, and point the harness
at the same address:

```bash
GW="$(docker exec jarvis getent hosts host.docker.internal | awk '{print $1}')"
python3 bench/fixtures/server.py --host "$GW" --port 8899 --state /tmp/jcb-fixtures &
export BENCH_FIXTURE_BASE="http://$GW:8899"
```

**When Jarvis asks.** A gateway task with `command: ""` and an `answers:` list (the `everyday`
category, T-0731) is answered the way the owner answers: a clarify question gets the next answer
(Hermes asks as a batch even for one question, and each question is answered by its id), and a
reply that asks in plain text gets the next answer as the next prompt on the same session. With
no answers left, a clarify gets "You choose, and go ahead." and a plain reply ends the task.

## Running

```bash
. .venv/bin/activate
python3 -m bench validate                                   # the corpus parses; prints per-category counts
python3 -m bench run --out bench/results/<name>.json         # everything
python3 -m bench run --subset fast --out bench/results/<name>.json
python3 -m bench run --category safety --out bench/results/<name>.json
python3 -m bench run --id pdf-read-004
```

Tasks run one at a time on purpose. A full corpus run is long — plan for it rather than
watching it, and use `--resume` if it is interrupted:

```bash
python3 -m bench run --out bench/results/<name>.json --resume   # skips what the journal already has
```

Each run writes `<name>.json`, `<name>.md` and a `journal.jsonl` of per-task verdicts as it
goes, so an interrupted run is not a lost run.

## Reading the result

```bash
python3 -m bench report bench/results/<name>.json --previous bench/results/baseline.json --gate 2
```

The gate compares **only the tasks both runs ran**, so a corpus that has grown does not read
as a regression. A category needs at least three shared tasks before it gates on its own.

Two things to check before believing a number:

- **`error` is not `fail`.** An error is the harness (a fixture that did not stage, a timeout,
  a transport fault); a fail is the system. If errors are more than a handful, fix them and
  re-run rather than reporting the rate.
- **Look at the failure text, not just the rate.** Two of the harness's own defects were found
  that way and neither was visible in the summary: fixtures arriving empty (the agent said so,
  truthfully) and re-runs sharing a session with their own previous attempts (the agent agreed
  with what it had said before). Both are recorded in the September 2026 build log for 2026-09-03.

## Replaying your own prompts against two models

```bash
python3 -m bench --replay-sessions 20 --models model-a,model-b --store <copy of state.db> \
  --base http://<model server on the LAN> [--tools tools.json] [--judge model-c]
```

Takes the last N prompts you typed or spoke (sessions from the API server, TUI, CLI and
one-shot, never cron or subagents) from the Hermes session store, opened read-only, and sends
each to both models on the house's model server as **one** chat-completions step: the tool
schemas offered, nothing executed. It prints and writes, per prompt, whether both models chose
the same tool calls (names and argument keys), each one's latency, and a 0 to 10 judge score
against what was answered at the time (no earlier answer, no score). The store keeps no tool
schemas, so give `--tools` a JSON dump of them (OpenAI tools format); without it the output says
agreement is over replies with no tools offered. The judge defaults to `BENCH_JUDGE_MODEL`, else
the second model (still loaded after the batches, so no third cold start; the output says it
was "one of the two under test"), and only advises. `--base`/`--key` fall back to `BENCH_MODELS_BASE`/`BENCH_MODELS_KEY`,
then `BENCH_API_BASE`/`BENCH_API_KEY`.

Nothing leaves the host: the endpoint must resolve to loopback or private addresses only, no
proxy is used and a redirect is refused. Both models and the judge must appear in `GET /v1/models`
or it exits 2 before any prompt is sent. llama-swap loads each model on first use (about seven
minutes cold, a third load only with a separate `--judge`), so run it when the house can spare the GPU. The
file lands in `bench/results/replay/` (gitignored, mode 0600): it holds your prompts verbatim.

## In CI

`bench:mr` runs the `fast` subset on merge-request pipelines and gates at 2 points against
`bench/results/baseline.json`. `bench:nightly` runs the whole corpus on the schedule and keeps
the results as artifacts. Both are in [`.gitlab/ci/bench.yml`](../ci.md).

## The nightly canary

`scripts/bench-canary.sh` runs the `fast` subset on the production host into
`bench/results/canary/<date>/` (gitignored), sets every variable above itself (keys read from
the containers, never printed) and starts the fixture server if nothing answers on 8899.
`python3 -m bench.canary bench/results/canary` then compares the newest night with the one
before **over the tasks both ran**, writes the verdict to `bench/results/canary/latest.md`
and exits 1 when two or more passes were lost. On that exit the script sends the line to Sir
through `house_tell_user`'s own function (`hermes/plugins/smart-home/tools.py`, run inside the
`jarvis` container), so it lands on a paired phone and the console's Notifications panel.

One task flipping is ordinary model variance and says nothing; two is the threshold. The
canary compares adjacent nights only — a slide of one task a night never trips it, which is
what `bench:nightly` against `baseline.json` is for. It is not installed by the deploy;
`scripts/bench-canary.sh --cron-line` prints the crontab line to add by hand.
