#!/usr/bin/env python3
"""Live check: does "hey jarvis" fire across ways of saying it, through the real models?

Not in `android-app/tools/` (whose every `.py` `make test-android` runs): it needs
piper-tts, onnxruntime and the wake models (`aura-core/config/models/`, fetched by
the house), and takes two minutes of every core. Exit 2 when any is missing, 1 when
a claim below fails.

The takes are synthetic: four Piper voices (`aura-core/wyoming/piper/`) saying the
name seven ways, at three speeds and three pitches, in a little room noise. Piper is
what openWakeWord's heads were trained on, so these takes are its home ground and
score far better than a real voice does: a stand-in for the owner, not the owner.

Piper's synthesis is random (VITS noise), so two runs differ by a take or two: a
near-name like "Hey Travis" fires on one run and not the next, on both pipelines.

Two claims:
  1. the pipeline: the phone's old mel steps against openWakeWord's (the phone's now),
     on the same takes: openWakeWord's fire at least as often;
  2. the verifier: each voice in turn plays the owner, enrols one take per style plus
     its own reading and room sound, and the held-out takes are scored with and without
     the lift at each sensitivity level's gate; at every level more held-out takes fire,
     and at STRICT every voice's other phrases fire no more than the head alone makes them.
     The rhymes ("Hey darling", "Hey Travis") are what the lower gates add: the trade
     each level makes, measured, not hidden. Rhyming names said at enrolment as extra
     negatives did not move it (T-0714's log).

The pipeline is OnDeviceWakeWord.kt's, step for step, with one switch: how much
audio the melspectrogram model sees per 80 ms step. openWakeWord feeds it the new
1280 samples plus the 480 before them (8 mel frames a step); the phone fed the
1280 alone (5 frames) until T-0714, so 3 of every 8 frames never reached the
embedding model and the name arrived squeezed and gapped in time. The first run:
114 of 252 takes fired the old way, 135 openWakeWord's.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "aura-core" / "config" / "models"
VOICES = ROOT / "aura-core" / "wyoming" / "piper"

CHUNK = 1280
OVERLAP = 480
MEL_WINDOW = 76
EMBEDDING_WINDOW = 16
THRESHOLD, FRAMES = 0.5, 2  # WakeSensitivity.NORMAL

try:
    import numpy as np
    import onnxruntime as ort
    from piper import PiperVoice
    from piper.config import SynthesisConfig
except ImportError as e:  # pragma: no cover - a box without the voice stack
    print(f"skip: {e}")
    sys.exit(2)


class Pipeline:
    """OnDeviceWakeWord, with the melspectrogram's input as the one variable."""

    def __init__(self, overlap: int) -> None:
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = opts.intra_op_num_threads = 1
        load = lambda name: ort.InferenceSession(str(MODELS / name), opts, providers=["CPUExecutionProvider"])  # noqa: E731
        self.mels, self.embed, self.head = load("melspectrogram.onnx"), load("embedding_model.onnx"), load("hey_jarvis_v0.1.onnx")
        self.overlap = overlap

    def run(self, pcm: np.ndarray) -> tuple[list[float], list[np.ndarray]]:
        """Each 80 ms step's probability and its 16x96 window, once both windows are full."""
        audio = np.concatenate([np.zeros(self.overlap, np.float32), pcm.astype(np.float32)])
        mel_window: list[np.ndarray] = []
        emb_window: list[np.ndarray] = []
        scores: list[float] = []
        windows: list[np.ndarray] = []
        for start in range(self.overlap, len(audio) - CHUNK + 1, CHUNK):
            chunk = audio[start - self.overlap:start + CHUNK][None, :]
            frames = self.mels.run(None, {self.mels.get_inputs()[0].name: chunk})[0][0, 0] / 10 + 2
            mel_window = (mel_window + list(frames))[-MEL_WINDOW:]
            if len(mel_window) < MEL_WINDOW:
                continue
            x = np.array(mel_window, np.float32)[None, :, :, None]
            emb = self.embed.run(None, {self.embed.get_inputs()[0].name: x})[0].reshape(-1)
            emb_window = (emb_window + [emb])[-EMBEDDING_WINDOW:]
            if len(emb_window) < EMBEDDING_WINDOW:
                continue
            w = np.array(emb_window, np.float32)[None]
            scores.append(float(self.head.run(None, {self.head.get_inputs()[0].name: w})[0].reshape(-1)[0]))
            windows.append(w.reshape(-1))
        return scores, windows


