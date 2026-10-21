"""Replaceable screen art: the title logo, the main-menu art and the boot screens.

Each *slot* is a texture page the game draws a screen's art from: a few LZSS
image blocks in one 2D data file, laid out on a canvas the way they sit in
VRAM, drawn through one or more CLUTs. `export_slot` decodes a slot to a PNG
with exactly that layout, so the art can be edited in place; `import_slot`
takes a PNG back, quantises it to each palette group's colour budget, writes
the indices into the blocks and the colours into the CLUT records.

Pieces are the rectangles the game's sprites actually draw, each with the CLUT
it draws through (read off the running game's primitive lists). Pixels of the
canvas that no piece covers are left as they are on import.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from ...core.errors import RomError
from .flag_atlas import (
    KIND_IMAGE,
    AtlasReport,
    BlockEdit,
    RawEdit,
    lz_compress,
    lz_decompress,
    read_data_file,
    rewrite_blocks,
)

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class Block:
    """One 32x128-halfword image record and where it sits on the canvas (pixels)."""

    x: int
    y: int
    cx: int
    cy: int


@dataclass(frozen=True)
class Piece:
    """A canvas rectangle (pixels) drawn through palette group `group`."""

    x: int
    y: int
    w: int
    h: int
    group: int
    #: cleared to transparent on import instead of taking pixels from the PNG
    #: (the menu ball's reflection: pointless under a user-supplied picture).
    clear: bool = False


@dataclass(frozen=True)
class Slot:
    name: str
    file: str
    bpp: int
    width: int
    height: int
    blocks: tuple[Block, ...]
    pieces: tuple[Piece, ...]
    #: per group, the CLUT records that receive the group's palette; the first
    #: is also the one the export reads. A ref is (file, x, y) of the record,
    #: or (file, x, y, entry) when the palette is one of several in a wider
    #: CLUT row: `entry` is its first colour's index within the record.
    cluts: tuple[tuple[tuple[int | str, ...], ...], ...]


SLOTS: dict[str, Slot] = {
    "title": Slot(
        name="title",
        file="TITLE.BIN",
        bpp=4,
        width=256,
        height=256,
        blocks=(Block(960, 0, 0, 0), Block(992, 0, 128, 0), Block(960, 128, 0, 128)),
        pieces=(Piece(0, 0, 128, 128, 0), Piece(128, 0, 128, 128, 0), Piece(0, 128, 128, 128, 0)),
        cluts=((("TITLE.BIN", 0, 491), ("TITLE.BIN", 16, 491)),),
    ),
    "menu": Slot(
        name="menu",
        file="DATSEL.BIN",
        bpp=4,
        width=256,
        height=256,
        blocks=(
            Block(384, 0, 0, 0),
            Block(416, 0, 128, 0),
            Block(384, 128, 0, 128),
            Block(416, 128, 128, 128),
        ),
        pieces=(
            Piece(0, 0, 128, 128, 0),  # the ball
            Piece(128, 0, 112, 56, 0, clear=True),  # its reflection, dropped on import
            Piece(128, 56, 128, 48, 1),  # "Winning Eleven" banner
            Piece(128, 104, 120, 24, 2),  # "2002"
            Piece(128, 128, 128, 128, 3),  # glow, lower right
            Piece(0, 152, 128, 56, 3),  # glow, upper
            Piece(0, 128, 128, 24, 3),  # glow strip
        ),
        cluts=(
            (("DAT2D.BIN", 192, 504),),
            (("DAT2D.BIN", 240, 504),),
            (("DAT2D.BIN", 128, 502),),
            (("DAT2D.BIN", 112, 502),),
        ),
    ),
    "boot": Slot(
        name="boot",
        file="LOGO.BIN",
        bpp=4,
        width=768,
        height=256,
        # Three texture pages side by side: the licence text (768..831), the
        # JFA screen (832..895) and the KONAMI logo (960..1023, rows 256+).
        blocks=(
            Block(768, 0, 0, 0),
            Block(800, 0, 128, 0),
            Block(832, 0, 256, 0),
            Block(864, 0, 384, 0),
            Block(832, 128, 256, 128),
            Block(864, 128, 384, 128),
            Block(960, 256, 512, 0),
            Block(992, 256, 640, 0),
            Block(960, 384, 512, 128),
        ),
        pieces=(
            Piece(0, 0, 128, 128, 0),  # licence text, left column
            Piece(128, 0, 128, 128, 0),  # licence text, right column
            Piece(256, 0, 128, 128, 1),  # JFA screen: frame strips, emblem
            Piece(384, 0, 128, 128, 1),  # JFA screen: emblem, caption
            Piece(256, 128, 128, 128, 1),
            Piece(384, 128, 128, 128, 1),
            Piece(512, 0, 72, 128, 2),  # KONAMI lettering
            Piece(600, 64, 16, 32, 3),
            Piece(616, 0, 24, 128, 3),
            Piece(640, 0, 128, 128, 4),  # KONAMI logo, upper half
            Piece(512, 128, 128, 128, 4),  # KONAMI logo, lower half
        ),
        # One 256-entry CLUT row holds every palette; each group is 16 of them.
        cluts=(
            (("LOGO.BIN", 0, 494, 32),),
            (("LOGO.BIN", 0, 494, 16),),
            (("LOGO.BIN", 0, 494, 112),),
            (("LOGO.BIN", 0, 494, 128),),
            (("LOGO.BIN", 0, 494, 144),),
        ),
    ),
}

SLOT_NAMES: tuple[str, ...] = tuple(SLOTS)


def _bgr555(c: RGB) -> int:
    r, g, b = (v >> 3 for v in c)
    v = r | (g << 5) | (b << 10)
    # 0x0000 is transparent on a sprite; keep true black opaque.
    return v if v else 0x8000


def _rgb(v: int) -> RGB:
    return ((v & 31) << 3, ((v >> 5) & 31) << 3, ((v >> 10) & 31) << 3)


class _Canvas:
    """Index values per pixel for a slot, decoded from its blocks."""

    def __init__(self, slot: Slot):
        self.slot = slot
        self.idx = bytearray(slot.width * slot.height)
        self.present: set[tuple[int, int]] = set()

    def get(self, x: int, y: int) -> int:
        return self.idx[y * self.slot.width + x]

    def set(self, x: int, y: int, v: int) -> None:
        self.idx[y * self.slot.width + x] = v

    def load_block(self, block: Block, raw: bytes) -> None:
        for row in range(128):
            line = raw[row * 64 : (row + 1) * 64]
            for i, byte in enumerate(line):
                if self.slot.bpp == 4:
                    self.set(block.cx + 2 * i, block.cy + row, byte & 0xF)
                    self.set(block.cx + 2 * i + 1, block.cy + row, byte >> 4)
                else:
                    self.set(block.cx + i, block.cy + row, byte)
        self.present.add((block.x, block.y))

    def block_bytes(self, block: Block) -> bytes:
        out = bytearray()
        for row in range(128):
            if self.slot.bpp == 4:
                for i in range(64):
                    lo = self.get(block.cx + 2 * i, block.cy + row)
                    hi = self.get(block.cx + 2 * i + 1, block.cy + row)
                    out.append(lo | (hi << 4))
            else:
                for i in range(64):
                    out.append(self.get(block.cx + i, block.cy + row))
        return bytes(out)


def _clut_entry(ref: tuple[int | str, ...]) -> int:
    """The first colour index a CLUT ref addresses within its record."""
    return int(ref[3]) if len(ref) > 3 else 0


def _read_clut(image_path: str, ref: tuple[int | str, ...], entries: int) -> list[RGB]:
    file, x, y = str(ref[0]), int(ref[1]), int(ref[2])
    parsed, _entry = read_data_file(image_path, file)
    for rec in parsed.records():
        if rec.kind & 0xFF != KIND_IMAGE and (rec.x, rec.y) == (x, y):
            off = rec.ptr - parsed.base + 2 * _clut_entry(ref)
            raw = parsed.data[off : off + 2 * entries]
            return [_rgb(int.from_bytes(raw[i : i + 2], "little")) for i in range(0, len(raw), 2)]
    raise RomError(f"{file} has no CLUT record at ({x},{y})")


def _load(image_path: str, slot: Slot) -> tuple[_Canvas, dict[tuple[int, int], bytes]]:
    parsed, _entry = read_data_file(image_path, slot.file)
    by_pos = {(r.x, r.y): r for r in parsed.records() if r.kind & 0xFF == KIND_IMAGE}
    canvas = _Canvas(slot)
    originals: dict[tuple[int, int], bytes] = {}
    for block in slot.blocks:
        rec = by_pos.get((block.x, block.y))
        if rec is None:
            continue
        raw, _ = lz_decompress(parsed.data, rec.ptr - parsed.base)
        canvas.load_block(block, raw)
        originals[(block.x, block.y)] = raw
    if not originals:
        raise RomError(f"{slot.file} holds none of the {slot.name} blocks")
    return canvas, originals


def export_slot(image_path: str, slot: Slot) -> bytes:
    """The slot's art as PNG bytes, each piece through its own palette."""
    from PIL import Image

    canvas, _originals = _load(image_path, slot)
    entries = 16 if slot.bpp == 4 else 256
    palettes = [_read_clut(image_path, group[0], entries) for group in slot.cluts]
    pixels: list[tuple[int, int, int, int]] = [(0, 0, 0, 0)] * (slot.width * slot.height)
    for piece in slot.pieces:
        pal = palettes[piece.group]
        for y in range(piece.y, piece.y + piece.h):
            for x in range(piece.x, piece.x + piece.w):
                v = canvas.get(x, y)
                if v:
                    pixels[y * slot.width + x] = (*pal[v], 255)
    img = Image.new("RGBA", (slot.width, slot.height))
    img.putdata(pixels)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _quantise(pixels: list[RGB], colours: int) -> list[RGB]:
    from PIL import Image

    if not pixels:
        return [(0, 0, 0)] * colours
    strip = Image.new("RGB", (len(pixels), 1))
    strip.putdata(pixels)
    q = strip.quantize(colors=colours, method=Image.Quantize.MEDIANCUT)
    flat = q.getpalette() or []
    pal = [(flat[i], flat[i + 1], flat[i + 2]) for i in range(0, min(len(flat), colours * 3), 3)]
    return pal + [(0, 0, 0)] * (colours - len(pal))


