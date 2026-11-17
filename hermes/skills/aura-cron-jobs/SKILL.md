---
name: aura-cron-jobs
description: "Remind me, every morning, later, repeatedly, keep an eye on, tell me when X is back / changes / opens, when she replies: the cronjob shapes for reminders, briefs, digests, threshold alerts on the GPU or the house and follow-ups on parked work, how to keep an alert from becoming noise, and how to make sure the output actually reaches Sir instead of sitting in a log."
version: 2.1.0
---

# Work that happens later

You have no memory that wakes you up. A turn ends and you stop existing until the next one.
So **every** "remind me", "every morning", "at 7", "keep an eye on", "let me know if", "check
back", "follow up" is scheduled for real — a one-shot reminder on his phone (§1), the rest a
`cronjob` — and answering one in prose ("I'll let you know") is a lie.

Three shapes. Pick by asking "does this need me to think when it fires?" -- but first, two
that are not cronjobs at all and need no approval:

- **Watching for something** -- "tell me when n8n is back", "let me know if that page
  changes", "tell me when the garage opens", "if the greenhouse drops below 18": `watch_add`
  (kind up / down / change / entity, `say` the sentence to tell him). It is checked every
  ten minutes without a model and he is told once when it happens. Never offer to "keep an
  eye on" anything you have not saved one for.
- **Doing something when an event happens** -- "when Lori replies, add it to my calendar",
  "tomorrow at 9 text Sam I'm running late": a routine (`routine_add`, event `message` or
  `time` with a `date`; the routines-people-places skill). A reminder only rings; a routine
  carries out an instruction when it fires.

## 1. A reminder — one-shot: the phone's own, first

"Remind me in 10 minutes to water the ferns", "remind me at 6 to call Mom": the phone keeps it.

```
phone_action(action="set_reminder", params={"text": "Water the ferns", "when": "+10m"})
```

