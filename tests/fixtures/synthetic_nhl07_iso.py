"""Fabricate a complete NHL 07 (PSP) disc image in memory.

    ISO 9660 -> /PSP_GAME/USRDIR/DB/DB.VIV -> BIGF -> three RefPacked TDBs
             -> PLAY / ROST / SPBT / SPAI / SGAI / STEA

Nothing here comes from a real disc; no ISO may enter this repository. Every byte
is generated, and the field layouts are this file's invention -- the real ones
have never been seen by anything in this project, upstream included.

A real PSP image is 500 MB to 1.5 GB and this one is under 300 KB. The patcher
touches the PVD, four directory sectors and the `db.viv` extent and nothing else,
and `build_iso(pad_to=...)` inflates the file with `truncate`, so a
multi-megabyte image for the copy path costs a sparse hole rather than real
bytes.

Three things are independent reimplementations of code under test:

- `iso_read_file` walks ISO 9660 itself rather than calling
  `NHL07PSPRomReader._extract_db_viv`, so the reader's walk is checked against a
  second one.
- `unpack_bits` reads LSB-first bit fields the long way, from a `FieldSpec` list
  this file owns. `TDBTable.write_record` is what puts them there, so a test can
  assert a named field holds the number it should.
- `synthetic_tdb.mpeg2_crc`, which `build_tdb` uses for the chain, is the bitwise
  form of the nibble-table CRC in `formats/ea_tdb.py`.

`refpack_compress` *is* the module's own, and that is the one place this fixture
leans on the code under test. `tests/formats/test_refpack.py` pins its output
byte-for-byte against the source compressor.

Record contents are self-identifying: every player's name, jersey, weight and
every attribute encode the team, the roster row and the field, so a write that
landed on the wrong record, table or field cannot satisfy an assertion.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

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

ISO_SECTOR_SIZE = 2048
ISO_PVD_SECTOR = 16

# Sector assignments. Every one is fixed so a test can name a sector without
# recomputing the layout, and the four directories are one sector each because
# none of them holds more than four entries.
ROOT_DIR_SECTOR = 17
PSP_GAME_SECTOR = 18
USRDIR_SECTOR = 19
DB_DIR_SECTOR = 20
DB_VIV_SECTOR = 21

# How many spare sectors sit between the end of `db.viv` and the next file. This
# is what `find_db_viv_location` reports as the rebuild budget: the archive may
# grow into these and no further.
GAP_SECTORS = 2

# The file that follows `db.viv` on the disc. It exists to give
# `_find_entry_with_gap` a next LBA -- without it the budget collapses to
# `db.viv`'s own sector-aligned length -- and its contents are a recognisable
# pattern so a byte-level assertion can show the patcher did not walk past the
# archive.
PAD_FILE_NAME = "ZZPAD.BIN"
PAD_FILE_BYTES = bytes(range(256)) * 8  # 2048 bytes, every value eight times

# Slack appended to each `db.viv` member, inside its declared entry size.
# `bigf_replace_inplace` refuses a replacement larger than the entry it is
# overwriting, and a recompressed table is not guaranteed to be smaller than the
# original -- new names compress differently. Real EA archives carry slack for
# the same reason; `refpack_decompress` stops at its own end marker and never
# reads it.
MEMBER_SLACK = 8192


#
# Bit offsets are chosen so that most integer fields are NOT byte-aligned and
# several straddle a byte boundary. A layout of byte-aligned bytes would let a
# writer that ignored `bit_width` pass every test here.

# 46 bytes: two 20-byte names, then seven integers packed into the tail.
#   bits   0..159  FNME  20 ASCII bytes
#   bits 160..319  LNME  20 ASCII bytes
#   bits 320..335  INDX  16 bits, byte-aligned
#   bits 336..342  JERS   7 bits
#   bit  343       HAND   1 bit, the top bit of byte 42
#   bits 344..349  TEAM   6 bits
#   bits 350..352  POS_   3 bits, straddling bytes 43 and 44
#   bits 353..360  WEIG   8 bits, straddling bytes 44 and 45
#   bits 361..365  HEIG   5 bits
SPBT_FIELDS = [
    FieldSpec("FNME", TYPE_STRING, 0, 160),
    FieldSpec("LNME", TYPE_STRING, 160, 160),
    FieldSpec("INDX", TYPE_UINT, 320, 16),
    FieldSpec("JERS", TYPE_UINT, 336, 7),
    FieldSpec("HAND", TYPE_UINT, 343, 1),
    FieldSpec("TEAM", TYPE_UINT, 344, 6),
    FieldSpec("POS_", TYPE_UINT, 350, 3),
    FieldSpec("WEIG", TYPE_UINT, 353, 8),
    FieldSpec("HEIG", TYPE_UINT, 361, 5),
]
SPBT_RECORD_SIZE = 46

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

# The thirty single-bit line flags, in the order `rom_writer.LINE_FLAGS` gives
# them. Restated here rather than imported: importing the list under test would
# let a name dropped from it disappear from the fixture too, and every test
# would still pass.
LINE_FLAG_NAMES = [
    "L1C_",
    "L2C_",
    "L3C_",
    "L4C_",
    "L1LW",
    "L2LW",
    "L3LW",
    "L4LW",
    "L1RW",
    "L2RW",
    "L3RW",
    "L4RW",
    "31LD",
    "32LD",
    "33LD",
    "31RD",
    "32RD",
    "33RD",
    "G1__",
    "G2__",
    "H1__",
    "H2__",
    "H3__",
    "H4__",
    "H5__",
    "S1__",
    "S2__",
    "S3__",
    "S4__",
    "S5__",
]

#   bits  0.. 5  TEAM   6 bits
#   bits  6..12  JERS   7 bits
#   bits 13..28  INDX  16 bits, straddling three bytes
#   bits 29..30  CAPT   2 bits
#   bits 31..32  DRES   2 bits, straddling bytes 3 and 4
#   bits 33..62  the thirty line flags, one bit each
ROST_FIELDS = [
    FieldSpec("TEAM", TYPE_UINT, 0, 6),
    FieldSpec("JERS", TYPE_UINT, 6, 7),
    FieldSpec("INDX", TYPE_UINT, 13, 16),
    FieldSpec("CAPT", TYPE_UINT, 29, 2),
    FieldSpec("DRES", TYPE_UINT, 31, 2),
] + [FieldSpec(name, TYPE_UINT, 33 + i, 1) for i, name in enumerate(LINE_FLAG_NAMES)]
ROST_RECORD_SIZE = 8  # 63 bits

#   bits  0..15  INDX
#   bits 16..19  TBLE
#   bits 20..35  ID__   straddling bytes 2, 3 and 4
PLAY_FIELDS = [
    FieldSpec("INDX", TYPE_UINT, 0, 16),
    FieldSpec("TBLE", TYPE_UINT, 16, 4),
    FieldSpec("ID__", TYPE_UINT, 20, 16),
]
PLAY_RECORD_SIZE = 5  # 36 bits

#   byte  0        INDX   6 bits
#   bytes 1..16    NAME  16 ASCII bytes
#   bytes 17..32   CITY  16 ASCII bytes
STEA_FIELDS = [
    FieldSpec("INDX", TYPE_UINT, 0, 6),
    FieldSpec("NAME", TYPE_STRING, 8, 128),
    FieldSpec("CITY", TYPE_STRING, 136, 128),
]
STEA_RECORD_SIZE = 33


# Four teams is enough for the two collisions that matter -- a slot patched and
# a slot left alone -- while keeping the whole image under 300 KB. Twenty-five
# rows a team is what a real NHL 07 team carries and is also what
# `stat_mapper.MAX_PLAYERS` selects, so the fixture exercises the exactly-full
# case rather than only the short one.
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
    """
    return {
        "FNME": f"DiscFirst{team}",
        "LNME": f"DiscLast{team}x{row}",
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
        # a team share one. A patcher that failed to clear the flags it does not
        # set would leave these behind, and the test that checks a patched row
        # has exactly its new flags is what catches it.
        values[LINE_FLAG_NAMES[row % len(LINE_FLAG_NAMES)]] = 1
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


# How many team slots the fixture's STEA table declares. 32, the full NHL 07
# slot count, even though only the first `TEAM_COUNT` of them have roster rows:
# `analyze_rom` lists every STEA record, and a fixture that declared only four
# could not show that the other twenty-eight are listed too.
STEA_CAPACITY = 32


def stea_name(index: int) -> str:
    """The team name the fixture disc carries for slot `index`.

    Deliberately not any real club name: `RomSlot.current_name` must come from
    the disc, and a fixture that used the same strings as `NHL07_TEAM_NAMES`
    could not tell a reader that read STEA from one that returned the constant.
    """
    return f"Disc Club {index:02d}"


def _stea_table() -> TableSpec:
    records = bytearray()
    for index in range(STEA_CAPACITY):
        records += pack_bits(
            STEA_FIELDS,
            {"INDX": index, "NAME": stea_name(index), "CITY": f"Town {index:02d}"},
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


def build_master_tdb(height: int = DEFAULT_DISC_HEIGHT) -> bytes:
    """`nhl2007.tdb`: every table, in an order the patcher does not assume.

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


def build_bioatt_tdb(height: int = DEFAULT_DISC_HEIGHT) -> bytes:
    """`nhlbioatt.tdb`: the SPBT/SPAI/SGAI mirror, same layouts, same capacities."""
    return build_tdb(
        [
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
        ]
    )


def build_roster_tdb() -> bytes:
    """`nhlrost.tdb`: the ROST mirror."""
    return build_tdb([_rost_table()])


@dataclass
class DiscSpec:
    """Everything a test may vary about the fabricated disc.

    Defaults build a well-formed NHL 07 image. Each override below produces one
    specific malformation, and the name says which:

    `height`
        the `HEIG` every SPBT record ships with.
    `master_name`, `bioatt_name`, `roster_name`
        the archive's own spelling of each member. `None` leaves it out.
    `archive_magic`
        the four bytes `db.viv` starts with, for the "not a BIGF" case.
    `declared_db_viv_size`
        what the ISO 9660 directory record claims `db.viv`'s length is,
        independently of how many bytes are actually written. Larger than the
        truth is the truncated-extent case the arithmetic bound refuses.
    `pvd_type`
        byte 0 of the Primary Volume Descriptor. Anything but 1 is not a PVD.
    `db_dir_name`
        the third directory's name. Anything but `DB` breaks the path walk.
    `pad_to`
        inflate the image to at least this many bytes with `truncate`, which
        costs a sparse hole and not real storage.
    `bioatt_payload`
        raw bytes for the `nhlbioatt.tdb` member, used verbatim with no
        compression and no slack. For the two cases where the archive holds a
        member of that name which is not a TDB.
    `root_dir_size`
        what the PVD declares the root directory's length to be, independently
        of how much was written there. Shorter than the truth is the case where
        an entry lies outside the declared extent and must not be found.
    `db_dir_exact_size`
        declare the `DB` directory's length as exactly its records rather than a
        whole sector, so its last record ends flush with the end of the extent.
    `db_viv_last`
        list `DB.VIV` after the padding file in the `DB` directory. With
        `db_dir_exact_size` it becomes the flush-final record, which is the one
        a scan breaking on `pos + rec_len >= len` rather than `>` loses.
    """

    height: int = DEFAULT_DISC_HEIGHT
    master_name: str | None = "nhl2007.tdb"
    bioatt_name: str | None = "nhlbioatt.tdb"
    roster_name: str | None = "nhlrost.tdb"
    archive_magic: bytes = b"BIGF"
    declared_db_viv_size: int | None = None
    pvd_type: int = 1
    db_dir_name: str = "DB"
    pad_to: int = 0
    bioatt_payload: bytes | None = None
    root_dir_size: int | None = None
    db_dir_exact_size: bool = False
    db_viv_last: bool = False


def build_db_viv(spec: DiscSpec | None = None) -> bytes:
    """The BIGF archive, with each member RefPacked and padded with slack.

    Members are laid out master, bioatt, roster, and a spec may drop any of
    them. The archive's spellings come from the spec, so a test can build a disc
    whose members are `NHL2007.TDB` in capitals and check that the write-back
    still finds them.
    """
    from retro_roster_patcher.formats.ea_tdb import refpack_compress

    spec = spec or DiscSpec()
    members: list[tuple[str, bytes]] = []
    for name, payload, verbatim in (
        (spec.master_name, build_master_tdb(spec.height), None),
        (spec.bioatt_name, build_bioatt_tdb(spec.height), spec.bioatt_payload),
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


# A fixed recording timestamp, so two builds of the same spec are byte-identical.
# Year is stored as years since 1900: 106 is 2006.
_RECORDING_DATE = bytes([106, 9, 15, 12, 0, 0, 0])


def _dir_record(name: bytes, lba: int, size: int, *, is_dir: bool) -> bytes:
    """One ISO 9660 directory record, complete rather than minimal.

    Both endian copies of the extent and the length are written, and the
    little-endian ones are what the reader uses. A fixture that wrote only those
    could not show that the writer's big-endian fix-up at +14 lands where it
    should.

    Records are padded to an even length, which ISO 9660 requires and which the
    scan in `_find_dir_entry` depends on only through `rec_len`.
    """
    length = 33 + len(name)
    if length % 2:
        length += 1
    record = bytearray(length)
    record[0] = length
    record[1] = 0  # extended attribute record length
    struct.pack_into("<I", record, 2, lba)
    struct.pack_into(">I", record, 6, lba)
    struct.pack_into("<I", record, 10, size)
    struct.pack_into(">I", record, 14, size)
    record[18:25] = _RECORDING_DATE
    record[25] = 0x02 if is_dir else 0x00
    record[26] = 0  # file unit size
    record[27] = 0  # interleave gap size
    struct.pack_into("<H", record, 28, 1)
    struct.pack_into(">H", record, 30, 1)
    record[32] = len(name)
    record[33 : 33 + len(name)] = name
    return bytes(record)


def _directory_sector(
    self_lba: int, parent_lba: int, entries: list[bytes], *, exact: bool = False
) -> tuple[bytes, int]:
    """A one-sector directory extent: `.`, `..`, then the given records.

    `.` and `..` carry the one-byte names 0x00 and 0x01 that ISO 9660 reserves
    for them. `_find_dir_entry` compares them like any other name and never
    matches, and `_find_entry_with_gap` skips them on `name_len > 1`, so both
    behaviours are exercised by every walk over this sector.
    """
    declared = ISO_SECTOR_SIZE
    out = bytearray()
    out += _dir_record(b"\x00", self_lba, declared, is_dir=True)
    out += _dir_record(b"\x01", parent_lba, declared, is_dir=True)
    for entry in entries:
        out += entry
    if len(out) > ISO_SECTOR_SIZE:
        raise AssertionError(f"directory is {len(out)} bytes, over one sector")
    used = len(out)
    return bytes(out) + b"\x00" * (ISO_SECTOR_SIZE - used), (used if exact else ISO_SECTOR_SIZE)


def _pvd(total_sectors: int, pvd_type: int, root_size: int = ISO_SECTOR_SIZE) -> bytes:
    """The Primary Volume Descriptor, with the root directory record at 156."""
    pvd = bytearray(ISO_SECTOR_SIZE)
    pvd[0] = pvd_type
    pvd[1:6] = b"CD001"
    pvd[6] = 1
    pvd[8:40] = b" " * 32  # system identifier
    pvd[40:72] = b"NHL07_FIXTURE".ljust(32)
    struct.pack_into("<I", pvd, 80, total_sectors)
    struct.pack_into(">I", pvd, 84, total_sectors)
    struct.pack_into("<H", pvd, 120, 1)  # volume set size
    struct.pack_into(">H", pvd, 122, 1)
    struct.pack_into("<H", pvd, 124, 1)  # volume sequence number
    struct.pack_into(">H", pvd, 126, 1)
    struct.pack_into("<H", pvd, 128, ISO_SECTOR_SIZE)
    struct.pack_into(">H", pvd, 130, ISO_SECTOR_SIZE)
    root = _dir_record(b"\x00", ROOT_DIR_SECTOR, root_size, is_dir=True)
    pvd[156 : 156 + len(root)] = root
    return bytes(pvd)


def _sectors_for(length: int) -> int:
    return (length + ISO_SECTOR_SIZE - 1) // ISO_SECTOR_SIZE


def build_iso(spec: DiscSpec | None = None) -> bytes:
    """A complete, well-formed NHL 07 PSP disc image.

    Sixteen empty sectors of system area, the PVD, four one-sector directories,
    `db.viv`, a gap, and one padding file. `build_iso().__len__()` is under
    300 KB with the default spec.
    """
    spec = spec or DiscSpec()
    viv = build_db_viv(spec)

    pad_lba = DB_VIV_SECTOR + _sectors_for(len(viv)) + GAP_SECTORS
    total_sectors = pad_lba + _sectors_for(len(PAD_FILE_BYTES))

    declared = spec.declared_db_viv_size
    if declared is None:
        declared = len(viv)

    image = bytearray(total_sectors * ISO_SECTOR_SIZE)

    def put(lba: int, data: bytes) -> None:
        image[lba * ISO_SECTOR_SIZE : lba * ISO_SECTOR_SIZE + len(data)] = data

    # Built before the two directories that name it, because
    # `db_dir_exact_size` needs the length its records actually occupy -- an
    # extent that ends flush with its final record, which is the case a scan
    # breaking on `pos + rec_len >= len` rather than `>` loses.
    db_records = [
        _dir_record(b"DB.VIV;1", DB_VIV_SECTOR, declared, is_dir=False),
        _dir_record(
            f"{PAD_FILE_NAME};1".encode("ascii"),
            pad_lba,
            len(PAD_FILE_BYTES),
            is_dir=False,
        ),
    ]
    if spec.db_viv_last:
        db_records.reverse()
    db_dir_sector, db_dir_used = _directory_sector(
        DB_DIR_SECTOR,
        USRDIR_SECTOR,
        db_records,
        exact=spec.db_dir_exact_size,
    )

    root_sector, root_used = _directory_sector(
        ROOT_DIR_SECTOR,
        ROOT_DIR_SECTOR,
        [_dir_record(b"PSP_GAME", PSP_GAME_SECTOR, ISO_SECTOR_SIZE, is_dir=True)],
    )
    root_size = spec.root_dir_size if spec.root_dir_size is not None else root_used
    put(ISO_PVD_SECTOR, _pvd(total_sectors, spec.pvd_type, root_size))
    put(ROOT_DIR_SECTOR, root_sector)
    put(
        PSP_GAME_SECTOR,
        _directory_sector(
            PSP_GAME_SECTOR,
            ROOT_DIR_SECTOR,
            [_dir_record(b"USRDIR", USRDIR_SECTOR, ISO_SECTOR_SIZE, is_dir=True)],
        )[0],
    )
    put(
        USRDIR_SECTOR,
        _directory_sector(
            USRDIR_SECTOR,
            PSP_GAME_SECTOR,
            [
                _dir_record(
                    spec.db_dir_name.encode("ascii"),
                    DB_DIR_SECTOR,
                    db_dir_used,
                    is_dir=True,
                )
            ],
        )[0],
    )
    put(DB_DIR_SECTOR, db_dir_sector)
    put(DB_VIV_SECTOR, viv)
    put(pad_lba, PAD_FILE_BYTES)

    if spec.pad_to > len(image):
        image.extend(b"\x00" * (spec.pad_to - len(image)))
    return bytes(image)


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

    Deliberately not `NHL07PSPRomReader._extract_db_viv`. The two walks agree
    only if both are right about where the PVD is, how a directory record is
    laid out, and which of the two endian copies of each number to believe.

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


DB_VIV_ISO_PATH = "PSP_GAME/USRDIR/DB/DB.VIV"


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
    """One `db.viv` member of an ISO, decompressed to its TDB bytes."""
    from retro_roster_patcher.formats.ea_tdb import bigf_extract, refpack_decompress

    viv = iso_read_file(image, DB_VIV_ISO_PATH)
    if viv is None:
        raise AssertionError("this image has no db.viv")
    raw = bigf_extract(viv, member)
    if raw is None:
        raise AssertionError(f"db.viv has no member {member}")
    return refpack_decompress(raw) if raw[:2] == b"\x10\xfb" else raw
