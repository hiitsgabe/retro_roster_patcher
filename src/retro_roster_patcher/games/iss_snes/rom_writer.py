"""ISS SNES ROM writer -- patches player and team data into a copy of the ROM.

Offset constants sourced from:
  https://github.com/rodmguerra/issparser (ISS Studio Java editor)
  https://github.com/EstebanFuentealba/web-iss-studio (web port)

ISS (International Superstar Soccer, 1994, SNES) uses a standard .sfc ROM.
27 teams x 15 players = 405 players total.

Player names use ISS custom character encoding (not ASCII).
Player data (nine ability fields, shirt number, hair, palette) is a 6-byte
record; `models.ISSPlayerAttributes` states its layout.
Kit colors use SNES BGR555 (15-bit, little-endian).

This is not a field writer with a table of offsets. Four things in it are ROM
hacking:

**It patches 65816 machine code.** `write_name_tiles` moves the in-game team-name
tile blobs out of bank $89 into a free `0xFF` region at 0x17680, then overwrites
one byte at each of the ten addresses in `_DISPLACEMENT_PATCH_POINTS` so the code
that loads them reads bank $82 instead. Those addresses are operand bytes of one
exact build, they are never read before being written, and nothing in this
package checks the ROM's version, so another revision is patched just as
willingly and crashes on the team-select screen.

**It compresses.** `_konami_compress_literal` emits the game's own format -- a
2-byte little-endian total length, then `0x80 | count` control bytes each
followed by up to 31 literal bytes -- but only the literal command, never a back
reference, so its output is *larger* than the data the game shipped. That is why
the name-tile blobs are relocated rather than written back in place. The flags
need every byte, so they go through `konami.compress`, the full format.

**It repacks the flags.** `write_flags` rewrites bank $89's flag room, which
holds the name tiles too until `write_name_tiles` moves them out, so that runs
first. Each team's flag is two compressed 24x8 halves; the unpatched teams keep
their own blobs, shared ones are stored once, and a crest that does not fit is
simplified, then replaced, until everything does.

**It rewrites three pointer tables in three different encodings.**
`_encode_p40000` and `_encode_p17000` bias the high byte by 0x80, because their
targets are read through a SNES address with bit 15 set; `_encode_p48000` does
not, because the flag-tile table stores a raw 16-bit offset from 0x40000. They
are not interchangeable; each serves exactly one table.

**It renders a bitmap font.** `_TILE_FONT` is hand-written pixels,
`_render_name_tiles` lays them proportionally into an 8x32 grid with a one-pixel
gap it drops when the name is too wide, and `_serialize_2bpp` interleaves that
into four 8x8 SNES tiles. A second, unrelated font -- `_char_top_tile` /
`_char_bottom_tile` plus `_NAME_CHAR_WIDTHS` -- drives the *selection screen*
name, a display list of tile ids and x offsets rather than a bitmap.
"""

from __future__ import annotations

import math
import os
import shutil
import struct
from collections import Counter
from collections.abc import Iterable
from itertools import permutations
from types import TracebackType
from typing import IO

from ...core.errors import RomError
from ...core.text import to_ascii as _to_ascii
from .konami import compress, decompress
from .models import (
    PLAYERS_PER_TEAM,
    TEAM_ENUM_ORDER,
    TOTAL_TEAMS,
    Flag,
    ISSPlayerRecord,
    ISSTeamRecord,
    name_storage_index,
)

# Offsets below are absolute and headerless.

# Player names: 8 bytes per player, stored in TEAM_NAME_ORDER
_OFS_PLAYER_NAMES = 0x3B62C
_PLAYER_NAME_LENGTH = 8

# Player data: 6 bytes per player, stored in TEAM_ENUM_ORDER. Layout in
# `models.ISSPlayerAttributes`.
_OFS_PLAYER_DATA = 0x387EC
_PLAYER_DATA_LENGTH = 6

# Uniform / kit colors
# An outfield kit record is a 0x001D header and 15 BGR555 colours, OBJ palette
# entries 1-15: hair, five skin shades, three shirt shades (body and sleeves
# alike), three shorts shades, two socks shades and the shadow. The game finds
# them through the tables at 0x8C94 (home) and 0x8CCC (away). The offsets below
# are record + 14, the shirt: of the 32 bytes from there, 16 are the kit,
# then this team's shadow, the next record's header and the next team's hair
# and skin, which is why the kit writers stop at 16.
# Range 1 (19 teams): Germany, Italy, Holland, Spain, England, France, Sweden,
#   Ireland, Belgium, Romania, Bulgaria, Argentina, Brazil, Colombia, Mexico,
#   U.S.A., Nigeria, Cameroon, Super Star
# Range 2 (8 teams): Russia, Scotland, S.Korea, Wales, Norway, Switz, Denmark, Austria
_OFS_KIT1_RANGE1 = 0x2EA3B  # 1st kit, range 1 (19 teams, 32 bytes each)
_OFS_KIT1_RANGE2 = 0x2F0EB  # 1st kit, range 2 (8 teams, 32 bytes each)
_OFS_KIT2_RANGE1 = 0x2ECBB  # 2nd kit, range 1 (19 teams, 32 bytes each)
_OFS_KIT2_RANGE2 = 0x2F1EB  # 2nd kit, range 2 (8 teams, 32 bytes each)
_OFS_GK_RANGE1 = 0x2EF37  # GK kit, range 1 (18 teams, 24 bytes each)
_OFS_GK_RANGE2 = 0x2F2E7  # GK kit, range 2 (8 teams, 24 bytes each)
_OUTFIELD_KIT_STRIDE = 32
_GK_KIT_STRIDE = 24

# This team's hair and skin colours sit 12 bytes before its kit. Nothing here
# writes them.
_OFS_HAIR_SKIN1_RANGE1 = 0x2EA2F  # = 0x2EA3B - 12
_OFS_HAIR_SKIN1_RANGE2 = 0x2F0DF  # = 0x2F0EB - 12
_OFS_HAIR_SKIN2_RANGE1 = 0x2ECAF  # = 0x2ECBB - 12
_OFS_HAIR_SKIN2_RANGE2 = 0x2F1DF  # = 0x2F1EB - 12

# Flag tile pointer table: 4 bytes per team (top half, bottom half), P48000 format
_OFS_FLAG_TILE_PTRS = 0x941A  # 27 entries, 4 bytes each
_FLAG_TILE_PTR_STRIDE = 4
#: Bank $89's flag room: the cartridge's flags (0x48000-0x483FD) and then the
#: name tiles (0x483FE-0x48A7F), which `write_name_tiles` moves out. Other
#: compressed data starts at 0x48A80.
_FLAG_ROOM_START = 0x48000
_FLAG_ROOM_END = 0x48A80
FLAG_ROOM_SIZE = _FLAG_ROOM_END - _FLAG_ROOM_START

#: $87:9D78: each team's flag cell on the screens that load every flag at once
#: ($80:F4EF -- team select, the cup's group table, the league's fixtures), as
#: a VRAM word offset from $6000. The cartridge gives eight teams $FFFF and has
#: them borrow a partner's cell, so there Holland shows Germany's tiles in
#: Holland's colours: harmless while both are flags of three bands, wrong once
#: either is a crest.
_OFS_FLAG_CELLS = 0x39D78
#: $87:9DE4: each team's pointer to its two-sprite record in bank $88. Bytes 3
#: and 7 of a record are the low bytes of its two 16x16 sprites' tiles.
_OFS_FLAG_SPRITES = 0x39DE4
_BANK_88 = 0x40000
#: The eight borrowers' own cells, as OBJ tiles of the second name table. Seen in
#: Mesen 2: on every preload screen these are drawn by no sprite and written by
#: nothing but $80:F4EF. Other screens do reuse them: the title (0x10x), a match
#: (0x1Ax), and the pre-match Team Colors screen, whose preview players are drawn
#: from OBJ tiles 0x102-0x117, over the Belgium, Austria and Russia cells. Each
#: preload screen runs $80:F4EF again on entry (Team Colors -> Ranking does), so
#: that sharing is harmless; a cell moved into a range a preload screen does not
#: reload would not be.
_OWN_FLAG_CELLS = {
    "Holland": 0x1A1,
    "England": 0x1A4,
    "Denmark": 0x1A7,
    "Norway": 0x1AA,
    "Ireland": 0x1AD,
    "Belgium": 0x103,
    "Austria": 0x106,
    "Russia": 0x109,
}

# Flag colors: 4 colors x 2 bytes BGR555 per team, 10-byte step, behind a
# 2-byte header -- the team colour records $81:9486 points to. Entry 0 is OBJ
# palette entry 12, which is also the colour of the team's shirt numbers in a
# match, on both kits (seen in Mesen 2): see `number_first`.
_OFS_FLAG_COLORS_RANGE1 = 0x2DD91  # 18 teams, step 10
_OFS_FLAG_COLORS_RANGE2 = 0x2DE4F  # 9 teams, step 10
_FLAG_COLORS_STEP = 10

