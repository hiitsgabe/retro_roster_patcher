"""EA's string tables: the `<name>.bin` members the front end looks text up in.

The menus name every string as `$ID`, and the game finds it by `ea_hash(ID)`.
A table, all little-endian:

    +0       0xFEFEFEFE, n, 0, 0
    +16      n u32 id hashes, in no particular order (it differs by language)
    +16+4n   n u32 absolute string offsets, ascending
    strings  UTF-8, each followed by four NULs, back to back

Lines inside a string break with CRLF. NHL 07 (PSP) and NHL 2005 (PS2) share it.
"""

from __future__ import annotations

import struct

from ..core.errors import RomError

_MAGIC = 0xFEFEFEFE


class StringTableError(RomError):
    """The bytes are not a string table this module can read."""


def ea_hash(name: str) -> int:
    """The id hash the game looks strings up by: h = h * 33 + byte, from 0xFFFFFFFF."""
    h = 0xFFFFFFFF
    for byte in name.encode("ascii"):
        h = (h * 33 + byte) & 0xFFFFFFFF
    return h


def parse_table(data: bytes) -> list[tuple[int, str]]:
    """(hash, text) per entry, in file order.

    Raises:
        StringTableError: not a table, or laid out other than `build_table` writes.
    """
    if len(data) < 16:
        raise StringTableError("String table too short")
    magic, n, zero1, zero2 = struct.unpack_from("<4I", data, 0)
    if magic != _MAGIC or zero1 or zero2 or 16 + 8 * n > len(data):
        raise StringTableError("Not a string table")
    hashes = struct.unpack_from(f"<{n}I", data, 16)
    offsets = struct.unpack_from(f"<{n}I", data, 16 + 4 * n)
    entries, pos = [], 16 + 8 * n
    for h, offset in zip(hashes, offsets, strict=True):
        end = data.find(b"\0", offset)
        if offset != pos or end < 0 or data[end : end + 4] != b"\0\0\0\0":
            raise StringTableError(f"Unexpected string layout at {offset:#x}")
        try:
            entries.append((h, data[offset:end].decode("utf-8")))
        except UnicodeDecodeError as e:
            raise StringTableError(f"String at {offset:#x} is not UTF-8") from e
        pos = end + 4
    if pos != len(data):
        raise StringTableError("Trailing bytes after the last string")
    return entries


def build_table(entries: list[tuple[int, str]]) -> bytes:
    """The table bytes; `build_table(parse_table(x)) == x`."""
    n = len(entries)
    blobs = [text.encode("utf-8") + b"\0\0\0\0" for _, text in entries]
    offsets, pos = [], 16 + 8 * n
    for blob in blobs:
        offsets.append(pos)
        pos += len(blob)
    head = struct.pack(f"<4I{n}I{n}I", _MAGIC, n, 0, 0, *(h for h, _ in entries), *offsets)
    return head + b"".join(blobs)
