# @generated from aura-core/aura/voice/speech_text.py by `make speech-copy` -- DO NOT EDIT; edit that file.

"""What a reply looks like when it is said rather than shown (M73).

The model writes for a screen: bold, bullets, ``~$78,721``, ``0.15%``,
``40 GB``, ``49/100``. Piper says what it is given, so the house read
"asterisk asterisk price colon asterisk asterisk tilde dollar seventy-eight
thousand…" to the operator on 26 Aug 2026. The transcript keeps the reply as
written; this is the form the synthesiser gets.

Deliberately a table of expansions, not a grammar: every rule names the
case it exists for, and a symbol not in the table is left alone rather than
guessed at. What it does NOT do: read acronyms (Piper's own rule stands),
convert units it was not taught, or touch anything inside the transcript.
"""

from __future__ import annotations

import re

__all__ = ["spoken_form", "taught_from"]

#: Markdown that carries no sound. Order matters: links before emphasis (a
#: link's text may be bold), fences before inline code.
_FENCE = re.compile(r"```.*?```", re.S)
_INLINE_CODE = re.compile(r"`([^`\n]*)`")
_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)\s]+)\)")
_IMAGE = re.compile(r"!\[[^\]]*\]\((?:[^)\s]+)\)")
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+", re.M)
_BULLET = re.compile(r"^[ \t]*(?:[-*+•]|\d+[.)])[ \t]+", re.M)
_EMPHASIS = re.compile(r"(\*{1,3}|_{1,3})(?=\S)(.+?)(?<=\S)\1")
_STRIKE = re.compile(r"~~(.+?)~~")
_TABLE_RULE = re.compile(r"^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$", re.M)
_TABLE_PIPES = re.compile(r"^[ \t]*\|(.*)\|[ \t]*$", re.M)

#: A bare address is read as its host — "news dot bitcoin dot com" says
#: where; forty characters of path say nothing a listener can use. Host and path
#: each end on a non-mark character, so the full stop, comma, ! or ? that closes
#: the sentence is left in the text (T-0836) instead of being eaten with the path.
_URL = re.compile(r"\bhttps?://([^\s/)>\]]*[^\s/)>\].,!?;:])(?:[^\s)>\]]*[^\s)>\].,!?;:])?")

#: Money. The sign comes first in writing and last in speech; a suffix that
#: means thousand/million/billion is spelled out, because "two B dollars"
#: is not a sum anyone says.
_MONEY = re.compile(
    r"(?P<sign>[$£€])(?P<num>\d[\d,]*(?:\.\d+)?)(?P<suffix>[kKmMbB]n?|bn|tn)?\b"
)
_CURRENCY = {"$": "dollars", "£": "pounds", "€": "euros"}
_MAGNITUDE = {"k": "thousand", "m": "million", "b": "billion", "bn": "billion", "tn": "trillion"}