def fires(scores) -> bool:
    """WakeScore at NORMAL: FRAMES consecutive steps at or over THRESHOLD."""
    run = 0
    for s in scores:
        run = run + 1 if s >= THRESHOLD else 0
        if run >= FRAMES:
            return True
    return False


def speak(voice: PiperVoice, text: str, length: float) -> np.ndarray:
    pcm = b"".join(c.audio_int16_bytes for c in voice.synthesize(text, SynthesisConfig(length_scale=length)))
    audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768
    rate = voice.config.sample_rate
    if rate != 16000:  # the models hear 16 kHz
        audio = np.interp(np.arange(0, len(audio), rate / 16000), np.arange(len(audio)), audio).astype(np.float32)
    return audio


def pitched(audio: np.ndarray, semitones: float) -> np.ndarray:
    """Resampled: higher and quicker, or lower and slower, as a voice under strain or tired."""
    step = 2 ** (semitones / 12)
    return np.interp(np.arange(0, len(audio), step), np.arange(len(audio)), audio).astype(np.float32)


def room(n: int, seed: int) -> np.ndarray:
    """A quiet room: low, slightly coloured noise."""
    white = np.random.default_rng(seed).standard_normal(n + 1)
    return ((white[1:] + 0.6 * white[:-1]) * 0.01).astype(np.float32)


def in_room(audio: np.ndarray, seed: int) -> np.ndarray:
    """A second of room before and after, and the room under the voice."""
    out = room(len(audio) + 16000 * 3, seed)
    out[16000 * 2:16000 * 2 + len(audio)] += audio
    return out


STYLES = {
    "plain": "Hey Jarvis.",
    "question": "Hey Jarvis?",
    "call": "Hey, Jarvis!",
    "trailing": "Hey Jarvis...",
    "okay": "Okay, Jarvis.",
    "name": "Jarvis.",
    "name?": "Jarvis?",
}
LENGTHS = (0.65, 1.0, 1.5)
PITCHES = (-4, 0, 4)
OTHERS = ["Hey Travis.", "The service is down.", "Hey darling.", "Harvest time.", "It is nervous work.",
          "Hey, are you there?", "Jasmine tea, please.", "Turn off the lights."]
READING = "The quick brown fox jumps over the lazy dog, and then it rests in the warm afternoon sun."


def fit(pos: np.ndarray, neg: np.ndarray, l2: float, epochs: int, rate: float, floor: float):
    """wake_verifier_test.fit, vectorised: the same algorithm on the real window length."""
    x = np.vstack([pos, neg]).astype(np.float64)
    mean, std = x.mean(0), np.maximum(x.std(0), floor)
    z = (x - mean) / std
    y = np.r_[np.ones(len(pos)), np.zeros(len(neg))]
    c = np.r_[np.full(len(pos), 0.5 / len(pos)), np.full(len(neg), 0.5 / len(neg))]
    w, b = np.zeros(x.shape[1]), 0.0
    for _ in range(epochs):
        e = (1 / (1 + np.exp(-(z @ w + b))) - y) * c
        w -= rate * (l2 * w + z.T @ e)
        b -= rate * e.sum()
    return lambda win: 1 / (1 + np.exp(-(((win - mean) / std) @ w + b)))


