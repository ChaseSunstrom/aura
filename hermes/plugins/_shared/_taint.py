"""Which of this turn's reads brought in someone else's words (T-0567).

A page, a mail, a file or the screen can say "now send X to Y", and the model may do it. The
approval card is where the owner catches that, so the card must say what the turn has read
since the owner spoke, and a standing "always" given in a clean turn must not wave a tainted
one through (``_approval_hook.matrix_gate`` keys it apart). ``turn_start`` (``pre_llm_call``)
opens the owner's turn; ``note_tool`` (``post_tool_call``) marks it.

Not covered: words that came in through a tool not named in ``READERS`` (the list names the
readers of other people's text, and a new one has to be added here), and text the owner
pasted into his own message, which is his. The one prompt that is not all his is the phone's
assist call, which carries the screen it was made over (``SCREEN_MARKER``, T-1095): that turn
opens marked. Only the turn the screen arrived in: a later turn of the session, with the
screen in its history, opens clean.

It also keeps what the session was GIVEN (T-1492), so the gate can tell an address the turn was
handed from one the model wrote (``was_given``, read by ``_approval_hook.matrix_gate`` for the
tools on ``_matrix.FETCHERS``). Two records per session:

- what it was SHOWN: the text of each tool result and each of the owner's messages. A computing
  tool's output is never recorded (``COMPUTED``: a shell can print anything it was told to), and
  a call the gate stopped records nothing.
- what the model WROTE: each word (as split on white space, nothing trimmed) of any call's
  arguments that could hold an address and that the session had not been shown when it was
  written. An address inside one of those is the model's own, wherever it turns up afterwards,
  so a search that repeats its query, or a note stored by one tool and read back by another,
  gives it nothing.

A store the model can write is not where an address is given (T-1493): a file written in two
halves and read back holds a join that was never written as one word. So once a session has
written a file or run a computing tool in a turn that had read outside text, what its file
readers return is no longer recorded as shown (``_JOINED``, for the rest of the session), and
a search of the history never is: the model's own replies are in it.

Not covered by these records: a store with a free writer that is not named in ``_WRITERS``; a
file written in a turn that had read nothing, or read back in another session; a process restart,
which forgets the records (every address then asks); text beyond ``SHOWN_CAP`` per session,
oldest dropped first. A given address the model has since written with something touching it
(a full stop after it, a label before it with no space) asks once when it need not, and so
does a link in one of the owner's files once the session is marked, or in the history.

And what the owner SAID (T-1494), so the gate can tell text the owner asked to have typed into
a page from text the turn picked up elsewhere (``was_said``, read for ``_matrix.TYPERS``): each
of the owner's messages of the session, up to the screen frame when the phone's call carries
one, with the last address the browser reported (``page``) for the card. Not covered: which
of the owner's words are typed; a restart, which forgets them (typed text then asks until the
owner speaks again); a page moved by a click since the browser last reported an address.
"""
from __future__ import annotations

import ast
import io
import json
import re
import threading
import tokenize
from collections import deque
from typing import Any

from . import _egress

#: The tools whose results are words someone else wrote. A prefix ends with "_". Held from
#: the other side by ``tests/test_readers_complete.py``: every tool on a free row is on this
#: list or is named there with the reason it is not, so a new free tool fails until decided.
#: ``mcp__`` is every tool of every connected server: one that is added later brings in its
#: author's words until its tools are named. ``delegate_task`` is here for its answer, which
#: is the helper's words about whatever the helper read; ``api_add`` and ``mcp_add`` for the
#: document and the tool list they bring back.
READERS: tuple[str, ...] = (
    "web_", "browser_", "mail_read", "mail_search", "fileshare_read", "read_file",
    "search_files", "calendar_events", "contacts_", "desktop_screen", "vision_analyze",
    "extract_", "connector_call", "connect_read", "feeds", "jellyfin_search", "plex_", "session_search",
    "texts_read", "texts_search", "calls_status",
    "recording_transcribe", "desktop_windows", "integration_search", "integration_inspect",
    "n8n_execution", "place_search", "jellyfin_resume", "jellyfin_sessions", "grafana_query",
    "grafana_panel_query", "delegate_task", "api_add", "mcp_add", "mcp__",
    "files_search", "fileshare_list", "desktop_files", "desktop_notes", "memory_proposals",
    # A discovered device's name is one the device gave itself on the network (T-1543).
    "house_ha_discovered",
)
#: A reader's actions that bring no words in: the count of the memory proposals is a number
#: (the morning brief reads it, and must not become a turn that read outside text for that),
#: and a drop answers with ids. Any other action, or none, reads (T-1500).
_QUIET_ACTIONS: dict[str, tuple[str, ...]] = {"memory_proposals": ("count", "drop")}

