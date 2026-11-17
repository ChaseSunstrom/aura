#!/usr/bin/env python3
"""Every icon Jarvis ships, drawn from the v2 core's own geometry.

The icon is the core on the stage (T-0808): the three segmented rings of
``tests/contracts/core_frames.json`` (the numbers ``engine.ts`` draws and the
phone's ``CoreEngineTest`` reads) at their rest pose, projected through the
same tilt and camera as ``canvas2d.ts``, with the particle shell round them and
the lit heart in the middle, painted in the same order (back shell, faces far
to near, front shell, struts, heart). It was the Reactor II instrument before,
and the stage stopped drawing that in v2, so the tab strip and the launcher
showed a JARVIS the console no longer had. Before that there were four
hand-drawn reactors (a favicon script in the web tree, five vector drawables on
the phone, a dot in the desktop shell's tray code, and the HUD's own) and none
of them agreed on how many rings there were; this file is why there is one.

Two tiers, because a 16 px tab cannot hold what a 512 px tile can:

  * **full** (above ``SMALL`` px, and every vector): the shell's points, each
    segment's dark face with its lit edge, far edge, windings and LED, the
    struts, the heart's halo and its wire sphere;
  * **reduced** (``SMALL`` px and under, the tray and the 24 dp status icon):
    the rings alone, each segment a solid face shaded by depth on a dark rim
    so the crossings still read, scaled up to fill the plate, and the heart.
    ``favicon.svg`` is this tier: a browser only ever draws it at tab size.

Outputs:

  * ``aura-web/static/``: ``favicon.svg``, ``favicon.ico`` (16 + 32),
    ``apple-touch-icon.png`` (180, opaque: iOS rounds it itself),
    ``icon-192.png``, ``icon-512.png``;
  * ``aura-web/src/lib/theme-icons/``: each theme pack's ``icons.favicon``, as ``<id>.ico``
    (copied, not drawn: the tab wears the worn theme's own icon);
  * ``android-app/app/src/main/res/drawable/``: the adaptive icon's
    ``ic_jarvis_foreground`` / ``_background`` / ``_monochrome`` layers,
    ``ic_jarvis_splash`` and the alpha-only ``ic_jarvis_status`` notification icon —
    vector drawables, so the launcher scales them itself;
  * ``aura-desktop-app/icons/``: PNGs at the contract's desktop sizes plus
    ``icon.png`` and the two tray sizes;
  * ``aura-desktop-app/src-tauri/icons/``: the Tauri bundle's set
    (``tauri.conf.json`` ``bundle.icon``): 32, 64, 128, 128@2x, ``icon.png``,
    ``icon.ico`` and ``icon.icns``. Hand-added until now, so the desktop app's
    window and installer still wore the old mark.

Colours are never typed here: they are read from the generated console theme
(``design/generated/console-theme.json``, which is ``design/tokens.json``'s),
and the script refuses to run if the core's own palette in the contract has
drifted from those tokens. Stdlib only, and every byte is deterministic (fixed
zlib level, no timestamps, fixed float formatting), so ``make icons && git diff
--exit-code`` proves the committed files are the geometry's and ``--check`` is
the same proof in CI without touching the tree. Not covered: what a launcher's
mask or a browser's SVG renderer does with them; the contact sheet
(``make icons`` then ``--sheet PATH``) is how a person looks.

    python3 scripts/gen-icons.py               write every icon
    python3 scripts/gen-icons.py --check       exit 1 naming any stale file
    python3 scripts/gen-icons.py --sheet PATH  a contact sheet PNG of the set
"""

from __future__ import annotations

import base64
import json
import math
import re
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "tests/contracts/reactor.json"
FRAMES = ROOT / "tests/contracts/core_frames.json"
THEME = ROOT / "design/generated/console-theme.json"

WEB_STATIC = ROOT / "aura-web/static"
WEB_THEME_ICONS = ROOT / "aura-web/src/lib/theme-icons"
ANDROID_DRAWABLE = ROOT / "android-app/app/src/main/res/drawable"
DESKTOP_ICONS = ROOT / "aura-desktop-app/icons"
TAURI_ICONS = ROOT / "aura-desktop-app/src-tauri/icons"
#: The default theme pack (design/themes.py DEFAULT). When it ships its own icon PNGs
#: (themes/<id>/theme.json ``icons.sizes``), the console's, the desktop app's and the
#: Tauri bundle's icons are those, not the drawn core (T-0861: AURA's galaxy is a
#: painting, not geometry this file can draw). The phone's per-pack icons are the
#: pack's own drawables (``ic_aura``); the ``ic_jarvis_*`` set stays JARVIS's.
THEMES = ROOT / "themes"

#: The generated marker every text output carries. The token lint skips a
#: file carrying ``@generated from design/tokens.json`` — these files hold hex
#: values, all of them the tokens', and the phrase is what says so.
GENERATED = "@generated from design/tokens.json and tests/contracts/core_frames.json by scripts/gen-icons.py — DO NOT EDIT"

# --- the still -------------------------------------------------------------------------

