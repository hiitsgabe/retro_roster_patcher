"""A team logo as the cartridge draws it: the big sprite logo and center ice.

Pillow is the optional `images` extra, imported on first use; both functions
raise `ImportError` without it, and whatever Pillow raises for a picture it
cannot read (`ValueError` for one with nothing opaque in it).
"""

from __future__ import annotations

import io
import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

#: The big logo: six 16x16 sprites, three across and two down, in 15 colours.
LOGO_SIZE = (48, 32)
#: Center ice: 8x4 tiles. The rink is drawn foreshortened, so a logo is
#: squashed to this much of its height, as the cartridge's are.
ICE_TILES = (8, 4)
ICE_SQUASH = 0.65
#: Tiles a center-ice logo may take, the blank tile 0 included (the
#: cartridge's largest).
ICE_MAX_TILES = 30


def _opaque(png: bytes) -> Image.Image:
    """`png` as RGBA, cropped to its opaque pixels."""
    from PIL import Image

    source = Image.open(io.BytesIO(png)).convert("RGBA")
    box = source.getchannel("A").point(lambda a: 255 if a >= 128 else 0).getbbox()
    if box is None:
        raise ValueError("the logo has no opaque pixels")
    return source.crop(box)


def _fit(source: Image.Image, size: tuple[int, int], squash: float = 1.0) -> Image.Image:
    """`source` scaled into `size` (its height times `squash` first), centred on
    a transparent canvas."""
    from PIL import Image

    w, h = source.width, source.height * squash
    k = min(size[0] / w, size[1] / h)
    inner = source.resize((max(1, round(w * k)), max(1, round(h * k))), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.alpha_composite(inner, ((size[0] - inner.width) // 2, (size[1] - inner.height) // 2))
    return canvas


def _bgr555(r: int, g: int, b: int) -> int:
    return r >> 3 | (g >> 3) << 5 | (b >> 3) << 10


def big_logo(png: bytes) -> tuple[bytes, bytes]:
    """The logo-archive entry and its 32-byte palette (colour 0 transparent).

    The entry is the cartridge's own layout: a 22-byte header (pieces, 0x80,
    tiles, x, width, y, height, the bytes of each of the two tile rows, where
    the tiles start, 4 zero bytes), 7 bytes a piece (x, y, first tile, 0xFF
    for 16x16), then the tiles, 4bpp planar, as two rows of 12.
    """
    from PIL import Image

    canvas = _fit(_opaque(png), LOGO_SIZE)
    rgb, alpha = canvas.convert("RGB"), canvas.getchannel("A").tobytes()
    raw = rgb.tobytes()
    solid = b"".join(raw[3 * i : 3 * i + 3] for i, a in enumerate(alpha) if a >= 128)
    strip = Image.frombytes("RGB", (len(solid) // 3, 1), solid)
    palette_image = strip.quantize(colors=15, dither=Image.Dither.NONE)
    mapped = rgb.quantize(palette=palette_image, dither=Image.Dither.NONE).tobytes()
    index = [m + 1 if a >= 128 else 0 for m, a in zip(mapped, alpha, strict=True)]
    # Pillow 13 trims the palette to the colours used; a flat logo has a few.
    rgbs = (palette_image.getpalette() or []) + [0] * 45
    colours = [_bgr555(*rgbs[3 * i : 3 * i + 3]) for i in range(15)]

    width = LOGO_SIZE[0]

    def tile(px: int, py: int) -> bytes:
        out = bytearray(32)
        for y in range(8):
            for x in range(8):
                c, bit = index[(py + y) * width + px + x], 7 - x
                out[2 * y] |= (c & 1) << bit
                out[2 * y + 1] |= (c >> 1 & 1) << bit
                out[16 + 2 * y] |= (c >> 2 & 1) << bit
                out[17 + 2 * y] |= (c >> 3 & 1) << bit
        return bytes(out)

    top, bottom, pieces = [], [], b""
    for k in range(6):
        sx, sy = 16 * (k % 3), 16 * (k // 3)
        pieces += struct.pack("<hhHB", sx, sy, 2 * k, 0xFF)
        top += [tile(sx, sy), tile(sx + 8, sy)]
        bottom += [tile(sx, sy + 8), tile(sx + 8, sy + 8)]
    rows = b"".join(top) + b"".join(bottom)
    header = struct.pack("<BBHhHhHHHH", 6, 0x80, 24, 0, width, 0, LOGO_SIZE[1], 0x180, 0x180, 64)
    return header + bytes(4) + pieces + rows, struct.pack("<16H", 0, *colours)


def center_ice(png: bytes, rink: tuple[int, ...]) -> tuple[bytes, bytes]:
    """The center-ice tiles (uncompressed, tile 0 blank) and their map, the
    logo drawn in the nearest of `rink`'s colours 1-15. White and near-white
    are left out, for the ice to show through."""
    from PIL import Image

    source = _opaque(png)
    choices = [((w & 31) << 3, (w >> 5 & 31) << 3, (w >> 10 & 31) << 3) for w in rink]
    cols, rows = ICE_TILES
    full = (8 * cols, 8 * rows)
    scale = 1.0
    while True:
        box = (round(full[0] * scale), round(full[1] * scale))
        canvas = Image.new("RGBA", full, (0, 0, 0, 0))
        canvas.alpha_composite(
            _fit(source, box, ICE_SQUASH), ((full[0] - box[0]) // 2, (full[1] - box[1]) // 2)
        )
        tiles, layout = [bytes(32)], []
        for ty in range(rows):
            for tx in range(cols):
                t = bytearray(32)
                for y in range(8):
                    for x in range(8):
                        r, g, b, a = canvas.getpixel((8 * tx + x, 8 * ty + y))  # type: ignore[misc]
                        if a < 128 or min(r, g, b) >= 200:
                            continue
                        c = min(
                            range(1, 16),
                            key=lambda i: sum(
                                (p - q) ** 2 for p, q in zip(choices[i], (r, g, b), strict=True)
                            ),
                        )
                        t[4 * y + x // 2] |= c << (0 if x % 2 else 4)
                if any(t):
                    tiles.append(bytes(t))
                    layout.append((len(tiles) - 1) | 0x0400)
                else:
                    layout.append(0)
        if len(tiles) <= ICE_MAX_TILES:
            break
        scale *= 0.9
    header = struct.pack("<HHH", cols, rows, 0x0308)
    return b"".join(tiles), header + struct.pack(f"<{len(layout)}H", *layout)
