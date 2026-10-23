"""Add what `games/kgj_mlb_snes/identity.py` edits to a synthetic KGJ image.

Nothing here comes from a real ROM: the names are CITYnn / NICKnn, the logos
and colours are patterns. The places are the cartridge's (headerless file
offsets), transcribed here rather than imported, so moving one in the module
breaks a test:

* the code operands `identity.known` checks;
* five text tables: 16-bit pointers, then strings packed from the start of the
  cartridge's string area (abbreviations: 3 bytes each);
* the 3-division table, the two team-select menus' 28 team cells and the grid
  script (`D1 x y`, then `DE` for team 0 or `DF` and 25 * team);
* resource directory entries for the 30 logos, the menu palettes, the logo
  palette, the season schedule's palettes and the four uniform sets, their
  streams at `STREAMS_AT`, some stored (codec 6), some in the tile codec's
  plain units (codec 4), the schedule's in the word RLE (codec 8).

Game team t is library slot (t + 14) % 28.
"""

from __future__ import annotations

import struct

TEAMS = 30
TITLE = (0x7FC0, b"KEN GRIFFEY JR BASEBA")
CODE = (
    (0x2A776, b"\xdb\x81"),
    (0x2EB45, b"\xf0\x86"),
    (0x2993F, b"\x2c\xd0\x83"),
    (0x1F0A0, b"\xf2\xdf\x83"),
    (0x1D437, b"\x66\xc8"),
    (0x2AE7C, b"\x3b\xe1\x81"),
    (0x28763, b"\xc2\xe4"),
    (0x28B5D, b"\x8e\xf0"),
    (0x2BB08, b"\x25\xd8"),
    (0x2AA0E, b"\x54\x11"),
)

ABBREVIATIONS = 0xC866
#: (pointer table, entries, string area, end-of-bank spill area)
FULL = (0xDBF6, 30, (0xDC32, 0xDE35), (0xF000, 0xFF00))
CITY_NICK = (0x3703A, 30, (0x37076, 0x37279), (0x37F70, 0x37FE0))
CITY = (0x1D02C, 28, (0x1D064, 0x1D16C), (0x1FFA0, 0x1FFC0))
CITY_BASE = 0x1D064  # the city table's pointers count from here
NICK = (0x1DFF2, 60, (0x1EE94, 0x1F04C), (0x1FFC0, 0x1FFF0))
DIVISIONS = 0xE13B
MENUS = (0x264D0, 0x270BC)
SCRIPT = 0x35825

#: Directory: codec words, stream offsets (from file 0x78000), lengths.
DIR_CODEC, DIR_OFFSET, DIR_LENGTH, DIR_BASE = 0x30000, 0x31460, 0x33D20, 0x78000
LOGO = 2218
MENU_PALETTES = 2296
LOGO_PALETTE = 2256
CALENDAR_PALETTES = 2298
BATTER_HOME, BATTER_ROAD = 2361, 2391
FIELDER_HOME, FIELDER_ROAD = 2303, 2333
STREAMS_AT = 0x1C0000

#: The grid: per page, (x, y) in reading order. NL page then AL page.
NL_CELLS = [(x, y) for y in (10, 16, 22) for x in (1, 7, 13, 19, 25)][:14]
AL_CELLS = [(x, y) for y in (10, 16, 22) for x in (2, 8, 14, 20, 26)][:10] + [
    (8, 22),
    (14, 22),
    (20, 22),
    (26, 22),
]
#: The team each cell holds on the synthetic cartridge: not alphabetical.
NL_ORDER = [0, 1, 2, 12, 13, 3, 4, 5, 6, 11, 7, 8, 9, 10]
AL_ORDER = [14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27]


def encode(text: str) -> bytes:
    """The team font: space 0, digits from 1, A-Z from 0x0B."""
    return bytes(0 if c == " " else 1 + int(c) if c.isdigit() else 0x0B + ord(c) - 65 for c in text)


def decode(data: bytes) -> str:
    return "".join(" " if b == 0 else str(b - 1) if b <= 10 else chr(65 + b - 0x0B) for b in data)


def city(t: int) -> str:
    return "NATIONAL" if t == 28 else "AMERICAN" if t == 29 else f"CITY{t:02d}"


