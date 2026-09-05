"""Fabricate a structurally valid NHL94 SNES ROM in memory.

Nothing here is derived from a real ROM. Every byte is computed from the format
`games/nhl94_snes/rom_reader.py` documents.

The team pointer table is at file offset 0xE25E7, and a team pointer carries only
16 bits: `_read_team_pointer` ORs them under a hardcoded bank $9C, and
`snes_to_file_offset` folds that to file offsets 0xE0000-0xE7FFF. So every team
block, and the pointer table itself, must live inside that one 32 KB window, and
the file must be at least 0xE8000 bytes for the window to exist at all. NHL '94
(SNES) is an 8 Mbit LoROM, 1 048 576 bytes, which is what this builds.

`NHL94SNESRomReader.validate` would accept a 649 728-byte file, the size its own
`ROM_SIZE_NO_HEADER` calls standard. Such a file has no bank $9C and no pointer
table; `tests/games/nhl94_snes/test_rom_reader.py` builds one to pin what happens
then.

Each player's name and each of its eight stat bytes encode both the team index
and the roster slot, so a reader that ignored `team_index`, returned a roster
reversed, or rotated the stat blocks by one cannot satisfy an equality.

Every multi-byte field is LITTLE-endian. The Genesis sibling in
`synthetic_rom.py` is big-endian and the two formats are otherwise close enough
to copy from one another by mistake.
"""

from __future__ import annotations

import pathlib
import struct

# 8 Mbit, the real cartridge size. `synthetic_rom.py`'s Genesis image is also
# 1 MB, which is a coincidence of two unrelated cartridge sizes and not a shared
# constant.
ROM_SIZE = 1048576

# `rom_reader.SMC_HEADER_SIZE`. A headered image is this much longer, which is
# what makes `len(data) % 0x8000 == 512` true and `_detect_header` say yes.
SMC_HEADER_SIZE = 512

POINTER_TABLE_OFFSET = 0xE25E7
POINTER_SIZE = 4
TEAM_COUNT = 28

# The 32 KB window bank $9C maps to. Everything the reader dereferences lives
# here, the pointer table included.
BANK_WINDOW_START = 0xE0000
BANK_WINDOW_SIZE = 0x8000

# Team blocks start after the pointer table, which ends at 0xE2657. 28 blocks of
# 0x300 bytes run from 0xE2700 to 0xE7B00, inside the window with room to spare.
TEAM_BLOCK_BASE = 0xE2700
TEAM_BLOCK_STRIDE = 0x300

# Team block header. `rom_writer` writes the player count at byte 17 and eight
# lines of seven slots at bytes 19-74, so the header has to be at least 75 bytes
# for the player records that follow it not to be overwritten. 0x50 leaves five
# spare bytes, which the writer must not touch.
TEAM_HEADER_SIZE = 0x50
PLAYER_COUNT_OFFSET = 17
TEAM_OVERALL_OFFSET = 18
LINE_ASSIGN_OFFSET = 19
LINE_COUNT = 8
LINE_SLOTS = 7

# Player records. 2 length bytes + name + 8 stat bytes, and the length field
# counts itself but not the stats -- the encoding `read_team_roster` decodes.
PLAYER_NAME_SIZE = 8
PLAYER_STATS_SIZE = 8
PLAYER_RECORD_SIZE = 2 + PLAYER_NAME_SIZE + PLAYER_STATS_SIZE  # 18
ROSTER_PLAYERS = 23

# What the reader calls the end of a roster: a length word below 3. The writer
# emits 0x0002, an empty length-prefixed string, and this builder does the same.
TERMINATOR = 0x0002

# Stat bytes 3-7, identical in every record. Every stat byte here stays inside
# the range `rom_writer._write_player_stats` can emit: byte 0 is a BCD jersey
# and `encode_nibble` clamps both nibbles of the rest to 0-6, so a byte like
# 0x77 is one the writer could never produce and a round-trip against it would
# be unsatisfiable.
STAT_PADDING = b"\x33\x44\x55\x66\x12"