def _nearest_map(pal: list[RGB]):
    from PIL import Image

    lookup = Image.new("P", (1, 1))
    n = len(pal)
    lookup.putpalette([c for i in range(256) for c in pal[i % n]])

    def snap(img):
        snapped = img.convert("RGB").quantize(palette=lookup, dither=Image.Dither.NONE)
        return [v % n for v in snapped.tobytes()]

    return snap


def import_slot(image_path: str, slot: Slot, png: bytes) -> tuple[list[BlockEdit], list[RawEdit]]:
    """Edits that put `png` into the slot: one per changed block, one per CLUT."""
    from PIL import Image

    canvas, originals = _load(image_path, slot)
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    if img.size != (slot.width, slot.height):
        img = img.resize((slot.width, slot.height), Image.Resampling.LANCZOS)
    rgba = img.tobytes()
    width = slot.width

    def pixel(x: int, y: int) -> tuple[int, int, int, int]:
        o = (y * width + x) * 4
        return rgba[o], rgba[o + 1], rgba[o + 2], rgba[o + 3]

    entries = 16 if slot.bpp == 4 else 256
    raw_edits: list[RawEdit] = []
    for group, clut_refs in enumerate(slot.cluts):
        pieces = [p for p in slot.pieces if p.group == group and not p.clear]
        opaque: list[RGB] = []
        for p in pieces:
            for y in range(p.y, p.y + p.h):
                for x in range(p.x, p.x + p.w):
                    r, g, b, a = pixel(x, y)
                    if a >= 128:
                        opaque.append((r, g, b))
        pal = _quantise(opaque, entries - 1)
        snap = _nearest_map(pal)
        for p in pieces:
            sub = img.crop((p.x, p.y, p.x + p.w, p.y + p.h))
            idx = snap(sub)
            alpha = sub.getchannel("A").tobytes()
            for j in range(p.h):
                for i in range(p.w):
                    k = j * p.w + i
                    canvas.set(p.x + i, p.y + j, 0 if alpha[k] < 128 else idx[k] + 1)
        clut = b"\x00\x00" + b"".join(_bgr555(c).to_bytes(2, "little") for c in pal)
        for ref in clut_refs:
            raw_edits.append(
                RawEdit(str(ref[0]), int(ref[1]), int(ref[2]), clut, offset=2 * _clut_entry(ref))
            )
    for p in slot.pieces:
        if p.clear:
            for y in range(p.y, p.y + p.h):
                for x in range(p.x, p.x + p.w):
                    canvas.set(x, y, 0)
    block_edits: list[BlockEdit] = []
    for block in slot.blocks:
        original = originals.get((block.x, block.y))
        if original is None:
            continue
        new = canvas.block_bytes(block)
        if new != original:
            block_edits.append(BlockEdit(original, lz_compress(new), (slot.file,)))
    return block_edits, raw_edits


def apply_images(image_path: str, images: dict[str, bytes]) -> AtlasReport:
    """Write every `images[slot_name]` PNG into its slot on the disc."""
    block_edits: list[BlockEdit] = []
    raw_edits: list[RawEdit] = []
    for name, png in images.items():
        slot = SLOTS.get(name)
        if slot is None:
            raise ValueError(f"unknown image slot {name!r}; slots are {', '.join(SLOTS)}")
        blocks, raws = import_slot(image_path, slot, png)
        block_edits += blocks
        raw_edits += raws
    return rewrite_blocks(image_path, block_edits, raw_edits)
