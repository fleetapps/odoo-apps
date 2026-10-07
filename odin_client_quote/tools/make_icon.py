#!/usr/bin/env python3
"""Draw the app icon: flat white comparison columns on the brand navy.

Odoo app icons are flat, legible at 40px, and carry one idea. The idea here is
the comparison itself -- plans side by side, the recommended one picked out --
so the glyph is four columns of rising height with the third lifted.

Rendered at 4x and box-filtered down, which is how the edges and the rounded
corners get their antialiasing without an imaging library.

    python3 tools/make_icon.py
"""

import os
import struct
import zlib

SIZE = 140
SS = 4                      # supersampling factor
W = SIZE * SS

NAVY = (0, 52, 120)         # #003478, the brand colour the reports use
WHITE = (255, 255, 255)
MUTED = (143, 176, 209)     # the columns that are not the recommendation

RADIUS = 30 * SS            # rounded-square corner, Odoo house style


def rounded_square(x, y, w, r):
    """Is pixel (x, y) inside a w-wide rounded square with corner radius r?"""
    cx = min(max(x, r), w - r)
    cy = min(max(y, r), w - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def main():
    # columns: (left, top, height-fraction, colour)
    pad = 26 * SS
    inner = W - 2 * pad
    gap = inner // 11
    bar_w = (inner - 3 * gap) // 4
    base = W - pad
    heights = (0.42, 0.60, 1.00, 0.78)
    colours = (MUTED, MUTED, WHITE, MUTED)
    bars = []
    for i, (frac, colour) in enumerate(zip(heights, colours)):
        left = pad + i * (bar_w + gap)
        height = int(inner * frac)
        bars.append((left, left + bar_w, base - height, base, colour))

    bar_r = bar_w // 2

    rows = []
    for y in range(W):
        row = []
        for x in range(W):
            px = (0, 0, 0, 0)
            if rounded_square(x, y, W, RADIUS):
                px = NAVY + (255,)
                for x0, x1, y0, y1, colour in bars:
                    if x0 <= x < x1 and y0 <= y < y1:
                        # round only the top of each column
                        cy = max(y, y0 + bar_r)
                        cx = min(max(x, x0 + bar_r), x1 - bar_r)
                        if y >= y0 + bar_r or (x - cx) ** 2 + (y - cy) ** 2 <= bar_r ** 2:
                            px = colour + (255,)
                        break
            row.append(px)
        rows.append(row)

    # box-filter down to the final size
    out = bytearray()
    for y in range(SIZE):
        out.append(0)
        for x in range(SIZE):
            acc = [0, 0, 0, 0]
            for dy in range(SS):
                for dx in range(SS):
                    p = rows[y * SS + dy][x * SS + dx]
                    for c in range(4):
                        acc[c] += p[c]
            out.extend(bytes(v // (SS * SS) for v in acc))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(out), 9))
           + chunk(b"IEND", b""))

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "static", "description", "icon.png")
    with open(path, "wb") as fh:
        fh.write(png)
    print("wrote %s (%d bytes)" % (path, len(png)))


if __name__ == "__main__":
    main()
