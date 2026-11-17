"""The speaker vocabulary is one table, read as text from the three places it is typed.

``owner`` / ``member`` / ``guest`` is spelled in jarvis-core (``voice/speaker.py``,
which decides the level), in the adapter (``speaker.py``, which carries it on a
ticket and answers ``/api/speaker/current``) and in the Hermes guard
(``hermes/plugins/_shared/_speaker.py``, which asks that path and turns a guest's
ASK into a DENY). They cannot share a module: jarvis-core does not depend on
aura-schema and the plugin imports it lazily, so a fourth spelling in the schema
would bind two of the three and leave the one that matters by hand. This reads the
three files and fails on the day one of them drifts — a level renamed on one side
becomes ``level not in LEVELS`` → ``None`` → the key holder's rights, silently.

Not covered: what level a given voice is GIVEN (jarvis-core's own tests), or that
the guard reads the answer correctly (``hermes/plugins/tests``). Only that the
words, the default and the path agree.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "aura-core/aura/voice/speaker.py"
ADAPTER = ROOT / "aura-adapter/aura_adapter/speaker.py"
GUARD = ROOT / "hermes/plugins/_shared/_speaker.py"
ADAPTER_APP = ROOT / "aura-adapter/aura_adapter/app.py"

LEVELS = ("owner", "member", "guest")
CURRENT_PATH = "/api/speaker/current"


def top_level_constants(path: Path) -> dict[str, object]:
    """Module-level ``NAME = literal`` and ``NAME = (OTHER_NAME, …)`` assignments, as values."""
    out: dict[str, object] = {}
    for node in ast.parse(path.read_text()).body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            out[target.id] = ast.literal_eval(node.value)
        except ValueError:
            if isinstance(node.value, ast.Tuple) and all(isinstance(e, ast.Name) for e in node.value.elts):
                out[target.id] = tuple(out[e.id] for e in node.value.elts)  # type: ignore[index]
    return out


@pytest.mark.parametrize("path", [CORE, ADAPTER, GUARD], ids=["core", "adapter", "guard"])
def test_every_copy_spells_the_levels_the_same(path: Path) -> None:
    consts = top_level_constants(path)
    assert consts.get("LEVELS") == LEVELS, f"{path.relative_to(ROOT)} spells LEVELS as {consts.get('LEVELS')!r}"


def test_the_default_is_the_owner_everywhere() -> None:
    core = top_level_constants(CORE)
    assert core["LEVEL_OWNER"] == LEVELS[0]
    for path in (ADAPTER, GUARD):
        assert top_level_constants(path)["KEY_HOLDER_LEVEL"] == core["LEVEL_OWNER"], path.relative_to(ROOT)


def test_the_guard_asks_the_path_the_adapter_serves() -> None:
    assert f'@app.get("{CURRENT_PATH}"' in ADAPTER_APP.read_text()
    assert CURRENT_PATH in GUARD.read_text()
