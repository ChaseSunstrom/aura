"""One implementation of the untrusted-content fence.

``web``, ``vision`` and ``narrate`` each wrap text an attacker may have
authored — a fetched page, a camera description, a sensor digest — in markers
that tell the model the span is DATA, not instructions, and that nothing
downstream may act on it without a fresh human approval. The three carried a
hand-mirrored copy of the same functions each; the module docstrings in
``web/fence.py``, ``vision/fence.py`` and ``narrate/fence.py`` still explain
*why* each kind keeps its own marker name and notice. This holds the one
implementation they now share, so the copies cannot drift.

What differs per kind is passed to :class:`Fence`:

* ``kind`` — the marker name (``<untrusted_web_content>`` vs ``_camera_`` vs
  ``_sensor_``), so a transcript reader can tell a page from a camera from a
  sensor;
* ``notice`` — the sentence the model is trained by repetition to recognise;
* ``recognises`` — the kinds this fence's :meth:`Fence.is_fenced` tripwire
  treats as fenced, so a chain (a page quoted into a camera description, or
  back) cannot slip a foreign marker past it;
* ``source_label`` — ``Source`` or ``Camera``, the word before the origin;
* ``strict_ensure`` — whether :meth:`Fence.ensure_fenced` guards with the
  strict :meth:`Fence.is_wrapped` (``web``, which re-fences jarvis-browser
  output that may already be wrapped) or the loose :meth:`Fence.is_fenced`
  (``vision``/``narrate``, whose input is freshly built in-house).

:meth:`Fence.is_fenced` is a deliberately broad tripwire; :meth:`Fence.is_wrapped`
is strict. Swapping them is a vulnerability one way and a mangled response the
other — see the note in ``web/fence.py``.
"""

from __future__ import annotations

import re

# A caller that strips the tags but pastes the body still leaves this behind.
_NOTICE_TRIPWIRE = "note to the model: everything between these markers"


class Fence:
    """The marker-wrapping for one kind of untrusted content."""

    def __init__(
        self,
        *,
        kind: str,
        notice: str,
        recognises: tuple[str, ...],
        source_label: str,
        strict_ensure: bool = False,
    ) -> None:
        #: This fence's own open/close markers, e.g. ``<untrusted_web_content>``.
        self.open = f"<untrusted_{kind}_content>"
        self.close = f"</untrusted_{kind}_content>"
        self.notice = notice
        self._source_label = source_label
        self._strict_ensure = strict_ensure
        #: Every marker this fence recognises — its own kind plus the crossings
        #: it must catch so a chain cannot close a fence with a foreign marker.
        self._marker_re = re.compile(
            rf"</?\s*untrusted_(?:{'|'.join(recognises)})_content\s*>", re.IGNORECASE
        )
        #: This fence's own marker only, for the strict :meth:`is_wrapped` count.
        self._own_re = re.compile(rf"</?\s*untrusted_{kind}_content\s*>", re.IGNORECASE)

    def sanitize_untrusted(self, text: str) -> str:
        """Neutralise fence markers so content cannot close its own fence."""
        if not text:
            return ""
        return self._marker_re.sub(lambda m: m.group(0).replace("<", "&lt;"), text)

    def is_fenced(self, text: str) -> bool:
        """True if ``text`` carries any recognised marker, or the notice.

        A deliberately *broad* tripwire, for one question: "did this string
        come off something untrusted?". Do not use it to decide whether a
        string is already wrapped — see :meth:`is_wrapped`.
        """
        if not text:
            return False
        return bool(self._marker_re.search(text)) or _NOTICE_TRIPWIRE in text.lower()

    def is_wrapped(self, text: str) -> bool:
        """True only if ``text`` is *this* fence, whole and intact.

        The strict counterpart to :meth:`is_fenced`: one open marker at the
        very start, one close at the very end, exactly that pair of this kind's
        markers and no others, and the notice between them. A payload that
        reproduces all of it has only wrapped itself in a label saying it is
        untrusted data — it cannot use the mere mention of the notice to opt a
        real page out of being fenced.
        """
        stripped = (text or "").strip()
        if not stripped.startswith(self.open) or not stripped.endswith(self.close):
            return False
        if len(self._own_re.findall(stripped)) != 2:
            return False
        return _NOTICE_TRIPWIRE in stripped.lower()

    def fence(self, text: str, *, source: str = "") -> str:
        """Wrap ``text`` as explicitly-untrusted data."""
        notice = self.notice
        if source:
            notice += f" {self._source_label}: {self.sanitize_untrusted(str(source))}"
        return (
            f"{self.open}\n{notice}\n\n{self.sanitize_untrusted(text or '')}\n{self.close}"
        )

    def ensure_fenced(self, text: str, *, source: str = "") -> str:
        """Fence ``text`` unless it already is. Fencing is the invariant.

        ``web`` guards with the strict :meth:`is_wrapped` because it re-fences
        content another service may have wrapped; the loose :meth:`is_fenced`
        there would let a page opt itself out by quoting the notice. The
        in-house kinds have no such input and guard with :meth:`is_fenced`.
        """
        text = text or ""
        already = self.is_wrapped(text) if self._strict_ensure else self.is_fenced(text)
        if already:
            return text
        return self.fence(text, source=source)
