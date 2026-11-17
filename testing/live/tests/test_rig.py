"""The rig's own arithmetic, tested without talking to anything.

A test suite that measures a system is a piece of software too, and a broken
WER function or a noise generator that quietly produces silence would make
every scenario pass for the wrong reason. None of this touches the model, the
voice services or the network — those are exercised by the scenarios
themselves, which is where a failure means something about Jarvis rather than
about the rig.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from testing.live import audio  # noqa: E402
from testing.live.judge import _parse  # noqa: E402
from testing.live.report import normalise, summarise, wer  # noqa: E402
from testing.live.report import ScenarioResult, TurnResult  # noqa: E402
from testing.live.scenario import Expectation, load_all, load_scenario  # noqa: E402


# --- word error rate ---------------------------------------------------------


def test_a_perfect_transcript_is_zero():
    assert wer("turn on the hall light", "Turn on the hall light.") == 0.0


def test_one_wrong_word_in_four_is_not_a_rounding_error():
    # Character distance would call this 0.06 and flatter a recogniser that
    # changed the meaning; word distance calls it what it is.
    assert wer("turn on the light", "turn on the lights") == pytest.approx(0.25)


def test_nothing_heard_is_a_total_loss():
    assert wer("turn on the light", "") == 1.0


def test_a_doubled_transcript_is_scored_as_the_error_it_is():
    """faster-whisper repeats itself on some short utterances. That is a real
    defect a user hears, so it must not be normalised away."""
    assert wer("turn on the lights", "turn on the lights turn on the lights") == 1.0


def test_notation_and_dialect_are_not_recognition_errors():
    # The model is en_US and writes numerals; the house is British and the
    # scenario is written in words. Both are the same recognition.
    assert wer("set it to twenty one degrees", "set it to 21 degrees") == 0.0
    assert wer("my favourite colour", "my favorite color") == 0.0
    assert normalise("Twenty One")[0] == "21"


# --- audio -------------------------------------------------------------------


def test_noise_lands_on_the_signal_to_noise_ratio_it_was_asked_for():
    speech = audio.clip(audio.room_tone(1.0, level_db=-6), 1.0)
    for want in (20.0, 10.0, 0.0):
        noisy = audio.add_noise(speech, want, shape="white")
        assert audio.snr_of(speech, noisy) == pytest.approx(want, abs=0.5)


def test_noise_is_deterministic():
    """A scenario that fails at 5 dB must fail at 5 dB tomorrow."""
    speech = audio.room_tone(0.5, level_db=-6)
    assert audio.add_noise(speech, 5.0) == audio.add_noise(speech, 5.0)


def test_silence_is_actually_silent_and_room_tone_is_not():
    assert audio.rms(audio.silence(0.5)) == 0.0
    assert audio.rms(audio.room_tone(0.5)) > 0.0


def test_room_tone_is_quiet_enough_to_be_a_room():
    # -50 dBFS by default. Loud enough that a wake detector must reject it on
    # purpose rather than by hearing nothing at all.
    assert 0.0 < audio.rms(audio.room_tone(1.0)) < 32767 * 0.02


def test_clipping_clips_rather_than_wrapping():
    """A sample that wrapped instead of clipping would be loud noise, and the
    scenario would be testing something nobody's microphone does."""
    loud = audio.clip(audio.room_tone(0.2, level_db=-6), 20.0)
    assert audio.rms(loud) > 0
    # The rails of signed 16-bit, which are not symmetrical: -32768 is a real
    # sample value and abs() of it is 32768.
    assert all(-32768 <= v <= 32767 for v in audio._samples(loud))


# --- the fixture format ------------------------------------------------------


def test_a_typo_in_an_expectation_is_an_error_not_a_silent_pass():
    """The failure this prevents: `reply_contian:` — an assertion that never
    runs and a scenario that is green for nothing."""
    with pytest.raises(ValueError, match="unknown expectation"):
        Expectation({"reply_contian": "hello"})


def test_a_key_at_the_wrong_level_of_a_turn_is_refused(tmp_path):
    """`reply_means` one level too shallow — on the turn, not its `expect` —
    loaded silently and judged nothing (27 Aug 2026). A turn key the rig does
    not read is refused at load, and the error says where it belongs."""
    path = tmp_path / "shallow.yaml"
    path.write_text(
        "name: shallow\ncapability: answer\nturns:\n"
        "  - say: hello\n    reply_means: says hello\n    expect: {reply_contains: hello}\n"
    )
    with pytest.raises(ValueError) as caught:
        load_scenario(path)
    assert "reply_means" in str(caught.value) and "under `expect:`" in str(caught.value)


def test_every_shipped_scenario_loads():
    scenarios = load_all()
    assert scenarios, "no scenarios"
    for scenario in scenarios:
        assert scenario.turns, f"{scenario.name} has no turns"
        assert scenario.intent, f"{scenario.name} does not say why it exists"


def test_a_gated_scenario_names_its_milestone():
    for scenario in load_all():
        if scenario.gated:
            assert scenario.gated_on.startswith("M"), scenario.name


