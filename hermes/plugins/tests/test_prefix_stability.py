"""What our plugins put in front of vLLM's prefix cache (T-0637).

Hermes builds the prompt as stable -> context -> volatile and appends a ``pre_llm_call``
hook's ``{"context": ...}`` to the current user message, never the system prompt. A plugin
that broke either rule (a section that differs per call, a section anchored earlier than the
volatile tail, a hook that returns something else) would re-prefill the whole 20-50k-token
prompt every turn. These tests pin our side of that; they do not cover Hermes's own assembly,
which the report in bench/results/model-first-token.md measured on the live prompt.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _plugin_loader import FakeCtx, load_plugin  # noqa: E402

PLUGINS_DIR = Path(__file__).resolve().parents[1]


class _Rec(FakeCtx):
    """FakeCtx keeps one hook per event; the HUD registers four pre_llm_call hooks."""

    def __init__(self) -> None:
        super().__init__()
        self.pre_llm: list = []
        self.sections: list[tuple[str, object, str]] = []

    def register_hook(self, event, callback):
        super().register_hook(event, callback)
        if event == "pre_llm_call":
            self.pre_llm.append(callback)

    def register_system_prompt_section(self, id, content, position="after_memory", **_):
        self.sections.append((id, content, position))


def _all() -> _Rec:
    ctx = _Rec()
    for manifest in sorted(PLUGINS_DIR.glob("*/plugin.yaml")):
        load_plugin(manifest.parent.name).register(ctx)
    return ctx


@pytest.fixture(scope="module")
def ctx(tmp_path_factory) -> _Rec:
    mp = pytest.MonkeyPatch()
    mp.setenv("HERMES_HOME", str(tmp_path_factory.mktemp("hermes-home")))
    try:
        yield _all()
    finally:
        mp.undo()


def test_prefix_every_section_sits_in_the_volatile_tail(ctx: _Rec) -> None:
    assert ctx.sections, "no plugin registered a system prompt section: the test lost its subject"
    for sid, _, position in ctx.sections:
        assert position == "after_memory", f"{sid} is anchored at {position}, ahead of the tail"


def test_prefix_a_section_renders_the_same_twice(ctx: _Rec) -> None:
    # Hermes freezes a section per session; a render that varies with the clock or a counter
    # would still break the rebuild after compaction, where the prefix is re-read.
    info = {"session_id": "prefix-test"}
    for sid, render, _ in ctx.sections:
        assert render(info) == render(info), f"{sid} differs between two renders"  # type: ignore[operator]


def test_pre_llm_call_hooks_only_return_user_message_context(ctx: _Rec) -> None:
    # Hermes appends {"context": str} (or a str) to the user message; any other key is dropped
    # and anything that mutated the system prompt here would be a per-turn value up front.
    assert ctx.pre_llm, "no pre_llm_call hook registered: the test lost its subject"
    for hook in ctx.pre_llm:
        out = hook(session_id="prefix-test", user_message="hello", platform="tui",
                   is_first_turn=True, model="m", conversation_history=[])
        assert out is None or isinstance(out, str) or (
            isinstance(out, dict) and set(out) == {"context"} and isinstance(out["context"], str)
        ), f"{getattr(hook, '__qualname__', hook)} returned {out!r}"
