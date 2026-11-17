"""The settings model: the registry is coherent and the validators fail fast."""
from __future__ import annotations

import pytest

from aura_schema import settings as S


# --------------------------------------------------------------------------- registry
def test_every_setting_key_is_unique():
    keys = [s.key for s in S.SETTINGS]
    assert len(keys) == len(set(keys))


def test_every_env_name_is_unique():
    envs = [s.env for s in S.SETTINGS if s.env]
    assert len(envs) == len(set(envs))


#: The environment variables §1.3 of V2_PLAN names as the schema's to own. If a
#: name here is not in the schema, the schema is not yet the one place they live.
PLAN_ENV_NAMES = {
    # secrets
    "HERMES_API_KEY", "AURA_API_KEY", "HERMES_SERVE_TOKEN", "AURA_TOKEN", "HASS_TOKEN",
    "GITLAB_TOKEN", "PROXMOX_TOKEN_ID", "PROXMOX_TOKEN_SECRET", "GRAFANA_TOKEN", "INFLUX_TOKEN",
    "SEARXNG_SECRET", "AURA_PAIRING_SECRET", "AURA_CONSOLE_PASSWORD",
    "AURA_CONSOLE_PASSWORD_HASH", "AURA_CONSOLE_PASSWORD_FILE",
    # urls / endpoints
    "LLM_URL", "LLAMA_SWAP_URL", "SEARXNG_URL", "COMFYUI_URL", "GRAFANA_URL", "INFLUX_URL",
    "N8N_URL", "HASS_URL", "AURA_URL", "PROXMOX_URL", "GITLAB_API_URL", "AURA_PUBLIC_URL",
    "AURA_ADAPTER_URL", "AURA_REGISTRY",
    # model routing
    "LLM_MODEL", "LLM_CONTEXT_LENGTH", "GOALS_MAX_TURNS", "WHISPER_MODEL", "PIPER_VOICE",
    "COMFYUI_WORKFLOW", "COMFYUI_CHECKPOINT",
    # feature flags / behaviour
    "MCP_GITLAB_ENABLED", "COMFYUI_VERIFY_SSL", "PROXMOX_VERIFY_SSL", "AURA_AUTO_UPDATE",
    "AURA_PUSH_TO_TALK", "AURA_VAD_HANGOVER_MS", "BACKUP_KEEP", "TZ",
    # ui / networking
    "TAILSCALE_IP", "AURA_CORS_ORIGINS", "AURA_ALLOWED_ORIGINS", "AURA_HUD_WEB_ORIGINS",
    "ADAPTER_PORT", "AURA_TTS_VOICE", "BACKUP_DIR", "INFLUX_ORG", "INFLUX_BUCKET",
    "GRAFANA_INFLUX_DS",
    # app-only additions §1.3 names explicitly
    "AURA_ADAPTER_KEY", "ADAPTER_HOST", "ADAPTER_LOG_LEVEL", "AURA_BACKEND",
    "INTEGRATIONS_READ_TIMEOUT", "INTEGRATIONS_WRITE_TIMEOUT", "LLAMA_SWAP_TIMEOUT",
}


def test_schema_owns_every_env_name_the_plan_lists():
    have = set(S.SETTINGS_BY_ENV)
    missing = PLAN_ENV_NAMES - have
    assert not missing, f"schema does not declare env names §1.3 assigns it: {sorted(missing)}"


def test_a_secret_never_carries_a_real_default():
    for s in S.SETTINGS:
        if s.secret:
            assert s.default in ("", None), f"{s.key} secret has a default value"


def test_enum_settings_carry_choices():
    for s in S.SETTINGS:
        if s.kind is S.Kind.ENUM:
            assert s.choices, s.key


def test_settable_settings_match_the_contract_order():
    order = [s.key for s in S.settable_settings()]
    assert order == list(S.CONTRACT_KEY_ORDER)
    assert set(order) == {s.key for s in S.SETTINGS if s.settable}


