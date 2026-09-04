"""Lay out ISO 9660 Mode 1 / 2048 images by hand, one sector at a time.

`formats/iso9660.py` reads directory records; this writes them, from the ECMA-119
field layout rather than from anything the module under test exposes. The two
agree only if both are right about where the PVD is, how a record is laid out,
and which of each number's two endian copies to believe.

Deliberately *not* a tree API. Every test here is about a specific
malformation -- a record whose declared length runs past its extent, a directory
whose declared size stops short of its last record, a file listed after the one
that follows it on the disc -- and a builder that took a nested `Directory`
object would have to grow an option for each. Placing sectors by number is what
lets a test say exactly what is wrong with the image it built.

Images are `bytes` and tests wrap them in `io.BytesIO`. Nothing here writes to a
filesystem: `formats/iso9660.py` takes a file object rather than a path
precisely so that this is possible, and no real ISO may enter this repository.
"""

from __future__ import annotations

import struct

SECTOR_SIZE = 2048
PVD_SECTOR = 16

# ISO 9660's reserved names for "this directory" and "the parent directory".
# Both are one byte, which is what `iso9660.find_entry_with_next_lba` excludes
# on and what `find_entry` compares like any other name and never matches.
SELF_NAME = b"\x00"
PARENT_NAME = b"\x01"

# A fixed recording timestamp so two builds of the same input are byte-identical.
# The year is stored as years since 1900.
_RECORDING_DATE = bytes([105, 4, 6, 9, 30, 0, 0])


def dir_record(
    name: bytes, lba: int, size: int, *, is_dir: bool, rec_len: int | None = None
) -> bytes:
    """One directory record, with every field ECMA-119 requires.

    Both endian copies of the extent and the length are written even though
    `formats/iso9660.py` reads only the little-endian ones: a fixture that wrote
    one copy could not show that a caller correcting a length at +14 lands where
    it should.

    Records are padded to an even length, which ISO 9660 requires.

    `rec_len` overrides byte 0, the record's declared length, *without* moving
    the bytes. That is the only way to build the malformed records this module's
    bounds exist for: a length longer than the record really is, or shorter than
    the 33-byte fixed part.
    """
    length = 33 + len(name)
    if length % 2:
        length += 1
    record = bytearray(length)
    record[0] = length if rec_len is None else rec_len
    record[1] = 0  # extended attribute record length
    struct.pack_into("<I", record, 2, lba)
    struct.pack_into(">I", record, 6, lba)
    struct.pack_into("<I", record, 10, size)
    struct.pack_into(">I", record, 14, size)
    record[18:25] = _RECORDING_DATE
    record[25] = 0x02 if is_dir else 0x00
    record[26] = 0  # file unit size
    record[27] = 0  # interleave gap size
    struct.pack_into("<H", record, 28, 1)  # volume sequence number
    struct.pack_into(">H", record, 30, 1)
    record[32] = len(name)
    record[33 : 33 + len(name)] = name
    return bytes(record)


def directory_extent(
    self_lba: int,
    parent_lba: int,
    records: list[bytes],
    *,
    sectors: int = 1,
) -> bytes:
    """A directory extent: `.`, `..`, the given records, then NUL padding.

    The padding is what makes `iso9660._records` jump to the next sector rather
    than stop -- a record length of zero means "nothing more in this sector".
    `sectors` above 1 builds a directory that spans several, which is the case a
    scan that stopped at the first zero byte would read only the beginning of.

    Returns exactly `sectors * SECTOR_SIZE` bytes. What a caller *declares* the
    length to be is a separate decision, made where the record naming this
    directory is built -- which is how a test builds an extent whose declared
    size stops short of its last record.
    """
    out = bytearray()
    out += dir_record(SELF_NAME, self_lba, sectors * SECTOR_SIZE, is_dir=True)
    out += dir_record(PARENT_NAME, parent_lba, SECTOR_SIZE, is_dir=True)
    for record in records:
        out += record
    if len(out) > sectors * SECTOR_SIZE:
        raise AssertionError(f"directory is {len(out)} bytes, over {sectors} sector(s)")
    return bytes(out) + b"\x00" * (sectors * SECTOR_SIZE - len(out))


def used_length(records: list[bytes]) -> int:
    """How many bytes `directory_extent` will have written before its padding.

    The `.` and `..` records are 34 bytes each. A test declaring a directory's
    length as exactly this builds an extent whose final record ends flush with
    its end, which is the case a scan breaking on `pos + rec_len >= len` rather
    than `>` loses.
    """
    return 34 + 34 + sum(len(record) for record in records)


def pvd(
    total_sectors: int,
    root_lba: int,
    root_size: int,
    *,
    type_code: int = 1,
    volume_id: bytes = b"SYNTHETIC",
) -> bytes:
    """A Primary Volume Descriptor with the root directory record at offset 156.

    `type_code` is byte 0. Anything but 1 is a descriptor of another kind and
    `iso9660.read_root` must refuse it rather than read offset 156 of whatever
    it actually is.
    """
    sector = bytearray(SECTOR_SIZE)
    sector[0] = type_code
    sector[1:6] = b"CD001"
    sector[6] = 1  # version
    sector[8:40] = b" " * 32  # system identifier
    sector[40:72] = volume_id.ljust(32)
    struct.pack_into("<I", sector, 80, total_sectors)
    struct.pack_into(">I", sector, 84, total_sectors)
    struct.pack_into("<H", sector, 120, 1)  # volume set size
    struct.pack_into(">H", sector, 122, 1)
    struct.pack_into("<H", sector, 124, 1)  # volume sequence number
    struct.pack_into(">H", sector, 126, 1)
    struct.pack_into("<H", sector, 128, SECTOR_SIZE)
    struct.pack_into(">H", sector, 130, SECTOR_SIZE)
    root = dir_record(SELF_NAME, root_lba, root_size, is_dir=True)
    sector[156 : 156 + len(root)] = root
    return bytes(sector)


def build_image(sectors: dict[int, bytes], total_sectors: int) -> bytes:
    """Assemble an image from `{lba: bytes}`, zero everywhere else.

    A value longer than one sector spans as many as it needs, which is what a
    multi-sector directory and a file both want. Overlap is not checked: a test
    that wants two things at one address is testing that.
    """
    image = bytearray(total_sectors * SECTOR_SIZE)
    for lba, data in sectors.items():
        start = lba * SECTOR_SIZE
        if start + len(data) > len(image):
            raise AssertionError(f"sector {lba} + {len(data)} bytes overruns the image")
        image[start : start + len(data)] = data
    return bytes(image)
