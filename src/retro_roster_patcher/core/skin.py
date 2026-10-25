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

Pillow is imported inside `_frame`, so the rest of the library stays
importable without the `images` extra. `core.hair` reads the same frame.
"""

from __future__ import annotations

import io
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

#: Fewer skin pixels than this in the face box (in the picture shrunk to 300
#: pixels) is no face: an empty or tiny picture, a grey silhouette.
MIN_SKIN_PIXELS = 200


def _lab(r: int, g: int, b: int) -> tuple[float, float, float]:
    """sRGB to CIELAB (L*, a*, b*), D65 white."""

    def linear(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    red, green, blue = linear(r), linear(g), linear(b)
    x = f((0.4124 * red + 0.3576 * green + 0.1805 * blue) / 0.95047)
    y = f(0.2126 * red + 0.7152 * green + 0.0722 * blue)
    z = f((0.0193 * red + 0.1192 * green + 0.9505 * blue) / 1.08883)
    return 116 * y - 16, 500 * (x - y), 200 * (y - z)


def _is_skin(r: int, g: int, b: int) -> bool:
    """The usual YCbCr skin box, widened at the dark end: hair, cap, jersey,
    background and teeth fall outside it."""
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    return 133 <= cr <= 180 and 77 <= cb <= 130 and 25 <= y <= 240


@dataclass(frozen=True)
class _Frame:
    """A head shot shrunk to 300 pixels: the figure and where its head is.

    `top` is the first row with figure in it (the cap, or the hair), `cx` the
    column the head is centred on. `inside` tells the figure from the
    background: the opaque pixels of a cut-out, or the pixels that differ
    from the border's median colour in a picture with no transparency.
    """

    w: int
    h: int
    raw: bytes  # RGBA, 4 bytes a pixel, row by row
    top: int
    cx: float
    inside: Callable[[int, int], bool]

    def at(self, x: int, y: int) -> tuple[int, int, int, int]:
        i = 4 * (y * self.w + x)
        return self.raw[i], self.raw[i + 1], self.raw[i + 2], self.raw[i + 3]


def _frame(png: bytes) -> _Frame | None:
    """The frame of a head shot (PNG or JPEG), or None when no figure is in it.
    Raises whatever Pillow raises for a picture it cannot read."""
    from PIL import Image  # the `images` extra

    picture = Image.open(io.BytesIO(png)).convert("RGBA")
    picture.thumbnail((300, 300))
    w, h = picture.size
    raw = picture.tobytes()

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
    return _Frame(w, h, raw, top, centres[len(centres) // 2], inside)


def _typical(pixels: list[tuple[float, float, float]], window: int) -> tuple[float, float, float]:
    """The mode of L* (a 7-unit window of the histogram), with the median a*
    and b* of the pixels within `window` of it: a beard, teeth, glasses or a
    shadow, each a minor cluster, do not move it."""
    histogram = [0] * 101
    for lightness, _, _ in pixels:
        histogram[min(100, max(0, round(lightness)))] += 1
    smoothed = [sum(histogram[max(0, i - 3) : i + 4]) for i in range(101)]
    mode = max(range(101), key=smoothed.__getitem__)
    near = [(a, b) for lightness, a, b in pixels if abs(lightness - mode) <= window] or [
        (a, b) for _, a, b in pixels
    ]
    reds = sorted(a for a, _ in near)
    yellows = sorted(b for _, b in near)
    return float(mode), reds[len(reds) // 2], yellows[len(yellows) // 2]


def face_lab(frame: _Frame) -> tuple[float, float, float] | None:
    """The face's skin colour as (L*, a*, b*), or None when there is no face.

    The face box hangs from the top of the figure: rows 0.20 to 0.50 of the
    picture's height below it, 0.11 of its width either side of the head's
    centre. That is brim to chin in ESPN's framing. The colour is `_typical`
    of the skin pixels (`_is_skin`) in it; fewer than `MIN_SKIN_PIXELS` of
    them is no face.
    """
    w, h, top, cx = frame.w, frame.h, frame.top, frame.cx
    skin: list[tuple[float, float, float]] = []
    for y in range(int(top + 0.20 * h), min(h, int(top + 0.50 * h))):
        for x in range(max(0, int(cx - 0.11 * w)), min(w, int(cx + 0.11 * w))):
            r, g, b, _ = frame.at(x, y)
            if frame.inside(x, y) and _is_skin(r, g, b):
                skin.append(_lab(r, g, b))
    if len(skin) < MIN_SKIN_PIXELS:
        return None
    return _typical(skin, 3)


def face_ita(png: bytes) -> float | None:
    """The ITA of the face in a head shot (PNG or JPEG), or None when there is
    no face to measure (`_frame`, `face_lab`). Raises whatever Pillow raises
    for a picture it cannot read."""
    frame = _frame(png)
    if frame is None:
        return None
    colour = face_lab(frame)
    if colour is None:
        return None
    lightness, _, yellow = colour
    return math.degrees(math.atan2(lightness - 50, yellow))


def tone(ita: float, cuts: Sequence[float]) -> int:
    """The game tone for an ITA: 0 (lightest) to `len(cuts)` (darkest).

    `cuts` are the angles between a game's tones, lightest first (so
    descending): an ITA below k of them is tone k.
    """
    return sum(ita < cut for cut in cuts)
