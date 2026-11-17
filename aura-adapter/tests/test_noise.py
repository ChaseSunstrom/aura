"""The noise gate on transcripts (aura_adapter/noise.py), from the owner's own logs."""

from __future__ import annotations

from aura_adapter import noise


def test_the_lines_that_reached_the_model_are_noise():
    for line in ("Can you, can you, to, to, to, to, to, to, to, to, to, to, to, to",
                 "We do have-", "Just attempt to-"):
        assert noise.reason(line), line


def test_short_and_ordinary_requests_pass():
    for line in ("Stop.", "Hello.", "Jarvis.", "Text Isa that I'm on my way to her house",
                 "Play my Rocky 3 playlist on shuffle", "Can you route me to 425 SW 11th Street?",
                 "Turn the lights on, on, on the porch", "Set a timer for twenty-five minutes", "",
                 "Hello. Hello. Hello. Hello. Hello. Hello.", "Jarvis, stop everything. Jarvis, stop everything."):
        assert noise.reason(line) is None, line