`when` is `+15m` / `+2h`, `HH:MM` (today, or tomorrow if past), or an ISO time. It needs no
approval and rings on his phone wherever he is. Only when no phone answers (`ok: false`, "no
phone") fall back to a one-shot cronjob:

```
cronjob(action="create", schedule="in 10m", name="Water the ferns",
        prompt="One-shot reminder for Sir: it's time to water the ferns. One short sentence.")
```

A cronjob asks for approval, and from a surface that cannot show it the approval times out:
"remind me in 10 minutes" failed that way three times (2026-09-18, 10-06). Recurring work
("every morning", "keep an eye on") is still a cronjob, below.

`schedule` takes `in 30m` / `in 2h` for "N from now" — **never hand-compute a timestamp**;
`every 2h`, `every weekday at 9am`, `0 7 * * *`, or an ISO instant for a fixed moment.
Schedules are read in the host's configured timezone (TZ), so `0 7 * * *` is 07:00 there.

## 2. A brief or digest — recurring, agent, needs the tools

A brief runs in a fresh session with no memory of this conversation, so the prompt must be
self-contained: who it is for, the timezone, the units, the sections, the length. Scope
`toolsets` to what it actually reads — it cuts the token cost of every fire, forever.

```
cronjob(action="create", schedule="0 7 * * *", name="Morning brief (07:00)",
        toolsets=["terminal", "gpu_telemetry", "llama_swap", "smart_home", "calendar"],
        continuity=true, deliver="<see Delivery>", prompt="<self-contained brief prompt>")
```

`continuity=true` lets Sir reply to a brief and have you hold the thread. Leave it off for
fire-and-forget alerts. `templates/morning-brief-prompt.md` is a proven full prompt to adapt.

**Format**: plain text, no markdown headings, under about 200 words. A date and one sentence
on the shape of the day, then the sections, then at most two things actually worth attention.
One source failing costs that section one honest line ("calendar unavailable — <reason>"),
never the whole brief and never an invented figure.

**What a fresh cron session can actually read**: `calendar_events` for the day (half-open
00:00–24:00 local, ISO 8601 with the local offset); `gpu_status` and `llama_swap_status` for
the GPUs and the model; `house_areas` / `house_automations` / `house_history` for the house;
`weather_forecast {days: 1}` for the weather at home (Open-Meteo's numbers, read by the tool; never a web
search, never guessed). Cron runs
have no chat context and no goal tools, so open goals come from the state store:

```
python3 -c "import sqlite3,json; [print(r[0].split(':',1)[1], '|', json.loads(r[1]).get('status'), '|', json.loads(r[1]).get('goal','')[:200]) for r in sqlite3.connect('/opt/data/state.db').execute(\"select key,value from state_meta where key like 'goal:%'\")]"
```

Only `active` and `waiting` are pending; the `cleared`/`done` rows are history, not news.

## 3. A threshold alert — recurring, **no agent**

A watchdog must not cost a model call every five minutes, and it must not be able to
hallucinate a temperature. Use a script with `no_agent=true`: the scheduler runs it, and
**stdout is the message**. Empty stdout sends nothing.

```
cronjob(action="create", schedule="every 5m", name="GPU temperature watchdog (>80C)",
        script="gpu-watch.py", no_agent=true, deliver="<see Delivery>")
```

`gpu-watch.py` ships with this install and already covers GPU temperature and VRAM on both
3090s. Its routine exists only where GPU telemetry is set up (a Grafana or InfluxDB address
in the settings); with neither, the script prints nothing, so do not create the job there. Write a new script only for a threshold it does not cover; scripts live under
`$HERMES_HOME/scripts` and `script` takes the bare filename, never a path.

### The debounce rule

A watchdog that prints its reading every tick is a watchdog Sir will mute, and a muted
watchdog is worse than none. **Print only transitions:**

- the first crossing of the limit, then at most one reminder an hour while it stays over;
- the recovery, once, and only after it falls back under a *lower* clear threshold than the
  one that fired it (80 °C fires, 78 °C clears) — without that gap a sensor sitting on the
  limit alternates alert/recovered forever;
- a telemetry outage once, and its recovery once. Silence otherwise.

If you write your own watchdog and it does not keep state between ticks, you have written a
spammer.

## Delivery — the part that silently fails

`deliver` decides where the output is **posted**; the job always runs in a fresh session.

| target | what it means |
|---|---|
| omitted / `origin` | the chat this job was created from |
| `local` | saved to the job's output only. **Nobody is told.** |
| `telegram`, `discord` | that platform's home channel |
| `platform:chat_id` | one exact chat |
| `all` | every connected home channel |

Four traps, all of which look like success:

- **A job created from the web console or the API has no chat to answer to.** That surface is
  HTTP request/response, not a socket — `deliver: origin` fires fine and the delivery fails at
  fire time with "API server uses HTTP request/response, not send()". Job healthy, brief
  written, Sir never told.
- **`deliver: local` is not delivery.** It stores the output. It is the right answer only when
  the job's whole purpose is the side effect (it wrote a file, it posted to a webhook), or
  when the job pushes for itself — see the next point.
- **A bare `telegram` needs a home channel.** The target resolves through
  `TELEGRAM_HOME_CHANNEL` (`DISCORD_HOME_CHANNEL`, and so on). A bot token by itself resolves
  to nothing: the job runs, reports `ok`, and delivers nowhere. Use `platform:chat_id` when
  you know the chat.
- **The scheduler tells every cron run not to self-deliver** ("the system handles the rest").
  So when a job is meant to push through the house companion instead, that push has to be an
  explicit instruction in the prompt — call `house_tell_user` with the finished text. A prompt
  that merely "writes the brief" will never push it, however it is phrased.

So: check what is actually connected before choosing
(`/opt/data/gateway_state.json` → `platforms`), name the target explicitly, and after the
first fire read `last_delivery_error` back. `last_status: ok` proves the job ran, not that it
arrived.

## Verify before you say it is scheduled

1. `cronjob(action="list")` — read the job back and quote its **next run time**.
2. For anything that matters, `cronjob(action="run")` once (it fires in the background and
   comes back on its own — do not wait or poll), then list again and check `last_status`
   **and** `last_delivery_error`.
3. To see what a run actually produced, read its transcript: `state.db`, table `messages`,
   rows where `session_id` LIKE `cron_<job_id>_%`, highest rowid last.
4. Say the job name, the schedule in local time, and where it will land. "I've set that up"
   is not a report.

And before creating: **list first**. If a job for this already exists, do not make a second
one — say which job covers it and when it next runs, and offer to change it
(`action="update"` on that `job_id`; never guess an id). Two morning briefs at 07:00 is a
worse outcome than none.

## Quiet hours

The house has quiet hours (default 22:00–07:00 local). A digest, a follow-up or a reminder
that comes due inside them waits for the morning — schedule it for after they end rather than
firing into a dark bedroom. A **threshold alert fires anyway**: a GPU at 90 °C at 3am is
exactly what Sir wants to be woken for.

## Removing what you made

A test job on a `*/1 * * * *` schedule fires every minute forever. When you create something
to prove a mechanism works, remove it in the same turn: `cronjob(action="list")` for the id,
then `action="remove"`, and say that you did.
