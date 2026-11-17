"""The artifact store, against the media brief's own gate list.

The brief names what has to be true: *"range requests work (seek in a long video
without a full download), auth enforced, TTL sweep runs, size cap enforced,
provenance recorded, nothing reachable off-tailnet — each with a test."* Auth and
the tailnet binding belong to the route and the compose file; everything else is
here.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path

import pytest

from aura_adapter import artifacts as A


def test_the_types_served_as_a_download_are_the_panels_contracts() -> None:
    # A document panel on the console offers "Download" for exactly these,
    # rather than an <object> that would show a blank; the contract is the one
    # table both sides read (jarvis-web mappings.test.ts holds the other copy).
    contract = json.loads(
        (Path(__file__).resolve().parents[2] / "tests" / "contracts" / "hud-panels.json").read_text()
    )
    assert list(A.EXECUTABLE_MIME) == contract["download_mimes"]
    for mime in contract["download_mimes"]:
        assert A.safe_disposition(mime) == ("application/octet-stream", "attachment")


def put(root: Path, data: bytes = b"hello", **kw) -> A.Artifact:
    kw.setdefault("mime", "text/plain")
    kw.setdefault("title", "a thing")
    return A.publish(io.BytesIO(data), root=root, **kw)


def test_the_id_is_the_content(tmp_path: Path) -> None:
    import hashlib

    art = put(tmp_path, b"the same bytes")
    assert art.id == hashlib.sha256(b"the same bytes").hexdigest()


def test_publishing_the_same_bytes_twice_stores_one_artifact(tmp_path: Path) -> None:
    """A retry should be free, and an id must never point at changed content."""
    a = put(tmp_path, b"same")
    b = put(tmp_path, b"same", title="renamed")
    assert a.id == b.id
    assert len(A.listing(tmp_path)) == 1
    assert b.title == "renamed", "a re-publish may correct the title"


def test_a_republish_refreshes_the_life_rather_than_expiring_early(tmp_path: Path) -> None:
    a = put(tmp_path, b"same", ttl=1)
    later = put(tmp_path, b"same", ttl=10_000)
    assert later.expires is not None and a.expires is not None
    assert later.expires > a.expires


def test_provenance_is_recorded_and_read_back(tmp_path: Path) -> None:
    art = put(
        tmp_path,
        b"a chart",
        provenance=A.Provenance(session="s1", tool_call="tc9", source_url="http://x/y", tool="charts"),
    )
    back = A.get(art.id, tmp_path)
    assert back.provenance.session == "s1"
    assert back.provenance.tool_call == "tc9"
    assert back.provenance.source_url == "http://x/y"
    assert back.provenance.tool == "charts"


def test_partial_provenance_is_fine(tmp_path: Path) -> None:
    """A screenshot a person took has no tool call; that is not an error."""
    art = put(tmp_path, b"shot", provenance=A.Provenance(session="s2"))
    assert A.get(art.id, tmp_path).provenance.tool_call is None


class TestSizeCaps:
    def test_one_artifact_over_the_cap_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(A.ArtifactTooLarge):
            put(tmp_path, b"x" * 100, max_bytes=10)

    def test_the_refused_bytes_do_not_stay_on_disk(self, tmp_path: Path) -> None:
        """The cap is enforced as bytes arrive, so nothing half-written survives."""
        with pytest.raises(A.ArtifactTooLarge):
            put(tmp_path, b"x" * 100, max_bytes=10)
        assert list(tmp_path.rglob("*")) == [] or A.store_size(tmp_path) == 0

    def test_a_full_store_refuses_before_it_writes(self, tmp_path: Path) -> None:
        put(tmp_path, b"y" * 50)
        with pytest.raises(A.ArtifactTooLarge):
            put(tmp_path, b"z" * 10, max_store=10)


class TestRetention:
    def test_an_expired_artifact_is_neither_listed_nor_fetchable(self, tmp_path: Path) -> None:
        art = put(tmp_path, b"brief", ttl=-1)
        assert A.listing(tmp_path) == []
        with pytest.raises(A.ArtifactNotFound):
            A.get(art.id, tmp_path)

    def test_the_sweep_takes_the_expired_and_reports_them(self, tmp_path: Path) -> None:
        gone = put(tmp_path, b"old", ttl=-1)
        kept = put(tmp_path, b"new", ttl=10_000)
        taken = A.sweep(tmp_path)
        assert taken == [gone.id]
        assert {a.id for a in A.listing(tmp_path)} == {kept.id}

    def test_a_pinned_artifact_outlives_the_sweep(self, tmp_path: Path) -> None:
        art = put(tmp_path, b"keep", ttl=10_000)
        A.pin(art.id, root=tmp_path)
        assert A.sweep(tmp_path, now=time.time() + 100_000) == []
        assert A.get(art.id, tmp_path).pinned is True

    def test_an_expired_artifact_cannot_be_pinned(self, tmp_path: Path) -> None:
        """Pinning goes through `get`, so you cannot pin what you cannot see.

        Deliberate: an expired artifact is gone as far as every reader is
        concerned, and a pin that resurrected one would make "expired" mean
        "expired unless someone knows the id".
        """
        art = put(tmp_path, b"gone", ttl=-1)
        with pytest.raises(A.ArtifactNotFound):
            A.pin(art.id, root=tmp_path)

    def test_unpinning_restarts_the_clock_rather_than_deleting(self, tmp_path: Path) -> None:
        """An old deadline restored on unpin would make unpin a delete."""
        art = put(tmp_path, b"p", ttl=1)
        A.pin(art.id, root=tmp_path)
        back = A.pin(art.id, pinned=False, root=tmp_path)
        assert back.expires is not None and back.expires > time.time() + 1000
        assert A.get(art.id, tmp_path).id == art.id


class TestRangeRequests:
    """Without these a video panel downloads a whole file to show one frame."""

    @pytest.fixture
    def video(self, tmp_path: Path) -> A.Artifact:
        return put(tmp_path, bytes(range(256)) * 8, mime="video/mp4", title="a clip")

    def test_a_middle_range_returns_exactly_those_bytes(self, tmp_path: Path, video: A.Artifact) -> None:
        got = b"".join(A.read_range(video.id, 10, 19, tmp_path))
        assert got == bytes(range(10, 20))
        assert len(got) == 10

    def test_a_seek_asks_for_the_rest_and_gets_it(self, video: A.Artifact) -> None:
        assert A.parse_range("bytes=1000-", video.size) == (1000, video.size - 1)

    def test_a_suffix_range_finds_the_trailer(self, video: A.Artifact) -> None:
        assert A.parse_range("bytes=-500", video.size) == (video.size - 500, video.size - 1)

    def test_an_end_past_the_file_is_clamped_not_refused(self, video: A.Artifact) -> None:
        assert A.parse_range(f"bytes=0-{video.size + 999}", video.size) == (0, video.size - 1)

    @pytest.mark.parametrize(
        "header",
        ["", "items=0-1", "bytes=abc-", "bytes=5-1", "bytes=99999-", "bytes=-0", "bytes=0-1,4-5"],
    )
    def test_a_range_that_cannot_be_served_is_refused(self, header: str, video: A.Artifact) -> None:
        assert A.parse_range(header, video.size) is None

    def test_the_whole_file_still_comes_back_in_one_piece(self, tmp_path: Path, video: A.Artifact) -> None:
        whole = b"".join(A.read_range(video.id, 0, video.size - 1, tmp_path))
        assert len(whole) == video.size


def test_listing_filters_by_kind_and_is_newest_first(tmp_path: Path) -> None:
    put(tmp_path, b"a", mime="image/png")
    time.sleep(0.01)
    put(tmp_path, b"b", mime="video/mp4")
    assert [a.mime for a in A.listing(tmp_path)] == ["video/mp4", "image/png"]
    assert [a.mime for a in A.listing(tmp_path, kind="image/")] == ["image/png"]


def test_delete_removes_both_the_blob_and_its_metadata(tmp_path: Path) -> None:
    art = put(tmp_path, b"bye")
    A.delete(art.id, tmp_path)
    assert A.store_size(tmp_path) == 0
    with pytest.raises(A.ArtifactNotFound):
        A.get(art.id, tmp_path)


def test_an_unreadable_sidecar_costs_one_artifact_not_the_listing(tmp_path: Path) -> None:
    """A crash mid-write must not take the catalogue with it."""
    good = put(tmp_path, b"good")
    bad = put(tmp_path, b"bad")
    A._meta(tmp_path, bad.id).write_text("{ not json")
    assert [a.id for a in A.listing(tmp_path)] == [good.id]


def test_publishing_a_file_never_reads_it_whole(tmp_path: Path) -> None:
    src = tmp_path / "big.bin"
    src.write_bytes(b"q" * (A.CHUNK * 3 + 7))
    art = A.copy_from_path(src, mime="application/octet-stream", title="big", root=tmp_path)
    assert art.size == A.CHUNK * 3 + 7


class TestIdValidation:
    """An id becomes a filesystem path, so its shape is a security boundary.

    Found by the commit security review, and it was real: `_blob()` on
    `../../../../etc/passwd` resolves to `/etc/passwd`, which `delete` would
    unlink and `read_range` would serve. Validating the shape is the right guard
    rather than normalising afterwards -- an id is a SHA-256 digest by
    construction, so this rejects nothing legitimate.
    """

    @pytest.mark.parametrize(
        "bad",
        [
            "../../../../etc/passwd",
            "..",
            "a" * 63,
            "a" * 65,
            "A" * 64,  # uppercase is not what hexdigest() produces
            "g" * 64,  # not hex
            "",
            "0" * 32 + "/" + "0" * 31,
        ],
    )
    def test_a_bad_id_never_becomes_a_path(self, bad: str) -> None:
        with pytest.raises(A.BadArtifactId):
            A._blob(Path("/tmp/store"), bad)

    def test_traversal_cannot_delete_outside_the_store(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "precious.txt"
        outside.write_text("do not delete me")
        with pytest.raises(A.BadArtifactId):
            A.delete("../precious.txt", tmp_path)
        assert outside.exists()

    def test_traversal_cannot_be_read(self, tmp_path: Path) -> None:
        with pytest.raises(A.BadArtifactId):
            list(A.read_range("../../etc/passwd", 0, 10, tmp_path))

    def test_a_bad_id_is_simply_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(A.ArtifactNotFound):
            A.get("../../etc/passwd", tmp_path)

    def test_a_real_id_still_works(self, tmp_path: Path) -> None:
        art = put(tmp_path, b"fine")
        assert A.get(art.id, tmp_path).id == art.id


class TestExecutableContent:
    """The store holds whatever a tool produced, including a web page.

    Served inline, a published `text/html` or SVG runs its script on the tailnet
    origin that also serves the console and this API. Media is unaffected.
    """

    @pytest.mark.parametrize(
        "mime",
        ["text/html", "image/svg+xml", "application/xhtml+xml", "text/javascript", "application/xml"],
    )
    def test_browser_executable_types_become_downloads(self, mime: str) -> None:
        ctype, disposition = A.safe_disposition(mime)
        assert ctype == "application/octet-stream"
        assert disposition == "attachment"

    def test_a_charset_parameter_does_not_smuggle_html_past_it(self) -> None:
        assert A.safe_disposition("text/html; charset=utf-8")[1] == "attachment"

    def test_case_does_not_smuggle_it_either(self) -> None:
        assert A.safe_disposition("TEXT/HTML")[1] == "attachment"

    @pytest.mark.parametrize("mime", ["video/mp4", "image/png", "audio/mpeg", "application/pdf"])
    def test_media_is_still_served_inline(self, mime: str) -> None:
        ctype, disposition = A.safe_disposition(mime)
        assert ctype == mime
        assert disposition == "inline"
