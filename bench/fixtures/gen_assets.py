"""Generate the JCB image and PDF fixtures with no third-party imaging library.

The dev ``.venv`` has numpy and pypdf but no Pillow/matplotlib/reportlab, so the images
are written with a hand-rolled PNG encoder (zlib + CRC, colour-type 2) over numpy pixel
arrays, and the table document with a hand-written text-layer PDF (Courier so the columns
align and any extractor reads the cells). Every fixture has a fixed, checkable answer:

- ``squares.png``   — four blue squares on white          → "how many blue squares?" = 4
- ``dashboard.png`` — 3×3 status tiles, two red           → "how many tiles are red?" = 2
- ``scene.png``     — sun + sky + ground                  → judged description
- ``color.png``     — a solid orange field               → "dominant colour?" = orange
- ``table.pdf``     — a quarterly sales table            → "revenue for East?" = 3000
- ``scan.djvu``     — a DjVu header and nothing else     → a format the image cannot render

Run: ``python3 bench/fixtures/gen_assets.py`` (idempotent — rewrites the files).

``clip.mp4`` (media-fetch-007, media-handoff-010) is not generated here: it needs an
encoder, and the point of the fixture is that it is a real video with a real audio track
(the media brief's "plays with audio"). It was made once with::

    ffmpeg -f lavfi -i "testsrc=size=64x64:rate=10:duration=2" \
           -f lavfi -i "sine=frequency=440:duration=2" \
           -c:v libx264 -preset ultrafast -crf 35 -pix_fmt yuv420p -c:a aac -b:a 24k \
           -movflags +faststart -shortest bench/fixtures/assets/clip.mp4
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np

_ASSETS = Path(__file__).resolve().parent / "assets"
_DOCS = Path(__file__).resolve().parent / "documents"


def _write_djvu(path: Path) -> None:
    """A DjVu container holding one 64×64 page's INFO chunk and no image data: the
    IFF85 magic every DjVu reader checks, so the file *is* DjVu to `file` and to
    any tool that opens it, and nothing in the image (`toolchain.MANIFEST`) can
    render it. That is the fixture media-gap-011 needs — a format, not a picture."""
    info = b"INFO" + struct.pack(">I", 10) + struct.pack(">HHBBBB", 64, 64, 0, 24, 100, 0)
    body = b"DJVU" + info
    path.write_bytes(b"AT&TFORM" + struct.pack(">I", len(body)) + body)


def _write_png(path: Path, rgb: np.ndarray) -> None:
    """Encode an ``H×W×3`` uint8 array as a PNG (colour type 2, filter 0 per row)."""
    height, width, _ = rgb.shape
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type "none" for this scanline
        raw.extend(rgb[y].tobytes())

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def _canvas(height: int, width: int, colour: tuple[int, int, int] = (255, 255, 255)) -> np.ndarray:
    return np.full((height, width, 3), colour, dtype=np.uint8)


def _rect(img: np.ndarray, x: int, y: int, w: int, h: int, colour: tuple[int, int, int]) -> None:
    img[y:y + h, x:x + w] = colour


def _disc(img: np.ndarray, cx: int, cy: int, r: int, colour: tuple[int, int, int]) -> None:
    ys, xs = np.ogrid[:img.shape[0], :img.shape[1]]
    img[(xs - cx) ** 2 + (ys - cy) ** 2 <= r * r] = colour


def gen_squares() -> None:
    """Four blue squares on white — a deterministic counting target."""
    img = _canvas(240, 320)
    blue = (30, 80, 220)
    for i, (x, y) in enumerate([(30, 40), (170, 40), (30, 150), (170, 150)]):
        _rect(img, x, y, 90, 60, blue)
    _write_png(_ASSETS / "squares.png", img)


def gen_dashboard() -> None:
    """A 3×3 grid of status tiles, two red (down) and seven green (up)."""
    img = _canvas(300, 300, (24, 26, 32))
    green, red = (46, 160, 67), (200, 48, 48)
    down = {(0, 1), (2, 0)}  # (row, col) of the two red tiles → count = 2
    for row in range(3):
        for col in range(3):
            colour = red if (row, col) in down else green
            _rect(img, 20 + col * 95, 20 + row * 95, 80, 80, colour)
    _write_png(_ASSETS / "dashboard.png", img)


def gen_scene() -> None:
    """A simple outdoor scene: sun (yellow disc), sky (blue), ground (green)."""
    img = _canvas(240, 320, (120, 180, 235))   # sky
    _rect(img, 0, 175, 320, 65, (70, 150, 60))  # ground
    _disc(img, 250, 60, 34, (250, 210, 40))     # sun
    _write_png(_ASSETS / "scene.png", img)


def gen_color() -> None:
    """A field that is unambiguously orange, with a small grey accent."""
    img = _canvas(200, 200, (240, 140, 20))
    _rect(img, 80, 80, 40, 40, (90, 90, 90))
    _write_png(_ASSETS / "color.png", img)


def _pdf_text(lines: list[str]) -> bytes:
    """Build a one-page PDF whose content stream prints ``lines`` in 12pt Courier.

    Offsets for the xref table are computed as the file is assembled, so the result is a
    valid, extractor-readable PDF without any PDF library.
    """
    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    stream_lines = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
    for i, line in enumerate(lines):
        stream_lines.append(f"({esc(line)}) Tj")
        if i != len(lines) - 1:
            stream_lines.append("T*")
    stream_lines.append("ET")
    stream = "\n".join(stream_lines).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n").encode()
    return bytes(out)


def gen_table_pdf() -> None:
    lines = [
        "Quarterly Sales Report - FY2026 Q3",
        "",
        "Region     Units      Revenue",
        "-------    -------    ---------",
        "North      120        2400",
        "South      90         1800",
        "East       150        3000",
        "West       60         1200",
        "",
        "Total      420        8400",
    ]
    (_DOCS / "table.pdf").write_bytes(_pdf_text(lines))


def gen_manual() -> None:
    """A plain-text 'document' with one specific figure for a web-research extract task."""
    (_DOCS / "manual.txt").write_text(
        "Aqua-Flow 3000 pump — operator manual\n\n"
        "Priming: fill the housing before first start.\n"
        "The pump's maximum flow rate is 4200 litres per hour at 2.5 bar.\n"
        "Service the impeller every 2000 hours.\n",
        encoding="utf-8",
    )


def main() -> None:
    _ASSETS.mkdir(parents=True, exist_ok=True)
    _DOCS.mkdir(parents=True, exist_ok=True)
    gen_squares()
    gen_dashboard()
    gen_scene()
    gen_color()
    gen_table_pdf()
    gen_manual()
    _write_djvu(_DOCS / "scan.djvu")
    made = sorted([*_ASSETS.glob("*"), *_DOCS.glob("*")])
    for path in made:
        print(f"wrote {path.relative_to(path.parents[2])} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
