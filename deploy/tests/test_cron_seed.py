"""The entrypoint's scheduled-job seeder, run for real against a temp HERMES_HOME.

``hermes`` is replaced by a recording stub on PATH, so what is asserted is the argv the
container would run and the manifest it would keep — the two things that decide whether an
image upgrade can change the 07:00 brief without trampling a job JARVIS wrote for itself.
"""
from __future__ import annotations

import json
import re
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "deploy" / "entrypoint.sh"
JOBS_TSV = ROOT / "hermes" / "cron" / "jobs.tsv"

ENV = {
    "LLM_URL": "http://ai.test:9000/v1", "LLM_MODEL": "qwen3.8-27b", "LLM_CONTEXT_LENGTH": "262144",
    "GOALS_MAX_TURNS": "20", "AURA_CORS_ORIGINS": "http://console.test", "WHISPER_MODEL": "base",
    "PIPER_VOICE": "en_GB-alan-medium", "GITLAB_API_URL": "https://gitlab.test/api/v4", "GITLAB_TOKEN": "",
    "MCP_GITLAB_ENABLED": "false", "ROUTING_DELEGATION_MAX_CONCURRENT": "3",
    "API_SERVER_KEY": "x" * 20,
    # The install these tests stage has GPU telemetry, as production does: the watchdog ships
    # only where one of its two sources is set (T-1502), and `_no_telemetry` takes it away.
    "GRAFANA_URL": "http://grafana.test:3000",
}

#: A recording stand-in for the `hermes` CLI: every invocation is appended to $HERMES_CALLS as
#: one JSON array, and `cron create` writes the job into jobs.json the way Hermes would, so a
#: second start of the entrypoint sees the state the first one produced.
HERMES_STUB = r"""#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["HERMES_CALLS"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(argv) + "\n")
path = os.path.join(os.environ["HERMES_HOME"], "cron", "jobs.json")
try:
    jobs = json.load(open(path, encoding="utf-8"))["jobs"]
except (OSError, ValueError, KeyError):
    jobs = []
if argv[:2] == ["cron", "create"]:
    jobs.append({"name": argv[argv.index("--name") + 1]})
elif argv[:2] == ["cron", "remove"]:
    jobs = [j for j in jobs if j["name"] != argv[2]]
else:
    sys.exit(0)
os.makedirs(os.path.dirname(path), exist_ok=True)
json.dump({"jobs": jobs}, open(path, "w", encoding="utf-8"))
"""


def _stage(tmp_path: Path, tsv: str | None = None) -> tuple[Path, Path, dict]:
    """A seed tree, a temp HERMES_HOME and the environment to run the entrypoint with."""
    home, seed, bin_dir = tmp_path / "data", tmp_path / "seed", tmp_path / "bin"
    (seed / "home").mkdir(parents=True)
    (seed / "plugins").mkdir()
    (seed / "agent-hooks").mkdir()
    (seed / "cron" / "scripts").mkdir(parents=True)
    bin_dir.mkdir()
    for f in ("SOUL.md", "USER.md", "MEMORY.md"):
        (seed / "home" / f).write_text(f"seed {f}\n")
    (seed / "config.template.yaml").write_text((ROOT / "deploy" / "config.template.yaml").read_text())
    (seed / "cron" / "jobs.tsv").write_text(tsv if tsv is not None else JOBS_TSV.read_text())
    (seed / "cron" / "scripts" / "gpu-watch.py").write_text("print()\n")
    stub = bin_dir / "hermes"
    stub.write_text(HERMES_STUB)
    stub.chmod(0o755)

    script = (ENTRYPOINT.read_text().replace("SEED=/opt/jarvis/seed", f"SEED={seed}")
              .replace('exec hermes "$@"', 'echo "would exec hermes $*"')
              .replace("exec sleep infinity", 'echo "would hold the container open"'))
    ep = tmp_path / "ep.sh"
    ep.write_text(script)
    env = {**os.environ, **ENV, "HERMES_HOME": str(home), "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "HERMES_CALLS": str(tmp_path / "calls.jsonl")}
    return ep, home, env


def _run(ep: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(ep), "gateway", "run"], check=True, env=env,
                          capture_output=True, text=True)


