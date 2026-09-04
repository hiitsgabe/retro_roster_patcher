"""Fabricate a complete NHL 2005 (PS2) disc image in memory.

    ISO 9660 -> /DB/DB.VIV -> BIGF -> two RefPacked TDBs
             -> PLAY / ROST / SPBT / SPAI / SGAI / STEA

Nothing here comes from a real disc; no ISO may enter this repository. Every
byte is generated, and the field layouts are this file's invention -- the real
ones have never been seen by anything in this project, upstream included.

**A real PS2 image is gigabytes and this one is under 300 KB.** That is not a
compromise: the patcher touches the PVD, two directory sectors and the `DB.VIV`
extent, and nothing else on the disc. `write_iso(pad_to=...)` inflates the file
with `truncate`, so a multi-megabyte image for the copy path costs a sparse hole
rather than real bytes.

**Four things make this disc NHL 2005 rather than NHL 07**, and each is a place
a copied fixture would leave a real difference untested:

- `DB.VIV` is at `/DB/DB.VIV`, one directory from the root and not three.
- The archive has **two** members. There is no `nhlbioatt.tdb`, so nothing here
  mirrors SPBT, SPAI or SGAI.
- ROST carries **64** line flags, in this game's own order, and `33LD`/`33RD`
  are not among them.
- `FNME` and `LNME` are **16 bytes**, not 20, so a 16-character name is
  truncated to 15 here and would survive on NHL 07.

Two things are deliberately **independent reimplementations** of code under
test, and that is the point of the file:

- `unpack_bits` reads LSB-first bit fields the long way, from a `FieldSpec`
  list this file owns. `TDBTable.write_record` is what puts them there, so a
  test can assert a named field holds the number it should -- rather than that a
  round trip through one bit-width mistake preserved it.
- `synthetic_tdb.mpeg2_crc`, which `build_tdb` uses for the chain, is the
  bitwise form of the nibble-table CRC in `formats/ea_tdb.py`.

The ISO 9660 layer is `synthetic_iso`, which lays records out from ECMA-119 and
is what `tests/formats/test_iso9660.py` checks `formats/iso9660.py` against.
Reading a patched image back goes through `iso_read_file` below, a third walk
that is neither of those two.

`refpack_compress` *is* the module's own, and that is the one place a fixture
leans on the code under test. It is justified: `tests/formats/test_refpack.py`
pins its output byte-for-byte against the source compressor over fifteen inputs
covering all seven command families, on top of a 52-case round-trip corpus, so
reimplementing it here would add a second compressor to get wrong.

Record contents are self-identifying. Every player's name, jersey, weight and
every attribute encode the team, the roster row and the field, so a write that
landed on the wrong record, the wrong table or the wrong field cannot satisfy an
assertion.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from . import synthetic_iso as iso
from .synthetic_tdb import (
    TYPE_STRING,
    TYPE_UINT,
    BigfSpec,
    FieldSpec,
    TableSpec,
    build_bigf,
    build_tdb,
    pack_bits,
)

ISO_SECTOR_SIZE = iso.SECTOR_SIZE
ISO_PVD_SECTOR = iso.PVD_SECTOR

# Sector assignments. Every one is fixed so a test can name a sector without
# recomputing the layout. Two directories, not four: this is a PS2 disc.
ROOT_DIR_SECTOR = 17
DB_DIR_SECTOR = 18
DB_VIV_SECTOR = 19

# How many spare sectors sit between the end of `DB.VIV` and the next file. This
# is what `find_db_viv_location` reports as the rebuild budget: the archive may
# grow into these and no further.
GAP_SECTORS = 2

# The file that follows `DB.VIV` on the disc. It exists to give
# `iso9660.find_entry_with_next_lba` a next LBA -- without it the budget
# collapses to `DB.VIV`'s own sector-aligned length -- and its contents are a
# recognisable pattern so a byte-level assertion can show the patcher did not
# walk past the archive.
PAD_FILE_NAME = "ZZPAD.BIN"
PAD_FILE_BYTES = bytes(range(256)) * 8  # 2048 bytes, every value eight times

# A file in the *root* directory, which the `DB` directory does not hold. It is
# here so that the root has something besides `DB` in it: a walk that returned
# the first record it found rather than the one it was asked for would otherwise
# be right by accident.
ROOT_FILE_NAME = "SYSTEM.CNF"
ROOT_FILE_BYTES = b"BOOT2 = cdrom0:\\SLUS_209.36;1\r\nVER = 1.00\r\n"

# Slack appended to each `DB.VIV` member, inside its declared entry size.
# `bigf_replace_inplace` refuses a replacement larger than the entry it is
# overwriting, and a recompressed table is not guaranteed to be smaller than the
# original -- new names compress differently. Real EA archives carry slack for
# the same reason; `refpack_decompress` stops at its own end marker and never
# reads it.
MEMBER_SLACK = 8192

# ──────────────────────────────────────────────────────────────
# TDB field layouts
# ──────────────────────────────────────────────────────────────
#
# Bit offsets are chosen so that most integer fields are NOT byte-aligned and
# several straddle a byte boundary. A layout of byte-aligned bytes would let a
# writer that ignored `bit_width` pass every test here.

# The width of `FNME` and `LNME` in this game, in bytes. **16, against NHL 07's
# 20**, which is what makes `models.NAME_FIELD_CHARS` 15 rather than 19.
NAME_FIELD_BYTES = 16

# 38 bytes: two 16-byte names, then seven integers packed into the tail.
#   bits   0..127  FNME  16 ASCII bytes
#   bits 128..255  LNME  16 ASCII bytes
#   bits 256..271  INDX  16 bits, byte-aligned
#   bits 272..278  JERS   7 bits
#   bit  279       HAND   1 bit, the top bit of byte 34
#   bits 280..285  TEAM   6 bits
#   bits 286..288  POS_   3 bits, straddling bytes 35 and 36
#   bits 289..296  WEIG   8 bits, straddling bytes 36 and 37
#   bits 297..301  HEIG   5 bits
SPBT_FIELDS = [
    FieldSpec("FNME", TYPE_STRING, 0, NAME_FIELD_BYTES * 8),
    FieldSpec("LNME", TYPE_STRING, NAME_FIELD_BYTES * 8, NAME_FIELD_BYTES * 8),
    FieldSpec("INDX", TYPE_UINT, 256, 16),
    FieldSpec("JERS", TYPE_UINT, 272, 7),
    FieldSpec("HAND", TYPE_UINT, 279, 1),
    FieldSpec("TEAM", TYPE_UINT, 280, 6),
    FieldSpec("POS_", TYPE_UINT, 286, 3),
    FieldSpec("WEIG", TYPE_UINT, 289, 8),
    FieldSpec("HEIG", TYPE_UINT, 297, 5),
]
SPBT_RECORD_SIZE = 38

# The 21 six-bit skater ratings, in the order the writer lists them. `FIGH` is
# two bits and is appended after them, so no rating is at a multiple of eight
# except the first.
SKATER_ATTR_NAMES = [
    "BALA",
    "PENA",
    "SACC",
    "WACC",
    "FACE",
    "ACCE",
    "SPEE",
    "POTE",
    "DEKG",
    "CHKG",
    "TOUG",
    "PUCK",
    "AGIL",
    "HERO",
    "AGGR",
    "PRES",
    "PASS",
    "ENDU",
    "INJU",
    "SPOW",
    "WPOW",
]
SPAI_FIELDS = [FieldSpec("INDX", TYPE_UINT, 0, 16)] + [
    FieldSpec(name, TYPE_UINT, 16 + i * 6, 6) for i, name in enumerate(SKATER_ATTR_NAMES)
]
SPAI_FIELDS.append(FieldSpec("FIGH", TYPE_UINT, 16 + len(SKATER_ATTR_NAMES) * 6, 2))
SPAI_RECORD_SIZE = 18  # 16 + 21*6 + 2 = 144 bits

GOALIE_ATTR_NAMES = [
    "BRKA",
    "REBC",
    "SREC",
    "SPEE",
    "POKE",
    "INTE",
    "POTE",
    "TOUG",
    "AGIL",
    "5HOL",
    "PASS",
    "ENDU",
    "GSH_",
    "SSH_",
    "GSL_",
    "SSL_",
]
SGAI_FIELDS = [FieldSpec("INDX", TYPE_UINT, 0, 16)] + [
    FieldSpec(name, TYPE_UINT, 16 + i * 6, 6) for i, name in enumerate(GOALIE_ATTR_NAMES)
]
SGAI_FIELDS.append(FieldSpec("FIGH", TYPE_UINT, 16 + len(GOALIE_ATTR_NAMES) * 6, 2))
SGAI_RECORD_SIZE = 15  # 16 + 16*6 + 2 = 114 bits

# The sixty-four single-bit line flags, in the order `rom_writer.LINE_FLAGS`
# gives them. Restated here rather than imported: importing the list under test
# would let a name dropped from it disappear from the fixture too, and every
# test would still pass.
#
# `33LD` and `33RD` are absent, exactly as they are absent from the writer's
# list, and `L3LD`/`L3RD` are present. That is what lets a test show the third
# defence pair is dropped rather than merely unasserted.
LINE_FLAG_NAMES = [
    "31LD",
    "41LD",
    "K1LD",
    "L1LD",
    "P1LD",
    "32LD",
    "42LD",
    "K2LD",
    "L2LD",
    "P2LD",
    "L3LD",
    "31RD",
    "41RD",
    "K1RD",
    "L1RD",
    "P1RD",
    "32RD",
    "42RD",
    "K2RD",
    "L2RD",
    "P2RD",
    "L3RD",
    "41LW",
    "K1LW",
    "L1LW",
    "P1LW",
    "42LW",
    "K2LW",
    "L2LW",
    "P2LW",
    "L3LW",
    "L4LW",
    "L1RW",
    "P1RW",
    "L2RW",
    "P2RW",
    "L3RW",
    "L4RW",
    "31C_",
    "41C_",
    "K1C_",
    "L1C_",
    "P1C_",
    "32C_",
    "42C_",
    "K2C_",
    "L2C_",
    "P2C_",
    "L3C_",
    "L4C_",
    "G1__",
    "H1__",
    "S1__",
    "X1__",
    "G2__",
    "H2__",
    "S2__",
    "X2__",
    "H3__",
    "S3__",
    "H4__",
    "S4__",
    "H5__",
    "S5__",
]

# Two flags the game has and the mapper never emits, and two the mapper emits
# and the game does not have. Named here so a test can state the asymmetry
# rather than restate the lists.
UNREACHABLE_FLAGS = ["X1__", "X2__", "L3LD", "L3RD"]
DROPPED_MAPPER_FLAGS = ["33LD", "33RD"]

#   bits  0.. 5  TEAM   6 bits
#   bits  6..12  JERS   7 bits
#   bits 13..28  INDX  16 bits, straddling three bytes
#   bits 29..30  CAPT   2 bits
#   bits 31..32  DRES   2 bits, straddling bytes 3 and 4
#   bits 33..96  the sixty-four line flags, one bit each
ROST_FIELDS = [
    FieldSpec("TEAM", TYPE_UINT, 0, 6),
    FieldSpec("JERS", TYPE_UINT, 6, 7),
    FieldSpec("INDX", TYPE_UINT, 13, 16),
    FieldSpec("CAPT", TYPE_UINT, 29, 2),
    FieldSpec("DRES", TYPE_UINT, 31, 2),
] + [FieldSpec(name, TYPE_UINT, 33 + i, 1) for i, name in enumerate(LINE_FLAG_NAMES)]
ROST_RECORD_SIZE = 13  # 97 bits

#   bits  0..15  INDX
#   bits 16..19  TBLE
#   bits 20..35  ID__   straddling bytes 2, 3 and 4
PLAY_FIELDS = [
    FieldSpec("INDX", TYPE_UINT, 0, 16),
    FieldSpec("TBLE", TYPE_UINT, 16, 4),
    FieldSpec("ID__", TYPE_UINT, 20, 16),
]
PLAY_RECORD_SIZE = 5  # 36 bits

# STEA, and it is not NHL 07's. This game reads `FNME` and `SNME` for the name
# and `ABBR` for the abbreviation; NHL 07 reads `NAME` and `CITY` and takes the
# abbreviation from a constant. A fixture carrying NHL 07's field names would
# make every slot fall back to the hard-coded list and nothing would notice.
#
#   bits   0..  7  INDX   8 bits
#   bits   8..135  FNME  16 ASCII bytes
#   bits 136..231  SNME  12 ASCII bytes
#   bits 232..263  ABBR   4 ASCII bytes
STEA_FIELDS = [
    FieldSpec("INDX", TYPE_UINT, 0, 8),
    FieldSpec("FNME", TYPE_STRING, 8, 128),
    FieldSpec("SNME", TYPE_STRING, 136, 96),
    FieldSpec("ABBR", TYPE_STRING, 232, 32),
]
STEA_RECORD_SIZE = 33

# ──────────────────────────────────────────────────────────────
# The roster the fixture disc ships with
# ──────────────────────────────────────────────────────────────

# Four teams is enough for the two collisions that matter -- a slot patched and
# a slot left alone -- while keeping the whole image small. Twenty-five rows a
# team is what `stat_mapper.MAX_PLAYERS` selects, so the fixture exercises the
# exactly-full case rather than only the short one.
TEAM_COUNT = 4
ROWS_PER_TEAM = 25
GOALIE_ROWS_PER_TEAM = 2
PLAYER_COUNT = TEAM_COUNT * ROWS_PER_TEAM

# The three identifier spaces are disjoint and none of them is a record
# position. A ROST row at position `p` carries `INDX = ROST_INDX_BASE + p`; the
# PLAY record with that `INDX` sits at position `PLAYER_COUNT - 1 - p`, so
# reading PLAY by position instead of by `INDX` reverses the league; and that
# record's `ID__` is `PLAYER_ID_BASE + p`, whose SPBT record sits at a position
# given by a stride-7 permutation. A patcher that confused any of the four
# writes a real player over a different real player, which is exactly the bug an
# offset-based test cannot see.
ROST_INDX_BASE = 1000
PLAYER_ID_BASE = 5000
SPBT_STRIDE = 7  # coprime with PLAYER_COUNT = 100, so the map is a bijection
# ...and shifted, so that no position is its own player. Without the shift,
# position 0 would hold player 0, and position 0 is the record a test is most
# likely to check.
SPBT_SHIFT = 13

# The height every SPBT record ships with, unless a test asks for another. 16 is
# the value the source's dead `HEIG` write always produced, so a disc built with
# this value is one where the deliberate divergence in `write_player_bio` --
# not writing `HEIG` at all -- is invisible in the output bytes. That is what
# makes a byte-identical differential against the source possible; build with a
# different value to see the divergence and nothing else.
DEFAULT_DISC_HEIGHT = 16


def is_goalie_row(row: int) -> bool:
    """Is roster row `row` one of a team's goalie rows?

    The first two rows of every team. `patcher._classify_slots` does not read
    this -- it decides from whether the player id has an SGAI record -- and this
    is what puts him there.
    """
    return row < GOALIE_ROWS_PER_TEAM


def player_id_for(team: int, row: int) -> int:
    """The `SPBT.INDX` / `PLAY.ID__` of the player the disc ships in this row."""
    return PLAYER_ID_BASE + team * ROWS_PER_TEAM + row


def rost_indx_for(team: int, row: int) -> int:
    """The `ROST.INDX` / `PLAY.INDX` of this roster row."""
    return ROST_INDX_BASE + team * ROWS_PER_TEAM + row


def rost_position(team: int, row: int) -> int:
    """Which ROST record position holds this row."""
    return team * ROWS_PER_TEAM + row


def spbt_position(team: int, row: int) -> int:
    """Which SPBT record position holds this player's bio.

    The inverse of the stride-7 permutation, so nothing here is the identity.
    """
    flat = team * ROWS_PER_TEAM + row
    for position in range(PLAYER_COUNT):
        if (position * SPBT_STRIDE + SPBT_SHIFT) % PLAYER_COUNT == flat:
            return position
    raise AssertionError(f"no SPBT position maps to player {flat}")


def _skater_player_ids() -> list[int]:
    return [
        player_id_for(t, r)
        for t in range(TEAM_COUNT)
        for r in range(ROWS_PER_TEAM)
        if not is_goalie_row(r)
    ]


def _goalie_player_ids() -> list[int]:
    return [
        player_id_for(t, r)
        for t in range(TEAM_COUNT)
        for r in range(ROWS_PER_TEAM)
        if is_goalie_row(r)
    ]


def spai_position(player_id: int) -> int:
    """Which SPAI record position holds this skater's attributes, or -1.

    The order is the skater ids reversed, which is a different permutation from
    SPBT's, so a patcher that reused one index for the other misses.
    """
    ids = list(reversed(_skater_player_ids()))
    return ids.index(player_id) if player_id in ids else -1


def sgai_position(player_id: int) -> int:
    """Which SGAI record position holds this goalie's attributes, or -1."""
    ids = list(reversed(_goalie_player_ids()))
    return ids.index(player_id) if player_id in ids else -1


