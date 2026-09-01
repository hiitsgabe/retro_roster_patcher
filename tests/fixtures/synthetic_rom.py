"""Fabricate a structurally valid NHL94 Genesis ROM in memory.

`NHL94GenesisRomReader.validate` checks three things: the file is at least 1 MB,
there is a pointer table at 0x030E, and the first pointer lands inside the file.
Everything past that is per-team sections addressed by 16-bit offsets relative to
the team block. This module satisfies all of it, with sections spaced so none
overlaps another and a roster region wide enough for a full 25-player squad.

Nothing here is derived from a real ROM. The team blocks are placed at a round
address well clear of the header.

Every player record is self-identifying: its name and its stat bytes both encode
the team index and the roster slot it was written to. Uniform filler would make
the 26 team blocks byte-identical, and then no assertion could tell which team a
read came from — a reader that ignored `team_index` entirely, or that returned a
roster reversed or with its stat blocks rotated by one, would satisfy every
equality a test could write.

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

# Player records. Each is 2 length bytes + name + 8 stat bytes, and the length
# field counts itself — the same encoding `read_team_roster` decodes. The roster
# region is therefore 25 * 18 + 2 = 452 bytes; the reader tests pin that as a
# literal rather than importing it back from here.
PLAYER_NAME_SIZE = 8
PLAYER_STATS_SIZE = 8
PLAYER_RECORD_SIZE = 2 + PLAYER_NAME_SIZE + PLAYER_STATS_SIZE  # 18
ROSTER_PLAYERS = 25

# Stat bytes 3-7, identical in every record. Every stat byte in this module stays
# inside the range `rom_writer._write_player_stats` can actually emit: byte 0 is a
# BCD jersey, and `encode_nibble` clamps both nibbles of the rest to 0-6, so a
# byte like 0x77 is one the writer could never produce and a round-trip against it
# would be unsatisfiable.
STAT_PADDING = b"\x33\x44\x55\x66\x12"

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


def _nibble_pair(value: int) -> int:
    """Pack 0-48 into one byte whose two nibbles are both inside the writer's 0-6.

    Base 7, because a nibble the writer can emit holds seven values. Both 26 teams
    and 25 slots fit, so a single byte can carry either one without going outside
    what `encode_nibble` will produce.
    """
    return (value // 7) << 4 | (value % 7)


def player_name(team_index: int, slot: int) -> str:
    """The name in one roster slot. Always 8 bytes, so records stay 18 bytes."""
    return f"T{team_index:02d}_PL{slot:02d}"


def player_stats(team_index: int, slot: int) -> bytes:
    """The eight stat bytes for one roster slot, identifying both coordinates.

    Byte 0 is the jersey number in BCD and so is never zero, which matters: the
    reader decodes a name and then `.strip("\\x00")`s it, so a decoder that read
    `length` bytes instead of `length - 2` would spill into bytes 0 and 1 here. A
    zero byte 0 would be stripped straight back off and the over-read would leave
    the name looking correct.
    """
    jersey = slot + 1
    identity = bytes(
        [
            (jersey // 10) << 4 | jersey % 10,
            _nibble_pair(team_index),
            _nibble_pair(slot),
        ]
    )
    return identity + STAT_PADDING


def decode_player_stats(stats: bytes) -> dict[str, int]:
    """Split the eight stat bytes back into the fourteen values they carry.

    Transcribed from the byte layout in `rom_writer._write_player_stats`'s
    docstring, deliberately not from its code: `src/` ships no inverse of
    `encode_nibble`/`encode_weight_nibble`, so a decoder derived from the writer's
    own statements would agree with any rearrangement of them. Byte 0 is BCD, the
    rest are two nibbles each, high first.
    """
    high = [b >> 4 for b in stats]
    low = [b & 0x0F for b in stats]
    return {
        "jersey_number": high[0] * 10 + low[0],
        "weight_class": high[1],
        "agility": low[1],
        "speed": high[2],
        "off_awareness": low[2],
        "def_awareness": high[3],
        "shot_power": low[3],
        "checking": high[4],
        "handedness": low[4],
        "stick_handling": high[5],
        "shot_accuracy": low[5],
        "endurance": high[6],
        "roughness": low[6],
        "pass_accuracy": high[7],
        "aggression": low[7],
    }


def player_record(team_index: int, slot: int) -> bytes:
    """One complete record: BE length, then the name, then the stat bytes."""
    name = player_name(team_index, slot).encode("ascii")
    return struct.pack(">H", len(name) + 2) + name + player_stats(team_index, slot)


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

        offset = base + SEC_PLAYERS
        for slot in range(ROSTER_PLAYERS):
            rom[offset : offset + PLAYER_RECORD_SIZE] = player_record(i, slot)
            offset += PLAYER_RECORD_SIZE
        _u16(rom, offset, 0x0000)  # end-of-roster sentinel

    return rom


def write_nhl94_genesis_rom(path: pathlib.Path) -> pathlib.Path:
    """Write a synthetic ROM to `path` and return it."""
    path.write_bytes(bytes(build_nhl94_genesis_rom()))
    return path
