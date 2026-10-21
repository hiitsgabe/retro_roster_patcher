"""The containers PES 2013 (PS2) keeps its data in.

An `.AFS` archive is `"AFS\\0", u32 count, {u32 offset, u32 size}[count]`; the
entries are unnamed (the name table on the disc is scrambled). Each entry is a
*block*: a 32-byte header `u8 kind, u8 flags, u8 zlib, u8 0, u32 csize, u32
usize, 20 x 0` followed by the payload, a zlib stream when `zlib == 1`. A
payload is often a *table*: `u32 n, u32 8, u32 offs[n-1]` where each non-zero
offset starts another block (zero = an unused slot). The table's parts keep
their file order, so a rebuilt table only moves offsets.

Nothing here knows what a part means; `rom_reader` and `rom_writer` do.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import BinaryIO

from ...core.errors import RomError

AFS_MAGIC = b"AFS\0"
BLOCK_HEADER = 32
#: Alignment of parts inside a rebuilt table.
PART_ALIGN = 16


@dataclass(frozen=True)
class AfsEntry:
    index: int
    offset: int
    size: int


def read_afs_entries(f: BinaryIO, base: int = 0) -> list[AfsEntry]:
    """The entry table of the archive starting at file offset `base`."""
    f.seek(base)
    head = f.read(8)
    if head[:4] != AFS_MAGIC:
        raise RomError("Not an AFS archive")
    count = struct.unpack("<I", head[4:])[0]
    raw = struct.unpack(f"<{2 * count}I", f.read(8 * count))
    return [AfsEntry(i, raw[2 * i], raw[2 * i + 1]) for i in range(count)]


def read_entry(f: BinaryIO, base: int, entry: AfsEntry) -> bytes:
    f.seek(base + entry.offset)
    return f.read(entry.size)


def unwrap(block: bytes) -> tuple[bytes, bytes]:
    """(header, payload) of a block, the payload inflated when the header says so."""
    if len(block) < BLOCK_HEADER:
        raise RomError("Block shorter than its header")
    header = block[:BLOCK_HEADER]
    csize = struct.unpack("<I", header[4:8])[0]
    payload = block[BLOCK_HEADER : BLOCK_HEADER + csize]
    if header[2] == 1:
        try:
            payload = zlib.decompress(payload)
        except zlib.error as exc:
            raise RomError(f"Corrupt zlib block: {exc}") from exc
    return header, payload


def wrap(header: bytes, payload: bytes) -> bytes:
    """A block carrying `payload`, deflated when `header` says so; sizes updated."""
    body = zlib.compress(payload, 9) if header[2] == 1 else payload
    head = bytearray(header)
    struct.pack_into("<II", head, 4, len(body), len(payload) if header[2] == 1 else 0)
    return bytes(head) + body


@dataclass
class Table:
    """A `u32 n, u32 8, u32 offs[n-1]` table and its parts (zero slots kept)."""

    count: int
    offsets: list[int]
    parts: list[bytes]

    @classmethod
    def parse(cls, data: bytes) -> Table:
        if len(data) < 8:
            raise RomError("Table shorter than its header")
        count = struct.unpack("<I", data[:4])[0]
        if not 2 <= count <= 1024 or len(data) < 4 + 4 * count:
            raise RomError("Not a part table")
        offsets = list(struct.unpack(f"<{count}I", data[4 : 4 + 4 * count]))
        if offsets[0] != 8:
            raise RomError("Not a part table")
        live = [o for o in offsets[1:] if o]
        if any(o > len(data) for o in live) or any(
            a > b for a, b in zip(live, live[1:], strict=False)
        ):
            raise RomError("Part offsets out of order")
        parts: list[bytes] = []
        for k, off in enumerate(offsets[1:]):
            if not off:
                parts.append(b"")
                continue
            following = [o for o in offsets[k + 2 :] if o]
            end = following[0] if following else len(data)
            parts.append(data[off:end])
        return cls(count, offsets, parts)

    def build(self) -> bytes:
        """The table with its current parts laid back in order."""
        head_len = 4 + 4 * self.count
        # The game's own files leave at least one byte of padding: the first
        # part starts at the next 16-byte boundary strictly past the table.
        pos = (head_len // PART_ALIGN + 1) * PART_ALIGN
        offsets = [8]
        body = bytearray()
        for part in self.parts:
            if not part:
                offsets.append(0)
                continue
            offsets.append(pos + len(body))
            body += part
            body += b"\0" * (-len(part) % PART_ALIGN)
        head = struct.pack(f"<I{self.count}I", self.count, *offsets)
        return head + b"\0" * (pos - len(head)) + bytes(body)