#: The readers of the owner's own files and history. A shell command after one of these is not
#: asked about; after any other reader it is (``read_beyond_house``, T-1493). A reader is from
#: beyond the house unless it is named here, so a new one asks until someone decides otherwise.
#: Names only, no prefixes. The split is by where the words are kept, not by who wrote them:
#: one of the owner's files can hold a page they saved.
#: The notes server's tools are named one by one (the ten its free row names): a tool it
#: grows later is from beyond the house until someone puts it here.
_NOTES_READS: tuple[str, ...] = tuple("mcp__notes__" + name for name in (
    "read_file", "read_text_file", "read_media_file", "read_multiple_files", "list_directory",
    "list_directory_with_sizes", "directory_tree", "search_files", "get_file_info",
    "list_allowed_directories"))
OWN_READERS: tuple[str, ...] = (
    "fileshare_read", "read_file", "search_files", "session_search",
    "files_search", "fileshare_list", "desktop_files", "desktop_notes", *_NOTES_READS)
#: The notes server's writes: on the prefix above, and they bring in nobody's words.
_NOTES_WRITES: frozenset[str] = frozenset("mcp__notes__" + name for name in (
    "write_file", "edit_file", "create_directory", "move_file"))
#: The shells, under each name Hermes has had for them.
_SHELLS: frozenset[str] = frozenset({"terminal", "process", "process_manage"})
#: An address in a shell command runs to the next white space: a quote or a ``$`` inside it
#: is something the shell rewrites before anything is fetched.
_ADDRESS = re.compile(r"https?://\S+", re.IGNORECASE)
#: The only shape taken as one of the stack's own: nothing a shell expands, joins or splits on.
_PLAIN_ADDRESS = re.compile(r"https?://[A-Za-z0-9._~:/?#+,=%&-]+", re.IGNORECASE)

#: The opening words of the phone's frame around a screen (``ScreenContext.MARKER``), bound by
#: ``tests/contracts/screen-frame.json``. A page cannot remove them, and typing them only asks more.
SCREEN_MARKER = "[What was on my phone's screen when I called you"

_LOCK = threading.Lock()
#: Per session, the readers this turn has run, in order. Bounded like later._TURN.
_TURN: dict[str, list[str]] = {}


def _fetched(args: Any) -> bool:
    """Whether a shell call has an http(s) address in it that is not the stack's own.

    Does not see a fetch with no scheme written (a bare ``curl host/path``), nor one a script
    on disk makes, nor the output of a background process read in a later turn than the one
    that started it: those bring words back without marking the turn.
    """
    for text in _strings(args):
        for found in _ADDRESS.findall(text):
            address = found.rstrip("'\"`)>;,.|&")
            try:
                # The stack's own only when the shell fetches exactly what is written: a quote,
                # a ``$`` or a ``;`` inside the run can make it another host or a second
                # command (``http://own.example'@a.example/'``), and so can a user part.
                own = bool(_PLAIN_ADDRESS.fullmatch(address)) and _egress.allowed(address)
            except Exception:  # an address nothing can read is not known to be the stack's own
                own = False
            if not own:
                return True
    return False


def _reads_outside(tool: str, args: Any = None) -> bool:
    """Whether this call's result can hold words someone else wrote. A shell is one only by
    its arguments: when it fetched (``_fetched``), its output is what came back."""
    if tool in _NOTES_WRITES:
        return False
    if tool in _SHELLS:
        return _fetched(args)
    if isinstance(args, dict) and args.get("action") in _QUIET_ACTIONS.get(tool, ()):
        return False
    return any(tool.startswith(r) if r.endswith("_") else tool == r for r in READERS)


#: Per session, the words that opened the turn and the surface they came from (the approval
#: card's "why", T-0606). Clipped: it is shown on a phone and is not the audit log.
_OPENED: dict[str, tuple[str, str]] = {}


#: Tools whose output is computed from the model's own words: never "shown".
COMPUTED: frozenset[str] = frozenset({
    "terminal", "process", "process_manage", "execute_code", "delegate_task"})

#: Characters of shown text kept per session and of one result; how many sessions; how many
#: written words per session, and how many of one call are looked up. Every limit fails toward
#: asking: dropped text is no longer shown; a call with more words than ``WROTE_PER_CALL`` has
#: every one counted as written; and once a session has written ``WROTE_CAP`` words, no later
#: result is taken as shown (a written word could no longer be told from a shown one), while
#: what it was shown before, and what the owner says after, still count.
SHOWN_CAP = 400_000
RESULT_CAP = 200_000
SHOWN_SESSIONS = 64
WROTE_CAP = 5_000
WROTE_PER_CALL = 200

