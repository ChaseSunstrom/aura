"""Serving on-device model files to the phone, from the user's own server.

The Android app can run wake-word detection and speech-to-text locally, which
is faster on the tail of an utterance, works with the server unreachable, and —
the real reason — stops a continuous microphone feed leaving the phone. To do
that it needs model weights, and the weights have to come from somewhere.

**Not from the APK.** They are tens of megabytes, they change on their own
schedule, and most people will never turn the feature on. Shipping them in the
package makes every install pay for a minority feature.

**Not from the internet, either.** That is the part worth being careful about.
A phone that fetches from GitHub or Hugging Face tells a third party — and
every network between — that this device is setting up a private voice
assistant, right down to which wake word. It also breaks the one rule the app
holds everywhere else: it talks to the configured Jarvis and to nothing else,
with a pinned host and a bearer token. An exception carved out for "just the
model download" is an exception in the code path that runs on an unconfigured,
freshly-installed device.

So the server is the mirror. jarvis-core fetches a model once, on a machine
that already reaches the internet for `ollama pull`, verifies it against a
digest pinned in this file, and serves it to the phone over the same
authenticated origin the phone already uses. The phone gains no new network
trust, and on a WireGuard-only network it works unchanged.

The digest is the whole security story for the fetch: the upstream URL is
plain HTTPS to a third party, so what makes the bytes trustworthy is that they
hash to a value written down here, in the repository, reviewed like code.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis

_LOGGER = logging.getLogger(__name__)

#: Where fetched weights live, under the config directory next to `.storage`.
CACHE_DIRNAME = "models"

#: Weights trained here and shipped in the repository (the personas' wake words,
#: 2026-09-25: "Train a model per name"): no upstream to mirror, so a catalogue
#: entry names one with ``bundled:<file>`` and it is copied from this directory,
#: through the same digest check as a download.
BUNDLED_DIR = Path(__file__).resolve().parent / "wake_models"
BUNDLED = "bundled:"

#: Moonshine base, at one Hugging Face commit: a moved tag cannot change the bytes under a pin.
MOONSHINE = (
    "https://huggingface.co/onnx-community/moonshine-base-ONNX/resolve/"
    "b1e9b6aae3c3c7298f10c3798393fdf38e8fbbad/"
)

#: Refuse anything larger than this, however big the catalogue claims it is.
#: A mirror that streams an unbounded body into a home server's disk is a
#: denial-of-service with extra steps.
MAX_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """One downloadable file, and what it must hash to."""

    name: str
    url: str
    sha256: str
    #: What it is for, shown in the console and in the app.
    purpose: str
    #: Rough size, for a progress bar before the download starts.
    bytes: int = 0


#: The catalogue. Deliberately a hardcoded tuple rather than anything the
#: network or the model can extend: this is a list of URLs the server will
#: fetch and write to disk, so it is exactly the kind of list that must not be
#: writable from a request.
#:
#: openWakeWord's three-stage pipeline. The first two are shared by every wake
#: word; only the last is specific to "hey jarvis", which is why they are
#: separate files rather than one bundle.
CATALOGUE: tuple[ModelSpec, ...] = (
    ModelSpec(
        name="melspectrogram.onnx",
        url=(
            "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
            "melspectrogram.onnx"
        ),
        sha256="ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f",
        purpose="Audio to mel frames. Shared by every wake word.",
        bytes=1_087_958,
    ),
    ModelSpec(
        name="embedding_model.onnx",
        url=(
            "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
            "embedding_model.onnx"
        ),
        sha256="70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f",
        purpose="Mel frames to embeddings. Shared by every wake word.",
        bytes=1_326_578,
    ),
    ModelSpec(
        name="hey_jarvis_v0.1.onnx",
        url=(
            "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
            "hey_jarvis_v0.1.onnx"
        ),
        sha256="94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb",
        purpose="The wake word itself.",
        bytes=1_271_370,
    ),
    # The personas' names, trained here: the purpose carries the held-out score, so the
    # console shows how far each is from hey_jarvis rather than implying parity. The three
    # retired personas' heads went with them (T-0936). AURA's name (T-0861): one head for "Aura" and "Hey Aura", trained here on Kokoro
    # voices with near-miss negatives; themes/aura/assets/wake/aura.metrics.json has the run.
    ModelSpec(
        name="aura.onnx",
        url=BUNDLED + "aura.onnx",
        sha256="d29e33c18e4ab6694b05f53246ae3a136995cf09529a0a1c1ea21a3831e83c4f",
        purpose="\"Aura\" and \"Hey Aura\": wake AURA (0.96 recall, 184 false wakes a day before"
        " speaker verification, which a turn it opens requires).",
        bytes=859395,
    ),
    # Speech to text on the phone (the owner, 2026-10-08: "on phone transcription isnt
    # working"). GrapheneOS has no on-device recogniser and Vosk's JNA dies there, so the
    # phone runs Moonshine base on the onnxruntime the wake word already uses. Pinned to a
    # Hugging Face commit, int8: four CosyVoice lines all right with 0.5 s of padding.
    ModelSpec(
        name="moonshine-base-encoder.onnx",
        url=MOONSHINE + "onnx/encoder_model_quantized.onnx",
        sha256="1dd9ab0a7f987113d30affcba5a068d11c8f90fa0223caa3e491ade431ad9751",
        purpose="Speech to text on the phone: audio to features (Moonshine base, int8).",
        bytes=20_513_063,
    ),
    ModelSpec(
        name="moonshine-base-decoder.onnx",
        url=MOONSHINE + "onnx/decoder_model_merged_quantized.onnx",
        sha256="cc9f3cd6698a369c6008b41aa60aa3fb3322e7f03c9bdf19d8e6b7200afca4f3",
        purpose="Speech to text on the phone: features to words (Moonshine base, int8).",
        bytes=42_498_870,
    ),
    ModelSpec(
        name="moonshine-tokenizer.json",
        url=MOONSHINE + "tokenizer.json",
        sha256="7b913404bdd039af4756783218af4440bc07fb7d6d8258d677e34f95b3ec416f",
        purpose="Speech to text on the phone: the words' spelling (Moonshine's tokenizer).",
        bytes=3_761_754,
    ),
    # The phone's offline voice (T-0618): Piper en_GB-alan-medium (fp32) with its espeak-ng
    # data, one archive from k2-fsa's sherpa-onnx release, unpacked by the phone. The voice
    # the persona's reference clip is made with, so a line spoken offline sounds like the
    # server's. Dataset licence is unclear upstream: docs/runbooks/voice.md, "Bundled voice".
    ModelSpec(
        name="vits-piper-en_GB-alan-medium.tar.bz2",
        url=(
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
            "vits-piper-en_GB-alan-medium.tar.bz2"
        ),
        sha256="a48d4017da0f77668b27bed63fe6e04dd64c6397e1fadad4f460efb0ef7c9012",
        purpose="The phone's offline voice: Piper en_GB-alan-medium, for when the server is out.",
        bytes=67_220_121,
    ),
    # AURA's own voice on the phone (T-0872): Kokoro-82M v1.0, int8, from the same release; the
    # phone speaks af_heart from it once it has measured it keeping up. Apache-2.0 (LICENSES.md).
    ModelSpec(
        name="kokoro-int8-multi-lang-v1_0.tar.bz2",
        url=(
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
            "kokoro-int8-multi-lang-v1_0.tar.bz2"
        ),
        sha256="72c347230107ef8a31098ec4d468a605244beef5f1ed00db97efa13518800653",
        purpose="AURA's offline voice on the phone: Kokoro-82M af_heart (int8), for when the server is out.",
        bytes=132_353_410,
    ),
)

CATALOGUE_BY_NAME: dict[str, ModelSpec] = {spec.name: spec for spec in CATALOGUE}


def cache_dir(jarvis: "Jarvis") -> Path:
    return Path(jarvis.config_dir) / CACHE_DIRNAME


def local_path(jarvis: "Jarvis", name: str) -> Path | None:
    """Where `name` lives, or None if it is not a catalogue entry.

    Membership of the catalogue IS the path validation. Building a path from a
    request-supplied name and then trying to prove it stayed inside a directory
    is the losing half of that argument; a fixed set of known names never
    enters it.
    """
    spec = CATALOGUE_BY_NAME.get(name)
    if spec is None:
        return None
    return cache_dir(jarvis) / spec.name


def digest_of(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def _current(spec: ModelSpec, path: Path) -> bool:
    """Whether the cached copy is this catalogue entry's. By size, not digest: hashing a
    speech model per request is the cost this avoids. T-0893: the persona heads were
    retrained with their weights inline (13.7 KB to 214 KB) and the old stubs stayed
    cached and served. Does not catch a retrain that keeps the exact size."""
    if not path.is_file():
        return False
    size = path.stat().st_size
    return size > 0 and (not spec.bytes or size == spec.bytes)


def is_cached(jarvis: "Jarvis", name: str) -> bool:
    path = local_path(jarvis, name)
    spec = CATALOGUE_BY_NAME.get(name)
    return bool(path and spec and _current(spec, path))


def catalogue_payload(jarvis: "Jarvis") -> list[dict[str, object]]:
    """Every model, whether it is here yet, and how big it is."""
    return [
        {
            "name": spec.name,
            "purpose": spec.purpose,
            "bytes": spec.bytes,
            "sha256": spec.sha256,
            "cached": is_cached(jarvis, spec.name),
        }
        for spec in CATALOGUE
    ]


class ModelError(Exception):
    """A fetch failed, with a sentence for the user."""


async def async_ensure(jarvis: "Jarvis", name: str) -> Path:
    """Return a verified local copy of `name`, fetching it once if needed.

    Raises [ModelError] rather than returning a half-written file: a truncated
    ONNX model does not fail loudly on the phone, it fails as a wake word that
    never triggers, which is indistinguishable from the feature being off.
    """
    spec = CATALOGUE_BY_NAME.get(name)
    if spec is None:
        raise ModelError(f"{name!r} is not a model this server knows about")

    path = cache_dir(jarvis) / spec.name
    if _current(spec, path):
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    try:
        await asyncio.to_thread(_download, spec, tmp)
        actual = await asyncio.to_thread(digest_of, tmp)
        if actual != spec.sha256:
            raise ModelError(
                f"{spec.name} did not match its pinned digest "
                f"(got {actual[:16]}…, expected {spec.sha256[:16]}…). "
                "Refusing to serve it."
            )
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    _LOGGER.info("models: fetched %s (%d bytes)", spec.name, path.stat().st_size)
    return path


def _download(spec: ModelSpec, target: Path) -> None:
    """Blocking fetch. Runs on a worker thread; never on the event loop."""
    import urllib.request

    if spec.url.startswith(BUNDLED):
        source = BUNDLED_DIR / spec.url[len(BUNDLED):]
        # A name, not a path: the catalogue is code, but a slash here would still
        # be a way out of the directory.
        if "/" in spec.url[len(BUNDLED):] or not source.is_file():
            raise ModelError(f"{spec.name} is not bundled with this server")
        target.write_bytes(source.read_bytes())
        return

    request = urllib.request.Request(spec.url, headers={"User-Agent": "jarvis-core"})
    written = 0
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        if getattr(response, "status", 200) != 200:
            raise ModelError(f"{spec.url} answered {response.status}")
        with target.open("wb") as handle:
            while True:
                block = response.read(1024 * 256)
                if not block:
                    break
                written += len(block)
                if written > MAX_BYTES:
                    raise ModelError(f"{spec.name} is larger than {MAX_BYTES} bytes")
                handle.write(block)
    if written == 0:
        raise ModelError(f"{spec.url} returned nothing")
