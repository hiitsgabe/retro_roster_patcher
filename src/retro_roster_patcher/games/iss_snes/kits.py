"""ISS kits: the colours a kit holds, the chest design, and a preview of both.

What a kit is on this cartridge (traced in Mesen 2): every team draws the same
player tiles, so the body of a kit is colours only, 15 OBJ palette entries per
kit record (`rom_writer`'s kit offsets): hair, five skin shades, the shirt in
three shades (body and sleeves together), the shorts in three, the socks in two
and the shadow. The one drawing a team owns is its chest design, an 8x8 tile in
eight poses chosen through the pointer table at 0x17153 and drawn in the team's
flag colours, entries 12-15 (12 is also the shirt numbers). It belongs to the
team, not the kit: the away shirt shows the same design.

A `KitSpec` becomes shirt, shorts and socks shades, and its pattern borrows the
nearest of the cartridge's own designs (`PATTERN_DESIGNS`); none is drawn here.
Because the design draws in flag entries, its colours are the crest's (or the
two-band flag's): `pins` asks `write_flags` to order entries 13-15 so the
design shows the crest colour nearest the spec's secondary colour.
"""

from __future__ import annotations

import base64
import io
import struct
import zlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...core.errors import CapabilityError
from ...core.kits import KitSpec, contrast, kit_from_image
from .rom_writer import (
    _DISPLACEMENT_PATCH_BYTE,
    _DISPLACEMENT_PATCH_POINTS,
    _OFS_CHEST_PTRS,
    _OFS_PREDOMINANT_COLOR,
    BLANK_DESIGN,
    CHEST_POSES,
    ISSRomWriter,
    Pins,
    _bgr555_to_rgb,
    _kit_offsets,
    _rgb_to_predominant,
    design_pointers,
    outfield_block,
)

RGB = tuple[int, int, int]

#: `{"template": n}`: the 26 national teams' kits, in `TEAM_ENUM_ORDER`.
TEMPLATES = 26

#: The retail cartridge's 26 national kits, 65 bytes each in slot order: the
#: home and away blocks (16 + 16), the eight chest pointers (24), the flag
#: colours, entries 12-15 (8), and the clash byte; read once from the USA
#: cartridge, the only one the patcher takes. See `retail`.
_RECORD = 65
_RETAIL = zlib.decompress(
    base64.b64decode(
        "eNqNVAlMXFUUvf//UaECHZBt2IapNga1WmpckFKwolagiohdaFlaoNgCAiENGYfhY6wxKgkhVpucbnT2fR+bxhBq"
        "GjHGqNFajGkcRk0zGjWTahpiGjP+P48IUUuavLzce5N33rnnnfuUWxfbEmJn2lx+XVlTnW5gOr20IHpPvGq+JaIr"
        "zY2uSzVg0oQcK47boXHB6sEGP4JBaJQlpFEs8ESDKUNx7fCzFT0Jke6jUWUSkFWb6iK6jV9P/O9S5xdTKR/RcedI"
        "z+3hGcBxGuMYAKvKAPca4DfhEStm7HjChU88eM6PS0HQXQlRpGk9n6MszB5TUXNdv3KMyJxNHcnon3y7ARdN2G3F"
        "gh3dLvziwZAfi0G0C7+nHUsXiV+iXNpZE9sra8CipepGc/ONWiBSUyFFdES15sGJZQ36lo4mq6tpoFFsHY3opg5x"
        "SsqhU8T2NMojBhWvCu+YGVkNoDZLScWUEHnqm+tzD7FjmRKp8qeSDJJVOR8w4JoJOis4B95wId2Ld/xQhaCm/fqW"
        "3oS4xIABLDNgVZlBvQlfWrDTju+c6PTgZx8GgrgWBkm36wfUxJfz83xCYKrHuXyBMVhuYcqAPDNOWnGnA3YXHvAi"
        "7EdlCNObZ4V2gfRCjkRzjFsSkWJFLGJVOe834Q8LtHYknHjdg9v9mAoi732JATUkRIlBB/dDanfueHKf5iRGSQYs"
        "lxkEDagwY9aKJx341IXnvfjGj9YQ2vkoP9qeEBXsLrZnkIr+3cIlA/aY8b0VPQ785sKwF3/6IYagUcizUC0s3Vi2"
        "/vOK8I5Y0UR5FhWQyDGAXIqQyojTZqy3welAuRtnvagK4EIoKaJaMhOt1EBmcLM+ICqhFtkH5fwiHxHi3CI/IzTx"
        "FUIm7Uw5pVwr7SeVWRLgogF6MwQb3nRgrRvvelEYwJkQ9m8upnquWhAmlWezxzbIjZwY1qYVaG72GQ+k1WSos2UG"
        "yQN1Q/Gho69kSbQONV596T1t6rbBroe1cn7FiF4Lrtow4sRfbrzmQ2oQk7KRaFRWgZRt4Zd1OvYjxXdX6KjGvC2l"
        "lR49UjPXSLWxxhuJGOG7xsRKyTsrnZgc52TEZvLIrt59lUZ8aMbTNnzmwAtufOtFewBXQhjPOK+/zs8KwkqAr6iY"
        "Y9FRukwlnJy3GvGjGQdtiDtw2I3rXrwawG1hqBVcA3WdFoRpjl/TncseTz7ANLmDtGkL+bKIohG3WPC2DZlOHHOj"
        "2AdjAGVhiBRVKTjJibLe0r+yN9bn1idbYJFiMaVNJX9pZ4y42wK3DQ86cc6Nah8+CqBeYkAVI/LY07iK1o1v6siM"
        "FnfcP55DJYzBT9xba37NXfUZO/gCbU9Ep4iqajad33JY8ZhGvWU444SaGYv5Ux6uC0Y8Y8EXNrzoxGU39vkQC6A/"
        "jEKKckW3Vgv/GSZzc1m/W9vZkHJw4vDcrt7uMhO8FjxkxwdOPO7Bxz5sD+JiGAlx/gDVqPP5vwHQYMfJ"
    )
)

