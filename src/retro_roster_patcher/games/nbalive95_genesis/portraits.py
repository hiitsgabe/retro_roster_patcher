"""The intro's player portraits: a photo of each team's five starters.

The intro draws a portrait from two frames of the asset table at
`logos.LOGO_TABLE`: a head, `HEADS + team * 5 + slot` (one per starting slot,
so it stays with the slot whoever is written there), and behind it a
shoulders-and-jersey body, one of `BODIES`. Both are laid out as a logo is
(`logos`) with flags 2: the tiles follow as an EA Huffman stream. The art
loader takes RefPack too, which is what `write` stores.

All portraits share CRAM line 0 (`PALETTE`): 2-7 a skin ramp, 11 black, 1, 13
and 15 greys, 14 white. 8-10 and 12 are the home team's colours, loaded per
team, so a photo is drawn without them (`COLOURS`).

A new portrait is the head shot's head and shoulders, 64x64 over the whole
picture the two frames covered (80x68 anchored at its bottom centre), so every
body frame is blanked (`blank_bodies`) and nothing of the old picture shows.
Each is about 800 bytes; they go where the old heads and bodies were (the
frames lie end to end, so freed neighbours join) and in free space. The image
cannot grow past 2 MiB: the cartridge's save RAM is mapped from 0x200000. A
starter without a head shot keeps his old head, without its body.
"""

from __future__ import annotations

import io
import struct
from collections.abc import Mapping

from ...formats import ea_huff
from ...formats.ea_tdb import EaTdbError, refpack_compress
from .logos import LOGO_BASE, LOGO_TABLE, TABLE_SIZE
from .space import Space

HEADS = 76
BODIES = range(55, 76)
STARTERS = 5
#: The teams with heads: the 1994 NBA's 27. The All-Star slots' intro shows none.
TEAMS = 27
SIDE = 64
#: The share of a head shot's height the square crop takes, from its top: head
#: and shoulders, as ESPN frames every player alike.
CROP = 0.85
#: The portrait palette as the intro loads it (CRAM line 0), measured in
#: genesis_plus_gx on Oct 27 2026.
PALETTE = (
    (144, 0, 0), (206, 206, 206), (255, 144, 144), (206, 116, 116),
    (144, 87, 87), (116, 52, 52), (87, 0, 0), (255, 172, 172),
    (206, 116, 0), (0, 0, 116), (0, 0, 206), (0, 0, 0),
    (206, 116, 0), (206, 206, 206), (255, 255, 255), (116, 116, 116),
)  # fmt: skip
COLOURS = (1, 2, 3, 4, 5, 6, 7, 11, 13, 14, 15)
#: A frame with one transparent tile, for the bodies.
EMPTY = struct.pack(">7h", 8, 8, 0, 0, 1, 1, 0) + struct.pack(">hBBhh", 0, 0, 0, 0, 0) + bytes(32)
#: Where each 4x4 sprite of a portrait sits from the anchor, in tile order.
_QUARTERS = ((-32, -66), (0, -66), (-32, -34), (0, -34))


def entry(team: int, slot: int) -> int:
    """The asset-table index of `team`'s head for starting slot `slot`."""
    return HEADS + team * STARTERS + slot


def _start(image: bytes | bytearray, index: int) -> int:
    return struct.unpack_from(">I", image, LOGO_TABLE + 4 * index)[0] + LOGO_BASE


def _point(image: bytearray, index: int, at: int) -> None:
    struct.pack_into(">I", image, LOGO_TABLE + 4 * index, at - LOGO_BASE)


def crop(png: bytes):  # -> PIL.Image.Image
    """A head shot's head and shoulders, 64x64: the square `CROP` of its
    height tall, centred, from its top."""
    from PIL import Image

    picture = Image.open(io.BytesIO(png)).convert("RGBA")
    width, height = picture.size
    side = round(height * CROP)
    x0 = (width - side) // 2
    square = picture.crop((x0, 0, x0 + side, side))
    return square.resize((SIDE, SIDE), Image.Resampling.LANCZOS)


