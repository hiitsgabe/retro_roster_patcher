"""The league as 32 clubs, and what each slot is called, wears and shows.

Measured on the retail cartridge in mednafen and blastem (Nov 2026); offsets
are file offsets. Every per-team table the game reads is found through a
`movea.l #table` whose immediate sits at the listed site:

* Team blocks, through the table at $30E (`movea.w`, so it stays there and
  grows to 34 entries). A block ends in four length-prefixed strings: city
  (Game Setup, menus), abbreviation (the HUD), nickname, arena.
* Player cards $F92F4 (`portraits`), big logos $F86F2 (an EA art record of
  6x6 tiles, drawn in the 16 colours at $FF462 + 32 a team: 0, 3, 4 the
  backdrop, 1 and 2 the team's colours, 5 white, 6 black, 7-15 the logo's),
  center ice $FEA74 (24 tiles, 6x4, in BG line 1 of the rink, whose colours
  are fixed), the team rating on the stats screen $FE18E (a byte a team), and
  the Game Setup banner colours $F8BF4 (64 bytes: colours 1 and 2 of the
  first 16 the home banner's bands, 3 and 4 of the second the visitor's).
* Banners. Game Setup draws each team's name box, 11x2 tiles, from one sheet
  at $AFE12 whose 398 tiles it loads twice (the visitor's copy with colours 1
  and 2 swapped for 3 and 4) next to everything else on that screen; there is
  no room for more. The routine at $17B78 is rewritten to take each team's
  own record instead and load its 22 tiles as it draws them, as the logos
  are; $F84D0 then only reserves the two banners' tiles. The intermission and
  playoff bracket draw a second sheet, $ABA14, whose bands are drawn in the
  rink's fixed colours; it is rebuilt with 34 frames (the NHL'26 ROM hack
  shows both screens take at least 704 tiles).
* Counts: Game Setup's Team 1 and Team 2 go to 34 ($F791E, $F7930), the
  playoffs' team choice to 32 ($F79E4), a random team to 34 ($F73E6) and
  the playoffs' random opponents to 32 ($18032). Nothing else in the code
  counts teams; its 26-long loops ($EF44, $FB010) walk a roster.

`expand` lays the league out: the 26 clubs where they were, six new ones in
slots 26-31 (copies of All-Stars East until a patch names them), All-Stars
East and West in 32 and 33. Everything it moves goes to the image grown to 2
MiB: the blocks to `TEAM_REGION` (`STRIDE` apart, room for longer names), the
tables to `TABLE_REGION`, the graphics to `HEAP`, which `write` rebuilds on
every patch, carrying over what an earlier one put there.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from ...core.errors import RomError
from ...formats import genesis
from ..nhl94_snes.identity import CLUBS, Club, club_code
from .models import MODERN_NHL_TO_NHL94_GEN
from .rom_reader import POINTER_TABLE_OFFSET as TEAM_TABLE
from .rom_writer import grow

StatusFn = Callable[[str], None]
RGB = tuple[int, int, int]
Grid = list[list[int]]

RETAIL_SLOTS = 28
SLOTS = 34
CLUB_SLOTS = 32
#: The cartridge slot each slot starts from: the 26 clubs, six copies of
#: All-Stars East for the new clubs, then the two All-Star teams.
SOURCE = (*range(26), *[26] * 6, 26, 27)
#: The clubs the cartridge never had, in slots 26-31.
NEW_CLUBS = ("CBJ", "MIN", "NSH", "SEA", "UTA", "VGK")

CARTRIDGE_BLOCKS = range(0x37E, 0x5576)
TEAM_REGION = range(0x110000, 0x118000)
STRIDE = 0x3C0
TABLE_REGION = range(0x118000, 0x119400)
HEAP = range(0x119400, 0x140000)


@dataclass(frozen=True)
class Table:
    retail: int
    size: int
    at: int
    #: File offsets of the u32 immediates that load the table.
    sites: tuple[int, ...]


CARDS = Table(0xF92F4, 4, 0x118000, (0xF894A, 0xFAE2C))
LOGOS = Table(0xF86F2, 4, 0x118100, (0xF8682, 0xFA69E, 0xFB1AC, 0xFD1F4, 0xFD9BA))
ICE = Table(0xFEA74, 4, 0x118200, (0xFEA5A,))
RATINGS = Table(0xFE18E, 1, 0x118300, (0xFE17C,))
LOGO_PALETTES = Table(0xFF462, 32, 0x118400, (0xF8694, 0xFA6B0, 0xFB1BE, 0xFD19C, 0xFD978, 0xFD9D6))
BANNER_PALETTES = Table(0xF8BF4, 64, 0x118880, (0x17B2A, 0x17B50, 0xFA2DA))
TABLES = (CARDS, LOGOS, ICE, RATINGS, LOGO_PALETTES, BANNER_PALETTES)
#: Each slot's two Game Setup banner records, home then visitor.
BANNERS = 0x119100

BANNER_SHEET = 0xAFE12
STRIPE_SHEET = 0xABA14
STRIPE_SITES = (0x16D26, 0x17672)
STRIPE_TILE_SITES = (0x11F24,)

#: The new banner routine at $17B78 (home) and $17B7C (visitor): d1 the team,
#: d4 the first tile. `lsl.w #3,d1; bra.s; lsl.w #3,d1; addq.w #4,d1;
#: movea.l #BANNERS,a0; movea.l (a0,d1.w),a0; movea.l a0,a1; adda.l 4(a0),a1;
#: lea 8(a0),a2; move.w (a1),d2; clr.w d0; clr.w d1; moveq #2,d3; moveq #0,d5;
#: jmp $1169A`, which draws the map and loads the tiles at a2.
_BANNER_CODE = (
    f"e7496004e7495841207c{BANNERS:08x}207010002248d3e8000445e8000834114240424176027a004ef90001169a"
)
#: (offset, cartridge bytes, expanded bytes).
CODE = (
    (0xF7922, "001c001c", "00220022"),
    (0xF7934, "001c001c", "00220022"),
    (0xF79E4, "781a", "7820"),
    (0xF73E6, "701c", "7022"),
    (0xF73F2, "701c", "7022"),
    (0x18032, "701a", "7020"),
    # New Playoffs looks the user's team up in the seed tables, capped at 25.
    (0x17D34, "b47c0019", "b47c001f"),
    (0x17D3C, "7419", "741f"),
    # Game Setup's banner tiles: 22 for each of the two, from tile 2.
    (0xF84D0, "7802247c000afe1a4eb9", "782e31fc0018d43a4e75"),
    # The visitor's banner calls its own entry.
    (0x17B38, "6100003e", "61000042"),
    (
        0x17B78,
        "4240e341207c000afe122248d1d0d3e90004347c030a341176027a0060009b044240e341207c000aba142248",
        _BANNER_CODE,
    ),
)

#: New Playoffs' brackets: 32 tables of 16 slots, eight of the East then
#: eight of the West, the first round's pairs in order. It picks one at random
#: and walks on to the first holding the user's team.
SEEDS = 0x5576
SEED_TABLES = 32
#: Each club slot's `CLUBS` key.
SLOT_CODES = {slot: code for code, slot in MODERN_NHL_TO_NHL94_GEN.items() if code in CLUBS}

#: The cartridge's banner texts, slot by slot; its font comes from these.
RETAIL_NAMES = (
    "ANAHEIM", "BOSTON", "BUFFALO", "CALGARY", "CHICAGO", "DALLAS", "DETROIT",
    "EDMONTON", "FLORIDA", "HARTFORD", "LOS ANGELES", "MONTREAL", "NEW JERSEY",
    "NEW YORK", "NEW YORK", "OTTAWA", "PHILADELPHIA", "PITTSBURGH", "QUEBEC",
    "SAN JOSE", "ST. LOUIS", "TAMPA BAY", "TORONTO", "VANCOUVER", "WASHINGTON",
    "WINNIPEG", "ALL STARS E.", "ALL STARS W.",
)  # fmt: skip
BANNER_WIDTH = 88
#: Text rows of a banner, and its two bands' first rows.
TEXT_ROWS = range(3, 13)
BOTTOM_BAND = 8
SPACE = 4
#: (face, shadow, top band, bottom band) colour numbers of each sheet.
BANNER_INKS = (5, 6, 1, 2)
STRIPE_INKS = (1, 4)
#: The visitor's banner tiles, as $1172C loads them: 1 and 2 become 3 and 4.
AWAY = {1: 3, 2: 4, 3: 1, 4: 2}

#: The fixed colours the intermission and playoff banners draw in.
RINK = (
    0x0EE8, 0x0CCC, 0x0888, 0x0444, 0x0222, 0x008C, 0x006A, 0x0000,
    0x0822, 0x0600, 0x0042, 0x0020, 0x004A, 0x0006, 0x066A, 0x0246,
)  # fmt: skip
#: Colours a band may take: not the greys the frame and the letters use.
BAND_INKS = range(5, 16)
ICE_TILES = (6, 4)
#: How many of every 2x2 pixels of the logo show through the ice (in the
#: order of `BAYER`), and the uniform colours it may take (0 the ice, 6-7 skin
#: and 14-15 stick and skates left out).
ICE_SHOW = 2
BAYER = ((0, 2), (3, 1))
ICE_INKS = (0, 1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13)
LOGO_TILES = (6, 6)
#: Colour numbers of the logo palette: backdrop, team colours, white, black.
BACKDROP = 0x0EE8
WHITE = 0x0EEE
LOGO_INKS = range(7, 16)
NEW_RATING = 75
#: The Matchups Overall ($FE18E) against the mean of the twelve rating nibbles
#: of the Best line: a least-squares fit over the cartridge's 26 clubs (within 7).
RATING_SLOPE, RATING_BASE = 22.4, -5.0


# -- one slot's identity -----------------------------------------------------------


@dataclass(frozen=True)
class Identity:
    """Everything `write` puts in one slot."""

    code: str  # the `CLUBS` key, also the HUD abbreviation
    club: Club
    color: RGB
    alternate: RGB
    #: The logo picture (PNG or anything Pillow reads); None keeps the slot's.
    logo: bytes | None = None


def _hex(value: str | None) -> RGB | None:
    value = (value or "").strip().lstrip("#")
    if len(value) != 6:
        return None
    try:
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except ValueError:
        return None


def identity_of(
    code: str, color: str | None = None, alternate: str | None = None
) -> Identity | None:
    """The club behind a provider's team code, in the provider's colours where
    it sent them; None for a code `CLUBS` does not know."""
    key = club_code(code)
    if key is None:
        return None
    club = CLUBS[key]
    main = _hex(color) or _hex(club.color)
    other = _hex(alternate) or _hex(club.alternate)
    assert main is not None and other is not None
    return Identity(key, club, main, other)


# -- bytes ------------------------------------------------------------------------------


def _u16(rom: bytes | bytearray, at: int) -> int:
    return int.from_bytes(rom[at : at + 2], "big")


def _u32(rom: bytes | bytearray, at: int) -> int:
    return int.from_bytes(rom[at : at + 4], "big")


def _put32(rom: bytearray, at: int, value: int) -> None:
    rom[at : at + 4] = value.to_bytes(4, "big")


def _block_end(rom: bytes | bytearray, base: int) -> int:
    at = base + _u16(rom, base + 4)
    for _ in range(4):
        at += _u16(rom, at)
    return at


def _string(text: str) -> bytes:
    data = text.encode("ascii", "replace")
    data += b"\0" * (len(data) % 2)
    return struct.pack(">H", len(data) + 2) + data


# -- art records ------------------------------------------------------------------------


def _tile(tiles: bytes, entry: int) -> Grid:
    at = (entry & 0x7FF) * 32
    rows = [
        [v for b in tiles[at + 4 * y : at + 4 * y + 4] for v in (b >> 4, b & 15)] for y in range(8)
    ]
    if entry & 0x800:
        rows = [r[::-1] for r in rows]
    if entry & 0x1000:
        rows = rows[::-1]
    return rows


def _frames(record: bytes, rows: int) -> list[Grid]:
    """Each `rows`-tile-high frame of an art record's map, as colour numbers."""
    map_at = _u32(record, 4)
    width, height = _u16(record, map_at), _u16(record, map_at + 2)
    tiles = record[10 : 10 + 32 * _u16(record, 8)]
    out = []
    for top in range(0, height, rows):
        grid: Grid = [[] for _ in range(8 * rows)]
        for ty in range(rows):
            for tx in range(width):
                entry = _u16(record, map_at + 4 + 2 * ((top + ty) * width + tx))
                for y, row in enumerate(_tile(tiles, entry)):
                    grid[8 * ty + y] += row
        out.append(grid)
    return out


