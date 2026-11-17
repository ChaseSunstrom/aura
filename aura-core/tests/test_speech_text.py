"""M73 — said, not shown: the reply the synthesiser gets is words.

The operator's own turn, 26 Aug 2026: a Bitcoin briefing written for the
screen — bold, bullets, "~$78,721", "0.15%", "40 GB", "49/100" — and Piper
read the asterisks and the dollar signs. Every row here is a case that was
heard wrong or would be; the transcript is untouched by any of it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aura.voice.pipeline import speakable  # noqa: E402
from aura.voice.speech_text import spoken_form  # noqa: E402

# The cases live in tests/contracts/speech-text.json (T-0218), which the adapter's copy is
# tested against too: one corpus, two places the rules run.
CORPUS = json.loads((Path(__file__).resolve().parents[2] / "tests" / "contracts" / "speech-text.json").read_text(encoding="utf-8"))
CASES = [(c["written"], c["said"]) for c in CORPUS["cases"]]


@pytest.mark.parametrize("written,said", CASES)
def test_the_spoken_form_of_what_was_written(written: str, said: str) -> None:
    assert spoken_form(written) == said


def test_what_is_left_alone() -> None:
    # No rule fires: the text is the text, so a reply with none of the above
    # reaches the synthesiser exactly as before M73.
    assert spoken_form("The kitchen light is on, Sir. Good night.") == "The kitchen light is on, Sir. Good night."
    # Acronyms are Piper's own business; a lone symbol the table does not know stays.
    assert spoken_form("MSTR closed at 5 ¤.") == "MSTR closed at 5 ¤."


def test_speakable_says_it_rather_than_shows_it() -> None:
    """The pipeline's one door to the synthesiser: chunks, the whole reply
    and the remainder all pass through `speakable`, so this is the guarantee
    that no surface hears an asterisk."""
    assert speakable("**Price:** ~$78,721.\n\nUp 0.15%.") == "Price: about 78,721 dollars. Up 0.15 percent."
    assert speakable("Done, Sir.") == "Done, Sir."
    assert speakable("...?") == ""
