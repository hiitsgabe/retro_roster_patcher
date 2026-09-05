"""Fabricate a structurally valid NBA Live 95 (Genesis) ROM in memory.

Nothing here is derived from a real ROM. Every byte is computed from the format
`games/nbalive95_genesis/rom_reader.py` and `rom_writer.py` document, and the
layout constants below are chosen to be legal rather than to match a real dump.

Why the image is 2 MB
---------------------
The 30 team pointer tables sit at absolute file offsets transcribed from
Team-95's ROM editor, and the furthest of them -- team 29, the Slammers -- runs
from 0x1F80AC to 0x1F80DC. So the file has to be at least 2 064 604 bytes before
the last team's pointers can be read at all. NBA Live 95 is a 2 MB cartridge,
0x200000 bytes, which is what this builds.

`NBALive95RomReader.validate` would accept a 1 572 864-byte file, which is what
its own `ROM_SIZE_MIN` calls the minimum. Such a file has no pointer table for
teams 18-29; `tests/games/nbalive95_genesis/test_rom_reader.py` builds one to
pin what happens then.

Why the constants here are literals and not imports
---------------------------------------------------
`TEAM_ROSTER_ADDRESSES` and the record field offsets are duplicated below rather
than imported from `games.nbalive95_genesis.models`. That duplication is the
point: a test that located a table using the very constant it is meant to pin
would move with the constant and assert nothing. Moving `OFF_RATINGS` or an
entry of the address table in `src/` must break tests here, and it only can if
these are independent transcriptions of the documented layout.

Why every byte in a record is unique to its (team, slot)
--------------------------------------------------------
Each player's name, jersey, position, ratings and season stats encode both the
team index and the roster slot. Uniform filler would make the 360 records
identical, and then no assertion could tell which record a read or a write
landed on: a writer that ignored `team_index`, or filled a roster in reverse, or
rotated the ratings block by one, would satisfy every equality a test could
write.

Every multi-byte field is BIG-endian, this being a 68000 cartridge.
"""

from __future__ import annotations

import pathlib
import struct

#: The real cartridge size, and the smallest round size above `_LAST_POINTER_END`.
ROM_SIZE = 0x200000

#: Where `NBALive95RomReader.validate` reads the Genesis domestic game name.
TITLE_OFFSET = 0x120
TITLE_LENGTH = 0x30
TITLE = "NBA LIVE 95"

#: The Genesis header checksum word. `NBALive95RomWriter._fix_checksum`
#: recomputes it; this filler is deliberately not the correct value, so a test
#: can tell "recomputed" from "left alone".
CHECKSUM_OFFSET = 0x18E
CHECKSUM_FILLER = 0xDEAD

#: Non-zero bytes at exactly the offset the header checksum starts summing from.
#:
#: `_fix_checksum` sums big-endian words from 0x200 to the end of the file, and
#: everything from the Genesis header to the first player record would otherwise
#: be zero -- which makes the first word of the sum a zero, and an off-by-one in
#: the start offset invisible. This is the byte pattern that makes the boundary
#: itself observable, so it must begin at 0x200 and its first word must not be
#: zero.
CHECKSUM_REGION_START = 0x200
BOOT_FILLER = bytes([0x4E, 0xF9, 0x00, 0x00, 0x02, 0x10, 0x60, 0xFE] * 4)

#: Six bytes of 68000 that `apply_patches` replaces with three NOPs: a
#: `JSR $001F9270`. Any six bytes would do; these are the instruction the
#: upstream comment says is there, so a test asserting the site changed is
#: asserting against something a real image could plausibly hold.
CHECKSUM_BYPASS_OFFSET = 0x690
ORIGINAL_JSR = bytes([0x4E, 0xB9, 0x00, 0x1F, 0x92, 0x70])

TEAM_COUNT = 30
PLAYERS_PER_TEAM = 12
TEAM_POINTER_SIZE = 4

