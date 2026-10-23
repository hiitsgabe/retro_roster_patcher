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

    Quantised at `scale` times the size, then each `scale` x `scale` block
    takes its most common colour, so an edge is one of the four colours rather
    than a blend of two.
    """
    from PIL import Image

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
    index = quantised.tobytes()  # one palette index per pixel, row by row
    pixels = []
    for y in range(FLAG_HEIGHT):
        row = []
        for x in range(FLAG_WIDTH):
            votes = [0] * 4
            for yy in range(y * scale, y * scale + scale):
                for xx in range(x * scale, x * scale + scale):
                    votes[index[yy * w + xx]] += 1
            row.append(votes.index(max(votes)))
        pixels.append(row)
    return Flag(pixels, colours)
