"""Screen art: the Konami logo, the title background and the menu wallpaper.

`patch(images=...)` replaces them and `export_images` writes them out. Each is
the BG2 layer of a Mode 1 screen (4 bits per pixel) that the list loader at
$80:A07E fills from Konami-compressed streams (`konami`): a tile set, a 32x32
map, and palette rows that the screen's palette tasks load from bank $85.

A picture is stretched to its slot and gets 15 colours for each palette row
the slot may use. Each 8x8 tile takes the row that draws it best, and tiles
are deduplicated with flips. If they still need more tile slots than video
memory holds, the closest pair is merged until they fit.

Space: a stream is written where the cartridge keeps it if it fits there.
Otherwise it goes in a bank's 0xFF tail (never the name tiles' 0x17680-0x17FFF
or the flags' 0x48000-0x48A7F). Otherwise the ROM grows to 2 MiB and each slot
gets a bank of its own, $A0-$A2. A list pointer keeps bit 6 of its bank byte,
which links the list on ($80:A270). No stream crosses a 32 KiB bank, because
$80:A563 reads `[$00],Y`. The header checksum is recomputed. Mesen boots the
2 MiB ROM, and in 6000 frames of intro, menus and a match the game read
nothing from the banks a 1 MiB cartridge only mirrors.

What each screen does with its picture was measured in Mesen 2 on the retail
cartridge:

* konami: a line sweeps down the screen and reveals BG2. BG1 draws that line
  from tiles 0-5 and 16-21 of the logo's own tile set in row 3, so those tiles
  and that row are kept. The cartridge shows the logo first as a blue outline,
  with its own colours from frame 289, and then rotates colours 6-15 of rows 1
  and 2 and colours 5-15 of row 0 (a shine, $80:9EE7). A picture keeps its own
  colours throughout: the three outline entries load the final rows, and the
  rotations are pointed at rows 4-6, which nothing on that screen draws. Row 0
  colours 0 and 1 fade to white with the backdrop ($80:9EA8, until $7FFF), so
  the picture uses colours 2-15 of row 0.
* title: rows 2 and 3 start as the flat grey of the backdrop, and the grass
  appears with the white flash at frame 743 (task $81:8A68). Row 7 (the red
  brush) shows from the start, wiped in from the left by a grey box. The
  players, grey at first, show white over a picture. So a picture stays hidden
  until the flash: row 7 now starts grey too. The cartridge's tile set
  shares tiles 220-234 with the match graphics (0x49CE1), so that entry now
  loads one blank tile over tile 0. BG1 draws tile 0 over the whole screen, so
  tile 0 stays blank.
* menu: a 256x256 pattern that the menu screens scroll. It is loaded by four
  lists, in one palette row, to tile 256 on. 256 tiles ($3000-$3FFF) stayed
  intact on every menu screen visited, from the main menu to the options, the
  password entry and both team selects; the cartridge uses 50. The 2P team
  select draws it from a second copy of
  that row ($85:F47B), so that copy gets the colours with red and blue swapped,
  as the cartridge's red copy of the blue wallpaper is.
"""

from __future__ import annotations

import heapq
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...core.errors import CapabilityError, RomError
from .konami import compress_large, decompress

if TYPE_CHECKING:
    from PIL import Image


@dataclass(frozen=True)
class Guide:
    """A region of a slot's picture, in the picture's own pixels, for templates."""

    x: int
    y: int
    w: int
    h: int
    label: str


@dataclass(frozen=True)
class Stream:
    """A compressed stream a slot owns.

    `retail` and `capacity` give where the cartridge keeps it and how many
    bytes it has there. `pointers` are the 3-byte list entries that load it.
    """

    retail: int
    capacity: int
    pointers: tuple[int, ...]


@dataclass(frozen=True)
class Colours:
    """Where a palette row's colours live in the ROM.

    The colour word at `offset` is colour `first` of `row`, and the rest of the
    row follows. With `swap`, red and blue are exchanged.
    """

    offset: int
    row: int
    first: int = 1
    swap: bool = False


@dataclass(frozen=True)
class Slot:
    name: str
    size: tuple[int, int]
    description: str
    #: Where the game draws over the picture, or what of it shows; for templates.
    guides: tuple[Guide, ...]
    tiles: Stream
    tile_map: Stream
    colours: tuple[Colours, ...]
    #: Map tile number of the stream's first tile (tile 0 sits at VRAM $2000).
    first_tile: int
    #: Tile slots the picture may use, those it keeps from the current stream among them.
    budget: int
    keep: tuple[int, ...]
    #: Bit 13 of every map word, as the cartridge sets it.
    priority: int
    #: What colour 0 shows on screen (BGR555), for `render`.
    backdrop: int
    #: The bank this slot's streams take in a 2 MiB ROM.
    bank: int
    #: A list entry that must load one blank tile at $2000; 0 for none.
    blank: int = 0
    #: (file offset, cartridge bytes, patched bytes) the slot's sequence needs.
    pokes: tuple[tuple[int, bytes, bytes], ...] = ()


