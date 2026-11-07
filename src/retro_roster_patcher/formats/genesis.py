"""Sega Genesis graphics: colours, 4bpp tiles, and the encoder that fits a
picture into a screen's palette lines and tile budget.

A colour is a VDP word, 0000BBB0GGG0RRR0, each channel one of eight levels
(`LEVELS`, as genesis_plus_gx draws them). A tile is 8x8 pixels of 4 bits, two
to a byte with the left one high, rows top to bottom: 32 bytes. A map word is
`line << 13 | tile`, with bits 11 and 12 flipping the tile.

`encode` gives each tile the palette line that draws it best (the lines'
colours chosen by median cut over the tiles that use them), stores identical
tiles once and merges similar ones until at most `max_tiles` differ. Pillow
(the `images` extra) is imported inside the functions that need it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: The eight levels of a channel, 0 to 7.
LEVELS = (0, 52, 87, 116, 144, 172, 206, 255)


def rgb(word: int) -> tuple[int, int, int]:
    """A VDP colour word as RGB."""
    return LEVELS[(word >> 1) & 7], LEVELS[(word >> 5) & 7], LEVELS[(word >> 9) & 7]


def _level(value: int) -> int:
    return min(range(8), key=lambda i: abs(LEVELS[i] - value))


def word(colour: Sequence[int]) -> int:
    """The VDP word nearest to an RGB colour."""
    r, g, b = (_level(c) for c in colour[:3])
    return b << 9 | g << 5 | r << 1


@dataclass
class Encoded:
    #: The picture's distinct tiles, 32 bytes each.
    tiles: list[bytes]
    #: One map word per 8x8 cell, row by row: `line << 13 | tile`.
    entries: list[int]
    #: Per palette line used, its 16 words; None where the picture leaves the
    #: colour as it was (colour 0, and every colour number not free).
    palettes: dict[int, list[int | None]]


def _tiles_of(picture: Any, size: tuple[int, int]) -> list[list[tuple[int, int, int]]]:
    px = picture.load()
    columns, rows = size[0] // 8, size[1] // 8
    return [
        [px[tx * 8 + x, ty * 8 + y] for y in range(8) for x in range(8)]
        for ty in range(rows)
        for tx in range(columns)
    ]


def _palette(
    tiles: list[list[tuple[int, int, int]]], members: list[int], count: int = 15
) -> list[tuple[int, int, int]]:
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
        rgb(word((flat[j], flat[j + 1], flat[j + 2])))
        for j in range(0, min(3 * count, len(flat)), 3)
    }
    return sorted(colours)[:count]


def _indices(tile: list[tuple], colours: list[tuple]) -> tuple[int, list[int]]:
    """Squared error and the nearest of `colours` (as 1-n) for each pixel."""
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


def _merge(patterns: list[bytes], max_tiles: int) -> list[bytes]:
    """`patterns` with similar tiles made one until at most `max_tiles` differ:
    tiles alike when shrunk to 4x4, then to 2x2, then to one pixel."""
    for step in (2, 4, 8):
        if len(set(patterns)) <= max_tiles:
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


def encode(
    picture: Any,
    size: tuple[int, int],
    lines: Sequence[int],
    free: Mapping[int, Sequence[int]],
    max_tiles: int,
) -> Encoded:
    """`picture` resized to `size` and drawn with the palette `lines`, each
    setting only its `free` colour numbers (1-15)."""
    from PIL import Image

    tiles = _tiles_of(picture.convert("RGB").resize(size, Image.Resampling.LANCZOS), size)

    def lum(t: list[tuple]) -> float:
        return sum(0.3 * r + 0.59 * g + 0.11 * b for r, g, b in t)

    count = len(lines)
    order = sorted(range(len(tiles)), key=lambda i: lum(tiles[i]))
    groups = [order[k * len(order) // count : (k + 1) * len(order) // count] for k in range(count)]
    for _ in range(2):  # build palettes, reassign tiles, once more
        palettes = [_palette(tiles, g, len(free[lines[k]])) for k, g in enumerate(groups)]
        choice = []
        for t in tiles:
            scored = [_indices(t, pal) for pal in palettes]
            k = min(range(count), key=lambda k: scored[k][0])
            choice.append((k, scored[k][1]))
        groups = [[i for i, (k, _) in enumerate(choice) if k == n] or [0] for n in range(count)]
    patterns = [bytes(free[lines[k]][j - 1] for j in idx) for k, idx in choice]
    patterns = _merge(patterns, max_tiles)
    unique: dict[bytes, int] = {}
    entries = []
    for (k, _), pattern in zip(choice, patterns, strict=True):
        if pattern not in unique:
            unique[pattern] = len(unique)
        entries.append(lines[k] << 13 | unique[pattern])
    out: dict[int, list[int | None]] = {}
    for k, pal in enumerate(palettes):
        words: list[int | None] = [None] * 16
        for slot, colour in zip(free[lines[k]], pal, strict=False):
            words[slot] = word(colour)
        out[lines[k]] = words
    return Encoded(
        tiles=[bytes(p[i] << 4 | p[i + 1] for i in range(0, 64, 2)) for p in unique],
        entries=entries,
        palettes=out,
    )


def render_tiles(tiles: bytes, entries: Sequence[int], width: int, palette: Sequence[int]) -> Any:
    """The picture `entries` (map words, `width` cells a row) draw from `tiles`
    in the 64 CRAM words `palette`; a tile past the end of `tiles` is colour 0."""
    from PIL import Image

    height = len(entries) // width
    picture = Image.new("RGB", (width * 8, height * 8))
    px = picture.load()
    assert px is not None
    for i, entry in enumerate(entries):
        tile, line = entry & 0x7FF, entry >> 13 & 3
        hflip, vflip = entry >> 11 & 1, entry >> 12 & 1
        base = 32 * tile
        for y in range(8):
            for x in range(8):
                sx, sy = (7 - x if hflip else x), (7 - y if vflip else y)
                b = tiles[base + sy * 4 + sx // 2] if base + 32 <= len(tiles) else 0
                c = b >> 4 if sx % 2 == 0 else b & 15
                px[i % width * 8 + x, i // width * 8 + y] = rgb(palette[line * 16 + c])
    return picture
