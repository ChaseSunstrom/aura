"""``python -m bench --replay-sessions N``: owner prompts from a made-up store, a fake model
server on loopback, nothing executed and nothing sent anywhere else (T-0588)."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import stat
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from bench import replay
from bench.__main__ import main as bench_main

REPO = Path(__file__).resolve().parents[2]

TOOLS = [
    {"type": "function", "function": {"name": "light_on", "parameters": {"type": "object"}}},
    {"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}},
]


def _store(path: Path) -> Path:
    if path.exists():
        return path
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE sessions(id TEXT PRIMARY KEY, source TEXT, user_id TEXT, started_at REAL);
        CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
                              tool_calls TEXT, tool_name TEXT, timestamp REAL, active INTEGER);
        """
    )
    for sid, source in [("s1", "api_server"), ("s2", "tui"), ("s3", "cron"), ("s4", "subagent")]:
        db.execute("INSERT INTO sessions VALUES (?,?,NULL,0)", (sid, source))
    rows = [
        (1, "s1", "user", "please turn the lamp on in the study", 100.0),
        (2, "s1", "assistant", "Done, the study lamp is on.", 101.0),
        (3, "s2", "user", "what is the weather in Oslo", 200.0),
        (4, "s2", "assistant", "", 201.0),
        (5, "s2", "tool", "sunny", 202.0),
        (6, "s2", "assistant", "Sunny and cold in Oslo.", 203.0),
        (7, "s3", "user", "CRON run the nightly sweep", 300.0),
        (8, "s4", "user", "SUBAGENT summarise this file", 400.0),
        (9, "s1", "user", "ping", 50.0),
    ]
    db.executemany(
        "INSERT INTO messages(id, session_id, role, content, timestamp, active) "
        "VALUES (?,?,?,?,?,1)", rows)
    db.commit()
    db.close()
    return path


class _Fake(BaseHTTPRequestHandler):
    log: list[tuple[str, str, dict]] = []
    models = ["model-a", "model-b", "judge-m"]
    redirect = False

    def log_message(self, *a):  # noqa: D401 - silence
        pass

    def _send(self, code, doc, headers=None):
        body = json.dumps(doc).encode()
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.log.append(("GET", self.path, {}))
        if self.redirect:
            return self._send(302, {}, {"Location": "http://203.0.113.9/v1/models"})
        self._send(200, {"data": [{"id": m} for m in self.models]})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.log.append(("POST", self.path, req))
        user = req["messages"][-1]["content"]
        if req["messages"][0]["role"] == "system":  # the judge
            return self._send(200, {"choices": [{"message": {"content": "SCORE: 7"}}]})
        msg: dict = {"content": "ok"}
        if "lamp" in user and req["model"] == "model-a":
            msg = {"content": None, "tool_calls": [
                {"function": {"name": "light_on", "arguments": '{"entity": "study"}'}}]}
        elif "weather" in user:
            msg = {"content": None, "tool_calls": [
                {"function": {"name": "get_weather", "arguments": '{"city": "Oslo"}'}}]}
        self._send(200, {"choices": [{"message": msg}]})


@pytest.fixture()
def server():
    _Fake.log = []
    _Fake.redirect = False
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _run(tmp_path, base, *extra, n="3", models="model-a,model-b"):
    store = _store(tmp_path / "state.db")
    out = tmp_path / "out"
    code = bench_main(["--replay-sessions", n, "--models", models, "--store", str(store),
                       "--base", base, "--out", str(out), *extra])
    return code, out


def test_replay_scores_agreement_latency_and_judge(tmp_path, server, capsys):
    tools = tmp_path / "tools.json"
    tools.write_text(json.dumps(TOOLS))
    code, out = _run(tmp_path, server, "--tools", str(tools))
    assert code == 0
    doc = json.loads(next(out.glob("replay-*.json")).read_text())
    rows = {r["prompt"]: r for r in doc["rows"]}
    assert len(rows) == 3
    lamp = rows["please turn the lamp on in the study"]
    assert lamp["agree"] is False  # model-a calls light_on, model-b answers in words
    assert lamp["models"]["model-a"]["tools"] == [["light_on", ["entity"]]]
    assert lamp["reference"] == "Done, the study lamp is on."
    weather = rows["what is the weather in Oslo"]
    assert weather["agree"] is True
    assert weather["reference"] == "Sunny and cold in Oslo."
    for r in rows.values():
        for m in r["models"].values():
            # "ping" had no answer in the store, so there is nothing to judge it against
            assert m["latency_s"] >= 0 and m["score"] == (7 if r["reference"] else None)
    table = capsys.readouterr().out
    assert "turn the lamp" in table and "agreement 2/3" in table
    # tools were offered; nothing but chat and listing reached the server
    chats = [r for m, p, r in _Fake.log if m == "POST"]
    assert chats and all(p == "/v1/chat/completions" for m, p, r in _Fake.log if m == "POST")
    assert any(r.get("tools") == TOOLS for r in chats)


