# Third-party models and voices

What the house runs that is not ours, under which licence, and where it came from.
Code dependencies are audited by `scripts/verify/dep-audit.py`; this file is for the
models and voices, which no package manager lists.

| Component | Used for | Licence | Source | Pinned at | Notes |
|---|---|---|---|---|---|
| Kokoro-82M, and its stock voices (`af_heart`, `af_bella`, `af_nicole`, `bm_george` and the other English voices of its VOICES.md) | AURA's voice (`af_heart`), through `jarvis-cosyvoice` | Apache-2.0 | [hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) | revision `f3ff3571791e39611d31c381e3a41a3af07b4987` (`KOKORO_REVISION` in `aura-voice/Dockerfile`) | Stock synthetic voice, not a clone of any person. Weights are fetched once at image build and baked into the image; nothing is downloaded at runtime. |
| Kokoro-82M v1.0, int8, as k2-fsa/sherpa-onnx exports it (`kokoro-int8-multi-lang-v1_0`: `model.int8.onnx`, `voices.bin`, `tokens.txt`, `lexicon-*.txt`) | AURA's offline voice on the phone (`af_heart`, speaker 3), through sherpa-onnx (T-0872) | Apache-2.0 (the archive's own LICENSE) | [sherpa-onnx tts-models release](https://github.com/k2-fsa/sherpa-onnx/releases/tag/tts-models) | sha256 `72c347230107ef8a31098ec4d468a605244beef5f1ed00db97efa13518800653` (`aura-core/aura/api/models.py`) | Mirrored by the house and fetched by the phone; never in the APK or git. |
| espeak-ng data (`espeak-ng-data/`, inside both phone voice archives) | Turning words into phonemes for the phone's offline voices | GPL-3.0-or-later | [espeak-ng](https://github.com/espeak-ng/espeak-ng) | inside the pinned archives above and in `docs/runbooks/voice.md` | Data files read at run time, downloaded to the owner's phone, not linked into the app. Check the GPL's terms before shipping the app or the archives to anyone else. |
| Piper `en_GB-alan-medium` (`vits-piper-en_GB-alan-medium`) | JARVIS's offline voice on the phone, through sherpa-onnx (T-0618) | Code MIT; the dataset's licence is not stated ("All Rights Reserved" in mimic3-voices) | [sherpa-onnx tts-models release](https://github.com/k2-fsa/sherpa-onnx/releases/tag/tts-models) | sha256 in `aura-core/aura/api/models.py` | For the owner's own devices only; `docs/runbooks/voice.md`, "Licence". |

The `kokoro` and `misaki[en]` packages (`0.9.4`, pinned in the same Dockerfile) and the
spaCy model `en_core_web_sm` (`3.8.0`, pinned by hash) ride along with it. Their licences
are those their projects publish; check them there before redistributing the image.
