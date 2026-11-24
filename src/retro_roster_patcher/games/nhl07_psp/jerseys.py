"""Jerseys: what the players wear in the game and the pictures the front end
shows of it, as NHL 2005 (PS2) dresses them (`nhl05_ps2.jerseys`).

The archives are NHL 2005's at half the size, in SHPM (`formats.shpm`):
`GAMEDATA/JERSEYS.VIV` holds per team and jersey `uni<art>_<n>.msh` (`TORS`,
256x256, and `HEL1`-`HEL5`), `num<art>_<n>.msh` and `letters_<art>_<n>.msh`
(4-bit shapes, only their 16-colour palettes recoloured), and `FE/JERAPT.VIV`
the 128x128 menu pictures, `JERS_<abbr>_<nn>.big`. The art sits where NHL
2005's does, scaled, so a picture is drawn at NHL 2005's size and scaled back.
The disc's Jets classics, art ids and All-Star jerseys are NHL 2005's too, and
there is no far-away torso (`TORL`).
"""

from __future__ import annotations

import struct
from collections.abc import Mapping
from typing import TYPE_CHECKING

from ...formats import shpm
from ...formats.ea_tdb import EaTdbError, bigf_parse, refpack_compress, refpack_decompress
from ..nhl05_ps2.jerseys import (
    CLASSICS,
    RESTYLED,
    Drawing,
    Restyle,
    limit,
    recolour,
    redraw_pictures,
    restyle_picture,
    restyle_torso,
    share,
)
from . import screens

if TYPE_CHECKING:
    from PIL import Image

__all__ = ["ARCHIVE", "BH_NAME", "PICTURES", "dress", "limit", "restyle"]

ARCHIVE = "PSP_GAME/USRDIR/GAMEDATA/JERSEYS.VIV"
PICTURES = "PSP_GAME/USRDIR/FE/JERAPT.VIV"
#: `ARCHIVE`'s directory as the game caches it, in `PRELOAD/GMISC.VIV`.
BH_NAME = "jerseys.bh"
PARTS = ("uni{:03d}_{}.msh", "num{:03d}_{}.msh", "letters_{:03d}_{}.msh")
#: NHL 2005's sizes of a torso and a menu picture, where its boxes are.
TORSO = (512, 512)
PICTURE = (256, 256)


def _picture_copy(abbr: str, n: int) -> screens.Copy:
    name = f"JERS_{abbr}_{n:02d}"
    return screens.Copy(PICTURES, f"{name}.big", f"{name}.msh", "1")


def _scaled(picture: Image.Image, size: tuple[int, int], draw) -> Image.Image:  # type: ignore[no-untyped-def]
    from PIL import Image

    return draw(picture.resize(size, Image.Resampling.LANCZOS)).resize(
        picture.size, Image.Resampling.LANCZOS
    )


def _palettes(data: bytes, style: Restyle) -> bytes:
    """`data` (numbers or a name bar) with every palette recoloured."""
    from PIL import Image

    parsed = shpm.parse(data)
    for shape in parsed.shapes:
        for block in shape.blocks[1:]:
            if block[0] != shpm.PAL32:
                continue
            w, h = struct.unpack_from("<HH", block, 4)
            colours = Image.frombytes("RGBA", (w * h, 1), bytes(block[16 : 16 + 4 * w * h]))
            block[16 : 16 + 4 * w * h] = recolour(
                colours, style.hue, style.colour, style.lightness
            ).tobytes()
    return shpm.build(parsed)


def _uniform(data: bytes, style: Restyle, crest: bytes, colours: int) -> bytes:
    from PIL import Image

    parsed = shpm.parse(data)
    for shape in parsed.shapes:
        picture = shpm.decode_shape(shape)[0]
        if shape.name.rstrip(b" \0") == b"TORS":
            picture = _scaled(picture, TORSO, lambda p: restyle_torso(p, style, crest))
        else:
            picture = recolour(picture, style.hue, style.colour, style.lightness)
        if colours < screens.COLOUR_STEPS[0]:
            picture = picture.quantize(colours, method=Image.Quantize.FASTOCTREE).convert("RGBA")
        shpm.encode_shape(shape, picture)
    return shpm.build(parsed)


def restyle(archive: bytes, style: Restyle, crest: bytes) -> tuple[bytes, int]:
    """`archive` (`JERSEYS.VIV`) with the style's jerseys drawn in place, and
    how many. A jersey whose uniform fits its member at no palette size stays
    whole, numbers included."""
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
                            for c in screens.COLOUR_STEPS
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
        except (EaTdbError, shpm.ShpmError, IndexError, ValueError):
            continue
        if len(writes) < len(names):
            continue
        for entry, new in writes:
            out[entry.offset : entry.offset + entry.size] = new.ljust(entry.size, b"\0")
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
    jerseys, changed = share(jerseys, moves, PARTS)
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
                drawings[(style.abbr, n)] = _scaled(
                    old, PICTURE, lambda p, s=style, c=crest: restyle_picture(p, s, c)
                )
    pictures, pictured = redraw_pictures(pictures, drawings, _picture_copy, screens)
    return jerseys, pictures, changed, pictured
