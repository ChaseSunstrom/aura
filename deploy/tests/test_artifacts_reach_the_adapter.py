"""The artifact tools can reach the adapter from the jarvis container.

``jarvis-artifacts`` registers every tool behind ``_available``, which needs
``adapter.key`` (env ``AURA_ADAPTER_KEY``). Compose gave that key to the console
only, so in production the plugin's toolset showed as enabled while every tool in it
was hidden from the model -- and the plugin's tests, which fake the HTTP call, could
not see it (found 2026-09-18 while wiring publish-to-panel). This pins the compose
file, the way test_sandbox_backend.py pins the sandbox's lines.

Not covered: whether the key the operator's .env carries is the adapter's (that is
``AURA_API_KEY``'s contract, checked by the adapter at request time).
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "docker-compose.yml"


def _env(service: str) -> dict[str, str]:
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return {k: str(v) for k, v in doc["services"][service]["environment"].items()}


def test_the_jarvis_container_carries_the_adapter_key_and_a_loopback_address():
    env = _env("jarvis")
    assert env.get("AURA_ADAPTER_KEY") == "${AURA_API_KEY:-${JARVIS_API_KEY:-}}", env.get("AURA_ADAPTER_KEY")
    assert env.get("AURA_ADAPTER_URL") == "http://127.0.0.1:8643", env.get("AURA_ADAPTER_URL")


def test_the_console_and_the_agent_use_the_same_key_variable():
    """One secret, two readers: the console proxies with it, the plugin publishes with it."""
    assert _env("jarvis-web")["AURA_ADAPTER_KEY"] == _env("jarvis")["AURA_ADAPTER_KEY"]


def test_the_console_relay_takes_a_long_voice_clip():
    """adapter-node's default 512K refused a 12 s clip posted through /hermes (T-0386)."""
    limit = _env("jarvis-web")["BODY_SIZE_LIMIT"]
    assert limit.endswith("M") and int(limit[:-1]) >= 2, limit