#: Rasters at or under this many pixels take the reduced tier.
SMALL = 64
#: Where the segments stand in the still: the spin phase, in radians. Any phase
#: is the core; this one keeps a gap off the equator ring's two ends, so the
#: ring reads as a ring and not as a bar, and no LED sits on the heart.
SPIN = 0.3
#: The still's moment of the rings' motion (engine.ts ringPose): the wobble at
#: its peak on the equator ring, so it shows its top and not a bar, and the gyro
#: this far round, so the two crossed rings stand open as they mostly do on the
#: stage rather than at the contract's narrow rest angle.
LEAN = 0.12
GYRO = 0.3
#: Points on the shell (the stage draws 1500; an icon needs the sphere's shape,
#: not its texture). The full tier fits the whole shell in the plate, as the
#: stage frames it; the reduced tier drops it and the rings fill FIT_REDUCED.
SHELL_POINTS = 200
FIT_REDUCED = 0.98
#: The shell's radius against the stage's. On the stage the shell is the room
#: round JARVIS; on a tile the rings are the subject, so the shell is drawn
#: closer in and the rings get three quarters of the plate instead of three fifths.
SHELL_IN = 0.85
#: Stroke widths and dot radii, in S (the stage's unit: SPREAD of the short
#: side). The stage's own are a pixel on a 600 px board, which is a hairline
#: here; these are what reads on a launcher tile and still looks like the core.
EDGE = 0.018
FAR_EDGE = 0.012
WINDING = 0.009
LED_R = 0.02
SHELL_R = 0.008
WIRE = 0.01
#: The reduced tier's dark rim round each face.
RIM = 0.05
#: Points per face arc: canvas2d.ts's ARC.
ARC = 5
#: The shell's alpha levels, as canvas2d.ts batches them (one style per level).
LEVELS = 4
#: The rounded plate's corner, as a fraction of the side.
PLATE_RADIUS = 0.2


def load() -> tuple[dict, dict, dict]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    frames = json.loads(FRAMES.read_text(encoding="utf-8"))
    theme = json.loads(THEME.read_text(encoding="utf-8"))
    return contract, frames, theme


class Core:
    """The core at rest, as numbers every emitter reads."""

    def __init__(self, contract: dict, frames: dict, theme: dict) -> None:
        view = frames["view"]
        self.tc, self.ts = math.cos(view["TILT"]), math.sin(view["TILT"])
        self.camera: float = view["CAMERA"]
        self.strut_in: float = view["STRUT_IN"]
        self.rings: list[dict] = frames["RINGS"]
        self.segment: dict = frames["SEGMENT"]
        idle = frames["LOOKS"]["idle"]
        self.glow: float = idle["glow"]
        self.shell_k: float = idle["shell"] * SHELL_IN
        self.margin: float = contract["geometry"]["margin"]
        self.safe_zone: float = contract["icons"]["android_safe_zone"]
        self.web_sizes: list[int] = contract["icons"]["sizes"]["web_favicon_png"]
        self.desktop_sizes: list[int] = contract["icons"]["sizes"]["desktop"]
        palette = theme["palette"]
        hexes = {name: palette[name]["hex"] for name in palette}
        self.bg = hexes["bg"]
        self.ground = hexes["core-glow"]
        self.haze = hexes["core-haze"]
        self.ink = hexes["accent"]
        self.white = hexes["core-white"]
        self.spark = hexes["core-spark"]
        self.halo = hexes["core-halo"]
        self.face = hexes["core-face"]
        # The core draws from the contract's numbers and the icon from the tokens:
        # refuse to paint a core whose colours have drifted from the stage's.
        drift = [
            name for name, have, want in (
                ("accent", self.ink, idle["color"]),
                *((f"core-{k}", hexes[f"core-{k}"], v) for k, v in frames["PALETTE"].items()),
            ) if [round(c * 255) for c in rgb(have)] != list(want)
        ]
        if drift:
            raise SystemExit(f"core_frames.json and the tokens disagree on: {', '.join(drift)} (run make tokens, then the core's contract test)")


# --- colour ------------------------------------------------------------------------------


def rgb(hex_: str) -> tuple[float, float, float]:
    h = hex_.lstrip("#")
    return int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0


def mix_hex(a: str, b: str, k: float) -> str:
    x, y = rgb(a), rgb(b)
    return "#" + "".join(f"{round((x[i] + (y[i] - x[i]) * k) * 255):02x}" for i in range(3))


def argb(hex_: str, alpha: float) -> str:
    """``#AARRGGBB`` for a vector drawable, alpha 0–1."""
    return f"#{round(alpha * 255):02X}{hex_.lstrip('#').upper()}"


# --- primitives ------------------------------------------------------------------------------

Pt = tuple[float, float]


@dataclass(frozen=True)
class Poly:
    """A filled polygon."""

    pts: tuple[Pt, ...]
    colour: str
    alpha: float
    cls: str


@dataclass(frozen=True)
class Lines:
    """Stroked polylines, one style."""

    lines: tuple[tuple[Pt, ...], ...]
    closed: bool
    colour: str
    alpha: float
    width: float
    cls: str


@dataclass(frozen=True)
class Dot:
    x: float
    y: float
    r: float
    colour: str
    alpha: float
    cls: str


@dataclass(frozen=True)
class Glow:
    """A radial gradient disc: stops of (offset, colour, alpha)."""

    x: float
    y: float
    r: float
    stops: tuple[tuple[float, str, float], ...]


@dataclass(frozen=True)
class Seg:
    """One ring segment's primitives, so the SVG can say which they are."""

    items: tuple


def flat(prims: list) -> list:
    out: list = []
    for p in prims:
        out += flat(list(p.items)) if isinstance(p, Seg) else [p]
    return out


def place(prims: list, cx: float, cy: float, S: float) -> list:
    """Primitives in S units about the centre, into an output's units."""
    def pt(p: Pt) -> Pt:
        return cx + p[0] * S, cy + p[1] * S

    out: list = []
    for p in prims:
        if isinstance(p, Seg):
            out.append(Seg(tuple(place(list(p.items), cx, cy, S))))
        elif isinstance(p, Poly):
            out.append(Poly(tuple(pt(q) for q in p.pts), p.colour, p.alpha, p.cls))
        elif isinstance(p, Lines):
            out.append(Lines(tuple(tuple(pt(q) for q in ln) for ln in p.lines), p.closed, p.colour, p.alpha, p.width * S, p.cls))
        elif isinstance(p, Dot):
            out.append(Dot(*pt((p.x, p.y)), p.r * S, p.colour, p.alpha, p.cls))
        else:
            out.append(Glow(*pt((p.x, p.y)), p.r * S, p.stops))
    return out


# --- the scene ---------------------------------------------------------------------------------


