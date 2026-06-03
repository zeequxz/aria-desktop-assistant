"""scripts/make_icon.py - Generate aria2/assets/aria2.ico programmatically.

Creates a multi-resolution .ico (16 / 32 / 48 / 64 / 128 / 256 px) with a
clean, flat design: dark background, accent-coloured rounded square, and a
stylised ✦ mark. No external assets needed — just Pillow.

Run standalone:  python scripts/make_icon.py
Also called by the app on first launch if the .ico is missing.
"""

from __future__ import annotations

import math
from pathlib import Path

OUTPUT = Path(__file__).resolve().parents[1] / "aria2" / "assets" / "aria2.ico"


def _draw_size(size: int) -> "Image":
    from PIL import Image, ImageDraw, ImageFont

    bg   = (11,  13,  18,  255)    # #0b0d12
    sq   = (22,  32,  58,  255)    # accent-tinted card  #16203a -> #16203a
    acc  = (108, 143, 255, 255)    # #6c8fff accent
    white = (238, 241, 246, 255)   # #eef1f6

    img = Image.new("RGBA", (size, size), bg)
    d   = ImageDraw.Draw(img)

    # Rounded-square background card
    pad   = size * 0.10
    r     = size * 0.22
    x0, y0 = pad, pad
    x1, y1 = size - pad, size - pad
    d.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=sq)

    # ✦ drawn as four diamond lobes + a tiny centre dot — works at all sizes
    cx, cy = size / 2, size / 2
    arm    = size * 0.26        # long lobe half-length
    thin   = size * 0.060       # short lobe half-width
    dot_r  = size * 0.032

    def lobe(angle_deg: float):
        a  = math.radians(angle_deg)
        pa = math.radians(angle_deg + 90)
        pts = [
            (cx + arm * math.cos(a),       cy + arm * math.sin(a)),
            (cx + thin * math.cos(pa),     cy + thin * math.sin(pa)),
            (cx - arm * math.cos(a),       cy - arm * math.sin(a)),
            (cx - thin * math.cos(pa),     cy - thin * math.sin(pa)),
        ]
        d.polygon(pts, fill=acc)

    lobe(0)    # right / left
    lobe(90)   # down  / up
    d.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=white)

    return img


def make(output: Path = OUTPUT) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    sizes  = [16, 32, 48, 64, 128, 256]
    images = [_draw_size(s) for s in sizes]
    # PIL ICO save: first image is the primary, rest appended;
    # pass explicit sizes= so every resolution is embedded.
    images[0].save(
        output, format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=images[1:],
    )
    # Verify all sizes were embedded (PIL sometimes only stores the first).
    from PIL import Image as _I
    _ico = _I.open(output)
    embedded = _ico.info.get("sizes", set())
    if len(embedded) < len(sizes):
        # Fallback: write each size as a separate PNG then reassemble.
        import tempfile, struct, io
        bufs = []
        for img in images:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            bufs.append(buf.getvalue())
        with open(output, "wb") as fh:
            n = len(bufs)
            fh.write(struct.pack("<HHH", 0, 1, n))   # ICONDIR
            offset = 6 + n * 16
            for i, (s, buf) in enumerate(zip(sizes, bufs)):
                sz = len(buf)
                fh.write(struct.pack("<BBBBHHII",
                                     s if s < 256 else 0,
                                     s if s < 256 else 0,
                                     0, 0, 1, 32, sz, offset))
                offset += sz
            for buf in bufs:
                fh.write(buf)
    return output


if __name__ == "__main__":
    p = make()
    print(f"Icon written: {p}")
