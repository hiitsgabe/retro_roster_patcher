"""EA's SHPM image container (`.msh`), as NHL 07 (PSP) stores its front-end art.

A file is `SHPM`, its total size (u32), a shape count (u32) and a 4-byte id,
then one (4-character name, u32 absolute offset) directory entry per shape,
then padding to the first shape. Each shape is a chain of blocks; a block
starts with a code byte and a 3-byte little-endian offset to the next block,
zero on the last, which runs to the next shape:

    0x5d  8-bit indexed pixels, followed by a 0x3b palette block
    0x5b  32-bit RGBA pixels
    0x3b  the palette: w * h RGBA entries
    other metadata (0x69, 0x70 with the shape's full name), kept as raw bytes

A pixel block's 16-byte header holds w, h, cx, cy, px, py (u16 each); the top
four bits of py count mip levels. Pixels are PSP-swizzled: 16-byte by 8-row
tiles, left to right then down, rows padded to 16 bytes and the height to 8.

Parsing and building need nothing; decoding and encoding need Pillow.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..core.errors import RomError

if TYPE_CHECKING:
    from PIL import Image

PIX32 = 0x5B
PIX8 = 0x5D
PAL32 = 0x3B
_BPP = {PIX32: 32, PIX8: 8}


class ShpmError(RomError):
    """The bytes are not an SHPM file this module can read."""


@dataclass
class Shape:
    name: bytes
    blocks: list[bytearray] = field(default_factory=list)


@dataclass
class Shpm:
    file_id: bytes
    pad: bytes
    shapes: list[Shape]


def _padded(w: int, h: int, bpp: int) -> tuple[int, int]:
    """Stored (row bytes, rows): row bytes rounded up to 16, rows up to 8."""
    return -(-w * bpp // 128) * 16, -(-h // 8) * 8


def unswizzle(data: bytes, w: int, h: int, bpp: int) -> bytes:
    """One swizzled level to linear bytes, `w * h * bpp / 8` long."""
    row_bytes, rows = _padded(w, h, bpp)
    out = bytearray(row_bytes * rows)
    src = 0
    for ty in range(0, rows, 8):
        for tx in range(0, row_bytes, 16):
            for y in range(ty, ty + 8):
                out[y * row_bytes + tx : y * row_bytes + tx + 16] = data[src : src + 16]
                src += 16
    width = w * bpp // 8
    return b"".join(bytes(out[y * row_bytes : y * row_bytes + width]) for y in range(h))


def swizzle(linear: bytes, w: int, h: int, bpp: int) -> bytes:
    """Linear bytes to one swizzled level, zero-padded to whole tiles."""
    row_bytes, rows = _padded(w, h, bpp)
    width = w * bpp // 8
    lin = bytearray(row_bytes * rows)
    for y in range(h):
        lin[y * row_bytes : y * row_bytes + width] = linear[y * width : (y + 1) * width]
    out = bytearray()
    for ty in range(0, rows, 8):
        for tx in range(0, row_bytes, 16):
            for y in range(ty, ty + 8):
                out += lin[y * row_bytes + tx : y * row_bytes + tx + 16]
    return bytes(out)


def _levels(w: int, h: int, bpp: int, mips: int) -> list[tuple[int, int, int]]:
    """(w, h, stored bytes) for level 0 and each mip level."""
    out = []
    for _ in range(mips + 1):
        row_bytes, rows = _padded(w, h, bpp)
        out.append((w, h, row_bytes * rows))
        w, h = max(1, w // 2), max(1, h // 2)
    return out


def parse(data: bytes) -> Shpm:
    """Split an SHPM file into its shapes and their blocks.

    Raises:
        ShpmError: no `SHPM` magic, a size field that disagrees with the data,
            or a block chain that runs past its shape.
    """
    if len(data) < 16 or data[:4] != b"SHPM":
        raise ShpmError("Not an SHPM file")
    total, count = struct.unpack_from("<II", data, 4)
    if total != len(data) or 16 + 8 * count > len(data):
        raise ShpmError(f"SHPM declares {total} bytes and {count} shapes in {len(data)} bytes")
    directory = [
        (data[16 + 8 * i : 20 + 8 * i], struct.unpack_from("<I", data, 20 + 8 * i)[0])
        for i in range(count)
    ]
    ends = [offset for _, offset in directory[1:]] + [len(data)]
    shapes = []
    for (name, offset), end in zip(directory, ends, strict=True):
        shape = Shape(name)
        while True:
            if offset + 4 > end:
                raise ShpmError("SHPM block chain runs past its shape")
            step = int.from_bytes(data[offset + 1 : offset + 4], "little")
            stop = offset + step if step else end
            if stop > end or stop <= offset:
                raise ShpmError("SHPM block chain runs past its shape")
            shape.blocks.append(bytearray(data[offset:stop]))
            if not step:
                break
            offset = stop
        shapes.append(shape)
    first = directory[0][1] if directory else 16
    return Shpm(file_id=data[12:16], pad=data[16 + 8 * count : first], shapes=shapes)


def build(shpm: Shpm) -> bytes:
    """Reassemble a parsed file; `build(parse(x)) == x`."""
    head = 16 + 8 * len(shpm.shapes) + len(shpm.pad)
    offsets, pos = [], head
    for shape in shpm.shapes:
        offsets.append(pos)
        pos += sum(len(block) for block in shape.blocks)
    out = bytearray(b"SHPM" + struct.pack("<II", pos, len(shpm.shapes)) + shpm.file_id)
    for shape, offset in zip(shpm.shapes, offsets, strict=True):
        out += shape.name + struct.pack("<I", offset)
    out += shpm.pad
    for shape in shpm.shapes:
        for block in shape.blocks:
            out += block
    return bytes(out)


def _header(shape: Shape) -> tuple[int, int, int, int]:
    """(pixel code, width, height, mip count) of a shape's pixel block."""
    block = shape.blocks[0]
    code = block[0]
    if code not in _BPP or len(block) < 16:
        raise ShpmError(f"Unsupported SHPM pixel block 0x{code:02x}")
    w, h, _cx, _cy, _px, py = struct.unpack_from("<6H", block, 4)
    return code, w, h, py >> 12


