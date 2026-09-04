"""ISS SNES ROM writer -- patches player and team data into a copy of the ROM.

Offset constants sourced from:
  https://github.com/rodmguerra/issparser (ISS Studio Java editor)
  https://github.com/EstebanFuentealba/web-iss-studio (web port)

ISS (International Superstar Soccer, 1994, SNES) uses a standard .sfc ROM.
27 teams x 15 players = 405 players total.

Player names use ISS custom character encoding (not ASCII).
Player data (abilities, number, hair, special) is a 6-byte interleaved block.
Kit colors use SNES BGR555 (15-bit, little-endian).

This is not a field writer with a table of offsets. Four things in it are ROM
hacking, and none of them is reviewable by reading:

**It patches 65816 machine code.** `write_name_tiles` moves the in-game team-name
tile blobs out of bank $89 and into a free `0xFF` region at 0x17680, and then
overwrites one byte at each of the ten addresses in `_DISPLACEMENT_PATCH_POINTS`
so the code that loads them reads bank $82 instead. Those ten addresses are the
operand bytes of ten instructions in one exact build of the game. **Nothing in
this package checks the ROM's version**, and no check here could: the bytes being
overwritten are not read first, so a different revision is patched just as
willingly and crashes on the team-select screen. `rom_reader.signature_ok` is the
nearest thing to a guard and it is a pointer-table shape test, not a build test.

**It compresses.** `_konami_compress_literal` emits the game's own format --
a 2-byte little-endian total length, then `0x80 | count` control bytes each
followed by up to 31 literal bytes -- but only the literal command, never a back
reference. So its output is *larger* than the data the game shipped, which is
why the tile blobs have to be relocated at all rather than written back in place.

**It rewrites three pointer tables in three different encodings.**
`_encode_p40000` and `_encode_p17000` both bias the high byte by 0x80, because
their targets are read through a SNES address with bit 15 set; `_encode_p48000`
does not, because the flag-tile table stores a raw 16-bit offset from 0x40000.
The three are not interchangeable and each is used for exactly one table.

**It renders a bitmap font.** `_TILE_FONT` is 38 glyphs of hand-written pixels,
`_render_name_tiles` lays them out proportionally into an 8x32 grid with a
one-pixel gap it drops when the name is too wide, and `_serialize_2bpp`
interleaves that into the four 8x8 SNES tiles the game draws. A second, unrelated
font -- `_char_top_tile` / `_char_bottom_tile` plus `_NAME_CHAR_WIDTHS` -- drives
the *selection screen* name, which is a display list of tile ids and x offsets
rather than a bitmap.
"""

from __future__ import annotations

import os
import shutil
import struct
import unicodedata
from types import TracebackType
from typing import IO

from ...core.errors import RomError
from .models import (
    HAIR_STYLES,
    PLAYERS_PER_TEAM,
    TEAM_ENUM_ORDER,
    TOTAL_TEAMS,
    ISSPlayerRecord,
    ISSTeamRecord,
    name_storage_index,
)

# -- Absolute byte offsets (headerless .sfc) ---------------------------------

# Player names: 8 bytes per player, stored in TEAM_NAME_ORDER
_OFS_PLAYER_NAMES = 0x3B62C
_PLAYER_NAME_LENGTH = 8

# Player data: 6 bytes per player, stored in TEAM_ENUM_ORDER
# Byte layout within each 6-byte record:
#   [0] speed data   [1] shooting data   [2] shooting+technique
#   [3] shirt number [4] stamina data    [5] hair_style | special_flag
_OFS_PLAYER_DATA = 0x387EC
_PLAYER_DATA_LENGTH = 6

# Uniform / kit colors
# Outfield kits: 32 bytes per team (shirt 6B + shorts 6B + socks 4B + 16B padding)
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

# Hair/skin colors sit 12 bytes before the kit data in the same blocks.
# Declared for the record: no method in this class writes them, and the two
# outfield kit writers stop after 16 of the 32 bytes precisely so they are left
# alone.
_OFS_HAIR_SKIN1_RANGE1 = 0x2EA2F  # = 0x2EA3B - 12
_OFS_HAIR_SKIN1_RANGE2 = 0x2F0DF  # = 0x2F0EB - 12
_OFS_HAIR_SKIN2_RANGE1 = 0x2ECAF  # = 0x2ECBB - 12
_OFS_HAIR_SKIN2_RANGE2 = 0x2F1DF  # = 0x2F1EB - 12

# Flag tile pointer table: 4 bytes per team (2 pointers x 2 bytes), P48000 format
_OFS_FLAG_TILE_PTRS = 0x941A  # 27 entries, 4 bytes each
# Write new flag tiles AFTER existing data (0x48000-0x483FE) to avoid overwriting
# unpatched teams' original flag graphics. Free space up to 0x48A7F.
_OFS_FLAG_TILE_NEW = 0x48400  # Safe write area for new flag tile data
_FLAG_TILE_PTR_STRIDE = 4

