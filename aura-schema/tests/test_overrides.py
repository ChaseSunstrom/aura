"""The operator-override helpers (ADR-0044): what may be overridden inside the
container, how a typed value spells itself in the env, and what the loader lets
through — the entrypoint exports its answer as root, so the filter is the test
that matters most here.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from aura_schema import settings as S

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "docker-compose.yml"
DEPLOY_ENV = ROOT / "deploy" / ".env.example"
ENTRYPOINT = ROOT / "deploy" / "entrypoint.sh"

#: The two services that apply the override file at start (the entrypoint in
#: ``jarvis``, ``aura_adapter.config`` in the adapter). A same-name pass-through
#: into one of these (``TTS_PROVIDER: ${TTS_PROVIDER:-openai}``) is a value the
#: container reads under the name the file carries, so an override shadows it.
_READS_THE_FILE = ("jarvis", "jarvis-adapter")


def _host_owned_by_the_deploy() -> set[str]:
    """Every schema env name the compose file, the app-only set or the entrypoint fixes."""
    names = set(S.SETTINGS_BY_ENV)
    sites: dict[str, list[bool]] = {}
    service, in_env = None, False
    for line in COMPOSE.read_text(encoding="utf-8").split("\n"):
        m = re.match(r"^  ([a-z0-9-]+):\s*$", line)
        if m:
            service, in_env = m.group(1), False
        if re.match(r"^    environment:\s*$", line):
            in_env = True
        elif re.match(r"^    [a-z_]+:", line):
            in_env = False
        for name in re.findall(r"\$\{([A-Z0-9_]+)", line):
            if name not in names:
                continue
            passthrough = bool(in_env and service in _READS_THE_FILE
                               and re.match(r"^\s+%s:\s*\$\{%s[:}]" % (name, name), line))
            sites.setdefault(name, []).append(passthrough)
    compose_owned = {n for n, hits in sites.items() if not all(hits)}
    # Not in the deploy .env and not an advanced tunable: the apps' own settings and the
    # adapter's bind, which the compose file fixes (test_env_examples.NOT_IN_DEPLOY_ENV).
    declared = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", DEPLOY_ENV.read_text(encoding="utf-8"), re.M))
    app_only = {n for n in names if n not in declared and not S.SETTINGS_BY_ENV[n].advanced}
    # Names the entrypoint reads for itself, as root, before the gateway exists.
    root_reads = {n for n in re.findall(r"\$\{([A-Z][A-Z0-9_]*)", ENTRYPOINT.read_text(encoding="utf-8"))
                  if n in names}
    return compose_owned | app_only | root_reads


def test_host_owned_env_is_exactly_what_the_deploy_fixes_outside_the_container():
    expected = _host_owned_by_the_deploy()
    assert set(S.HOST_OWNED_ENV) == expected, (
        f"add: {sorted(expected - S.HOST_OWNED_ENV)}; drop: {sorted(S.HOST_OWNED_ENV - expected)}")


def test_host_owned_names_are_all_schema_env_names():
    assert S.HOST_OWNED_ENV <= set(S.SETTINGS_BY_ENV)


def test_applies_answers_for_the_three_shapes():
    assert S.applies(S.SETTINGS_BY_KEY["goals.max_turns"]) == "now"       # Hermes key, live
    assert S.applies(S.SETTINGS_BY_KEY["tts.provider"]) == "restart"      # Hermes key, restart_needed
    assert S.applies(S.SETTINGS_BY_KEY["adapter.port"]) == "compose"      # the compose file binds it
    assert S.applies(S.SETTINGS_BY_KEY["selfext.repo_path"]) == "compose" # root acts on it first
    env_only = next(s for s in S.SETTINGS if s.env and not s.hermes_key and s.env not in S.HOST_OWNED_ENV)
    assert S.applies(env_only) == "restart"


@pytest.mark.parametrize("key, value, spelled", [
    ("goals.max_turns", 30, "30"),
    ("adapter.upstream_timeout", 12.5, "12.5"),
    ("mcp.gitlab_enabled", True, "true"),
    ("mcp.gitlab_enabled", False, "false"),
    ("model.base_url", "http://jarvis-models:8000/v1", "http://jarvis-models:8000/v1"),
])
def test_env_string_round_trips_through_from_env(key, value, spelled):
    setting = S.SETTINGS_BY_KEY[key]
    assert S.env_string(setting, value) == spelled
    assert getattr(S.from_env({setting.env: spelled}), setting.field) == value


def test_env_string_joins_a_list_the_way_the_gateway_splits_it():
    setting = next(s for s in S.SETTINGS if s.env and s.kind is S.Kind.LIST)
    spelled = S.env_string(setting, ["111", "222"])
    assert spelled == "111,222"
    assert getattr(S.from_env({setting.env: spelled}), setting.field) == ["111", "222"]
    assert S.env_string(setting, None) == ""


def test_load_overrides_lets_only_schema_names_through(tmp_path):
    """The entrypoint exports this as root: ``LD_PRELOAD`` and a path it chowns
    must never come back, whatever wrote the file."""
    path = tmp_path / "environment.json"
    path.write_text(json.dumps({
        "LLM_URL": "http://jarvis-models:8000/v1",          # a setting: kept
        "LD_PRELOAD": "/opt/data/evil.so",       # not a setting: dropped
        "SELFEXT_REPO_PATH": "/etc",             # root chowns it at start: dropped
        "TAILSCALE_IP": "10.0.0.1",              # a port bind: dropped
        "GOALS_MAX_TURNS": 30,                   # not a string: dropped
    }))
    assert S.load_overrides(path) == {"LLM_URL": "http://jarvis-models:8000/v1"}


def test_load_overrides_missing_file_is_no_overrides_and_a_bad_one_raises(tmp_path):
    assert S.load_overrides(tmp_path / "absent.json") == {}
    bad = tmp_path / "environment.json"
    bad.write_text("[1, 2]")
    with pytest.raises(ValueError):
        S.load_overrides(bad)
    bad.write_text("{not json")
    with pytest.raises(ValueError):
        S.load_overrides(bad)


def test_from_env_applies_overrides_over_the_environment():
    cfg = S.from_env({"GOALS_MAX_TURNS": "20"}, overrides={"GOALS_MAX_TURNS": "30"})
    assert cfg.goals_max_turns == 30
    assert S.from_env({"GOALS_MAX_TURNS": "20"}, overrides={}).goals_max_turns == 20


def test_overrides_path_is_under_the_home():
    assert S.overrides_path("/opt/data") == "/opt/data/.storage/environment.json"
    assert S.OVERRIDES_REL == ".storage/environment.json"
