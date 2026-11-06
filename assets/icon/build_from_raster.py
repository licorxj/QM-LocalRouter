#!/usr/bin/env python3
"""Build the full LocalRouter icon set from an AI-generated raster master.

Pipeline (run from assets/icon):
  python build_from_raster.py source/<master>.jpg

Steps:
  1. Detect the badge (dark / saturated pixels) and crop surrounding margins.
  2. Center-crop to a square and measure the badge's corner radius.
  3. Render a 1024px RGBA master whose rounded corners are transparent
     (supersampled mask, tiny inset + feather to avoid white fringe).
  4. Emit renditions: 256/128/64/48 straight LANCZOS; 32/24/16 with a mild
     unsharp pass so tiny ICO frames stay crisp.
  5. Encode multi-frame Windows ICOs via make_ico.encode_frame.

Outputs land in out/ ; distribution (build/app.ico, frontend/public/*) is a
plain copy step documented in README.md.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from make_ico import encode_frame

MASTER = 1024
ICO_SIZES_FULL = (256, 128, 64, 48)
ICO_SIZES_SHARP = (32, 24, 16)  # small frames get unsharp masking
BADGE_DARK = 190       # max channel min() for a pixel to count as badge
BADGE_SAT = 40         # min channel spread for saturated (glow) pixels
MASK_INSET = 1.0       # px at master scale, pulls mask inside the badge edge
MASK_FEATHER = 3.0     # gaussian radius on the 4x supersampled mask


def badge_mask(arr: np.ndarray) -> np.ndarray:
    mn = arr.min(axis=2)
    mx = arr.max(axis=2)
    return (mn < BADGE_DARK) | ((mx.astype(np.int32) - mn) > BADGE_SAT)


def first_run(mask_row: np.ndarray, run: int = 24) -> int:
    """First index where `run` consecutive badge pixels start; -1 if none."""
    hits = np.flatnonzero(mask_row)
    for i in hits:
        if mask_row[i:i + run].all():
            return int(i)
    return -1


def measure_radius(badge: np.ndarray) -> int:
    """Corner radius ≈ first badge pixel offset on the top row / left column."""
    dark = badge_mask(badge)
    h, w = dark.shape
    candidates = [first_run(dark[0]), first_run(dark[:, 0]),
                  first_run(dark[h - 1][::-1]), first_run(dark[::-1][:, w - 1])]
    vals = [c for c in candidates if c >= 0]
    side = min(h, w)
    r = int(np.mean(vals)) if vals else side // 10
    return max(int(side * 0.07), min(r, int(side * 0.22)))


def square_crop(badge: Image.Image) -> Image.Image:
    w, h = badge.size
    s = min(w, h)
    return badge.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2))


def rounded_alpha(size: int, radius: int) -> Image.Image:
    ss = size * 4
    m = Image.new("L", (ss, ss), 0)
    d = ImageDraw.Draw(m)
    inset = MASK_INSET * 4
    d.rounded_rectangle([inset, inset, ss - inset, ss - inset],
                        radius=radius * 4, fill=255)
    m = m.filter(ImageFilter.GaussianBlur(MASK_FEATHER))
    return m.resize((size, size), Image.LANCZOS)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    src = Path(sys.argv[1])
    out = Path("out")
    out.mkdir(exist_ok=True)

    im = Image.open(src).convert("RGB")
    arr = np.asarray(im)
    ys, xs = np.where(badge_mask(arr))
    box = (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)
    badge = square_crop(im.crop(box))
    radius = measure_radius(np.asarray(badge))
    print(f"source {im.size} bbox {box} badge {badge.size} radius {radius}px "
          f"({radius / badge.size[0]:.1%})")

    master = badge.resize((MASTER, MASTER), Image.LANCZOS).convert("RGBA")
    master.putalpha(rounded_alpha(MASTER, radius))
    master.save(out / "icon-master-1024.png")

    renditions = {}
    for size in ICO_SIZES_FULL + ICO_SIZES_SHARP:
        img = master.resize((size, size), Image.LANCZOS)
        if size in ICO_SIZES_SHARP:
            img = img.filter(ImageFilter.UnsharpMask(1.2, 80, 2))
        img.save(out / f"icon-{size}.png")
        renditions[size] = img.convert("RGBA")
        print(f"OK icon-{size}.png")

    def build_ico(dest: Path, sizes) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        frames = [(s, encode_frame(renditions[s], s)) for s in sizes]
        header = struct_hdr(len(frames))
        directory = b""
        offset = 6 + 16 * len(frames)
        body = b""
        for s, payload in frames:
            dim = 0 if s >= 256 else s
            directory += pack_entry(dim, len(payload), offset)
            body += payload
            offset += len(payload)
        dest.write_bytes(header + directory + body)
        print(f"OK {dest} sizes={sorted(sizes)} bytes={dest.stat().st_size}")

    build_ico(out / "app.ico", ICO_SIZES_FULL + ICO_SIZES_SHARP)
    build_ico(out / "favicon.ico", (16, 32, 48))
    return 0


def struct_hdr(count: int) -> bytes:
    import struct
    return struct.pack("<HHH", 0, 1, count)


def pack_entry(dim: int, length: int, offset: int) -> bytes:
    import struct
    return struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, length, offset)


if __name__ == "__main__":
    raise SystemExit(main())
