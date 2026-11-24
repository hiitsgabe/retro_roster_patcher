"""Centre ice: the club's crest the rink shows under the opening faceoff, as
NHL 2005 (PS2) draws it (`nhl05_ps2.ice`).

`GAMEDATA/ICETXTRS.BIG` holds one RefPack-compressed 256x256 SHPM picture per
arena, `ICE_<n>.msh` (one shape), `n` the arena's SARE `LOGO`. Unlike NHL
2005's, the rink is not that one at half size: the circle sits elsewhere, and
the red centre line and the blue faceoff dot run through it, both kept. The
All-Star game's ring is smaller and broken by its stars, so a club drawn on
it gets a regular season rink's ring instead.
"""

from __future__ import annotations

import io
from collections.abc import Collection, Mapping
from typing import TYPE_CHECKING

from ...formats.ea_tdb import bigf_parse
from ..nhl05_ps2.ice import logo_ids
from . import screens

if TYPE_CHECKING:
    from PIL import Image

__all__ = ["ARCHIVE", "draw", "logo_ids", "redraw"]

ARCHIVE = "PSP_GAME/USRDIR/GAMEDATA/ICETXTRS.BIG"
#: The centre circle's centre and its ring's inner and outer radius, measured.
CENTRE = (110, 99)
RING = (80, 85)
#: A regular season rink's ring, drawn on the ones `ringed`: radius, width and
#: colour, measured.
RING_DRAWN = (83, 2, (160, 170, 198))
#: The faceoff dot's radius, the centre line's columns, how far from the centre
#: the crest and the arena's lettering reach, and the crest's box.
DOT = 4
LINE = (105, 116)
CLEAR = 100
CREST = 140
FADE = 0.5


def draw(original: Image.Image, crest: bytes, ring: bool = False) -> Image.Image:
    """`original` with its centre circle holding `crest` (an image file), and
    with `ring` a regular season rink's ring drawn around it in place of its
    own."""
    from PIL import Image, ImageDraw

    picture = original.convert("RGBA")
    # The most common colour: never None with every pixel allowed a colour.
    ice = max(picture.getcolors(picture.width * picture.height))[1]  # type: ignore[arg-type,type-var]
    cx, cy = CENTRE
    clear = Image.new("L", picture.size, 0)
    ImageDraw.Draw(clear).ellipse((cx - CLEAR, cy - CLEAR, cx + CLEAR, cy + CLEAR), fill=255)
    for y in range(max(0, cy - CLEAR), min(picture.height, cy + CLEAR + 1)):
        for x in range(max(0, cx - CLEAR), min(picture.width, cx + CLEAR + 1)):
            r, g, b, _ = picture.getpixel((x, y))  # type: ignore[misc]
            d2 = (x - cx) ** 2 + (y - cy) ** 2
            blue = b > r + 12
            band = RING[0] ** 2 <= d2 <= RING[1] ** 2 and not ring
            line = LINE[0] <= x < LINE[1] and r > b + 20
            if (blue and (band or d2 <= DOT**2)) or line:
                clear.putpixel((x, y), 0)
    picture.paste(Image.new("RGBA", picture.size, ice), (0, 0), clear)  # type: ignore[arg-type]
    if ring:
        radius, width, colour = RING_DRAWN
        ImageDraw.Draw(picture).ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            outline=(*colour, 255),
            width=width,
        )
    art = Image.open(io.BytesIO(crest)).convert("RGBA")
    art.thumbnail((CREST, CREST), Image.Resampling.LANCZOS)
    art.putalpha(art.getchannel("A").point(lambda a: round(a * FADE)))
    picture.alpha_composite(art, (cx - art.width // 2, cy - art.height // 2))
    return picture


def redraw(
    archive: bytes, crests: Mapping[int, bytes], ringed: Collection[int] = ()
) -> tuple[bytes, list[int]]:
    """`archive` (`ICETXTRS.BIG`) with each `{logo id: crest}` centre ice
    redrawn in place, the `ringed` ones with a regular ring, and the ids
    drawn. A picture that cannot be read, or fits at no palette size, stays."""
    out = bytearray(archive)
    entries = {e.name.lower(): e for e in bigf_parse(archive)}
    drawn = []
    for logo, crest in crests.items():
        name = f"ICE_{logo:03d}.msh"
        entry = entries.get(name.lower())
        if entry is None:
            continue
        member = archive[entry.offset : entry.offset + entry.size]
        copy = screens.Copy(ARCHIVE, name, None, "icex")
        original = screens.read_picture(member, copy)
        if original is None:
            continue
        data = screens.fitted(member, copy, draw(original, crest, logo in ringed))
        if data is None:
            continue
        out[entry.offset : entry.offset + entry.size] = data.ljust(entry.size, b"\0")
        drawn.append(logo)
    return bytes(out), drawn
