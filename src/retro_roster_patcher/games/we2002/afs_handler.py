"""Konami AFS archive handler for WE2002 game assets.

Layout: magic "AFS" + NUL at 0, u32 file count at 4, then a TOC of u32 offset +
u32 size per entry from 8. Header and every entry are padded to a 2048-byte CD
sector.
"""

import os
import struct

from .models import AfsEntry


class AfsHandler:
    AFS_MAGIC = b"AFS\x00"
    SECTOR_SIZE = 2048  # CD sector alignment

    def __init__(self, afs_path: str):
        self.afs_path = afs_path
        self._entries: list[AfsEntry] = []
        self._raw: bytes = b""
        if os.path.exists(afs_path):
            with open(afs_path, "rb") as f:
                self._raw = f.read()
            self._parse()

    def _parse(self):
        if len(self._raw) < 8:
            return
        magic = self._raw[:4]
        if magic != self.AFS_MAGIC:
            raise ValueError(f"Not a valid AFS archive (magic: {magic!r})")
        file_count = struct.unpack_from("<I", self._raw, 4)[0]
        self._entries = []
        for i in range(file_count):
            toc_offset = 8 + i * 8
            offset, size = struct.unpack_from("<II", self._raw, toc_offset)
            self._entries.append(AfsEntry(index=i, offset=offset, size=size))

    def list_entries(self) -> list[AfsEntry]:
        return list(self._entries)

    def extract_entry(self, index: int) -> bytes:
        if index < 0 or index >= len(self._entries):
            raise IndexError(f"AFS entry index {index} out of range")
        entry = self._entries[index]
        return self._raw[entry.offset : entry.offset + entry.size]

    def replace_entry(self, index: int, data: bytes):
        """Replace an entry's data in the in-memory copy only.

        The replacement is written inside the entry's own extent and zero-padded
        to the original size, so no TOC offset moves. Nothing reaches disk until
        the caller runs `rebuild`.
        """
        if index < 0 or index >= len(self._entries):
            raise IndexError(f"AFS entry index {index} out of range")
        entry = self._entries[index]
        if len(data) > entry.size:
            raise ValueError(
                f"New data ({len(data)} bytes) exceeds original entry size "
                f"({entry.size} bytes). Use rebuild() for larger replacements."
            )
        raw_list = bytearray(self._raw)
        raw_list[entry.offset : entry.offset + entry.size] = data + b"\x00" * (
            entry.size - len(data)
        )
        self._raw = bytes(raw_list)

    def rebuild(self, output_path: str, replacements: dict | None = None):
        """Rebuild the archive into `output_path`.

        `replacements` maps entry index to new data and may grow an entry, since
        every offset is recomputed.
        """
        if replacements is None:
            replacements = {}

        entry_data_list = []
        for entry in self._entries:
            if entry.index in replacements:
                entry_data_list.append(replacements[entry.index])
            else:
                entry_data_list.append(self._raw[entry.offset : entry.offset + entry.size])

        header_size = 8 + len(self._entries) * 8
        header_padded_size = (
            (header_size + self.SECTOR_SIZE - 1) // self.SECTOR_SIZE
        ) * self.SECTOR_SIZE

        new_offsets = []
        current_offset = header_padded_size
        for data in entry_data_list:
            new_offsets.append(current_offset)
            padded_size = (
                (len(data) + self.SECTOR_SIZE - 1) // self.SECTOR_SIZE
            ) * self.SECTOR_SIZE
            current_offset += padded_size

        output = bytearray()
        output += self.AFS_MAGIC
        output += struct.pack("<I", len(self._entries))
        for i, _entry in enumerate(self._entries):
            new_size = len(entry_data_list[i])
            output += struct.pack("<II", new_offsets[i], new_size)
        output += b"\x00" * (header_padded_size - len(output))
        for data in entry_data_list:
            output += data
            padded_size = (
                (len(data) + self.SECTOR_SIZE - 1) // self.SECTOR_SIZE
            ) * self.SECTOR_SIZE
            output += b"\x00" * (padded_size - len(data))

        with open(output_path, "wb") as f:
            f.write(output)
