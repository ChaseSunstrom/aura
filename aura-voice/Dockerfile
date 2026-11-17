# jarvis-cosyvoice — the house voice: Kokoro-82M behind an OpenAI-shaped
# /v1/audio/speech, Whisper on the card for /v1/audio/transcriptions, and the speaker
# gate's CAM++ voiceprints. CosyVoice 3 itself was removed (T-0955); the name stays
# until T-0953 renames the service.
#
# Python 3.11: the runtime the voice was measured on. A move is its own change.
FROM python:3.11.18-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# Debian's security updates at build time, as every other image here does: the
# CI CVE gate fails the build on a critical the base image has already fixed.
# APT_REFRESH (the day, from the build job) changes this layer's cache key daily, as in
# deploy/Dockerfile: without it the registry cache served the layer from before Debian's
# perl and openssl fixes and pipeline 489's gate failed on an upgrade asked for here.
# ffmpeg encodes Hermes's MP3s and decodes what Whisper hears.
ARG APT_REFRESH=unset
RUN echo "apt refresh $APT_REFRESH" && apt-get update -qq && apt-get -y -qq --no-install-recommends upgrade \
    && apt-get install -y -qq --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# CUDA wheels by default: the GPUs are on THIS host (ADR-0039). The CPU index stays a
# one-flag build for a host without a card --
#   --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cpu
# -- because the service still has to run there, just slowly (ADR-0039 rule 4).
# cu124 against a 610.57.04 driver: the CUDA minor-version compatibility guarantee
# covers it, and the driver userspace is mounted from the host rather than baked.
#
# torch 2.6.0: GHSA-53q9-r3pm-6pq6 (torch.load with weights_only=True still executes
# code) is fixed there, and the CI CVE gate refused the image without it (pipeline 289).
ARG TORCH_INDEX=https://download.pytorch.org/whl/cu124
# `--extra-index-url`, not `--index-url`: the PyTorch index carries torch and
# nothing else, and with it as the ONLY index the build died on
# `typing_extensions`'s build backend ("No matching distribution found for
# flit_core") the first time anyone ran it (2026-09-09).
#
# openai-whisper==20231117 has a setup.py that imports pkg_resources, which the
# newest setuptools no longer ships, and pip's isolated build environment takes
# the newest setuptools (the second build, 2026-09-09). So it is built WITHOUT
# isolation, against a setuptools that still has pkg_resources installed first.
#
# No cuFFT pin: 11.0.2.54 was pinned on 2026-09-09, when torch 2.6's 11.2.1.3 failed
# every FFT on that driver, but the Kokoro layer's install put 11.2.1.3 back, and the
# running image's FFTs on the card work with it (checked 2026-11-10).
#
# numpy, onnxruntime, soundfile and transformers at the versions the voice ran on
# under CosyVoice's requirements (2026-11-10), so dropping those moves nothing else.
# onnxruntime is the CPU build: the voiceprints are the only ONNX model, on the CPU.
RUN pip install --no-cache-dir --upgrade pip "setuptools<81" wheel \
    && pip install --no-cache-dir --extra-index-url ${TORCH_INDEX} "torch==2.6.0" "torchaudio==2.6.0" "numpy==1.26.4" \
    && pip install --no-cache-dir --no-build-isolation "openai-whisper==20231117" \
    && pip install --no-cache-dir --extra-index-url ${TORCH_INDEX} "torch==2.6.0" "torchaudio==2.6.0" "numpy==1.26.4" \
       "onnxruntime==1.18.0" "soundfile==0.12.1" "transformers==4.51.3" "fastapi<1" "uvicorn[standard]" pydantic \
    && python -c "import torch, torchaudio, numpy; assert torch.__version__.startswith('2.6.0'), torch.__version__; assert numpy.__version__ == '1.26.4', numpy.__version__; print('torch', torch.__version__, 'torchaudio', torchaudio.__version__)"

