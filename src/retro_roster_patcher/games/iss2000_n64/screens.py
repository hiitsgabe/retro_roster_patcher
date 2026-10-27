"""Screen art: two pictures the patch can replace.

`menu`, the wallpaper behind every menu (main menu, team select, lineups,
options, data screens). It is a 320x240 CI8 picture drawn at 2x across the
screen from asset 20, in 40 tiles of 32x64 texels, 2048 bytes each, row-major
inside a tile and placed out of order in the asset. Screen texel (x, y) for
4 <= x < 316 and 2 <= y < 238 reads asset byte `TILE_START[(y-2)//64][(x-4)//32]
+ 32*((y-2)%64) + (x-4)%32`; the frame outside that stays black. The palette
is 256 RGBA5551 colours, raw in ROM at 0x360F80; nothing else on the
cartridge carries the same 512 bytes. World League, Training and Option tint
it (purple, yellow, green) as they draw it.

`title`, the PRESS START screen: one 320x240 RGBA5551 picture in asset 458
(backdrop, ISS 2000 logo and copyright lines all in it; only PRESS START is
drawn on top). Five 64-texel columns, each 224 rows in 32-row bands stored
bottom-up, then a 16-row strip per column, also bottom-up (`title_texel`).
The screen shows texels 4-316 x 1-237.

Both maps were found by writing each bit of the texel offset into the asset
as two colours and reading the screenshots back.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

from . import rom as R

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


ASSET = 20
PALETTE = 0x360F80
X0, Y0, W, H = 4, 2, 312, 236
TILE_START = (
    (11904, 13952, 16000, 18048, 28288, 30336, 32384, 34432, 77440, 79488),
    (20096, 22144, 24192, 26240, 36480, 38528, 40576, 42624, 85632, 87680),
    (44672, 46720, 48768, 50816, 61056, 63104, 65152, 67200, 81536, 83584),
    (52864, 54912, 56960, 59008, 69248, 71296, 73344, 75392, 89728, 91776),
)

TITLE_ASSET = 458
TITLE_W, TITLE_H = 320, 240
#: Colour counts the title steps down through when the full picture's stream
#: does not fit the room the cartridge has left (fewer colours pack smaller).
TITLE_COLOURS = (None, 256, 128, 64, 32, 16)

SLOTS = ("menu", "title")
SIZES = {"menu": (W, H), "title": (TITLE_W, TITLE_H)}
GUIDES: dict[str, tuple[Guide, ...]] = {
    "menu": (
        Guide(16, 8, 296, 32, "screen title banner (game's)"),
        Guide(0, 40, 312, 160, "menus and panels (change per screen)"),
        Guide(80, 203, 160, 22, "button help (game's)"),
    ),
    "title": (
        Guide(4, 1, 312, 237, "shown on screen"),
        Guide(91, 163, 138, 14, "PRESS START (game's, blinks)"),
    ),
}


def texel_offset(x: int, y: int) -> int:
    u, v = x - X0, y - Y0
    return TILE_START[v // 64][u // 32] + 32 * (v % 64) + u % 32


def title_texel(x: int, y: int) -> int:
    """Texel index in asset 458 of title picture texel (x, y)."""
    column, u = divmod(x, 64)
    if y < 224:
        return column * 14336 + 64 * (32 * (y // 32) + 31 - y % 32) + u
    return 71680 + column * 1024 + 64 * (239 - y) + u


def _colour(word: int) -> tuple[int, int, int]:
    return (
        ((word >> 11) & 31) * 255 // 31,
        ((word >> 6) & 31) * 255 // 31,
        ((word >> 1) & 31) * 255 // 31,
    )


def _word(r: int, g: int, b: int) -> int:
    return (r >> 3) << 11 | (g >> 3) << 6 | (b >> 3) << 1 | 1


def export(image: bytes | bytearray, out_dir: Path) -> list[Path]:
    from PIL import Image

    table = R.assets(image)
    data = R.read_asset(image, table[ASSET])
    pal = [_colour(w) for w in struct.unpack_from(">256H", image, PALETTE)]
    menu = Image.new("RGB", (W, H))
    menu.putdata([pal[data[texel_offset(X0 + x, Y0 + y)]] for y in range(H) for x in range(W)])

    words = struct.unpack_from(">76800H", R.read_asset(image, table[TITLE_ASSET]))
    title = Image.new("RGB", (TITLE_W, TITLE_H))
    title.putdata(
        [_colour(words[title_texel(x, y)]) for y in range(TITLE_H) for x in range(TITLE_W)]
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, pic in (("menu", menu), ("title", title)):
        path = out_dir / f"{name}.png"
        pic.save(path)
        paths.append(path)
    return paths


def _fit(png: bytes, size: tuple[int, int]) -> Image.Image:
    from PIL import Image, ImageOps

    return ImageOps.fit(Image.open(BytesIO(png)).convert("RGB"), size, Image.Resampling.LANCZOS)


def write(image: bytearray, space: R.AssetSpace, pictures: dict[str, bytes]) -> None:
    if "menu" in pictures:
        _write_menu(image, space, pictures["menu"])
    if "title" in pictures:
        _write_title(image, space, pictures["title"])


def _write_menu(image: bytearray, space: R.AssetSpace, png: bytes) -> None:
    from PIL import Image

    q = _fit(png, (W, H)).quantize(
        colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG
    )
    flat = (q.getpalette() or [])[: 256 * 3]
    flat += [0] * (768 - len(flat))
    words = [_word(*flat[i * 3 : i * 3 + 3]) for i in range(256)]
    image[PALETTE : PALETTE + 512] = struct.pack(">256H", *words)
    asset = R.assets(image)[ASSET]
    data = bytearray(R.read_asset(image, asset))
    indices = q.tobytes()  # one byte a pixel, row by row
    for y in range(H):
        for x in range(W):
            data[texel_offset(X0 + x, Y0 + y)] = indices[y * W + x]
    space.put(asset, bytes(data))


def title_stream(pic: Image.Image) -> bytes:
    """Asset 458's bytes for a 320x240 RGB picture."""
    rgb = pic.tobytes()  # three bytes a pixel, row by row
    words = [0] * (TITLE_W * TITLE_H)
    for y in range(TITLE_H):
        for x in range(TITLE_W):
            o = 3 * (y * TITLE_W + x)
            words[title_texel(x, y)] = _word(rgb[o], rgb[o + 1], rgb[o + 2])
    return struct.pack(f">{len(words)}H", *words)


def _write_title(image: bytearray, space: R.AssetSpace, png: bytes) -> None:
    from PIL import Image

    pic = _fit(png, (TITLE_W, TITLE_H))
    asset = R.assets(image)[TITLE_ASSET]
    room = space.room(asset)
    for colours in TITLE_COLOURS:
        shown = pic
        if colours is not None:
            # ponytail: no dither, so flat runs repeat and the LZ stream shrinks
            shown = pic.quantize(
                colors=colours, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
            ).convert("RGB")
        blob = R.lz_compress(title_stream(shown))
        if len(blob) + 3 <= room:
            break
    space.place(asset, blob)
