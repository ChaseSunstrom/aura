"""Write src/lib/audio/fixtures/wake.json: the phone's wake chain (OnDeviceWakeWord.kt) in numpy
and onnxruntime over two clips, the scores the console's chain (wake.ts) must match.

    .venv/bin/python aura-web/scripts/wake-fixture.py MODELS_DIR WAKE_CLIP NO_WAKE_CLIP

MODELS_DIR holds melspectrogram.onnx and embedding_model.onnx (the house's model mirror,
aura-core/config/models). The clips are copied beside the json as 16 kHz mono PCM16; the
scores are over each clip with two seconds of silence before it and one after, as the test feeds
it. Does not cover the fire rule (tests/contracts/wake_score.json) or the browser's resampling.
"""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
import sys
import wave
from pathlib import Path

import numpy as np
import onnxruntime as ort

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "aura-web/src/lib/audio/fixtures"
HEAD = "themes/aura/assets/wake/aura.onnx"
PAD_BEFORE, PAD_AFTER = 32000, 16000


def catalogue() -> dict[str, dict[str, str]]:
    """The mel and embedding models' pinned url and sha256, read from the house's catalogue."""
    tree = ast.parse((ROOT / "aura-core/aura/api/models.py").read_text())
    out: dict[str, dict[str, str]] = {}
    for call in ast.walk(tree):
        if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "ModelSpec":
            kw = {k.arg: k.value for k in call.keywords}
            name = ast.literal_eval(kw["name"])
            if name in ("melspectrogram.onnx", "embedding_model.onnx"):
                out[name] = {"url": ast.literal_eval(kw["url"]), "sha256": ast.literal_eval(kw["sha256"])}
    return out


def load(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2), path
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def scores(models: Path, pcm: np.ndarray) -> list[float | None]:
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = opts.inter_op_num_threads = 1
    mel = ort.InferenceSession(str(models / "melspectrogram.onnx"), opts)
    emb = ort.InferenceSession(str(models / "embedding_model.onnx"), opts)
    head = ort.InferenceSession(str(ROOT / HEAD), opts)
    x = pcm.astype(np.float32) / 32768
    tail = np.zeros(480, np.float32)
    mels: list[np.ndarray] = []
    embs: list[np.ndarray] = []
    out: list[float | None] = []
    for i in range(0, len(x) - 1279, 1280):
        chunk = x[i:i + 1280]
        step = np.concatenate([tail, chunk])
        tail = chunk[-480:].copy()
        m = mel.run(None, {mel.get_inputs()[0].name: step[None]})[0][0, 0] / 10 + 2
        mels = (mels + list(m))[-76:]
        if len(mels) < 76:
            out.append(None)
            continue
        window = np.array(mels, np.float32)[None, :, :, None]
        embs = (embs + [emb.run(None, {emb.get_inputs()[0].name: window})[0].reshape(-1)])[-16:]
        if len(embs) < 16:
            out.append(None)
            continue
        flat = np.array(embs, np.float32)[None]
        out.append(round(float(head.run(None, {head.get_inputs()[0].name: flat})[0].reshape(-1)[0]), 6))
    return out


def main(models: str, wake_clip: str, no_wake_clip: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    clips = []
    for name, src, fires in (("hey-aura.wav", wake_clip, True), ("no-wake.wav", no_wake_clip, False)):
        shutil.copyfile(src, OUT / name)
        pcm = load(OUT / name)
        padded = np.concatenate([np.zeros(PAD_BEFORE, np.int16), pcm, np.zeros(PAD_AFTER, np.int16)])
        clips.append({"file": name, "fires": fires, "scores": scores(Path(models), padded)})
    fixture = {
        "about": "aura-web/scripts/wake-fixture.py: the phone's chain over each clip, padded",
        "padBefore": PAD_BEFORE,
        "padAfter": PAD_AFTER,
        "head": HEAD,
        "headSha256": hashlib.sha256((ROOT / HEAD).read_bytes()).hexdigest(),
        "models": catalogue(),
        "clips": clips,
    }
    (OUT / "wake.json").write_text(json.dumps(fixture, indent=1) + "\n")


if __name__ == "__main__":
    main(*sys.argv[1:4])