_SHOWN: dict[str, deque[str]] = {}
_WROTE: dict[str, set[str]] = {}

#: The tools that write where a file reader reads: the file writers, the share's (it asks,
#: but a yes to it can be remembered) and anything that computes (a shell writes files too).
_WRITERS: frozenset[str] = frozenset({"write_file", "patch", "fileshare_write"}) | COMPUTED | _NOTES_WRITES
#: The two desktop families read and write by ``action``: these are their reads, and any
#: other action, or none, is taken as a write (as their rows in the matrix take it).
_READ_ACTIONS: dict[str, tuple[str, ...]] = {"desktop_files": ("list", "read"), "desktop_notes": ("find",)}
#: The readers of files and notes the model can write: the owner's own, less the history.
_FILE_READERS: frozenset[str] = frozenset(OWN_READERS) - {"session_search"}


def _writes(tool: str, args: Any) -> bool:
    if tool in _WRITERS:
        return True
    reads = _READ_ACTIONS.get(tool)
    if reads is None:
        return False
    return (args.get("action") if isinstance(args, dict) else None) not in reads


#: Never recorded as shown: a computing tool's output, the history, which holds the model's
#: own replies (a store it writes with no call at all), and the memory proposals, which a
#: turn that had read a page wrote: an address in one is not a link the session was given.
_NEVER_SHOWN: frozenset[str] = COMPUTED | {"session_search", "memory_proposals"}
#: Sessions that wrote a file in a turn that had read outside text. Kept long after the other
#: records of a session are dropped: forgetting one would take its files as given again.
JOINED_SESSIONS = 5_000
_JOINED: dict[str, bool] = {}

#: Characters of the owner's own words kept per session (white space collapsed), oldest
#: message dropped first; a dropped word is no longer theirs, which only asks more.
SAID_CAP = 60_000
_SAID: dict[str, deque[str]] = {}
#: Per session, the last address the browser reported (a navigate's or a back's answer).
_PAGE: dict[str, str] = {}
#: The browser tools whose answer names the page they landed on.
_REPORTS_PAGE: frozenset[str] = frozenset({"browser_navigate", "browser_back"})


def _plain(text: Any) -> str:
    """A result as the text an address would be found in: JSON's escaped slash and HTML's
    ``&amp;`` undone."""
    return str(text if text is not None else "").replace("\\/", "/").replace("&amp;", "&")


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v)]
    return []


def _in_shown(session_id: str, needle: str) -> bool:
    return any(needle in chunk for chunk in _SHOWN.get(session_id, ()))


def _keep(session_id: str, text: str) -> None:
    """Add to the session's shown text. Called under ``_LOCK``."""
    if not text:
        return
    chunks = _SHOWN.pop(session_id, None) or deque()
    _SHOWN[session_id] = chunks   # re-inserted last: the session shown to longest ago is dropped first
    chunks.append(text[:RESULT_CAP])
    while sum(map(len, chunks)) > SHOWN_CAP and len(chunks) > 1:
        chunks.popleft()
    while len(_SHOWN) > SHOWN_SESSIONS:
        gone = next(iter(_SHOWN))
        _SHOWN.pop(gone)
        _WROTE.pop(gone, None)


def was_shown(session_id: str, text: str) -> bool:
    """Was this text, as written, in a result this session was shown or in the owner's words?"""
    if not session_id or not text:
        return False
    with _LOCK:
        return _in_shown(session_id, text)


def was_given(session_id: str, forms: Any) -> bool:
    """Was an address the session's to follow? True when one of its forms was shown as written
    and is not inside a word the model itself wrote before the session had been shown it."""
    if not session_id:
        return False
    with _LOCK:
        wrote = _WROTE.get(session_id, ())
        return any(
            bool(form) and _in_shown(session_id, form) and not any(form in word for word in wrote)
            for form in forms)


