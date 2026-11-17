#!/usr/bin/env python3
"""Fetch the sherpa-onnx runtime AAR at build time, pinned by sha256 (T-0618).

It is code, so it ships in the APK (the voice's weights do not: the phone downloads those from
the house, `aura-core/aura/api/models.py`). It is 38 MB, so like tailnet.aar it is fetched
into the gitignored `app/libs/`, the way design/fonts.py pins fonts. The static-onnxruntime build
is used so its runtime cannot clash with onnxruntime-android's libonnxruntime.so. A second run
with the file present does nothing; move the pin on purpose.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import urllib.request
from pathlib import Path

VERSION = "2.0.1"
NAME = f"sherpa-onnx-static-link-onnxruntime-{VERSION}.aar"
URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/v{VERSION}/{NAME}"
SHA = "07e042d97f3a6bd5b7fe74d4eef0513dc01a714b16c15a903250095aba43a575"
DEST = Path(__file__).resolve().parents[1] / "app" / "libs" / NAME


def main() -> None:
    if DEST.is_file() and hashlib.sha256(DEST.read_bytes()).hexdigest() == SHA:
        return
    DEST.parent.mkdir(parents=True, exist_ok=True)
    part = DEST.with_suffix(".part")
    with urllib.request.urlopen(URL, timeout=120) as r, open(part, "wb") as f:  # noqa: S310
        shutil.copyfileobj(r, f)
    got = hashlib.sha256(part.read_bytes()).hexdigest()
    if got != SHA:
        part.unlink()
        sys.exit(f"{URL}: sha256 {got} is not the pinned {SHA}")
    part.replace(DEST)


if __name__ == "__main__":
    main()