#: Pose 0 of the cartridge designs a pattern borrows, all eight poses drawn:
#: Argentina's (and Austria's) stripes, in entry 13, and Germany's chest band,
#: in entries 13-15. The rest of the cartridge's designs are collars, badges and
#: flags of their own nation.
STRIPES_DESIGN = 0x9AA319
BAND_DESIGN = 0x9AA209
#: A `KitSpec.pattern` -> the design that stands for it; a pattern missing here
#: (plain, halves, sleeves) has a blank chest.
PATTERN_DESIGNS = {
    "stripes": STRIPES_DESIGN,
    "pinstripes": STRIPES_DESIGN,
    "hoops": BAND_DESIGN,
    "sash": BAND_DESIGN,
    "chevron": BAND_DESIGN,
}

#: The Team Colors screen's player: animation frame 92 ($82:BE3E), its tiles
#: raw at these file offsets (past each run's 2-byte length), as the 16-tile
#: VRAM slot holds them: tiles 0-7 then 0x10-0x16; 0x17 is the chest design.
_FRAME_TILES = ((0x564AE, 0x00, 8), (0x565B0, 0x10, 7))
_CHEST_TILE = 0x17
#: (dx, dy, slot tile, 16x16) of each sprite from the Team Colors OAM, before
#: the horizontal flip the game draws them with; the first is on top.
_LAYOUT = (
    (4, 9, 0x17, False),
    (15, 32, 0x16, False),
    (-8, 14, 0x07, False),
    (16, 14, 0x06, False),
    (-1, 32, 0x04, True),
    (1, 16, 0x02, True),
    (0, 0, 0x00, True),
)
_WIDTH, _HEIGHT, _LEFT = 32, 48, 8
PREVIEW_SCALE = 3


def _file(address: int) -> int:
    """A LoROM SNES address as a headerless file offset."""
    return (address >> 16 & 0x7F) << 15 | address & 0x7FFF


def _words(rom: bytes, at: int, count: int) -> list[int]:
    return list(struct.unpack_from(f"<{count}H", rom, at))


def chest(rom: bytes, slot: int) -> bytes:
    """`slot`'s eight chest-design pointers, as `ISSRomWriter.write_chest` takes them."""
    return b"".join(
        rom[at : at + 3]
        for at in (_OFS_CHEST_PTRS + (pose * 28 + slot) * 3 for pose in range(CHEST_POSES))
    )


