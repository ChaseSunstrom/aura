#!/usr/bin/env python3
"""Executable spec: the offline page in the APK has what ConsolePage.kt writes into and catches.

The page is the console's /offline, inlined into the assets by
`jarvis-web/scripts/offline-asset.mjs`; ConsolePage fills its `next` id with the countdown
and `detail` with when Jarvis was last reached, and catches its two links. A page rebuilt
without one of them shows a dead button or no countdown, and nothing else would notice: this
pins both sides to the same names, and that the asset needs no network (no script, no
external stylesheet or font).

Run:  python3 android-app/tools/offline_page_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSET = ROOT / "android-app/app/src/main/assets/console-offline.html"
KOTLIN = ROOT / "android-app/app/src/main/kotlin/ai/jarvis/app/ui/ConsolePage.kt"


def test_the_page_has_what_the_phone_fills_and_catches() -> None:
    page, kotlin = ASSET.read_text(), KOTLIN.read_text()
    assert 'OFFLINE_ASSET = "console-offline.html"' in kotlin, "ConsolePage no longer loads console-offline.html"
    # `next` the countdown, `detail` when Jarvis was last reached (T-0481).
    for id_ in ("next", "detail"):
        assert f"getElementById('{id_}')" in kotlin and f'id="{id_}"' in page, f"the {id_} id differs"
    for link in ("jarvis-offline:reconnect", "jarvis-offline:settings"):
        assert f'href="{link}"' in page and f'"{link}"' in kotlin, f"{link} is not on both sides"
    assert 'data-state="offline"' in page, "the page lost the offline core"


def test_the_page_needs_no_network() -> None:
    page = ASSET.read_text()
    assert "<script" not in page, "the offline page carries script"
    assert not re.search(r'<link[^>]+stylesheet', page), "a stylesheet is linked, not inlined"
    assert not re.search(r"url\(['\"]?(\.\./)*/?fonts/", page), "a font is fetched, not inlined"


def main() -> int:
    failed = 0
    for test in (test_the_page_has_what_the_phone_fills_and_catches, test_the_page_needs_no_network):
        try:
            test()
            print(f"ok   {test.__name__}")
        except (AssertionError, OSError) as exc:
            print(f"FAIL {test.__name__}: {exc}")
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
