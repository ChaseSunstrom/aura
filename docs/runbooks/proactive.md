# Runbook: scheduled work (the morning brief, the threshold alert, reminders)

JARVIS does three kinds of work nobody asked for in the moment: a brief on a
schedule, a watchdog that speaks only when something crosses a line, and whatever
reminders and follow-ups it has been asked to hold. All of them are Hermes cron
jobs, all of them run in the `jarvis` container, and the scheduler that fires them
is a thread inside the running gateway — there is no separate service to start.

## What ships, and where it comes from

`hermes/cron/jobs.tsv` is the manifest. `deploy/entrypoint.sh` seeds it on **every**
container start, not only the first, because a brief the image ships is a code-like
asset like a plugin or a skill: an image that could not change it would freeze the
07:00 brief at whatever the first-ever start of the volume wrote.

| Job | Schedule | Kind | What it does |
| --- | --- | --- | --- |
| `Morning brief (07:00)` | `0 7 * * *` | agent | Calendar, weather if the house exposes it, house state, resident model and both 3090s, overnight cron failures, how many memory proposals wait for a yes or no (a count, never their text; T-1500), open goals |
| `Evening summary` | `0 22 * * *` | agent | What changed in the house, goals still running, tomorrow's first item |
| `Nightly self-review` | `30 3 * * *` | agent | Reads its own last 24 hours with `session_search`; delivered nowhere on purpose; proposes standing rules with `memory_propose`, never a dated run log (the store is searched every turn), run notes go in `/opt/data/work/self-review/`. Having read the day's sessions, an addition of its own would be asked about like any after outside text (T-1490) with nobody awake to answer, so a lesson waits as a proposal the owner keeps or drops (`memory_proposals`, T-1500): the morning brief says how many wait, and nothing is stored until the owner keeps it. A proposal that is refused (a full queue, a credential shape) goes under "To confirm" in the run notes; rewrites of an entry are made only with the owner there. A `tool` lesson is set aside (trust 0.25, tag `stale:<plugin>@<date>`) when a deploy changes the plugin behind a tool it names (`deploy/memory_health.py`, T-0208) |
| `Watches and homelab (10 min)` | `*/10 * * * *` | agent, **gated** | `hermes/cron/scripts/watch-check.py` probes Sir's watches (`watch_add`: a URL up/down/changed, a house entity's state) and n8n, Grafana, llama-swap, the house service and the voice (CosyVoice `/health`: down when it runs on the CPU or has no clip, which the adapter otherwise covers silently with Piper) without a model; the model runs only when one tripped (the script's last line is otherwise `{"wakeAgent": false}`). A service going down and coming back is said once each; one still down at most once a day. Replaced the hourly `Homelab health`, which re-alerted one n8n 502 every hour for three weeks |
| `GPU temperature watchdog (>80C)` | every 5 min | **no-agent** | `hermes/cron/scripts/gpu-watch.py`; silent unless a threshold is crossed. Seeded only where `GRAFANA_URL` or `INFLUX_URL` is set (the table's `needs` column) |

The seeder owns exactly those names. A job JARVIS created for itself, or one the
owner wrote, is never removed or rewritten — `$HERMES_HOME/cron/.seeded.json`
records what the image put there. A shipped name that is present but unrecorded
(seeded by an older image, before the manifest existed) is adopted, because that
name is the image's. A `-` in the schedule column retires a job the file used to
ship.

```bash
docker exec jarvis hermes cron list          # every job, its next run, its last result
docker exec jarvis hermes cron status        # is the ticker alive, and when is the next fire
docker exec jarvis cat /opt/data/cron/.seeded.json   # what the image seeded, by digest
```

## Where the output goes

One knob: **`PROACTIVE_DELIVER`**.

| value | what happens |
| --- | --- |
| unset (default `house`) | The scheduler stores the output; an *agent* job pushes it itself through `house_tell_user`, which lands on the console's Notifications panel and on a paired phone or desktop when one is present. The only path that reaches the owner while no messaging platform is connected. A no-agent script cannot push for itself, so under `house` the watchdog's output is recorded and not delivered. |
| `telegram` / `discord` | Hermes delivers to that platform's home channel. Needs the platform on and a home channel — see [messaging.md](messaging.md). |
| `platform:chat_id` | One exact chat. |
| `local` | Stored, delivered nowhere. |

Changing it takes a deploy (it is read when the entrypoint seeds the jobs), and the
seeder re-creates each job because its digest changed.

**Where one result ends up, under `house`.** Three places, checked in this order. The
scheduler writes the full text to `/opt/data/cron/output/<job_id>/` whatever happens
(`docker exec jarvis hermes cron runs` lists the attempts). The job's `house_tell_user`
call records it in jarvis-core's notifications store — `GET /api/notifications` on
:8080 with the house token, or the console's Notifications panel, which redraws on the
`jarvis_notification` event — and pushes it to a paired phone or desktop, which is
`pushed: 0` in the tool's answer while nothing is paired (the console is not a companion
device). A `[SILENT]` reply is not pushed at all. A run that shows `ok` but no
notification means the model never called the tool: read the output file, which says
what it decided. Hermes prepends "your final response will be automatically delivered —
do NOT deliver it yourself" to every cron prompt, `local` ones included, so the
entrypoint's `HOUSE_PUSH` suffix overrides that line by name; a job seeded by an older
image without the override is why the shipped jobs ran for a week without a result
reaching anyone (2026-09-18).

**The trap worth knowing**: a bare `telegram` target resolves through
`TELEGRAM_HOME_CHANNEL`. With a bot token but no home channel, the job runs and
reports `ok` on its run line, and delivers nowhere. The run status is not the
delivery status, and reading only the run status is how this goes unnoticed for a
week. `hermes cron list` does say so, on a **separate line below the execution
line**, for a send that failed *and* for a target that never resolved:

```
    Last run:  2026-09-03T22:02:11  ok
    Execution: completed  0b93cc8d80c8
    ⚠ Delivery failed: no delivery target resolved for deliver=telegram
```

So after changing a delivery target, fire a job once and read the list back. Give
`grep` enough context to reach that warning: it is the **ninth** line under the job's
name for a no-agent job (which carries two extra `Script:`/`Mode:` lines) and the
seventh for an agent job, so `-A9` covers both and the `-A3` this runbook used to
recommend reaches neither.

```bash
docker exec jarvis hermes cron run "Watches and homelab (10 min)"   # fires on the next tick
docker exec jarvis hermes cron list | grep -A9 "Watches and homelab"
docker exec jarvis cat /opt/data/plugin-data/jarvis-watches/watches.json   # the watches and each service's state
```

## The threshold watchdog

`hermes/cron/scripts/gpu-watch.py` is seven lines that load
`plugins/gpu-telemetry/watch.py`, so the Grafana/InfluxDB client exists once rather
than twice. It runs with **no model in the loop**: whatever it prints is delivered
verbatim, and an empty stdout delivers nothing.

It reads the homelab's telemetry, not the machine's own card, so it ships only where a
telemetry source is set: its line in `hermes/cron/jobs.tsv` names `GRAFANA_URL|INFLUX_URL`
in the `needs` column, and with neither set the seeder leaves it out (the start log says
`cron: leaving out 'GPU temperature watchdog (>80C)'`) and takes back one it seeded on an
earlier start. Set either and restart to get it. Run by hand with neither set, the script
prints nothing and writes no state.

Everything about it is about not speaking:

- **edge-triggered** — the first crossing speaks, the ticks after it do not;
- **hysteresis** — fires at `GPU_TEMP_ALERT_C` (80), clears under `GPU_TEMP_CLEAR_C`
  (78). Without the gap a card sitting exactly on the limit alternates
  alert/recovered on every tick, which is how an alert channel becomes noise;
- **a reminder ceiling** — at most one per `GPU_ALERT_RENOTICE_S` (an hour) while it
  stays over;
- **quiet hours** — `PROACTIVE_QUIET_HOURS_FROM`/`_TO` (22→07) suppress the recovery
  and the hourly reminder. A first crossing always speaks: a card at 90 °C at 3am is
  exactly what the owner wants to be woken for.

VRAM has the same pair (`GPU_VRAM_ALERT_PCT` 95, `GPU_VRAM_CLEAR_PCT` 90). All of
them are typed settings — see [the settings reference](../reference/settings.md)
under **Homelab** and **Agent**.

State lives in `$HERMES_HOME/state/gpu-watch.json`. Deleting it re-announces whatever
is currently wrong on the next tick, which is the safe direction to fail in.

```bash
# what the watchdog thinks right now
docker exec jarvis cat /opt/data/state/gpu-watch.json
# run it by hand; silence means everything is under its limit
docker exec -u hermes jarvis /opt/hermes/.venv/bin/python /opt/data/scripts/gpu-watch.py
```

## When a brief does not arrive

1. `hermes cron list` — did it run? `last_status: ok` proves the job ran, not that
   anything arrived.
2. The `⚠ Delivery failed:` line under that row — ran, nobody was told. Usually a
   delivery target that resolved to nothing. The same string is on the job in
   `/opt/data/cron/jobs.json` as `last_delivery_error`, which is the one to read from a
   script rather than by grepping a table.
3. `ls /opt/data/cron/output/<job_id>/` — the full text of every run, so you can see
   whether the brief was right as well as whether it was sent.
4. `hermes cron incidents` — the durable record of repeated failures.
5. If the job is missing entirely, check `.seeded.json` against `jobs.tsv`: a job the
   seeder could not create logs `cron: could not create '<name>'` in the container
   boot log (`docker logs jarvis`).

## Proving a delivery without a bot token

`deploy/tests/test_cron_delivery_live.py` starts a real gateway against a throwaway
`HERMES_HOME` with the Telegram platform pointed at `bench/fixtures/telegram_fake.py`
— python-telegram-bot's `base_url`, the option that exists for a self-hosted
`telegram-bot-api`. Nothing on the Hermes side is stubbed. Skipped unless the flag is
set, because it needs Hermes installed and about three minutes:

```bash
AURA_LIVE_CRON_PROOF=1 python3 -m pytest deploy/tests/test_cron_delivery_live.py -q
```

The day a real token exists, only the three lines under "the one configuration step"
in that file change.