def _calls(env: dict) -> list[list[str]]:
    path = Path(env["HERMES_CALLS"])
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _creates(env: dict) -> dict[str, list[str]]:
    return {c[c.index("--name") + 1]: c for c in _calls(env) if c[:2] == ["cron", "create"]}


def test_the_shipped_jobs_are_created_with_their_schedule_and_delivery(tmp_path):
    ep, _home, env = _stage(tmp_path)
    _run(ep, env)
    creates = _creates(env)

    assert "Morning brief (07:00)" in creates and "GPU temperature watchdog (>80C)" in creates
    brief = creates["Morning brief (07:00)"]
    assert brief[2] == "0 7 * * *"
    assert "calendar_events" in brief[-1] and "gpu_status" in brief[-1]
    # The 2026-10-08 briefs: n8n's 502 in full every morning for three weeks, "Proxmox
    # unavailable" daily on an install with no Proxmox, and lights nobody acted on.
    assert "watch_list" in brief[-1] and "never its error again" in brief[-1]
    assert "left out without a word" in brief[-1] and "offer once to make it a routine" in brief[-1]

    watchdog = creates["GPU temperature watchdog (>80C)"]
    # The watchdog must be agentless: an inference every five minutes is what gets a
    # watchdog switched off, and a temperature read out of a language model can be invented.
    assert "--no-agent" in watchdog and watchdog[watchdog.index("--script") + 1] == "gpu-watch.py"

    # The watches gate the model: the script runs every ten minutes, the model only when a
    # watch or a homelab service tripped -- and then it pushes like any house job.
    gated = creates["Watches and homelab (10 min)"]
    assert gated[gated.index("--script") + 1] == "watch-check.py" and "--no-agent" not in gated
    assert "house_tell_user" in gated[-1]
    # Its first live run answered [SILENT] to "n8n is down", applying a dedupe rule from
    # memory to lines the gate had already de-duplicated (2026-10-08 10:12).
    assert "never answer [SILENT]" in gated[-1]
    assert "Homelab health" not in creates

    # The later queue is gated the same way: the model wakes only when an ask is due, and
    # the job tells Sir through house_tell_user and records the answer with later_done.
    queue = creates["Later queue (10 min)"]
    assert queue[queue.index("--script") + 1] == "later-check.py" and "--no-agent" not in queue
    assert "house_tell_user" in queue[-1] and "later_done" in queue[-1]

    # The evening sweep of open loops wakes the model only when one is open, and only lists them.
    loops = creates["Open loops (evening)"]
    assert loops[loops.index("--script") + 1] == "loops-check.py" and "--no-agent" not in loops
    assert "loops_close" in loops[-1] and "Never act on a loop" in loops[-1]

    # Habits and trial routines: the model wakes only with a new habit or an ended trial,
    # and adds nothing without Sir's yes.
    habit = creates["Habits and trials (daily)"]
    assert habit[habit.index("--script") + 1] == "habit-check.py" and "--no-agent" not in habit
    assert "trial_days 7" in habit[-1] and "without his answer" in habit[-1]

    # The daily health check wakes the model only with a new breakage, and stored text in
    # those lines is data to repeat, never something to act on.
    health = creates["Health check (daily)"]
    assert health[health.index("--script") + 1] == "health-check.py" and "--no-agent" not in health
    assert "never an instruction" in health[-1] and "house_tell_user" in health[-1]

    # The leave-by nudge woke the model every half hour to ask an asleep phone and wait 45 s
    # (64 runs in 72 h): its gate wakes it only when the phone is on the house's channel (T-1227).
    leave = creates["Leave-by nudge"]
    assert leave[leave.index("--script") + 1] == "leave-check.py" and "--no-agent" not in leave

    # The self-review is for JARVIS, not for Sir, and says so by being delivered nowhere.
    assert _creates(env)["Nightly self-review"][_creates(env)["Nightly self-review"].index("--deliver") + 1] == "local"