def test_a_scenario_with_no_turns_is_refused(tmp_path):
    path = tmp_path / "empty.yaml"
    path.write_text("name: empty\ncapability: house\nturns: []\n")
    with pytest.raises(ValueError, match="no turns"):
        load_scenario(path)


def test_a_turn_that_says_nothing_and_plays_nothing_is_refused(tmp_path):
    path = tmp_path / "mute.yaml"
    path.write_text("name: mute\ncapability: house\nturns:\n  - expect: {}\n")
    with pytest.raises(ValueError, match="says nothing"):
        load_scenario(path)


# --- the judge's parser ------------------------------------------------------


def test_the_judge_is_understood_however_it_answers():
    assert _parse('{"ok": true, "why": "it does"}')[0] is True
    assert _parse('```json\n{"ok": false, "why": "it dodges"}\n```')[0] is False
    assert _parse("Yes — the reply confirms it.")[0] is True
    assert _parse("No, it never says so.")[0] is False


def test_an_unparseable_verdict_is_not_a_pass():
    """A judge that mumbled must not be read as agreement."""
    assert _parse("hmm, hard to say")[0] is None


# --- the scorecard -----------------------------------------------------------


def test_a_rate_over_no_samples_is_not_a_hundred_percent():
    result = ScenarioResult(name="x", capability="house", variant="text", turns=[])
    totals = summarise([result])
    assert totals["routing_accuracy"] is None
    assert totals["wer_mean"] is None


def test_the_summary_counts_what_actually_happened():
    passed = TurnResult(scenario="a", capability="house", variant="voice", index=0,
                        said="x", ok=True, wer=0.0, latency={"total": 1.0})
    failed = TurnResult(scenario="a", capability="house", variant="voice", index=1,
                        said="y", ok=False, failures=["no"], wer=0.5,
                        latency={"total": 3.0})
    result = ScenarioResult(name="a", capability="house", variant="voice", ok=False,
                            turns=[passed, failed])
    totals = summarise([result])
    assert totals["turns"] == 2
    assert totals["turns_passed"] == 1
    assert totals["scenarios_passed"] == 0
    assert totals["round_trip_median"] == 2.0
    assert totals["wer_mean"] == 0.25


# --- the routing table names real tools ---------------------------------------


def test_every_tool_in_the_routing_table_exists():
    """The table is how routing accuracy is measured, so a name that no tool
    has makes the measurement quietly wrong rather than loudly broken.

    It was: the table said `write_note`/`find_note`/`read_note` while the tools
    are `note_create`/`note_append`/`note_search`, and every
    note-taking turn was scored as "skills" because reading the house style
    guide was the only thing it recognised.
    """
    from testing.live.capability import TOOL_CAPABILITY

    core = REPO_ROOT / "aura-core" / "aura"
    registered = set()
    for path in core.rglob("*.py"):
        registered.update(
            match.group(1)
            for match in __import__("re").finditer(
                r'name="([a-z_]+)"', path.read_text(encoding="utf-8")
            )
        )
    missing = sorted(name for name in TOOL_CAPABILITY if name not in registered)
    assert not missing, f"the routing table names tools that do not exist: {missing}"


# --- the ground a scenario runs on --------------------------------------------
#
# M29: the suite talks to the containers the operator runs, not to a copy of
# them. What follows pins the parts of that which can be checked without a
# Docker daemon; the rest is checked by running it.


def test_scenarios_default_to_the_running_stack():
    from testing.live.scenario import load_all

    on_stack = [s.name for s in load_all() if s.ground == "stack"]
    assert len(on_stack) > 10, on_stack


def test_only_scenarios_that_need_our_own_web_leave_the_stack():
    """A scenario asks for the fixture ground for one reason, and says so."""
    from testing.live.scenario import load_all

    # `security` joined them with M43: a red-team probe's hostile page is one
    # this repository serves, and pointing an injection probe at the open web
    # would be testing somebody else's server. The channel probes are here for
    # the same reason — the message they send is one we wrote.
    # `vision` joined with M56: the camera the scenario looks through is a
    # JPEG this repository renders and its fixture site serves, so the answer
    # ("a red mug") is one we can assert on. No real camera is.
    # `online` joined with M59: the page a watch notices changing is one the
    # rig rewrites on its own fixture web; nothing on the open web is watched.
    allowed = {"research", "coding", "subagents", "skills", "security", "vision", "online"}
    off_stack = {s.name: s.capability for s in load_all() if s.ground == "fixture"}
    strays = {name: cap for name, cap in off_stack.items() if cap not in allowed}
    assert not strays, f"these do not need the fixture web: {strays}"


def test_an_unknown_ground_is_a_loud_failure(tmp_path):
    from testing.live.scenario import load_scenario

    path = tmp_path / "x.yaml"
    path.write_text(
        "name: x\ncapability: house\nground: production\n"
        "turns:\n  - say: hello\n    expect: {}\n"
    )
    with __import__("pytest").raises(ValueError, match="unknown ground"):
        load_scenario(path)


