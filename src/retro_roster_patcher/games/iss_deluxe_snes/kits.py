"""ISS Deluxe kits: colours, the chest design, the shirt numbers, the clash class.

Traced in Mesen 2 on the USA cartridge:

* Outfield palettes: per team a pointer into bank $89 for the first kit
  ($82:827A, ROM `FIRST`) and the second ($82:82D0, `SECOND`), 17 words: a
  header, then OBJ entries 0-15. Painting each showed entries 7-9 the shirt
  (dark to light), 10-12 the shorts, 13-14 the socks, 15 the shadow; 1-6 are
  hair and skin. No two teams share one.
* Chest design: every player draws the same body; what a team owns is an 8x8
  tile over the chest in eight poses, 0x100 bytes per design from $98:C5D4
  (`DESIGNS`), picked per team by $81:CE8A (`CHEST`; $98:F014 is the blank
  tile). It draws in the flag's palette: entries 13-15 are the flag colours
  (`flags`), 12 the shirt numbers, 1-11 the keeper's palette ($82:8326). Like
  the clash class it belongs to the team: the second kit wears it too.
* Shirt numbers: $82:8459 (`NUMBERS`), a byte per team and kit picking one
  of the eleven colours at $89:832A (`NUMBER_COLOURS`, by byte offset; 0
  keeps the flag palette's entry 12).
* Team photo (team select): OBJ palette 6 from a record per team ($82:FA5F,
  `PHOTO`): entries 1-3 the shirt, 4-6 the shorts, 7-8 the socks, light to
  dark (the rest is the looks', `looks`). Its shirt design is a byte per
  team ($82:F91B, `LAYOUT`: 0x00 Brazil's plain shirt, 0x60 Argentina's
  stripes, 0x70 Croatia's checks...), its stripes or collar OBJ palette 3
  entries 1-3 from a record ($82:F9B3, `TRIM`) several nations share; a
  patched team gets its own (`TRIMS`, bank $89's free run after `looks`').
* Clash: $82:842E (`CLASH`), a byte per team (0 white, 1 blue, 2 red,
  3 yellow, 4 green). When both teams' bytes are equal the away side wears its
  second kit ($98:FA53); nothing else is compared.

A `KitSpec` becomes shirt, shorts and socks shades, its pattern borrows the
nearest of the cartridge's designs (`PATTERN_DESIGNS`) in the secondary colour
(asked of the flag's entries, `pins`), and the numbers take the fixed colour
that reads best on the shirt. Template n is nation n's kit as the image holds
it, design and flag colours included.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...core.errors import CapabilityError
from ...core.kits import KitSpec, kit_from_image
from ..iss_snes.kits import _decode
from ..iss_snes.rom_writer import _contrast, _make_shades, _rgb_to_predominant
from . import flags as F
from .rom import TEAMS

FIRST = 0x1027A  # $82:827A
SECOND = 0x102D0  # $82:82D0
KEEPER = 0x10326  # $82:8326
CLASH = 0x1042E  # $82:842E
NUMBERS = 0x10459  # $82:8459
NUMBER_COLOURS = 0x4832A  # $89:832A
CHEST = 0xCE8A  # $81:CE8A
DESIGNS = 0xC45D4  # $98:C5D4
BLANK = 0xF014  # $98:F014
PHOTO = 0x17A5F  # $82:FA5F
LAYOUT = 0x1791B  # $82:F91B
TRIM = 0x179B3  # $82:F9B3
TRIMS = 0xFB00  # $89:FB00, 24 bytes per team: header and 11 colours
TRIM_WORDS = 11
PLAIN_LAYOUT, STRIPES_LAYOUT = 0x00, 0x60
#: The photo's shirt for a `KitSpec.pattern`; the rest wear the plain one.
PATTERN_LAYOUTS = {"stripes": STRIPES_LAYOUT, "pinstripes": STRIPES_LAYOUT}
_UNUSED = 0x03E0  # the cartridge's filler in a trim record (pure green)
BANK_89, BANK_98 = 0x48000, 0xC0000
KIT_WORDS = 8  # shirt 3, shorts 3, socks 2 (entries 7-14)
TEMPLATES = len(TEAMS)


def _design(index: int) -> int:
    return (DESIGNS + index * 0x100) & 0x7FFF | 0x8000


STRIPES, BAND, CHECKS = _design(0x19), _design(0x0D), _design(0x0E)
#: A `KitSpec.pattern` -> the design that stands for it (Argentina's stripes,
#: Germany's band, Croatia's checks); plain, halves and sleeves are blank.
PATTERN_DESIGNS = {
    "stripes": STRIPES,
    "pinstripes": STRIPES,
    "hoops": BAND,
    "sash": BAND,
    "chevron": BAND,
}
#: The Team Colors-style preview: one front-facing match frame, raw 4bpp tiles
#: (file offset per 16x16 sprite's four tiles), the hair tile and the chest
#: design's pose and place, all read from Mesen's OAM and VRAM.
_BODY = (
    (0, (0xA33FE, 0xA341E, 0xA34E0, 0xA3500)),
    (16, (0xA343E, 0xA345E, 0xA3520, 0xA3540)),
    (32, (0xA347E, 0xA349E, 0xA3560, 0xA3580)),
)
_HAIR = (0xBE658, 7, 2)
_POSE, _CHEST_AT = 1, (6, 10)
_WIDTH, _HEIGHT, _LEFT = 32, 48, 8
PREVIEW_SCALE = 3

RGB = tuple[int, int, int]


def _rgb(word: int) -> RGB:
    r, g, b = (word >> s & 31 for s in (0, 5, 10))
    return r << 3 | r >> 2, g << 3 | g >> 2, b << 3 | b >> 2


def palette_at(image: bytes | bytearray, team: int, kit: int) -> int:
    """Where `team`'s palette for `kit` (0 first, 1 second) starts: its header."""
    table = (FIRST, SECOND)[kit]
    return BANK_89 + (struct.unpack_from("<H", image, table + 2 * team)[0] & 0x7FFF)