_ABOUT = re.compile(r"~\s*(?=[$£€]?\d)")
_PERCENT = re.compile(r"(?<=\d)\s?%")
_RATIO = re.compile(r"\b(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\b")
_UNITS = {
    "TB": "terabytes", "GB": "gigabytes", "MB": "megabytes", "KB": "kilobytes",
    "kWh": "kilowatt hours", "kW": "kilowatts", "MWh": "megawatt hours", "MW": "megawatts",
    "W": "watts", "V": "volts", "A": "amps", "Hz": "hertz", "kHz": "kilohertz", "GHz": "gigahertz",
    "km/h": "kilometres per hour", "mph": "miles per hour", "km": "kilometres", "kg": "kilograms",
    "ms": "milliseconds", "°C": "degrees Celsius", "°F": "degrees Fahrenheit", "°": "degrees",
}
#: A one-letter unit only when it is written against its number ("10A", "12V"): after a
#: space it is a word ("3 A grades" was "3 amps grades", T-0218).
_UNIT = re.compile(
    r"(?<=\d)(?:\s?(" + "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True) if len(u) > 1)
    + r")|(" + "|".join(re.escape(u) for u in _UNITS if len(u) == 1) + r"))(?![A-Za-z])"
)
#: Dates, month first as the house's locale writes them: 10/09/2026 is October 9th. Before the
#: ratio rule, which read it as "10 out of 09/2026" (T-0218).
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_US_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
#: "24/7" is round the clock, not a score.
_ROUND_THE_CLOCK = re.compile(r"\b24/7\b")
#: A fraction in front of a noun is a quantity ("1/2 cup"); anywhere else it stays a score.
_FRACTION = re.compile(r"\b(1/2|1/4|3/4)(?=\s+[A-Za-z])")
_FRACTIONS = {"1/2": "a half", "1/4": "a quarter", "3/4": "three quarters"}
#: "5-10 minutes": a range, not a minus. Not three groups (a phone number) or a date.
_RANGE = re.compile(r"(?<![\d.\-/])(\d+(?:\.\d+)?)\s?[-–]\s?(\d+(?:\.\d+)?)(?![\d\-/])")
#: A sign in front of a number, where no word or number comes right before it.
_MINUS = re.compile(r"(?<![\w)\]])-(?=\d)")
_PLUS = re.compile(r"(?<![\w)\]])\+(?=\d)")
_PLUS_BETWEEN = re.compile(r"(?<=\d)\s?\+\s?(?=\d)")
_EQUALS = re.compile(r"(?<=[\d\s])=(?=[\s\d])")
_SIGNS = {"±": "plus or minus ", "≈": "about ", "≥": "at least ", "≤": "at most ", "≠": "not equal to "}
_SIGN = re.compile("(" + "|".join(_SIGNS) + r")\s?")
#: Read part by part: "v2.3.1" and an address on the LAN.
_VERSION = re.compile(r"\bv(\d+(?:\.\d+)+)\b")
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_EMAIL = re.compile(r"\b([\w.+-]+)@([\w-]+(?:\.[\w-]+)+)\b")
_NUMBER_SIGN = re.compile(r"#(?=\d)")
_LATIN = {"e.g.": "for example", "i.e.": "that is", "etc.": "et cetera"}
_LATIN_RE = re.compile(r"\b(e\.g\.|i\.e\.|etc\.)")
#: "3 hrs" said in full; singular after exactly one.
_SHORT_TIME = re.compile(r"(?<=\d)(\s?)(hrs?|mins?|secs?)\b")
_SHORT_TIMES = {"hr": "hour", "min": "minute", "sec": "second"}
_AMPERSAND = re.compile(r"\s&\s")
_ARROW = re.compile(r"\s*(?:->|→)\s*")
_TIMES = re.compile(r"(?<=\d)\s?[×x]\s?(?=\d)")
#: "2x faster" — a multiplier with nothing after it is "times", not the letter.
_TIMES_ALONE = re.compile(r"(?<=\d)x\b")
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF⬀-⯿️]"
)
_SPACES = re.compile(r"[ \t]+")


def _money(match: re.Match[str]) -> str:
    num = match.group("num")
    suffix = (match.group("suffix") or "").lower()
    magnitude = _MAGNITUDE.get(suffix, "")
    unit = _CURRENCY[match.group("sign")]
    if num in ("1", "1.0") and not magnitude:
        unit = unit[:-1]
    return f"{num} {magnitude} {unit}".replace("  ", " ")


def _unit(match: re.Match[str]) -> str:
    return " " + _UNITS[match.group(1) or match.group(2)]


def _date(year: str, month: str, day: str) -> str | None:
    m, d = int(month), int(day)
    return f"{_MONTHS[m - 1]} {d}, {year}" if 1 <= m <= 12 and 1 <= d <= 31 else None


def _short_time(match: re.Match[str]) -> str:
    word = _SHORT_TIMES[match.group(2).rstrip("s")]
    before = match.string[:match.start()].split()
    one = bool(before) and before[-1].lstrip("(~") == "1"
    return f" {word}" + ("" if one else "s")


