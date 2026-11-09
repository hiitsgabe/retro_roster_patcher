"""Centre ice: the club's crest the rink shows under the opening faceoff.

`GAMEDATA/ICETXTRS.BIG` holds one RefPack-compressed 512x512 picture per
arena, `ICE_<n>.ssh` (the square packing, `formats.ssh`), `n` the arena's
SARE `LOGO`. Its top left holds the centre circle, a blue ring around
(`CENTRE`, `RING`), the club's crest inside, faded halfway into the ice, and
on most rinks the arena's or club's name lettered around it; the rest (the
faceoff spots, the sponsors at the bottom, the boards' edge) stays.

`draw` clears the circle and its lettering to the ice, keeps the blue ring
and centres the new crest, faded as the disc's are. The picture is written in
place, with the most colours that let its member keep its size.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from ...formats.ea_tdb import TDBFile, bigf_parse
from . import screens

if TYPE_CHECKING:
    from PIL import Image

ARCHIVE = "GAMEDATA/ICETXTRS.BIG"
#: The centre circle's centre and its ring's inner and outer radius, in pixels.
CENTRE = (202, 202)
RING = (133, 145)
#: How far from the centre the crest and lettering reach.
CLEAR = 206
#: The crest's box, and how much of it shows through the ice (the disc's
#: crests read about half their colour).
CREST = 232
FADE = 0.5


def logo_ids(tdb: TDBFile, slots: list[int]) -> dict[int, int]:
    """Each slot's centre ice picture: its arena's (SARE row of the slot's
    index) `LOGO`."""
    sare = tdb.get_table("SARE")
    if sare is None:
        return {}
    out = {}
    for i in range(min(sare.num_records, sare.capacity)):
        row = sare.read_record(i)
        if row.get("INDX") in slots and isinstance(row.get("LOGO"), int):
            out[int(row["INDX"])] = int(row["LOGO"])  # type: ignore[call-overload]
    return out


def draw(original: Image.Image, crest: bytes) -> Image.Image:
    """`original` with its centre circle holding `crest` (an image file)."""
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
            ring = RING[0] ** 2 <= (x - cx) ** 2 + (y - cy) ** 2 <= RING[1] ** 2
            if ring and blues[at] > reds[at] + 12:
                clear.putpixel((x, y), 0)
    picture.paste(Image.new("RGBA", picture.size, ice), (0, 0), clear)
    art = Image.open(io.BytesIO(crest)).convert("RGBA")
    art.thumbnail((CREST, CREST), Image.Resampling.LANCZOS)
    art.putalpha(art.getchannel("A").point(lambda a: round(a * FADE)))
    picture.alpha_composite(art, (cx - art.width // 2, cy - art.height // 2))
    return picture


def redraw(archive: bytes, crests: dict[int, bytes]) -> tuple[bytes, list[int]]:
    """`archive` (`ICETXTRS.BIG`) with each `{logo id: crest}` centre ice
    redrawn in place, and the ids drawn. A picture that cannot be read, or
    fits at no palette size, stays."""
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
        data = screens.fitted(member, copy, draw(original, crest))
        if data is None:
            continue
        out[entry.offset : entry.offset + entry.size] = data.ljust(entry.size, b"\0")
        drawn.append(logo)
    return bytes(out), drawn
