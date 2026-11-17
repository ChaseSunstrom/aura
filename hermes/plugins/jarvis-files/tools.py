"""WebDAV client for the ``files_share`` toolset, over the shared ``_shared._dav`` layer.

The share is rooted at ``{DAV_URL}/{WEBDAV_PATH}/{DAV_USERNAME}/`` (Nextcloud's
``/remote.php/dav/files/alice/``); every tool path is relative to that root, so
``Documents/notes.md`` is the whole argument a caller ever passes.

The toolset is ``files_share`` and not ``file``: Hermes already owns a ``file``
toolset for the local filesystem, and a second registration under that name
would shadow it — the model would be offered two different ``file_read`` tools
and pick by luck.

What this does *not* cover: no move, copy, lock, share-link or versioning; no
directory creation (MKCOL); no chunked upload, so a write is one PUT and a read
is one GET, both bounded by :data:`MAX_BYTES`. The read path is for text a model
can actually use — configuration, notes, CSV — and refuses binary rather than
returning mojibake.
"""
from __future__ import annotations

from typing import Any
import time
from urllib.parse import quote, unquote

import httpx

from aura_schema import from_env

from _shared import _dav, _house
from _shared._approval_hook import split_session

#: Read/write ceiling, in bytes. A model's context is the real limit here: a
#: megabyte of YAML crowds out the conversation that asked for it, and a file
#: that large is a job for a script, not a tool call.
MAX_BYTES: int = 256 * 1024

#: Tools that change the share and therefore pass the approval gate.
GATED_TOOLS: tuple[str, ...] = ("fileshare_write", "fileshare_delete")

#: Content types read as text. Everything else is refused: a JPEG decoded as
#: UTF-8-with-replacement is thousands of tokens of noise that looks like data.
_TEXT_TYPES: tuple[str, ...] = ("text/", "application/json", "application/xml",
                                "application/javascript", "application/x-yaml",
                                "application/yaml", "application/toml",
                                "application/x-sh", "message/rfc822")

#: PROPFIND body for a listing: the properties a file browser shows, no more.
_FILE_PROPS = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:"><d:prop>'
    "<d:displayname/><d:resourcetype/><d:getcontentlength/>"
    "<d:getcontenttype/><d:getlastmodified/><d:getetag/>"
    "</d:prop></d:propfind>"
)


def root() -> str:
    """The share root, relative to ``DAV_URL``."""
    cfg = from_env()
    return _dav.join(cfg.webdav_path, cfg.dav_username)


def resolve(path: str) -> str:
    """A caller's path as a path under :func:`root`.

    Raises ``ValueError`` on a ``..`` segment: the DAV server resolves it before
    we ever see it, so ``../../other-user/private`` would walk straight out of
    the share the operator meant to expose. A leading slash is stripped rather
    than rejected — it reads as "the root of the share" to everybody who types it.
    """
    cleaned = (path or "").strip().lstrip("/")
    if any(part == ".." for part in cleaned.replace("\\", "/").split("/")):
        raise ValueError(f"path {path!r} escapes the share root with '..'")
    # Percent-encoded, because the path goes into a URL: unencoded, `C# notes.md` is a request
    # for `C` with a fragment, and a sibling file named `C` would be read in its place.
    return quote(_dav.join(root(), cleaned), safe="/")


def _relative(href: str) -> str:
    """A multistatus href as a path relative to the share root.

    The href is server-absolute and percent-encoded
    (``/remote.php/dav/files/alice/My%20Docs/a.txt``); callers pass paths back
    in, so what comes out must be what goes in — decoded, and with the root cut.
    """
    decoded = unquote(href or "").rstrip("/")
    marker = "/" + root().strip("/")
    if marker != "/" and marker in decoded:
        return decoded.split(marker, 1)[1].lstrip("/")
    return decoded.lstrip("/")


def _is_text(content_type: str) -> bool:
    kind = (content_type or "").split(";", 1)[0].strip().lower()
    if not kind:
        # A server that omits getcontenttype (Radicale, some proxies) is not a
        # reason to refuse: the decode check below is the real guard.
        return True
    return kind.startswith(_TEXT_TYPES) or kind.endswith(("+json", "+xml"))


