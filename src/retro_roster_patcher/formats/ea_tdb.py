"""EA's RefPack / BIGF / TDB stack, as used by NHL 05, NHL 07 and MVP Baseball.

Three layers, each read-write, stacked in that order on a disc:

    ISO -> db.viv (BIGF archive) -> *.tdb (RefPack-compressed) -> tables

- **RefPack** (also called QFS) is EA's LZ77 variant. MVP Baseball uses only
  this layer: its `database.big` is 18 concatenated RefPack streams of CSV,
  with no BIGF and no TDB above them.
- **BIGF** is EA's archive container: a header, a table of (offset, size, name)
  entries, then the file data.
- **TDB** is a record database with bit-packed, LSB-first integer fields and
  byte-aligned fixed-width strings, addressed by a four-character field name
  rather than by byte offset. It carries a **CRC chain**: each table's CRC is
  stored in the *next* table's header, and the last table's lands in the file's
  final four bytes.

References:
  - RefPack/QFS: https://simswiki.info/wiki.php?title=DBPF_Compression
  - TDB table and record headers follow madden-file-tools' layout.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping
from dataclasses import dataclass, field

from ..core.errors import RomError


class EaTdbError(RomError):
    """Raised when data handed to this module is not the format it claims."""


def refpack_decompress(data: bytes) -> bytes:
    """Decompress a RefPack/QFS stream.

    Header: `0x10 0xFB` then a 3-byte big-endian decompressed size. The body is
    a sequence of commands, each emitting 0-3 literal bytes from the input then
    0 or more bytes copied from what has already been emitted; the command's
    first byte selects which of five encodings it is, by range.

    Raises:
        EaTdbError: The first two bytes are not `0x10 0xFB`, or there are fewer
            than five bytes in total.
    """
    if len(data) < 5 or data[0] != 0x10 or data[1] != 0xFB:
        raise EaTdbError("Not RefPack data (missing 0x10 0xFB header)")

    decompressed_size = (data[2] << 16) | (data[3] << 8) | data[4]
    out = bytearray()
    pos = 5

    while pos < len(data):
        b0 = data[pos]

        if b0 < 0x80:
            # 2-byte command: 0-3 literals, copy 3-10 bytes from within 1024.
            if pos + 1 >= len(data):
                break
            b1 = data[pos + 1]
            pos += 2
            num_literal = b0 & 0x03
            num_copy = ((b0 & 0x1C) >> 2) + 3
            copy_offset = ((b0 & 0x60) << 3) + b1 + 1

        elif b0 < 0xC0:
            # 3-byte command: 0-3 literals, copy 4-67 bytes from within 16384.
            if pos + 2 >= len(data):
                break
            b1 = data[pos + 1]
            b2 = data[pos + 2]
            pos += 3
            num_literal = ((b1 & 0xC0) >> 6) & 0x03
            num_copy = (b0 & 0x3F) + 4
            copy_offset = ((b1 & 0x3F) << 8) + b2 + 1

        elif b0 < 0xE0:
            # 4-byte command: 0-3 literals, copy 5-1028 bytes from within 131072.
            if pos + 3 >= len(data):
                break
            b1 = data[pos + 1]
            b2 = data[pos + 2]
            b3 = data[pos + 3]
            pos += 4
            num_literal = b0 & 0x03
            num_copy = ((b0 & 0x0C) << 6) + b3 + 5
            copy_offset = ((b0 & 0x10) << 12) + (b1 << 8) + b2 + 1

        elif b0 < 0xFC:
            # Literal-only command: 4-128 literals, always a multiple of four.
            num_literal = ((b0 & 0x1F) << 2) + 4
            num_copy = 0
            copy_offset = 0
            pos += 1

        else:
            # End marker (0xFC-0xFF), carrying its 0-3 trailing literals.
            num_literal = b0 & 0x03
            num_copy = 0
            copy_offset = 0
            pos += 1

        if num_literal > 0:
            if pos + num_literal > len(data):
                num_literal = len(data) - pos
            out.extend(data[pos : pos + num_literal])
            pos += num_literal

        if num_copy > 0:
            # Copy byte at a time, never by slice: a match may overlap its own
            # output.
            src = len(out) - copy_offset
            for _ in range(num_copy):
                if src >= 0 and src < len(out):
                    out.append(out[src])
                else:
                    out.append(0)
                src += 1

        if b0 >= 0xFC:
            break

    # Truncate only, never pad: a stream that ran out of input early returns
    # short without complaint.
    if len(out) > decompressed_size:
        out = out[:decompressed_size]

    return bytes(out)


def _is_encodable(length: int, offset: int) -> bool:
    """Whether a match of this length and distance fits any copy command.

    Keep the three clauses identical to `_emit_copy`'s: anything accepted here
    must be emittable there.
    """
    if length <= 10 and offset <= 1024:
        return True
    if 4 <= length <= 67 and offset <= 16384:
        return True
    if 5 <= length <= 1028 and offset <= 131072:
        return True
    return False


def _emit_copy(out: bytearray, nl: int, lit_bytes: bytes, length: int, offset: int) -> None:
    """Append a copy command carrying `nl` (0-3) attached literals.

    The three branches invert `refpack_decompress`'s first three encodings.
    """
    if length <= 10 and offset <= 1024:
        # 2-byte: length 3-10, offset 1-1024.
        b0 = (nl & 0x03) | (((length - 3) & 0x07) << 2) | (((offset - 1) >> 3) & 0x60)
        b1 = (offset - 1) & 0xFF
        out.extend([b0, b1])
    elif 4 <= length <= 67 and offset <= 16384:
        # 3-byte: length 4-67, offset 1-16384.
        b0 = 0x80 | ((length - 4) & 0x3F)
        b1 = ((nl & 0x03) << 6) | (((offset - 1) >> 8) & 0x3F)
        b2 = (offset - 1) & 0xFF
        out.extend([b0, b1, b2])
    else:
        # 4-byte: length 5-1028, offset 1-131072.
        b0 = 0xC0 | (nl & 0x03) | (((length - 5) >> 6) & 0x0C) | (((offset - 1) >> 12) & 0x10)
        b1 = ((offset - 1) >> 8) & 0xFF
        b2 = (offset - 1) & 0xFF
        b3 = (length - 5) & 0xFF
        out.extend([b0, b1, b2, b3])

    out.extend(lit_bytes)


def refpack_compress(data: bytes) -> bytes:
    """Compress with RefPack/QFS: hash-chain LZ77 with lazy match evaluation."""
    size = len(data)
    out = bytearray()

    # Header: 0x10 0xFB then the decompressed size, 3 bytes big-endian. Sizes of
    # 16 MiB and over wrap silently.
    out.extend(b"\x10\xfb")
    out.append((size >> 16) & 0xFF)
    out.append((size >> 8) & 0xFF)
    out.append(size & 0xFF)

    if size == 0:
        out.append(0xFC)
        return bytes(out)

    hash_bits = 16
    hash_mask = (1 << hash_bits) - 1
    max_chain = 128
    max_offset = 131072
    max_match = 1028

    head = [-1] * (hash_mask + 1)
    chain = [-1] * size
    inserted = bytearray(size)

    def calc_hash(p: int) -> int:
        return ((data[p] << 8) ^ (data[p + 1] << 4) ^ data[p + 2]) & hash_mask

    def insert(p: int) -> None:
        if p + 2 >= size or inserted[p]:
            return
        inserted[p] = 1
        h = calc_hash(p)
        chain[p] = head[h]
        head[h] = p

    def find_match(p: int) -> tuple[int, int]:
        """Longest match for the three bytes at `p`, as (offset, length).

        Returns (0, 0) when there is nothing at least three bytes long.
        """
        if p + 2 >= size:
            return 0, 0
        h = calc_hash(p)
        cand = head[h]
        best_len = 2
        best_off = 0
        depth = 0
        d0, d1, d2 = data[p], data[p + 1], data[p + 2]

        while cand >= 0 and depth < max_chain:
            off = p - cand
            if off > max_offset:
                break
            if off >= 1 and data[cand] == d0 and data[cand + 1] == d1 and data[cand + 2] == d2:
                ml = 3
                limit = min(max_match, size - p, size - cand)
                while ml < limit and data[cand + ml] == data[p + ml]:
                    ml += 1
                if ml > best_len:
                    best_len = ml
                    best_off = off
                    if ml >= max_match:
                        break
            cand = chain[cand]
            depth += 1

        if best_len < 3:
            return 0, 0
        return best_off, best_len

    def flush_literals(lit_start: int, end: int) -> int:
        """Emit literal-only commands until at most 3 literals are left pending.

        Those last 0-3 ride along on the next copy command, or on the end
        marker. Chunks are a multiple of four: the literal-only command encodes
        `(count - 4) / 4` in five bits, so 4 to 128 in steps of four.
        """
        while end - lit_start > 3:
            chunk = min(end - lit_start, 112)
            chunk = (chunk // 4) * 4
            if chunk < 4:
                break
            out.append(0xE0 + ((chunk - 4) >> 2))
            out.extend(data[lit_start : lit_start + chunk])
            lit_start += chunk
        return lit_start

    pos = 0
    lit_start = 0

    while pos < size:
        offset, length = find_match(pos)

        if length < 3 or not _is_encodable(length, offset):
            insert(pos)
            pos += 1
            continue

        if length < max_match and pos + 1 < size - 2:
            insert(pos)
            next_offset, next_length = find_match(pos + 1)
            if next_length > length + 1 and _is_encodable(next_length, next_offset):
                pos += 1
                continue

        lit_start = flush_literals(lit_start, pos)

        nl = pos - lit_start
        lit_bytes = data[lit_start:pos]
        _emit_copy(out, nl, lit_bytes, length, offset)

        # Link the matched span so later positions can reference into it.
        for i in range(pos, min(pos + length, size - 2)):
            insert(i)
        pos += length
        lit_start = pos

    lit_start = flush_literals(lit_start, size)

    trail = size - lit_start
    out.append(0xFC + trail)
    if trail > 0:
        out.extend(data[lit_start:size])

    return bytes(out)


@dataclass
class BigfEntry:
    """One file's directory entry in a BIGF archive."""

    name: str
    offset: int
    size: int