def _words(image: bytes | bytearray, at: int, n: int) -> list[int]:
    return list(struct.unpack_from(f"<{n}H", image, at))


def chest(image: bytes | bytearray, team: int) -> int:
    return int(struct.unpack_from("<H", image, CHEST + 2 * team)[0])


def _design_tile(image: bytes | bytearray, design: int, pose: int) -> list[list[int]]:
    if design == BLANK:
        return [[0] * 8 for _ in range(8)]
    at = BANK_98 + (design & 0x7FFF) + 32 * pose
    return _decode(bytes(image[at : at + 32]))


def _design_entries(image: bytes | bytearray, design: int) -> dict[int, int]:
    """How many pixels of `design`, over its eight poses, use each flag entry 13-15."""
    counts: dict[int, int] = {}
    for pose in range(8):
        for row in _design_tile(image, design, pose):
            for v in row:
                if v >= 13:
                    counts[v] = counts.get(v, 0) + 1
    return counts


def _number_colours(image: bytes | bytearray) -> dict[int, RGB]:
    """The fixed shirt-number colours by the byte that picks them."""
    return {2 * i: _rgb(w) for i, w in enumerate(_words(image, NUMBER_COLOURS, 12)) if i}


@dataclass(frozen=True)
class Kit:
    """One kit as `write` writes it: `words` entries 7-14 (BGR555), `number`
    the shirt-number byte; `design`, `pins` (flag entry -> wanted colour and
    weight) and `clash` belong to the team, so only the first kit's count;
    so do the team photo's shirt design (`layout`) and its trim record's
    colours (`trim`, empty to keep the slot's)."""

    words: tuple[int, ...]
    number: int
    design: int
    pins: dict[int, tuple[RGB, int]]
    clash: int
    layout: int = PLAIN_LAYOUT
    trim: tuple[int, ...] = ()

    @property
    def shades(self) -> list[RGB]:
        return [_rgb(w) for w in self.words[:3]]


def parse(value: Any) -> KitSpec | int:
    """One KIT of the `kits` option: a `KitSpec`, its dict, `{"image": png
    bytes or path}` or `{"template": n}`, as a spec or a template number."""
    if isinstance(value, KitSpec):
        return value
    value = dict(value)
    if "template" in value:
        n = int(value["template"])
        if not 0 <= n < TEMPLATES:
            raise CapabilityError(
                f"Unknown kit template {n}; iss-deluxe-snes has 0..{TEMPLATES - 1}"
            )
        return n
    if "image" in value:
        png = value["image"]
        return kit_from_image(Path(png).read_bytes() if isinstance(png, str | Path) else bytes(png))
    return KitSpec.from_dict(value)


def _best_number(image: bytes | bytearray, shades: list[RGB]) -> int:
    choices = _number_colours(image)
    return max(choices, key=lambda k: (min(_contrast(choices[k], s) for s in shades), -k))