def test_the_resilience_scenarios_exist_and_act_on_containers():
    """Both halves of what M29 asks for, asserted as fixtures rather than prose."""
    from testing.live.scenario import load_all

    by_name = {s.name: s for s in load_all()}
    restart = by_name["resilience-core-restart"]
    assert any(turn.restart for turn in restart.turns)
    # And it is a mid-conversation restart, not a restart before anything was
    # said — the promise is that the thread survives, which needs a thread.
    assert not restart.turns[0].restart

    stt = by_name["resilience-stt-down"]
    killed = [turn.kill for turn in stt.turns if turn.kill]
    assert killed == ["wyoming-whisper"], killed
    # The failure must be asserted as visible. A scenario that killed a service
    # and expected nothing would pass against a Jarvis that silently hung.
    assert "error" in stt.turns[0].expect
    # And a turn afterwards, or nothing proves the service came back.
    assert len(stt.turns) > 1


def test_error_records_are_grouped_before_the_allowlist_sees_them():
    """A traceback is one event spread over twenty lines.

    Line-at-a-time matching forced the allowlist to name
    `Task exception was never retrieved` — the useless line that introduces a
    reset connection — which would have hidden every async crash there is.
    """
    from testing.live.stack import _records

    lines = [
        "wyoming-piper  | ERROR:asyncio:Task exception was never retrieved",
        "wyoming-piper  | Traceback (most recent call last):",
        'wyoming-piper  |   File "/usr/src/handler.py", line 63, in handle_event',
        "wyoming-piper  |     await self.write_event(info)",
        "wyoming-piper  | ConnectionResetError: Connection lost",
        "jarvis-core  | ERROR:jarvis:the roof is on fire",
    ]
    found = _records(lines)
    assert len(found) == 1, found
    assert "roof is on fire" in found[0]
    assert "jarvis-core" in found[0]


def test_a_plain_error_line_is_not_swallowed():
    from testing.live.stack import _records

    assert _records(["jarvis-core  | ERROR:jarvis:cannot reach the model server"])


def test_the_snapshot_covers_the_directories_the_database_lives_in():
    """A snapshot with a hole in it restores a house missing its state."""
    from testing.live.ground import STACK_PATHS

    assert "aura-core/config" in STACK_PATHS  # jarvis.db, notes, memory
    assert ".storage" in STACK_PATHS  # the console's password hash


def test_threads_the_suite_opens_are_namespaced():
    from testing.live.ground import TEST_NAMESPACE

    # `test:` and not a bare prefix: an operator scrolling their own thread
    # list has to be able to tell at a glance which conversations were a test
    # run, and the sweep has to know what it may delete.
    assert TEST_NAMESPACE.endswith(":")


def test_the_namespace_is_the_one_habits_skips():
    # hermes/plugins/jarvis-hud/habits.py drops sessions carrying it (T-0523).
    import json
    from pathlib import Path

    from testing.live.ground import TEST_NAMESPACE

    contract = Path(__file__).resolve().parents[3] / "tests" / "contracts" / "rig-session.json"
    assert json.loads(contract.read_text())["namespace"] == TEST_NAMESPACE


def test_records_survive_containers_interleaving_their_output():
    """`docker compose logs` threads every container's output together.

    A grouper that treated any following line as the end of the record cut
    every traceback off before the line that names its exception, so the
    allowlist could only ever match `Task exception was never retrieved` —
    which is true of every async failure there is. Two runs of the live suite
    failed on exactly this before it was fixed.
    """
    from testing.live.stack import _records

    lines = [
        "wyoming-piper  | ERROR:asyncio:Task exception was never retrieved",
        "jarvis-core  | 2026-08-25 INFO an ordinary line arriving in between",
        "wyoming-piper  | Traceback (most recent call last):",
        "jarvis-core  | 2026-08-25 INFO and another",
        'wyoming-piper  |   File "/usr/src/handler.py", line 63, in handle_event',
        "wyoming-piper  | ConnectionResetError: Connection lost",
        "jarvis-core  | ERROR:jarvis:the roof is on fire",
    ]
    found = _records(lines)
    assert len(found) == 1, found
    assert "roof is on fire" in found[0]


def test_the_stack_ground_refuses_a_house_with_nothing_in_it():
    """A fresh Jarvis controls nothing, and that is correct.

    Running the suite against one made every house scenario fail on a missing
    entity, which reads like a broken assistant rather than an empty house. The
    rig says which file to drop in instead.
    """
    import asyncio

    from testing.live import LiveError
    from testing.live.runner import Runner

    class _Bare:
        async def command(self, name, **kwargs):
            return [{"entity_id": "sun.sun", "state": "above_horizon"}]

    runner = Runner([])
    try:
        asyncio.run(runner._house_exists(_Bare()))
    except LiveError as err:
        assert "packages-demo-house.yaml" in str(err)
    else:  # pragma: no cover - the point of the test
        raise AssertionError("an empty house was accepted")


