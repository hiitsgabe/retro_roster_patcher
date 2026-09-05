"""Rebuild `database.big`'s CSV sections and write them back into a copy of the ISO.

    records -> CSV text -> RefPack -> the section's own fixed allocation
                                   -> database.big -> ISO copy

Every section has a fixed home and no length word. A section starts at its
offset in `SECTION_MAP` and ends where the next one starts, so the only way to
store one is to fit it in that space and zero the remainder. A recompressed
section that does not fit cannot be written at all.
"""

from __future__ import annotations

import os

from ...core.errors import RomError
from ...formats.ea_tdb import refpack_compress
from .models import (
    COMPACT_ATTRIB_TABLE,
    DATABASE_BIG_SIZE,
    SECTION_ALLOCATIONS,
    SECTION_MAP,
    database_big_extent,
)
from .rom_reader import MVPPSPRomReader, Table

# How much of a file to move per read while copying.
COPY_CHUNK_BYTES = 4 * 1024 * 1024

# What a record and a section line end with. The comma before the semicolon is
# part of the record: a record with no fields at all is still `id,;`.
RECORD_SUFFIX = ",;"
LINE_TERMINATOR = "\r\n"

# What a header line ends with. One semicolon and no comma, unlike a record:
# the header is `field0name,field1name,...;` where a record is `id,...,;`.
HEADER_SUFFIX = ";"

# What `_extract_headers` searches for to find where the header line ends, and
# what `rom_reader` splits records on.
RECORD_TERMINATOR = HEADER_SUFFIX + LINE_TERMINATOR


def build_csv_record(hash_id: str, fields: dict[int, str]) -> str:
    """One record line, without its terminator.

    `00b87d5f5,0 Ichiro,1 Suzuki,22 61,;`

    Columns are emitted in ascending numeric order, which is not necessarily the
    order the disc had them in: a record read back and written out unchanged is
    byte-identical only if the disc's own order was ascending.
    """
    parts = [hash_id]
    parts.extend(f"{num} {fields[num]}" for num in sorted(fields))
    return ",".join(parts) + RECORD_SUFFIX


def build_csv_section(
    header: str,
    records: Table,
    record_order: list[str] | None = None,
) -> bytes:
    """The whole of one table: its header line, then one line per record.

    `record_order` is what the reader saw on the disc. Ids in it are emitted
    first, in that order and including any repeats it holds; ids in `records`
    that the order does not name are appended afterwards in dict order, which
    for this package means the order `patch` created them in. An id in the order
    that is no longer in `records` is skipped, which is how a deleted record
    leaves.

    Encoded ASCII with `errors="replace"`, so a name carrying a character the
    disc's charset has no room for becomes `?` rather than raising in the middle
    of a rebuild.
    """
    lines = [header + RECORD_TERMINATOR]

    if record_order:
        hash_list = list(record_order)
        seen = set(hash_list)
        hash_list.extend(h for h in records if h not in seen)
    else:
        hash_list = sorted(records)

    for hash_id in hash_list:
        fields = records.get(hash_id)
        if fields is None:
            continue
        lines.append(build_csv_record(hash_id, fields) + LINE_TERMINATOR)
    return "".join(lines).encode("ascii", errors="replace")


class SectionTooLargeError(RomError):
    """A rebuilt section does not fit the space `database.big` reserves for it."""

    def __init__(self, table: str, compressed: int, allocation: int) -> None:
        self.table = table
        self.compressed = compressed
        self.allocation = allocation
        super().__init__(
            f"The rebuilt {table!r} table compresses to {compressed} bytes and "
            f"database.big reserves {allocation} for it, {compressed - allocation} short. "
            f"Sections sit at fixed offsets with no length word, so it cannot be stored. "
            f"Patch fewer teams, or use shorter player names."
        )


