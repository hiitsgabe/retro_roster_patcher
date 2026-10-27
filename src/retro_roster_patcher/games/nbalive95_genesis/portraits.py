"""The intro's player portraits: a photo of each team's five starters.

A portrait is two pictures the intro draws together: the head, frame
`HEADS + team * 5 + slot` of the asset table at `logos.LOGO_TABLE` (one per
starting slot, so it stays with the slot whoever is written there), and a
shoulders-and-jersey frame the team shares. A frame is laid out as a logo is
(`logos`) with flags 2: its tiles follow as an EA Huffman stream. The art
loader takes RefPack as well, which is what `write` stores.

Every head is drawn with CRAM line 0, one palette all portraits share: 2-7 a
skin ramp, 11 black, 1, 13 and 15 greys, 14 white; 8-10 and 12 are the
jersey's, which a head does not use (`COLOURS`).

A new head is 32x48 (4x4 tiles over 4x2) in the old one's place when it fits,
else in free space; a starter without a head shot keeps the old picture.
"""

from __future__ import annotations

import io
import struct
from collections.abc import Mapping, Sequence

from ...formats import ea_huff
from ...formats.ea_tdb import EaTdbError, refpack_compress
from .logos import LOGO_BASE, LOGO_TABLE, table_starts
from .space import Space

HEADS = 76
STARTERS = 5
#: The teams with heads: the 1994 NBA's 27. The intro of Toronto, Memphis and
#: New Orleans (slots 27-29) shows no head.
TEAMS = 27
WIDTH, HEIGHT = 32, 48
COLOURS = (1, 2, 3, 4, 5, 6, 7, 11, 13, 14, 15)
_LEVELS = (0, 52, 87, 116, 144, 172, 206, 255)
#: The portrait palette as the intro loads it (CRAM line 0), measured in
#: genesis_plus_gx on Oct 27 2026.
PALETTE = (
    (144, 0, 0), (206, 206, 206), (255, 144, 144), (206, 116, 116),
    (144, 87, 87), (116, 52, 52), (87, 0, 0), (255, 172, 172),
    (206, 116, 0), (0, 0, 116), (0, 0, 206), (0, 0, 0),
    (206, 116, 0), (206, 206, 206), (255, 255, 255), (116, 116, 116),
)  # fmt: skip


def entry(team: int, slot: int) -> int:
    """The asset-table index of `team`'s head for starting slot `slot`."""
    return HEADS + team * STARTERS + slot


def _start(image: bytes | bytearray, index: int) -> int:
    return struct.unpack_from(">I", image, LOGO_TABLE + 4 * index)[0] + LOGO_BASE


def crop(png: bytes):  # -> PIL.Image.Image
    """The head and neck of a head shot, 32x48 on a transparent ground: a 2:3
    box centred on the head (the opaque columns of the subject's top third),
    its top a little above the hair."""
    from PIL import Image

    picture = Image.open(io.BytesIO(png)).convert("RGBA")
    alpha = picture.getchannel("A")
    left, top, right, bottom = alpha.getbbox() or (0, 0, *picture.size)
    head = alpha.crop((left, top, right, top + max(1, (bottom - top) // 3))).getbbox()
    hl, hr = (left + head[0], left + head[2]) if head else (left, right)
    width = round((hr - hl) * 1.3)
    x0 = (hl + hr) // 2 - width // 2
    y0 = top - round(width * 0.05)
    box = picture.crop((x0, y0, x0 + width, y0 + width * HEIGHT // WIDTH))
    return box.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)


def _ycc(r: int, g: int, b: int) -> tuple[float, float, float]:
    """Luma and chroma: nearer in these keeps dark hair dark against the
    palette's red-tinted ramp, where nearest RGB turns it red."""
    y = 0.299 * r + 0.587 * g + 0.114 * b
    return y, (b - y) * 0.564, (r - y) * 0.713


_PAL_YCC = [_ycc(*c) for c in PALETTE]


def tiles(picture) -> bytes:
    """`picture` (32x48 RGBA) as 24 tiles in sprite order (each sprite's tiles
    column by column), every opaque pixel the nearest of `COLOURS`."""
    raw = picture.convert("RGBA").tobytes()
    cache: dict[bytes, int] = {}

    def nearest(pixel: bytes) -> int:
        if pixel[3] < 128:
            return 0
        if pixel not in cache:
            y, cb, cr = _ycc(pixel[0], pixel[1], pixel[2])
            cache[pixel] = min(
                COLOURS,
                key=lambda i: sum(
                    (p - q) ** 2 for p, q in zip(_PAL_YCC[i], (y, cb, cr), strict=True)
                ),
            )
        return cache[pixel]

    px = [nearest(raw[i : i + 4]) for i in range(0, len(raw), 4)]
    out = bytearray()
    for y0, h in ((0, 4), (32, 2)):
        for tx in range(4):
            for ty in range(h):
                for row in range(8):
                    at = (y0 + ty * 8 + row) * WIDTH + tx * 8
                    out += bytes(px[at + i] << 4 | px[at + i + 1] for i in range(0, 8, 2))
    return bytes(out)


def frame(image: bytes | bytearray, index: int, data: bytes) -> bytes:
    """A head frame for `data` (24 tiles) at the old head's hotspot."""
    _, _, hx, hy = struct.unpack_from(">4h", image, _start(image, index))
    head = struct.pack(">7h", WIDTH, HEIGHT, hx, hy, 2, 24, 2)
    entries = struct.pack(">hBBhh", -hy, 0x0F, 0, 0, -hx) + struct.pack(
        ">hBBhh", -hy + 32, 0x0D, 0, 16, -hx
    )
    return head + entries + refpack_compress(data)


def _room(image: bytes | bytearray, index: int) -> int:
    """The bytes a head frame takes up: header, sprites and its Huffman
    stream; 0 for one that does not decode."""
    at = _start(image, index)
    count = struct.unpack_from(">h", image, at + 8)[0]
    body = at + 14 + 8 * count
    try:
        return 14 + 8 * count + ea_huff.stream_size(bytes(image[body : body + 0x2000]))
    except EaTdbError:
        return 0


def write(
    image: bytearray, heads: Mapping[tuple[int, int], bytes], free: Space
) -> list[tuple[int, int]]:
    """Draw each `{(team, slot): head shot}` (slot 0-4) as that starter's
    portrait; the ones written. A photo that will not crop, or with no room
    left for it, keeps the old portrait."""
    done = []
    # Slot by slot, so a shortage of room costs every team its fifth starter
    # before any team its first.
    for (team, slot), png in sorted(heads.items(), key=lambda item: (item[0][1], item[0][0])):
        index = entry(team, slot)
        try:
            blob = frame(image, index, tiles(crop(png)))
        except Exception:  # noqa: BLE001 - Pillow's errors share no base
            continue
        old, room = _start(image, index), _room(image, index)
        at = old if len(blob) <= room else free.alloc(len(blob))
        if at is None:
            continue
        image[at : at + len(blob)] = blob
        struct.pack_into(">I", image, LOGO_TABLE + 4 * index, at - LOGO_BASE)
        if at != old and room and old not in table_starts(image):
            free.release(old, room)
        done.append((team, slot))
    return done


def starters(rosters: Mapping[int, Sequence[str]]) -> dict[tuple[int, int], str]:
    """`{(team, slot): photo URL}` for the first five of each roster."""
    return {
        (team, slot): url
        for team, urls in rosters.items()
        for slot, url in enumerate(urls[:STARTERS])
        if url
    }