ENV PYTHONPATH=/app
# What the service imports, imported here: a package that breaks on this Python fails
# the build (allow_failure, production stays on its last good :latest) instead of the
# deploy's warm-up (T-0417). Import only: no weights, no GPU.
RUN python -c "import whisper, onnxruntime, soundfile, torchaudio.compliance.kaldi, fastapi, uvicorn"

# Kokoro-82M (Apache-2.0), the voice of every pack (aura_voice/kokoro_engine.py).
# Pinned: the two packages by version, the weights by the Hugging Face commit of
# hexgrad/Kokoro-82M, so a voice cannot change without someone deciding it.
#
# Baked into the image: the model, config and the 28
# English voices are ~370 MB and the owner wants no request out at runtime, so nothing
# is left for `kokoro` to download on first use. Three things would otherwise ask the
# internet: the hub (voices and weights; the engine opens every file by path and
# HF_HUB_OFFLINE below refuses the rest), misaki's spaCy model (`spacy.cli.download`
# when en_core_web_sm is absent, so it is installed here from its pinned release wheel
# and checked by hash) and espeak-ng (misaki's espeakng-loader wheel carries its own
# library, so no apt package).
#
# Installed under the torch/numpy/transformers/pydantic the layer above pinned: a kokoro
# that wants different ones fails this build (allow_failure, production stays on its last
# good :latest) instead of quietly moving the stack the voice was measured on.
ARG KOKORO_VERSION=0.9.4
ARG MISAKI_VERSION=0.9.4
ARG KOKORO_REVISION=f3ff3571791e39611d31c381e3a41a3af07b4987
ARG SPACY_MODEL_SHA256=1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85
RUN python -c "import importlib.metadata as m; print('\n'.join(n + '==' + m.version(n) for n in ('torch', 'torchaudio', 'numpy', 'transformers', 'huggingface-hub', 'pydantic', 'fastapi')))" > /tmp/keep.txt \
    && pip install --no-cache-dir -c /tmp/keep.txt "kokoro==${KOKORO_VERSION}" "misaki[en]==${MISAKI_VERSION}" "spacy>=3.8,<3.9" \
       "en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl#sha256=${SPACY_MODEL_SHA256}" \
    && python -c "from huggingface_hub import snapshot_download; snapshot_download('hexgrad/Kokoro-82M', revision='${KOKORO_REVISION}', local_dir='/opt/kokoro', allow_patterns=['config.json', 'kokoro-v1_0.pth', 'voices/a*.pt', 'voices/b*.pt'])" \
    && rm -rf /opt/kokoro/.cache /tmp/keep.txt \
    && chmod -R a+rX /opt/kokoro \
    && python -c "import spacy; spacy.load('en_core_web_sm'); from kokoro import KModel, KPipeline; import pathlib; assert pathlib.Path('/opt/kokoro/voices/af_heart.pt').is_file() and pathlib.Path('/opt/kokoro/kokoro-v1_0.pth').is_file()"
ENV HF_HUB_OFFLINE=1 KOKORO_MODEL_DIR=/opt/kokoro

RUN groupadd -g 10005 cosyvoice && useradd -u 10005 -g cosyvoice -m -s /usr/sbin/nologin cosyvoice
WORKDIR /app
COPY aura-voice/aura_voice ./aura_voice
# The allocation probe ships in the image so the deploy can ask the running
# container -- not the host -- whether CUDA really works here. A device node and
# a healthy nvidia-smi both survive a driver/userspace mismatch; cuMemAlloc does
# not, which is the whole point of shipping it.
COPY scripts/verify/gpu-allocation.py /opt/verify/gpu-allocation.py
USER cosyvoice
# The voiceprints' campplus.onnx arrives in a read-only mount (COSYVOICE_MODEL_DIR), the
# model directory CosyVoice shipped it in:
#
#   huggingface-cli download FunAudioLLM/Fun-CosyVoice3-0.5B-2512 campplus.onnx \
#     --revision 29e01c4e8d000f4bcd70751be16fa94bf3d85a18 \
#     --local-dir aura-core/cosyvoice/models/Fun-CosyVoice3-0.5B-2512
EXPOSE 8890
CMD ["python", "-m", "aura_voice"]
