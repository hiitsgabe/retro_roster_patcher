"""LoROM cartridge images: growing one and fixing its header checksum.

Shared by the SNES games that write past the cartridge's own data (screen art,
team logos, relocated tables). Every function takes the image without a copier
header; the caller strips and restores one.
"""

from __future__ import annotations

from ..core.errors import RomError

#: The LoROM header's ROM size byte (2^n KiB) and checksum complement/checksum.
SIZE_BYTE = 0x7FD7
CHECKSUM = 0x7FDC


def grow(rom: bytearray, size: int) -> None:
    """Pad `rom` with 0xFF to `size` bytes (a power of two, at most 4 MiB, the
    most a LoROM maps) and set the header's size byte. A ROM already that
    size is left alone, so every writer can call it."""
    if len(rom) == size:
        return
    if size & (size - 1) or not len(rom) < size <= 0x400000:
        raise RomError(f"Cannot grow a {len(rom)}-byte LoROM to {size} bytes")
    rom += b"\xff" * (size - len(rom))
    rom[SIZE_BYTE] = (size >> 10).bit_length() - 1


def fix_checksum(rom: bytes | bytearray) -> bytearray:
    """`rom` with the header's checksum and complement rewritten for the whole
    image (a power-of-two size, so no mirroring is needed)."""
    out = bytearray(rom)
    out[CHECKSUM : CHECKSUM + 4] = b"\xff\xff\x00\x00"
    total = sum(out) & 0xFFFF
    out[CHECKSUM : CHECKSUM + 4] = bytes(
        (~total & 0xFF, (~total >> 8) & 0xFF, total & 0xFF, total >> 8)
    )
    return out
