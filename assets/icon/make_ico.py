#!/usr/bin/env python3
"""Build a multi-size Windows .ico from pre-rendered square PNGs.

Usage: python make_ico.py <output.ico> <size:png> [<size:png> ...]
Example:
  python make_ico.py build/app.ico 16:icon-16.png 24:icon-24.png 32:icon-32.png \
      48:icon-48.png 64:icon-64.png 128:icon-128.png 256:icon-256.png

Each PNG must already be exactly `size` pixels square (RGBA). Pillow's ICO
plugin cannot merge arbitrary frames, so this renders one single-size ICO per
PNG and then merges the encoded frames into one file with exact offsets.
Frame encoding (BMP/PNG) is whatever Pillow produces per size; only 256px
frames may exceed it in byte order — both are valid ICO images.
"""
import io
import struct
import sys
from pathlib import Path

from PIL import Image


def encode_frame(img: Image.Image, size: int) -> bytes:
    """Encode one exact-size RGBA image as a single-frame ICO, return payload."""
    buf = io.BytesIO()
    img.save(buf, format="ICO", sizes=[(size, size)])
    data = buf.getvalue()
    count = struct.unpack("<H", data[4:6])[0]
    if count != 1:
        raise SystemExit(f"unexpected frame count {count} for size {size}")
    entry = data[6:22]
    _, _, _, _, _, _, bytes_in_res, image_offset = struct.unpack("<BBBBHHII", entry)
    return data[image_offset : image_offset + bytes_in_res]


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    out = Path(sys.argv[1])
    frames = []
    for spec in sys.argv[2:]:
        size_str, _, png = spec.partition(":")
        size = int(size_str)
        img = Image.open(png).convert("RGBA")
        if img.size != (size, size):
            raise SystemExit(f"{png} is {img.size}, expected ({size}, {size})")
        frames.append((size, encode_frame(img, size)))

    out.parent.mkdir(parents=True, exist_ok=True)
    header = struct.pack("<HHH", 0, 1, len(frames))
    directory = b""
    offset = 6 + 16 * len(frames)
    body = b""
    for size, payload in frames:
        dim = 0 if size >= 256 else size
        directory += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                                 len(payload), offset)
        body += payload
        offset += len(payload)
    out.write_bytes(header + directory + body)
    sizes_saved = sorted({s for s, _ in frames})
    print(f"OK {out} sizes={sizes_saved} bytes={out.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
