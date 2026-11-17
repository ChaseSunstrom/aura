"""The preflight toolchain check.

The brief's gate: *"the preflight check reports every pinned tool; a deliberately
missing tool produces a capability-gap MR and no install attempt."* The second
half is a behaviour of the agent, and what this module can pin is the half it
depends on: that a missing tool is *reported as missing* rather than omitted, and
that the advice it comes with names the merge request rather than an install.
"""

from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402

toolchain = load_plugin("aura-artifacts").toolchain


def test_the_manifest_is_what_the_brief_asks_for() -> None:
    """Named rather than discovered.

    A preflight check that only reports what is installed cannot tell you the
    image regressed, which is the one thing it is for.
    """
    names = {t.name for t in toolchain.MANIFEST}
    assert {"ffmpeg", "ffprobe", "yt-dlp", "node", "python3", "magick", "pandoc"} <= names


def test_libreoffice_is_optional_and_the_reason_is_recorded() -> None:
    """The brief says "if the size is acceptable — measure". It is ~500 MB."""
    lo = next(t for t in toolchain.MANIFEST if t.name == "libreoffice")
    assert lo.required is False


def test_every_tool_says_what_it_is_for() -> None:
    """The purpose is what makes a gap report actionable rather than a list."""
    assert all(t.purpose for t in toolchain.MANIFEST)


class TestCheck:
    def test_a_present_tool_reports_a_version(self) -> None:
        found = toolchain.check((toolchain.Tool("python3", "run things"),))
        assert found[0].present and "Python" in found[0].version

    def test_a_missing_tool_is_reported_not_omitted(self) -> None:
        found = toolchain.check((toolchain.Tool("definitely-not-a-real-tool", "nothing"),))
        assert len(found) == 1
        assert found[0].present is False and found[0].version == ""

    def test_a_tool_that_hangs_or_errors_does_not_hang_the_check(self) -> None:
        # `false` exists and exits non-zero with no output: present, no version.
        found = toolchain.check((toolchain.Tool("false", "exits non-zero"),))
        assert found[0].present is True


class TestGapReport:
    def test_nothing_missing_says_nothing(self) -> None:
        assert toolchain.gap_report(toolchain.check((toolchain.Tool("python3", "x"),))) == ""

    def test_it_names_the_tool_and_what_it_was_for(self) -> None:
        found = toolchain.check((toolchain.Tool("nope-not-here", "download a video"),))
        report = toolchain.gap_report(found)
        assert "nope-not-here" in report and "download a video" in report

    def test_it_asks_for_a_merge_request_and_forbids_an_install(self) -> None:
        """The whole point of Part 5: the image is the place, not the container."""
        report = toolchain.gap_report(toolchain.check((toolchain.Tool("nope-not-here", "x"),)))
        assert "merge request" in report
        assert "Do not install them at runtime" in report
        assert "egress allowlist" in report

    def test_an_optional_tool_is_not_a_gap(self) -> None:
        found = toolchain.check((toolchain.Tool("nope-not-here", "x", required=False),))
        assert toolchain.missing(found) == []
        assert toolchain.gap_report(found) == ""


def test_the_image_promises_every_required_tool() -> None:
    """The Dockerfile must install what the manifest requires.

    This is the pin that catches the two drifting apart -- a manifest entry with
    no line in the Dockerfile is a preflight check that will always report a gap,
    and a tool in the image that nothing declares is one nobody knows they have.
    """
    dockerfile = (Path(__file__).resolve().parents[3].parent / "deploy" / "Dockerfile").read_text()
    # The base image supplies these; the rest must appear in our own layers.
    from_base = {"ffmpeg", "ffprobe", "node", "python3"}
    for tool in toolchain.MANIFEST:
        if not tool.required or tool.name in from_base:
            continue
        apt = {"magick": "imagemagick", "pdftoppm": "poppler-utils"}.get(tool.name, tool.name)
        assert apt in dockerfile, f"{tool.name} is required but the Dockerfile never installs {apt}"
