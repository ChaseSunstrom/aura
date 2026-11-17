"""The generators are deterministic, in sync, and never leak a secret value."""
from __future__ import annotations

import json

import pytest

from aura_schema import generate as G
from aura_schema import settings as S


# --------------------------------------------------------------------------- sync
def test_generated_files_are_in_sync_with_the_schema():
    # If this fails, run `make schema` and commit the result.
    assert G.check_all() == [], "generated files are stale — run `make schema`"


def test_writing_is_idempotent():
    G.write_all()
    assert G.write_all() == []  # a second write changes nothing


# --------------------------------------------------------------------------- config template
def test_config_template_round_trips_the_committed_file():
    rendered = G.render_config_template()
    assert rendered == G.CONFIG_TEMPLATE.read_text(encoding="utf-8")


def test_config_template_keeps_every_env_placeholder_backed_by_a_setting():
    rendered = G.render_config_template()
    for var in G._PLACEHOLDER_RE.findall(rendered):
        assert var in S.SETTINGS_BY_ENV, f"${{{var}}} names no setting"


def test_config_template_renders_no_secret_placeholder():
    rendered = G.render_config_template()
    for s in S.SETTINGS:
        if s.secret and s.env in {"HERMES_API_KEY", "AURA_API_KEY", "HASS_TOKEN"}:
            assert "${" + s.env + "}" not in rendered


def test_an_unknown_template_token_is_a_fail_fast_error(tmp_path, monkeypatch):
    bad = tmp_path / "bad.in"
    bad.write_text("model:\n  default: \"@no.such.key@\"\n", encoding="utf-8")
    monkeypatch.setattr(G, "TEMPLATE_IN", bad)
    with pytest.raises(G.GenerationError) as exc:
        G.render_config_template()
    assert "no.such.key" in str(exc.value)


# --------------------------------------------------------------------------- contract
#: The eight runtime keys the plugin serves, with the exact strings its own
#: allow-list carries — locked here so a schema reword cannot silently drift.
EXPECTED_KEYS = {
    "goals.max_turns": {"type": "int", "min": 1, "max": 200, "restart_needed": False,
                        "doc": "Turns a standing goal may take before it parks"},
    "approvals.mode": {"type": "enum", "values": ["manual", "smart", "off"], "restart_needed": False,
                       "doc": "When the approval door opens: manual = every gated tool asks, "
                              "smart = an auxiliary model decides for you, off = never"},
    "tools.enabled": {"type": "list", "restart_needed": False, "doc": "Toolsets on for new sessions"},
    "tools.disabled": {"type": "list", "restart_needed": False, "doc": "Toolsets off for new sessions"},
    "model.default": {"type": "string", "restart_needed": False,
                      "doc": "The model every conversation runs on"},
    "image_gen.provider": {"type": "string", "restart_needed": False,
                           "doc": "Which image backend image_generate uses"},
    "tool_loop_guardrails.hard_stop_enabled": {"type": "bool", "restart_needed": False,
                                               "doc": "Stop a turn whose tool calls loop without progress"},
    "tool_loop_guardrails.loop_caps.max_web_searches": {"type": "int", "min": 1, "max": 200,
                                                        "restart_needed": False,
                                                        "doc": "Web searches one turn may make"},
}


def _contract() -> dict:
    return json.loads(G.render_contract())


def test_contract_runtime_pieces():
    data = _contract()
    assert data["command"]["name"] == "jsettings"
    assert data["tools"] == ["settings_list", "settings_get", "settings_set"]
    assert data["audit_log"] == "logs/settings.jsonl"


def test_contract_keys_match_the_plugin_allow_list():
    data = _contract()
    # the eight original runtime keys, unchanged, then the D30 HUD keys
    assert list(data["keys"])[:8] == list(EXPECTED_KEYS)
    for key, want in EXPECTED_KEYS.items():
        got = data["keys"][key]
        for field, value in want.items():
            assert got[field] == value, (key, field, got.get(field), value)
    hud = {k: v for k, v in data["keys"].items() if k.startswith("hud.")}
    assert set(hud) == {"hud.mode", "hud.lock_layout", "hud.reduced_transparency", "hud.reduced_motion",
                        "hud.sync_layouts", "hud.layouts",
                        # The car's route: Jarvis's map or an app, and its turns said (2026-09-25).
                        "hud.car_navigation", "hud.car_speak_directions",
                        "hud.car_clock", "hud.car_speed_warnings",
                        "hud.car_speed_warn_over", "hud.car_speed_full_over",
                        "hud.car_speed_average_s", "hud.car_speed_repeat_s",
                        "hud.car_voice"}
    assert hud["hud.mode"] == {"type": "enum", "values": ["overview", "task", "focus", "ambient"],
                               "restart_needed": False, "doc": hud["hud.mode"]["doc"], "group": "HUD",
                               "widget": "select"}
    assert hud["hud.layouts"]["type"] == "object" and hud["hud.layouts"]["shape"] == "hud_layouts"
    assert hud["hud.layouts"]["widget"] == "managed"


def test_contract_groups_are_ordered_and_described():
    data = _contract()
    assert [g["id"] for g in data["groups"]] == [g.value for g in S.Group]
    for g in data["groups"]:
        assert g["title"] and g["description"]
    ids = {g["id"] for g in data["groups"]}
    for key, entry in data["keys"].items():
        assert entry["group"] in ids
        assert entry["widget"] in data["widgets"]


def test_contract_hud_section_examples_agree_with_the_model():
    from pydantic import ValidationError

    hud = _contract()["hud"]
    assert hud["surfaces"] == list(S.SURFACES) and hud["modes"] == list(S.HUD_MODES)
    assert hud["tri_state"] == ["system", "on", "off"]
    assert hud["layout_document"]["title"] == "LayoutDocument"
    for doc in hud["examples"]["valid"]:
        S.LayoutDocument.model_validate(doc)
    for doc in hud["examples"]["invalid"]:
        with pytest.raises(ValidationError):
            S.LayoutDocument.model_validate(doc)


def test_contract_catalogue_covers_every_setting():
    data = _contract()
    assert {row["key"] for row in data["settings"]} == {s.key for s in S.SETTINGS}
    by_key = {row["key"]: row for row in data["settings"]}
    assert by_key["comfyui.steps"]["advanced"] is True and by_key["model.default"]["advanced"] is False
    assert by_key["hud.layouts"]["shape"] == "hud_layouts"


def test_contract_catalogue_never_emits_a_secret_default():
    data = _contract()
    by_key = {row["key"]: row for row in data["settings"]}
    for s in S.SETTINGS:
        if s.secret:
            assert "default" not in by_key[s.key], f"{s.key} leaked a default into the contract"
            assert by_key[s.key]["secret"] is True


# --------------------------------------------------------------------------- no secret leak
def test_no_generated_file_contains_a_real_secret_pattern():
    forbidden = ("glpat-", "glrt-", "sk-", "BEGIN OPENSSH", "PRIVATE KEY")
    for path, render in G.OUTPUTS:
        text = render()
        for needle in forbidden:
            assert needle not in text, f"{path.name} contains {needle!r}"