def project(core: Core, x: float, y: float, z: float) -> tuple[float, float, float]:
    """canvas2d.ts ``project``, in S units with y down: (x, y, depth)."""
    y1 = y * core.tc - z * core.ts
    z1 = y * core.ts + z * core.tc
    s = core.camera / (core.camera - z1)
    return x * s, -y1 * s, z1


def on_ring(core: Core, pose: tuple[float, float, float, float], th: float, r: float, z: float) -> tuple[float, float, float]:
    """canvas2d.ts ``onRing``: turned a about X, then b about Y."""
    ca, sa, cb, sb = pose
    x0, y0 = math.cos(th) * r, math.sin(th) * r
    y1 = y0 * ca - z * sa
    z1 = y0 * sa + z * ca
    return project(core, x0 * cb + z1 * sb, y1, z1 * cb - x0 * sb)


def poses(core: Core) -> list[tuple[float, float, float, float]]:
    # One moment of engine.ts ringPose: the contract's a and b, leant and turned
    # by LEAN and GYRO the way the wobble and the gyro move them on the stage.
    out = []
    for ri, R in enumerate(core.rings):
        a = R["a"] + LEAN * math.sin(math.pi / 2.0 + ri)
        b = R["b"] + GYRO * R["dir"]
        out.append((math.cos(a), math.sin(a), math.cos(b), math.sin(b)))
    return out


def faces(core: Core) -> list[tuple[float, list[Pt], list[Pt]]]:
    """Every segment's (depth, near face, far face), far to near: each face is
    the outer arc then the inner arc back, as canvas2d.ts builds them."""
    seg = core.segment
    out = []
    for R, pose in zip(core.rings, poses(core)):
        # Each ring's own weight, depth and gap (engine.ts segmentOf, T-0748).
        h = seg["depth"] * R["d"] / 2.0
        span = 2.0 * math.pi / R["seg"]
        half = (span - R["gap"]) / 2.0
        r0, r1 = R["r"] - seg["inner"] * R["w"], R["r"] + seg["outer"] * R["w"]
        for k in range(R["seg"]):
            mid = SPIN * R["dir"] + k * span
            sides = []
            for z in (h, -h):
                outer = [on_ring(core, pose, mid - half + j / (ARC - 1) * half * 2, r1, z) for j in range(ARC)]
                inner = [on_ring(core, pose, mid + half - j / (ARC - 1) * half * 2, r0, z) for j in range(ARC)]
                pts = outer + inner
                sides.append((sum(p[2] for p in pts) / len(pts), [(p[0], p[1]) for p in pts]))
            near, far = sorted(sides, key=lambda s: -s[0])
            out.append(((near[0] + far[0]) / 2.0, near[1], far[1]))
    out.sort(key=lambda f: f[0])
    return out


def along(pts: list[Pt], base: int, idx: float) -> Pt:
    o = min(ARC - 2, int(idx))
    t = idx - o
    a, b = pts[base + o], pts[base + o + 1]
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


def extent(core: Core) -> float:
    """How far the rings reach from the centre, in S: what FIT scales."""
    return max(math.hypot(*p) for _, near, far in faces(core) for p in near + far)


