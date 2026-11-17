"""jarvis-mail against fake IMAP/SMTP servers.

There is no MockTransport for imaplib, so the fakes here stand in for
``imaplib.IMAP4_SSL`` and ``smtplib.SMTP`` themselves — the classes the plugin
names at call time. Nothing in ``tools.py`` exists for the sake of these tests:
production opens a connection per call through the same two names, which is what
makes them substitutable at all.
"""
from __future__ import annotations

import email
import email.policy
import imaplib
import json
import re
import smtplib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("jarvis-mail")
tools = plugin.tools

PASSWORD = "s3cr3t-mail-pw"

# A real RFC 2047 encoded-word: "=?UTF-8?B?w5xiZXJnYWJl?=" is "Übergabe".
MULTIPART = (
    b"Date: Tue, 01 Sep 2026 09:15:00 +0200\r\n"
    b"From: =?UTF-8?Q?Bj=C3=B6rn?= <bjorn@example.test>\r\n"
    b"To: owner@example.test\r\n"
    b"Subject: =?UTF-8?B?w5xiZXJnYWJl?=\r\n"
    b"Message-ID: <uebergabe-1@example.test>\r\n"
    b"MIME-Version: 1.0\r\n"
    b'Content-Type: multipart/alternative; boundary="BOUND"\r\n'
    b"\r\n"
    b"--BOUND\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"Content-Transfer-Encoding: base64\r\n"
    b"\r\n"
    # "Die Uebergabe ist am Freitag." base64-encoded, so a raw partial fetch of
    # this part would be gibberish unless the plugin decodes the MIME.
    b"RGllIFVlYmVyZ2FiZSBpc3QgYW0gRnJlaXRhZy4=\r\n"
    b"--BOUND\r\n"
    b"Content-Type: text/html; charset=utf-8\r\n"
    b"\r\n"
    b"<html><body><p>HTML VERSION SHOULD LOSE</p></body></html>\r\n"
    b"--BOUND--\r\n"
)

HTML_ONLY = (
    b"Date: Tue, 01 Sep 2026 10:00:00 +0200\r\n"
    b"From: shop@example.test\r\n"
    b"Subject: Receipt\r\n"
    b"MIME-Version: 1.0\r\n"
    b"Content-Type: text/html; charset=utf-8\r\n"
    b"\r\n"
    b"<html><head><style>p{color:red}</style></head><body>"
    b"<p>Order&nbsp;42</p><p>Thanks</p></body></html>\r\n"
)

LONG_BODY = (
    b"Date: Tue, 01 Sep 2026 11:00:00 +0200\r\n"
    b"From: list@example.test\r\n"
    b"Subject: Newsletter\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
) + (b"long line of newsletter filler\r\n" * 2000)

WITH_ATTACHMENT = (
    b"Date: Tue, 01 Sep 2026 12:00:00 +0200\r\n"
    b"From: scanner@example.test\r\n"
    b"Subject: Scan\r\n"
    b"MIME-Version: 1.0\r\n"
    b'Content-Type: multipart/mixed; boundary="MIX"\r\n'
    b"\r\n"
    b"--MIX\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"See attached.\r\n"
    b"--MIX\r\n"
    b'Content-Type: application/pdf; name="scan.pdf"\r\n'
    b"Content-Transfer-Encoding: base64\r\n"
    b'Content-Disposition: attachment; filename="scan.pdf"\r\n'
    b"\r\n"
    b"JVBERi0xLjQK\r\n"
    b"--MIX--\r\n"
)

MESSAGES = {"11": MULTIPART, "12": HTML_ONLY, "13": LONG_BODY, "14": WITH_ATTACHMENT}
FLAGS = {"11": rb"\Seen", "12": b"", "13": b"", "14": rb"\Seen \Answered"}

