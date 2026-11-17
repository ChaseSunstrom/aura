"""Synthesise the JCB voice fixtures with Piper (the same TTS the stack ships).

Each voice task needs a small spoken WAV whose transcript is known, so the checker can
assert the agent heard it and took the right action. We synthesise with the repo ``.venv``
Piper and the ``en_GB-alan-medium`` voice (the stack's ``PIPER_VOICE``), which is exactly
what faster-whisper transcribes back on the live path.

Run: ``python3 bench/fixtures/gen_audio.py`` (idempotent). Needs Piper on PATH or in the
``.venv``; the model is looked up in the Hermes cache and the in-repo wyoming copy.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

_AUDIO = Path(__file__).resolve().parent / "audio"

#: id → phrase. The id is the WAV basename and matches the voice task that plays it.
_PHRASES: dict[str, str] = {
    "gpu-status": "Jarvis, what is the GPU status right now?",
    "start-goal": "Jarvis, start a goal to summarise my notes.",
    "dismiss": "Jarvis, dismiss the notification on screen.",
    "greenhouse": "Jarvis, what was the greenhouse high temperature this week?",
    "reminder": "Jarvis, remind me tonight to lock the back door.",
}

_MODEL_CANDIDATES = [
    Path.home() / ".hermes/cache/piper-voices/en_GB-alan-medium.onnx",
    Path("/opt/jarvis/aura-core/wyoming/piper/en_GB-alan-medium.onnx"),
]


def _piper() -> str:
    exe = shutil.which("piper") or str(Path(sys.executable).parent / "piper")
    if not Path(exe).exists():
        raise SystemExit("piper not found — activate .venv or install piper")
    return exe


def _model() -> Path:
    for candidate in _MODEL_CANDIDATES:
        if candidate.exists():
            return candidate
    raise SystemExit(f"piper voice model not found in {[str(p) for p in _MODEL_CANDIDATES]}")


def main() -> None:
    _AUDIO.mkdir(parents=True, exist_ok=True)
    piper, model = _piper(), _model()
    # Pin thread count so onnxruntime does not try (and noisily fail) to set CPU affinity.
    env = {**os.environ, "OMP_NUM_THREADS": "1"}
    for name, phrase in _PHRASES.items():
        out = _AUDIO / f"{name}.wav"
        proc = subprocess.run(
            [piper, "-m", str(model), "-f", str(out)],
            input=phrase.encode(), capture_output=True, env=env,
        )
        if proc.returncode != 0 or not out.exists():
            sys.stderr.write(proc.stderr.decode(errors="replace"))
            raise SystemExit(f"piper failed for {name!r}")
        print(f"wrote {out.relative_to(out.parents[2])} ({out.stat().st_size} bytes) — {phrase!r}")


if __name__ == "__main__":
    main()
