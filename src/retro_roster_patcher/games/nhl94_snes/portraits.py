"""Player portraits: the 48x48 pictures on Game Setup, Matchups and Edit Lines.

Measured in Mesen 2 on the retail cartridge:

* Two loaders read them. $9D:CC36 loads the six of each team's Best line
  when Game Setup or Matchups opens (Game Setup shows them as its cursor
  moves down the options); $9D:C9A8 loads one, the player under the cursor
  on Edit Lines. Both take the stream `TABLE` names for `team * 26 + record`,
  `record` being the line table's 1-based entry less one ($9D:C973).
* An entry is a 3-byte pointer and a 0. The stream goes through the art
  loader at $80:C373 (EA Huffman on the cartridge; it takes RefPack too) and
  decodes to 0x480 bytes: 6x6 tiles of 8x8 pixels, 4 bits a pixel, two to a
  byte with the left one high, each tile's rows top to bottom.
* The colours are the team's 16, from the pointer at `PALETTES + 4 * team`
  ($9D:CC04 copies them to palette row 1 or 5): 0 is transparent, 1-12 a
  skin ramp, 13-15 the team's colours (14 the jersey, 13 and 15 its
  stripes), which `identity` writes for a mapped slot before this runs.
* 156 of the 728 entries are photographs, the Best line's goalie and the
  stars. The rest point at four drawings every team shares, a goalie and
  three skaters, coloured by the team's palette.

`write` gives each patched player his head shot, as RefPack in `REGION` (the
last 544 KiB of the 2 MiB image, one stream never crossing a 32 KiB bank),
fetched in the order `ranked` gives until the room is full, and a player
without one, or past the room, the drawing. The cartridge's streams stay
where they are.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING

from ...formats import snes
from ...formats.ea_tdb import refpack_compress

if TYPE_CHECKING:
    from PIL import Image

TABLE = 0xECD53  # $9D:CD53
PALETTES = 0xED8B3  # $9D:D8B3
TEAMS = 28
RECORDS = 26  # entries per team
SIDE = 48
RAW_SIZE = SIDE * SIDE // 2

GENERIC_GOALIE = 0x99C64E
GENERIC_SKATERS = (0x99E1E8, 0x8FFE74, 0x9AB13D)

#: Everything in the 2 MiB image past `screens.REGION`: identity's region
#: (whose worst case ends at 0x118440) and screens' three banks come first, and
#: tests/games/nhl94_snes/test_regions.py checks neither can reach this. Room
#: for about 850 faces of some 640 bytes: every player of every team.
REGION = range(0x178000, 0x200000)
_BANK = 0x8000
_TWO_MIB = 0x200000

# How far above the hair the cartridge's photographs start: a row or two of 48.
_HEADROOM = 0.03
# The square's side as a share of the subject's height: the head about as wide
# as the cartridge's photographs have it.
_SCALE = 0.68
# A team colour (13-15) is taken only when it is this many times closer than
# the nearest skin tone, so a highlight on a forehead stays skin and a jersey
# takes the team's colour.
_TEAM_COLOUR_BIAS = 2

# Head shots fetched at a time, in rank order: at most this many past the room.
_BATCH = 32
# A head shot whose most saturated opaque pixel is below this is grey all over:
# the NHL API's silhouette for a player without a photo (0; real ones 208+).
_GREY = 16


def _off(address: int) -> int:
    return (address >> 16 & 0x7F) * _BANK + (address & 0x7FFF)


def _cpu(offset: int) -> int:
    return 0x800000 | offset // _BANK << 16 | 0x8000 | offset % _BANK


def _entry(team: int, record: int) -> int:
    return TABLE + 4 * (team * RECORDS + record)


def _pointer(rom: bytes | bytearray, team: int, record: int) -> int:
    return int.from_bytes(rom[_entry(team, record) : _entry(team, record) + 3], "little")


def table_ok(rom: bytes | bytearray) -> bool:
    """Is the portrait table there: every entry a pointer to an art stream
    (magic `xx FB`) that decodes to 0x480 bytes?"""
    if len(rom) < _entry(TEAMS, 0):
        return False
    for i in range(TEAMS * RECORDS):
        entry = TABLE + 4 * i
        address = _pointer(rom, 0, i)
        at = _off(address)
        if rom[entry + 3] or address < 0x800000 or at + 5 > len(rom):
            return False
        if rom[at + 1] != 0xFB or int.from_bytes(rom[at + 2 : at + 5], "big") != RAW_SIZE:
            return False
    return True


def palette(rom: bytes | bytearray, team: int) -> list[tuple[int, int, int]]:
    """The team's 16 portrait colours, as RGB."""
    at = _off(int.from_bytes(rom[PALETTES + 4 * team : PALETTES + 4 * team + 3], "little"))
    words = [int.from_bytes(rom[at + 2 * i : at + 2 * i + 2], "little") for i in range(16)]
    return [((w & 31) << 3, (w >> 5 & 31) << 3, (w >> 10 & 31) << 3) for w in words]


