"""EA's PS2 shape files (`.ssh`, magic `ShpS`), 8-bit ones: NHL 2005's pictures.

The file's first shape starts at the big-endian offset at 0x10. A shape is a
chain of blocks, each a 32-byte header then data: byte 0 the block's kind,
the little-endian word at +4 the distance to the next block, the word at +0xC
the data's length, the words at +0x18 and +0x1C its width and height.

    0x02  8-bit indexed pixels, row by row (the portraits, the boot screen)
    0x08  8-bit indexed pixels packed for the GS (the menu and title pictures)
    0x21  the palette: 256 RGBA entries, alpha 0-0x80 (0x80 opaque), in the
          GS's CSM1 order (within every 32, entries 8-15 and 16-23 swapped)

A packed picture (0x08) is what the game draws into a 32-bit frame half its
width and height and then reads as an 8-bit texture (`formats.gs`): the
upper seven bits of every index, as a 32-bit image of a quarter the height
drawn twice as tall, then one bit per pixel, as a 4-bit texture added to it
(the game uploads that plane as a 16-bit image). So two vertically adjacent
pixels share an even index and each adds its own low bit: the palette comes
in pairs of similar colours, and `encode` chooses it that way, 128 pairs of
two shades.

`decode` and `encode` need Pillow; `encode` keeps the file's size.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING, NamedTuple

from ..core.errors import RomError
from . import gs

if TYPE_CHECKING:
    from PIL import Image

MAGIC = b"ShpS"
PIXELS = 0x02
PACKED = 0x08
PALETTE = 0x21
OPAQUE = 0x80


class SshError(RomError):
    """The bytes are not an 8-bit `ShpS` shape this module can read."""


class _Shape(NamedTuple):
    kind: int
    data: int  # where the pixel data starts
    size: int
    width: int
    height: int
    palette: int  # where the 1024 palette bytes start


def _blocks(data: bytes) -> _Shape:
    if data[:4] != MAGIC:
        raise SshError("Not an EA PS2 shape (missing ShpS)")
    block = struct.unpack_from(">I", data, 0x10)[0]
    if block + 0x20 > len(data) or data[block] not in (PIXELS, PACKED):
        raise SshError("The first shape is not 8-bit indexed pixels")
    width, height = struct.unpack_from("<II", data, block + 0x18)
    pal = block + struct.unpack_from("<I", data, block + 4)[0] & 0xFFFFFF
    if pal + 0x20 + 1024 > len(data) or data[pal] != PALETTE:
        raise SshError("The pixels are not followed by a 256-colour palette")
    size = width * height if data[block] == PIXELS else width * height * 5 // 8
    if block + 0x20 + size > pal:
        raise SshError("The shape is shorter than its size says")
    return _Shape(data[block], block + 0x20, size, width, height, pal + 0x20)


def _csm1(index: int) -> int:
    """Where colour `index` sits in a CSM1 palette (the swap is its own inverse)."""
    part = index & 0x18
    return index ^ 0x18 if part in (0x08, 0x10) else index


def _colours(data: bytes, pal: int) -> list[tuple[int, int, int, int]]:
    out = []
    for i in range(256):
        r, g, b, a = data[pal + 4 * _csm1(i) : pal + 4 * _csm1(i) + 4]
        out.append((r, g, b, min(255, a * 2)))
    return out


def _table(colours: list[tuple[int, int, int, int]]) -> bytes:
    """256 RGBA colours as the file's CSM1 palette, alpha 0 or `OPAQUE`."""
    table = bytearray(1024)
    for i, (r, g, b, a) in enumerate(colours[:256]):
        table[4 * _csm1(i) : 4 * _csm1(i) + 4] = bytes([r, g, b, OPAQUE if a >= 128 else 0])
    return bytes(table)


def dimensions(data: bytes) -> tuple[int, int]:
    """The shape's (width, height)."""
    shape = _blocks(data)
    return shape.width, shape.height