#: The last 3 bytes of bank $81's 0xFF tail: the title's grey row-7 palette entry.
_GREY_ROW_7 = 0xFFFD
#: LoROM tile slots from VRAM $2000 up to the next thing loaded ($5000).
_VRAM_TILES = 768
#: Colours in a palette row; colour 0 is transparent, so a picture gets 15 at most.
_ROW = 16

SLOTS: tuple[Slot, ...] = (
    Slot(
        "konami",
        (256, 224),
        "the Konami logo screen, revealed top to bottom by a sweeping line, "
        "in 44 colours (3 palette rows)",
        (
            Guide(
                0,
                0,
                256,
                224,
                "a line sweeps it in; 28 tiles stay its",
            ),
        ),
        # List $82:8054 also points at this stream (to $6000), but no code ever
        # runs that list (watched in Mesen through intro, menus and matches),
        # so whatever 0x960B0 holds after a patch is irrelevant to it.
        tiles=Stream(0x960B0, 2745, (0x10025,)),
        tile_map=Stream(0xF3487, 499, (0x1002A,)),
        colours=(Colours(0x2DAE9, 0, first=2), Colours(0x2DB07, 1), Colours(0x2DB27, 2)),
        first_tile=0,
        budget=_VRAM_TILES,
        # The sweep line (tiles 0-5, 16-21) and the sparkle at its head, four
        # 16x16 frames of tiles 6-13 and 22-29 that BG1 draws from this set.
        keep=tuple(range(0, 14)) + tuple(range(16, 30)),
        priority=0,
        backdrop=0x7FFF,
        bank=0x100000,
        pokes=(
            # Outline palettes ($81:89E6) -> the final rows 0, 1 and 2.
            (0x8DD1, b"\xa1\xda", b"\xe3\xda"),
            (0x8DD4, b"\xc3\xda", b"\x05\xdb"),
            (0x8DD7, b"\xc3\xda", b"\x25\xdb"),
            # The shine's rotations ($80:9E66, 9E71, 9EA1) -> rows 4, 5 and 6.
            (0x1E67, b"\x2c\x32", b"\x8c\x32"),
            (0x1E72, b"\x4c\x32", b"\xac\x32"),
            (0x1EA2, b"\x0a\x32", b"\xca\x32"),
        ),
    ),
    Slot(
        "title",
        (256, 224),
        "the title background under the sprite logo, shown from the white flash on, "
        "in 45 colours (3 palette rows)",
        # Measured in Mesen against this module's own render of the same ROM.
        (
            Guide(0, 0, 256, 224, "shown from the white flash"),
            Guide(20, 20, 216, 76, "ISS logo (game's)"),
            Guide(80, 110, 88, 10, "PRESS START"),
            Guide(100, 124, 52, 66, "players"),
            Guide(48, 190, 160, 26, "copyright (game's)"),
        ),
        tiles=Stream(0xBE106, 1533, (0x1017C,)),
        tile_map=Stream(0x67E6E, 380, (0x1019A,)),
        colours=(Colours(0x2DCF5, 2), Colours(0x2DCF5 + 30, 3), Colours(0x2D4FE, 7)),
        first_tile=0,
        budget=_VRAM_TILES,
        keep=(0,),
        priority=1,
        backdrop=0x739C,
        bank=0x108000,
        blank=0x10181,
        pokes=(
            # Row 7 starts grey like rows 2-3: a new entry, $85:D5F2 into row 7, in
            # the first palette task ($81:8A44) instead of $81:880C. The flash's
            # task ($81:8A68) still loads $81:880C.
            (_GREY_ROW_7, b"\xff\xff\xff", b"\xf2\xd5\x0e"),
            (0x8A4C, b"\x0c\x88", bytes((_GREY_ROW_7 & 0xFF, 0x80 | _GREY_ROW_7 >> 8 & 0x7F))),
            # The title's settings ($81:80E6) add a fixed colour to BG2 (CGADSUB
            # $02), a haze on lines 112-127 and a tint below: right for the
            # cartridge's grass horizon, a white stripe through a picture. Only
            # the title (LDY #$80E6 at $80:8E0C) loads this entry.
            (0x80E7, b"\x02", b"\x00"),
        ),
    ),
    Slot(
        "menu",
        (256, 256),
        "the wallpaper behind the menus, a 256x256 pattern they scroll, in 15 colours "
        "(1 palette row); the 2P team select shows it with red and blue swapped",
        (
            Guide(
                0,
                0,
                256,
                256,
                "repeats both ways",
            ),
        ),
        tiles=Stream(0xADECC, 1113, (0x10463, 0x10510, 0x1053A, 0x106E0)),
        tile_map=Stream(
            0xD45D8,
            332,
            (0x10468, 0x1046D, 0x1051F, 0x10524, 0x10549, 0x1054E, 0x106E5, 0x106EA),
        ),
        colours=(Colours(0x2F49D, 2), Colours(0x2F47D, 2, swap=True)),
        first_tile=256,
        budget=256,
        keep=(0,),
        priority=0,
        backdrop=0x0000,
        bank=0x110000,
    ),
)