def scene(core: Core, tier: str, ink: str, mono: bool, clip: float) -> list:
    """The still, in S units about the centre, back to front.

    ``mono`` is the alpha-only form the themed-icon and notification layers
    need: they keep alpha and discard colour, so the dark faces (which only
    occlude) are left out and depth is carried by alpha alone. ``clip`` drops
    shell points beyond that radius, so none hangs off the plate.
    """
    white = "#FFFFFF" if mono else core.white
    spark = "#FFFFFF" if mono else core.spark
    halo = "#FFFFFF" if mono else core.halo
    shell_front: list = []
    out: list = []
    if tier == "full":
        n = SHELL_POINTS
        ga = math.pi * (3.0 - math.sqrt(5.0))
        back: list = []
        for i in range(n):
            y = 1.0 - (i + 0.5) / n * 2.0
            r = math.sqrt(1.0 - y * y)
            x, sy, z = project(core, math.cos(i * ga) * r * core.shell_k, y * core.shell_k, math.sin(i * ga) * r * core.shell_k)
            # Depth-shaded and batched into levels, as canvas2d.ts draws the shell.
            a = 0.25 + 0.75 * min(1.0, max(0.0, (z + 1.1) / 2.2))
            level = min(LEVELS - 1, int(a * LEVELS))
            dot = Dot(x, sy, SHELL_R * (1.0 + 0.9 * a), ink, round((level + 0.5) / LEVELS * 0.9, 4), "shell")
            if math.hypot(x, sy) + dot.r <= clip:
                (shell_front if z >= 0 else back).append(dot)
        out += sorted(back, key=lambda d: d.alpha)
    for depth, near, far in faces(core):
        items: list = []
        if tier == "reduced":
            # Shaded by depth, so where two rings cross the nearer one wins.
            k = min(1.0, max(0.0, (depth + 0.7) / 1.4))
            if mono:
                items.append(Poly(tuple(near), "#FFFFFF", round(0.35 + 0.65 * k, 4), "face"))
            else:
                # A dark rim under the face, so a segment in front reads apart from
                # the one behind without the rim eating into an edge-on sliver.
                items.append(Lines((tuple(near),), True, core.face, 1.0, RIM, "rim"))
                items.append(Poly(tuple(near), mix_hex(core.face, ink, 0.35 + 0.65 * k), 1.0, "face"))
        else:
            if not mono:
                items.append(Poly(tuple(far), core.face, 1.0, "face far"))
                items.append(Poly(tuple(near), core.face, 1.0, "face"))
            items.append(Lines((tuple(far),), True, ink, 0.25, FAR_EDGE, "far"))
            items.append(Lines((tuple(near),), True, ink, 0.85, EDGE, "edge"))
            windings = []
            for w in range(1, core.segment["windings"] + 1):
                u = w / (core.segment["windings"] + 1)
                windings.append((along(near, 0, u * (ARC - 1)), along(near, ARC, (1 - u) * (ARC - 1))))
            items.append(Lines(tuple(windings), False, ink, 0.3, WINDING, "wind"))
            led = near[ARC // 2]
            items.append(Dot(led[0], led[1], LED_R, spark, 0.9, "led"))
        out.append(Seg(tuple(items)))
    out += sorted(shell_front, key=lambda d: d.alpha)
    pose = poses(core)[0]
    R = core.rings[0]
    r0 = 0.15
    g = min(1.0, core.glow)
    if tier == "full":
        # The struts: ring 0 held to the core, between its segments.
        span = 2.0 * math.pi / R["seg"]
        struts = []
        for q in range(4):
            th = SPIN * R["dir"] + span / 2.0 + q * math.pi / 2.0
            p0, p1 = on_ring(core, pose, th, core.strut_in, 0.0), on_ring(core, pose, th, R["r"] - core.segment["inner"] * R["w"], 0.0)
            struts.append(((p0[0], p0[1]), (p1[0], p1[1])))
        out.append(Lines(tuple(struts), False, ink, 0.45, WINDING, "strut"))
        out.append(Glow(0.0, 0.0, r0 * 4.0, (
            (0.0, white, g), (0.22, halo, 0.55 * g), (0.5, ink, 0.2 * min(1.5, core.glow)), (1.0, ink, 0.0),
        )))
    else:
        out.append(Glow(0.0, 0.0, r0 * 2.2, ((0.0, white, g), (0.5, ink, 0.35), (1.0, ink, 0.0))))
    out.append(Glow(0.0, 0.0, r0, ((0.0, white, 1.0), (0.7, spark, 0.8 * g), (1.0, ink, 0.0))))
    if tier == "full":
        rw = r0 * 1.35
        wires = []
        for i in range(3):
            turn = i * math.pi / 3.0
            ry = rw * abs(math.cos(SPIN * 0.7 + turn))
            ct, st = math.cos(turn), math.sin(turn)
            wires.append(tuple(
                (rw * math.cos(t) * ct - ry * math.sin(t) * st, rw * math.cos(t) * st + ry * math.sin(t) * ct)
                for t in (j / 32 * 2.0 * math.pi for j in range(32))
            ))
        out.append(Lines(tuple(wires), True, halo, 0.35, WIRE, "wire"))
    return out


def scale(core: Core, tier: str, r_out: float) -> float:
    """S for a still that fills ``r_out``: the shell's edge in the full tier, the
    rings' in the reduced one."""
    if tier == "full":
        return r_out / (core.shell_k * core.camera / math.sqrt(core.camera ** 2 - 1.0) + SHELL_R * 1.9)
    return FIT_REDUCED * r_out / extent(core)


def ground(core: Core, cx: float, cy: float, r: float) -> Glow:
    """The stage's ground (``--jv-stage-ground``): the core's glow fading to the bg."""
    return Glow(cx, cy, r, ((0.0, core.ground, 1.0), (0.5, core.haze, 1.0), (1.0, core.bg, 1.0)))


# --- the raster -----------------------------------------------------------------------------------


def rounded_rect(size: float, radius: float) -> tuple[Pt, ...]:
    pts = []
    for cx, cy, a0 in ((size - radius, radius, -90), (size - radius, size - radius, 0), (radius, size - radius, 90), (radius, radius, 180)):
        for j in range(9):
            a = math.radians(a0 + j * 90 / 8)
            pts.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
    return tuple(pts)


def render(prims: list, size: int) -> bytes:
    """PNG rows (filter 0) for ``prims`` in pixel units, painted source-over.

    Coverage is analytic from a distance field per primitive, one sample at the
    pixel centre, over each primitive's bounding box only: a stroke or a dot
    thinner than a pixel is drawn a pixel wide at its area's alpha, so a ring
    at 16 px fades rather than breaks up. Painted in premultiplied space and
    unpremultiplied once, so a half-covered edge is not darker than the plate.
    """
    buf = [0.0] * (size * size * 4)

    def paint(x0: float, y0: float, x1: float, y1: float, at) -> None:
        for py in range(max(0, int(y0)), min(size, int(math.ceil(y1)) + 1)):
            for px in range(max(0, int(x0)), min(size, int(math.ceil(x1)) + 1)):
                hit = at(px + 0.5, py + 0.5)
                if hit is None:
                    continue
                r, g, b, a = hit
                if a <= 0.0:
                    continue
                i = (py * size + px) * 4
                k = 1.0 - a
                buf[i] = r * a + buf[i] * k
                buf[i + 1] = g * a + buf[i + 1] * k
                buf[i + 2] = b * a + buf[i + 2] * k
                buf[i + 3] = a + buf[i + 3] * k

    def segments(lines, closed):
        out = []
        for ln in lines:
            pairs = list(zip(ln, ln[1:])) + ([(ln[-1], ln[0])] if closed else [])
            for (ax, ay), (bx, by) in pairs:
                ex, ey = bx - ax, by - ay
                out.append((ax, ay, ex, ey, ex * ex + ey * ey))
        return out

    def dist2(x, y, edges):
        best = math.inf
        for ax, ay, ex, ey, l2 in edges:
            t = 0.0 if l2 == 0.0 else min(1.0, max(0.0, ((x - ax) * ex + (y - ay) * ey) / l2))
            dx, dy = x - ax - t * ex, y - ay - t * ey
            d = dx * dx + dy * dy
            if d < best:
                best = d
        return best

    for p in flat(prims):
        if isinstance(p, Poly):
            col = rgb(p.colour)
            edges = segments([p.pts], True)
            xs, ys = [q[0] for q in p.pts], [q[1] for q in p.pts]

            def poly_at(x, y, edges=edges, col=col, alpha=p.alpha):
                inside = False
                for ax, ay, ex, ey, _ in edges:
                    by = ay + ey
                    if (ay > y) != (by > y) and x < ax + (y - ay) * ex / ey:
                        inside = not inside
                d = math.sqrt(dist2(x, y, edges))
                cov = min(1.0, max(0.0, 0.5 + (d if inside else -d)))
                return (*col, alpha * cov) if cov > 0.0 else None

            paint(min(xs) - 1, min(ys) - 1, max(xs) + 1, max(ys) + 1, poly_at)
        elif isinstance(p, Lines):
            col = rgb(p.colour)
            w, alpha = p.width, p.alpha
            if w < 1.0:
                w, alpha = 1.0, alpha * w
            for ln in p.lines:
                edges = segments([ln], p.closed)
                xs, ys = [q[0] for q in ln], [q[1] for q in ln]

                def line_at(x, y, edges=edges, col=col, alpha=alpha, w=w):
                    cov = min(1.0, max(0.0, 0.5 + w / 2.0 - math.sqrt(dist2(x, y, edges))))
                    return (*col, alpha * cov) if cov > 0.0 else None

                paint(min(xs) - w - 1, min(ys) - w - 1, max(xs) + w + 1, max(ys) + w + 1, line_at)
        elif isinstance(p, Dot):
            col = rgb(p.colour)
            r, alpha = p.r, p.alpha
            if r < 0.5:
                r, alpha = 0.5, alpha * (p.r / 0.5) ** 2

            def dot_at(x, y, p=p, col=col, alpha=alpha, r=r):
                cov = min(1.0, max(0.0, 0.5 + r - math.hypot(x - p.x, y - p.y)))
                return (*col, alpha * cov) if cov > 0.0 else None

            paint(p.x - r - 1, p.y - r - 1, p.x + r + 1, p.y + r + 1, dot_at)
        else:
            stops = [(o, rgb(c), a) for o, c, a in p.stops]

            def glow_at(x, y, p=p, stops=stops):
                t = math.hypot(x - p.x, y - p.y) / p.r
                if t >= 1.0:
                    return None
                for (o0, c0, a0), (o1, c1, a1) in zip(stops, stops[1:]):
                    if t <= o1:
                        k = 0.0 if o1 == o0 else (t - o0) / (o1 - o0)
                        return tuple(c0[i] + (c1[i] - c0[i]) * k for i in range(3)) + (a0 + (a1 - a0) * k,)
                return None

            paint(p.x - p.r, p.y - p.r, p.x + p.r, p.y + p.r, glow_at)

    rows = bytearray()
    for py in range(size):
        rows.append(0)  # PNG filter type: none
        for px in range(size):
            i = (py * size + px) * 4
            a = buf[i + 3]
            if a <= 0.0:
                rows += b"\0\0\0\0"
                continue
            rows += bytes((
                min(255, round(buf[i] / a * 255)),
                min(255, round(buf[i + 1] / a * 255)),
                min(255, round(buf[i + 2] / a * 255)),
                min(255, round(a * 255)),
            ))
    return bytes(rows)


def raster(core: Core, size: int, plate: str | None) -> bytes:
    """A ``size``-square PNG: the still on a rounded plate, a square one, or none."""
    tier = "reduced" if size <= SMALL else "full"
    c = size / 2.0
    r_out = c - core.margin * size
    S = scale(core, tier, r_out)
    prims: list = []
    if plate == "square":
        prims.append(Poly(((0.0, 0.0), (size, 0.0), (size, size), (0.0, size)), core.bg, 1.0, "plate"))
    elif plate == "rounded":
        prims.append(Poly(rounded_rect(size, PLATE_RADIUS * size), core.bg, 1.0, "plate"))
    if plate:
        prims.append(ground(core, c, c, c))
    prims += place(scene(core, tier, core.ink, mono=False, clip=(c - 1.0) / S), c, c, S)
    return png(size, render(prims, size))


def unpng(data: bytes) -> tuple[int, bytes]:
    """A pack's 8-bit RGB or RGBA, non-interlaced PNG as (size, rows) in :func:`png`'s
    shape: filter 0 and RGBA on every row. Refuses any other kind rather than guess."""
    at, size, kind, idat = 8, 0, 0, b""
    while at < len(data):
        n = struct.unpack(">I", data[at:at + 4])[0]
        tag, body = data[at + 4:at + 8], data[at + 8:at + 8 + n]
        if tag == b"IHDR":
            w, h, depth, kind, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if w != h or depth != 8 or kind not in (2, 6) or interlace:
                raise SystemExit(f"a theme icon must be a square 8-bit RGB(A) PNG, not {w}x{h} depth {depth} kind {kind}")
            size = w
        elif tag == b"IDAT":
            idat += body
        at += 12 + n
    bpp = 4 if kind == 6 else 3
    raw, stride = zlib.decompress(idat), size * bpp
    prev, out = bytearray(stride), bytearray()
    for y in range(size):
        f, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b, c = prev[i], prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        prev = line
        out.append(0)
        out += line if bpp == 4 else b"".join(bytes(line[i:i + 3]) + b"\xff" for i in range(0, stride, 3))
    return size, bytes(out)


def theme_icons() -> tuple[str, dict[int, bytes]]:
    """The default pack's name and icon PNGs by size, re-encoded by :func:`png` so
    ``--check`` is byte-stable; no PNGs for a pack that ships none (the drawn core is used then)."""
    default = re.search(r'^DEFAULT = "([a-z0-9-]+)"', (ROOT / "design/themes.py").read_text(encoding="utf-8"), re.M)
    pack_dir = THEMES / (default.group(1) if default else "")
    pack = json.loads((pack_dir / "theme.json").read_text(encoding="utf-8")) if (pack_dir / "theme.json").is_file() else {}
    return pack.get("name", ""), {int(s): png(*unpng((pack_dir / rel).read_bytes()))
                                  for s, rel in pack.get("icons", {}).get("sizes", {}).items()}


def png(size: int, rows: bytes) -> bytes:
    """A PNG from raw RGBA rows. Fixed compression, no ancillary chunks: two
    runs of this script must produce the same bytes or ``--check`` lies."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


def ico(pngs: list[tuple[int, bytes]]) -> bytes:
    """An .ico holding PNG entries (every browser since Vista reads them)."""
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries = b""
    blobs = b""
    for size, data in pngs:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return header + entries + blobs


def icns(entries: list[tuple[bytes, bytes]]) -> bytes:
    """An Apple .icns of PNG entries: ``icns``, the total length, then each
    entry's four-letter type, its length (with its own header) and the PNG."""
    body = b"".join(kind + struct.pack(">I", 8 + len(data)) + data for kind, data in entries)
    return b"icns" + struct.pack(">I", 8 + len(body)) + body


# --- the vector forms ---------------------------------------------------------------------------


def num(value: float) -> str:
    """A float the way every emitter writes one: two decimals (a hundredth of a
    unit is under a twentieth of a pixel at any size an icon is drawn), no
    trailing zeros, never ``-0`` — fixed so a Python version cannot change the bytes."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def path_of(pts, closed: bool) -> str:
    d = "M" + " L".join(f"{num(x)},{num(y)}" for x, y in pts)
    return d + ("Z" if closed else "")


def circle_path(cx: float, cy: float, r: float) -> str:
    """A full circle as two arcs: what a vector drawable can fill."""
    return f"M{num(cx - r)},{num(cy)} a{num(r)},{num(r)} 0 1,0 {num(2 * r)},0 a{num(r)},{num(r)} 0 1,0 {num(-2 * r)},0"


def packed_svg(png64: bytes, name: str) -> str:
    """``favicon.svg`` for a pack that ships its icon as a painting: its 64 px PNG in a
    64-unit box, so the tab shows the same icon the .ico and the PNGs do."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64" role="img">\n'
        f"<!-- {GENERATED} -->\n"
        f"<title>{name}</title>\n"
        f'<image width="64" height="64" href="data:image/png;base64,{base64.b64encode(png64).decode()}"/>\n'
        "</svg>\n"
    )


