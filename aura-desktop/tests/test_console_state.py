"""The console reads the gateway's events the way the web does, and the
reactor it derives is the contract's.

The event shapes are ``docs/hermes-hud-protocol.md``'s; the reactor truth
table is ``tests/contracts/reactor.json`` (``derivation.cases``), replayed
through the console's own inputs; the approval parse is held to
``tests/contracts/approval-event.json``, the table the audit log and the web
read, since the console cannot import the plugin tree's copy.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aura_desktop import reactor
from aura_desktop.console import state as S

REPO = Path(__file__).resolve().parents[2]
REACTOR = json.loads((REPO / "tests/contracts/reactor.json").read_text(encoding="utf-8"))
APPROVAL = json.loads((REPO / "tests/contracts/approval-event.json").read_text(encoding="utf-8"))
PANELS = json.loads((REPO / "tests/contracts/hud-panels.json").read_text(encoding="utf-8"))
LAYOUT = json.loads((REPO / "tests/contracts/hud-layout.json").read_text(encoding="utf-8"))


def ev(kind: str, payload: dict | None = None, sid: str = "s1", seq: int = 0) -> dict:
    return {"type": kind, "session_id": sid, "seq": seq, "payload": payload or {}}


@pytest.fixture()
def hud() -> S.Hud:
    h = S.Hud(session_id="s1")
    h.link(True)
    return h


# --- a turn ------------------------------------------------------------------------


def test_a_turn_streams_into_one_pending_reply_with_tool_rows_before_it(hud):
    hud.begin_turn("Warm the study", 1.0)
    assert hud.turn.running and hud.reactor().state is reactor.State.THINKING
    hud.apply(ev("tool.start", {"tool_id": "t1", "name": "ha_call_service", "args": {"a": 1}}), 1.1)
    assert hud.reactor().state is reactor.State.TOOL
    hud.apply(ev("tool.progress", {"tool_id": "t1", "text": "calling"}), 1.2)
    assert hud.turn.tools[0].progress == "calling"
    hud.apply(ev("tool.complete", {"tool_id": "t1", "name": "ha_call_service", "duration": 0.4}), 1.3)
    assert hud.turn.tools[0].state == "done" and hud.turn.tools[0].duration == 0.4
    hud.apply(ev("message.delta", {"text": "Done. "}), 1.4)
    hud.apply(ev("message.delta", {"text": "The heater is on."}), 1.5)
    assert hud.turn.got_token and hud.reactor().state is reactor.State.THINKING
    hud.apply(ev("message.complete", {"text": "ignored: the stream wins"}), 1.6)
    roles = [m.role for m in hud.messages]
    assert roles == ["you", "tool", "jarvis"], "the tool row sits between the question and the reply"
    reply = hud.messages[-1]
    assert reply.text == "Done. The heater is on." and not reply.pending
    assert not hud.turn.running and hud.reactor().state is reactor.State.IDLE


def test_a_failed_tool_and_a_failed_turn_read_as_alert(hud):
    hud.begin_turn("x", 0)
    hud.apply(ev("tool.start", {"tool_id": "t", "name": "web_search"}), 1)
    hud.apply(ev("tool.complete", {"tool_id": "t", "error": "timeout"}), 2)
    assert hud.turn.tools[0].state == "failed"
    hud.apply(ev("error", {"message": "the model went away"}), 3)
    assert hud.error == "the model went away" and not hud.turn.running
    assert hud.messages[-1].error == "the model went away" and not hud.messages[-1].pending
    assert hud.reactor().state is reactor.State.ALERT
    hud.begin_turn("again", 4)
    assert hud.error is None and hud.reactor().state is reactor.State.THINKING


def test_events_for_another_session_are_ignored(hud):
    hud.apply(ev("message.delta", {"text": "not mine"}, sid="other"), 1)
    assert hud.messages == []


def test_the_adapter_going_away_is_offline_at_rest_and_alert_mid_turn(hud):
    hud.apply(ev("adapter.error", {"error": "backend unavailable"}), 1)
    assert hud.backend_down and hud.reactor().state is reactor.State.OFFLINE
    hud.apply(ev("gateway.ready"), 2)
    assert not hud.backend_down
    hud.begin_turn("x", 3)
    hud.apply(ev("adapter.error", {"error": "backend unavailable"}), 4)
    assert hud.reactor().state is reactor.State.ALERT
    hud.link(False)
    assert hud.reactor().state is reactor.State.OFFLINE


# --- approvals and clarifies ---------------------------------------------------------


@pytest.mark.parametrize("case", APPROVAL["cases"], ids=lambda c: c["name"])
def test_the_approval_parse_is_the_shared_tables(case):
    assert S.approval_tool(case["event"]) == case["tool"]


def test_a_sensitive_ask_can_only_be_denied_here_and_says_where_the_yes_is(hud):
    spec = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/approval-event.json").read_text())["phone_confirmation"]
    hud.apply(ev("approval.request", {
        "request_id": "proof-1", "command": "<ha_call_service> (plugin approval rule)",
        "choices": spec["choices"], spec["confirm_field"]: spec["confirm_value"]}), 1)
    a = hud.approvals[0]
    assert a.on_phone and a.choices == ("deny",)
    from aura_desktop.console import render
    assert "fingerprint on the phone" in render._approval_lines(a, 120)[-1][0]
    hud.apply(ev("approval.resolved", {"request_id": "proof-1", "choice": "once"}), 2)
    assert hud.approvals == []


def test_an_approval_is_attention_and_its_choices_are_the_offered_ones(hud):
    hud.apply(ev("approval.request", {
        "request_id": "r1", "command": "<write_file> (plugin approval rule)",
        "pattern_key": "plugin_rule:file:sealed", "description": "Writing outside the workspace.",
        "choices": ["once", "deny"]}), 1)
    a = hud.approvals[0]
    assert a.tool == "write_file" and a.reason == "Writing outside the workspace."
    assert a.choices == ("once", "deny")
    assert a.detail == "", "a plugin-rule label is not a command worth repeating"
    assert hud.reactor().state is reactor.State.ATTENTION
    # Re-announced on a reconnect: one card, not two.
    hud.apply(ev("approval.request", {"request_id": "r1", "command": "<write_file> (plugin approval rule)"}), 2)
    assert len(hud.approvals) == 1 and hud.approvals[0].reason == "Writing outside the workspace."
    hud.apply(ev("approval.resolved", {"request_id": "r1"}), 3)
    assert hud.approvals == [] and hud.reactor().state is reactor.State.IDLE


def test_a_shell_gate_shows_its_command(hud):
    hud.apply(ev("approval.request", {"request_id": "r2", "command": "rm -rf /opt/data/work",
                                      "pattern_key": "dangerous_command:rm"}), 1)
    assert hud.approvals[0].tool == "Command" and hud.approvals[0].detail == "rm -rf /opt/data/work"
    assert hud.approvals[0].choices == S.APPROVAL_CHOICES


def test_a_clarify_question_is_attention_too(hud):
    hud.apply(ev("clarify.request", {"request_id": "c1", "question": "Which study?", "options": ["Upstairs", "Down"]}), 1)
    assert hud.clarifies[0].options == ("Upstairs", "Down")
    assert hud.reactor().state is reactor.State.ATTENTION


# --- the goal ------------------------------------------------------------------------


@pytest.mark.parametrize("text,phase,turn,budget,reason", [
    ("⊙ Goal set (20-turn budget): Keep the study at 21°", "set", None, 20, "Keep the study at 21°"),
    ("↻ Continuing toward goal (3/20): The study is still at 19°.", "continuing", 3, 20, "The study is still at 19°."),
    ("⏳ Goal (parked waiting on pid 4242)", "parked", None, None, "parked waiting on pid 4242"),
    ("⏸ Goal paused — 3/20 turns used", "paused", 3, 20, "3/20 turns used"),
    ("✓ Goal achieved: the room is warm", "achieved", None, None, "the room is warm"),
    ("Goal cleared", "cleared", None, None, ""),
    ("✓ Goal cleared.", "cleared", None, None, ""),
    ("No active goal", "none", None, None, ""),
    ("", "none", None, None, ""),
])
def test_the_goal_line_is_read_by_its_glyph_and_the_reason_kept_verbatim(text, phase, turn, budget, reason):
    g = S.parse_goal(text, 5.0)
    assert (g.phase, g.turn, g.budget, g.reason) == (phase, turn, budget, reason)
    assert g.text == text.strip() and g.at == 5.0


def test_a_live_goal_is_the_overlay_and_a_parked_one_is_attention(hud):
    hud.apply(ev("status.update", {"kind": "goal", "text": "⊙ Goal set (20-turn budget): x"}), 1)
    d = hud.reactor()
    assert d.state is reactor.State.IDLE and d.goal
    hud.apply(ev("status.update", {"kind": "goal", "text": "⏳ Goal (parked waiting on pid 1)"}), 2)
    assert hud.reactor().state is reactor.State.ATTENTION
    hud.apply(ev("status.update", {"kind": "goal", "text": "✓ Goal achieved: done"}), 3)
    assert not hud.reactor().goal
    hud.apply(ev("status.update", {"kind": "other", "text": "thinking about it"}), 4)
    assert hud.turn.status == "thinking about it"


# --- the reactor truth table ----------------------------------------------------------


def _hud_for(inputs: dict) -> S.Hud:
    """A Hud in the state the contract's inputs describe."""
    h = S.Hud(session_id="s1")
    h.link(inputs.get("linkUp", True))
    h.kill = inputs.get("kill", False)
    h.backend_down = inputs.get("adapterError", False)
    if inputs.get("turnRunning"):
        h.begin_turn("x", 0)
    if inputs.get("errorEvent"):
        h.turn.error = "boom"
    if inputs.get("attention"):
        h.approvals.append(S.Approval("r", "s1", "t", "", "", S.APPROVAL_CHOICES, 0))
    if inputs.get("warming"):
        h.model_state = "starting"
    for i in range(inputs.get("toolsRunning", 0)):
        h.turn.tools.append(S.Tool(f"t{i}", "tool"))
    if inputs.get("goalActive"):
        h.goal = S.parse_goal("⊙ Goal set (20-turn budget): x")
    return h