# --- a socket the house closed (27 Aug 2026) ---------------------------------
def test_a_listing_on_a_socket_the_house_closed_is_named_not_empty():
    """"tasks were []" thirty times was one closed socket, unnamed.

    A build without the command is the scenario's failed assertion and reads
    as an empty list; a socket the house closed is the run's, and carries the
    close code — 1012 is uvicorn shutting down — so the restart can be found.
    """
    import asyncio

    from testing.live import LiveError
    from testing.live.world import Observer

    class _Dead:
        closed_reason = "the websocket closed (received 1012 (service restart))"

        async def command(self, name, **kwargs):
            raise ConnectionError(self.closed_reason)

    class _Lacks:
        closed_reason = None

        async def command(self, name, **kwargs):
            raise RuntimeError("unknown command")

    try:
        asyncio.run(Observer(_Dead()).tasks())
    except LiveError as err:
        assert "1012" in str(err) and "jarvis/tasks/list" in str(err)
    else:  # pragma: no cover - the point of the test
        raise AssertionError("a dead socket read as an empty house")
    assert asyncio.run(Observer(_Lacks()).notifications()) == []


def _containers_row(boots, closes=(), ordered=0, protect=True):
    """The containers row for a run that saw these boots and socket closes."""
    from testing.live.runner import Runner

    class _Stack:
        def errors_since(self, since):
            return []

        def boots_since(self, since):
            return list(boots)

    class _Ground:
        stack = _Stack()

    _Ground.protect = protect
    runner = Runner([])
    runner.stack_ground = _Ground()
    runner.ordered_restarts = ordered
    runner.socket_closes = list(closes)
    (row,) = runner._stack_logs_are_clean(0.0)
    return row


def test_the_containers_row_counts_boots_the_rig_did_not_order():
    """The recorder is restored away at the end of a run; the core's log is not.

    One boot is the restore's own, one per `restart: true` turn is ordered;
    any more is somebody restarting the house under the run, and the row says
    when.
    """
    assert _containers_row(["10:08:03"]).ok
    assert _containers_row(["10:08:03", "10:20:11"], ordered=1).ok
    row = _containers_row(["09:56:12", "10:08:03"])
    assert not row.ok
    assert "booted 2 time(s)" in row.error and "09:56:12" in row.error and "ordered 1" in row.error


def test_a_socket_the_house_closed_is_on_the_containers_row():
    row = _containers_row(
        ["10:08:03"],
        closes=[{"at": 0.0, "why": "received 1012 (service restart)", "after": "notes-append"}],
    )
    assert not row.ok
    assert "after notes-append" in row.error and "1012" in row.error


def test_the_runner_reads_expectations_and_the_link_the_way_they_are_shaped():
    """Two crashes from the report run of 27 Aug 2026, pinned at the source.

    `Expectation` is read with `.get`, never subscripted; the `Link` wraps a
    client and has no `base_url` of its own — `stop-means-stop` and
    `memory-per-person` each died on one of these before saying a word.
    """
    from testing.live.scenario import Expectation
    from testing.live.transport import Link

    source = (REPO_ROOT / "testing" / "live" / "runner.py").read_text()
    assert 'expect["' not in source, "an Expectation is read with .get()"
    assert "self.link.base_url" not in source and "self.link.token" not in source
    assert not hasattr(Expectation, "__getitem__")
    assert not hasattr(Link(object()), "base_url")


# --- the one browser (M31) -------------------------------------------------
def _borrower(monkeypatch, *, health="healthy", token="t0ken", docker=True):
    """A `SharedBrowser` whose container and docker are whatever the test says."""
    from testing.live import browser_service

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(browser_service, "docker_available", lambda: docker)
    monkeypatch.setattr(browser_service, "browser_token", lambda: token)
    borrower = browser_service.SharedBrowser()
    monkeypatch.setattr(borrower.stack, "health_of", lambda _s: health)
    monkeypatch.setattr(
        borrower.stack, "recreate",
        lambda service, env=None, **_k: calls.append((service, dict(env or {}))),
    )
    return borrower, calls


def test_borrowing_the_browser_exempts_only_the_fixture_hosts(monkeypatch):
    """The SSRF guard is not weakened — two loopback addresses are exempted."""
    from testing.live.browser_service import FIXTURE_HOSTS

    monkeypatch.delenv("LIVE_SHARED_BROWSER", raising=False)
    borrower, calls = _borrower(monkeypatch)
    assert borrower.start().endswith(":8210")
    (service, env), = calls
    assert service == "jarvis-browser"
    assert env["BROWSER_LAN_ALLOWLIST"] == ",".join(FIXTURE_HOSTS)
    assert set(FIXTURE_HOSTS) == {"127.0.0.2", "127.0.0.3"}


def test_giving_it_back_takes_the_exemption_off(monkeypatch):
    """An exemption left behind is a guard quietly weakened for good."""
    monkeypatch.delenv("LIVE_SHARED_BROWSER", raising=False)
    borrower, calls = _borrower(monkeypatch)
    borrower.start()
    borrower.stop()
    assert calls[-1] == ("jarvis-browser", {"BROWSER_LAN_ALLOWLIST": ""})
    # And twice is not an error: `stop()` runs in a `finally` that may already
    # have run.
    borrower.stop()
    assert len(calls) == 2


