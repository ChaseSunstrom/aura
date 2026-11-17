"""jarvis-files against a mocked WebDAV share.

Pinned here: the listing shape and that a directory does not list itself, the
size and binary refusals (with the limit named), the traversal refusal, and that
only the two writing tools reach the approval gate. Not pinned: a live server —
every call goes through httpx.MockTransport.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin, serve_dav, set_dav_env  # noqa: E402

plugin = load_plugin("jarvis-files")
tools = plugin.tools

#: The real class, captured before any test replaces it.
LISTING = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:nc="http://nextcloud.org/ns">
  <d:response>
    <d:href>/remote.php/dav/files/alice/Documents/</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/></d:resourcetype>
        <d:displayname>Documents</d:displayname>
        <d:getlastmodified>Tue, 01 Sep 2026 10:15:00 GMT</d:getlastmodified>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/alice/Documents/shopping%20list.md</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype/>
        <d:displayname>shopping list.md</d:displayname>
        <d:getcontentlength>42</d:getcontentlength>
        <d:getcontenttype>text/markdown</d:getcontenttype>
        <d:getlastmodified>Wed, 02 Sep 2026 08:00:00 GMT</d:getlastmodified>
        <d:getetag>"abc123"</d:getetag>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/alice/Documents/holiday.jpg</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype/>
        <d:displayname>holiday.jpg</d:displayname>
        <d:getcontentlength>4194304</d:getcontentlength>
        <d:getcontenttype>image/jpeg</d:getcontenttype>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
"""

#: A depth-0 PROPFIND of one big text file — the size guard's input.
BIG_FILE = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/remote.php/dav/files/alice/Documents/huge.csv</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype/>
        <d:getcontentlength>1048576</d:getcontentlength>
        <d:getcontenttype>text/csv</d:getcontenttype>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
"""

SMALL_FILE = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/remote.php/dav/files/alice/Documents/shopping%20list.md</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype/>
        <d:getcontentlength>18</d:getcontentlength>
        <d:getcontenttype>text/markdown</d:getcontenttype>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
"""

BINARY_FILE = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/remote.php/dav/files/alice/Documents/holiday.jpg</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype/>
        <d:getcontentlength>2048</d:getcontentlength>
        <d:getcontenttype>image/jpeg</d:getcontenttype>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
