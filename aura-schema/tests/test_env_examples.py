"""The ``.env.example`` files agree with the schema and leak no secret value.

One file is the schema's to keep honest: ``deploy/.env.example``, the deploy
superset (the repo-root companion-stack file went with that stack, 2026-10-09). ``aura-core/.env.example`` is *not* checked here — jarvis-core keeps its
own Home-Assistant-shaped config, covered by its packaging tests (V2_PLAN §3.3).
"""
from __future__ import annotations

import re
from pathlib import Path

from aura_schema import settings as S

ROOT = Path(__file__).resolve().parents[2]
DEPLOY_ENV = ROOT / "deploy" / ".env.example"

#: Schema settings that are deliberately *not* in the deploy compose stack's
#: ``.env.example`` — the phone/desktop apps and the adapter's own bind/CORS
#: knobs. ``advanced`` settings (fine-grained tunables that fall back to their
#: schema default) are exempt by flag, not by name. Adding a deploy setting means
#: NOT listing it here, which is what makes the file carry it.
NOT_IN_DEPLOY_ENV = {
    # app-only (phone / desktop)
    "AURA_ADAPTER_URL", "AURA_ADAPTER_KEY", "AURA_TTS_VOICE", "AURA_VAD_HANGOVER_MS",
    "AURA_PUSH_TO_TALK", "AURA_PUSH_TO_TALK_KEY", "AURA_PALETTE_KEY", "AURA_DICTATION_KEY", "AURA_AUTO_UPDATE",
    "AURA_HUD_WEB_ORIGINS", "AURA_BACKEND",
    "AURA_PIPELINE", "AURA_ALLOWED_ORIGINS",
    # adapter bind/CORS (the compose file fixes them)
    "ADAPTER_CORS_ORIGINS", "ADAPTER_HOST", "ADAPTER_PORT",
}

#: Substrings that mark a *real* secret — a filled-in value, not a placeholder.
FORBIDDEN_SECRET_PATTERNS = ("glpat-", "glrt-", "sk-", "BEGIN OPENSSH", "PRIVATE KEY")


def _declared(path: Path) -> dict[str, str]:
    """``VAR -> value`` for each ``VAR=value`` line (value keeps any inline comment)."""
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def test_deploy_env_example_declares_only_schema_variables():
    declared = set(_declared(DEPLOY_ENV))
    unknown = declared - set(S.SETTINGS_BY_ENV)
    assert not unknown, f"deploy/.env.example declares variables the schema does not: {sorted(unknown)}"


def test_deploy_env_example_declares_every_deploy_setting():
    declared = set(_declared(DEPLOY_ENV))
    expected = {s.env for s in S.SETTINGS if s.env and not s.advanced} - NOT_IN_DEPLOY_ENV
    missing = expected - declared
    assert not missing, f"deploy/.env.example is missing schema settings: {sorted(missing)}"


def test_no_env_example_carries_a_real_secret_value():
    secret_envs = {s.env for s in S.SETTINGS if s.secret and s.env}
    for path in (DEPLOY_ENV,):
        for var, value in _declared(path).items():
            for needle in FORBIDDEN_SECRET_PATTERNS:
                assert needle not in value, f"{path.name}:{var} looks like a real secret ({needle!r})"
            if var in secret_envs:
                # A secret's example value must be empty or an obvious placeholder,
                # never a plausible token committed by accident.
                bare = value.split("#", 1)[0].strip()
                assert bare == "" or "change" in bare.lower() or "scrypt$" in bare, (
                    f"{path.name}:{var} has a non-placeholder secret value {bare!r}"
                )


def test_every_bare_placeholder_in_the_config_template_is_in_the_deploy_env_example():
    """An *unquoted* ``${VAR}`` must be a variable the deploy ``.env`` carries.

    The entrypoint's renderer has no default syntax: an unset variable becomes the empty
    string. Inside quotes that is a harmless empty value, but a bare
    ``max_concurrent_children: ${VAR}`` becomes ``max_concurrent_children:`` — YAML null,
    which Hermes reads as a configured None rather than falling back to its default. Any
    setting rendered bare therefore has to be one the host's .env is known to declare.
    """
    template = (ROOT / "deploy" / "config.template.yaml").read_text(encoding="utf-8")
    declared = set(_declared(DEPLOY_ENV))
    bare = {
        m.group(1)
        for m in re.finditer(r"^[^#\n]*?:\s+\$\{([A-Z0-9_]+)\}\s*$", template, re.MULTILINE)
    }
    missing = bare - declared
    assert not missing, (
        "config.template.yaml renders these unquoted, so an unset variable would write a "
        f"YAML null; declare them in deploy/.env.example: {sorted(missing)}"
    )
