"""The README's clips are bound to the page that shows them (T-0865).

`tests/contracts/readme-clips.json` names each clip once. The recorder films a clip at the size
its row gives (`aura-web/e2e/readme-clips.spec.ts`); this file holds the rest of the row: the
README has the heading, under it the looping preview with alt text, linked to the full MP4 on the
project's Pages site, and both files exist inside their size limits. A clip nobody shows, a
section whose clip was never filmed and a preview that links nowhere each fail here.

Not covered: what a clip looks like (a person reads it), that the Pages site is up (it is
deployed from `site/` by the public repository, which does not exist until it is pushed), and
links that are not media (`scripts/check-links.py`, in `make docs-check`).
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text(encoding="utf-8")
TABLE = json.loads((ROOT / "tests" / "contracts" / "readme-clips.json").read_text(encoding="utf-8"))
CLIPS = TABLE["clips"]
PREVIEWS = ROOT / "docs" / "media"
FILMS = ROOT / "site" / "media"

# A preview that links to its film: [![alt](docs/media/x.webp)](https://…/media/x.mp4)
LINKED = re.compile(r"\[!\[(?P<alt>[^\]]*)\]\((?P<img>docs/media/[^)\s]+)\)\]\((?P<to>[^)\s]+)\)")


def _pages() -> str:
    """The project's Pages address, from the one list of links the tree may hold."""
    spec = importlib.util.spec_from_file_location("private_values_for_readme", ROOT / "scripts" / "verify" / "private_values.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod                         # a dataclass looks its module up by name
    spec.loader.exec_module(mod)
    (pages,) = [link for link in mod.PROJECT_LINKS if ".github.io/" in link]
    return f"https://{pages}"


def _section(heading: str) -> str:
    """The README from this heading to the next one of its level or above."""
    found = re.search(rf"^(#{{2,4}}) {re.escape(heading)}\s*$", README, re.M)
    assert found, f"the README has no heading {heading!r}"
    level = len(found.group(1))
    rest = README[found.end():]
    nxt = re.search(rf"^#{{1,{level}}} ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def test_the_table_names_each_clip_once_and_no_heading_twice():
    names = [c["name"] for c in CLIPS]
    assert len(set(names)) == len(names)
    assert all(re.fullmatch(r"[a-z][a-z0-9-]*", n) for n in names)
    headings = [c["heading"] for c in CLIPS]
    assert len(set(headings)) == len(headings)


@pytest.mark.parametrize("clip", CLIPS, ids=lambda c: c["name"])
def test_a_clips_section_shows_its_preview_linked_to_its_film(clip):
    name = clip["name"]
    shown = [m for m in LINKED.finditer(_section(clip["heading"])) if m.group("img") == f"docs/media/{name}.webp"]
    assert len(shown) == 1, f"{clip['heading']!r} shows docs/media/{name}.webp linked to its MP4 exactly once"
    (m,) = shown
    assert m.group("to") == f"{_pages()}/media/{name}.mp4"
    alt = m.group("alt").strip()
    # Alt text says what the clip shows; "clip", "video" or the file's name says nothing.
    assert len(alt.split()) >= 6, f"alt text for {name} says what it shows: {alt!r}"
    assert not re.search(r"\b(screenshot|video|clip|gif)\b", alt, re.I), alt


@pytest.mark.parametrize("clip", CLIPS, ids=lambda c: c["name"])
def test_a_clips_two_files_exist_are_what_they_say_and_are_small_enough(clip):
    preview = PREVIEWS / f"{clip['name']}.webp"
    film = FILMS / f"{clip['name']}.mp4"
    assert preview.is_file() and film.is_file()
    head = preview.read_bytes()[:64]
    assert head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    # A still would pass for a preview: an animated WebP's first chunk is VP8X with the animation bit.
    assert head[12:16] == b"VP8X" and head[20] & 0x02, f"{preview.name} is animated"
    assert film.read_bytes()[4:8] == b"ftyp"
    assert preview.stat().st_size <= TABLE["limits"]["preview_bytes"], f"{preview.name} is {preview.stat().st_size} bytes"
    assert film.stat().st_size <= TABLE["limits"]["mp4_bytes"], f"{film.name} is {film.stat().st_size} bytes"


def test_no_media_file_is_an_orphan():
    names = {c["name"] for c in CLIPS}
    assert {p.name for p in PREVIEWS.iterdir()} == {f"{n}.webp" for n in names}
    assert {p.name for p in FILMS.iterdir()} == {f"{n}.mp4" for n in names}
    # And the README shows nothing from the media folder that the table does not name.
    assert {m.group("img") for m in LINKED.finditer(README)} == {f"docs/media/{n}.webp" for n in names}
    assert set(re.findall(r"docs/media/[^)\s\"']+", README)) == {f"docs/media/{n}.webp" for n in names}


def test_the_first_screen_is_what_it_is_the_install_line_and_a_moving_picture():
    top = "\n".join(README.splitlines()[:45])
    assert top.startswith("# AURA")
    assert re.search(r"curl -fsSL \S+install\.sh \| sh", top)
    assert "install.ps1" in top
    first = CLIPS[0]["name"]
    assert f"docs/media/{first}.webp" in top
    # The install line comes before anything about how it is built.
    assert README.index("curl -fsSL") < README.index("## How it is built")


def test_the_readme_counts_the_cores_states_as_the_code_does():
    states = (ROOT / "aura-web" / "src" / "lib" / "ds" / "core" / "states.ts").read_text(encoding="utf-8")
    listed = re.search(r"export const CORE_STATES = \[(.*?)\] as const", states, re.S)
    assert listed
    names = re.findall(r"'([a-z]+)'", listed.group(1))
    words = {8: "eight", 9: "nine", 10: "ten"}
    assert f"{words[len(names)]} states" in _section("The core")
    for name in names:
        assert name in _section("The core"), f"the core's section names the {name} state"


def test_the_recorder_films_every_clip_in_the_table_and_no_other():
    spec = (ROOT / "aura-web" / "e2e" / "readme-clips.spec.ts").read_text(encoding="utf-8")
    # The table's type holds an arrow, so the opening is found by its line, not by its first "=".
    scripts = re.search(r"^const SCRIPTS\b[^\n]*= \{\n(.*?)\n\};", spec, re.S | re.M)
    assert scripts
    filmed = set(re.findall(r"^\t([a-z][a-z0-9-]*): async", scripts.group(1), re.M))
    assert filmed == {c["name"] for c in CLIPS}


def test_the_readme_figures_palette_is_auras_own():
    """DESIGN.md's dark column is the palette `sampo readme diagram` draws the README's figures in.

    It is copied from the AURA theme pack, so a recoloured theme would leave the diagrams in the
    old colours; this names each colour that parted. The light column has no source to match:
    it exists only for figures on a light page.
    """
    pack = json.loads((ROOT / "themes" / "aura" / "theme.json").read_text(encoding="utf-8"))["color"]
    rows = re.findall(r"^\| `(--[a-z-]+)` \| colour \| (#[0-9A-F]{6}) \| (#[0-9A-F]{6}) \| ([a-z.]+) \|$",
                      (ROOT / "DESIGN.md").read_text(encoding="utf-8"), re.M)
    assert len(rows) >= 6, "DESIGN.md's ## Tokens table lost its rows"
    parted = [f"{name}: {dark} is not {source} {pack.get(source)}" for name, _light, dark, source in rows
              if str(pack.get(source, "")).upper() != dark]
    assert not parted, parted


def test_the_architecture_figure_is_drawn_from_its_spec_in_both_schemes():
    spec = json.loads((ROOT / "docs" / "assets" / "diagrams" / "architecture.json").read_text(encoding="utf-8"))
    for scheme in ("dark", "light"):
        svg = (ROOT / "docs" / "assets" / "diagrams" / f"architecture-{scheme}.svg").read_text(encoding="utf-8")
        missing = [n["label"] for n in spec["nodes"] if n["label"].replace("&", "&amp;") not in svg]
        assert not missing, f"architecture-{scheme}.svg is stale: redraw it (sampo readme diagram); missing {missing}"
    assert "docs/assets/diagrams/architecture-dark.svg" in README and "docs/assets/diagrams/architecture-light.svg" in README