def taught_from(setting: str) -> dict[str, str]:
    """`voice.pronunciations` ("Siobhan=shi-vawn; Nguyen=win") as written -> spoken.

    A pair without both halves is skipped, not guessed at. Keys are lower-cased:
    :func:`spoken_form` matches them in any case.
    """
    pairs = (part.split("=", 1) for part in str(setting or "").split(";") if "=" in part)
    return {w.strip().lower(): s.strip() for w, s in pairs if w.strip() and s.strip()}


def spoken_form(text: str, taught: dict[str, str] | None = None) -> str:
    """`text` as words a synthesiser can say. The transcript keeps the original.

    `taught` (from :func:`taught_from`) is said first, as whole words in any case, so
    a name the voice gets wrong is said the way the owner taught it (T-0587). It does
    not cover a name inside a longer word ("Siobhan's" is matched; "Siobhanfest" is not).
    """
    out = str(text or "")
    if not out.strip():
        return ""
    if taught:
        names = "|".join(re.escape(w) for w in sorted(taught, key=len, reverse=True))
        out = re.sub(rf"\b({names})\b", lambda m: taught[m.group(1).lower()], out, flags=re.I)
    out = _FENCE.sub(" ", out)
    out = _IMAGE.sub(" ", out)
    out = _LINK.sub(r"\1", out)
    out = _URL.sub(lambda m: m.group(1).replace(".", " dot "), out)
    out = _INLINE_CODE.sub(r"\1", out)
    out = _TABLE_RULE.sub("", out)
    out = _TABLE_PIPES.sub(lambda m: m.group(1).replace("|", ","), out)
    out = _HEADING.sub("", out)
    out = _BULLET.sub("", out)
    out = _STRIKE.sub(r"\1", out)
    for _ in range(3):  # nested emphasis: ***x***, **_x_**
        out = _EMPHASIS.sub(r"\2", out)
    out = _EMOJI.sub("", out)
    # Before anything that reads dots, slashes or hyphens between digits.
    out = _EMAIL.sub(lambda m: f"{m.group(1)} at {m.group(2).replace('.', ' dot ')}", out)
    out = _IPV4.sub(lambda m: m.group(0).replace(".", " dot "), out)
    out = _VERSION.sub(lambda m: "version " + m.group(1).replace(".", " point "), out)
    out = _ISO_DATE.sub(lambda m: _date(m.group(1), m.group(2), m.group(3)) or m.group(0), out)
    out = _US_DATE.sub(lambda m: _date(m.group(3), m.group(1), m.group(2)) or m.group(0), out)
    out = _ROUND_THE_CLOCK.sub("24 7", out)
    out = _FRACTION.sub(lambda m: _FRACTIONS[m.group(1)], out)
    out = _LATIN_RE.sub(lambda m: _LATIN[m.group(1)], out)
    out = _NUMBER_SIGN.sub("number ", out)
    out = _SIGN.sub(lambda m: _SIGNS[m.group(1)], out)
    out = _RANGE.sub(r"\1 to \2", out)
    out = _PLUS_BETWEEN.sub(" plus ", out)
    out = _EQUALS.sub(" equals ", out)
    out = _MINUS.sub("minus ", out)
    out = _PLUS.sub("plus ", out)
    out = _ABOUT.sub("about ", out)
    out = _MONEY.sub(_money, out)
    out = _PERCENT.sub(" percent", out)
    out = _UNIT.sub(_unit, out)
    out = _SHORT_TIME.sub(_short_time, out)
    out = _RATIO.sub(r"\1 out of \2", out)
    out = _TIMES.sub(" by ", out)
    out = _TIMES_ALONE.sub(" times", out)
    out = _AMPERSAND.sub(" and ", out)
    out = _ARROW.sub(" to ", out)
    out = _SPACES.sub(" ", out)
    return "\n".join(line.strip() for line in out.splitlines()).strip()
