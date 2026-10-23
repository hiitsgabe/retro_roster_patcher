"""A player's skin tone, measured from his head shot, for games that draw players
in a handful of skin tones.

The measure is the face's individual typology angle (ITA), the dermatology
scale for skin colour: `atan2(L* - 50, b*)` in degrees, from the CIELAB
lightness L* and yellow-blue b* of the skin. Pale skin is high (55 and up),
dark skin low (below -30). Lightness alone mistakes a brightly lit brown face
for a tanned pale one; the yellow in b* tells them apart.

`face_ita` finds the face in a provider head shot and measures it, and `tone`
cuts the angle into however many tones a game has, at the game's own cut
points. Tuned on ESPN's 681 MLB head shots of 2026 (cut-outs, cap at the top,
fixed framing); every one had a face found. About one face in eight comes out
a tone off from what the eye says, mostly between the middle tones: warm
light reads a pale face darker, bright light a brown face lighter.

Pillow is imported inside `face_ita`, so the rest of the library stays
importable without the `images` extra.
"""

from __future__ import annotations

import io
import math
from collections.abc import Sequence

#: Fewer skin pixels than this in the face box (in the picture shrunk to 300
#: pixels) is no face: an empty or tiny picture, a grey silhouette.
MIN_SKIN_PIXELS = 200


def _lab(r: int, g: int, b: int) -> tuple[float, float]:
    """sRGB to CIELAB (L*, b*), D65 white."""

    def linear(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    red, green, blue = linear(r), linear(g), linear(b)
    y = f(0.2126 * red + 0.7152 * green + 0.0722 * blue)
    z = f((0.0193 * red + 0.1192 * green + 0.9505 * blue) / 1.08883)
    return 116 * y - 16, 200 * (y - z)


def _is_skin(r: int, g: int, b: int) -> bool:
    """The usual YCbCr skin box, widened at the dark end: hair, cap, jersey,
    background and teeth fall outside it."""
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    return 133 <= cr <= 180 and 77 <= cb <= 130 and 25 <= y <= 240


def face_ita(png: bytes) -> float | None:
    """The ITA of the face in a head shot (PNG or JPEG), or None when there is
    no face to measure. Raises whatever Pillow raises for a picture it cannot
    read.

    1. The figure: the opaque pixels of a cut-out; in a picture with no
       transparency, the pixels that differ from the border's median colour.
    2. The face box hangs from the top of the figure, which is the cap: rows
       0.20 to 0.50 of the picture's height below it, 0.11 of its width either
       side of the cap's centre. That is brim to chin in ESPN's framing.
    3. Skin pixels in the box (`_is_skin`), as L* and b*.
    4. L* is the mode of their histogram (7-unit window), not the mean, so a
       beard, teeth, glasses or a shadow, each a minor cluster, do not move
       it; b* is the median over the pixels within 3 of that mode.
    """
    from PIL import Image  # the `images` extra

    picture = Image.open(io.BytesIO(png)).convert("RGBA")
    picture.thumbnail((300, 300))
    w, h = picture.size
    raw = picture.tobytes()  # RGBA, 4 bytes a pixel, row by row

    def at(x: int, y: int) -> tuple[int, int, int, int]:
        i = 4 * (y * w + x)
        return raw[i], raw[i + 1], raw[i + 2], raw[i + 3]

    if min(raw[3::4]) < 128:

        def inside(x: int, y: int) -> bool:
            return at(x, y)[3] >= 128

    else:
        border = [at(x, y) for x in range(w) for y in (0, h - 1)]
        border += [at(x, y) for y in range(h) for x in (0, w - 1)]
        bg = [sorted(p[i] for p in border)[len(border) // 2] for i in range(3)]

        def inside(x: int, y: int) -> bool:
            p = at(x, y)
            return sum(abs(p[i] - bg[i]) for i in range(3)) > 60

    top = next((y for y in range(h) if sum(inside(x, y) for x in range(w)) > w * 0.05), None)
    if top is None:
        return None
    cap = [[x for x in range(w) if inside(x, y)] for y in range(top, min(h, top + h // 8))]
    centres = sorted((row[0] + row[-1]) / 2 for row in cap if row)
    cx = centres[len(centres) // 2]

    skin: list[tuple[float, float]] = []
    for y in range(int(top + 0.20 * h), min(h, int(top + 0.50 * h))):
        for x in range(max(0, int(cx - 0.11 * w)), min(w, int(cx + 0.11 * w))):
            r, g, b, _ = at(x, y)
            if inside(x, y) and _is_skin(r, g, b):
                skin.append(_lab(r, g, b))
    if len(skin) < MIN_SKIN_PIXELS:
        return None

    histogram = [0] * 101
    for lightness, _ in skin:
        histogram[min(100, max(0, round(lightness)))] += 1
    window = [sum(histogram[max(0, i - 3) : i + 4]) for i in range(101)]
    mode = max(range(101), key=window.__getitem__)
    yellows = sorted(b for lightness, b in skin if abs(lightness - mode) <= 3)
    if not yellows:
        return None
    return math.degrees(math.atan2(mode - 50, yellows[len(yellows) // 2]))


def tone(ita: float, cuts: Sequence[float]) -> int:
    """The game tone for an ITA: 0 (lightest) to `len(cuts)` (darkest).

    `cuts` are the angles between a game's tones, lightest first (so
    descending): an ITA below k of them is tone k.
    """
    return sum(ita < cut for cut in cuts)