def bigf_parse(archive: bytes) -> list[BigfEntry]:
    """Read a BIGF archive's directory.

    Layout: `BIGF`, then a 4-byte total size, a 4-byte big-endian file count and
    a 4-byte big-endian header size; then one entry per file, each a 4-byte
    big-endian offset, a 4-byte big-endian size and a NUL-terminated name.

    The total size and header size are deliberately not read: neither is
    reliable across the discs this handles. A truncated directory ends the scan
    early rather than raising.

    Raises:
        EaTdbError: Fewer than 16 bytes, or the magic is not `BIGF`.
    """
    if len(archive) < 16 or archive[:4] != b"BIGF":
        raise EaTdbError("Not a BIGF archive")

    num_files = struct.unpack_from(">I", archive, 8)[0]

    entries: list[BigfEntry] = []
    pos = 16
    for _ in range(num_files):
        if pos + 8 > len(archive):
            break
        file_offset = struct.unpack_from(">I", archive, pos)[0]
        file_size = struct.unpack_from(">I", archive, pos + 4)[0]
        pos += 8
        name_start = pos
        while pos < len(archive) and archive[pos] != 0:
            pos += 1
        name = archive[name_start:pos].decode("ascii", errors="replace")
        pos += 1
        entries.append(BigfEntry(name=name, offset=file_offset, size=file_size))

    return entries