def svg(core: Core) -> str:
    """``favicon.svg``: the reduced still on a rounded plate in a 64-unit box.

    Reduced, because a browser draws it in a tab strip, a bookmark bar and a
    history row, all 16–32 px; the full still is the PNGs' and the phone's.
    """
    V = 64.0
    c = V / 2.0
    r_out = c - core.margin * V
    S = scale(core, "reduced", r_out)
    defs: list[str] = []

    def glow(p: Glow) -> str:
        gid = f"g{len(defs)}"
        stops = "".join(f'<stop offset="{num(o)}" stop-color="{col}" stop-opacity="{num(a)}"/>' for o, col, a in p.stops)
        defs.append(f'<radialGradient id="{gid}" cx="{num(p.x)}" cy="{num(p.y)}" r="{num(p.r)}" gradientUnits="userSpaceOnUse">{stops}</radialGradient>')
        return f'<circle cx="{num(p.x)}" cy="{num(p.y)}" r="{num(p.r)}" fill="url(#{gid})"/>'

    def element(p) -> str:
        if isinstance(p, Seg):
            return '<g class="seg">' + "".join(element(q) for q in p.items) + "</g>"
        if isinstance(p, Poly):
            return f'<path d="{path_of(p.pts, True)}" fill="{p.colour}"/>'
        if isinstance(p, Lines):
            return f'<path class="{p.cls}" d="{" ".join(path_of(ln, p.closed) for ln in p.lines)}"/>'
        return glow(p)

    prims = place(scene(core, "reduced", core.ink, mono=False, clip=c), c, c, S)
    plate = glow(ground(core, c, c, c))
    body = [element(p) for p in prims]
    rim = f".rim{{fill:none;stroke:{core.face};stroke-width:{num(RIM * S)};stroke-linejoin:round}}"
    return "\n".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {num(V)} {num(V)}" width="{num(V)}" height="{num(V)}" role="img" aria-label="Jarvis">',
        "<title>Jarvis</title>",
        f"<!-- {GENERATED} -->",
        f"<style>{rim}</style>",
        "<defs>",
        *defs,
        "</defs>",
        f'<rect width="{num(V)}" height="{num(V)}" rx="{num(PLATE_RADIUS * V)}" fill="{core.bg}"/>',
        plate,
        *body,
        "</svg>",
    ]) + "\n"


