"""Turn a crest image into a 24x16, four-colour ISS flag.

Pillow is the optional `images` extra, imported on first use so the package
imports without it; `logo_flag` raises `ImportError` in its absence.
"""

from __future__ import annotations

import io

from .models import Flag
from .rom_writer import FLAG_HEIGHT, FLAG_WIDTH

WHITE = (255, 255, 255)


def logo_flag(png: bytes, background: tuple[int, int, int] = WHITE, scale: int = 10) -> Flag:
    """`png` cropped to its opaque pixels, fitted into 24x16 without distortion
    and centred on `background`, in four colours.

    The four colours come from the crest drawn at `scale` times the size. Each
    flag pixel is the area average of its `scale` x `scale` block, sharpened,
    then snapped to the nearest of the four, so an edge is one of them rather
    than a blend. Averaging keeps a stroke thinner than a block (Everton's
    tower and lettering) that a vote for the block's most common colour would
    drop; the sharpening keeps it from fading into its background.
    """
    from PIL import Image, ImageFilter

    source = Image.open(io.BytesIO(png)).convert("RGBA")
    bbox = source.getchannel("A").getbbox()
    if bbox is not None:
        source = source.crop(bbox)
    w, h = FLAG_WIDTH * scale, FLAG_HEIGHT * scale
    fit = min(w / source.width, h / source.height)
    inner = source.resize(
        (max(1, round(source.width * fit)), max(1, round(source.height * fit))),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", (w, h), (*background, 255))
    canvas.alpha_composite(inner, ((w - inner.width) // 2, (h - inner.height) // 2))
    quantised = canvas.convert("RGB").quantize(
        colors=4, method=Image.Quantize.MAXCOVERAGE, kmeans=4, dither=Image.Dither.NONE
    )
    palette = (quantised.getpalette() or [])[:12]
    colours = [(palette[i], palette[i + 1], palette[i + 2]) for i in range(0, len(palette), 3)]
    colours += [colours[-1]] * (4 - len(colours))
    small = (
        canvas.convert("RGB")
        .resize((FLAG_WIDTH, FLAG_HEIGHT), Image.Resampling.BOX)
        .filter(ImageFilter.UnsharpMask(radius=1, percent=300, threshold=0))
        .tobytes()
    )

    def nearest(p: int) -> int:
        rgb = small[p * 3 : p * 3 + 3]
        return min(
            range(4), key=lambda c: sum((a - b) ** 2 for a, b in zip(colours[c], rgb, strict=True))
        )

    pixels = [[nearest(y * FLAG_WIDTH + x) for x in range(FLAG_WIDTH)] for y in range(FLAG_HEIGHT)]
    return Flag(pixels, colours)