class MVPPSPRomWriter:
    """Copies the ISO, rewrites `database.big` inside the copy.

    Only the 386 977 bytes of `database.big` are rewritten; every other byte of
    the output is the input's. There is no ISO 9660 rewrite, no directory
    record to update -- the file's length never changes -- and no checksum
    anywhere on a PSP UMD image that this touches.
    """

    def __init__(self, iso_path: str, output_path: str) -> None:
        self.iso_path = iso_path
        self.output_path = output_path
        self.reader = MVPPSPRomReader(iso_path)
        # table name -> the section's first line, without its `;\r\n`
        self.section_headers: dict[str, str] = {}
        self._modified_tables: set[str] = set()

    def load(self) -> bool:
        """Read and parse the source ISO's `database.big`.

        False when the file cannot be read or does not carry this game's
        `database.big`. The caller turns that into a `RomError`.
        """
        if not self.reader.load():
            return False
        if not self.reader.validate():
            return False
        self.reader.decompress_all()
        self._extract_headers()
        self.reader.parse_all()
        return True

    def _extract_headers(self) -> None:
        """Keep each section's first line, which names its columns.

        A section with no `;\\r\\n` in it at all contributes no header and is
        therefore never rewritten -- `_rebuild_section_bytes` refuses to invent
        one, because a table written without its column names is a table the
        game cannot read.
        """
        for name, data in self.reader.sections.items():
            text = data.decode("ascii", errors="replace")
            idx = text.find(RECORD_TERMINATOR)
            if idx >= 0:
                self.section_headers[name] = text[:idx]

    def update_records(self, table_name: str, records: Table) -> None:
        """Replace a whole table. Used for `roster`, which is rebuilt from scratch."""
        self.reader.records[table_name] = records
        self._modified_tables.add(table_name)

    def update_player_record(
        self, table_name: str, player_hash: str, fields: dict[int, str]
    ) -> None:
        """Merge `fields` into one record, creating it if the table has none.

        The merge is what preserves everything this patcher has no source for --
        spray charts, batted-ball tendencies, salary, contract length, birthday.
        A column absent from `fields` keeps whatever the disc had for the player
        whose id is being reused.

        Copy the existing record before updating it: mutating the dict
        `reader.records` handed back would make a second `patch` on the same
        reader start from the first one's results.
        """
        table = self.reader.records.setdefault(table_name, {})
        merged = dict(table.get(player_hash, {}))
        merged.update(fields)
        table[player_hash] = merged
        self._modified_tables.add(table_name)

    def _rebuild_section_bytes(self, name: str) -> bytes | None:
        """Rebuild one table and compress it, or None if it cannot be rebuilt.

        None means "this table was never read, or has no header line", both of
        which mean there is nothing to write. It does **not** mean "too large":
        that raises.
        """
        records = self.reader.records.get(name)
        if records is None:
            return None
        header = self.section_headers.get(name)
        if not header:
            return None
        order = self.reader.record_order.get(name)
        return refpack_compress(build_csv_section(header, records, order))

    def rebuild_database_big(self) -> bytes:
        """The whole of `database.big`, with every modified section rewritten.

        Each rewritten section goes back at its own offset and the rest of its
        allocation is zeroed, so the fixed offsets the game expects are
        preserved. Sections nothing touched are copied through byte for byte.

        Deliberate, adjudicated divergence from upstream: a section that does not
        fit raises `SectionTooLargeError` where the source did `continue`. Do not
        restore the `continue`; it kept the original section, dropped every edit
        to that table, and still reported a successful patch.

        The `attrib_compact` section is skipped, and that is an inherited defect
        rather than a decision -- see `_skip_compact_attrib`.
        """
        original = self.reader.database_big
        if original is None:
            raise RomError("database.big was never loaded")

        result = bytearray(original)
        for _, name in SECTION_MAP:
            if name not in self._modified_tables:
                continue
            # Redundant with the `if not compressed` below, and kept: this one
            # says which sections are candidates, that one says the rebuild
            # produced nothing.
            if name not in self.section_headers:
                continue
            if self._skip_compact_attrib(name):
                continue

            compressed = self._rebuild_section_bytes(name)
            if not compressed:
                continue

            offset, allocation = SECTION_ALLOCATIONS[name]
            if len(compressed) > allocation:
                raise SectionTooLargeError(name, len(compressed), allocation)

            result[offset : offset + len(compressed)] = compressed
            result[offset + len(compressed) : offset + allocation] = b"\x00" * (
                allocation - len(compressed)
            )
        return bytes(result)

    @staticmethod
    def _skip_compact_attrib(name: str) -> bool:
        """Is this the compact attribute table, which is never rewritten?

        Inherited defect, preserved: `attrib_compact` is a second copy of the
        player attributes in a narrower form -- 324 bytes of allocation against
        `attrib`'s 61 448 -- and any screen that reads it shows the 2005 player.
        Fixing it needs the compact table's column layout, which is nowhere in
        this repository, and writing a guess is worse than not writing.

        Unreachable through `patch`, since `_modified_tables` only ever gains the
        names in `models.MODIFIED_TABLES`. Kept so a future caller that stages an
        edit here is stopped rather than writing it.
        """
        return name == COMPACT_ATTRIB_TABLE

    def copy_iso(self) -> None:
        """Copy the source image to the output path, creating its directory.

        Let `OSError` propagate; the caller wraps it and names the file the OS
        complained about.
        """
        output_dir = os.path.dirname(self.output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(self.iso_path, "rb") as src, open(self.output_path, "wb") as dst:
            while True:
                chunk = src.read(COPY_CHUNK_BYTES)
                if not chunk:
                    break
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())

    def finalize(self) -> None:
        """Copy the ISO and write the rebuilt `database.big` into the copy.

        The rebuilt blob is always exactly `DATABASE_BIG_SIZE` bytes -- it is a
        copy of the original with sections overwritten in place -- so the output
        file's length equals the input's and no ISO 9660 record needs updating.
        That invariant is checked rather than assumed: a rebuild of the wrong
        length would silently move every byte of the image after it.

        Raises:
            RomError: nothing was loaded, or a section does not fit.
            OSError: the copy or the write failed; the caller wraps it.
        """
        # Redundant with the identical test in `rebuild_database_big`, and kept:
        # `finalize` copies a several-hundred-megabyte image, so the precondition
        # belongs where the method begins.
        if self.reader.database_big is None:
            raise RomError("database.big was never loaded")

        new_db = self.rebuild_database_big()
        if len(new_db) != DATABASE_BIG_SIZE:
            raise RomError(
                f"The rebuilt database.big is {len(new_db)} bytes and must be "
                f"{DATABASE_BIG_SIZE}; writing it would shift the rest of the image"
            )

        offset, _ = database_big_extent()
        self.copy_iso()
        with open(self.output_path, "r+b") as f:
            f.seek(offset)
            f.write(new_db)
            f.flush()
            os.fsync(f.fileno())


__all__ = [
    "MVPPSPRomWriter",
    "SectionTooLargeError",
    "build_csv_record",
    "build_csv_section",
]