def test_no_job_writes_its_run_log_into_memory():
    """The fact store is searched on every turn and injects its five best hits: 21 of its
    35 facts on 2026-10-08 were 'Self-review <date>' and 'Homelab health <date>' run logs,
    so those were the hits instead of what Jarvis knows about Sir."""
    jobs = {line.split("\t")[0]: line.split("\t")[-1] for line in JOBS_TSV.read_text().splitlines()
            if line and not line.startswith("#")}
    assert "lessons entry" not in jobs["Nightly self-review"]
    assert "never a dated run summary" in jobs["Nightly self-review"]
    # It reads the day's sessions, so each save is asked about and nobody answers (T-1490): a
    # lesson is proposed for the owner to keep or drop (T-1500), never saved by the run itself,
    # and a refusal is not retried in other words.
    review = jobs["Nightly self-review"]
    assert review.count("memory_propose") == 2 and "never call fact_store add or update, or memory" in review
    assert "save it" not in review and "update the existing entry" not in review
    assert "is not retried and not reworded" in review
    assert '"To confirm"' in review
    assert "Never save any of it to memory" in jobs["Watches and homelab (10 min)"]


def test_the_morning_brief_says_how_many_proposals_wait_and_reads_none_of_them():
    """T-1500: the brief counts what the routines proposed to remember. A count is a number; a
    list would bring a night's proposals into a turn nobody is watching, and a keep would be an
    ask nobody answers."""
    jobs = {line.split("\t")[0]: line.split("\t") for line in JOBS_TSV.read_text().splitlines()
            if line and not line.startswith("#")}
    brief = jobs["Morning brief (07:00)"][4]
    assert 'memory_proposals {action: "count"}' in brief
    assert brief.count("memory_proposals") == 1 and "memory_propose " not in brief
    assert "never list or keep them here" in brief
    assert brief.index("(6)") < brief.index("memory_proposals") < brief.index("(7)")


def test_the_skill_curator_runs_weekly_and_proposes_rather_than_builds(tmp_path):
    """M10's curator: the proposal it writes is the delivery, so the job pushes nothing."""
    ep, _home, env = _stage(tmp_path)
    _run(ep, env)
    curator = _creates(env)["Skill curator (weekly)"]
    assert curator[2] == "0 4 * * 1"
    assert curator[curator.index("--deliver") + 1] == "local"
    assert "session_search" in curator[-1] and "skill_propose" in curator[-1]
    assert "Do not approve" in curator[-1]
    # `local` verbatim, not `house`: no house_tell_user suffix, nothing to push.
    assert "house_tell_user" not in curator[-1]


def test_house_delivery_makes_an_agent_job_push_for_itself(tmp_path):
    """`house` is JARVIS' own mode: nothing is connected, so the job pushes to the house."""
    ep, _home, env = _stage(tmp_path)
    _run(ep, env)
    brief = _creates(env)["Morning brief (07:00)"]
    assert brief[brief.index("--deliver") + 1] == "local"
    assert "house_tell_user" in brief[-1]
    # Hermes prepends "your final response will be automatically delivered — do NOT deliver
    # it yourself" to every cron prompt, `local` included; the suffix must name that line
    # and override it, or the model sides with the scheduler and nothing is pushed.
    assert "scheduler's delivery note above does not apply" in brief[-1]
    assert "lands on the console" in brief[-1]
    assert "[SILENT] is not delivered" in brief[-1]


