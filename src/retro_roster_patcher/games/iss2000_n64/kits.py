"""Kit colours: the palettes the players' shirts, shorts and socks are drawn in.

Assets 627-635, read end to end, hold 512-byte kit records from offset 0x80.
Record k is a team's first kit and k + 106 its second; `KIT_INDEX` gives k
per team id. It was measured, not found in a table: the team-select model's
palette (RAM 0x80376D48) for each team, with each kit, matched to its record.
Swapping two records swaps the colours on team select and in a match.

Inside a record, each 32 bytes is a 16-colour RGBA5551 palette:

    0x00  shirt      0x40  shorts      0x60  socks      0x80  waistband

(painting each a solid colour in Mupen64Plus-Next showed which part it
draws; 0xA0-0xDF drew nothing on the outfield players). Each palette starts
with its part's shading ramp, lightest first; what follows is trim, badge
and the legs' skin tones, which are kept. A new colour replaces the ramp,
keeping each shade's brightness relative to the ramp's lightest.

The shirt's design (Argentina's stripes, say) is not in the record: a team
keeps its slot's design in the new colours.
"""

from __future__ import annotations

import struct

from ...core.kits import KitSpec
from . import rom as R

PACKS = tuple(range(627, 636))
FIRST = 0x80
SIZE = 0x200
AWAY = 106

#: Kit record per team id 0-99 (first kit; the second is +106).
KIT_INDEX = (
    46, 44, 59, 96, 49, 98, 16, 36, 20, 86, 69, 91, 13, 67, 39, 93, 109, 80, 57, 97,
    81, 43, 53, 41, 110, 31, 88, 24, 48, 85, 34, 94, 52, 95, 22, 11, 70, 89, 102, 40,
    65, 68, 19, 104, 75, 45, 15, 18, 73, 33, 58, 25, 79, 90, 101, 76, 38, 66, 12, 72,
    47, 106, 26, 74, 60, 32, 50, 30, 100, 23, 14, 29, 105, 83, 21, 27, 84, 108, 37, 61,
    63, 35, 28, 51, 99, 71, 92, 103, 55, 107, 62, 77, 54, 56, 64, 87, 17, 78, 82, 42,
)  # fmt: skip

SHIRT, SHORTS, SOCKS, WAIST = 0x00, 0x40, 0x60, 0x80

RGB = tuple[int, int, int]


def _split(word: int) -> RGB:
    return (word >> 11) & 31, (word >> 6) & 31, (word >> 1) & 31


def _lum(c: RGB) -> float:
    return 0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2]


def ramp_length(palette: list[int]) -> int:
    """How many leading colours step down in brightness: the shading ramp."""
    n = 1
    while n < len(palette) and _lum(_split(palette[n])) < _lum(_split(palette[n - 1])) + 0.05:
        n += 1
    return n


def _shade(colour: RGB, f: float) -> int:
    r, g, b = (min(255, round(v * max(0.4, min(1.0, f)))) for v in colour)
    return (r >> 3) << 11 | (g >> 3) << 6 | (b >> 3) << 1 | 1


def recolour(palette: list[int], colour: RGB, start: int = 0, stop: int | None = None) -> list[int]:
    """`palette` with the ramp at `start` (up to `stop`) in `colour`, each
    shade as much darker than the new colour as it was than the ramp's
    lightest (never below 40%)."""
    n = start + ramp_length(palette[start:stop])
    top = max(_lum(_split(palette[start])), 0.5)
    out = list(palette)
    for i in range(start, n):
        out[i] = _shade(colour, _lum(_split(palette[i])) / top)
    return out


def paint(record: bytearray, kit: KitSpec) -> None:
    """Recolour a 512-byte kit record for `kit`.

    The shirt palette is laid out as the cartridge's designs use it
    (painting one colour at a time in Mupen64Plus-Next): its first ramp is
    the shirt, the colours after it up to index 7 the collar and trim, and
    a ramp from index 8 a design's stripes (Argentina's). Trim takes the
    secondary colour; stripes take it too on a patterned kit, the shirt
    colour on a plain one, so a striped slot can wear a plain kit.
    """
    shirt = list(struct.unpack_from(">16H", record, SHIRT))
    n = ramp_length(shirt[:8])
    shirt = recolour(shirt, kit.shirt, 0, 8)
    if n < 8:
        top = max(_lum(_split(shirt[n])), 0.5)
        for i in range(n, 8):
            shirt[i] = _shade(kit.secondary, _lum(_split(shirt[i])) / top)
    if ramp_length(shirt[8:]) >= 4:
        stripes = kit.secondary if kit.pattern != "plain" else kit.shirt
        shirt = recolour(shirt, stripes, 8)
    struct.pack_into(">16H", record, SHIRT, *shirt)
    for at, colour in ((SHORTS, kit.shorts), (SOCKS, kit.socks), (WAIST, kit.shorts)):
        pal = list(struct.unpack_from(">16H", record, at))
        struct.pack_into(">16H", record, at, *recolour(pal, colour))


def write(
    image: bytearray, space: R.AssetSpace, kits: dict[int, tuple[KitSpec | None, KitSpec | None]]
) -> None:
    """Give each team id in `kits` its (first, second) kit; None keeps one."""
    if not kits:
        return
    assets = R.assets(image)
    data = {k: R.read_asset(image, assets[k]) for k in PACKS}
    stream = bytearray(b"".join(data[k] for k in PACKS))
    for team, sides in kits.items():
        for side, kit in enumerate(sides):
            if kit is None:
                continue
            o = FIRST + (KIT_INDEX[team] + AWAY * side) * SIZE
            record = stream[o : o + SIZE]
            paint(record, kit)
            stream[o : o + SIZE] = record
    o = 0
    for k in PACKS:
        new = bytes(stream[o : o + len(data[k])])
        o += len(data[k])
        if new != data[k]:
            space.put(assets[k], new)
