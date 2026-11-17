"""The adapter reads its configuration from the one settings schema, and nowhere else.

``config.Settings`` is the adapter's view of ``aura_schema.Settings``: every
attribute is filled from ``from_env()`` and the module never touches
``os.environ`` itself. These tests pin that — a stray environment read, or an
attribute whose default drifts from the schema's, fails here.
"""
from __future__ import annotations

import re
from pathlib import Path

from aura_schema import from_env

from aura_adapter import config

PACKAGE = Path(config.__file__).resolve().parent


def test_no_adapter_module_reads_the_environment_directly():
    hits = []
    for path in PACKAGE.glob("*.py"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            # Code, not prose. A module that explains *why* it does not read the
            # environment names the thing it is avoiding, and a line-grep cannot
            # tell that comment from the call it warns about -- which fails the
            # module for having good documentation.
            code = line.split("#", 1)[0]
            if re.search(r"os\.environ|os\.getenv", code):
                hits.append(f"{path.name}:{n}: {line.strip()}")
    assert not hits, "the adapter must read settings through aura_schema, not os.environ:\n" + "\n".join(hits)


def test_hand_built_settings_default_to_the_schema_defaults():
    s = config.Settings()
    d = from_env({})
    assert s.hermes_api_url == d.hermes_api_url
    assert s.hermes_serve_url == d.hermes_serve_url
    assert s.model == d.adapter_model
    assert s.upstream_timeout == d.adapter_upstream_timeout
    assert s.host == d.adapter_host and s.port == d.adapter_port and s.log_level == d.adapter_log_level
    assert s.api_key == "" and s.hermes_api_key == "" and s.hermes_serve_token == ""


def test_from_environment_maps_every_schema_value(monkeypatch):
    env = {
        "AURA_API_KEY": "k" * 20, "HERMES_API_KEY": "hk", "HERMES_SERVE_TOKEN": "st",
        "HERMES_API_URL": "http://127.0.0.1:1", "HERMES_SERVE_URL": "http://127.0.0.1:2",
        "LLAMA_SWAP_URL": "http://swap.test", "AURA_MODEL": "m", "ADAPTER_CORS_ORIGINS": "http://a.test, http://b.test",
        "AURA_PUBLIC_URL": "https://pub.test", "ADAPTER_UPSTREAM_TIMEOUT": "12.5",
        "ADAPTER_LOG_LEVEL": "DEBUG", "ADAPTER_HOST": "127.0.0.1", "ADAPTER_PORT": "9000",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    s = config.Settings.from_environment()
    assert s.api_key == env["AURA_API_KEY"] and s.hermes_api_key == "hk" and s.hermes_serve_token == "st"
    assert s.hermes_api_url == "http://127.0.0.1:1" and s.hermes_serve_url == "http://127.0.0.1:2"
    assert s.llama_swap_url == "http://swap.test" and s.model == "m" and s.public_url == "https://pub.test"
    assert s.cors_origins == ["http://a.test", "http://b.test"]
    assert s.upstream_timeout == 12.5 and s.log_level == "DEBUG" and s.host == "127.0.0.1" and s.port == 9000


def test_a_bad_value_fails_at_start_naming_the_variable(monkeypatch):
    import pytest
    from pydantic import ValidationError

    monkeypatch.setenv("ADAPTER_PORT", "not-a-port")
    with pytest.raises(ValidationError) as exc:
        config.Settings.from_environment()
    assert "ADAPTER_PORT" in str(exc.value)


# --------------------------------------------------------------------------- #
# The settings override file (ADR-0044) applies to the adapter's own leaves
# --------------------------------------------------------------------------- #
def _home_with(tmp_path, monkeypatch, text: str):
    home = tmp_path / "home"
    (home / ".storage").mkdir(parents=True)
    (home / ".storage" / "environment.json").write_text(text, encoding="utf-8")
    monkeypatch.setattr(config, "HERMES_HOME", str(home))
    return home


def test_an_override_wins_over_the_environment_for_an_adapter_leaf(monkeypatch, tmp_path):
    import json

    monkeypatch.setenv("ADAPTER_LOG_LEVEL", "INFO")
    monkeypatch.setenv("ADAPTER_UPSTREAM_TIMEOUT", "5")
    _home_with(tmp_path, monkeypatch, json.dumps({"ADAPTER_LOG_LEVEL": "DEBUG",
                                                  "ADAPTER_UPSTREAM_TIMEOUT": "7.5",
                                                  "AURA_PUBLIC_URL": "https://pub.test"}))
    s = config.Settings.from_environment()
    assert s.log_level == "DEBUG" and s.upstream_timeout == 7.5 and s.public_url == "https://pub.test"


def test_a_compose_owned_name_in_the_file_is_ignored(monkeypatch, tmp_path):
    """HERMES_API_KEY reaches the gateway renamed by the compose file; an override of it
    here would change the adapter's copy alone and break the pair."""
    import json

    monkeypatch.setenv("HERMES_API_KEY", "real")
    monkeypatch.setenv("ADAPTER_PORT", "8643")
    _home_with(tmp_path, monkeypatch, json.dumps({"HERMES_API_KEY": "other", "ADAPTER_PORT": "1",
                                                  "LD_PRELOAD": "/x.so"}))
    s = config.Settings.from_environment()
    assert s.hermes_api_key == "real" and s.port == 8643


def test_an_unreadable_or_absent_file_means_the_environment_alone(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("ADAPTER_LOG_LEVEL", "WARNING")
    monkeypatch.setattr(config, "HERMES_HOME", str(tmp_path / "nowhere"))
    assert config.Settings.from_environment().log_level == "WARNING"
    # a directory where the file should be: an OSError, one warning, and the env is used
    home = tmp_path / "home"
    (home / ".storage" / "environment.json").mkdir(parents=True)
    monkeypatch.setattr(config, "HERMES_HOME", str(home))
    with caplog.at_level("WARNING"):
        assert config.Settings.from_environment().log_level == "WARNING"
    assert any("not applied" in r.getMessage() for r in caplog.records)
