"""Fabricate an MVP Baseball (PSP) disc image in memory.

    ISO bytes -> database.big at a chosen LBA -> 19 RefPack sections
              -> 19 CSV tables linked by nine-hex-digit ids

Nothing here comes from a real disc; no ISO may enter this repository. Every
byte is generated, and the column layouts are this file's invention -- the real
ones have never been seen by anything in this project, upstream included.

**A real PSP UMD image is hundreds of megabytes and this one is under 500 KB.**
The patcher touches exactly one region, `[lba * 2048, lba * 2048 + 386977)`, and
nothing else, so the fixture writes a small header, that region, and a short
tail. Tests shrink `models.DATABASE_BIG_LBA` with `monkeypatch` --
`models.database_big_extent()` reads it at call time, so the reader, the writer
and the patcher shrink together -- and `use_small_layout` is the one place that
happens. A sparse file would not help: `MVPPSPRomWriter.copy_iso` reads the
image and writes what it read, so a 686 MB hole costs 686 MB of real writes.

**Three parts of this file are independent reimplementations of code under
test, and that is the point of it.** Every read-back in a test goes through
these rather than through the writer that produced the bytes, so a bug shared
between a writer and its own reader cannot satisfy an assertion:

- `render_table` builds CSV section text from the format description, not from
  `rom_writer.build_csv_section`. It emits columns in the order it is given
  them, which lets a fixture hold a record whose columns are *not* ascending --
  the one case where `build_csv_record`'s `sorted()` is visible.
- `parse_table` reads a section back the long way, not through
  `rom_reader.parse_csv_section`.
- `read_database_big` seeks the extent out of a written image with its own
  arithmetic, not through `MVPPSPRomReader.load`.

`refpack_compress` and `refpack_decompress` *are* the module's own, and that is
the one place this fixture leans on code under test. It is justified:
`tests/formats/test_refpack.py` pins the compressor byte-for-byte against the
source compressor over fifteen inputs covering all seven command families, on
top of a 52-case round-trip corpus, so a second compressor here would be a
second thing to get wrong.

**No identifier equals a record position.** Every generated id is at least
0x100000000, which is the smallest nine-hex-digit value, so no id can collide
with an index, a slot number or a loop counter. Names, jersey numbers and every
rating encode the team and the roster slot they belong to, so a write that
landed on the wrong record, the wrong table or the wrong column cannot satisfy
an assertion.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from retro_roster_patcher.formats.ea_tdb import refpack_compress, refpack_decompress
from retro_roster_patcher.games.mvp_psp import models as mvp_models

SECTOR_SIZE = mvp_models.ISO_SECTOR_SIZE
DATABASE_BIG_SIZE = mvp_models.DATABASE_BIG_SIZE

# The LBA tests use in place of the real 334 832. 40 sectors is 81 920 bytes,
# enough to hold a plausible ISO header region ahead of the blob and small
# enough that the whole image is under half a megabyte.
SMALL_LBA = 40

# How many bytes follow `database.big` in a fixture image. It exists so a test
# can prove the patcher wrote nothing past the extent it declared.
TAIL_BYTES = 4096

# What fills the image outside `database.big`. A repeating pattern with no
# zero byte in it, so a region the patcher zeroed by mistake is visible; and
# not a constant, so an off-by-one in the seek shows up as a shifted pattern
# rather than as the same byte.
FILLER = bytes(range(1, 256))

# The smallest nine-hex-digit number. Every generated id is at or above it, so
# no id can equal a record position, a team slot or a loop counter.
ID_BASE = 0x100000000

# Strides used to space generated ids out. `TEAM_STRIDE` is larger than
# `SLOT_LIMIT * PLAYER_STRIDE`, so no two (team, slot) pairs collide, and every
# id stays inside nine hex digits -- the largest is under 0xFFFFFFFFF.
TEAM_STRIDE = 0x3B9ACA07  # 1 000 000 007
PLAYER_STRIDE = 0x1E8481  # 2 000 001
SLOT_LIMIT = 64

# Roster rows live above every player id and are strided far more finely, so
# `patch`'s `max(...) + 1` counter starts above the whole generated id space
# while staying nine digits for the seven hundred and fifty ids it may issue.
ROSTER_BASE = 0xA00000000
ROSTER_STRIDE = 0x10001

# The separator between a column number and its value, and the record and line
# terminators. Spelled out here rather than imported so this file describes the
# format independently.
COLUMN_SEP = " "
RECORD_SEP = ","
RECORD_SUFFIX = ",;"
HEADER_SUFFIX = ";"
CRLF = "\r\n"


def player_id(team: int, slot: int) -> str:
    """A nine-hex-digit id for the disc's own player at (team, slot).

    Never equal to `team`, to `slot`, or to the record's position in any table.

    **Ids descend as `slot` ascends**, while the tables are written in ascending
    slot order. So a section's record order is deliberately *not* its sorted
    order, which is what makes preserving the disc's order distinguishable from
    sorting -- `build_csv_section` does one when it has an order and the other
    when it does not, and a fixture in ascending id order could not tell them
    apart.
    """
    return f"{ID_BASE + team * TEAM_STRIDE + (SLOT_LIMIT - 1 - slot) * PLAYER_STRIDE:09x}"


def roster_row_id(team: int, slot: int) -> str:
    """A nine-hex-digit id for a `roster` row.

    Disjoint from `player_id`'s range by construction, and higher than every
    one of them, so `patch`'s `max(...) + 1` counter starts above the whole
    generated id space and a new row cannot collide with an old one.
    """
    return f"{ROSTER_BASE + ROSTER_STRIDE * (1 + team * SLOT_LIMIT + slot):09x}"


# ──────────────────────────────────────────────────────────────
# CSV rendering and parsing, both independent of the code under test
# ──────────────────────────────────────────────────────────────


def render_record(hash_id: str, columns: list[tuple[int, str]]) -> str:
    """One record line, in the order the columns are given.

    Written from the format description rather than from
    `rom_writer.build_csv_record`, and deliberately *not* sorting: a fixture
    that hands the columns over out of order is the only way to see that the
    writer sorts them.
    """
    parts = [hash_id]
    parts.extend(f"{number}{COLUMN_SEP}{value}" for number, value in columns)
    return RECORD_SEP.join(parts) + RECORD_SUFFIX


def render_table(header: str, records: list[tuple[str, list[tuple[int, str]]]]) -> bytes:
    """A whole section: the header line, then one line per record."""
    lines = [header + HEADER_SUFFIX + CRLF]
    lines.extend(render_record(hash_id, columns) + CRLF for hash_id, columns in records)
    return "".join(lines).encode("ascii")


def parse_table(data: bytes) -> dict[str, dict[int, str]]:
    """Read a section back into `{id: {column: value}}`, the long way.

    An independent reimplementation of `rom_reader.parse_csv_section`, so a
    test asserting on what the patcher wrote is not reading it back through the
    same parse that produced it. It differs in one way that matters and is
    stated so nobody harmonises them: it keeps the record's own *order* of
    columns nowhere, because a dict does not have one -- assertions about column
    ordering read `parse_table_order` instead.
    """
    result: dict[str, dict[int, str]] = {}
    for line in data.decode("ascii", errors="replace").split(HEADER_SUFFIX + CRLF):
        line = line.strip()
        if RECORD_SEP not in line:
            continue
        parts = line.split(RECORD_SEP)
        hash_id = parts[0].strip()
        if len(hash_id) < 5 or any(c not in "0123456789abcdef" for c in hash_id):
            continue
        columns: dict[int, str] = {}
        for part in parts[1:]:
            if not part.strip():
                continue
            head, sep, tail = part.partition(COLUMN_SEP)
            if not sep:
                continue
            try:
                columns[int(head)] = tail
            except ValueError:
                continue
        if columns:
            result[hash_id] = columns
    return result


def parse_table_order(data: bytes) -> list[str]:
    """The record ids of a section, in the order the section lists them.

    Repeats included: a table holding one id twice yields it twice, which is
    what makes the duplicate-id behaviour testable.
    """
    order: list[str] = []
    for line in data.decode("ascii", errors="replace").split(HEADER_SUFFIX + CRLF):
        line = line.strip()
        if RECORD_SEP not in line:
            continue
        hash_id = line.split(RECORD_SEP)[0].strip()
        if len(hash_id) < 5 or any(c not in "0123456789abcdef" for c in hash_id):
            continue
        if COLUMN_SEP in line.split(RECORD_SEP, 1)[1]:
            order.append(hash_id)
    return order


# ──────────────────────────────────────────────────────────────
# Table contents
# ──────────────────────────────────────────────────────────────

# Header lines, one per section. Each names its columns, and no first token is
# nine hex characters, so `_looks_like_record_id` rejects every one of them.
# `attrib`'s names its real columns; the tables the patcher never rewrites get a
# short generic header, because their contents only have to survive being copied
# through.
ATTRIB_HEADER = (
    "firstname,lastname,jersey,bats,throws,primarypos,secondarypos,"
    "unused7,unused8,height,weight,pad11,pad12,pad13,pad14,pad15,pad16,pad17,"
    "discipline,bunting,stealing,baserunning,speed,fielding,armrange,"
    "throwpower,throwacc,durability,pad28,pad29,pad30,pad31,pad32,pad33,"
    "pad34,pad35,pad36,pad37,pad38,salary,contract,starpower,pad42,birthday"
)
LR_HEADER = "firstname,lastname,contact,power,sprayul,spraycm,fieldlf,hrpct,groundball"
PITCHATTRIB_HEADER = (
    "firstname,lastname,stamina,pickoff,p1move,p1desc,p1ctrl,p1velo,"
    "p2type,p2move,p2desc,p2ctrl,p2velo,p3type,p3move,p3desc,p3ctrl,p3velo,"
    "p4type,p4move,p4desc,p4ctrl,p4velo,p5type,p5move,p5desc,p5ctrl,p5velo,delivery"
)
ROSTER_HEADER = (
    "teamid,playerid,rhalpos,rhalorder,rhnlpos,rhnlorder,lhalpos,lhalorder,lhnlpos,lhnlorder"
)
TEAM_HEADER = "teamname,league,division,artid"
GENERIC_HEADER = "keyone,keytwo,keythree"

# Which sections get which header. Every name in `models.SECTION_MAP` appears.
SECTION_HEADERS: dict[str, str] = {
    "attrib_compact": GENERIC_HEADER,
    "attrib": ATTRIB_HEADER,
    "lrattrib_rhp": LR_HEADER,
    "lrattrib_lhp": LR_HEADER,
    "batstat": GENERIC_HEADER,
    "fieldstat": GENERIC_HEADER,
    "lrbatstat_rhp": GENERIC_HEADER,
    "lrpitchstat_rhp": GENERIC_HEADER,
    "pitchstat": GENERIC_HEADER,
    "lrbatstat_lhp": GENERIC_HEADER,
    "lrpitchstat_lhp": GENERIC_HEADER,
    "pitchattrib": PITCHATTRIB_HEADER,
    "team": TEAM_HEADER,
    "teamstat": GENERIC_HEADER,
    "roster": ROSTER_HEADER,
    "careerstats": GENERIC_HEADER,
    "pitchcareer": GENERIC_HEADER,
    "organization": GENERIC_HEADER,
    "manager": GENERIC_HEADER,
}

# How many of a disc team's players are pitchers, and therefore have a
# `pitchattrib` row. The rest are batters. Chosen so that neither pool is a
# multiple or a half of the other: a bug that swapped the two pools would
# otherwise still hand out the right number of ids.
PITCHER_SHARE = 3  # every third disc player is a pitcher


def _is_disc_pitcher(slot: int) -> bool:
    return slot % PITCHER_SHARE == 0


def _attrib_columns(team: int, slot: int) -> list[tuple[int, str]]:
    """The disc's own `attrib` record for one player.

    Every value encodes the team and the slot, so a record written to the wrong
    id is visible. Height and weight are *plausible and distinct per player*,
    which is what makes the height and weight divergences testable: a patcher
    that stamped one constant over them would be obvious.

    Column 43 (birthday) and column 39 (salary) are here and are never written
    by the patcher, so they are the two that prove the merge preserves what it
    was not asked about.

    Column 6, the second position, is here for a narrower reason.
    `_build_attrib_fields` writes it only when the mapper produced one, which it
    never does, and writing an empty string instead would erase the disc's own
    value and leave the player unable to be moved in the field. Without a
    disc-side value there is nothing for that guard to protect and dropping it
    was invisible.
    """
    return [
        (mvp_models.ATTRIB_FIRST_NAME, f"Disc{team:02d}"),
        (mvp_models.ATTRIB_LAST_NAME, f"Player{slot:02d}"),
        (mvp_models.ATTRIB_JERSEY, str(team * 2 + slot)),
        (mvp_models.ATTRIB_BATS, str(slot % 3)),
        (mvp_models.ATTRIB_THROWS, str(slot % 2)),
        (mvp_models.ATTRIB_PRIMARY_POS, str(slot % 9)),
        (mvp_models.ATTRIB_SECONDARY_POS, str((slot + 4) % 9)),
        (mvp_models.ATTRIB_HEIGHT, str(68 + (team + slot) % 11)),
        (mvp_models.ATTRIB_WEIGHT, str(150 + (team * 3 + slot * 7) % 91)),
        (mvp_models.ATTRIB_SPEED, str((team + slot) % 100)),
        (mvp_models.ATTRIB_SALARY, f"{team}{slot}00000"),
        (mvp_models.ATTRIB_BIRTHDAY, f"19{70 + (team + slot) % 20}0101"),
    ]


def _lr_columns(team: int, slot: int, vs: str) -> list[tuple[int, str]]:
    """A split-attribute record. The spray columns are never written by the patcher."""
    bump = 0 if vs == "rhp" else 7
    return [
        (mvp_models.LR_FIRST_NAME, f"Disc{team:02d}"),
        (mvp_models.LR_LAST_NAME, f"Player{slot:02d}"),
        (mvp_models.LR_CONTACT, str((team * 5 + slot + bump) % 100)),
        (mvp_models.LR_POWER, str((team * 7 + slot + bump) % 100)),
        (mvp_models.LR_SPRAY_UL, str((team + slot + bump) % 100)),
        (mvp_models.LR_GB, str((team * 11 + slot + bump) % 100)),
    ]


def _pitchattrib_columns(team: int, slot: int) -> list[tuple[int, str]]:
    """A pitching record, including the delivery column the patcher never writes."""
    return [
        (mvp_models.PA_FIRST_NAME, f"Disc{team:02d}"),
        (mvp_models.PA_LAST_NAME, f"Player{slot:02d}"),
        (mvp_models.PA_STAMINA, str((team * 3 + slot) % 100)),
        (mvp_models.PA_PICKOFF, str((team * 13 + slot) % 100)),
        (mvp_models.PA_PITCH1_VELOCITY, str((team * 17 + slot) % 100)),
        (mvp_models.PA_PITCHER_DELIVERY, str((team + slot) % 8)),
    ]


def _roster_columns(team_hash: str, pid: str, slot: int) -> list[tuple[int, str]]:
    return [
        (mvp_models.ROSTER_TEAMID, team_hash),
        (mvp_models.ROSTER_PLAYERID, pid),
        (mvp_models.ROSTER_RH_AL_POS, mvp_models.LINEUP_POSITIONS[slot % 9]),
        (mvp_models.ROSTER_RH_AL_ORDER, str(slot % 9 + 1)),
    ]


@dataclass
class DiscSpec:
    """What to put on a fabricated disc.

    Attributes:
        teams: how many of the 30 slots have a roster. Slots past this have no
            `roster` rows at all, which is what an unpopulated slot looks like.
        players_per_team: how many players each of those teams has. This is
            also what sizes the id pool `patch` draws from, so a test that wants
            the pool to run out sets it low and one that wants it not to sets it
            high.
        compact_flag_c0: give section 0 the `0xC0` first byte a real disc is
            reported to have, rather than the ordinary `0x10`. Both are accepted
            by `validate` and handled by `decompress_section`, along different
            branches.
        team_records: write the `team` table with MVP Baseball's own 30 team
            ids. False leaves the table populated with ids that are not this
            game's, which is what `validate_deep` must reject.
        extra_attrib_columns: appended verbatim to every `attrib` record, to
            make the table larger. Used to build a disc whose rebuilt sections
            do not fit.
        duplicate_first_player: emit the first team's first player's `attrib`
            record twice, with different values the second time.
        attrib_headroom_bytes: pad the `attrib` section with incompressible
            filler records until its compressed form is within this many bytes
            of its 61 448-byte allocation. That is how a disc is fabricated on
            which a real roster patch cannot be stored -- which is the
            condition the source silently swallowed. None leaves the section
            its natural size.
    """

    teams: int = 30
    players_per_team: int = 8
    compact_flag_c0: bool = True
    team_records: bool = True
    extra_attrib_columns: int = 0
    duplicate_first_player: bool = False
    attrib_headroom_bytes: int | None = None
    header_overrides: dict[str, str] = field(default_factory=dict)


def _build_sections(spec: DiscSpec) -> dict[str, bytes]:
    """The decompressed bytes of all nineteen sections."""
    attrib: list[tuple[str, list[tuple[int, str]]]] = []
    lr_rhp: list[tuple[str, list[tuple[int, str]]]] = []
    lr_lhp: list[tuple[str, list[tuple[int, str]]]] = []
    pitchattrib: list[tuple[str, list[tuple[int, str]]]] = []
    roster: list[tuple[str, list[tuple[int, str]]]] = []

    for team in range(spec.teams):
        abbrev = mvp_models.MVP_TEAM_ABBREVS[team]
        team_hash = mvp_models.TEAM_HASHES[abbrev]
        for slot in range(spec.players_per_team):
            pid = player_id(team, slot)
            columns = _attrib_columns(team, slot)
            columns.extend(
                (100 + i, f"filler{team:02d}{slot:02d}{i:02d}")
                for i in range(spec.extra_attrib_columns)
            )
            attrib.append((pid, columns))
            lr_rhp.append((pid, _lr_columns(team, slot, "rhp")))
            lr_lhp.append((pid, _lr_columns(team, slot, "lhp")))
            if _is_disc_pitcher(slot):
                pitchattrib.append((pid, _pitchattrib_columns(team, slot)))
            roster.append((roster_row_id(team, slot), _roster_columns(team_hash, pid, slot)))

    if spec.attrib_headroom_bytes is not None:
        _pad_to_headroom(attrib, spec.attrib_headroom_bytes)

    if spec.duplicate_first_player and attrib:
        first_id = attrib[0][0]
        attrib.append((first_id, [(mvp_models.ATTRIB_FIRST_NAME, "Duplicate")]))

    if spec.team_records:
        team_rows = [
            (
                mvp_models.TEAM_HASHES[abbrev],
                [
                    (mvp_models.TEAM_NAME, mvp_models.MVP_TEAM_ORDER[i]),
                    (mvp_models.TEAM_LEAGUE, "0" if i < mvp_models.AL_SLOT_COUNT else "1"),
                ],
            )
            for i, abbrev in enumerate(mvp_models.MVP_TEAM_ABBREVS)
        ]
    else:
        # Ids of the right shape that are not this game's, so the shallow
        # header check still passes and the deep one does not.
        team_rows = [
            (f"{ID_BASE + i * TEAM_STRIDE:09x}", [(mvp_models.TEAM_NAME, f"Other{i:02d}")])
            for i in range(30)
        ]

    contents = {
        "attrib": attrib,
        "lrattrib_rhp": lr_rhp,
        "lrattrib_lhp": lr_lhp,
        "pitchattrib": pitchattrib,
        "roster": roster,
        "team": team_rows,
    }

    sections: dict[str, bytes] = {}
    for index, (_, name) in enumerate(mvp_models.SECTION_MAP):
        header = spec.header_overrides.get(name, SECTION_HEADERS[name])
        records = contents.get(
            name,
            # Two rows for every table this patcher never touches, so a copy
            # that dropped one is visible and a `len` assertion is not against
            # one item.
            [
                (f"{ID_BASE + (index * 2 + n) * TEAM_STRIDE:09x}", [(0, f"{name}{n}")])
                for n in range(2)
            ],
        )
        sections[name] = render_table(header, records)
    return sections


#: Ids for the filler records `_pad_to_headroom` adds. Far above every other
#: generated id, so they cannot collide with a player or a roster row.
FILLER_ID_BASE = 0xF00000000


def _pad_to_headroom(attrib: list, headroom: int) -> None:
    """Grow `attrib` until it compresses to just under its allocation.

    The result lands in `[allocation - headroom, allocation]`, so the section
    still fits -- a disc that could not hold its own tables could not exist --
    while leaving too little room for a patch to grow it. That is the condition
    the source silently swallowed and `SectionTooLargeError` now refuses.

    Filler values are hex digits from a seeded generator, which RefPack cannot
    compress well, so each record costs a predictable and roughly constant
    number of bytes. The count is estimated from one measured batch rather than
    found by recompressing the whole table per record, which for a 60 KB
    section is seconds of work.
    """
    _, allocation = mvp_models.SECTION_ALLOCATIONS["attrib"]
    target = allocation - headroom
    rng = random.Random(0x4D5650)
    header = SECTION_HEADERS["attrib"]

    def filler(n: int) -> tuple:
        value = "".join(rng.choice("0123456789abcdef") for _ in range(120))
        return (f"{FILLER_ID_BASE + n:09x}", [(200, value)])

    def size() -> int:
        return len(refpack_compress(render_table(header, attrib)))

    base = size()
    if base >= target:
        return
    sample = 64
    for n in range(sample):
        attrib.append(filler(n))
    per_record = (size() - base) / sample
    wanted = int((target - size()) / per_record)
    for n in range(sample, sample + max(wanted, 0)):
        attrib.append(filler(n))
    # Then one at a time, which costs one recompression per step and never
    # more than a handful of steps.
    n = len(attrib)
    while size() < target:
        attrib.append(filler(n))
        n += 1
    while size() > allocation:
        attrib.pop()


#: Built blobs, keyed by their `DiscSpec`. Building one costs a RefPack pass
#: over every section, and a padded disc costs several more while
#: `_pad_to_headroom` converges; a whole test module asks for the same handful
#: of specs over and over. The bytes are immutable and the build is
#: deterministic, so handing the same object back is safe.
_BLOB_CACHE: dict[tuple, bytes] = {}


def _spec_key(spec: DiscSpec) -> tuple:
    return (
        spec.teams,
        spec.players_per_team,
        spec.compact_flag_c0,
        spec.team_records,
        spec.extra_attrib_columns,
        spec.duplicate_first_player,
        spec.attrib_headroom_bytes,
        tuple(sorted(spec.header_overrides.items())),
    )


def build_database_big(spec: DiscSpec | None = None) -> bytes:
    """The whole 386 977-byte `database.big`, sections at their real offsets.

    Every section is compressed with `refpack_compress` and zero-padded out to
    its allocation, which is exactly what `MVPPSPRomWriter.rebuild_database_big`
    does with a rewritten one -- and is why a test that only compared the two
    would prove nothing. The read-backs go through `parse_table`.

    Raises:
        ValueError: a section does not fit its allocation. That is the fixture
            refusing to fabricate a disc that could not exist, and it is
            distinct from `SectionTooLargeError`, which is the patcher refusing
            to write one.
    """
    spec = spec or DiscSpec()
    key = _spec_key(spec)
    cached = _BLOB_CACHE.get(key)
    if cached is not None:
        return cached
    sections = _build_sections(spec)
    blob = bytearray(DATABASE_BIG_SIZE)
    for _, name in mvp_models.SECTION_MAP:
        offset, allocation = mvp_models.SECTION_ALLOCATIONS[name]
        compressed = bytearray(refpack_compress(sections[name]))
        if len(compressed) > allocation:
            raise ValueError(
                f"synthetic {name} is {len(compressed)} compressed bytes "
                f"and its allocation is {allocation}"
            )
        if offset == 0 and spec.compact_flag_c0:
            # A real disc is reported to flag section 0 with 0xC0 where every
            # other section uses 0x10. `decompress_section` rewrites the first
            # two bytes back to `10 FB` and passes the rest through, so this
            # round-trips exactly.
            compressed[0] = 0xC0
        blob[offset : offset + len(compressed)] = compressed
    result = bytes(blob)
    _BLOB_CACHE[key] = result
    return result


def decompress_section_at(blob: bytes, name: str) -> bytes:
    """One section's decompressed bytes, read straight out of a `database.big`.

    Used to read a *patched* blob back. It repeats the `0xC0` fixup rather than
    calling `decompress_section`, so a test is not reading through the method it
    is checking.
    """
    offset, _ = mvp_models.SECTION_ALLOCATIONS[name]
    raw = blob[offset:]
    if raw[0] == 0xC0:
        raw = b"\x10\xfb" + raw[2:]
    return refpack_decompress(raw)


# ──────────────────────────────────────────────────────────────
# Images on disk
# ──────────────────────────────────────────────────────────────


def _filler(length: int, seed: int) -> bytes:
    """`length` bytes of a repeating non-zero pattern, offset by `seed`.

    Two regions built with different seeds do not share a prefix, so a write
    that landed in the wrong one is visible.
    """
    start = seed % len(FILLER)
    repeats = length // len(FILLER) + 2
    return ((FILLER[start:] + FILLER[:start]) * repeats)[:length]


def build_iso(
    database_big: bytes | None = None,
    *,
    lba: int = SMALL_LBA,
    tail: int = TAIL_BYTES,
) -> bytes:
    """A whole image: filler, then `database.big` at `lba`, then more filler.

    The head is `lba * 2048` bytes and the tail is `tail` bytes, both filler, so
    a test can assert that patching changed exactly the extent and nothing else.
    """
    if database_big is None:
        database_big = build_database_big()
    head = _filler(lba * SECTOR_SIZE, seed=1)
    return head + database_big + _filler(tail, seed=97)


def read_database_big(image: bytes, *, lba: int = SMALL_LBA) -> bytes:
    """Slice `database.big` out of an image, with this file's own arithmetic.

    Independent of `MVPPSPRomReader.load`, which is what makes it usable to
    check what `load` read.
    """
    start = lba * SECTOR_SIZE
    return image[start : start + DATABASE_BIG_SIZE]


def use_small_layout(monkeypatch, lba: int = SMALL_LBA) -> None:
    """Move `database.big` to `lba` for the duration of one test.

    One `setattr`, on the one global `models.database_big_extent` reads at call
    time. The reader, the writer and the patcher all reach the extent through
    that function, so they cannot be patched into disagreeing -- which is the
    reason the extent is a function rather than a pair of imported constants.
    """
    monkeypatch.setattr(mvp_models, "DATABASE_BIG_LBA", lba)