def nick(t: int) -> str:
    return "ALL STARS" if t >= 28 else f"NICK{t:02d}"


def _bank(at: int) -> int:
    return 0x8000 | (at & 0x7FFF)


def _strings(body: bytearray, table: tuple, entries: list[bytes], *, base: int | None = None):
    pointers, count, (start, _end), _spill = table
    at = start
    for i, data in enumerate(entries[:count]):
        body[at : at + len(data)] = data
        struct.pack_into("<H", body, pointers + 2 * i, at - base if base is not None else _bank(at))
        at += len(data)


def string_at(body: bytes | bytearray, table: tuple, i: int, *, base: int | None = None) -> int:
    """File offset of entry `i` of `table`, resolved as the game does."""
    (p,) = struct.unpack_from("<H", body, table[0] + 2 * i)
    return base + p if base is not None else (table[0] & ~0x7FFF) + (p & 0x7FFF)


def read_string(body: bytes | bytearray, table: tuple, i: int, *, base=None, term=0xFF) -> str:
    at = string_at(body, table, i, base=base)
    return decode(bytes(body[at : body.index(term, at)]))


def logo_pixels(t: int) -> list[int]:
    """Team t's synthetic 40x40 logo as palette indices: a frame of colour 0
    around a block of two colours that depend on t."""
    return [
        0 if min(x, y, 39 - x, 39 - y) < 4 else 1 + (t + (x // 10 + y // 10) % 2) % 15
        for y in range(40)
        for x in range(40)
    ]


def tile_bytes(indices: list[int]) -> bytes:
    """40x40 indices as 25 row-major 4bpp tiles (planes 0/1 then 2/3 per row)."""
    out = bytearray()
    for ty in range(5):
        for tx in range(5):
            tile = bytearray(32)
            for y in range(8):
                for x in range(8):
                    v = indices[(ty * 8 + y) * 40 + tx * 8 + x]
                    for plane in range(4):
                        if v >> plane & 1:
                            tile[(plane // 2) * 16 + 2 * y + plane % 2] |= 0x80 >> x
            out += tile
    return bytes(out)


def plain_units(data: bytes) -> bytes:
    """The tile codec with nothing but plain units: command 0, 16 literals."""
    return b"".join(b"\x00" + data[i : i + 16] for i in range(0, len(data), 16))


def menu_palettes() -> bytes:
    """8 rows of 16 colours; row 3 is the logos', colour n = n * 0x0421."""
    return b"".join(
        struct.pack("<H", n * 0x421 if row == 3 else 0x1000 + row * 16 + n)
        for row in range(8)
        for n in range(16)
    )


def calendar_palettes() -> bytes:
    """The season schedule's 5 rows: row 3 the logos' (as `menu_palettes`), row
    4 those at half brightness, the others different colours."""
    return b"".join(
        struct.pack(
            "<H", n * 0x421 if row == 3 else n // 2 * 0x421 if row == 4 else 0x2000 + row * 16 + n
        )
        for row in range(5)
        for n in range(16)
    )


#: The synthetic batter's club colours (5-bit RGB): a navy cap (6-8), sleeves
#: (25-28) and piping (29, 47), a red bill (5), stripes (37-40) and piping (45),
#: a gold accent (31), white trim (30, 46). Every other colour is a grey.
BATTER_DESIGN = {
    5: (19, 0, 0),
    6: (2, 2, 16),
    7: (0, 0, 11),
    8: (0, 0, 6),
    **{25 + k: (5 - k, 6 - k, 21 - 3 * k) for k in range(4)},
    29: (0, 0, 11),
    30: (31, 31, 31),
    31: (25, 23, 0),
    **{37 + k: (21 - 2 * k, 0, 0) for k in range(4)},
    45: (20, 0, 0),
    46: (31, 31, 31),
    47: (0, 0, 6),
}


def batter(t: int, road: bool) -> bytes:
    """48 colours: `BATTER_DESIGN`, greys that depend on t and road elsewhere."""
    words = []
    for i in range(48):
        r, g, b = BATTER_DESIGN.get(i) or (12 + (i + t + 3 * road) % 20,) * 3
        words.append(r | g << 5 | b << 10)
    return struct.pack("<48H", *words)


def fielder(t: int, road: bool) -> bytes:
    """16 colours that depend on t and road; the jersey (1-3) a grey, as the
    cartridge has it, but the All-Stars' in the batter's cap colour (blue, red)."""
    words = [(t * 32 + i + (0x4000 if road else 0x6000)) & 0x7FFF for i in range(16)]
    words[1:4] = ((12 + (i + t + 3 * road) % 20) * 0x421 for i in range(1, 4))
    if t >= 28:
        words[1:4] = (r | g << 5 | b << 10 for r, g, b in (BATTER_DESIGN[n] for n in (6, 7, 8)))
    return struct.pack("<16H", *words)


def set_entry(body: bytearray, i: int, codec: int, data: bytes, at: int) -> int:
    body[at : at + len(data)] = data
    struct.pack_into("<H", body, DIR_CODEC + 2 * i, codec)
    struct.pack_into("<I", body, DIR_OFFSET + 4 * i, at - DIR_BASE)
    struct.pack_into("<H", body, DIR_LENGTH + 2 * i, len(data))
    return at + len(data)


def add_identity(body: bytearray) -> bytearray:
    """`body` (headerless, 2 MiB) with the identity structures planted."""
    for at, data in (TITLE, *CODE):
        body[at : at + len(data)] = data
    for t in range(TEAMS):
        body[ABBREVIATIONS + 3 * t : ABBREVIATIONS + 3 * t + 3] = encode(f"A{t:02d}")
    ff, d0 = b"\xff", b"\xd0"
    _strings(body, FULL, [encode(f"{city(t)} {nick(t)}") + ff for t in range(TEAMS)])
    _strings(body, CITY_NICK, [encode(city(t)) + ff + encode(nick(t)) + ff for t in range(TEAMS)])
    _strings(body, CITY, [encode(city(t)) + ff for t in range(28)], base=CITY_BASE)
    nicks = [encode(nick(t)) + d0 for t in range(TEAMS)]
    nicks += [encode(nick(t)[:-1]) + d0 for t in range(28)]
    nicks += [encode("LEAGUE N") + d0, encode("LEAGUE A") + d0]
    _strings(body, NICK, nicks)
    for k in range(56):
        struct.pack_into("<H", body, DIVISIONS + 2 * k, 0x0006)
    for menu in MENUS:
        for i, ((x, y), t) in enumerate(zip(NL_CELLS + AL_CELLS, NL_ORDER + AL_ORDER, strict=True)):
            cell = menu + 16 * i
            body[cell : cell + 12] = bytes([x, y]) + bytes(10)
            struct.pack_into("<HH", body, cell + 12, 2 * t, 0)
    script = bytearray(b"\xd6\xd2\x00\x2c")
    for cells, order in ((NL_CELLS, NL_ORDER), (AL_CELLS, AL_ORDER)):
        for (x, y), t in zip(cells, order, strict=True):
            script += bytes([0xD1, x, y]) + (
                b"\xde" if t == 0 else b"\xdf" + struct.pack("<H", 25 * t)
            )
        script += b"\xd4\x00\x10\x7f\x00\xc0" if cells is NL_CELLS else b"\xf3"
    body[SCRIPT : SCRIPT + len(script)] = script
    at = STREAMS_AT
    for t in range(TEAMS):
        data = tile_bytes(logo_pixels(t))
        at = set_entry(body, LOGO + t, 4 if t % 2 else 6, plain_units(data) if t % 2 else data, at)
    at = set_entry(body, MENU_PALETTES, 4, plain_units(menu_palettes()), at)
    at = set_entry(body, LOGO_PALETTE, 6, menu_palettes()[96:128], at)
    # The word RLE (codec 8) with escape pair AA BB and only literal words.
    at = set_entry(body, CALENDAR_PALETTES, 8, b"\xaa\xbb" + calendar_palettes(), at)
    for t in range(TEAMS):
        for entry, data, road in (
            (BATTER_HOME, batter(t, False), False),
            (FIELDER_HOME, fielder(t, False), False),
            (BATTER_ROAD, batter(t, True), True),
            (FIELDER_ROAD, fielder(t, True), True),
        ):
            if road and t >= 28:
                continue
            at = set_entry(
                body,
                entry + t,
                4 if t % 3 == 0 else 6,
                plain_units(data) if t % 3 == 0 else data,
                at,
            )
    return body
