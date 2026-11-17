"""What the JARVIS command plugins share on their way into Hermes.

``jarvis-goals``, ``jarvis-hud``, ``jarvis-integrations`` and ``jarvis-settings``
each register a slash command and (two of them) a ``pre_tool_call`` approval
hook, and each carried its own copy of the same three pieces of plumbing:

* :func:`split_session` — Hermes hands a plugin command only the raw argument
  string, never the session, so the apps prefix the argument with the session
  id ``session.create`` returned (``@<sid> <rest>``) and the plugin peels it off.
  Four copies of the regex is four chances for one to accept a different id
  alphabet than the console sends.
* :func:`guarded_command` — a command handler that raises would take the
  gateway's command dispatcher down with it, so every handler is wrapped to
  turn an exception into a reply and a logged traceback.
* :func:`approval_gate` — the ``pre_tool_call`` hook shape: Hermes calls it as
  ``hook(tool_name, args, task_id, **extra)`` and the plugin only wants
  ``(tool_name, args)`` to decide whether the call opens the approval door.

``_shared/`` is a sibling of every plugin directory and has no ``plugin.yaml``,
so Hermes never loads it as a plugin; each plugin's ``__init__`` puts the
plugins root on ``sys.path`` and imports this as ``_shared._approval_hook``
(the loader gives a plugin no parent package to reach a sibling through).
``hermes/plugins/tests/test_plugin_loader.py`` loads every consumer the way
Hermes does, with that root deliberately absent from ``sys.path`` beforehand,
so a plugin that forgot the shim fails there rather than in the gateway.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
from typing import Any, Callable, Optional

from . import _approval_event, _audit, _breaker, _grants, _matrix, _messages, _plan, _speaker, _taint

#: ``@<sid> <rest>`` — the id alphabet is what the TUI gateway issues.
SID_RE = re.compile(r"^@(?P<sid>[A-Za-z0-9_.:-]+)\s*(?P<rest>.*)$", re.S)


def split_session(raw: str) -> tuple[Optional[str], str]:
    """``"@abc123 get goals.max_turns"`` -> ``("abc123", "get goals.max_turns")``.

    Without the prefix the whole string is the rest and the session is None; the
    caller decides whether a missing session is usable (``/hud`` cannot route a
    panel without one, ``/jsettings list`` could but refuses for consistency).
    """
    raw = (raw or "").strip()
    m = SID_RE.match(raw)
    if not m:
        return None, raw
    return m.group("sid"), m.group("rest").strip()


def guarded_command(
    handler: Callable[[str], str],
    label: str,
    *,
    logger: logging.Logger,
    log_label: str | None = None,
) -> Callable[[str], str]:
    """Wrap a slash-command handler so an exception becomes a reply.

    The reply is ``"<label> error: <exc>"`` — the string the plugin tests pin —
    and the traceback goes to ``logger`` under ``log_label`` (defaults to
    ``label``). Never let a command crash the gateway: the dispatcher runs the
    handler inline and does not catch for it.
    """

    def _cmd(raw_args: str) -> str:
        try:
            return handler(raw_args)
        except Exception as exc:
            logger.exception("%s command failed", log_label or label)
            return f"{label} error: {exc}"

    return _cmd


def approval_gate(
    build: Callable[[str, dict], Optional[dict]],
) -> Callable[..., Optional[dict]]:
    """A ``pre_tool_call`` hook from a ``(tool_name, args) -> directive | None``.

    ``build`` returns the approval directive for a gated tool or None to let
    the call run freely; the extra positional/keyword arguments Hermes passes
    (``task_id`` and whatever a later version adds) are dropped here so the
    plugin's decision function keeps a stable two-argument signature. A
    non-dict ``args`` is handed on as ``{}`` — the decision functions index it.
    """

    def _gate(tool_name: str, args: Any, task_id: str = "", **kwargs: Any) -> Optional[dict]:
        del task_id
        shaped = args if isinstance(args, dict) else {}
        directive = build(tool_name, shaped)
        if directive and directive.get("action") == "approve":
            # A sensitive call (T-0945) is the matrix gate's alone. A second, ordinary ask
            # from here would be answered by a tap: before the fingerprint if this gate ran
            # first (Hermes takes the first directive), after it if it ran second.
            try:
                if _matrix.sensitive(tool_name, shaped):
                    return None
            except Exception:  # the matrix gate asks for it either way
                logging.getLogger(__name__).exception("sensitive check raised for %s", tool_name)
                return None
        if directive and directive.get("action") == "approve" and directive.get("rule_key"):
            # A plugin's own key gets the token too, or its "always" would never run out (T-0570).
            directive = {**directive, "rule_key": _grants.key_for(str(directive["rule_key"]))}
        # A step of a plan the owner approved in one card (_plan.py) is not asked again here.
        # The matrix gate beside this one still decides it: a guest, the kill switch and a
        # DENY row are refused there whatever this says.
        if directive and directive.get("action") == "approve" and _plan.consume(
                str(kwargs.get("session_id") or ""), tool_name, shaped,
                str(kwargs.get("tool_call_id") or "")) is not None:
            return None
        return directive

    return _gate


#: How much of the arguments the approval prompt carries, and how much of any
#: one of them. Sized to stay one readable line on the phone's consent sheet,
#: which is the narrowest surface an approval is answered on.
ARGS_IN_PROMPT: int = 160
_VALUE_IN_PROMPT: int = 72


def summarise_args(args: Any, *, limit: int = ARGS_IN_PROMPT) -> str:
    """A one-line, redacted rendering of a gated call's arguments.

    The failure this prevents is the owner approving blind. Hermes'
    ``request_tool_approval(tool_name, reason, rule_key=...)`` has **no slot for
    the call's arguments** — the prompt it builds carries the tool's name and
    the plugin's sentence and nothing else. So "ha_call_service acts on the
    house" was the entire question asked before unlocking the front door, and it
    is the same question that would be asked before turning on a lamp. The only
    channel to the human is the reason string, so the arguments go in it.

    Redaction is the audit log's, not a second engine: a credential must not be
    masked in the file that gets backed up and then printed in clear on a
    consent screen. Values are clipped individually before the line is clipped,
    so a long ``content`` cannot crowd out the ``path`` it is being written to.

    Not claimed: this is a summary, not the arguments. A call whose meaning
    lives past 160 characters is one the owner should answer from the audit log
    rather than the prompt.
    """
    if not isinstance(args, dict) or not args:
        return ""
    parts: list[str] = []
    for key, value in _audit.redact(args).items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        text = value if isinstance(value, str) else json.dumps(value, default=str,
                                                               ensure_ascii=False)
        # A multi-line script is one line here, or it breaks the prompt's layout.
        text = " ".join(text.split())
        if len(text) > _VALUE_IN_PROMPT:
            text = text[:_VALUE_IN_PROMPT - 1] + "…"
        parts.append(f"{key}: {text}")
    joined = ", ".join(parts)
    if len(joined) > limit:
        joined = joined[:limit - 1] + "…"
    return joined


#: Tool name -> what that call would change, so its approval can show the value now and the
#: value asked for (T-1317). The plugin that owns the tool fills this at ``register``: only it
#: can read the value now, and the gate must not import a plugin. A describer returns None or
#: ``{"key", "before", "after", "known", "secret"}``: ``known`` false writes no lines (nothing
#: is known to show), ``secret`` true keeps the call's ``value`` out of the text and the audit.
#: It decides nothing: not the verdict, not who may answer, not the rule key. It is called as
#: ``describe(tool_name, args, session)``: the session is the chat the call came from, for a
#: value that lives per chat (the model a chat uses, T-1329). It never raises and makes no long
#: call: the approval waits on it. Tools with no value to replace are listed, with the reason,
#: under ``no_prior_value`` in ``tests/contracts/console-actions.json``.
CHANGE_DESCRIBERS: dict[str, Callable[[str, dict, str], Optional[dict]]] = {}


def _change(tool_name: str, shaped: dict, session: str, log: logging.Logger) -> Optional[dict]:
    describe = CHANGE_DESCRIBERS.get(tool_name)
    if describe is None:
        return None
    try:
        return describe(tool_name, shaped, session)
    except Exception:
        # Describing never vetoes and never waves through. What it could not read might be a
        # secret, so the ask goes on with no value shown rather than with the raw one.
        log.exception("change describer raised for %s", tool_name)
        return {"secret": True}


def matrix_gate(
    *,
    logger: logging.Logger | None = None,
) -> Callable[..., Optional[dict]]:
    """The one ``pre_tool_call`` hook: the approval matrix, plus the audit line.

    Registered once, by ``jarvis-guard``, and it governs the whole surface —
    Hermes invokes every loaded plugin's ``pre_tool_call`` for every tool call,
    built-in and plugin and MCP alike, so a hook that does not filter by tool
    name sees all of them. That is what closes the hole the JCB baseline found:
    the eight plugin gates could only ever see their own eight plugins' tools,
    and the two calls that deleted a folder and unlocked a door were Hermes
    built-ins that no gate was looking at.

    The three verdicts map onto Hermes' three directive shapes:

    * ``free``  -> ``None``: the call proceeds, and nothing is written. A log
      line per ``read_file`` would bury the lines that matter.
    * ``ask``   -> ``{"action": "approve", ...}``: escalates to the human
      approval gate — the ``[o]nce/[s]ession/[a]lways/[d]eny`` door on the CLI,
      an ``approval.request`` event on the API server and the HUD.
    * ``deny``  -> ``{"action": "block", "message": ...}``: the message becomes
      the tool result the model reads, so it learns *why* and can say so,
      rather than retrying the same call until the loop guard stops it.

    The audit line is written *before* the directive is returned, so a call that
    was asked about is in the log whether or not the human ever answers. A
    logging failure never changes the verdict.
    """
    log = logger or logging.getLogger(__name__)

    def _gate(tool_name: str, args: Any, task_id: str = "", **kwargs: Any) -> Optional[dict]:
        shaped = args if isinstance(args, dict) else {}
        # A service that just failed three times in a row is refused before the matrix
        # is asked: an ASK row would otherwise put a call to a dead host to the owner.
        held = _breaker.held(tool_name)
        if held:
            return {"action": "block", "message": held}
        try:
            decision = _matrix.decide(tool_name, shaped)
        except Exception:
            # A gate that raises is a gate Hermes fails closed on, which would
            # stop every tool call in the process. Failing to ASK instead keeps
            # the assistant usable and still keeps a human in the loop.
            log.exception("approval matrix raised for %s — falling back to ask", tool_name)
            decision = _matrix.UNKNOWN
        session = str(kwargs.get("session_id") or "")
        # A sensitive row (T-0945) is asked every time, with the fingerprint: no level lifts
        # it, no plan covers it and (below) no answer to it is remembered.
        try:
            sensitive = decision.verdict != _matrix.DENY and _matrix.sensitive(tool_name, shaped)
        except Exception:  # a check that cannot say is not a reason for the lesser ask
            log.exception("sensitive check raised for %s", tool_name)
            sensitive = decision.verdict != _matrix.DENY
        if sensitive and decision.free:
            decision = _matrix.Decision(_matrix.ASK, decision.reason, decision.rule_key)
        if decision.lifted:
            read = _taint.read_outside(session)
            if read:
                cap = decision.rule_key.split(":")[1]
                decision = _matrix.Decision(
                    _matrix.ASK, f"{cap} is set to Allowed, but this turn read outside text "
                    f"({', '.join(read)}): {decision.reason}", decision.rule_key)
        one_time = False
        if decision.free and _matrix.asks_after_outside_text(tool_name, shaped):
            # Free because the owner asked for it. In a turn that has read someone else's words,
            # those words may be what asked, and may be what the call carries (an alert, or a
            # sentence stored in memory to be read back as the owner's own: T-1490).
            read = _taint.read_outside(session)
            if read:
                decision = _matrix.Decision(
                    _matrix.ASK, f"this turn read outside text ({', '.join(read)}): {decision.reason}",
                    decision.rule_key)
                # Asked each time: a remembered yes, or a plan, would be the next one unseen.
                one_time = True
        shell: tuple[str, str] = ("", "")
        if (decision.verdict != _matrix.DENY
                and _matrix.HERMES_RENAMED.get(tool_name, tool_name) in _matrix.SHELLS):
            # A shell can send what the turn knows anywhere, in more shapes than a rule could
            # list (T-1493). After a read from beyond the house each command asks, whatever it
            # looks like; after a read of the owner's own files only, the fetch rule below
            # still holds a written address. Before that rule, so the card is the whole command.
            # A row that already asks is asked each time too: a yes remembered for writing to a
            # process would otherwise carry any text for a shell that is waiting on its input.
            beyond = _taint.read_beyond_house(session)
            if beyond:
                try:
                    shell = _matrix.shell_command(tool_name, shaped)
                except Exception:  # a command the gate cannot read is not one to run unasked
                    log.exception("shell command raised for %s", tool_name)
                    shell = ("a command the gate could not read", "")
                if shell[0]:
                    if decision.free:
                        decision = _matrix.Decision(
                            _matrix.ASK, "runs a shell command after reading text from beyond the "
                            f"house ({', '.join(beyond)})", decision.rule_key)
                    # Asked each time: the next command is another one.
                    one_time = True
        composed: list[str] = []
        if decision.free and _matrix.HERMES_RENAMED.get(tool_name, tool_name) in _matrix.FETCHERS:
            # A fetch is a read, and the address is what it sends. In a turn that has read
            # someone else's words, an address the session was never given is the way out for
            # what the turn knows (T-1492): a link it was given is followed, anything else asks.
            read = _taint.read_outside(session)
            if read:
                try:
                    composed = [address for address, forms in _matrix.fetch_targets(tool_name, shaped)
                                if not _taint.was_given(session, forms)]
                except Exception:  # an address the gate cannot read is not one it was given
                    log.exception("fetch targets raised for %s", tool_name)
                    composed = ["an address the gate could not read"]
                if composed:
                    decision = _matrix.Decision(
                        _matrix.ASK, "reaches an address this turn was not given, after reading "
                        f"outside text ({', '.join(read)})", decision.rule_key)
                    # Asked each time, like the block above: the next address is another one.
                    one_time = True
        typed: list[str] = []
        if decision.free and _matrix.HERMES_RENAMED.get(tool_name, tool_name) in _matrix.TYPERS:
            # Whoever runs a page reads a field as it is typed, so text put into one is sent
            # (T-1494). The owner's own words of the session are typed with no ask; anything
            # else may be what the page asked for, out of what the turn knows. Not tied to
            # the turn's mark: the page being typed into is itself someone else's words.
            try:
                typed = [text for text in _matrix.typed_text(tool_name, shaped)
                         if not _taint.was_said(session, text)]
            except Exception:  # text the gate cannot read is not text the owner said
                log.exception("typed text raised for %s", tool_name)
                typed = ["text the gate could not read"]
            if typed:
                decision = _matrix.Decision(
                    _matrix.ASK, "types text the owner did not say into the page the browser has open",
                    decision.rule_key)
                # Asked each time: the next text is another one, and so may the page be.
                one_time = True
        if decision.free:
            # Free for the owner is not free for a guest: off GUEST_FREE, a free row is the
            # owner's own things (mail, facts, files, the shell), and a guest's turn ran
            # every one of them until T-0445. On the list, no lookup is made at all.
            if _matrix.guest_may(tool_name):
                return None
            who = _speaker.speaker_of(session)
            if who["level"] != "guest":
                return None
            try:
                _audit.append(_audit.KIND_TOOL_CALL, actor="model", session=session,
                              task=str(task_id or ""), tool=tool_name, args=shaped,
                              verdict=_matrix.DENY,
                              reason=f"a guest ({who['label']}) is not read or given the owner's own",
                              rule_key="guest:owners-own", outcome="gated")
            except Exception:  # the audit is a record, never a veto
                log.exception("audit append failed for %s", tool_name)
            return {"action": "block",
                    "message": f"Refused: {tool_name} reaches the owner's own things, and the "
                               f"person speaking ({who['label']}) is a guest here. Tell them it "
                               f"is the owner's to ask, and say nothing of what it holds or "
                               f"whether it exists."}
        # Whose turn this is decides whether a gated row may even be asked
        # about: a guest's voice (the adapter's record for the session) turns
        # ASK into DENY. Owner and member are the matrix as written -- the
        # owner's HUD answering the ASK is the member's second factor.
        who = _speaker.speaker_of(session)
        guest = who["level"] == "guest"
        # A step of a plan the owner approved in one card (_plan.py). Only an ASK from an owner
        # or member turn: a DENY row, the kill switch (decide() already made it DENY) and a
        # guest are never a plan's to wave through.
        if not guest and not sensitive and not one_time and decision.verdict == _matrix.ASK:
            covered = _plan.consume(session, tool_name, shaped, str(kwargs.get("tool_call_id") or ""))
            if covered is not None:
                try:
                    _audit.append(_audit.KIND_TOOL_CALL, actor="model", session=session,
                                  task=str(task_id or ""), tool=tool_name, args=shaped,
                                  verdict=decision.verdict, reason=f"a step of an approved plan ({covered[:120]})",
                                  rule_key=decision.rule_key, outcome="approved-by-plan")
                except Exception:  # the audit is a record, never a veto
                    log.exception("audit append failed for %s", tool_name)
                return None
        change = _change(tool_name, shaped, session, log) or {}
        # A secret's new value is masked by name here: the audit's redactor masks by shape,
        # and a secret that looks like a plain word went into the log and the prompt in clear.
        logged = {**shaped, "value": _audit.MASK} if change.get("secret") and "value" in shaped else shaped
        try:
            _audit.append(
                _audit.KIND_TOOL_CALL,
                actor="model",
                session=session,
                task=str(task_id or ""),
                tool=tool_name,
                args=logged,
                verdict=_matrix.DENY if guest else decision.verdict,
                reason=f"a guest ({who['label']}) may not have this done" if guest else decision.reason,
                rule_key=decision.rule_key,
                outcome="gated",
            )
        except Exception:  # the audit is a record, never a veto
            log.exception("audit append failed for %s", tool_name)
        if guest:
            return {"action": "block",
                    "message": f"Refused: {tool_name} {decision.reason}, and the person speaking "
                               f"({who['label']}) is a guest here. A guest may ask and be told; "
                               f"anything that would need the owner's approval is not put to the "
                               f"owner on a guest's behalf. Say so, and suggest they ask the owner."}
        if decision.verdict == _matrix.DENY:
            return {"action": "block",
                    "message": f"Refused: {tool_name} {decision.reason}. "
                               f"This is a standing rule ({decision.rule_key}), not a "
                               f"preference — ask the owner to do it from a shell if it "
                               f"is really needed."}
        # The arguments ride in the message because Hermes gives them no field
        # of their own; see summarise_args. Without this the owner is asked
        # "ha_call_service acts on the house" and cannot tell a lamp from a lock.
        # The sentence itself is the shared template's, so the card on the
        # phone, the console and Telegram read the same way.
        # The card's fields (T-0606): the apps read the last five lines of the reason. "Home" is
        # whether the owner is home right now; only then is a remembered "always" a home grant
        # (its rule_key ends :at-home), so a yes given at home never covers a request made away.
        home = _owner_home()
        said, platform = _taint.opened_by(session)
        if not said:
            why = "Jarvis started this on its own; no request of yours is on record"
        elif platform == "tui" and not said.startswith("["):
            why = f"You asked: “{said}”"
        else:
            why = f"Started by {platform or 'a routine or message'}: “{said}”"
        # An import's card is read from the export as the handler reads it (T-0827): parsed from
        # the masked text, a masked credential line became a "fact" on the card and pushed the
        # last real one off it. The parse leaves credentials out itself.
        shown = shaped if tool_name == "memory_import" else _audit.redact(shaped)
        detail = summarise_args(logged)
        lines = ""
        if change.get("known"):
            # The value now and the value asked for (T-1317), as three lines the apps read just
            # above the card. They replace the argument summary, which named the same key and
            # value less clearly; the card's ask names the setting, which the redactor masks
            # because an argument called "key" is usually a credential.
            lines = _messages.change(change["key"], *(
                v if change.get("secret") else _audit.redact_text(_messages.words(v))
                for v in (change.get("before"), change.get("after"))))
            shown = {**shown, "key": change["key"]}
            detail = ""
        # A card goes to a phone and into a log, so a credential on it is masked like anywhere
        # else; the card then says so, because the yes covers text that is not all on it.
        card_addresses = [_audit.redact_text(a) for a in composed]
        card_typed = [_audit.redact_text(t) for t in typed]
        card_shell = (_audit.redact_text(shell[0]) if shell[0] else "",
                      _audit.redact_text(shell[1]) if shell[1] else "")
        card = lines + _messages.card(tool_name, shown, decision.reason, why, home=home,
                                      composed=card_addresses, typed=card_typed,
                                      page=_taint.page(session) if typed else "",
                                      shell=card_shell,
                                      masked=(card_addresses != composed or card_typed != typed
                                              or card_shell != shell))
        suffix = _messages.AT_HOME if home else ""
        if tool_name == "plan_approve":
            # Its own key per card: Hermes remembers "session"/"always" by rule_key, and a shared
            # key would let one remembered answer approve every later plan unseen (_plan.py).
            return {"action": "approve", "message": _plan.describe(
                shaped, lambda call: json.dumps(_audit.redact(call), sort_keys=True, default=str)) + card,
                    "rule_key": _grants.key_for(_plan.rule_key(shaped)) + suffix}
        message = _messages.approval(tool_name, decision.reason, detail) + card
        # The token first, the scopes after it (T-0570): an expiry or a revoke moves every scope.
        rule_key = _grants.key_for(_matrix.call_grain(tool_name, shaped) or decision.rule_key)
        new = _first_time(recipients(tool_name, shaped))
        if new:
            # A first message to someone is the one a slip costs most: say so before the yes (T-0571).
            message = f"First message to {', '.join(new)} — Jarvis has never written to them. {message}"
        read = _taint.read_outside(session)
        if read:
            # Someone else's words may be what asked for this (T-0567): say what was read, and key
            # the card apart so an "always" given in a clean turn does not approve it unseen.
            message = (f"This turn read outside text ({', '.join(read)}) since you spoke: check this "
                       f"is what you asked for. {message}")
            rule_key = f"{rule_key}:after-outside"
        if sensitive:
            # Its own key per ask: Hermes remembers "session" and "always" by rule key, and a
            # remembered yes would be the next unlock with no fingerprint. No home suffix for
            # the same reason. The random half is also what the phone signs.
            marked = f"{rule_key}{_matrix.SENSITIVE_MARK}{secrets.token_hex(16)}"
            # Asked here, inside the call, and not through Hermes' approval queue: Hermes takes
            # a yes for a queued approval from any socket, any chat platform and its HTTP API.
            # The adapter says "once" only for the phone's signature (approval_proof.py).
            choice = phone_proof(session, tool_name, message, marked)
            if choice is not None:
                try:
                    _audit.append(_audit.KIND_TOOL_CALL, actor="owner" if choice == "once" else "model",
                                  session=session, task=str(task_id or ""), tool=tool_name, args=logged,
                                  verdict=decision.verdict, reason=f"sensitive: the phone's fingerprint ({choice})",
                                  rule_key=decision.rule_key,
                                  outcome="approved-by-fingerprint" if choice == "once" else f"refused-{choice}")
                except Exception:  # the audit is a record, never a veto
                    log.exception("audit append failed for %s", tool_name)
                if choice == "once":
                    return None
                return {"action": "block", "message": PROOF_REFUSALS.get(choice, PROOF_REFUSALS["deny"]).format(tool=tool_name)}
            # No phone has a signing key yet, or the adapter could not be asked: the ordinary
            # one-time ask, as before T-0945, so the owner is never locked out of these rows.
            return {"action": "approve", "message": message, "rule_key": marked}
        if one_time:
            return {"action": "approve", "message": message, "rule_key": f"{rule_key}:once:{secrets.token_hex(16)}"}
        return {"action": "approve", "message": message, "rule_key": rule_key + suffix}

    return _gate


#: How long the gate waits for the fingerprint; the adapter's own wait, plus the round trip.
PROOF_WAIT_S = 120.0

#: What the model is told, so it says the right thing and does not simply try again.
PROOF_REFUSALS = {
    "deny": "Refused: the owner said no to {tool} on the phone. Do not try it again unless asked.",
    "timeout": "Not done: {tool} needs the owner's fingerprint on the phone, and nobody confirmed it "
               "in time. Say that it is waiting on the phone; try again only when the owner asks.",
    "error": "Not done: {tool} needs the owner's fingerprint on the phone, and the confirmation could "
             "not be asked for (the adapter did not answer, or it can no longer read the phone's key). "
             "Say so; the owner can do it from a shell.",
}


#: Hermes' own default for ``plugins.hook_callback_timeout`` (hermes_cli/plugins_dispatch.py).
HOOK_LIMIT_S = 30.0
#: Left of that limit for the answer to travel back and the gate to return.
HOOK_MARGIN_S = 10.0


#: The shortened wait last logged, so it is said once and not per call.
_wait_logged: float | None = None


def proof_wait() -> float:
    """Seconds the phone gets to sign: PROOF_WAIT_S, kept under Hermes' limit on a hook.

    Hermes abandons a pre_tool_call callback that runs past ``plugins.hook_callback_timeout``,
    blocks the call and skips the callback for a minute after. A wait that outlived the limit
    would turn a slow fingerprint into a minute of refused tools, so the wait ends first and the
    call is refused as an unanswered ask. The deployed config raises the limit to fit two
    minutes (``config.template.yaml.in``); an older volume gets what its limit allows.
    """
    try:
        from hermes_cli.plugins import _resolve_hook_callback_timeout
        limit = float(_resolve_hook_callback_timeout())
    except Exception:  # not under Hermes, or a Hermes that moved it: assume its default
        limit = HOOK_LIMIT_S
    if limit <= 0:  # Hermes runs hooks inline, with no limit
        return PROOF_WAIT_S
    wait = max(5.0, min(PROOF_WAIT_S, limit - HOOK_MARGIN_S))
    global _wait_logged
    if wait < PROOF_WAIT_S and _wait_logged != wait:
        # Once per value: a wait this short reads as "the phone never asked" unless the log
        # names the setting that cut it.
        _wait_logged = wait
        logging.getLogger(__name__).warning(
            "sensitive approval: the phone gets %.0f s for the fingerprint, not %.0f: "
            "plugins.hook_callback_timeout is %.0f s", wait, PROOF_WAIT_S, limit)
    return wait


def phone_proof(session: str, tool: str, message: str, rule_key: str) -> str | None:
    """Ask the adapter to get this call signed on the phone: "once", "deny", "timeout" or "error".

    None only when there is provably no phone to sign: no adapter key set here, an adapter that
    says no phone has a key ("unavailable"), or one too old to have the route (404). The caller
    then asks the ordinary way. Anything else that goes wrong is "error" and the call is
    refused: a fault must not turn a fingerprint into a tap. Does not cover an adapter that
    answers wrongly: its "once" is taken on trust, over loopback, under the master key.
    """
    import json
    from urllib import error, request

    try:
        from aura_schema import from_env
        settings = from_env()
        base, key = str(settings.adapter_url or "http://127.0.0.1:8643").rstrip("/"), str(settings.adapter_key or "")
    except Exception:  # unreadable settings do not prove there is no phone
        logging.getLogger(__name__).warning("sensitive approval: settings unreadable", exc_info=True)
        return "error"
    if not key:
        return None
    wait = proof_wait()
    try:
        body = json.dumps({"session_id": session, "tool": tool, "message": message, "rule_key": rule_key,
                           "wait_s": wait}).encode()
        req = request.Request(f"{base}/api/approval/proof", data=body, method="POST",
                              headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        with request.urlopen(req, timeout=wait + HOOK_MARGIN_S / 2) as r:  # noqa: S310 - the house's own adapter
            choice = json.load(r).get("choice")
    except error.HTTPError as exc:
        if exc.code == 404:
            return None  # an adapter from before T-0945: no phone can have registered a key
        logging.getLogger(__name__).warning("sensitive approval: the adapter answered %s", exc.code)
        return "error"
    except Exception:  # never a yes, and never a quiet step down to a tap
        logging.getLogger(__name__).warning("sensitive approval: the adapter could not be asked", exc_info=True)
        return "error"
    if choice == "unavailable":
        return None
    return choice if choice in ("once", "deny", "timeout") else "error"


def _owner_home() -> bool:
    """Presence from the house; any failure (module, network, token) is away."""
    try:
        from . import _house
        return bool(_house.owner_home())
    except Exception:  # noqa: BLE001 - the safe side: a home grant does not apply
        return False


#: The tools whose completed call wrote to a person, and where its recipients are (T-0571).
_SENDS: dict[str, tuple[str, ...]] = {"mail_send": ("to", "cc"), "texts_reply": ("conversation",), "calls_place": ("to",)}


def recipients(tool_name: str, args: Any) -> list[str]:
    """Who a send writes to, lower-cased, in order; [] for anything that is not a send."""
    fields = _SENDS.get(tool_name, ())
    out: list[str] = []
    for field in fields:
        value = args.get(field) if isinstance(args, dict) else None
        for one in value if isinstance(value, list) else [value] if isinstance(value, str) else []:
            for part in str(one).split(","):
                who = part.strip().lower()
                if who and who not in out:
                    out.append(who)
    return out


def _first_time(people: list[str]) -> list[str]:
    """The ones no completed send has written to yet, by the audit log's outbound rows."""
    if not people:
        return []
    if not _audit.readable():
        return list(people)  # a log that cannot be read cannot vouch for anyone (T-0608)
    seen: set[str] = set()
    for row in _audit.read(limit=100_000, kind=_audit.KIND_OUTBOUND):
        seen.update(str(w).lower() for w in ((row.get("args") or {}).get("to") or []))
    return [p for p in people if p not in seen]


