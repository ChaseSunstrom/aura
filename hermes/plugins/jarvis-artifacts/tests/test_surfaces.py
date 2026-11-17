"""The capability registry and the routing rule.

The brief's own regression assertion is about a *trace*, not prose: with the TUI
active, "play this video" must produce the card-plus-handoff response "and no
exploratory tool calls hunting for a way to render it". These tests pin the half
that makes that possible -- that the answer is a lookup rather than a deduction,
and that there is no branch anywhere that turns a video into text.
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402

surfaces = load_plugin("jarvis-artifacts").surfaces
MB = surfaces.MB


class TestCapabilityForMime:
    @pytest.mark.parametrize(
        "mime,need",
        [
            ("video/mp4", "video"),
            ("audio/mpeg", "audio"),
            ("image/png", "images"),
            ("application/pdf", "documents"),
            ("text/html", "interactive"),
            ("application/octet-stream", "downloads"),
        ],
    )
    def test_each_type_asks_for_what_it_needs(self, mime: str, need: str) -> None:
        assert surfaces.capability_for(mime) == need

    def test_an_svg_needs_interactive_not_images(self) -> None:
        """An SVG is a document that can run script; showing one is not showing a picture."""
        assert surfaces.capability_for("image/svg+xml") == "interactive"

    def test_a_charset_parameter_does_not_confuse_it(self) -> None:
        assert surfaces.capability_for("text/html; charset=utf-8") == "interactive"


class TestRegistry:
    def test_the_console_is_a_terminal_and_the_table_says_so(self) -> None:
        c = surfaces.REGISTRY["console"]
        assert not c.can("video") and not c.can("images") and not c.can("documents")
        assert c.can("downloads"), "it can still hand a file over"

    def test_an_unknown_surface_gets_the_poorest_profile(self) -> None:
        """Guessing generously is how a terminal ends up being sent a video."""
        u = surfaces.surface_for("something-new")
        assert not u.can("video") and not u.can("images")
        assert u.max_bytes <= 1 * MB

    def test_a_client_may_take_capabilities_away(self) -> None:
        s = surfaces.surface_for("web", {"video": False})
        assert not s.can("video") and s.can("images")

    def test_a_client_may_not_add_them(self) -> None:
        """A surface claiming more than the table says is mistaken or lying, and
        either way the person is the one who watches the video fail to play."""
        assert not surfaces.surface_for("console", {"video": True}).can("video")

    def test_a_reported_size_cap_only_lowers(self) -> None:
        assert surfaces.surface_for("web", {"max_bytes": 4 * MB}).max_bytes == 4 * MB
        assert surfaces.surface_for("console", {"max_bytes": 900 * MB}).max_bytes == 1 * MB


class TestRouting:
    def test_a_video_on_the_web_is_simply_shown(self) -> None:
        r = surfaces.route("video/mp4", 20 * MB, "web")
        assert r.action == "present" and r.to is None

    def test_a_video_on_the_console_goes_to_a_surface_that_can_play_it(self) -> None:
        """The brief's scenario: no exploration, one sentence, a handoff."""
        r = surfaces.route("video/mp4", 20 * MB, "console", connected=["desktop", "android"])
        assert r.action == "handoff"
        assert r.to == "desktop"
        assert r.say.count(".") == 1, "the brief asks for one sentence"
        assert "desktop" in r.say

    def test_a_video_on_the_console_with_nothing_else_open_gives_the_url(self) -> None:
        r = surfaces.route("video/mp4", 20 * MB, "console", url="http://jarvis.tail/a/1")
        assert r.action == "link" and "http://jarvis.tail/a/1" in r.say

    def test_with_no_url_it_says_so_rather_than_inventing_one(self) -> None:
        r = surfaces.route("video/mp4", 20 * MB, "console")
        assert r.action == "link" and "no link to give" in r.say

    def test_something_too_big_for_here_is_a_link_not_a_handoff(self) -> None:
        """A handoff to somewhere no better is worse than a link."""
        r = surfaces.route("video/mp4", 900 * MB, "web", connected=["desktop"], url="u")
        assert r.action == "link" and r.to is None

    def test_a_handoff_target_must_be_big_enough_too(self) -> None:
        r = surfaces.route("video/mp4", 40 * MB, "console", connected=["discord", "desktop"])
        assert r.to == "desktop", "discord's 25 MB cap rules it out"

    def test_an_image_still_reaches_the_console_as_a_link(self) -> None:
        r = surfaces.route("image/png", 200_000, "console", url="u")
        assert r.action == "link"

    def test_a_download_is_presentable_anywhere_that_takes_files(self) -> None:
        r = surfaces.route("application/octet-stream", 500_000, "console")
        assert r.action == "present"

    def test_there_is_no_branch_that_degrades_media_into_text(self) -> None:
        """The brief: ASCII-art video and frame slideshows only on explicit request.

        Asserted structurally rather than by example, because the failure is a
        branch someone adds later meaning well.
        """
        # Docstrings and comments stripped, not just comments: this module's own
        # header quotes the brief's "ASCII-art video". Third time today that a
        # scanner has read prose about a thing as the thing itself.
        tree = ast.parse(Path(surfaces.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and ast.get_docstring(node):
                node.body = node.body[1:]
        code = ast.unparse(tree)
        for word in ("ascii", "sixel", "kitty", "slideshow", "thumbnail_text"):
            assert word not in code.lower(), f"{word} suggests a novelty representation"

    @pytest.mark.parametrize("action", ["present", "handoff", "link"])
    def test_every_action_is_one_of_the_three(self, action: str) -> None:
        results = [
            surfaces.route("video/mp4", 1 * MB, "web"),
            surfaces.route("video/mp4", 1 * MB, "console", connected=["web"]),
            surfaces.route("video/mp4", 1 * MB, "console"),
        ]
        assert {r.action for r in results} == {"present", "handoff", "link"}


class TestPromptSummary:
    def test_it_is_one_line(self) -> None:
        assert "\n" not in surfaces.summary("web")

    def test_it_names_what_the_surface_can_do(self) -> None:
        assert "video" in surfaces.summary("web")

    def test_a_terminal_is_told_it_can_show_nothing(self) -> None:
        s = surfaces.summary("console")
        assert "no media at all" in s

    def test_it_states_the_rule_so_planning_starts_from_it(self) -> None:
        assert "never converted into text or frames" in surfaces.summary("console")


class TestContract:
    """`tests/contracts/surface-capabilities.json`: the report's fields are the
    registry's, and each shipped client's table is the registry row it narrows —
    so a client that sends the table verbatim takes nothing away, and one that
    sent more than the row would be lying (surface_for ignores it)."""

    contract = json.loads(
        (Path(__file__).resolve().parents[4] / "tests/contracts/surface-capabilities.json").read_text()
    )

    def test_the_report_rides_the_prompt_as_surface(self) -> None:
        assert self.contract["param"] == "surface"

    def test_the_fields_are_the_dataclass_fields(self) -> None:
        assert self.contract["fields"] == list(surfaces.Surface.__dataclass_fields__)

    @pytest.mark.parametrize("name", ["web", "android", "console"])
    def test_each_shipped_table_is_its_registry_row(self, name: str) -> None:
        row = self.contract["surfaces"][name]
        assert row == asdict(surfaces.REGISTRY[name])
        assert surfaces.surface_for(name, row) == surfaces.REGISTRY[name], "sent verbatim, it narrows nothing"