def retail(rom: bytes) -> bytes:
    """`rom` itself when it was never patched; for one this library patched (its
    name tiles moved, `ISSRomWriter.write_name_tiles`) a copy with the 26
    national kits, chest pointers, flag colours and clash bytes put back from
    `_RETAIL`, so template n is the nation's kit whichever image it is read
    from, and re-patching gives the same image."""
    if rom[_DISPLACEMENT_PATCH_POINTS[0]] != _DISPLACEMENT_PATCH_BYTE:
        return rom
    out = bytearray(rom)
    for n in range(TEMPLATES):
        record = _RETAIL[n * _RECORD : (n + 1) * _RECORD]
        home, away = _kit_offsets(n)
        out[home : home + 16] = record[:16]
        out[away : away + 16] = record[16:32]
        for pose in range(CHEST_POSES):
            at = _OFS_CHEST_PTRS + (pose * 28 + n) * 3
            out[at : at + 3] = record[32 + 3 * pose : 35 + 3 * pose]
        at = ISSRomWriter._flag_colour_offset(n)
        out[at : at + 8] = record[56:64]
        out[_OFS_PREDOMINANT_COLOR + n] = record[64]
    return bytes(out)


def _tile(rom: bytes, pointer: bytes) -> list[list[int]]:
    """The 8x8 tile a 3-byte pointer names, past its 2-byte header."""
    at = _file(int.from_bytes(pointer, "little")) + 2
    return _decode(rom[at : at + 32])


#: (bit, byte offset in the row's pair) of a 4bpp tile's four bitplanes.
_PLANES = tuple(enumerate((0, 1, 16, 17)))


def _decode(data: bytes) -> list[list[int]]:
    """A 32-byte 4bpp tile as 8 rows of 8 entries; transparent if cut short."""
    if len(data) < 32:
        data = bytes(32)
    return [
        [
            sum((data[plane + 2 * y] >> (7 - x) & 1) << bit for bit, plane in _PLANES)
            for x in range(8)
        ]
        for y in range(8)
    ]


def _entries(rom: bytes, pointers: bytes) -> Counter[int]:
    """How many pixels of the design, over all eight poses, use each flag
    entry (12-15)."""
    return Counter(
        v
        for pose in range(CHEST_POSES)
        for row in _tile(rom, pointers[3 * pose : 3 * pose + 3])
        for v in row
        if v >= 12
    )


def flag_colours(rom: bytes, slot: int) -> list[RGB]:
    """`slot`'s flag colours, entries 12-15."""
    return [_rgb8(w) for w in _words(rom, ISSRomWriter._flag_colour_offset(slot), 4)]


def _rgb8(word: int) -> RGB:
    """BGR555 as the emulator shows it, each channel's top bits repeated
    below (Mesen 2), so a preview matches its screenshots pixel for pixel."""
    r, g, b = (word >> shift & 31 for shift in (0, 5, 10))
    return r << 3 | r >> 2, g << 3 | g >> 2, b << 3 | b >> 2


@dataclass(frozen=True)
class Kit:
    """One kit as `patch` writes it: `block` the 16 bytes of shirt, shorts and
    socks; `chest` the 8 design pointers; `pins` the colours the design asks
    of the flag (`write_flags`); `clash` the 0x8DB2 byte. Only the home kit's
    chest, pins and clash are written: they belong to the team."""

    block: bytes
    chest: bytes
    pins: Pins
    clash: int

    @property
    def shades(self) -> list[RGB]:
        """The three shirt shades, as written."""
        return [_bgr555_to_rgb(w) for w in struct.unpack_from("<3H", self.block)]


def parse(value: Any) -> KitSpec | int:
    """One KIT of the `kits` option: a `KitSpec`, its dict, `{"image": png
    bytes or path}` (read with `kit_from_image`) or `{"template": n}`, as a
    spec or a template number. `CapabilityError` for an unknown template;
    whatever reading the picture or the dict raises otherwise."""
    if isinstance(value, KitSpec):
        return value
    value = dict(value)
    if "template" in value:
        template = int(value["template"])
        if not 0 <= template < TEMPLATES:
            raise CapabilityError(
                f"Unknown kit template {template}; iss-snes has 0..{TEMPLATES - 1}"
            )
        return template
    if "image" in value:
        png = value["image"]
        return kit_from_image(Path(png).read_bytes() if isinstance(png, str | Path) else bytes(png))
    return KitSpec.from_dict(value)


