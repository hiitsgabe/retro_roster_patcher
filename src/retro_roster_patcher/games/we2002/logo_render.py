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
#: A pixel at least this opaque after the downsize is ink. 128 dropped most of
#: a thin-stroke crest (Liverpool, Forest, Spurs) at 24x16 (issue #4).
INK_ALPHA = 40
#: Ink darker than this (mean luma) disappears on the navy grid and VS banner,
#: so the menu rendering outlines it in white.
DARK_LUMA = 50


WHITE = (255, 255, 255)


def render_logo(png: bytes) -> LogoImages:
    """The menu rendering of `png` (see `render_logos`)."""
    return render_logos(png)[0]


def render_logos(png: bytes) -> tuple[LogoImages, LogoImages]:
    """Fit `png` into each slot, sharing one 15-colour palette across all three.

    Two renderings come back on the same palette: the menu one, with index 0
    (transparent) around the logo, and the stadium one, with the logo on a
    white ground, for the flags the match draws (crowd, sideline, scoreboard),
    which look like torn cloth when their background is see-through. White is
    always the palette's last colour so both renderings share one CLUT.

    The logo is cropped to its opaque pixels and scaled to fit each slot without
    distortion, centred on a transparent background. One palette serves all
    three sizes because the game draws them through the same CLUT; it is
    quantised from the two larger renders, and every pixel of each render is
    then snapped to its nearest entry. Transparent pixels become index 0. A dark
    logo (Spurs' navy) gets a 1-pixel white outline in the menu rendering, which
    the game draws on navy.
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
        # A plain paste: the canvas is empty, and pasting through the logo's own
        # alpha as the mask squared it (a half-covered stroke pixel became a quarter).
        canvas.paste(resized, ((w - inner[0]) // 2, (h - inner[1]) // 2))
        return canvas

    big, medium, mini = fit(BIG), fit(MEDIUM), fit(MINI)

    opaque: list[tuple[int, int, int]] = []
    for img in (big, medium):
        raw = img.tobytes()
        for i in range(0, len(raw), 4):
            if raw[i + 3] >= INK_ALPHA:
                opaque.append((raw[i], raw[i + 1], raw[i + 2]))
    dark = bool(opaque) and sum(
        0.299 * r + 0.587 * g + 0.114 * b for r, g, b in opaque
    ) < DARK_LUMA * len(opaque)
    if not opaque:
        opaque = [(0, 0, 0)]
    strip = Image.new("RGB", (len(opaque), 1))
    strip.putdata(opaque)
    quantised = strip.quantize(colors=COLOURS - 1, method=Image.Quantize.MEDIANCUT)
    flat = quantised.getpalette() or []
    palette: list[tuple[int, int, int]] = [
        (flat[i], flat[i + 1], flat[i + 2]) for i in range(0, min(len(flat), (COLOURS - 1) * 3), 3)
    ]
    palette += [(0, 0, 0)] * (COLOURS - 1 - len(palette))
    palette.append(WHITE)  # the stadium flags' ground, index COLOURS

    # A 256-entry palette image for the nearest-colour pass: the fifteen colours
    # repeated, so whatever entry the quantiser picks maps back with `% 15`.
    lookup = Image.new("P", (1, 1))
    lookup.putpalette([c for i in range(256) for c in palette[i % COLOURS]])

    def indices(img: Image.Image, ground: int) -> list[list[int]]:
        snapped = img.convert("RGB").quantize(palette=lookup, dither=Image.Dither.NONE)
        idx = snapped.tobytes()
        alpha = img.getchannel("A").tobytes()
        w, h = img.size
        return [
            [
                ground if alpha[y * w + x] < INK_ALPHA else (idx[y * w + x] % COLOURS) + 1
                for x in range(w)
            ]
            for y in range(h)
        ]

    def outlined(rows: list[list[int]]) -> list[list[int]]:
        """`rows` with every see-through pixel next to the ink turned white."""
        if not dark:
            return rows
        h, w = len(rows), len(rows[0])
        return [
            [
                COLOURS
                if v == 0
                and any(
                    0 <= x + dx < w and 0 <= y + dy < h and rows[y + dy][x + dx]
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                )
                else v
                for x, v in enumerate(row)
            ]
            for y, row in enumerate(rows)
        ]

    menu = LogoImages(
        palette=palette,
        big=outlined(indices(big, 0)),
        medium=outlined(indices(medium, 0)),
        mini=outlined(indices(mini, 0)),
    )
    stadium = LogoImages(
        palette=palette,
        big=indices(big, COLOURS),
        medium=indices(medium, COLOURS),
        mini=indices(mini, COLOURS),
    )
    return menu, stadium