def _row(entry: dict[str, Any]) -> dict[str, Any]:
    props = entry.get("props") or {}
    path = _relative(entry.get("href", ""))
    length = props.get("getcontentlength") or ""
    return {
        "path": path,
        "name": str(props.get("displayname") or "").strip() or path.rsplit("/", 1)[-1],
        "is_dir": _dav.is_collection(props),
        "size": int(length) if str(length).isdigit() else None,
        "content_type": str(props.get("getcontenttype") or ""),
        "modified": str(props.get("getlastmodified") or ""),
    }


def listing(path: str = "") -> dict[str, Any]:
    """The entries directly under *path* (a directory relative to the share root)."""
    if not _dav.available():
        return _dav.not_configured()
    target = resolve(path)
    here = _relative("/" + target)
    rows = []
    for entry in _dav.propfind(target, depth=1, body=_FILE_PROPS):
        row = _row(entry)
        # A depth-1 PROPFIND returns the directory itself first; listing it as
        # its own child makes every caller that recurses loop forever.
        if row["path"].strip("/") == here.strip("/"):
            continue
        rows.append(row)
    rows.sort(key=lambda item: (not item["is_dir"], item["path"].lower()))
    return {"ok": True, "path": here, "entries": rows, "count": len(rows)}


def _refuse_oversize(path: str, size: int) -> dict[str, Any]:
    return {"ok": False, "error": f"{path!r} is {size} bytes; fileshare_read refuses anything "
                                 f"over the {MAX_BYTES} byte (256 KB) limit"}


def read(path: str) -> dict[str, Any]:
    """GET one text file under the share root, refusing binary and oversized files.

    The size is checked with a PROPFIND *before* the GET, so a 2 GB video is
    never pulled into memory to be rejected; the post-GET check is the fallback
    for a server that does not report ``getcontentlength``.
    """
    if not _dav.available():
        return _dav.not_configured()
    if not (path or "").strip():
        return {"ok": False, "error": "path is required"}
    target = resolve(path)
    head = _dav.propfind(target, depth=0, body=_FILE_PROPS)
    props = (head[0].get("props") if head else {}) or {}
    if _dav.is_collection(props):
        return {"ok": False, "error": f"{path!r} is a directory — use fileshare_list"}
    declared = str(props.get("getcontentlength") or "")
    if declared.isdigit() and int(declared) > MAX_BYTES:
        return _refuse_oversize(path, int(declared))
    content_type = str(props.get("getcontenttype") or "")
    if not _is_text(content_type):
        return {"ok": False, "error": f"{path!r} is {content_type}, not text; fileshare_read "
                                      "only returns text files"}
    response = _dav.request("GET", target)
    response.raise_for_status()
    raw = response.content
    if len(raw) > MAX_BYTES:
        return _refuse_oversize(path, len(raw))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "error": f"{path!r} is not UTF-8 text; fileshare_read only "
                                      "returns text files"}
    if "\x00" in text:
        return {"ok": False, "error": f"{path!r} contains NUL bytes; fileshare_read only "
                                      "returns text files"}
    return {"ok": True, "path": _relative("/" + target), "bytes": len(raw),
            "content_type": content_type or "text/plain", "content": text}


def write(path: str, content: str) -> dict[str, Any]:
    """PUT a text file. Approval-gated: it overwrites whatever is at *path*."""
    if not _dav.available():
        return _dav.not_configured()
    if not (path or "").strip():
        return {"ok": False, "error": "path is required"}
    payload = str(content or "").encode("utf-8")
    if len(payload) > MAX_BYTES:
        return {"ok": False, "error": f"{len(payload)} bytes is over the {MAX_BYTES} byte "
                                      "(256 KB) limit fileshare_write accepts"}
    target = resolve(path)
    response = _dav.request("PUT", target, content=payload,
                            headers={"Content-Type": "text/plain; charset=utf-8"})
    response.raise_for_status()
    return {"ok": True, "path": _relative("/" + target), "bytes": len(payload),
            "http": response.status_code}


