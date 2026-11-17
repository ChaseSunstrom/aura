"""Transcripts that are noise, not a request, dropped before they become a prompt.

The owner's devices hear a room, a car and a classroom: on 2026-09-25 a presentation
nearby came through as "Into ploy.", "We do have-", "Shift, push, let's out", and on
2026-10-06 a stuck microphone as "Can you, can you, to, to, to, to, to...". Each reached
the model, which started tools on some of them.

Two shapes are dropped: one word said over and over (Whisper's loop on noise), and a
line Whisper marks as cut off mid-word ("We do have-"). Does not judge meaning: a short
real request ("Stop.", "Hello.") passes, and a stranger speaking plainly is the speaker
gate's to refuse, not this.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[\w']+", re.UNICODE)
#: Whisper writes a line it heard break off with a trailing hyphen or dash.
_CUT_OFF = re.compile(r"\w[-—–]\s*$")
#: At least this many words before a repeat can be called a loop.
LOOP_MIN_WORDS = 6
#: One word making up this share of the line is a loop, not a sentence.
LOOP_SHARE = 0.5
#: Said over and over on purpose: "Hello. Hello. Hello." is the owner checking he is heard.
CALLS = frozenset({"hello", "hey", "hi", "jarvis", "stop", "yes", "no"})


def reason(transcript: str) -> str | None:
    """Why [transcript] is noise, or None when it may be a request."""
    text = (transcript or "").strip()
    if not text:
        return None
    words = [w.lower() for w in _WORD.findall(text)]
    if len(words) >= LOOP_MIN_WORDS:
        top_word = max(set(words), key=words.count)
        if top_word not in CALLS and words.count(top_word) / len(words) >= LOOP_SHARE:
            return "one word repeated"
    if _CUT_OFF.search(text) and len(words) <= 8:
        return "cut off mid-word"
    return None