def vector(core: Core, *, viewport: float, r_out: float, tier: str, ink: str, mono: bool, clip: float | None = None) -> str:
    """An Android vector drawable of the still, centred in ``viewport``.

    Consecutive primitives of one style share a path, so a launcher inflates a
    few dozen paths and not three hundred.
    """
    c = viewport / 2.0
    S = scale(core, tier, r_out)
    prims = flat(place(scene(core, tier, ink, mono, clip=(clip if clip is not None else r_out) / S), c, c, S))
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f"<!-- {GENERATED} -->",
        '<vector xmlns:android="http://schemas.android.com/apk/res/android"',
        f'    android:width="{num(viewport)}dp"',
        f'    android:height="{num(viewport)}dp"',
        f'    android:viewportWidth="{num(viewport)}"',
        f'    android:viewportHeight="{num(viewport)}">',
    ]

    def style(p):
        if isinstance(p, Lines):
            return ("lines", p.colour, p.alpha, p.width, p.closed)
        if isinstance(p, (Poly, Dot)):
            return ("fill", p.colour, p.alpha)
        return None

    i = 0
    while i < len(prims):
        p = prims[i]
        if isinstance(p, Glow):
            lines += [
                "    <path",
                f'        android:pathData="{circle_path(p.x, p.y, p.r)}">',
                '        <aapt:attr xmlns:aapt="http://schemas.android.com/aapt" name="android:fillColor">',
                "            <gradient",
                '                android:type="radial"',
                f'                android:centerX="{num(p.x)}"',
                f'                android:centerY="{num(p.y)}"',
                f'                android:gradientRadius="{num(p.r)}">',
                *(f'                <item android:offset="{num(o)}" android:color="{argb(col, a)}" />' for o, col, a in p.stops),
                "            </gradient>",
                "        </aapt:attr>",
                "    </path>",
            ]
            i += 1
            continue
        j = i
        while j + 1 < len(prims) and style(prims[j + 1]) == style(p):
            j += 1
        run = prims[i : j + 1]
        if isinstance(p, Lines):
            d = " ".join(path_of(ln, p.closed) for q in run for ln in q.lines)
            lines += [
                "    <path",
                f'        android:pathData="{d}"',
                f'        android:strokeColor="{p.colour}"',
                f'        android:strokeAlpha="{num(p.alpha)}"',
                f'        android:strokeWidth="{num(p.width)}"',
                '        android:strokeLineJoin="round"',
                '        android:fillColor="#00000000" />',
            ]
        else:
            d = " ".join(path_of(q.pts, True) if isinstance(q, Poly) else circle_path(q.x, q.y, q.r) for q in run)
            lines += [
                "    <path",
                f'        android:pathData="{d}"',
                f'        android:fillColor="{p.colour}"',
                f'        android:fillAlpha="{num(p.alpha)}" />',
            ]
        i = j + 1
    lines.append("</vector>")
    return "\n".join(lines) + "\n"


