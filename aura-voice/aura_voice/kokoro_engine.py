"""Kokoro-82M's stock voices: the voice service's one engine (T-0955).

AURA speaks in `af_heart` and JARVIS in `bm_george`, stock synthetic voices
(Apache-2.0, hexgrad/Kokoro-82M); the adapter turns a persona's name into its voice.

The weights are in the image (`/opt/kokoro`, fetched at build at the revision the
Dockerfile pins), and every file is opened by path: `kokoro`'s own loader asks the
Hugging Face hub for a voice it has not cached, and the house makes no request out
at runtime.

Deliberately not covered: a delivery direction (OpenAI's `instructions`), which
Kokoro has none of. Nor is a clone of anyone: these are the model's own voices.
"""

from __future__ import annotations

import gc
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from .engine import DEFAULT_DEVICE, EngineError, UnknownVoice

_LOGGER = logging.getLogger(__name__)

#: Where the Dockerfile puts `config.json`, `kokoro-v1_0.pth` and `voices/*.pt`.
DEFAULT_MODEL_DIR = Path(os.environ.get("KOKORO_MODEL_DIR", "/opt/kokoro"))

#: The English voices of hexgrad/Kokoro-82M's VOICES.md (American `a*`, British `b*`),
#: at the revision the Dockerfile pins. The other languages need their own misaki
#: extras and are not installed. The first letter is the pipeline's language code.
VOICES = frozenset({
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore", "af_nicole",
    "af_nova", "af_river", "af_sarah", "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael", "am_onyx",
    "am_puck", "am_santa",
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
})

SAMPLE_RATE = 24000
WARM_VOICE = "af_heart"
WARM_TEXT = "Ready."

#: One piece per sentence, so the first sentence is out while the rest is still being
#: made (Kokoro's default splits only on newlines: a one-line reply would be one piece).
SENTENCES = r"(?<=[.!?])\s+|\n+"

#: Between sentences, not before the first one (that would be latency). Commas get
#: their pause from the model itself, which renders the mark as a short break.
SENTENCE_GAP_MS = 120

#: Peak of each piece, as a fraction of full scale. CosyVoice's output is only clamped,
#: so there was no level to match: this leaves 1 dB of headroom, and a piece that is
#: near silence is left alone rather than amplified into hiss.
TARGET_PEAK = 0.89
SILENT_PEAK = 0.01