def _keep_said(session_id: str, message: Any, parent: str = "") -> None:
    """Add the owner's message to what they said this session. Called under ``_LOCK``.

    A session started from another one (a helper: its opening message is the task the model
    wrote for it) is given what the owner said in that one and nothing of its own message.
    """
    if parent:
        _SAID.pop(session_id, None)
        _SAID[session_id] = deque(_SAID.get(parent, ()))
        message = ""
    raw = message if isinstance(message, str) else " ".join(_strings(message))
    # The phone's assist call appends the screen after the owner's words: only up to the frame.
    own = " ".join(raw.split(SCREEN_MARKER, 1)[0].split())[:SAID_CAP]
    said = _SAID.pop(session_id, None) or deque()
    _SAID[session_id] = said   # re-inserted last: the session spoken to longest ago goes first
    if own:
        said.append(own)
    while sum(map(len, said)) > SAID_CAP and len(said) > 1:
        said.popleft()
    while len(_SAID) > SHOWN_SESSIONS:
        gone = next(iter(_SAID))
        _SAID.pop(gone)
        _PAGE.pop(gone, None)


def _as_said(said: str, typed: str) -> bool:
    """Is ``typed`` in ``said`` as whole words: not cut from the middle of one?"""
    at = said.find(typed)
    while at >= 0:
        end = at + len(typed)
        cut_before = at > 0 and said[at - 1].isalnum() and typed[0].isalnum()
        cut_after = end < len(said) and said[end].isalnum() and typed[-1].isalnum()
        if not cut_before and not cut_after:
            return True
        at = said.find(typed, at + 1)
    return False


def was_said(session_id: str, text: Any) -> bool:
    """Is this text the owner's own words of this session? True when it stands in one of
    their messages (white space there collapsed) in the case it was said and as whole words,
    and is itself written with one space between words and none around them.

    White space is the model's own: how many spaces, a tab or a line break between the
    owner's words is something a page can read, so text spaced any other way was not said.
    Never true for text a tool showed the session (a mail the turn read is not the owner's),
    for the screen the phone's call carries, or for empty text. Does not say the owner asked
    for these words to be typed: which of them the model picks is its own choice.
    """
    typed = str(text if text is not None else "")
    if not session_id or not typed or typed != " ".join(typed.split()):
        return False
    with _LOCK:
        return any(_as_said(said, typed) for said in _SAID.get(session_id, ()))


def page(session_id: str) -> str:
    """The last address the browser reported for this session; "" when none is on record."""
    with _LOCK:
        return _PAGE.get(session_id, "")


def _note_page(tool: str, session_id: str, args: Any, result: Any) -> None:
    """Keep the address a navigate or a back landed on, from its answer; the address asked for
    when a navigate's answer names none. Called for a call that ran."""
    found: Any = None
    try:
        answer = json.loads(result) if isinstance(result, str) else result
        if isinstance(answer, dict):
            inner = answer.get("data")
            found = answer.get("url") or (inner.get("url") if isinstance(inner, dict) else None)
    except ValueError:
        found = None
    if not isinstance(found, str) or not found:
        found = args.get("url") if tool == "browser_navigate" and isinstance(args, dict) else None
    if isinstance(found, str) and found:
        with _LOCK:
            if session_id in _SAID or len(_PAGE) < SHOWN_SESSIONS:
                _PAGE[session_id] = found[:2000]


def shown_size(session_id: str) -> int:
    """Characters of shown text kept for this session."""
    with _LOCK:
        return sum(map(len, _SHOWN.get(session_id, ())))


def turn_start(session_id: str = "", user_message: Any = "", platform: str = "",
               parent_session_id: Any = "", **_: Any) -> None:
    """``pre_llm_call``: the owner's words open a clean turn, and what they name is given.

    With a ``parent_session_id`` (a helper the model started) the opening message is the
    model's, not the owner's: it is not kept as said (T-1494).
    """
    if not session_id:
        return
    said = " ".join(str(user_message or "").split())[:140]
    with _LOCK:
        _keep(session_id, _plain(user_message))
        _keep_said(session_id, user_message, str(parent_session_id or ""))
        _TURN[session_id] = ["phone_screen"] if SCREEN_MARKER in str(user_message or "") else []
        _OPENED[session_id] = (said, str(platform or ""))
        while len(_TURN) > 500:
            _OPENED.pop(next(iter(_TURN)), None)
            _TURN.pop(next(iter(_TURN)))


def opened_by(session_id: str) -> tuple[str, str]:
    """``(words, platform)`` that opened this session's turn; ``("", "")`` when none is on record."""
    with _LOCK:
        return _OPENED.get(session_id, ("", ""))


#: Python's tokens that carry no text the model could have written an address in.
_CODE_SYNTAX = frozenset({tokenize.NAME, tokenize.OP, tokenize.NUMBER, tokenize.NEWLINE,
                          tokenize.NL, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER,
                          tokenize.ENCODING})