def test_one_environment_value_moves_every_job_to_a_platform(tmp_path):
    """The live delivery is one config step: PROACTIVE_DELIVER=telegram and nothing else."""
    ep, _home, env = _stage(tmp_path)
    _run(ep, {**env, "PROACTIVE_DELIVER": "telegram"})
    for name in ("Morning brief (07:00)", "GPU temperature watchdog (>80C)", "Watches and homelab (10 min)"):
        call = _creates(env)[name]
        assert call[call.index("--deliver") + 1] == "telegram", name
    # The scheduler delivers it now, so the job no longer pushes for itself.
    assert "house_tell_user" not in _creates(env)["Morning brief (07:00)"][-1]


def test_a_second_start_changes_nothing(tmp_path):
    ep, _home, env = _stage(tmp_path)
    _run(ep, env)
    first = len(_calls(env))
    _run(ep, env)
    assert len(_calls(env)) == first, "the seeder re-created a job that was already correct"


def test_an_upgrade_replaces_a_shipped_job_whose_line_changed(tmp_path):
    ep, home, env = _stage(tmp_path, "Brief\t0 7 * * *\tlocal\t\told prompt\n")
    _run(ep, env)
    assert list(_creates(env)) == ["Brief"]

    # The image ships a new prompt for the same job.
    (Path(env["HERMES_HOME"]).parent / "seed" / "cron" / "jobs.tsv").write_text(
        "Brief\t0 7 * * *\tlocal\t\tnew prompt\n")
    _run(ep, env)
    assert ["cron", "remove", "Brief"] in _calls(env)
    assert _calls(env)[-1][-1] == "new prompt"
    assert json.loads((home / "cron" / ".seeded.json").read_text())["Brief"]


def test_a_job_jarvis_wrote_for_itself_is_never_touched(tmp_path):
    """The seeder owns the names in jobs.tsv and nothing else."""
    ep, home, env = _stage(tmp_path, "Brief\t0 7 * * *\tlocal\t\tp\n")
    _run(ep, env)
    jobs = json.loads((home / "cron" / "jobs.json").read_text())
    jobs["jobs"].append({"name": "Water the ferns"})
    (home / "cron" / "jobs.json").write_text(json.dumps(jobs))

    (Path(env["HERMES_HOME"]).parent / "seed" / "cron" / "jobs.tsv").write_text(
        "Brief\t0 7 * * *\tlocal\t\tchanged\n")
    _run(ep, env)
    assert not any(c[:2] == ["cron", "remove"] and c[2] == "Water the ferns" for c in _calls(env))
    assert "Water the ferns" in {j["name"] for j in
                                 json.loads((home / "cron" / "jobs.json").read_text())["jobs"]}


def test_a_shipped_name_seeded_by_an_older_image_is_adopted(tmp_path):
    """No manifest, but the name is one this file ships: it is ours, so it is replaced."""
    ep, home, env = _stage(tmp_path, "Brief\t0 7 * * *\tlocal\t\tp\n")
    (home / "cron").mkdir(parents=True)
    (home / "cron" / "jobs.json").write_text(json.dumps({"jobs": [{"name": "Brief"}]}))
    out = _run(ep, env)
    assert "adopting" in out.stderr
    assert ["cron", "remove", "Brief"] in _calls(env)
    assert "Brief" in _creates(env)


def test_a_retired_job_is_removed_once_and_not_re_created(tmp_path):
    ep, home, env = _stage(tmp_path, "Gone\t-\t\t\tsuperseded\nBrief\t0 7 * * *\tlocal\t\tp\n")
    (home / "cron").mkdir(parents=True)
    (home / "cron" / "jobs.json").write_text(json.dumps({"jobs": [{"name": "Gone"}]}))
    _run(ep, env)
    assert ["cron", "remove", "Gone"] in _calls(env)
    assert "Gone" not in _creates(env)

    before = len(_calls(env))
    _run(ep, env)
    assert len(_calls(env)) == before


# --- a routine that needs a source this install may not have (T-1502) --------------------------
#
# A new install on a machine with no telemetry showed "NEXT: GPU temperature watchdog" on its
# home, and the watchdog's first tick told the owner to check a Grafana they never set up. A
# line's sixth column names the settings it needs; with none of them set the line does not ship.