def audit_outcome_hook(
    *,
    logger: logging.Logger | None = None,
) -> Callable[..., None]:
    """The ``post_tool_call`` half: what actually happened to a gated call.

    ``pre_tool_call`` records the decision; without this, the log would say
    "asked" and never say "and then it ran and failed". Only gated tools are
    recorded, matching the pre-hook, so the two halves of one call join on
    ``tool`` + ``session`` + ``task`` and the free calls stay out of the way.

    Hermes ignores this hook's return value and swallows its exceptions
    (``model_tools.py``, ``_emit_post_tool_call_hook``), so it is an observer
    and nothing here can change what the tool did.
    """
    log = logger or logging.getLogger(__name__)

    def _observe(tool_name: str = "", args: Any = None, result: Any = None,
                 task_id: str = "", session_id: str = "", duration_ms: int = 0,
                 status: str = "", error_type: str = "", error_message: str = "",
                 **kwargs: Any) -> None:
        del result, kwargs
        # Every outcome, FREE tools included: a dead ComfyUI fails its free status call first.
        # A call the gate blocked ("blocked") says nothing about the service.
        if status in ("ok", "error"):
            _breaker.record(tool_name, status == "ok", str(error_message or ""))
        try:
            if _matrix.decide(tool_name, args if isinstance(args, dict) else {}).free:
                return
            _audit.append(
                _audit.KIND_TOOL_CALL,
                actor="model",
                session=str(session_id or ""),
                task=str(task_id or ""),
                tool=tool_name,
                args=args if isinstance(args, dict) else {},
                outcome=status or "ok",
                detail=f"{error_type}: {error_message}".strip(": ") if error_type else "",
                duration_ms=int(duration_ms or 0),
            )
            sent_to = recipients(tool_name, args) if status == "ok" else []
            if sent_to:
                # Who has been written to: the next card to them is no longer a first (T-0571).
                _audit.append(_audit.KIND_OUTBOUND, actor="model", session=str(session_id or ""),
                              tool=tool_name, args={"to": sent_to}, outcome="sent")
        except Exception:
            log.exception("audit outcome append failed for %s", tool_name)

    return _observe


