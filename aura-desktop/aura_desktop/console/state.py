"""What the console shows, reduced from the gateway's events.

The web's ``hud.ts`` reducer, in the shape a terminal needs: the turn and its
tool calls, the messages, the approvals and clarify questions, the goal, the
background rows, the ``hud.panel.*`` content JARVIS pulled up, the
``hud.layout.*`` frames, and the adapter's ``GET /api/status`` report. Every
event shape is ``docs/hermes-hud-protocol.md``'s, and the reactor's inputs
are the contract's (``tests/contracts/reactor.json`` ``derivation``), read
off this state the way ``reactorState.ts`` reads them off the web's.

Two readings are deliberately the web's and not simpler ones:

* **warming** counts only while a turn is waiting on it. ``/api/status`` says
  ``starting`` whenever llama-swap has the model unloaded, which at rest is
  most of the evening; a reactor that sat in "Warming up" all night would say
  the assistant was busy when it was idle.
* **the goal's reason is verbatim.** The judge's sentence is the one honest
  account of why the loop went round again; the console never paraphrases it.

What this module does not do: it holds no clock of its own (``now`` is passed
in, so a test can replay a tape at any speed) and it never talks to a socket.
:mod:`app` feeds it frames; :mod:`render` reads it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .. import reactor

#: What this terminal can show, sent as ``surface`` on every ``prompt.submit``
#: (``tests/contracts/surface-capabilities.json``, the ``console`` row; the
#: registry the artifacts plugin keeps says the same). It can name a file and
#: hand one over; it cannot show one -- which is the whole point of saying so:
#: a video asked for here is handed to the desktop by ``artifact_send`` rather
#: than described, and the model is told that in one prompt line instead of
#: working it out.
SURFACE_REPORT: dict[str, Any] = {
    "name": "console",
    "images": False,
    "video": False,
    "audio": False,
    "documents": False,
    "interactive": False,
    "downloads": True,
    "notifications": False,
    "max_bytes": 1024 * 1024,
}

__all__ = [
    "Tool",
    "Message",
    "Approval",
    "Clarify",
    "Goal",
    "Background",
    "ContentPanel",
    "Turn",
    "Hud",
    "parse_goal",
    "approval_tool",
    "panel_type_for",
    "APPROVAL_CHOICES",
    "CONTENT_HOME",
    "ACTIVITY_KIND",
]

#: The ``message.complete`` statuses that end a turn without an error
#: (``tests/contracts/reactor.json`` ``derivation.quiet_ends``). A stop the person
#: asked for is not a failure; counting ``interrupted`` as one put the ring at alert.
QUIET_ENDS: tuple[str, ...] = ("complete", "completed", "ok", "interrupted")

#: The choices ``approval.respond`` accepts, in the order the console offers them.
APPROVAL_CHOICES: tuple[str, ...] = ("once", "session", "always", "deny")

#: The activity view (``tests/contracts/activity-frames.json``) as one card in the
#: browser panel, as the web draws it. Not a ``hud.panel.show`` kind, so it is
#: not in CONTENT_HOME, whose keys are the panel contract's.
ACTIVITY_KIND = "activity"
#: The trail the card keeps: a terminal shows the last few steps, not a scrollback.
ACTIVITY_STEPS = 6

#: ``hud.panel.show`` kind -> the §9.1 panel type that hosts it.
CONTENT_HOME: dict[str, str] = {
    "text": "document", "code": "document",
    "table": "telemetry", "chart": "telemetry", "telemetry": "telemetry",
    "image": "image", "camera": "image",
    "terminal": "task", "web": "browser", "device": "house",
    "goal": "goals", "approval": "approvals", "clarify": "approvals",
    "notice": "notifications",
    # The media kinds: a published file by url + mime. A terminal plays none of
    # them; the card says what the file is and where it is (render.py).
    "video": "video", "audio": "audio", "document": "document",
}

#: The goal line's leading glyph -> phase (``docs/hermes-hud-protocol.md``).
_GOAL_GLYPHS: tuple[tuple[str, str], ...] = (
    ("⊙", "set"), ("↻", "continuing"), ("⏳", "parked"), ("⏸", "paused"), ("✓", "achieved"),
    ("✔", "achieved"),
)
#: ``<tool_name> (plugin approval rule)``: the only place Hermes names the tool an
#: approval is about. The parse is the audit log's and the web's
#: (``hermes/plugins/_shared/_approval_event.py``, ``approvalEvent.ts``); the
#: console cannot import the plugin tree, so ``tests/contracts/approval-event.json``
#: is what holds this copy to the same answers.
_TOOL_IN_COMMAND = re.compile(r"^<([A-Za-z0-9_.:-]+)>\s*\(plugin approval rule\)")
_PLUGIN_RULE = "plugin_rule:"
_TURNS = re.compile(r"(\d+)\s*/\s*(\d+)")
_BUDGET = re.compile(r"(\d+)-turn budget")
_CLEARED = re.compile(r"^[^a-z]*goal cleared\b", re.IGNORECASE)
_NO_GOAL = re.compile(r"^no active goal\b", re.IGNORECASE)


#: The two (tests/contracts/persona.json), as the switch and the status name them.
PERSONAS: tuple[str, ...] = ("aura", "jarvis")
#: How each is written in the prompt: both packs write their name in capitals.
PERSONA_NAMES: dict[str, str] = {p: p.upper() for p in PERSONAS}


#: What the console shows when the adapter does not answer (the table's ``no_answer``).
NO_ANSWER = ("down", "no answer")


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _obj(value: Any) -> dict[str, Any]:
    """``value`` as a dict copy, or an empty one: a payload field that should be
    an object and is not (a string, null) reads as absent rather than raising."""
    return dict(value) if isinstance(value, dict) else {}


@dataclass
class Tool:
    """One tool call, from ``tool.start`` to ``tool.complete``."""

    id: str
    name: str
    context: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    state: str = "running"  # running | done | failed
    progress: str = ""
    duration: float | None = None
    error: str | None = None
    result: str | None = None
    started_at: float = 0.0
    finished_at: float | None = None


@dataclass
class Message:
    """A line of the stream: ``you``, ``jarvis``, or a tool row between them."""

    role: str
    text: str = ""
    pending: bool = False
    error: str | None = None
    tool: Tool | None = None
    at: float = 0.0


@dataclass
class Approval:
    request_id: str
    session_id: str
    tool: str
    detail: str
    reason: str
    choices: tuple[str, ...]
    at: float
    #: A sensitive ask (T-0945): only the phone's fingerprint says yes; here it can be denied.
    on_phone: bool = False


@dataclass
class Clarify:
    request_id: str
    session_id: str
    question: str
    options: tuple[str, ...]
    at: float


@dataclass
class Goal:
    phase: str = "none"  # none | set | continuing | parked | paused | achieved | cleared | status
    text: str = ""
    reason: str = ""
    turn: int | None = None
    budget: int | None = None
    at: float = 0.0

    @property
    def live(self) -> bool:
        """Set, continuing or parked: the states the reactor's goal overlay shows."""
        return self.phase in ("set", "continuing", "parked")


