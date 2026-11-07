"""Screen art: the title screen's background, the arena behind the logo.

The title is drawn from entries of the art table at `ART_TABLE` (32-bit offsets
from the table itself): a tile stream (`TILES`: a word, the tile count, then the
4bpp tiles, loaded at VRAM 0x1000), a map (`MAP`: width and height in tiles,
39x28, then a nametable word per cell, tile numbers counted from the first
tile) and palettes (`PALETTES`: 64 raw colours each, the screen's dark, lit
and flashing phases). The animated "NBA LIVE 95" letters and the NBA logo are
sprites of their own and stay on top: "LIVE" is drawn with colours 9-12 of
CRAM line 1, which the flashing phase changes, so those stay the cartridge's
(`RESERVED`), and "95" and the letters' outline are two maps (`OVERLAYS`) of
the background's tiles, which a new set keeps, appended, the maps renumbered
to them. A new picture leaves room for the logo.

A picture is drawn with CRAM lines 0-2 (line 3 is the logo's) less the
reserved colours: each tile takes the line that draws it best, identical tiles
are stored once, and similar ones are merged until the set fits `MAX_TILES`
(the letters' tiles follow in VRAM).
The streams go to free space as RefPack; the three palettes are written in
place, all three the picture's, so it does not change colour between phases.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ...formats import ea_huff, genesis
from ...formats.ea_tdb import refpack_compress, refpack_decompress
from ..iss_snes import screens as iss
from .space import Space

ART_TABLE = 0x4F7CE
PALETTES = (228, 229, 230)
TILES = 231
MAP = 232
#: Maps drawn over the background with CRAM line 3: the "LIVE" and "95"
#: letters, from tiles of the background's own set.
OVERLAYS = (233, 234)
COLUMNS, ROWS = 39, 28
SIZE = (COLUMNS * 8, ROWS * 8)
MAX_TILES = 700
LINES = 3
#: Colours of a line the picture leaves alone: "LIVE"'s, which flash.
RESERVED = {1: (9, 10, 11, 12)}
IMAGE_SLOT_NAMES = ("title",)
#: What the game draws over the title picture, (label, (x, y, width, height))
#: in its pixels, measured in genesis_plus_gx: keep these parts plain.
GUIDES = (
    ("NBA logo", (86, 56, 64, 136)),
    ("NBA LIVE 95 letters", (148, 28, 164, 172)),
    ("Credits", (0, 192, 312, 32)),
)


def _start(rom: bytes | bytearray, index: int) -> int:
    return struct.unpack_from(">I", rom, ART_TABLE + 4 * index)[0] + ART_TABLE


def _stream(rom: bytes | bytearray, index: int) -> bytes:
    at = _start(rom, index)
    data = bytes(rom[at : at + 0x10000])
    return ea_huff.decompress(data) if data[:2] == ea_huff.MAGIC else refpack_decompress(data)


def render(rom: bytes | bytearray):  # -> PIL.Image.Image
    """The title's background as the lit screen shows it, 312x224."""
    tiles = _stream(rom, TILES)[2:]
    cells = _stream(rom, MAP)
    width, height = struct.unpack_from(">HH", cells)
    entries = struct.unpack_from(f">{width * height}H", cells, 4)
    words = struct.unpack_from(">64H", rom, _start(rom, PALETTES[1]))
    return genesis.render_tiles(tiles, entries, width, words)


def _slots(line: int) -> list[int]:
    """The colour numbers of `line` a picture may use."""
    return [i for i in range(1, 16) if i not in RESERVED.get(line, ())]


def encode(picture) -> tuple[bytes, list[int], list[list[int | None]]]:
    """`picture` as (tiles, map entries, three palette lines of 16 words, None
    where a colour is the cartridge's own)."""
    encoded = genesis.encode(
        picture, SIZE, range(LINES), {line: _slots(line) for line in range(LINES)}, MAX_TILES
    )
    lines = []
    for line in range(LINES):
        words = list(encoded.palettes[line])
        words[0] = 0
        lines.append(words)
    tiles = struct.pack(">H", len(encoded.tiles)) + b"".join(encoded.tiles)
    return tiles, encoded.entries, lines


def _keep_overlays(rom: bytes | bytearray, tiles: bytes) -> tuple[bytes, dict[int, bytes]]:
    """`tiles` (count word first) with the old set's tiles the `OVERLAYS`
    use appended, and each overlay map renumbered to them."""
    old = _stream(rom, TILES)[2:]
    count = struct.unpack_from(">H", tiles)[0]
    data = bytearray(tiles[2:])
    moved: dict[int, int] = {}

    def keep(entry: int) -> int:
        tile = entry & 0x7FF
        if tile not in moved:
            moved[tile] = count + len(moved)
            data.extend(old[32 * tile : 32 * tile + 32])
        return entry & ~0x7FF | moved[tile]

    maps = {}
    for index in OVERLAYS:
        cells = _stream(rom, index)
        width, height = struct.unpack_from(">HH", cells)
        overlay = [keep(e) if e else 0 for e in struct.unpack_from(f">{width * height}H", cells, 4)]
        maps[index] = struct.pack(f">HH{len(overlay)}H", width, height, *overlay)
    return struct.pack(">H", count + len(moved)) + bytes(data), maps


def write(rom: bytearray, pictures: Mapping[str, Any], free: Space) -> list[str]:
    """Draw each `{slot: picture}`; the slots written."""
    picture = pictures.get("title")
    if picture is None:
        return []
    tiles, entries, lines = encode(picture)
    tiles, overlays = _keep_overlays(rom, tiles)
    cells = struct.pack(">HH", COLUMNS, ROWS) + struct.pack(f">{len(entries)}H", *entries)
    for index, raw in ((TILES, tiles), (MAP, cells), *overlays.items()):
        blob = refpack_compress(raw)
        at = free.alloc(len(blob))
        if at is None:
            return []
        rom[at : at + len(blob)] = blob
        struct.pack_into(">I", rom, ART_TABLE + 4 * index, at - ART_TABLE)
    for index in PALETTES:
        for line, words in enumerate(lines):
            for n, word in enumerate(words):
                if word is not None:
                    struct.pack_into(">H", rom, _start(rom, index) + 32 * line + 2 * n, word)
    return ["title"]


def load_images(given: Any, status: Callable[[str], None]) -> dict[str, Any]:
    """`patch(images=...)`: slot name to PNG path or bytes, decoded (see ISS)."""
    return iss.load_images(given, status, IMAGE_SLOT_NAMES)


def export(rom: bytes, out_dir: Path) -> list[Path]:
    """Write each slot's current art as `<out_dir>/<slot>.png`."""
    return iss.save_pngs(out_dir, [("title", render(rom))])
