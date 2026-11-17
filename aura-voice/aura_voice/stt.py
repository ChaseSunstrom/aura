"""Speech to text on the voice's own card: Whisper beside CosyVoice.

Hermes transcribes with faster-whisper on the CPU, ~3 s for a short car request
(2026-10-08, warm: 2.7-3.7 s for a 4.5 s clip whatever the beam or thread count),
and every voice turn waits for it before the model starts. On card 0 the same
`small` model takes 0.6-0.8 s in fp16 and ~1 GB. The adapter asks here first and
falls back to Hermes on any failure, so this is a speed-up, never a dependency.

The weights are read from the read-only models mount (`WHISPER_DIR`), never
downloaded at runtime: a missing file is a 503, and the adapter's fallback.
`release` drops the model with CosyVoice's own for a llama-swap load (ADR-0039 §3).

What it does not do: VAD. A segment Whisper itself thinks is silence and is not
confident about is dropped, the same gate Hermes applies (`no_speech_prob` over
0.6 AND `avg_logprob` under -1.0); the adapter's noise filter runs after either.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

#: Where `<model>.pt` lives: the host's voice models directory, mounted read-only.
WHISPER_DIR = Path(os.environ.get("WHISPER_DIR", "/models/whisper"))
#: `small` is what Hermes runs; the same model keeps the words the same.
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
#: Hermes' `stt.local.initial_prompt`: the house's words, so "Jarvis" is spelled.
PROMPT = "Jarvis, turn on the lights. Jarvis, show me the GPU. Jarvis, text Isa. Jarvis, approve it."
#: The house's names the adapter sends (T-0426) are cut here, whoever sent them: whisper
#: keeps only a prompt's last ~223 tokens, and PROMPT must survive at the end.
NAMES_MAX = 500

#: Which card: `cuda:1` when the deploy passes a second one (docker-compose.gpu2.yml).
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cuda")
#: Free memory the card must have before Whisper (~0.8 GB in use) is loaded on it.
MIN_FREE_BYTES = int(os.environ.get("WHISPER_MIN_FREE_MB", "2500")) << 20

NO_SPEECH = 0.6
LOW_CONFIDENCE = -1.0


class Unavailable(RuntimeError):
    """No card or no weights: the caller falls back to the CPU path."""


def initial_prompt(names: str = "") -> str:
    """The house's own names, then PROMPT. Whisper keeps a prompt's tail, so the examples
    that teach it to spell "Jarvis" go last. Does not check the names are the house's."""
    names = " ".join(str(names or "").split())[:NAMES_MAX]
    return f"{names}. {PROMPT}" if names else PROMPT


def keep(segment: dict[str, Any]) -> bool:
    """Pure: whether a segment is speech, by Hermes' own silence gate."""
    return not (
        float(segment.get("no_speech_prob", 0.0)) > NO_SPEECH
        and float(segment.get("avg_logprob", 0.0)) < LOW_CONFIDENCE
    )


def words(result: dict[str, Any]) -> str:
    """The transcript from Whisper's result, without the segments the gate drops."""
    segments = result.get("segments")
    if not isinstance(segments, list):
        return str(result.get("text") or "").strip()
    return " ".join(str(s.get("text") or "").strip() for s in segments if keep(s)).strip()


class Transcriber:
    """One Whisper model, loaded on first use, one transcription at a time."""

    def __init__(self, directory: Path = WHISPER_DIR, name: str = WHISPER_MODEL) -> None:
        self._path = directory / f"{name}.pt"
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        if not self._path.is_file():
            raise Unavailable(f"no Whisper weights at {self._path}")
        import torch  # noqa: PLC0415 - torch is heavy and only this path needs it
        import whisper  # noqa: PLC0415

        if not torch.cuda.is_available():
            raise Unavailable("no CUDA card; the CPU path is Hermes'")
        # Only with room to spare: the card is shared with other machines' work (~1 GB
        # free on 2026-10-08, where the load ran out of memory), and a Whisper that takes
        # the last of it leaves CosyVoice to run out mid-reply -- worse than a slow
        # transcript. Without the headroom the adapter's CPU path answers.
        free, _total = torch.cuda.mem_get_info(torch.device(WHISPER_DEVICE))
        if free < MIN_FREE_BYTES:
            raise Unavailable(f"the card has {free >> 20} MiB free; Whisper wants {MIN_FREE_BYTES >> 20}")
        # Half precision except the LayerNorms, which Whisper runs in fp32.
        try:
            model = whisper.load_model(str(self._path), device="cpu").half()
            for module in model.modules():
                if isinstance(module, torch.nn.LayerNorm):
                    module.float()
            self._model = model.to(WHISPER_DEVICE)
        except torch.OutOfMemoryError as err:
            self._model = None
            torch.cuda.empty_cache()
            raise Unavailable(f"the card is full: {err}") from err
        _LOGGER.info("whisper %s loaded on %s (fp16)", self._path.stem, WHISPER_DEVICE)
        return self._model

    def warm(self) -> None:
        """Load now rather than on the first request (~3 s): called by the boot warm-up.
        A card without room or without weights is left for the CPU path, quietly."""
        with self._lock:
            try:
                self._load()
            except Unavailable as err:
                _LOGGER.info("whisper not warmed: %s", err)

    def transcribe(self, path: str, language: str = "en", names: str = "") -> str:
        with self._lock:
            model = self._load()
            try:
                result = model.transcribe(
                    path, language=language or None, fp16=True, beam_size=5,
                    condition_on_previous_text=False, initial_prompt=initial_prompt(names),
                )
            except RuntimeError as err:
                # torch.OutOfMemoryError is a RuntimeError: a full card mid-decode is the
                # fallback's case too, and the model is kept for the next request.
                if "out of memory" not in str(err).lower():
                    raise
                import torch  # noqa: PLC0415

                torch.cuda.empty_cache()
                raise Unavailable(f"the card is full: {err}") from err
        return words(result)

    def release(self) -> None:
        with self._lock:
            if self._model is None:
                return
            self._model = None
            try:
                import torch  # noqa: PLC0415

                torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001 - freeing is best effort
                pass
