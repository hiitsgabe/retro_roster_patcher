"""EA's PS2 shape files (`.ssh`, magic `ShpS`), 8-bit ones: NHL 2005's pictures.

The file's shapes are listed from 0x10: for each, its big-endian offset and
length and a NUL-terminated name; the first starts at the offset at 0x10. A
shape is a chain of blocks, each a 32-byte header then data: byte 0 the
block's kind, the little-endian word at +4 the distance to the next block,
the words at +0x18 and +0x1C its width and height.

    0x02  8-bit indexed pixels, row by row (the portraits, the boot screen)
    0x08  8-bit indexed pixels packed for the GS (the menu and title pictures)
    0x0e  the same, packed tighter (the faces, skates and gear in the game)
    0x21  the palette: 256 RGBA entries, alpha 0-0x80 (0x80 opaque), in the
          GS's CSM1 order (within every 32, entries 8-15 and 16-23 swapped)

A packed picture is what the game draws into a 32-bit frame half its width
and height and then reads as an 8-bit texture (`formats.gs`): a 32-bit image
of the indices' upper bits, drawn stretched over the frame, then a few low
bits per pixel added through a 4-bit or 8-bit texture (`PACKINGS`). So the
pixels drawn from one 32-bit pixel of the first image share an index and
each adds its own low bits: the palette comes in runs of similar shades, and
`encode` chooses it that way.

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
PACKED_TIGHT = 0x0E
PALETTE = 0x21
OPAQUE = 0x80


class Packing(NamedTuple):
    """How a packed kind splits an index: the first image is drawn `wide` x
    `tall` times its size, and `bits` low bits per pixel come from a texture
    of `plane` format read as `read` format."""

    wide: int
    tall: int
    plane: str
    read: str
    bits: int

    @property
    def shares(self) -> int:
        """Pixels that share the upper bits of one index."""
        return self.wide * self.tall


#: `PACKED` keeps 7 bits of every two vertically adjacent pixels and one bit
#: each; `PACKED_TIGHT` keeps 6 bits of eight pixels and two bits each.
PACKINGS = {PACKED: Packing(1, 2, "CT16", "T4", 1), PACKED_TIGHT: Packing(4, 2, "CT32", "T8", 2)}


class SshError(RomError):
    """The bytes are not an 8-bit `ShpS` shape this module can read."""


class _Shape(NamedTuple):
    kind: int
    data: int  # where the pixel data starts
    size: int
    width: int
    height: int
    palette: int  # where the 1024 palette bytes start


def shapes(data: bytes) -> list[tuple[str, int, int]]:
    """The file's shapes: (name, offset, length) each, in directory order."""
    if data[:4] != MAGIC:
        raise SshError("Not an EA PS2 shape (missing ShpS)")
    count = struct.unpack_from(">I", data, 8)[0]
    out, pos = [], 0x10
    for _ in range(count):
        if pos + 8 > len(data):
            raise SshError("The shape directory runs off the file")
        offset, length = struct.unpack_from(">II", data, pos)
        end = data.find(b"\0", pos + 8)
        if end < 0:
            raise SshError("A shape's name is not NUL-terminated")
        out.append((data[pos + 8 : end].decode("ascii", "replace"), offset, length))
        pos = end + 1
    return out


def _blocks(data: bytes, shape: str | int | None = None) -> _Shape:
    """The named or numbered shape (the first by default)."""
    if data[:4] != MAGIC:
        raise SshError("Not an EA PS2 shape (missing ShpS)")
    if shape is None:
        block = struct.unpack_from(">I", data, 0x10)[0]
    else:
        listed = shapes(data)
        found = next((o for i, (name, o, _) in enumerate(listed) if shape in (i, name)), None)
        if found is None:
            raise SshError(f"No shape {shape!r} in the file")
        block = found
    if block + 0x20 > len(data) or data[block] not in (PIXELS, *PACKINGS):
        raise SshError("The first shape is not 8-bit indexed pixels")
    width, height = struct.unpack_from("<II", data, block + 0x18)
    pal = block + struct.unpack_from("<I", data, block + 4)[0] & 0xFFFFFF
    if pal + 0x20 + 1024 > len(data) or data[pal] != PALETTE:
        raise SshError("The pixels are not followed by a 256-colour palette")
    size = _data_size(data[block], width, height)
    if block + 0x20 + size > pal:
        raise SshError("The shape is shorter than its size says")
    return _Shape(data[block], block + 0x20, size, width, height, pal + 0x20)


