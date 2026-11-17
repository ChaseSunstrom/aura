"""Kokoro speaks every voice the house asks for; CosyVoice is not loaded (T-0901).

The owner, 2026-11-09: replies shown but not said. The voice service held CosyVoice's model
(2.1 GB, loaded at boot for the four cloned persona clips mounted at /voices), Kokoro (both
packs' engine) and the speaker embedder on one card under a 2560 MiB cap measured for CosyVoice
alone; 14 of 20 sentences failed "CUDA out of memory ... 2.50 GiB allowed" with 1.6 GiB of the
card free. The owner: "you don't need to use [it] if kokoro is better, you can remove it".
With no clip mounted CosyVoice skips its warm-up and never loads, so the cap never applies.

CosyVoice's code and image layers are gone since (T-0955). Does not cover: the speaker gate's
CAM++ prints, which run on the CPU from the mounted model directory and stay.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

yaml = pytest.importorskip("yaml")

ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "docker-compose.yml"


def _voice() -> dict:
    return yaml.safe_load(COMPOSE.read_text())["services"]["jarvis-cosyvoice"]


def test_the_voice_service_mounts_no_reference_clips() -> None:
    svc = _voice()
    mounts = [str(v).split(":")[1] if ":" in str(v) else str(v) for v in svc.get("volumes", [])]
    assert "/voices" not in mounts, "a mounted clip makes CosyVoice load its model at boot"
    env = svc.get("environment", {})
    assert "COSYVOICE_VOICES_DIR" not in env


def test_every_pack_speaks_a_kokoro_voice_the_adapter_maps() -> None:
    adapter = (ROOT / "aura-adapter" / "aura_adapter" / "app.py").read_text()
    mapped = dict(re.findall(r'"(\w+)": "(\w+)"', re.search(r"THEME_VOICES[^=]*= \{([^}]*)\}", adapter).group(1)))
    for pack in sorted((ROOT / "themes").glob("*/theme.json")):
        voice = json.loads(pack.read_text())["voice"]
        assert voice["engine"] == "kokoro", f"{pack.parent.name} speaks {voice['engine']}"
        assert mapped.get(pack.parent.name) == voice["voiceId"], f"the adapter maps {pack.parent.name} elsewhere"
