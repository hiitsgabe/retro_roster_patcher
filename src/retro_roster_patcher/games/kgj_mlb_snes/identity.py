"""Team identity: names, abbreviations, divisions, the team-select order, logos
and uniform colours.

The game numbers its teams NL first: 0-13 are the National League (ATL ... FLA),
14-27 the American League (BAL ... TOR), 28 and 29 the NL and AL All-Stars. A
club's library slot (AL first) is game team `(slot + 14) % 28`, and slots 28
and 29, Arizona and Tampa Bay (`expansion`), are the All-Stars' (`team_of`).

Everything below was found on the retail cartridge and checked in Mesen 2:

* Text, in five tables of the big team font (space, 0-9, A-Z; no punctuation):
  abbreviations (3 bytes each, the scoreboard, World Series menu), full names
  (Today's Game: the second team's name is cut after 18 characters, so a
  longer one prints as the nickname alone), city / nickname pairs (team
  select), cities (standings, 28 entries) and nicknames (the season news: 30
  plurals, 28 singulars, the two league names). Each is a table of 16-bit
  pointers into one bank; today's names need a few bytes more than the
  cartridge's, which go into 0xFF padding at the end of the same bank.
* Divisions: 28 words at $81:E13B, 0 East, 2 Central, 4 West, 0x8000 for the
  AL, read by the season standings (the 1993 two-division half after them is
  left alone).
* Team select: two menus of 16-byte cells ($84:E4C2 and $84:F08E: x, y, links,
  handlers, team word) say which team a cell picks, and a tile script at
  $86:D825 (`D1 x y` then `DF` and 25 * team) draws the logos of the grid. A
  page is put in alphabetical order by moving the team between cells in all
  three, the positions and links staying.
* Logos: resources 2218 + team (30 of them), 40x40, 4bpp, all drawn through
  one 15-colour palette, which the cartridge keeps three copies of: row 3 of
  resource 2296 (the menu palettes), resource 2256 (that row alone) and row 3
  of resource 2298 (the season schedule's palettes, row 4 the same colours
  dimmed to about 0.7). New logos get a new shared palette, so every logo
  kept, the All-Stars' included, is redrawn in it.
* Uniforms: the batter's three palettes (96 bytes; home resource 2361 + team,
  30 of them; road 2391 + team, 28) and the fielders' one (32 bytes; home 2303
  + team, 30; road 2333 + team, 28). The table of which each team wears
  ($81:89E6) gives the All-Stars their home uniforms on the road too. In the
  batter's, row 0 colours 6-8 are the cap,
  5 its bill; rows 1 and 2 hold the jersey, pants, sleeves and stripes as runs
  of four shades (row 1 colours 1, 5 and 9, row 2 colours 1, 5 and 9) and
  piping and lettering as single colours (row 1 colours 13-15, row 2 colours
  13-15). Which of those are the club's colour, which its second colour and
  which the white or grey of the uniform differs from club to club, so the
  patch reads it off the slot's own palette (`_role`). Row 1 colours 13 and
  14 are also the Line Up and Bullpen cards' name and the bar it sits on,
  which the cartridge always keeps apart. In the fielders', 10 and 11 are the
  cap and sleeves, 14 the trim, on every club; 1-3 the jersey, white or grey
  but for the All-Stars' (blue, red) and Chicago's home (black), the cap's
  colour. The fielders' palette dresses the pitcher too, on the mound and on
  the Bullpen cards (Mesen).

New logos and uniforms are stored (codec 6) in this module's part of a 4 MiB
image, 0x200000-0x2FFFFF (the ROM grows), at fixed offsets, so a patched ROM
patches again the same way. The cartridge's streams stay where they are, and a
re-patch draws the All-Stars' logos from them again (`_CARTRIDGE`).

Tables of 28 (cities, singulars, divisions, road uniforms) have no entry for
Arizona and Tampa Bay.
"""

from __future__ import annotations

import math
import re
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...core.kits import _hex_colour
from ...core.text import to_ascii
from ...formats import snes
from ..iss_snes.screens import tile_bytes, tile_indices, to_bgr555, to_rgb
from . import resources
from .models import AL_TEAMS, KGJ_TEAM_ORDER, TEAM_COUNT, KGJTeamRecord
from .rom_reader import header_size