NEEDY = ("Watchdog\tevery 5m\tlocal\twatch.py\t\tGRAFANA_URL|INFLUX_URL\n"
         "Brief\t0 7 * * *\tlocal\t\tp\n")
SOURCES = ("GRAFANA_URL", "INFLUX_URL")


def _no_telemetry(env: dict) -> dict:
    return {k: v for k, v in env.items() if k not in SOURCES}


def _removes(env: dict) -> list[str]:
    return [c[2] for c in _calls(env) if c[:2] == ["cron", "remove"]]


def test_a_routine_whose_source_is_not_set_is_not_seeded(tmp_path):
    ep, home, env = _stage(tmp_path, NEEDY)
    out = _run(ep, _no_telemetry(env))
    assert list(_creates(env)) == ["Brief"]
    assert "Watchdog" not in json.loads((home / "cron" / ".seeded.json").read_text())
    assert "Watchdog" in out.stderr, "the start log does not say which routine was left out"


@pytest.mark.parametrize("source", SOURCES)
def test_either_source_set_seeds_the_routine_as_before(tmp_path, source):
    ep, _home, env = _stage(tmp_path, NEEDY)
    _run(ep, {**_no_telemetry(env), source: "http://telemetry.test:8086"})
    watchdog = _creates(env)["Watchdog"]
    assert "--no-agent" in watchdog and watchdog[watchdog.index("--script") + 1] == "watch.py"
    # The column is the seeder's own: nothing of it reaches the job.
    assert not any("GRAFANA_URL" in part for part in watchdog)


def test_a_source_that_is_only_spaces_is_not_set(tmp_path):
    ep, _home, env = _stage(tmp_path, NEEDY)
    _run(ep, {**_no_telemetry(env), "GRAFANA_URL": "   "})
    assert "Watchdog" not in _creates(env)


def test_a_routine_seeded_earlier_is_removed_when_its_source_is_gone_and_comes_back_with_it(tmp_path):
    ep, home, env = _stage(tmp_path, NEEDY)
    _run(ep, env)
    assert "Watchdog" in _creates(env)

    _run(ep, _no_telemetry(env))
    assert _removes(env) == ["Watchdog"]
    assert "Watchdog" not in json.loads((home / "cron" / ".seeded.json").read_text())
    quiet = len(_calls(env))
    _run(ep, _no_telemetry(env))
    assert len(_calls(env)) == quiet, "a start with nothing to do called hermes"

    _run(ep, env)
    again = [c[c.index("--name") + 1] for c in _calls(env)[quiet:] if c[:2] == ["cron", "create"]]
    assert again == ["Watchdog"]


def test_a_job_of_that_name_the_seeder_did_not_create_is_left_alone(tmp_path):
    """With no source it is not ours to remove: only a recorded job is taken back."""
    ep, home, env = _stage(tmp_path, NEEDY)
    (home / "cron").mkdir(parents=True)
    (home / "cron" / "jobs.json").write_text(json.dumps({"jobs": [{"name": "Watchdog"}]}))
    _run(ep, _no_telemetry(env))
    assert _removes(env) == []
    assert "Watchdog" not in _creates(env)
    assert "Watchdog" in {j["name"] for j in
                          json.loads((home / "cron" / "jobs.json").read_text())["jobs"]}


def test_a_line_with_no_needs_ships_everywhere(tmp_path):
    ep, _home, env = _stage(tmp_path, NEEDY)
    _run(ep, _no_telemetry(env))
    assert "Brief" in _creates(env)


def test_a_new_install_with_no_telemetry_gets_no_gpu_watchdog(tmp_path):
    """The shipped table, on the install the clean-install proof makes."""
    ep, _home, env = _stage(tmp_path)
    _run(ep, _no_telemetry(env))
    creates = _creates(env)
    assert "GPU temperature watchdog (>80C)" not in creates
    assert "Morning brief (07:00)" in creates and "Watches and homelab (10 min)" in creates