def bigf_extract(archive: bytes, filename: str) -> bytes | None:
    """Return one file's bytes, matched case-insensitively, or None.

    Case-insensitive: the same archive is spelled `db.viv` on one disc and
    `DB.VIV` on another.

    Raises:
        EaTdbError: `archive` is not a BIGF.
    """
    entries = bigf_parse(archive)
    filename_lower = filename.lower()
    for entry in entries:
        if entry.name.lower() == filename_lower:
            return archive[entry.offset : entry.offset + entry.size]
    return None


def bigf_replace(archive: bytes, filename: str, new_data: bytes) -> bytes:
    """Rebuild the archive with one file's contents replaced.

    Offsets move: the result is a fresh `bigf_build`, so a replacement of a
    different length shifts everything after it. Use `bigf_replace_inplace`
    when the archive's position inside a disc image must not change.

    Raises:
        EaTdbError: `archive` is not a BIGF, or holds no file called `filename`.
    """
    entries = bigf_parse(archive)
    filename_lower = filename.lower()
    file_contents: dict[str, bytes] = {}
    for entry in entries:
        if entry.name.lower() == filename_lower:
            file_contents[entry.name] = new_data
        else:
            file_contents[entry.name] = archive[entry.offset : entry.offset + entry.size]

    # Case-SENSITIVE, unlike the loop above: pass the archive's own spelling.
    if filename not in file_contents:
        raise EaTdbError(f"File '{filename}' not found in BIGF archive")

    return bigf_build(entries, file_contents)