def disc_bio_values(team: int, row: int, height: int = DEFAULT_DISC_HEIGHT) -> dict[str, object]:
    """What the fixture disc's SPBT record for this row holds before patching.

    Every value encodes both coordinates and none of them collides with what
    `stat_mapper` produces: the names carry the word `Disc`, the jerseys run
    from 90 upwards where a mapped roster's run from 1, and the weights are
    below the 190 lb default. So an assertion that a record changed cannot be
    satisfied by a record that was merely left alone.

    The names are kept under 15 characters so that the disc's own bios survive a
    round trip through a 16-byte field unchanged. A test about truncation builds
    its own long name and asserts on what came back.
    """
    return {
        "FNME": f"DiscF{team}",
        "LNME": f"DiscL{team}x{row}",
        "INDX": player_id_for(team, row),
        "JERS": 90 + (row % 30),
        "HAND": (team + row) % 2,
        "TEAM": team,
        "POS_": 4 if is_goalie_row(row) else (row % 4),
        "WEIG": 120 + row,
        "HEIG": height,
    }


def _spbt_table(height: int) -> TableSpec:
    records = bytearray()
    for position in range(PLAYER_COUNT):
        flat = (position * SPBT_STRIDE + SPBT_SHIFT) % PLAYER_COUNT
        team, row = divmod(flat, ROWS_PER_TEAM)
        records += pack_bits(SPBT_FIELDS, disc_bio_values(team, row, height), SPBT_RECORD_SIZE)
    return TableSpec(
        name="SPBT",
        fields=list(SPBT_FIELDS),
        record_size=SPBT_RECORD_SIZE,
        capacity=PLAYER_COUNT,
        num_records=PLAYER_COUNT,
        records=bytes(records),
    )


