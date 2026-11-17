"""What the surface a person is on can actually render, and where to send what it cannot.

The media brief opens with the failure this prevents:

    "JARVIS must know, before it plans, what the surface I'm on can actually
    render."

and names the cost of not knowing:

    "when the current surface can't present something, do not degrade it into a
    novelty representation ... ASCII-art video, frame slideshows and similar are
    only produced if I explicitly ask for them. **The reasoning budget spent
    deciding that is itself the bug.**"

That last sentence is the design. A model that has to work out whether a terminal
can play video has already lost, whatever it concludes — so the answer is a
lookup, injected into the prompt as one line, and the routing rule is a function
rather than a judgement.

Everything here is pure. The registry is a table, the routing is a decision over
that table, and a client's reported capabilities merge into it conservatively:
an unknown surface gets the *least* capable profile, because guessing generously
produces exactly the novelty representation the brief is trying to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any
from typing import Iterable

MB = 1024 * 1024


@dataclass(frozen=True)
class Surface:
    """One place a person might be, and what it can put in front of them."""

    name: str
    #: Still images, shown inline rather than as a link.
    images: bool = False
    #: Moving pictures *with sound*. A surface that can show frames but not play
    #: audio is not a video surface -- that distinction is the whole point.
    video: bool = False
    audio: bool = False
    #: PDFs and office formats, rendered rather than downloaded.
    documents: bool = False
    #: Live HTML. Deliberately separate from `images`: a surface that renders
    #: arbitrary HTML is running someone's script.
    interactive: bool = False
    #: Can hand the person a file to keep.
    downloads: bool = True
    notifications: bool = False
    #: The largest thing worth sending here, in bytes. Not a hard limit — a
    #: practical one, past which the experience is bad even though it "works".
    max_bytes: int = 8 * MB

    def can(self, kind: str) -> bool:
        return bool(getattr(self, kind, False))


#: The shipped table. `console` is deliberately the poorest: it is a terminal.
REGISTRY: dict[str, Surface] = {
    "web": Surface("web", images=True, video=True, audio=True, documents=True,
                   interactive=True, notifications=True, max_bytes=512 * MB),
    "desktop": Surface("desktop", images=True, video=True, audio=True, documents=True,
                       interactive=True, notifications=True, max_bytes=512 * MB),
    "android": Surface("android", images=True, video=True, audio=True, documents=True,
                       interactive=False, notifications=True, max_bytes=256 * MB),
    # A terminal. It can name a file and it can hand one over; it cannot show one.
    "console": Surface("console", downloads=True, max_bytes=1 * MB),
    "telegram": Surface("telegram", images=True, video=True, audio=True, documents=True,
                        notifications=True, max_bytes=50 * MB),
    "discord": Surface("discord", images=True, video=True, audio=True, documents=True,
                       notifications=True, max_bytes=25 * MB),
}

#: What an unknown surface gets: the least capable thing that is still useful.
#: Guessing generously is how a terminal ends up being sent a video.
UNKNOWN = Surface("unknown", downloads=True, max_bytes=1 * MB)

#: MIME prefix → the capability it needs.
_NEEDS = (
    ("video/", "video"),
    ("audio/", "audio"),
    ("image/svg", "interactive"),
    ("image/", "images"),
    ("text/html", "interactive"),
    ("application/pdf", "documents"),
    ("application/vnd.openxmlformats", "documents"),
    ("application/msword", "documents"),
)


def capability_for(mime: str) -> str:
    """Which capability an artifact of this type needs. `downloads` is the floor."""
    base = (mime or "").split(";", 1)[0].strip().lower()
    for prefix, need in _NEEDS:
        if base.startswith(prefix):
            return need
    return "downloads"


def surface_for(name: str, reported: dict | None = None) -> Surface:
    """The named surface, narrowed by what the client actually reported.

    Merged conservatively: a client may only ever take capabilities *away*. A
    surface that claims more than the table says is either mistaken or lying, and
    in both cases the person is the one who watches the video fail to play.
    """
    base = REGISTRY.get((name or "").lower(), UNKNOWN)
    if not reported:
        return base
    narrowed: dict[str, Any] = {
        k: bool(base.can(k) and reported.get(k, True))
        for k in ("images", "video", "audio", "documents", "interactive", "downloads", "notifications")
    }
    cap = reported.get("max_bytes")
    if isinstance(cap, int) and cap > 0:
        narrowed["max_bytes"] = min(base.max_bytes, cap)
    return replace(base, **narrowed)


@dataclass(frozen=True)
class Routing:
    """What to do with an artifact, given where the person is."""

    #: 'present' | 'handoff' | 'link'
    action: str
    surface: str
    #: Where to send it, when the action is a handoff.
    to: str | None
    #: One sentence, for the reply. The brief asks for exactly one.
    say: str


def route(
    mime: str,
    size: int,
    active: str,
    *,
    connected: Iterable[str] = (),
    reported: dict | None = None,
    url: str = "",
) -> Routing:
    """Present it here, hand it to a surface that can, or give the URL.

    Never "degrade it into a novelty representation": there is no branch here
    that renders a video as text, and adding one is the bug the brief names.
    """
    here = surface_for(active, reported)
    need = capability_for(mime)
    others = [s for s in connected if s.lower() != here.name]

    if here.can(need) and size <= here.max_bytes:
        return Routing("present", here.name, None, "")

    # Too big for here, but the right kind of surface: a link beats a handoff to
    # somewhere that is no better.
    if here.can(need):
        return Routing(
            "link", here.name, None,
            f"It is {size // MB} MB, past what {here.name} shows comfortably — here is the link: {url}",
        )

    better = next(
        (s for s in others if surface_for(s).can(need) and size <= surface_for(s).max_bytes), None
    )
    if better:
        return Routing(
            "handoff", here.name, better,
            f"{here.name} cannot play that, so I have put it on the {better}.",
        )

    return Routing(
        "link", here.name, None,
        f"Nothing you have open can show that — here is the link: {url}" if url
        else "Nothing you have open can show that, and there is no link to give.",
    )


def summary(active: str, reported: dict | None = None) -> str:
    """The one line injected into the system prompt.

    One line because it is read on every turn, and because a surface's
    capabilities are a fact rather than a topic.
    """
    s = surface_for(active, reported)
    can = [k for k in ("images", "video", "audio", "documents", "interactive") if s.can(k)]
    return (
        f"Active surface: {s.name}. It can show {', '.join(can) if can else 'no media at all'}"
        f"; up to {s.max_bytes // MB} MB. Anything it cannot show is handed to a surface that can, "
        "never converted into text or frames."
    )