FOLDER_ROWS = [
    rb'(\HasNoChildren) "/" "INBOX"',
    rb'(\HasNoChildren) "/" "Archive"',
    # Modified UTF-7: the server's spelling of "Gelöscht".
    rb'(\HasNoChildren \Trash) "/" "Gel&APY-scht"',
    # Special-use \Drafts under a name that is not "Drafts", as Gmail and Dovecot spell it.
    rb'(\HasNoChildren \Drafts) "/" "Entw&APw-rfe"',
    rb'(\HasNoChildren \Sent) "/" "Gesendet"',
]


class FakeIMAP:
    """Enough of ``imaplib.IMAP4_SSL`` for the four tools, recording every call."""

    instances: list["FakeIMAP"] = []
    login_error: str | None = None

    def __init__(self, host, port=993):
        self.host, self.port = host, port
        self.calls: list[tuple] = []
        self.searches: list[list] = []
        self.credentials: tuple[str, str] | None = None
        self.selected: str | None = None
        self.capabilities = ("IMAP4REV1", "UIDPLUS")
        self.appended: list[tuple[str, str, bytes]] = []
        FakeIMAP.instances.append(self)

    def append(self, mailbox, flags, date_time, message):
        del date_time  # imaplib's signature, kept exactly
        self.calls.append(("append", mailbox, flags))
        self.appended.append((mailbox, flags, message))
        return ("OK", [b"APPEND completed"])

    def login(self, user, password):
        self.calls.append(("login", user))
        if FakeIMAP.login_error:
            raise imaplib.IMAP4.error(FakeIMAP.login_error)
        self.credentials = (user, password)
        return ("OK", [b"LOGIN completed"])

    def logout(self):
        self.calls.append(("logout",))
        return ("BYE", [b"logging out"])

    def list(self, directory='""', pattern="*"):
        # imaplib's signature, kept exactly: the fake stands in for IMAP4, and a
        # narrower one would accept calls the real client rejects.
        del directory, pattern
        self.calls.append(("list",))
        return ("OK", list(FOLDER_ROWS))

    def select(self, mailbox="INBOX", readonly=False):
        self.calls.append(("select", mailbox, readonly))
        self.selected = mailbox
        return ("OK", [b"4"])

    def uid(self, command, *args):
        cmd = command.upper()
        self.calls.append((cmd,) + tuple(a for a in args if a is not None))
        if cmd == "SEARCH":
            self.searches.append([a for a in args if a is not None])
            return ("OK", [b" ".join(u.encode() for u in sorted(MESSAGES, key=int))])
        if cmd == "FETCH":
            return self._fetch(str(args[0]), str(args[1]))
        raise imaplib.IMAP4.error(f"unsupported UID command {cmd}")

    def _fetch(self, uid, spec):
        raw = MESSAGES.get(uid)
        if raw is None:
            return ("OK", [None])
        header, _, body = raw.partition(b"\r\n\r\n")
        header += b"\r\n\r\n"
        flags = FLAGS.get(uid, b"")
        if "HEADER" in spec:
            meta = (b"1 (UID " + uid.encode() + b" FLAGS (" + flags
                    + b") BODY[HEADER] {%d}" % len(header))
            return ("OK", [(meta, header), b")"])
        if "TEXT" in spec:
            size = int(re.search(r"<0\.(\d+)>", spec).group(1))
            chunk = body[:size]
            return ("OK", [(b"1 (BODY[TEXT] {%d}" % len(chunk), chunk), b")"])
        meta = b"1 (UID " + uid.encode() + b" FLAGS (" + flags + b") BODY[] {%d}" % len(raw)
        return ("OK", [(meta, raw), b")"])


class FakeSMTP:
    """Enough of ``smtplib.SMTP`` to record the envelope and the TLS handshake."""

    instances: list["FakeSMTP"] = []
    refused: dict | None = None

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.calls: list = []
        self.sent: list = []
        FakeSMTP.instances.append(self)

    def ehlo(self, *args):
        self.calls.append("ehlo")

    def starttls(self, *args, **kwargs):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg, from_addr=None, to_addrs=None):
        self.sent.append((msg, from_addr, list(to_addrs or [])))
        return dict(FakeSMTP.refused or {})

    def quit(self):
        self.calls.append("quit")


