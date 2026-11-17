"""The fake Bot API answers what python-telegram-bot actually asks it.

A fake that a real client cannot talk to proves nothing, so the shapes here are the ones PTB
parses (``getMe`` into a ``User``, ``sendMessage`` into a ``Message``) rather than shapes that
merely satisfy this file.
"""
from __future__ import annotations

import json
import urllib.request

import pytest

from bench.fixtures import telegram_fake
from bench.fixtures.telegram_fake import BOT, FakeTelegram

TOKEN = "424242:not-a-real-token"


@pytest.fixture()
def fake():
    with FakeTelegram() as running:
        yield running


def call(fake: FakeTelegram, method: str, payload: dict | None = None) -> dict:
    """One Bot API call the way PTB makes it: POST /bot<token>/<method> with JSON."""
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(f"{fake.base_url}{TOKEN}/{method}", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def test_get_me_identifies_a_bot():
    with FakeTelegram() as fake:
        result = call(fake, "getMe")
        assert result["ok"] is True
        assert result["result"]["is_bot"] is True and result["result"]["id"] == BOT["id"]


def test_any_token_works(fake):
    """The whole point: a delivery proof must not need a real credential."""
    for token in ("1:a", "999999:zzz", ""):
        req = urllib.request.Request(f"{fake.base_url}{token}/getMe", data=b"{}",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert json.loads(resp.read())["ok"] is True


def test_send_message_is_recorded_and_answered_as_a_message(fake):
    result = call(fake, "sendMessage", {"chat_id": 77, "text": "GPU 0 is at 84.2 °C"})
    message = result["result"]
    assert message["chat"]["id"] == 77 and message["text"] == "GPU 0 is at 84.2 °C"
    assert isinstance(message["message_id"], int)

    assert [(e["chat_id"], e["text"]) for e in fake.sent] == [("77", "GPU 0 is at 84.2 °C")]


def test_form_encoded_bodies_are_understood_too(fake):
    """PTB sends JSON, but the Bot API also takes form bodies and so must the fake."""
    req = urllib.request.Request(f"{fake.base_url}{TOKEN}/sendMessage",
                                 data=b"chat_id=5&text=hello+there",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert json.loads(resp.read())["ok"] is True
    assert fake.sent[-1]["text"] == "hello there"


def test_unknown_methods_answer_ok(fake):
    """deleteWebhook, setMyCommands and friends are startup noise, not the subject."""
    for method in ("deleteWebhook", "setMyCommands", "sendChatAction"):
        assert call(fake, method)["ok"] is True


def test_get_updates_returns_injected_traffic_then_empties(fake):
    fake.state.inject(9, "hello jarvis")
    updates = call(fake, "getUpdates")["result"]
    assert len(updates) == 1 and updates[0]["message"]["text"] == "hello jarvis"
    assert updates[0]["message"]["chat"]["id"] == 9

    assert call(fake, "getUpdates")["result"] == []


def test_wait_for_message_finds_a_late_delivery(fake):
    import threading

    threading.Timer(0.3, lambda: call(fake, "sendMessage",
                                      {"chat_id": 1, "text": "the brief, at last"})).start()
    found = fake.wait_for_message("the brief", timeout=10)
    assert found is not None and found["text"] == "the brief, at last"


def test_wait_for_message_gives_up_rather_than_hanging(fake):
    assert fake.wait_for_message("never sent", timeout=0.5) is None


def test_reset_forgets_everything(fake):
    call(fake, "sendMessage", {"chat_id": 1, "text": "x"})
    urllib.request.urlopen(urllib.request.Request(f"http://{fake.host}:{fake.port}/__reset",
                                                  data=b"{}"), timeout=10).read()
    assert fake.sent == []


def test_the_sent_route_serves_what_the_property_returns(fake):
    call(fake, "sendMessage", {"chat_id": 3, "text": "over http"})
    with urllib.request.urlopen(f"http://{fake.host}:{fake.port}/__sent", timeout=10) as resp:
        over_http = json.loads(resp.read())
    assert [e["text"] for e in over_http] == [e["text"] for e in fake.sent] == ["over http"]


# --- MarkdownV2, the trap that cost a working proof ---------------------------------------
#
# Hermes sends a cron delivery as MarkdownV2, so the wire never carries the sentence a person
# reads. `wait_for_message("84.2")` found nothing while the alert was arriving correctly every
# minute, and the delivery proof reported a capability failure that did not exist.

#: A real cron delivery, captured verbatim from the fake during the phase 6 proof.
DELIVERED = ("Cronjob Response: proof\\-alert\n\\(job\\_id: f1e72d4688d6\\)\n"
             "\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\n\n"
             "GPU 0 temperature is 84\\.2 C, above the 80\\.0 C limit \\(delivery proof\\)\\.")


def test_the_captured_delivery_does_not_contain_its_own_temperature():
    """The premise, pinned: matching the raw text is what fails, not the delivery."""
    assert "84.2" not in DELIVERED
    assert "84\\.2" in DELIVERED


def test_unescaping_gives_back_the_sentence_a_person_reads():
    plain = telegram_fake.unescape_markdown_v2(DELIVERED)
    assert "GPU 0 temperature is 84.2 C, above the 80.0 C limit (delivery proof)." in plain
    assert "Cronjob Response: proof-alert" in plain
    assert "(job_id: f1e72d4688d6)" in plain


def test_a_backslash_that_is_not_an_escape_survives():
    """Only a backslash before a *reserved* character is an escape. A Windows path is not."""
    assert telegram_fake.unescape_markdown_v2(r"C:\Users\me") == r"C:\Users\me"
    assert telegram_fake.unescape_markdown_v2(r"a\\b") == r"a\\b"


def test_wait_for_message_finds_the_unescaped_form(fake):
    call(fake, "sendMessage", {"chat_id": 4242, "text": DELIVERED})
    assert fake.wait_for_message("84.2", timeout=5) is not None, "the reason this exists"
    assert fake.wait_for_message("proof-alert", timeout=5) is not None
    # And the raw form still matches, so no existing caller changes behaviour.
    assert fake.wait_for_message("84\\.2", timeout=5) is not None
    # The entry handed back is the message as sent, not a rewritten one: a test that wants to
    # assert on what Telegram received must be able to see it.
    assert fake.wait_for_message("84.2", timeout=5)["text"] == DELIVERED