def _attr_table(
    name: str,
    fields: list[FieldSpec],
    record_size: int,
    attr_names: list[str],
    player_ids: list[int],
) -> TableSpec:
    """An SPAI- or SGAI-shaped table whose every rating is a distinct number.

    Rating `k` of player `p` starts at `(p * 11 + k * 5) % 64`, which is inside
    the six-bit range and differs between neighbouring fields and neighbouring
    records. A writer that wrote one field's value into the next field's bits
    lands on a number that is in the table but in the wrong place, and a reader
    that used the wrong width reads a number that is in neither.
    """
    records = bytearray()
    for player_id in player_ids:
        values: dict[str, object] = {"INDX": player_id, "FIGH": player_id % 4}
        for k, attr in enumerate(attr_names):
            values[attr] = (player_id * 11 + k * 5) % 64
        records += pack_bits(fields, values, record_size)
    return TableSpec(
        name=name,
        fields=list(fields),
        record_size=record_size,
        capacity=len(player_ids),
        num_records=len(player_ids),
        records=bytes(records),
    )


def _rost_table() -> TableSpec:
    records = bytearray()
    for position in range(PLAYER_COUNT):
        team, row = divmod(position, ROWS_PER_TEAM)
        values: dict[str, object] = {
            "TEAM": team,
            "JERS": 90 + (row % 30),
            "INDX": rost_indx_for(team, row),
            "CAPT": row % 3,
            "DRES": 1,
        }
        # Every row ships with one line flag set, chosen so that no two rows of
        # a team share one -- 25 rows against 64 flags, so this is a stride
        # rather than a modulo. A patcher that failed to clear the flags it does
        # not set would leave these behind, and the test that checks a patched
        # row has exactly its new flags is what catches it.
        values[LINE_FLAG_NAMES[(row * 2) % len(LINE_FLAG_NAMES)]] = 1
        records += pack_bits(ROST_FIELDS, values, ROST_RECORD_SIZE)
    return TableSpec(
        name="ROST",
        fields=list(ROST_FIELDS),
        record_size=ROST_RECORD_SIZE,
        capacity=PLAYER_COUNT,
        num_records=PLAYER_COUNT,
        records=bytes(records),
    )