def pcm16(audio: Any) -> bytes:
    """A float waveform (a torch tensor or an array) as 16-bit PCM, peak-normalised.

    Per piece, so two sentences can differ a little in loudness; a fixed gain would
    have to be tuned per voice. The clamp is for the same reason as `engine._pcm16`.
    """
    import numpy as np

    samples = audio.detach().cpu().numpy() if hasattr(audio, "detach") else audio
    samples = np.asarray(samples, dtype="float32").reshape(-1)
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak > SILENT_PEAK:
        samples = samples * (TARGET_PEAK / peak)
    return (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def _select_device() -> str:
    """The card if one is usable, else the CPU; `COSYVOICE_DEVICE` forces either."""
    if DEFAULT_DEVICE == "cpu":
        return "cpu"
    try:
        import torch

        if not torch.cuda.is_available():
            raise RuntimeError("torch.cuda.is_available() is false")
        torch.empty(16, device="cuda")
        return "cuda"
    except Exception as err:  # noqa: BLE001 - degrading is the point
        if DEFAULT_DEVICE == "cuda":
            raise EngineError(f"COSYVOICE_DEVICE=cuda but no CUDA device is usable: {err}") from err
        _LOGGER.warning("kokoro: no usable CUDA device (%s); running on the CPU", err)
        return "cpu"


class KokoroEngine:
    """Kokoro's stock voices, loaded on first use and given back on release."""

    sample_rate = SAMPLE_RATE
    voices = VOICES

    def __init__(self, model_dir: Path | None = None, pipeline_factory: Any = None) -> None:
        self.model_dir = Path(model_dir) if model_dir is not None else DEFAULT_MODEL_DIR
        self.device = "cpu"
        self.warm = False
        # `factory(lang_code, model_dir, device) -> pipeline`, injectable so the tests
        # need neither torch nor weights. A pipeline is called as
        # `pipeline(text, voice=<path>, speed=, split_pattern=)` and yields objects
        # with `.audio` (a float waveform at 24 kHz), as kokoro's KPipeline does.
        self._factory = pipeline_factory or self._kokoro_pipeline
        self._pipelines: dict[str, Any] = {}
        self._model: Any = None
        # The model is not re-entrant, and one line at a time is as fast as two.
        self._lock = threading.Lock()

    def available(self) -> bool:
        return (self.model_dir / "kokoro-v1_0.pth").is_file()

    def _kokoro_pipeline(self, lang: str, model_dir: Path, device: str) -> Any:  # pragma: no cover - needs the stack
        try:
            from kokoro import KModel, KPipeline
        except ImportError as err:
            raise EngineError(f"the kokoro package is not importable in this image: {err}") from err
        if self._model is None:
            self._model = KModel(
                repo_id="hexgrad/Kokoro-82M",
                config=str(model_dir / "config.json"),
                model=str(model_dir / "kokoro-v1_0.pth"),
            ).to(device).eval()
        return KPipeline(lang_code=lang, repo_id="hexgrad/Kokoro-82M", model=self._model, device=device)

    def _load(self, lang: str) -> Any:
        pipeline = self._pipelines.get(lang)
        if pipeline is not None:
            return pipeline
        if not self.available():
            raise EngineError(
                f"no Kokoro weights at {self.model_dir}: the image is meant to carry them"
            )
        began = time.monotonic()
        try:
            self.device = _select_device()
            pipeline = self._factory(lang, self.model_dir, self.device)
        except EngineError:
            raise
        except Exception as err:  # noqa: BLE001 - one error type leaves this module
            raise EngineError(f"could not load Kokoro: {type(err).__name__}: {err}") from err
        _LOGGER.info("kokoro %s pipeline ready on %s in %.1fs", lang, self.device, time.monotonic() - began)
        self._pipelines[lang] = pipeline
        return pipeline

    def warm_up(self) -> None:
        """Load and speak one word, so the first line a person hears is not the load."""
        began = time.monotonic()
        self.synthesize(WARM_TEXT, WARM_VOICE)
        # The British pipeline too: bm_george is the JARVIS theme's voice.
        with self._lock:
            self._load("b")
        self.warm = True
        _LOGGER.info("kokoro warm on %s in %.1fs", self.device, time.monotonic() - began)

    def release(self) -> None:
        """Give the card back (ADR-0039 §3); the next line pays the load again."""
        with self._lock:
            self._pipelines.clear()
            self._model = None
            self.warm = False
            try:
                import torch

                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001 - nothing to give back without torch
                pass

    def synthesize(self, text: str, voice: str, speed: float = 1.0) -> tuple[bytes, int]:
        pieces = list(self.stream(text, voice, speed))
        if not pieces:
            raise EngineError("the model produced no audio")
        return b"".join(pieces), SAMPLE_RATE

    def stream(self, text: str, voice: str, speed: float = 1.0):
        """16-bit PCM a sentence at a time; the first is out before the rest exists."""
        if voice not in VOICES:
            raise UnknownVoice(voice)
        gap = b"\x00\x00" * (SAMPLE_RATE * SENTENCE_GAP_MS // 1000)
        with self._lock:
            pipeline = self._load(voice[0])
            first = True
            try:
                for result in pipeline(
                    text, voice=str(self.model_dir / "voices" / f"{voice}.pt"),
                    speed=speed, split_pattern=SENTENCES,
                ):
                    audio = getattr(result, "audio", None)
                    if audio is None:
                        continue
                    if not first:
                        yield gap
                    first = False
                    yield pcm16(audio)
            except Exception as err:  # noqa: BLE001 - one error type leaves this module
                raise EngineError(f"synthesis failed: {type(err).__name__}: {err}") from err
