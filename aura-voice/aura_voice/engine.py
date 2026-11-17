"""The voice service's shared errors and device choice, and the CAM++ voiceprints.

CosyVoice 3's engine lived here until T-0955 removed it (Kokoro speaks every line,
kokoro_engine.py). What stays is what the rest of the service shares: the errors,
the device setting, and the speaker gate's voiceprints, which read `campplus.onnx`
from the CosyVoice model directory the compose file still mounts.

Deliberately not covered: whether a voiceprint is good enough to tell two people
apart. That is the house's speaker gate's threshold, not this module's.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

#: The directory that holds `campplus.onnx` (3D-Speaker's CAM++), mounted read-only:
#: the model CosyVoice shipped for its voice cloning, kept for the voiceprints.
DEFAULT_MODEL_DIR = Path(os.environ.get("COSYVOICE_MODEL_DIR", "/models/Fun-CosyVoice3-0.5B-2512"))

#: `auto` (the default) means CUDA if a device is really allocatable and CPU
#: otherwise; `cpu` and `cuda` force. The service must never refuse to start
#: because a card is absent (ADR-0039 rule 4): degrading is the point.
DEFAULT_DEVICE = os.environ.get("COSYVOICE_DEVICE", "auto").strip().lower() or "auto"


class EngineError(RuntimeError):
    """The model could not be loaded, or could not speak the text it was given."""


class UnknownVoice(LookupError):
    """No voice is registered under that name."""


#: A frame of the voiceprint's trim, and how far above the clip's own noise floor (its
#: quietest fifth of frames) a frame must be to count as voice. Measured on the car's
#: case (2026-10-08): a clip padded with 3 s of silence and heavy road noise went from
#: 0.55 to 0.71 against a clean line in the same voice; a clean clip from 0.92 to 0.88.
VOICED_FRAME_MS = 30
VOICED_MARGIN_DB = 6.0


def voiced(samples: Any, rate: int) -> Any:
    """The frames of `samples` that stand above the clip's noise floor, joined.

    The car's clips are seconds of silence and road noise around a short command, and
    CAM++ fingerprinted all of it: the owner's car turns scored 0.08-0.42 against his
    own car prints. Not a speech detector: music or a voice on the radio stands above
    the floor too. Under half a second kept is too little to judge on, so the whole
    clip is returned rather than a sliver.
    """
    import numpy as np

    n = max(1, int(rate * VOICED_FRAME_MS / 1000))
    count = len(samples) // n
    if count < 2:
        return samples
    frames = np.asarray(samples[: count * n], dtype="float32").reshape(count, n)
    level = 20 * np.log10(np.sqrt(np.mean(frames * frames, axis=1)) + 1e-9)
    keep = level > np.percentile(level, 20) + VOICED_MARGIN_DB
    if int(keep.sum()) * n < rate // 2:
        return samples
    return frames[keep].reshape(-1)


class VoicePrint:
    """A speaker's voiceprint from CAM++ (3D-Speaker), the model CosyVoice ships for
    its own voice cloning (`campplus.onnx`), on the CPU and apart from the voice
    model: the house's speaker gate asks for one per sample it hears (the owner,
    2026-09-25: "enrollment isnt great it still picks up on other people").

    192 floats per clip, compared by cosine. Built on first use and kept; never
    touches the GPU or the synthesis lock.
    """

    def __init__(self, model_dir: Path | None = None) -> None:
        self.path = (Path(model_dir) if model_dir is not None else DEFAULT_MODEL_DIR) / "campplus.onnx"
        self._session: Any = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        return self.path.is_file()

    def embed(self, wav: bytes) -> list[float]:
        import io as _io

        import numpy as np
        import onnxruntime
        import soundfile
        import torch
        import torchaudio.compliance.kaldi as kaldi

        data, rate = soundfile.read(_io.BytesIO(wav), dtype="float32", always_2d=True)
        # Every print and every check is taken from the voiced frames alike: the
        # enrolled and the heard clip have to be judged the same way.
        mono = torch.from_numpy(np.ascontiguousarray(voiced(data.mean(axis=1), rate))).unsqueeze(0)
        if rate != 16000:
            import torchaudio.functional as taf

            mono = taf.resample(mono, rate, 16000)
        if mono.shape[1] < 16000 // 2:
            raise EngineError("under half a second of audio: too short for a voiceprint")
        with self._lock:
            if self._session is None:
                options = onnxruntime.SessionOptions()
                options.intra_op_num_threads = 1
                self._session = onnxruntime.InferenceSession(str(self.path), sess_options=options, providers=["CPUExecutionProvider"])
            feat = kaldi.fbank(mono, num_mel_bins=80, dither=0, sample_frequency=16000)
            feat = feat - feat.mean(dim=0, keepdim=True)
            out = self._session.run(None, {self._session.get_inputs()[0].name: feat.unsqueeze(0).numpy()})[0]
        vector = np.asarray(out, dtype="float32").flatten()
        return [round(float(x), 6) for x in vector]