def _play_table() -> TableSpec:
    """PLAY, laid out in reverse of ROST so position is never the answer."""
    records = bytearray()
    for position in range(PLAYER_COUNT):
        flat = PLAYER_COUNT - 1 - position
        team, row = divmod(flat, ROWS_PER_TEAM)
        records += pack_bits(
            PLAY_FIELDS,
            {
                "INDX": rost_indx_for(team, row),
                "TBLE": 1 if is_goalie_row(row) else 2,
                "ID__": player_id_for(team, row),
            },
            PLAY_RECORD_SIZE,
        )
    return TableSpec(
        name="PLAY",
        fields=list(PLAY_FIELDS),
        record_size=PLAY_RECORD_SIZE,
        capacity=PLAYER_COUNT,
        num_records=PLAYER_COUNT,
        records=bytes(records),
    )


# How many team records the fixture's STEA table declares. Forty, against the 30
# club slots the patcher writes: NHL 2005's STEA is reported to hold 94 records,
# the extra ones being national and historic sides, and the reader's job is to
# drop every record whose `INDX` is past 29. A fixture of exactly 30 could not
# show that it does.
STEA_CAPACITY = 40


def stea_indx_for(position: int) -> int:
    """The `INDX` the fixture's STEA record at `position` carries.

    **Descending, so no record's `INDX` is its position.** Phase 4a shipped a
    STEA whose `INDX` equalled the position and could therefore not tell a
    reader that read the field from one that used its loop counter. It also
    means the surviving records arrive in reverse slot order, so the reader's
    sort is doing work: without it the slot list would come out 29 down to 0.
    """
    return STEA_CAPACITY - 1 - position