def bigf_replace_inplace(archive: bytearray, filename: str, new_data: bytes) -> bool:
    """Overwrite one file's bytes where they already sit, zero-padding the rest.

    Every other file keeps its offset, which is what lets the caller write the
    archive back into a disc image at its original LBA. The directory entry is
    left alone, size included: the game reads the full original allocation and
    RefPack stops at its own end marker, so the padding is never looked at.

    Returns:
        True on success. False if there is no such file, or if `new_data` is
        larger than the space the original occupied. Check this return, or an
        over-large write is silently skipped.

    Raises:
        EaTdbError: `archive` is not a BIGF.
    """
    entries = bigf_parse(bytes(archive))
    filename_lower = filename.lower()

    target_entry = None
    for entry in entries:
        if entry.name.lower() == filename_lower:
            target_entry = entry

    if target_entry is None:
        return False

    if len(new_data) > target_entry.size:
        return False

    archive[target_entry.offset : target_entry.offset + len(new_data)] = new_data
    remaining = target_entry.size - len(new_data)
    if remaining > 0:
        start = target_entry.offset + len(new_data)
        archive[start : target_entry.offset + target_entry.size] = b"\x00" * remaining

    return True


def bigf_build(entries: list[BigfEntry], file_contents: dict[str, bytes]) -> bytes:
    """Assemble a BIGF from a directory and a name-to-bytes mapping.

    `entries` supplies the names and their order; the offsets and sizes on them
    are ignored and recomputed. A name absent from `file_contents` becomes an
    empty file rather than an error.

    Files start on 128-byte boundaries, matching EA's own archives, and the last
    file is not padded.
    """
    num_files = len(entries)

    header_size = 16
    for entry in entries:
        header_size += 8 + len(entry.name) + 1

    out = bytearray()
    out.extend(b"BIGF")
    out.extend(b"\x00" * 12)

    entry_positions = []
    for entry in entries:
        entry_positions.append(len(out))
        out.extend(b"\x00\x00\x00\x00")  # offset, patched in below
        data = file_contents.get(entry.name, b"")
        out.extend(struct.pack(">I", len(data)))
        out.extend(entry.name.encode("ascii"))
        out.append(0)

    pad_to_128 = (128 - (len(out) % 128)) % 128
    out.extend(b"\x00" * pad_to_128)

    for i, entry in enumerate(entries):
        data = file_contents.get(entry.name, b"")
        file_offset = len(out)
        out.extend(data)
        struct.pack_into(">I", out, entry_positions[i], file_offset)
        if i < num_files - 1:
            pad = (128 - (len(out) % 128)) % 128
            out.extend(b"\x00" * pad)

    # Total size is little-endian, the other two big-endian. Not a typo.
    total_size = len(out)
    struct.pack_into("<I", out, 4, total_size)
    struct.pack_into(">I", out, 8, num_files)
    struct.pack_into(">I", out, 12, header_size)

    return bytes(out)


