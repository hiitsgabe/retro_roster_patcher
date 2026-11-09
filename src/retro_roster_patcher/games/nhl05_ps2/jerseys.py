"""Jerseys: what the players wear in the game and the pictures the front end
shows of it.

`GAMEDATA/JERSEYS.VIV` holds, per team and jersey, three RefPack-compressed
members named by the team's art id (STEA `ART_`) and the jersey's number:
`uni<art>_<n>.ssh` (`TORS`, the jersey, pants and gloves unwrapped on a
512x512 picture, and `HEL1`-`HEL5`, the helmet), `num<art>_<n>.ssh` (the
numbers) and `letters_<art>_<n>.ssh` (the name bar). STEA `JRSY` says how
many a club offers; jersey 1 is its home one, 0 its away. `FE/JERAPT.VIV`
holds the picture of each the jersey menu shows, `JERS_<abbr>_<nn>.big`,
named by the front end's abbreviation (`logos.SLOT_FILES`).

The Coyotes' slot carries four Winnipeg Jets classics (jerseys 5 to 8, the
1980s and 1990s white and blue). `dress` gives them to the Thrashers' slot,
the Jets now (`CLASSICS`): its jerseys' directory entries point at the
classics' data, and its menu pictures are redrawn from the classics' own.
The archive ends where the next file starts, so nothing here ever grows: a
drawn jersey is written where its member sits, with the most colours that fit.

No jersey on the disc is the Mammoth's, so `restyle` draws Utah's from the
Coyotes' own (`RESTYLED`): their brick red becomes Utah's black, shading kept,
the coyote on the chest and the shoulder patch give way to Utah's crest, and
"PHOENIX" leaves the pants. The numbers and name bar take the same colours.
Utah then offers those two jerseys alone (`JRSY` 2): the Coyotes' thirds and
classics, and the Jets', are no longer its own.
"""

from __future__ import annotations

import io
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

from ...formats import ssh
from ...formats.ea_tdb import (
    BigfEntry,
    EaTdbError,
    TDBFile,
    bigf_alias,
    bigf_parse,
    refpack_compress,
    refpack_decompress,
)
from . import screens

if TYPE_CHECKING:
    from PIL import Image

#: A picture to draw: an image, or (abbreviation, jersey) of one to copy.
Drawing = Union["Image.Image", tuple[str, int]]

ARCHIVE = "GAMEDATA/JERSEYS.VIV"
PICTURES = "FE/JERAPT.VIV"
#: The members of one jersey, by art id and number.
PARTS = ("uni{:03d}_{}.ssh", "num{:03d}_{}.ssh", "letters_{:03d}_{}.ssh")

#: By slot, the jerseys a club wears instead of its own, on the retail disc's
#: art ids: (art, abbreviation, jersey) <- (art, abbreviation, jersey). The
#: Jets: away the white 1990s classic, home the blue one, third the blue 1980s
#: with the white band.
CLASSICS: Mapping[int, tuple[tuple[tuple[int, str, int], tuple[int, str, int]], ...]] = {
    1: (
        ((1, "ATL", 0), (22, "PHO", 8)),
        ((1, "ATL", 1), (22, "PHO", 6)),
        ((1, "ATL", 2), (22, "PHO", 7)),
    ),
}


def share(archive: bytes, moves: Mapping[int, Mapping[int, tuple[int, int]]]) -> tuple[bytes, int]:
    """`archive` (`JERSEYS.VIV`) with each `{art: {n: (art, n)}}` jersey's
    members pointing at the other jersey's data, and how many jerseys changed.
    A jersey either side lacks a member of is left as it was."""
    out = bytearray(archive)
    names = {e.name for e in bigf_parse(archive)}
    changed = 0
    for art, jerseys in moves.items():
        for n, (src_art, src_n) in jerseys.items():
            pairs = {p.format(art, n): p.format(src_art, src_n) for p in PARTS}
            if all(d in names and s in names for d, s in pairs.items()):
                bigf_alias(out, pairs)
                changed += 1
    return bytes(out), changed


def _picture_copy(abbr: str, n: int) -> screens.Copy:
    name = f"JERS_{abbr}_{n:02d}"
    return screens.Copy(PICTURES, f"{name}.big", f"{name}.ssh")