@pytest.fixture
def mailbox(monkeypatch):
    monkeypatch.setenv("MAIL_IMAP_HOST", "imap.example.test")
    monkeypatch.setenv("MAIL_IMAP_PORT", "993")
    monkeypatch.setenv("MAIL_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("MAIL_SMTP_PORT", "587")
    monkeypatch.setenv("MAIL_USERNAME", "owner@example.test")
    monkeypatch.setenv("MAIL_PASSWORD", PASSWORD)
    monkeypatch.setenv("MAIL_FROM", "jarvis@example.test")
    FakeIMAP.instances = []
    FakeIMAP.login_error = None
    FakeSMTP.instances = []
    FakeSMTP.refused = None
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return FakeIMAP


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_encoded_words_are_decoded():
    assert tools.decode_header_value("=?UTF-8?B?w5xiZXJnYWJl?=") == "Übergabe"
    assert tools.decode_header_value("=?UTF-8?Q?Bj=C3=B6rn?= <b@x.test>") == "Björn <b@x.test>"
    assert tools.decode_header_value("plain subject") == "plain subject"
    assert tools.decode_header_value(None) == ""


def test_modified_utf7_mailbox_names():
    assert tools.decode_mailbox("Gel&APY-scht") == "Gelöscht"
    assert tools.decode_mailbox("R&AOk-pertoire") == "Répertoire"
    assert tools.decode_mailbox("Tom &- Jerry") == "Tom & Jerry"
    assert tools.decode_mailbox("INBOX") == "INBOX"


def test_imap_date_is_locale_independent():
    assert tools.imap_date("2026-09-01") == "01-Sep-2026"
    assert tools.imap_date("1-Sep-2026") == "1-Sep-2026"
    with pytest.raises(ValueError):
        tools.imap_date("last tuesday")


def test_search_criteria_from_friendly_arguments():
    assert tools.search_criteria() == ["ALL"]
    assert tools.search_criteria(unread=True) == ["UNSEEN"]
    assert tools.search_criteria(from_="a@b.test", subject="rent", since="2026-09-01") == [
        "FROM", '"a@b.test"', "SUBJECT", '"rent"', "SINCE", "01-Sep-2026"]
    with pytest.raises(ValueError):
        tools.search_criteria(subject="rent\r\nX-Evil: 1")


def test_search_by_what_the_mail_says(mailbox):
    """"The email about the plumber" names neither the sender nor the subject.

    Before this, mail_search could only match sender and subject, so a find by content
    searched the whole inbox's snippets or nothing. IMAP TEXT matches headers and body.
    """
    assert tools.search_criteria(text="plumber", since="2026-09-01") == [
        "TEXT", '"plumber"', "SINCE", "01-Sep-2026"]
    assert "text" in plugin.schemas.MAIL_SEARCH["parameters"]["properties"]
    out = json.loads(tools.handle_mail_search({"text": "Uebergabe"}))
    assert out["ok"] and mailbox.instances[0].searches == [["TEXT", '"Uebergabe"']]


# --------------------------------------------------------------------------- #
# Read-only tools
# --------------------------------------------------------------------------- #
def test_folders_are_listed_and_decoded(mailbox):
    out = json.loads(tools.handle_mail_folders({}))
    assert out["ok"] and out["count"] == 5
    names = [f["name"] for f in out["folders"]]
    assert names == ["INBOX", "Archive", "Gelöscht", "Entwürfe", "Gesendet"]
    # The server's own spelling is kept so a later SELECT can use it.
    assert out["folders"][2]["raw"] == "Gel&APY-scht"
    assert out["folders"][2]["flags"] == ["\\HasNoChildren", "\\Trash"]


def test_search_rows_decode_the_subject_and_cap_the_snippet(mailbox):
    out = json.loads(tools.handle_mail_search({"unread": True, "limit": 10}))
    assert out["ok"] and out["snippet_chars"] == tools.SNIPPET_CHARS
    rows = {r["uid"]: r for r in out["messages"]}
    assert out["messages"][0]["uid"] == "14", "newest UID first"
    assert rows["11"]["subject"] == "Übergabe"
    assert rows["11"]["from"] == "Björn <bjorn@example.test>"
    assert rows["11"]["date"].startswith("2026-09-01T09:15:00")
    assert rows["11"]["seen"] is True and rows["12"]["seen"] is False
    # The base64 text/plain part was decoded rather than handed over raw.
    assert rows["11"]["snippet"] == "Die Uebergabe ist am Freitag."
    assert rows["11"]["snippet_part"] == "text/plain"
    assert all(len(r["snippet"]) <= tools.SNIPPET_CHARS for r in out["messages"])
    # Reading must not mark mail read: EXAMINE, not SELECT.
    assert ("select", '"INBOX"', True) in mailbox.instances[0].calls
    assert mailbox.instances[0].searches == [["UNSEEN"]]


def test_search_snippet_never_carries_a_whole_body(mailbox):
    out = json.loads(tools.handle_mail_search({}))
    newsletter = [r for r in out["messages"] if r["uid"] == "13"][0]
    assert len(newsletter["snippet"]) == tools.SNIPPET_CHARS
    assert len(json.dumps(out)) < 4000, "a search must not return whole bodies"


def test_read_prefers_text_plain_over_text_html(mailbox):
    out = json.loads(tools.handle_mail_read({"uid": "11"}))
    assert out["ok"] and out["body_part"] == "text/plain"
    assert out["body"] == "Die Uebergabe ist am Freitag."
    assert "HTML VERSION SHOULD LOSE" not in out["body"]
    assert out["subject"] == "Übergabe" and out["truncated"] is False


def test_read_falls_back_to_stripped_html_and_says_so(mailbox):
    out = json.loads(tools.handle_mail_read({"uid": "12"}))
    assert out["body_part"] == "text/html (tags stripped)"
    assert "Order 42" in out["body"] and "Thanks" in out["body"]
    assert "<p>" not in out["body"] and "color:red" not in out["body"]


def test_read_truncation_names_the_cap(mailbox):
    out = json.loads(tools.handle_mail_read({"uid": "13"}))
    assert out["truncated"] is True and out["body_cap_chars"] == tools.BODY_CHARS
    assert f"body cut at {tools.BODY_CHARS} characters" in out["body"]
    assert f"({tools.BODY_CHARS // 1024} KB)" in out["body"]
    assert len(out["body"]) < tools.BODY_CHARS + 200


def test_read_lists_attachments_without_decoding_them(mailbox):
    out = json.loads(tools.handle_mail_read({"uid": "14"}))
    assert out["body"] == "See attached." and out["body_part"] == "text/plain"
    assert out["attachments"] == [{"filename": "scan.pdf", "content_type": "application/pdf",
                                  "bytes": 9}]


def test_read_refuses_a_uid_that_is_not_a_number(mailbox):
    out = json.loads(tools.handle_mail_read({"uid": "1 (BODY[]) UID STORE 1 +FLAGS"}))
    assert out["ok"] is False and "uid must be a number" in out["error"]


def test_a_missing_uid_is_an_answer_not_a_crash(mailbox):
    out = json.loads(tools.handle_mail_read({"uid": "999"}))
    assert out["ok"] is False and "999" in out["error"]


def test_a_non_ascii_folder_is_matched_back_to_the_server_spelling(mailbox):
    out = json.loads(tools.handle_mail_search({"folder": "Gelöscht", "limit": 1}))
    assert out["ok"] is True
    assert ("select", '"Gel&APY-scht"', True) in mailbox.instances[0].calls


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #
def test_send_builds_the_message_and_the_envelope(mailbox, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))  # no persona record: AURA, the default
    out = json.loads(tools.handle_mail_send({
        "to": ["a@example.test", "b@example.test"], "cc": "c@example.test",
        "subject": "Rent", "body": "Paid today."}))
    assert out["ok"] is True and out["refused"] == []
    assert out["to"] == ["a@example.test", "b@example.test"] and out["cc"] == ["c@example.test"]
    server = FakeSMTP.instances[0]
    assert server.host == "smtp.example.test" and server.port == 587
    assert "starttls" in server.calls and "quit" in server.calls
    msg, from_addr, to_addrs = server.sent[0]
    assert from_addr == "jarvis@example.test"
    assert to_addrs == ["a@example.test", "b@example.test", "c@example.test"]
    # Every message says who wrote it (the owner, 2026-10-08): the worn persona (T-0927).
    assert msg["Subject"] == "Rent" and msg.get_content().strip() == "Paid today.\n- sent by Aura, an AI assistant"


