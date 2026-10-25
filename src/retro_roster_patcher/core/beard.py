"""Whether a player has a beard, read from his head shot, for games that draw one.

Below the face box `core.skin` measures (brow to nose), a narrow band down the
middle of the face is scanned row by row, from the box's bottom to where the
face ends (the rows stop being skin or hair for more than `GAP` of the
picture's height: the shirt; a mouth's few rows of teeth and lips are
crossed). A pixel there is skin when it is close to the face's colour, and
hair when it is much darker than the face (`DARKER`) and dull (`CHROMA`).
The beard is the hair's share of skin and hair; from `SHARE` it is a beard.

Tuned by eye on 109 Brasileirão 2026 head shots (FotMob and ESPN): full and
short beards score above `SHARE`, clean-shaven faces mostly below 0.2, and
stubble, moustaches and the chin shadow of a dark clean-shaven face fall in
between, so the cut is high: a light beard is missed rather than a clean
face given one.
"""

from __future__ import annotations

import math

from .skin import _Frame, _frame, _lab, face_lab

#: The band's half width, a share of the picture's width.
HALF_WIDTH = 0.09
#: Skin: CIELAB distance from the face's colour below this.
SKIN_DISTANCE = 16.0
#: Hair: lightness this far below the face's, and chroma below `CHROMA`.
DARKER = 22.0
CHROMA = 28.0
#: A row is part of the face while skin and hair are this share of it.
FACE_ROW = 0.45
#: The face ends after this many rows (a share of the height) that are not.
GAP = 0.06
#: A beard: hair's share of skin and hair from this.
SHARE = 0.40
#: Fewer skin and hair pixels than this says nothing.
MIN_PIXELS = 50


def beard(png: bytes) -> bool | None:
    """Whether the head shot shows a beard; None when it shows no face.
    Raises whatever Pillow raises for a picture it cannot read."""
    frame = _frame(png)
    if frame is None:
        return None
    skin = face_lab(frame)
    return None if skin is None else beard_of(frame, skin)


def beard_of(frame: _Frame, skin: tuple[float, float, float]) -> bool | None:
    """`beard` on a frame whose face colour (`face_lab`) is `skin`."""
    w, h, top, cx = frame.w, frame.h, frame.top, frame.cx
    rows: list[tuple[int, int, int]] = []
    for y in range(int(top + 0.50 * h), h):
        hair = face = inside = 0
        for x in range(max(0, int(cx - HALF_WIDTH * w)), min(w, int(cx + HALF_WIDTH * w))):
            if not frame.inside(x, y):
                continue
            inside += 1
            r, g, b, _ = frame.at(x, y)
            lab = _lab(r, g, b)
            if math.dist(lab, skin) < SKIN_DISTANCE:
                face += 1
            elif lab[0] < skin[0] - DARKER and math.hypot(lab[1], lab[2]) < CHROMA:
                hair += 1
        rows.append((hair, face, inside))
    end, misses = 0, 0
    for i, (hair, face, inside) in enumerate(rows):
        if inside and hair + face >= FACE_ROW * inside:
            end, misses = i + 1, 0
        else:
            misses += 1
            if misses > GAP * h:
                break
    hair = sum(row[0] for row in rows[:end])
    total = hair + sum(row[1] for row in rows[:end])
    if total < MIN_PIXELS:
        return None
    return hair / total >= SHARE