TDB_TYPE_STRING = 0
TDB_TYPE_BINARY = 1
TDB_TYPE_SINT = 2
TDB_TYPE_UINT = 3
TDB_TYPE_FLOAT = 4

TDB_MAGIC = b"DB\x00\x08"


def _build_crc_table() -> list[int]:
    """The 16-entry nibble table for CRC-32/MPEG-2 (polynomial 0x04C11DB7).

    Sixteen entries, not 256: `tdb_crc` consumes four bits at a time.
    """
    poly = 0x04C11DB7
    table = [0] * 16
    crc = 0x80000000
    i = 1
    while i < 16:
        crc = ((crc << 1) ^ (poly if (crc & 0x80000000) else 0)) & 0xFFFFFFFF
        for j in range(i):
            table[i + j] = crc ^ table[j]
        i <<= 1
    return table


_CRC_TABLE = _build_crc_table()


def tdb_crc(data: bytes) -> int:
    """EA's TDB checksum: CRC-32/MPEG-2's raw accumulator, with no final XOR.

    Never `zlib.crc32`; that is reflected and inverted, and disagrees.
    """
    crc = 0xFFFFFFFF
    for byte in data:
        crc = (crc ^ (byte << 24)) & 0xFFFFFFFF
        crc = ((crc << 4) & 0xFFFFFFFF) ^ _CRC_TABLE[crc >> 28]
        crc = ((crc << 4) & 0xFFFFFFFF) ^ _CRC_TABLE[crc >> 28]
    return crc


@dataclass
class TDBField:
    """One field definition: where it sits in a record and how wide it is.

    `bit_offset` and `bit_width` are in bits for every type. For a string both
    are whole multiples of eight and the field is byte-aligned; for an integer
    neither has to be.
    """

    name: str
    field_type: int
    bit_offset: int
    bit_width: int
    name_hash: int = 0

    @property
    def is_string(self) -> bool:
        return self.field_type == TDB_TYPE_STRING

    @property
    def is_int(self) -> bool:
        return self.field_type in (TDB_TYPE_SINT, TDB_TYPE_UINT)