def redraw_pictures(archive: bytes, moves: Mapping[tuple[str, int], Drawing]) -> tuple[bytes, int]:
    """`archive` (`JERAPT.VIV`) with each `{(abbr, n): picture}` jersey picture
    drawn in place; `picture` is a PIL image or `(abbr, n)` of another picture
    of the archive to copy. Returns the archive and how many were drawn."""
    out = bytearray(archive)
    entries = {e.name.lower(): e for e in bigf_parse(archive)}

    def member(abbr: str, n: int) -> tuple[BigfEntry, bytes] | None:
        entry = entries.get(_picture_copy(abbr, n).member.lower())
        if entry is None:
            return None
        return entry, archive[entry.offset : entry.offset + entry.size]

    drawn = 0
    for (abbr, n), picture in moves.items():
        found = member(abbr, n)
        if found is None:
            continue
        image: Image.Image | None
        if isinstance(picture, tuple):
            source = member(*picture)
            image = screens.read_picture(source[1], _picture_copy(*picture)) if source else None
        else:
            image = picture
        if image is None:
            continue
        entry, data = found
        fitted = screens.fitted(data, _picture_copy(abbr, n), image)
        if fitted is None:
            continue
        out[entry.offset : entry.offset + entry.size] = fitted.ljust(entry.size, b"\0")
        drawn += 1
    return bytes(out), drawn


def dress(
    jerseys: bytes, pictures: bytes, slots: list[int], crests: Mapping[int, bytes]
) -> tuple[bytes, bytes, int, int]:
    """Each club among `slots` dressed in `JERSEYS.VIV` and `JERAPT.VIV`: the
    `CLASSICS` it wears, and the `RESTYLED` jerseys drawn with its crest
    (none without one). Returns both archives, the jerseys changed and the
    menu pictures drawn."""
    moves: dict[int, dict[int, tuple[int, int]]] = {}
    drawings: dict[tuple[str, int], Drawing] = {}
    for slot in slots:
        for (art, abbr, n), (src_art, src_abbr, src_n) in CLASSICS.get(slot, ()):
            moves.setdefault(art, {})[n] = (src_art, src_n)
            drawings[(abbr, n)] = (src_abbr, src_n)
    jerseys, changed = share(jerseys, moves)
    entries = {e.name.lower(): e for e in bigf_parse(pictures)}
    for slot in slots:
        style, crest = RESTYLED.get(slot), crests.get(slot)
        if style is None or crest is None:
            continue
        jerseys, drawn = restyle(jerseys, style, crest)
        changed += drawn
        for n in style.jerseys:
            copy = _picture_copy(style.abbr, n)
            entry = entries.get(copy.member.lower())
            old = (
                screens.read_picture(pictures[entry.offset : entry.offset + entry.size], copy)
                if entry is not None
                else None
            )
            if old is not None:
                drawings[(style.abbr, n)] = restyle_picture(old, style, crest)
    pictures, pictured = redraw_pictures(pictures, drawings)
    return jerseys, pictures, changed, pictured


@dataclass(frozen=True)
class Restyle:
    """A club drawn from a template: the template's main colour (hue, 0-1) for
    the club's, on these jerseys (art id, the front end's abbreviation)."""

    art: int
    abbr: str
    jerseys: tuple[int, ...]
    hue: float
    colour: tuple[int, int, int]


#: By slot. Utah: the Coyotes' away (0) and home (1), their brick red
#: (hue 352 degrees) to Utah's black.
RESTYLED: Mapping[int, Restyle] = {22: Restyle(22, "PHO", (0, 1), 352 / 360, (0, 0, 0))}

#: Where the template's art sits on the 512x512 `TORS` (shared by every
#: jersey: one model): the chest crest, a clean patch of the back the same size
#: to cover it with, the left shoulder patch, and the pants' lettering (left
#: and right leg).
CHEST = (204, 330, 310, 452)
BACK = (204, 60, 310, 182)
SHOULDER = (148, 236, 194, 275)
PANTS = ((20, 2, 100, 30), (395, 6, 480, 36))
#: The chest crest on the front end's 256x256 jersey pictures.
PICTURE_CHEST = (98, 74, 164, 186)
#: A recoloured pixel's lightness against the template colour's own (the
#: brick's mean `V`, 0-255), and how much of the difference shows.
LIGHTNESS = 140
SHADING = 0.6
#: The darkest a recoloured colour gets, so a black jersey keeps its folds.
FLOOR = 24
#: How far (sum over channels) a pixel is from the fabric around it to be
#: part of old art: the crest on a picture, the letters on the pants.
ART_DISTANCE = 120
LETTERING_DISTANCE = 60
#: How many pixels a cover fades in over.
FEATHER = 6
SHAPES = ("TORS", "TORL", "HEL1", "HEL2", "HEL3", "HEL4", "HEL5")
COLOUR_STEPS = (256, 128, 64)