# STEA records whose `INDX` is inside the club range, in the order the reader
# should return them after sorting.
STEA_PATCHABLE_INDICES = list(range(30))

# Slots whose `FNME` the fixture leaves empty, to exercise the reader's two
# fallbacks. Chosen inside the club range so they are not dropped first.
STEA_NO_FNME = 1  # falls back to SNME
STEA_NO_NAME_AT_ALL = 2  # falls back to NHL05_TEAM_NAMES[2]
STEA_NO_ABBR = 3  # falls back to NHL05_TEAM_INDEX[3]


def stea_full_name(index: int) -> str:
    """The `FNME` the fixture disc carries for slot `index`, or "".

    Deliberately not any real club name: `RomSlot.current_name` must come from
    the disc, and a fixture that used the same strings as `NHL05_TEAM_NAMES`
    could not tell a reader that read STEA from one that returned the constant.
    """
    if index in (STEA_NO_FNME, STEA_NO_NAME_AT_ALL):
        return ""
    return f"Disc Club {index:02d}"


def stea_short_name(index: int) -> str:
    """The `SNME` the fixture carries, which only slot `STEA_NO_FNME` shows."""
    if index == STEA_NO_NAME_AT_ALL:
        return ""
    return f"Sh{index:02d}"


def stea_abbr(index: int) -> str:
    """The `ABBR` the fixture carries, four characters and not a real code."""
    if index == STEA_NO_ABBR:
        return ""
    return f"D{index:02d}"