def headshot_portrait(png: bytes) -> Image.Image | None:
    """A provider head shot cropped and scaled to a 48x48 portrait, or None
    for a grey one (the NHL API's silhouette for a player without a photo),
    which would draw as a blank skin-coloured head.

    A square centred on the head (the opaque columns in the top third of the
    subject, or the whole picture when it has no transparency), the hair a
    row or two from the top as on the cartridge's photographs.
    """
    from PIL import Image

    picture = Image.open(io.BytesIO(png)).convert("RGBA")
    alpha = picture.getchannel("A")
    saturation = picture.convert("RGB").convert("HSV").getchannel("S")
    if not any(saturation.histogram(mask=alpha)[_GREY:]):
        return None
    left, top, right, bottom = alpha.getbbox() or (0, 0, *picture.size)
    head = alpha.crop((left, top, right, top + max(1, (bottom - top) // 3))).getbbox()
    centre = left + ((head[0] + head[2]) // 2 if head else (right - left) // 2)
    side = max(1, round((bottom - top) * _SCALE))
    x0, y0 = centre - side // 2, top - round(side * _HEADROOM)
    square = picture.crop((x0, y0, x0 + side, y0 + side))
    return square.resize((SIDE, SIDE), Image.Resampling.LANCZOS)


def encode(image: Image.Image, colours: Sequence[tuple[int, int, int]]) -> bytes:
    """`image` (48x48) as the game's 0x480 bytes, each pixel the nearest of
    `colours` 1-15 and a transparent one 0."""
    weight = [_TEAM_COLOUR_BIAS**2 if i >= 13 else 1 for i in range(16)]
    cache: dict[bytes, int] = {}

    def nearest(pixel: bytes) -> int:
        if pixel[3] < 128:
            return 0
        if pixel not in cache:
            r, g, b = pixel[:3]
            cache[pixel] = min(
                range(1, 16),
                key=lambda i: (
                    weight[i]
                    * (
                        (r - colours[i][0]) ** 2
                        + (g - colours[i][1]) ** 2
                        + (b - colours[i][2]) ** 2
                    )
                ),
            )
        return cache[pixel]

    raw = image.convert("RGBA").tobytes()
    pixels = [nearest(raw[i : i + 4]) for i in range(0, len(raw), 4)]
    out = bytearray()
    for tile in range(36):
        x, y = tile % 6 * 8, tile // 6 * 8
        for row in range(y, y + 8):
            for col in range(x, x + 8, 2):
                at = row * SIDE + col
                out.append(pixels[at] << 4 | pixels[at + 1])
    return bytes(out)


def ranked(lines: Mapping[int, bytes], counts: Mapping[int, int]) -> list[tuple[int, int]]:
    """Every `(team, record)` in the order head shots are drawn while there is
    room: each team's records as its line table names them (the Best line
    first, the six Game Setup and Matchups show), then the rest; the n-th of
    every team before the (n+1)-th of any."""
    order: dict[int, list[int]] = {}
    for team, count in counts.items():
        seen = list(dict.fromkeys(b - 1 for b in lines.get(team, b"") if 0 < b <= count))
        order[team] = seen + [r for r in range(count) if r not in seen]
    depth = max((len(records) for records in order.values()), default=0)
    return [
        (team, records[n])
        for n in range(depth)
        for team, records in sorted(order.items())
        if n < len(records)
    ]


def write(
    rom: bytearray,
    players: Mapping[int, Sequence[tuple[bool, str]]],
    lines: Mapping[int, bytes],
    load: Callable[[list[str]], Sequence[Image.Image | None]] | None = None,
) -> tuple[int, int]:
    """Point each patched record at a head shot or a drawing; `rom` headerless,
    in place. `players[team]` is `(is_goalie, head shot URL or "")` per
    record, in record order; `load` turns URLs into portraits (None for a
    failure), called a batch at a time in `ranked` order and no more once the
    room is full; without it every record gets a drawing. Grows the image to
    2 MiB when a head shot is written; the caller fixes the checksum. Returns
    (head shots, drawings).

    Records past the 26th have no entry of their own and are left alone. The
    region is rewritten, so a record a previous patch pointed into it that
    this one does not write gets a drawing: a re-patch that maps only some
    teams leaves the others drawings. An image larger than 2 MiB is another
    hack's layout: no head shots, the region left alone.
    """
    urls = {
        (team, record): url
        for team, records in players.items()
        for record, (_goalie, url) in enumerate(records[:RECORDS])
    }
    goalies = {
        (team, record)
        for team, records in players.items()
        for record, (goalie, _url) in enumerate(records[:RECORDS])
        if goalie
    }
    if len(rom) == _TWO_MIB:
        for team in range(TEAMS):
            for record in range(RECORDS):
                if (team, record) not in urls and _off(_pointer(rom, team, record)) in REGION:
                    # ponytail: an earlier patch wrote goalies as records 0-1
                    _point(rom, team, record, GENERIC_SKATERS[record % 3], goalie=record < 2)
        rom[REGION.start : REGION.stop] = b"\xff" * len(REGION)

    counts = {team: len(records[:RECORDS]) for team, records in players.items()}
    order = ranked(lines, counts)
    wanted = list(dict.fromkeys(urls[spot] for spot in order if urls[spot]))
    faces: dict[str, Image.Image | None] = {}
    room = load is not None and len(rom) <= _TWO_MIB
    at, drawn = REGION.start, 0
    for team, record in order:
        url = urls[team, record]
        if url and room and load is not None:
            if url not in faces:  # the next one wanted: fetch it and a batch after
                batch = wanted[len(faces) : len(faces) + _BATCH]
                faces.update(zip(batch, load(batch), strict=True))
            face = faces[url]
            if face is not None:
                data = refpack_compress(encode(face, palette(rom, team)))
                if at % _BANK + len(data) > _BANK:
                    at += -at % _BANK
                if at + len(data) <= REGION.stop:
                    snes.grow(rom, _TWO_MIB)
                    rom[at : at + len(data)] = data
                    _point(rom, team, record, _cpu(at))
                    at, drawn = at + len(data), drawn + 1
                    continue
                room = False  # full: the rest get drawings, and nothing more is fetched
        _point(rom, team, record, GENERIC_SKATERS[record % 3], goalie=(team, record) in goalies)
    return drawn, len(urls) - drawn


def forget(rom: bytearray, players: Mapping[int, Sequence[tuple[bool, str]]]) -> None:
    """`patch(portraits=False)` on an image an earlier patch drew: a patched
    record it pointed into `REGION` gets the drawing, since that face was the
    earlier player's; the cartridge's pointers and the region stay."""
    if len(rom) != _TWO_MIB:
        return
    for team, records in players.items():
        for record, (goalie, _url) in enumerate(records[:RECORDS]):
            if _off(_pointer(rom, team, record)) in REGION:
                _point(rom, team, record, GENERIC_SKATERS[record % 3], goalie=goalie)


def _point(rom: bytearray, team: int, record: int, address: int, *, goalie: bool = False) -> None:
    """Aim an entry at `address`, or at the goalie drawing for a goalie."""
    at = _entry(team, record)
    rom[at : at + 3] = (GENERIC_GOALIE if goalie else address).to_bytes(3, "little")
