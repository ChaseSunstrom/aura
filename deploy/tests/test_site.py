"""The Pages site (site/, T-0866): what must hold of its sources with no browser and no node.

The built page is held by ``jarvis-web/e2e/site.spec.ts``, which builds it and opens it. This
file holds what a build cannot show: that the template types no address and no claim of its
own to drift from the README, that every clip in the table has the still its film shows first,
that nothing in the site reaches outside or keeps anything, that it says how it behaves under
reduced motion, and that the workflow GitHub runs is the one that deploys the folder.

Not covered: the look, and GitHub's deploy itself.
"""
from __future__ import annotations

import json
import re
import struct
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "site"
TABLE = json.loads((ROOT / "tests" / "contracts" / "readme-clips.json").read_text(encoding="utf-8"))
CLIPS = TABLE["clips"]
TEMPLATE = (SITE / "index.html").read_text(encoding="utf-8")
SOURCES = [SITE / "index.html", SITE / "site.css", SITE / "build.mjs", *sorted((SITE / "src").glob("*.ts"))]
#: The largest a still may be: it is the first thing a phone downloads for each film.
POSTER_BYTES = 150_000


def _code(path: Path) -> str:
    """A source without its comments: a comment may name what the code must not do."""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/|<!--.*?-->", "", text, flags=re.S)
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))


def test_the_template_types_no_address_and_takes_its_words_from_the_readme():
    """An address or a paragraph typed here would be a second copy to keep true."""
    for path in (SITE / "index.html", SITE / "site.css", *sorted((SITE / "src").glob("*.ts"))):
        assert not re.search(r"https?://", path.read_text(encoding="utf-8")), f"{path.name} types an address"
    # build.mjs names the two hosts the install line is made of, to derive the repository from it.
    hosts = set(re.findall(r"https?:\\?/\\?/([\w.\\-]+)", (SITE / "build.mjs").read_text(encoding="utf-8")))
    assert {h.replace("\\", "") for h in hosts} <= {"raw.githubusercontent.com", "github.com"}, hosts
    assert "@@features@@" in TEMPLATE
    assert "@@install-sh@@" in TEMPLATE and "@@install-ps@@" in TEMPLATE
    # Every paragraph marker names words the README still has: a reworded README stops the
    # build, and this says so before a push does.
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    marks = re.findall(r"@@(?:para|plain):([^:@]*):([^@]+)@@", TEMPLATE)
    assert len(marks) >= 6
    for heading, starts in marks:
        assert starts in readme, f"the README has no paragraph starting {starts!r}"
        assert not heading or f"## {heading} " in readme, f"the README has no section {heading!r}"


def test_a_film_the_template_places_by_hand_is_in_the_table():
    placed = set(re.findall(r"@@(?:film|number):([a-z-]+)@@", TEMPLATE))
    assert placed <= {c["name"] for c in CLIPS}, placed


@pytest.mark.parametrize("clip", CLIPS, ids=lambda c: c["name"])
def test_every_clip_has_the_still_its_film_shows_first(clip):
    poster = SITE / "posters" / f"{clip['name']}.jpg"
    assert poster.is_file(), f"no still for {clip['name']}: `make readme-clips` writes it"
    data = poster.read_bytes()
    assert data[:3] == b"\xff\xd8\xff", "not a JPEG"
    assert len(data) <= POSTER_BYTES, f"{len(data)} bytes"
    assert 0 < clip["poster_at"] < 1, "a still is cut at a share of the film's length"


def _boxes(data: bytes, start: int, end: int):
    """The MP4 boxes between two offsets, as (kind, where its content starts, where it ends)."""
    at = start
    while at + 8 <= end:
        size, kind = struct.unpack(">I4s", data[at:at + 8])
        head = 8
        if size == 1:
            size, head = struct.unpack(">Q", data[at + 8:at + 16])[0], 16
        elif size == 0:
            size = end - at
        assert size >= head, "a box shorter than its own head"
        yield kind.decode("latin1"), at + head, at + size
        at += size


@pytest.mark.parametrize("clip", CLIPS, ids=lambda c: c["name"])
def test_every_film_is_one_a_browser_plays_and_starts_before_it_has_all_arrived(clip):
    """The browser the page is tested in decodes no H.264, so nothing there shows a film is
    playable. This reads the file: H.264 video, a length, and its index ahead of its frames
    (an index at the end makes a phone fetch the whole film before the first frame).

    Not covered: that every frame decodes; the films were watched when they were made.
    """
    data = (SITE / "media" / f"{clip['name']}.mp4").read_bytes()
    top = list(_boxes(data, 0, len(data)))
    order = [kind for kind, _, _ in top]
    assert order[0] == "ftyp" and "moov" in order and "mdat" in order, order
    assert order.index("moov") < order.index("mdat"), "the index is after the frames: encode with +faststart"
    assert b"avc1" in data, "not H.264"
    moov = next((a, b) for kind, a, b in top if kind == "moov")
    mvhd = next(a for kind, a, _ in _boxes(data, *moov) if kind == "mvhd")
    if data[mvhd] == 0:
        scale, length = struct.unpack(">II", data[mvhd + 12:mvhd + 20])
    else:
        scale, length = struct.unpack(">IQ", data[mvhd + 20:mvhd + 32])
    assert 3 <= length / scale <= 40, f"{length / scale:.1f} s is not a short clip"