if TYPE_CHECKING:
    from PIL import Image

    from ...sports.models import Team

#: Game teams, the two All-Star teams included.
TEAMS = 30
#: Today's Game cuts the second team's name after this many characters.
FULL_NAME_MAX = 18
#: Team select prints city and nickname on lines this long.
LINE_MAX = 24

_SIZE = 0x400000
_LOGOS_AT = 0x200000
_PALETTES_AT = _LOGOS_AT + TEAMS * 800
_CALENDAR_AT = _PALETTES_AT + 256
_UNIFORMS_AT = 0x206000

#: Code and data this module relies on, as the retail cartridge has them and
#: no patch changes them: the table addresses in the code that reads each one.
_SIGNATURE = (
    (0x7FC0, b"KEN GRIFFEY JR BASEBA"),
    (0x2A776, b"\xdb\x81"),  # $85:A774 LDA $81DBF6,X: full names
    (0x2EB45, b"\xf0\x86"),  # $85:EB43 LDA $86F03A,X: city / nickname
    (0x2993F, b"\x2c\xd0\x83"),  # $85:993E LDA $83D02C,X: cities
    (0x1F0A0, b"\xf2\xdf\x83"),  # $83:F09F LDA $83DFF2,X: nicknames
    (0x1D437, b"\x66\xc8"),  # $83:D436 LDA $C866,X: abbreviations
    (0x2AE7C, b"\x3b\xe1\x81"),  # $85:AE7B LDA $81E13B,X: divisions
    (0x28763, b"\xc2\xe4"),  # $85:8762 LDA #$E4C2: team-select menu
    (0x28B5D, b"\x8e\xf0"),  # $85:8B5C LDA #$F08E: the other one
    (0x2BB08, b"\x25\xd8"),  # $85:BB07 LDX #$D825: the grid's tile script
    (0x2AA0E, b"\x54\x11"),  # $85:AA0D ADC #$1154: logo resources
    (0x35825, b"\xd6\xd2\x00\x2c\xd1"),  # the grid script's first commands
)


def team_of(slot: int) -> int:
    """The game's team number for a library slot (and the slot of a game team)."""
    return slot if slot >= TEAM_COUNT else (slot + AL_TEAMS) % TEAM_COUNT


def known(rom: bytes | bytearray) -> bool:
    """Is `rom` (headerless) the cartridge this module was written against?"""
    return len(rom) >= 0x200000 and all(rom[at : at + len(data)] == data for at, data in _SIGNATURE)


# --- text ----------------------------------------------------------------------


def clean(text: str) -> str:
    """`text` as the team font can print it: capitals, digits and spaces."""
    upper = to_ascii(text).upper()
    kept = "".join(c for c in upper if c == " " or c.isalnum())
    return " ".join(kept.split())


def _encode(text: str) -> bytes:
    return bytes(0 if c == " " else 1 + int(c) if c.isdigit() else 0x0B + ord(c) - 65 for c in text)


#: Cities the provider does not give: it calls the Athletics just that, and
#: they play in Sacramento from 2025 to 2027.
CITY = {"ATH": "SACRAMENTO"}


def stamp(record: KGJTeamRecord, team: Team) -> None:
    """Put `team`'s identity on `record`, for `write`. A club whose slot moved
    takes its own 1994 name along (the Astros in the Brewers' slot are
    'Houston Astros')."""
    nickname = clean(team.short_name or team.name)
    city = CITY.get(team.code.upper()) or clean(clean(team.name).removesuffix(nickname))
    record.city, record.nickname = city or nickname, nickname
    record.code = clean(team.code)
    record.colors = tuple(c for c in map(_hex_colour, (team.color, team.alternate_color)) if c)
    record.team_id, record.logo_url = team.id, team.logo_url
    if record.code in _MOVED_LOGOS:
        record.name = KGJ_TEAM_ORDER[team_of(_MOVED_LOGOS[record.code][0])]


def full_name(city: str, nickname: str) -> str:
    """The Today's Game name: city and nickname, or the nickname alone when
    both run past `FULL_NAME_MAX` (LOS ANGELES DODGERS prints as DODGERS)."""
    both = f"{city} {nickname}".strip()
    return both if len(both) <= FULL_NAME_MAX else nickname[:FULL_NAME_MAX]