@pytest.mark.parametrize("case", REACTOR["derivation"]["cases"], ids=lambda c: c["name"])
def test_the_contracts_truth_table_holds_through_the_consoles_inputs(case):
    inputs = case["inputs"]
    if inputs.get("speaking") or inputs.get("listening"):
        pytest.skip("voice states come from the microphone path, which the console has none of")
    if inputs.get("warming") and not inputs.get("turnRunning"):
        pytest.skip("warming counts only while a turn waits on it (the web's reading)")
    h = _hud_for(inputs)
    got = h.reactor()
    assert got.state.value == case["state"]
    assert got.goal == case["goal"]


def test_warming_counts_only_while_a_turn_waits(hud):
    hud.apply_status({"model": {"state": "starting", "progress": 40}, "kill_switch": {"engaged": False}})
    assert hud.reactor().state is reactor.State.IDLE
    assert hud.warming_progress == 0.4
    hud.begin_turn("x", 0)
    assert hud.reactor().state is reactor.State.WARMING


def test_the_kill_switch_outranks_everything(hud):
    hud.begin_turn("x", 0)
    hud.apply_status({"model": {"state": "ready"}, "kill_switch": {"engaged": True, "reason": "leak"}})
    assert hud.reactor().state is reactor.State.KILL
    assert not hud.reactor().goal