@dataclass
class TDBTable:
    """One table: a field layout and a flat array of bit-packed records.

    `capacity` is the allocation (`maxRecords` in the file) and `num_records` is
    how many of them the game treats as live (`currentRecords`). Reads and
    writes are bounded by `capacity`; the two search helpers only look at
    `num_records`. Both are stored 16-bit little-endian at offsets 20 and 22 of
    the table's 40-byte header.

    `num_records > capacity` is not rejected, so bound any
    `range(table.num_records)` loop in the caller or it can raise `IndexError`.
    """

    name: str
    name_hash: int
    fields: list[TDBField] = field(default_factory=list)
    record_size: int = 0  # bytes per record
    capacity: int = 0  # maxRecords
    num_records: int = 0  # currentRecords
    data_offset: int = 0  # record start, relative to the whole-file buffer
    _raw_data: bytearray = field(default_factory=bytearray)
    _header_crc: int = 0
    _header_unk: int = 0
    _padding: int = 0

    def __post_init__(self) -> None:
        # Must be a `bytearray` from the start: `write_record` mutates in place.
        if not isinstance(self._raw_data, bytearray):
            self._raw_data = bytearray(self._raw_data)

    def allocate_record(self) -> int:
        """Mark one more record live, returning its index, or -1 if full."""
        if self.num_records >= self.capacity:
            return -1
        idx = self.num_records
        self.num_records += 1
        return idx

    def read_record(self, index: int) -> dict[str, object]:
        """Every field of one record, as a name-to-value mapping.

        Strings come back `str`, truncated at the first NUL and decoded as ASCII
        with unmappable bytes replaced; everything else comes back `int`, read
        LSB-first. A field type this module does not know reads as an integer,
        which is what `TDB_TYPE_BINARY` and `TDB_TYPE_FLOAT` therefore do.

        Raises:
            IndexError: `index` is outside `0 .. capacity - 1`. Keep it a
                builtin: it is a caller bug, not a claim about the user's disc.
        """
        if index < 0 or index >= self.capacity:
            raise IndexError(f"Record {index} out of range (0-{self.capacity - 1})")

        rec_start = index * self.record_size
        rec_data = self._raw_data[rec_start : rec_start + self.record_size]

        result: dict[str, object] = {}
        for f in self.fields:
            if f.is_string:
                byte_off = f.bit_offset // 8
                byte_len = f.bit_width // 8
                raw = rec_data[byte_off : byte_off + byte_len]
                null_idx = raw.find(b"\x00")
                if null_idx >= 0:
                    raw = raw[:null_idx]
                result[f.name] = raw.decode("ascii", errors="replace")
            else:
                result[f.name] = self._read_bits(rec_data, f.bit_offset, f.bit_width)
        return result

    def write_record(self, index: int, values: Mapping[str, object]) -> None:
        """Update the named fields of one record, leaving the others alone.

        A key naming no field of this table is ignored, not reported: callers
        hand the same value dictionary to tables with different layouts.

        Strings are encoded ASCII, truncated to the field width and NUL-padded
        to fill it. Integers are clamped into the field's width rather than
        wrapped or rejected, so an out-of-range rating saturates.

        Raises:
            IndexError: `index` is outside `0 .. capacity - 1`.
            TypeError: A value for an integer field is not something `int()`
                accepts.
        """
        if index < 0 or index >= self.capacity:
            raise IndexError(f"Record {index} out of range (capacity={self.capacity})")

        rec_start = index * self.record_size

        for f in self.fields:
            if f.name not in values:
                continue
            val = values[f.name]

            if f.is_string:
                byte_off = f.bit_offset // 8
                byte_len = f.bit_width // 8
                if isinstance(val, str):
                    encoded = val.encode("ascii", errors="replace")
                else:
                    encoded = bytes(val)  # type: ignore[call-overload]
                padded = encoded[:byte_len]
                padded = padded + b"\x00" * (byte_len - len(padded))
                for i, b in enumerate(padded):
                    self._raw_data[rec_start + byte_off + i] = b
            else:
                if not isinstance(val, int | float | str | bytes):
                    raise TypeError(f"Field {f.name!r} takes an integer, not {type(val).__name__}")
                self._write_bits(rec_start, f.bit_offset, f.bit_width, int(val))

    def _read_bits(self, rec_data: bytes | bytearray, bit_offset: int, bit_width: int) -> int:
        """Read `bit_width` bits as an unsigned integer, LSB first.

        Bit `i` of the value comes from bit `i % 8` of byte `(bit_offset + i)
        // 8`, counting from the low bit of each byte. Bits past the end of
        `rec_data` read as zero rather than raising.
        """
        value = 0
        for i in range(bit_width):
            bit_pos = bit_offset + i
            byte_idx = bit_pos // 8
            bit_idx = bit_pos % 8
            if byte_idx < len(rec_data):
                if rec_data[byte_idx] & (1 << bit_idx):
                    value |= 1 << i
        return value

    def _write_bits(self, rec_start: int, bit_offset: int, bit_width: int, value: int) -> None:
        """Write an unsigned integer into `bit_width` bits, LSB first.

        Clamp to `0 .. 2**bit_width - 1` before touching a bit, or an oversized
        value corrupts the neighbouring field. Bits past the end of the buffer
        are dropped, mirroring `_read_bits`.
        """
        max_val = (1 << bit_width) - 1
        value = max(0, min(max_val, value))

        for i in range(bit_width):
            bit_pos = bit_offset + i
            byte_idx = rec_start + bit_pos // 8
            bit_idx = bit_pos % 8
            if byte_idx < len(self._raw_data):
                if value & (1 << i):
                    self._raw_data[byte_idx] |= 1 << bit_idx
                else:
                    self._raw_data[byte_idx] &= ~(1 << bit_idx)

    def find_record(self, field_name: str, value: object) -> int:
        """Index of the first live record whose field equals `value`, or -1.

        Search only `num_records`, never the whole allocation: a slot past the
        live count holds whatever the last roster left there.
        """
        for i in range(self.num_records):
            rec = self.read_record(i)
            if rec.get(field_name) == value:
                return i
        return -1

    def find_records(self, field_name: str, value: object) -> list[int]:
        """Indices of every live record whose field equals `value`.

        Bounded by `num_records`, as `find_record` is.
        """
        results = []
        for i in range(self.num_records):
            rec = self.read_record(i)
            if rec.get(field_name) == value:
                results.append(i)
        return results