@dataclass
class Background:
    id: str
    kind: str  # process | subagent
    label: str
    state: str  # running | done | failed
    detail: str = ""
    at: float = 0.0


@dataclass
class ContentPanel:
    """A ``hud.panel.show`` card: what the assistant pulled up, by kind."""

    id: str
    kind: str
    title: str
    payload: dict[str, Any]
    origin: str = "agent"
    rev: int = 0
    ttl_s: float | None = None
    at: float = 0.0

    @property
    def home(self) -> str:
        return panel_type_for(self.kind)


@dataclass
class Turn:
    running: bool = False
    got_token: bool = False
    text: str = ""
    reasoning: str = ""
    status: str = ""
    tools: list[Tool] = field(default_factory=list)
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def running_tools(self) -> int:
        return sum(1 for t in self.tools if t.state == "running")


def parse_goal(raw: Any, at: float = 0.0) -> Goal:
    """The goal's state read off the sentence Hermes sends (the web's ``goal.ts``)."""
    text = _s(raw).strip()
    if not text:
        return Goal(at=at)
    phase = "status"
    for glyph, name in _GOAL_GLYPHS:
        if text.startswith(glyph):
            phase = name
            break
    if _CLEARED.match(text):
        phase = "cleared"
    if phase == "status" and _NO_GOAL.match(text):
        phase = "none"
    turns = _TURNS.search(text)
    budget = _BUDGET.search(text)
    index = text.find(": ")
    reason = text[index + 2:].strip() if index >= 0 else ""
    if phase == "parked" and not reason:
        bracket = re.search(r"\((parked[^)]*)\)", text, re.IGNORECASE)
        reason = bracket.group(1) if bracket else re.sub(r"^⏳\s*", "", text)
    if phase == "paused" and not reason:
        parts = re.split(r"\s[—–-]\s", text)
        reason = " — ".join(parts[1:]).strip() if len(parts) > 1 else ""
    return Goal(
        phase=phase,
        text=text,
        reason=reason,
        turn=int(turns.group(1)) if turns else None,
        budget=int(turns.group(2)) if turns else (int(budget.group(1)) if budget else None),
        at=at,
    )