def test_default_judge_is_the_second_model_and_the_output_says_so(tmp_path, server, capsys,
                                                                   monkeypatch):
    monkeypatch.delenv("BENCH_JUDGE_MODEL", raising=False)
    code, out = _run(tmp_path, server)
    assert code == 0
    judged = {r["model"] for m, p, r in _Fake.log
              if m == "POST" and r["messages"][0]["role"] == "system"}
    assert judged == {"model-b"}  # still loaded after the batches: no third cold start
    note = "judged by model-b, one of the two under test"
    assert note in capsys.readouterr().out
    assert json.loads(next(out.glob("replay-*.json")).read_text())["judge_note"] == note


def test_separate_judge_is_not_called_one_of_the_two(tmp_path, server, capsys):
    code, out = _run(tmp_path, server, "--judge", "judge-m")
    assert code == 0
    table = capsys.readouterr().out
    assert "judged by judge-m" in table and "under test" not in table


def test_without_tools_file_says_so(tmp_path, server, capsys):
    code, out = _run(tmp_path, server)
    assert code == 0
    assert "no tools offered" in capsys.readouterr().out
    assert all("tools" not in r for m, p, r in _Fake.log if m == "POST")


def test_only_owner_rows_are_read(tmp_path):
    rows = replay.read_prompts(_store(tmp_path / "state.db"), 10)
    texts = [r.prompt for r in rows]
    assert texts == ["what is the weather in Oslo", "please turn the lamp on in the study", "ping"]
    assert not any("CRON" in t or "SUBAGENT" in t for t in texts)
    assert replay.read_prompts(tmp_path / "state.db", 1)[0].prompt == "what is the weather in Oslo"


def test_store_is_opened_read_only(tmp_path):
    store = _store(tmp_path / "state.db")
    before = store.read_bytes()
    replay.read_prompts(store, 5)
    assert store.read_bytes() == before
    assert not list(tmp_path.glob("state.db-*"))


def test_results_file_mode_0600(tmp_path, server):
    code, out = _run(tmp_path, server)
    path = next(out.glob("replay-*.json"))
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_results_dir_is_git_ignored():
    r = subprocess.run(["git", "check-ignore", "-q", "bench/results/replay/replay-x.json"],
                       cwd=REPO)
    assert r.returncode == 0


def test_public_address_is_refused(tmp_path, server, monkeypatch, capsys):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80))])
    code, _ = _run(tmp_path, "http://models.example.test:8080")
    assert code == 2 and "private" in capsys.readouterr().err
    assert _Fake.log == []


@pytest.mark.parametrize("ip,ok", [
    ("127.0.0.1", True), ("192.168.1.5", True), ("10.0.0.2", True), ("::1", True),
    ("8.8.8.8", False), ("::ffff:8.8.8.8", False), ("100.64.0.1", False), ("0.0.0.0", False),
])
def test_public_address_classification(ip, ok):
    assert replay.is_private_address(ip) is ok


def test_redirect_is_refused(tmp_path, server, capsys):
    _Fake.redirect = True
    code, _ = _run(tmp_path, server)
    assert code == 2 and "redirect" in capsys.readouterr().err.lower()


def test_unlisted_model_exits_2_before_any_prompt(tmp_path, server, capsys):
    code, out = _run(tmp_path, server, models="model-a,ripple-65b")
    assert code == 2 and "ripple-65b" in capsys.readouterr().err
    assert [m for m, p, r in _Fake.log if m == "POST"] == []
    assert not out.exists()


def test_unlisted_judge_exits_2_before_any_prompt(tmp_path, server):
    code, _ = _run(tmp_path, server, "--judge", "nope")
    assert code == 2
    assert [m for m, p, r in _Fake.log if m == "POST"] == []


def test_two_models_required_and_n_bounded(tmp_path, server):
    assert _run(tmp_path, server, models="model-a")[0] == 2
    assert _run(tmp_path, server, n="0")[0] == 2
    assert _Fake.log == []


def test_judge_score_parse():
    assert replay.parse_score("SCORE: 9") == 9
    assert replay.parse_score("thinking... SCORE: 10\n") == 10
    assert replay.parse_score("SCORE: 11") is None
    assert replay.parse_score("no number") is None