#: Independent transcription of `models.TEAM_ROSTER_ADDRESSES`. Deliberately not
#: evenly spaced: team 17 sits at 0x00044AF4 and team 18 at 0x001F4EF4.
TEAM_ROSTER_ADDRESSES = [
    0x0003FEB4,
    0x0004031A,
    0x00040788,
    0x00040C1A,
    0x00041084,
    0x000414FE,
    0x00041976,
    0x00041E12,
    0x00042282,
    0x00042712,
    0x00042B80,
    0x00043004,
    0x0004349A,
    0x0004390E,
    0x00043D76,
    0x000441D4,
    0x00044658,
    0x00044AF4,
    0x001F4EF4,
    0x001F5384,
    0x001F5810,
    0x001F5C84,
    0x001F612A,
    0x001F65A6,
    0x001F6A2C,
    0x001F6EA8,
    0x001F7328,
    0x001F77A4,
    0x001F7C2A,
    0x001F80AC,
]

#: One past the last byte any pointer table occupies. The smallest file every
#: pointer can be read out of.
LAST_POINTER_END = max(TEAM_ROSTER_ADDRESSES) + PLAYERS_PER_TEAM * TEAM_POINTER_SIZE

#: Where the 360 player records live. Chosen to clear the header, the bypass
#: site at 0x690 and both runs of pointer tables, with room for 30 blocks of
#: 4 KB: 0x100000 through 0x11E000.
PLAYER_BLOCK_BASE = 0x100000
PLAYER_BLOCK_STRIDE = 0x1000

#: Independent transcription of the record layout `models.py` documents. The
#: fixed part is 69 bytes and the name follows it; records are packed with no
#: padding, so a record is 69 + however long its name encoding is.
FIXED_SIZE = 0x45
OFF_JERSEY = 0x00
OFF_POSITION = 0x01
OFF_HEIGHT = 0x02
OFF_WEIGHT = 0x03
OFF_EXPERIENCE = 0x04
OFF_UNIVERSITY = 0x05
OFF_SKIN = 0x06
OFF_HAIR = 0x07
OFF_STATS = 0x08
OFF_UNKNOWN2 = 0x2A
OFF_RATINGS = 0x2B
OFF_UNKNOWN3 = 0x3B
OFF_NAME = 0x45

RATING_COUNT = 16
STAT_COUNT = 17
UNKNOWN3_SIZE = 10


def player_last_name(team: int, slot: int) -> str:
    """The last name in one roster slot, encoding both coordinates.

    Padded to a per-slot length so the 12 records of a team are not all the same
    size. The name budget a patch gets is the gap to the next record, so varying
    the sizes here is what makes `_compute_record_limits` observable at all.
    """
    return f"LAST{team:02d}{slot:02d}" + "Z" * (slot % 4)


def player_first_name(team: int, slot: int) -> str:
    """The first name in one roster slot, encoding both coordinates."""
    return f"FIRST{team:02d}{slot:02d}" + "W" * (team % 3)


def encoded_name(team: int, slot: int) -> bytes:
    """`LAST\\0FIRST\\0\\0`, the encoding `_decode_name` reads back.

    The trailing pair of nulls is what `_original_record_size` scans for, so it
    is also what fixes the last record of a team's name budget.
    """
    return (
        player_last_name(team, slot).encode("ascii")
        + b"\x00"
        + player_first_name(team, slot).encode("ascii")
        + b"\x00\x00"
    )


def record_size(team: int, slot: int) -> int:
    """Bytes one record occupies: the 69 fixed bytes plus its name encoding."""
    return FIXED_SIZE + len(encoded_name(team, slot))


def player_offset(team: int, slot: int) -> int:
    """Absolute file offset of one player record.

    Computed forwards from the layout, by summing the sizes of the records that
    precede it inside the team's block -- the same quantity the pointer table
    holds, derived rather than read back out of the image.
    """
    offset = PLAYER_BLOCK_BASE + team * PLAYER_BLOCK_STRIDE
    for earlier in range(slot):
        offset += record_size(team, earlier)
    return offset


def name_budget(team: int, slot: int) -> int:
    """How many bytes `write_player` may spend on this slot's name.

    Transcribed from what `_compute_record_limits` documents -- the gap to the
    next record, less the 69 fixed bytes, floored at 4 -- and computed from this
    module's own layout rather than by calling the writer.
    """
    return max(4, record_size(team, slot) - FIXED_SIZE)