def approval_tool(payload: dict[str, Any]) -> str:
    """The tool an ``approval.request`` is about, or ``""`` for a shell-command prompt.

    A structured ``tool``/``tool_name`` wins if a later Hermes adds one; else the
    name is read out of ``command``, then out of ``pattern_key``'s head. Empty is
    an answer — Hermes's own dangerous-command gate asks about a command.
    """
    for key in ("tool", "tool_name"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    match = _TOOL_IN_COMMAND.match(_s(payload.get("command")).strip())
    if match:
        return match.group(1)
    key = _s(payload.get("pattern_key")).strip()
    return key[len(_PLUGIN_RULE):].split(":", 1)[0] if key.startswith(_PLUGIN_RULE) else ""


def panel_type_for(kind: str) -> str:
    """The layout panel a content kind lives in; an unknown kind reads as a document."""
    if kind == ACTIVITY_KIND:
        return "browser"
    return CONTENT_HOME.get(kind, "document")


@dataclass
class Hud:
    """The console's whole model of the session. Mutated by :meth:`apply`."""

    session_id: str | None = None
    stored_session_id: str | None = None
    title: str = ""
    model: str = ""
    provider: str = ""
    approval_mode: str = ""
    tools_count: int | None = None
    turn: Turn = field(default_factory=Turn)
    messages: list[Message] = field(default_factory=list)
    approvals: list[Approval] = field(default_factory=list)
    clarifies: list[Clarify] = field(default_factory=list)
    goal: Goal = field(default_factory=Goal)
    background: list[Background] = field(default_factory=list)
    panels: list[ContentPanel] = field(default_factory=list)
    #: The adapter said it cannot reach Hermes.
    backend_down: bool = False
    #: The last ``error`` event's message; cleared by the next turn.
    error: str | None = None
    last_seq: int = 0
    #: The socket is open (the contract's ``linkUp``; the socket and only the socket).
    link_up: bool = False
    #: ``GET /api/status``: the model's state and the kill switch.
    model_state: str = ""
    #: The adapter's one word and why (``status: {word, reason}``, T-0583), printed as
    #: ``status_label``; both empty from an adapter older than the field, and the model
    #: pane then words ``model_state`` itself as it always did.
    status_word: str = ""
    status_reason: str = ""
    warming_progress: float | None = None
    kill: bool = False
    #: Who is primary (ADR-0043), lower-case, from ``persona.switch`` or the status
    #: report's ``persona.active``; ``jarvis`` until either says. The ring's colour
    #: and the name in the prompt follow it -- a console that still said JARVIS
    #: after a switch to Ada is the defect the brief's acceptance names.
    persona: str = "jarvis"
    #: The layout document the plugin last applied, with its frame's context.
    layout: dict[str, Any] = field(default_factory=dict)
    layout_rev: int = 0
    layout_surface: str = ""
    layout_activity: str = ""
    layout_reason: str = ""
    layout_op: str = ""
    lock: bool = False
    undo_depth: int = 0
    #: A ``hud.layout.highlight``: the panel and when its rim stops pulsing.
    highlight: tuple[str, float] | None = None

    # --- reading ------------------------------------------------------------------

    def reactor_inputs(self) -> reactor.Inputs:
        """The contract's inputs, read the way the web reads them (``inputsFrom``)."""
        turn_running = self.turn.running
        return reactor.Inputs(
            kill=self.kill,
            linkUp=self.link_up,
            adapterError=self.backend_down,
            turnRunning=turn_running,
            errorEvent=bool(self.turn.error) or (bool(self.error) and not self.backend_down),
            attention=bool(self.approvals) or bool(self.clarifies) or self.goal.phase == "parked",
            warming=self.model_state in ("starting", "loading") and turn_running,
            speaking=False,
            listening=False,
            toolsRunning=self.turn.running_tools,
            goalActive=self.goal.live,
        )

    def reactor(self) -> reactor.Derived:
        return reactor.derive(self.reactor_inputs())

    def content_for(self, panel_type: str) -> list[ContentPanel]:
        """The content cards a layout panel of ``panel_type`` hosts, oldest first."""
        return [p for p in self.panels if p.home == panel_type]

    def homeless(self, placed_types: set[str]) -> list[ContentPanel]:
        """Content with no placed panel of its type — the chat shows these inline
        so nothing JARVIS pulled up is lost to the layout."""
        return [p for p in self.panels if p.home not in placed_types]

    def panel_titles(self) -> dict[str, str]:
        return {p["id"]: str(p.get("type", p["id"])) for p in self.layout.get("panels", [])
                if isinstance(p, dict) and p.get("id")}

    # --- status report --------------------------------------------------------------

    @property
    def status_label(self) -> str:
        """``word: reason``, the word alone with no reason, empty with no word. Never derived
        from the rest of the report (``tests/contracts/status-words.json``)."""
        return f"{self.status_word}: {self.status_reason}" if self.status_word and self.status_reason else self.status_word

    def apply_status(self, report: dict[str, Any] | None) -> None:
        """``GET /api/status``: the adapter's word, the model's state, its progress if any, the
        kill switch. ``None`` is the adapter not answering: the table's fixed ``no_answer``
        label, the one this console words itself, and everything else stays as last heard."""
        if report is None:
            self.status_word, self.status_reason = NO_ANSWER
            return
        said = report.get("status")
        said = said if isinstance(said, dict) else {}
        self.status_word, self.status_reason = _s(said.get("word")), _s(said.get("reason"))
        raw = report.get("model")
        model: dict[str, Any] = raw if isinstance(raw, dict) else {}
        self.model_state = _s(model.get("state"))
        raw = _num(model.get("progress"))
        self.warming_progress = None if raw is None else max(0.0, min(1.0, raw / 100 if raw > 1 else raw))
        kill = report.get("kill_switch")
        self.kill = bool(kill.get("engaged")) if isinstance(kill, dict) else False
        who = report.get("persona")
        active = _s(who.get("active")).lower() if isinstance(who, dict) else ""
        if active in PERSONAS:
            self.persona = active

    # --- the socket -------------------------------------------------------------------

    def link(self, up: bool) -> None:
        self.link_up = up

    def begin_turn(self, text: str, now: float) -> None:
        """``prompt.submit`` sent: the person's line lands and a turn starts."""
        self.error = None
        self.messages.append(Message("you", text, at=now))
        self.turn = Turn(running=True, started_at=now)
        self.messages.append(Message("jarvis", "", pending=True, at=now))

    def _ours(self, params: dict[str, Any]) -> bool:
        sid = params.get("session_id")
        return not sid or self.session_id is None or sid == self.session_id

    def _pending(self, now: float) -> Message:
        for m in reversed(self.messages):
            if m.role == "jarvis" and m.pending:
                return m
        m = Message("jarvis", "", pending=True, at=now)
        self.messages.append(m)
        return m

    def _tool_row(self, tool: Tool, now: float) -> None:
        for m in self.messages:
            if m.tool is not None and m.tool.id == tool.id:
                m.tool = tool
                return
        # The row goes before the pending reply, so the stream reads in order.
        pending = self._pending(now)
        index = self.messages.index(pending)
        self.messages.insert(index, Message("tool", tool.name, tool=tool, at=now))

    def apply(self, params: dict[str, Any], now: float) -> None:  # noqa: C901 — one switch, like hud.ts
        """One ``method: "event"`` frame's ``params``."""
        kind = _s(params.get("type"))
        if not kind:
            return
        raw = params.get("payload")
        p: dict[str, Any] = raw if isinstance(raw, dict) else {}
        seq = params.get("seq")
        if isinstance(seq, int):
            self.last_seq = seq
        if kind == "gateway.ready":
            # The adapter's "backend unavailable" goes with the outage it reported: kept, it
            # read as an error event once the backend answered, and the reactor sat at alert
            # until the next turn (T-1483). An `error` event's text is not touched here.
            if self.backend_down:
                self.error = None
            self.backend_down = False
            return
        if kind == "adapter.error":
            self.backend_down = True
            self.error = _s(p.get("error")) or "backend unavailable"
            return
        if not self._ours(params):
            return

        if kind == "session.info":
            self.model = _s(p.get("model")) or self.model
            self.provider = _s(p.get("provider")) or self.provider
            self.approval_mode = _s(p.get("approval_mode")) or self.approval_mode
            self.title = _s(p.get("title")) or self.title
            self.stored_session_id = _s(p.get("stored_session_id")) or self.stored_session_id
            tools = p.get("tools")
            if isinstance(tools, list):
                self.tools_count = len(tools)
            elif isinstance(tools, int):
                self.tools_count = tools
            if p.get("running") and not self.turn.running:
                self.turn = Turn(running=True, started_at=now)
        elif kind == "session.title":
            self.title = _s(p.get("title")) or self.title
        elif kind == "message.start":
            self.error = None
            if not self.turn.running:
                self.turn = Turn(running=True, started_at=now)
            self._pending(now)
        elif kind == "message.delta":
            delta = _s(p.get("text"))
            if delta:
                self.turn.running = True
                self.turn.got_token = True
                self.turn.text += delta
                self._pending(now).text += delta
        elif kind == "message.interim":
            if not p.get("already_streamed"):
                text = _s(p.get("text"))
                if text:
                    self.turn.text += text
                    self._pending(now).text += text
        elif kind in ("reasoning.delta", "reasoning.available"):
            self.turn.reasoning += _s(p.get("text"))
        elif kind == "thinking.delta":
            self.turn.status = _s(p.get("text")) or self.turn.status
        elif kind == "tool.start":
            tid = _s(p.get("tool_id")) or f"{kind}-{self.last_seq}"
            tool = Tool(tid, _s(p.get("name")) or "tool", _s(p.get("context")), _obj(p.get("args")),
                        started_at=now)
            self.turn.running = True
            self.turn.tools = [tool if t.id == tid else t for t in self.turn.tools] \
                if any(t.id == tid for t in self.turn.tools) else [*self.turn.tools, tool]
            self._tool_row(tool, now)
        elif kind == "tool.progress":
            tid = _s(p.get("tool_id"))
            line = _s(p.get("text") or p.get("message") or p.get("progress") or p.get("line"))
            for t in self.turn.tools:
                if t.id == tid and line:
                    t.progress = line
        elif kind == "tool.complete":
            tid = _s(p.get("tool_id")) or f"{kind}-{self.last_seq}"
            known: Tool | None = next((t for t in self.turn.tools if t.id == tid), None)
            error = p.get("error")
            error_text = None if error in (None, "") else _s(error)
            tool = Tool(
                tid,
                _s(p.get("name")) or (known.name if known else "tool"),
                _s(p.get("context")) or (known.context if known else ""),
                _obj(p.get("args")) if isinstance(p.get("args"), dict) else (known.args if known else {}),
                state="failed" if error_text else "done",
                progress=known.progress if known else "",
                duration=_num(p.get("duration")),
                error=error_text,
                result=None if p.get("result") is None else _s(p.get("result")),
                started_at=known.started_at if known else now,
                finished_at=now,
            )
            if known:
                self.turn.tools = [tool if t.id == tid else t for t in self.turn.tools]
            else:
                self.turn.tools.append(tool)
            self._tool_row(tool, now)
        elif kind == "message.complete":
            final = _s(p.get("text"))
            status = _s(p.get("status"))
            failed = bool(status) and status not in QUIET_ENDS
            self.turn.running = False
            self.turn.finished_at = now
            self.turn.text = self.turn.text or final
            self.turn.reasoning = self.turn.reasoning or _s(p.get("reasoning"))
            if failed:
                self.turn.error = status
            for m in self.messages:
                if m.role == "jarvis" and m.pending:
                    m.pending = False
                    m.text = m.text or final
                    if failed:
                        m.error = status
        elif kind == "approval.request":
            rid = _s(p.get("request_id"))
            if not rid:
                return
            offered = tuple(c for c in (p.get("choices") or []) if c in APPROVAL_CHOICES) \
                if isinstance(p.get("choices"), list) else ()
            command = _s(p.get("command"))
            raw_args = p.get("args")
            args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
            summary = ", ".join(f"{k}={_s(v)[:40]}" for k, v in list(args.items())[:3])
            # A plugin-gated call's ``command`` is the rendered label the title
            # already says; the shell gate's is the command itself, worth showing.
            detail = summary if _TOOL_IN_COMMAND.match(command.strip()) else (command or summary)
            request = Approval(
                rid, _s(params.get("session_id")) or _s(self.session_id),
                approval_tool(p) or "Command", detail,
                _s(p.get("reason") or p.get("description")), offered or APPROVAL_CHOICES, now,
            )
            if p.get("confirm") == "phone":
                # The adapter refuses an allow that the phone did not sign: offer only the no.
                request.on_phone, request.choices = True, ("deny",)
            seen = next((a for a in self.approvals if a.request_id == rid), None)
            if seen:
                seen.detail = request.detail or seen.detail
                seen.reason = request.reason or seen.reason
            else:
                self.approvals.append(request)
        elif kind in ("approval.resolved", "approval.cancelled"):
            rid = _s(p.get("request_id"))
            self.approvals = [a for a in self.approvals if a.request_id != rid]
        elif kind == "clarify.request":
            rid = _s(p.get("request_id"))
            if not rid:
                return
            options = tuple(_s(o) for o in p.get("options") or []) if isinstance(p.get("options"), list) else ()
            self.clarifies = [c for c in self.clarifies if c.request_id != rid]
            self.clarifies.append(Clarify(rid, _s(params.get("session_id")) or _s(self.session_id),
                                          _s(p.get("question")), options, now))
        elif kind == "status.update":
            if _s(p.get("kind")) == "goal":
                self.goal = parse_goal(p.get("text"), now)
            else:
                self.turn.status = _s(p.get("text"))
        elif kind == "hud.panel.show":
            pid = _s(p.get("id"))
            if not pid:
                return
            card = ContentPanel(
                pid, _s(p.get("kind")) or "text", _s(p.get("title")),
                _obj(p.get("payload")),
                _s(p.get("origin")) or "agent", int(p.get("rev") or 0), _num(p.get("ttl_s")), now,
            )
            held = next((c for c in self.panels if c.id == pid), None)
            if held is None:
                self.panels.append(card)
            elif card.rev >= held.rev:
                self.panels[self.panels.index(held)] = card
        elif kind == "hud.panel.update":
            pid = _s(p.get("id"))
            rev = int(p.get("rev") or 0)
            for card in self.panels:
                if card.id == pid and rev >= card.rev:
                    card.rev = rev
                    if isinstance(p.get("payload"), dict):
                        card.payload = _obj(p.get("payload"))
                    if p.get("title") is not None:
                        card.title = _s(p.get("title"))
        elif kind == "persona.switch":
            active = _s(p.get("active")).lower()
            if active in PERSONAS:
                self.persona = active
        elif kind.startswith("activity."):
            self._apply_activity(kind, p, now)
        elif kind == "hud.panel.dismiss":
            pid = _s(p.get("id"))
            self.panels = [] if pid == "*" else [c for c in self.panels if c.id != pid]
        elif kind == "subagent.start":
            sid = _s(p.get("id") or p.get("subagent_id") or p.get("task_id")) or f"subagent-{self.last_seq}"
            row = Background(sid, "subagent", _s(p.get("goal") or p.get("task") or p.get("name")) or "delegated task",
                             "running", _s(p.get("status")), now)
            self.background = [row if r.id == sid and r.kind == "subagent" else r for r in self.background] \
                if any(r.id == sid and r.kind == "subagent" for r in self.background) else [*self.background, row]
        elif kind == "subagent.complete":
            sid = _s(p.get("id") or p.get("subagent_id") or p.get("task_id"))
            status = _s(p.get("status")).lower()
            for r in self.background:
                if r.kind == "subagent" and (r.id == sid or not sid):
                    r.state = "failed" if ("fail" in status or "error" in status) else "done"
                    r.detail = _s(p.get("summary")) or r.detail
        elif kind.startswith("process."):
            verb = kind[len("process."):]
            pid = _s(p.get("pid") or p.get("id") or p.get("process_id")) or f"process-{self.last_seq}"
            label = _s(p.get("command") or p.get("name") or p.get("label")) or f"process {pid}"
            state = "running" if verb in ("start", "started", "spawn", "output") else \
                ("failed" if verb in ("fail", "failed", "error", "killed") else "done")
            existing = next((r for r in self.background if r.id == pid and r.kind == "process"), None)
            if existing:
                existing.state = state
                existing.detail = _s(p.get("text") or p.get("output") or p.get("status")) or existing.detail
            else:
                self.background.append(Background(pid, "process", label, state,
                                                  _s(p.get("text") or p.get("output") or p.get("status")), now))
        elif kind == "error":
            message = _s(p.get("message")) or "error"
            self.error = message
            self.turn.running = False
            self.turn.finished_at = now
            self.turn.error = message
            for m in self.messages:
                if m.role == "jarvis" and m.pending:
                    m.pending = False
                    m.error = message
        elif kind == "hud.layout.apply":
            rev = int(p.get("rev") or 0)
            if rev and rev < self.layout_rev:
                return  # a stale frame, dropped as the protocol says
            layout = p.get("layout")
            if isinstance(layout, dict):
                self.layout = layout
            self.layout_rev = rev or self.layout_rev
            self.layout_surface = _s(p.get("surface"))
            self.layout_activity = _s(p.get("activity"))
            self.layout_reason = _s(p.get("reason"))
            self.layout_op = _s(p.get("op"))
            depth = p.get("undo_depth")
            if isinstance(depth, int):
                self.undo_depth = depth
        elif kind == "hud.layout.highlight":
            pid = _s(p.get("panel"))
            duration = _num(p.get("duration_ms"))
            if pid:
                self.highlight = (pid, now + (duration or 0.0) / 1000.0)

    def _apply_activity(self, kind: str, p: dict[str, Any], now: float) -> None:
        """Fold one ``activity.*`` event into the one activity card.

        A start replaces the card; a step or a frame for a run this console did
        not see start opens one, so a late joiner still sees what is going on.
        The ended card stays, with its outcome, until the next run: a terminal
        is read back, and a card that vanished would take the answer with it.
        Redaction is the emitter's; nothing here un-masks or re-reads a page.
        """
        rid = _s(p.get("id"))
        card = next((c for c in self.panels if c.kind == ACTIVITY_KIND), None)
        if kind == "activity.start" or card is None or card.payload.get("id") != rid:
            if kind == "activity.end":
                return
            fresh = {"id": rid, "surface": _s(p.get("surface")) or "browser", "intent": _s(p.get("intent")),
                     "url": "", "title": "", "text": "", "frame_seq": 0, "steps": [], "status": "running"}
            self.panels = [c for c in self.panels if c.kind != ACTIVITY_KIND]
            card = ContentPanel(ACTIVITY_KIND, ACTIVITY_KIND, "", fresh, "agent", 0, None, now)
            self.panels.append(card)
        a = card.payload
        card.title = "Desktop" if a["surface"] == "desktop" else "Browser"
        if kind == "activity.step" and a["status"] == "running":
            step = {"seq": int(_num(p.get("seq")) or 0), "action": _s(p.get("action")),
                    "ok": p.get("ok") is not False, "error": _s(p.get("error"))}
            a["steps"] = sorted([s for s in a["steps"] if s["seq"] != step["seq"]] + [step],
                                key=lambda s: s["seq"])[-ACTIVITY_STEPS:]
        elif kind == "activity.frame":
            seq = int(_num(p.get("seq")) or 0)
            if seq >= a["frame_seq"]:
                a.update(frame_seq=seq, url=_s(p.get("url")) or a["url"],
                         title=_s(p.get("title")) or a["title"], text=_s(p.get("text")))
        elif kind == "activity.end" and a["status"] == "running":
            a["status"] = "failed" if _s(p.get("status")) == "failed" else "done"

    def load_history(self, rows: list[Any], now: float) -> None:
        """The transcript of a resumed session (``session.resume`` ``messages``), the
        web's ``fromHistory``: user and assistant rows in order, a tool row as the
        row between them. Replaces what was shown; a resume is a new attachment."""
        messages: list[Message] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            role = _s(row.get("role"))
            stamp = float(row["timestamp"]) if isinstance(row.get("timestamp"), (int, float)) else now
            if role == "user":
                messages.append(Message("you", _s(row.get("text")), at=stamp))
            elif role == "tool":
                error = row.get("error")
                tool = Tool(f"h{len(messages)}", _s(row.get("name")) or "tool", _s(row.get("context")),
                            _obj(row.get("args")), state="failed" if error else "done",
                            error=None if not error else _s(error), started_at=stamp, finished_at=stamp)
                messages.append(Message("tool", tool.name, tool=tool, at=stamp))
            elif role == "assistant":
                messages.append(Message("jarvis", _s(row.get("text")), at=stamp))
        self.messages = messages

    def apply_layout_state(self, state: dict[str, Any], surface: str) -> None:
        """``hud_layout @<sid> get``: the lock, the activity and this surface's layout,
        for a console that joins after the frames went by."""
        self.lock = bool(state.get("locked"))
        self.layout_activity = _s(state.get("activity")) or self.layout_activity
        depth = state.get("undo_depth")
        if isinstance(depth, int):
            self.undo_depth = depth
        layouts = state.get("layouts")
        if isinstance(layouts, dict):
            doc = layouts.get(surface) or layouts.get("web")
            if isinstance(doc, dict) and not self.layout:
                self.layout = doc
                self.layout_surface = surface if surface in layouts else "web"