_FLAG_COLORS_RANGE1_TEAMS = [
    "Germany",
    "England",
    "Italy",
    "Holland",
    "France",
    "Spain",
    "Belgium",
    "Ireland",
    "Colombia",
    "Brazil",
    "Argentina",
    "Mexico",
    "Nigeria",
    "Cameroon",
    "U.S.A.",
    "Bulgaria",
    "Romania",
    "Sweden",
]
_FLAG_COLORS_RANGE2_TEAMS = [
    "Scotland",
    "S.Korea",
    "Super Star",
    "Russia",
    "Switz",
    "Denmark",
    "Austria",
    "Wales",
    "Norway",
]

# Predominant color byte: the clash rule. When two teams' bytes are equal the
# second wears its away kit (code at $80:ADB1).
_OFS_PREDOMINANT_COLOR = 0x8DB2  # 1 byte per team, enum order

#: $82:F153: each team's chest design, the one per-team drawing on a shirt: an
#: 8x8 tile in eight poses (separate drawings per body angle), drawn in the
#: team's flag colours, entries 12-15. 8 poses x 28 slots (TEAM_ENUM_ORDER and
#: one more) of 3-byte SNES addresses, each of a 0x0020 header and the tile.
_OFS_CHEST_PTRS = 0x17153
CHEST_POSES = 8
_CHEST_SLOTS = 28
#: The cartridge's empty design, $8C:D430, the one Italy and England wear.
BLANK_DESIGN = 0x8CD430

# In-game team name tiles: pointer table at 0x93CD, P48000/P17000 format
# Each entry points to Konami-compressed 2bpp tile data (64 bytes decompressed)
# Displacement: move tile data to 0x17680+ free region, patch code to read from there
_OFS_NAME_TILES_PTRS = 0x93CD  # 2 bytes per team, 27 entries
_NAME_TILES_DISPLACED_BASE = 0x17680  # Free 0xFF region in ROM
_NAME_TILES_DISPLACED_END = 0x18000
#: Bytes available for all 27 compressed name-tile blobs after displacement.
NAME_TILES_CAPACITY = _NAME_TILES_DISPLACED_END - _NAME_TILES_DISPLACED_BASE
_DISPLACEMENT_PATCH_BYTE = 0x82  # Value to write at patch points
#: Operand bytes of ten 65816 instructions, in one exact build of the game.
#: Overwritten unconditionally and never read first; see the module docstring.
_DISPLACEMENT_PATCH_POINTS = [
    0x93C6,
    0x93CB,
    0x3A7EB,
    0x3A7F0,
    0x3A7F5,
    0x3A7FA,
    0x3A7FF,
    0x3A804,
    0x3A809,
    0x3A80E,
]

# Team description text: pointer table at 0x38000, SNES LoROM pointers
# Each entry is 2 bytes (16-bit SNES address within bank $02)
# Points to FE + formation_line(16B) + FE + ' ' + FD + description_text
# Description is plain ASCII, 15-char line wrapping, variable length (46-90 bytes)
_OFS_DESC_PTRS = 0x38000  # 2 bytes per team, 27 entries, TEAM_ENUM_ORDER
_DESC_LINE_WIDTH = 15  # Characters per line in the description text box
#: Bank $02 of a LoROM image starts here and the SNES half of the pointer starts
#: at $8000, so `rom_addr = _DESC_BANK_BASE + (snes_addr - _DESC_BANK_SNES_BASE)`,
#: which reduces to `0x8000 + snes_addr`. A pointer below $8000 therefore lands
#: one bank low, in 0x8000..0xFFFF, where three of this module's own pointer
#: tables live.
_DESC_BANK_BASE = 0x10000
_DESC_BANK_SNES_BASE = 0x8000

# Team name text: pointer table in TEAM_ENUM_ORDER, P40000 format
_OFS_TEAM_NAME_TEXT_PTRS = 0x39DAE  # 2 bytes per team, 27 teams
_MAX_NAME_TEXT_ADDR = 0x44478  # must not overwrite extra entries at 0x44478+

# Kit colour team orderings, disjoint from TEAM_ENUM_ORDER.
_KIT_RANGE1_TEAMS = [
    "Germany",
    "Italy",
    "Holland",
    "Spain",
    "England",
    "France",
    "Sweden",
    "Ireland",
    "Belgium",
    "Romania",
    "Bulgaria",
    "Argentina",
    "Brazil",
    "Colombia",
    "Mexico",
    "U.S.A.",
    "Nigeria",
    "Cameroon",
    "Super Star",
]

_KIT_RANGE2_TEAMS = [
    "Russia",
    "Scotland",
    "S.Korea",
    "Wales",
    "Norway",
    "Switz",
    "Denmark",
    "Austria",
]

# GK range 1 is same as kit range 1 but without Super Star (18 teams)
_GK_RANGE1_TEAMS = _KIT_RANGE1_TEAMS[:18]


# Team name text character widths, in pixels.
_NAME_CHAR_WIDTHS = {"I": 7, ".": 7, "M": 8, "N": 8, "T": 8, "W": 8}
_NAME_SPACE_WIDTH = 3
_NAME_DEFAULT_WIDTH = 9
_MAX_NAME_WIDTH = 70


def _build_char_to_byte() -> dict[str, int]:
    """The ISS font's character-to-byte table.

    Build it once and return it; never fill a module-level dict by side effect.
    """
    table: dict[str, int] = {
        " ": 0x00,
        ".": 0x54,
        "-": 0x53,
        '"': 0x56,
        "'": 0x5C,
        "/": 0x5F,
    }
    for i, c in enumerate("0123456789"):
        table[c] = 0x62 + i
    for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        table[c] = 0x6C + i
    for i, c in enumerate("abcdefghijklmnopqrstuvwxyz"):
        table[c] = 0x86 + i
    return table


#: ISS custom character encoding. Note 0x00 is a SPACE and not a terminator, so
#: a character this table has no entry for encodes to a space silently.
CHAR_TO_BYTE = _build_char_to_byte()


def _encode_iss_name(name: str, max_len: int = _PLAYER_NAME_LENGTH) -> bytes:
    """Encode a player name to ISS custom encoding, padded with spaces (0x00)."""
    ascii_name = _to_ascii(name)
    encoded = bytearray(max_len)  # filled with 0x00 = space
    for i, c in enumerate(ascii_name[:max_len]):
        encoded[i] = CHAR_TO_BYTE.get(c, 0x00)
    return bytes(encoded)


