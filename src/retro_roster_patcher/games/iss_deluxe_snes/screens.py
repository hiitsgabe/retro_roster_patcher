"""Screen art: the Konami logo, the title background and the menu wallpaper.

`patch(images=...)` replaces them and `export_images` writes them out. The
cartridge loads each from Konami-compressed streams (`iss_snes.konami`) named
by `dw vram : dl source` entries of the load lists in bank $82 ($80:B527), as
ISS does, so ISS's encoder, renderer and Pillow helpers (`iss_snes.screens`)
do the work; this module holds the cartridge's places and what each screen
needs. A picture is stretched to its slot, gets 15 colours per palette row,
each 8x8 tile takes the row that draws it best, and tiles are deduplicated
with flips and merged until they fit the slot's tile budget.

Space: a replaced slot's streams leave the cartridge's places of them free,
and each new stream takes the smallest gap it fits among those and the free
runs of the disassembly's `%FREE_BYTES` (`FREE`); never bank $AF, whose free
space the flags and names take. No space crosses a 32 KiB bank. The title's
tiles go first and, like any tile set, shrink to fewer tile slots while their
stream fits nowhere.
The ROM stays 2 MiB; the patcher fixes the checksum.

Measured in Mesen 2 on the USA cartridge, labels from Yoshifanatic1's
disassembly:

* konami: the same tile set, map and effects as ISS's Konami screen (BG2 of a
  Mode 1 screen; list $82:84E8, and $82:8BBF, which nothing runs). BG1 covers
  the logo with tile 2 in row 0 colour 1 and scrolls it away, then draws the
  sweep line and its sparkle from tiles 0-13 and 16-29 of the logo's set in
  row 3; those tiles stay, and row 0 colour 1 stays the cartridge's (it fades
  to white with the backdrop, $80:A234), so row 0 gives the picture colours
  2-15. The logo first shows dark ($89:8022 in rows 0-2, script $81:8889):
  those entries now load the final rows ($89:8044, 8066, 8088). The shine's
  rotations of rows 1, 2 and 0 ($80:A1F2, A1FD, A22D) now turn rows 4, 5 and
  6, which nothing on that screen draws.
* title: lines 0-111 are Mode 1 (the gold ball on BG1, the red brush on BG2)
  and lines 112-223 Mode 3 (the five photos, 8 bits per pixel on BG1), by an
  HDMA table on $2105 ($81:8439). The table now keeps Mode 1, so BG1 holds
  the picture for the whole screen in 4 bits per pixel: rows 2-7 of the
  photos' palette ($89:C3F8; rows 0 and 1 are the brush's and the ball's,
  and the ball's glint cycles row 1), and tiles from VRAM $2A80 (after the
  brush's set) up to the sprites at $6000, which the title loads nothing
  else into. The list entry that loaded the photos to $4000 ($82:996D) now
  loads the picture's tiles to $2A80. The ISS lettering, PRESS START and the
  copyright are sprites (priority 2-3) and stay over it; the brush and the
  ball are gone under it. The title's code then clears rows 14-27 of BG1's
  map ($8B:D295), draws the photo cards there ($8B:D284) and the TM mark
  ($81:F617): the clearing and the cards are skipped, and the mark goes to
  the map's hidden quarter. The picture fades in with the title, where the
  cartridge shows its blank BG1 before the cards slide in.
* menu: the wallpaper the menu screens scroll on BG2, 256x256 in row 1 ($89:
  E49C), from tile 384 (VRAM $3800, four lists). Only the wallpaper's own
  loads and same-content restores wrote $3800-$3FFF from the main menu
  through team select, stadium, handicap and game start, and the tiles stayed
  intact on every mode's menus, the scenario select, the options and the
  game-start submenus, so it gets those 128 tile slots (the cartridge uses
  101). The password screen draws its own background. The CPU's team select shows it from a
  second copy of the row ($89:E4BE), red: that copy gets the colours with red
  and blue swapped. The menus subtract (8, 8, 8) from BG2 (colour math with a
  fixed colour), so it shows darker than its colours.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...core.errors import RomError
from ..iss_snes import screens as iss
from ..iss_snes.konami import compress_large

if TYPE_CHECKING:
    from PIL import Image

Guide = iss.Guide

#: Free runs (the disassembly's `%FREE_BYTES`): $B0:C2DE, $AE:E4EA, $9B:EA6D,
#: $86:E5AB. Each ends at or before its bank's end.
FREE: tuple[tuple[int, int], ...] = (
    (0x1842DE, 0x188000),
    (0x1764EA, 0x178000),
    (0x0DEA6D, 0x0E0000),
    (0x0365AB, 0x037962),
)

#: The title's palette script ($81:864E): (first CGRAM entry, record). A
#: record is a word (byte count - 1), then the colours.
_TITLE_PALETTES = (
    (0x00, 0x4C290),
    (0x10, 0x4C2B2),
    (0x20, 0x4C3F8),
    (0xB0, 0x4C2D4),
    (0xC1, 0x4C358),
    (0xD0, 0x4C2F6),
)
#: The title's HDMA table on $2105: 112 lines of Mode 1, then Mode 3 at these bytes.
_TITLE_MODE3 = (0x843C, 0x843E)
#: The list entries of the title's BG1 tiles and BG2 tiles and maps.
_TITLE_BG1 = 0x11971
#: What the title's code draws into BG1's map after the load ($80:A022 lists:
#: a destination word, then tile bytes ORed with an attribute, $FE for the next
#: run, $FF to end): rows 14-27 cleared to $0101 ($8B:D295), the five photo
#: cards ($8B:D284) and the TM mark ($81:F617).
_TITLE_CLEAR = 0x5D295
_TITLE_CARDS = 0x5D287
_TITLE_CARD_LISTS = (0xF61C, 0xF66E, 0xF6B1, 0xF6FF, 0xF740)
_TITLE_TM = 0xF617
_TITLE_BG2 = (0x11979, 0x11983)
_VRAM_TILES = 0x2000  # word address of tile 0 for BG1 and BG2 on every screen here

SLOTS: tuple[iss.Slot, ...] = (
    iss.Slot(
        "konami",
        (256, 224),
        "the Konami logo screen, revealed top to bottom by a sweeping line, "
        "in 44 colours (3 palette rows)",
        (Guide(0, 0, 256, 224, "a line sweeps it in; 28 tiles stay its"),),
        tiles=iss.Stream(0x1474F5, 2745, (0x104FB, 0x10BC3)),
        tile_map=iss.Stream(0x10F969, 499, (0x104EC,)),
        colours=(
            iss.Colours(0x4804A, 0, first=2),
            iss.Colours(0x4806A, 1),
            iss.Colours(0x4808C, 2),
        ),
        first_tile=0,
        # Tiles $2000-$4FFF: BG3's map at $5000 comes next.
        budget=768,
        keep=tuple(range(0, 14)) + tuple(range(16, 30)),
        priority=0,
        backdrop=0x7FFF,
        bank=0,
        pokes=(
            # Script $81:8889: the dark rows 0-2 -> the final rows.
            (0x8894, b"\x22\x80", b"\x44\x80"),
            (0x889B, b"\x22\x80", b"\x66\x80"),
            (0x88A2, b"\x22\x80", b"\x88\x80"),
            # The shine's rotations (LDX #mirror entry) -> rows 4, 5 and 6.
            (0x21F3, b"\x2c\x2c", b"\x8c\x2c"),
            (0x21FE, b"\x4c\x2c", b"\xac\x2c"),
            (0x222E, b"\x0a\x2c", b"\xca\x2c"),
        ),
    ),
    iss.Slot(
        "title",
        (256, 224),
        "the title background under the sprite lettering, in 90 colours (6 palette rows)",
        (
            Guide(0, 0, 256, 224, "fades in as the title starts"),
            Guide(16, 41, 168, 57, "ISS lettering (game's)"),
            Guide(79, 122, 99, 8, "PRESS START"),
            Guide(14, 206, 235, 17, "copyright (game's)"),
        ),
        tiles=iss.Stream(0x12CB7F, 12979, (_TITLE_BG1,)),
        tile_map=iss.Stream(0x150E61, 245, (0x1197E,)),
        colours=tuple(iss.Colours(0x4C3FC + 32 * (row - 2), row) for row in range(2, 8)),
        first_tile=168,
        budget=1024 - 168,
        keep=(),
        priority=0,
        backdrop=0x7FFE,
        bank=0,
        pokes=(
            (_TITLE_BG1 - 2, b"\x00\x40", b"\x80\x2a"),  # load the tiles to $2A80
            *((at, b"\x03", b"\x01") for at in _TITLE_MODE3),  # Mode 1 below line 112
            (_TITLE_CLEAR, b"\xa9", b"\x60"),  # no clearing rows 14-27 (RTS)
            (_TITLE_CARDS, b"\x22\x22\xa0\x80", b"\xea" * 4),  # no photo cards
            (_TITLE_TM + 1, b"\x01", b"\x05"),  # the TM mark into the hidden quadrant
        ),
    ),
    iss.Slot(
        "menu",
        (256, 256),
        "the wallpaper behind the menus, a 256x256 pattern they scroll, in 15 colours "
        "(1 palette row); the CPU's team select shows it with red and blue swapped",
        (Guide(0, 0, 256, 256, "repeats both ways; shown darker"),),
        tiles=iss.Stream(0x0F58AB, 2413, (0x10E6F, 0x10E81, 0x1189C, 0x11933)),
        tile_map=iss.Stream(0x1169D0, 337, (0x10E74, 0x10E86, 0x118A1, 0x11938)),
        colours=(iss.Colours(0x4E4A0, 1), iss.Colours(0x4E4C2, 1, swap=True)),
        first_tile=384,
        budget=128,
        keep=(),
        priority=0,
        backdrop=0x0000,
        bank=0,
    ),
)

IMAGE_SLOT_NAMES: tuple[str, ...] = tuple(slot.name for slot in SLOTS)
SLOTS_BY_NAME: Mapping[str, iss.Slot] = {slot.name: slot for slot in SLOTS}


# --- rendering ---------------------------------------------------------------


def _vram(rom: bytes | bytearray, sites: tuple[int, ...]) -> bytearray:
    """Video memory as the list entries at `sites` leave it, in order."""
    vram = bytearray(0x10000)
    for site in sites:
        at = 2 * iss._le16(rom, site - 2)
        data = iss._stream(rom, iss.target(rom, site))[: 0x10000 - at]
        vram[at : at + len(data)] = data
    return vram


def _draw_list(rom: bytes | bytearray, vram: bytearray, at: int, attribute: int) -> None:
    """Replay one of the title's map-drawing lists into `vram`."""
    while True:
        dest, at = iss._le16(rom, at), at + 2
        step = 1
        if rom[at] == 0xFC:
            step, at = 32, at + 1
        while True:
            b, at = rom[at], at + 1
            if b == 0xFF:
                return
            if b == 0xFE:
                break
            if b == 0xFD:
                attribute, at = (attribute & 0xFF) | rom[at] << 8, at + 1
                continue
            vram[2 * dest : 2 * dest + 2] = (b | attribute).to_bytes(2, "little")
            dest += step