# The forward and defenceman counts written into byte 17 of each team block, one
# pair per slot. Deliberately not 28 copies of one pair: `read_team_player_counts`
# is indexed by slot and a uniform table would let a reader that ignored the
# index pass. The values are all inside the reader's sanity test (forwards >= 3,
# defensemen >= 2) except the two named below, which are there to drive the
# fallback.
#
# Slot 26 has 2 forwards and slot 27 has 1 defenceman, both under the reader's
# floor, so both fall back to (2, 14, 7). They are the two All-Star slots, which
# `MODERN_NHL_TO_NHL94` maps no team to, so no test's roster depends on them.
TEAM_FD_COUNTS = [
    (14, 7),  # 0
    (13, 8),  # 1
    (15, 6),  # 2
    (12, 9),  # 3
    (14, 6),  # 4
    (13, 7),  # 5
    (15, 7),  # 6
    (12, 8),  # 7
    (14, 8),  # 8
    (13, 6),  # 9
    (15, 8),  # 10
    (12, 7),  # 11
    (14, 9),  # 12
    (13, 9),  # 13
    (15, 9),  # 14
    (12, 6),  # 15
    (11, 7),  # 16
    (10, 8),  # 17
    (9, 6),  # 18
    (8, 7),  # 19
    (7, 8),  # 20
    (6, 6),  # 21
    (5, 7),  # 22
    (4, 8),  # 23
    (3, 6),  # 24
    (14, 2),  # 25 - the low edge of the reader's defenceman test, which passes
    (2, 7),  # 26 - under the forward floor, so the reader falls back
    (14, 1),  # 27 - under the defenceman floor, so the reader falls back
]

# The city written into each team's strings section. Deliberately not a copy of
# `NHL94_TEAM_ORDER`: slot 20 is "St Louis" here against "St. Louis" there,
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
    "All-Star East",
    "All-Star West",
]

# A second length-prefixed string immediately after the city, as in the real
# strings section. It is there to be in the way: `_read_team_city` reads only
# the first string, so trailing zero padding would let a decoder that took
# `length` bytes rather than `length - 2` return the right city anyway once
# `.strip("\x00")` ran. The next string's length word is not zero, so it cannot.
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
    "ASE",
    "ASW",
]


