"""A spoken prompt runs at the voice reasoning effort, a typed one at the default (T-0641)."""
from __future__ import annotations

import json

from aura_adapter import config, effort


def _prompt(session="s-1", ticket=None):
    params = {"session_id": session, "text": "hi"}
    if ticket:
        params["speaker_ticket"] = ticket
    return json.dumps({"jsonrpc": "2.0", "id": 1, "method": "prompt.submit", "params": params})


def _levels(frames):
    return [(json.loads(f)["params"]["session_id"], json.loads(f)["params"]["value"]) for f in frames]


def test_a_ticketed_prompt_is_lowered_and_the_next_typed_one_is_put_back():
    e = effort.Efforts(lambda: ("low", "medium"))
    assert _levels(e.before(_prompt(ticket="t"))) == [("s-1", "low")]
    assert e.before(_prompt(ticket="t")) == []  # already low: no second frame
    back = e.before(_prompt())
    assert _levels(back) == [("s-1", "medium")]
    assert json.loads(back[0])["method"] == "config.set" and json.loads(back[0])["params"]["key"] == "reasoning"
    assert e.before(_prompt()) == []  # typed on a typed session: untouched, as before


def test_a_typed_prompt_on_a_fresh_session_sends_nothing():
    assert effort.Efforts(lambda: ("low", "medium")).before(_prompt()) == []


def test_sessions_are_independent():
    e = effort.Efforts(lambda: ("low", "medium"))
    e.before(_prompt("a", ticket="t"))
    assert e.before(_prompt("b")) == []


def test_the_setting_is_read_on_every_prompt():
    levels = [("low", "medium")]
    e = effort.Efforts(lambda: levels[0])
    e.before(_prompt(ticket="t"))
    levels[0] = ("none", "medium")  # said by voice between two turns
    assert _levels(e.before(_prompt(ticket="t"))) == [("s-1", "none")]


def test_voice_equal_to_typed_sends_nothing():
    assert effort.Efforts(lambda: ("medium", "medium")).before(_prompt(ticket="t")) == []


def _voice_prompt(session="s-1", voice=True, ticket=None):
    msg = json.loads(_prompt(session, ticket))
    msg["params"]["voice"] = voice
    return json.dumps(msg)


def test_voice_flag_lowers_a_prompt_with_no_ticket_and_typed_puts_it_back():
    # The speaker gate off or refusing leaves no ticket; the app still says it was spoken.
    e = effort.Efforts(lambda: ("low", "medium"))
    assert _levels(e.before(_voice_prompt())) == [("s-1", "low")]
    assert _levels(e.before(_prompt())) == [("s-1", "medium")]


def test_voice_flag_must_be_true_not_merely_present():
    e = effort.Efforts(lambda: ("low", "medium"))
    for v in (False, None, "true", 1, "yes"):
        assert e.before(_voice_prompt(voice=v)) == []


def test_voice_flag_beats_a_missing_ticket_and_a_ticket_alone_still_counts():
    e = effort.Efforts(lambda: ("low", "medium"))
    assert _levels(e.before(_prompt(ticket="t"))) == [("s-1", "low")]
    assert e.before(_voice_prompt(ticket="t")) == []


def test_voice_flag_is_stripped_before_hermes():
    from aura_adapter import speaker
    out = json.loads(speaker.rewrite_prompt(_voice_prompt(ticket="t"), speaker.Speakers(key="k")))
    assert "voice" not in out["params"] and "speaker_ticket" not in out["params"]
    out = json.loads(speaker.rewrite_prompt(_voice_prompt(), speaker.Speakers(key="k")))
    assert out["params"] == {"session_id": "s-1", "text": "hi"}  # no ticket, voice alone
    # a prompt that never carried it passes byte for byte
    typed = _prompt()
    assert speaker.rewrite_prompt(typed, speaker.Speakers(key="k")) == typed


def test_other_frames_pass_without_a_frame():
    e = effort.Efforts(lambda: ("low", "medium"))
    for raw in ("not json", "[]", json.dumps({"method": "session.create"}),
                json.dumps({"method": "prompt.submit", "params": {"text": "x"}}),
                json.dumps({"method": "prompt.submit", "params": "x"})):
        assert e.before(raw) == []


def test_only_our_replies_are_recognised():
    ours = json.dumps({"jsonrpc": "2.0", "id": "adapter-effort-3", "result": {"value": "low"}})
    assert effort.is_ours(ours)
    assert not effort.is_ours(json.dumps({"id": 1, "result": {}}))
    assert not effort.is_ours(json.dumps({"method": "event", "params": {"text": "adapter-effort-"}}))
    assert not effort.is_ours("adapter-effort- not json")


def test_the_efforts_come_from_the_schema_defaults_and_the_override_file(tmp_path, monkeypatch):
    for name in ("VOICE_REASONING_EFFORT", "LLM_REASONING_EFFORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "HERMES_HOME", str(tmp_path))
    # Equal by default (T-0814): `low` puts a line at the top of the model's system prompt, so
    # a spoken turn at low after typed ones at medium shared no cached prefix and re-read the
    # whole conversation (22 s on a 30k-token prompt; bench/results/reasoning-effort.md).
    assert config.reasoning_efforts() == ("medium", "medium")
    path = config.overrides_path(tmp_path)
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"VOICE_REASONING_EFFORT": "none"}, fh)
    assert config.reasoning_efforts() == ("none", "medium")
