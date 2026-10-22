"""The emblem atlases of PES 2013 (PS2): every flag and club crest as an 8-bit
indexed image, one zlib stream each, drawn through a palette the atlas shares.

`0_TEXT.AFS` entries 604 (64x64) and 600 (32x32) hold three tables each:
national flags, club crests (record id = club index) and a few extras; entries
606 and 602 repeat the club table alone. Each table ends with its own 1024-byte
palette (256 x RGBA, alpha 0x80 = opaque, entry 0 transparent).

Layout of an atlas payload (after the entry's 32-byte block header):

    0x00  u32 image bytes | u16 ?, u8 w, u8 h, u8 w, u8 h, u32 1|1
    0x10  (u32 count, u32 offset) x 3 tables
    0x28  records: {u32 id, u32 size, u32 offset, u32 palette offset} per table
          (the first record of a table carries 0x04000000 in `size` and the
          table's palette offset; the others carry 0)
          zlib streams, 4-byte aligned
          palettes, one per table

A rebuilt atlas keeps every stream it did not replace byte for byte.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

from ...core.errors import RomError

RGB = tuple[int, int, int]

TABLE_COUNT = 3
#: Table index of the club crests inside the full atlases.
CLUB_TABLE = 1
PALETTE_BYTES = 1024
SIZE_FLAG = 0x04000000


@dataclass
class Record:
    ident: int
    flags: int
    stream: bytes


@dataclass
class Atlas:
    header: bytes  # the first 0x10 bytes
    side: int  # 32 or 64
    tables: list[list[Record]]
    palettes: list[bytes]
    extra: list[int] = field(default_factory=list)  # (count, offset) pairs beyond the data

    @classmethod
    def parse(cls, data: bytes) -> Atlas:
        if len(data) < 0x28:
            raise RomError("Emblem atlas too small")
        side = data[6]
        if side not in (32, 64) or data[7] != side:
            raise RomError("Not an emblem atlas")
        counts = struct.unpack("<6I", data[0x10:0x28])
        tables: list[list[Record]] = []
        palettes: list[bytes] = []
        for t in range(TABLE_COUNT):
            count, off = counts[2 * t], counts[2 * t + 1]
            recs: list[Record] = []
            pal_off = 0
            for k in range(count):
                ident, size, stream_off, pal = struct.unpack(
                    "<IIII", data[off + 16 * k : off + 16 * k + 16]
                )
                flags = size & ~0xFFFFFF
                size &= 0xFFFFFF
                if k == 0:
                    pal_off = pal
                recs.append(Record(ident, flags, data[stream_off : stream_off + size]))
            tables.append(recs)
            palettes.append(data[pal_off : pal_off + PALETTE_BYTES] if count else b"")
        return cls(data[:0x10], side, tables, palettes)

    def image(self, table: int, ident: int) -> bytes | None:
        for rec in self.tables[table]:
            if rec.ident == ident:
                return zlib.decompress(rec.stream)
        return None

    def set_image(self, table: int, ident: int, indices: bytes) -> None:
        if len(indices) != self.side * self.side:
            raise RomError(f"Emblem must be {self.side}x{self.side} indices")
        for rec in self.tables[table]:
            if rec.ident == ident:
                rec.stream = zlib.compress(indices, 9)
                return
        raise RomError(f"Emblem atlas table {table} has no record {ident}")

    def build(self) -> bytes:
        out = bytearray(self.header)
        table_off = 0x28
        offsets = []
        for recs in self.tables:
            offsets.append((len(recs), table_off if recs else 0))
            table_off += 16 * len(recs)
        out += struct.pack("<6I", *(v for pair in offsets for v in pair))
        data_off = table_off
        body = bytearray()
        stream_offs: list[list[int]] = []
        for recs in self.tables:
            offs = []
            for rec in recs:
                offs.append(data_off + len(body))
                body += rec.stream
                body += b"\0" * (-len(rec.stream) % 4)
            stream_offs.append(offs)
        pal_offs = []
        for pal in self.palettes:
            pal_offs.append(data_off + len(body) if pal else 0)
            body += pal
        for t, recs in enumerate(self.tables):
            for k, rec in enumerate(recs):
                out += struct.pack(
                    "<IIII",
                    rec.ident,
                    rec.flags | len(rec.stream),
                    stream_offs[t][k],
                    pal_offs[t] if k == 0 else 0,
                )
        assert len(out) == data_off
        return bytes(out) + bytes(body)


def used_indices(atlas: Atlas, table: int) -> set[int]:
    """Every palette index some image of the table draws with."""
    used: set[int] = set()
    for rec in atlas.tables[table]:
        used.update(zlib.decompress(rec.stream))
    return used


def paint_logos(atlas: Atlas, table: int, logos: dict[int, bytes]) -> int:
    """Draw each PNG as the record `ident`'s image, with colours of their own.

    The table's palette is shared by every crest in it, so the new logos get
    the palette entries no remaining crest uses (half the palette on the real
    disc): one median-cut palette over all the logos, written into those free
    entries, and each logo mapped to it. Returns the logos drawn.
    """

    targets = {
        ident: png
        for ident, png in logos.items()
        if any(r.ident == ident for r in atlas.tables[table])
    }
    if not targets:
        return 0
    keep = {rec.ident for rec in atlas.tables[table]} - set(targets)
    used: set[int] = {0}
    for rec in atlas.tables[table]:
        if rec.ident in keep:
            used.update(zlib.decompress(rec.stream))
    free = sorted(set(range(1, 256)) - used)
    if not free:
        raise RomError("The emblem palette has no free entries")
    fitted = {ident: _fit(png, atlas.side) for ident, png in targets.items()}
    opaque = [px[:3] for canvas in fitted.values() for px in canvas.getdata() if px[3] >= 128]
    colours = _median_cut(opaque, min(len(free), 128))
    palette = bytearray(atlas.palettes[table])
    for idx, (r, g, b) in zip(free, colours, strict=False):
        palette[4 * idx : 4 * idx + 4] = bytes((r, g, b, 0x80))
    atlas.palettes[table] = bytes(palette)
    lookup = {free[i]: colours[i] for i in range(len(colours))}
    for ident, canvas in fitted.items():
        atlas.set_image(table, ident, _map_to(list(canvas.getdata()), lookup))
    return len(fitted)


def _fit(png: bytes, side: int):  # type: ignore[no-untyped-def]
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(png)).convert("RGBA")
    img.thumbnail((side, side), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    return canvas


def _median_cut(pixels: list[tuple[int, int, int]], count: int) -> list[RGB]:
    """Up to `count` representative colours of `pixels` (Pillow's median cut)."""
    from PIL import Image

    if not pixels:
        return []
    strip = Image.new("RGB", (len(pixels), 1))
    strip.putdata(pixels)
    q = strip.quantize(colors=count, method=Image.Quantize.MEDIANCUT)
    flat = q.getpalette() or []
    n = min(count, len(flat) // 3)
    return [(flat[3 * i], flat[3 * i + 1], flat[3 * i + 2]) for i in range(n)]


def _map_to(pixels: list[tuple[int, int, int, int]], lookup: dict[int, RGB]) -> bytes:
    """Nearest of the given palette entries per pixel; transparent pixels take 0."""
    indices = list(lookup)
    colours = [lookup[i] for i in indices]
    cache: dict[tuple[int, int, int], int] = {}
    out = bytearray()
    for r, g, b, a in pixels:
        if a < 128:
            out.append(0)
            continue
        key = (r, g, b)
        idx = cache.get(key)
        if idx is None:
            best = min(
                range(len(colours)),
                key=lambda i: (
                    (colours[i][0] - r) ** 2 + (colours[i][1] - g) ** 2 + (colours[i][2] - b) ** 2
                ),
            )
            idx = indices[best]
            cache[key] = idx
        out.append(idx)
    return bytes(out)


def quantise(pixels: list[tuple[int, int, int, int]], palette: bytes) -> bytes:
    """Nearest palette index per RGBA pixel; transparent pixels take index 0."""
    colours = [
        (palette[i * 4], palette[i * 4 + 1], palette[i * 4 + 2])
        for i in range(256)
        if palette[i * 4 + 3] >= 0x80
    ]
    indices = [i for i in range(256) if palette[i * 4 + 3] >= 0x80]
    cache: dict[tuple[int, int, int], int] = {}
    out = bytearray()
    for r, g, b, a in pixels:
        if a < 128:
            out.append(0)
            continue
        key = (r, g, b)
        idx = cache.get(key)
        if idx is None:
            best = min(
                range(len(colours)),
                key=lambda i: (
                    (colours[i][0] - r) ** 2 + (colours[i][1] - g) ** 2 + (colours[i][2] - b) ** 2
                ),
            )
            idx = indices[best]
            cache[key] = idx
        out.append(idx)
    return bytes(out)


def render_logo(png: bytes, side: int, palette: bytes) -> bytes:
    """A PNG fitted into a side x side square, as atlas indices. Needs Pillow."""
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(png)).convert("RGBA")
    img.thumbnail((side, side), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    return quantise(list(canvas.getdata()), palette)
