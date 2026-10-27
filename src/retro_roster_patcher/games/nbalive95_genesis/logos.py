"""Team-select logos: one sprite frame per team, drawn in a palette all teams share.

`LOGO_TABLE` holds one big-endian long per team, in team index order (Atlanta 0
.. Washington 26, then the East and West All-Stars and the Slammers); a frame
starts at that long plus `LOGO_BASE`. A frame is six big-endian words -- width,
height, hotspot x and y, sprite count, tile count -- two zero bytes, then 8 bytes
per sprite (y, size byte `(w-1)<<2 | (h-1)`, 0, first tile, x) and the 4bpp
tiles, each sprite's tiles column by column as the VDP reads them.

Every logo is drawn with CRAM line 2, the 16 colours at `PALETTE` (colour 0 is
transparent), so a new logo is matched to those colours and not the reverse.
A new frame is 48x40 (30 tiles, the most a retail logo uses) and goes in the
cartridge's free space (`space.Space`); the team's old frame stays where it was.
"""

from __future__ import annotations

import io
import struct

from .space import Space

LOGO_TABLE = 0x504DA
LOGO_BASE = 0x4F7CE
PALETTE = 0xA4218
WIDTH, HEIGHT = 48, 40
#: (x, y, width in tiles, height in tiles) of the four sprites covering 48x40.
_SPRITES = ((0, 0, 4, 4), (32, 0, 2, 4), (0, 32, 4, 1), (32, 32, 2, 1))
_LEVELS = (0, 52, 87, 116, 144, 172, 206, 255)


def palette(image: bytes | bytearray) -> list[tuple[int, int, int]]:
    """The shared logo palette as RGB, colour 0 (transparent) included."""
    words = struct.unpack_from(">16H", image, PALETTE)
    return [(_LEVELS[(w >> 1) & 7], _LEVELS[(w >> 5) & 7], _LEVELS[(w >> 9) & 7]) for w in words]


def pixels(png: bytes, pal: list[tuple[int, int, int]]) -> list[list[int]]:
    """`png` fitted inside 48x40 and centred, as palette indices: 0 where it is
    transparent, else the nearest of colours 1-15."""
    from PIL import Image

    img = Image.open(io.BytesIO(png)).convert("RGBA")
    img.thumbnail((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (WIDTH, HEIGHT))
    canvas.paste(img, ((WIDTH - img.width) // 2, (HEIGHT - img.height) // 2))
    cache: dict[tuple[int, int, int], int] = {}

    def nearest(rgb: tuple[int, int, int]) -> int:
        if rgb not in cache:
            cache[rgb] = min(
                range(1, 16),
                key=lambda i: sum((a - b) ** 2 for a, b in zip(pal[i], rgb, strict=True)),
            )
        return cache[rgb]

    rgba = canvas.tobytes()
    return [
        [
            0 if (p := rgba[4 * (y * WIDTH + x) :][:4])[3] < 128 else nearest((p[0], p[1], p[2]))
            for x in range(WIDTH)
        ]
        for y in range(HEIGHT)
    ]


def frame(rows: list[list[int]]) -> bytes:
    """A 48x40 sprite frame of palette indices."""
    head = struct.pack(">6H", WIDTH, HEIGHT, 0, 0, len(_SPRITES), WIDTH * HEIGHT // 64) + bytes(2)
    entries, tiles = b"", b""
    for x0, y0, w, h in _SPRITES:
        entries += struct.pack(">HBBHH", y0, (w - 1) << 2 | (h - 1), 0, len(tiles) // 32, x0)
        for tx in range(w):  # column by column
            for ty in range(h):
                for y in range(8):
                    row = rows[y0 + ty * 8 + y][x0 + tx * 8 : x0 + tx * 8 + 8]
                    tiles += bytes(row[i] << 4 | row[i + 1] for i in range(0, 8, 2))
    return head + entries + tiles


def write(image: bytearray, logos: dict[int, bytes], free: Space) -> list[int]:
    """Draw each `{team index: png}` logo into `image`; the teams written, in
    order. A logo that does not fit the free space left is skipped."""
    pal = palette(image)
    done = []
    for team, png in sorted(logos.items()):
        blob = frame(pixels(png, pal))
        start = free.alloc(len(blob))
        if start is None:
            break
        old = frame_start(image, team)
        image[start : start + len(blob)] = blob
        struct.pack_into(">I", image, LOGO_TABLE + 4 * team, start - LOGO_BASE)
        if old not in table_starts(image):
            free.release(old, raw_size(image, old))
        done.append(team)
    return done


def frame_start(image: bytes | bytearray, index: int) -> int:
    """Where the asset table's frame `index` starts."""
    return struct.unpack_from(">I", image, LOGO_TABLE + 4 * index)[0] + LOGO_BASE


#: Entries in the asset table (logos, portraits and the rest the game draws).
TABLE_SIZE = 700


def table_starts(image: bytes | bytearray) -> set[int]:
    """Every frame the asset table points at."""
    return {frame_start(image, i) for i in range(TABLE_SIZE)}


def raw_size(image: bytes | bytearray, start: int) -> int:
    """The bytes an uncompressed frame (flags 0) takes up."""
    count, tiles, _ = struct.unpack_from(">3h", image, start + 8)
    return 14 + 8 * count + 32 * tiles


def read(image: bytes | bytearray, team: int) -> list[list[int]]:
    """Team `team`'s logo as rows of palette indices, its own width and height."""
    start = struct.unpack_from(">I", image, LOGO_TABLE + 4 * team)[0] + LOGO_BASE
    w, h, _, _, count, _ = struct.unpack_from(">6H", image, start)
    tiles = start + 14 + 8 * count
    rows = [[0] * w for _ in range(h)]
    hx, hy = struct.unpack_from(">hh", image, start + 4)
    for i in range(count):
        y0, size, _, first, x0 = struct.unpack_from(">HBBHH", image, start + 14 + 8 * i)
        tw, th = (size >> 2) + 1, (size & 3) + 1
        for tx in range(tw):
            for ty in range(th):
                base = tiles + 32 * (first + tx * th + ty)
                for y in range(8):
                    for x in range(8):
                        b = image[base + y * 4 + x // 2]
                        px, py = x0 + hx + tx * 8 + x, y0 + hy + ty * 8 + y
                        if 0 <= px < w and 0 <= py < h:
                            rows[py][px] = (b >> 4) if x % 2 == 0 else b & 15
    return rows
