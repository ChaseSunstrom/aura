"""Telegram, Discord and the inbound webhook: enabled deliberately, allowlisted always.

Two layers, because the allowlist has two halves and only one of them is ours.

*Ours* is the configuration: Hermes turns a chat platform on the moment it sees a
``{PLATFORM}_BOT_TOKEN`` in the environment (``gateway/config.py`` ``_enable_from_env``),
so a token pasted into ``.env`` would put JARVIS on a public network with nobody
having decided to. The rendered config therefore writes ``enabled`` explicitly for
every platform, from a setting of its own. These tests pin that, and pin that no
fail-open flag is ever set.

*Hermes'* is the enforcement, in ``gateway/authz_mixin.py``. Re-implementing it here
would produce a test that agrees with itself; instead ``scripts/verify/gateway-allowlist.py``
drives Hermes' real function, and the last test in this file runs that script wherever
Hermes is importable (the dev box, the JARVIS image, CI's plugin-discovery job) and
skips with a reason where it is not.

Not covered: whether a real bot token works. No token exists in this repo, by design.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

# The renderer is the one in test_config_render (it extracts the python embedded in
# entrypoint.sh, so both files test what actually runs). deploy/tests is not a package,
# so it is reached by path the way the plugin tests reach _plugin_loader.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_config_render import _render  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "deploy" / "config.template.yaml"
DEPLOY_ENV = ROOT / "deploy" / ".env.example"
PROOF = ROOT / "scripts" / "verify" / "gateway-allowlist.py"

#: Every variable Hermes reads as "let anyone in". A JARVIS deployment sets none of
#: them; the proof script demonstrates that each really does disable the allowlist,
#: so this list is not folklore.
FAIL_OPEN_VARS = (
    "TELEGRAM_ALLOW_ALL_USERS",
    "DISCORD_ALLOW_ALL_USERS",
    "GATEWAY_ALLOW_ALL_USERS",
)

PLATFORM_ENV = {"TELEGRAM_ENABLED": "false", "DISCORD_ENABLED": "false",
                "WEBHOOK_ENABLED": "false", "WEBHOOK_PORT": "8650", "WEBHOOK_SECRET": "",
                "GATEWAY_UNAUTHORIZED_DM_BEHAVIOR": "pair"}


def _declared_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in DEPLOY_ENV.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).split("#", 1)[0].strip()
    return out


def test_every_chat_platform_is_enabled_explicitly_not_by_a_token(tmp_path):
    cfg = _render(tmp_path / "config.yaml", {**PLATFORM_ENV})
    platforms = cfg["gateway"]["platforms"]
    for name in ("telegram", "discord", "webhook"):
        assert name in platforms, f"{name} has no block, so a bot token alone would enable it"
        assert platforms[name]["enabled"] is False


def test_the_enable_flags_are_settings_not_constants(tmp_path):
    cfg = _render(tmp_path / "config.yaml",
                  {**PLATFORM_ENV, "TELEGRAM_ENABLED": "true", "DISCORD_ENABLED": "true"})
    assert cfg["gateway"]["platforms"]["telegram"]["enabled"] is True
    assert cfg["gateway"]["platforms"]["discord"]["enabled"] is True
    assert cfg["gateway"]["platforms"]["webhook"]["enabled"] is False


def test_no_bot_token_is_rendered_into_the_config(tmp_path):
    """The adapters read their token from the environment; config.yaml never carries one."""
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "${TELEGRAM_BOT_TOKEN}" not in text and "${DISCORD_BOT_TOKEN}" not in text
    cfg = _render(tmp_path / "rendered.yaml",
                  {**PLATFORM_ENV, "TELEGRAM_BOT_TOKEN": "123:should-not-appear",
                   "DISCORD_BOT_TOKEN": "also-should-not-appear"})
    rendered = yaml.safe_dump(cfg)
    assert "should-not-appear" not in rendered


def test_no_fail_open_flag_is_set_anywhere(tmp_path):
    template_text = TEMPLATE.read_text(encoding="utf-8")
    declared = _declared_env()
    for var in FAIL_OPEN_VARS:
        assert var not in declared, f"deploy/.env.example sets {var}, which disables the allowlist"
        assert f"${{{var}}}" not in template_text, f"the config template renders {var}"
    cfg = _render(tmp_path / "config.yaml", {**PLATFORM_ENV})
    assert "allow_all" not in yaml.safe_dump(cfg)


def test_the_env_example_ships_the_platforms_off_with_empty_allowlists():
    declared = _declared_env()
    assert declared["TELEGRAM_ENABLED"] == "false" and declared["DISCORD_ENABLED"] == "false"
    assert declared["WEBHOOK_ENABLED"] == "false"
    # Empty is the safe default and means "nobody": Hermes default-denies when no
    # allowlist is configured, so a token that leaks before the ids are filled in
    # still reaches no one.
    assert declared["TELEGRAM_ALLOWED_USERS"] == ""
    assert declared["DISCORD_ALLOWED_USERS"] == ""


def test_an_unlisted_direct_message_is_offered_pairing_not_the_model(tmp_path):
    cfg = _render(tmp_path / "config.yaml", {**PLATFORM_ENV})
    assert cfg["gateway"]["unauthorized_dm_behavior"] == "pair"
    other = _render(tmp_path / "ignore.yaml",
                    {**PLATFORM_ENV, "GATEWAY_UNAUTHORIZED_DM_BEHAVIOR": "ignore"})
    assert other["gateway"]["unauthorized_dm_behavior"] == "ignore"


def test_the_inbound_webhook_route_demands_an_hmac_secret(tmp_path):
    cfg = _render(tmp_path / "config.yaml",
                  {**PLATFORM_ENV, "WEBHOOK_ENABLED": "true", "WEBHOOK_SECRET": "s" * 32})
    webhook = cfg["gateway"]["platforms"]["webhook"]
    assert webhook["enabled"] is True and webhook["port"] == 8650
    route = webhook["extra"]["routes"]["n8n"]
    assert route["secret"] == "s" * 32
    # Hermes refuses to start a route whose secret is the testing escape hatch; make
    # sure we never ship it.
    assert "INSECURE_NO_AUTH" not in TEMPLATE.read_text(encoding="utf-8")


def test_the_proof_skips_where_a_module_hermes_needs_is_missing():
    """Hermes found but python-dotenv absent is "not importable" (exit 2), not a failure.

    The authorization path imports gateway.run lazily, on the first check, so the
    guarded import at the top used to pass and the traceback came later as exit 1.
    """
    hide = ("import runpy, sys; sys.modules['dotenv'] = None; sys.argv = ['proof']; "
            f"runpy.run_path({str(PROOF)!r}, run_name='__main__')")
    proc = subprocess.run([sys.executable, "-c", hide], capture_output=True, text=True,
                          timeout=120)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert proc.stderr.startswith("SKIP:")


def test_hermes_own_authorization_refuses_an_unlisted_id():
    """The real proof: run scripts/verify/gateway-allowlist.py against installed Hermes."""
    proc = subprocess.run([sys.executable, str(PROOF)], capture_output=True, text=True,
                          timeout=120)
    if proc.returncode == 2:
        pytest.skip(f"Hermes not importable here: {proc.stderr.strip().splitlines()[0]}")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # Column widths are cosmetic; the pairing of id and verdict is the claim.
    for unlisted in ("telegram:999", "discord:444"):
        assert re.search(rf"OK\s+{re.escape(unlisted)}\s+authorized=False", proc.stdout), proc.stdout
    assert "All cases hold" in proc.stdout
