#!/usr/bin/env python3
"""The contrast helper behind the a11y gate (GLASS_HUD §3.2, §12).

Two jobs, one formula:

``--table``  reproduces the §3.2 table — every text grade over pane, panel and
             control glass, each over the room, the reactor's halo and the
             hottest point beside the reactor — from the hex values the design
             document names. The document says this script is what produced
             the numbers; if the table and the document disagree, one of them
             is wrong and the diff says which cell.

``REPORT``   reads the samples ``aura-web/e2e/a11y-contrast.spec.ts`` wrote
             to ``.verify/hud-contrast.json`` and re-derives the verdict from
             the raw pixel colours (the spec computes it in the browser; this
             is the second opinion in a second language, so a mistake in one
             cannot pass the other), printing the worst blocks.

Exit 1 when a table cell that the document marks as passing falls below AA, or
when a reported block is below its threshold and not in the spec's baseline
(``aura-web/e2e/a11y-baseline.json``, ``contrast``) at that ratio or better. Not covered: sampling the pixels
itself (the browser does that), and the reduced-transparency solids of §5.3,
which sit at or above the room column by construction.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / ".verify" / "hud-contrast.json"
#: The spec's ratchet: blocks known to fail, with the ratio they were recorded at.
BASELINE = ROOT / "aura-web" / "e2e" / "a11y-baseline.json"

# §2.1 / §3.2 / §5.1, as the design document names them.
#: BUILD_BRIEF §1: the ground, the panel colour and its alpha (rgba(4,11,19,.78)).
ROOM = "#02050a"
GLASS = (4, 11, 19)
GRADES = {"pane": 0.85, "panel": 0.78, "control": 0.6}
#: The three backdrops as composited colours: the ground, the core's radial glow
#: (color.core-glow) and the hottest point (the accent at 0.2 over the ground).
BACKDROPS = {"room": ROOM, "halo": "#061826", "hot": "#0e2e36"}
TEXT = {
    "Text": "#dceaf5",
    "Muted": "#a9bbcd",
    "Faint": "#7f93a8",
    "Reactor light": "#3fd0e8",
    "attention": "#ffb547",
    "danger-text": "#ff8fa3",
    "ok": "#5fe3a1",
}
#: Cells below AA that §3.2's two rules answer: Faint never sits on a pane (every
#: Faint/pane cell), and the scrim lifts Faint on a panel over the hot point. The
#: document marks only two of these bold and says "everything else clears AA";
#: the reproduction shows Faint/pane/halo (4.2) and Muted/pane/hot (4.2) do not.
#: The first is covered by rule 1; the second is covered by nothing and is
#: reported to the design owner — it stays flagged here until the document
#: carries a rule for it.
BELOW_BY_DESIGN = {("Faint", "pane", "room"), ("Faint", "pane", "halo"), ("Faint", "pane", "hot"), ("Faint", "panel", "hot")}
AA = 4.5


def hex_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def luminance(rgb: tuple[float, float, float]) -> float:
    def channel(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def over(top: tuple[float, float, float], alpha: float, under: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(top[i] * alpha + under[i] * (1 - alpha) for i in range(3))  # type: ignore[return-value]


def backdrop(name: str) -> tuple[float, float, float]:
    """One §3.2 column: the composited colour the text's glass sits over."""
    return hex_rgb(BACKDROPS[name])


def table() -> int:
    bad = 0
    print(f"{'Text':<14}" + "".join(f"{g + ' room/halo/hot':>28}" for g in GRADES))
    for text, hex_text in TEXT.items():
        row = f"{text:<14}"
        for grade, alpha in GRADES.items():
            cells = []
            for name in BACKDROPS:
                glass = over(GLASS, alpha, backdrop(name))
                ratio = contrast(hex_rgb(hex_text), glass)
                mark = ""
                if ratio < AA:
                    if (text, grade, name) in BELOW_BY_DESIGN:
                        mark = "*"
                    else:
                        mark = "!"
                        bad += 1
                cells.append(f"{ratio:.1f}{mark}")
            row += f"{' / '.join(cells):>28}"
        print(row)
    print("\n* below AA by design (§3.2: Faint never on a pane; the scrim on a panel)  ! below AA and not in the design")
    return 1 if bad else 0


def parse_rgb(s: str) -> tuple[float, float, float, float]:
    nums = [float(x) for x in s.replace("rgba(", "").replace("rgb(", "").rstrip(")").split(",")]
    return (nums[0], nums[1], nums[2], nums[3] if len(nums) > 3 else 1.0)


def report(path: Path) -> int:
    doc = json.loads(path.read_text())
    blocks = doc["blocks"]
    rows = []
    for b in blocks:
        r, g, bl, a = parse_rgb(b["color"])
        wr, wg, wb, _ = parse_rgb(b["worst"])
        fg = over((r, g, bl), a, (wr, wg, wb))
        ratio = contrast(fg, (wr, wg, wb))
        rows.append((ratio, b))
        # The browser's number and this one must agree to the rounding it printed.
        if abs(ratio - b["min"]) > 0.02:
            print(f"DISAGREE {b['path']} {b['text']!r}: browser {b['min']}, here {ratio:.2f}")
            return 1
    rows.sort(key=lambda t: t[0])
    known = {(k["path"], k["text"]): k["min"] for k in json.loads(BASELINE.read_text()).get("contrast", [])}
    failing = [(ratio, b) for ratio, b in rows if ratio < b["required"]]
    fresh = [(ratio, b) for ratio, b in failing if (b["path"], b["text"]) not in known or ratio < known[(b["path"], b["text"])] - 0.1]
    print(f"{len(blocks)} text blocks on {doc['scene']} at {doc['viewport']['width']}x{doc['viewport']['height']}; worst five:")
    for ratio, b in rows[:5]:
        print(f"  {ratio:5.2f}:1  {b['path']} {b['text']!r} {b['color']} over {b['worst']} (needs {b['required']})")
    print(f"{len(failing)} below AA, {len(failing) - len(fresh)} of them in the baseline, {len(fresh)} new or worse")
    return 1 if fresh else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--table", action="store_true", help="reproduce the §3.2 table from the design document's values")
    ap.add_argument("report", nargs="?", default=str(REPORT), help="the spec's samples (default .verify/hud-contrast.json)")
    args = ap.parse_args()
    if args.table:
        return table()
    path = Path(args.report)
    if not path.exists():
        print(f"SKIP: {path} not found — run `npx playwright test e2e/a11y-contrast.spec.ts` in aura-web first")
        return 1
    return report(path)


if __name__ == "__main__":
    sys.exit(main())
