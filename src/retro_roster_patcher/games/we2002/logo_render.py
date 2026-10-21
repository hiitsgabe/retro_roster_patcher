"""Turn a logo image into the three 4bpp bitmaps a WE2002 flag style holds.

Pillow is the optional `images` extra, imported on first use so the package
imports without it; `render_logo` raises `ImportError` in its absence.
"""

from __future__ import annotations

import io

from .flag_atlas import LogoImages

BIG = (64, 28)
MEDIUM = (24, 16)
MINI = (24, 8)
COLOURS = 15


def render_logo(png: bytes) -> LogoImages:
    """Fit `png` into each slot, sharing one 15-colour palette across all three.

    The logo is cropped to its opaque pixels and scaled to fit each slot without
    distortion, centred on a transparent background. One palette serves all
    three sizes because the game draws them through the same CLUT; it is
    quantised from the two larger renders, and every pixel of each render is
    then snapped to its nearest entry. Transparent pixels become index 0.
    """
    from PIL import Image

    source = Image.open(io.BytesIO(png)).convert("RGBA")
    bbox = source.split()[3].getbbox()
    if bbox is not None:
        source = source.crop(bbox)

    def fit(size: tuple[int, int]) -> Image.Image:
        w, h = size
        scale = min(w / source.width, h / source.height)
        inner = (max(1, round(source.width * scale)), max(1, round(source.height * scale)))
        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        resized = source.resize(inner, Image.Resampling.LANCZOS)
        canvas.paste(resized, ((w - inner[0]) // 2, (h - inner[1]) // 2), resized)
        return canvas

    big, medium, mini = fit(BIG), fit(MEDIUM), fit(MINI)

    opaque: list[tuple[int, int, int]] = []
    for img in (big, medium):
        raw = img.tobytes()
        for i in range(0, len(raw), 4):
            if raw[i + 3] >= 128:
                opaque.append((raw[i], raw[i + 1], raw[i + 2]))
    if not opaque:
        opaque = [(0, 0, 0)]
    strip = Image.new("RGB", (len(opaque), 1))
    strip.putdata(opaque)
    quantised = strip.quantize(colors=COLOURS, method=Image.Quantize.MEDIANCUT)
    flat = quantised.getpalette() or []
    palette: list[tuple[int, int, int]] = [
        (flat[i], flat[i + 1], flat[i + 2]) for i in range(0, min(len(flat), COLOURS * 3), 3)
    ]
    palette += [(0, 0, 0)] * (COLOURS - len(palette))

    # A 256-entry palette image for the nearest-colour pass: the fifteen colours
    # repeated, so whatever entry the quantiser picks maps back with `% 15`.
    lookup = Image.new("P", (1, 1))
    lookup.putpalette([c for i in range(256) for c in palette[i % COLOURS]])

    def indices(img: Image.Image) -> list[list[int]]:
        snapped = img.convert("RGB").quantize(palette=lookup, dither=Image.Dither.NONE)
        idx = snapped.tobytes()
        alpha = img.getchannel("A").tobytes()
        w, h = img.size
        return [
            [0 if alpha[y * w + x] < 128 else (idx[y * w + x] % COLOURS) + 1 for x in range(w)]
            for y in range(h)
        ]

    return LogoImages(palette=palette, big=indices(big), medium=indices(medium), mini=indices(mini))