IMAGE_SLOT_NAMES: tuple[str, ...] = tuple(slot.name for slot in SLOTS)
SLOTS_BY_NAME: Mapping[str, Slot] = {slot.name: slot for slot in SLOTS}

#: Bank tails of 0xFF in the cartridge, less a 16-byte guard after the data
#: before them. Bank $82's (the name tiles) and bank $89's (the flags) are the
#: roster writer's. What is free in them is what no slot's stream holds, by the
#: slots' own pointers, so a stream ending in 0xFF bytes is never written over.
FREE: tuple[tuple[int, int], ...] = (
    # Ends at 0xFFE4: every match reads $81:FFF2-FFF3 ($83:E5FD, LDA $000E,Y
    # with Y=$FFE4) and relies on the 0xFFFF there.
    (0x0FEBF, 0xFFE4),
    (0x1FE3A, 0x20000),
    (0x3FFB5, 0x40000),
    (0x47FB5, 0x48000),
    (0x6FE72, 0x70000),
    (0x77F3F, 0x78000),
    (0xA7F85, 0xA8000),
    (0xBFFBB, 0xC0000),
    (0xD7F82, 0xD8000),
)

_ONE_MIB = 0x100000
_TWO_MIB = 0x200000
_BANK = 0x8000
_ROM_SIZE_BYTE = 0x7FD7
_CHECKSUM = 0x7FDC


def _le16(data: bytes | bytearray, at: int) -> int:
    return data[at] | data[at + 1] << 8


def target(rom: bytes | bytearray, site: int) -> int:
    """The file offset a 3-byte list pointer at `site` loads from."""
    return ((rom[site + 2] & 0x3F) << 15) | (_le16(rom, site) & 0x7FFF)


def point(rom: bytearray, site: int, at: int) -> None:
    """Aim the list pointer at `site` at file offset `at`, keeping bit 6 of its bank."""
    bank = 0x80 | at >> 15 | (rom[site + 2] & 0x40)
    rom[site : site + 3] = bytes([at & 0xFF, 0x80 | (at >> 8 & 0x7F), bank])


def _stream(rom: bytes | bytearray, at: int) -> bytes:
    """The bytes the stream at `at` decodes to, planar ones re-interleaved ($80:A453)."""
    raw = decompress(bytes(rom), at)
    if not rom[at + 1] & 0x80:
        return raw
    whole = len(raw) - len(raw) % 16
    out = bytearray()
    for i in range(0, whole, 16):
        for j in range(8):
            out += bytes((raw[i + j], raw[i + 8 + j]))
    return bytes(out + raw[whole:])


# --- tiles -----------------------------------------------------------------


def tile_indices(data: bytes) -> bytes:
    """The 64 colour indices of a 32-byte 4bpp SNES tile, row by row."""
    out = bytearray(64)
    for y in range(8):
        planes = (data[2 * y], data[2 * y + 1], data[16 + 2 * y], data[17 + 2 * y])
        for x in range(8):
            bit = 7 - x
            out[y * 8 + x] = sum(((p >> bit) & 1) << n for n, p in enumerate(planes))
    return bytes(out)


def tile_bytes(indices: bytes) -> bytes:
    """The 32-byte 4bpp SNES tile of 64 colour indices."""
    out = bytearray(32)
    for y in range(8):
        for plane in range(4):
            byte = 0
            for x in range(8):
                byte |= ((indices[y * 8 + x] >> plane) & 1) << (7 - x)
            out[(plane >> 1) * 16 + 2 * y + (plane & 1)] = byte
    return bytes(out)


def _hflip(p: bytes) -> bytes:
    return b"".join(p[y * 8 : y * 8 + 8][::-1] for y in range(8))


def _vflip(p: bytes) -> bytes:
    return b"".join(p[y * 8 : y * 8 + 8] for y in reversed(range(8)))