# Flag colors: 4 colors x 2 bytes BGR555 per team, 10-byte step
# Colors: entry0 (COLOR_1/shirt numbers), entry1 (COLOR_2), entry2 (COLOR_3), entry3 (COLOR_4)
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

# Predominant color byte
_OFS_PREDOMINANT_COLOR = 0x8DB2  # 1 byte per team, enum order

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

# -- Kit color team orderings ------------------------------------------------
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


# -- Team name text character widths (pixels) --------------------------------
_NAME_CHAR_WIDTHS = {"I": 7, ".": 7, "M": 8, "N": 8, "T": 8, "W": 8}
_NAME_SPACE_WIDTH = 3
_NAME_DEFAULT_WIDTH = 9
_MAX_NAME_WIDTH = 70

# -- Shooting/technique value table (3-bit -> odd values 1-15) ---------------
_SHOOTING_VALUES = [1, 3, 5, 7, 9, 11, 13, 15]


def _build_char_to_byte() -> dict[str, int]:
    """The ISS font's character-to-byte table.

    DELIBERATE DIVERGENCE, and it moves no byte. Upstream declared an empty
    module-level dict and filled it by side effect from `_init_encoding()`,
    called from three places -- `ISSRomWriter.__init__`, `_encode_iss_name` and,
    in `rom_reader`, a second copy of the same table filled by a second copy of
    the same function. Two consequences: the table's contents depended on
    whether anything had happened to call the initialiser yet, so importing
    `_encode_iss_name` alone and calling it worked only because it re-checked;
    and the two modules' tables could drift. Built once, here, and returned
    rather than mutated in place.
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


def _to_ascii(text: str) -> str:
    """Strip diacritics and non-ASCII chars."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c) and ord(c) < 128)


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


def _shooting_to_rom(value: int) -> int:
    """Map a shooting/technique value (1-15) to the 3-bit ROM index (0-7)."""
    # Find closest value in the table
    best_idx = 0
    best_dist = abs(_SHOOTING_VALUES[0] - value)
    for i, sv in enumerate(_SHOOTING_VALUES):
        d = abs(sv - value)
        if d < best_dist:
            best_dist = d
            best_idx = i
    return best_idx


def _speed_to_rom(value: int) -> int:
    """Encode speed value (1-16) to the ROM byte format.

    From SkillData.java:
      if (b % 0x20 == 0) return b / 0x20 + 1;
      return ((int) b + 1) / 0x20 + 8;

    We need the inverse. Values 1-7 -> multiples of 0x20 (0x00, 0x20, ..., 0xC0).
    Values 8-16 -> non-multiples: approximate as (value - 8) * 0x20 + some offset.
    Simplified: clamp to 0x00-0xE0 range in 0x20 steps.

    INHERITED DEFECT, ported unchanged. The comment above is upstream's and says
    what it is: an approximation. It is exact for 1-7, and for 8-16 it is not an
    inverse at all -- `(value - 8) * 0x20 - 1` is a multiple of 0x20 minus one,
    so the decoder's first branch does not fire and its second answers
    `((v-8)*0x20 - 1 + 1) / 0x20 + 8 = v`, which round-trips for 9-16 but sends
    8 to `max(0, -1) = 0`, and 0 is a multiple of 0x20 and decodes to 1. So the
    single value 8 -- the scale's midpoint and `ISSPlayerAttributes.speed`'s
    default -- reads back as the minimum. Fixing it changes what reaches the
    cartridge and cannot be verified from inside this repository, so it is
    reported rather than corrected.
    """
    clamped = max(1, min(16, value))
    # Simple linear mapping: value 1->0x00, 8->0xE0, 16->0xE0
    # Based on the decoder, multiples of 0x20 decode to b/0x20+1
    # So value V -> (V-1)*0x20 for V in 1..7
    if clamped <= 7:
        return (clamped - 1) * 0x20
    # For 8-16, use non-multiples: ((V-8)*0x20) + some offset
    # decoder: ((b+1)/0x20) + 8 = V -> b = (V-8)*0x20 - 1
    return max(0, (clamped - 8) * 0x20 - 1) & 0xFF


def _char_top_tile(c: str) -> int | None:
    """Get the top-half tile ID for a character, or None if no top half."""
    if "A" <= c <= "P":
        return 0xC0 + (ord(c) - ord("A"))
    if "Q" <= c <= "Z":
        return 0xE0 + (ord(c) - ord("Q"))
    if "0" <= c <= "9":
        return 0xA0 + (ord(c) - ord("0"))
    return None