"""




@pytest.fixture
def dav(monkeypatch):
    set_dav_env(monkeypatch)
    monkeypatch.setenv("WEBDAV_PATH", "files")
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append({"method": request.method, "path": path,
                     "body": request.content.decode("utf-8") if request.content else "",
                     "type": request.headers.get("content-type", "")})
        if request.method == "PROPFIND":
            if path.endswith("huge.csv"):
                return httpx.Response(207, text=BIG_FILE)
            if path.endswith("holiday.jpg"):
                return httpx.Response(207, text=BINARY_FILE)
            if path.endswith(".md"):
                return httpx.Response(207, text=SMALL_FILE)
            return httpx.Response(207, text=LISTING)
        if request.method == "GET":
            return httpx.Response(200, text="- milk\n- bread\n")
        if request.method in ("PUT", "DELETE"):
            return httpx.Response(204, text="")
        return httpx.Response(405, text="not allowed")

    serve_dav(monkeypatch, handler)
    return seen


def test_a_listing_is_relative_to_the_share_root(dav):
    out = json.loads(tools.handle_list({"path": "Documents"}))
    assert out["ok"] and out["path"] == "Documents"
    # The directory itself is the first row of a depth-1 PROPFIND; listing it as
    # its own child makes a caller that recurses loop forever.
    assert [row["path"] for row in out["entries"]] == ["Documents/holiday.jpg",
                                                       "Documents/shopping list.md"]
    row = out["entries"][1]
    assert row["name"] == "shopping list.md" and row["size"] == 42
    assert row["is_dir"] is False and row["content_type"] == "text/markdown"
    assert dav[0]["method"] == "PROPFIND"


def test_read_returns_text(dav):
    out = json.loads(tools.handle_read({"path": "Documents/shopping list.md"}))
    assert out["ok"] and out["content"] == "- milk\n- bread\n" and out["bytes"] == 15
    assert out["path"] == "Documents/shopping list.md"


def test_read_refuses_an_oversized_file_and_names_the_limit(dav):
    out = json.loads(tools.handle_read({"path": "Documents/huge.csv"}))
    assert out["ok"] is False
    assert "1048576 bytes" in out["error"] and "262144" in out["error"] and "256 KB" in out["error"]
    # The refusal must come from the PROPFIND: a gigabyte is never fetched to be
    # rejected afterwards.
    assert [call["method"] for call in dav] == ["PROPFIND"]


def test_read_refuses_a_binary_file(dav):
    out = json.loads(tools.handle_read({"path": "Documents/holiday.jpg"}))
    assert out["ok"] is False and "image/jpeg" in out["error"]
    assert [call["method"] for call in dav] == ["PROPFIND"]


def test_read_refuses_bytes_that_are_not_utf8(monkeypatch, dav):
    serve_dav(monkeypatch, lambda r: httpx.Response(207, text=SMALL_FILE) if r.method == "PROPFIND"
           else httpx.Response(200, content=b"\xff\xfe\x00binary"))
    out = json.loads(tools.handle_read({"path": "Documents/shopping list.md"}))
    assert out["ok"] is False and "UTF-8" in out["error"]


def test_a_path_that_escapes_the_share_is_refused(dav):
    for handler in (tools.handle_read, tools.handle_list, tools.handle_delete):
        out = json.loads(handler({"path": "../../etc/passwd"}))
        assert out["ok"] is False and ".." in out["error"]
    assert dav == []


def test_write_puts_utf8_text(dav):
    out = json.loads(tools.handle_write({"path": "Documents/notes.md", "content": "hello"}))
    assert out["ok"] and out["bytes"] == 5 and out["http"] == 204
    put = [call for call in dav if call["method"] == "PUT"][0]
    assert put["path"] == "/remote.php/dav/files/alice/Documents/notes.md"
    assert put["body"] == "hello" and "utf-8" in put["type"]


def test_write_refuses_an_oversized_body(dav):
    out = json.loads(tools.handle_write({"path": "a.txt", "content": "x" * (300 * 1024)}))
    assert out["ok"] is False and "262144" in out["error"]
    assert dav == []


def test_delete_refuses_the_share_root(dav):
    out = json.loads(tools.handle_delete({"path": " / "}))
    assert out["ok"] is False and "share root" in out["error"]
    assert dav == []


def test_delete_removes_one_path(dav):
    out = json.loads(tools.handle_delete({"path": "Documents/notes.md"}))
    assert out["ok"] and out["http"] == 204
    assert dav[0]["method"] == "DELETE"


def test_a_failing_status_becomes_ok_false_without_the_password(monkeypatch, dav):
    serve_dav(monkeypatch, lambda r: httpx.Response(507, text="quota for s3cr3t-app-password"))
    out = tools.handle_list({"path": "Documents"})
    assert json.loads(out)["ok"] is False and "507" in out
    assert "s3cr3t-app-password" not in out


def test_without_dav_url_the_tools_say_so(monkeypatch):
    monkeypatch.delenv("DAV_URL", raising=False)
    out = json.loads(tools.handle_list({}))
    assert out["ok"] is False and "DAV_URL" in out["error"]


def test_the_slash_command_lists_the_root_and_ignores_the_session_prefix(dav):
    reply = tools.handle_command("@sess-1 Documents")
    assert "shopping list.md" in reply and "(42 B)" in reply
    assert dav[0]["path"] == "/remote.php/dav/files/alice/Documents"


def test_a_failing_command_does_not_echo_the_password_in_the_url(monkeypatch, dav):
    # DAV_URL written with userinfo is the leak this guards: httpx puts the
    # request URL in every error message it raises.
    monkeypatch.setenv("DAV_URL", "https://alice:s3cr3t-app-password@cloud.test/remote.php/dav")
    sent = []
    serve_dav(monkeypatch, lambda r: sent.append(r) or httpx.Response(500, text="server error"))
    reply = tools.handle_command("@sess-1 Documents")
    # The address with a login is refused before a request goes out (T-1374), so the password is
    # neither in the answer nor on the wire.
    assert "s3cr3t-app-password" not in reply and "DAV_URL" in reply and sent == []


def test_register_gates_only_the_writing_tools():
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert set(ctx.tools) == {"fileshare_list", "fileshare_read", "files_search", "fileshare_write",
                              "fileshare_delete"}
    # Not `file`: Hermes owns that toolset and a second one would shadow it.
    assert {entry["toolset"] for entry in ctx.tools.values()} == {"files_share"}
    assert set(ctx.commands) == {"files"}
    gate = ctx.hooks["pre_tool_call"]
    write = gate("fileshare_write", {"path": "Documents/notes.md", "content": "hello"}, "task-1")
    assert write["action"] == "approve" and "Documents/notes.md" in write["message"]
    assert "5 bytes" in write["message"]
    assert write["rule_key"].rsplit(":g", 1)[0] == "files_share:write:Documents/notes.md"
    remove = gate("fileshare_delete", {"path": "Documents/old.md"}, "task-1")
    assert remove["action"] == "approve" and "no undo" in remove["message"]
    assert remove["rule_key"].rsplit(":g", 1)[0] == "files_share:delete:Documents/old.md"
    assert gate("fileshare_list", {"path": "Documents"}, "task-1") is None
    assert gate("fileshare_read", {"path": "Documents/notes.md"}, "task-1") is None


# --- files_search (T-1249) --------------------------------------------------------
class FakeHouse:
    """The two house routes, with the index reduced to "which path at which etag"."""

    def __init__(self) -> None:
        self.held: dict[str, str] = {}
        self.texts: dict[str, str] = {}
        self.syncs: list[dict] = []
        self.status = 200
        self.available = True

    def answer(self, request: httpx.Request) -> httpx.Response:
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": "starting"})
        body = json.loads(request.content)
        if request.url.path == "/api/files_index/search":
            return httpx.Response(200, json={"results": [
                {"path": p, "passage": t, "score": 0.9} for p, t in self.texts.items() if t]})
        self.syncs.append(body)
        listing = body["listing"]
        if self.available:
            for path, text in body.get("files", {}).items():
                self.held[path], self.texts[path] = listing[path], text
        stale = sorted(p for p, etag in listing.items() if self.held.get(p) != etag)
        return httpx.Response(200, json={"available": self.available, "files": len(listing),
                                         "indexed": len(listing) - len(stale), "stale": stale})


@pytest.fixture
def share(monkeypatch, dav):
    """The `dav` share plus a house: every request's host is recorded."""
    monkeypatch.setenv("HASS_URL", "http://house.test:8080")
    monkeypatch.setenv("HASS_TOKEN", "house-token")
    house = FakeHouse()
    house.hosts = set()
    house.gets = []
    house.get_body = b"- milk\n- bread\n"
    house.get_status = 200

    def handler(request: httpx.Request) -> httpx.Response:
        house.hosts.add(request.url.host)
        if request.url.host == "house.test":
            return house.answer(request)
        path = request.url.path
        if request.method == "GET":
            house.gets.append(request.url.raw_path.decode())
            return httpx.Response(house.get_status, content=house.get_body)
        if path.endswith(".md"):
            return httpx.Response(207, text=SMALL_FILE)
        return httpx.Response(207, text=LISTING)

    serve_dav(monkeypatch, handler)
    return house