def singular(nickname: str) -> str:
    """One player of the club, for the season news: BRAVES -> BRAVE."""
    return nickname[:-1] if nickname.endswith("S") else nickname


@dataclass(frozen=True)
class _Table:
    pointers: int
    count: int
    #: The cartridge's strings, end to end, and the 0xFF padding at the end of
    #: the same bank that takes what does not fit.
    area: tuple[int, int]
    spill: tuple[int, int]
    terminator: int = 0xFF
    #: Strings per entry.
    parts: int = 1
    #: Pointers count from this file offset rather than being bank addresses.
    relative: int | None = None

    def at(self, rom: bytes | bytearray, i: int) -> int:
        (p,) = struct.unpack_from("<H", rom, self.pointers + 2 * i)
        if self.relative is not None:
            return self.relative + p
        return (self.pointers & ~0x7FFF) + (p & 0x7FFF)

    def read(self, rom: bytes | bytearray, i: int) -> bytes:
        """Entry `i`, terminators included."""
        start = end = self.at(rom, i)
        for _ in range(self.parts):
            end = rom.index(self.terminator, end) + 1
        return bytes(rom[start:end])

    def write(self, rom: bytearray, entries: list[bytes]) -> None:
        """Pack `entries` into the area, then the spill, and point at them."""
        pos, spill = self.area[0], self.spill[0]
        where = []
        for data in entries:
            if pos + len(data) <= self.area[1]:
                where.append(pos)
                pos += len(data)
            elif spill + len(data) <= self.spill[1]:
                where.append(spill)
                spill += len(data)
            else:
                raise ValueError(f"team names do not fit the table at 0x{self.pointers:05X}")
        rom[self.area[0] : self.area[1]] = b"\xff" * (self.area[1] - self.area[0])
        rom[self.spill[0] : self.spill[1]] = b"\xff" * (self.spill[1] - self.spill[0])
        for i, (data, at) in enumerate(zip(entries, where, strict=True)):
            rom[at : at + len(data)] = data
            p = at - self.relative if self.relative is not None else 0x8000 | (at & 0x7FFF)
            struct.pack_into("<H", rom, self.pointers + 2 * i, p)


_ABBREVIATIONS = 0xC866
_FULL = _Table(0xDBF6, 30, (0xDC32, 0xDE35), (0xF000, 0xFF00))
# The spill stops short of $86:FFE0, `expansion`'s menu string.
_CITY_NICK = _Table(0x3703A, 30, (0x37076, 0x37279), (0x37F70, 0x37FE0), parts=2)
_CITY = _Table(0x1D02C, 28, (0x1D064, 0x1D16C), (0x1FFA0, 0x1FFC0), relative=0x1D064)
_NICK = _Table(0x1DFF2, 60, (0x1EE94, 0x1F04C), (0x1FFC0, 0x1FFF0), terminator=0xD0)


def names(rom: bytes | bytearray) -> list[tuple[str, str]]:
    """(city, nickname) of every game team as team select prints them."""
    pairs = [_CITY_NICK.read(rom, t).split(b"\xff") for t in range(TEAMS)]
    return [(_decode(city), _decode(nick)) for city, nick, *_ in pairs]


def _decode(data: bytes) -> str:
    return "".join(
        " " if b == 0 else str(b - 1) if b <= 10 else chr(65 + b - 0x0B) if b <= 0x24 else "?"
        for b in data
    )


