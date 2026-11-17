"""Tool schemas for the ``mail`` toolset.

The descriptions name the caps (300-character snippets, a 20 KB body) because
the model plans around what a tool returns: told that a search row is a snippet,
it calls ``mail_read`` for the one message it needs instead of widening the
search until the whole mailbox is in context.
"""
from __future__ import annotations

from .tools import BODY_CHARS, SNIPPET_CHARS

MAIL_FOLDERS = {
    "name": "mail_folders",
    "description": "Which mailboxes or folders my mail account has: list the mailboxes (IMAP "
                   "folders) on the mail account, with their flags. Read-only.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

MAIL_SEARCH = {
    "name": "mail_search",
    "description": (
        "How many unread, what's in my inbox, find mail from X or about Y: search a mail "
        "folder and return envelope rows: uid, date, from, subject, whether it has been read,"
        f" and a snippet of at most {SNIPPET_CHARS} characters. Read-only, and it does "
        "not mark anything read. The snippet is NOT the message — call mail_read with a row's uid "
        "for the body. `matched` counts every hit; only the newest `limit` rows are returned."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "folder": {"type": "string", "description": "Mailbox to search. Default INBOX."},
            "unread": {"type": "boolean", "description": "Only unread (IMAP UNSEEN) messages."},
            "from_": {"type": "string", "description": "Substring of the sender address or name."},
            "subject": {"type": "string", "description": "Substring of the subject."},
            "text": {"type": "string",
                     "description": "One word or one exact phrase the mail contains, in its "
                                    "body or headers: 'the email about the plumber' is text: "
                                    "plumber. Two words are one phrase, not either word: "
                                    "search each synonym in its own call. ASCII only."},
            "since": {"type": "string",
                      "description": "Only mail on or after this date, YYYY-MM-DD. Day-granular."},
            "limit": {"type": "integer",
                      "description": "Rows to return, newest first. Default 20, max 100."},
        },
        "required": [],
    },
}

MAIL_READ = {
    "name": "mail_read",
    "description": (
        "Read one message by its uid (from a mail_search row): headers, the text/plain body when "
        f"the message has one — otherwise text/html with the tags stripped — capped at "
        f"{BODY_CHARS} "
        "characters, and a list of attachment names and sizes. `body_part` says which part was "
        "used. Read-only: the message is not marked read."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "uid": {"type": "string", "description": "The uid from a mail_search row (digits)."},
            "folder": {"type": "string",
                       "description": "Folder the uid belongs to. Default INBOX."},
        },
        "required": ["uid"],
    },
}

MAIL_DRAFT = {
    "name": "mail_draft",
    "description": (
        "Write a plain-text email into the owner's Drafts folder WITHOUT sending it: the way to "
        "prepare a reply or a message to review. Nothing leaves the mailbox, so it needs no "
        "approval; say it is in Drafts. Give `reply_to` (a uid from mail_search, with its "
        "`folder`) to thread a reply; the subject then defaults to 'Re: …'. Sending is mail_send."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "to": {"type": "array", "items": {"type": "string"}, "description": "Recipient addresses."},
            "subject": {"type": "string", "description": "Subject line (optional for a reply)."},
            "body": {"type": "string", "description": "Plain-text body."},
            "cc": {"type": "array", "items": {"type": "string"},
                   "description": "Carbon-copy addresses (optional)."},
            "reply_to": {"type": "string", "description": "The uid of the message this answers (optional)."},
            "folder": {"type": "string", "description": "The folder reply_to is in. Default INBOX."},
        },
        "required": ["to", "body"],
    },
}

MAIL_SEND = {
    "name": "mail_send",
    "description": (
        "Send a plain-text email from the household mailbox. This leaves the house, so it needs "
        "approval and the owner is shown the recipients and the subject first. Plain text only: "
        "no attachments and no HTML."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "to": {"type": "array", "items": {"type": "string"},
                   "description": "Recipient addresses."},
            "subject": {"type": "string", "description": "Subject line."},
            "body": {"type": "string", "description": "Plain-text body."},
            "cc": {"type": "array", "items": {"type": "string"},
                   "description": "Carbon-copy addresses (optional)."},
        },
        "required": ["to", "subject", "body"],
    },
}
