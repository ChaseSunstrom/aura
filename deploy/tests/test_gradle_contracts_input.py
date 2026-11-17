"""The phone's unit-test tasks declare every repo file their tests read as an input (T-1174, T-1224).

Gradle skips a test task whose declared inputs did not change, so a table read from outside the
module and not declared goes stale-green.

Does not cover: a path a test builds from pieces instead of one string literal, or a file that
does not exist yet when the scan runs.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ANDROID = ROOT / "android-app"
GRADLE = ANDROID / "app" / "build.gradle.kts"

_LITERAL = re.compile(r'"((?:\.\./)+[^"$\s]+)"')
_INPUT = re.compile(r'inputs\.(?:dir|file)\(rootProject\.file\("(\.\./[^"]+)"\)\)')


def _undeclared(module: Path, gradle_text: str, sources: dict[str, str]) -> list[str]:
    """Literals that name an existing repo path outside android-app and under no declared input.

    Tests run with the module folder as the working directory, so that is where a literal resolves.
    Traversal fixtures ("../evil") name nothing that exists and are not counted.
    """
    declared = [(ANDROID / p).resolve() for p in _INPUT.findall(gradle_text)]
    missed = []
    for name, text in sources.items():
        for literal in _LITERAL.findall(text):
            target = (module / literal).resolve()
            if not target.exists() or not target.is_relative_to(ROOT) or target.is_relative_to(ANDROID):
                continue
            if not any(target.is_relative_to(d) for d in declared):
                missed.append(f"{name}: {literal}")
    return missed


def test_contracts_input_is_declared_on_the_unit_test_task() -> None:
    text = GRADLE.read_text(encoding="utf-8")
    assert 'it.inputs.dir(rootProject.file("../tests/contracts"))' in text


def test_every_repo_file_a_phone_test_reads_is_a_declared_input() -> None:
    missed = []
    for tests in sorted(ANDROID.glob("*/src/test")):
        module = tests.parents[1]
        gradle = module / "build.gradle.kts"
        sources = {
            str(f.relative_to(ROOT)): f.read_text(encoding="utf-8", errors="replace")
            for f in tests.rglob("*.kt")
        }
        missed += _undeclared(module, gradle.read_text(encoding="utf-8") if gradle.exists() else "", sources)
    assert not missed, f"read by a test but not a declared Gradle input: {missed}"


def test_an_undeclared_read_is_reported_by_name() -> None:
    gradle = GRADLE.read_text(encoding="utf-8")
    sources = {"FakeTest.kt": 'File("../../docs/README.md"); File("../../tests/contracts/reactor.json"); File("../../etc/passwd")'}
    assert _undeclared(ANDROID / "app", gradle, sources) == ["FakeTest.kt: ../../docs/README.md"]