def test_registry_is_derived_from_the_typed_model():
    # One declaration: every registry row is a Settings field and vice versa.
    assert {s.field for s in S.SETTINGS} == set(S.Settings.model_fields)
    assert S.SETTINGS_BY_KEY["goals.max_turns"].field == "goals_max_turns"


def test_every_group_has_a_description():
    assert set(S.GROUP_DESCRIPTIONS) == set(S.Group)
    assert S.GROUP_DESCRIPTIONS[S.Group.HUD] == (
        "How the HUD is arranged; JARVIS may rearrange it unless locked, and your own arrangement always wins."
    )


# --------------------------------------------------------------------------- hud (D30)
def test_hud_group_carries_the_d30_fields():
    # The D30 fields are the config-key ones; the group also carries the activity
    # view's env-backed switch (M5), which is the plugin's, not the layout's.
    hud = {s.key: s for s in S.SETTINGS if s.group is S.Group.HUD and s.hermes_key}
    assert set(hud) == {"hud.mode", "hud.lock_layout", "hud.reduced_transparency", "hud.reduced_motion",
                        "hud.sync_layouts", "hud.layouts",
                        # The car's route: Jarvis's map or an app, and its turns said (2026-09-25).
                        "hud.car_navigation", "hud.car_speak_directions",
                        "hud.car_clock", "hud.car_speed_warnings",
                        "hud.car_speed_warn_over", "hud.car_speed_full_over",
                        "hud.car_speed_average_s", "hud.car_speed_repeat_s",
                        "hud.car_voice"}
    assert S.SETTINGS_BY_ENV["ACTIVITY_ENABLED"].group is S.Group.HUD
    assert hud["hud.mode"].choices == ("overview", "task", "focus", "ambient") and hud["hud.mode"].default == "task"
    assert hud["hud.lock_layout"].kind is S.Kind.BOOL and hud["hud.lock_layout"].default is False
    for key in ("hud.reduced_transparency", "hud.reduced_motion"):
        assert hud[key].choices == ("system", "on", "off") and hud[key].default == "system"
    assert hud["hud.sync_layouts"].kind is S.Kind.BOOL
    assert hud["hud.layouts"].kind is S.Kind.OBJECT and hud["hud.layouts"].widget is S.Widget.MANAGED
    assert all(s.settable and s.hermes_key for s in hud.values())


def test_layout_document_shape():
    doc = {"mode": "task", "tree": {"split": "row", "children": ["chat", {"split": "column", "children": ["a", "b"]}]},
           "panels": [{"id": "chat", "type": "chat"}, {"id": "a", "type": "goals", "pinned": True},
                      {"id": "b", "type": "timeline", "rect": {"x": 0, "y": 0, "w": 0.5, "h": 0.5}}]}
    out = S.validate_settings({"hud.layouts": {"desktop": {"goal": doc}}})["hud.layouts"]
    assert out["desktop"]["goal"]["tree"]["children"][1]["ratio"] == 0.5  # defaults filled in
    for bad, needle in [
        ({"mode": "grid", "tree": None, "panels": []}, "mode"),
        ({"mode": "task", "tree": "chat", "panels": []}, "chat"),
        ({"mode": "task", "tree": None, "panels": [{"id": "x", "type": "t"}, {"id": "x", "type": "t"}]}, "duplicate"),
        ({"mode": "task", "tree": None, "panels": [{"id": "x", "type": "t", "colour": "red"}]}, "colour"),
        ({"mode": "task", "tree": {"split": "row", "children": ["x"]}, "panels": [{"id": "x", "type": "t"}]}, "tree"),
    ]:
        with pytest.raises(S.SettingsError) as exc:
            S.validate_settings({"hud.layouts": {"web": {"chat": bad}}})
        assert "hud.layouts.web.chat" in str(exc.value) and needle in str(exc.value)
    with pytest.raises(S.SettingsError):
        S.validate_settings({"hud.layouts": {"tv": {}}})  # not a surface


