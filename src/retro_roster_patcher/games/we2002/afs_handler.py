"""Konami AFS archive handler for WE2002 game assets."""

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
        """Parse the AFS header and TOC."""
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
        """Return all file entries in the archive."""
        return list(self._entries)

    def extract_entry(self, index: int) -> bytes:
        """Extract file data for the given entry index."""
        if index < 0 or index >= len(self._entries):
            raise IndexError(f"AFS entry index {index} out of range")
        entry = self._entries[index]
        return self._raw[entry.offset : entry.offset + entry.size]

    def replace_entry(self, index: int, data: bytes):
        """Replace an entry's data in-place (data must be <= original size)."""
        if index < 0 or index >= len(self._entries):
            raise IndexError(f"AFS entry index {index} out of range")
        entry = self._entries[index]
        if len(data) > entry.size:
            raise ValueError(
                f"New data ({len(data)} bytes) exceeds original entry size "
                f"({entry.size} bytes). Use rebuild() for larger replacements."
            )
        # Patch in-place: overwrite, then pad with zeros
        raw_list = bytearray(self._raw)
        raw_list[entry.offset : entry.offset + entry.size] = data + b"\x00" * (
            entry.size - len(data)
        )
        self._raw = bytes(raw_list)

    def rebuild(self, output_path: str, replacements: dict | None = None):
        """Rebuild the AFS archive, optionally with replaced entry data.

        Args:
            output_path: Path to write the new AFS file.
            replacements: Optional dict of {index: new_data_bytes}.
        """
        if replacements is None:
            replacements = {}

        # Collect all entry data (with replacements)
        entry_data_list = []
        for entry in self._entries:
            if entry.index in replacements:
                entry_data_list.append(replacements[entry.index])
            else:
                entry_data_list.append(self._raw[entry.offset : entry.offset + entry.size])

        # Compute new offsets (pad each to sector boundary)
        header_size = 8 + len(self._entries) * 8
        # Pad header to sector boundary
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

        # Build the new archive
        output = bytearray()
        # Magic + count
        output += self.AFS_MAGIC
        output += struct.pack("<I", len(self._entries))
        # TOC
        for i, _entry in enumerate(self._entries):
            new_size = len(entry_data_list[i])
            output += struct.pack("<II", new_offsets[i], new_size)
        # Pad header
        output += b"\x00" * (header_padded_size - len(output))
        # Entry data
        for data in entry_data_list:
            output += data
            padded_size = (
                (len(data) + self.SECTOR_SIZE - 1) // self.SECTOR_SIZE
            ) * self.SECTOR_SIZE
            output += b"\x00" * (padded_size - len(data))

        with open(output_path, "wb") as f:
            f.write(output)
