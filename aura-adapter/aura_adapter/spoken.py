"""A line as it is said, not as it is shown.

The phone and the car send the reply's own text to ``speak``/``speak-pcm``,
and CosyVoice says what it is given: the owner heard "asterisk" and "double
quote" read out on a drive (2026-10-08). The console strips its markdown
before it asks (``speech.ts`` ``speakable``) but not the quotes, so the
adapter -- the one door every app speaks through -- cleans every line once,
whatever the engine.

Money, units and symbols are said as words by the house's own rules
(jarvis-core's ``spoken_form``, carried here as ``speech_text.py``): until
2026-10-08 only the house's Piper had them, and CosyVoice read "$4.50" and
"22°C" as written. What this adds on top: quotes and the marks left after
them, snake_case read as two words, one line. It does not touch apostrophes,
which are part of words.

And what the screen shows but a voice would spell (T-0729, the owner heard a
reply spelled out on 2026-11-08), taken before ``spoken_form`` reads dashes
and dots in it: a fenced block is "The code is on screen.", each list item
ends a sentence, an id (a UUID, a hex run of eight or more with a digit and a
letter, or sixteen or more letters and digits mixed) is "the one on screen",
several in a row "the ones on screen", and a path (one that starts at a root,
or ends in a file name) is its file name. Not covered: an id shorter than that
(a seven-character sha is read out); a word that happens to be all hex letters
and digits ("facade12") is taken for an id; a relative directory with no file
name ("aura-web/src/lib") and a Windows path keep their separators, because
"yes/no/maybe" is prose.
"""

from __future__ import annotations

import array
import datetime
import re
import sys

from .speech_text import spoken_form

_SPAN = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)\s*-\s*([01]?\d|2[0-3]):([0-5]\d)")

# A fence opens only at the start of a line (CommonMark): three ticks in prose are not one, and
# a block the model never closed runs to the end.
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})[^`\n]*$.*?(?:^[ \t]{0,3}\1[^\n]*$|\Z)", re.S | re.M)
_ITEM = re.compile(r"^[ \t]*(?:[-*+•]|\d+[.)])[ \t]+(.+?)[ \t]*$", re.M)
_TOKEN = re.compile(r"(?<!\S)\S+")
_LEAD = "`'\"“”‘’([{<*_"
_TAIL = "`'\"“”‘’)]}>,.;:!?*_"
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_HEX = re.compile(r"(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{8,}", re.I)
_MIXED = re.compile(r"(?=\D*\d)(?=[^A-Za-z]*[A-Za-z])[A-Za-z0-9+/=]{16,}")
_FILE = re.compile(r"[^/]{2,}\.[A-Za-z0-9]{1,5}$")  # "i.e" is not a file name
_ON_SCREEN = "the one on screen"
_SEVERAL = re.compile(rf"{_ON_SCREEN}(?:,?\s+(?:and\s+|or\s+)?{_ON_SCREEN})+")

_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<=[A-Za-z0-9])_(?=[A-Za-z0-9])"), " "),  # snake_case is two words
    (re.compile(r"[*_`#|\"“”«»]"), ""),  # what emphasis left, code ticks, quotes
    (re.compile(r"\s*=>\s*"), " to "),
    (re.compile(r"\s*!=\s*"), " is not "),
    (re.compile(r"(?<=\w)\s*\+\s*(?=[A-Za-z])|(?<=[A-Za-z])\s*\+\s*(?=\w)"), " plus "),  # digits: spoken_form
    (re.compile(r"(?<=\w)\s*={1,2}\s*(?=\w)"), " equals "),
    (re.compile(r"(?<=\d)\s*\^\s*(?=\d)"), " to the power of "),
    (re.compile(r"\s+>=\s+"), " at least "),
    (re.compile(r"\s+<=\s+"), " at most "),
    (re.compile(r"\s+>\s+"), " greater than "),
    (re.compile(r"\s+<\s+"), " less than "),
    (re.compile(r"[{}\[\]<>^]"), " "),  # brackets a voice would name part words: "array[0]" is "array 0"
    (re.compile(r"\s+"), " "),
    # Nothing left standing where a link or code went; a dotfile keeps its space.
    (re.compile(r"\s+(?=[.,;:!?]+(?:\s|$))"), ""),
)