def approval_observer(
    *,
    logger: logging.Logger | None = None,
) -> tuple[Callable[..., None], Callable[..., None]]:
    """``(pre_approval_request, post_approval_response)`` observers for the log.

    Hermes fires these from ``tools/approval.py`` for every approval decision —
    the CLI prompt, the gateway/HUD door and the ``smart`` mode's auxiliary-LLM
    verdict alike — and they are the only place the *answer* is visible. The
    tool name is not a field on them; it is inside ``pattern_key``
    (``plugin_rule:<rule_key>``) and ``command`` (``<tool> (plugin approval
    rule)``), which is what :func:`tool_from_approval` reads back out.
    """
    log = logger or logging.getLogger(__name__)

    def _requested(command: str = "", description: str = "", pattern_key: str = "",
                   session_key: str = "", surface: str = "", **kwargs: Any) -> None:
        del kwargs
        try:
            _audit.append(
                _audit.KIND_APPROVAL, actor="jarvis", session=str(session_key or ""),
                tool=tool_from_approval(command, pattern_key), rule_key=pattern_key,
                reason=description, decision="requested", outcome=surface or "gateway",
                detail=command,
            )
        except Exception:
            log.exception("audit approval-request append failed")

    def _responded(command: str = "", description: str = "", pattern_key: str = "",
                   session_key: str = "", surface: str = "", choice: str = "",
                   decided_by: str = "", **kwargs: Any) -> None:
        del description, kwargs
        # Only a human's "always" starts a clock: smart mode answers smart_<verdict> as aux_llm,
        # and the model cannot answer an approval (T-0570).
        if choice == "always" and decided_by != "aux_llm":
            try:
                _grants.record(pattern_key, tool_from_approval(command, pattern_key))
            except Exception:
                # Hermes keeps this key whatever happens here; with no row it would never
                # lapse or be listed, so its generation moves on and the key is dead.
                log.exception("standing grant record failed; voiding %s", pattern_key[:80])
                try:
                    _grants.void(pattern_key)
                except Exception:
                    log.exception("standing grant void failed: %s stands with no clock", pattern_key[:80])
        try:
            _audit.append(
                _audit.KIND_APPROVAL, actor=decided_by or "owner",
                session=str(session_key or ""),
                tool=tool_from_approval(command, pattern_key), rule_key=pattern_key,
                decision=choice or "unknown", outcome=surface or "gateway",
                detail=command,
            )
        except Exception:
            log.exception("audit approval-response append failed")

    return _requested, _responded


def tool_from_approval(command: str, pattern_key: str = "") -> str:
    """The tool an approval prompt is about — see :mod:`_approval_event`.

    Re-exported here because this is where the approval observers live; the
    parse itself is in a module with no imports beyond ``re`` so the benchmark
    runner can share it without pulling in ``aura_schema``.
    """
    return _approval_event.from_strings(command, pattern_key)