def _stea_table() -> TableSpec:
    records = bytearray()
    for position in range(STEA_CAPACITY):
        index = stea_indx_for(position)
        records += pack_bits(
            STEA_FIELDS,
            {
                "INDX": index,
                "FNME": stea_full_name(index),
                "SNME": stea_short_name(index),
                "ABBR": stea_abbr(index),
            },
            STEA_RECORD_SIZE,
        )
    return TableSpec(
        name="STEA",
        fields=list(STEA_FIELDS),
        record_size=STEA_RECORD_SIZE,
        capacity=STEA_CAPACITY,
        num_records=STEA_CAPACITY,
        records=bytes(records),
    )


# ──────────────────────────────────────────────────────────────
# TDB files and the BIGF around them
# ──────────────────────────────────────────────────────────────


def build_master_tdb(height: int = DEFAULT_DISC_HEIGHT) -> bytes:
    """`nhl2005.tdb`: every table, in an order the patcher does not assume.

    STEA is last so that the CRC chain link the patcher's edits invalidate is
    not the final one, and PLAY is first so the table `serialize` never writes a
    header CRC for is one the patcher reads rather than writes.
    """
    return build_tdb(
        [
            _play_table(),
            _rost_table(),
            _spbt_table(height),
            _attr_table(
                "SPAI",
                SPAI_FIELDS,
                SPAI_RECORD_SIZE,
                SKATER_ATTR_NAMES,
                list(reversed(_skater_player_ids())),
            ),
            _attr_table(
                "SGAI",
                SGAI_FIELDS,
                SGAI_RECORD_SIZE,
                GOALIE_ATTR_NAMES,
                list(reversed(_goalie_player_ids())),
            ),
            _stea_table(),
        ]
    )


def build_roster_tdb() -> bytes:
    """`nhlrost.tdb`: the ROST mirror, and this game's only mirror."""
    return build_tdb([_rost_table()])


@dataclass
class DiscSpec:
    """Everything a test may vary about the fabricated disc.

    Defaults build a well-formed NHL 2005 image. Each override below produces
    one specific malformation, and the name says which:

    `height`
        the `HEIG` every SPBT record ships with.
    `master_name`, `roster_name`
        the archive's own spelling of each member. `None` leaves it out.
    `archive_magic`
        the four bytes `DB.VIV` starts with, for the "not a BIGF" case.
    `declared_db_viv_size`
        what the ISO 9660 directory record claims `DB.VIV`'s length is,
        independently of how many bytes are actually written. Larger than the
        truth is the truncated-extent case the arithmetic bound refuses.
    `pvd_type`
        byte 0 of the Primary Volume Descriptor. Anything but 1 is not a PVD.
    `db_dir_name`
        the one directory's name. Anything but `DB` breaks the path walk.
    `db_is_file`
        author `DB` as a file rather than a directory, so `iso9660.walk` must
        refuse to descend into it.
    `pad_to`
        inflate the image to at least this many bytes with `truncate`, which
        costs a sparse hole and not real storage.
    `master_payload`
        raw bytes for the `nhl2005.tdb` member, used verbatim with no
        compression and no slack. For the cases where the archive holds a
        member of that name which is not a TDB.
    `db_dir_exact_size`
        declare the `DB` directory's length as exactly its records rather than a
        whole sector, so its last record ends flush with the end of the extent.
    `db_viv_last`
        list `DB.VIV` after the padding file in the `DB` directory. With
        `db_dir_exact_size` it becomes the flush-final record.
    `no_pad_file`
        leave `ZZPAD.BIN` out, so `DB.VIV` is the only file in its directory and
        the rebuild budget collapses to its own sector-aligned length.
    """

    height: int = DEFAULT_DISC_HEIGHT
    master_name: str | None = "nhl2005.tdb"
    roster_name: str | None = "nhlrost.tdb"
    archive_magic: bytes = b"BIGF"
    declared_db_viv_size: int | None = None
    pvd_type: int = 1
    db_dir_name: str = "DB"
    db_is_file: bool = False
    pad_to: int = 0
    master_payload: bytes | None = None
    db_dir_exact_size: bool = False
    db_viv_last: bool = False
    no_pad_file: bool = False


