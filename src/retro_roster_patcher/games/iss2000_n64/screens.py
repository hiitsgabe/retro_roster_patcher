"""Screen art: the wallpaper behind every menu (main menu, team select,
lineups, options, data screens).

It is a 320x240 CI8 picture drawn at 2x across the screen from asset 20,
in 40 tiles of 32x64 texels, 2048 bytes each, row-major inside a tile and
placed out of order in the asset. Screen texel (x, y) for 4 <= x < 316 and
2 <= y < 238 reads asset byte `TILE_START[(y-2)//64][(x-4)//32] +
32*((y-2)%64) + (x-4)%32`; the frame outside that stays black. Found by
writing each bit of the byte offset into the asset as two colours and
reading the 17 screenshots back. The palette is 256 RGBA5551 colours, raw in
ROM at 0x360F80; nothing else on the cartridge carries the same 512 bytes.
"""

from __future__ import annotations

import struct
from io import BytesIO
from pathlib import Path

from . import rom as R

ASSET = 20
PALETTE = 0x360F80
X0, Y0, W, H = 4, 2, 312, 236
TILE_START = (
    (11904, 13952, 16000, 18048, 28288, 30336, 32384, 34432, 77440, 79488),
    (20096, 22144, 24192, 26240, 36480, 38528, 40576, 42624, 85632, 87680),
    (44672, 46720, 48768, 50816, 61056, 63104, 65152, 67200, 81536, 83584),
    (52864, 54912, 56960, 59008, 69248, 71296, 73344, 75392, 89728, 91776),
)
SLOTS = ("menu",)


def texel_offset(x: int, y: int) -> int:
    u, v = x - X0, y - Y0
    return TILE_START[v // 64][u // 32] + 32 * (v % 64) + u % 32


def _colour(word: int) -> tuple[int, int, int]:
    return (
        ((word >> 11) & 31) * 255 // 31,
        ((word >> 6) & 31) * 255 // 31,
        ((word >> 1) & 31) * 255 // 31,
    )


def export(image: bytes | bytearray, out_dir: Path) -> list[Path]:
    from PIL import Image

    data = R.read_asset(image, R.assets(image)[ASSET])
    pal = [_colour(w) for w in struct.unpack_from(">256H", image, PALETTE)]
    pic = Image.new("RGB", (W, H))
    pic.putdata([pal[data[texel_offset(X0 + x, Y0 + y)]] for y in range(H) for x in range(W)])
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "menu.png"
    pic.save(path)
    return [path]


def write(image: bytearray, space: R.AssetSpace, pictures: dict[str, bytes]) -> None:
    png = pictures.get("menu")
    if png is None:
        return
    from PIL import Image, ImageOps

    pic = ImageOps.fit(Image.open(BytesIO(png)).convert("RGB"), (W, H), Image.Resampling.LANCZOS)
    q = pic.quantize(
        colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG
    )
    flat = (q.getpalette() or [])[: 256 * 3]
    flat += [0] * (768 - len(flat))
    words = [
        (flat[i * 3] >> 3) << 11 | (flat[i * 3 + 1] >> 3) << 6 | (flat[i * 3 + 2] >> 3) << 1 | 1
        for i in range(256)
    ]
    image[PALETTE : PALETTE + 512] = struct.pack(">256H", *words)
    asset = R.assets(image)[ASSET]
    data = bytearray(R.read_asset(image, asset))
    indices = q.tobytes()  # one byte a pixel, row by row
    for y in range(H):
        for x in range(W):
            data[texel_offset(X0 + x, Y0 + y)] = indices[y * W + x]
    space.put(asset, bytes(data))
