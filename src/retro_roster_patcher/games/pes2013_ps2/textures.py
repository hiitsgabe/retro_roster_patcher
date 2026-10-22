"""Named texture bundles of PES 2013 (PS2) and the PSMT8 textures inside them.

A bundle (one `*_TEXT.AFS` entry per language) is:

    u32 count, u32 0x10, u32 table, u32 0, u32 name ptr[count], ...
    table:  u32 block offset[count] (zero-terminated)
    names:  char[16] per texture
    blocks: 32-byte block header (kind 0, 3, zlib 1) + zlib stream, each
            inflating to a texture

A texture is a 128-byte header (magic 0x29857294; `+0x14` = `u16 w, u16 h`;
`+0x19` = GS pixel format, 0x13 = 8-bit indexed), then for 8-bit textures a
256-entry RGBA palette (alpha 0x80 = opaque) and the w*h indices. Palette and
pixels are laid out the way the GS keeps them: the palette with its 8-entry
blocks swapped, the pixels block-swizzled.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from ...core.errors import RomError
from .afs import unwrap, wrap

TEXTURE_MAGIC = 0x29857294
TEXTURE_HEADER = 128
PSMT8 = 0x13
PALETTE_BYTES = 1024


def clut_swizzle(pal: bytes) -> bytes:
    """Swap entries 8..15 and 16..23 of every 32-entry block (its own inverse)."""
    out = bytearray(pal)
    for b in range(0, 256, 32):
        for k in range(8):
            a, c = (b + 8 + k) * 4, (b + 16 + k) * 4
            out[a : a + 4], out[c : c + 4] = pal[c : c + 4], pal[a : a + 4]
    return bytes(out)


def _swizzle_map(w: int, h: int) -> list[int]:
    """For each linear pixel (y*w+x), where the GS keeps it in a PSMT8 image."""
    out: list[int] = []
    for y in range(h):
        for x in range(w):
            block = (y & ~0xF) * w + (x & ~0xF) * 2
            swap = (((y + 2) >> 2) & 1) * 4
            py = (((y & ~3) >> 1) + (y & 1)) & 7
            col = py * w * 2 + ((x + swap) & 7) * 4
            bn = ((y >> 1) & 1) + ((x >> 2) & 2)
            out.append(block + col + bn)
    return out


def unswizzle8(data: bytes, w: int, h: int) -> bytes:
    m = _swizzle_map(w, h)
    return bytes(data[p] for p in m)


def swizzle8(linear: bytes, w: int, h: int) -> bytes:
    out = bytearray(w * h)
    for i, p in enumerate(_swizzle_map(w, h)):
        out[p] = linear[i]
    return bytes(out)


@dataclass
class Texture:
    header: bytes
    palette: bytes  # linear RGBA, 256 entries
    pixels: bytes  # linear indices, w*h

    @property
    def width(self) -> int:
        return struct.unpack_from("<H", self.header, 0x14)[0]

    @property
    def height(self) -> int:
        return struct.unpack_from("<H", self.header, 0x16)[0]

    @classmethod
    def parse(cls, data: bytes) -> Texture:
        if len(data) < TEXTURE_HEADER or struct.unpack_from("<I", data)[0] != TEXTURE_MAGIC:
            raise RomError("Not a texture")
        if data[0x19] != PSMT8:
            raise RomError(f"Texture format {data[0x19]:#x} is not 8-bit indexed")
        w, h = struct.unpack_from("<HH", data, 0x14)
        pal = clut_swizzle(data[TEXTURE_HEADER : TEXTURE_HEADER + PALETTE_BYTES])
        px = data[TEXTURE_HEADER + PALETTE_BYTES : TEXTURE_HEADER + PALETTE_BYTES + w * h]
        if len(px) != w * h:
            raise RomError("Texture shorter than its size says")
        return cls(data[:TEXTURE_HEADER], pal, unswizzle8(px, w, h))

    def build(self) -> bytes:
        return (
            self.header
            + clut_swizzle(self.palette)
            + swizzle8(self.pixels, self.width, self.height)
        )

    def to_png(self) -> bytes:
        import io

        from PIL import Image

        rgba = bytes(
            c
            for v in self.pixels
            for c in (
                self.palette[v * 4],
                self.palette[v * 4 + 1],
                self.palette[v * 4 + 2],
                min(255, self.palette[v * 4 + 3] * 2),
            )
        )
        buf = io.BytesIO()
        Image.frombytes("RGBA", (self.width, self.height), rgba).save(buf, "PNG")
        return buf.getvalue()

    def paint(self, png: bytes, *, icon: bool = False) -> None:
        """Replace palette and pixels with `png` fitted into this texture's size;
        an `icon` is trimmed and sharpened like a crest (`emblems.fit_logo`)."""
        import io

        from PIL import Image

        w, h = self.width, self.height
        if icon and w == h:
            from .emblems import fit_logo

            canvas = fit_logo(png, w)
        else:
            img = Image.open(io.BytesIO(png)).convert("RGBA")
            img.thumbnail((w, h), Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            canvas.paste(img, ((w - img.width) // 2, (h - img.height) // 2))
        alpha = canvas.getchannel("A").tobytes()
        # 255 opaque colours; index 0 is reserved for transparency.
        quant = canvas.convert("RGB").quantize(colors=255, method=Image.Quantize.MEDIANCUT)
        flat = quant.getpalette() or []
        pal = bytearray(PALETTE_BYTES)
        for i in range(255):
            r, g, b = flat[3 * i : 3 * i + 3] if 3 * i + 3 <= len(flat) else (0, 0, 0)
            pal[4 * (i + 1) : 4 * (i + 1) + 4] = bytes((r, g, b, 0x80))
        idx = quant.tobytes()
        self.pixels = bytes(0 if alpha[k] < 128 else idx[k] + 1 for k in range(w * h))
        self.palette = bytes(pal)


@dataclass
class Bundle:
    head: bytes  # everything up to the first block
    names: list[str]
    blocks: list[bytes]  # block header + stream, per texture

    @classmethod
    def parse(cls, data: bytes) -> Bundle:
        if len(data) < 16:
            raise RomError("Bundle too small")
        count, _, table, _ = struct.unpack_from("<IIII", data)
        if not 1 <= count <= 4096 or table + 4 * count > len(data):
            raise RomError("Not a texture bundle")
        name_ptrs = struct.unpack_from(f"<{count}I", data, 16)
        offsets = struct.unpack_from(f"<{count}I", data, table)
        if any(o >= len(data) for o in offsets) or any(
            a >= b for a, b in zip(offsets, offsets[1:], strict=False)
        ):
            raise RomError("Not a texture bundle")
        names = [data[p : p + 16].split(b"\0")[0].decode("ascii", "replace") for p in name_ptrs]
        blocks = []
        for k, off in enumerate(offsets):
            end = offsets[k + 1] if k + 1 < count else len(data)
            blocks.append(data[off:end])
        return cls(data[: offsets[0]], names, blocks)

    def texture(self, name: str) -> Texture:
        _h, payload = unwrap(self.blocks[self.names.index(name)])
        return Texture.parse(payload)

    def set_texture(self, name: str, tex: Texture) -> None:
        k = self.names.index(name)
        header, _ = unwrap(self.blocks[k])
        self.blocks[k] = wrap(header, tex.build())

    def build(self) -> bytes:
        head = bytearray(self.head)
        table = struct.unpack_from("<I", head, 8)[0]
        pos = len(head)
        body = bytearray()
        for k, block in enumerate(self.blocks):
            struct.pack_into("<I", head, table + 4 * k, pos + len(body))
            body += block
            body += b"\0" * (-len(block) % 16)
        return bytes(head) + bytes(body)


def inflate_all(data: bytes) -> list[bytes]:
    """Every zlib stream in a blob, for callers probing unknown bundles."""
    out: list[bytes] = []
    pos = 0
    while True:
        pos = data.find(b"x\xda", pos)
        if pos < 0:
            return out
        try:
            out.append(zlib.decompress(data[pos:]))
        except zlib.error:
            pass
        pos += 2
