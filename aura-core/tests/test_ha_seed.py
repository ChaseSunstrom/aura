"""HA's HTTP store is seeded on the bind before HA's first start (T-1525).

Without it, HA 2026.11 runs configuration.yaml's ``http:`` block as a five-minute trial and then
reverts to every interface, onboarding page and all, when nobody promotes it. The seed runs here
against a stand-in for HA's ``homeassistant.components.http.config`` with that module's
constants and default config. Not covered: HA reading the file, which the live check (the seed
in the pinned image, a throwaway HA that logs "Using stable HTTP config") shows.
"""
from __future__ import annotations

import importlib.util
import json
import stat
from enum import StrEnum
from pathlib import Path
from types import SimpleNamespace

SEED = Path(__file__).resolve().parents[1] / "home-assistant" / "seed-http.py"
_spec = importlib.util.spec_from_file_location("seed_http", SEED)
assert _spec and _spec.loader
seed_http = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed_http)


class SSLProfile(StrEnum):
    MODERN = "modern"


# homeassistant/components/http/config.py in 2026.11.2: these names and values.
HA_CONF = SimpleNamespace(
    STORAGE_KEY="http", STORAGE_VERSION=3, STORAGE_MINOR_VERSION=1,
    KEY_STABLE="stable", KEY_PENDING="pending", KEY_YAML_MIGRATION_DONE="yaml_migration_done",
    _DEFAULT_CONFIG={"server_port": 8123, "cors_allowed_origins": [], "use_x_forwarded_for": False,
                     "trusted_proxies": [], "ip_ban_enabled": True, "login_attempts_threshold": -1,
                     "ssl_profile": SSLProfile.MODERN, "created_at": "2026-11-17T00:00:00+00:00",
                     "error": None, "error_message": None},
)


def test_ha_seed_writes_a_stable_config_on_the_bind_with_no_trial(tmp_path):
    assert seed_http.seed(tmp_path, "127.0.0.1", HA_CONF) is True

    path = tmp_path / ".storage" / "http"
    doc = json.loads(path.read_text())
    assert (doc["version"], doc["minor_version"], doc["key"]) == (3, 1, "http")
    data = doc["data"]
    assert data["stable"]["server_host"] == ["127.0.0.1"]
    assert data["pending"] is None, "a pending config is a trial HA reverts in five minutes"
    assert data["yaml_migration_done"] is True, "else HA stages the YAML block as a trial anyway"
    assert data["stable"]["ssl_profile"] == "modern", "HA reads it back with SSLProfile(value)"
    assert {k: v for k, v in data["stable"].items() if k != "server_host"} == {
        **HA_CONF._DEFAULT_CONFIG, "ssl_profile": "modern"}, "everything else is HA's default"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600, "HA keeps the store private"
    assert not list(path.parent.glob("*.aura-seed")), "no half-written file is left beside it"


def test_ha_seed_never_touches_a_store_ha_already_has(tmp_path):
    path = tmp_path / ".storage" / "http"
    path.parent.mkdir(parents=True)
    path.write_text('{"owner": "settings"}')

    assert seed_http.seed(tmp_path, "127.0.0.1", HA_CONF) is False
    assert path.read_text() == '{"owner": "settings"}'


def test_ha_seed_with_no_bind_leaves_ha_to_its_default(tmp_path):
    assert seed_http.seed(tmp_path, "", HA_CONF) is False
    assert not (tmp_path / ".storage").exists()