def test_send_refuses_a_recipient_that_would_inject_a_header(mailbox):
    out = json.loads(tools.handle_mail_send({
        "to": ["a@example.test\r\nBcc: everyone@example.test"], "subject": "x", "body": "y"}))
    assert out["ok"] is False and "newline" in out["error"]
    assert FakeSMTP.instances == [], "nothing may be opened before the address is checked"


def test_send_reports_a_refused_recipient(mailbox):
    FakeSMTP.refused = {"b@example.test": (550, b"no such user")}
    out = json.loads(tools.handle_mail_send({"to": ["a@example.test", "b@example.test"],
                                             "subject": "x", "body": "y"}))
    assert out["ok"] is False and out["refused"] == ["b@example.test"]


# --------------------------------------------------------------------------- #
# The approval gate and registration
# --------------------------------------------------------------------------- #
def test_register_gates_only_the_tool_that_leaves_the_house(mailbox):
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert set(ctx.tools) == {"mail_folders", "mail_search", "mail_read", "mail_draft", "mail_send"}
    assert all(entry["toolset"] == "mail" for entry in ctx.tools.values())
    assert "mail" in ctx.commands

    gate = ctx.hooks["pre_tool_call"]
    directive = gate("mail_send", {"to": ["neighbour@example.test"], "cc": ["boss@example.test"],
                                   "subject": "The keys"}, "")
    assert directive["action"] == "approve"
    assert "neighbour@example.test" in directive["message"]
    assert "boss@example.test" in directive["message"]
    assert "The keys" in directive["message"]
    # Per-recipient, so a remembered approval never becomes "mail anyone".
    assert directive["rule_key"].rsplit(":g", 1)[0] == "mail:send:boss@example.test,neighbour@example.test"

    # A draft is not sent: it waits in the owner's Drafts for them or for mail_send.
    for unsent in ("mail_folders", "mail_search", "mail_read", "mail_draft"):
        assert gate(unsent, {"folder": "INBOX", "to": ["neighbour@example.test"]}, "") is None


