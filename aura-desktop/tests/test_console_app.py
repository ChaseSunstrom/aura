"""The console's socket client, keys and verbs, without a terminal or a network.

The one protocol rule that shapes the client is proved here: events a handler
emits arrive **before** that handler's reply, and none may be lost while a
request waits. The verbs are the §9.8 chords the console offers, each turned
into the request the protocol names, and nothing else: the tree operations
(split, swap, close, mode) are the plugin's and the web's, and the console
does not pretend to have them.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from aura_desktop.console import app, panes, render
from aura_desktop.console import theme as T
from aura_desktop.console.state import Hud

REPO = Path(__file__).resolve().parents[2]
LAYOUT = json.loads((REPO / "tests/contracts/hud-layout.json").read_text(encoding="utf-8"))
NONE = T.Caps("none", True, True)


# --- the gateway -------------------------------------------------------------------


def test_events_before_a_reply_are_queued_and_the_reply_still_resolves():
    async def go():
        gw = app.Gateway("ws://x/ws", "k")
        sent: list[str] = []

        class Ws:
            async def send(self, raw: str) -> None:
                sent.append(raw)

        gw._ws = Ws()
        waiting = asyncio.ensure_future(gw.request("command.dispatch", {"name": "hud_layout"}))
        await asyncio.sleep(0)
        request = json.loads(sent[0])
        assert request["jsonrpc"] == "2.0" and request["method"] == "command.dispatch" and request["id"] == 1
        # The plugin's apply frame goes out on the socket ahead of the response.
        gw.feed(json.dumps({"jsonrpc": "2.0", "method": "event",
                            "params": {"type": "hud.layout.apply", "session_id": "s", "payload": {"rev": 1}}}))
        gw.feed(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"type": "plugin", "output": "{}"}}))
        assert await waiting == {"type": "plugin", "output": "{}"}
        assert gw.events.get_nowait()["type"] == "hud.layout.apply"
        # A reply for an id nobody waits on, and junk, are dropped, not raised.
        gw.feed(json.dumps({"jsonrpc": "2.0", "id": 99, "result": {}}))
        gw.feed("not json")
        gw.feed(json.dumps([1, 2]))
        assert gw.events.empty()

    asyncio.run(go())


def test_a_json_rpc_error_becomes_a_gateway_error_with_its_code():
    async def go():
        gw = app.Gateway("ws://x/ws", "k")

        class Ws:
            async def send(self, raw: str) -> None:
                pass

        gw._ws = Ws()
        waiting = asyncio.ensure_future(gw.request("session.resume", {"session_id": "old"}))
        await asyncio.sleep(0)
        gw.feed(json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": 4001, "message": "session not found"}}))
        with pytest.raises(app.GatewayError) as exc:
            await waiting
        assert exc.value.code == 4001 and "session not found" in str(exc.value)

    asyncio.run(go())


@pytest.mark.parametrize("adapter,ws,status", [
    ("https://jarvis.example:8643", "wss://jarvis.example:8643/ws", "https://jarvis.example:8643/api/status"),
    ("http://127.0.0.1:8643", "ws://127.0.0.1:8643/ws", "http://127.0.0.1:8643/api/status"),
    ("wss://jarvis.example:8643/ws", "wss://jarvis.example:8643/ws", "https://jarvis.example:8643/api/status"),
    ("jarvis.example:8643", "ws://jarvis.example:8643/ws", "http://jarvis.example:8643/api/status"),
])
def test_the_adapter_address_gives_the_socket_and_the_status_url(adapter, ws, status):
    assert app.ws_url(adapter) == ws
    assert app.http_url(adapter, "/api/status") == status


# --- keys ----------------------------------------------------------------------------


def test_raw_bytes_become_keys_and_a_held_alt_is_a_chord():
    keys = app.parse_keys(b"hi\r\x7f\x1b\x1bl\x1b1\x1bH\x03\x1b[A\x1bOB")
    assert keys == [
        app.Key("char", "h"), app.Key("char", "i"), app.Key("enter"), app.Key("backspace"),
        app.Key("esc"), app.Key("alt", "l"), app.Key("alt", "1"), app.Key("alt", "H"), app.Key("ctrl", "c"),
    ], "arrow sequences are dropped rather than typed"
    assert app.parse_keys(b"\x1b") == [app.Key("esc")]
    assert app.parse_keys("é".encode()) == [app.Key("char", "é")]


# --- the verbs -----------------------------------------------------------------------


@pytest.fixture()
def console() -> app.Console:
    hud = app.demo_hud("task", LAYOUT)
    c = app.Console(hud, T.Theme(), NONE, cols=100, rows=28, presets=["approval-focus", "working"])
    c.frame(0.0)
    return c


def test_typing_and_enter_submit_a_prompt(console):
    for ch in "warm it":
        console.on_key(app.Key("char", ch))
    console.on_key(app.Key("backspace"))
    assert console.composer == "warm i"
    console.on_key(app.Key("enter"))
    assert console.composer == ""
    assert console.outbox == [("prompt.submit", {"session_id": "demo", "text": "warm i", "surface": app.SURFACE_REPORT})]
    assert console.hud.messages[-2].text == "warm i" and console.hud.turn.running


def test_a_slash_command_is_dispatched_not_submitted(console):
    for ch in "/goal status":
        console.on_key(app.Key("char", ch))
    console.on_key(app.Key("enter"))
    assert console.outbox == [("command.dispatch", {"session_id": "demo", "name": "goal", "arg": "status"})]


def test_the_layout_chords_go_through_the_plugin_command(console):
    console.on_key(app.Key("alt", "l"))
    console.on_key(app.Key("alt", "l"))
    console.on_key(app.Key("alt", "z"))
    console.on_key(app.Key("alt", "k"))
    assert console.hud.lock
    console.on_key(app.Key("alt", "k"))
    assert not console.hud.lock
    args = [p["arg"] for m, p in console.outbox if m == "command.dispatch" and p["name"] == "hud_layout"]
    assert args == ["@demo arrange approval-focus", "@demo arrange working", "@demo undo", "@demo lock", "@demo unlock"]


def test_focus_moves_by_ordinal_and_direction_and_stays_local(console):
    assert console.focused == "chat"
    console.on_key(app.Key("alt", "2"))
    assert console.focused == "timeline"
    console.on_key(app.Key("alt", "J"))
    assert console.focused == "goals"
    console.on_key(app.Key("alt", "L"))
    assert console.focused == "model"
    console.on_key(app.Key("alt", "H"))
    assert console.focused == "goals"
    console.on_key(app.Key("alt", "9"))
    assert console.focused == "goals", "an ordinal with no pane leaves focus where it was"
    assert console.outbox == [], "focus is the console's own; nothing is sent"


def test_the_approvals_panel_answers_with_the_choices_letter(console):
    hud = app.demo_hud("focus", LAYOUT)
    console.hud = hud
    console.frame(0.0)
    assert console.focused == "approvals"
    console.on_key(app.Key("char", "x"))
    assert console.composer == "x", "a letter that is no choice is typed"
    console.composer = ""
    console.on_key(app.Key("char", "s"))
    assert console.outbox[-1] == ("approval.respond", {"session_id": "demo", "request_id": "r1", "choice": "session"})
    assert hud.approvals == []


def test_only_offered_choices_answer(console):
    hud = app.demo_hud("focus", LAYOUT)
    hud.approvals[0].choices = ("once", "deny")
    console.hud = hud
    console.frame(0.0)
    console.on_key(app.Key("char", "a"))
    assert console.outbox == [] and console.composer == "a"


def test_a_clarify_answers_by_number(console):
    hud = console.hud
    hud.apply({"type": "clarify.request", "session_id": "demo", "payload": {
        "request_id": "c1", "question": "Which?", "options": ["Up", "Down"]}}, 1)
    hud.layout = LAYOUT["presets"]["approval-focus"]["layout"]
    console.frame(0.0)
    console.on_key(app.Key("char", "2"))
    assert console.outbox[-1] == ("clarify.respond", {"session_id": "demo", "request_id": "c1", "answer": "Down"})


def test_the_goal_panel_takes_pause_and_clear(console):
    console.on_key(app.Key("alt", "3"))
    assert console.focused_type() == "goals"
    console.on_key(app.Key("char", "p"))
    console.on_key(app.Key("char", "c"))
    assert [p["arg"] for _, p in console.outbox] == ["pause", "clear"]


def test_escape_clears_the_composer_then_dismisses_the_last_panel(console):
    console.on_key(app.Key("char", "x"))
    console.on_key(app.Key("esc"))
    assert console.composer == "" and console.outbox == []
    console.on_key(app.Key("esc"))
    assert console.outbox == [("command.dispatch", {"session_id": "demo", "name": "hud", "arg": "@demo dismiss p_gpus"})]


def test_ctrl_c_quits(console):
    console.on_key(app.Key("ctrl", "c"))
    assert console.quit


def test_a_send_result_starts_the_turn_it_describes():
    async def go():
        console = app.Console(Hud(session_id="s"), T.Theme(), NONE)
        console.outbox.append(("command.dispatch", {"session_id": "s", "name": "goal", "arg": "warm it"}))

        class Gw:
            async def request(self, method, params):
                if method == "command.dispatch":
                    return {"type": "send", "notice": "⊙ Goal set (20-turn budget): warm it", "message": "warm it"}
                return {"status": "streaming"}

        await app._flush(Gw(), console)  # noqa: SLF001 — the loop's own step
        assert console.outbox == [] and console.hud.turn.running
        assert console.hud.messages[0].text == "⊙ Goal set (20-turn budget): warm it"

    asyncio.run(go())


# --- the demo and the command --------------------------------------------------------


@pytest.mark.parametrize("mode", panes.MODES)
def test_the_demo_command_prints_one_frame_per_mode(mode):
    out = subprocess.run(
        [sys.executable, "-m", "aura_desktop", "console", "--demo", mode, "--cols", "90", "--rows", "20", "--plain"],
        capture_output=True, text=True, cwd=REPO / "aura-desktop", check=True,
    ).stdout.splitlines()
    assert len(out) == 20 and all(len(r) == 90 for r in out)
    assert "\x1b" not in "".join(out)


def test_the_demo_frames_in_the_docs_are_what_the_code_draws():
    """The captured frames are evidence only while they are current."""
    for mode in panes.MODES:
        path = REPO / "docs/design/screens" / f"console-{mode}.txt"
        assert path.is_file(), f"missing {path.name}: run scripts/console-frames.sh"
        rows = app.demo_frame(mode, 100, 30, NONE, plain=True, contract=LAYOUT)
        assert path.read_text(encoding="utf-8").rstrip("\n").splitlines() == rows, path.name


def test_the_console_needs_an_adapter_and_a_key(capsys):
    import argparse

    bare = argparse.Namespace(demo=None, adapter=None, key=None, key_file=None, reduced_motion=False)
    rc = app.main(bare, env={})
    assert rc == 2 and "AURA_ADAPTER_URL / AURA_ADAPTER_KEY" in capsys.readouterr().err
    # The key alone is not enough either, and the environment is read only through
    # the injected mapping: the schema's two names, the ones the web console uses.
    assert app.main(bare, env={"AURA_ADAPTER_KEY": "k"}) == 2


def test_width_helper_agrees_with_the_frames():
    fr = render.frame(app.demo_hud("task", LAYOUT), 77, 19, T.Theme(), T.Caps("256", True, True))
    assert render.width_ok(fr.rows, 77)


def test_a_late_joiner_reads_presets_the_layout_state_and_the_panels():
    """`_join` against a fake gateway answering the plugin's own shapes: the
    presets come from the `presets` table, `working` is the default layout when
    none was applied, and `hud list` replays the cards."""
    async def go():
        working = LAYOUT["presets"]["working"]
        replies = {
            "@s get": {"ok": True, "locked": True, "activity": "default", "layouts": {}, "undo_depth": 0,
                       "presets": sorted(LAYOUT["presets"])},
            "@s presets": {"ok": True, "presets": LAYOUT["presets"]},
        }

        class Gw:
            async def request(self, method, params):
                if params["name"] == "hud_layout":
                    return {"type": "plugin", "output": json.dumps(replies[params["arg"]])}
                return {"type": "plugin", "output": json.dumps([{
                    "id": "p1", "kind": "notice", "title": "Model warmed up", "origin": "system",
                    "created_at": "x", "rev": 1, "payload": {"level": "info", "text": "ready"}}])}

        console = app.Console(Hud(session_id="s"), T.Theme(), NONE)
        await app._join(Gw(), console)
        assert console.presets == sorted(LAYOUT["presets"])
        assert console.hud.layout == working["layout"] and console.hud.layout_activity == "default"
        assert console.hud.lock
        assert [c.title for c in console.hud.panels] == ["Model warmed up"]

    asyncio.run(go())