def write_names(rom: bytearray, teams: Mapping[int, tuple[str, str, str]]) -> None:
    """(city, nickname, abbreviation) for game teams 0-29, into all five
    tables, those of 28 (cities, singulars) for 0-27 only. A team left out
    keeps its text."""
    ff, d0 = b"\xff", b"\xd0"
    full = [_FULL.read(rom, t) for t in range(TEAMS)]
    city_nick = [_CITY_NICK.read(rom, t) for t in range(TEAMS)]
    city = [_CITY.read(rom, t) for t in range(TEAM_COUNT)]
    nick = [_NICK.read(rom, i) for i in range(_NICK.count)]
    for t, (c, n, code) in teams.items():
        rom[_ABBREVIATIONS + 3 * t : _ABBREVIATIONS + 3 * t + 3] = _encode(code.ljust(3)[:3])
        full[t] = _encode(full_name(c, n)) + ff
        city_nick[t] = _encode(c[:LINE_MAX]) + ff + _encode(n[:LINE_MAX]) + ff
        nick[t] = _encode(n) + d0
        if t < TEAM_COUNT:
            city[t] = _encode(c) + ff
            nick[TEAMS + t] = _encode(singular(n)) + d0
    _FULL.write(rom, full)
    _CITY_NICK.write(rom, city_nick)
    _CITY.write(rom, city)
    _NICK.write(rom, nick)


# --- divisions -----------------------------------------------------------------

_DIVISIONS = 0xE13B
_EAST, _CENTRAL, _WEST = 0, 2, 4
#: Today's divisions by provider abbreviation. The league comes from the slot.
DIVISION = {
    **dict.fromkeys(("BAL", "BOS", "NYY", "TB", "TOR"), _EAST),
    **dict.fromkeys(("CHW", "CWS", "CLE", "DET", "KC", "MIN"), _CENTRAL),
    **dict.fromkeys(("ATH", "OAK", "HOU", "LAA", "SEA", "TEX"), _WEST),
    **dict.fromkeys(("ATL", "MIA", "NYM", "PHI", "WSH"), _EAST),
    **dict.fromkeys(("CHC", "CIN", "MIL", "PIT", "STL"), _CENTRAL),
    **dict.fromkeys(("ARI", "COL", "LAD", "SD", "SF"), _WEST),
}


def write_divisions(rom: bytearray, codes: Mapping[int, str]) -> None:
    """Each club's division of today (three-division format); 28 entries, none
    for the All-Star teams."""
    for t, code in codes.items():
        if t < TEAM_COUNT and code in DIVISION:
            league = 0x8000 if t >= AL_TEAMS else 0
            struct.pack_into("<H", rom, _DIVISIONS + 2 * t, league | DIVISION[code])


# --- team select ---------------------------------------------------------------

#: The 28 team cells of each menu (16 bytes: x, y, ... team word at +12).
_MENUS = (0x264D0, 0x270BC)
#: The grid script: per page 14 cells, `D1 x y` then `DF lo hi` (logo 25 *
#: team) or `DE` (logo 0, Atlanta's, which keeps its cell), and 6 bytes of page
#: switch between the pages.
_SCRIPT = 0x35829


def _grid_script(rom: bytes | bytearray) -> dict[tuple[int, int, bool], int | None]:
    """(x, y, NL page) of each cell to the file offset of its logo word, or
    None for the `DE` cell."""
    out: dict[tuple[int, int, bool], int | None] = {}
    at = _SCRIPT
    for nl in (True, False):
        for _ in range(14):
            short = rom[at + 3] == 0xDE
            out[rom[at + 1], rom[at + 2], nl] = None if short else at + 4
            at += 4 if short else 6
        at += 6
    return out


def sort_grid(rom: bytearray) -> None:
    """Each team-select page in alphabetical order of the names in `rom`, ST
    read as SAINT, as the cartridge has it (ST LOUIS before SAN DIEGO)."""
    keys = [re.sub(r"^ST ", "SAINT ", f"{c} {n}") for c, n in names(rom)]
    script = _grid_script(rom)
    for page in (range(AL_TEAMS), range(AL_TEAMS, TEAM_COUNT)):
        nl = page.start == 0
        spots = sorted(
            (rom[at + 1], rom[at])
            for at in range(_MENUS[0], _MENUS[0] + 16 * TEAM_COUNT, 16)
            if rom[at + 12] // 2 in page
        )
        order = sorted(page, key=lambda t: keys[t])
        for y, x in spots:
            if script[x, y, nl] is None:  # the `DE` cell draws team 0 only
                order.remove(0)
                order.insert(spots.index((y, x)), 0)
        for (y, x), t in zip(spots, order, strict=True):
            for menu in _MENUS:
                for cell in range(menu, menu + 16 * TEAM_COUNT, 16):
                    if (rom[cell], rom[cell + 1]) == (x, y) and rom[cell + 12] // 2 in page:
                        struct.pack_into("<H", rom, cell + 12, 2 * t)
            word = script[x, y, nl]
            if word is not None:
                struct.pack_into("<H", rom, word, 25 * t)


