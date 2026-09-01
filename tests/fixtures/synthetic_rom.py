"""Fabricate a structurally valid NHL94 Genesis ROM in memory.

`NHL94GenesisRomReader.validate` checks three things: the file is at least 1 MB,
there is a pointer table at 0x030E, and the first pointer lands inside the file.
Everything past that is per-team sections addressed by 16-bit offsets relative to
the team block. This module satisfies all of it, with sections spaced so none
overlaps another and a roster region wide enough for a full 25-player squad.

Nothing here is derived from a real ROM. The team blocks are placed at a round
address well clear of the header, and every stat byte starts at zero.

Every multi-byte field is big-endian, matching `_read_u16_be`/`_read_u32_be` in
the reader and `_write_u16_be` in the writer. The `NHL94GenPlayerRecord`
docstring in `games/nhl94_genesis/models.py` claims the name length is
little-endian; that docstring is wrong, and this module follows the code.
"""

import pathlib
import struct

ROM_SIZE = 1048576  # 0x100000, the standard 1 MB cartridge
POINTER_TABLE_OFFSET = 0x030E
TEAM_COUNT = 26

TEAM_BLOCK_BASE = 0x010000  # first team block, clear of the header and code
TEAM_BLOCK_STRIDE = 0x1000  # 4 KB per team, more than any team needs

# Section offsets relative to a team block. `write_team_header` writes at
# ratings+3 and goalies+1, and 64 bytes of line data at `lines`, so these are
# spaced to keep every section disjoint.
SEC_RATINGS = 0x0060
SEC_GOALIES = 0x0070
SEC_PALETTES = 0x0080  # 64 bytes
SEC_STRINGS = 0x00C0
SEC_LINES = 0x0100  # 8 lines x 8 bytes
SEC_PLAYERS = 0x0200

# Filler player records. Each is 2 length bytes + name + 8 stat bytes, and the
# length field counts itself — the same encoding `read_team_roster` decodes.
FILLER_NAME = b"PLAYER00"
# Deliberately not zero. The reader decodes a name then `.strip("\x00")`s it, so
# against zeroed stat bytes a decoder that read `length` bytes instead of
# `length - 2` would produce the identical string and the over-read would be
# invisible. Byte 0 is the jersey BCD field, hence a legal 11.
FILLER_STATS = b"\x11\x22\x33\x44\x55\x66\x77\x88"
FILLER_RECORD_SIZE = 2 + len(FILLER_NAME) + len(FILLER_STATS)  # 18
ROSTER_PLAYERS = 25
ROSTER_REGION_SIZE = ROSTER_PLAYERS * FILLER_RECORD_SIZE + 2  # + end sentinel

# The city written into each team's strings section. Deliberately not a copy of
# `NHL94_GEN_TEAM_ORDER`: slot 20 is "St Louis" here against "St. Louis" there,
# which is what lets a test tell `current_name` (read from the image) apart from
# `display_name` (read from the constant table).
CITIES = [
    "Anaheim",
    "Boston",
    "Buffalo",
    "Calgary",
    "Chicago",
    "Dallas",
    "Detroit",
    "Edmonton",
    "Florida",
    "Hartford",
    "Los Angeles",
    "Montreal",
    "New Jersey",
    "NY Islanders",
    "NY Rangers",
    "Ottawa",
    "Philadelphia",
    "Pittsburgh",
    "Quebec",
    "San Jose",
    "St Louis",
    "Tampa Bay",
    "Toronto",
    "Vancouver",
    "Washington",
    "Winnipeg",
]

# A second length-prefixed string immediately after the city, as in the real
# strings section. It is there to be in the way: `_read_team_city` reads only the
# first string, so trailing zero padding would let a decoder that took `length`
# bytes rather than `length - 2` return the right city anyway once `.strip("\x00")`
# ran. The next string's length word is not zero, so it cannot.
ABBREVIATIONS = [
    "ANA",
    "BOS",
    "BUF",
    "CGY",
    "CHI",
    "DAL",
    "DET",
    "EDM",
    "FLA",
    "HFD",
    "LAK",
    "MTL",
    "NJD",
    "NYI",
    "NYR",
    "OTT",
    "PHI",
    "PIT",
    "QUE",
    "SJS",
    "STL",
    "TBL",
    "TOR",
    "VAN",
    "WSH",
    "WPG",
]


def _u16(rom: bytearray, offset: int, value: int) -> None:
    struct.pack_into(">H", rom, offset, value)


def team_base(team_index: int) -> int:
    """Absolute file offset of one team's data block."""
    return TEAM_BLOCK_BASE + team_index * TEAM_BLOCK_STRIDE


def filler_record() -> bytes:
    """One complete player record: BE length, then the name, then the stat bytes."""
    return struct.pack(">H", len(FILLER_NAME) + 2) + FILLER_NAME + FILLER_STATS


def build_nhl94_genesis_rom() -> bytearray:
    """Return a 1 MB ROM image the reader accepts and the writer can fill."""
    rom = bytearray(ROM_SIZE)
    rom[0x100:0x110] = b"SEGA GENESIS    "
    rom[0x120:0x130] = b"NHL 94 SYNTHETIC"

    for i in range(TEAM_COUNT):
        base = team_base(i)
        struct.pack_into(">I", rom, POINTER_TABLE_OFFSET + i * 4, base)

        # The six 16-bit section pointers, relative to the team block.
        _u16(rom, base + 0x00, SEC_PLAYERS)
        _u16(rom, base + 0x02, SEC_PALETTES)
        _u16(rom, base + 0x04, SEC_STRINGS)
        _u16(rom, base + 0x06, SEC_LINES)
        _u16(rom, base + 0x08, SEC_RATINGS)
        _u16(rom, base + 0x0A, SEC_GOALIES)

        strings = base + SEC_STRINGS
        for text in (CITIES[i], ABBREVIATIONS[i]):
            encoded = text.encode("ascii")
            _u16(rom, strings, len(encoded) + 2)
            rom[strings + 2 : strings + 2 + len(encoded)] = encoded
            strings += 2 + len(encoded)

        record = filler_record()
        offset = base + SEC_PLAYERS
        for _ in range(ROSTER_PLAYERS):
            rom[offset : offset + FILLER_RECORD_SIZE] = record
            offset += FILLER_RECORD_SIZE
        _u16(rom, offset, 0x0000)  # end-of-roster sentinel

    return rom


def write_nhl94_genesis_rom(path: pathlib.Path) -> pathlib.Path:
    """Write a synthetic ROM to `path` and return it."""
    path.write_bytes(bytes(build_nhl94_genesis_rom()))
    return path