Box = tuple[int, int, int, int]
Pixel = tuple[int, int, int, int]


def recolour(picture: Image.Image, hue: float, colour: tuple[int, int, int]) -> Image.Image:
    """`picture` with the pixels of `hue` (and their blends) in `colour`, each
    keeping its lightness against the template colour's."""
    from PIL import Image

    rgba = picture.convert("RGBA")
    h, s, v = rgba.convert("RGB").convert("HSV").split()
    centre = round(hue * 255)

    def near(x: int) -> int:
        d = min(abs(x - centre), 256 - abs(x - centre))
        return 255 if d <= 18 else max(0, 255 - (d - 18) * 12)

    weight = Image.composite(
        h.point(near), Image.new("L", h.size, 0), s.point(lambda x: min(255, max(0, (x - 30) * 5)))
    )
    channels = [
        v.point(lambda x, c=c: min(255, max(0, round(max(c, FLOOR) + (x - LIGHTNESS) * SHADING))))
        for c in colour
    ]
    new = Image.merge("RGBA", (*channels, rgba.getchannel("A")))
    return Image.composite(new, rgba, weight)


def _pixels(picture: Image.Image, box: Box) -> list[tuple[int, int, Pixel]]:
    x0, y0, x1, y1 = box
    rgba = picture.convert("RGBA")
    return [(x, y, rgba.getpixel((x, y))) for y in range(y0, y1) for x in range(x0, x1)]  # type: ignore[misc]


