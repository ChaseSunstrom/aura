"""The plugin suite says when the Hermes it ran against is not the release production is built on.

`hermes/conftest.py` puts an installed Hermes on the path when the checkout has one beside it,
and a handful of tests then prove plugin behaviour against the real runtime. The pipeline runs
them in the image of the pinned release (`HERMES_TAG`, `test:hermes`); a developer's install
is whatever was cloned last. This desk's was a release behind for weeks and nothing said so: a
green `make test` read as proof against the deployed release (T-1456).

These tests build the few git files a checkout's release is read from, so they need neither
git nor a Hermes install and run in every image.

Not covered: an install that is not a git checkout (its release is unknown and the line says
so); whether the Hermes-dependent tests would pass on the pinned release, which only
`test:hermes` shows.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HEAD = "29" * 20
TAG_OBJECT = "6e" * 20
OTHER = "aa" * 20


def _conftest():
    spec = importlib.util.spec_from_file_location("hermes_conftest_under_test", ROOT / "hermes" / "conftest.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


conf = _conftest()


def _checkout(tmp_path: Path, head: str, packed: str = "", loose: dict[str, str] | None = None,
              refs: dict[str, str] | None = None) -> Path:
    git = tmp_path / "hermes-agent" / ".git"
    (git / "refs" / "tags").mkdir(parents=True)
    (git / "HEAD").write_text(head + "\n")
    if packed:
        (git / "packed-refs").write_text("# pack-refs with: peeled fully-peeled sorted\n" + packed)
    for name, sha in (loose or {}).items():
        (git / "refs" / "tags" / name).write_text(sha + "\n")
    for ref, sha in (refs or {}).items():
        (git / ref).parent.mkdir(parents=True, exist_ok=True)
        (git / ref).write_text(sha + "\n")
    return git.parent


def test_the_release_is_the_tag_the_checkout_stands_on_read_from_its_git_files(tmp_path):
    # As this desk's install is: a detached head, the tag annotated and packed with its peeled line.
    packed = f"{OTHER} refs/tags/v2026.7.1\n{TAG_OBJECT} refs/tags/v2026.8.31\n^{HEAD}\n{OTHER} refs/heads/main\n"
    assert conf.hermes_release(_checkout(tmp_path / "a", HEAD, packed)) == "v2026.8.31"
    # A lightweight tag, loose; and a head that is a branch, itself loose or packed.
    assert conf.hermes_release(_checkout(tmp_path / "b", HEAD, loose={"v2026.9.24": HEAD})) == "v2026.9.24"
    on_branch = _checkout(tmp_path / "c", "ref: refs/heads/main", loose={"v2026.9.24": HEAD}, refs={"refs/heads/main": HEAD})
    assert conf.hermes_release(on_branch) == "v2026.9.24"
    packed_branch = f"{HEAD} refs/heads/main\n{HEAD} refs/tags/v2026.9.24\n"
    assert conf.hermes_release(_checkout(tmp_path / "d", "ref: refs/heads/main", packed_branch)) == "v2026.9.24"


def test_a_checkout_between_releases_or_without_git_has_no_release_and_nothing_is_guessed(tmp_path):
    # A commit no tag names (a branch tip past the last release), also when a tag object happens to carry its sha.
    assert conf.hermes_release(_checkout(tmp_path / "a", OTHER, f"{TAG_OBJECT} refs/tags/v2026.8.31\n^{HEAD}\n")) is None
    plain = tmp_path / "copied" / "hermes-agent"
    plain.mkdir(parents=True)
    assert conf.hermes_release(plain) is None, "an install copied without its git folder (CI's image)"
    assert conf.hermes_release(_checkout(tmp_path / "b", "ref: refs/heads/gone")) is None
    assert conf.hermes_release(_checkout(tmp_path / "c", "not a sha")) is None


def test_the_pin_is_the_one_the_pipeline_and_the_image_are_built_from():
    pinned = conf.pinned_release()
    assert re.fullmatch(r"v\d{4}\.\d+\.\d+", pinned or ""), pinned
    assert f"ARG HERMES_TAG={pinned}\n" in (ROOT / "deploy" / "Dockerfile").read_text()


def test_the_line_names_both_releases_and_is_silent_when_they_match_or_no_install_was_found(tmp_path):
    older = _checkout(tmp_path / "a", HEAD, f"{TAG_OBJECT} refs/tags/v2026.8.31\n^{HEAD}\n")
    line = conf.release_line(older, "v2026.9.24")
    assert line and "v2026.8.31" in line and "v2026.9.24" in line and "test:hermes" in line
    assert "hermes-agent" in line, "where it was found, so the install to move is named"
    same = _checkout(tmp_path / "b", HEAD, loose={"v2026.9.24": HEAD})
    assert conf.release_line(same, "v2026.9.24") is None
    assert conf.release_line(None, "v2026.9.24") is None, "no install: the Hermes-dependent tests skip and say so themselves"
    # An install whose release cannot be read is not called current.
    unknown = conf.release_line(_checkout(tmp_path / "c", OTHER), "v2026.9.24")
    assert unknown and "unknown" in unknown and "v2026.9.24" in unknown
    # The pipeline's image has no git folder (looked at: v2026.9.24 has none), and its job says
    # which image it is: unknown there is the pin, not a finding. A release that was read still is.
    bare = tmp_path / "image" / "hermes-agent"
    bare.mkdir(parents=True)
    assert conf.release_line(bare, "v2026.9.24", image_tag="v2026.9.24") is None
    assert conf.release_line(bare, "v2026.9.24", image_tag="v2026.8.31") is not None
    assert conf.release_line(older, "v2026.9.24", image_tag="v2026.9.24") is not None
    assert conf.release_line(older, None) is None, "no pin to compare with: nothing is claimed"


def test_the_line_is_written_at_the_end_of_a_run_where_a_quiet_run_still_shows_it(monkeypatch, tmp_path):
    """`make test` runs pytest with -q, which drops the report header: the line is in the end summary."""
    older = _checkout(tmp_path, HEAD, f"{TAG_OBJECT} refs/tags/v2026.8.31\n^{HEAD}\n")
    written: list[str] = []

    class Reporter:
        def write_line(self, text: str, **_markup) -> None:
            written.append(text)

    monkeypatch.setattr(conf, "HERMES_INSTALL", older)
    monkeypatch.setattr(conf, "pinned_release", lambda: "v2026.9.24")
    monkeypatch.delenv("HERMES_TAG", raising=False)
    conf.pytest_terminal_summary(Reporter())
    assert len(written) == 1 and written[0].startswith("Hermes found") and "v2026.8.31" in written[0]
    written.clear()
    monkeypatch.setattr(conf, "HERMES_INSTALL", None)
    conf.pytest_terminal_summary(Reporter())
    assert written == []