def _is_id(core: str) -> bool:
    return bool(_UUID.fullmatch(core)) or any(
        _HEX.fullmatch(part) or _MIXED.fullmatch(part) for part in re.split(r"[-:_]", core)
    )


def _is_path(core: str) -> bool:
    if "/" not in core or not re.search(r"[A-Za-z]", core):
        return False
    return core.startswith(("/", "~/", "./", "../")) or bool(_FILE.search(core))


def _token(match: re.Match[str]) -> str:
    """One word as it is said: an id is the one on screen, a path its file name."""
    word = match.group(0)
    rest = word.lstrip(_LEAD)  # strip, not a regex: a run of 20,000 dots backtracked for seconds
    core = rest.rstrip(_TAIL)
    lead, tail = word[: len(word) - len(rest)], rest[len(core) :]
    if not core:
        return word
    if "://" in core or "@" in core:  # spoken_form says a URL or an address, and ate the mark after it
        return f"{lead}{core} {tail}" if tail and not tail.strip(".,;:!?") else word
    if _is_path(core):
        core = core.rstrip("/").rsplit("/", 1)[-1]
        if not _is_id(core):
            return f"{lead}{core}{tail}"
    if _is_id(core):
        return f"{lead}{_ON_SCREEN}{tail}"
    return word


def spoken(text: str, taught: dict[str, str] | None = None) -> str:
    """`text` without the marks a voice would pronounce; "" when nothing is left.

    `taught` is ``speech_text.taught_from(voice.pronunciations)``: names said the owner's way.
    """
    out = _FENCE.sub("\nThe code is on screen.\n", str(text or ""))
    out = _ITEM.sub(lambda m: m.group(1) if m.group(1)[-1] in ".!?:;" else m.group(1) + ".", out)
    out = _SEVERAL.sub("the ones on screen", _TOKEN.sub(_token, out))
    out = spoken_form(out, taught)
    for pattern, repl in _RULES:
        out = pattern.sub(repl, out)
    return out.strip()


#: Quiet hours (T-0587): a little slower, and about half as loud. Kokoro honours the speed;
#: the gain is the adapter's own, so it holds whatever the engine. Not `instructions`: on
#: CosyVoice a request's instructions replace the persona's own brief, and Kokoro has none.
QUIET_SPEED = 0.9
QUIET_GAIN = 0.5


def quiet_now(span: str, now: datetime.time) -> bool:
    """Is `now` inside `span` ("23:00-07:00", which wraps midnight)? False for an empty or bad span."""
    m = _SPAN.fullmatch(str(span or "").strip())
    if not m:
        return False
    start, end = (datetime.time(int(h), int(mi)) for h, mi in (m.group(1, 2), m.group(3, 4)))
    return start <= now < end if start <= end else (now >= start or now < end)


class Softer:
    """16-bit little-endian PCM at :data:`QUIET_GAIN`, chunk by chunk.

    A chunk can end mid-sample, so an odd byte waits for the next one; one left at the very end
    is half a sample and is dropped.
    """

    def __init__(self, gain: float = QUIET_GAIN) -> None:
        self._gain = gain
        self._carry = b""

    def feed(self, chunk: bytes) -> bytes:
        data = self._carry + chunk
        whole = len(data) - len(data) % 2
        self._carry = data[whole:]
        samples = array.array("h", data[:whole])
        if sys.byteorder == "big":
            samples.byteswap()
        samples = array.array("h", (int(v * self._gain) for v in samples))
        if sys.byteorder == "big":
            samples.byteswap()
        return samples.tobytes()