def android_background(core: Core) -> str:
    """The adaptive icon's ground: the stage's, the core's glow fading to the bg."""
    g = ground(core, 54.0, 54.0, 54.0)
    return "\n".join([
        '<?xml version="1.0" encoding="utf-8"?>',
        f"<!-- {GENERATED} -->",
        '<vector xmlns:android="http://schemas.android.com/apk/res/android"',
        '    android:width="108dp"',
        '    android:height="108dp"',
        '    android:viewportWidth="108"',
        '    android:viewportHeight="108">',
        "    <path",
        '        android:pathData="M0,0h108v108h-108z"',
        f'        android:fillColor="{core.bg}" />',
        "    <path",
        f'        android:pathData="{circle_path(g.x, g.y, g.r)}">',
        '        <aapt:attr xmlns:aapt="http://schemas.android.com/aapt" name="android:fillColor">',
        "            <gradient",
        '                android:type="radial"',
        f'                android:centerX="{num(g.x)}"',
        f'                android:centerY="{num(g.y)}"',
        f'                android:gradientRadius="{num(g.r)}">',
        *(f'                <item android:offset="{num(o)}" android:color="{argb(col, a)}" />' for o, col, a in g.stops),
        "            </gradient>",
        "        </aapt:attr>",
        "    </path>",
        "</vector>",
    ]) + "\n"


# --- outputs ---------------------------------------------------------------------------------


def outputs(contract: dict, frames: dict, theme: dict) -> dict[Path, bytes]:
    core = Core(contract, frames, theme)
    out: dict[Path, bytes] = {}
    cache: dict[tuple[int, str | None], bytes] = {}

    name, packed = theme_icons()

    def still(size: int, plate: str | None = "rounded") -> bytes:
        if size in packed:
            return packed[size]
        if (size, plate) not in cache:
            cache[(size, plate)] = raster(core, size, plate)
        return cache[(size, plate)]

    # The web: the .ico carries the two small sizes, the PNGs the large ones.
    out[WEB_STATIC / "favicon.svg"] = (packed_svg(packed[64], name) if 64 in packed else svg(core)).encode("utf-8")
    out[WEB_STATIC / "favicon.ico"] = ico([(16, still(16)), (32, still(32))])
    for size in (s for s in core.web_sizes if s > 32):
        name = "apple-touch-icon.png" if size == 180 else f"icon-{size}.png"
        # iOS draws its own corners over the touch icon, so that one is opaque.
        out[WEB_STATIC / name] = still(size, "square" if size == 180 else "rounded")
    # Each pack's tab icon, copied into the console's own tree: the image builds from aura-web/
    # alone, so favicon.ts reading ../themes found nothing there and every deployed tab asked for
    # "undefined" (T-0883).
    for pack in sorted(THEMES.glob("*/theme.json")):
        rel = json.loads(pack.read_text(encoding="utf-8")).get("icons", {}).get("favicon")
        if rel:
            out[WEB_THEME_ICONS / f"{pack.parent.name}.ico"] = (pack.parent / rel).read_bytes()

    # Android: the rings fill the safe zone's FIT (66 of 108 units is all a
    # launcher's mask is sure to keep); the shell runs on to the 72-unit visible
    # square's inscribed circle.
    safe_r = 108.0 * core.safe_zone / 2.0
    def adaptive(ink: str, mono: bool = False) -> bytes:
        return vector(core, viewport=108.0, r_out=safe_r, tier="full", ink=ink, mono=mono, clip=36.0).encode("utf-8")

    out[ANDROID_DRAWABLE / "ic_jarvis_foreground.xml"] = adaptive(core.ink)
    out[ANDROID_DRAWABLE / "ic_jarvis_background.xml"] = android_background(core).encode("utf-8")
    out[ANDROID_DRAWABLE / "ic_jarvis_monochrome.xml"] = adaptive("#FFFFFF", mono=True)
    out[ANDROID_DRAWABLE / "ic_jarvis_splash.xml"] = adaptive(core.ink)
    out[ANDROID_DRAWABLE / "ic_jarvis_status.xml"] = vector(
        core, viewport=24.0, r_out=12.0 - core.margin * 24.0, tier="reduced", ink="#FFFFFF", mono=True
    ).encode("utf-8")

    # The desktop shell: its icon set, and the tray sizes it loads at start.
    for size in core.desktop_sizes:
        out[DESKTOP_ICONS / f"{size}x{size}.png"] = still(size)
    out[DESKTOP_ICONS / "icon.png"] = still(max(core.desktop_sizes))
    out[DESKTOP_ICONS / "tray-16.png"] = still(16, None)
    out[DESKTOP_ICONS / "tray-32.png"] = still(32, None)

    # The Tauri bundle (tauri.conf.json bundle.icon): the window, the tray, the
    # installers. The .icns holds the 1x and 2x Mac sizes up to 512.
    for name, size in (("32x32.png", 32), ("64x64.png", 64), ("128x128.png", 128), ("128x128@2x.png", 256), ("icon.png", 512)):
        out[TAURI_ICONS / name] = still(size)
    out[TAURI_ICONS / "icon.ico"] = ico([(s, still(s)) for s in (16, 24, 32, 48, 64, 256) if s in packed or not packed])
    out[TAURI_ICONS / "icon.icns"] = icns([
        (b"ic11", still(32)), (b"ic12", still(64)), (b"ic07", still(128)),
        (b"ic13", still(256)), (b"ic08", still(256)), (b"ic14", still(512)), (b"ic09", still(512)),
    ])
    return out