def test_the_shipped_watchdog_names_the_two_sources_its_script_reads():
    rows = [line.split("\t") for line in JOBS_TSV.read_text().splitlines()
            if line.startswith("GPU temperature watchdog (>80C)\t")]
    assert len(rows) == 1 and len(rows[0]) == 6
    assert rows[0][4] == "", "the watchdog is agentless: its prompt column stays empty"
    assert sorted(rows[0][5].split("|")) == sorted(SOURCES)
    # Every other line ships everywhere: a needs column is a claim that a routine is useless
    # without a source, and nobody has made it for another one.
    others = [line.split("\t") for line in JOBS_TSV.read_text().splitlines()
              if line and not line.startswith("#") and not line.startswith("GPU temperature")]
    assert all(len(row) <= 5 for row in others)


def test_the_cron_scripts_are_installed_where_the_scheduler_resolves_them(tmp_path):
    """`--script gpu-watch.py` resolves under $HERMES_HOME/scripts and nowhere else."""
    ep, home, env = _stage(tmp_path)
    _run(ep, env)
    assert (home / "scripts" / "gpu-watch.py").exists()
    assert os.access(home / "scripts" / "gpu-watch.py", os.X_OK)


def test_no_jobs_are_seeded_when_hermes_is_missing(tmp_path):
    """The seeder is skipped, loudly, rather than aborting the container's start."""
    ep, _home, env = _stage(tmp_path)
    # A PATH with a shell and python but no hermes at all — the developer box has its own
    # copy in ~/.local/bin, so dropping only the stub would still find one. The interpreter's
    # own directory is included because the entrypoint needs python3 and the CI image keeps it
    # in /usr/local/bin: hard-coding /usr/bin:/bin made this a test of the developer box,
    # where the entrypoint died at 127 for want of python rather than skipping the seeder.
    env = {**env, "PATH": f"{os.path.dirname(sys.executable)}:/usr/bin:/bin"}
    out = subprocess.run(["bash", str(ep), "gateway", "run"], env=env, capture_output=True, text=True)
    assert out.returncode == 0
    assert "hermes not on PATH" in out.stderr
    assert _calls(env) == []


def test_the_proactive_jobs_stay_quiet_cheap_and_out_of_memory():
    """Phase 5 (2026-10-08): the leave-by nudge runs every half hour in waking hours only and
    exits [SILENT] when nothing is near; the weekly could-not-do report likewise. Neither
    writes to memory -- the lesson of the 21 run-log facts."""
    jobs = {line.split("\t")[0]: line.split("\t") for line in JOBS_TSV.read_text().splitlines()
            if line and not line.startswith("#")}
    leave = jobs["Leave-by nudge"]
    assert leave[1] == "*/30 6-21 * * *"
    assert "[SILENT]" in leave[-1] and "route_eta" in leave[-1] and "read_calendar" in leave[-1]
    assert "Never save any of this to memory" in leave[-1]
    report = jobs["What I could not do (weekly)"]
    assert report[1] == "0 19 * * 0" and "[SILENT]" in report[-1] and "Never save the report" in report[-1]


def test_the_morning_brief_reads_the_weather_from_weather_forecast():
    """The brief had no weather line for weeks (T-0715): it allowed only a house weather
    entity, and the house has none."""
    jobs = {line.split("\t")[0]: line.split("\t") for line in JOBS_TSV.read_text().splitlines()
            if line and not line.startswith("#")}
    brief = jobs["Morning brief (07:00)"][-1]
    assert "weather_forecast {days: 1}" in brief and "never search the web for it" in brief
    assert "if and only if the house exposes it" not in brief


