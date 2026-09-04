"""Fabricate EA TDB files and BIGF archives in memory.

Nothing here comes from a real disc. Every byte is generated, and the layouts
are the ones `formats/ea_tdb.py` documents.

Three things below are deliberately **independent reimplementations** of code in
the module under test, and that is the point of this file rather than an
oversight:

- `mpeg2_crc` is the bitwise form of CRC-32/MPEG-2. `ea_tdb.tdb_crc` is the
  nibble-table form. Two ways of computing the same function disagree the moment
  either one is wrong, where a test that fed `tdb_crc` its own output would
  agree with any polynomial at all.
- `pack_bits` writes LSB-first bit fields the long way, from a `values` mapping.
  `TDBTable._read_bits` is what reads them back, so a test can assert a named
  field equals the number this file put there — not merely that a round trip
  preserved whatever was in the buffer.
- `build_bigf` assembles an archive by hand instead of calling `ea_tdb`'s own
  `bigf_build`, so `bigf_parse` is checked against a directory it did not write.

`build_tdb` fills in the CRC chain itself, using `mpeg2_crc`. That is what makes
`serialize(parse(b)) == b` a real claim: the fixture's chain is computed by the
independent implementation, so a file built here is only byte-stable under
`serialize` if the module's chain arithmetic agrees with this one, link for
link, including the two links `serialize` never rewrites.

Record contents are self-identifying. Every record encodes both its table index
and its own index, in a string field and in two integer fields of different
widths, so a reader that returned the wrong table, the wrong record, or the
right record's fields in the wrong order cannot satisfy an assertion here.
"""

import struct
from dataclasses import dataclass, field

TDB_MAGIC = b"DB\x00\x08"

# Field type codes, restated rather than imported: importing them from the
# module under test would let a renumbering there silently renumber the fixture
# too, and every test would still pass.
TYPE_STRING = 0
TYPE_SINT = 2
TYPE_UINT = 3

TABLE_HEADER_SIZE = 40
FIELD_DEF_SIZE = 16
DIRECTORY_ENTRY_SIZE = 8
DIRECTORY_START = 24  # 20-byte file header plus the 4-byte directory hash
EOF_CRC_SIZE = 4


def mpeg2_crc(data: bytes) -> int:
    """CRC-32/MPEG-2, computed one bit at a time.

    Initial value 0xFFFFFFFF, polynomial 0x04C11DB7, not reflected in either
    direction, no final XOR. Deliberately not the table-driven form the module
    uses; see this file's docstring.
    """
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            if crc & 0x80000000:
                crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
            else:
                crc = (crc << 1) & 0xFFFFFFFF
    return crc


@dataclass(frozen=True)
class FieldSpec:
    """One field definition, as it is written into a table's field block."""

    name: str  # exactly four ASCII characters
    field_type: int
    bit_offset: int
    bit_width: int


@dataclass
class TableSpec:
    """One table: its layout, its counts and its record bytes.

    `records` is the whole allocation — `capacity * record_size` bytes — not
    just the live ones, because that is what the file holds and what the module
    reads back.
    """

    name: str  # exactly four ASCII characters
    fields: list[FieldSpec]
    record_size: int
    capacity: int
    num_records: int
    records: bytes
    header_unk: int = 0
    padding: int = 0
    marker: int = 0
    field_hash: int = 0

    def block_size(self) -> int:
        return (
            TABLE_HEADER_SIZE + FIELD_DEF_SIZE * len(self.fields) + self.capacity * self.record_size
        )