def _char_bottom_tile(c: str) -> int | None:
    """Get the bottom-half tile ID for a character, or None if no bottom half."""
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
    # Build (char, x_position) list
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
    # Compress if too wide
    if total_width > _MAX_NAME_WIDTH and total_width > 0:
        scale = _MAX_NAME_WIDTH / total_width
        chars = [(c, int(xp * scale), t, b) for c, xp, t, b in chars]
        total_width = _MAX_NAME_WIDTH

    # Center: shift all x positions
    half = total_width // 2
    entries = []
    # Characters in reverse order (right-to-left), bottom (F9) before top (F1)
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
    set. A `b2` below 0x80 therefore makes this answer an offset *below* 0x40000
    -- and Python's `|` on a negative left operand keeps it negative, so the
    result is a negative file offset. `rom_reader.signature_ok` refuses an image
    whose table contains one; there is no in-band way for this function to
    report it.
    """
    return 0x40000 | ((b2 - 0x80) << 8) | b1


def _encode_p40000(address: int) -> bytes:
    """Encode ROM file offset as P40000 pointer bytes [low, high].

    Valid for addresses in 0x40000..0x47FFF. Above that the biased high byte
    exceeds 0xFF and `bytes()` raises `ValueError`; `write_team_name_texts` is
    the only caller and every address it passes is below `_MAX_NAME_TEXT_ADDR`,
    which is 0x44478.
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
        # Bright color: base is lightest, darken for shadow shades
        for i in range(count):
            t = (count - 1 - i) / (count - 1) * 0.5 if count > 1 else 0
            rv = max(0, round(r5 * (1.0 - t)))
            gv = max(0, round(g5 * (1.0 - t)))
            bv = max(0, round(b5 * (1.0 - t)))
            shades.append(rv | (gv << 5) | (bv << 10))
    else:
        # Dark/medium: base is darkest, lighter shades blend toward white
        for i in range(count):
            t = i / (count - 1) * 0.5 if count > 1 else 0
            rv = min(31, round(r5 + t * (31 - r5)))
            gv = min(31, round(g5 + t * (31 - g5)))
            bv = min(31, round(b5 + t * (31 - b5)))
            shades.append(rv | (gv << 5) | (bv << 10))
    return shades


def _rgb_to_predominant(r: int, g: int, b: int) -> int:
    """Map RGB to ISS predominant color (0=White,1=Blue,2=Red,3=Yellow,4=Green)."""
    max_c = max(r, g, b)
    min_c = min(r, g, b)
    if max_c > 200 and (max_c - min_c) < 50:
        return 0  # White
    if r >= g and r >= b:
        if g > 150 and g > b:
            return 3  # Yellow
        return 2  # Red
    if g >= r and g >= b:
        return 4  # Green
    return 1  # Blue


# -- In-game team name tile font (5px wide x 8px tall, 2bpp) -----------------
# 0=TRANSPARENT, 1=COLOR_1 (white stroke)
# Row 0 and 7 are top/bottom borders (mostly transparent)


def _f(s: str) -> list[list[int]]:
    """Parse a compact font string into rows of pixel values."""
    return [[int(c) for c in row] for row in s.strip().split("/")]