def delete(path: str) -> dict[str, Any]:
    """DELETE a file or directory. Approval-gated, and DAV has no undo.

    An empty path is refused rather than resolved: it would address the share
    root, and a DELETE there takes every file with it.
    """
    if not _dav.available():
        return _dav.not_configured()
    if not (path or "").strip().strip("/"):
        return {"ok": False, "error": "path is required — refusing to delete the share root"}
    target = resolve(path)
    response = _dav.request("DELETE", target)
    response.raise_for_status()
    return {"ok": True, "path": _relative("/" + target), "http": response.status_code}


def build_approval(tool_name: str, args: dict) -> dict | None:
    """The approval directive for a writing tool, or None to let a read through.

    The message names the path and the size, never a credential; the rule key is
    per path so approving one file does not stand in for approving the share.
    """
    if tool_name not in GATED_TOOLS:
        return None
    path = str(args.get("path") or "?").strip()
    if tool_name == "fileshare_delete":
        return {"action": "approve",
                "message": f"Delete {path!r} from the DAV file share. WebDAV has no undo and "
                           "a directory goes with everything under it.",
                "rule_key": f"files_share:delete:{path}"}
    size = len(str(args.get("content") or "").encode("utf-8"))
    return {"action": "approve",
            "message": f"Write {size} bytes to {path!r} on the DAV file share, replacing "
                       "whatever is there.",
            "rule_key": f"files_share:write:{path}"}


def _format(result: dict[str, Any]) -> str:
    """The ``/files`` reply: one line per entry, directories first."""
    if not result.get("ok"):
        return f"files: {result.get('error', 'failed')}"
    rows = result.get("entries") or []
    if not rows:
        return f"{result.get('path') or '/'} is empty."
    lines = [f"{result.get('path') or '/'}:"]
    for row in rows:
        size = "dir" if row["is_dir"] else (f"{row['size']} B" if row["size"] is not None else "?")
        lines.append(f"  {row['name']}  ({size})")
    return "\n".join(lines)


def handle_command(raw: str) -> str:
    """``/files [path]`` — a listing of the share root, or of one directory.

    The apps prefix a command with the session id (``@<sid> rest``); peeling it
    off here stops ``@abc123`` being looked up as a directory name.
    """
    _session, rest = split_session(raw)
    if not _dav.available():
        return _dav.not_configured()["error"]
    # Through safe_result, not the bare call: `guarded_command` would otherwise
    # put an httpx message — which carries the request URL, and its userinfo —
    # into the reply.
    return _format(_dav.safe_result(lambda params: listing(rest.strip())))


_safe = _dav.safe_json  # one wrapper for every plugin: it is also the redaction funnel

handle_list = _safe(lambda p: listing(str(p.get("path") or "")))
handle_read = _safe(lambda p: read(str(p.get("path") or "")))
handle_write = _safe(lambda p: write(str(p.get("path") or ""), str(p.get("content") or "")))
handle_delete = _safe(lambda p: delete(str(p.get("path") or "")))


# --- search by meaning (T-1249) --------------------------------------------------
#: Changed files read and sent to the house per search. The first searches after a big
#: change answer from a partial index and say so; a search must not take a minute.
PUSH_FILES: int = 8
#: The text sent per search, all files together: eight full-size files in one request is
#: some 1700 passages to embed while the model waits on the answer.
PUSH_BYTES: int = 2 * MAX_BYTES
#: Directories walked per search: a bound on a share with a loop or a huge tree.
MAX_DIRS: int = 300
#: And the seconds the walk may take: a slow server must not hold a turn for minutes.
CRAWL_SECONDS: float = 20.0


