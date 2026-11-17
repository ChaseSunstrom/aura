"""The first clause of a reply, so the first word is heard sooner (T-0605).

CosyVoice makes its first hop of speech before it sends a byte, and a long first
sentence also costs a long text prefill. The first chunk sent to it is therefore the
reply's first clause. The rule and its cases are `tests/contracts/first-clause.json`,
which the console's `speech.ts` reads too. Does not cover: a clause that is not a
clause (a list, "Well, ...") is still cut; the join between the two chunks has the
voice's own pause, not a prosody carried across.
"""

from __future__ import annotations

import re

MIN_WORDS = 3
MAX_WORDS = 8

#: A comma or semicolon followed by space, a spaced hyphen, or an en/em dash; or a
#: sentence end, which stops the search (the sentence is already the first chunk).
_MARK = re.compile(r"[,;](?=\s)|\s-+(?=\s)|[–—]|[.!?](?=\s|$)|\n")


def split_first_clause(text: str) -> tuple[str, str] | None:
    """(first clause with its mark, the rest), or None when the text is not cut."""
    for m in _MARK.finditer(text):
        if m.group(0)[0] in ".!?\n":
            return None
        words = len(text[: m.start()].split())
        if words > MAX_WORDS:
            return None
        if words >= MIN_WORDS:
            first, rest = text[: m.end()].strip(), text[m.end():].strip()
            return (first, rest) if rest else None
    return None
