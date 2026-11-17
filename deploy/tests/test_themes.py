"""Theme packs drive every surface through the one token pipeline (T-0861, design/AURA/PROMPT.md §1).

A pack re-points the existing token vocabulary; these pin that the default pack applies
before the page knows the persona, that an alias follows the theme (the reactor's colours
are aliases of the accent), that the JARVIS pack is the base look and so moves nothing,
and that the validator refuses what PROMPT.md §1 says it must.

Not covered: how a surface draws a renderer or a marker it reads from the data; the web
and phone suites own that.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "design"))
import build  # noqa: E402
import themes  # noqa: E402


def _check():
    spec = importlib.util.spec_from_file_location("theme_check", ROOT / "scripts/verify/theme_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _block(css: str, theme_id: str) -> dict[str, str]:
    start = css.index(f":root[data-theme='{theme_id}'] {{")
    body = css[start:css.index("}", start)]
    out = {}
    for line in body.splitlines()[1:]:
        name, _, value = line.strip().partition(": ")
        out[name] = value.rstrip(";")
    return out


@pytest.fixture(scope="module")
def css() -> str:
    return themes.gen_theme_css(build, build.load(), themes.load_themes())


def test_both_packs_load_and_aura_is_the_default():
    packs = themes.load_themes()
    assert {"aura", "jarvis"} <= set(packs) and themes.DEFAULT == "aura"


def test_the_default_pack_applies_before_the_page_knows_the_persona(css):
    assert ":root:not([data-theme]),\n:root[data-theme='aura'] {" in css


def test_the_header_comment_closes_only_at_its_end(css):
    # A "*/" inside the mark ended the comment early and PostCSS failed the console build.
    assert "THEMES.md" in css[:css.index("*/")]


def test_aura_re_points_the_accent_and_the_aliases_follow(css):
    aura = _block(css, "aura")
    assert aura["--jv-accent"] == "#b79cff"
    assert aura["--jv-reactor-listening"] == "#b79cff"
    assert aura["--jv-font-display"].startswith("Sora")


def test_jarvis_is_the_base_look_and_moves_no_colour(css):
    moved = [k for k in _block(css, "jarvis") if not k.startswith("--jv-theme-")]
    assert moved == [], moved


def test_the_generated_theme_files_are_current():
    for path, text in themes.outputs(build, build.load()).items():
        assert path.read_text(encoding="utf-8") == text, f"stale {path}: run python3 design/build.py"


def test_the_validator_passes_both_packs():
    # The wake model is trained outside the suite; the full run (with it) is criterion 1.
    assert _check().main(["--skip", "wake"]) == 0


def test_the_validator_refuses_text_below_aa(tmp_path):
    check = _check()
    pack = json.loads((ROOT / "themes/aura/theme.json").read_text(encoding="utf-8"))
    pack["color"]["text.label"] = "#2a2244"
    problems = check.contrast_problems(pack)
    assert any("text.label" in p for p in problems), problems


def test_the_validator_refuses_a_missing_icon_size(tmp_path):
    check = _check()
    pack = json.loads((ROOT / "themes/aura/theme.json").read_text(encoding="utf-8"))
    pack["icons"]["sizes"]["2048"] = "assets/icons/aura-icon-2048.png"
    problems = check.asset_problems(pack, ROOT / "themes/aura")
    assert any("2048" in p for p in problems), problems


def test_the_validator_refuses_an_offline_order_the_phone_does_not_know():
    # OfflineVoice.kt knows two orders; any other value would be read as the bundled voice's.
    check = _check()
    pack = json.loads((ROOT / "themes/aura/theme.json").read_text(encoding="utf-8"))
    pack["voice"]["offline"]["fallback"] = "system"
    schema = json.loads((ROOT / "themes/theme.schema.json").read_text(encoding="utf-8"))
    assert any("fallback" in p for p in check.schema_problems(pack, schema))


def test_the_literal_lint_finds_names_voices_and_wake_phrases_but_not_identifiers():
    """design/AURA/PROMPT.md §1: no assistant name, voice id or wake phrase typed in app code."""
    spec = importlib.util.spec_from_file_location("theme_literal_lint", ROOT / "scripts/verify/theme_literal_lint.py")
    lint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lint)
    hits = lint.scan('val a = "Say Hey, Aura"\nval v = \'af_heart\'\nlabel = `AURA is offline`\n'
                     "import ai.jarvis.app.JarvisTokens\nval x = JarvisTokens.Color\n")
    assert [h.split()[1] for h in hits] == ["wake", "voice", "name"]
    assert lint.main([]) == 0


def test_the_literal_lint_skips_only_the_exact_identifier_and_none_go_stale():
    """T-0881: a SKIP is one exact literal with a reason; the baseline is empty."""
    spec = importlib.util.spec_from_file_location("theme_literal_lint", ROOT / "scripts/verify/theme_literal_lint.py")
    lint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lint)
    text = 'val a = "Jarvis"\nval b = "Hello Jarvis"\n'
    used: set[str] = set()
    hits = lint.scan(text, {'"Jarvis"': "a stored key"}, used)
    assert [h.split()[0] for h in hits] == ["2:"] and used == {'"Jarvis"'}
    stale: list[str] = []
    lint.scan_all(stale)
    assert stale == []
    assert all(reason for skip in lint.SKIP.values() for reason in skip.values())
    assert json.loads((ROOT / "design/theme-literal.baseline.json").read_text())["files"] == {}
