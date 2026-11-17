"""The suite runs against the versions the adapter's image ships.

The image installs `aura-adapter/requirements.txt`, every line an exact pin. The
pipeline's job installed the same packages unpinned, so on 2026-11-15 it was
testing fastapi 0.163 while production ran 0.133 (T-1460), and the house's suite
had just shown what one release in between can do: a route listing changed shape
and a security test passed for nothing (T-1459). A green suite on other versions
says little about the image.

So the comparison lives in the suite: wherever these tests run (a desk, the
pipeline's job, GitHub's), a package that differs from its pin fails here, by
name, with the command that fixes it. A new job cannot drift quietly.

What this does not cover: packages the image gets only as dependencies of these
(starlette, anyio, h11), which the file does not pin; the Python version; and
anything the image has that is not a Python package.
"""

from __future__ import annotations

import importlib.metadata as metadata
import re
from pathlib import Path
from typing import Callable

import pytest

REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements.txt"

_PIN = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([^\s;#]+)$")


def pins(text: str) -> dict[str, str]:
    """Each package the file pins and its version; a line that is not `name==version` fails."""
    found: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = _PIN.match(line)
        assert match, f"aura-adapter/requirements.txt: {raw!r} is not an exact pin (name==version)"
        found[match.group(1)] = match.group(2)
    return found


def differences(pinned: dict[str, str], installed: Callable[[str], str]) -> list[str]:
    """One line per package whose installed version is not the pinned one."""
    wrong: list[str] = []
    for name, want in pinned.items():
        try:
            have = installed(name)
        except metadata.PackageNotFoundError:
            have = "nothing"
        if have != want:
            wrong.append(f"{name}: installed {have}, the image ships {want}")
    return wrong


def test_the_image_requirements_are_all_exact_pins():
    pinned = pins(REQUIREMENTS.read_text())
    assert {"fastapi", "uvicorn", "httpx"} <= set(pinned), "the web framework's pins are gone"


def test_the_suite_runs_against_what_the_image_ships():
    wrong = differences(pins(REQUIREMENTS.read_text()), metadata.version)
    assert not wrong, (
        "this environment is not the adapter image's, so a green suite here says little "
        "about production: " + "; ".join(wrong) + ". Fix: pip install -r aura-adapter/requirements.txt"
    )


def test_a_difference_is_reported_with_the_package_and_both_versions():
    def installed(name: str) -> str:
        if name == "gone":
            raise metadata.PackageNotFoundError(name)
        return {"fastapi": "0.163.1"}.get(name, "1.0")

    assert differences({"fastapi": "0.133.1", "same": "1.0", "gone": "2.0"}, installed) == [
        "fastapi: installed 0.163.1, the image ships 0.133.1",
        "gone: installed nothing, the image ships 2.0",
    ]


@pytest.mark.parametrize("line", ["fastapi>=0.133", "fastapi", "fastapi==0.133.1 ; python_version < '3.13'"])
def test_a_line_that_is_not_an_exact_pin_fails_the_read(line):
    """A range would make "what the image ships" depend on the day it was built."""
    with pytest.raises(AssertionError, match="not an exact pin"):
        pins(line + "\n")


def test_extras_and_comments_are_read_as_the_package_they_pin():
    assert pins("# web\nuvicorn[standard]==0.41.0  # the fast parsers\n\n") == {"uvicorn": "0.41.0"}