def _crawl() -> tuple[dict[str, str], dict[str, int]]:
    """Every text file under the share root that fileshare_read would return, as
    path -> a change mark (the etag, or last-modified plus size where the server gives
    none), and how many entries were left out and why."""
    files: dict[str, str] = {}
    skipped = {"not_text": 0, "too_big": 0, "folders_not_walked": 0, "unreadable": 0}
    todo, walked, seen = [""], 0, {""}
    deadline = time.monotonic() + CRAWL_SECONDS
    while todo:
        if walked >= MAX_DIRS or time.monotonic() > deadline:
            skipped["folders_not_walked"] += len(todo)
            break
        here = todo.pop()
        walked += 1
        try:
            target = resolve(here)
            entries = _dav.propfind(target, depth=1, body=_FILE_PROPS)
        except (httpx.HTTPError, ValueError):
            # One folder the server refuses, or an href with `..` in it, costs that folder,
            # not the search.
            skipped["folders_not_walked"] += 1
            continue
        own = _relative("/" + target).strip("/")
        for entry in entries:
            row = _row(entry)
            if row["path"].strip("/") == own:
                continue
            if row["is_dir"]:
                # A folder is walked once: a server that lists a parent must not loop the walk.
                if row["path"].strip("/") not in seen:
                    seen.add(row["path"].strip("/"))
                    todo.append(row["path"])
            elif row["size"] is not None and row["size"] > MAX_BYTES:
                skipped["too_big"] += 1
            elif not _is_text(row["content_type"]):
                skipped["not_text"] += 1
            else:
                etag = str((entry.get("props") or {}).get("getetag") or "").strip()
                files[row["path"]] = etag or f"{row['modified']}:{row['size']}"
    return files, skipped


def search(query: str) -> dict[str, Any]:
    """The passages of the shared files nearest in meaning to *query*, with their file.

    The house keeps the index (jarvis-core ``integrations/memory/files.py``): this lists
    the share, sends the house the listing and the text of up to PUSH_FILES files that
    changed since it last saw them, then asks it. The text goes share -> this plugin ->
    the house and nowhere else; the house embeds it with its own local model.

    Does not cover: files that are not text or are over the read limit (counted in
    ``skipped``); a share changed by more than PUSH_FILES files at once (``indexed`` of
    ``files`` says how far along it is; each search moves it on)."""
    if not _dav.available():
        return _dav.not_configured()
    if not (query or "").strip():
        return {"ok": False, "error": "query is required: what to look for, in plain words"}
    if not _house.configured():
        return {"ok": False, "error": "the house is not configured, so there is no index to search"}
    listing, skipped = _crawl()
    with _house.client(timeout=60.0) as house:
        def sync(files: dict[str, str]) -> dict[str, Any]:
            sent = house.post("/api/files_index/sync", json={"listing": listing, "files": files})
            if sent.status_code >= 400:
                raise _IndexUnavailable(str(_house.body_of(sent))[:200])
            return sent.json()

        try:
            state = sync({})
            texts: dict[str, str] = {}
            sent_bytes = 0
            for path in state.get("stale", [])[:PUSH_FILES]:
                if sent_bytes >= PUSH_BYTES:
                    break
                try:
                    got = read(path)
                except (httpx.HTTPError, ValueError):
                    # The server failed this once: not sent, so it stays stale and the next
                    # search tries it again. One bad file must not lose the others' text.
                    skipped["unreadable"] += 1
                    continue
                if not got.get("ok"):
                    skipped["unreadable"] += 1
                # A file that cannot be text (binary behind a text type, not UTF-8) is sent
                # empty: held at its change mark with no passages, instead of taking one of
                # every later search's reads.
                texts[path] = got["content"] if got.get("ok") else ""
                sent_bytes += len(texts[path].encode("utf-8"))
            if texts:
                state = sync(texts)
        except _IndexUnavailable as err:
            return {"ok": False, "error": f"the house's files index is not available: {err}"}
        if not state.get("available"):
            return {"ok": False, "error": "the house has no embedding model running, so the shared files "
                                          "cannot be searched by meaning; fileshare_list and fileshare_read still work"}
        found = house.post("/api/files_index/search", json={"query": query})
        found.raise_for_status()
        results = found.json().get("results") or []
    out: dict[str, Any] = {"ok": True, "results": results, "files": state.get("files", 0),
                           "indexed": state.get("indexed", 0), "skipped": skipped}
    if state.get("full"):
        out["note"] = (f"the index is full: {out['indexed']} of {out['files']} text files are searchable; "
                       "the rest are found by name with fileshare_list")
    elif out["indexed"] < out["files"]:
        out["note"] = (f"only {out['indexed']} of {out['files']} text files are indexed so far; "
                       "search again to index more")
    elif not results:
        out["note"] = "nothing in the shared files is close to that"
    return out


class _IndexUnavailable(Exception):
    pass


handle_search = _safe(lambda p: search(str(p.get("query") or "")))