def _title_cgram(rom: bytes | bytearray) -> list[int]:
    cgram = [0] * 256
    for first, record in _TITLE_PALETTES:
        for i in range((iss._le16(rom, record) + 1) // 2):
            cgram[first + i] = iss._le16(rom, record + 2 + 2 * i)
    return cgram


def _render_title(rom: bytes | bytearray) -> Image.Image:
    """The title as the screen shows it without sprites: BG1 over BG2 over the
    backdrop, BG1 at 8 bits per pixel from line 112 while the HDMA says Mode 3."""
    from PIL import Image

    vram = _vram(rom, (*_TITLE_BG2, _TITLE_BG1, 0x1197E))
    if rom[_TITLE_CLEAR] != 0x60:
        vram[2 * 0x1C0 : 2 * 0x380] = b"\x01\x01" * 0x1C0
    if rom[_TITLE_CARDS] == 0x22:
        for at in _TITLE_CARD_LISTS:
            _draw_list(rom, vram, at, 0x0100)
    _draw_list(rom, vram, _TITLE_TM, 0x0400)
    mode3 = rom[_TITLE_MODE3[0]] == 3
    pixels = bytearray(256 * 224)
    base = 2 * _VRAM_TILES
    for layer, words_at in ((2, 0x2000), (1, 0)):
        for ty in range(28):
            deep = layer == 1 and mode3 and ty >= 14
            for tx in range(32):
                word = iss._le16(vram, words_at + 2 * (32 * ty + tx))
                n = word & 0x3FF
                if deep:
                    at = base + 64 * n
                    lo, hi = bytes(vram[at : at + 32]), bytes(vram[at + 32 : at + 64])
                    pairs = zip(iss.tile_indices(lo), iss.tile_indices(hi), strict=True)
                    tile = bytes(a | b << 4 for a, b in pairs)
                    row = 0
                else:
                    tile = iss.tile_indices(bytes(vram[base + 32 * n : base + 32 * n + 32]))
                    row = (word >> 10 & 7) * 16
                tile = iss._flipped(tile, (word >> 14 & 1) | (word >> 15 & 1) << 1)
                for y in range(8):
                    at = (ty * 8 + y) * 256 + tx * 8
                    for x, v in enumerate(tile[y * 8 : y * 8 + 8]):
                        if v:
                            pixels[at + x] = row + v
    image = Image.frombytes("P", (256, 224), bytes(pixels))
    image.putpalette([c for colour in _title_cgram(rom) for c in iss.to_rgb(colour)])
    return image.convert("RGB")


def render(rom: bytes | bytearray, slot: iss.Slot) -> Image.Image:
    """The slot's picture as the ROM holds it."""
    return _render_title(rom) if slot.name == "title" else iss.render(rom, slot)


# --- writing -----------------------------------------------------------------


def _sites(slot: iss.Slot) -> tuple[int, int]:
    return slot.tiles.pointers[0], slot.tile_map.pointers[0]


#: Where streams may go: the free runs, and the cartridge's own place of every
#: slot's streams (free once that slot is replaced: only its list entries read it).
SPACES: tuple[tuple[int, int], ...] = FREE + tuple(
    (stream.retail, stream.retail + stream.capacity)
    for slot in SLOTS
    for stream in (slot.tiles, slot.tile_map)
)


def _in_spaces(at: int) -> bool:
    return any(lo <= at < hi for lo, hi in SPACES)


def _blank(rom: bytearray, lo: int, hi: int, taken: list[tuple[int, int]]) -> None:
    """Fill [lo, hi) with 0xFF, except what `taken` holds."""
    for start, end in sorted(t for t in taken if t[1] > lo and t[0] < hi) + [(hi, hi)]:
        if start > lo:
            rom[lo:start] = b"\xff" * (start - lo)
        lo = max(lo, end)


def _gaps(rom: bytes | bytearray, taken: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Every stretch of the spaces that no taken span overlaps and that holds 0xFF."""
    gaps = []
    for lo, hi in SPACES:
        at = lo
        for start, end in sorted(t for t in taken if t[1] > lo and t[0] < hi) + [(hi, hi)]:
            if start > at and rom[at:start] == b"\xff" * (start - at):
                gaps.append((at, start))
            at = max(at, end)
    return gaps


def _place(rom: bytearray, data: bytes, taken: list[tuple[int, int]]) -> int | None:
    """Write `data` in the smallest gap it fits; its offset, or None."""
    fits = [(end - at, at) for at, end in _gaps(rom, taken) if end - at >= len(data)]
    if not fits:
        return None
    at = min(fits)[1]
    taken.append((at, at + len(data)))
    rom[at : at + len(data)] = data
    return at


def _kept(rom: bytes | bytearray, slot: iss.Slot) -> dict[int, bytes]:
    if not slot.keep:
        return {}
    data = iss._stream(rom, iss.target(rom, slot.tiles.pointers[0]))
    if len(data) < 32 * (max(slot.keep) + 1):
        raise RomError(f"The {slot.name} tiles are shorter than the cartridge's")
    return {i: data[32 * i : 32 * i + 32] for i in slot.keep}


def _tiles(
    rom: bytearray,
    image: Image.Image,
    slot: iss.Slot,
    kept: Mapping[int, bytes],
    taken: list[tuple[int, int]],
) -> iss.Encoded:
    """Encode and place the slot's tiles, with fewer tile slots while the
    compressed stream fits nowhere."""
    while True:
        encoded = iss.encode(image, slot, kept)
        data = compress_large(encoded.tiles)
        at = _place(rom, data, taken)
        if at is not None:
            for site in slot.tiles.pointers:
                iss.point(rom, site, at)
            return encoded
        room = max((end - start for start, end in _gaps(rom, taken)), default=0)
        budget = min(slot.budget - 1, slot.budget * room * 95 // (100 * len(data)))
        if budget < 64:
            raise RomError(f"No room for the {slot.name} picture")
        slot = replace(slot, budget=budget)


def write(rom: bytearray, pictures: Mapping[str, Image.Image]) -> list[str]:
    """Draw each slot in `pictures` into `rom` (2 MiB, headerless); status notes.
    The caller fixes the checksum."""
    for name in pictures:
        iss._check(rom, SLOTS_BY_NAME[name])
    kept = {name: _kept(rom, SLOTS_BY_NAME[name]) for name in pictures}
    # What the slots left alone hold stays; the replaced slots' streams and
    # their cartridge places become 0xFF.
    taken = [
        iss._span(rom, iss.target(rom, site))
        for slot in SLOTS
        if slot.name not in pictures
        for site in _sites(slot)
    ]
    for name in pictures:
        slot = SLOTS_BY_NAME[name]
        for site, stream in zip(_sites(slot), (slot.tiles, slot.tile_map), strict=True):
            current = iss._span(rom, iss.target(rom, site))
            if _in_spaces(current[0]):
                _blank(rom, *current, taken)
            _blank(rom, stream.retail, stream.retail + stream.capacity, taken)
    notes = []
    # The title first: its tiles need the most room.
    for slot in sorted((SLOTS_BY_NAME[n] for n in pictures), key=lambda s: s.name != "title"):
        encoded = _tiles(rom, pictures[slot.name], slot, kept[slot.name], taken)
        if encoded.used < encoded.distinct:
            notes.append(f"{slot.name}: {encoded.distinct} tiles merged to {encoded.used}")
        at = _place(rom, compress_large(encoded.words), taken)
        if at is None:
            raise RomError(f"No room for the {slot.name} picture's map")
        for site in slot.tile_map.pointers:
            iss.point(rom, site, at)
        for spot in slot.colours:
            colours = encoded.colours[spot.row]
            if spot.swap:
                colours = [iss._swap_rb(c) for c in colours]
            colours = (colours + [0] * 16)[: 16 - spot.first]
            rom[spot.offset : spot.offset + 2 * len(colours)] = b"".join(
                c.to_bytes(2, "little") for c in colours
            )
        for at, _cart, ours in slot.pokes:
            rom[at : at + len(ours)] = ours
    return notes


# --- files -------------------------------------------------------------------


def load_images(given: Any, status: Callable[[str], None]) -> dict[str, Any]:
    """`patch(images=...)`: slot name to PNG path or bytes, decoded (see ISS)."""
    return iss.load_images(given, status, IMAGE_SLOT_NAMES)


def export(rom: bytes, out_dir: Path) -> list[Path]:
    """Write each slot's current art as `<out_dir>/<slot>.png`."""
    return iss.save_pngs(out_dir, ((slot.name, render(rom, slot)) for slot in SLOTS))