def _rgb_to_bgr555(r: int, g: int, b: int) -> int:
    """Convert RGB888 to SNES BGR555 (15-bit)."""
    r5 = min(31, r * 31 // 255)
    g5 = min(31, g * 31 // 255)
    b5 = min(31, b * 31 // 255)
    return r5 | (g5 << 5) | (b5 << 10)


def _char_top_tile(c: str) -> int | None:
    if "A" <= c <= "P":
        return 0xC0 + (ord(c) - ord("A"))
    if "Q" <= c <= "Z":
        return 0xE0 + (ord(c) - ord("Q"))
    if "0" <= c <= "9":
        return 0xA0 + (ord(c) - ord("0"))
    return None


def _char_bottom_tile(c: str) -> int | None:
    if "A" <= c <= "P":
        return 0xD0 + (ord(c) - ord("A"))
    if "Q" <= c <= "Z":
        return 0xF0 + (ord(c) - ord("Q"))
    if "0" <= c <= "9":
        return 0xB0 + (ord(c) - ord("0"))
    if c == ".":
        return 0xFA
    return None


def _encode_team_name_text(name: str) -> bytes:
    """Encode a team name into ISS team-name-text format.

    Returns: [count_byte] [entry0_4bytes] [entry1_4bytes] ...
    Each visible character produces bottom+top entries (4 bytes each).
    Period only has a bottom-half tile.

    Original ROM ordering: characters right-to-left, F9 (bottom) before F1 (top).
    """
    clean = _to_ascii(name).upper()
    chars: list[tuple[str, int, int | None, int | None]] = []
    x = 0
    for c in clean:
        if c == " ":
            x += _NAME_SPACE_WIDTH
            continue
        top = _char_top_tile(c)
        bot = _char_bottom_tile(c)
        if top is None and bot is None:
            continue
        w = _NAME_CHAR_WIDTHS.get(c, _NAME_DEFAULT_WIDTH)
        chars.append((c, x, top, bot))
        x += w

    total_width = x
    if total_width > _MAX_NAME_WIDTH and total_width > 0:
        scale = _MAX_NAME_WIDTH / total_width
        chars = [(c, int(xp * scale), t, b) for c, xp, t, b in chars]
        total_width = _MAX_NAME_WIDTH

    half = total_width // 2
    entries = []
    # Characters right-to-left, bottom (F9) before top (F1), as the ROM stores.
    for _c, xp, top, bot in reversed(chars):
        x_centered = xp - half
        x_byte = x_centered & 0xFF  # signed to unsigned byte
        if bot is not None:
            entries.append(bytes([0xF9, x_byte, bot, 0x06]))
        if top is not None:
            entries.append(bytes([0xF1, x_byte, top, 0x06]))

    count = len(entries)
    result = bytearray([count])
    for e in entries:
        result.extend(e)
    return bytes(result)


def _decode_p40000(b1: int, b2: int) -> int:
    """Decode P40000 pointer bytes to ROM file offset.

    The high byte is biased by 0x80 because the SNES address it holds has bit 15
    set. A `b2` below 0x80 yields a *negative* file offset, which only
    `rom_reader.signature_ok` can refuse; there is no in-band way to report it.
    """
    return 0x40000 | ((b2 - 0x80) << 8) | b1


def _encode_p40000(address: int) -> bytes:
    """Encode ROM file offset as P40000 pointer bytes [low, high].

    Valid for 0x40000..0x47FFF only: above that the biased high byte exceeds 0xFF
    and `bytes()` raises `ValueError`.
    """
    raw = address - 0x40000
    b1 = raw & 0xFF
    b2 = ((raw >> 8) & 0xFF) + 0x80
    return bytes([b1, b2])


def _make_shades(r: int, g: int, b: int, count: int) -> list[int]:
    """Generate BGR555 shades (dark->light) from a single RGB color.

    For bright colors (near white): base is lightest shade, darker shades generated below.
    For dark/medium colors: base is darkest shade, lighter shades blend toward white.
    Spread is ~50% of the available range per channel.
    """
    r5 = min(31, r * 31 // 255)
    g5 = min(31, g * 31 // 255)
    b5 = min(31, b * 31 // 255)

    brightness = (r5 + g5 + b5) / 3.0

    shades = []
    if brightness > 22:
        for i in range(count):
            t = (count - 1 - i) / (count - 1) * 0.5 if count > 1 else 0
            rv = max(0, round(r5 * (1.0 - t)))
            gv = max(0, round(g5 * (1.0 - t)))
            bv = max(0, round(b5 * (1.0 - t)))
            shades.append(rv | (gv << 5) | (bv << 10))
    else:
        for i in range(count):
            t = i / (count - 1) * 0.5 if count > 1 else 0
            rv = min(31, round(r5 + t * (31 - r5)))
            gv = min(31, round(g5 + t * (31 - g5)))
            bv = min(31, round(b5 + t * (31 - b5)))
            shades.append(rv | (gv << 5) | (bv << 10))
    return shades


def _rgb_to_predominant(r: int, g: int, b: int) -> int:
    """Map RGB to the clash byte: the cartridge's 0=White, 1=Blue, 2=Red,
    3=Yellow, 4=Green, and our own 5=Black, 6=Grey. The game only tests two
    teams' bytes for equality ($80:ADBB, $80:ADC8), so black meets red without
    a change of kit and black against black still changes. A colour with
    little hue goes by brightness."""
    max_c = max(r, g, b)
    min_c = min(r, g, b)
    if max_c - min_c < 50:
        if max_c > 160:
            return 0  # White
        return 5 if max_c < 80 else 6  # Black, Grey
    if r >= g and r >= b:
        if g > 150 and g > b:
            return 3  # Yellow
        return 2  # Red
    if g >= r and g >= b:
        return 4  # Green
    return 1  # Blue


# In-game team name tile font: 5px wide x 8px tall, 2bpp, where 0=TRANSPARENT
# and 1=COLOR_1 (white stroke). Rows 0 and 7 are the top/bottom borders.


def _f(s: str) -> list[list[int]]:
    """Parse a `/`-separated compact font string into rows of pixel values."""
    return [[int(c) for c in row] for row in s.strip().split("/")]


#: `_render_name_tiles` skips any character not present, so these keys are also
#: the characters that can reach an in-game name tile.
_TILE_FONT = {
    "A": _f("01110/10001/10001/11111/10001/10001"),
    "B": _f("11110/10001/11110/10001/10001/11110"),
    "C": _f("01110/10001/10000/10000/10001/01110"),
    "D": _f("11100/10010/10001/10001/10010/11100"),
    "E": _f("11111/10000/11110/10000/10000/11111"),
    "F": _f("11111/10000/11110/10000/10000/10000"),
    "G": _f("01110/10001/10000/10111/10001/01110"),
    "H": _f("10001/10001/11111/10001/10001/10001"),
    "I": _f("111/010/010/010/010/111"),
    "J": _f("00111/00010/00010/00010/10010/01100"),
    "K": _f("10001/10010/11100/10010/10001/10001"),
    "L": _f("10000/10000/10000/10000/10000/11111"),
    "M": _f("10001/11011/10101/10001/10001/10001"),
    "N": _f("10001/11001/10101/10011/10001/10001"),
    "O": _f("01110/10001/10001/10001/10001/01110"),
    "P": _f("11110/10001/11110/10000/10000/10000"),
    "Q": _f("01110/10001/10001/10101/10010/01101"),
    "R": _f("11110/10001/11110/10010/10001/10001"),
    "S": _f("01111/10000/01110/00001/00001/11110"),
    "T": _f("11111/00100/00100/00100/00100/00100"),
    "U": _f("10001/10001/10001/10001/10001/01110"),
    "V": _f("10001/10001/10001/01010/01010/00100"),
    "W": _f("10001/10001/10101/10101/11011/10001"),
    "X": _f("10001/01010/00100/01010/10001/10001"),
    "Y": _f("10001/01010/00100/00100/00100/00100"),
    "Z": _f("11111/00010/00100/01000/10000/11111"),
    "0": _f("01110/10001/10011/10101/11001/01110"),
    "1": _f("010/110/010/010/010/111"),
    "2": _f("01110/10001/00010/00100/01000/11111"),
    "3": _f("11110/00001/01110/00001/00001/11110"),
    "4": _f("10010/10010/11111/00010/00010/00010"),
    "5": _f("11111/10000/11110/00001/00001/11110"),
    "6": _f("01110/10000/11110/10001/10001/01110"),
    "7": _f("11111/00001/00010/00100/01000/01000"),
    "8": _f("01110/10001/01110/10001/10001/01110"),
    "9": _f("01110/10001/01111/00001/00001/01110"),
    ".": _f("0/0/0/0/0/1"),
    " ": _f("00/00/00/00/00/00"),
}

_TILE_COLS = 32
_TILE_ROWS = 8
# 2bpp color codes
_TC_TRANSPARENT = 0
_TC_WHITE = 1  # COLOR_1 (letter stroke)
_TC_SHADOW = 3  # COLOR_3 (dark background behind text)


def _render_name_tiles(name: str) -> list[list[int]]:
    """Render a team name to an 8x32 pixel grid (2bpp color codes).

    Returns list of 8 rows, each a list of 32 ints (0-3). A name with nothing
    the font draws (a blank slot's) is all transparent.
    """
    clean = _to_ascii(name).upper()
    letters = []
    for c in clean:
        glyph = _TILE_FONT.get(c)
        if glyph is None:
            continue
        w = len(glyph[0]) if glyph else 0
        letters.append((glyph, w))

    # 1px gap between letters, dropped when the name does not otherwise fit.
    total_w = sum(w for _, w in letters) + max(0, len(letters) - 1)

    gap = 1
    if total_w > _TILE_COLS:
        gap = 0
        total_w = sum(w for _, w in letters)

    grid = [[_TC_TRANSPARENT] * _TILE_COLS for _ in range(_TILE_ROWS)]

    start_x = max(0, (_TILE_COLS - total_w) // 2)
    x = start_x

    for glyph, w in letters:
        if x + w > _TILE_COLS:
            break
        # Shadow background, 1px padding around the letter area.
        for row in range(_TILE_ROWS):
            for col in range(max(0, x - 1), min(_TILE_COLS, x + w + 1)):
                if grid[row][col] == _TC_TRANSPARENT:
                    grid[row][col] = _TC_SHADOW
        # Letter strokes occupy rows 1-6 of the 8-pixel height.
        for gr, glyph_row in enumerate(glyph[:6]):
            row = gr + 1  # offset by 1 for top border
            for gc, pixel in enumerate(glyph_row):
                col = x + gc
                if col < _TILE_COLS and pixel:
                    grid[row][col] = _TC_WHITE
        x += w + gap

    return grid


def _serialize_2bpp(grid: list[list[int]]) -> bytes:
    """Serialize an 8x32 pixel grid to 64 bytes of SNES 2bpp tile data.

    4 tiles of 8x8 arranged horizontally. Standard SNES 2bpp:
    each row = 2 bytes (bit plane 0, bit plane 1).
    """
    data = bytearray(64)
    for b in range(32):
        # Map linear column sweep to tile-interleaved byte index
        i = 16 * (b // 8) + (b % 8) * 2
        for j in range(8):
            row = b % 8
            col = (b // 8) * 8 + j
            color = grid[row][col] if col < len(grid[row]) else 0
            bit0 = color & 1
            bit1 = (color >> 1) & 1
            data[i] |= bit0 << (7 - j)
            data[i + 1] |= bit1 << (7 - j)
    return bytes(data)


#: Longest literal run one Konami control byte can introduce.
_KONAMI_MAX_RUN = 31


def _konami_compress_literal(raw: bytes) -> bytes:
    """Wrap raw data in Konami literal-only compression format.

    Format: [2-byte LE size header] [0x80|count] [data...] ..., where the size
    header is the total stream length including itself and one RAW command
    carries at most 31 literal bytes.

    Literal-only, so the output is always *longer* than the input: two bytes of
    header plus one control byte per 31 bytes of payload. That is why
    `write_name_tiles` relocates every blob instead of writing back in place.
    """
    out = bytearray()
    out.extend([0, 0])  # placeholder for size header
    pos = 0
    while pos < len(raw):
        chunk = min(_KONAMI_MAX_RUN, len(raw) - pos)
        out.append(0x80 | chunk)
        out.extend(raw[pos : pos + chunk])
        pos += chunk
    total = len(out)
    out[0] = total & 0xFF
    out[1] = (total >> 8) & 0xFF
    return bytes(out)


def _encode_p17000(address: int) -> bytes:
    """Encode ROM address as P17000 pointer [low, high]."""
    raw = address - 0x10000
    b1 = raw & 0xFF
    b2 = ((raw >> 8) & 0xFF) + 0x80
    return bytes([b1, b2])


def _encode_p48000(address: int) -> bytes:
    """Encode ROM address as P48000 pointer [low, high] (bank $09/$89).

    Unbiased, unlike the two above: this table stores a raw 16-bit offset from
    0x40000, which is why `write_name_tiles` reads its own table back as
    `0x40000 + b1 + (b2 << 8)` and not through `_decode_p40000`.
    """
    raw = address - 0x40000
    b1 = raw & 0xFF
    b2 = (raw >> 8) & 0xFF
    return bytes([b1, b2])


FLAG_WIDTH = 24
FLAG_HEIGHT = 16


def two_band_flag(primary: tuple[int, int, int], alternate: tuple[int, int, int]) -> Flag:
    """The plain flag: the primary colour over the alternate."""
    top = [[0] * FLAG_WIDTH for _ in range(FLAG_HEIGHT // 2)]
    bottom = [[1] * FLAG_WIDTH for _ in range(FLAG_HEIGHT // 2)]
    return Flag(top + bottom, [primary, alternate, primary, alternate])


def _distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return sum((x - y) ** 2 for x, y in zip(a, b, strict=True))


def _contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """How well a shirt number in colour `a` reads on shirt colour `b`.

    The distance in BT.601 luma and chroma, with luma counted twice and the
    blue-yellow axis (Cb) half: digits a pixel or two wide are read by their
    brightness, and by red-green hue far better than by blue-yellow. Plain
    RGB distance misses this: yellow on a white shirt differs from it almost
    only in blue and scores 209 there, 79 here; red on black and on white
    scores 130 and up, white on red 190, black on green 174.
    """

    def ycc(c: tuple[int, int, int]) -> tuple[float, float, float]:
        y = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
        return 2 * y, 0.282 * (c[2] - y), 0.713 * (c[0] - y)

    return math.dist(ycc(a), ycc(b))


#: Shirt-number colours tried after the crest's own: red, yellow, blue, then
#: white, black and mid-grey.
_NUMBER_COLOURS = [
    (255, 0, 0),
    (255, 255, 0),
    (0, 0, 255),
    (255, 255, 255),
    (0, 0, 0),
    (128, 128, 128),
]

#: The `_contrast` a crest colour needs from every shade of both shirts to
#: number them. No colour reaches it on a black and a white shirt (red comes
#: closest, at 131), so there the best one wins.
_NUMBER_MARGIN = 150
#: Scores within this of the best (`_contrast`) count as a tie, broken by the
#: brighter contrast (`_luminance_ratio`).
_NUMBER_TIE = 10


def _kit_shades(shirt: tuple[int, int, int]) -> list[tuple[int, int, int]]:
    """The three shades the game draws a shirt of colour `shirt` in, as
    `write_kit_colors` writes them; the numbers sit on all three."""
    return [_bgr555_to_rgb(word) for word in _make_shades(*shirt, 3)]


def number_colour(flag: Flag, shades: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    """The colour for the shirt numbers, which are drawn over `shades`, every
    shade of both shirts (`_kit_shades`).

    The crest colour farthest (`_contrast`) from its nearest shade, if that
    clears `_NUMBER_MARGIN`, so the crest loses nothing; otherwise whichever of
    the crest's colours and `_NUMBER_COLOURS` is farthest, the earlier on a tie.
    """

    def score(colour: tuple[int, int, int]) -> float:
        return min(_contrast(colour, shade) for shade in shades)

    def brightness_gap(colour: tuple[int, int, int]) -> float:
        return min(_luminance_ratio(colour, shade) for shade in shades)

    used = [flag.colours[i] for i in sorted({v for row in flag.pixels for v in row})]
    best = max(used, key=score)
    if score(best) >= _NUMBER_MARGIN:
        return best
    candidates = used + _NUMBER_COLOURS
    top = max(score(c) for c in candidates)
    # A near tie goes to the brighter contrast: a hue a point ahead (Bahia's
    # blue on red, Remo's red on steel blue) reads less crisply than black.
    close = [c for c in candidates if score(c) >= top - _NUMBER_TIE]
    return max(close, key=brightness_gap)


def _luminance_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """The WCAG contrast ratio of `a` on `b`, from 1 (same brightness) to 21."""

    def lum(c: tuple[int, int, int]) -> float:
        def lin(channel: int) -> float:
            v = channel / 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

        return 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2])

    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def number_first(flag: Flag, shades: list[tuple[int, int, int]]) -> Flag:
    """`flag` redrawn with the shirt-number colour at entry 12.

    Entry 12 colours a team's shirt numbers on both kits, so it must stand out
    from every shade of both shirts (`number_colour`). When that is one of the
    flag's colours nothing is lost; otherwise the flag keeps three colours on
    entries 13-15, dropping the one whose pixels move least to the nearest of
    what is left, the number colour included. Entries 13-15 then take the order
    that compresses smallest (`_smallest`).
    """
    n = number_colour(flag, shades)
    counts = Counter(v for row in flag.pixels for v in row)
    rest = [i for i in sorted(counts) if flag.colours[i] != n]

    def cost(i: int) -> int:
        others = [n] + [flag.colours[j] for j in rest if j != i]
        return counts[i] * min(_distance(flag.colours[i], c) for c in others)

    if len(rest) > 3:
        rest.remove(min(rest, key=cost))
    palette = [n] + [flag.colours[i] for i in rest]
    where = {
        i: 1 + rest.index(i)
        if i in rest
        else min(range(len(palette)), key=lambda k: _distance(flag.colours[i], palette[k]))
        for i in counts
    }
    palette += [palette[-1]] * (4 - len(palette))
    return _smallest(Flag([[where[v] for v in row] for row in flag.pixels], palette))


def _smallest(flag: Flag) -> Flag:
    """`flag` with the colours it uses on entries 13-15 moved to whichever of
    those entries compress smallest; entry 12, the shirt numbers, stays.

    Colours a single bitplane tells apart compress better than ones that
    differ in both: a speckled crest down to two colours, its white off entry
    12, takes about 114 bytes on entries 13 and 14 and 106 on 14 and 15, and
    a plain band 12 bytes on entry 13 and 8 on 15. At most six orders.
    """
    moved = sorted({v for row in flag.pixels for v in row} - {0})

    def arranged(spots: tuple[int, ...]) -> Flag:
        where = dict(zip(moved, spots, strict=True))
        colours = list(flag.colours)
        for i, spot in where.items():
            colours[spot] = flag.colours[i]
        return Flag([[where.get(v, v) for v in row] for row in flag.pixels], colours)

    # The order it has comes first, so a tie keeps it.
    orders = [tuple(moved), *permutations((1, 2, 3), len(moved))]
    return min(map(arranged, orders), key=lambda f: sum(map(len, _flag_blobs(f))))


def _pinned(flag: Flag, want: Pins) -> Flag:
    """`flag` with its entries 13-15 in the order that puts on each entry in
    `want` the colour nearest the one asked for, weighted by how many of the
    design's pixels use it. The flag looks the same; entry 12, the shirt
    numbers, stays; a tie keeps the order it has.

    An entry no flag pixel uses is free: it costs nothing and takes the colour
    asked for exactly (Botafogo's crest uses entries 12 and 14 only, so
    Argentina's stripes in entry 13 are drawn in the secondary, not black)."""
    used = {v for row in flag.pixels for v in row}

    def arranged(order: tuple[int, ...]) -> Flag:
        where = {0: 0} | {old: new for new, old in enumerate(order, 1)}
        colours = [flag.colours[0]] + [flag.colours[old] for old in order]
        for entry, (colour, _) in want.items():
            if 13 <= entry <= 15 and order[entry - 13] not in used:
                colours[entry - 12] = colour
        return Flag([[where[v] for v in row] for row in flag.pixels], colours)

    def cost(order: tuple[int, ...]) -> int:
        return sum(
            weight * _distance(flag.colours[order[entry - 13]], colour)
            for entry, (colour, weight) in want.items()
            if 13 <= entry <= 15 and order[entry - 13] in used
        )

    return arranged(min(permutations((1, 2, 3)), key=cost))


#: How near (RGB) a crest colour must be to the shirt-number colour to be
#: redrawn in it when the crest does not use it: a shade of the same colour,
#: as (200, 0, 0) is of red, never black for red.
_SAME_COLOUR = 96


def _fewer_colours(flag: Flag) -> Flag | None:
    """`flag` with its rarest colour painted over in its nearest, in its
    smallest order (`_smallest`); `None` where that would leave one colour.

    Entry 12 is never painted over. Where the crest does not use it, it is
    still a colour to paint in for a colour within `_SAME_COLOUR` of it: it
    numbers the shirts either way, and the format packs a crest that uses it
    best (two colours: about 70 bytes with entry 12, 100 without).
    """
    counts = Counter(v for row in flag.pixels for v in row)
    rest = [i for i in counts if i != 0]
    if not rest:
        return None
    rare = min(rest, key=lambda i: (counts[i], i))
    targets = set(counts) - {rare}
    if _distance(flag.colours[0], flag.colours[rare]) <= _SAME_COLOUR**2:
        targets.add(0)
    near = min(
        targets,
        key=lambda i: (_distance(flag.colours[i], flag.colours[rare]), i),
        default=None,
    )
    if near is None or len(set(counts) - {rare} | {near}) < 2:
        return None
    return _smallest(
        Flag([[near if v == rare else v for v in row] for row in flag.pixels], flag.colours)
    )


def _flag_half_tiles(rows: list[list[int]]) -> bytes:
    """8 rows of 24 colour indices -> three 4bpp tiles, palette entries 12-15.

    Each 32-byte tile holds bitplanes 0/1 interleaved by row, then 2/3; 12-15
    all set planes 2 and 3.
    """
    out = bytearray(96)
    for tile in range(3):
        for r in range(8):
            for c in range(8):
                value = 12 + rows[r][8 * tile + c]
                for plane, base in ((0, 0), (1, 1), (2, 16), (3, 17)):
                    if value >> plane & 1:
                        out[32 * tile + base + 2 * r] |= 0x80 >> c
    return bytes(out)


def _half_rows(tiles: bytes) -> list[list[int]]:
    """`_flag_half_tiles` backwards: three 4bpp tiles -> 8 rows of 24 entries 0-3."""
    rows = [[0] * FLAG_WIDTH for _ in range(FLAG_HEIGHT // 2)]
    for tile in range(3):
        for r in range(8):
            for c in range(8):
                for plane, base in ((0, 0), (1, 1)):
                    if tiles[32 * tile + base + 2 * r] & 0x80 >> c:
                        rows[r][8 * tile + c] |= 1 << plane
    return rows


def _bgr555_to_rgb(value: int) -> tuple[int, int, int]:
    """`_rgb_to_bgr555` backwards, rounded up so that a round trip is exact."""
    r, g, b = (value >> shift & 31 for shift in (0, 5, 10))
    return (r * 255 + 30) // 31, (g * 255 + 30) // 31, (b * 255 + 30) // 31


#: A team's home and away shirts: a colour (drawn in `_kit_shades`), the
#: three shades as written, or `None` for a kit left as it was.
Shirt = tuple[int, int, int] | list[tuple[int, int, int]] | None
Shirts = tuple[Shirt, Shirt]
#: Flag entry (13-15) -> the colour a chest design wants there and how many of
#: its pixels use it; see `_pinned`.
Pins = dict[int, tuple[tuple[int, int, int], int]]
#: A flag half with every pixel transparent, compressed: a blank slot's flag.
_BLANK_HALF = compress(bytes(96))


def _kit_offsets(slot: int) -> tuple[int, int]:
    """Where `slot`'s home and away outfield kits start."""
    name = TEAM_ENUM_ORDER[slot]
    if name in _KIT_RANGE1_TEAMS:
        pos = _KIT_RANGE1_TEAMS.index(name) * _OUTFIELD_KIT_STRIDE
        return _OFS_KIT1_RANGE1 + pos, _OFS_KIT2_RANGE1 + pos
    pos = _KIT_RANGE2_TEAMS.index(name) * _OUTFIELD_KIT_STRIDE
    return _OFS_KIT1_RANGE2 + pos, _OFS_KIT2_RANGE2 + pos


def outfield_block(colors: tuple[tuple[int, int, int], ...]) -> bytes:
    """The 16 bytes of an outfield kit: shirt and shorts in three shades and
    socks in two (`_make_shades`, dark->light). One colour is the whole kit, two
    are the shirt and the shorts and the socks take the shirt's."""
    shirt = colors[0]
    shorts = colors[1] if len(colors) > 1 else shirt
    socks = colors[2] if len(colors) > 2 else shirt
    words = _make_shades(*shirt, 3) + _make_shades(*shorts, 3) + _make_shades(*socks, 2)
    return struct.pack("<8H", *words)


def design_pointers(design: int) -> bytes:
    """The eight pose pointers of the cartridge design whose pose 0 is at SNES
    address `design`: the poses follow 0x286 bytes apart; the blank design is
    one tile for every pose."""
    step = 0 if design == BLANK_DESIGN else 0x286
    return b"".join((design + pose * step).to_bytes(3, "little") for pose in range(CHEST_POSES))


def _flag_blobs(flag: Flag) -> tuple[bytes, bytes]:
    """The two compressed halves the game decodes into `flag`."""
    half = FLAG_HEIGHT // 2
    return (
        compress(_flag_half_tiles(flag.pixels[:half])),
        compress(_flag_half_tiles(flag.pixels[half:])),
    )


def _min_patchable_size() -> int:
    """Highest file offset any write in this module reaches, in headerless bytes.

    Every write below is at a fixed offset into a file opened `r+b`, so seeking
    past the end *extends* it: a file shorter than this provably cannot be
    patched, which is why `patcher.py` applies it to `patch` as well.

    Keep every term a `max` over this module's own constants, never a transcribed
    total, so moving an offset moves this bound with it. The list is deliberately
    over-complete: one term dominates today, so dropping any other changes
    nothing and no test can tell. Reads are excluded -- a short read in
    0x40000..0x4FFFF answers fewer bytes rather than corrupting anything, and
    `rom_reader.signature_ok` rejects it.
    """
    return max(
        _OFS_PLAYER_NAMES + TOTAL_TEAMS * PLAYERS_PER_TEAM * _PLAYER_NAME_LENGTH,
        _OFS_PLAYER_DATA + TOTAL_TEAMS * PLAYERS_PER_TEAM * _PLAYER_DATA_LENGTH,
        _OFS_KIT1_RANGE1 + len(_KIT_RANGE1_TEAMS) * _OUTFIELD_KIT_STRIDE,
        _OFS_KIT2_RANGE1 + len(_KIT_RANGE1_TEAMS) * _OUTFIELD_KIT_STRIDE,
        _OFS_KIT1_RANGE2 + len(_KIT_RANGE2_TEAMS) * _OUTFIELD_KIT_STRIDE,
        _OFS_KIT2_RANGE2 + len(_KIT_RANGE2_TEAMS) * _OUTFIELD_KIT_STRIDE,
        _OFS_GK_RANGE1 + len(_GK_RANGE1_TEAMS) * _GK_KIT_STRIDE,
        _OFS_GK_RANGE2 + len(_KIT_RANGE2_TEAMS) * _GK_KIT_STRIDE,
        _OFS_FLAG_COLORS_RANGE1 + (len(_FLAG_COLORS_RANGE1_TEAMS) - 1) * _FLAG_COLORS_STEP + 8,
        _OFS_FLAG_COLORS_RANGE2 + (len(_FLAG_COLORS_RANGE2_TEAMS) - 1) * _FLAG_COLORS_STEP + 8,
        _OFS_FLAG_TILE_PTRS + TOTAL_TEAMS * _FLAG_TILE_PTR_STRIDE,
        _FLAG_ROOM_END,
        _OFS_FLAG_CELLS + TOTAL_TEAMS * 2,
        # The sprite records: bank $88 ends at 0x48000.
        _BANK_88 + 0x8000,
        _OFS_PREDOMINANT_COLOR + TOTAL_TEAMS,
        _OFS_CHEST_PTRS + CHEST_POSES * _CHEST_SLOTS * 3,
        _OFS_NAME_TILES_PTRS + TOTAL_TEAMS * 2,
        _NAME_TILES_DISPLACED_END,
        _OFS_DESC_PTRS + TOTAL_TEAMS * 2,
        _DESC_BANK_BASE + (0xFFFF - _DESC_BANK_SNES_BASE) + 1,
        _OFS_TEAM_NAME_TEXT_PTRS + TOTAL_TEAMS * 2,
        _MAX_NAME_TEXT_ADDR,
        max(_DISPLACEMENT_PATCH_POINTS) + 1,
    )


#: Smallest headerless image every write in this module lands inside of.
MIN_PATCHABLE_SIZE = _min_patchable_size()


class ISSRomWriter:
    """Copies the ROM to `output_path` and patches the copy in place.

    `__init__` copies the input over the output and leaves a handle open on
    `self`. Use `with`: `write_name_tiles` raises by design, and without the
    context manager that leaks the descriptor and leaves a half-written file.
    """

    def __init__(self, rom_path: str, output_path: str, header_offset: int = 0):
        self.output_path = output_path
        self.header_offset = header_offset

        shutil.copy2(rom_path, output_path)
        self._f: IO[bytes] | None = open(output_path, "r+b")

    def __enter__(self) -> ISSRomWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Release the output handle if it is still open.

        Keep it idempotent: `finalize` inside a `with` block would otherwise
        double-close.
        """
        if self._f is not None:
            self._f.close()
            self._f = None

    @property
    def _file(self) -> IO[bytes]:
        """The open handle, or a `RomError` naming what happened to it.

        Go through this, never straight to `self._f`: `finalize` sets it to
        `None` and a later write would raise `AttributeError` instead.
        """
        if self._f is None:
            raise RomError(f"The ISS ROM writer for {self.output_path} is already closed")
        return self._f

    def _seek(self, offset: int) -> None:
        """Seek to a ROM offset, accounting for copier header."""
        self._file.seek(offset + self.header_offset)

    def write_player_names(self, enum_index: int, players: list[ISSPlayerRecord]) -> int:
        """Write player names for a team, and return how many were written.

        Names are stored in TEAM_NAME_ORDER, so `enum_index` must be translated
        by `name_storage_index`.
        """
        base = (
            _OFS_PLAYER_NAMES
            + name_storage_index(enum_index) * PLAYERS_PER_TEAM * _PLAYER_NAME_LENGTH
        )
        written = 0
        for i, player in enumerate(players[:PLAYERS_PER_TEAM]):
            self._seek(base + i * _PLAYER_NAME_LENGTH)
            self._file.write(_encode_iss_name(player.name, _PLAYER_NAME_LENGTH))
            written += 1
        return written

    def write_player_data(self, enum_index: int, players: list[ISSPlayerRecord]) -> int:
        """Write each player's ability fields and shirt number.

        Player data is stored in TEAM_ENUM_ORDER, 6 bytes per player, laid out
        as `ISSPlayerAttributes` documents. Every field is clamped to its bit
        width and the shirt number to 1-15. Hair is 0, the retail default: the
        engine updates only two hair objects per team. b5 bit 6 (the alternate
        palette) is kept from the disc; every other unknown bit is written 0.

        Returns the records actually written, the squad size capped at
        `PLAYERS_PER_TEAM`: `PatchResult.players_patched` counts what reached the
        image, so counting the squad handed in would over-report.
        """

        def nib(value: int, top: int = 15) -> int:
            return max(0, min(top, value))

        base = _OFS_PLAYER_DATA + enum_index * PLAYERS_PER_TEAM * _PLAYER_DATA_LENGTH
        written = 0
        for i, player in enumerate(players[:PLAYERS_PER_TEAM]):
            self._seek(base + i * _PLAYER_DATA_LENGTH + 5)
            palette = self._file.read(1)[0] & 0x40
            a = player.attributes
            number = max(1, min(PLAYERS_PER_TEAM, player.shirt_number))
            self._seek(base + i * _PLAYER_DATA_LENGTH)
            self._file.write(
                bytes(
                    [
                        nib(a.dash) << 4 | nib(a.dribble),
                        nib(a.shooting, 7) << 4 | nib(a.kick, 7),
                        nib(a.stamina) << 4 | nib(a.technique, 7),
                        nib(a.ball_dash) << 4 | (number - 1),
                        nib(a.offense) << 4 | nib(a.defense),
                        palette,
                    ]
                )
            )
            written += 1
        return written

    def write_kit_colors(self, enum_index: int, team: ISSTeamRecord) -> None:
        """Write kit colors (home, away, GK) for a team, each one given.

        Outfield: the 16 bytes of shirt, shorts and socks (`outfield_block`).
        GK: see `write_gk_kit`. Colors are BGR555 little-endian.
        """
        home, away = _kit_offsets(enum_index)
        if team.kit_home:
            self._write_outfield_kit(home, team.kit_home)
        if team.kit_away:
            self._write_outfield_kit(away, team.kit_away)
        if team.kit_gk:
            self.write_gk_kit(enum_index, team.kit_gk)

    def write_kit_block(self, enum_index: int, kit: int, block: bytes) -> None:
        """Write the 16 bytes of shirt, shorts and socks of `enum_index`'s home
        (`kit` 0) or away kit: `outfield_block`'s, or another kit's as read."""
        self._seek(_kit_offsets(enum_index)[kit])
        self._file.write(bytes(block[:16]))

    def write_gk_kit(self, enum_index: int, colors: tuple[tuple[int, int, int], ...]) -> None:
        """Write the goalkeeper's shirt and shorts; Super Star has no keeper record."""
        enum_name = TEAM_ENUM_ORDER[enum_index]
        if enum_name in _GK_RANGE1_TEAMS:
            offset = _OFS_GK_RANGE1 + _GK_RANGE1_TEAMS.index(enum_name) * _GK_KIT_STRIDE
        elif enum_name in _KIT_RANGE2_TEAMS:
            offset = _OFS_GK_RANGE2 + _KIT_RANGE2_TEAMS.index(enum_name) * _GK_KIT_STRIDE
        else:
            return
        self._write_gk_kit(offset, colors)

    def _write_outfield_kit(self, offset: int, colors: tuple[tuple[int, int, int], ...]) -> None:
        if not colors:
            return
        self._seek(offset)
        self._file.write(outfield_block(colors))

    def write_chest(self, enum_index: int, pointers: bytes) -> None:
        """Point `enum_index`'s chest design, all eight poses, at `pointers`
        (8 x 3 bytes, `design_pointers` or another team's as read)."""
        for pose in range(CHEST_POSES):
            self._seek(_OFS_CHEST_PTRS + (pose * _CHEST_SLOTS + enum_index) * 3)
            self._file.write(bytes(pointers[3 * pose : 3 * pose + 3]))

    def _write_gk_kit(self, offset: int, colors: tuple[tuple[int, int, int], ...]) -> None:
        """Write GK kit with proper shade gradients.

        A keeper record is a 0x0015 header and 11 colours: hair, three skin
        shades, the gloves, four shirt shades, the shorts and the shadow. The
        offset is record + 10: word 0 the gloves (always near-white), words 1-4
        the shirt (dark->light), word 5 the shorts; the 12 bytes after are the
        shadow, the next header and the next keeper's hair and skin (untouched).
        """
        if not colors:
            return
        self._seek(offset)
        existing = bytearray(self._file.read(12))
        self._seek(offset)

        shirt_color = colors[0] if len(colors) > 0 else (0, 128, 0)
        shorts_color = colors[1] if len(colors) > 1 else shirt_color

        # Word 0: specular highlight (always near-white)
        struct.pack_into("<H", existing, 0, 0x7FFE)

        # Words 1-4: 4 shirt shades (bytes 2-9)
        for i, shade in enumerate(_make_shades(*shirt_color, 4)):
            struct.pack_into("<H", existing, 2 + i * 2, shade)

        # Word 5: shorts (single color, bytes 10-11)
        struct.pack_into("<H", existing, 10, _rgb_to_bgr555(*shorts_color))

        self._file.write(bytes(existing))

    def write_flags(
        self,
        flags: dict[int, Flag],
        fallbacks: dict[int, Flag | None] | None = None,
        shirts: dict[int, Shirts] | None = None,
        blank: Iterable[int] = (),
        pins: dict[int, Pins] | None = None,
    ) -> dict[int, str]:
        """Give each slot in `flags` that flag and its four colours.

        Repacks the whole flag room: every team's current halves are read first
        (so an unpatched team, or one an earlier patch drew, keeps its blobs
        byte for byte), each distinct blob is stored once, and the pointer
        table is rewritten.

        `shirts` gives a slot's home and away shirt colours, `None` for a kit
        this patch left as it was (its three shades are then read from the
        ROM). That slot's flag and fallback are redrawn by `number_first`, so
        entry 12, the shirt numbers, stands out from every shade of both
        shirts; if a kit changed and the slot keeps the cartridge's flag, that
        flag is redrawn the same way.

        A slot in `fallbacks` holds a crest that may give way when the room is
        full. The largest crest that can still save bytes by losing colours
        loses them, one at a time down to two, until a step saves some; only
        when none can is the largest replaced by its fallback (`None`: the
        cartridge's own flag). Returns what gave way, as `{slot: what happened}`.

        A slot in `blank` gets a fully transparent flag, its colours untouched.
        A slot in `pins` has its flag's entries 13-15 reordered last (`_pinned`)
        so its chest design shows the colours it asks for, unless that no
        longer fits.

        Raises `RomError` unless `write_name_tiles` has moved the name tiles
        out of the room, or if even the fallbacks do not fit.
        """
        fallbacks = fallbacks or {}
        shirts = shirts or {}
        pins = pins or {}
        self._seek(_DISPLACEMENT_PATCH_POINTS[0])
        if self._file.read(1)[0] != _DISPLACEMENT_PATCH_BYTE:
            raise RomError(
                "The flags need bank $89's name tiles moved out first: call write_name_tiles"
            )

        self._seek(_OFS_FLAG_TILE_PTRS)
        table = self._file.read(TOTAL_TEAMS * _FLAG_TILE_PTR_STRIDE)
        current: list[tuple[bytes, ...]] = []
        for team in range(TOTAL_TEAMS):
            halves = []
            for k in (0, 2):
                at = team * _FLAG_TILE_PTR_STRIDE + k
                address = 0x40000 + (table[at] | table[at + 1] << 8)
                self._seek(address)
                head = self._file.read(2)
                self._seek(address)
                halves.append(self._file.read((head[0] | head[1] << 8) & 0x7FFF))
            current.append(tuple(halves))
        for team in blank:
            current[team] = (_BLANK_HALF, _BLANK_HALF)

        def shades(slot: int, kit: int, shirt: Shirt) -> list[tuple[int, int, int]]:
            if shirt is None:
                return self._shades(slot, kit)
            return list(shirt) if isinstance(shirt, list) else _kit_shades(shirt)

        def numbered(slot: int, flag: Flag) -> Flag:
            if slot not in shirts:
                return flag
            return number_first(
                flag,
                [s for kit, shirt in enumerate(shirts[slot]) for s in shades(slot, kit, shirt)],
            )

        redressed = {slot for slot, kits in shirts.items() if kits != (None, None)}

        def cartridge(slot: int) -> Flag | None:
            """The flag already there, redrawn if the slot's kits changed."""
            if slot not in redressed:
                return None
            half = [_half_rows(decompress(blob)) for blob in current[slot]]
            self._seek(self._flag_colour_offset(slot))
            words = struct.unpack("<4H", self._file.read(8))
            return numbered(slot, Flag(half[0] + half[1], [_bgr555_to_rgb(w) for w in words]))

        chosen = {slot: numbered(slot, flag) for slot, flag in flags.items()}
        backup = {
            slot: numbered(slot, flag) if flag is not None else cartridge(slot)
            for slot, flag in fallbacks.items()
        }
        for slot in redressed - set(chosen):
            if (flag := cartridge(slot)) is not None:
                chosen[slot] = flag
        crests = set(backup) & set(chosen)
        stuck: set[int] = set()
        notes: dict[int, str] = {}

        def blobs(team: int) -> tuple[bytes, ...]:
            flag = chosen.get(team)
            return _flag_blobs(flag) if flag is not None else current[team]

        def used() -> int:
            return sum(len(b) for b in {b for team in range(TOTAL_TEAMS) for b in blobs(team)})

        while (total := used()) > FLAG_ROOM_SIZE:
            largest = sorted(sorted(crests), key=lambda team: -sum(map(len, blobs(team))))
            slot = next((team for team in largest if team not in stuck), -1)
            if slot >= 0:
                # A step that saves nothing can lead to one that does: three
                # colours compress no better than four, two often far better.
                before = chosen[slot]
                simpler = _fewer_colours(before)
                while simpler is not None:
                    chosen[slot] = simpler
                    if used() < total:
                        kept = len({v for row in simpler.pixels for v in row})
                        notes[slot] = f"simplified to {kept} colours to fit"
                        break
                    simpler = _fewer_colours(simpler)
                else:
                    chosen[slot] = before
                    stuck.add(slot)
                continue
            if not crests:
                raise RomError(f"The flags need {total} bytes and bank $89 holds {FLAG_ROOM_SIZE}")
            slot = largest[0]
            crests.discard(slot)
            fallback = backup[slot]
            if fallback is None:
                del chosen[slot]
            else:
                chosen[slot] = fallback
            notes[slot] = (
                "did not fit; the cartridge's flag stays"
                if fallbacks[slot] is None
                else "did not fit; two-band flag instead"
            )

        for slot, want in sorted(pins.items()):
            if slot not in chosen:
                continue
            before = chosen[slot]
            chosen[slot] = _pinned(before, want)
            if used() > FLAG_ROOM_SIZE:
                chosen[slot] = before
                notes[slot] = "kept its colour order; the chest design's would not fit"

        where: dict[bytes, int] = {}
        address = _FLAG_ROOM_START
        for team in range(TOTAL_TEAMS):
            for blob in blobs(team):
                if blob not in where:
                    where[blob] = address
                    address += len(blob)
        self._seek(_FLAG_ROOM_START)
        self._file.write(b"\xff" * FLAG_ROOM_SIZE)
        for blob, address in where.items():
            self._seek(address)
            self._file.write(blob)
        self._seek(_OFS_FLAG_TILE_PTRS)
        for team in range(TOTAL_TEAMS):
            self._file.write(b"".join(_encode_p48000(where[blob]) for blob in blobs(team)))

        for slot, flag in chosen.items():
            self._write_flag_colours(slot, flag.colours)
        self._give_own_flag_cells()
        return notes

    def _give_own_flag_cells(self) -> None:
        """Give the eight teams that borrow a partner's cell one of their own.

        The cell then loads the team's own flag, so it shows on every screen;
        an unpatched team's looks as it always did.
        """
        for name, tile in _OWN_FLAG_CELLS.items():
            team = TEAM_ENUM_ORDER.index(name)
            self._seek(_OFS_FLAG_SPRITES + 2 * team)
            raw = self._file.read(2)
            pointer = raw[0] | raw[1] << 8
            record = _BANK_88 + pointer - 0x8000
            self._seek(record)
            if pointer < 0x8000 or self._file.read(1) != b"\x02":
                raise RomError(
                    f"{name}'s flag sprite record at {pointer:#06x} is not two sprites: "
                    "this is not an ISS ROM, or not one this patcher knows"
                )
            self._seek(_OFS_FLAG_CELLS + 2 * team)
            self._file.write(struct.pack("<H", 0x1000 + (tile - 0x100) * 16))
            self._seek(record + 3)
            self._file.write(bytes([(tile + 1) & 0xFF]))
            self._seek(record + 7)
            self._file.write(bytes([tile & 0xFF]))

    @staticmethod
    def _flag_colour_offset(slot: int) -> int:
        """Where palette entries 12-15 of `slot`'s flag sit in its colour record."""
        enum_name = TEAM_ENUM_ORDER[slot]
        if enum_name in _FLAG_COLORS_RANGE1_TEAMS:
            pos = _FLAG_COLORS_RANGE1_TEAMS.index(enum_name)
            return _OFS_FLAG_COLORS_RANGE1 + pos * _FLAG_COLORS_STEP
        pos = _FLAG_COLORS_RANGE2_TEAMS.index(enum_name)
        return _OFS_FLAG_COLORS_RANGE2 + pos * _FLAG_COLORS_STEP

    def _write_flag_colours(self, slot: int, colours: list[tuple[int, int, int]]) -> None:
        self._seek(self._flag_colour_offset(slot))
        self._file.write(struct.pack("<4H", *(_rgb_to_bgr555(*rgb) for rgb in colours)))

    def _shades(self, slot: int, kit: int) -> list[tuple[int, int, int]]:
        """The three shirt shades of `slot`'s home (`kit` 0) or away kit, as
        the ROM holds them."""
        self._seek(_kit_offsets(slot)[kit])
        return [_bgr555_to_rgb(word) for word in struct.unpack("<3H", self._file.read(6))]

    def write_team_name_texts(self, patched_names: dict[int, str]) -> None:
        """Write team names to the selection-screen text data.

        Preserves original ROM data for unpatched teams to avoid overflow
        (original uses combined-character encoding we don't replicate).
        Truncates long patched names if total would exceed available space.

        patched_names: dict of {slot_index: new_name_str} for teams to update.
        """
        self._seek(_OFS_TEAM_NAME_TEXT_PTRS)
        raw_ptrs = self._file.read(TOTAL_TEAMS * 2)

        orig_addrs = []
        orig_data = []
        for i in range(TOTAL_TEAMS):
            b1 = raw_ptrs[i * 2]
            b2 = raw_ptrs[i * 2 + 1]
            addr = _decode_p40000(b1, b2)
            orig_addrs.append(addr)
            self._seek(addr)
            count = self._file.read(1)[0]
            size = 1 + count * 4
            self._seek(addr)
            orig_data.append(self._file.read(size))

        min_addr = min(orig_addrs)
        budget = _MAX_NAME_TEXT_ADDR - min_addr
        # Keep this guard: a negative budget makes `all_data[:budget]` below drop
        # bytes off the *end* and write the rest past the ceiling, and
        # `patch --game iss-snes` never consults `rom_reader.signature_ok`.
        if budget <= 0:
            raise RomError(
                f"The team-name-text pointer table of {self.output_path} points to "
                f"{min_addr:#x}, at or past the {_MAX_NAME_TEXT_ADDR:#x} ceiling: "
                "this is not an ISS ROM, or not one this patcher knows"
            )

        # Patched teams get the new encoding; the rest keep their original blob.
        names_copy = dict(patched_names)
        team_blobs = []
        for i in range(TOTAL_TEAMS):
            if i in names_copy:
                team_blobs.append(_encode_team_name_text(names_copy[i]))
            else:
                team_blobs.append(orig_data[i])

        # Progressively truncate the longest patched names until the data fits.
        while sum(len(b) for b in team_blobs) > budget:
            longest_idx = -1
            longest_len = 0
            for idx in names_copy:
                if len(names_copy[idx]) > 3 and len(team_blobs[idx]) > longest_len:
                    longest_len = len(team_blobs[idx])
                    longest_idx = idx
            if longest_idx < 0:
                break  # Can't shrink further
            names_copy[longest_idx] = names_copy[longest_idx][:-1]
            team_blobs[longest_idx] = _encode_team_name_text(names_copy[longest_idx])

        current_addr = min_addr
        new_pointers = []
        all_data = bytearray()
        for blob in team_blobs:
            new_pointers.append(_encode_p40000(current_addr))
            all_data.extend(blob)
            current_addr += len(blob)

        self._seek(_OFS_TEAM_NAME_TEXT_PTRS)
        for ptr in new_pointers:
            self._file.write(ptr)

        # KNOWN DEFECT, PRESERVED: when the shrink loop gives up with every
        # patched name at three characters and the total still over budget, this
        # slice cuts a blob in half while the pointer table above already names
        # every blob's intended address, so some teams end up pointing at bytes
        # never written and nothing signals it. Unlike `write_name_tiles`, the
        # budget here is read out of the image, so raising or falling back would
        # both be guesses about images nobody here has seen.
        self._seek(min_addr)
        self._file.write(bytes(all_data[:budget]))

    def write_predominant_color(self, enum_index: int, rgb: tuple[int, int, int]) -> None:
        """Write the predominant color byte for a team, the clash rule's.

        0=White, 1=Blue, 2=Red, 3=Yellow, 4=Green
        """
        self.write_clash(enum_index, _rgb_to_predominant(*rgb))

    def write_clash(self, enum_index: int, value: int) -> None:
        """Write the predominant color byte as given, another team's for one."""
        self._seek(_OFS_PREDOMINANT_COLOR + enum_index)
        self._file.write(bytes([value]))

    def write_name_tiles(self, patched_names: dict[int, str]) -> None:
        """Write in-game team name tiles using displacement to free ROM region.

        Displaces Konami-compressed 2bpp tile data from 0x48000+ to 0x17680+.
        Patches 10 ROM code bytes to redirect reads to the new region.
        For patched teams: renders name -> 2bpp tiles -> Konami literal compress.
        For unpatched teams: copies original compressed data as-is.

        patched_names: dict of {slot_index: name_str} for teams to update.

        Call this BEFORE `write_flags`: on a retail ROM the blobs sit in bank
        $89 at 0x483FE-0x48A7F, inside the flag room `write_flags` repacks, and
        the first step below reads them all back. `write_flags` refuses to run
        until the code bytes say they have moved.
        """
        # A ROM this writer already patched keeps the tiles in bank $82 (the
        # code bytes point there): P17000 pointers, biased by 0x8000 from file
        # 0x10000. A retail ROM keeps them in bank $89: P48000, raw offsets from
        # 0x40000.
        self._seek(_DISPLACEMENT_PATCH_POINTS[0])
        relocated = self._file.read(1)[0] == _DISPLACEMENT_PATCH_BYTE
        orig_ptrs = []
        orig_blobs = []
        self._seek(_OFS_NAME_TILES_PTRS)
        raw_ptrs = self._file.read(TOTAL_TEAMS * 2)
        for i in range(TOTAL_TEAMS):
            ptr = raw_ptrs[i * 2] | (raw_ptrs[i * 2 + 1] << 8)
            addr = 0x10000 + ptr - 0x8000 if relocated else 0x40000 + ptr
            orig_ptrs.append(addr)
            # Each blob starts with its own 2-byte LE size.
            self._seek(addr)
            size_bytes = self._file.read(2)
            blob_size = size_bytes[0] | (size_bytes[1] << 8)
            self._seek(addr)
            orig_blobs.append(self._file.read(blob_size))

        new_blobs = []
        for i in range(TOTAL_TEAMS):
            if i in patched_names:
                grid = _render_name_tiles(patched_names[i])
                raw = _serialize_2bpp(grid)
                new_blobs.append(_konami_compress_literal(raw))
            else:
                new_blobs.append(orig_blobs[i])

        total_size = sum(len(b) for b in new_blobs)
        if total_size > NAME_TILES_CAPACITY:
            # Must stay inside this library's hierarchy: a bare `ValueError`
            # escapes a consumer catching `RetroRosterError`.
            raise RomError(f"Name tiles too large: {total_size} bytes > {NAME_TILES_CAPACITY}")

        # Patch the displacement code bytes: bank $89 -> $82.
        for addr in _DISPLACEMENT_PATCH_POINTS:
            self._seek(addr)
            self._file.write(bytes([_DISPLACEMENT_PATCH_BYTE]))

        current_addr = _NAME_TILES_DISPLACED_BASE
        new_ptrs = []
        for blob in new_blobs:
            new_ptrs.append(current_addr)
            self._seek(current_addr)
            self._file.write(blob)
            current_addr += len(blob)

        # The relocated table is P17000, not P48000 like the one read above.
        self._seek(_OFS_NAME_TILES_PTRS)
        for addr in new_ptrs:
            self._file.write(_encode_p17000(addr))

    def write_team_descriptions(self, patched_names: dict[int, str]) -> None:
        """Replace the selection screen's description blurb with the team name.

        Centred in the text box: 15-char wide lines, plain ASCII.

        patched_names: dict of {slot_index: team_name_str}.
        """
        for slot_index, name in patched_names.items():
            self._seek(_OFS_DESC_PTRS + slot_index * 2)
            raw = self._file.read(2)
            snes_addr = raw[0] | (raw[1] << 8)
            # LoROM bank $02: ROM offset = 0x10000 + (snes_addr - 0x8000), which
            # reduces to `0x8000 + snes_addr`. Keep this bank test: a pointer
            # below $8000 addresses 0x8000..0xFFFF instead, where the
            # predominant-colour table (0x8DB2) and the name-tile (0x93CD) and
            # flag-tile (0x941A) pointer tables live.
            if snes_addr < _DESC_BANK_SNES_BASE:
                continue
            rom_addr = _DESC_BANK_BASE + (snes_addr - _DESC_BANK_SNES_BASE)

            # 0xFD is the control byte that starts the description text.
            desc_start = None
            self._seek(rom_addr)
            header = self._file.read(25)
            for j in range(len(header)):
                if header[j] == 0xFD:
                    desc_start = rom_addr + j + 1
                    break
            if desc_start is None:
                continue

            # The block ends at the next FF, or at FE followed by a formation
            # byte (0x24 or 0x2C).
            self._seek(desc_start)
            block = self._file.read(120)
            desc_end = desc_start
            for j in range(len(block)):
                b = block[j]
                if b == 0xFF:
                    desc_end = desc_start + j
                    break
                if b == 0xFE and j + 1 < len(block) and block[j + 1] in (0x24, 0x2C):
                    desc_end = desc_start + j
                    break
            else:
                desc_end = desc_start + len(block)

            available = desc_end - desc_start

            clean = _to_ascii(name).strip()
            lines = []
            words = clean.split()
            current_line = ""
            for word in words:
                if current_line and len(current_line) + 1 + len(word) > _DESC_LINE_WIDTH:
                    lines.append(current_line)
                    current_line = word
                else:
                    current_line = (current_line + " " + word).strip()
            if current_line:
                lines.append(current_line)

            padded_lines = []
            for line in lines:
                if len(line) > _DESC_LINE_WIDTH:
                    line = line[:_DESC_LINE_WIDTH]
                pad_total = _DESC_LINE_WIDTH - len(line)
                pad_left = pad_total // 2
                pad_right = pad_total - pad_left
                padded_lines.append(" " * pad_left + line + " " * pad_right)

            text = "".join(padded_lines)
            if len(text) < available:
                text += " " * (available - len(text))
            text = text[:available]

            self._seek(desc_start)
            self._file.write(text.encode("ascii", errors="replace"))

    def finalize(self) -> None:
        if self._f is not None:
            self._f.flush()
            os.fsync(self._f.fileno())
            self._f.close()
            self._f = None