def resolve(rom: bytes, choice: KitSpec | int, kit: int) -> Kit:
    """`choice` for the home (`kit` 0) or away kit, read against `rom`.

    A spec: its shirt, shorts and socks in shades, the design its pattern
    borrows (blank for none), pinned to its secondary colour, and the clash
    byte of its shirt. Template n: kit n's own 16 bytes, chest pointers and
    clash byte, the design pinned to kit n's own flag colours, as the retail
    cartridge has them (`retail`).
    """
    if isinstance(choice, KitSpec):
        design = PATTERN_DESIGNS.get(choice.pattern, BLANK_DESIGN)
        pointers = design_pointers(design)
        return Kit(
            block=outfield_block((choice.shirt, choice.shorts, choice.socks)),
            chest=pointers,
            pins={e: (choice.secondary, n) for e, n in _entries(rom, pointers).items() if e > 12},
            clash=_rgb_to_predominant(*choice.shirt),
        )
    rom = retail(rom)
    at = _kit_offsets(choice)[kit]
    pointers = chest(rom, choice)
    colours = flag_colours(rom, choice)
    return Kit(
        block=bytes(rom[at : at + 16]),
        chest=pointers,
        pins={e: (colours[e - 12], n) for e, n in _entries(rom, pointers).items() if e > 12},
        clash=rom[_OFS_PREDOMINANT_COLOR + choice],
    )


def preview_png(
    rom: bytes, slot: int, kit: int, choice: Kit | None = None, home: Kit | None = None
) -> bytes:
    """The Team Colors player in `slot`'s home (`kit` 0) or away kit, drawn
    from the cartridge's own tiles: a `PREVIEW_SCALE`x RGBA PNG of 32x48.

    `choice` is drawn instead of the kit the image holds. The chest design is
    the team's, so it comes from the home kit: `choice` for the home kit,
    `home` for the away one, else the one the image holds; in the slot's flag
    colours with the pinned entries in the colours asked for, which is what
    the patch aims at (in the game a pinned entry a crest pixel uses takes
    the nearest colour the crest has). Entry 12, the shirt numbers', is not
    known before the crest is: for a `choice` it is drawn black or white,
    whichever reads on the shirt. Needs Pillow.
    """
    from PIL import Image

    at = _kit_offsets(slot)[kit]
    block = choice.block if choice is not None else rom[at : at + 16]
    palette = [0, *_words(rom, at - 12, 6), *_words(block, 0, 8), *_words(rom, at + 16, 1)]
    pointers = chest(rom, slot)
    colours = flag_colours(rom, slot)
    if choice is not None:
        colours[0] = contrast(choice.shades[1])
    design = choice if kit == 0 else home
    if design is not None:
        pointers = design.chest
        for entry, (colour, _) in design.pins.items():
            colours[entry - 12] = colour
    tiles = {
        first + i: _decode(rom[start + 32 * i : start + 32 * i + 32])
        for start, first, count in _FRAME_TILES
        for i in range(count)
    }
    tiles[_CHEST_TILE] = _tile(rom, pointers[:3])

    image = Image.new("RGBA", (_WIDTH, _HEIGHT), (0, 0, 0, 0))
    px = image.load()
    assert px is not None
    for dx, dy, first, big in reversed(_LAYOUT):
        size = 16 if big else 8
        for ty in range(size // 8):
            for tx in range(size // 8):
                for y, row in enumerate(tiles[first + 16 * ty + tx]):
                    for x, v in enumerate(row):
                        if not v:
                            continue
                        if first == _CHEST_TILE and v >= 12:
                            rgb = colours[v - 12]
                        else:
                            rgb = _rgb8(palette[v])
                        px[_LEFT + dx + size - 1 - (8 * tx + x), dy + 8 * ty + y] = (*rgb, 255)
    scaled = (_WIDTH * PREVIEW_SCALE, _HEIGHT * PREVIEW_SCALE)
    image = image.resize(scaled, Image.Resampling.NEAREST)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()