def sheet(files: dict[Path, bytes], core: Core) -> bytes:
    """A contact sheet: the rasters at 1x and the small ones at 4x, on a mid
    grey so plate edges show, plus the adaptive icon as a launcher masks it."""
    tiles: list[tuple[int, bytes]] = []
    for path in (WEB_STATIC / "icon-512.png", WEB_STATIC / "apple-touch-icon.png", DESKTOP_ICONS / "64x64.png",
                 DESKTOP_ICONS / "32x32.png", DESKTOP_ICONS / "16x16.png", DESKTOP_ICONS / "tray-32.png"):
        tiles.append((int.from_bytes(files[path][16:20], "big"), files[path]))
    # The adaptive icon, rasterised the way a launcher shows it: the 72-unit
    # middle of the 108-unit layers, under a circle mask.
    size = 288
    k = size / 72.0
    S = scale(core, "full", 108.0 * core.safe_zone / 2.0)
    prims = [ground(core, size / 2, size / 2, size / 2)]
    adaptive = []
    for ink in (core.ink,):
        layer = prims + place(scene(core, "full", ink, False, clip=36.0 / S), size / 2, size / 2, S * k)
        adaptive.append(png(size, render([Poly(rounded_rect(size, size / 2), core.bg, 1.0, "plate")] + layer, size)))
    # The themed icon: white alpha on a stand-in for a wallpaper-picked tint. Neither is
    # an icon colour (the launcher chooses both), so neither is a token.
    mono = place(scene(core, "full", "#FFFFFF", True, clip=36.0 / S), size / 2, size / 2, S * k)
    adaptive.append(png(size, render([Poly(rounded_rect(size, size / 2), "#3a4a3a", 1.0, "plate")] + mono, size)))
    S24 = scale(core, "reduced", 12.0 - core.margin * 24.0)
    status = place(scene(core, "reduced", "#FFFFFF", True, clip=99.0), 48, 48, S24 * 4)
    adaptive.append(png(96, render(status, 96)))
    return contact(tiles + [(int.from_bytes(a[16:20], "big"), a) for a in adaptive])


def contact(tiles: list[tuple[int, bytes]]) -> bytes:
    """Lay PNG tiles out in one row on grey, small ones scaled up 4x (nearest)."""
    decoded = []
    for size, data in tiles:
        raw = zlib.decompress(b"".join(_chunks(data, b"IDAT")))
        stride = size * 4
        px = [raw[y * (stride + 1) + 1 : (y + 1) * (stride + 1)] for y in range(size)]
        zoom = 4 if size <= 64 else 1
        decoded.append((size * zoom, zoom, px))
    pad = 16
    W = sum(s for s, _, _ in decoded) + pad * (len(decoded) + 1)
    H = max(s for s, _, _ in decoded) + pad * 2
    canvas = [bytearray(b"\x80\x80\x80\xff" * W) for _ in range(H)]
    x = pad
    for s, zoom, px in decoded:
        for y in range(s):
            row = px[y // zoom]
            for xx in range(s):
                i = (xx // zoom) * 4
                r, g, b, a = row[i], row[i + 1], row[i + 2], row[i + 3]
                o = (x + xx) * 4
                dst = canvas[pad + y]
                for ch, v in enumerate((r, g, b)):
                    dst[o + ch] = round(v * a / 255 + dst[o + ch] * (255 - a) / 255)
        x += s + pad
    rows = b"".join(b"\0" + bytes(r) for r in canvas)
    out = png(W, rows)
    # png() writes a square header; patch the height.
    ihdr = struct.pack(">IIBBBBB", W, H, 8, 6, 0, 0, 0)
    body = b"IHDR" + ihdr
    return out[:8] + struct.pack(">I", 13) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF) + out[33:]


def _chunks(data: bytes, kind: bytes) -> list[bytes]:
    at, out = 8, []
    while at < len(data):
        n = struct.unpack(">I", data[at : at + 4])[0]
        if data[at + 4 : at + 8] == kind:
            out.append(data[at + 8 : at + 8 + n])
        at += 12 + n
    return out


def main(argv: list[str]) -> int:
    contract, frames, theme = load()
    files = outputs(contract, frames, theme)
    if "--sheet" in argv:
        target = Path(argv[argv.index("--sheet") + 1])
        target.write_bytes(sheet(files, Core(contract, frames, theme)))
        print(f"wrote {target}")
        return 0
    if "--check" in argv:
        stale = []
        for path, data in files.items():
            if not path.is_file():
                stale.append(f"missing: {path.relative_to(ROOT)}")
            elif path.read_bytes() != data:
                stale.append(f"stale: {path.relative_to(ROOT)} (run make icons)")
        if stale:
            print("\n".join(stale))
            return 1
        print(f"{len(files)} icons current")
        return 0
    for path, data in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f"wrote {path.relative_to(ROOT)} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