def test_the_morning_brief_reads_the_calendar_through_a_connected_account_first():
    """T-1368: connect_read (a Google or Microsoft account the owner connected) comes before
    calendar_events and the phone, which stay as the fallback when no calendar is connected."""
    jobs = {line.split("\t")[0]: line.split("\t") for line in JOBS_TSV.read_text().splitlines()
            if line and not line.startswith("#")}
    brief = jobs["Morning brief (07:00)"][-1]
    assert "connect_status" in brief and "connect_read" in brief
    assert brief.index("connect_read") < brief.index("calendar_events") < brief.index("read_calendar")
    template = (JOBS_TSV.parents[1] / "skills/aura-cron-jobs/templates/morning-brief-prompt.md").read_text()
    assert '"connect"' in template.splitlines()[0] and "connect_read" in template
    assert "google-workspace" in template  # the old path stays as the fallback


def test_the_brief_adds_a_tasks_line_from_todoist_only_when_it_is_connected():
    """T-1415: the template and the seeded row both read Todoist's `due` through connect_read
    after the calendar read, cap it at five tasks, overdue first with each due date, and say
    nothing about tasks when Todoist is not connected or the read fails."""
    jobs = {line.split("\t")[0]: line.split("\t") for line in JOBS_TSV.read_text().splitlines()
            if line and not line.startswith("#")}
    template = (JOBS_TSV.parents[1] / "skills/aura-cron-jobs/templates/morning-brief-prompt.md").read_text()
    for where, text in (("row", jobs["Morning brief (07:00)"][-1]), ("template", template)):
        # The same tools in the same order: status, the calendar read, then the tasks read.
        order = [text.index(t) for t in ("connect_status", "connect_read", 'what "due"'
                                         if where == "template" else 'what: "due"')]
        assert order == sorted(order), where
        assert "todoist" in text.lower() and "at most five tasks" in text, where
        assert "overdue first" in text and "due date" in text, where
        assert "not connected or the read fails" in text, where
        assert "no tasks line" in text, where
    # The section list is unchanged: the template's three numbered sections stay in order.
    numbered = [template.index(f"\n{n}. ") for n in (1, 2, 3)]
    assert numbered == sorted(numbered)
    row = jobs["Morning brief (07:00)"][-1]
    assert [row.index(f"({n})") for n in range(1, 8)] == sorted(row.index(f"({n})") for n in range(1, 8))


SKILL_DIR = JOBS_TSV.parents[1] / "skills" / "aura-cron-jobs"
#: An IANA zone name (Area/Place) and a capitalised first name before "'s birthday" or "'s is on".
_ZONE_NAME = re.compile(r"\b[A-Z][a-z]+/[A-Z][A-Za-z_]+\b")
_NAMED_EXAMPLE = re.compile(r"\b[A-Z][a-z]+'s (?:birthday|is on)\b")


def test_the_brief_template_and_rows_name_no_person_and_no_fixed_timezone():
    """T-1394: the tracked prompt carried a first name, example relatives and one region's
    timezone. The persona's form of address is the only person; the zone is the host's TZ."""
    template = (SKILL_DIR / "templates" / "morning-brief-prompt.md").read_text()
    texts = {"jobs.tsv": JOBS_TSV.read_text(), "template": template,
             "SKILL.md": (SKILL_DIR / "SKILL.md").read_text()}
    for where, text in texts.items():
        assert not _ZONE_NAME.search(text), f"{where} fixes a timezone"
    assert not _NAMED_EXAMPLE.search(texts["jobs.tsv"]), "a birthday example names a person"
    # The template's addressee is the persona's, not a name: "for Sir (".
    assert re.search(r"morning brief for Sir \(", template)
    assert "the configured timezone" in template