def resolve(image: bytes | bytearray, choice: KitSpec | int, kit: int) -> Kit:
    """`choice` for the first (`kit` 0) or second kit, read against `image`."""
    if isinstance(choice, KitSpec):
        words = (
            _make_shades(*choice.shirt, 3)
            + _make_shades(*choice.shorts, 3)
            + _make_shades(*choice.socks, 2)
        )
        design = PATTERN_DESIGNS.get(choice.pattern, BLANK)
        shades = [_rgb(w) for w in words[:3]]
        return Kit(
            words=tuple(words),
            number=_best_number(image, shades),
            design=design,
            pins={e: (choice.secondary, n) for e, n in _design_entries(image, design).items()},
            clash=_rgb_to_predominant(*choice.shirt),
            layout=PATTERN_LAYOUTS.get(choice.pattern, PLAIN_LAYOUT),
            trim=(*_make_shades(*choice.secondary, 3)[::-1], *[_UNUSED] * (TRIM_WORDS - 3)),
        )
    n = choice
    trim_at = BANK_89 + (struct.unpack_from("<H", image, TRIM + 2 * n)[0] & 0x7FFF) + 2
    design = chest(image, n)
    flag = F.colours(image, n)
    return Kit(
        words=tuple(_words(image, palette_at(image, n, kit) + 16, KIT_WORDS)),
        number=image[NUMBERS + 2 * n + kit],
        design=design,
        pins={e: (flag[e - 12], c) for e, c in _design_entries(image, design).items()},
        clash=image[CLASH + n],
        layout=image[LAYOUT + n],
        trim=tuple(_words(image, trim_at, TRIM_WORDS)),
    )


def write(image: bytearray, team: int, kits: tuple[Kit | None, Kit | None]) -> None:
    """Both kits' colours and numbers; the first kit's design, clash class and
    team photo (colours, shirt design, trim). The pins go to `flags.write`."""
    for kit, k in enumerate(kits):
        if k is None:
            continue
        struct.pack_into(f"<{KIT_WORDS}H", image, palette_at(image, team, kit) + 16, *k.words)
        image[NUMBERS + 2 * team + kit] = k.number
        if kit == 0:
            struct.pack_into("<H", image, CHEST + 2 * team, k.design)
            image[CLASH + team] = k.clash
            w = k.words  # dark to light; the photo's are light to dark
            photo = BANK_89 + (struct.unpack_from("<H", image, PHOTO + 2 * team)[0] & 0x7FFF)
            struct.pack_into(
                "<8H", image, photo + 2, w[2], w[1], w[0], w[5], w[4], w[3], w[7], w[6]
            )
            image[LAYOUT + team] = k.layout
            if k.trim:
                at = TRIMS + 24 * team
                o = BANK_89 + at - 0x8000
                struct.pack_into(f"<{1 + TRIM_WORDS}H", image, o, 2 * TRIM_WORDS - 1, *k.trim)
                struct.pack_into("<H", image, TRIM + 2 * team, at)


def preview_png(
    image: bytes | bytearray,
    team: int,
    kit: int,
    choice: Kit | None = None,
    home: Kit | None = None,
) -> bytes:
    """A match player of `team` in its first (`kit` 0) or second kit, drawn
    from the cartridge's own tiles: a `PREVIEW_SCALE`x RGBA PNG of 32x48.
    `choice` is drawn instead of the kit the image holds; the chest design is
    the first kit's (`choice` for kit 0, `home` for kit 1, else the image's),
    its pinned flag entries in the colours asked for. Needs Pillow."""
    from PIL import Image

    pal_at = palette_at(image, team, kit)
    body = [_rgb(w) for w in _words(image, pal_at + 2, 16)]
    if choice is not None:
        body[7:15] = [_rgb(w) for w in choice.words]
    keeper = [
        _rgb(w)
        for w in _words(
            image, BANK_89 + (struct.unpack_from("<H", image, KEEPER + 2 * team)[0] & 0x7FFF), 12
        )
    ]
    flag = F.colours(image, team)
    number = choice.number if choice is not None else image[NUMBERS + 2 * team + kit]
    numbers = _number_colours(image)
    chest_pal = [(0, 0, 0)] + keeper[1:12] + [numbers.get(number, flag[0])] + flag[1:]
    owner = choice if kit == 0 else home
    design = owner.design if owner is not None else chest(image, team)
    if owner is not None:
        for e, (colour, _) in owner.pins.items():
            chest_pal[e] = colour

    im = Image.new("RGBA", (_WIDTH, _HEIGHT), (0, 0, 0, 0))

    def put(tile: list[list[int]], x0: int, y0: int, pal: list[RGB]) -> None:
        for y, row in enumerate(tile):
            for x, v in enumerate(row):
                if v:
                    im.putpixel((x0 + x, y0 + y), (*pal[v], 255))

    for top, tiles in _BODY:
        for (dx, dy), at in zip(((0, 0), (8, 0), (0, 8), (8, 8)), tiles, strict=True):
            put(_decode(bytes(image[at : at + 32])), _LEFT + dx, top + dy, body)
    at, hx, hy = _HAIR
    put(_decode(bytes(image[at : at + 32])), _LEFT + hx, hy, body)
    put(_design_tile(image, design, _POSE), _LEFT + _CHEST_AT[0], _CHEST_AT[1], chest_pal)
    im = im.resize((_WIDTH * PREVIEW_SCALE, _HEIGHT * PREVIEW_SCALE), Image.Resampling.NEAREST)
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()