# --- panels and layout ------------------------------------------------------------------


@pytest.mark.parametrize("kind", PANELS["kinds"])
def test_every_content_kind_has_a_home_panel_type(kind):
    assert S.panel_type_for(kind) in LAYOUT["panel_types"]


def test_content_cards_follow_rev_and_dismiss(hud):
    show = {"id": "p1", "kind": "telemetry", "title": "GPUs", "payload": PANELS["examples"]["telemetry"],
            "origin": "agent", "created_at": "x", "rev": 2}
    hud.apply(ev("hud.panel.show", show), 1)
    hud.apply(ev("hud.panel.update", {"id": "p1", "rev": 1, "title": "stale"}), 2)
    assert hud.panels[0].title == "GPUs", "a lower rev is ignored"
    hud.apply(ev("hud.panel.update", {"id": "p1", "rev": 3, "title": "GPUs (live)"}), 3)
    assert hud.panels[0].title == "GPUs (live)" and hud.panels[0].home == "telemetry"
    assert hud.content_for("telemetry") == hud.panels and hud.homeless({"chat"}) == hud.panels
    assert hud.homeless({"telemetry"}) == []
    hud.apply(ev("hud.panel.dismiss", {"id": PANELS["dismiss_all_id"]}), 4)
    assert hud.panels == []


