"""M6, in the gate's own words: *frames are throttled and redacted, verified by
inspecting the event stream for a password field never leaving the container.*

The event stream is inspected here literally: every frame the emitter sends is
recorded as the JSON text that would go on the socket, and the password's value
is searched for in all of it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("aura-activity")
activity = plugin.activity

SECRET = "hunter2-Correct-Horse-9"
LOGIN_PAGE = "\n".join([
    "- heading \"Sign in\"",
    "- textbox \"Email\" [ref=@e2]",
    f"- textbox \"Password\" [ref=@e3] value=\"{SECRET}\"",
    "- button \"Sign in\" [ref=@e4]",
])


class Wire:
    """Every event as the socket would carry it: (session, event, JSON text)."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str, str]] = []

    def __call__(self, session_id: str, event: str, payload: dict) -> bool:
        self.lines.append((session_id, event, json.dumps(payload, ensure_ascii=False)))
        return True

    def text(self) -> str:
        return "\n".join(line for _, _, line in self.lines)


@pytest.fixture
def wire(monkeypatch):
    monkeypatch.setenv("ACTIVITY_ENABLED", "true")
    monkeypatch.setattr(activity, "SEND", Wire())
    monkeypatch.setattr(activity, "THROTTLE_MS", 0)
    activity.reset()
    yield activity.SEND
    activity.reset()


def _navigate(sid: str, page: str = LOGIN_PAGE) -> None:
    result = json.dumps({"status": "success", "url": "https://example.com/login",
                         "title": "Sign in", "snapshot": page})
    activity.pre_tool_call("browser_navigate", {"url": "https://example.com/login"}, session_id=sid)
    activity.post_tool_call(tool_name="browser_navigate", args={"url": "https://example.com/login"},
                            result=result, session_id=sid, status="ok")


def test_a_password_field_never_leaves_the_container(wire):
    """A frame of a page with a password field's value, then the value typed into
    that field: the value appears in no emitted frame.

    What IS covered: the page text the frame carries (the snapshot line that
    named the field, with whatever the format put after the name); the typed
    text in the step's action; the typed text wherever it recurs later in the
    activity (a result echoing it, an error message, the page after submit),
    including the start's intent and the end. What is NOT covered: the pixels of
    a picture. A browser draws a password input as bullets, so the value is not
    in the picture, and this test carries no picture — the pixel path masks no
    region because the browser tool's screenshot carries no element boxes.
    """
    sid = "s-login"
    _navigate(sid)
    activity.post_tool_call(tool_name="browser_type", args={"ref": "@e3", "text": SECRET},
                            result=f"Typed '{SECRET}' into @e3", session_id=sid, status="ok")
    # A later result that quotes the value back (a failed submit, a re-snapshot).
    activity.post_tool_call(tool_name="browser_click", args={"ref": "@e4"},
                            result=json.dumps({"status": "error", "error": f"login rejected for {SECRET}"}),
                            session_id=sid, status="error", error_message=f"login rejected for {SECRET}")
    activity._idle(sid, activity._LIVE[sid])  # the idle end, without waiting for it

    events = [e for _, e, _ in wire.lines]
    assert events[0] == "activity.start" and events[-1] == "activity.end"
    assert "activity.frame" in events and events.count("activity.step") == 3
    assert SECRET not in wire.text()
    # The trail still says what happened, by the field's name, with the value masked.
    steps = [json.loads(line) for _, e, line in wire.lines if e == "activity.step"]
    assert steps[1]["action"] == f"type into Password: {activity.MASK}"
    frame = next(json.loads(line) for _, e, line in wire.lines if e == "activity.frame")
    assert '- textbox "Password" [ref=@e3] [redacted]' in frame["text"]
    assert '- textbox "Email" [ref=@e2]' in frame["text"]  # an ordinary field is untouched


def test_text_typed_into_an_ordinary_field_is_shown(wire):
    sid = "s-search"
    _navigate(sid, "- searchbox \"Search\" [ref=@e1]\n- button \"Go\" [ref=@e2]")
    activity.post_tool_call(tool_name="browser_type", args={"ref": "@e1", "text": "cheap flights"},
                            result="ok", session_id=sid, status="ok")
    step = json.loads(wire.lines[-1][2])
    assert step["action"] == "type into Search: cheap flights"


def test_a_field_whose_label_was_never_seen_is_treated_as_sensitive(wire):
    # The snapshot went by before the plugin loaded: the value must not leave on
    # the guess that an unseen field is a search box.
    sid = "s-blind"
    activity.post_tool_call(tool_name="browser_type", args={"ref": "@e9", "text": SECRET},
                            result="ok", session_id=sid, status="ok")
    assert SECRET not in wire.text()
    assert json.loads(wire.lines[-1][2])["action"] == f"type into @e9: {activity.MASK}"


def test_the_redaction_layer_masks_a_credential_shape_in_page_text(wire):
    # Not a password field: a token printed on the page, which the audit log's
    # redactor (Hermes' engine or its local fallback) knows the shape of.
    sid = "s-token"
    _navigate(sid, "- text \"api_key: sk-" + "abcdefghijklmnopqrstuvwxyz123456\"")
    frame = next(json.loads(line) for _, e, line in wire.lines if e == "activity.frame")
    assert "sk-" + "abcdefghijklmnopqrstuvwxyz123456" not in frame["text"]


def test_page_text_redaction_keeps_role_name_and_ref():
    text = "- textbox \"Password\" [ref=@e3] value=\"x\" placeholder=\"y\"\nPassword: hunter\n- link \"Home\" [ref=@e1]"
    out = activity.redact_page_text(text)
    assert out.splitlines() == [
        f"- textbox \"Password\" [ref=@e3] {activity.MASK}",
        f"Password {activity.MASK}",
        "- link \"Home\" [ref=@e1]",
    ]