def test_strict_coercion_refuses_typed_strings():
    assert S.validate_settings({"goals.max_turns": "30"})["goals.max_turns"] == 30
    with pytest.raises(S.SettingsError):
        S.validate_settings({"goals.max_turns": "30"}, strict=True)
    with pytest.raises(S.SettingsError):
        S.validate_settings({"hud.lock_layout": "true"}, strict=True)


# --------------------------------------------------------------------------- from_env
def test_from_env_coerces_declared_types_and_ignores_the_rest():
    env = {"GOALS_MAX_TURNS": "30", "LLM_CONTEXT_LENGTH": "1024", "ADAPTER_PORT": "9000",
           "COMFYUI_VERIFY_SSL": "true", "COMFYUI_STEPS": "", "PATH": "/usr/bin", "HOME": "/root"}
    es = S.from_env(env)
    assert isinstance(es, S.Settings)
    assert es.goals_max_turns == 30 and es.adapter_port == 9000 and es.comfyui_verify_ssl is True
    assert es.comfyui_steps is None  # a blank variable is unset, not a parse error
    dumped = es.model_dump(by_alias=True)
    assert dumped["GOALS_MAX_TURNS"] == 30 and isinstance(dumped["GOALS_MAX_TURNS"], int)
    assert "PATH" not in dumped  # unrelated env is ignored, not an error


def test_from_env_defaults_when_absent():
    es = S.from_env({})
    dumped = es.model_dump(by_alias=True)
    assert dumped["BACKUP_KEEP"] == 14
    assert dumped["PIPER_VOICE"] == "en_GB-alan-medium"


def test_from_env_rejects_a_value_that_does_not_fit_naming_the_variable():
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as exc:
        S.from_env({"GOALS_MAX_TURNS": "not-a-number"})
    assert "GOALS_MAX_TURNS" in str(exc.value)


def test_from_env_enforces_int_bounds():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        S.from_env({"GOALS_MAX_TURNS": "9999"})  # max is 200


def test_delegation_depth_is_a_leaf_and_the_batch_ceiling_holds_the_proof():
    """The delegation proof runs twenty children two levels deep (2026-09-18). Hermes caps
    neither; the schema's ceilings are what stop a settings edit from asking the one model
    server for more than it can answer, so both bounds are pinned here, not only declared."""
    from pydantic import ValidationError

    depth = S.SETTINGS_BY_ENV["ROUTING_DELEGATION_MAX_DEPTH"]
    assert (depth.key, depth.hermes_key) == ("routing.delegation_max_depth", "delegation.max_spawn_depth")
    assert (depth.default, depth.min, depth.max, depth.tier) == (2, 1, 3, "advanced")
    assert not depth.restart_needed
    batch = S.SETTINGS_BY_ENV["ROUTING_DELEGATION_MAX_CONCURRENT"]
    assert (batch.default, batch.min, batch.max) == (5, 1, 24)
    assert S.from_env({"ROUTING_DELEGATION_MAX_CONCURRENT": "20", "ROUTING_DELEGATION_MAX_DEPTH": "3"}).routing_delegation_max_depth == 3
    with pytest.raises(ValidationError):
        S.from_env({"ROUTING_DELEGATION_MAX_DEPTH": "4"})
    with pytest.raises(ValidationError):
        S.from_env({"ROUTING_DELEGATION_MAX_CONCURRENT": "25"})


# --------------------------------------------------------------------------- validate_settings
def test_validate_settings_unknown_key_is_an_error_naming_it():
    with pytest.raises(S.SettingsError) as exc:
        S.validate_settings({"gateway.secret": "x"})
    assert "gateway.secret" in str(exc.value)


def test_validate_settings_wrong_type_is_an_error_naming_the_key():
    with pytest.raises(S.SettingsError) as exc:
        S.validate_settings({"goals.max_turns": "abc"})
    assert "goals.max_turns" in str(exc.value)


def test_validate_settings_rejects_bool_for_an_int_key():
    with pytest.raises(S.SettingsError):
        S.validate_settings({"goals.max_turns": True})


