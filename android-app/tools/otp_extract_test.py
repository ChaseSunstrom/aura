#!/usr/bin/env python3
"""The one-time-code rule (OtpExtractor.kt), mirrored and run against its contract.

The rule and its cases are tests/contracts/otp_extract.json; OtpExtractorTest.kt runs the
same cases on the Kotlin side, so a case added there is checked in both languages.

Run:  python3 android-app/tools/otp_extract_test.py
      python3 -m pytest android-app/tools/otp_extract_test.py -q
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "tests" / "contracts" / "otp_extract.json")
                      .read_text(encoding="utf-8"))

# Two groups of three, or 4-8 digits with an optional letter prefix ("G-123456").
CANDIDATE = re.compile(r"(?<![\w-])(?:[A-Za-z]-)?(\d{3})[- ](\d{3})(?![\w])|(?<![\w])(?:[A-Za-z]-)?(\d{4,8})(?![\w])")


def extract(text: str, keywords: list[str]) -> str | None:
    kw = re.search(r"(?<![\w-])(?:" + "|".join(re.escape(k) for k in keywords) + r")(?![\w-])", text, re.I)
    if not kw:
        return None
    best: tuple[int, str] | None = None
    for m in CANDIDATE.finditer(text):
        start, end = m.start(), m.end()
        before = text[start - 1] if start else ""
        after = text[end:end + 2]
        if (before and before in "$€£#+/") or (before == "-" and start >= 2 and text[start - 2].isdigit()):
            continue
        if after[:1] == "/" or (len(after) == 2 and after[0] in ".,-:" and after[1].isdigit()):
            continue
        code = (m.group(1) + m.group(2)) if m.group(1) else m.group(3)
        dist = kw.start() - end if end <= kw.start() else max(start - kw.end(), 0)
        if best is None or dist < best[0]:
            best = (dist, code)
    return best[1] if best else None


def test_every_case_in_the_contract():
    for case in CONTRACT["cases"]:
        assert extract(case["text"], CONTRACT["keywords"]) == case["code"], case["name"]


if __name__ == "__main__":
    bad = [c["name"] for c in CONTRACT["cases"] if extract(c["text"], CONTRACT["keywords"]) != c["code"]]
    for name in bad:
        print(f"FAIL {name}", file=sys.stderr)
    print(f"{len(CONTRACT['cases']) - len(bad)}/{len(CONTRACT['cases'])} cases")
    sys.exit(1 if bad else 0)
