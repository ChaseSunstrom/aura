"""The deploy's smoke hears which voice speaks: male, from CosyVoice on the GPU (T-0429).

The owner heard "a girl speaking instead of jarvis", and the smoke's round trip passes for
any voice that says the words. voice_pitch.py measures the line's pitch and asks the voice
which device it runs on.
"""
from __future__ import annotations

import array
import importlib.util
import json
import math
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("voice_pitch", ROOT / "scripts/voice_pitch.py")
assert _spec and _spec.loader
vp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vp)

RATE = 24000


def voiced(f0: float, seconds: float = 1.0, rate: int = RATE) -> bytes:
    """A buzz with eight harmonics, the shape a vowel has, as 16-bit little-endian PCM."""
    s = array.array("h", (
        int(4000 * sum(math.sin(2 * math.pi * f0 * k * i / rate) / k for k in range(1, 9)))
        for i in range(int(seconds * rate))))
    if sys.byteorder == "big":
        s.byteswap()
    return s.tobytes()


@pytest.mark.parametrize("f0", [95.0, 110.0, 130.0, 210.0, 240.0])
def test_the_pitch_of_a_vowel_is_measured_within_five_percent(f0):
    assert vp.median_f0(voiced(f0), RATE) == pytest.approx(f0, rel=0.05)


def test_a_high_voice_is_never_read_an_octave_down():
    # Half the period's lag correlates as well as the period itself; reading 220 Hz as
    # 110 Hz would pass the very voice this check exists to catch.
    assert vp.median_f0(voiced(220.0), RATE) > 200
    assert vp.median_f0(voiced(220.0, rate=16000), 16000) > 200


def test_silence_has_no_pitch():
    assert vp.median_f0(bytes(RATE * 2), RATE) == 0.0
    assert vp.median_f0(b"", RATE) == 0.0


class _Stub(BaseHTTPRequestHandler):
    pcm = b""
    engine = "cosyvoice"
    voice = "bm_george"
    device = "cuda"
    speak_status = 200
    speak_detail = ""
    seen_auth: list[str] = []

    def log_message(self, *_):
        pass

    def _send(self, status, body, ctype, headers=()):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, json.dumps({"status": "ok", "device": _Stub.device}).encode(), "application/json")

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        _Stub.seen_auth.append(self.headers.get("Authorization", ""))
        if _Stub.speak_status != 200:
            self._send(_Stub.speak_status, json.dumps({"detail": _Stub.speak_detail}).encode(), "application/json")
            return
        self._send(200, _Stub.pcm, "audio/pcm",
                   [("x-sample-rate", str(RATE)), ("x-jarvis-tts-engine", _Stub.engine),
                    ("x-jarvis-voice", _Stub.voice)])


@pytest.fixture
def stack(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setenv("AURA_BASE", base)
    monkeypatch.setenv("AURA_API_KEY", "k")
    monkeypatch.setenv("SMOKE_VOICE_BASE", base)
    monkeypatch.delenv("SMOKE_VOICE_MAX_HZ", raising=False)
    _Stub.pcm, _Stub.engine, _Stub.device, _Stub.voice = voiced(115.0, 0.6), "cosyvoice", "cuda", "bm_george"
    _Stub.speak_status, _Stub.speak_detail, _Stub.seen_auth = 200, "", []
    yield _Stub
    srv.shutdown()


def test_jarvis_on_the_gpu_passes(stack, capsys):
    assert vp.main() == 0
    out = capsys.readouterr().out
    assert "cuda" in out and "Hz" in out
    assert stack.seen_auth == ["Bearer k"]


def test_a_female_voice_for_jarvis_fails_the_deploy(stack, capsys):
    stack.pcm = voiced(220.0, 0.6)
    assert vp.main() == 1
    assert "165 Hz" in capsys.readouterr().out


def test_auras_voice_is_female_and_a_male_one_for_her_fails(stack, capsys):
    """T-0861: AURA's af_heart sits over the line; the smoke of a55e109b failed her as 'not male'."""
    stack.voice, stack.pcm = "af_heart", voiced(220.0, 0.6)
    assert vp.main() == 0
    stack.pcm = voiced(115.0, 0.6)
    assert vp.main() == 1
    assert "should be female" in capsys.readouterr().out


def test_the_ceiling_is_a_knob(stack, monkeypatch):
    stack.pcm = voiced(220.0, 0.6)
    monkeypatch.setenv("SMOKE_VOICE_MAX_HZ", "260")
    assert vp.main() == 0


def test_the_houses_fallback_voice_fails_the_deploy(stack, capsys):
    stack.engine = "house"   # what the adapter answers while CosyVoice is on the CPU
    assert vp.main() == 1
    assert "house" in capsys.readouterr().out


def test_cosyvoice_on_the_cpu_fails_the_deploy(stack, capsys):
    stack.device = "cpu"
    assert vp.main() == 1
    assert "cpu" in capsys.readouterr().out


def test_a_voice_with_no_speech_fails(stack):
    stack.pcm = bytes(RATE)
    assert vp.main() == 1


def test_an_engine_that_does_not_stream_is_skipped(stack, capsys):
    # CI's stack speaks through Piper (AURA_TTS_ENGINE=wyoming): the adapter's own 404.
    stack.speak_status, stack.speak_detail = 404, "this voice does not stream; use /api/audio/speak"
    assert vp.main() == 0
    assert "SKIP" in capsys.readouterr().out


def test_a_missing_voice_is_not_mistaken_for_a_skip(stack):
    stack.speak_status, stack.speak_detail = 404, "no voice named jarvis"
    assert vp.main() == 1


def test_the_smoke_runs_the_check_and_both_deploys_ship_it_beside_it():
    # The smoke runs from the host's copy (scripts/ flat beside smoke.sh): a deploy that
    # forgot to copy the file would fail every smoke on "No such file".
    assert '"$(dirname "$0")/voice_pitch.py"' in (ROOT / "scripts/smoke.sh").read_text()
    for deploy in ("scripts/deploy.sh", "scripts/deploy-here.sh"):
        assert '/scripts/voice_pitch.py"' in (ROOT / deploy).read_text(), deploy