def test_layout_frames_replace_the_document_and_stale_revs_are_dropped(hud):
    working = LAYOUT["presets"]["working"]["layout"]
    goal = LAYOUT["presets"]["goal-start"]["layout"]
    hud.apply(ev("hud.layout.apply", {"op": "arrange", "surface": "console", "activity": "default",
                                      "layout": working, "rev": 2, "reason": "preset working", "undo_depth": 1}), 1)
    hud.apply(ev("hud.layout.apply", {"op": "arrange", "surface": "console", "activity": "goal",
                                      "layout": goal, "rev": 1, "reason": "old"}), 2)
    assert hud.layout == working and hud.layout_activity == "default" and hud.undo_depth == 1
    hud.apply(ev("hud.layout.apply", {"op": "arrange", "surface": "console", "activity": "goal",
                                      "layout": goal, "rev": 3, "reason": "preset goal-start"}), 3)
    assert hud.layout == goal and hud.layout_op == "arrange"
    hud.apply(ev("hud.layout.highlight", {"panel": "goals", "duration_ms": LAYOUT["highlight_ms"]}), 4)
    assert hud.highlight == ("goals", 4 + LAYOUT["highlight_ms"] / 1000)


def test_a_late_joiner_reads_the_layout_state(hud):
    hud.apply_layout_state({"locked": True, "activity": "goal", "undo_depth": 2,
                            "layouts": {"web": LAYOUT["presets"]["working"]["layout"]}}, "console")
    assert hud.lock and hud.undo_depth == 2
    assert hud.layout == LAYOUT["presets"]["working"]["layout"] and hud.layout_surface == "web"


def test_session_info_and_background_rows(hud):
    hud.apply(ev("session.info", {"model": "qwen3-30b", "provider": "llama-swap", "approval_mode": "smart",
                                  "tools": [{}] * 14, "running": True}), 1)
    assert (hud.model, hud.tools_count, hud.turn.running) == ("qwen3-30b", 14, True)
    hud.apply(ev("subagent.start", {"id": "a1", "goal": "find the manual"}), 2)
    hud.apply(ev("process.start", {"pid": "9", "command": "make test"}), 3)
    assert [(r.kind, r.state) for r in hud.background] == [("subagent", "running"), ("process", "running")]
    hud.apply(ev("subagent.complete", {"id": "a1", "status": "failed", "summary": "no manual"}), 4)
    hud.apply(ev("process.exit", {"pid": "9", "status": "0"}), 5)
    assert [(r.state, r.detail) for r in hud.background] == [("failed", "no manual"), ("done", "0")]