class TDBFile:
    """A parsed TDB, held as the original bytes plus per-table record buffers.

    `parse` keeps the whole file and `serialize` writes the record buffers back
    over it in place, so every byte this module does not understand — several
    header words included — survives untouched.

    File layout::

        0   4B  magic, `DB\\x00\\x08`
        4   4B  zero
        8   4B  data size, little-endian
        12  4B  zero
        16  4B  table count, little-endian
        20  4B  directory hash
        24  8B per table: 4-byte ASCII name, 4-byte little-endian offset
        ..      table blocks, in directory order
        -4  4B  the last table's CRC

    Table offsets are relative to the end of the directory, not to the file.
    """

    def __init__(self) -> None:
        self.tables: dict[str, TDBTable] = {}
        self._raw: bytearray = bytearray()
        self._table_order: list[str] = []

    @classmethod
    def parse(cls, data: bytes) -> TDBFile:
        """Read a whole TDB.

        A directory entry pointing past the end of the file, or at a block too
        short to hold a table header, is skipped rather than raising, and the
        remaining tables still parse.

        Two tables sharing a name is not rejected either, and makes `serialize`
        compute one link of the CRC chain wrongly. No EA file is known to do it.

        Raises:
            EaTdbError: Fewer than 20 bytes, or the magic is not `DB\\x00\\x08`.
        """
        tdb = cls()
        tdb._raw = bytearray(data)

        if len(data) < 20 or data[:4] != TDB_MAGIC:
            raise EaTdbError(f"Not a TDB file (magic: {data[:4]!r})")

        num_tables = struct.unpack_from("<I", data, 16)[0]

        dir_start = 24  # 20-byte header plus the 4-byte directory hash
        dir_end = dir_start + num_tables * 8

        table_entries = []
        pos = dir_start
        for _ in range(num_tables):
            if pos + 8 > len(data):
                break
            t_name_raw = data[pos : pos + 4]
            t_rel_offset = struct.unpack_from("<I", data, pos + 4)[0]
            t_name = t_name_raw.decode("ascii", errors="replace").strip("\x00")
            t_abs_offset = dir_end + t_rel_offset
            table_entries.append((t_name, t_abs_offset))
            pos += 8

        for t_name, t_offset in table_entries:
            table = cls._parse_table(data, t_offset, t_name)
            if table is not None:
                tdb.tables[t_name] = table
                tdb._table_order.append(t_name)

        return tdb

    @classmethod
    def _parse_table(cls, data: bytes, offset: int, name: str) -> TDBTable | None:
        """One table block, or None if the block is truncated.

        The block is a 40-byte header, then one 16-byte definition per field,
        then `capacity * record_size` bytes of records::

            +0   4B  the PREVIOUS table's CRC (the chain link)
            +4   4B  unknown, preserved
            +8   4B  record size in bytes
            +12  4B  maxRecords, again
            +16  4B  padding, preserved
            +20  2B  maxRecords
            +22  2B  currentRecords
            +24  4B  marker
            +28  4B  field count
            +32  4B  padding
            +36  4B  field-name hash

        `maxRecords` appears twice, at +12 as four bytes and at +20 as two. Read
        and write the 16-bit one at +20; the 32-bit one is preserved untouched,
        so a table of more than 65535 records would round-trip inconsistently.
        """
        if offset + 20 > len(data):
            return None

        header_crc = struct.unpack_from("<I", data, offset)[0]
        header_unk = struct.unpack_from("<I", data, offset + 4)[0]
        rec_size = struct.unpack_from("<I", data, offset + 8)[0]
        padding = struct.unpack_from("<I", data, offset + 16)[0]

        pos = offset + 20

        if pos + 16 > len(data):
            return None
        max_records = struct.unpack_from("<H", data, pos)[0]
        current_records = struct.unpack_from("<H", data, pos + 2)[0]
        # +4 marker and +12 padding are stepped over, not read; `serialize`
        # preserves them by never writing over them.
        num_fields = struct.unpack_from("<I", data, pos + 8)[0]
        pos += 16

        if pos + 4 > len(data):
            return None
        pos += 4  # field-name hash, likewise preserved rather than read

        fields = []
        for _ in range(num_fields):
            if pos + 16 > len(data):
                break
            f_type = struct.unpack_from("<I", data, pos)[0]
            f_bit_offset = struct.unpack_from("<I", data, pos + 4)[0]
            f_name_raw = data[pos + 8 : pos + 12]
            f_bit_width = struct.unpack_from("<I", data, pos + 12)[0]
            f_name = f_name_raw.decode("ascii", errors="replace").strip("\x00")
            fields.append(
                TDBField(
                    name=f_name,
                    field_type=f_type,
                    bit_offset=f_bit_offset,
                    bit_width=f_bit_width,
                    name_hash=0,
                )
            )
            pos += 16

        # The full allocation, not just the live records.
        data_offset = pos
        raw_data = data[data_offset : data_offset + max_records * rec_size]

        return TDBTable(
            name=name,
            name_hash=0,
            fields=fields,
            record_size=rec_size,
            capacity=max_records,
            num_records=current_records,
            data_offset=data_offset,
            _raw_data=bytearray(raw_data),
            _header_crc=header_crc,
            _header_unk=header_unk,
            _padding=padding,
        )

    def get_table(self, name: str) -> TDBTable | None:
        """The table with this exact four-character name, or None.

        Case-sensitive, unlike `bigf_extract`.
        """
        return self.tables.get(name)

    def serialize(self) -> bytes:
        """Write the tables back over the original bytes and refresh the CRCs.

        Three things change and nothing else does: each table's record data at
        its original offset, the two 16-bit counts in its header, and the CRC
        chain.

        The chain is what makes the file valid to the game::

            table[0]'s stored CRC is the directory's, and is left alone
            table[i + 1]'s stored CRC = crc(table[i]'s fields and records)
            the file's last 4 bytes = crc(the last table's fields and records)

        So each table's CRC lives in the *next* table's header. With a single
        table the chain degenerates to just the trailing four bytes, and nothing
        is written into any header.

        A table whose record buffer is shorter than `capacity * record_size`
        makes the slice assignment below **shrink** the output, moving every
        later offset. Reject the truncation upstream, not here.
        """
        out = bytearray(self._raw)

        for table in self.tables.values():
            rec_end = table.data_offset + table.capacity * table.record_size
            if rec_end <= len(out):
                out[table.data_offset : rec_end] = table._raw_data[
                    : table.capacity * table.record_size
                ]
            header_offset = self._header_offset(table)
            if header_offset >= 0:
                struct.pack_into("<H", out, header_offset + 20, table.capacity)
                struct.pack_into("<H", out, header_offset + 22, table.num_records)

        ordered = [self.tables[n] for n in self._table_order if n in self.tables]
        for i, table in enumerate(ordered):
            # Everything after the 40-byte header. Excluding the header is
            # required: it holds the previous link's CRC.
            crc_start = self._header_offset(table) + 40
            crc_end = table.data_offset + table.capacity * table.record_size
            crc = tdb_crc(bytes(out[crc_start:crc_end]))

            if i + 1 < len(ordered):
                next_t = ordered[i + 1]
                struct.pack_into("<I", out, self._header_offset(next_t), crc)
            else:
                struct.pack_into("<I", out, len(out) - 4, crc)

        return bytes(out)

    @staticmethod
    def _header_offset(table: TDBTable) -> int:
        """Where a table's 40-byte header starts, worked back from its records.

        Sixteen bytes per field definition, plus the 40-byte header itself.
        """
        return table.data_offset - len(table.fields) * 16 - 40