def test_validate_settings_accepts_by_key_env_name_or_hermes_key():
    assert S.validate_settings({"goals.max_turns": 30}) == {"goals.max_turns": 30}
    assert S.validate_settings({"GOALS_MAX_TURNS": "30"}) == {"GOALS_MAX_TURNS": 30}
    # a Hermes-only key with no env still resolves
    assert S.validate_settings({"approvals.mode": "smart"}) == {"approvals.mode": "smart"}


def test_validate_settings_enum_membership():
    assert S.validate_settings({"approvals.mode": "off"}) == {"approvals.mode": "off"}
    with pytest.raises(S.SettingsError):
        S.validate_settings({"approvals.mode": "always"})


def test_validate_settings_list_of_strings():
    assert S.validate_settings({"tools.enabled": ["web", "browser"]})["tools.enabled"] == ["web", "browser"]
    with pytest.raises(S.SettingsError):
        S.validate_settings({"tools.enabled": "web"})
    with pytest.raises(S.SettingsError):
        S.validate_settings({"tools.enabled": ["web", 3]})


def test_exactly_seven_settings_are_basic_and_the_rest_land_somewhere():
    """The Basic tier is one screen (ADR-0035), and it stays one screen.

    The tier is a field on the setting rather than a list beside the schema, so a
    new setting lands in `advanced` by itself. What that cannot stop is a
    drive-by promotion growing the first screen one row at a time, so the count
    is asserted: changing it is a decision, and a decision belongs in a commit
    message.
    """
    basic = [s.key for s in S.SETTINGS if s.tier == "basic"]
    assert sorted(basic) == sorted([
        "model.default",
        "tts.engine",
        "tts.voice",
        "voice.push_to_talk",
        "approvals.mode",
        "proactive.quiet_hours_from",
        "hud.reduced_motion",
    ]), basic
    # Every row is in exactly one tier, and no row is in none of them.
    assert {s.tier for s in S.SETTINGS} <= {"basic", "advanced", "expert"}
    assert all(s.tier for s in S.SETTINGS)


def test_a_basic_row_is_one_a_person_can_answer():
    """Nothing on the first screen is a secret, a managed object or a restart."""
    for s in S.SETTINGS:
        if s.tier != "basic":
            continue
        assert not s.secret, f"{s.key} is a secret and cannot be on the first screen"
        assert s.widget is not S.Widget.MANAGED, f"{s.key} is written by a program, not a person"


def test_the_privacy_quiet_house_switch_is_off_by_default_and_settable():
    """T-0837: one switch for every call the house makes on its own; off keeps today's behaviour."""
    row = S.SETTINGS_BY_KEY["privacy.quiet_house"]
    assert row.env == "AURA_QUIET_HOUSE" and row.default is False and row.settable
    assert S.from_env({}).privacy_quiet_house is False
    assert S.from_env({"AURA_QUIET_HOUSE": "true"}).privacy_quiet_house is True
    # The pre-AURA spelling of an existing host's .env is adopted by the schema, not by each reader.
    assert S.from_env({"JARVIS_QUIET_HOUSE": "true"}).privacy_quiet_house is True


def test_settings_nothing_reads_are_not_offered():
    """A knob no service reads lies in the console: these three had no reader (T-0301)."""
    gone = {"tts.instructions", "tts.reference_sample", "voice.long_form_chars"}
    assert gone.isdisjoint(S.SETTINGS_BY_KEY)


def test_car_voice_is_a_setting_jarvis_can_change_with_the_contracts_modes():
    # T-1161: which voice says a reply in the car. The phone reads the modes from the same
    # contract (CarTurn.usesPhone), so a mode added here and not there fails.
    import json
    from pathlib import Path

    contract = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/car-state.json").read_text())
    spec = {s.key: s for s in S.SETTINGS}["hud.car_voice"]
    assert spec.choices == tuple(contract["voice"]["modes"]) == ("auto", "house", "phone")
    assert spec.default == "auto" and spec.settable and spec.hermes_key == "hud.car_voice"