def _flipped(p: bytes, flip: int) -> bytes:
    """`p` under a map word's flip bits: bit 0 horizontal, bit 1 vertical."""
    if flip & 1:
        p = _hflip(p)
    if flip & 2:
        p = _vflip(p)
    return p


# --- colours ---------------------------------------------------------------


def to_rgb(colour: int) -> tuple[int, int, int]:
    return tuple((colour >> s & 31) * 255 // 31 for s in (0, 5, 10))  # type: ignore[return-value]


def to_bgr555(rgb: tuple[int, ...]) -> int:
    r, g, b = ((c * 31 + 127) // 255 for c in rgb[:3])
    return r | g << 5 | b << 10


def _swap_rb(colour: int) -> int:
    return (colour & 0x3E0) | (colour >> 10 & 31) | (colour & 31) << 10


def read_colours(rom: bytes | bytearray, slot: Slot) -> dict[int, list[int]]:
    """Each palette row the slot draws with, 16 colours, colour 0 the backdrop."""
    rows: dict[int, list[int]] = {}
    for spot in slot.colours:
        if spot.swap:
            continue
        row = [slot.backdrop] * _ROW
        for i in range(spot.first, _ROW):
            row[i] = _le16(rom, spot.offset + 2 * (i - spot.first))
        rows[spot.row] = row
    return rows


# --- rendering -------------------------------------------------------------


def _vram_tiles(rom: bytes | bytearray, slot: Slot) -> dict[int, bytes]:
    """Tile number to 32 bytes, as the slot's list entries leave video memory."""
    tiles: dict[int, bytes] = {}
    for site in _sites(slot):
        if site == slot.tile_map.pointers[0]:
            continue
        word = _le16(rom, site - 2)
        data = _stream(rom, target(rom, site))
        start = (word - 0x2000) // 16
        for i in range(len(data) // 32):
            tiles[start + i] = data[i * 32 : i * 32 + 32]
    return tiles


def render(rom: bytes | bytearray, slot: Slot) -> Image.Image:
    """The slot's picture as the ROM holds it, colour 0 drawn as the backdrop."""
    from PIL import Image

    tiles = _vram_tiles(rom, slot)
    words = _stream(rom, target(rom, slot.tile_map.pointers[0]))
    rows = read_colours(rom, slot)
    w, h = slot.size
    pixels = bytearray(w * h)
    blank = bytes(64)
    cache: dict[int, bytes] = {}
    for ty in range(h // 8):
        for tx in range(w // 8):
            word = _le16(words, 2 * (ty * 32 + tx))
            number = word & 0x3FF
            if number not in cache:
                cache[number] = tile_indices(tiles[number]) if number in tiles else blank
            p = _flipped(cache[number], (word >> 14 & 1) | (word >> 15 & 1) << 1)
            row = (word >> 10 & 7) * _ROW
            for y in range(8):
                at = (ty * 8 + y) * w + tx * 8
                pixels[at : at + 8] = bytes(row + v for v in p[y * 8 : y * 8 + 8])
    palette: list[int] = []
    for r in range(8):
        colours = rows.get(r, [slot.backdrop] * _ROW)
        palette += [c for i in range(_ROW) for c in to_rgb(slot.backdrop if i == 0 else colours[i])]
    image = Image.frombytes("P", (w, h), bytes(pixels))
    image.putpalette(palette)
    return image.convert("RGB")


# --- encoding --------------------------------------------------------------


@dataclass
class Encoded:
    """A picture in the slot's terms: raw tile and map bytes, colours per row."""

    tiles: bytes
    words: bytes
    colours: dict[int, list[int]]
    #: Distinct tiles before and after merging.
    distinct: int
    used: int


def fit(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """`image` stretched to `size`, on black, on the SNES's 5-bit colour grid."""
    from PIL import Image

    rgba = image.convert("RGBA")
    if rgba.size != size:
        rgba = rgba.resize(size, Image.Resampling.LANCZOS)
    flat = Image.new("RGBA", size, (0, 0, 0, 255))
    flat.alpha_composite(rgba)
    grid = [((v * 31 + 127) // 255) * 255 // 31 for v in range(256)]
    return flat.convert("RGB").point(grid * 3)


def _palette_image(colours: list[int]) -> Image.Image:
    from PIL import Image

    holder = Image.new("P", (1, 1))
    holder.putpalette([c for colour in colours for c in to_rgb(colour)])
    return holder


def _cells(width: int, height: int) -> list[tuple[int, int, int, int]]:
    return [(x, y, x + 8, y + 8) for y in range(0, height, 8) for x in range(0, width, 8)]


def _row_colours(
    picture: Image.Image, cells: list[tuple[int, int, int, int]], count: int
) -> list[int]:
    """At most `count` BGR555 colours for the picture's tiles in `cells`."""
    from PIL import Image

    if not cells:
        return []
    mosaic = Image.new("RGB", (8 * len(cells), 8))
    for i, box in enumerate(cells):
        mosaic.paste(picture.crop(box), (8 * i, 0))
    q = mosaic.quantize(count, method=Image.Quantize.MEDIANCUT, kmeans=1)
    flat = q.getpalette() or []
    found = sorted(set(q.tobytes()))
    return list(dict.fromkeys(to_bgr555(tuple(flat[3 * i : 3 * i + 3])) for i in found))


def _tile_errors(picture: Image.Image, colours: list[int]) -> bytes:
    """Per tile, the mean difference between the picture and its nearest colours."""
    from PIL import Image, ImageChops

    mapped = picture.quantize(palette=_palette_image(colours), dither=Image.Dither.NONE)
    diff = ImageChops.difference(picture, mapped.convert("RGB"))
    return diff.convert("L", (1 / 3, 1 / 3, 1 / 3, 0)).reduce(8).tobytes()


def _assign_rows(picture: Image.Image, counts: list[int]) -> tuple[list[int], list[list[int]]]:
    """Each tile's palette row (an index into `counts`) and each row's colours.

    Rows start from the tiles' mean colours and alternate between choosing
    colours for their tiles and moving each tile to the row that draws it best.
    """
    from PIL import Image

    cells = _cells(*picture.size)
    k = len(counts)
    if k == 1:
        assign = [0] * len(cells)
    else:
        means = picture.reduce(8).quantize(k, method=Image.Quantize.MEDIANCUT)
        assign = list(means.tobytes())
    colours: list[list[int]] = []
    for rounds in range(4):
        colours = [
            _row_colours(picture, [c for c, a in zip(cells, assign, strict=True) if a == r], n)
            for r, n in enumerate(counts)
        ]
        if k == 1 or rounds == 3:
            break
        errors = [_tile_errors(picture, c) if c else bytes([255]) * len(cells) for c in colours]
        moved = [min(range(k), key=[e[i] for e in errors].__getitem__) for i in range(len(cells))]
        if moved == assign:
            break
        assign = moved
    return assign, colours


def _distances(
    patterns: list[bytes], colours: list[int], flips: bool = True
) -> tuple[list[bytes], list[bytes]]:
    """For each pair: how far apart the tiles look in `colours`, and the flip that does best
    (always none without `flips`, for Mode 7, which cannot flip a tile).

    `colours` has 16 entries, or 256. The distance is the mean of |dR|+|dG|+|dB| / 3 over
    the tile, rounded (0-255); flip codes are map-word bits (1 h, 2 v).
    """
    from PIL import Image, ImageChops

    m = len(patterns)
    strip = Image.frombytes(
        "P", (8 * m, 8), b"".join(p[y * 8 : y * 8 + 8] for y in range(8) for p in patterns)
    )
    strip.putpalette([c for colour in colours for c in to_rgb(colour)])
    rgb = strip.convert("RGB")
    t = Image.Transpose
    strips = (
        (0, rgb, False),
        (1, rgb.transpose(t.FLIP_LEFT_RIGHT), True),
        (2, rgb.transpose(t.FLIP_TOP_BOTTOM), False),
        (3, rgb.transpose(t.ROTATE_180), True),
    )[: 4 if flips else 1]
    data = rgb.tobytes()
    width = 24 * m
    near: list[bytes] = []
    codes: list[bytes] = []
    for i in range(m):
        rows = [data[y * width + 24 * i : y * width + 24 * i + 24] * m for y in range(8)]
        rep = Image.frombytes("RGB", (8 * m, 8), b"".join(rows))
        best = bytearray([255]) * m
        how = bytearray(m)
        for code, other, reverse in strips:
            d = ImageChops.difference(rep, other).convert("L", (1 / 3, 1 / 3, 1 / 3, 0))
            got = d.reduce(8).tobytes()
            if reverse:
                got = got[::-1]
            for j, v in enumerate(got):
                if v < best[j]:
                    best[j] = v
                    how[j] = code
        best[i] = 255
        near.append(bytes(best))
        codes.append(bytes(how))
    return near, codes


def _merge(
    patterns: list[bytes],
    rows: list[int],
    weights: list[int],
    colours: dict[int, list[int]],
    room: int,
    flips: bool = True,
) -> dict[int, tuple[int, int]]:
    """Merge tiles until `room` remain: pattern -> (pattern it became, flip).

    Greedy: the cheapest merge first, costed as the tile's users times how far
    it looks from its nearest same-row neighbour. The survivor keeps its pixels.
    """
    groups: dict[int, list[int]] = {}
    for p, row in enumerate(rows):
        groups.setdefault(row, []).append(p)
    alive = [True] * len(patterns)
    weight = list(weights)
    heap: list[tuple[int, int, int]] = []
    tables: dict[int, tuple[list[int], list[bytes], list[bytes], list[list[int]], list[int]]] = {}
    for row, members in groups.items():
        near, how = _distances([patterns[p] for p in members], colours[row], flips)
        order = [sorted(range(len(members)), key=d.__getitem__) for d in near]
        tables[row] = (members, near, how, order, [0] * len(members))
    local = {
        p: (row, i) for row, (members, *_rest) in tables.items() for i, p in enumerate(members)
    }

    def push(p: int) -> None:
        row, i = local[p]
        members, near, _flips, order, cursor = tables[row]
        while cursor[i] < len(members) and (
            order[i][cursor[i]] == i or not alive[members[order[i][cursor[i]]]]
        ):
            cursor[i] += 1
        if cursor[i] < len(members):
            j = order[i][cursor[i]]
            heapq.heappush(heap, (weight[p] * near[i][j], p, members[j]))

    for p in range(len(patterns)):
        push(p)
    became: dict[int, tuple[int, int]] = {}
    left = len(patterns)
    while left > room and heap:
        cost, p, q = heapq.heappop(heap)
        if not alive[p]:
            continue
        row, i = local[p]
        members, near, how, _order, _cursor = tables[row]
        j = local[q][1]
        if not alive[q] or cost != weight[p] * near[i][j]:
            push(p)
            continue
        alive[p] = False
        weight[q] += weight[p]
        became[p] = (q, how[i][j])
        left -= 1
    return became


def encode(image: Image.Image, slot: Slot, kept: Mapping[int, bytes]) -> Encoded:
    """`image` as the slot's tiles, map words and colours.

    `kept` holds the 32-byte tiles the slot keeps, by tile slot.
    """
    from PIL import Image

    picture = fit(image, slot.size)
    spots = [s for s in slot.colours if not s.swap]
    assign, colours = _assign_rows(picture, [_ROW - s.first for s in spots])
    planes = [
        picture.quantize(palette=_palette_image(c), dither=Image.Dither.NONE).tobytes()
        if c
        else b""
        for c in colours
    ]
    w, h = slot.size
    canon: dict[bytes, int] = {}
    patterns: list[bytes] = []
    prows: list[int] = []
    users: list[int] = []
    cells: list[tuple[int, int, int]] = []  # (pattern, flip, palette row)
    for n, (x, y, _x1, _y1) in enumerate(_cells(w, h)):
        spot = spots[assign[n]]
        plane = planes[assign[n]]
        tile = bytes(
            spot.first + plane[(y + dy) * w + x + dx] for dy in range(8) for dx in range(8)
        )
        forms = [_flipped(tile, f) for f in range(4)]
        flip = min(range(4), key=forms.__getitem__)
        key = forms[flip]
        if key not in canon:
            canon[key] = len(patterns)
            patterns.append(key)
            prows.append(spot.row)
            users.append(0)
        users[canon[key]] += 1
        cells.append((canon[key], flip, spot.row))
    free = [i for i in range(slot.budget) if i not in kept]
    full = {
        s.row: [slot.backdrop] + [0] * (s.first - 1) + c + [0] * (_ROW - s.first - len(c))
        for s, c in zip(spots, colours, strict=True)
    }
    became = _merge(patterns, prows, users, full, len(free)) if len(patterns) > len(free) else {}

    def survivor(p: int) -> tuple[int, int]:
        flip = 0
        while p in became:
            p, f = became[p]
            flip ^= f
        return p, flip

    number: dict[int, int] = {}
    words = bytearray()
    for p, flip, row in cells:
        s, f = survivor(p)
        if s not in number:
            number[s] = free[len(number)]
        f ^= flip
        word = (
            (slot.first_tile + number[s])
            | row << 10
            | slot.priority << 13
            | (f & 1) << 14
            | (f >> 1) << 15
        )
        words += bytes((word & 0xFF, word >> 8))
    filler = slot.first_tile + min(kept, default=0) | slot.priority << 13
    while len(words) < 2048:
        words += bytes((filler & 0xFF, filler >> 8))
    slots_used = max([*number.values(), *kept], default=0) + 1
    raw = bytearray(32 * slots_used)
    for at, data in kept.items():
        raw[32 * at : 32 * at + 32] = data
    for s, at in number.items():
        raw[32 * at : 32 * at + 32] = tile_bytes(patterns[s])
    return Encoded(
        bytes(raw),
        bytes(words),
        {s.row: c for s, c in zip(spots, colours, strict=True)},
        len(patterns),
        len(number),
    )


# --- writing ---------------------------------------------------------------


def _kept(rom: bytes | bytearray, slot: Slot) -> dict[int, bytes]:
    data = _stream(rom, target(rom, slot.tiles.pointers[0]))
    if len(data) < 32 * (max(slot.keep) + 1):
        raise RomError(f"The {slot.name} tiles are shorter than the cartridge's")
    return {i: data[32 * i : 32 * i + 32] for i in slot.keep}


def _check(rom: bytes | bytearray, slot: Slot) -> None:
    for at, retail, ours in slot.pokes:
        if bytes(rom[at : at + len(retail)]) not in (retail, ours):
            raise RomError(
                f"The {slot.name} screen's code at 0x{at:05X} is not the cartridge's this "
                f"patcher knows; screen art left alone"
            )


def _sites(slot: Slot) -> list[int]:
    """One list pointer per stream the slot owns."""
    return [slot.tiles.pointers[0], slot.tile_map.pointers[0]] + (
        [slot.blank] if slot.blank else []
    )


def _span(rom: bytes | bytearray, at: int) -> tuple[int, int]:
    return at, at + (_le16(rom, at) & 0x7FFF)


def _ours(at: int, slot: Slot) -> bool:
    """Is `at` in space this module hands out: a free run, or the slot's bank?"""
    return any(lo <= at < hi for lo, hi in FREE) or slot.bank <= at < slot.bank + _BANK


def _taken(rom: bytes | bytearray, skip: Iterable[str]) -> list[tuple[int, int]]:
    """The spans other slots' streams hold in the space this module hands out."""
    return [
        _span(rom, at)
        for slot in SLOTS
        if slot.name not in skip
        for at in (target(rom, site) for site in _sites(slot))
        if _ours(at, slot) and at + 2 <= len(rom)
    ]


def _release(rom: bytearray, slot: Slot) -> None:
    """Hand the space the slot's current streams hold back as 0xFF."""
    for site in _sites(slot):
        at = target(rom, site)
        if _ours(at, slot) and at + 2 <= len(rom):
            start, end = _span(rom, at)
            rom[start:end] = b"\xff" * (min(end, len(rom)) - start)


def _find_free(
    rom: bytearray, lo: int, hi: int, n: int, taken: list[tuple[int, int]]
) -> int | None:
    """The first offset in [lo, hi) with `n` bytes of 0xFF that no taken span overlaps."""
    at = lo
    for start, end in sorted(t for t in taken if t[1] > lo and t[0] < hi) + [(hi, hi)]:
        if start - at >= n and rom[at : at + n] == b"\xff" * n:
            return at
        at = max(at, end)
    return None


def expand(rom: bytearray) -> None:
    """Grow a 1 MiB LoROM to 2 MiB: 0xFF padding and the header's size byte $0B."""
    if len(rom) != _ONE_MIB:
        raise RomError(f"Cannot grow a {len(rom)}-byte ROM to 2 MiB for screen art")
    rom += b"\xff" * _ONE_MIB
    rom[_ROM_SIZE_BYTE] = 0x0B


def fix_checksum(rom: bytearray) -> None:
    """Rewrite the header's checksum and complement for the whole image."""
    rom[_CHECKSUM : _CHECKSUM + 4] = b"\xff\xff\x00\x00"
    total = sum(rom) & 0xFFFF
    rom[_CHECKSUM : _CHECKSUM + 4] = bytes(
        (~total & 0xFF, (~total >> 8) & 0xFF, total & 0xFF, total >> 8)
    )


def _free_spot(
    rom: bytearray, n: int, slot: Slot, taken: list[tuple[int, int]], notes: list[str]
) -> int:
    """Room for `n` bytes: a free run, else the slot's bank, growing the ROM if need be."""
    for lo, hi in FREE:
        at = _find_free(rom, lo, hi, n, taken)
        if at is not None:
            return at
    if len(rom) < _TWO_MIB:
        expand(rom)
        notes.append("ROM grown to 2 MiB")
    at = _find_free(rom, slot.bank, slot.bank + _BANK, n, taken)
    if at is None:
        raise RomError(f"No room for the {slot.name} picture in bank 0x{slot.bank:06X}")
    return at


def _place(
    rom: bytearray,
    data: bytes,
    stream: Stream | None,
    slot: Slot,
    taken: list[tuple[int, int]],
    notes: list[str],
) -> int:
    """Write `data` where the cartridge kept it, else in free space; its offset."""
    if stream is not None and len(data) <= stream.capacity:
        at = stream.retail
    else:
        at = _free_spot(rom, len(data), slot, taken, notes)
        taken.append((at, at + len(data)))
    rom[at : at + len(data)] = data
    return at


def write(rom: bytes, pictures: Mapping[str, Image.Image]) -> tuple[bytes, list[str]]:
    """`rom` (headerless) with each slot in `pictures` replaced; and status notes."""
    out = bytearray(rom)
    if len(out) < _ONE_MIB:
        raise RomError("Too small for ISS screen art: expected a 1 MiB cartridge")
    notes: list[str] = []
    for name in pictures:
        _check(out, SLOTS_BY_NAME[name])
    taken = _taken(out, pictures)
    for name, image in pictures.items():
        slot = SLOTS_BY_NAME[name]
        encoded = encode(image, slot, _kept(out, slot))
        if encoded.used < encoded.distinct:
            notes.append(f"{name}: {encoded.distinct} tiles merged to {encoded.used}")
        _release(out, slot)
        streams: list[tuple[bytes, Stream | None, tuple[int, ...]]] = [
            (compress_large(encoded.tiles), slot.tiles, slot.tiles.pointers),
            (compress_large(encoded.words), slot.tile_map, slot.tile_map.pointers),
        ]
        if slot.blank:
            streams.append((compress_large(bytes(32)), None, (slot.blank,)))
            out[slot.blank - 2 : slot.blank] = b"\x00\x20"
        for data, stream, sites in streams:
            at = _place(out, data, stream, slot, taken, notes)
            for site in sites:
                point(out, site, at)
        for spot in slot.colours:
            colours = encoded.colours[spot.row]
            if spot.swap:
                colours = [_swap_rb(c) for c in colours]
            colours = (colours + [0] * _ROW)[: _ROW - spot.first]
            out[spot.offset : spot.offset + 2 * len(colours)] = b"".join(
                bytes((c & 0xFF, c >> 8)) for c in colours
            )
        for at, _retail, ours in slot.pokes:
            out[at : at + len(ours)] = ours
    fix_checksum(out)
    return bytes(out), notes


# --- files -----------------------------------------------------------------


def _header(size: int) -> int:
    """Bytes of copier header before the ROM image, as `ISSRomReader` counts them."""
    return 512 if size % 1024 == 512 else 0


def load_images(
    given: Any, status: Callable[[str], None], names: tuple[str, ...] = IMAGE_SLOT_NAMES
) -> dict[str, Any]:
    """`patch(images=...)`: slot name (one of `names`) to PNG path or bytes, decoded.

    Returns {} without Pillow (with a status line).

    Raises:
        CapabilityError: an unknown slot, or a picture Pillow cannot read.
    """
    images = dict(given or {})
    for name in images:
        if name not in names:
            raise CapabilityError(f"Unknown image slot {name!r}. Slots: {', '.join(names)}")
    if not images:
        return {}
    try:
        from PIL import Image
    except ImportError:
        status("Pillow is not installed; screen images skipped")
        return {}
    import io

    pictures = {}
    for name, png in images.items():
        data = Path(png).read_bytes() if isinstance(png, str | Path) else bytes(png)
        try:
            picture = Image.open(io.BytesIO(data))
            picture.load()
        except Exception as e:  # noqa: BLE001 - Pillow's errors share no base class
            raise CapabilityError(f"The {name!r} image is not a picture: {e}") from e
        pictures[name] = picture
    return pictures


def write_file(path: Path, pictures: Mapping[str, Any], status: Callable[[str], None]) -> None:
    """Draw `pictures` into the ROM file at `path`, in place, and correct the
    header checksum. With no pictures only the checksum is corrected, and only
    on a full cartridge image."""
    data = Path(path).read_bytes()
    base = _header(len(data))
    if not pictures:
        if len(data) - base >= _ONE_MIB:
            body = bytearray(data[base:])
            fix_checksum(body)
            Path(path).write_bytes(data[:base] + bytes(body))
        return
    drawn, notes = write(data[base:], pictures)
    Path(path).write_bytes(data[:base] + drawn)
    for note in notes:
        status(note)


def export(rom_path: Path, out_dir: Path) -> list[Path]:
    """Write each slot's current art as `<out_dir>/<slot>.png`."""
    data = Path(rom_path).read_bytes()
    rom = data[_header(len(data)) :]
    if len(rom) < _ONE_MIB:
        raise RomError(f"Too small to hold ISS screen art: {rom_path}")
    return save_pngs(out_dir, ((slot.name, render(rom, slot)) for slot in SLOTS))


def save_pngs(out_dir: Path, pictures: Iterable[tuple[str, Image.Image]]) -> list[Path]:
    """Write each (slot name, picture) as `<out_dir>/<slot>.png`."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, picture in pictures:
        path = out / f"{name}.png"
        picture.save(path)
        written.append(path)
    return written
