"""aura-mail — the household mailbox over IMAP and SMTP.

Registers the ``mail`` toolset (``mail_folders`` / ``mail_search`` /
``mail_read`` read-only, ``mail_draft`` writing an unsent draft to the owner's Drafts,
``mail_send`` behind the approval gate), the ``/mail``
slash command, and the ``pre_tool_call`` hook that gates the one tool whose call
leaves the house. The protocol work and the caps live in :mod:`tools`.

Reading is gated on IMAP being configured and sending on SMTP being configured
separately: a household that only wants JARVIS to *read* mail sets
``MAIL_IMAP_HOST`` and leaves ``MAIL_SMTP_HOST`` empty, and ``mail_send`` is
then never offered to the model at all.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from aura_schema import from_env

# `_shared/` is a sibling dir, reachable only via sys.path: see _shared/_approval_hook.py.
_PLUGINS_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGINS_ROOT not in sys.path:
    sys.path.append(_PLUGINS_ROOT)

from _shared._approval_hook import approval_gate, guarded_command  # noqa: E402
from . import schemas, tools  # noqa: E402

logger = logging.getLogger(__name__)

TOOLSET = "mail"


def _can_read() -> bool:
    cfg = from_env()
    return bool(cfg.mail_imap_host and cfg.mail_username)


def _can_send() -> bool:
    cfg = from_env()
    return bool(cfg.mail_smtp_host and (cfg.mail_from or cfg.mail_username))


def register(ctx) -> None:
    from_env()  # fail fast: a mistyped MAIL_* value names itself at load
    for name, schema, handler, check_fn, emoji in (
        ("mail_folders", schemas.MAIL_FOLDERS, tools.handle_mail_folders, _can_read, "📬"),
        ("mail_search", schemas.MAIL_SEARCH, tools.handle_mail_search, _can_read, "📬"),
        ("mail_read", schemas.MAIL_READ, tools.handle_mail_read, _can_read, "📖"),
        ("mail_draft", schemas.MAIL_DRAFT, tools.handle_mail_draft, _can_read, "📝"),
        ("mail_send", schemas.MAIL_SEND, tools.handle_mail_send, _can_send, "✉️"),
    ):
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler,
                          check_fn=check_fn, emoji=emoji)

    ctx.register_hook("pre_tool_call", approval_gate(tools.approval_for))
    ctx.register_command(
        "mail",
        handler=guarded_command(tools.command, "mail", logger=logger),
        description="Unread count and the newest unread subjects in INBOX",
        args_hint="[count]",
    )