def test_send_is_not_offered_without_smtp(monkeypatch):
    monkeypatch.setenv("MAIL_IMAP_HOST", "imap.example.test")
    monkeypatch.setenv("MAIL_USERNAME", "owner@example.test")
    monkeypatch.delenv("MAIL_SMTP_HOST", raising=False)
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert ctx.available("mail_search") is True
    assert ctx.available("mail_send") is False


def test_the_slash_command_reports_the_unread_count(mailbox):
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    reply = ctx.commands["mail"]("2")
    assert reply.startswith("4 unread in INBOX, newest 2:")
    assert "Übergabe" not in reply, "the newest two are 14 and 13"
    assert "Scan" in reply


# --------------------------------------------------------------------------- #
# The password
# --------------------------------------------------------------------------- #
def test_the_password_is_in_no_payload_and_no_error(mailbox):
    payloads = [
        tools.handle_mail_folders({}),
        tools.handle_mail_search({"limit": 5}),
        tools.handle_mail_read({"uid": "11"}),
        tools.handle_mail_send({"to": ["a@example.test"], "subject": "x", "body": "y"}),
    ]
    for payload in payloads:
        assert PASSWORD not in payload

    # A server that quotes the failing LOGIN back at us is the leak path that
    # matters: the scrub has to cover error strings, not just results.
    FakeIMAP.login_error = f"LOGIN owner@example.test {PASSWORD} failed"
    out = json.loads(tools.handle_mail_search({}))
    assert out["ok"] is False and PASSWORD not in json.dumps(out)
    assert "***" in out["error"]
    assert PASSWORD not in plugin.tools.command("")