#: 38 glyphs. `_render_name_tiles` skips any character not present, so the set of
#: keys is also the set of characters that can reach an in-game name tile.
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

    Returns list of 8 rows, each a list of 32 ints (0-3).
    """
    clean = _to_ascii(name).upper()
    # Collect letter bitmaps and widths
    letters = []
    for c in clean:
        glyph = _TILE_FONT.get(c)
        if glyph is None:
            continue
        w = len(glyph[0]) if glyph else 0
        letters.append((glyph, w))

    # Calculate total width (letters + 1px gap between)
    if not letters:
        letters = [(_TILE_FONT["A"], len(_TILE_FONT["A"][0]))]
    total_w = sum(w for _, w in letters) + max(0, len(letters) - 1)

    # Scale down if too wide
    gap = 1
    if total_w > _TILE_COLS:
        gap = 0
        total_w = sum(w for _, w in letters)

    # Initialize grid with transparent
    grid = [[_TC_TRANSPARENT] * _TILE_COLS for _ in range(_TILE_ROWS)]

    # Center horizontally
    start_x = max(0, (_TILE_COLS - total_w) // 2)
    x = start_x

    for glyph, w in letters:
        if x + w > _TILE_COLS:
            break
        # Draw shadow background (1px padding around letter area)
        for row in range(_TILE_ROWS):
            for col in range(max(0, x - 1), min(_TILE_COLS, x + w + 1)):
                if grid[row][col] == _TC_TRANSPARENT:
                    grid[row][col] = _TC_SHADOW
        # Draw letter strokes (rows 1-6 of the 8-pixel height)
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

    Format: [2-byte LE size header] [0x80|count] [data...] ...
    RAW command: control byte 0x80|count, followed by count literal bytes (max 31).
    Size header = total compressed stream length including header.

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
    # Write size header (total length including header)
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


def _make_solid_4bpp_tile(color_code: int) -> bytes:
    """Create a solid 8x8 4bpp SNES tile (32 bytes) filled with one color.

    4bpp interleaved: bytes 0-15 = bitplanes 0,1; bytes 16-31 = bitplanes 2,3.
    Each row = 2 bytes per bitplane pair.
    """
    bp0 = 0xFF if (color_code & 1) else 0x00
    bp1 = 0xFF if (color_code & 2) else 0x00
    bp2 = 0xFF if (color_code & 4) else 0x00
    bp3 = 0xFF if (color_code & 8) else 0x00

    data = bytearray(32)
    for row in range(8):
        data[row * 2] = bp0  # bitplane 0
        data[row * 2 + 1] = bp1  # bitplane 1
        data[16 + row * 2] = bp2  # bitplane 2
        data[16 + row * 2 + 1] = bp3  # bitplane 3
    return bytes(data)


# 4bpp color codes for flag tiles (SNES palette indices)
_FLAG_COLOR_1 = 0x0C  # Palette index 12 -> flag_color_1 (primary)
_FLAG_COLOR_2 = 0x0D  # Palette index 13 -> flag_color_2 (alternate)


def _make_flag_half(color_code: int) -> bytes:
    """Create one flag half: 3 solid tiles side by side (96 bytes decompressed).

    ISS flags are 24x16 pixels (3 tiles wide x 2 halves of 8 rows).
    """
    tile = _make_solid_4bpp_tile(color_code)
    return tile + tile + tile


#: Bytes the two compressed flag halves occupy at `_OFS_FLAG_TILE_NEW`.
#:
#: Computed by running the compressor over the data it will actually be given,
#: not transcribed. Both halves are the same length -- one solid colour each --
#: so the pair is twice one of them.
_FLAG_TILE_BLOB_SIZE = 2 * len(_konami_compress_literal(_make_flag_half(_FLAG_COLOR_1)))


def _min_patchable_size() -> int:
    """Highest file offset any write in this module reaches, in headerless bytes.

    IMPROVEMENT, with no upstream equivalent, and the reason it exists is that
    upstream's only size test was a 1 MB floor -- three and a half times more
    than the writer actually needs, and a guess about which cartridge this is
    rather than a statement about whether the data fits.

    This is the other kind of test. Every write below is at a fixed offset, and
    the file is opened `r+b`, so seeking past the end and writing *extends* the
    file: handed a 300 KB file the writer produces a 297 KB "patched ROM" made
    of a hole and a flag. A file shorter than this therefore provably cannot be
    patched, which is why `patcher.py` applies it to `patch` as well as to
    `analyze_rom`, where the 1 MB floor guards only the latter.

    Every term is a `max` over this module's own constants rather than a
    transcribed total, so moving any offset moves this with it. The list is
    therefore deliberately over-complete: one term dominates all the others
    today, so deleting any of the rest changes nothing and no test can tell.
    Mutation testing confirms that -- three dropped terms survive, and they are
    the only survivors in this package -- and it is the price of a bound that
    follows an offset when one of them moves. Reads are not
    included: `write_name_tiles` dereferences the ROM's own pointers into
    0x40000..0x4FFFF and a short read there answers fewer bytes rather than
    corrupting anything, and `rom_reader.signature_ok` is what rejects it.
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
        _OFS_FLAG_TILE_NEW + _FLAG_TILE_BLOB_SIZE,
        _OFS_PREDOMINANT_COLOR + TOTAL_TEAMS,
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

    `__init__` has two side effects -- it `shutil.copy2`s the input over the
    output and leaves a handle open on `self` -- and both are upstream's.
    `RomWriter` in `games/we2002` does the same thing. What is new here is
    `close`, and the context-manager protocol built on it: upstream's only way
    to release the handle was `finalize`, so any exception between the
    constructor and it -- and `write_name_tiles` raises one by design -- leaked
    the descriptor and left a half-written file behind. `patch` uses `with`.
    """

    def __init__(self, rom_path: str, output_path: str, header_offset: int = 0):
        self.output_path = output_path
        self.header_offset = header_offset

        # Copy ROM to output
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

        Idempotent, and `finalize` leaves nothing for it to do -- which is what
        makes `with ISSRomWriter(...) as w: ...; w.finalize()` correct rather
        than a double close.
        """
        if self._f is not None:
            self._f.close()
            self._f = None

    @property
    def _file(self) -> IO[bytes]:
        """The open handle, or a `RomError` naming what happened to it.

        Upstream's methods went straight to `self._f`, which `finalize` sets to
        `None`; a second write after finalising raised `AttributeError: 'NoneType'
        object has no attribute 'seek'` from inside the writer, which is outside
        this library's exception hierarchy.
        """
        if self._f is None:
            raise RomError(f"The ISS ROM writer for {self.output_path} is already closed")
        return self._f

    def _seek(self, offset: int) -> None:
        """Seek to a ROM offset, accounting for copier header."""
        self._file.seek(offset + self.header_offset)

    def write_player_names(self, enum_index: int, players: list[ISSPlayerRecord]) -> int:
        """Write player names for a team, and return how many were written.

        Names are stored in TEAM_NAME_ORDER, so we need to convert from
        enum_index to the name storage index.
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
        """Write player abilities, shirt numbers, hair and the special flag.

        Player data is stored in TEAM_ENUM_ORDER.
        6 bytes per player:
          [0] speed   [1] shooting (high nibble)
          [2] shooting(low) + technique(high)  [3] shirt_number
          [4] stamina [5] hair_style | special_flag

        Returns the number of records written, which is the squad size capped at
        `PLAYERS_PER_TEAM`. Upstream returned `None`; `PatchResult.players_patched`
        is defined in `core/models.py` as records that reached the image, and
        counting the squad handed in would over-report a 22-man list by seven.
        """
        base = _OFS_PLAYER_DATA + enum_index * PLAYERS_PER_TEAM * _PLAYER_DATA_LENGTH
        written = 0
        for i, player in enumerate(players[:PLAYERS_PER_TEAM]):
            self._seek(base + i * _PLAYER_DATA_LENGTH)
            existing = bytearray(self._file.read(_PLAYER_DATA_LENGTH))
            self._seek(base + i * _PLAYER_DATA_LENGTH)

            attrs = player.attributes

            # Byte 0: speed
            existing[0] = _speed_to_rom(attrs.speed)

            # Bytes 1-2: shooting + technique
            # Shooting is stored as a 3-bit value in the data
            shoot_idx = _shooting_to_rom(attrs.shooting)
            tech_idx = _shooting_to_rom(attrs.technique)
            # Preserve non-ability bits, write ability bits
            existing[1] = (existing[1] & 0xF8) | (shoot_idx & 0x07)
            existing[2] = (existing[2] & 0xF8) | (tech_idx & 0x07)

            # Byte 3: shirt number (low nibble = number - 1, preserve high nibble)
            number = max(1, min(16, player.shirt_number))
            existing[3] = (existing[3] & 0xF0) | ((number - 1) & 0x0F)

            # Byte 4: stamina (low nibble = stamina - 1)
            stamina = max(1, min(16, attrs.stamina))
            existing[4] = (existing[4] & 0xF0) | ((stamina - 1) & 0x0F)

            # Byte 5: hair_style (low nibble) | special (bit 6). The clamp is
            # `len(HAIR_STYLES) - 1` rather than upstream's literal 10, so adding
            # a style to that list is one edit and not two.
            hair = max(0, min(len(HAIR_STYLES) - 1, player.hair_style))
            special_bit = 0x40 if player.is_special else 0x00
            existing[5] = (existing[5] & 0xB0) | special_bit | (hair & 0x0F)

            self._file.write(bytes(existing))
            written += 1
        return written

    def write_kit_colors(self, enum_index: int, team: ISSTeamRecord) -> None:
        """Write kit colors (home, away, GK) for a team.

        Outfield kits are 32 bytes: 6B shirt + 6B shorts + 4B socks + 16B extra
        GK kits are 24 bytes: 10B shirt + 2B shorts + 12B extra
        Colors are BGR555 little-endian.
        """
        enum_name = TEAM_ENUM_ORDER[enum_index]

        # Determine which range and position
        if enum_name in _KIT_RANGE1_TEAMS:
            kit1_base = _OFS_KIT1_RANGE1
            kit2_base = _OFS_KIT2_RANGE1
            pos = _KIT_RANGE1_TEAMS.index(enum_name)
        elif enum_name in _KIT_RANGE2_TEAMS:
            kit1_base = _OFS_KIT1_RANGE2
            kit2_base = _OFS_KIT2_RANGE2
            pos = _KIT_RANGE2_TEAMS.index(enum_name)
        else:
            return

        # Write home kit (1st kit)
        if team.kit_home:
            self._write_outfield_kit(kit1_base + pos * _OUTFIELD_KIT_STRIDE, team.kit_home)

        # Write away kit (2nd kit)
        if team.kit_away:
            self._write_outfield_kit(kit2_base + pos * _OUTFIELD_KIT_STRIDE, team.kit_away)

        # Write GK kit
        if team.kit_gk:
            if enum_name in _GK_RANGE1_TEAMS:
                gk_base = _OFS_GK_RANGE1
                gk_pos = _GK_RANGE1_TEAMS.index(enum_name)
            elif enum_name in _KIT_RANGE2_TEAMS:
                gk_base = _OFS_GK_RANGE2
                gk_pos = _KIT_RANGE2_TEAMS.index(enum_name)
            else:
                return
            self._write_gk_kit(gk_base + gk_pos * _GK_KIT_STRIDE, team.kit_gk)

    def _write_outfield_kit(self, offset: int, colors: tuple[tuple[int, int, int], ...]) -> None:
        """Write shirt/shorts/socks with proper shade gradients (dark->light).

        32-byte block: words 0-2 shirt, words 3-5 shorts, words 6-7 socks,
        words 8-15 hair/skin (untouched).
        """
        if not colors:
            return
        self._seek(offset)
        existing = bytearray(self._file.read(16))
        self._seek(offset)

        shirt_color = colors[0] if len(colors) > 0 else (255, 255, 255)
        shorts_color = colors[1] if len(colors) > 1 else shirt_color
        socks_color = colors[2] if len(colors) > 2 else shirt_color

        # Shirt: 3 shades (bytes 0-5)
        for i, shade in enumerate(_make_shades(*shirt_color, 3)):
            struct.pack_into("<H", existing, i * 2, shade)

        # Shorts: 3 shades (bytes 6-11)
        for i, shade in enumerate(_make_shades(*shorts_color, 3)):
            struct.pack_into("<H", existing, 6 + i * 2, shade)

        # Socks: 2 shades (bytes 12-15)
        for i, shade in enumerate(_make_shades(*socks_color, 2)):
            struct.pack_into("<H", existing, 12 + i * 2, shade)

        self._file.write(bytes(existing))

    def _write_gk_kit(self, offset: int, colors: tuple[tuple[int, int, int], ...]) -> None:
        """Write GK kit with proper shade gradients.

        24-byte block: word 0 specular (always near-white), words 1-4 shirt
        (4 shades dark->light), word 5 shorts, words 6-11 hair/skin (untouched).
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

    def write_flag_tiles_and_colors(
        self,
        patched_colors: dict[int, tuple[tuple[int, int, int], tuple[int, int, int]]],
    ) -> None:
        """Write simple two-band flag tiles and team colors for all patched teams.

        Creates a simple rectangular flag design (top=primary, bottom=alternate)
        and writes it once. Points all patched team flag entries to this design.
        Sets each team's flag palette to its primary/alternate colors.

        patched_colors: dict of {slot_index: (primary_rgb, alt_rgb)} for teams.
        """
        # Step 1: Create simple two-band flag tile data
        # Top half: all COLOR_1 (primary), Bottom half: all COLOR_2 (alternate)
        top_raw = _make_flag_half(_FLAG_COLOR_1)
        bot_raw = _make_flag_half(_FLAG_COLOR_2)
        top_compressed = _konami_compress_literal(top_raw)
        bot_compressed = _konami_compress_literal(bot_raw)

        # Step 2: Write both compressed halves after existing flag data
        top_addr = _OFS_FLAG_TILE_NEW
        bot_addr = top_addr + len(top_compressed)
        self._seek(top_addr)
        self._file.write(top_compressed)
        self._seek(bot_addr)
        self._file.write(bot_compressed)

        # Build P48000 pointer entry for our flag (4 bytes: top_ptr + bot_ptr)
        flag_entry = _encode_p48000(top_addr) + _encode_p48000(bot_addr)

        # Step 3: Update flag pointer table for patched teams
        for slot_index in patched_colors:
            self._seek(_OFS_FLAG_TILE_PTRS + slot_index * _FLAG_TILE_PTR_STRIDE)
            self._file.write(flag_entry)

        # Step 4: Write flag colors for each patched team
        for slot_index, (primary, alt) in patched_colors.items():
            enum_name = TEAM_ENUM_ORDER[slot_index]

            # Find color address via range lookup (step=10)
            if enum_name in _FLAG_COLORS_RANGE1_TEAMS:
                pos = _FLAG_COLORS_RANGE1_TEAMS.index(enum_name)
                addr = _OFS_FLAG_COLORS_RANGE1 + pos * _FLAG_COLORS_STEP
            elif enum_name in _FLAG_COLORS_RANGE2_TEAMS:
                pos = _FLAG_COLORS_RANGE2_TEAMS.index(enum_name)
                addr = _OFS_FLAG_COLORS_RANGE2 + pos * _FLAG_COLORS_STEP
            else:
                continue

            # 8 bytes: entry0=COLOR_1 (primary), entry1=COLOR_2 (alt),
            # entry2=COLOR_3, entry3=COLOR_4
            # Our flag top half uses COLOR_1 (palette 12), bottom uses COLOR_2 (palette 13)
            data = bytearray(8)
            struct.pack_into("<H", data, 0, _rgb_to_bgr555(*primary))  # COLOR_1 (top half)
            struct.pack_into("<H", data, 2, _rgb_to_bgr555(*alt))  # COLOR_2 (bottom half)
            struct.pack_into("<H", data, 4, _rgb_to_bgr555(*primary))  # COLOR_3 (unused)
            struct.pack_into("<H", data, 6, _rgb_to_bgr555(*alt))  # COLOR_4 (unused)
            self._seek(addr)
            self._file.write(bytes(data))

    def write_team_name_texts(self, patched_names: dict[int, str]) -> None:
        """Write team names to the selection-screen text data.

        Preserves original ROM data for unpatched teams to avoid overflow
        (original uses combined-character encoding we don't replicate).
        Truncates long patched names if total would exceed available space.

        patched_names: dict of {slot_index: new_name_str} for teams to update.
        """
        # Read existing pointer table and data
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
        # DELIBERATE DIVERGENCE: upstream had no guard here. A non-negative
        # budget is a precondition of the last statement in this method --
        # `all_data[:budget]` with a negative budget is not a truncation, it is
        # a slice that drops bytes from the *end* and then writes what is left
        # at `min_addr`, which is by definition past the ceiling this constant
        # exists to protect. `rom_reader.signature_ok` refuses such an image
        # from `analyze`, but `patch --game iss-snes` does not consult it.
        if budget <= 0:
            raise RomError(
                f"The team-name-text pointer table of {self.output_path} points to "
                f"{min_addr:#x}, at or past the {_MAX_NAME_TEXT_ADDR:#x} ceiling: "
                "this is not an ISS ROM, or not one this patcher knows"
            )

        # Build team data blobs -- patched teams get new encoding, others keep original
        names_copy = dict(patched_names)
        team_blobs = []
        for i in range(TOTAL_TEAMS):
            if i in names_copy:
                team_blobs.append(_encode_team_name_text(names_copy[i]))
            else:
                team_blobs.append(orig_data[i])

        # Progressively truncate longest patched names until data fits
        while sum(len(b) for b in team_blobs) > budget:
            # Find the longest patched name we can still shorten
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

        # Build final data with pointers
        current_addr = min_addr
        new_pointers = []
        all_data = bytearray()
        for blob in team_blobs:
            new_pointers.append(_encode_p40000(current_addr))
            all_data.extend(blob)
            current_addr += len(blob)

        # Write pointer table
        self._seek(_OFS_TEAM_NAME_TEXT_PTRS)
        for ptr in new_pointers:
            self._file.write(ptr)

        # Write name data (capped at budget for safety).
        #
        # INHERITED DEFECT, ported unchanged: the `break` above gives up once
        # every patched name is down to three characters, and if the total is
        # still over budget this slice cuts the tail off mid-blob while the
        # pointer table written just above still names the addresses the blobs
        # would have had. The teams past the cut then draw whatever follows.
        # Fixing it means either refusing the patch or falling back to the
        # original names, and choosing between those needs a real ROM's own
        # blob sizes, which this repository has none of.
        self._seek(min_addr)
        self._file.write(bytes(all_data[:budget]))

    def write_predominant_color(self, enum_index: int, rgb: tuple[int, int, int]) -> None:
        """Write the predominant color byte for a team.

        0=White, 1=Blue, 2=Red, 3=Yellow, 4=Green
        """
        color = _rgb_to_predominant(*rgb)
        self._seek(_OFS_PREDOMINANT_COLOR + enum_index)
        self._file.write(bytes([color]))

    def write_name_tiles(self, patched_names: dict[int, str]) -> None:
        """Write in-game team name tiles using displacement to free ROM region.

        Displaces Konami-compressed 2bpp tile data from 0x48000+ to 0x17680+.
        Patches 10 ROM code bytes to redirect reads to the new region.
        For patched teams: renders name -> 2bpp tiles -> Konami literal compress.
        For unpatched teams: copies original compressed data as-is.

        patched_names: dict of {slot_index: name_str} for teams to update.

        Call this LAST of the writes that touch 0x48000 and up.
        `write_flag_tiles_and_colors` writes 204 bytes at 0x48400, and Step 1
        below reads the original blobs back out of the same bank; whether they
        overlap depends on where the game's own tile data sits, which no test in
        this repository can establish. `patcher.patch` preserves upstream's
        order for exactly that reason.
        """
        # Step 1: Read original pointer table and compressed data for all 27 teams
        orig_ptrs = []
        orig_blobs = []
        self._seek(_OFS_NAME_TILES_PTRS)
        raw_ptrs = self._file.read(TOTAL_TEAMS * 2)
        for i in range(TOTAL_TEAMS):
            b1 = raw_ptrs[i * 2]
            b2 = raw_ptrs[i * 2 + 1]
            # Original pointers use P48000 format (SNES bank $89):
            # addr = 0x40000 + raw 16-bit LE pointer
            addr = 0x40000 + b1 + (b2 << 8)
            orig_ptrs.append(addr)
            # Read compressed blob (starts with 2-byte LE size)
            self._seek(addr)
            size_bytes = self._file.read(2)
            blob_size = size_bytes[0] | (size_bytes[1] << 8)
            self._seek(addr)
            orig_blobs.append(self._file.read(blob_size))

        # Step 2: Build new compressed blobs for each team
        new_blobs = []
        for i in range(TOTAL_TEAMS):
            if i in patched_names:
                grid = _render_name_tiles(patched_names[i])
                raw = _serialize_2bpp(grid)
                new_blobs.append(_konami_compress_literal(raw))
            else:
                new_blobs.append(orig_blobs[i])

        # Step 3: Verify total fits in displaced region
        total_size = sum(len(b) for b in new_blobs)
        if total_size > NAME_TILES_CAPACITY:
            # DELIBERATE DIVERGENCE: upstream raised a bare `ValueError`, which
            # is outside this library's exception hierarchy, so a consumer
            # catching `RetroRosterError` did not catch it. Same condition, same
            # message content, correct type.
            raise RomError(f"Name tiles too large: {total_size} bytes > {NAME_TILES_CAPACITY}")

        # Step 4: Patch displacement code bytes (0x89 -> 0x82)
        for addr in _DISPLACEMENT_PATCH_POINTS:
            self._seek(addr)
            self._file.write(bytes([_DISPLACEMENT_PATCH_BYTE]))

        # Step 5: Write all compressed blobs to displaced region
        current_addr = _NAME_TILES_DISPLACED_BASE
        new_ptrs = []
        for blob in new_blobs:
            new_ptrs.append(current_addr)
            self._seek(current_addr)
            self._file.write(blob)
            current_addr += len(blob)

        # Step 6: Update pointer table with P17000 pointers
        self._seek(_OFS_NAME_TILES_PTRS)
        for addr in new_ptrs:
            self._file.write(_encode_p17000(addr))

    def write_team_descriptions(self, patched_names: dict[int, str]) -> None:
        """Replace team description text with the full team name.

        The team selection screen shows a description blurb for each team.
        For patched teams, replace it with the real team name centered in
        the text box (15-char wide lines, plain ASCII).

        patched_names: dict of {slot_index: team_name_str}.
        """
        for slot_index, name in patched_names.items():
            # Read this team's description pointer from the table
            self._seek(_OFS_DESC_PTRS + slot_index * 2)
            raw = self._file.read(2)
            snes_addr = raw[0] | (raw[1] << 8)
            # LoROM bank $02: ROM offset = 0x10000 + (snes_addr - 0x8000)
            #
            # DELIBERATE DIVERGENCE: the bank test is new. The arithmetic
            # above reduces to `0x8000 + snes_addr`, so a pointer below $8000
            # -- which is not a bank-$02 pointer at all -- addresses
            # 0x8000..0xFFFF, one bank low. That range is not empty: it holds
            # the predominant-colour table at 0x8DB2, the name-tile pointer
            # table at 0x93CD and the flag-tile pointer table at 0x941A. If a
            # 0xFD turns up in the 25 bytes read there, upstream wrote 60 bytes
            # of description text over whichever of them followed. Skipping the
            # slot leaves its 1994 description in place, which is what an
            # unpatched team gets.
            if snes_addr < _DESC_BANK_SNES_BASE:
                continue
            rom_addr = _DESC_BANK_BASE + (snes_addr - _DESC_BANK_SNES_BASE)

            # Find the FD control byte that starts the description text
            desc_start = None
            self._seek(rom_addr)
            header = self._file.read(25)
            for j in range(len(header)):
                if header[j] == 0xFD:
                    desc_start = rom_addr + j + 1
                    break
            if desc_start is None:
                continue

            # Find the end of this description block (next FE+formation or FF)
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

            # Format the team name centered in 15-char lines
            clean = _to_ascii(name).strip()
            lines = []
            # Word-wrap the name across lines
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

            # Center each line and pad to 15 chars
            padded_lines = []
            for line in lines:
                if len(line) > _DESC_LINE_WIDTH:
                    line = line[:_DESC_LINE_WIDTH]
                pad_total = _DESC_LINE_WIDTH - len(line)
                pad_left = pad_total // 2
                pad_right = pad_total - pad_left
                padded_lines.append(" " * pad_left + line + " " * pad_right)

            text = "".join(padded_lines)
            # Pad remaining space with spaces
            if len(text) < available:
                text += " " * (available - len(text))
            text = text[:available]

            self._seek(desc_start)
            self._file.write(text.encode("ascii", errors="replace"))

    def finalize(self) -> None:
        """Flush, sync, and close the output file."""
        if self._f is not None:
            self._f.flush()
            os.fsync(self._f.fileno())
            self._f.close()
            self._f = None
