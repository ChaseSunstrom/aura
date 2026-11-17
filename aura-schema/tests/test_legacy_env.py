"""Env names say AURA_ (T-0959), and a JARVIS_ name set outside the repo still counts.

Production's .env, GitLab's project variables, a desktop's environment and the
overrides file already in the Hermes volume were written with JARVIS_* names that
Claude cannot rename; each is read as its AURA_* name unless that one is set too.
"""
from __future__ import annotations

import json

from aura_schema import settings as S


def test_no_setting_is_named_jarvis_any_more():
    assert [s.env for s in S.SETTINGS if s.env and s.env.startswith("JARVIS_")] == []


def test_a_legacy_name_in_the_environment_still_sets_its_setting():
    assert S.from_env({"JARVIS_TTS_ENGINE": "openai"}).tts_engine == "openai"


def test_the_aura_name_wins_when_both_are_set():
    env = {"JARVIS_TTS_ENGINE": "openai", "AURA_TTS_ENGINE": "cosyvoice"}
    assert S.from_env(env).tts_engine == "cosyvoice"


def test_an_override_under_a_legacy_name_still_wins_over_the_environment():
    settings = S.from_env({"AURA_TTS_ENGINE": "wyoming"}, overrides=S.adopt_legacy({"JARVIS_TTS_ENGINE": "openai"}))
    assert settings.tts_engine == "openai"


def test_the_overrides_file_keeps_a_legacy_key_under_its_new_name(tmp_path):
    path = tmp_path / "environment.json"
    path.write_text(json.dumps({"JARVIS_TTS_ENGINE": "cosyvoice"}))
    assert S.load_overrides(path) == {"AURA_TTS_ENGINE": "cosyvoice"}


def test_the_overrides_file_prefers_the_new_key(tmp_path):
    path = tmp_path / "environment.json"
    path.write_text(json.dumps({"JARVIS_TTS_ENGINE": "openai", "AURA_TTS_ENGINE": "cosyvoice"}))
    assert S.load_overrides(path) == {"AURA_TTS_ENGINE": "cosyvoice"}


def test_a_legacy_name_the_schema_does_not_know_is_still_dropped(tmp_path):
    path = tmp_path / "environment.json"
    path.write_text(json.dumps({"JARVIS_LD_PRELOAD": "/tmp/x.so", "LD_PRELOAD": "/tmp/x.so"}))
    assert S.load_overrides(path) == {}