# --- logos ---------------------------------------------------------------------

_LOGO = 0x1154 // 2
_MENU_PALETTES = 0x11F0 // 2
_LOGO_PALETTE = 0x11A0 // 2
#: The season schedule's palettes ($85:8B82): the logo row, then it dimmed.
_CALENDAR_PALETTES = 0x11F4 // 2
_LOGO_ROW = 3
_DIM = 0.7
LOGO_SIZE = 40
#: The cartridge's directory entries (codec, length, offset) of the logos a
#: patch redraws without a new picture, the All-Stars', and of the palette
#: they are drawn in: a re-patch draws them from these again, not from its
#: own redrawing, so it writes the same bytes.
_CARTRIDGE = {
    _LOGO + 28: (4, 626, 0x1B6639),
    _LOGO + 29: (4, 466, 0x1B68AB),
    _MENU_PALETTES: (4, 241, 0x1B878B),
}


def _logo_palette(rom: bytes | bytearray) -> list[int]:
    data = resources.load(rom, _MENU_PALETTES)[32 * _LOGO_ROW : 32 * _LOGO_ROW + 32]
    return [data[i] | data[i + 1] << 8 for i in range(0, 32, 2)]


def _tiles_of(indices: list[int]) -> bytes:
    """A 40x40 picture of palette indices as the game's 25 tiles, row-major."""
    return b"".join(
        tile_bytes(
            bytes(indices[(ty * 8 + y) * 40 + tx * 8 + x] for y in range(8) for x in range(8))
        )
        for ty in range(5)
        for tx in range(5)
    )