def _u16(rom: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<H", rom, offset, value)


def team_base(team_index: int) -> int:
    """Absolute file offset of one team's data block, in a headerless image."""
    return TEAM_BLOCK_BASE + team_index * TEAM_BLOCK_STRIDE


def team_pointer_offset(team_index: int) -> int:
    """Absolute file offset of one team's entry in the pointer table."""
    return POINTER_TABLE_OFFSET + team_index * POINTER_SIZE


def _nibble_pair(value: int) -> int:
    """Pack 0-48 into one byte whose two nibbles are both inside the writer's 0-6.

    Base 7, because a nibble the writer can emit holds seven values. Both 28
    teams and 25 slots fit, so a single byte can carry either one without going
    outside what `encode_nibble` will produce.
    """
    return (value // 7) << 4 | (value % 7)


def player_name(team_index: int, slot: int) -> str:
    """The name in one roster slot. Always 8 bytes, so records stay 18 bytes."""
    return f"T{team_index:02d}_PL{slot:02d}"


def player_stats(team_index: int, slot: int) -> bytes:
    """The eight stat bytes for one roster slot, identifying both coordinates.

    Byte 0 is the jersey number in BCD and so is never zero, which matters: the
    reader decodes a name and then `.strip("\\x00")`s it, so a decoder that read
    `length` bytes instead of `length - 2` would spill into bytes 0 and 1 here.
    A zero byte 0 would be stripped straight back off and the over-read would
    leave the name looking correct.
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

    Transcribed from the comments beside each write in
    `rom_writer._write_player_stats`, deliberately not from its code: `src/`
    ships no inverse of `encode_nibble`/`encode_weight_nibble`, so a decoder
    derived from the writer's own expressions would agree with any rearrangement
    of them. Byte 0 is BCD, the rest are two nibbles each, high first.
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


def player_record(team_index: int, slot: int, name_size: int = PLAYER_NAME_SIZE) -> bytes:
    """One complete record: LE length, then the name, then the stat bytes."""
    name = player_name(team_index, slot).encode("ascii")[:name_size]
    return struct.pack("<H", len(name) + 2) + name + player_stats(team_index, slot)


def roster_region_size(players: int, name_size: int = PLAYER_NAME_SIZE) -> int:
    """Bytes from the first record to the end of the terminator, inclusive.

    The same quantity `rom_writer._get_team_player_region` measures, computed
    forwards from the layout rather than read back out of the image.
    """
    return players * (2 + name_size + PLAYER_STATS_SIZE) + 2


def build_nhl94_snes_rom(
    *,
    players_per_team: int = ROSTER_PLAYERS,
    name_size: int = PLAYER_NAME_SIZE,
    size: int = ROM_SIZE,
    with_smc_header: bool = False,
) -> bytearray:
    """Return an image the reader accepts and the writer can fill.

    `players_per_team` and `name_size` set how much room each team's existing
    roster occupies, and so how much room a patch has: the writer patches in
    place inside the region the records already there define. A ROM built with
    ten 8-character names holds 182 bytes per team, and a 23-player squad of
    14-character names needs 24 bytes each, so seven of them fit and sixteen are
    dropped. That gap is the point -- it is what makes "players requested" and
    "players written" different numbers.

    `with_smc_header` prepends 512 zero bytes, the copier header
    `_detect_header` recognises by `len(data) % 0x8000 == 512`. Every offset
    shifts by that much, which is what `header_offset` exists to absorb.
    """
    rom = bytearray(size)

    for i in range(TEAM_COUNT):
        base = team_base(i)
        # Only the low 16 bits are stored; the reader ORs bank $9C on itself.
        _u16(rom, team_pointer_offset(i), base - BANK_WINDOW_START)

        _u16(rom, base, TEAM_HEADER_SIZE)
        forwards, defensemen = TEAM_FD_COUNTS[i]
        rom[base + PLAYER_COUNT_OFFSET] = (forwards << 4) | defensemen
        # Byte 18 is the team's overall rating. Nothing reads it; it is filled
        # with a per-team value so a writer that scribbled over it is visible.
        rom[base + TEAM_OVERALL_OFFSET] = 0x40 | (i & 0x0F)
        # 56 bytes of line assignments, each byte carrying its own position and
        # the team index, so a header write that landed on the wrong team or at
        # the wrong offset changes bytes a test can name.
        #
        # The high bit is set on every one of them, which puts all 56 outside
        # the range `write_team_header` can emit: a line byte is a player index,
        # and there are never more than 2 + 15 + 15 players. Without that, a
        # filler byte that happened to equal the index the writer was about to
        # write would leave the byte unchanged, and a test that counts which
        # bytes a header write touched would undercount.
        for position in range(LINE_COUNT * LINE_SLOTS):
            rom[base + LINE_ASSIGN_OFFSET + position] = 0x80 | ((position + i) & 0x3F)

        offset = base + TEAM_HEADER_SIZE
        for slot in range(players_per_team):
            record = player_record(i, slot, name_size)
            rom[offset : offset + len(record)] = record
            offset += len(record)
        _u16(rom, offset, TERMINATOR)
        offset += 2

        for text in (CITIES[i], ABBREVIATIONS[i]):
            encoded = text.encode("ascii")
            _u16(rom, offset, len(encoded) + 2)
            rom[offset + 2 : offset + 2 + len(encoded)] = encoded
            offset += 2 + len(encoded)

    if with_smc_header:
        return bytearray(SMC_HEADER_SIZE) + rom
    return rom


def write_nhl94_snes_rom(
    path: pathlib.Path,
    *,
    players_per_team: int = ROSTER_PLAYERS,
    name_size: int = PLAYER_NAME_SIZE,
    size: int = ROM_SIZE,
    with_smc_header: bool = False,
) -> pathlib.Path:
    """Write a synthetic ROM to `path` and return it."""
    path.write_bytes(
        bytes(
            build_nhl94_snes_rom(
                players_per_team=players_per_team,
                name_size=name_size,
                size=size,
                with_smc_header=with_smc_header,
            )
        )
    )
    return path