def _palette(shape: Shape) -> tuple[bytearray, int]:
    block = next((b for b in shape.blocks[1:] if b[0] == PAL32), None)
    if block is None:
        raise ShpmError("8-bit SHPM shape has no palette block")
    w, h = struct.unpack_from("<HH", block, 4)
    return block, w * h


def decode_shape(shape: Shape) -> list[Image.Image]:
    """Every level of a shape as an RGBA image, full size first."""
    from PIL import Image

    code, w, h, mips = _header(shape)
    bpp = _BPP[code]
    palette = b""
    if code == PIX8:
        block, colours = _palette(shape)
        palette = bytes(block[16 : 16 + 4 * colours])
    images, pos = [], 16
    for lw, lh, size in _levels(w, h, bpp, mips):
        linear = unswizzle(bytes(shape.blocks[0][pos : pos + size]), lw, lh, bpp)
        pos += size
        if code == PIX32:
            images.append(Image.frombytes("RGBA", (lw, lh), linear))
        else:
            indexed = Image.frombytes("P", (lw, lh), linear)
            indexed.putpalette(palette, rawmode="RGBA")
            images.append(indexed.convert("RGBA"))
    return images


def _indexed(image: Image.Image, colours: int, old: bytes) -> tuple[bytes, bytes]:
    """(index bytes, palette bytes) for `image` in at most `colours` colours.

    The shape's own palette when it already holds every colour, so an unedited
    image re-encodes byte for byte; Pillow's octree quantiser otherwise.
    """
    from PIL import Image

    rgba = image.convert("RGBA")
    raw = rgba.tobytes()
    pixels = [raw[i : i + 4] for i in range(0, len(raw), 4)]
    lookup: dict[bytes, int] = {}
    for i in range(colours - 1, -1, -1):  # the first of duplicate entries wins
        lookup[old[4 * i : 4 * i + 4]] = i
    if all(p in lookup for p in set(pixels)):
        return bytes(lookup[p] for p in pixels), old
    quantised = rgba.quantize(colours, method=Image.Quantize.FASTOCTREE)
    palette = bytes(quantised.getpalette(rawmode="RGBA") or b"")[: 4 * colours]
    return quantised.tobytes(), palette + bytes(4 * colours - len(palette))


def encode_shape(shape: Shape, image: Image.Image) -> None:
    """Replace a shape's pixels, and palette, with `image`, in place.

    Same size and pixel format as before. Mip levels, when the shape has any,
    are box-filtered from the full image and mapped to the nearest palette
    colour.

    Raises:
        ValueError: `image` is not the shape's size.
    """
    from PIL import Image

    code, w, h, mips = _header(shape)
    if image.size != (w, h):
        raise ValueError(f"Image is {image.size}, shape is {(w, h)}")
    bpp = _BPP[code]
    full = image.convert("RGBA")
    indices = b""
    colours_rgba: list[bytes] = []
    if code == PIX8:
        block, colours = _palette(shape)
        indices, palette = _indexed(full, colours, bytes(block[16 : 16 + 4 * colours]))
        block[16 : 16 + 4 * colours] = palette
        colours_rgba = [palette[i : i + 4] for i in range(0, len(palette), 4)]
    nearest_cache: dict[bytes, int] = {}

    def nearest(pixel: bytes) -> int:
        # ponytail: brute-force nearest colour, cached; only mip levels use it
        if pixel not in nearest_cache:
            nearest_cache[pixel] = min(
                range(len(colours_rgba)),
                key=lambda i: sum(
                    (a - b) ** 2 for a, b in zip(colours_rgba[i], pixel, strict=True)
                ),
            )
        return nearest_cache[pixel]

    data = bytearray()
    for lw, lh, _ in _levels(w, h, bpp, mips):
        if (lw, lh) == (w, h):
            raw = indices if code == PIX8 else full.tobytes()
        else:
            small = full.resize((lw, lh), Image.Resampling.BOX).tobytes()
            if code == PIX8:
                raw = bytes(nearest(small[i : i + 4]) for i in range(0, len(small), 4))
            else:
                raw = small
        data += swizzle(raw, lw, lh, bpp)
    shape.blocks[0][16:] = data