def _ycc(r: int, g: int, b: int) -> tuple[float, float, float]:
    """Luma and chroma: nearer in these keeps dark hair dark against the
    palette's red-tinted ramp, where nearest RGB turns it red."""
    y = 0.299 * r + 0.587 * g + 0.114 * b
    return y, (b - y) * 0.564, (r - y) * 0.713


_PAL_YCC = [_ycc(*c) for c in PALETTE]


def tiles(picture) -> bytes:
    """`picture` (64x64 RGBA) as 64 tiles in sprite order: the four 4x4
    sprites of `_QUARTERS`, each one's tiles column by column; every opaque
    pixel the nearest of `COLOURS`."""
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
    for qx, qy in _QUARTERS:
        x0, y0 = qx + 32, qy + 66
        for tx in range(4):
            for ty in range(4):
                for row in range(8):
                    at = (y0 + ty * 8 + row) * SIDE + x0 + tx * 8
                    out += bytes(px[at + i] << 4 | px[at + i + 1] for i in range(0, 8, 2))
    return bytes(out)


def frame(data: bytes) -> bytes:
    """A 64x64 portrait frame for `data` (64 tiles), anchored at its bottom
    centre as the old head and body are."""
    head = struct.pack(">7h", SIDE, SIDE, SIDE // 2, SIDE + 2, len(_QUARTERS), 64, 2)
    entries = b"".join(
        struct.pack(">hBBhh", y, 0x0F, 0, 16 * k, x) for k, (x, y) in enumerate(_QUARTERS)
    )
    return head + entries + refpack_compress(data)


def _room(image: bytes | bytearray, index: int) -> int:
    """The bytes a retail frame takes up: header, sprites and its Huffman
    stream; 0 for one that does not decode."""
    at = _start(image, index)
    count = struct.unpack_from(">h", image, at + 8)[0]
    body = at + 14 + 8 * count
    try:
        return 14 + 8 * count + ea_huff.stream_size(bytes(image[body : body + 0x2000]))
    except EaTdbError:
        return 0


def _release(image: bytearray, index: int, free: Space) -> None:
    """Give frame `index`'s bytes back when no other entry reads them."""
    old = _start(image, index)
    if sum(_start(image, i) == old for i in range(TABLE_SIZE)) == 1:
        room = _room(image, index)
        if room:
            free.release(old, room)


def blank_bodies(image: bytearray, free: Space) -> None:
    """Point every body frame at one empty frame."""
    at = free.alloc(len(EMPTY))
    if at is None:
        return
    image[at : at + len(EMPTY)] = EMPTY
    for index in BODIES:
        _release(image, index, free)
        _point(image, index, at)


def write(
    image: bytearray, heads: Mapping[tuple[int, int], bytes], free: Space
) -> list[tuple[int, int]]:
    """Draw each `{(team, slot): head shot}` (slot 0-4) as that starter's
    portrait; the ones written. A photo that will not decode, or with no room
    left for it, keeps the old head."""
    blobs = {}
    for key, png in sorted(heads.items()):
        try:
            blobs[key] = frame(tiles(crop(png)))
        except Exception:  # noqa: BLE001 - Pillow's errors share no base
            continue
    empty = free.alloc(len(EMPTY))
    if empty is None:
        return []
    image[empty : empty + len(EMPTY)] = EMPTY
    # Every old head first, so neighbours join before the first new one is
    # placed; a head with no room left is then drawn empty, its old bytes gone.
    for team, slot in blobs:
        _release(image, entry(team, slot), free)
    done = []
    for (team, slot), blob in blobs.items():
        at = free.alloc(len(blob))
        if at is None:
            _point(image, entry(team, slot), empty)
            continue
        image[at : at + len(blob)] = blob
        _point(image, entry(team, slot), at)
        done.append((team, slot))
    return done


def starters(rosters: Mapping[int, list[str]]) -> dict[tuple[int, int], str]:
    """`{(team, slot): photo URL}` for the first five of each roster."""
    return {
        (team, slot): url
        for team, urls in rosters.items()
        for slot, url in enumerate(urls[:STARTERS])
        if url
    }
