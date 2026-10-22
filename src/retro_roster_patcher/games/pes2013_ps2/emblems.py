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
from collections import Counter
from dataclasses import dataclass, field

from ...core.errors import RomError
from .afs import deflate
from .textures import clut_swizzle

RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]

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


#: How the shared palette is built and used (see `paint_logos`).
NEUTRALS = (0, 255, 36, 72, 108, 146, 182, 218)  # a grey ramp, always present
DOMINANT = 2  # dominant colours each new crest keeps exactly
COVER = 0.04  # a dominant colour covers at least this share of its crest
BOXES = 8  # the crest's own median cut, whose box modes are its candidates
MERGE = 6.0  # colours closer than this (Lab dE) are one palette entry
CHROMA = 9.0  # below this Lab chroma a colour is grey, and greys stay grey
DESPECKLE = 20.0  # a lone pixel joins a neighbour entry this close to its colour
KEY_SHIFT = 2  # the mapping cache ignores this many low bits per channel


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
    the flags' colours (seen on the real disc). So one palette is built for
    the new logos and every image that stays, in every table, and every
    image is drawn through it: a grey ramp, then each new crest's dominant
    colours kept exactly (merged when alike, the closest pairs merged further
    when they outnumber the room), then a median cut over whatever those do
    not cover. Pixels take the nearest entry in CIE Lab, and a grey pixel may
    only take a grey entry, so a black-and-white crest never picks up
    coloured specks; a lone pixel then joins a neighbour's entry when that is
    close to its own colour. Index 0 stays transparent, a 64x64 atlas keeps
    to the 128 entries its images ever index, and `colours` caps the palette
    lower still, which makes the streams smaller when the rebuilt atlas
    outgrows its room. With `palette` (1024 bytes, as stored on the disc) no
    palette is built: every image is drawn through that one, so the atlases
    of both sizes agree index for index. Returns the logos drawn.
    """
    targets = {
        ident: png
        for ident, png in logos.items()
        if any(r.ident == ident for r in atlas.tables[table])
    }
    if not targets:
        return 0
    fitted = {ident: fit_logo(png, atlas.side) for ident, png in targets.items()}
    kept: list[tuple[Record, list[RGBA]]] = []
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
    if palette is None:
        new_pixels = [list(canvas.getdata()) for canvas in fitted.values()]
        chosen = _shared_palette(new_pixels, [pixels for _rec, pixels in kept], entries - 1)
        linear = bytearray(1024)
        linear[0:4] = bytes((255, 255, 255, 0))
        for idx, (r, g, b) in enumerate(chosen, start=1):
            linear[4 * idx : 4 * idx + 4] = bytes((r, g, b, 0x80))
        palette = clut_swizzle(bytes(linear))
    else:
        linear = bytearray(clut_swizzle(palette))
    mapper = Mapper(
        [
            (idx, (linear[4 * idx], linear[4 * idx + 1], linear[4 * idx + 2]))
            for idx in range(1, 256)
            if linear[4 * idx + 3]
        ]
    )
    for t in range(len(atlas.palettes)):
        if atlas.palettes[t]:
            atlas.palettes[t] = bytes(palette)
    for rec, pixels in kept:
        rec.stream = deflate(mapper.map(pixels))
    for ident, canvas in fitted.items():
        pixels = list(canvas.getdata())
        indices = mapper.despeckle(mapper.map(pixels), pixels, atlas.side)
        atlas.set_image(table, ident, indices)
    return len(fitted)


def _shared_palette(new: list[list[RGBA]], kept: list[list[RGBA]], room: int) -> list[RGB]:
    """Up to `room` colours for the new crests and the images that stay."""
    merge2 = MERGE * MERGE
    anchors: list[tuple[RGB, int]] = [((v, v, v), 1 << 30) for v in NEUTRALS]
    for pixels in new:
        for rgb, weight in _dominant([px[:3] for px in pixels if px[3] >= 128]):
            for i, (a, w) in enumerate(anchors):
                if _de2(a, rgb) <= merge2:
                    anchors[i] = (a, w + weight)
                    break
            else:
                anchors.append((rgb, weight))
    while len(anchors) > room:  # the closest pair becomes the heavier one
        best = min(
            (
                (_de2(anchors[i][0], anchors[j][0]), i, j)
                for i in range(len(anchors))
                for j in range(i + 1, len(anchors))
            ),
        )
        _d, i, j = best
        if anchors[j][1] > anchors[i][1]:
            i, j = j, i
        anchors[i] = (anchors[i][0], anchors[i][1] + anchors[j][1])
        del anchors[j]
    chosen = [rgb for rgb, _w in anchors]
    probe = Mapper(list(enumerate(chosen)))
    rest = [
        px[:3]
        for pixels in new + kept
        for px in pixels
        if px[3] >= 128 and _de2(px[:3], chosen[probe.index(px[:3])]) > merge2
    ]
    return _fill(chosen, rest, room - len(chosen))


def _dominant(pixels: list[RGB]) -> list[tuple[RGB, int]]:
    """(colour, pixels) of a crest's dominant colours: the mode of each box of
    its own median cut, for boxes covering at least COVER of its pixels."""
    if not pixels:
        return []
    from PIL import Image

    strip = Image.new("RGB", (len(pixels), 1))
    strip.putdata(pixels)
    boxes_of = list(strip.quantize(colors=BOXES, method=Image.Quantize.MEDIANCUT).getdata())
    boxes: dict[int, Counter[RGB]] = {}
    for px, k in zip(pixels, boxes_of, strict=True):
        boxes.setdefault(k, Counter())[px] += 1
    found = [
        (box.most_common(1)[0][0], sum(box.values()))
        for box in boxes.values()
        if sum(box.values()) >= COVER * len(pixels)
    ]
    found.sort(key=lambda t: -t[1])
    return found[:DOMINANT]


def _fill(chosen: list[RGB], pixels: list[RGB], room: int) -> list[RGB]:
    """Extend `chosen` by up to `room` colours for `pixels`: candidates from a
    median cut twice as fine, taken greedily by (pixels covered) x (distance
    from the palette so far)^2, so the room goes where it buys most."""
    cands = _median_cut(pixels, min(256, 2 * room)) if room > 0 else []
    gaps = [min(_de2(rgb, c) for c in chosen) for rgb, _n in cands]
    while room > 0 and cands:
        k = max(range(len(cands)), key=lambda i: cands[i][1] * gaps[i])
        if gaps[k] <= 0:
            break
        rgb = cands.pop(k)[0]
        gaps.pop(k)
        chosen.append(rgb)
        room -= 1
        gaps = [min(g, _de2(c, rgb)) for (c, _n), g in zip(cands, gaps, strict=True)]
    return chosen


class Mapper:
    """Nearest palette entry in Lab, greys kept to grey entries."""

    def __init__(self, entries: list[tuple[int, RGB]]) -> None:
        self.all = [(i, _lab(rgb)) for i, rgb in entries]
        self.grey = [(i, lab) for i, lab in self.all if (lab[1] ** 2 + lab[2] ** 2) ** 0.5 < CHROMA]
        self.cache: dict[tuple[int, int, int], int] = {}

    def index(self, rgb: RGB) -> int:
        key = (rgb[0] >> KEY_SHIFT, rgb[1] >> KEY_SHIFT, rgb[2] >> KEY_SHIFT)
        idx = self.cache.get(key)
        if idx is None:
            light, a, b = _lab(rgb)
            pool = self.grey if (a * a + b * b) ** 0.5 < CHROMA and self.grey else self.all
            idx = min(
                pool, key=lambda e: (light - e[1][0]) ** 2 + (a - e[1][1]) ** 2 + (b - e[1][2]) ** 2
            )[0]
            self.cache[key] = idx
        return idx

    def map(self, pixels: list[RGBA]) -> bytes:
        return bytes(self.index((r, g, b)) if a >= 128 else 0 for r, g, b, a in pixels)

    def despeckle(self, indices: bytes, pixels: list[RGBA], side: int) -> bytes:
        """A pixel whose entry matches none of its 4 neighbours takes the
        neighbour entry nearest its own colour, when that is within DESPECKLE;
        a grey pixel only joins a grey entry."""
        labs = dict(self.all)
        grey = {i for i, _lab_ in self.grey}
        out = bytearray(indices)
        tol2 = DESPECKLE * DESPECKLE
        for y in range(side):
            for x in range(side):
                i = y * side + x
                v = indices[i]
                if not v:
                    continue
                near = {
                    indices[j]
                    for j in (i - 1 if x else -1, i + 1 if x < side - 1 else -1, i - side, i + side)
                    if 0 <= j < len(indices)
                }
                near.discard(0)
                if v in near or not near:
                    continue
                light, a, b = _lab(pixels[i][:3])
                if (a * a + b * b) ** 0.5 < CHROMA:
                    near &= grey
                best: tuple[float, int] | None = None
                for k in near:
                    l2, a2, b2 = labs[k]
                    d = (light - l2) ** 2 + (a - a2) ** 2 + (b - b2) ** 2
                    if d <= tol2 and (best is None or d < best[0]):
                        best = (d, k)
                if best is not None:
                    out[i] = best[1]
        return bytes(out)


def _lab(rgb: RGB) -> tuple[float, float, float]:
    """CIE Lab (D65) of an sRGB colour, cached: distances that match the eye."""
    found = _LAB.get(rgb)
    if found is None:

        def lin(c: float) -> float:
            c /= 255.0
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

        r, g, b = (lin(c) for c in rgb)
        x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
        y = 0.2126 * r + 0.7152 * g + 0.0722 * b
        z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

        def f(t: float) -> float:
            return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

        fx, fy, fz = f(x), f(y), f(z)
        found = _LAB[rgb] = (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))
    return found


_LAB: dict[RGB, tuple[float, float, float]] = {}


def _de2(a: RGB, b: RGB) -> float:
    la, lb = _lab(a), _lab(b)
    return (la[0] - lb[0]) ** 2 + (la[1] - lb[1]) ** 2 + (la[2] - lb[2]) ** 2


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


def _through(indices: bytes, palette: bytes) -> list[RGBA]:
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


def _median_cut(pixels: list[RGB], count: int) -> list[tuple[RGB, int]]:
    """(colour, pixels) per box of a median cut of `pixels` into `count` boxes."""
    from PIL import Image

    if not pixels or count <= 0:
        return []
    strip = Image.new("RGB", (len(pixels), 1))
    strip.putdata(pixels)
    q = strip.quantize(colors=count, method=Image.Quantize.MEDIANCUT)
    flat = q.getpalette() or []
    hist = q.histogram()
    n = min(count, len(flat) // 3)
    return [((flat[3 * i], flat[3 * i + 1], flat[3 * i + 2]), hist[i]) for i in range(n) if hist[i]]


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
