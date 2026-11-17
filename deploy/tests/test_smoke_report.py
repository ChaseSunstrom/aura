"""scripts/smoke-report.py: a smoke log becomes one run for the last-verified board (T-0580)."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("smoke_report", ROOT / "scripts" / "smoke-report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)
CONTRACT = json.loads((ROOT / "tests/contracts/verified.json").read_text())


def test_the_prefix_table_is_the_contract():
    assert list(report.PREFIXES) == [(c["prefix"], c["id"]) for c in CONTRACT["checks"]]
    assert report.SECTIONS == {c["id"]: c["section"] for c in CONTRACT["checks"]}
    # Every section named is a header the smoke prints.
    smoke = (ROOT / "scripts" / "smoke.sh").read_text()
    assert all(f'echo "== {s} ' in smoke for s in report.SECTIONS.values())


def test_every_pass_line_the_smoke_can_print_is_a_check():
    """A new PASS in smoke.sh with no row would be dropped from the board without a word."""
    smoke = (ROOT / "scripts" / "smoke.sh").read_text()
    said = re.findall(r'^\s*pass "([^"$]+)', smoke, re.M) + re.findall(r'print\(f?"PASS: ([^"{]+)', smoke)
    assert len(said) == len(CONTRACT["checks"]), said
    checks = [next((c for p, c in report.PREFIXES if s.startswith(p)), None) for s in said]
    assert None not in checks and len(set(checks)) == len(checks), list(zip(said, checks))


def test_a_clean_run_passes_everything_it_printed():
    log = "== 1. health\nPASS: health\nPASS: SSE chunks=12, [DONE] seen\nPASS: voice: male\nALL SMOKE CHECKS PASSED\n"
    assert report.read(log) == (["health", "chat", "voice"], None, None)


def test_a_failed_run_keeps_what_passed_before_and_the_first_failure():
    log = "== 1. health\nPASS: health\n== 4. goal loop\nPASS: goal loop achieved\nFAIL: steps 4-5d (goal loop)\n== 6. voice\nFAIL: later\n"
    assert report.read(log) == (["health", "goal"], "steps 4-5d (goal loop)", "gate")


def test_a_run_that_died_without_a_word_is_a_failure():
    assert report.read("== 1. health\nPASS: health\n== 2. chat\n") == (["health"], "the smoke ended before its last check", "chat")
    assert report.read("PASS: health\n") == (["health"], "the smoke ended before its last check", None)


def test_the_voice_line_is_not_taken_for_the_speech_one():
    assert report.read("PASS: TTS 900 ms (cosyvoice), STT 300 ms, transcript: voice check\nALL SMOKE CHECKS PASSED")[0] == ["speech"]


def test_the_smoke_sends_its_own_run_and_keeps_its_exit_code(tmp_path):
    """smoke.sh against a house that fails its first check: exit 1, and the run reaches the board."""
    import shutil
    import subprocess
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    import pytest

    if not all(shutil.which(t) for t in ("bash", "curl", "jq")):
        pytest.skip("needs bash, curl and jq")
    got: list[dict] = []
    key = __import__("secrets").token_hex(8)  # made at run time: no credential is written out here

    class House(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(503)
            self.end_headers()

        def do_PUT(self):  # noqa: N802
            got.append({"path": self.path, "auth": self.headers.get("Authorization"),
                        "body": json.loads(self.rfile.read(int(self.headers["content-length"])))})
            self.send_response(200)
            self.send_header("content-length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), House)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        env = {"PATH": __import__("os").environ["PATH"], "AURA_BASE": f"http://127.0.0.1:{srv.server_port}",
               "AURA_API_KEY": key, "SMOKE_TAG": "tag-under-test"}
        done = subprocess.run(["bash", str(ROOT / "scripts" / "smoke.sh")], env=env, capture_output=True, text=True, timeout=60)
    finally:
        srv.shutdown()
    assert done.returncode == 1, done.stderr[-400:]
    assert len(got) == 1 and got[0]["path"] == "/api/verified" and got[0]["auth"] == f"Bearer {key}"
    assert got[0]["body"]["tag"] == "tag-under-test" and got[0]["body"]["passed"] == []
    assert got[0]["body"]["failed"] == "adapter /health" and got[0]["body"]["failed_check"] == "health"
