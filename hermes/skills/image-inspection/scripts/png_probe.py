#!/usr/bin/env python3
"""Pure-stdlib PNG colour probe.

Decodes a PNG (8-bit RGB or RGBA), samples the centre of each cell of an
R x C grid, and prints a colour-classified grid plus per-colour counts.
Verifies what a vision model reports about tile/status dashboards.

Usage: python3 png_probe.py <image.png> [rows cols]
Defaults: 3x3 grid; red = R>150 & G<100 & B<100; green = G>150 & R<100.
"""
import sys, zlib, struct


def parse(path):
    with open(path, 'rb') as f:
        data = f.read()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise SystemExit('not a PNG')
    pos, width, height, depth, color_type = 8, None, None, None, None
    idat = b''
    while pos < len(data):
        length = struct.unpack('>I', data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8].decode()
        chunk = data[pos + 8:pos + 8 + length]
        if ctype == 'IHDR':
            width, height, depth, color_type = struct.unpack('>IIBB', chunk[:10])
        elif ctype == 'IDAT':
            idat += chunk
        pos += 12 + length
    if depth != 8 or color_type not in (2, 6):
        raise SystemExit('unsupported PNG: depth=%s color_type=%s (need 8-bit RGB or RGBA)' % (depth, color_type))
    return width, height, color_type, zlib.decompress(idat)


def unfilter(width, height, color_type, raw):
    ch = 3 if color_type == 2 else 4
    rowlen = width * ch
    rows = []
    i = 0
    for y in range(height):
        fb = raw[i]; i += 1
        row = bytearray(raw[i:i + rowlen]); i += rowlen
        prev = rows[-1] if rows else None
        if fb == 1:
            for x in range(ch, rowlen):
                row[x] = (row[x] + row[x - ch]) % 256
        elif fb == 2:
            for x in range(rowlen):
                row[x] = (row[x] + (prev[x] if prev else 0)) % 256
        elif fb == 3:
            for x in range(rowlen):
                left = row[x - ch] if x >= ch else 0
                up = prev[x] if prev else 0
                row[x] = (row[x] + (left + up) // 2) % 256
        elif fb == 4:
            for x in range(rowlen):
                left = row[x - ch] if x >= ch else 0
                up = prev[x] if prev else 0
                ul = (prev[x - ch] if x >= ch else 0) if prev else 0
                p = left + up - ul
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - ul)
                pred = left if (pa <= pb and pa <= pc) else (up if pb <= pc else ul)
                row[x] = (row[x] + pred) % 256
        elif fb != 0:
            raise SystemExit('unknown filter byte %d at row %d' % (fb, y))
        rows.append(row)
    return rows


def main():
    args = sys.argv[1:]
    if not args:
        raise SystemExit('usage: png_probe.py <image.png> [rows cols]')
    path = args[0]
    rows_n = int(args[1]) if len(args) > 1 else 3
    cols_n = int(args[2]) if len(args) > 2 else 3
    width, height, color_type, raw = parse(path)
    ch = 3 if color_type == 2 else 4
    rows = unfilter(width, height, color_type, raw)

    def sample(fx, fy):
        x = min(int(fx * width), width - 1)
        y = min(int(fy * height), height - 1)
        return rows[y][x * ch], rows[y][x * ch + 1], rows[y][x * ch + 2]

    counts = {}
    for r in range(rows_n):
        cells = []
        for c in range(cols_n):
            rr, gg, bb = sample((c + 0.5) / cols_n, (r + 0.5) / rows_n)
            if rr > 150 and gg < 100 and bb < 100:
                cls = 'RED'
            elif gg > 150 and rr < 100:
                cls = 'GREEN'
            else:
                cls = 'other(%d,%d,%d)' % (rr, gg, bb)
            key = cls.split('(')[0]
            counts[key] = counts.get(key, 0) + 1
            cells.append(cls)
        print(' '.join(cells))
    print('size %dx%d' % (width, height))
    for k, v in sorted(counts.items()):
        print('%s: %d' % (k, v))


main()
