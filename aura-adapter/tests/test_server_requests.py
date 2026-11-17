"""The relay's translation of Hermes' server→client requests for the apps' event protocol."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aura_adapter.server_requests import HELLO_ID, RequestBridge


def _req(rid: str, method: str, **params) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": {"session_id": "s1", **params}})


def _respond(method: str, app_id: int, **params) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": app_id, "method": method, "params": {"session_id": "s1", **params}})


def test_server_request_capability_is_declared_and_its_answer_kept_from_the_app():
    hello = json.loads(RequestBridge.hello())
    assert hello["method"] == "client.capabilities" and hello["params"] == {"server_requests": True}
    reply = json.dumps({"jsonrpc": "2.0", "id": HELLO_ID, "result": {"server_requests": ["approval"]}})
    assert RequestBridge().from_upstream(reply) == ([], [])


def test_server_request_approval_becomes_the_event_and_the_answer_a_response():
    """2026-10-06: every approval was withdrawn with 'the attached client cannot answer approval
    requests', so a gated tool could not be approved from any surface."""
    bridge = RequestBridge()
    to_app, to_up = bridge.from_upstream(_req("srq-abc", "approval", request_id="q-1",
                                              command="settings_set hud.lock_layout", choices=["once", "deny"]))
    event = json.loads(to_app[0])["params"]
    assert to_up == [] and event["type"] == "approval.request" and event["session_id"] == "s1"
    assert event["payload"]["request_id"] == "q-1" and event["payload"]["choices"] == ["once", "deny"]
    to_up, to_app = bridge.from_app(_respond("approval.respond", 9, request_id="q-1", choice="once"))
    assert json.loads(to_up[0]) == {"jsonrpc": "2.0", "id": "srq-abc", "result": {"choice": "once"}}
    assert json.loads(to_app[0]) == {"jsonrpc": "2.0", "id": 9, "result": {"ok": True}}
    # Answered once: a second answer is not ours any more and goes through unchanged.
    assert bridge.from_app(_respond("approval.respond", 10, request_id="q-1", choice="once")) is None


def test_server_request_clarify_single_and_batch():
    bridge = RequestBridge()
    to_app, _ = bridge.from_upstream(_req("srq-one", "clarify", question="Which room?", choices=["den", "hall"]))
    payload = json.loads(to_app[0])["params"]["payload"]
    assert payload == {"question": "Which room?", "choices": ["den", "hall"], "options": ["den", "hall"],
                       "request_id": "srq-one"}
    to_up, _ = bridge.from_app(_respond("clarify.respond", 3, request_id="srq-one", answer="den"))
    assert json.loads(to_up[0]) == {"jsonrpc": "2.0", "id": "srq-one", "result": {"answer": "den"}}

    bridge.from_upstream(_req("srq-two", "clarify", questions=[{"qid": "a", "question": "?", "choices": []}]))
    to_up, to_app = bridge.from_app(_respond("clarify.respond", 4, request_id="srq-two", answer="yes", question_id="a"))
    lock = json.loads(to_up[0])
    assert lock["method"] == "clarify.lock" and lock["id"] == 4 and to_app == []
    assert lock["params"] == {"request_id": "srq-two", "question_id": "a", "answer": "yes"}


def test_a_clarify_batch_is_a_question_every_app_can_show_and_answer():
    """Hermes asks as a batch even for one question (every clarify in state.db, 2026-11-09). The
    phone, the car and the desktop console read `question` and `options` and answer with no
    question_id, so they showed an empty question and the batch resolved empty ("the line went
    quiet"). The first question rides flat on the event; an answer without an id locks the next
    question and asks the one after, under the same request id."""
    bridge = RequestBridge()
    to_app, _ = bridge.from_upstream(_req("srq-b", "clarify", session_id="s1", questions=[
        {"qid": "a", "question": "Which site?", "choices": ["news", "weather"]},
        {"qid": "b", "question": "Which page?", "choices": []}]))
    payload = json.loads(to_app[0])["params"]["payload"]
    assert payload["question"] == "Which site?" and payload["request_id"] == "srq-b"
    assert payload["choices"] == payload["options"] == ["news", "weather"]
    assert [q["qid"] for q in payload["questions"]] == ["a", "b"]  # the console's batch path

    to_up, to_app = bridge.from_app(_respond("clarify.respond", 5, request_id="srq-b", answer="news"))
    assert json.loads(to_up[0])["params"] == {"request_id": "srq-b", "question_id": "a", "answer": "news"}
    asked = json.loads(to_app[0])["params"]
    assert asked["type"] == "clarify.request" and asked["session_id"] == "s1"
    assert asked["payload"]["question"] == "Which page?" and asked["payload"]["request_id"] == "srq-b"

    to_up, to_app = bridge.from_app(_respond("clarify.respond", 6, request_id="srq-b", answer="home"))
    assert json.loads(to_up[0])["params"] == {"request_id": "srq-b", "question_id": "b", "answer": "home"}
    assert to_app == []
    # The last lock resolved the request: a further answer is not ours.
    assert bridge.from_app(_respond("clarify.respond", 7, request_id="srq-b", answer="x")) is None


def test_a_console_answer_by_id_keeps_the_batch_in_step():
    """The console answers each question by its id; a later answer without one takes the
    question the console has not answered."""
    bridge = RequestBridge()
    bridge.from_upstream(_req("srq-c", "clarify", session_id="s1", questions=[
        {"qid": "a", "question": "One?", "choices": []}, {"qid": "b", "question": "Two?", "choices": []}]))
    bridge.from_app(_respond("clarify.respond", 1, request_id="srq-c", answer="1", question_id="a"))
    to_up, _ = bridge.from_app(_respond("clarify.respond", 2, request_id="srq-c", answer="2"))
    assert json.loads(to_up[0])["params"]["question_id"] == "b"