def build_db_viv(spec: DiscSpec | None = None) -> bytes:
    """The BIGF archive, with each member RefPacked and padded with slack.

    Members are laid out master then roster, and a spec may drop either. The
    archive's spellings come from the spec, so a test can build a disc whose
    members are `NHL2005.TDB` in capitals and check that the write-back still
    finds them.
    """
    from retro_roster_patcher.formats.ea_tdb import refpack_compress

    spec = spec or DiscSpec()
    members: list[tuple[str, bytes]] = []
    for name, payload, verbatim in (
        (spec.master_name, build_master_tdb(spec.height), spec.master_payload),
        (spec.roster_name, build_roster_tdb(), None),
    ):
        if name is None:
            continue
        if verbatim is not None:
            members.append((name, verbatim))
            continue
        members.append((name, refpack_compress(payload) + b"\x00" * MEMBER_SLACK))

    archive = build_bigf(BigfSpec(files=members))
    if spec.archive_magic != b"BIGF":
        archive = spec.archive_magic + archive[4:]
    return archive


# ──────────────────────────────────────────────────────────────
# ISO 9660
# ──────────────────────────────────────────────────────────────


def _sectors_for(length: int) -> int:
    return (length + ISO_SECTOR_SIZE - 1) // ISO_SECTOR_SIZE


def build_iso(spec: DiscSpec | None = None) -> bytes:
    """A complete, well-formed NHL 2005 PS2 disc image.

    Sixteen empty sectors of system area, the PVD, two one-sector directories,
    `DB.VIV`, a gap, and one padding file.
    """
    spec = spec or DiscSpec()
    viv = build_db_viv(spec)

    pad_lba = DB_VIV_SECTOR + _sectors_for(len(viv)) + GAP_SECTORS
    root_file_lba = pad_lba + _sectors_for(len(PAD_FILE_BYTES))
    total_sectors = root_file_lba + _sectors_for(len(ROOT_FILE_BYTES))

    declared = spec.declared_db_viv_size
    if declared is None:
        declared = len(viv)

    db_records = [iso.dir_record(b"DB.VIV;1", DB_VIV_SECTOR, declared, is_dir=False)]
    if not spec.no_pad_file:
        db_records.append(
            iso.dir_record(
                f"{PAD_FILE_NAME};1".encode("ascii"),
                pad_lba,
                len(PAD_FILE_BYTES),
                is_dir=False,
            )
        )
    if spec.db_viv_last:
        db_records.reverse()

    db_dir = iso.directory_extent(DB_DIR_SECTOR, ROOT_DIR_SECTOR, db_records)
    db_dir_declared = iso.used_length(db_records) if spec.db_dir_exact_size else ISO_SECTOR_SIZE

    root_records = [
        iso.dir_record(
            spec.db_dir_name.encode("ascii"),
            DB_DIR_SECTOR,
            db_dir_declared,
            is_dir=not spec.db_is_file,
        ),
        iso.dir_record(
            f"{ROOT_FILE_NAME};1".encode("ascii"),
            root_file_lba,
            len(ROOT_FILE_BYTES),
            is_dir=False,
        ),
    ]
    root_dir = iso.directory_extent(ROOT_DIR_SECTOR, ROOT_DIR_SECTOR, root_records)

    image = iso.build_image(
        {
            ISO_PVD_SECTOR: iso.pvd(
                total_sectors,
                ROOT_DIR_SECTOR,
                ISO_SECTOR_SIZE,
                type_code=spec.pvd_type,
                volume_id=b"NHL05_FIXTURE",
            ),
            ROOT_DIR_SECTOR: root_dir,
            DB_DIR_SECTOR: db_dir,
            DB_VIV_SECTOR: viv,
            pad_lba: PAD_FILE_BYTES,
            root_file_lba: ROOT_FILE_BYTES,
        },
        total_sectors,
    )

    if spec.pad_to > len(image):
        image += b"\x00" * (spec.pad_to - len(image))
    return image


def write_iso(path, spec: DiscSpec | None = None) -> int:  # type: ignore[no-untyped-def]
    """Write a fabricated ISO to `path`, sparsely where it is padded.

    `spec.pad_to` above the image's natural length is written with `truncate`,
    so a 64 MB image for the copy path occupies one hole and a few hundred KB of
    real blocks. Returns the file's length in bytes.
    """
    spec = spec or DiscSpec()
    natural = DiscSpec(**{**spec.__dict__, "pad_to": 0})
    image = build_iso(natural)
    with open(path, "wb") as f:
        f.write(image)
        if spec.pad_to > len(image):
            f.truncate(spec.pad_to)
    return max(len(image), spec.pad_to)


# ──────────────────────────────────────────────────────────────
# Independent readers, for assertions
# ──────────────────────────────────────────────────────────────


