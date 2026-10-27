"""EA's PS2 shape files (`.ssh`, magic `ShpS`), 8-bit ones: NHL 2005's portraits.

The file's first shape starts at the big-endian offset at 0x10. A shape is a
chain of blocks, each a 32-byte header then data: byte 0 the block's kind,
the little-endian word at +4 the distance to the next block, the words at
+0x18 and +0x1C its width and height.

    0x02  8-bit indexed pixels, row by row (not swizzled)
    0x21  the palette: 256 RGBA entries, alpha 0-0x80 (0x80 opaque), in the
          GS's CSM1 order (within every 32, entries 8-15 and 16-23 swapped)

`decode` and `encode` need Pillow; `encode` keeps the file's size.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from ..core.errors import RomError

if TYPE_CHECKING:
    from PIL import Image

MAGIC = b"ShpS"
PIXELS = 0x02
PALETTE = 0x21


class SshError(RomError):
    """The bytes are not an 8-bit `ShpS` shape this module can read."""


def _blocks(data: bytes) -> tuple[int, int, int, int]:
    """(pixel data offset, width, height, palette data offset)."""
    if data[:4] != MAGIC:
        raise SshError("Not an EA PS2 shape (missing ShpS)")
    block = struct.unpack_from(">I", data, 0x10)[0]
    if block + 0x20 > len(data) or data[block] != PIXELS:
        raise SshError("The first shape is not 8-bit indexed pixels")
    width, height = struct.unpack_from("<II", data, block + 0x18)
    pal = block + struct.unpack_from("<I", data, block + 4)[0] & 0xFFFFFF
    if pal + 0x20 + 1024 > len(data) or data[pal] != PALETTE:
        raise SshError("The pixels are not followed by a 256-colour palette")
    return block + 0x20, width, height, pal + 0x20


def _csm1(index: int) -> int:
    """Where colour `index` sits in a CSM1 palette (the swap is its own inverse)."""
    part = index & 0x18
    return index ^ 0x18 if part in (0x08, 0x10) else index


def decode(data: bytes):  # -> Image.Image
    """The shape as an RGBA picture."""
    from PIL import Image

    pixels, width, height, pal = _blocks(data)
    colours = []
    for i in range(256):
        r, g, b, a = data[pal + 4 * _csm1(i) : pal + 4 * _csm1(i) + 4]
        colours.append((r, g, b, min(255, a * 2)))
    picture = Image.new("RGBA", (width, height))
    picture.putdata([colours[p] for p in data[pixels : pixels + width * height]])
    return picture


def encode(data: bytes, picture: Image.Image, colours: int = 256) -> bytes:
    """`data` with its pixels and palette replaced by `picture` (the shape's
    size), reduced to `colours` (at most 256; the transparent one included)."""
    from PIL import Image

    pixels, width, height, pal = _blocks(data)
    if picture.size != (width, height):
        raise ValueError(f"Picture is {picture.size}, shape is {(width, height)}")
    rgba = picture.convert("RGBA")
    alpha = rgba.getchannel("A")
    reduced = rgba.convert("RGB").quantize(colours - 1, method=Image.Quantize.MEDIANCUT)
    flat = reduced.getpalette() or []
    indices = [
        0 if a < 128 else i + 1 for i, a in zip(reduced.tobytes(), alpha.tobytes(), strict=True)
    ]
    table = bytearray(1024)
    for i in range(1, 256):
        rgb = flat[3 * (i - 1) : 3 * i] if 3 * i <= len(flat) else [0, 0, 0]
        table[4 * _csm1(i) : 4 * _csm1(i) + 4] = bytes([*rgb, 0x80])
    out = bytearray(data)
    out[pixels : pixels + width * height] = bytes(indices)
    out[pal : pal + 1024] = table
    return bytes(out)
