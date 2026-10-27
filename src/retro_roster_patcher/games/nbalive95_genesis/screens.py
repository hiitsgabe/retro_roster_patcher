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

from ...formats import ea_huff
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
_LEVELS = (0, 52, 87, 116, 144, 172, 206, 255)


def _start(rom: bytes | bytearray, index: int) -> int:
    return struct.unpack_from(">I", rom, ART_TABLE + 4 * index)[0] + ART_TABLE


def _stream(rom: bytes | bytearray, index: int) -> bytes:
    at = _start(rom, index)
    data = bytes(rom[at : at + 0x10000])
    return ea_huff.decompress(data) if data[:2] == ea_huff.MAGIC else refpack_decompress(data)


def _rgb(word: int) -> tuple[int, int, int]:
    return _LEVELS[(word >> 1) & 7], _LEVELS[(word >> 5) & 7], _LEVELS[(word >> 9) & 7]


def _level(value: int) -> int:
    return min(range(8), key=lambda i: abs(_LEVELS[i] - value))


def _word(rgb: tuple[int, int, int]) -> int:
    r, g, b = (_level(c) for c in rgb)
    return b << 9 | g << 5 | r << 1


def render(rom: bytes | bytearray):  # -> PIL.Image.Image
    """The title's background as the lit screen shows it, 312x224."""
    from PIL import Image

    tiles = _stream(rom, TILES)[2:]
    cells = _stream(rom, MAP)
    width, height = struct.unpack_from(">HH", cells)
    words = struct.unpack_from(">64H", rom, _start(rom, PALETTES[1]))
    picture = Image.new("RGB", (width * 8, height * 8))
    px = picture.load()
    assert px is not None
    for i in range(width * height):
        entry = struct.unpack_from(">H", cells, 4 + 2 * i)[0]
        tile, line = entry & 0x7FF, entry >> 13 & 3
        hflip, vflip = entry >> 11 & 1, entry >> 12 & 1
        base = 32 * tile
        for y in range(8):
            for x in range(8):
                sx, sy = (7 - x if hflip else x), (7 - y if vflip else y)
                b = tiles[base + sy * 4 + sx // 2] if base + 32 <= len(tiles) else 0
                c = b >> 4 if sx % 2 == 0 else b & 15
                px[i % width * 8 + x, i // width * 8 + y] = _rgb(words[line * 16 + c])
    return picture


def _tiles_of(picture) -> list[list[tuple[int, int, int]]]:
    px = picture.load()
    return [
        [px[tx * 8 + x, ty * 8 + y] for y in range(8) for x in range(8)]
        for ty in range(ROWS)
        for tx in range(COLUMNS)
    ]


def _slots(line: int) -> list[int]:
    """The colour numbers of `line` a picture may use."""
    return [i for i in range(1, 16) if i not in RESERVED.get(line, ())]


def _palette(
    tiles: list[list[tuple[int, int, int]]], members: list[int], count: int = 15
) -> list[tuple]:
    """Up to `count` Genesis colours for `members`' pixels (median cut)."""
    from PIL import Image

    sample = Image.new("RGB", (8, 8 * max(1, len(members))))
    sp = sample.load()
    assert sp is not None
    for n, i in enumerate(members):
        for k, p in enumerate(tiles[i]):
            sp[k % 8, n * 8 + k // 8] = p
    flat = sample.quantize(count, Image.Quantize.MEDIANCUT).getpalette() or []
    colours = {
        _rgb(_word((flat[j], flat[j + 1], flat[j + 2])))
        for j in range(0, min(3 * count, len(flat)), 3)
    }
    return sorted(colours)[:count]


def _indices(tile: list[tuple], colours: list[tuple]) -> tuple[int, list[int]]:
    """Squared error and the nearest of `colours` (as 1-15) for each pixel."""
    error, out, cache = 0, [], {}
    for p in tile:
        if p not in cache:
            cache[p] = min(
                range(len(colours)),
                key=lambda j: sum((a - b) ** 2 for a, b in zip(colours[j], p, strict=True)),
            )
        j = cache[p]
        out.append(j + 1)
        error += sum((a - b) ** 2 for a, b in zip(colours[j], p, strict=True))
    return error, out


def encode(picture) -> tuple[bytes, list[int], list[list[int | None]]]:
    """`picture` as (tiles, map entries, three palette lines of 16 words, None
    where a colour is the cartridge's own)."""
    from PIL import Image

    tiles = _tiles_of(picture.convert("RGB").resize(SIZE, Image.Resampling.LANCZOS))

    def lum(t: list[tuple]) -> float:
        return sum(0.3 * r + 0.59 * g + 0.11 * b for r, g, b in t)

    order = sorted(range(len(tiles)), key=lambda i: lum(tiles[i]))
    groups = [order[k * len(order) // LINES : (k + 1) * len(order) // LINES] for k in range(LINES)]
    for _ in range(2):  # build palettes, reassign tiles, once more
        palettes = [_palette(tiles, g, len(_slots(line))) for line, g in enumerate(groups)]
        choice = []
        for t in tiles:
            scored = [_indices(t, pal) for pal in palettes]
            k = min(range(LINES), key=lambda k: scored[k][0])
            choice.append((k, scored[k][1]))
        groups = [
            [i for i, (k, _) in enumerate(choice) if k == line] or [0] for line in range(LINES)
        ]
    patterns = [bytes(_slots(line)[j - 1] for j in idx) for line, idx in choice]
    patterns = _merge(patterns)
    unique: dict[bytes, int] = {}
    data = bytearray()
    entries = []
    for (line, _), pattern in zip(choice, patterns, strict=True):
        if pattern not in unique:
            unique[pattern] = len(unique)
            data += bytes(pattern[i] << 4 | pattern[i + 1] for i in range(0, 64, 2))
        entries.append(line << 13 | unique[pattern])
    lines = []
    for line, pal in enumerate(palettes):
        words: list[int | None] = [None] * 16
        words[0] = 0
        for slot, colour in zip(_slots(line), pal, strict=False):
            words[slot] = _word(colour)
        lines.append(words)
    return struct.pack(">H", len(unique)) + bytes(data), entries, lines


def _merge(patterns: list[bytes]) -> list[bytes]:
    """`patterns` with similar tiles made one until at most `MAX_TILES` differ:
    tiles alike when shrunk to 4x4, then to 2x2, then to one pixel."""
    for step in (2, 4, 8):
        if len(set(patterns)) <= MAX_TILES:
            break
        first: dict[tuple, bytes] = {}

        def key(p: bytes, step: int = step) -> tuple:
            return tuple(
                sorted(p[(y + dy) * 8 + x + dx] for dy in range(step) for dx in range(step))[
                    step * step // 2
                ]
                for y in range(0, 8, step)
                for x in range(0, 8, step)
            )

        patterns = [first.setdefault(key(p), p) for p in patterns]
    return patterns


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