def test_a_single_clarify_shows_its_choices_as_options_too():
    """Hermes sends `choices`; the phone and the desktop console read `options`."""
    bridge = RequestBridge()
    to_app, _ = bridge.from_upstream(_req("srq-s", "clarify", question="Which room?", choices=["den"]))
    assert json.loads(to_app[0])["params"]["payload"]["options"] == ["den"]


_CLARIFY = json.loads((Path(__file__).resolve().parents[2] / "tests" / "contracts" / "clarify.json").read_text(encoding="utf-8"))


def test_clarify_contract_has_a_single_and_a_batch_case():
    kinds = {c["kind"] for c in _CLARIFY["cases"]}
    assert {"single", "batch"} <= kinds, "tests/contracts/clarify.json must pin both Hermes shapes"


@pytest.mark.parametrize("case", _CLARIFY["cases"], ids=lambda c: c["name"])
def test_clarify_contract_adapter_side(case):
    """T-0802: both Hermes shapes, answered with and without a question_id, from the file the
    console's and the phone's suites also read (T-0798 shipped on single-question tests only)."""
    srq = _CLARIFY["srq"]
    bridge = RequestBridge()
    to_app, _ = bridge.from_upstream(_req(srq, "clarify", **case["upstream"]))
    event = json.loads(to_app[0])["params"]
    assert event["type"] == "clarify.request" and event["session_id"] == _CLARIFY["session_id"]
    for key, want in case["app_payload"].items():
        assert event["payload"].get(key) == want, key
    for i, step in enumerate(case["answers"]):
        to_up, to_app = bridge.from_app(_respond("clarify.respond", 100 + i, request_id=srq, **step["send"]))
        sent = json.loads(to_up[0])
        for key, want in step["hermes"].items():
            assert sent[key] == want, key
        if step["asks_next"] is None:
            assert not any("clarify.request" in f for f in to_app)
        else:
            payload = json.loads(to_app[0])["params"]["payload"]
            for key, want in step["asks_next"].items():
                assert payload[key] == want, key
    # Answered to the end: the request is resolved and a further answer is not ours.
    assert bridge.from_app(_respond("clarify.respond", 999, request_id=srq, answer="x")) is None


def test_server_request_cancel_and_kinds_no_app_can_answer():
    bridge = RequestBridge()
    bridge.from_upstream(_req("srq-x", "approval", request_id="q-9"))
    cancel = json.dumps({"jsonrpc": "2.0", "method": "event", "params": {
        "type": "request.cancel", "session_id": "s1", "payload": {"id": "srq-x", "method": "approval", "reason": "timeout"}}})
    to_app, _ = bridge.from_upstream(cancel)
    assert json.loads(to_app[0])["params"]["type"] == "approval.cancelled"
    assert json.loads(to_app[0])["params"]["payload"]["request_id"] == "q-9"
    # A vault prompt: refused at once rather than left to its deadline.
    to_app, to_up = bridge.from_upstream(_req("srq-v", "secret", prompt="master password"))
    assert to_app == [] and json.loads(to_up[0])["error"]["code"] == -32601
    # Ordinary frames pass through untouched.
    assert bridge.from_upstream('{"jsonrpc":"2.0","method":"event","params":{"type":"message.delta"}}') is None
    assert bridge.from_app('{"jsonrpc":"2.0","id":1,"method":"prompt.submit","params":{}}') is None


def test_a_question_still_open_when_the_app_reconnects_is_asked_again_and_its_answer_reaches_hermes():
    """The phone resumes its session on a new socket (T-0730's resumed run, 2026-11-09): Hermes
    returns the clarify it is still waiting on as ``open_requests`` in the resume result, but a
    new socket's bridge had never seen it -- the app was not asked again, and its answer, under a
    request id nothing knew, started no turn: every later turn got no reply in 240 s."""
    bridge = RequestBridge()
    resumed = json.dumps({"jsonrpc": "2.0", "id": 9, "result": {"session_id": "s2", "open_requests": [
        {"id": "srq-r", "method": "clarify", "params": {"session_id": "s2", "questions": [
            {"qid": "a", "question": "Which fruit?", "choices": ["mango", "peach"]},
            {"qid": "b", "question": "How many?", "choices": []}], "answers": {"a": "mango"}}},
        {"id": "srq-k", "method": "secret", "params": {"session_id": "s2", "prompt": "key"}}]}})
    to_app, to_up = bridge.from_upstream(resumed)
    assert json.loads(to_app[0]) == json.loads(resumed)  # the result itself, untouched
    asked = json.loads(to_app[1])["params"]
    assert asked["type"] == "clarify.request" and asked["session_id"] == "s2"
    # The question locked before the drop is not asked again.
    assert asked["payload"]["question"] == "How many?" and asked["payload"]["request_id"] == "srq-r"
    assert len(to_app) == 2  # a kind no app answers is refused, as when it first arrived
    assert json.loads(to_up[0]) == {"jsonrpc": "2.0", "id": "srq-k",
                                    "error": {"code": -32601, "message": "this client cannot answer secret"}}

    to_up, _ = bridge.from_app(_respond("clarify.respond", 10, request_id="srq-r", answer="two"))
    assert json.loads(to_up[0])["params"] == {"request_id": "srq-r", "question_id": "b", "answer": "two"}
    # A resume with nothing open is relayed as it is.
    assert bridge.from_upstream(json.dumps({"jsonrpc": "2.0", "id": 11, "result": {"session_id": "s3"}})) is None