def player_jersey(team: int, slot: int) -> int:
    return (team + slot) % 100


def player_position(team: int, slot: int) -> int:
    """0-4. Outside that range `_looks_like_nbalive95` rejects the image."""
    return (team + slot) % 5


def player_height_byte(team: int, slot: int) -> int:
    return 60 + (team * 3 + slot) % 40


def player_weight_byte(team: int, slot: int) -> int:
    return 50 + (team * 5 + slot) % 100


def player_experience(team: int, slot: int) -> int:
    return (team + slot * 2) % 20


def player_university(team: int, slot: int) -> int:
    """Byte 5, which no writer in this package touches. Non-zero everywhere, so
    a write that strayed one byte either side of the skin or experience byte is
    visible."""
    return 1 + (team * 7 + slot) % 200


def player_skin(team: int, slot: int) -> int:
    """1-3, never 0. `map_player` never sets skin, so every patched record
    arrives at the writer carrying 0; against a filler of 0 there would be no
    telling "the writer wrote the record's zero" -- which is what it does, as
    upstream did -- from "the writer left this byte alone"."""
    return 1 + slot % 3


def player_hair(team: int, slot: int) -> int:
    """1-0x26, never 0, for the same reason as `player_skin`. Varies with the
    team as well as the slot, so a whole patched roster's twelve styles are
    twelve distinct values."""
    return 1 + (team + slot) % 0x26


def player_unknown2(team: int, slot: int) -> int:
    """Byte 0x2A, which `write_player` zeroes. Non-zero here so that is visible."""
    return 1 + (team * 3 + slot) % 255


def player_ratings(team: int, slot: int) -> list[int]:
    """16 bytes, each 0-99 and each distinct within the record.

    A rotation of this block by one position changes every byte, which is what
    lets a test tell "wrote the ratings" from "wrote the ratings in the wrong
    order".
    """
    return [(team * 7 + slot * 13 + index * 3) % 100 for index in range(RATING_COUNT)]


def player_season_stats(team: int, slot: int) -> list[int]:
    """17 big-endian 16-bit values, all non-zero.

    Non-zero matters: `map_player` supplies no season stats, so `write_player`
    writes 17 zeros over these. Against zero filler that write would be
    invisible.
    """
    return [1 + (team * 1000 + slot * 50 + index) % 0xFFFE for index in range(STAT_COUNT)]


def player_unknown3(team: int, slot: int) -> bytes:
    """Bytes 0x3B-0x44, which `write_player` says it preserves."""
    return bytes((1 + (team * 11 + slot * 5 + index) % 255) for index in range(UNKNOWN3_SIZE))


def build_player_record(team: int, slot: int) -> bytes:
    """One complete record: 69 fixed bytes then the name encoding."""
    record = bytearray(FIXED_SIZE)
    record[OFF_JERSEY] = player_jersey(team, slot)
    record[OFF_POSITION] = player_position(team, slot)
    record[OFF_HEIGHT] = player_height_byte(team, slot)
    record[OFF_WEIGHT] = player_weight_byte(team, slot)
    record[OFF_EXPERIENCE] = player_experience(team, slot)
    record[OFF_UNIVERSITY] = player_university(team, slot)
    record[OFF_SKIN] = player_skin(team, slot)
    record[OFF_HAIR] = player_hair(team, slot)
    for index, value in enumerate(player_season_stats(team, slot)):
        struct.pack_into(">H", record, OFF_STATS + index * 2, value)
    record[OFF_UNKNOWN2] = player_unknown2(team, slot)
    for index, value in enumerate(player_ratings(team, slot)):
        record[OFF_RATINGS + index] = value
    unknown3 = player_unknown3(team, slot)
    record[OFF_UNKNOWN3 : OFF_UNKNOWN3 + UNKNOWN3_SIZE] = unknown3
    return bytes(record) + encoded_name(team, slot)