def test_it_will_not_borrow_a_browser_that_is_not_healthy(monkeypatch):
    monkeypatch.delenv("LIVE_SHARED_BROWSER", raising=False)
    borrower, calls = _borrower(monkeypatch, health="unhealthy")
    assert borrower.start() == ""
    assert "unhealthy" in borrower.why
    assert calls == []


def test_it_will_not_mint_itself_a_token(monkeypatch):
    """The operator's token or nothing: a suite must not issue its own key."""
    monkeypatch.delenv("LIVE_SHARED_BROWSER", raising=False)
    borrower, calls = _borrower(monkeypatch, token="")
    assert borrower.start() == ""
    assert "AURA_BROWSER_TOKEN" in borrower.why
    assert calls == []


def test_the_stand_in_can_be_asked_for_on_purpose(monkeypatch):
    """So that "this scenario fails without a real browser" can be proven."""
    monkeypatch.setenv("LIVE_SHARED_BROWSER", "0")
    borrower, calls = _borrower(monkeypatch)
    assert borrower.start() == ""
    assert "LIVE_SHARED_BROWSER=0" in borrower.why
    assert calls == []


def test_script_source_is_not_page_text():
    """It is not text, and in a page a model reads it is an injection surface.

    Also the difference between a browser test that means something and one
    that reads the answer out of a <script> the fetcher never ran.
    """
    from testing.live.fixture_search import _text

    html = (
        "<!doctype html><title>T</title><p>visible</p>"
        "<script>const secret = 'immersion heater 3 kW';</script>"
        "<style>.x { content: 'css'; }</style>"
    )
    text = _text(html)
    assert "visible" in text
    assert "immersion" not in text and "css" not in text


def test_every_ui_probe_names_a_testid_the_console_renders():
    """A `ui:` expectation is only as good as its testid.

    `task-live-ui` asserted on `task-activity` for weeks; nothing in the
    console has ever rendered that id, so the scenario could not have passed
    even with a page open. Every testid a scenario probes must appear as a
    `data-testid` in the console's source — a literal, or the prefix before a
    `{...}` interpolation.
    """
    import re
    web = Path(__file__).resolve().parents[3] / "jarvis-web" / "src"
    rendered: set[str] = set()
    for path in web.rglob("*.svelte"):
        for m in re.finditer(r'data-testid=(?:"([^"{]+)|\{`([^`{]+)|"([^"]*?)\{)', path.read_text()):
            rendered.add((m.group(1) or m.group(2) or m.group(3) or "").rstrip("-"))
    probed: list[tuple[str, str]] = []
    for scenario in load_all():
        for turn in scenario.turns:
            raw = turn.expect.get("ui") or []
            for item in raw if isinstance(raw, list) else [raw]:
                probed.append((scenario.name, str(item.get("testid") or "")))
            if raw:
                assert all(v.endswith("-ui") for v in scenario.variants), (
                    f"{scenario.name} asserts on the page but runs API variants {scenario.variants}"
                )
    missing = [(s, t) for s, t in probed if t not in rendered and t.rsplit("-", 1)[0] not in rendered]
    assert not missing, f"scenarios probe testids the console never renders: {missing}"


def test_a_task_older_than_the_turn_does_not_satisfy_the_turn():
    """`wait_for_task` used to accept any task in the list.

    Four sensor audits interrupted by a restart hours earlier satisfied
    "a background task appeared" for every scenario after them, so a turn
    that made no task at all passed. With `since`, only a task created after
    the turn began counts.
    """
    import asyncio
    from testing.live.world import Observer

    stale = {"id": "old", "kind": "background", "status": "error", "created": 1000.0}
    fresh = {"id": "new", "kind": "background", "status": "running", "created": 5000.0}

    class Stub(Observer):
        def __init__(self, rows):
            self.rows = rows

        async def tasks(self):
            return self.rows

    assert asyncio.run(Stub([stale]).wait_for_task(kind="background", timeout=0.6, since=4000.0)) is None
    assert asyncio.run(Stub([stale, fresh]).wait_for_task(kind="background", timeout=0.6, since=4000.0))["id"] == "new"
    # No floor: the old behaviour, for callers that want the whole list.
    assert asyncio.run(Stub([stale]).wait_for_task(kind="background", timeout=0.6))["id"] == "old"


def test_compose_never_sees_the_callers_exported_env(monkeypatch):
    """`docker compose` is run with a clean environment.

    A shell that did `set -a; . .env` to hand the rig LLM_URL also handed
    compose the root `.env`'s values, and compose prefers a shell variable to
    the project's own `.env`: one run re-created jarvis-core, the gateway and
    the browser service with the wrong values. Only docker's own knobs and the
    basics survive; a service's variables never come from the caller.
    """
    from testing.live.stack import _compose_env

    monkeypatch.setenv("LITELLM_MASTER_KEY", "from-the-shell")
    monkeypatch.setenv("AURA_BROWSER_TOKEN", "from-the-shell")
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "x")
    env = _compose_env({"WAIT": "1"})
    assert "LITELLM_MASTER_KEY" not in env and "AURA_BROWSER_TOKEN" not in env
    assert env["DOCKER_HOST"] and env["COMPOSE_PROJECT_NAME"] == "x" and env["WAIT"] == "1"
    assert "PATH" in env


