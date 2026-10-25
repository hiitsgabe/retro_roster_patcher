"""A player's hair colour, read from his head shot, for games that pick a
hair colour from a short list.

The hair sits between the top of the figure and the face box `core.skin`
measures: rows down to 0.20 of the picture's height below the top, 0.13 of
its width either side of the head's centre. Hair is every figure pixel there
whose colour is well away from the face's own skin colour (the YCbCr skin
box is no use for this: brown hair sits inside it). The colour is the mode
of the hair's L* with the median a* and b* around it, cut into the `COLOURS`
by the rules in `hair_colour`, tuned by eye on 83 ESPN NHL and 54 FotMob
Premier League photos. An MLB cap lands in the band and reads as the cap's
colour, which Ken Griffey Jr. MLB ignores.
"""

from __future__ import annotations

import math

from .skin import _Frame, _frame, _lab, _typical, face_lab

#: Every answer `hair_colour` gives.
COLOURS = ("black", "dark_brown", "brown", "blond", "red", "grey", "bald")

#: The band: its depth as a share of the picture's height, its half width as
#: a share of the picture's width.
BAND_DEPTH = 0.20
BAND_HALF_WIDTH = 0.13
#: A pixel closer than this (CIELAB distance) to the face's colour is skin.
SKIN_DISTANCE = 14.0
#: Grey: lightness from, chroma below.
GREY = (38.0, 12.0)
#: Bald: the hair's share of the band below this, or lightness within
#: `BALD_GAP` of the skin's with a* (the skin's redness) from `BALD_RED`:
#: a scalp seen through stubble.
BALD_SHARE = 0.35
BALD_GAP = 20.0
BALD_RED = 14.0
#: Red: a* from, and more red than yellow.
RED_A = 15.0
#: Blond: (lightness from, b* from), either pair.
BLOND = ((30.0, 12.0), (20.0, 20.0))
#: Black: lightness below, or dull (lightness below, chroma below).
BLACK = 13.0
BLACK_DULL = (30.0, 5.0)
#: Dark brown: lightness below (black aside); brown is what is left.
DARK_BROWN = 22.0


def hair_colour(png: bytes) -> str | None:
    """One of `COLOURS` for the hair in a head shot, or None when the picture
    shows no face to hang the band from. Raises whatever Pillow raises for a
    picture it cannot read."""
    frame = _frame(png)
    if frame is None:
        return None
    skin = face_lab(frame)
    return None if skin is None else hair_colour_of(frame, skin)


def hair_colour_of(frame: _Frame, skin: tuple[float, float, float]) -> str:
    """`hair_colour` for a frame and face colour already found, so one
    decode serves both measures (`core.headshots.measure_faces`)."""
    skin_l, skin_a, skin_b = skin
    w, h, top, cx = frame.w, frame.h, frame.top, frame.cx
    band = 0
    hair: list[tuple[float, float, float]] = []
    left, right = max(0, int(cx - BAND_HALF_WIDTH * w)), min(w, int(cx + BAND_HALF_WIDTH * w))
    for y in range(top, min(h, int(top + BAND_DEPTH * h))):
        for x in range(left, right):
            if not frame.inside(x, y):
                continue
            band += 1
            r, g, b, _ = frame.at(x, y)
            colour = _lab(r, g, b)
            if math.dist(colour, (skin_l, skin_a, skin_b)) > SKIN_DISTANCE:
                hair.append(colour)
    if not hair:
        return "bald"
    lightness, red, yellow = _typical(hair, 8, mode=True)
    chroma = math.hypot(red, yellow)
    if lightness >= GREY[0] and chroma < GREY[1]:
        return "grey"
    if len(hair) / band < BALD_SHARE or (lightness >= skin_l - BALD_GAP and red >= BALD_RED):
        return "bald"
    if red >= RED_A and red > yellow:
        return "red"
    if any(lightness >= light and yellow >= yellow_from for light, yellow_from in BLOND):
        return "blond"
    if lightness < BLACK or (lightness < BLACK_DULL[0] and chroma < BLACK_DULL[1]):
        return "black"
    if lightness < DARK_BROWN:
        return "dark_brown"
    return "brown"