def unpack_bits(fields: list[FieldSpec], record: bytes) -> dict[str, object]:
    """Decode one record LSB-first, the long way, from a `FieldSpec` list.

    The inverse of `synthetic_tdb.pack_bits` and deliberately not a call to
    `TDBTable.read_record`: a test that wrote through the module and read back
    through the module agrees with itself whatever bit width both used. Bit `i`
    of an integer field comes from bit `i % 8` of byte `(bit_offset + i) // 8`.

    A string field is truncated at its first NUL and decoded ASCII, unmappable
    bytes replaced -- the same rule `TDBTable.read_record` states, restated here
    rather than shared.
    """
    result: dict[str, object] = {}
    for f in fields:
        if f.field_type == TYPE_STRING:
            byte_off = f.bit_offset // 8
            raw = record[byte_off : byte_off + f.bit_width // 8]
            nul = raw.find(b"\x00")
            if nul >= 0:
                raw = raw[:nul]
            result[f.name] = raw.decode("ascii", errors="replace")
            continue
        value = 0
        for i in range(f.bit_width):
            bit_pos = f.bit_offset + i
            if record[bit_pos // 8] & (1 << (bit_pos % 8)):
                value |= 1 << i
        result[f.name] = value
    return result


def iso_read_file(image: bytes, path: str) -> bytes | None:
    """Read one file out of an ISO 9660 image, by an independent walk.

    Deliberately neither `formats/iso9660.py` nor `synthetic_iso`: this walk
    slices the image rather than seeking a handle, and it re-derives the record
    layout a third time. The three agree only if all three are right about where
    the PVD is, how a directory record is laid out, and which of the two endian
    copies of each number to believe.

    `path` is slash-separated and matched case-insensitively with any `;N`
    version suffix removed.
    """
    pvd_start = ISO_PVD_SECTOR * ISO_SECTOR_SIZE
    pvd = image[pvd_start : pvd_start + ISO_SECTOR_SIZE]
    if len(pvd) < ISO_SECTOR_SIZE or pvd[0] != 1:
        return None
    lba = struct.unpack_from("<I", pvd, 156 + 2)[0]
    size = struct.unpack_from("<I", pvd, 156 + 10)[0]

    parts = path.split("/")
    for depth, part in enumerate(parts):
        found = None
        extent = image[lba * ISO_SECTOR_SIZE : lba * ISO_SECTOR_SIZE + size]
        pos = 0
        while pos < len(extent):
            rec_len = extent[pos]
            if rec_len == 0:
                pos = ((pos // ISO_SECTOR_SIZE) + 1) * ISO_SECTOR_SIZE
                if pos >= len(extent):
                    break
                continue
            name_len = extent[pos + 32]
            name = extent[pos + 33 : pos + 33 + name_len].decode("ascii", errors="replace")
            if name.split(";")[0].upper() == part.upper():
                found = (
                    struct.unpack_from("<I", extent, pos + 2)[0],
                    struct.unpack_from("<I", extent, pos + 10)[0],
                    bool(extent[pos + 25] & 0x02),
                )
                break
            pos += rec_len
        if found is None:
            return None
        lba, size, is_dir = found
        if is_dir != (depth < len(parts) - 1):
            return None
    return image[lba * ISO_SECTOR_SIZE : lba * ISO_SECTOR_SIZE + size]


DB_VIV_ISO_PATH = "DB/DB.VIV"


def read_table_records(
    tdb_bytes: bytes, table: str, fields: list[FieldSpec], record_size: int
) -> list[dict[str, object]]:
    """Every allocated record of one table, decoded by `unpack_bits`.

    The table's *position* in the file comes from `TDBFile.parse`, which is the
    code under test and is the right tool for it -- `tests/formats/test_tdb.py`
    pins the parse against fixtures this module's sibling built. The record
    *values* come from `unpack_bits`, which is this file's own.
    """
    from retro_roster_patcher.formats.ea_tdb import TDBFile

    parsed = TDBFile.parse(tdb_bytes).get_table(table)
    if parsed is None:
        raise AssertionError(f"{table} is not in this TDB")
    raw = bytes(parsed._raw_data)
    return [
        unpack_bits(fields, raw[i * record_size : (i + 1) * record_size])
        for i in range(parsed.capacity)
    ]


def read_member(image: bytes, member: str) -> bytes:
    """One `DB.VIV` member of an ISO, decompressed to its TDB bytes."""
    from retro_roster_patcher.formats.ea_tdb import bigf_extract, refpack_decompress

    viv = iso_read_file(image, DB_VIV_ISO_PATH)
    if viv is None:
        raise AssertionError("this image has no DB.VIV")
    raw = bigf_extract(viv, member)
    if raw is None:
        raise AssertionError(f"DB.VIV has no member {member}")
    return refpack_decompress(raw) if raw[:2] == b"\x10\xfb" else raw