def _written_texts(tool: str, args: Any) -> list[str]:
    """The texts a call's arguments hold, as the model wrote them.

    ``browser_exec``'s code is read token by token: its string literals and comments whole,
    and none of the call syntax around them. Split on white space alone,
    ``new_tab("https://example.com")`` was one word the session had never been shown, so the
    owner's own address inside it counted as the model's and following it asked (the T-1519
    proof turn). Not every tool: a bracket or a comma can be inside an address, and a split on
    them would let a join of two shown pieces pass as shown. Code that does not tokenize is
    read as plain text, which only asks more.
    """
    code = args.get("code") if tool == "browser_exec" and isinstance(args, dict) else None
    if not isinstance(code, str):
        return _strings(args)
    # Its other arguments, as any tool's.
    texts: list[str] = _strings({k: v for k, v in args.items() if k != "code"})
    try:
        for token in tokenize.generate_tokens(io.StringIO(code).readline):
            if token.type in _CODE_SYNTAX:
                continue
            text = token.string
            if token.type == tokenize.STRING:
                try:
                    text = str(ast.literal_eval(text))
                except (ValueError, SyntaxError):
                    text = text.strip("rRbBuUfF").strip("\"'")
            texts.append(text)
    except (tokenize.TokenError, SyntaxError):
        return _strings(args)
    return texts


def _could_hold_an_address(word: str) -> bool:
    """A dot, a slash or a colon, or a run of digits (a host can be written as one number)."""
    return len(word) >= 4 and (any(c in word for c in "./:") or word.isdigit())


def _note_shown(tool: str, session_id: str, args: Any, result: Any) -> None:
    """Record what the model wrote in this call, then what the call showed the session."""
    words = {w for s in _written_texts(tool, args) for w in _plain(s).split()
             if _could_hold_an_address(w)}
    with _LOCK:
        wrote = _WROTE.pop(session_id, None) or set()
        _WROTE[session_id] = wrote   # re-inserted last, so it is never the one dropped below
        while len(_WROTE) > SHOWN_SESSIONS:
            # The two records go together: shown text with no record of what was written would
            # take the model's own words as given.
            gone = next(iter(_WROTE))
            _WROTE.pop(gone)
            _SHOWN.pop(gone, None)
        if _writes(tool, args) and _TURN.get(session_id):
            # Written after someone else's words came in: from here on a file may hold what
            # the model joined, so reading one back gives the session nothing.
            _JOINED.pop(session_id, None)
            _JOINED[session_id] = True
            while len(_JOINED) > JOINED_SESSIONS:
                _JOINED.pop(next(iter(_JOINED)))
        if len(wrote) >= WROTE_CAP:
            return   # see WROTE_CAP: nothing later is taken as shown
        # Written by the model unless the session had already been shown it, whole. Past the
        # per-call cap nothing is looked up: every word counts as written, which only asks more.
        checked = len(words) <= WROTE_PER_CALL
        wrote.update(w for w in words if not (checked and _in_shown(session_id, w)))
        if tool not in _NEVER_SHOWN and not (tool in _FILE_READERS and session_id in _JOINED):
            raw = result if isinstance(result, str) else ("" if result is None else str(result))
            _keep(session_id, _plain(raw[:RESULT_CAP]))


def note_tool(tool_name: str = "", session_id: str = "", args: Any = None, result: Any = None,
              status: str = "", **_: Any) -> None:
    """``post_tool_call``: a reader of other people's words marks the turn, and what a call
    returned is what the session was shown. A call the gate stopped ran nothing."""
    tool = str(tool_name or "")
    if not session_id:
        return
    if status != "blocked":
        _note_shown(tool, session_id, args, result)
        if tool in _REPORTS_PAGE:
            _note_page(tool, session_id, args, result)
    if status == "blocked" and tool in _SHELLS:
        return   # a shell the gate stopped fetched nothing
    if not _reads_outside(tool, args):
        return
    with _LOCK:
        seen = _TURN.setdefault(session_id, [])
        if tool not in seen:
            seen.append(tool)


def read_outside(session_id: str) -> list[str]:
    """The readers this turn has run, oldest first; [] for a clean turn."""
    with _LOCK:
        return list(_TURN.get(session_id) or [])


def read_beyond_house(session_id: str) -> list[str]:
    """The readers this turn has run that are not the owner's own files or history
    (``OWN_READERS``), oldest first. The screen a phone's call carried counts.

    Does not say the owner's own files hold only their own words: a saved page or a download
    is read by an own reader, and a shell after it is not asked about for that.
    """
    return [tool for tool in read_outside(session_id) if tool not in OWN_READERS]