def test_a_reminder_is_a_schedule_entry_before_it_is_a_task():
    """`schedule:` waits for the entry, with the same floor as `task:`.

    "Remind me in one minute" registers a schedule entry and only makes a task
    when it fires, so a scenario that asked for a task within 30 s could never
    pass; and it used to name a `notify` kind that has never existed.
    """
    import asyncio
    from testing.live.world import Observer

    old = {"id": "a", "title": "Check the oven", "kind": "notify", "created": 1000.0}
    new = {"id": "b", "title": "Check the oven", "kind": "notify", "created": 5000.0}

    class Stub(Observer):
        def __init__(self, rows):
            self.rows = rows

        async def schedules(self):
            return self.rows

    assert asyncio.run(Stub([old]).wait_for_schedule(title_contains="oven", timeout=0.6, since=4000.0)) is None
    assert asyncio.run(Stub([old, new]).wait_for_schedule(title_contains="oven", timeout=0.6, since=4000.0))["id"] == "b"
    assert asyncio.run(Stub([new]).wait_for_schedule(title_contains="kettle", timeout=0.6)) is None


def test_the_browser_page_opens_and_reports_the_huds_stored_session():
    """The page's half of T-0489: `browser_turn.cjs` opens `?session=`, the
    name the HUD reads, and reports the id under the key the HUD stores it at.
    Either drifting starts every `-ui` turn on a fresh thread, silently."""
    import re
    from pathlib import Path

    here = Path(__file__).resolve().parents[1]
    script = (here / "browser_turn.cjs").read_text()
    link = (here.parents[1] / "jarvis-web/src/lib/hermes/link.ts").read_text()
    route = (here.parents[1] / "jarvis-web/src/routes/[...path]/+page.svelte").read_text()
    assert "searchParams.get('session')" in route
    assert "params.set('session', job.session)" in script
    key = re.search(r"SESSION_KEY = '([^']+)'", link).group(1)
    assert f"localStorage.getItem('{key}')" in script


def test_the_typed_turn_drives_testids_the_chat_screen_renders():
    """A text-ui turn types into /chat's composer and reads its replies. When the
    HUD renamed its chat, the rig still waited on `chat-input` and every text-ui
    turn timed out at 180 s with nothing said about why."""
    from pathlib import Path

    here = Path(__file__).resolve().parents[1]
    script = (here / "browser_turn.cjs").read_text()
    # The v2 chat screen kept the legacy testids so this rig drives either (T-0611).
    screen = (here.parents[1] / "jarvis-web/src/lib/ds/screens/Chat.svelte").read_text()
    for testid in ("chatscreen-input", "chatscreen-send", "chatscreen-msg"):
        assert testid in script, testid
        assert f'testid="{testid}"' in screen, f"the chat screen no longer renders {testid}"
    assert "job.mode === 'text' ? '/chat'" in script


def test_the_browser_turn_waits_only_on_testids_the_console_renders():
    """The voice turn waited on `hud`, `transcript`, `response` and `latency` for
    weeks after the v2 shell stopped rendering all four: every voice-ui turn timed
    out at 180 s with nothing said about why. The ids come out of the script
    itself (no second list to forget), and each must be a `data-testid` or a
    `testid` prop the console source writes, so a rename on either side fails here."""
    import re

    here = Path(__file__).resolve().parents[1]
    script = (here / "browser_turn.cjs").read_text()
    named = set(re.findall(r"getByTestId\('([\w-]+)'\)", script))
    named |= set(re.findall(r'data-testid="([\w-]+)"', script))
    # The voice turn's own: a script that names none of these is not reading the page.
    assert {"shell", "mic", "conversation-you", "conversation-jarvis"} <= named, named

    src = here.parents[1] / "jarvis-web" / "src"
    rendered: set[str] = set()
    for path in src.rglob("*.svelte"):
        text = path.read_text()
        rendered |= set(re.findall(r"testid\s*=\s*['\"]([\w-]+)['\"]", text))
        # The conversation's `conversation-${turn.role}`: one id per role it draws.
        for prefix in re.findall(r"`([\w-]+)\$\{turn\.role\}`", text):
            rendered |= {f"{prefix}you", f"{prefix}jarvis"}
    missing = sorted(named - rendered)
    assert not missing, f"browser_turn.cjs waits on testids the console never renders: {missing}"


