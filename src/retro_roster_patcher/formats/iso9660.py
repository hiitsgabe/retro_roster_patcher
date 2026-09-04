"""Read the directory tree of an ISO 9660 Mode 1 / 2048 disc image.

Enough of ISO 9660 to answer one question: where in the image does a named file
begin, how long is it, and where is the directory record that says so. That is
what `games/nhl07_psp` and `games/nhl05_ps2` need -- each has exactly one file
to find, `db.viv`, differing only in how deep it is buried -- and it is all this
module does. No Joliet, no Rock Ridge, no path table, no writing.

**Mode 1 with 2048-byte sectors**, which is what a PSP UMD image and a PS2 DVD
image both are: a sector is nothing but its 2048 payload bytes, so a logical
block address multiplied by 2048 is a byte offset into the file. **This is not
the sector layout `games/we2002` uses.** That game reads Mode 2 / 2352 images,
where each sector carries a 12-byte sync pattern, a 4-byte header, an 8-byte
subheader and a trailing EDC/ECC block. The two share the word "sector" and no
arithmetic, and `formats/__init__.py` says why they are not unified.

**This module takes a file object, and that is a considered exception to
`formats/`'s "pure bytes in, bytes out" rule.** A PSP or PS2 image is 500 MB to
1.5 GB; handing one over as `bytes` is not an option on the handhelds this
library targets. What the rule was protecting is testability without a real
image, and a `BinaryIO` keeps that: nothing here opens, closes, stats or names a
file, so a test hands it an `io.BytesIO` over a fabricated image and never
touches a filesystem. A `Path` parameter would have been the version that broke
the property.

**Structural disappointment is `None`, never an exception.** A missing PVD, a
missing directory, a name that is not there: each means "this image is not the
one you were looking for", which is a normal answer for a caller probing every
registered patcher against one file, and not a claim about the user's disc. The
caller decides whether that is an error. Contrast `formats/ea_tdb.py`, which
raises `EaTdbError` -- it is handed bytes that were already located and claimed
to be an archive, so a mismatch there is a real complaint.

**None of this has ever been run against a retail disc**, here or upstream. No
real ISO may enter this repository. The record layout is ECMA-119's and the
scan is transcribed from the two game packages.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass
from typing import BinaryIO

# A Mode 1 sector is its payload and nothing else, so this is both the sector
# size and the multiplier that turns an LBA into a byte offset.
SECTOR_SIZE = 2048

# ECMA-119 reserves the first 16 sectors as the system area; the volume
# descriptors begin at 16, and the Primary one is the first of them.
PVD_OFFSET = 16 * SECTOR_SIZE

# Byte 0 of a volume descriptor: 1 is Primary. `read_root` refuses anything
# else rather than trusting offset 156 of a Supplementary or Terminator.
PVD_TYPE_PRIMARY = 1

# The root directory record is embedded in the PVD at this offset, as a
# fixed-length 34-byte record -- one byte of name, so 33 + 1.
PVD_ROOT_RECORD_OFFSET = 156
PVD_ROOT_RECORD_LENGTH = 34

# A directory record's fixed part: 33 bytes up to and including the name-length
# byte at +32, then the name. `_scan` will not index +32 unless this many bytes
# remain, which is the bound the source lacked -- see `_scan`.
RECORD_HEADER_LENGTH = 33

# Where the numbers live inside a directory record. Every multi-byte field is
# stored twice, little-endian then big-endian; the little-endian copy is what
# this module reads, and `+14` is here because a writer correcting a length has
# to correct both.
_EXTENT_LBA_LE = 2
_DATA_LENGTH_LE = 10
_FILE_FLAGS = 25
_NAME_LENGTH = 32

# Bit 1 of the file-flags byte marks a directory.
_FLAG_DIRECTORY = 0x02


@dataclass(frozen=True)
class Extent:
    """Where a file or directory lives: a start sector and a length in bytes.

    `size` is a byte count and is not sector-aligned; a 3000-byte file occupies
    two sectors and reports 3000.
    """

    lba: int
    size: int

    @property
    def offset(self) -> int:
        """The first byte of the extent, as an offset into the image."""
        return self.lba * SECTOR_SIZE

    @property
    def end(self) -> int:
        """One past the last byte of the extent.

        The image must be at least this long for the extent to be readable,
        which is the arithmetic both game packages check before they trust a
        `db.viv` -- a short file otherwise reads short and silently.
        """
        return self.lba * SECTOR_SIZE + self.size


@dataclass(frozen=True)
class DirEntry:
    """One directory record: what it names, where it points, where it sits.

    `record_offset` is the absolute offset of the record itself, not of the data
    it points at. It is here because a caller that rewrites a file's length has
    to find the two length fields, at `record_offset + 10` little-endian and
    `record_offset + 14` big-endian -- **and because carrying it merges two
    scans into one.** Both game packages had a `_find_dir_entry` and a
    `_find_dir_entry_abs_offset` that walked the same records the same way and
    returned different halves of the same record; four copies of one loop.
    """

    name: str
    extent: Extent
    is_dir: bool
    record_offset: int


def read_root(f: BinaryIO) -> Extent | None:
    """The root directory's extent, read from the Primary Volume Descriptor.

    None when sector 16 is not a PVD -- too short to be one, or a descriptor of
    another type. That is the cheapest "this is not an ISO 9660 image at all"
    test there is, and it is the first thing both games do.
    """
    f.seek(PVD_OFFSET)
    pvd = f.read(SECTOR_SIZE)
    if len(pvd) < SECTOR_SIZE or pvd[0] != PVD_TYPE_PRIMARY:
        return None

    record = pvd[PVD_ROOT_RECORD_OFFSET : PVD_ROOT_RECORD_OFFSET + PVD_ROOT_RECORD_LENGTH]
    return Extent(
        lba=struct.unpack_from("<I", record, _EXTENT_LBA_LE)[0],
        size=struct.unpack_from("<I", record, _DATA_LENGTH_LE)[0],
    )


def _clean_name(raw: bytes) -> str:
    """A directory record's name, as this module compares them.

    Uppercased and with ISO 9660's `;N` version suffix removed, because the same
    disc is authored `DB.VIV;1` in one image and `db.viv;1` in another and both
    are the file the caller means. Undecodable bytes become U+FFFD rather than
    raising: a record this module cannot read the name of is a record it should
    walk past, not a reason to abandon the directory.
    """
    return raw.decode("ascii", errors="replace").split(";")[0].upper()


def _records(extent_bytes: bytes, base_offset: int, *, min_name_length: int) -> list[DirEntry]:
    """Every directory record in one extent, in the order they are stored.

    A record length of zero is padding to the end of the sector, so the scan
    jumps to the next sector boundary rather than stopping -- a directory
    spanning several sectors would otherwise be read only as far as its first.

    `min_name_length` is 1 for a normal lookup and 2 to exclude `.` and `..`,
    whose one-byte names are 0x00 and 0x01. Callers wanting a file's neighbours
    exclude them; a caller looking a name up does not need to, since neither
    name can be matched.

    IMPROVEMENT over both source packages, carried in from `nhl07-psp`'s port:
    the bound at `RECORD_HEADER_LENGTH`. The sources tested `pos + rec_len`
    against the extent and then indexed byte `pos + 32` regardless, so a record
    claiming a length under 33 in the last bytes of an extent passed that test
    and raised `IndexError` -- swallowed upstream by a blanket `except
    Exception`, which is how it stayed invisible. The exact constant is not
    load-bearing: a record is at least 34 bytes, 33 plus a one-byte name padded
    to an even length, so an extent never leaves exactly 33 free, and the
    `name_len` test below would refuse it if one did. What matters is that the
    index is bounded at all.
    """
    entries: list[DirEntry] = []
    pos = 0
    while pos < len(extent_bytes):
        rec_len = extent_bytes[pos]
        if rec_len == 0:
            next_sector = ((pos // SECTOR_SIZE) + 1) * SECTOR_SIZE
            # `>=` and not `>`, and mutation testing says the two are the same
            # function: at `next_sector == len(extent_bytes)` the `>` form
            # assigns `pos = len(extent_bytes)` and the loop condition then
            # fails on the next iteration, so both leave. Kept as `>=` because
            # it says "there is no next sector" at the point the question is
            # asked rather than one statement later.
            if next_sector >= len(extent_bytes):
                break
            pos = next_sector
            continue

        if pos + rec_len > len(extent_bytes):
            break
        if pos + RECORD_HEADER_LENGTH > len(extent_bytes):
            break

        name_len = extent_bytes[pos + _NAME_LENGTH]
        name_end = pos + RECORD_HEADER_LENGTH + name_len
        if name_len >= min_name_length and name_end <= len(extent_bytes):
            entries.append(
                DirEntry(
                    name=_clean_name(extent_bytes[pos + RECORD_HEADER_LENGTH : name_end]),
                    extent=Extent(
                        lba=struct.unpack_from("<I", extent_bytes, pos + _EXTENT_LBA_LE)[0],
                        size=struct.unpack_from("<I", extent_bytes, pos + _DATA_LENGTH_LE)[0],
                    ),
                    is_dir=bool(extent_bytes[pos + _FILE_FLAGS] & _FLAG_DIRECTORY),
                    record_offset=base_offset + pos,
                )
            )

        pos += rec_len
    return entries


def _read_extent(f: BinaryIO, directory: Extent) -> bytes:
    """The bytes of a directory extent, however many of them the image has.

    A short read is left short rather than padded or rejected. The scan is
    bounded by what it got, so a truncated image yields the records that are
    actually there -- and the caller that cares whether the whole extent is
    present checks `Extent.end` against the file's length, which is arithmetic
    this module cannot do without a filesystem.
    """
    f.seek(directory.offset)
    return f.read(directory.size)


def find_entry(f: BinaryIO, directory: Extent, name: str) -> DirEntry | None:
    """The first record in `directory` named `name`, or None.

    Case-insensitive, `;N` stripped from the record's name -- see `_clean_name`.
    First rather than only: nothing in ISO 9660 forbids two records with one
    name, and stopping at the first is what both source packages did.
    """
    wanted = name.upper()
    for entry in _records(_read_extent(f, directory), directory.offset, min_name_length=1):
        if entry.name == wanted:
            return entry
    return None


def walk(f: BinaryIO, start: Extent, names: Sequence[str]) -> Extent | None:
    """Follow a chain of directory names down from `start`, or None.

    Every name must resolve, and every one must resolve to a *directory*: a
    record with the directory flag clear ends the walk with None rather than
    being descended into, because a file called `DB` is not the `DB` directory
    and reading its contents as directory records produces nonsense.

    An empty `names` returns `start` unchanged, which is what a file at the root
    of the image wants and is the only reason this is not simply a loop at the
    call site.
    """
    current = start
    for name in names:
        entry = find_entry(f, current, name)
        if entry is None or not entry.is_dir:
            return None
        current = entry.extent
    return current


def find_entry_with_next_lba(
    f: BinaryIO, directory: Extent, name: str
) -> tuple[DirEntry, int] | None:
    """A named record and the LBA of whatever starts next on the disc, or None.

    "Next" is by position in the image, not by directory order, which is why the
    records are sorted by LBA first. The second element is 0 when nothing in
    *this* directory starts after the named entry -- which does NOT mean it is
    last on the disc, only that this directory holds nothing after it. A caller
    sizing a rewrite must treat 0 as "no headroom known" rather than as "the
    rest of the image".

    `.` and `..` are excluded, and that is what `min_name_length=2` is for: `.`
    points at this very directory, whose extent sits below everything the
    directory holds, so sorting it in would make it look like the file preceding
    them all.

    Subdirectories are NOT excluded. A subdirectory's extent is as real an
    occupant of the disc as a file's, and a rewrite that grew into one would
    corrupt it just the same.
    """
    wanted = name.upper()
    entries = sorted(
        _records(_read_extent(f, directory), directory.offset, min_name_length=2),
        key=lambda entry: entry.extent.lba,
    )
    for i, entry in enumerate(entries):
        if entry.name == wanted:
            next_lba = entries[i + 1].extent.lba if i + 1 < len(entries) else 0
            return entry, next_lba
    return None