def test_search_pushes_a_changed_file_once_then_answers_path_and_passage(share):
    first = tools.search("what do we need from the shop")
    assert first["ok"] and first["results"][0]["path"] == "Documents/shopping list.md"
    assert "milk" in first["results"][0]["passage"]
    assert (first["files"], first["indexed"]) == (1, 1) and "note" not in first
    # The 4 MB photo is counted, never fetched.
    assert first["skipped"]["too_big"] >= 1 and len(share.gets) == 1
    assert [sorted(s.get("files", {})) for s in share.syncs] == [[], ["Documents/shopping list.md"]]
    assert share.syncs[0]["listing"] == {"Documents/shopping list.md": '"abc123"'}
    tools.search("milk")
    assert len(share.gets) == 1 and len(share.syncs) == 3  # unchanged etag: listed, not read again


def test_search_caps_the_files_it_reads_and_says_how_far_along_it_is(monkeypatch, share):
    monkeypatch.setattr(tools, "PUSH_FILES", 0)
    out = tools.search("milk")
    assert out["ok"] and share.gets == []
    assert out["indexed"] == 0 and "only 0 of 1" in out["note"]


def test_search_sends_an_unreadable_file_empty_so_it_is_not_read_for_ever(share):
    share.get_body = b"\xff\xfe\x00 not utf-8"
    out = tools.search("milk")
    assert out["ok"] and out["results"] == [] and out["indexed"] == 1
    assert share.syncs[-1]["files"] == {"Documents/shopping list.md": ""}


def test_search_says_when_the_house_is_down_or_has_no_embedding_model(monkeypatch, share):
    share.status = 503
    down = json.loads(tools.handle_search({"query": "milk"}))
    assert down["ok"] is False and "not available" in down["error"]
    share.status, share.available = 200, False
    off = tools.search("milk")
    assert off["ok"] is False and "embedding model" in off["error"] and "fileshare_read" in off["error"]
    monkeypatch.delenv("HASS_TOKEN")
    assert "house is not configured" in tools.search("milk")["error"]
    assert "query is required" in tools.search("  ")["error"]


def test_search_leaves_a_file_the_server_failed_on_stale_and_still_answers(share):
    share.get_status = 500
    out = tools.search("milk")
    assert out["ok"] and out["skipped"]["unreadable"] == 1 and out["indexed"] == 0
    assert all(not s.get("files") for s in share.syncs)  # not sent empty: the next search tries again
    share.get_status = 200
    assert tools.search("milk")["indexed"] == 1


def test_a_name_with_a_hash_or_a_question_mark_is_requested_whole(share):
    tools.read("Documents/C# notes?.md")
    assert share.gets[-1].endswith("/Documents/C%23%20notes%3F.md")


def test_search_and_only_the_share_and_the_house_are_called(share):
    assert tools.search("milk")["ok"]
    assert share.hosts == {"cloud.test", "house.test"}