# --------------------------------------------------------------------------- #
# Drafts (T-0544): written to the owner's Drafts folder, never sent
# --------------------------------------------------------------------------- #
def test_a_draft_is_appended_to_the_special_use_drafts_folder_and_never_sent(mailbox):
    out = tools.draft(to="neighbour@example.test", subject="The keys", body="Under the mat.")
    assert out["ok"] and out["sent"] is False and out["folder"] == "Entwürfe"
    ((folder, flags, raw),) = FakeIMAP.instances[-1].appended
    assert folder == '"Entw&APw-rfe"' and flags == "(\\Draft)"
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    assert msg["To"] == "neighbour@example.test" and msg["Subject"] == "The keys"
    assert msg.get_content().strip() == "Under the mat."
    assert FakeSMTP.instances == []  # SMTP is never opened for a draft


def test_a_reply_draft_threads_on_the_message_it_answers(mailbox):
    out = tools.draft(to="bjorn@example.test", subject="", body="Yes, Tuesday.", reply_to="11")
    assert out["ok"]
    msg = email.message_from_bytes(FakeIMAP.instances[-1].appended[0][2], policy=email.policy.default)
    assert msg["In-Reply-To"] == "<uebergabe-1@example.test>"
    assert "<uebergabe-1@example.test>" in msg["References"]
    assert str(msg["Subject"]) == "Re: Übergabe"


def test_a_draft_refuses_a_recipient_that_would_inject_a_header(mailbox):
    with pytest.raises(ValueError):
        tools.draft(to="a@example.test\r\nBcc: x@example.test", subject="s", body="b")
    assert all(not i.appended for i in FakeIMAP.instances)


# --------------------------------------------------------------------------- #
# Sent mail is filed (T-0576): SMTP alone left nothing in the owner's Sent folder
# --------------------------------------------------------------------------- #
def test_a_sent_mail_is_filed_in_the_sent_folder_and_the_answer_says_so(mailbox):
    out = json.loads(tools.handle_mail_send({"to": ["a@example.test"], "subject": "Rent", "body": "Paid."}))
    assert out["ok"] is True and out["sent_folder"] == "Gesendet"
    ((folder, flags, raw),) = [a for i in FakeIMAP.instances for a in i.appended]
    assert folder == '"Gesendet"' and flags == "(\\Seen)"
    assert email.message_from_bytes(raw, policy=email.policy.default)["Subject"] == "Rent"


def test_a_send_that_cannot_be_filed_still_went_and_says_why(mailbox, monkeypatch):
    def refuse(self, *a):
        raise imaplib.IMAP4.error("APPEND failed: over quota")
    monkeypatch.setattr(FakeIMAP, "append", refuse)
    out = json.loads(tools.handle_mail_send({"to": ["a@example.test"], "subject": "Rent", "body": "Paid."}))
    assert out["ok"] is True and out["sent_folder"] is None
    assert "over quota" in out["sent_folder_error"]
