"""The token lint walks the car modules, not just the phone app (T-1336).

`android-app/car/core-dash` types its whole palette in `CarTheme.kt`; the lint used to look
only under `android-app/app`, so a new typed colour in a car module was never seen.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("token_lint", ROOT / "scripts/verify/token_lint.py")
token_lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(token_lint)


def _scan(tmp_path, monkeypatch, module: str) -> dict:
    src = tmp_path / "android-app/car" / module / "src/main/kotlin/dev/x"
    src.mkdir(parents=True)
    (src / "Paint.kt").write_text("val ink = 0xFF112233.toInt()\n")
    monkeypatch.setattr(token_lint, "ROOT", tmp_path)
    monkeypatch.setattr(token_lint, "generated_names", set)  # the web token table is not under test
    return token_lint.scan_all({})


def test_a_typed_colour_in_a_car_ui_module_is_reported(tmp_path, monkeypatch):
    results = _scan(tmp_path, monkeypatch, "core-dash")
    assert "android-app/car/core-dash/src/main/kotlin/dev/x/Paint.kt" in results


def test_protocol_modules_stay_out_of_the_walk(tmp_path, monkeypatch):
    """Ports, buffer sizes and timeouts are not design values."""
    assert _scan(tmp_path, monkeypatch, "core-protocol") == {}


def test_every_car_ui_file_is_baselined_or_clean():
    """The real tree: a car file over its baseline, or new, fails the lint itself."""
    results = token_lint.scan_all(token_lint.load_baseline().get("exceptions", {}))
    car = {p for p in results if p.startswith("android-app/car/")}
    assert car <= set(token_lint.load_baseline()["files"])