def test_the_brief_rows_render_the_hosts_timezone_and_default_when_it_is_unset(tmp_path):
    ep, _home, env = _stage(tmp_path)
    _run(ep, {**env, "TZ": "Test/Zone"})
    brief = _creates(env)["Morning brief (07:00)"][-1]
    assert "Timezone Test/Zone," in brief and "${" not in brief
    assert "Test/Zone" in _creates(env)["Evening summary"][-1]

    # No TZ set: the schema's default fills it, never an empty "Timezone ,". The entrypoint's
    # python3 must be able to import the schema, as it can in the image.
    (tmp_path / "unset").mkdir()
    ep2, _h2, env2 = _stage(tmp_path / "unset")
    env2.pop("TZ", None)
    env2["PATH"] = f"{Path(sys.executable).parent}:{env2['PATH']}"
    _run(ep2, env2)
    from aura_schema import SETTINGS
    default = next(s.default for s in SETTINGS if s.env == "TZ")
    assert default and f"Timezone {default}," in _creates(env2)["Morning brief (07:00)"][-1]


def test_the_certificate_check_is_weekly_silent_when_fine_and_files_renewals():
    jobs = {line.split("\t")[0]: line.split("\t") for line in JOBS_TSV.read_text().splitlines()
            if line and not line.startswith("#")}
    cert = jobs["Certificate check (weekly)"]
    assert cert[1] == "0 9 * * 1"
    # The hosts come from the settings at run time (T-0572): the job names no host, and the
    # token is read in the gateway, which has it, not in a cron terminal, which does not.
    assert "setup_gaps {expiry: true}" in cert[-1] and ".net" not in cert[-1] and "openssl" not in cert[-1]
    assert "[SILENT]" in cert[-1] and "ledger_add" in cert[-1] and "Never save" in cert[-1]


def test_the_goal_check_in_is_weekly_short_and_silent_without_goals():
    jobs = {line.split("\t")[0]: line.split("\t") for line in JOBS_TSV.read_text().splitlines()
            if line and not line.startswith("#")}
    coach = jobs["Goal check-in (weekly)"]
    assert coach[1] == "0 10 * * 6"
    assert "goal_status" in coach[-1] and "[SILENT]" in coach[-1] and "Never save" in coach[-1]


def test_a_seed_naming_the_house_zone_is_rendered_with_gitlabs_zone(tmp_path):
    """MEMORY.md names the house as ${HOUSE_DOMAIN} (T-0841: the repo names no domain). A host
    whose .env leaves it unset gets the zone GitLab is in; one that sets it gets its own."""
    ep, home, env = _stage(tmp_path)
    (tmp_path / "seed" / "home" / "MEMORY.md").write_text("registry on registry.${HOUSE_DOMAIN}\n")
    env = {**env, "GITLAB_API_URL": "https://gitlab.lab.test:8443/api/v4"}
    env.pop("HOUSE_DOMAIN", None)
    _run(ep, env)
    memory = home / "memories" / "MEMORY.md"
    assert memory.read_text() == "registry on registry.lab.test\n"

    # The zone changes: the file is still the seed's, so it is refreshed like an image update.
    _run(ep, {**env, "HOUSE_DOMAIN": "house.test"})
    assert memory.read_text() == "registry on registry.house.test\n"


def test_a_rendered_seed_installs_when_the_digest_directory_is_not_writable(tmp_path):
    """The entrypoint runs as root and again as the gateway's uid. Rendering into the digest
    directory left a root-owned file the second pass could not overwrite, and `set -e` turned
    that into a crash loop on production (2026-11-09). An unwritable directory stands in for it."""
    ep, home, env = _stage(tmp_path)
    (tmp_path / "seed" / "home" / "MEMORY.md").write_text("registry on registry.${HOUSE_DOMAIN}\n")
    env = {**env, "HOUSE_DOMAIN": "lab.test"}
    _run(ep, env)
    digests = home / ".seed-digests"
    files = list(digests.iterdir())
    for path in [*files, digests]:
        path.chmod(0o555 if path.is_dir() else 0o444)
    try:
        _run(ep, env)
    finally:
        for path in [digests, *files]:
            path.chmod(0o755 if path.is_dir() else 0o644)
    assert (home / "memories" / "MEMORY.md").read_text() == "registry on registry.lab.test\n"
