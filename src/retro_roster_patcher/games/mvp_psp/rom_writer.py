"""Rebuild `database.big`'s CSV sections and write them back into a copy of the ISO.

    records -> CSV text -> RefPack -> the section's own fixed allocation
                                   -> database.big -> ISO copy

**Every section has a fixed home and no length word.** A section starts at its
offset in `SECTION_MAP` and ends where the next one starts; there is nothing in
the file that says how long it actually is, so the only way to store a section
is to fit it in that space and zero the remainder. A recompressed section that
does not fit cannot be written at all.

That is the whole of this module's difficulty, and it is where the source's
worst defect was. See `_rebuild_section_bytes`.
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

# How much of a file to move per read while copying. 4 MiB, the source's.
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
    order the disc had them in -- a record read back and written out unchanged
    is byte-identical only if the disc's own order was ascending. Nothing in
    this repository can establish whether it is, and the source made the same
    assumption; it is stated here rather than left implicit.
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

    Encoded ASCII with `errors="replace"`, the source's choice, so a name
    carrying a character the disc's charset has no room for becomes `?` rather
    than raising in the middle of a rebuild. Every non-ASCII character costs one
    byte of a fixed allocation and this is the point at which that is decided.
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
    """A rebuilt section does not fit the space `database.big` reserves for it.

    A `RomError` because it is a statement about what will and will not fit on
    the user's disc, and `Patcher.patch` promises `RomError` for that.
    """

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

    # -- loading ------------------------------------------------------------

    def load(self) -> bool:
        """Read and parse the source ISO's `database.big`.

        False when the file cannot be read or does not carry this game's
        `database.big`. The caller turns that into a `RomError`; the source
        returned it up through three layers of boolean and lost the reason.
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

    # -- staging edits ------------------------------------------------------

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

        The existing record is copied before being updated. The source mutated
        the dict it got back from `reader.records`, which is the same object the
        reader's own `record_order` and every other accessor see; under the new
        architecture `patch` may be called twice on one writer's reader and the
        second call would have started from the first call's results.
        """
        table = self.reader.records.setdefault(table_name, {})
        merged = dict(table.get(player_hash, {}))
        merged.update(fields)
        table[player_hash] = merged
        self._modified_tables.add(table_name)

    # -- rebuilding ---------------------------------------------------------

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

        DELIBERATE DIVERGENCE -- a section that does not fit raises
        `SectionTooLargeError` where the source did `continue`. That `continue`
        kept the *original* section, dropping every edit to that table, and then
        returned a successful `PatchResult` reporting the full count of teams
        and players patched. For the `roster` table that is the entire roster
        assignment for all thirty clubs; for `attrib` it is every name. And it
        is not a theoretical branch: RefPack is an LZ77 variant, so how well the
        table compresses depends on how much text repeats inside it, and a
        rebuild that replaces 750 names with 750 different names can compress
        worse than what it replaced. The user would be told the patch succeeded
        and would boot a disc with the 2005 roster on it.

        Raising costs a user who hits it the patch. That is the right trade
        because the alternative costs them the same patch *and* the knowledge
        that they did not get it.

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
            # PROVEN EQUIVALENT under mutation, and kept. `if False` here
            # changes nothing: `_rebuild_section_bytes` reads the same
            # dictionary with `.get`, answers None for a name it has no header
            # for, and `if not compressed` below then does the same `continue`.
            # It is kept because it says at the top of the loop which sections
            # are candidates, where the other reads as "the rebuild produced
            # nothing" -- a different fact that happens to share an outcome.
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

        INHERITED DEFECT, preserved and named. `attrib_compact` is a second copy
        of the player attributes in some narrower form -- 324 bytes of
        allocation against `attrib`'s 61 448 -- and the source wrote `attrib`
        and deliberately skipped it. If any screen in the game reads the compact
        table, that screen shows the 2005 player where every other screen shows
        the patched one.

        It is preserved rather than fixed because fixing it needs the compact
        table's column layout, which is not in `models` and cannot be derived
        from anything in this repository: no disc may enter it. Writing a guess
        into a table the game reads is worse than not writing it.

        The check is also **unreachable through `patch`**, and that is worth
        being precise about rather than deleting it as dead. `_modified_tables`
        only ever gains the five names in `models.MODIFIED_TABLES`, so the guard
        above it already excludes this table. It is kept as the statement of
        intent, because a future caller that stages an edit here would otherwise
        write it, and that is exactly the change that needs a reader to stop.
        """
        return name == COMPACT_ATTRIB_TABLE

    # -- output -------------------------------------------------------------

    def copy_iso(self) -> None:
        """Copy the source image to the output path, creating its directory.

        Raises `OSError`; the caller wraps it. The source caught everything and
        returned False, which reached the user as "Failed to save patched ISO"
        whether the cause was a full disk, a read-only directory or a vanished
        source.
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
        # PROVEN EQUIVALENT under mutation, and kept. `rebuild_database_big`
        # makes the same test on the same attribute one line later and raises a
        # `RomError` carrying the identical message, so no observation --
        # not the type, not the text, not the fact that nothing was written --
        # can tell this check from its absence. It is kept because `finalize`
        # copies a several-hundred-megabyte image, and stating the precondition
        # where the method begins is worth one redundant comparison.
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
