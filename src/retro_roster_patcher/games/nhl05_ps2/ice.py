"""Centre ice: the club's crest the rink shows under the opening faceoff.

`GAMEDATA/ICETXTRS.BIG` holds one RefPack-compressed 512x512 picture per
arena, `ICE_<n>.ssh` (the square packing, `formats.ssh`), `n` the arena's
SARE `LOGO`. Its top left holds the centre circle, a blue ring around
(`CENTRE`, `RING`), the club's crest inside, faded halfway into the ice, and
on most rinks the arena's or club's name lettered around it; the rest (the
faceoff spots, the sponsors at the bottom, the boards' edge) stays.

`draw` clears the circle and its lettering to the ice, keeps the blue ring
and centres the new crest, faded as the disc's are. The All-Star game's rink
rings its circle in red, so a club drawn on it gets the blue ring of a
regular season rink instead. The picture is written in place, with the most
colours that let its member keep its size.
"""

from __future__ import annotations

import io
from collections.abc import Collection, Mapping
from typing import TYPE_CHECKING

from ...formats.ea_tdb import TDBFile, bigf_parse
from . import screens

if TYPE_CHECKING:
    from PIL import Image

ARCHIVE = "GAMEDATA/ICETXTRS.BIG"
#: The centre circle's centre and its ring's inner and outer radius, in pixels.
CENTRE = (202, 202)
RING = (133, 145)
#: A regular season rink's ring, drawn where the template's is not blue: its
#: radius, width and colour (the disc's rinks', measured).
RING_DRAWN = (137, 2, (133, 162, 199))
#: How far from the centre the crest and lettering reach.
CLEAR = 206
#: The crest's box, and how much of it shows through the ice (the disc's
#: crests read about half their colour).
CREST = 232
FADE = 0.5


def logo_ids(tdb: TDBFile, slots: list[int]) -> dict[int, int]:
    """Each slot's centre ice picture: the `LOGO` of the arena it plays at.
    AREN gives a team's arena (SARE `INDX`): a club's own slot mostly, but
    San Jose's and St. Louis' are swapped and the All-Star sides share one."""
    sare, aren = tdb.get_table("SARE"), tdb.get_table("AREN")
    if sare is None:
        return {}
    homes = {slot: slot for slot in slots}
    if aren is not None:
        for i in range(min(aren.num_records, aren.capacity)):
            row = aren.read_record(i)
            if row.get("INDX") in homes and isinstance(row.get("ID__"), int):
                homes[int(row["INDX"])] = int(row["ID__"])  # type: ignore[call-overload]
    logos = {}
    for i in range(min(sare.num_records, sare.capacity)):
        row = sare.read_record(i)
        if isinstance(row.get("LOGO"), int):
            logos[row.get("INDX")] = int(row["LOGO"])  # type: ignore[call-overload]
    return {slot: logos[arena] for slot, arena in homes.items() if arena in logos}


def draw(original: Image.Image, crest: bytes, ring: bool = False) -> Image.Image:
    """`original` with its centre circle holding `crest` (an image file), and
    with `ring` a regular season rink's blue ring drawn around it."""
    from PIL import Image, ImageDraw

    picture = original.convert("RGBA")
    ice = picture.getpixel((4, 4))
    cx, cy = CENTRE
    clear = Image.new("L", picture.size, 0)
    pen = ImageDraw.Draw(clear)
    pen.ellipse((cx - CLEAR, cy - CLEAR, cx + CLEAR, cy + CLEAR), fill=255)
    # In the ring's band only the ring stays: its blue, not the dashes or
    # letters some rinks draw beside it.
    reds, blues = picture.getchannel("R").tobytes(), picture.getchannel("B").tobytes()
    for y in range(cy - RING[1], cy + RING[1] + 1):
        for x in range(cx - RING[1], cx + RING[1] + 1):
            at = y * picture.width + x
            band = RING[0] ** 2 <= (x - cx) ** 2 + (y - cy) ** 2 <= RING[1] ** 2
            if band and blues[at] > reds[at] + 12:
                clear.putpixel((x, y), 0)
    picture.paste(Image.new("RGBA", picture.size, ice), (0, 0), clear)
    if ring:
        r, width, colour = RING_DRAWN
        ImageDraw.Draw(picture).ellipse(
            (cx - r, cy - r, cx + r, cy + r), outline=(*colour, 255), width=width
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
    redrawn in place, the `ringed` ones with the blue ring drawn, and the ids
    drawn. A picture that cannot be read, or fits at no palette size, stays."""
    out = bytearray(archive)
    entries = {e.name.lower(): e for e in bigf_parse(archive)}
    drawn = []
    for logo, crest in crests.items():
        name = f"ICE_{logo:03d}.ssh"
        entry = entries.get(name.lower())
        if entry is None:
            continue
        member = archive[entry.offset : entry.offset + entry.size]
        copy = screens.Copy(ARCHIVE, name)
        original = screens.read_picture(member, copy)
        if original is None:
            continue
        data = screens.fitted(member, copy, draw(original, crest, logo in ringed))
        if data is None:
            continue
        out[entry.offset : entry.offset + entry.size] = data.ljust(entry.size, b"\0")
        drawn.append(logo)
    return bytes(out), drawn