def test_no_still_is_an_orphan():
    assert {p.name for p in (SITE / "posters").iterdir()} == {f"{c['name']}.jpg" for c in CLIPS}


def test_the_site_reaches_nowhere_and_keeps_nothing():
    """Self-contained: no tracker, no outside font or script, nothing fetched, nothing stored."""
    banned = r"fetch\(|XMLHttpRequest|sendBeacon|WebSocket|EventSource|localStorage|sessionStorage|document\.cookie|indexedDB"
    for path in sorted((SITE / "src").glob("*.ts")):
        assert not re.search(banned, _code(path)), f"{path.name} reaches out or stores"
    css = _code(SITE / "site.css")
    assert "@import" not in css and "url(" not in css
    policy = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', TEMPLATE)
    assert policy, "the page states no content policy"
    assert "default-src 'self'" in policy.group(1)
    assert "unsafe" not in policy.group(1) and "http" not in policy.group(1) and "*" not in policy.group(1)
    assert not re.search(r"<script(?![^>]*\bsrc=)|<style|\sstyle=|\son[a-z]+=", _code(SITE / "index.html")), \
        "an inline script, style or handler: the policy would refuse it"


def test_the_site_says_what_it_does_under_reduced_motion():
    assert "@media (prefers-reduced-motion: reduce)" in (SITE / "site.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion: reduce" in _code(SITE / "src" / "site.ts")
    assert "prefers-reduced-motion: reduce" in _code(SITE / "src" / "core.ts")


def test_the_sites_styles_are_under_the_token_lint():
    lint = (ROOT / "scripts" / "verify" / "token_lint.py").read_text(encoding="utf-8")
    roots = re.search(r"^WEB_ROOTS = \(([^)]*)\)", lint, re.M)
    assert roots and '"site"' in roots.group(1), "site/ is not in the token lint's roots"


def test_the_pages_workflow_builds_the_folder_and_deploys_it_from_main_only():
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8"))
    on = doc[True]  # YAML reads the key `on` as a boolean
    assert on["push"]["branches"] == ["main"]
    assert "pull_request" not in on, "a pull request must not publish the site"
    for needed in ("site/**", "README.md", "tests/contracts/readme-clips.json", "jarvis-web/src/lib/ds/core/**"):
        assert needed in on["push"]["paths"], needed
    build, deploy = doc["jobs"]["build"], doc["jobs"]["deploy"]
    runs = [step.get("run", "") for step in build["steps"]]
    assert "make site" in runs
    upload = next(step for step in build["steps"] if "upload-pages-artifact" in step.get("uses", ""))
    assert upload["with"]["path"] == "_site"
    assert deploy["needs"] == "build"
    # Only the job that publishes can publish, and it can do nothing else.
    assert deploy["permissions"] == {"pages": "write", "id-token": "write"}
    assert "permissions" not in build
    assert deploy["environment"]["name"] == "github-pages"
    assert any("deploy-pages" in step.get("uses", "") for step in deploy["steps"])


def test_make_site_is_the_build_the_workflow_runs_and_its_output_is_not_tracked():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = re.search(r"^site:.*\n((?:\t.*\n)+)", makefile, re.M)
    assert recipe and "site/build.mjs" in recipe.group(1)
    assert "_site" in recipe.group(1)
    assert "/_site/" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_the_turns_drawing_lights_each_step_the_readme_numbers_and_no_other():
    """The drawing in the flow section is typed in the template; the steps come from the README.
    A step added to the README with nothing lit for it, or a part lit for a step that is gone,
    would leave the drawing dark or describing a different turn."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("### How a turn flows", 1)[1].split("\n#", 1)[0]
    steps = {m for m in re.findall(r"^(\d+)\. ", section, re.M)}
    assert steps == {str(n) for n in range(1, len(steps) + 1)} and len(steps) >= 5
    lit = set()
    for on in re.findall(r'data-on="([^"]+)"', TEMPLATE):
        lit |= set(on.split())
    assert lit == steps, f"steps the README numbers: {sorted(steps)}; steps the drawing lights: {sorted(lit)}"
    for back in re.findall(r'data-back="([^"]+)"', TEMPLATE):
        assert set(back.split()) <= lit
