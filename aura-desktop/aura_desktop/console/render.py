"""A frame: the layout, its panels' content, the reactor and the strip, as rows.

Everything is drawn on a :class:`Canvas` of cells and serialised once per
frame, so a row is exactly ``cols`` visible cells whatever the colour tier —
the tests count them. Painting goes through :class:`theme.Theme` only, so no
colour and no glyph is typed here beyond the letters of the copy.

The light (GLASS_HUD §2.3, §7) is the border: the edges of a panel that face
the reactor are drawn in the state colour C — bold on the focused panel and
while a ``hud.layout.highlight`` runs — and the edges that face away in Mark,
so a panel reads as lit from the reactor's side and shadowed on the other. In
Tiled, Floating and Focus the reactor sits in the strip at the bottom-left, so
bottoms and (right of it) left edges are lit; in Ambient it is at the centre.

Every panel implements the four content states (§4 of the design skill) inside
its own border: **offline** when the socket is down, **error** when the turn
failed, **empty** with a sentence that says how content gets here, and
**loading** while a turn is running with nothing to show yet. A panel type the
console cannot draw (memory, settings, the kanban …) says so instead of showing
nothing — the phone and the web draw those.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Iterable

from .. import reactor
from . import panes
from .panes import Arrangement, Placed, Rect
from .state import PERSONA_NAMES, Approval, Background, ContentPanel, Hud, Tool
from .theme import Caps, Theme, visible_width
from ..theme import worn_name

__all__ = ["Canvas", "frame", "TITLES", "EMPTY", "NOT_HERE", "Frame"]

#: Panel type -> the title in its border (§11: name things by what the person understands).
TITLES: dict[str, str] = {
    "chat": "Chat", "timeline": "Tool calls", "approvals": "Approvals", "goals": "Goal",
    "kanban": "Tasks", "task": "Task", "memory": "Memory", "model": "Model", "voice": "Voice",
    "audit": "Audit", "settings": "Settings", "image": "Image", "browser": "Browser",
    "document": "Document", "telemetry": "Telemetry", "notifications": "Notifications",
    "house": "House", "automations": "Automations", "integrations": "Integrations",
    "jobs": "Jobs", "pairing": "Pairing", "gallery": "Gallery", "video": "Video", "audio": "Audio",
}

#: The empty state's sentence, per type: what would be here and how it gets here.
EMPTY: dict[str, str] = {
    "chat": "Ask for something. Replies and tool calls land here.",
    "timeline": "No tool calls yet. Ask for something and they land here.",
    "approvals": "Nothing is waiting on you.",
    "goals": "No goal. Say \"keep working on … until …\" to set one.",
    "model": "No model reported yet.",
    "notifications": "Nothing to report.",
    "image": "No image yet. Ask for one and it opens here.",
    "document": f"No document open. Ask {worn_name()} to show a file or a note.",
    "telemetry": "No readings yet. Ask for the GPUs or a room.",
    "browser": f"No page open. Ask {worn_name()} to look something up.",
    "house": "No device shown. Ask about a room or a light.",
    "task": "No task output yet.",
    "voice": "Voice is not on the console. Talk to the phone or the web console.",
    "video": "No video open. Ask for one; it plays on the web console or the phone.",
    "audio": "Nothing to play. Ask for a recording; it plays on the web console or the phone.",
}

#: Types the console does not draw: they say so, rather than sit blank.
NOT_HERE = "Not on the console. Open it on the web console or the phone."

#: The chrome's non-ASCII marks and what a terminal without Unicode gets instead.
ASCII_MARKS: dict[str, str] = {"…": "~", "—": "-", "“": '"', "”": '"'}

#: Copy for the four states that are not "empty".
OFFLINE = f"Couldn't reach {worn_name()}. Reconnecting; what you see is the last known state."
ERROR = "The turn failed: {error}"
LOADING = "Working on it."


@dataclass(frozen=True)
class Frame:
    """The rows of one frame, and the geometry they were drawn on."""

    rows: tuple[str, ...]
    arrangement: Arrangement


class Canvas:
    """Cells of (glyph, escape) that serialise to rows of exactly ``cols`` cells."""

    def __init__(self, cols: int, rows: int, theme: Theme, caps: Caps) -> None:
        self.cols = cols
        self.rows = rows
        self.theme = theme
        self.caps = caps
        self._cells: list[list[tuple[str, str] | None]] = [
            [(" ", "") for _ in range(cols)] for _ in range(rows)
        ]

    def put(self, x: int, y: int, text: str, fg: str | None = None, *,
            attrs: Iterable[str] = (), bg: str | None = None, limit: int | None = None) -> int:
        """Write ``text`` at (x, y), clipped to the canvas and to ``limit`` cells.
        Returns how many cells were written."""
        if y < 0 or y >= self.rows or not text:
            return 0
        esc = self.theme.sgr(fg, self.caps, attrs=attrs, bg=bg)
        if not self.caps.unicode:
            # The chrome's own marks have ASCII stand-ins; a reply's characters
            # are the model's and go through as they are.
            for mark, plain in ASCII_MARKS.items():
                text = text.replace(mark, plain)
        room = self.cols - x if limit is None else min(limit, self.cols - x)
        n = 0
        for ch in text[: max(room, 0)]:
            if x + n >= 0:
                self._cells[y][x + n] = (ch, esc)
            n += 1
        return n

    def put_raw(self, x: int, y: int, painted: str, width: int) -> None:
        """A string already painted (the reactor's rows) occupying ``width`` cells."""
        if y < 0 or y >= self.rows or x < 0 or x + width > self.cols or width <= 0:
            return
        self._cells[y][x] = (painted, "\x00")
        for i in range(1, width):
            self._cells[y][x + i] = None

    def serialise(self) -> list[str]:
        out: list[str] = []
        for row in self._cells:
            parts: list[str] = []
            current = ""
            for cell in row:
                if cell is None:
                    continue
                glyph, esc = cell
                if esc == "\x00":
                    if current:
                        parts.append(reactor.RESET)
                        current = ""
                    parts.append(glyph)
                    continue
                if esc != current:
                    parts.append(reactor.RESET if not esc else esc)
                    current = esc
                parts.append(glyph)
            if current:
                parts.append(reactor.RESET)
            out.append("".join(parts))
        return out


# --- text helpers ---------------------------------------------------------------


def _wrap(text: str, width: int) -> list[str]:
    if width <= 0:
        return []
    lines: list[str] = []
    for paragraph in str(text).splitlines() or [""]:
        wrapped = textwrap.wrap(paragraph, width, break_long_words=True, break_on_hyphens=False)
        lines.extend(wrapped or [""])
    return lines


def _clip(text: str, width: int) -> str:
    text = str(text).replace("\n", " ")
    if width <= 0:
        return ""
    return text if len(text) <= width else (text[: width - 1] + "…" if width > 1 else text[:1])


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    return f"{int(seconds * 1000)} ms" if seconds < 1 else f"{seconds:.1f} s"


Line = tuple[str, str]  # (text, role or "C:<palette>" or "")


def _plain_markdown(markdown: str) -> str:
    """Enough of markdown for a terminal: emphasis marks off, headings as lines."""
    out = []
    for line in str(markdown).splitlines():
        stripped = line.lstrip("#").strip() if line.startswith("#") else line
        out.append(stripped.replace("**", "").replace("`", ""))
    return "\n".join(out)


# --- one panel's lines --------------------------------------------------------------


def _tool_line(tool: Tool, width: int, live: str) -> tuple[str, str]:
    state = {"running": "running", "done": "done", "failed": "failed"}[tool.state]
    when = _duration(tool.duration) if tool.state != "running" else tool.progress
    name = _clip(tool.name, max(width - 20, 8))
    text = f"{live}{name}"
    tail = f"{state} {when}".strip()
    pad = max(width - len(text) - len(tail) - 1, 1)
    role = {"running": "C:accent", "done": "C:ok", "failed": "C:danger-text"}[tool.state]
    return (_clip(text + " " * pad + tail, width), role)


def _background_line(row: Background, width: int, live: str) -> tuple[str, str]:
    label = _clip(row.label, max(width - 18, 8))
    tail = "running" if row.state == "running" else row.state
    text = f"{live}{label}"
    pad = max(width - len(text) - len(tail) - 1, 1)
    role = {"running": "C:accent", "done": "C:ok", "failed": "C:danger-text"}[row.state]
    return (_clip(text + " " * pad + tail, width), role)


def _content_lines(card: ContentPanel, width: int, caps: Caps) -> list[Line]:
    """A ``hud.panel.show`` card as lines, by kind (the content contract's payloads)."""
    p = card.payload
    kind = card.kind
    lines: list[Line] = []
    if kind == "text":
        lines += [(l, "body") for l in _wrap(_plain_markdown(p.get("markdown", "")), width)]
    elif kind in ("code", "terminal"):
        if kind == "terminal" and p.get("command"):
            lines.append((_clip(f"$ {p['command']}", width), "mono"))
        for l in str(p.get("text") or p.get("output") or "").splitlines():
            lines.append((_clip(l, width), "mono"))
        if kind == "terminal" and p.get("exit_code") not in (None, 0):
            lines.append((f"exit {p['exit_code']}", "C:danger-text"))
        if p.get("path"):
            lines.append((_clip(str(p["path"]), width), "mono-faint"))
    elif kind == "table":
        columns = [str(c) for c in p.get("columns", [])]
        rows = [[str(c) for c in r] for r in p.get("rows", []) if isinstance(r, list)]
        if columns:
            widths = [max([len(c)] + [len(r[i]) for r in rows if i < len(r)]) for i, c in enumerate(columns)]
            def fmt(cells: list[str]) -> str:
                return "  ".join(cells[i].ljust(widths[i]) for i in range(len(columns)) if i < len(cells))
            lines.append((_clip(fmt(columns), width), "label"))
            lines += [(_clip(fmt(r), width), "mono") for r in rows]
    elif kind == "chart":
        unit = str(p.get("unit") or "")
        for series in p.get("series", []):
            points = [pt for pt in series.get("points", []) if isinstance(pt, list) and len(pt) == 2]
            last = points[-1][1] if points else ""
            lines.append((_clip(f"{series.get('name', '')}  {last} {unit}".rstrip(), width), "mono"))
    elif kind in ("image", "camera"):
        head = str(p.get("alt") or p.get("camera") or card.title)
        lines.append((_clip(head, width), "body"))
        if p.get("taken_at"):
            lines.append((_clip(str(p["taken_at"]), width), "mono-faint"))
        lines.append((_clip("Opens on the web console; its address is", width), "secondary"))
        lines.append((_clip(str(p.get("url", "")), width), "link"))
    elif kind == "web":
        lines.append((_clip(str(p.get("title") or card.title), width), "body"))
        lines.append((_clip(str(p.get("url", "")), width), "link"))
    elif kind in ("video", "audio", "document"):
        # No pretending: a terminal plays nothing, so the card says what the
        # file is, where it came from, and where it is.
        lines.append((_clip(str(p.get("title") or card.title), width), "body"))
        raw_prov = p.get("provenance")
        prov: dict[str, object] = raw_prov if isinstance(raw_prov, dict) else {}
        origin = "  ".join(str(v) for v in (prov.get("tool"), prov.get("source_url")) if v)
        lines.append((_clip("  ".join(s for s in (str(p.get("mime") or ""), origin) if s), width), "mono-faint"))
        lines.append((_clip("Plays on the web console or the phone; its address is", width), "secondary"))
        lines.append((_clip(str(p.get("url", "")), width), "link"))
    elif kind == "activity":
        # What the browser or the desktop is doing, as text: the terminal draws
        # no picture, so the frame is its page snapshot and the steps its trail.
        status = str(p.get("status") or "running")
        word = {"running": f"Live  {p.get('surface', '')}", "done": "Done", "failed": "Failed"}.get(status, status)
        role = {"running": "C:accent", "failed": "C:danger-text"}.get(status, "secondary")
        lines.append((_clip("  ".join(s for s in (word, str(p.get("title") or p.get("intent") or "")) if s), width), role))
        if p.get("url"):
            lines.append((_clip(str(p["url"]), width), "link"))
        for l in str(p.get("text") or "").splitlines()[:4]:
            lines.append((_clip(l, width), "mono-faint"))
        for step in p.get("steps", []) if isinstance(p.get("steps"), list) else []:
            mark = "x" if not step.get("ok", True) else ">"
            lines.append((_clip(f"{mark} {step.get('seq', '')}  {step.get('action', '')}", width),
                          "C:danger-text" if not step.get("ok", True) else "mono"))
    elif kind == "device":
        lines.append((_clip(f"{p.get('name', '')}  {p.get('state', '')}", width), "body"))
        raw_attrs = p.get("attributes")
        attrs: dict[str, object] = raw_attrs if isinstance(raw_attrs, dict) else {}
        for key, value in list(attrs.items())[:3]:
            lines.append((_clip(f"{key} {value}", width), "secondary"))
    elif kind == "telemetry":
        for gpu in p.get("gpus", []) if isinstance(p.get("gpus"), list) else []:
            lines.append((_clip(
                f"GPU {gpu.get('gpu', '?')}  util {gpu.get('utilization', '?')} %  "
                f"vram {gpu.get('vram_pct', '?')} %  {gpu.get('temperature', '?')} °C  "
                f"{gpu.get('power', '?')} W", width), "mono"))
        for metric in p.get("metrics", []) if isinstance(p.get("metrics"), list) else []:
            lines.append((_clip(f"{metric.get('name', '')}  {metric.get('value', '')} {metric.get('unit', '')}".rstrip(),
                                width), "mono"))
    elif kind == "goal":
        lines.append((_clip(str(p.get("text") or p.get("phase") or ""), width), "body"))
    elif kind == "notice":
        level = str(p.get("level") or "info")
        role = {"error": "C:danger-text", "warn": "C:warn"}.get(level, "body")
        lines += [(l, role) for l in _wrap(str(p.get("text", "")), width)]
    elif kind in ("approval", "clarify"):
        lines += [(l, "body") for l in _wrap(str(p.get("reason") or p.get("question") or ""), width)]
    else:
        lines += [(l, "body") for l in _wrap(str(p), width)]
    return lines


def _cards(cards: list[ContentPanel], width: int, caps: Caps, theme: Theme) -> list[Line]:
    lines: list[Line] = []
    rule = theme.divider(caps)
    for card in cards:
        head = f"{rule * 2} {card.title or card.kind} "
        lines.append((_clip(head + rule * max(width - len(head), 0), width), "C:line-soft"))
        lines += _content_lines(card, width, caps)
    return lines


def _chat_lines(hud: Hud, width: int, caps: Caps, theme: Theme, composer: str,
                inline: list[ContentPanel]) -> list[Line]:
    lines: list[Line] = []
    live = theme.live(caps)
    replies = [m for m in hud.messages if m.role == "jarvis" and not m.pending]
    last_reply = replies[-1] if replies else None
    for m in hud.messages:
        if m.role == "you":
            wrapped = _wrap(m.text, max(width - 5, 1))
            lines += [((("you  " if i == 0 else "     ") + l), "secondary" if i else "label")
                      for i, l in enumerate(wrapped)]
        elif m.role == "tool" and m.tool is not None:
            lines.append(_tool_line(m.tool, width, live))
        elif m.role == "jarvis":
            if m.text:
                role = "display" if m is last_reply else "body"
                lines += [(l, role) for l in _wrap(m.text, width)]
            elif m.pending:
                lines.append((_clip(hud.turn.status or LOADING, width), "secondary"))
            if m.error:
                lines.append((_clip(ERROR.format(error=m.error), width), "C:danger-text"))
        if m.role != "tool":
            lines.append(("", ""))
    if inline:
        lines += _cards(inline, width, caps, theme)
    return lines


def _timeline_lines(hud: Hud, width: int, caps: Caps, theme: Theme) -> list[Line]:
    live = theme.live(caps)
    lines: list[Line] = [_tool_line(t, width, live) for t in hud.turn.tools]
    lines += [_background_line(r, width, live) for r in hud.background]
    return lines


def _approval_lines(a: Approval, width: int) -> list[Line]:
    lines: list[Line] = [(_clip(a.tool, width), "title")]
    if a.detail:
        lines += [(l, "mono") for l in _wrap(a.detail, width)[:3]]
    if a.reason:
        lines += [(l, "secondary") for l in _wrap(a.reason, width)[:3]]
    keys = "  ".join(f"[{c[0]}]{c[1:]}" for c in a.choices)
    if a.on_phone:
        keys = f"confirm with your fingerprint on the phone  {keys}"
    lines.append((_clip(keys, width), "C:warn"))
    return lines


def _approvals_lines(hud: Hud, width: int) -> list[Line]:
    lines: list[Line] = []
    for a in hud.approvals:
        lines += _approval_lines(a, width) + [("", "")]
    for c in hud.clarifies:
        lines += [(l, "title") for l in _wrap(c.question, width)]
        for i, option in enumerate(c.options, 1):
            lines.append((_clip(f"[{i}] {option}", width), "C:warn"))
        lines.append(("", ""))
    return lines[:-1] if lines else lines


def _goal_lines(hud: Hud, width: int) -> list[Line]:
    g = hud.goal
    if g.phase in ("none", "cleared"):
        return []
    word = {"set": "Set", "continuing": "Continuing", "parked": "Parked, waiting on you",
            "paused": "Paused", "achieved": "Achieved", "status": "Status"}.get(g.phase, g.phase)
    colour = {"parked": "C:warn", "achieved": "C:ok"}.get(g.phase, "title")
    lines: list[Line] = [(word, colour)]
    lines += [(l, "body") for l in _wrap(g.reason or g.text, width)]
    if g.turn is not None and g.budget is not None:
        lines.append((f"turn {g.turn} of {g.budget}", "secondary"))
    elif g.budget is not None:
        lines.append((f"{g.budget} turns", "secondary"))
    if g.live or g.phase == "paused":
        lines.append((("[r]esume" if g.phase == "paused" else "[p]ause") + "  [c]lear", "C:accent-deep"))
    return lines


def _model_lines(hud: Hud, width: int) -> list[Line]:
    if not hud.model and not hud.model_state and not hud.status_label:
        return []
    lines: list[Line] = [(_clip(hud.model or "model", width), "figure")]
    if hud.provider:
        lines.append((_clip(hud.provider, width), "secondary"))
    state = hud.model_state
    if hud.status_label:
        # The adapter's word, as the console and the phone print it (T-0583); the colour is the word's.
        tone = {"ready": "C:ok", "down": "C:danger-text"}.get(hud.status_word, "C:warming")
        pct = f" {int(hud.warming_progress * 100)} %" if hud.warming_progress is not None else ""
        lines.append((_clip(hud.status_label + pct, width), tone))
    elif state in ("starting", "loading"):
        pct = f" {int(hud.warming_progress * 100)} %" if hud.warming_progress is not None else ""
        lines.append((f"warming up{pct}", "C:warming"))
    elif state == "ready":
        lines.append(("ready", "C:ok"))
    elif state in ("unreachable", "stopping"):
        lines.append((state, "C:danger-text"))
    elif state:
        lines.append((state, "secondary"))
    if hud.approval_mode:
        lines.append((_clip(f"approvals {hud.approval_mode}", width), "secondary"))
    if hud.tools_count is not None:
        lines.append((f"tools {hud.tools_count}", "secondary"))
    return lines


def _notification_lines(hud: Hud, width: int, caps: Caps, theme: Theme) -> list[Line]:
    lines: list[Line] = []
    if hud.error and not hud.backend_down:
        lines += [(l, "C:danger-text") for l in _wrap(hud.error, width)]
    if hud.layout_reason:
        lines.append((_clip(f"Layout: {hud.layout_reason}", width), "secondary"))
    lines += _cards(list(reversed(hud.content_for("notifications"))), width, caps, theme)
    return lines


def _lines_for(placed: Placed, hud: Hud, width: int, caps: Caps, theme: Theme, composer: str,
               inline: list[ContentPanel]) -> list[Line]:
    t = placed.type
    if t == "chat":
        return _chat_lines(hud, width, caps, theme, composer, inline)
    if t == "timeline":
        return _timeline_lines(hud, width, caps, theme)
    if t == "approvals":
        return _approvals_lines(hud, width)
    if t == "goals":
        return _goal_lines(hud, width) + _cards(hud.content_for("goals"), width, caps, theme)
    if t == "model":
        return _model_lines(hud, width)
    if t == "notifications":
        return _notification_lines(hud, width, caps, theme)
    if t in ("document", "telemetry", "image", "browser", "house", "task", "video", "audio"):
        return _cards(hud.content_for(t), width, caps, theme)
    return []


# --- drawing --------------------------------------------------------------------


def _facing(rect: Rect, light: tuple[float, float]) -> set[str]:
    """Which edges of ``rect`` face the reactor at ``light``."""
    lx, ly = light
    out: set[str] = set()
    if ly >= rect.bottom:
        out.add("bottom")
    elif ly <= rect.y:
        out.add("top")
    if lx <= rect.x:
        out.add("left")
    elif lx >= rect.right:
        out.add("right")
    return out


def _draw_box(canvas: Canvas, rect: Rect, grade: str, title: str, *, facing: set[str],
              colour: str, lit: bool, right: str = "") -> Rect:
    """A bordered box; returns its interior. ``lit`` bolds the facing edges.

    The title stays on the box's own top border rather than moving to a
    separate header row with a rule under it, as the web's ``.bar`` does
    (ADR-0031, "small capitals, dim, a rule under it"): that would cost every
    panel two rows of content height and risk the sizing this surface already
    tunes to today's overhead, for a purely visual difference — the border a
    panel already draws reads as the rule under an embedded title just as
    well. Small capitals and dim are the two parts an ANSI border can carry.
    """
    theme, caps = canvas.theme, canvas.caps
    box = theme.box(grade, caps)
    far = theme.data["borders"]["far"]
    attrs = theme.focused_attrs if lit else ()

    def edge(name: str) -> tuple[str, tuple[str, ...]]:
        return (colour, attrs) if name in facing else (far, ())

    top_c, top_a = edge("top")
    bottom_c, bottom_a = edge("bottom")
    left_c, left_a = edge("left")
    right_c, right_a = edge("right")
    x, y, w, h = rect.x, rect.y, rect.w, rect.h
    if w < 2 or h < 2:
        return Rect(x, y, 0, 0)
    canvas.put(x, y, box.tl + box.h * (w - 2) + box.tr, top_c, attrs=top_a)
    canvas.put(x, y + h - 1, box.bl + box.h * (w - 2) + box.br, bottom_c, attrs=bottom_a)
    for row in range(y + 1, y + h - 1):
        canvas.put(x, row, box.v, left_c, attrs=left_a)
        canvas.put(x + w - 1, row, box.v, right_c, attrs=right_a)
    if title and w > 6:
        # Sentence case, dim, matching the web's panel title (HUD.md §3). It was
        # upper-cased here as the terminal's reading of "small capitals" — and
        # the web's tracked capitals went in v3 for the same reason they were
        # never right here: an all-caps panel name is the eyebrow label the
        # brief names as a generic tell. The console is on the same design
        # system, so it says the same words the same way.
        label = f" {_clip(title, w - 6)} "
        canvas.put(x + 2, y, label, *theme.role("title")[:1], attrs=theme.role("title")[1])
    if right and w > len(right) + 8:
        canvas.put(x + w - 3 - len(right), y, f" {right} ", *theme.role("figure")[:1],
                   attrs=theme.role("figure")[1])
    return Rect(x + 1, y + 1, w - 2, h - 2)


def _write(canvas: Canvas, inner: Rect, lines: list[Line], *, bottom: bool = False) -> None:
    """Lines into an interior, clipped; ``bottom`` keeps the tail (the stream)."""
    if inner.w <= 0 or inner.h <= 0:
        return
    shown = lines[-inner.h:] if bottom else lines[: inner.h]
    theme = canvas.theme
    for i, (text, role) in enumerate(shown):
        if not text:
            continue
        if role.startswith("C:"):
            canvas.put(inner.x, inner.y + i, text, role[2:], limit=inner.w)
        elif role:
            colour, attrs = theme.role(role)
            canvas.put(inner.x, inner.y + i, text, colour, attrs=attrs, limit=inner.w)
        else:
            canvas.put(inner.x, inner.y + i, text, limit=inner.w)


def _state_lines(placed: Placed, hud: Hud, lines: list[Line], width: int) -> list[Line]:
    """The four states, decided once for every panel type."""
    if hud.backend_down or not hud.link_up:
        return [(l, "C:warn") for l in _wrap(OFFLINE, width)] + ([("", "")] + lines if lines else [])
    if lines:
        return lines
    if placed.type == "chat" and hud.turn.running:
        return [(LOADING, "secondary")]
    if placed.type in ("timeline", "goals") and hud.turn.running and placed.type == "timeline":
        return [(LOADING, "secondary")]
    if hud.turn.error and placed.type in ("chat", "notifications"):
        return [(l, "C:danger-text") for l in _wrap(ERROR.format(error=hud.turn.error), width)]
    sentence = EMPTY.get(placed.type, NOT_HERE)
    return [(l, "secondary") for l in _wrap(sentence, width)]


def _readout(kind: str, hud: Hud) -> str:
    """The one value a panel says about itself, on the right of its own border.

    The web's panel header carries the same thing (Reactor II wave 3, the mockup's
    ``.panel h2 b``) and this is where a border can put it. Terser than the web's,
    because a border is not a header row: ``5`` where the console says ``5 calls``,
    and only where the number is the panel's own — the terminal talks to Hermes and
    not to the house, so there is no device or reading count to say.
    """
    if kind == "timeline" and hud.turn.tools:
        return str(len(hud.turn.tools))
    if kind == "approvals" and (hud.approvals or hud.clarifies):
        return str(len(hud.approvals) + len(hud.clarifies))
    if kind == "chat":
        sid = hud.session_id or hud.stored_session_id or ""
        return sid[-8:] if sid else ""
    if kind == "goals" and hud.goal.turn is not None and hud.goal.budget:
        return f"{hud.goal.turn}/{hud.goal.budget}"
    if kind == "model":
        return hud.status_word or hud.model_state or ""
    return ""


def _draw_panel(canvas: Canvas, placed: Placed, hud: Hud, light: tuple[float, float], colour: str,
                now: float, composer: str, inline: list[ContentPanel]) -> None:
    highlighted = hud.highlight is not None and hud.highlight[0] == placed.pid and now < hud.highlight[1]
    facing = {"top", "bottom", "left", "right"} if highlighted else _facing(placed.rect, light)
    right = _readout(placed.type, hud)
    title = TITLES.get(placed.type, placed.type)
    if placed.pinned:
        title += " (pinned)"
    inner = _draw_box(canvas, placed.rect, "panel", title, facing=facing, colour=colour,
                      lit=placed.focused or highlighted, right=right)
    if inner.w <= 0 or inner.h <= 0:
        return
    body = Rect(inner.x + 1, inner.y, inner.w - 2, inner.h)
    lines = _lines_for(placed, hud, body.w, canvas.caps, canvas.theme, composer, inline)
    lines = _state_lines(placed, hud, lines, body.w)
    if placed.type == "chat":
        # The composer is the panel's last row, under a divider (§9.7).
        rule = canvas.theme.divider(canvas.caps)
        stream = Rect(body.x, body.y, body.w, max(body.h - 2, 0))
        _write(canvas, stream, lines, bottom=True)
        canvas.put(inner.x, inner.y + inner.h - 2, rule * inner.w, "line-soft")
        # The name in the prompt is the primary's (ADR-0043): "Ada> " after a
        # switch, never a stale "JARVIS> ".
        prompt = f"{PERSONA_NAMES.get(hud.persona, '{worn_name()}')}> " + composer
        canvas.put(body.x, inner.y + inner.h - 1, _clip(prompt, body.w - 1), "text-bright")
        if placed.focused:
            canvas.put(body.x + min(len(prompt), body.w - 1), inner.y + inner.h - 1, "_", "accent",
                       attrs=("bold",))
    else:
        _write(canvas, body, lines)


def _draw_rail(canvas: Canvas, rail: panes.Rail, hud: Hud, light: tuple[float, float], colour: str) -> None:
    inner = _draw_box(canvas, rail.rect, "pane", "Panels", facing=_facing(rail.rect, light),
                      colour=colour, lit=False)
    body = Rect(inner.x + 1, inner.y, inner.w - 2, inner.h)
    titles = hud.panel_titles()
    lines: list[Line] = []
    for pid in rail.pids:
        ptype = titles.get(pid, pid)
        lines.append((_clip(TITLES.get(ptype, ptype), body.w), "title"))
        summary = _summary(ptype, hud)
        if summary:
            lines.append((_clip(summary, body.w), "secondary"))
        lines.append(("", ""))
    _write(canvas, body, lines)


def _summary(ptype: str, hud: Hud) -> str:
    if ptype == "timeline":
        n = len(hud.turn.tools)
        return f"{n} tool call{'s' if n != 1 else ''}" if n else "no tool calls"
    if ptype == "approvals":
        n = len(hud.approvals) + len(hud.clarifies)
        return f"{n} waiting" if n else "nothing waiting"
    if ptype == "goals":
        return hud.goal.phase if hud.goal.phase != "none" else "no goal"
    if ptype == "model":
        return hud.model or hud.model_state
    if ptype == "chat":
        replies = [m.text for m in hud.messages if m.role == "jarvis" and m.text]
        return replies[-1] if replies else ""
    cards = hud.content_for(ptype)
    return cards[-1].title if cards else ""


#: States whose colour is information; every other state wears the persona's hue.
_LOUD_STATES = frozenset({reactor.State.LISTENING, reactor.State.THINKING, reactor.State.ATTENTION,
                          reactor.State.WARMING, reactor.State.ALERT, reactor.State.KILL, reactor.State.OFFLINE})


def _ring_colour(theme: Theme, state: reactor.State, persona: str) -> str:
    """The ring's palette name: the persona's hue while the room is calm (ADR-0043),
    the state's own whenever that colour says something."""
    if persona != "jarvis" and state not in _LOUD_STATES and f"persona-{persona}" in theme.palette:
        return f"persona-{persona}"
    return theme.reactor_colour(state)


def _strip(canvas: Canvas, arrangement: Arrangement, hud: Hud, derived: reactor.Derived,
           ring: list[str], caps: Caps, theme: Theme) -> None:
    """The bottom strip: the ring, the state word, the counts, the keys."""
    strip = arrangement.strip
    if strip.h <= 0:
        return
    colour = _ring_colour(theme, derived.state, hud.persona)
    r = arrangement.reactor
    for i, row in enumerate(ring[: strip.h]):
        canvas.put_raw(r.x, r.y + i, row, 3)
    x = r.right + 2
    word = reactor.WORDS[derived.state]
    if derived.goal and derived.state is not reactor.State.GOAL:
        word += ", " + reactor.WORDS[reactor.State.GOAL].lower()
    n = canvas.put(x, strip.y, word, colour, attrs=("bold", *theme.state_attrs(derived.state)))
    facts: list[str] = []
    if hud.turn.tools:
        running = hud.turn.running_tools
        facts.append(f"Tool calls {len(hud.turn.tools)}" + (f", {running} running" if running else ""))
    if hud.goal.turn is not None and hud.goal.budget is not None:
        facts.append(f"turn {hud.goal.turn} of {hud.goal.budget}")
    if hud.approvals or hud.clarifies:
        facts.append(f"{len(hud.approvals) + len(hud.clarifies)} waiting on you")
    if arrangement.hidden:
        facts.append(f"{len(arrangement.hidden)} hidden")
    canvas.put(x + n + 2, strip.y, "   ".join(facts), "text-dim", limit=strip.w - x - n - 2)
    if strip.h > 1:
        activity = hud.layout_activity or "default"
        mode = arrangement.mode
        second = f"{activity}, {mode}" + ("  Locked" if hud.lock else "")
        keys = "alt-1..9 focus  alt-l layouts  alt-k lock  alt-z undo  Esc dismiss"
        canvas.put(x, strip.y + 1, second, "text-dim", limit=strip.w - x)
        kx = strip.right - len(keys) - 1
        if kx > x + len(second) + 2:
            canvas.put(kx, strip.y + 1, keys, "text-faint")


def _ambient(canvas: Canvas, arrangement: Arrangement, hud: Hud, derived: reactor.Derived,
             ring: list[str], caps: Caps, theme: Theme) -> None:
    """§9.5: the reactor at the centre, the last exchange beneath, one strip pane."""
    r = arrangement.reactor
    colour = theme.reactor_colour(derived.state)
    for i, row in enumerate(ring[:2]):
        canvas.put_raw(r.x, r.y + i, row, 3)
    word = reactor.WORDS[derived.state]
    canvas.put(max((canvas.cols - len(word)) // 2, 0), r.bottom, word, colour,
               attrs=("bold", *theme.state_attrs(derived.state)))
    width = min(60, canvas.cols - 4)
    left = max((canvas.cols - width) // 2, 0)
    y = r.bottom + 2
    yours = [m.text for m in hud.messages if m.role == "you"]
    replies = [m for m in hud.messages if m.role == "jarvis" and (m.text or m.pending)]
    if yours:
        for line in _wrap(yours[-1], width)[:2]:
            canvas.put(left, y, line, *theme.role("label")[:1], attrs=theme.role("label")[1])
            y += 1
    if replies:
        last = replies[-1]
        text = last.text or hud.turn.status or LOADING
        for line in _wrap(text, width)[:4]:
            canvas.put(left, y, line, *theme.role("display")[:1], attrs=theme.role("display")[1])
            y += 1
    strip = arrangement.strip
    if strip.h >= 3 and strip.w >= 8:
        inner = _draw_box(canvas, Rect(strip.x + 1, strip.y, strip.w - 2, strip.h), "pane", "",
                          facing={"top"}, colour=colour, lit=False)
        glyph = reactor.badge(derived.state, caps)
        canvas.put_raw(inner.x + 1, inner.y, glyph, visible_width(glyph))
        tail = f"Tool calls {len(hud.turn.tools)}" if hud.turn.tools else (
            f"{len(hud.approvals) + len(hud.clarifies)} waiting on you" if hud.approvals or hud.clarifies else "")
        if tail:
            canvas.put(inner.right - len(tail) - 1, inner.y, tail, "text-dim")


def frame(hud: Hud, cols: int, rows: int, theme: Theme, caps: Caps, *, now: float = 0.0,
          animator: reactor.Animator | None = None, focused: str | None = None,
          composer: str = "", reduced: bool = False) -> Frame:
    """One frame of the console for ``hud`` on a ``cols`` × ``rows`` terminal.

    ``animator`` supplies the reactor's rows on its own clock (the app's); a
    test passes none and gets the reactor at ``now``. ``focused`` is the pane
    with the keyboard; ``composer`` the line being typed.
    """
    arrangement = panes.arrange(hud.layout, cols, rows, focused)
    canvas = Canvas(cols, rows, theme, caps)
    derived = hud.reactor()
    if animator is not None:
        animator.set_state(derived.state, derived.goal)
        animator.segments = max(1, len(hud.turn.tools))
        animator.progress = hud.warming_progress
        ring = animator.frame_at()
    else:
        ring = reactor.frame(derived.state, now, goal=derived.goal, progress=hud.warming_progress,
                             segments=max(1, len(hud.turn.tools)), caps=caps, reduced=reduced)
    colour = theme.reactor_colour(derived.state)
    if arrangement.mode == "ambient":
        _ambient(canvas, arrangement, hud, derived, ring, caps, theme)
        return Frame(tuple(canvas.serialise()), arrangement)

    light = arrangement.reactor.centre
    placed_types = {p.type for p in arrangement.placed}
    inline = hud.homeless(placed_types)
    for placed in arrangement.placed:
        _draw_panel(canvas, placed, hud, light, colour, now, composer,
                    inline if placed.type == "chat" else [])
    if arrangement.rail is not None:
        _draw_rail(canvas, arrangement.rail, hud, light, colour)
    if not arrangement.placed and arrangement.rail is None:
        message = "No panels on this layout. alt-l picks one." if hud.layout else \
            f"Waiting for a layout from {worn_name()}. alt-l picks one."
        canvas.put(2, 1, message, "text-dim", limit=cols - 4)
    _strip(canvas, arrangement, hud, derived, ring, caps, theme)
    return Frame(tuple(canvas.serialise()), arrangement)


def rows_plain(rows: Iterable[str]) -> list[str]:
    """Rows with their escapes removed: the text-only capture the docs keep."""
    out = []
    for row in rows:
        text = []
        i = 0
        while i < len(row):
            if row[i] == "\x1b":
                j = row.find("m", i)
                i = (j + 1) if j >= 0 else len(row)
                continue
            text.append(row[i])
            i += 1
        out.append("".join(text))
    return out


def width_ok(rows: Iterable[str], cols: int) -> bool:
    """Every row is exactly ``cols`` cells: the invariant the tests assert."""
    return all(visible_width(r) == cols for r in rows)
