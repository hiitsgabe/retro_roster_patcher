"""The emblem atlases of PES 2013 (PS2): every flag and club crest as an 8-bit
indexed image, one zlib stream each, drawn through a palette the atlas shares.

`0_TEXT.AFS` entries 604 (64x64) and 600 (32x32) hold three tables each:
national flags, club crests (record id = club index) and a few extras; entries
606 and 602 repeat the club table alone. Each table ends with a 1024-byte
palette (256 x RGBA, alpha 0x80 = opaque, entry 0 transparent), but the three
hold the same colours: the atlas is drawn through one palette, so a repaint
rewrites every table's. The 64x64 images only ever index entries 0..127.

The palette is stored the way the GS wants its CLUT (CSM1): within every 32
entries, 8..15 and 16..23 are swapped, like the UI textures' palettes. A
palette written in index order comes out with half its colours exchanged
(a red crest turned blue on the real disc), so `Atlas.colours` gives the
linear view and `paint_logos` swizzles what it writes.

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
from .afs import deflate
from .textures import clut_swizzle

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

    def colours(self, table: int) -> bytes:
        """The table's palette in index order (the disc keeps it GS-swizzled)."""
        return clut_swizzle(self.palettes[table]) if self.palettes[table] else b""

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
                rec.stream = deflate(indices)
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


def paint_logos(
    atlas: Atlas,
    table: int,
    logos: dict[int, bytes],
    colours: int | None = None,
    palette: bytes | None = None,
) -> int:
    """Draw each PNG as the record `ident`'s image, with colours of their own.

    The atlas is one texture with one palette: every table's palette holds
    the same colours on the disc, and the game draws the club crests through
    the first one, so a palette written for the crests alone leaves them in
    the flags' colours (seen on the real disc). So one median-cut palette is
    built over the new logos and every image that stays, in every table, and
    every image is drawn through it. Index 0 stays transparent, and a 64x64
    atlas keeps to the 128 entries its images ever index; `colours` caps the
    palette lower still, which makes the streams smaller when the rebuilt
    atlas outgrows its room. With `palette` (1024 bytes, as stored on the
    disc) no palette is built: every image is drawn through that one, so the
    atlases of both sizes agree index for index. Returns the logos drawn.
    """
    targets = {
        ident: png
        for ident, png in logos.items()
        if any(r.ident == ident for r in atlas.tables[table])
    }
    if not targets:
        return 0
    fitted = {ident: fit_logo(png, atlas.side) for ident, png in targets.items()}
    kept: list[tuple[Record, list[tuple[int, int, int, int]]]] = []
    highest = 0
    for t, recs in enumerate(atlas.tables):
        for rec in recs:
            indices = zlib.decompress(rec.stream)
            highest = max(highest, max(indices))
            if t == table and rec.ident in targets:
                continue
            kept.append((rec, _through(indices, atlas.colours(t))))
    entries = 128 if highest < 128 else 256
    if colours is not None:
        entries = min(entries, colours + 1)
    opaque = [px[:3] for canvas in fitted.values() for px in canvas.getdata() if px[3] >= 128]
    opaque += [px[:3] for _rec, pixels in kept for px in pixels if px[3] >= 128]
    lookup: dict[int, RGB] = {}
    if palette is None:
        chosen = _median_cut(opaque, entries - 1)
        linear = bytearray(1024)
        linear[0:4] = bytes((255, 255, 255, 0))
        for idx, (r, g, b) in enumerate(chosen, start=1):
            linear[4 * idx : 4 * idx + 4] = bytes((r, g, b, 0x80))
            lookup[idx] = (r, g, b)
        palette = clut_swizzle(bytes(linear))
    else:
        linear = bytearray(clut_swizzle(palette))
        for idx in range(1, 256):
            if linear[4 * idx + 3]:
                lookup[idx] = (linear[4 * idx], linear[4 * idx + 1], linear[4 * idx + 2])
    for t in range(len(atlas.palettes)):
        if atlas.palettes[t]:
            atlas.palettes[t] = bytes(palette)
    for rec, pixels in kept:
        rec.stream = deflate(_map_to(pixels, lookup))
    for ident, canvas in fitted.items():
        atlas.set_image(table, ident, _map_to(list(canvas.getdata()), lookup))
    return len(fitted)


def copy_club_table(source: Atlas, target: Atlas) -> None:
    """Give `target` the club crests and palette of `source` (same size), so
    the two atlases of a size agree on every index."""
    streams = {rec.ident: rec.stream for rec in source.tables[CLUB_TABLE]}
    for rec in target.tables[CLUB_TABLE]:
        if rec.ident in streams:
            rec.stream = streams[rec.ident]
    for t in range(len(target.palettes)):
        if target.palettes[t]:
            target.palettes[t] = source.palettes[CLUB_TABLE]


def _through(indices: bytes, palette: bytes) -> list[tuple[int, int, int, int]]:
    """An image's pixels as RGBA through a palette in index order (alpha 0x80 = opaque)."""
    return [
        (palette[4 * v], palette[4 * v + 1], palette[4 * v + 2], min(255, palette[4 * v + 3] * 2))
        for v in indices
    ]


def fit_logo(png: bytes, side: int):  # type: ignore[no-untyped-def]
    """A PNG as a side x side RGBA canvas, the way the disc's own crests are
    drawn: the transparent margin trimmed so the emblem fills the square, and
    the downscaled image sharpened, since a plain resample to 32 pixels turns
    outlines and lettering to mush next to the hand-pixelled originals.
    Needs Pillow."""
    import io

    from PIL import Image, ImageFilter

    img = Image.open(io.BytesIO(png)).convert("RGBA")
    bbox = img.getchannel("A").point(lambda a: 255 if a >= 32 else 0).getbbox()
    if bbox:
        img = img.crop(bbox)
    scale = side / max(img.width, img.height)  # up or down: the emblem fills the square
    img = img.resize(
        (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    return canvas.filter(ImageFilter.UnsharpMask(radius=1, percent=150, threshold=0))


_fit = fit_logo


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
    return quantise(list(fit_logo(png, side).getdata()), palette)