def _data_size(kind: int, width: int, height: int) -> int:
    """How many bytes a shape's pixel data takes."""
    if kind == PIXELS:
        return width * height
    packing = PACKINGS[kind]
    return width * height // packing.shares + width * height * packing.bits // 8


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


def dimensions(data: bytes, shape: str | int | None = None) -> tuple[int, int]:
    """The shape's (width, height)."""
    found = _blocks(data, shape)
    return found.width, found.height


def indices(data: bytes, shape: str | int | None = None) -> tuple[bytes, int, int]:
    """The shape's pixels as one index per pixel, row by row, with its size."""
    found = _blocks(data, shape)
    w, h = found.width, found.height
    if found.kind == PIXELS:
        return data[found.data : found.data + w * h], w, h
    packing = PACKINGS[found.kind]
    fw, fh = w // 2, h // 2
    mw, mh = fw // packing.wide, fh // packing.tall
    main = data[found.data : found.data + mw * mh * 4]
    plane = data[found.data + mw * mh * 4 : found.data + found.size]
    pw, ph = _plane_size(packing, fw, fh)
    bits = gs.unpack(packing.read, gs.pack(packing.plane, plane, pw, ph), fw, fh, bw=fw)
    mask = (1 << packing.bits) - 1
    frame = bytearray(fw * fh * 4)
    for fy in range(fh):
        row = (fy // packing.tall) * mw * 4
        for fx in range(fw):
            t = bits[fy * fw + fx]
            at = 4 * (fy * fw + fx)
            src = row + 4 * (fx // packing.wide)
            for k in range(4):
                frame[at + k] = (main[src + k] + ((t >> (packing.bits * k)) & mask)) & 0xFF
    return gs.unpack("T8", gs.pack("CT32", bytes(frame), fw, fh), w, h, bw=w), w, h


def _plane_size(packing: Packing, fw: int, fh: int) -> tuple[int, int]:
    """The plane's size as uploaded: a quarter of the frame's pixels, each
    holding the low bits of the four pixels of one frame pixel."""
    return fw // 2, fh // 2


def decode(data: bytes, shape: str | int | None = None):  # -> Image.Image
    """The shape (the first, or the named or numbered one) as an RGBA picture."""
    from PIL import Image

    pixels, width, height = indices(data, shape)
    colours = _colours(data, _blocks(data, shape).palette)
    picture = Image.new("RGBA", (width, height))
    picture.putdata([colours[p] for p in pixels])
    return picture


def _groups(w: int, h: int, packing: Packing) -> list[list[int]]:
    """The pixels (row-major indices) that share an index's upper bits: one
    list per 32-bit pixel of the first image and lane of it."""
    fw, fh = w // 2, h // 2
    where = {gs.offset("CT32", fx, fy, fw): (fx, fy) for fy in range(fh) for fx in range(fw)}
    groups: dict[tuple[int, int, int], list[int]] = {}
    for y in range(h):
        for x in range(w):
            at = gs.offset("T8", x, y, w)
            fx, fy = where[at // 4]
            key = (fx // packing.wide, fy // packing.tall, at % 4)
            groups.setdefault(key, []).append(y * w + x)
    return list(groups.values())


def _pack(pixels: bytes, w: int, h: int, packing: Packing) -> bytes:
    """One index per pixel as a packed shape's data: the upper-bits image and
    the low-bit plane. The pixels of a group (`_groups`) must share their
    upper bits."""
    fw, fh = w // 2, h // 2
    mw = fw // packing.wide
    mask = (1 << packing.bits) - 1
    frame = gs.unpack("CT32", gs.pack("T8", pixels, w, h), fw, fh, bw=fw)
    main = bytearray(mw * (fh // packing.tall) * 4)
    texels = bytearray(fw * fh)
    for fy in range(fh):
        for fx in range(fw):
            at = 4 * (fy * fw + fx)
            t = 0
            for k in range(4):
                v = frame[at + k]
                t |= (v & mask) << (packing.bits * k)
                if fy % packing.tall == 0 and fx % packing.wide == 0:
                    main[4 * ((fy // packing.tall) * mw + fx // packing.wide) + k] = v & ~mask
            texels[fy * fw + fx] = t
    pw, ph = _plane_size(packing, fw, fh)
    plane = gs.unpack(packing.plane, gs.pack(packing.read, bytes(texels), fw, fh), pw, ph, bw=pw)
    return bytes(main) + plane


def _mean(rgb: bytes, pixels: list[int]) -> tuple[int, int, int]:
    n = max(1, len(pixels))
    return tuple(sum(rgb[3 * i + c] for i in pixels) // n for c in range(3))  # type: ignore[return-value]


def _grouped(
    picture: Image.Image, packing: Packing, runs: int
) -> tuple[bytes, list[tuple[int, int, int, int]]]:
    """`picture` as indices into a palette of `runs` runs of shades, every
    group of pixels (`_groups`) sharing a run: the index is the run shifted
    by the packing's bits plus the shade. Run 0 is transparent when the
    picture needs one; a group is transparent when at least half of it is."""
    from PIL import Image

    rgba = picture.convert("RGBA")
    w, h = rgba.size
    alpha = rgba.getchannel("A").tobytes()
    rgb = rgba.convert("RGB").tobytes()
    groups = _groups(w, h, packing)
    clear = [sum(alpha[i] < 128 for i in group) * 2 >= len(group) for group in groups]
    first = 1 if any(clear) else 0
    shades = 1 << packing.bits
    runs = max(1, min((256 >> packing.bits) - first, runs))
    # Each group's mean colour, quantized: which run of shades it takes.
    means = Image.new("RGB", (len(groups), 1))
    means.putdata([_mean(rgb, group) for group in groups])
    run_of = means.quantize(runs, method=Image.Quantize.MEDIANCUT).tobytes()
    members: list[list[int]] = [[] for _ in range(runs)]
    for group, run, hidden in zip(groups, run_of, clear, strict=True):
        if not hidden:
            members[run].extend(group)
    colours: list[tuple[int, int, int, int]] = [(0, 0, 0, 0)] * (shades * first)
    tones: list[list[tuple[int, int, int]]] = []
    for pixels in members:
        # The run's shades: the means of the group's pixels split by lightness.
        lit = sorted(pixels, key=lambda i: rgb[3 * i] * 3 + rgb[3 * i + 1] * 6 + rgb[3 * i + 2])
        tone: list[tuple[int, int, int]] = []
        for n in range(shades):
            part = lit[n * len(lit) // shades : (n + 1) * len(lit) // shades]
            tone.append(_mean(rgb, part) if part else (tone[-1] if tone else _mean(rgb, lit)))
        tones.append(tone)
        colours += [(*t, 255) for t in tone]
    colours += [(0, 0, 0, 255)] * (256 - len(colours))
    out = bytearray(w * h)
    for group, run, hidden in zip(groups, run_of, clear, strict=True):
        if hidden:
            continue
        for i in group:
            p = rgb[3 * i : 3 * i + 3]
            shade = min(
                range(shades),
                key=lambda n: sum((a - b) ** 2 for a, b in zip(p, tones[run][n], strict=True)),
            )
            out[i] = ((run + first) << packing.bits) | shade
    return bytes(out), colours


def encode(
    data: bytes, picture: Image.Image, colours: int = 256, shape: str | int | None = None
) -> bytes:
    """`data` with its pixels and palette replaced by `picture` (the shape's
    size), reduced to `colours` (at most 256; the transparent one included).
    A packed shape takes `colours // 2` pairs of shades."""
    from PIL import Image

    found = _blocks(data, shape)
    width, height = found.width, found.height
    if picture.size != (width, height):
        raise ValueError(f"Picture is {picture.size}, shape is {(width, height)}")
    out = bytearray(data)
    if found.kind in PACKINGS:
        packing = PACKINGS[found.kind]
        pixels, palette = _grouped(picture, packing, colours >> packing.bits)
        out[found.data : found.data + found.size] = _pack(pixels, width, height, packing)
        out[found.palette : found.palette + 1024] = _table(palette)
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
    out[found.data : found.data + width * height] = pixels
    out[found.palette : found.palette + 1024] = table
    return bytes(out)