def _median(pixels: list[Pixel]) -> Pixel:
    return sorted(pixels, key=lambda p: sum(p[:3]))[len(pixels) // 2]


def _paint_out(picture: Image.Image, box: Box, fabric: Pixel, distance: int) -> None:
    """The pixels of `box` further than `distance` from `fabric` painted in it."""
    for x, y, p in _pixels(picture, box):
        if sum(abs(a - b) for a, b in zip(p[:3], fabric[:3], strict=True)) > distance:
            picture.putpixel((x, y), fabric)


def _border(picture: Image.Image, box: Box) -> Pixel:
    """The fabric's colour just outside `box`."""
    x0, y0, x1, y1 = box
    return _median(
        [p for _, _, p in _pixels(picture, (x0 - 3, y0 - 3, x1 + 3, y0 - 1))]
        + [p for _, _, p in _pixels(picture, (x0 - 3, y1 + 1, x1 + 3, y1 + 3))]
    )


def _centre_crest(picture: Image.Image, box: Box, crest: bytes) -> None:
    from PIL import Image

    x0, y0, x1, y1 = box
    art = Image.open(io.BytesIO(crest)).convert("RGBA")
    art.thumbnail((x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
    picture.alpha_composite(
        art, (x0 + (x1 - x0 - art.width) // 2, y0 + (y1 - y0 - art.height) // 2)
    )


def _cover(picture: Image.Image, source: Box, box: Box) -> None:
    """`box` covered by the fabric of `source` (the same size), toned to the
    fabric around `box` and faded in over `FEATHER` pixels at its edges."""
    from PIL import Image, ImageDraw, ImageFilter

    patch = picture.crop(source).convert("RGBA")
    around, inside = _border(picture, box), _border(picture, source)
    patch = Image.merge(
        "RGBA",
        [
            band.point(lambda v, d=around[i] - inside[i]: min(255, max(0, v + d)))
            for i, band in enumerate(patch.split()[:3])
        ]
        + [patch.getchannel("A")],
    )
    w, h = patch.size
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rectangle((FEATHER, FEATHER, w - 1 - FEATHER, h - 1 - FEATHER), fill=255)
    picture.paste(patch, box[:2], mask.filter(ImageFilter.GaussianBlur(FEATHER / 2)))


def _clear_lettering(picture: Image.Image, box: Box) -> None:
    """The light letters in `box` painted in the pants' colour."""
    pixels = [p for _, _, p in _pixels(picture, box)]
    pants = _median(pixels)
    for x, y, p in _pixels(picture, box):
        if sum(p[:3]) > sum(pants[:3]) + LETTERING_DISTANCE:
            picture.putpixel((x, y), pants)


def restyle_torso(torso: Image.Image, style: Restyle, crest: bytes) -> Image.Image:
    """The template's `TORS` in the club's colour, with its crest: the chest's
    old crest under a patch of the clean back, the shoulder's painted out."""
    picture = recolour(torso, style.hue, style.colour)
    for box in PANTS:
        _clear_lettering(picture, box)
    _cover(picture, BACK, CHEST)
    _centre_crest(picture, CHEST, crest)
    _paint_out(picture, SHOULDER, _border(picture, SHOULDER), 0)
    _centre_crest(picture, SHOULDER, crest)
    return picture


def restyle_picture(picture: Image.Image, style: Restyle, crest: bytes) -> Image.Image:
    """A front-end jersey picture in the club's colour, with its crest."""
    from PIL import Image, ImageDraw, ImageFilter

    out = recolour(picture, style.hue, style.colour)
    # The old crest painted out and the box softened into the fabric around
    # it: the pictures are small, so a blur reads as the jersey's folds.
    _paint_out(out, PICTURE_CHEST, _border(out, PICTURE_CHEST), ART_DISTANCE)
    x0, y0, x1, y1 = PICTURE_CHEST
    mask = Image.new("L", out.size, 0)
    ImageDraw.Draw(mask).rectangle((x0, y0, x1 - 1, y1 - 1), fill=255)
    mask = Image.composite(mask.filter(ImageFilter.GaussianBlur(FEATHER)), mask, mask)
    out.paste(out.filter(ImageFilter.GaussianBlur(5)), (0, 0), mask)
    _centre_crest(out, PICTURE_CHEST, crest)
    return out


def _palettes(data: bytes, style: Restyle) -> bytes:
    """`data` (numbers or a name bar: 4-bit shapes) with every 16-colour
    palette recoloured."""
    from PIL import Image

    out = bytearray(data)
    for _, block, _ in ssh.shapes(data):
        if data[block] != 0x01:
            continue
        pal = block + (struct.unpack_from("<I", data, block + 4)[0] & 0xFFFFFF) + 0x20
        colours = Image.new("RGBA", (16, 1))
        colours.putdata([tuple(data[pal + 4 * i : pal + 4 * i + 3]) + (255,) for i in range(16)])
        new = recolour(colours, style.hue, style.colour)
        for i in range(16):
            out[pal + 4 * i : pal + 4 * i + 3] = bytes(new.getpixel((i, 0))[:3])  # type: ignore[index]
    return bytes(out)


def _uniform(data: bytes, style: Restyle, crest: bytes, colours: int) -> bytes:
    for shape in SHAPES:
        picture = ssh.decode(data, shape)
        if shape == "TORS":
            picture = restyle_torso(picture, style, crest)
        else:
            picture = recolour(picture, style.hue, style.colour)
        data = ssh.encode(data, picture, colours, shape)
    return data


def restyle(archive: bytes, style: Restyle, crest: bytes) -> tuple[bytes, int]:
    """`archive` (`JERSEYS.VIV`) with the style's jerseys drawn in place, and
    how many. A jersey whose uniform fits its member at no palette size
    stays whole, numbers included."""
    out = bytearray(archive)
    entries = {e.name: e for e in bigf_parse(archive)}
    drawn = 0
    for n in style.jerseys:
        names = [part.format(style.art, n) for part in PARTS]
        if not all(name in entries for name in names):
            continue
        writes = []
        try:
            for name in names:
                entry = entries[name]
                data = refpack_decompress(archive[entry.offset : entry.offset + entry.size])
                if name.startswith("uni"):
                    new = next(
                        (
                            packed
                            for c in COLOUR_STEPS
                            if len(
                                packed := refpack_compress(
                                    _uniform(data, style, crest, c), tight=True
                                )
                            )
                            <= entry.size
                        ),
                        None,
                    )
                else:
                    new = refpack_compress(_palettes(data, style), tight=True)
                if new is None or len(new) > entry.size:
                    break
                writes.append((entry, new))
        except (EaTdbError, ssh.SshError, IndexError, ValueError):
            continue
        if len(writes) < len(names):
            continue
        for entry, new in writes:
            out[entry.offset : entry.offset + entry.size] = new.ljust(entry.size, b"\0")
        drawn += 1
    return bytes(out), drawn


def limit(tdb: TDBFile, slots: list[int]) -> None:
    """Each restyled club among `slots` offers its drawn jerseys alone (STEA
    `JRSY`)."""
    stea = tdb.get_table("STEA")
    if stea is None:
        return
    for i in range(min(stea.num_records, stea.capacity)):
        slot = stea.read_record(i).get("INDX")
        if isinstance(slot, int) and slot in slots and slot in RESTYLED:
            stea.write_record(i, {"JRSY": len(RESTYLED[slot].jerseys)})