def logo_indices(rom: bytes | bytearray, t: int) -> list[int]:
    """Game team `t`'s logo as 1600 palette indices, row by row."""
    data = resources.load(rom, _LOGO + t)
    out = [0] * 1600
    for tile in range(25):
        pixels = tile_indices(data[32 * tile : 32 * tile + 32])
        for i, v in enumerate(pixels):
            out[(tile // 5 * 8 + i // 8) * 40 + tile % 5 * 8 + i % 8] = v
    return out


def render_logo(rom: bytes | bytearray, t: int) -> Image.Image:
    """Game team `t`'s logo as the ROM draws it, transparent where it is."""
    from PIL import Image

    palette = _logo_palette(rom)
    image = Image.new("RGBA", (LOGO_SIZE, LOGO_SIZE))
    image.putdata([(*to_rgb(palette[v]), 255) if v else (0, 0, 0, 0) for v in logo_indices(rom, t)])
    return image


def fit_logo(png: Image.Image) -> Image.Image:
    """A logo cropped to its opaque pixels and fitted, centred, in 38x38 of a
    transparent 40x40."""
    from PIL import Image

    source = png.convert("RGBA")
    box = source.getchannel("A").getbbox()
    if box is not None:
        source = source.crop(box)
    scale = (LOGO_SIZE - 2) / max(source.size)
    inner = source.resize(
        (max(1, round(source.width * scale)), max(1, round(source.height * scale))),
        Image.Resampling.LANCZOS,
    )
    out = Image.new("RGBA", (LOGO_SIZE, LOGO_SIZE))
    out.alpha_composite(inner, ((LOGO_SIZE - inner.width) // 2, (LOGO_SIZE - inner.height) // 2))
    return out


def _rgba(image: Image.Image) -> list[tuple[int, int, int, int]]:
    data = image.convert("RGBA").tobytes()
    return [(data[i], data[i + 1], data[i + 2], data[i + 3]) for i in range(0, len(data), 4)]


#: The cartridge's own logo of each club whose slot moved: its game team on
#: the cartridge, and the directory entry (codec, length, offset) of the logo.
_MOVED_LOGOS = {"HOU": (3, (4, 0x1FC, 0x1B3B6B)), "MIL": (21, (4, 0x1CC, 0x1B5A27))}


def keep_logos(rom: bytearray, codes: Mapping[int, str]) -> None:
    """A club whose slot moved takes its cartridge logo along (Milwaukee's
    slot was Houston's), unless a patch has redrawn the logos already."""
    if resources.entry(rom, _MENU_PALETTES)[2] >= _LOGOS_AT:
        return
    for t, code in codes.items():
        home, logo = _MOVED_LOGOS.get(code, (t, None))
        if logo is not None and home != t:
            resources.set_entry(rom, _LOGO + t, *logo)


def write_logos(rom: bytearray, pictures: Mapping[int, Image.Image]) -> None:
    """New logos for game teams, one 15-colour palette over them and every logo
    kept (redrawn in it), written into all three copies of the logo palette.
    `rom` must be 4 MiB."""
    from PIL import Image

    # ponytail: only the All-Stars come from the cartridge again; a club that
    # gets no picture on a re-patch is redrawn from its patched logo and can
    # drift a shade. Add its entries to `_CARTRIDGE` if that ever shows.
    cartridge: bytes | bytearray = rom
    if resources.entry(rom, _MENU_PALETTES)[2] >= _LOGOS_AT:  # patched before
        cartridge = bytearray(rom)
        for i, found in _CARTRIDGE.items():
            resources.set_entry(cartridge, i, *found)
    logos = [
        fit_logo(pictures[t])
        if t in pictures
        else render_logo(cartridge if _LOGO + t in _CARTRIDGE else rom, t)
        for t in range(TEAMS)
    ]
    pixels = [_rgba(logo) for logo in logos]
    opaque = [p[:3] for logo in pixels for p in logo if p[3] >= 128]
    strip = Image.new("RGB", (len(opaque), 1))
    strip.putdata(opaque)
    quantised = strip.quantize(
        colors=15, method=Image.Quantize.MEDIANCUT, kmeans=8, dither=Image.Dither.NONE
    )
    flat = (quantised.getpalette() or [])[:45]
    words = [to_bgr555(tuple(flat[i : i + 3])) for i in range(0, len(flat), 3)]
    words += [words[-1]] * (15 - len(words))
    shown = [to_rgb(w) for w in words]  # what the SNES draws

    def nearest(rgb: tuple[int, ...]) -> int:
        return 1 + min(
            range(15), key=lambda i: sum((a - b) ** 2 for a, b in zip(rgb, shown[i], strict=False))
        )

    at = _LOGOS_AT
    for t, logo in enumerate(pixels):
        indices = [nearest(p[:3]) if p[3] >= 128 else 0 for p in logo]
        at = resources.store(rom, _LOGO + t, _tiles_of(indices), at)
    row = b"".join(w.to_bytes(2, "little") for w in words)
    menus = bytearray(resources.load(rom, _MENU_PALETTES).ljust(256, b"\0")[:256])
    menus[32 * _LOGO_ROW + 2 : 32 * _LOGO_ROW + 32] = row
    resources.store(rom, _MENU_PALETTES, bytes(menus), _PALETTES_AT)
    codec, length, own = resources.entry(rom, _LOGO_PALETTE)
    if codec == resources.STORED and length >= 32:
        rom[own + 2 : own + 32] = row
    dim = b"".join(
        sum(round((w >> s & 31) * _DIM) << s for s in (0, 5, 10)).to_bytes(2, "little")
        for w in words
    )
    calendar = bytearray(resources.load(rom, _CALENDAR_PALETTES).ljust(160, b"\0")[:160])
    calendar[32 * _LOGO_ROW + 2 : 32 * _LOGO_ROW + 32] = row
    calendar[32 * _LOGO_ROW + 34 : 32 * _LOGO_ROW + 64] = dim
    resources.store(rom, _CALENDAR_PALETTES, bytes(calendar), _CALENDAR_AT)


# --- uniforms ------------------------------------------------------------------

_BATTER_HOME = 0x1272 // 2
_BATTER_ROAD = 0x12AE // 2
_FIELDER_HOME = 0x11FE // 2
_FIELDER_ROAD = 0x123A // 2
#: Colour numbers (row * 16 + colour), light to dark.
_BATTER_CAP = (6, 7, 8)
#: The batter's runs of four shades and single colours that may be the club's.
_BATTER_RUNS = tuple(tuple(range(n, n + 4)) for n in (17, 21, 25, 33, 37, 41))
_BATTER_SINGLES = (5, 29, 30, 31, 45, 46, 47)
_FIELDER_CAP = (10, 11)
_FIELDER_TRIM = (14,)
_FIELDER_JERSEY = (1, 2, 3)
#: The Line Up and Bullpen cards print the player's name in the batter's
#: colour 29 on a bar of colour 30 (the home uniform's; Mesen, marker colours).
_NAME, _BAR = 29, 30
#: How far apart (RGB, as drawn) the name and its bar must be. The cartridge
#: keeps every club's at least 105 apart (Seattle's navy on teal), some by hue
#: alone (the Cubs' road blue on red), so it is a distance, not a luminance.
_LEGIBLE = 100
_WHITE, _BLACK = 0x7FFF, 0

RGB = tuple[int, int, int]


def _shades(colour: RGB, n: int) -> list[int]:
    """`n` shades of `colour`, lightest first: `colour` itself first for one or
    two (as the cartridge's fielders have them), second for three or four. A
    black or near-black colour ramps up toward grey, as the cartridge's black
    caps do ((8, 8, 8), (4, 4, 4), (0, 0, 0)), rather than staying flat."""
    steps = (1.3, 1.0, 0.75, 0.6)[n <= 2 :][:n]
    lift = 2 * max(0, 50 - max(colour))
    return [
        to_bgr555(tuple(min(255, round(c * f + lift * (f - 0.6))) for c in colour)) for f in steps
    ]


def _light(colour: RGB) -> bool:
    """White, silver or a pale grey: lost on a white or grey jersey. Gold is not."""
    r, g, b = colour
    return 0.299 * r + 0.587 * g + 0.114 * b > 190 and max(colour) - min(colour) < 60


def _rgb5(word: int) -> RGB:
    return word & 31, word >> 5 & 31, word >> 10 & 31


def _apart(a: RGB, b: RGB) -> int:
    return max(abs(x - y) for x, y in zip(a, b, strict=True))


def _legible(text: int, ground: int) -> bool:
    return math.dist(to_rgb(text), to_rgb(ground)) >= _LEGIBLE


def _role(colour: RGB, cap: list[RGB]) -> bool | None:
    """What a colour of the cartridge's batter (5-bit channels) is on its club:
    True for the cap's colour (near one of its three shades), False for a
    second colour, None for white or grey (low saturation, not near black),
    the jersey and pants, which a patch leaves alone."""
    top = max(colour)
    if top >= 12 and top - min(colour) <= top // 4:
        return None
    return min(_apart(colour, shade) for shade in cap) <= 8


def uniform(batter: bytes, fielder: bytes, primary: RGB, alternate: RGB) -> tuple[bytes, bytes]:
    """A team's batter and fielder palettes recoloured. The batter's cap takes
    `primary`; each other colour of the cartridge's that is the cap's colour
    takes `primary` too, each that is a second colour (stripes, the bill,
    piping) takes `alternate`, and the whites and greys stay (`_role`); a run
    of four shades goes by its lightest. The fielders' cap takes `primary`,
    their trim `alternate`, their jersey `primary` when it is the cap's
    colour (the All-Stars'). `alternate` gives way to `primary` when it is
    white or silver, which a white or grey jersey would hide. Colours 29 and
    30, the cards' name and its bar, end up `_LEGIBLE` apart."""
    trim = primary if _light(alternate) else alternate
    words = struct.unpack("<48H", batter)
    old = [_rgb5(w) for w in words]
    cap = [old[n] for n in _BATTER_CAP]
    caps, trims = _shades(primary, 3), _shades(trim, 4)

    def role(n: int, trim_word: int) -> bool | None:
        # A colour that is the trim already (a patch before) stays the trim,
        # however near the cap it is.
        return False if words[n] == trim_word else _role(old[n], cap)

    bat = dict(zip(_BATTER_CAP, caps, strict=True))
    for run in _BATTER_RUNS:
        is_cap = role(run[0], trims[0])
        if is_cap is not None:
            bat.update(zip(run, _shades(primary, 4) if is_cap else trims, strict=True))
    for n in _BATTER_SINGLES:
        is_cap = role(n, trims[1])
        if is_cap:  # the shade of the cap it is nearest to
            bat[n] = caps[min(range(3), key=lambda k: _apart(old[n], cap[k]))]
        elif is_cap is not None:
            bat[n] = trims[1]
    # The card's name is lost on its bar when both turn the same colour (the
    # Angels' silver gives way to red; St. Louis's gold and navy are both a
    # second colour): it takes the cap's, the trim, white or black instead,
    # the first that stands out. A re-patch picks the same.
    bar = bat.get(_BAR, words[_BAR])
    if not _legible(bat.get(_NAME, words[_NAME]), bar):
        bat[_NAME] = next(c for c in (caps[1], trims[1], _WHITE, _BLACK) if _legible(c, bar))
    fld = dict(zip(_FIELDER_CAP, _shades(primary, 2), strict=True))
    fld.update(zip(_FIELDER_TRIM, _shades(trim, 1), strict=True))
    # A jersey of the cap's colour (the batter's cap: the fielders' own is two
    # shades and a re-patch's lightest jersey shade can fall outside it) takes
    # `primary`, as the batter's does.
    # ponytail: no cartridge fielder wears a second colour, so that stays.
    jersey = struct.unpack_from("<H", fielder, 2 * _FIELDER_JERSEY[0])[0]
    if jersey != trims[0] and _role(_rgb5(jersey), cap):
        fld.update(zip(_FIELDER_JERSEY, _shades(primary, 4), strict=False))
    out = []
    for palette, new in ((batter, bat), (fielder, fld)):
        data = bytearray(palette)
        for n, word in new.items():
            struct.pack_into("<H", data, 2 * n, word)
        out.append(bytes(data))
    return out[0], out[1]


def write_uniforms(rom: bytearray, colours: Mapping[int, tuple[RGB, RGB]]) -> None:
    """Home and road uniforms of game teams 0-27, home of 28 and 29 (which
    wear them on the road too). `rom` must be 4 MiB."""
    for t, (primary, alternate) in colours.items():
        at = _UNIFORMS_AT + 256 * t
        sets = ((_BATTER_HOME, _FIELDER_HOME), (_BATTER_ROAD, _FIELDER_ROAD))
        for batter, fielder in sets[: 2 if t < TEAM_COUNT else 1]:
            bat, fld = uniform(
                resources.load(rom, batter + t),
                resources.load(rom, fielder + t),
                primary,
                alternate,
            )
            at = resources.store(rom, batter + t, bat, at)
            at = resources.store(rom, fielder + t, fld, at)


# --- the whole patch -----------------------------------------------------------


def write(
    rom: bytearray,
    teams: Mapping[int, KGJTeamRecord],
    pictures: Mapping[int, Image.Image],
) -> list[str]:
    """Each mapped slot's names, division, logo (from `pictures`, by slot) and
    uniform colours, and the team-select pages re-sorted, in `rom` (headerless);
    status notes. The caller fixes the header checksum."""
    named = {team_of(s): r for s, r in teams.items() if 0 <= s < TEAMS and r.code}
    coloured = {t: (r.colors[0], r.colors[-1]) for t, r in named.items() if r.colors}
    drawn = {team_of(s): p for s, p in pictures.items() if 0 <= s < TEAMS}
    if not (named or drawn):
        return []
    if not known(rom):
        return ["Team names, logos and colours left alone: not the cartridge this patcher knows"]
    if named:
        write_names(rom, {t: (r.city, r.nickname, r.code) for t, r in named.items()})
        write_divisions(rom, {t: r.code for t, r in named.items()})
        sort_grid(rom)
        keep_logos(rom, {t: r.code for t, r in named.items()})
    if coloured or drawn:
        snes.grow(rom, _SIZE)
    if drawn:
        write_logos(rom, drawn)
    write_uniforms(rom, coloured)
    return []


def draw(
    data: bytearray, teams: Mapping[int, KGJTeamRecord], pictures: Mapping[int, Image.Image]
) -> list[str]:
    """`write` on a whole file's bytes, copier header or not, in place."""
    base = header_size(len(data))
    body = data[base:]
    notes = write(body, teams, pictures)
    data[base:] = body
    return notes
