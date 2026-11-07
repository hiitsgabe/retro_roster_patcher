"""Screen art: the title screen and the main menu (Game Setup's rink).

`patch(images=...)` replaces them and `export_images` writes them out. Each is
an EA art record: a u32 offset to its palette (64 colour words, four lines), a
u32 offset to its map (u16 width and height in cells, then one map word per
cell) and a u16 tile count, then the 4bpp tiles. The game loads a record with
`movea.l #record,a0` (`Slot.sites`: the addresses of the immediates) and puts
the next record of the screen in the VRAM tiles after it, so a picture keeps to
the cartridge's tile count. A new picture is a new record in the image grown to
2 MiB (`Slot.region`) and the sites point at it; the cartridge's records stay
where they are, so a patched ROM can be patched again.

Measured in genesis_plus_gx on the retail cartridge (Nov 2026):

* title: record $4E45C, 32x28 cells, 526 tiles, palette lines 0-2 (line 3 is
  the game's own reds, for the letters). The load at $FF10C draws rows 0-22 in
  the window plane, the one at $FF13E rows 23-27 in plane B. Over it: the NHL
  shield, the NHLPA logo and the NHL HOCKEY 94 letters (plane B, in front) and
  the credits on plane A, in the bottom 40 lines. They draw with line 1 colours
  1-10, 13 and 15 and line 2 colours 1, 2, 13 and 15, which a picture keeps.
* menu: record $4B7A0, 40x28 cells, 240 tiles in line 0 (colour 0, the
  backdrop, kept), loaded at $F823A for Game Setup. Over it: the team names,
  the two team logos (and, in their place, the player cards Game Setup cycles)
  and the options box, dithered so the rink shows through.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...formats import genesis
from ..iss_snes import screens as iss
from .rom_writer import GROWN_SIZE, grow

Guide = iss.Guide


@dataclass(frozen=True)
class Slot:
    name: str
    size: tuple[int, int]
    description: str
    #: Where the game draws over the picture; for templates.
    guides: tuple[Guide, ...]
    #: File offsets of the `movea.l #record,a0` immediates that load the record.
    sites: tuple[int, ...]
    #: The cartridge's record.
    retail: int
    #: Where a new record goes in the image grown to 2 MiB.
    region: range
    max_tiles: int
    #: The palette lines a picture draws with, and the colour numbers of each it sets.
    lines: tuple[int, ...]
    free: Mapping[int, tuple[int, ...]]


SLOTS: tuple[Slot, ...] = (
    Slot(
        "title",
        (256, 224),
        "the title screen behind the NHL and NHLPA shields, the NHL HOCKEY 94 "
        "letters and the credits: 3 palette lines (29 colours of their own), 526 tiles",
        (
            Guide(8, 8, 48, 56, "NHL shield"),
            Guide(200, 8, 48, 56, "NHLPA logo"),
            Guide(8, 112, 240, 48, "NHL HOCKEY 94 letters"),
            Guide(0, 184, 256, 40, "credits"),
        ),
        (0xFF10E, 0xFF140),
        0x4E45C,
        range(0x100000, 0x108000),
        526,
        (0, 1, 2),
        {0: tuple(range(1, 16)), 1: (11, 12, 14), 2: (*range(3, 13), 14)},
    ),
    Slot(
        "menu",
        (320, 224),
        "the rink behind Game Setup, the main menu: 15 colours, 240 tiles",
        (
            Guide(0, 0, 320, 32, "team names"),
            Guide(24, 40, 264, 64, "team logos and player cards"),
            Guide(8, 112, 304, 104, "options, see-through"),
        ),
        (0xF823C,),
        0x4B7A0,
        range(0x108000, 0x110000),
        240,
        (0,),
        {0: tuple(range(1, 16))},
    ),
)

IMAGE_SLOT_NAMES: tuple[str, ...] = tuple(slot.name for slot in SLOTS)
SLOTS_BY_NAME: Mapping[str, Slot] = {slot.name: slot for slot in SLOTS}


def record(rom: bytes | bytearray, at: int) -> tuple[list[int], int, int, list[int], bytes]:
    """The EA art record at `at`: (64 palette words, map width, height, map
    words, 4bpp tile bytes)."""
    pal_off, map_off, count = struct.unpack_from(">IIH", rom, at)
    palette = list(struct.unpack_from(">64H", rom, at + pal_off))
    width, height = struct.unpack_from(">HH", rom, at + map_off)
    entries = list(struct.unpack_from(f">{width * height}H", rom, at + map_off + 4))
    return palette, width, height, entries, bytes(rom[at + 10 : at + 10 + 32 * count])


def make_record(
    tiles: bytes, palette: Sequence[int], width: int, height: int, entries: Sequence[int]
) -> bytes:
    """An EA art record: header, tiles, the 64 palette words, the map."""
    pal_off = 10 + len(tiles)
    head = struct.pack(">IIH", pal_off, pal_off + 0x80, len(tiles) // 32)
    cells = struct.pack(f">HH{len(entries)}H", width, height, *entries)
    return head + tiles + struct.pack(">64H", *palette) + cells


def _current(rom: bytes | bytearray, slot: Slot) -> int | None:
    """The record every site loads, when it is the cartridge's or this module's."""
    targets = {struct.unpack_from(">I", rom, site)[0] for site in slot.sites}
    if len(targets) == 1 and targets <= {slot.retail, slot.region.start}:
        return targets.pop()
    return None


def render(rom: bytes | bytearray, slot: Slot) -> Any:
    """The picture the slot's first load draws (its own colours, no overlays)."""
    palette, width, _height, entries, tiles = record(
        rom, struct.unpack_from(">I", rom, slot.sites[0])[0]
    )
    return genesis.render_tiles(tiles, entries, width, palette)


def draw(rom: bytearray, pictures: Mapping[str, Any], status: Callable[[str], None]) -> list[str]:
    """Write each `{slot: picture}` as a new record and point its loads at it,
    growing the image to 2 MiB; the slots written. A slot whose loads do not
    hold the cartridge's record (or this module's) is left alone, said in a
    status line."""
    written = []
    for slot in SLOTS:
        picture = pictures.get(slot.name)
        if picture is None:
            continue
        current = _current(rom, slot) if len(rom) <= GROWN_SIZE else None
        if current is None:
            what = slot.name.capitalize()
            status(f"{what} art kept: the code that loads it is not the cartridge's")
            continue
        encoded = genesis.encode(picture, slot.size, slot.lines, slot.free, slot.max_tiles)
        palette, *_ = record(rom, current)
        for line, words in encoded.palettes.items():
            for i, colour in enumerate(words):
                if colour is not None:
                    palette[line * 16 + i] = colour
        width, height = slot.size[0] // 8, slot.size[1] // 8
        data = make_record(b"".join(encoded.tiles), palette, width, height, encoded.entries)
        grow(rom)
        rom[slot.region.start : slot.region.stop] = b"\xff" * len(slot.region)
        rom[slot.region.start : slot.region.start + len(data)] = data
        for site in slot.sites:
            struct.pack_into(">I", rom, site, slot.region.start)
        written.append(slot.name)
    return written


def load_images(given: Any, status: Callable[[str], None]) -> dict[str, Any]:
    """`patch(images=...)`: slot name to PNG path or bytes, decoded (see ISS)."""
    return iss.load_images(given, status, IMAGE_SLOT_NAMES)


def export(rom: bytes | bytearray, out_dir: Path) -> list[Path]:
    """Write each slot's current art as `<out_dir>/<slot>.png`."""
    return iss.save_pngs(out_dir, [(slot.name, render(rom, slot)) for slot in SLOTS])