def _pack(rows: Grid) -> bytes:
    return bytes(r[x] << 4 | r[x + 1] for r in rows for x in range(0, 8, 2))


def _record(frames: Sequence[Grid], palette: bytes = b"") -> bytes:
    """An art record of `frames` stacked (line 1, each tile once, flips
    shared), with `palette` (0x80 bytes) or none."""
    tiles: dict[bytes, int] = {}
    entries = []
    width = len(frames[0][0]) // 8
    for grid in frames:
        for ty in range(len(grid) // 8):
            for tx in range(width):
                rows = [grid[8 * ty + y][8 * tx : 8 * tx + 8] for y in range(8)]
                variants = (
                    (0, rows),
                    (0x800, [r[::-1] for r in rows]),
                    (0x1000, rows[::-1]),
                    (0x1800, [r[::-1] for r in rows[::-1]]),
                )
                found = [(flip, _pack(v)) for flip, v in variants if _pack(v) in tiles]
                if found:
                    flip, key = found[0]
                else:
                    flip, key = 0, _pack(rows)
                    tiles[key] = len(tiles)
                entries.append(0x2000 | flip | tiles[key])
    data = b"".join(tiles)
    map_at = 10 + len(data) + len(palette)
    head = struct.pack(">IIH", 10 + len(data) if palette else map_at, map_at, len(tiles))
    height = len(entries) // width
    return head + data + palette + struct.pack(f">HH{len(entries)}H", width, height, *entries)


def _record_size(rom: bytes | bytearray, at: int) -> int:
    map_at = _u32(rom, at + 4)
    return map_at + 4 + 2 * _u16(rom, at + map_at) * _u16(rom, at + map_at + 2)


# -- the font ------------------------------------------------------------------------------


def _glyphs(rom: bytes | bytearray) -> dict[str, Grid]:
    """The banner letters, cut from the cartridge's Game Setup banners: each
    the face and shadow pixels of the text rows, 0 elsewhere."""
    record = bytes(rom[BANNER_SHEET : BANNER_SHEET + _record_size(rom, BANNER_SHEET)])
    face, shadow, _, _ = BANNER_INKS
    out: dict[str, Grid] = {}
    for grid, name in zip(_frames(record, 2), RETAIL_NAMES, strict=False):
        columns = [
            x in range(2, BANNER_WIDTH - 2) and any(grid[y][x] == face for y in TEXT_ROWS)
            for x in range(BANNER_WIDTH)
        ]
        runs, start = [], None
        for x, inked in enumerate([*columns, False]):
            if inked and start is None:
                start = x
            if not inked and start is not None:
                runs.append((start, x))
                start = None
        letters = name.replace(" ", "")
        if len(runs) != len(letters):
            continue
        for (a, b), letter in zip(runs, letters, strict=True):
            out.setdefault(
                letter,
                [[v if v in (face, shadow) else 0 for v in grid[y][a : b + 1]] for y in TEXT_ROWS],
            )
    return out


def banner_text(club: Club) -> str:
    return club.city.upper()


def _letter(text: str, glyphs: Mapping[str, Grid]) -> list[Grid | None]:
    return [None if c == " " else glyphs.get(c) for c in text if c == " " or c in glyphs]


def _text_width(text: str, glyphs: Mapping[str, Grid]) -> int:
    return sum(SPACE if g is None else len(g[0]) for g in _letter(text, glyphs))


def _banner(background: Grid, text: str, glyphs: Mapping[str, Grid], inks: tuple[int, ...]) -> Grid:
    """`background` (a banner frame) with its text rows cleared to its bands and
    `text` drawn centred in `inks` (face, shadow, top band, bottom band)."""
    face, shadow, top, bottom = inks
    grid = [row[:] for row in background]
    left, right = 2, BANNER_WIDTH - 2
    for y in TEXT_ROWS:
        for x in range(left, right):
            grid[y][x] = top if y < BOTTOM_BAND else bottom
    x = (BANNER_WIDTH - _text_width(text, glyphs)) // 2
    for glyph in _letter(text, glyphs):
        if glyph is None:
            x += SPACE
            continue
        for gy, row in enumerate(glyph):
            for gx, v in enumerate(row):
                if v and left <= x + gx < right:
                    grid[TEXT_ROWS.start + gy][x + gx] = face if v == BANNER_INKS[0] else shadow
        x += len(glyph[0])
    return grid


def _recolour(grid: Grid, mapping: Mapping[int, int]) -> Grid:
    return [[mapping.get(v, v) for v in row] for row in grid]


# -- colours -------------------------------------------------------------------------------


def _luma(rgb: RGB) -> float:
    return 0.3 * rgb[0] + 0.59 * rgb[1] + 0.11 * rgb[2]


def _scale(rgb: RGB, k: float) -> RGB:
    r, g, b = (max(0, min(255, round(c * k))) for c in rgb)
    return r, g, b


def _bands(team: Identity) -> tuple[RGB, RGB]:
    """The banner's top and bottom band colours: the team's, the bottom one
    darkened from the top when the alternate is too light for white letters."""
    top = team.color if _luma(team.color) < 200 else _scale(team.color, 0.6)
    bottom = team.alternate
    if bottom == top or _luma(bottom) > 170:
        bottom = _scale(top, 0.6)
    return top, bottom


def _nearest(rgb: RGB, words: Sequence[int], allowed: Sequence[int]) -> int:
    return min(
        allowed,
        key=lambda i: sum((a - b) ** 2 for a, b in zip(genesis.rgb(words[i]), rgb, strict=True)),
    )


def _uniforms(current: bytes, team: Identity) -> bytes:
    """The block's two in-game palettes, home and away, as the cartridge dresses
    a club: 3-5 the team colour dark to light in both; home a white jersey
    (11-13) with stripes of the team colours (8-10), away the team colour
    (11-13) with white stripes."""
    words = list(struct.unpack(">32H", current))
    c1, c2 = team.color, team.alternate
    ramp = [genesis.word(_scale(c1, k)) for k in (0.55, 0.8, 1.0)]
    home = {3: ramp[0], 4: ramp[1], 5: ramp[2], 8: ramp[0], 9: genesis.word(c2), 10: ramp[2]}
    home |= {11: 0x0CCC, 12: 0x0EEE, 13: 0x0AAA}
    away = {3: ramp[0], 4: ramp[1], 5: ramp[2], 8: 0x0AAA, 9: genesis.word(c2), 10: 0x0EEE}
    away |= {11: ramp[1], 12: ramp[2], 13: ramp[0]}
    for line, roles in ((0, home), (16, away)):
        for i, w in roles.items():
            words[line + i] = w
    return struct.pack(">32H", *words)


def _logo_palette(current: bytes, team: Identity, logo: Sequence[int] | None) -> bytes:
    words = list(struct.unpack(">16H", current))
    top, bottom = _bands(team)
    words[:7] = [BACKDROP, genesis.word(top), genesis.word(bottom), BACKDROP, BACKDROP, WHITE, 0]
    if logo is not None:
        words[7:] = logo
    return struct.pack(">16H", *words)


def _banner_palette(logo_palette: bytes) -> bytes:
    """Home line: the logo palette; visitor line: its colours 1 and 2 at 3 and 4."""
    words = list(struct.unpack(">16H", logo_palette))
    away = [BACKDROP, BACKDROP, BACKDROP, words[1], words[2], *words[5:]]
    return logo_palette + struct.pack(">16H", *away)


# -- logos ---------------------------------------------------------------------------------


def _logo(png: bytes) -> tuple[bytes, list[int]]:
    """A big-logo record (6x6 tiles, line 1) on white, and its nine colours (7-15)."""
    from PIL import Image

    from ...core.assets import centred_crest

    size = (8 * LOGO_TILES[0], 8 * LOGO_TILES[1])
    canvas = centred_crest(png, size, white=False)
    alpha = canvas.getchannel("A").tobytes()
    rgb = canvas.convert("RGB").tobytes()
    solid = [rgb[3 * i : 3 * i + 3] for i, a in enumerate(alpha) if a >= 128]
    if not solid:
        raise ValueError("the logo has no opaque pixels")
    strip = Image.frombytes("RGB", (len(solid), 1), b"".join(solid))
    flat = strip.quantize(colors=len(LOGO_INKS), dither=Image.Dither.NONE).getpalette() or []
    colours = [
        genesis.word(flat[j : j + 3]) for j in range(0, min(len(flat), 3 * len(LOGO_INKS)), 3)
    ]
    colours += [0] * (len(LOGO_INKS) - len(colours))
    words = [BACKDROP] * 5 + [WHITE, 0] + colours
    inks = [5, 6, *LOGO_INKS]
    cache: dict[bytes, int] = {}
    grid: Grid = [[0] * size[0] for _ in range(size[1])]
    for i, a in enumerate(alpha):
        if a < 128:
            grid[i // size[0]][i % size[0]] = 5
            continue
        px = rgb[3 * i : 3 * i + 3]
        if px not in cache:
            cache[px] = _nearest((px[0], px[1], px[2]), words, inks)
        grid[i // size[0]][i % size[0]] = cache[px]
    return _record([grid]), colours


def _ice(png: bytes, home: Sequence[int]) -> bytes:
    """A center-ice entry: 24 tiles, 6x4, in `home`, the home team's uniform
    palette, which the game draws center ice in (0 the ice). The logo shows
    through the ice, as if painted under it: `ICE_SHOW` of every 2x2 pixels
    are drawn, the rest left to the ice, in the team's colours and the greys
    (not the skin and stick ones)."""
    from ...core.assets import centred_crest

    size = (8 * ICE_TILES[0], 8 * ICE_TILES[1])
    canvas = centred_crest(png, size)
    alpha = canvas.getchannel("A").tobytes()
    rgb = canvas.convert("RGB").tobytes()
    cache: dict[bytes, int] = {}
    pixels = []
    for i, a in enumerate(alpha):
        px = rgb[3 * i : 3 * i + 3]
        x, y = i % size[0], i // size[0]
        if a < 128 or BAYER[y % 2][x % 2] >= ICE_SHOW:
            pixels.append(0)
            continue
        if px not in cache:
            cache[px] = _nearest((px[0], px[1], px[2]), home, ICE_INKS[1:])
        pixels.append(cache[px])
    tiles = b""
    for ty in range(ICE_TILES[1]):
        for tx in range(ICE_TILES[0]):
            for y in range(8):
                row = pixels[(8 * ty + y) * size[0] + 8 * tx :][:8]
                tiles += bytes(row[k] << 4 | row[k + 1] for k in range(0, 8, 2))
    return struct.pack(">IIH", 0, 0, ICE_TILES[0] * ICE_TILES[1]) + tiles


# -- the league ----------------------------------------------------------------------------


def _code_state(rom: bytes | bytearray) -> str | None:
    """'retail' or 'expanded' when every patched site holds one or the other, else None."""
    states = set()
    for at, old, new in CODE:
        here = bytes(rom[at : at + len(old) // 2]).hex()
        states.add("retail" if here == old else "expanded" if here == new else None)
    for table in TABLES:
        for site in table.sites:
            pointer = _u32(rom, site)
            states.add(
                "retail" if pointer == table.retail else "expanded" if pointer == table.at else None
            )
    return states.pop() if len(states) == 1 else None


def expanded(rom: bytes | bytearray) -> bool:
    return len(rom) > HEAP.stop and _code_state(rom) == "expanded"


def expand(rom: bytearray) -> bool:
    """Lay the cartridge out as 34 slots (see the module doc), growing it to 2
    MiB. True when it is so laid out (now or by an earlier patch); False,
    writing nothing, for an image that is neither that nor the cartridge's."""
    if expanded(rom):
        return True
    if _code_state(rom) != "retail":
        return False
    bases = [_u32(rom, TEAM_TABLE + 4 * s) for s in range(RETAIL_SLOTS)]
    if not all(b in CARTRIDGE_BLOCKS for b in bases):
        return False
    grow(rom)
    rom[TEAM_REGION.start : HEAP.stop] = b"\xff" * (HEAP.stop - TEAM_REGION.start)
    # Read before the table grows: its new entries overwrite the block at $37E.
    blocks = [bytes(rom[b : _block_end(rom, b)]) for b in bases]
    for slot, source in enumerate(SOURCE):
        block = blocks[source]
        at = TEAM_REGION.start + STRIDE * slot
        rom[at : at + len(block)] = block
        _put32(rom, TEAM_TABLE + 4 * slot, at)
    for table in TABLES:
        for slot, source in enumerate(SOURCE):
            at = table.retail + table.size * source
            new = table.at + table.size * slot
            rom[new : new + table.size] = rom[at : at + table.size]
        for site in table.sites:
            _put32(rom, site, table.at)
    for slot in range(RETAIL_SLOTS - 2, CLUB_SLOTS):
        rom[RATINGS.at + slot] = NEW_RATING
    banner_sheet = bytes(rom[BANNER_SHEET : BANNER_SHEET + _record_size(rom, BANNER_SHEET)])
    stripe_sheet = bytes(rom[STRIPE_SHEET : STRIPE_SHEET + _record_size(rom, STRIPE_SHEET)])
    banners = _frames(banner_sheet, 2)
    stripes = _frames(stripe_sheet, 2)
    palette = stripe_sheet[_u32(stripe_sheet, 0) :][:0x80]
    for at, _old, code in CODE:
        rom[at : at + len(code) // 2] = bytes.fromhex(code)
    rom[SEEDS : SEEDS + SEED_TABLES * 16] = _seeds()
    _lay_out(
        rom,
        [banners[s] for s in SOURCE],
        [stripes[s] for s in SOURCE],
        palette,
        {},
        {},
    )
    return True


def _seeds() -> bytes:
    """32 brackets of today's conferences, each conference's 16 clubs rotated
    through the tables eight at a time so every club plays in some."""
    east = sorted(slot for slot, code in SLOT_CODES.items() if CLUBS[code].east)
    west = sorted(slot for slot, code in SLOT_CODES.items() if not CLUBS[code].east)
    out = b""
    for k in range(SEED_TABLES):
        out += bytes(east[(3 * k + i) % len(east)] for i in range(8))
        out += bytes(west[(5 * k + i) % len(west)] for i in range(8))
    return out


class _Heap:
    """A bump allocator over `HEAP`, keeping every record on an even byte."""

    def __init__(self, rom: bytearray) -> None:
        self.rom = rom
        self.at = HEAP.start

    def put(self, blob: bytes) -> int:
        if self.at + len(blob) > HEAP.stop:
            raise RomError("The team identity does not fit its part of the ROM")
        self.rom[self.at : self.at + len(blob)] = blob
        start, self.at = self.at, self.at + (len(blob) + 1) & ~1
        return start


def _lay_out(
    rom: bytearray,
    banners: Sequence[Grid],
    stripes: Sequence[Grid],
    stripe_palette: bytes,
    logos: Mapping[int, bytes],
    ices: Mapping[int, bytes],
) -> None:
    """Clear `HEAP` and write into it each slot's banner records, the stripe
    sheet and the logos and center ice of `logos` and `ices` (slot to record),
    pointing every table and site at them."""
    rom[HEAP.start : HEAP.stop] = b"\xff" * len(HEAP)
    heap = _Heap(rom)
    for slot, grid in enumerate(banners):
        _put32(rom, BANNERS + 8 * slot, heap.put(_record([grid])))
        _put32(rom, BANNERS + 8 * slot + 4, heap.put(_record([_recolour(grid, AWAY)])))
    sheet = heap.put(_record(stripes, stripe_palette))
    for site in STRIPE_SITES:
        _put32(rom, site, sheet)
    for site in STRIPE_TILE_SITES:
        _put32(rom, site, sheet + 8)
    for table, records in ((LOGOS, logos), (ICE, ices)):
        for slot, record in sorted(records.items()):
            _put32(rom, table.at + 4 * slot, heap.put(record))


def write(rom: bytearray, teams: Mapping[int, Identity], status: StatusFn) -> None:
    """Write each slot's identity into an `expanded` `rom` (in place): its
    names, colours, uniforms, banners and, where it has one, its logo."""
    if not expanded(rom):
        raise RomError("The league is not laid out for 32 clubs")
    old = bytes(rom[HEAP.start : HEAP.stop])

    def read(at: int) -> bytes:
        return old[at - HEAP.start :] if at in HEAP else bytes(rom[at:])

    banners = [_frames(read(_u32(rom, BANNERS + 8 * s)), 2)[0] for s in range(SLOTS)]
    sheet = read(_u32(rom, STRIPE_SITES[0]))
    stripes = _frames(sheet, 2)
    stripe_palette = sheet[_u32(sheet, 0) :][:0x80]
    logos: dict[int, bytes] = {}
    ices: dict[int, bytes] = {}
    for table, records in ((LOGOS, logos), (ICE, ices)):
        for slot in range(SLOTS):
            at = _u32(rom, table.at + 4 * slot)
            if at in HEAP:
                record = read(at)
                size = _record_size(record, 0) if table is LOGOS else 10 + 32 * _u16(record, 8)
                records[slot] = record[:size]

    glyphs = _glyphs(rom)
    face, shadow = STRIPE_INKS
    for slot, team in sorted(teams.items()):
        if not 0 <= slot < SLOTS:
            continue
        text = banner_text(team.club)
        if text != RETAIL_NAMES[SOURCE[slot]] or slot in range(26, CLUB_SLOTS):
            if _text_width(text, glyphs) > BANNER_WIDTH - 4:
                status(f"{team.club.city}: banner name too wide; kept")
            else:
                banners[slot] = _banner(banners[slot], text, glyphs, BANNER_INKS)
        top, bottom = _bands(team)
        bands = (_nearest(top, RINK, BAND_INKS), _nearest(bottom, RINK, BAND_INKS))
        stripes[slot] = _banner(stripes[slot], text, glyphs, (face, shadow, *bands))
        _write_block(rom, slot, team, status)
        logo_words = None
        if team.logo is not None:
            base = _u32(rom, TEAM_TABLE + 4 * slot)
            home = struct.unpack_from(">16H", rom, base + 0xC)
            try:
                logos[slot], logo_words = _logo(team.logo)
                ices[slot] = _ice(team.logo, home)
            except Exception as exc:  # noqa: BLE001 - Pillow's errors share no base
                status(f"{team.club.city}: logo skipped: {exc}")
        at = LOGO_PALETTES.at + 32 * slot
        palette = _logo_palette(bytes(rom[at : at + 32]), team, logo_words)
        rom[at : at + 32] = palette
        at = BANNER_PALETTES.at + 64 * slot
        rom[at : at + 64] = _banner_palette(palette)
        if (rating := _rating(rom, slot)) is not None:
            rom[RATINGS.at + slot] = rating
    _lay_out(rom, banners, stripes, stripe_palette, logos, ices)


def _rating(rom: bytes | bytearray, slot: int) -> int | None:
    """The slot's Matchups Overall from its Best line (line 0's six players)."""
    base = _u32(rom, TEAM_TABLE + 4 * slot)
    line0 = rom[base + _u16(rom, base + 6) :][:6]
    stats: list[bytes | bytearray] = []
    at = base + _u16(rom, base)
    while (length := _u16(rom, at)) >= 3 and len(stats) < 40:
        stats.append(rom[at + length : at + length + 8])
        at += length + 8
    best = [stats[n - 1] for n in line0 if 0 < n <= len(stats)]
    if not best:
        return None
    nibbles = [
        [s[1] & 15, s[2] >> 4, s[2] & 15, s[3] >> 4, s[3] & 15, s[4] >> 4]
        + [s[5] >> 4, s[5] & 15, s[6] >> 4, s[6] & 15, s[7] >> 4, s[7] & 15]
        for s in best
    ]
    mean = sum(map(sum, nibbles)) / (12 * len(nibbles))
    return max(1, min(99, round(RATING_SLOPE * mean + RATING_BASE)))


def _write_block(rom: bytearray, slot: int, team: Identity, status: StatusFn) -> None:
    """The block's four strings, rewritten where they are, and its uniforms."""
    base = _u32(rom, TEAM_TABLE + 4 * slot)
    if base not in TEAM_REGION:
        status(f"Slot {slot}: team block not where expected; its name was kept")
        return
    club = team.club
    strings = b"".join(_string(t) for t in (club.city, team.code, club.nickname, club.arena))
    start = base + _u16(rom, base + 4)
    end = base + STRIDE
    if start + len(strings) > end:
        status(f"{club.city}: names too long for the team block; kept")
    else:
        rom[start:end] = strings.ljust(end - start, b"\xff")
    rom[base + 0xC : base + 0x4C] = _uniforms(bytes(rom[base + 0xC : base + 0x4C]), team)
