"""Read the directory tree of an ISO 9660 Mode 1 / 2048 disc image.

Enough of ECMA-119 to locate a named file: its start sector, its length, and
the offset of the directory record that says so. No Joliet, no Rock Ridge, no
path table, no writing.

**Mode 1 with 2048-byte sectors**, which is what a PSP UMD image and a PS2 DVD
image both are: a sector is nothing but its 2048 payload bytes, so a logical
block address multiplied by 2048 is a byte offset into the file. This is not the
Mode 2 / 2352 layout `games/we2002` uses.

Structural disappointment is `None`, never an exception: a missing PVD or a
missing name means "this image is not the one you were looking for", which is a
normal answer for a caller probing every registered patcher against one file.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass
from typing import BinaryIO

# Both the sector size and the multiplier that turns an LBA into a byte offset.
SECTOR_SIZE = 2048

# ECMA-119 reserves the first 16 sectors as the system area; the volume
# descriptors begin at 16, and the Primary one is the first of them.
PVD_OFFSET = 16 * SECTOR_SIZE

# Byte 0 of a volume descriptor: 1 is Primary.
PVD_TYPE_PRIMARY = 1

# The root directory record is embedded in the PVD at this offset, as a
# fixed-length 34-byte record -- one byte of name, so 33 + 1.
PVD_ROOT_RECORD_OFFSET = 156
PVD_ROOT_RECORD_LENGTH = 34

# A directory record's fixed part: 33 bytes up to and including the name-length
# byte at +32, then the name.
RECORD_HEADER_LENGTH = 33

# Where the numbers live inside a directory record. Every multi-byte field is
# stored twice, little-endian then big-endian; the little-endian copy is what
# this module reads, and a writer correcting a length must correct both.
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
    it points at. A caller rewriting the file's length must patch both copies,
    at `record_offset + 10` little-endian and `record_offset + 14` big-endian.
    """

    name: str
    extent: Extent
    is_dir: bool
    record_offset: int


def read_root(f: BinaryIO) -> Extent | None:
    """The root directory's extent, read from the Primary Volume Descriptor.

    None when sector 16 is not a PVD -- too short to be one, or a descriptor of
    another type. That is the cheapest "not an ISO 9660 image" test there is.
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

    Uppercased and with ISO 9660's `;N` version suffix removed: the same disc is
    authored `DB.VIV;1` in one image and `db.viv;1` in another. Undecodable bytes
    become U+FFFD so an unreadable name is walked past, not raised on.
    """
    return raw.decode("ascii", errors="replace").split(";")[0].upper()


def _records(extent_bytes: bytes, base_offset: int, *, min_name_length: int) -> list[DirEntry]:
    """Every directory record in one extent, in the order they are stored.

    A record length of zero is padding to the end of the sector, so the scan
    jumps to the next sector boundary rather than stopping -- a directory
    spanning several sectors would otherwise be read only as far as its first.

    `min_name_length` is 1 for a normal lookup and 2 to exclude `.` and `..`,
    whose one-byte names are 0x00 and 0x01.
    """
    entries: list[DirEntry] = []
    pos = 0
    while pos < len(extent_bytes):
        rec_len = extent_bytes[pos]
        if rec_len == 0:
            next_sector = ((pos // SECTOR_SIZE) + 1) * SECTOR_SIZE
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

    A short read is left short rather than padded or rejected; a caller that
    needs the whole extent checks `Extent.end` against the file's length.
    """
    f.seek(directory.offset)
    return f.read(directory.size)


def find_entry(f: BinaryIO, directory: Extent, name: str) -> DirEntry | None:
    """The first record in `directory` named `name`, or None.

    Case-insensitive, `;N` stripped -- see `_clean_name`. First rather than
    only: nothing in ISO 9660 forbids two records sharing a name.
    """
    wanted = name.upper()
    for entry in _records(_read_extent(f, directory), directory.offset, min_name_length=1):
        if entry.name == wanted:
            return entry
    return None


def walk(f: BinaryIO, start: Extent, names: Sequence[str]) -> Extent | None:
    """Follow a chain of directory names down from `start`, or None.

    Every name must resolve to a *directory*: a record with the directory flag
    clear ends the walk with None rather than being descended into. An empty
    `names` returns `start` unchanged.
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

    "Next" is by position in the image, not by directory order, hence the sort
    by LBA. The second element is 0 when nothing in *this* directory starts
    after the named entry; treat that as "no headroom known", not as "the rest
    of the image".

    `min_name_length=2` excludes `.` and `..`: `.` points at this directory,
    whose extent sits below everything in it. Subdirectories are NOT excluded --
    a rewrite growing into one would corrupt it just as it would a file.
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