def test_a_resumed_transcript_is_loaded_in_order(hud):
    hud.load_history([
        {"role": "user", "text": "Warm the study", "timestamp": 10},
        {"role": "tool", "name": "ha_call_service", "args": {"entity_id": "climate.study"}, "timestamp": 11},
        {"role": "tool", "name": "web_search", "error": "timeout", "timestamp": 12},
        {"role": "assistant", "text": "Done.", "reasoning": "…", "timestamp": 13},
        "not a row",
    ], 99.0)
    assert [m.role for m in hud.messages] == ["you", "tool", "tool", "jarvis"]
    assert hud.messages[1].tool is not None and hud.messages[1].tool.state == "done"
    assert hud.messages[2].tool is not None and hud.messages[2].tool.state == "failed"
    assert hud.messages[-1].text == "Done." and not hud.messages[-1].pending and hud.messages[-1].at == 13


# --- who is primary ----------------------------------------------------------------


def test_the_persona_follows_the_switch_and_the_status_and_ignores_a_stranger(hud):
    """ADR-0043: a console that still says JARVIS after a switch to AURA is the defect."""
    assert hud.persona == "jarvis"
    hud.apply_status({"model": {"state": "ready"}, "kill_switch": {"engaged": False}, "persona": {"active": "aura"}})
    assert hud.persona == "aura"
    hud.apply(ev("persona.switch", {"active": "Jarvis", "previous": "aura"}), 1.0)
    assert hud.persona == "jarvis"
    hud.apply(ev("persona.switch", {"active": "hal"}), 2.0)
    assert hud.persona == "jarvis", "not one of the two: left alone, not guessed"
    hud.apply(ev("persona.switch", {"active": "ada"}), 3.0)  # T-0936: removed, so a stranger now
    assert hud.persona == "jarvis"
    hud.apply_status({"model": {"state": "ready"}})
    assert hud.persona == "jarvis", "an older adapter says nothing about it: nothing changes"


def test_the_surface_report_is_the_contract_row_for_the_console():
    """`tests/contracts/surface-capabilities.json`: a terminal can hand a file
    over and show nothing, and it says so on every prompt so a video asked for
    here is handed to the desktop rather than described."""
    contract = json.loads((REPO / "tests/contracts/surface-capabilities.json").read_text())
    assert S.SURFACE_REPORT == contract["surfaces"]["console"]
    assert list(S.SURFACE_REPORT) == contract["fields"]


# --- the activity view (tests/contracts/activity-frames.json) -----------------------

_ACTIVITY = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/activity-frames.json").read_text())


def _ev(kind: str, payload: dict) -> dict:
    return {"type": kind, "session_id": "live-1", "payload": payload}


def test_the_activity_events_fold_into_one_card_in_the_browser_panel():
    hud = S.Hud(session_id="live-1")
    ex = _ACTIVITY["examples"]
    for kind in ("activity.start", "activity.step", "activity.frame"):
        hud.apply(_ev(kind, ex[kind]), 1.0)
    cards = hud.content_for("browser")
    assert [c.kind for c in cards] == [S.ACTIVITY_KIND]
    a = cards[0].payload
    assert a["status"] == "running" and a["title"] == "Sign in"
    assert a["steps"][0]["action"] == "type into Password: [redacted]"
    # A late, older frame does not paint over a newer one.
    hud.apply(_ev("activity.frame", {**ex["activity.frame"], "seq": 1, "title": "Old"}), 2.0)
    assert cards[0].payload["title"] == "Sign in"
    hud.apply(_ev("activity.end", {**ex["activity.end"], "status": "failed"}), 3.0)
    assert cards[0].payload["status"] == "failed"
    # The next run replaces the card rather than stacking a second one.
    hud.apply(_ev("activity.start", {**ex["activity.start"], "id": "a_next"}), 4.0)
    assert [c.payload["id"] for c in hud.content_for("browser")] == ["a_next"]


def test_the_activity_card_renders_as_text_with_its_trail():
    from aura_desktop.console import render as R

    hud = S.Hud(session_id="live-1")
    ex = _ACTIVITY["examples"]
    for kind in ("activity.start", "activity.step", "activity.frame"):
        hud.apply(_ev(kind, ex[kind]), 1.0)
    card = hud.content_for("browser")[0]
    text = "\n".join(line for line, _ in R._content_lines(card, 80, R.Caps(unicode=True)))
    assert "Live  browser" in text and "https://example.com/login" in text
    assert "> 2  type into Password: [redacted]" in text