def indices(data: bytes) -> tuple[bytes, int, int]:
    """The shape's pixels as one index per pixel, row by row, with its size."""
    shape = _blocks(data)
    w, h = shape.width, shape.height
    if shape.kind == PIXELS:
        return data[shape.data : shape.data + w * h], w, h
    half = w * h // 2
    main = data[shape.data : shape.data + half]
    plane = data[shape.data + half : shape.data + half + half // 4]
    fw, fh = w // 2, h // 2
    bits = gs.unpack("T4", gs.pack("CT16", plane, w // 4, h // 4), fw, fh, bw=fw)
    frame = bytearray(fw * fh * 4)
    for fy in range(fh):
        row = (fy // 2) * fw * 4
        for fx in range(fw):
            t = bits[fy * fw + fx]
            at = 4 * (fy * fw + fx)
            for k in range(4):
                frame[at + k] = (main[row + 4 * fx + k] + ((t >> k) & 1)) & 0xFF
    return gs.unpack("T8", gs.pack("CT32", bytes(frame), fw, fh), w, h, bw=w), w, h


def decode(data: bytes):  # -> Image.Image
    """The shape as an RGBA picture."""
    from PIL import Image

    pixels, width, height = indices(data)
    colours = _colours(data, _blocks(data).palette)
    picture = Image.new("RGBA", (width, height))
    picture.putdata([colours[p] for p in pixels])
    return picture


def _pack(pixels: bytes, w: int, h: int) -> bytes:
    """One index per pixel as a packed shape's data: the seven-bit image and
    the low-bit plane. Vertically adjacent pixels must share their upper bits."""
    fw, fh = w // 2, h // 2
    frame = gs.unpack("CT32", gs.pack("T8", pixels, w, h), fw, fh, bw=fw)
    main = bytearray(fw * fh * 2)
    texels = bytearray(fw * fh)
    for fy in range(fh):
        for fx in range(fw):
            at = 4 * (fy * fw + fx)
            t = 0
            for k in range(4):
                v = frame[at + k]
                t |= (v & 1) << k
                if fy % 2 == 0:
                    main[4 * ((fy // 2) * fw + fx) + k] = v & 0xFE
            texels[fy * fw + fx] = t
    plane = gs.unpack("CT16", gs.pack("T4", bytes(texels), fw, fh), w // 4, h // 4, bw=w // 4)
    return bytes(main) + plane


def _mean(rgb: bytes, pixels: list[int]) -> tuple[int, int, int]:
    n = max(1, len(pixels))
    return tuple(sum(rgb[3 * i + c] for i in pixels) // n for c in range(3))  # type: ignore[return-value]


def _paired(picture: Image.Image, pairs: int) -> tuple[bytes, list[tuple[int, int, int, int]]]:
    """`picture` as indices into a palette of `pairs` pairs of shades, each
    vertical pair of pixels sharing a pair: the index is twice the pair plus
    the shade (`_pack`). Pair 0 is transparent when the picture needs one."""
    from PIL import Image

    rgba = picture.convert("RGBA")
    w, h = rgba.size
    alpha = rgba.getchannel("A").tobytes()
    rgb = rgba.convert("RGB").tobytes()
    clear = min(alpha) < 128
    first = 1 if clear else 0
    pairs = max(1, min(128 - first, pairs))
    # Each vertical pair's mean colour, quantized: which pair of shades it takes.
    means = rgba.convert("RGB").resize((w, h // 2), Image.Resampling.BOX)
    groups = means.quantize(pairs, method=Image.Quantize.MEDIANCUT).tobytes()
    members: list[list[int]] = [[] for _ in range(pairs)]
    for y in range(h):
        for x in range(w):
            if alpha[y * w + x] >= 128:
                members[groups[(y // 2) * w + x]].append(y * w + x)
    colours: list[tuple[int, int, int, int]] = [(0, 0, 0, 0), (0, 0, 0, 0)] if clear else []
    shades: list[tuple[tuple[int, int, int], tuple[int, int, int]]] = []
    for group in members:
        # The darker and the lighter half of the group's pixels, as two shades.
        lit = sorted(group, key=lambda i: rgb[3 * i] * 3 + rgb[3 * i + 1] * 6 + rgb[3 * i + 2])
        lo = _mean(rgb, lit[: max(1, len(lit) // 2)])
        hi = _mean(rgb, lit[max(1, len(lit) // 2) :]) if len(lit) > 1 else lo
        shades.append((lo, hi))
        colours += [(*lo, 255), (*hi, 255)]
    colours += [(0, 0, 0, 255)] * (256 - len(colours))
    out = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            i = y * w + x
            if alpha[i] < 128:
                continue
            k = groups[(y // 2) * w + x]
            lo, hi = shades[k]
            p = rgb[3 * i : 3 * i + 3]
            near = sum((a - b) ** 2 for a, b in zip(p, hi, strict=True)) < sum(
                (a - b) ** 2 for a, b in zip(p, lo, strict=True)
            )
            out[i] = 2 * (k + first) + (1 if near else 0)
    return bytes(out), colours


def encode(data: bytes, picture: Image.Image, colours: int = 256) -> bytes:
    """`data` with its pixels and palette replaced by `picture` (the shape's
    size), reduced to `colours` (at most 256; the transparent one included).
    A packed shape takes `colours // 2` pairs of shades."""
    from PIL import Image

    shape = _blocks(data)
    width, height = shape.width, shape.height
    if picture.size != (width, height):
        raise ValueError(f"Picture is {picture.size}, shape is {(width, height)}")
    out = bytearray(data)
    if shape.kind == PACKED:
        pixels, palette = _paired(picture, colours // 2)
        out[shape.data : shape.data + width * height * 5 // 8] = _pack(pixels, width, height)
        out[shape.palette : shape.palette + 1024] = _table(palette)
        return bytes(out)
    rgba = picture.convert("RGBA")
    alpha = rgba.getchannel("A")
    reduced = rgba.convert("RGB").quantize(colours - 1, method=Image.Quantize.MEDIANCUT)
    flat = reduced.getpalette() or []
    pixels = bytes(
        0 if a < 128 else i + 1 for i, a in zip(reduced.tobytes(), alpha.tobytes(), strict=True)
    )
    table = bytearray(1024)
    for i in range(1, 256):
        rgb = flat[3 * (i - 1) : 3 * i] if 3 * i <= len(flat) else [0, 0, 0]
        table[4 * _csm1(i) : 4 * _csm1(i) + 4] = bytes([*rgb, OPAQUE])
    out[shape.data : shape.data + width * height] = pixels
    out[shape.palette : shape.palette + 1024] = table
    return bytes(out)