def pack_bits(fields: list[FieldSpec], values: dict[str, object], record_size: int) -> bytes:
    """Encode one record the long way, LSB-first, from named values.

    A string field is written ASCII, truncated to the field's byte width and
    NUL-padded to fill it. An integer field's bit `i` goes to bit `i % 8` of
    byte `(bit_offset + i) // 8`, counting from the low bit of each byte.

    A field absent from `values` is left as zero bits. A value too wide for its
    field raises rather than being clamped: the module clamps on write, and a
    fixture that clamped too could not tell the two apart.
    """
    buf = bytearray(record_size)
    for f in fields:
        if f.name not in values:
            continue
        value = values[f.name]
        if f.field_type == TYPE_STRING:
            if not isinstance(value, str):
                raise TypeError(f"{f.name} is a string field")
            byte_off = f.bit_offset // 8
            byte_len = f.bit_width // 8
            encoded = value.encode("ascii")[:byte_len]
            buf[byte_off : byte_off + byte_len] = encoded + b"\x00" * (byte_len - len(encoded))
            continue
        if not isinstance(value, int):
            raise TypeError(f"{f.name} is an integer field")
        if value < 0 or value > (1 << f.bit_width) - 1:
            raise ValueError(f"{value} does not fit {f.name}'s {f.bit_width} bits")
        for i in range(f.bit_width):
            if value & (1 << i):
                bit_pos = f.bit_offset + i
                buf[bit_pos // 8] |= 1 << (bit_pos % 8)
    return bytes(buf)


def build_table_block(spec: TableSpec, prior_crc: int) -> bytes:
    """One table's block: 40-byte header, field definitions, then records.

    `prior_crc` goes at offset 0 — it is the CRC of the *previous* table, which
    is the chain link. For the first table it is whatever the directory's CRC
    would be, and nothing recomputes it.
    """
    if len(spec.records) != spec.capacity * spec.record_size:
        raise ValueError(
            f"{spec.name}: {len(spec.records)} record bytes, "
            f"expected {spec.capacity * spec.record_size}"
        )
    out = bytearray()
    out += struct.pack("<I", prior_crc)
    out += struct.pack("<I", spec.header_unk)
    out += struct.pack("<I", spec.record_size)
    out += struct.pack("<I", spec.capacity)  # the 32-bit copy of maxRecords
    out += struct.pack("<I", spec.padding)
    out += struct.pack("<H", spec.capacity)  # the 16-bit one the module reads
    out += struct.pack("<H", spec.num_records)
    out += struct.pack("<I", spec.marker)
    out += struct.pack("<I", len(spec.fields))
    out += struct.pack("<I", 0)
    out += struct.pack("<I", spec.field_hash)
    if len(out) != TABLE_HEADER_SIZE:
        raise AssertionError(f"table header is {len(out)} bytes, not {TABLE_HEADER_SIZE}")
    for f in spec.fields:
        name = f.name.encode("ascii")
        if len(name) != 4:
            raise ValueError(f"field name {f.name!r} is not four characters")
        out += struct.pack("<I", f.field_type)
        out += struct.pack("<I", f.bit_offset)
        out += name
        out += struct.pack("<I", f.bit_width)
    out += spec.records
    return bytes(out)


def build_tdb(specs: list[TableSpec], *, directory_crc: int = 0x1234ABCD) -> bytes:
    """A whole TDB file, with a CRC chain this module computes for itself.

    `directory_crc` lands in the first table's header and is never recomputed by
    anything, which is exactly why it is a distinctive constant here: a
    `serialize` that overwrote it would change this byte and fail the round trip.

    With no tables at all the file is a header, a directory hash and four
    trailing bytes; `serialize` then writes the last table's CRC over those four
    bytes and there is no last table, so the chain loop does nothing and the
    file round-trips unchanged.
    """
    dir_end = DIRECTORY_START + DIRECTORY_ENTRY_SIZE * len(specs)

    # Two passes: the chain link stored in table i's header is the CRC over
    # table i-1's field definitions and records, so the blocks have to be laid
    # out before any header can be finished.
    blocks: list[bytes] = []
    prior = directory_crc
    for spec in specs:
        block = build_table_block(spec, prior)
        blocks.append(block)
        prior = mpeg2_crc(block[TABLE_HEADER_SIZE:])

    out = bytearray()
    out += TDB_MAGIC
    out += struct.pack("<I", 0)
    out += struct.pack("<I", 0)  # data size: written by nobody, read by nobody
    out += struct.pack("<I", 0)
    out += struct.pack("<I", len(specs))
    out += struct.pack("<I", 0xDEADBEEF)  # the directory hash, likewise inert

    rel = 0
    for spec, block in zip(specs, blocks, strict=True):
        name = spec.name.encode("ascii")
        if len(name) != 4:
            raise ValueError(f"table name {spec.name!r} is not four characters")
        out += name
        out += struct.pack("<I", rel)
        rel += len(block)

    if len(out) != dir_end:
        raise AssertionError(f"directory ends at {len(out)}, not {dir_end}")

    for block in blocks:
        out += block

    # The trailing CRC slot: the last table's CRC, or zero when there is none.
    out += struct.pack("<I", prior if specs else 0)
    return bytes(out)


# ──────────────────────────────────────────────────────────────
# Ready-made tables
# ──────────────────────────────────────────────────────────────

# A player-bio table shaped like the SPBT the two NHL patchers write: two string
# fields and three integers, one of them not byte-aligned and one of them
# straddling a byte boundary. 16 bytes a record.
#
#   bits   0.. 95  FNME  12 ASCII bytes
#   bits  96..111  INDX  16-bit, byte-aligned
#   bits 112..118  SACC   7-bit, byte-aligned, not byte-wide
#   bits 119..122  TEAM   4-bit, straddles bytes 14 and 15
#   bits 123..127  WGHT   5-bit, runs to the last bit of the record
PLAYER_FIELDS = [
    FieldSpec("FNME", TYPE_STRING, 0, 96),
    FieldSpec("INDX", TYPE_UINT, 96, 16),
    FieldSpec("SACC", TYPE_UINT, 112, 7),
    FieldSpec("TEAM", TYPE_UINT, 119, 4),
    FieldSpec("WGHT", TYPE_UINT, 123, 5),
]
PLAYER_RECORD_SIZE = 16


def player_values(table_index: int, record_index: int) -> dict[str, object]:
    """The values `player_table` writes into one record.

    Every one of the five encodes both coordinates, so a reader that swapped two
    records, two tables or two fields lands on a different number. The integer
    fields deliberately do not share a value: `INDX` is wide enough to hold the
    pair outright, while `SACC`, `TEAM` and `WGHT` are narrow and take different
    residues, so a read that used the wrong bit width still differs.
    """
    return {
        "FNME": f"T{table_index}R{record_index}",
        "INDX": (table_index << 8) | (record_index & 0xFF),
        "SACC": (table_index * 13 + record_index * 5) % 128,
        "TEAM": (table_index * 3 + record_index) % 16,
        "WGHT": (table_index + record_index * 7) % 32,
    }


def player_table(
    name: str,
    table_index: int,
    capacity: int,
    num_records: int | None = None,
) -> TableSpec:
    """A `PLAYER_FIELDS`-shaped table with `capacity` self-identifying records.

    Records past `num_records` are filled in too, so a reader that ignored
    `currentRecords` and walked the whole allocation would still find plausible
    data — which is what makes `find_records`' bound testable.
    """
    if num_records is None:
        num_records = capacity
    records = b"".join(
        pack_bits(PLAYER_FIELDS, player_values(table_index, i), PLAYER_RECORD_SIZE)
        for i in range(capacity)
    )
    return TableSpec(
        name=name,
        fields=list(PLAYER_FIELDS),
        record_size=PLAYER_RECORD_SIZE,
        capacity=capacity,
        num_records=num_records,
        records=records,
        header_unk=0x11110000 | table_index,
        padding=0x22220000 | table_index,
        marker=0x33330000 | table_index,
        field_hash=0x44440000 | table_index,
    )


def empty_table(name: str, table_index: int) -> TableSpec:
    """A table with fields but no allocation at all.

    `capacity` zero makes the record region empty, which is the degenerate case
    for every offset `serialize` computes: the CRC window is the field
    definitions alone.
    """
    return player_table(name, table_index, capacity=0)


# ──────────────────────────────────────────────────────────────
# BIGF
# ──────────────────────────────────────────────────────────────


@dataclass
class BigfSpec:
    """How to lay a BIGF out, including the parts that are free to be wrong.

    `total_size_endianness` and `stated_header_size` exist because `bigf_parse`
    reads neither field, and a test that never varies them cannot show it.
    """

    files: list[tuple[str, bytes]] = field(default_factory=list)
    align: int = 128
    total_size_endianness: str = "<"
    stated_header_size: int | None = None
    stated_num_files: int | None = None


def build_bigf(spec: BigfSpec) -> bytes:
    """Assemble a BIGF archive by hand.

    Deliberately not a call to `ea_tdb.bigf_build`: `bigf_parse` has to be
    checked against a directory something else wrote, or the two functions agree
    with each other while both being wrong about the format.
    """
    header_size = 16
    for name, _ in spec.files:
        header_size += 8 + len(name) + 1

    out = bytearray()
    out += b"BIGF"
    out += b"\x00" * 12

    entry_positions = []
    for name, data in spec.files:
        entry_positions.append(len(out))
        out += b"\x00\x00\x00\x00"
        out += struct.pack(">I", len(data))
        out += name.encode("ascii")
        out += b"\x00"

    if spec.align:
        out += b"\x00" * ((spec.align - len(out) % spec.align) % spec.align)

    for i, (_, data) in enumerate(spec.files):
        struct.pack_into(">I", out, entry_positions[i], len(out))
        out += data
        if spec.align and i < len(spec.files) - 1:
            out += b"\x00" * ((spec.align - len(out) % spec.align) % spec.align)

    struct.pack_into(spec.total_size_endianness + "I", out, 4, len(out))
    num_files = spec.stated_num_files
    if num_files is None:
        num_files = len(spec.files)
    struct.pack_into(">I", out, 8, num_files)
    stated = spec.stated_header_size
    struct.pack_into(">I", out, 12, header_size if stated is None else stated)
    return bytes(out)