def test_an_interrupted_turn_is_stopped_not_failed():
    """A stop the person asked for is not an error: the ring stays out of alert."""
    reactor_contract = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/reactor.json").read_text())
    assert list(S.QUIET_ENDS) == reactor_contract["derivation"]["quiet_ends"]["statuses"]
    hud = S.Hud(session_id="live-1")
    hud.begin_turn("delete the scratch folder", 1.0)
    hud.apply(_ev("message.complete", {"text": "Not running it.", "status": "interrupted"}), 2.0)
    assert hud.turn.error is None and not hud.turn.running
    hud.begin_turn("again", 3.0)
    hud.apply(_ev("message.complete", {"text": "", "status": "timeout"}), 4.0)
    assert hud.turn.error == "timeout"


# --- the one status word (T-0583) -----------------------------------------------------
# Read with no guard: a moved table fails here, as it fails the console's and the phone's suites.
STATUS_WORDS = json.loads((REPO / "tests/contracts/status-words.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", STATUS_WORDS["cases"], ids=lambda c: c["name"])
def test_status_word_is_printed_from_the_adapters_field(case):
    from aura_desktop.console import render

    hud = S.Hud()
    # The rest of the report disagrees with the field on purpose: the label reads only the field.
    hud.apply_status({"ok": True, "model": {"model": "m", "state": "ready"},
                      "status": {"word": case["word"], "reason": case["reason"]}})
    assert hud.status_label == case["label"]
    assert case["label"] in [text for text, _ in render._model_lines(hud, 60)]


def test_status_word_no_answer_is_the_tables_fixed_label():
    hud = S.Hud()
    hud.apply_status({"model": {"state": "ready"}, "status": {"word": "ready", "reason": ""}})
    hud.apply_status(None)
    assert hud.status_label == STATUS_WORDS["no_answer"]["label"]
    assert hud.model_state == "ready"  # what was last heard stays
    # Started with the adapter out of reach: no model was ever heard, and the pane still says so.
    from aura_desktop.console import render

    cold = S.Hud()
    cold.apply_status(None)
    assert (STATUS_WORDS["no_answer"]["label"], "C:danger-text") in render._model_lines(cold, 60)


def test_status_word_absent_leaves_the_present_wording():
    from aura_desktop.console import render

    hud = S.Hud()
    hud.apply_status({"model": {"state": "starting"}, "kill_switch": {"engaged": False}})
    assert hud.status_label == ""
    assert ("warming up", "C:warming") in render._model_lines(hud, 60)
    hud.apply_status({"model": {"state": "ready"}, "status": "fine"})
    assert ("ready", "C:ok") in render._model_lines(hud, 60)


def test_status_word_length_every_reason_is_within_the_tables_limit():
    limit = STATUS_WORDS["reason_max"]
    reasons = [c["reason"] for c in STATUS_WORDS["cases"]] + [STATUS_WORDS["no_answer"]["reason"]]
    assert limit <= 28 and all(len(r) <= limit for r in reasons)


@pytest.mark.parametrize("case", REACTOR["derivation"]["sequences"]["cases"], ids=lambda c: c["name"])
def test_what_the_reducer_feeds_the_table(case):
    """The contract's event sequences, through this surface's own reducer (T-1483).

    Session-less events, as the adapter sends its own two; not covered: an event for
    another session, which `_ours` drops before any of this.
    """
    hud = S.Hud()
    hud.link(True)
    for at, event in enumerate(case["events"]):
        hud.apply({"type": event["type"], "payload": event.get("payload") or {}}, float(at))
    assert hud.reactor().state.value == case["state"]


def test_the_sequences_table_is_not_empty():
    assert len(REACTOR["derivation"]["sequences"]["cases"]) > 2