def test_a_voice_ui_turn_reads_the_heard_and_said_words_off_the_built_console():
    """The page's own microphone path, end to end against the e2e mock server: a
    fake capture device plays a loud burst, the page's VAD hears it, the mock
    transcribes it and answers. The offline testid test only proves the ids are
    written in the source; this proves the page fills them. Skips (it is not a
    pass) when the console is not built or node is missing."""
    import asyncio
    import shutil
    import socket
    import subprocess
    import time
    from math import pi, sin
    from types import SimpleNamespace

    from testing.live import transport
    from testing.live.audio import _pcm

    root = Path(__file__).resolve().parents[3]
    if not (root / "jarvis-web" / "build" / "index.js").is_file() or not shutil.which("node"):
        pytest.skip("the console is not built (cd jarvis-web && npm run build) or node is missing")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # The energy VAD hears level, not words, so a loud voiced buzz (a harmonic stack with a
    # syllable-rate envelope) is an utterance.
    rate = 22050

    def voiced(i: int) -> int:
        t = i / rate
        f0 = 110 + 25 * sin(2 * pi * 0.7 * t)
        env = 0.55 + 0.45 * sin(2 * pi * 4 * t)
        return int(5000 * env * sum(sin(2 * pi * f0 * k * t) / k for k in range(1, 12)))

    burst = _pcm([max(-32768, min(32767, voiced(i))) for i in range(rate * 2)])
    server = subprocess.Popen(
        ["node", "tests/web/serve-e2e.mjs"],
        cwd=str(root),
        env={**__import__("os").environ, "PORT": str(port)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 60
        while True:
            assert server.poll() is None, "the e2e server exited"
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                assert time.monotonic() < deadline, "the e2e server never listened"
                time.sleep(0.3)
        # The e2e server's console has a password; its seeded session is the cookie every spec carries.
        browser = transport.Browser(
            SimpleNamespace(url=f"http://127.0.0.1:{port}"), None, None,
            cookie={"name": "jarvis_console", "value": "e2e-signed-in"},
        )
        turn = asyncio.run(
            browser.say("turn on the lab lights", pcm=burst, rate=rate, mode="voice", timeout=60)
        )
    finally:
        server.terminate()
        server.wait(timeout=15)
    assert turn.transcript.strip(), "the page showed no heard words"
    assert turn.reply_text.strip(), "the page showed no answer"
    assert {"stt", "response"} <= set(turn.latency)


def test_compose_is_refused_from_a_git_worktree(monkeypatch, tmp_path):
    """The rig will not bring the stack up from a worktree.

    A worktree's compose project is the production project — the name comes
    from the directory — and twice in one night an agent's worktree
    re-created the house's containers from its own checkout. `.git` is a
    file in a worktree; the guard reads that, and only an explicit
    AURA_ALLOW_WORKTREE_COMPOSE lets compose through.
    """
    from testing.live import stack
    from testing.live.stack import LiveError, _run, in_worktree

    (tmp_path / ".git").write_text("gitdir: /somewhere/.git/worktrees/x\n")
    monkeypatch.setattr(stack, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("AURA_ALLOW_WORKTREE_COMPOSE", raising=False)
    assert in_worktree(tmp_path)
    with pytest.raises(LiveError, match="worktree"):
        _run(["docker", "compose", "-f", "x.yml", "up", "-d"])
    # A plain docker command is not compose and is not refused by this guard.
    out = _run(["true"], check=False)
    assert out == ""


def test_a_background_task_that_also_read_a_sensor_is_a_task():
    """The outer act, not the reader (the intelligence eval's route-6)."""
    from testing.live.capability import capability_of

    assert capability_of(["background"], [], ["sensor_readings"], "") == "task"
    assert capability_of([], [], ["sensor_readings"], "") == "sensors"
    assert capability_of([], [], ["deep_research", "read_page"], "") == "research"


def test_a_scenario_that_stops_early_still_leaves_no_retained_sensor(monkeypatch):
    """Two jarvis-test sensors stayed retained on the production broker and the morning
    brief reported a "Probe Sensor" as maybe the greenhouse (2026-10-08): the sweep empties
    every retained topic a scenario set and did not clear itself."""
    import asyncio
    import types

    from testing.live.runner import Runner

    sent: list[tuple[str, str, bool]] = []

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def publish(self, topic, payload, retain=False, qos=0):
            sent.append((topic, payload, retain))

    monkeypatch.setitem(sys.modules, "aiomqtt", types.SimpleNamespace(Client=Client))
    monkeypatch.setattr(asyncio, "sleep", lambda *_: asyncio.ensure_future(asyncio.gather()))
    runner = Runner.__new__(Runner)

    async def run():
        await runner._mqtt_publish([
            {"topic": "homeassistant/sensor/t/config", "retain": True, "payload": {"name": "T"}},
            {"topic": "jarvis-test/t", "retain": True, "payload": "11.0"},
            {"topic": "jarvis-test/live", "payload": "1"},
        ])
        await runner._mqtt_publish([{"topic": "jarvis-test/t", "retain": True, "payload": ""}])
        sent.clear()
        problems: list[str] = []
        await runner._clear_retained(problems)
        return problems

    assert asyncio.run(run()) == []
    assert sent == [("homeassistant/sensor/t/config", "", True)]


# --- a thread continued in the console (T-0489) ------------------------------
def test_the_browser_continues_the_thread_its_last_page_was_on(monkeypatch):
    """The HUD opens `?session=<stored id>`; a test-namespace name is not one.

    Each browser turn is a fresh page with empty storage, so without the id the
    page remembered, the second turn of a thread starts a new conversation.
    """
    import asyncio
    import json
    import types

    from testing.live.transport import Browser

    jobs: list[dict] = []

    async def fake_exec(*argv, **_kw):
        jobs.append(json.loads(argv[2]))

        async def communicate():
            return json.dumps({"response": "ok", "session": "20261107_abc"}).encode(), b""

        return types.SimpleNamespace(communicate=communicate, kill=lambda: None)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    browser = Browser(types.SimpleNamespace(url="http://console"), None, None)

    async def thread():
        first = await browser.say("one", mode="text", conversation_id="test:thread:text-ui")
        return await browser.say("two", mode="text", conversation_id=first.conversation_id)

    assert asyncio.run(thread()).conversation_id == "20261107_abc"
    assert [job["session"] for job in jobs] == ["", "20261107_abc"]


# --- the car journey (T-0581) ------------------------------------------------
def _journey_tools():
    """The tools each turn of car-journey.yaml is promised to call."""
    from testing.live.runner import _as_list

    scenario = load_scenario(REPO_ROOT / "testing/live/scenarios/car-journey.yaml")
    return scenario, [_as_list(t.expect.get("tool")) for t in scenario.turns]


def _kotlin_const(name: str) -> str:
    import re

    kotlin = (REPO_ROOT / "android-app/app/src/main/kotlin/ai/jarvis/app/car/voice/CarTurn.kt").read_text()
    m = re.search(rf"const val {name} =\n((?:\s*\".*?\"(?: \+)?\n)+)", kotlin)
    assert m, f"CarTurn.{name} is gone"
    return "".join(re.findall(r'"(.*?)"', m.group(1)))


def test_the_car_journeys_event_turns_are_the_phones_words():
    """The scenario sends what the phone sends. A reworded event in the Kotlin
    would otherwise leave the journey testing a prompt no phone says."""
    scenario, _ = _journey_tools()
    said = [t.say for t in scenario.turns]
    assert _kotlin_const("DRIVE_START") == said[0]
    assert _kotlin_const("DRIVE_END") == said[2]


def test_the_car_journey_walks_the_whole_drive_in_order():
    scenario, tools = _journey_tools()
    assert scenario.variants == ("text",)
    assert tools == [["phone_action"], ["car_navigate"], ["later_release", "loops_list"], ["phone_action"]]


def _play_journey(monkeypatch, missing: int):
    """Run car-journey.yaml through the real runner with every turn's tools
    called except those of turn `missing` (-1: none missing). Returns the
    result and the number of turns actually spoken."""
    import asyncio
    import types

    from testing.live.runner import Runner
    from testing.live.transport import Turn as Spoken

    scenario, tools = _journey_tools()
    called: list[str] = []

    class Observer:
        approvals: list = []

        def mark(self): return 0
        def event_mark(self): return 0
        def approval_mark(self): return 0
        def tool_mark(self): return len(called)
        def tools_since(self, mark): return called[mark:]
        def calls_since(self, mark): return []

        async def tasks(self): return []

    # Not Runner(): its constructor loads the owner's voice model, which a
    # rig test has no business needing.
    runner = Runner.__new__(Runner)
    runner._approvers, runner._approval_cursor = [], 0
    runner.observer = Observer()
    runner.link = types.SimpleNamespace(client=None)
    spoken: list[str] = []

    async def nothing(*_a, **_k): return {}

    async def speak(_transport, turn, _variant, conversation_id, _timeout):
        spoken.append(turn.say)
        if len(spoken) - 1 != missing:
            called.extend(tools[len(spoken) - 1])
        return Spoken(said=turn.say, reply_text="(quiet)", conversation_id=conversation_id or "t")

    async def judged(*_a, **_k): return types.SimpleNamespace(ok=True, why="")

    monkeypatch.setattr(runner, "_baseline", nothing)
    monkeypatch.setattr(runner, "_setup", nothing)
    monkeypatch.setattr(runner, "_sweep", lambda *_a, **_k: nothing())
    monkeypatch.setattr(runner, "_speak", speak)
    runner.judge = types.SimpleNamespace(check=judged)
    ground = types.SimpleNamespace(name="harness")
    result = asyncio.run(runner._run_scenario(scenario, "text", {"text": object()}, ground))
    return result, spoken


def test_the_car_journey_passes_when_every_step_happens(monkeypatch):
    result, spoken = _play_journey(monkeypatch, missing=-1)
    assert result.ok, [t.failures for t in result.turns]
    assert len(spoken) == 4


@pytest.mark.parametrize("step", [0, 1, 2, 3])
def test_a_journey_with_one_steps_tool_missing_fails_there_and_first(monkeypatch, step):
    """The failure this prevents: a journey that goes on to say the debrief
    after the route never opened, so the red line points at the debrief."""
    result, spoken = _play_journey(monkeypatch, missing=step)
    assert not result.ok
    assert len(result.turns) == step + 1 and len(spoken) == step + 1, "it played past the failed step"
    failed = result.turns[-1]
    assert failed.index == step and not failed.ok
    _, tools = _journey_tools()
    note = " ".join(failed.failures)
    assert f"turn {step + 1}" in note and tools[step][0] in note