def decode_player_record(rom: bytes | bytearray, offset: int) -> dict:
    """Split one record back into the fields it carries.

    Transcribed from the layout `models.py` documents, deliberately not from
    `NBALive95RomReader.read_player`: a decoder that called the reader would
    agree with any rearrangement of the reader's own offsets. The name is split
    at the first null and the first name ends at the next one, which is the
    format `rom_writer._encode_name_variable` emits.
    """
    fixed = rom[offset : offset + FIXED_SIZE]
    name_field = bytes(rom[offset + OFF_NAME : offset + OFF_NAME + 64])
    last, _, rest = name_field.partition(b"\x00")
    first, _, _ = rest.partition(b"\x00")
    return {
        "jersey": fixed[OFF_JERSEY],
        "position": fixed[OFF_POSITION],
        "height_byte": fixed[OFF_HEIGHT],
        "weight_byte": fixed[OFF_WEIGHT],
        "experience": fixed[OFF_EXPERIENCE],
        "university": fixed[OFF_UNIVERSITY],
        "skin": fixed[OFF_SKIN],
        "hair": fixed[OFF_HAIR],
        "season_stats": [
            struct.unpack_from(">H", fixed, OFF_STATS + index * 2)[0] for index in range(STAT_COUNT)
        ],
        "unknown2": fixed[OFF_UNKNOWN2],
        "ratings": list(fixed[OFF_RATINGS : OFF_RATINGS + RATING_COUNT]),
        "unknown3": bytes(fixed[OFF_UNKNOWN3 : OFF_UNKNOWN3 + UNKNOWN3_SIZE]),
        "last_name": last.decode("ascii", errors="replace"),
        "first_name": first.decode("ascii", errors="replace"),
    }


def build_nbalive95_rom(
    *,
    size: int = ROM_SIZE,
    title: str = TITLE,
    teams: int = TEAM_COUNT,
    players_per_team: int = PLAYERS_PER_TEAM,
) -> bytearray:
    """Return an image the reader accepts and the writer can fill.

    `teams` and `players_per_team` cut how many pointer entries are populated;
    the rest are left as the zero a `_get_player_offset` reads as "no player".
    `size` shorter than `LAST_POINTER_END` produces the file the ported reader's
    own `ROM_SIZE_MIN` accepts and the patcher refuses.
    """
    rom = bytearray(size)

    encoded_title = title.encode("ascii")[:TITLE_LENGTH]
    rom[TITLE_OFFSET : TITLE_OFFSET + len(encoded_title)] = encoded_title
    if size >= CHECKSUM_OFFSET + 2:
        struct.pack_into(">H", rom, CHECKSUM_OFFSET, CHECKSUM_FILLER)
    if size >= CHECKSUM_REGION_START + len(BOOT_FILLER):
        rom[CHECKSUM_REGION_START : CHECKSUM_REGION_START + len(BOOT_FILLER)] = BOOT_FILLER
    if size >= CHECKSUM_BYPASS_OFFSET + len(ORIGINAL_JSR):
        rom[CHECKSUM_BYPASS_OFFSET : CHECKSUM_BYPASS_OFFSET + len(ORIGINAL_JSR)] = ORIGINAL_JSR

    for team in range(teams):
        table = TEAM_ROSTER_ADDRESSES[team]
        for slot in range(players_per_team):
            offset = player_offset(team, slot)
            record = build_player_record(team, slot)
            if offset + len(record) > size or table + (slot + 1) * TEAM_POINTER_SIZE > size:
                continue
            struct.pack_into(">I", rom, table + slot * TEAM_POINTER_SIZE, offset)
            rom[offset : offset + len(record)] = record

    return rom


def write_nbalive95_rom(
    path: pathlib.Path,
    *,
    size: int = ROM_SIZE,
    title: str = TITLE,
    teams: int = TEAM_COUNT,
    players_per_team: int = PLAYERS_PER_TEAM,
) -> pathlib.Path:
    """Write a synthetic ROM to `path` and return it."""
    path.write_bytes(
        bytes(
            build_nbalive95_rom(
                size=size,
                title=title,
                teams=teams,
                players_per_team=players_per_team,
            )
        )
    )
    return path
