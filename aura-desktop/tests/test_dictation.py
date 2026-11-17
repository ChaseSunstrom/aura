"""Hold-to-dictate (T-1258): the app's key, not the model, authorises the typing.

The desktop app records while the owner holds a key, has the adapter transcribe it, and sends
the words here over its loopback socket as a ``dictate`` frame. What is pinned is the direction
every failure falls: a tap, an empty transcript, a refused transcription, a disabled input
switch, an owner who has taken the machine back and a connection with the wrong token each type
nothing. And that no model path reaches it: ``dictate`` is a frame on the shell's socket, never
an action the registry advertises.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import platform
import types
from pathlib import Path

import pytest

from aura_desktop.actions import inputauto
from aura_desktop.actions.builtins import build_registry
from aura_desktop.config import InputConfig, default_config_path, load_config
from aura_desktop.dictation import Dictation, clean
from aura_desktop.ipc import IpcServer
from aura_desktop.policy import ActionTier

from .test_ipc import Shell

pytestmark = pytest.mark.asyncio


class FakeGui:
    FAILSAFE = False
    PAUSE = 0.0

    def __init__(self) -> None:
        self.writes: list[str] = []
        self.pressed: list[str] = []

    def write(self, text, interval=0.0):
        self.writes.append(text)

    def press(self, key):
        self.pressed.append(key)


@pytest.fixture()
def gui(monkeypatch):
    fake = FakeGui()
    monkeypatch.setattr(inputauto, "_pyautogui", lambda: fake)
    return fake


@pytest.fixture()
def registry(config, policy, audit):
    config = dataclasses.replace(config, input_automation=InputConfig(enabled=True))
    return build_registry(config, policy, audit)


@pytest.fixture()
async def wired(registry):
    server = IpcServer()
    await server.start()
    dictation = Dictation(registry, registry.audit)
    dictation.attach(server)
    shell = await Shell.connect(server)
    await shell.next()  # the status frame
    yield types.SimpleNamespace(server=server, shell=shell, dictation=dictation, registry=registry)
    await shell.close()
    await server.stop()


async def until(predicate, timeout: float = 2.0) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


async def dictate(shell: Shell, text) -> None:
    await shell.send({"type": "dictate", "text": text})


async def test_dictation_types_what_the_shell_sends_into_the_focused_window(wired, gui):
    await dictate(wired.shell, "hello world")
    await until(lambda: gui.writes)
    assert gui.writes == ["hello world"]


async def test_dictation_that_is_empty_or_not_text_types_nothing(wired, gui):
    for nothing in ("", "   ", "\n\t", None, 7, ["a"], {"text": "a"}):
        await dictate(wired.shell, nothing)
    await dictate(wired.shell, "the one that counts")
    await until(lambda: gui.writes)
    # Frames are handled in order: when the last one has typed, the ones before it have been seen.
    assert gui.writes == ["the one that counts"]


async def test_dictation_never_presses_enter_whatever_the_transcript_holds(wired, gui):
    await dictate(wired.shell, "first line\nrm -rf nothing\r\nsecond\tcolumn")
    await until(lambda: gui.writes)
    assert gui.pressed == []
    assert gui.writes == ["first line rm -rf nothing second column"]


async def test_dictation_with_input_automation_off_types_nothing(wired, gui, config, policy, audit):
    off = build_registry(config, policy, audit)  # the config fixture has the switch off
    server = IpcServer()
    await server.start()
    Dictation(off, off.audit).attach(server)
    shell = await Shell.connect(server)
    await shell.next()
    await dictate(shell, "should not appear")
    await asyncio.sleep(0.2)
    assert gui.writes == []
    await shell.close()
    await server.stop()


async def test_dictation_waits_while_the_owner_has_taken_the_machine_back(wired, gui):
    wired.registry.ctx.visible = types.SimpleNamespace(takeover=types.SimpleNamespace(paused=True))
    await dictate(wired.shell, "not while they hold the mouse")
    await asyncio.sleep(0.2)
    assert gui.writes == []


async def test_dictation_over_the_limit_types_nothing_rather_than_a_cut_sentence(wired, gui):
    await dictate(wired.shell, "a" * (inputauto.MAX_TYPE_CHARS + 1))
    await dictate(wired.shell, "short")
    await until(lambda: gui.writes)
    assert gui.writes == ["short"]


async def test_dictation_from_a_connection_with_the_wrong_token_types_nothing(wired, gui):
    stranger = await Shell.connect(wired.server, token="not-the-token")
    await dictate(stranger, "let me in")
    await asyncio.sleep(0.2)
    assert gui.writes == []
    await stranger.close()


async def test_dictation_is_a_frame_on_the_shell_socket_not_an_action_the_model_can_call(wired):
    assert "dictate" not in wired.registry.ids()
    assert "dictation" not in wired.registry.capabilities()
    # type_text is untouched: still a fresh human approval every time.
    assert wired.registry.get("type_text").tier is ActionTier.CONFIRM
    assert all("dictat" not in entry["id"] for entry in wired.registry.manifest())


async def test_dictation_is_audited_by_length_never_by_its_words(wired, gui, audit):
    await dictate(wired.shell, "my password is hunter2 for the bank")
    await until(lambda: gui.writes)
    await until(lambda: audit.count() >= 1)
    line = json.dumps(audit.read_json())
    assert "hunter2" not in line
    entry = audit.read_json()[0]
    assert entry["action"] == "dictate" and entry["source"] == "dictation"
    assert entry["params"] == {"characters": len("my password is hunter2 for the bank")}
    assert entry["ok"] is True and entry["decision"] == "ALLOW"


async def test_dictation_is_audited_when_it_could_not_type(wired, gui, audit):
    wired.registry.ctx.visible = types.SimpleNamespace(takeover=types.SimpleNamespace(paused=True))
    await dictate(wired.shell, "held back")
    await until(lambda: audit.count() >= 1)
    entry = audit.read_json()[0]
    assert entry["ok"] is False and entry["status"] != "ok"


async def test_two_dictations_type_one_after_the_other_never_interleaved(wired, gui):
    await dictate(wired.shell, "one")
    await dictate(wired.shell, "two")
    await until(lambda: len(gui.writes) == 2)
    assert gui.writes == ["one", "two"]


async def test_dictation_clean_flattens_whitespace_and_refuses_an_empty_result():
    assert clean("  a\nb\r\nc\td  ") == "a b c d"
    assert clean("tabs\x00and\x1bcontrols") == "tabs and controls"
    assert clean("") is None and clean("\n\n") is None and clean(None) is None and clean(5) is None
    assert clean("café — ok") == "café — ok"


async def test_dictation_clean_drops_c1_controls_bidi_controls_and_zero_width_characters():
    # C1 controls are gaps like the C0 ones; the invisible ones vanish without splitting a word.
    assert clean("one\x9bd\x90two") == "one d two"
    assert clean("pay‮alice‬ now") == "payalice now"
    assert clean("a⁦b⁩c‎d‏e") == "abcde"
    assert clean("zero​width‌‍⁠join﻿") == "zerowidthjoin"
    assert clean("​‮\x9b") is None


SWITCH = json.loads(
    (Path(__file__).resolve().parents[2] / "tests" / "contracts" / "input-automation-switch.json").read_text(encoding="utf-8")
)


async def test_input_automation_switch_is_read_the_way_the_apps_contract_says(tmp_path):
    # T-1307: the desktop app reads this same file and variable to decide whether to register its
    # dictation key at all; the table both suites read is what keeps the two readings the same.
    for case in SWITCH["switch"]:
        config_file = tmp_path / "config.json"
        if case["config"] is None:
            config_file.unlink(missing_ok=True)
        else:
            config_file.write_text(case["config"], encoding="utf-8")
        env = {"AURA_STATE_DIR": str(tmp_path / "state")}
        if case["env"] is not None:
            env["AURA_INPUT_ENABLED"] = case["env"]
        if case.get("unreadable"):
            with pytest.raises(ValueError):
                load_config(config_file, env=env)
            continue
        assert load_config(config_file, env=env).input_automation.enabled is case["enabled"], case["name"]


async def test_input_automation_config_file_is_where_the_apps_contract_says(monkeypatch):
    for case in SWITCH["path"]:
        monkeypatch.setattr(platform, "system", lambda os=case["os"]: {"linux": "Linux", "macos": "Darwin"}[os])
        monkeypatch.setenv("HOME", case["home"])
        if case["xdg_config_home"] is None:
            monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        else:
            monkeypatch.setenv("XDG_CONFIG_HOME", case["xdg_config_home"])
        assert default_config_path() == Path(case["path"]), case


async def test_a_typing_failure_says_its_type_not_its_message(wired, gui, audit):
    # An exception's message can hold anything the typing library saw, a spoken password included.
    secret = "hunter2 spoken aloud"

    def boom(ctx, params):
        raise RuntimeError(secret)

    wired.registry.get("type_text").run = boom
    result = await wired.dictation.type_out("my words")
    assert not result.ok and "RuntimeError" in result.error and secret not in result.error
    await until(lambda: audit.count() >= 1)
    assert secret not in json.dumps(audit.read_json())
