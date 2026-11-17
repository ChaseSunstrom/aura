"""A content-addressed artifact store.

The media brief's first step, and it is deliberately first: *"Artifact store
service, artifact tool, delivery events, retention -- with tests, before any
UI."* The pattern it exists to retire is named in the brief too -- "here's a path
on the container" -- which is useless to every surface that is not a shell on
that container.

**Content-addressed** means the id is the SHA-256 of the bytes. Two tools that
produce the same image produce one artifact, a re-publish after a retry is free,
and an id can never point at content that changed underneath it. The cost is
that metadata has to live beside the blob rather than in its name, which is what
the sidecar JSON is for.

**Why in the adapter.** The brief asks for "a single service ... auth by the same
bearer key as the API server; nothing reachable off the tailnet". The adapter is
already that service: it terminates the apps' bearer key, binds to the tailnet
only, and mounts the volume. A second container would need its own copy of all
three and would still have to be reached through this one.

Everything here is filesystem work with no database. A store this size does not
need one, and a sidecar per blob survives a crash mid-write in a way a single
index file does not: a half-written sidecar loses one artifact's metadata, not
the catalogue.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Iterator

_LOGGER = logging.getLogger(__name__)

#: Where blobs live: a volume of the store's own (`jarvis-artifacts` in the
#: compose file), *beside* the Hermes home and not under it. The home is mounted
#: read-only on purpose -- releasing the kill switch must stay a host-side act --
#: so the first cut (`/opt/data/artifacts`) could never write, and every publish
#: was an EROFS traceback. Nesting a writable volume under the read-only one is
#: not the fix either: runc has to make the mountpoint on the parent, and on a
#: read-only parent that fails at container start ("make mountpoint ...
#: read-only file system", tried 2026-09-18). The image creates this directory
#: owned by the adapter user so a fresh volume takes that ownership.
ROOT = Path("/opt/artifacts")

#: How long an unpinned artifact lives, in seconds. A day is long enough that a
#: conversation about a video does not outlive it, short enough that a week of
#: generated images does not fill the volume.
DEFAULT_TTL = 24 * 60 * 60

#: Refuse a single artifact larger than this. The volume is shared with the
#: model cache and the Hermes state; one careless download must not fill it.
MAX_BYTES = 2 * 1024 * 1024 * 1024

#: Stop accepting new artifacts when the store exceeds this. Checked before a
#: write rather than after, so the cap is a limit and not a high-water mark. The
#: volume is the store's own, but the disk under it is the host's.
MAX_STORE_BYTES = 8 * 1024 * 1024 * 1024

#: Read blobs in chunks rather than whole: a 2 GB video must not become 2 GB of
#: resident memory because someone asked for it.
CHUNK = 256 * 1024


class ArtifactTooLarge(Exception):
    """The artifact, or the store with it, would exceed its cap."""


class ArtifactNotFound(Exception):
    """No artifact with that id, or it has expired."""


@dataclass
class Provenance:
    """Where an artifact came from.

    Every field is optional because a screenshot taken by a person has no tool
    call and a chart drawn from a query has no source URL, and an artifact with
    partial provenance is far better than one with none.
    """

    session: str | None = None
    tool_call: str | None = None
    source_url: str | None = None
    tool: str | None = None


@dataclass
class Artifact:
    id: str
    mime: str
    title: str
    size: int
    created: float
    #: Absolute epoch seconds, or None when pinned.
    expires: float | None
    pinned: bool = False
    provenance: Provenance = field(default_factory=Provenance)

    @property
    def expired(self) -> bool:
        return not self.pinned and self.expires is not None and self.expires <= time.time()


#: An id is a SHA-256 hex digest and nothing else.
_ID = re.compile(r"^[0-9a-f]{64}$")


class BadArtifactId(ValueError):
    """The id is not a SHA-256 hex digest."""


def check_id(ident: str) -> str:
    """Refuse anything that is not a hex digest, before it becomes a path.

    Ids come off a URL path and out of tool arguments, and every one of them is
    turned into a filesystem path. Without this, `../../../../etc/passwd` walks
    out of the store: `_blob()` on that resolves to `/etc/passwd`, which `delete`
    would unlink and `read_range` would happily serve. Validating the *shape* is
    the right guard rather than normalising the path afterwards, because an id is
    a digest by construction -- there is no legitimate id this rejects.
    """
    if not _ID.match(ident or ""):
        raise BadArtifactId(f"not an artifact id: {ident!r}")
    return ident


def _blob(root: Path, ident: str) -> Path:
    # Two levels of fan-out: a flat directory of a few thousand entries is slow
    # to list on some filesystems and unpleasant to look at on all of them.
    check_id(ident)
    return root / ident[:2] / ident[2:4] / ident


def _meta(root: Path, ident: str) -> Path:
    return _blob(root, ident).with_suffix(".json")


def _load(root: Path, ident: str) -> Artifact | None:
    try:
        check_id(ident)
    except BadArtifactId:
        return None
    try:
        raw = json.loads(_meta(root, ident).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    prov = raw.pop("provenance", {}) or {}
    try:
        return Artifact(**raw, provenance=Provenance(**prov))
    except TypeError:
        # A sidecar from an older shape. Losing one artifact's metadata is not a
        # reason to fail the listing that found it.
        _LOGGER.warning("artifacts: unreadable sidecar for %s", ident)
        return None


def _save(root: Path, art: Artifact) -> None:
    path = _meta(root, art.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write-then-rename, so a reader never sees a half-written sidecar.
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    tmp.write_text(json.dumps(asdict(art)), encoding="utf-8")
    tmp.replace(path)


def store_size(root: Path | None = None) -> int:
    root = root or ROOT
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and p.suffix != ".json")


def publish(
    source: BinaryIO,
    *,
    mime: str,
    title: str,
    provenance: Provenance | None = None,
    ttl: float | None = DEFAULT_TTL,
    root: Path | None = None,
    max_bytes: int = MAX_BYTES,
    max_store: int = MAX_STORE_BYTES,
) -> Artifact:
    """Take bytes, return an artifact.

    Streamed to a temporary file while hashing, so the size cap is enforced as
    the bytes arrive rather than after a 2 GB upload has already landed.
    """
    root = root or ROOT
    root.mkdir(parents=True, exist_ok=True)
    if store_size(root) >= max_store:
        raise ArtifactTooLarge(f"the store is at its {max_store} byte cap")

    tmp = root / f".incoming-{uuid.uuid4().hex}"
    digest = hashlib.sha256()
    size = 0
    try:
        with tmp.open("wb") as out:
            while chunk := source.read(CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise ArtifactTooLarge(f"artifact exceeds the {max_bytes} byte cap")
                digest.update(chunk)
                out.write(chunk)

        ident = digest.hexdigest()
        blob = _blob(root, ident)
        blob.parent.mkdir(parents=True, exist_ok=True)
        existing = _load(root, ident)
        if existing and blob.exists():
            # Same bytes, so the same artifact. Refresh its life rather than
            # storing a second copy: a retry should be free.
            existing.expires = None if ttl is None else time.time() + ttl
            existing.title = title or existing.title
            _save(root, existing)
            return existing
        tmp.replace(blob)
        art = Artifact(
            id=ident,
            mime=mime,
            title=title,
            size=size,
            created=time.time(),
            expires=None if ttl is None else time.time() + ttl,
            provenance=provenance or Provenance(),
        )
        _save(root, art)
        return art
    finally:
        tmp.unlink(missing_ok=True)


def get(ident: str, root: Path | None = None) -> Artifact:
    root = root or ROOT
    if not _ID.match(ident or ""):
        raise ArtifactNotFound(ident)
    art = _load(root, ident)
    if art is None or not _blob(root, ident).exists() or art.expired:
        raise ArtifactNotFound(ident)
    return art


def listing(root: Path | None = None, kind: str = "") -> list[Artifact]:
    """Everything live, newest first. Expired artifacts are not listed."""
    root = root or ROOT
    if not root.is_dir():
        return []
    out: list[Artifact] = []
    for meta in root.rglob("*.json"):
        art = _load(root, meta.stem)
        if art and not art.expired and (not kind or art.mime.startswith(kind)):
            out.append(art)
    out.sort(key=lambda a: a.created, reverse=True)
    return out


def pin(ident: str, pinned: bool = True, root: Path | None = None) -> Artifact:
    root = root or ROOT
    art = get(ident, root)
    art.pinned = pinned
    # Unpinning restarts the clock rather than resurrecting an old deadline that
    # may already have passed -- otherwise unpin would be a delete.
    art.expires = None if pinned else time.time() + DEFAULT_TTL
    _save(root, art)
    return art


def delete(ident: str, root: Path | None = None) -> None:
    root = root or ROOT
    check_id(ident)
    _blob(root, ident).unlink(missing_ok=True)
    _meta(root, ident).unlink(missing_ok=True)


class PartialErase(OSError):
    """`delete_window` removed `erased` artifacts and could not remove `failed` more."""

    def __init__(self, erased: int, failed: int) -> None:
        super().__init__(f"{erased} erased, {failed} could not be")
        self.erased, self.failed = erased, failed


def delete_window(minutes: int, root: Path | None = None, now: float | None = None) -> int:
    """Delete every artifact made in the last `minutes`, pinned ones too, and return how many.

    Goes by the sidecar's `created`, so it takes the ones the sweep has not yet taken though
    expired. Raises `PartialErase` after trying them all when any delete failed, so a caller
    never reads a half erase as a whole one. Does NOT cover: a blob with no readable sidecar
    (nothing says when it was made), or bytes published again in the window that an older
    artifact already holds (the store keeps one copy and its first `created`)."""
    root = root or ROOT
    if not root.is_dir():
        return 0
    since = (now if now is not None else time.time()) - minutes * 60
    erased = failed = 0
    for meta in list(root.rglob("*.json")):
        art = _load(root, meta.stem)
        if art is None or art.created < since:
            continue
        try:
            delete(art.id, root)
            erased += 1
        except OSError:
            failed += 1
    if failed:
        raise PartialErase(erased, failed)
    return erased


def sweep(root: Path | None = None, now: float | None = None) -> list[str]:
    """Remove expired artifacts. Returns the ids taken, for the audit log."""
    root = root or ROOT
    if not root.is_dir():
        return []
    when = now if now is not None else time.time()
    taken: list[str] = []
    for meta in list(root.rglob("*.json")):
        art = _load(root, meta.stem)
        if art and not art.pinned and art.expires is not None and art.expires <= when:
            delete(art.id, root)
            taken.append(art.id)
    return taken


def read_range(
    ident: str, start: int, end: int, root: Path | None = None
) -> Iterator[bytes]:
    """Yield ``[start, end]`` inclusive, in chunks.

    Range requests are not an optimisation here: without them a video panel
    downloads a whole file before it can show a frame, and seeking is impossible.
    The brief gates on it.
    """
    root = root or ROOT
    path = _blob(root, ident)  # validates the id
    remaining = end - start + 1
    with path.open("rb") as fh:
        fh.seek(start)
        while remaining > 0:
            chunk = fh.read(min(CHUNK, remaining))
            if not chunk:
                return
            remaining -= len(chunk)
            yield chunk


def parse_range(header: str, size: int) -> tuple[int, int] | None:
    """``bytes=start-end`` → an inclusive pair, or None when unsatisfiable.

    Handles the two open-ended forms browsers actually send: ``bytes=1000-`` for
    "the rest from here", which is what a seek produces, and ``bytes=-500`` for
    "the last 500 bytes", which is how some players find a container's trailer.
    """
    if not header.startswith("bytes=") or "," in header:
        return None
    spec = header[len("bytes=") :].strip()
    try:
        if spec.startswith("-"):
            length = int(spec[1:])
            if length <= 0:
                return None
            return max(0, size - length), size - 1
        first, _, last = spec.partition("-")
        start = int(first)
        end = int(last) if last else size - 1
    except ValueError:
        return None
    if start < 0 or start >= size or end < start:
        return None
    return start, min(end, size - 1)


def copy_from_path(path: Path, **kwargs: Any) -> Artifact:
    """Publish a file already on disk, without reading it into memory."""
    with path.open("rb") as fh:
        return publish(fh, **kwargs)


#: MIME types a browser will execute if it renders them inline. Served as a
#: download instead: the store holds whatever a tool produced, and a published
#: `text/html` or SVG rendered inline would run its script on the tailnet origin
#: that also serves the console and the API. Media types are unaffected, so a
#: video still streams and an image still shows.
EXECUTABLE_MIME = (
    "text/html",
    "application/xhtml+xml",
    "image/svg+xml",
    "application/xml",
    "text/xml",
    "application/javascript",
    "text/javascript",
)


def safe_disposition(mime: str) -> tuple[str, str]:
    """The content type and disposition to serve an artifact with.

    Returns the type unchanged and `inline` for anything a browser only
    displays, and `application/octet-stream` with `attachment` for anything it
    would execute.
    """
    base = (mime or "").split(";", 1)[0].strip().lower()
    if base in EXECUTABLE_MIME:
        return "application/octet-stream", "attachment"
    return mime or "application/octet-stream", "inline"


__all__ = [
    "Artifact",
    "ArtifactNotFound",
    "ArtifactTooLarge",
    "BadArtifactId",
    "EXECUTABLE_MIME",
    "check_id",
    "safe_disposition",
    "Provenance",
    "copy_from_path",
    "delete",
    "get",
    "listing",
    "parse_range",
    "pin",
    "publish",
    "read_range",
    "store_size",
    "sweep",
]