def main() -> int:
    missing = [p for p in [MODELS / "melspectrogram.onnx", MODELS / "embedding_model.onnx", MODELS / "hey_jarvis_v0.1.onnx"] if not p.exists()]
    voices = sorted(VOICES.glob("*.onnx"))
    if missing or not voices:
        print(f"skip: no models ({', '.join(p.name for p in missing)}) or voices in {VOICES}")
        return 2
    sys.path.insert(0, str(ROOT / "android-app" / "tools"))
    from wake_verifier_test import CONTRACT, LEVELS  # the spec's numbers, not a second copy

    phone, owww = Pipeline(overlap=0), Pipeline(overlap=OVERLAP)
    seed = 0
    takes: dict[str, list[tuple[str, list[float], list[np.ndarray], list[float]]]] = {}
    others: dict[str, list[tuple[list[float], list[np.ndarray], list[float]]]] = {}
    reading: dict[str, list[np.ndarray]] = {}
    for path in voices:
        voice = PiperVoice.load(path)
        takes[path.stem] = []
        for style, text in STYLES.items():
            for length in LENGTHS:
                for semis in PITCHES:
                    seed += 1
                    audio = in_room(pitched(speak(voice, text, length), semis), seed)
                    s_phone, _ = phone.run(audio)
                    s, w = owww.run(audio)
                    takes[path.stem].append((style, s, w, s_phone))
        others[path.stem] = []
        for text in OTHERS:
            seed += 1
            audio = in_room(speak(voice, text, 1.0), seed)
            s, w = owww.run(audio)
            others[path.stem].append((s, w, phone.run(audio)[0]))
        _, w = owww.run(in_room(speak(voice, READING, 1.0), seed + 1000))
        _, w_room = owww.run(room(16000 * 10, seed + 2000))
        reading[path.stem] = w + w_room

    n = sum(len(t) for t in takes.values())
    m = sum(len(o) for o in others.values())
    hit_phone = sum(fires(t[3]) for ts in takes.values() for t in ts)
    hit_owww = sum(fires(t[1]) for ts in takes.values() for t in ts)
    false_phone = sum(fires(o[2]) for os_ in others.values() for o in os_)
    false_owww = sum(fires(o[0]) for os_ in others.values() for o in os_)
    print(f"1. pipeline, NORMAL {THRESHOLD}/{FRAMES}, {n} takes in a room: the old 5-frame steps fire {hit_phone}/{n}, "
          f"openWakeWord's 8-frame steps (the phone's now) {hit_owww}/{n}; other phrases fire {false_phone}/{m} and {false_owww}/{m}")

    f = CONTRACT["fit"]
    # Each voice's verifier meets every voice's other phrases, so the head's own false
    # fires are had once per voice.
    false_total, false_head = m * len(takes), false_owww * len(takes)
    ok = hit_owww >= hit_phone
    for level, gate in LEVELS.items():
        base_hits = lifted_hits = held = false_lifted = 0
        for owner, ts in takes.items():
            # Enrolment: one take per style at the plain speed and pitch (each style's block
            # is LENGTHS x PITCHES, so index 4 is 1.0 and 0); its top three windows at or
            # over the gate, as WakeEnrol.take keeps them.
            enrol = ts[4::len(LENGTHS) * len(PITCHES)]
            pos = np.array([t[2][i] for t in enrol for i in np.argsort(t[1])[-3:] if t[1][i] >= gate])
            p = fit(pos, np.array(reading[owner]), f["l2"], f["epochs"], f["rate"], f["std_floor"])
            lift = lambda s, w, p=p, gate=gate: [max(a, float(p(win))) if a >= gate else a for a, win in zip(s, w)]  # noqa: E731
            for _, s, w, _ in (t for t in ts if not any(t is e for e in enrol)):
                held += 1
                base_hits += fires(s)
                lifted_hits += fires(lift(s, w))
            false_lifted += sum(fires(lift(s, w)) for other in others.values() for s, w, _ in other)
        print(f"2. verifier at {level} (gate {gate}), each voice the owner: held-out takes fire {base_hits}/{held} "
              f"without it, {lifted_hits}/{held} with it; other phrases {false_lifted}/{false_total} with it, "
              f"{false_head}/{false_total} with the head alone")
        ok = ok and lifted_hits > base_hits and (level != "STRICT" or false_lifted <= false_head)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
