"""Player portraits: the head shots in `FE/PORTAPT.VIV`.

The archive is a BIGF of RefPack-compressed entries named `NNNN.big`, where
NNNN is a bio's `ART_`. Each entry is itself a BIGF of a small Flash movie
(`.apt`, `.const`, `.o`) and `NNNN.msh`, one SHPM shape: an 8-bit 32x64 head
and shoulders cut out on transparency. Entry 0000 is the game's own silhouette
for a player without a photo. Checked on the retail disc: 2,270 of the 2,282
entries are 8-bit; the other 12 use a pixel format `formats.shpm` does not
read, and are left alone.
"""

from __future__ import annotations

import io
from collections import Counter
from collections.abc import Mapping
from typing import TYPE_CHECKING

from ...formats import shpm
from ...formats.ea_tdb import (
    EaTdbError,
    bigf_build,
    bigf_parse,
    bigf_replace,
    refpack_compress,
    refpack_decompress,
)

if TYPE_CHECKING:
    from PIL import Image

PORTAPT_PATH = "PSP_GAME/USRDIR/FE/PORTAPT.VIV"
PORTRAIT_SIZE = (32, 64)
SILHOUETTE_ART = 0

# How far down EA's portraits start the hair: about 9 rows of 64.
_HEADROOM = 0.14

# Palette sizes tried in turn until a portrait fits its entry's slot. Ours
# compress worse than EA's: at 256 colours 517 of 626 head shots outgrew their
# slot on the retail disc, at 48 seven did, at 32 none.
COLOUR_STEPS = (256, 128, 96, 64, 48, 32)


def entry_name(art: int) -> str:
    return f"{art:04d}.big"


def portrait_targets(art_of: Mapping[int, object], photos: Mapping[int, str]) -> dict[int, str]:
    """Which portraits to draw: `ART_` -> head shot URL ("" for the silhouette).

    `art_of` maps every live bio record to its `ART_`, `photos` each written
    bio record to its player's head shot. Only an `ART_` that exactly one bio
    uses is drawn: the retail disc gives one id to dozens of European players,
    and drawing it would put one man's face on all of them. 0 is the
    silhouette itself and is never drawn over.
    """
    uses = Counter(art_of.values())
    targets = {}
    for bio, url in photos.items():
        art = art_of.get(bio)
        if isinstance(art, int) and art != SILHOUETTE_ART and uses[art] == 1:
            targets[art] = url
    return targets


def headshot_portrait(png: bytes) -> Image.Image:
    """A provider head shot cropped and scaled to a 32x64 portrait.

    The crop is a strip half as wide as it is tall, centred on the subject
    (the opaque pixels, or the whole picture when it has no transparency),
    with the head starting about 14% down as EA's do. Parts of the strip
    outside the picture come out transparent.
    """
    from PIL import Image

    picture = Image.open(io.BytesIO(png)).convert("RGBA")
    left, top, right, bottom = picture.getchannel("A").getbbox() or (0, 0, *picture.size)
    height = round((bottom - top) / (1 - _HEADROOM))
    width = height // 2
    x0 = (left + right) // 2 - width // 2
    y0 = bottom - height
    strip = picture.crop((x0, y0, x0 + width, y0 + height))
    return strip.resize(PORTRAIT_SIZE, Image.Resampling.LANCZOS)


def read_portrait(archive: bytes, art: int) -> Image.Image | None:
    """Entry `art`'s portrait, or None when it is missing, unreadable or not 8-bit."""
    try:
        entry = next((e for e in bigf_parse(archive) if e.name == entry_name(art)), None)
        if entry is None:
            return None
        inner = refpack_decompress(archive[entry.offset : entry.offset + entry.size])
        msh = next((e for e in bigf_parse(inner) if e.name.endswith(".msh")), None)
        if msh is None:
            return None
        shape = shpm.parse(inner[msh.offset : msh.offset + msh.size]).shapes[0]
        if shape.blocks[0][0] != shpm.PIX8:
            return None
        return shpm.decode_shape(shape)[0]
    except (EaTdbError, shpm.ShpmError, IndexError, ValueError):
        return None


def _fitted_entry(compressed: bytes, image: Image.Image) -> bytes | None:
    """`_repacked_entry` with the most colours that fit the entry's own slot.

    The 32-colour encoding when even that does not fit; None to skip.
    """
    from PIL import Image

    data = None
    for colours in COLOUR_STEPS:
        reduced = image
        if colours < COLOUR_STEPS[0]:
            reduced = image.quantize(colours, method=Image.Quantize.FASTOCTREE).convert("RGBA")
        data = _repacked_entry(compressed, reduced)
        if data is None or len(data) <= len(compressed):
            return data
    return data


def _repacked_entry(compressed: bytes, image: Image.Image) -> bytes | None:
    """One `NNNN.big` with its portrait replaced, recompressed; None to skip."""
    try:
        inner = refpack_decompress(compressed)
        msh = next((e for e in bigf_parse(inner) if e.name.endswith(".msh")), None)
        if msh is None:
            return None
        parsed = shpm.parse(inner[msh.offset : msh.offset + msh.size])
        shape = parsed.shapes[0]
        if shape.blocks[0][0] != shpm.PIX8:
            return None
        shpm.encode_shape(shape, image)
    except (EaTdbError, shpm.ShpmError, IndexError, ValueError):
        return None
    return refpack_compress(bigf_replace(inner, msh.name, shpm.build(parsed)))


def repack_portraits(archive: bytes, images: Mapping[int, Image.Image]) -> tuple[bytes, int]:
    """`archive` with the portrait of each `ART_` in `images` replaced.

    Returns the new archive and how many portraits went in. Each portrait
    takes the most colours that fit its entry's slot, so normally every entry
    is written where it sits and the directory, and every cached copy of it,
    stays valid. Otherwise the archive is rebuilt compactly and its directory
    changes. Entries that are missing or not 8-bit are skipped.
    """
    entries = bigf_parse(archive)
    by_name = {e.name: e for e in entries}
    replaced: dict[str, bytes] = {}
    for art, image in images.items():
        entry = by_name.get(entry_name(art))
        if entry is None:
            continue
        data = _fitted_entry(archive[entry.offset : entry.offset + entry.size], image)
        if data is not None:
            replaced[entry.name] = data

    if all(len(data) <= by_name[name].size for name, data in replaced.items()):
        out = bytearray(archive)
        for name, data in replaced.items():
            entry = by_name[name]
            out[entry.offset : entry.offset + entry.size] = data.ljust(entry.size, b"\0")
        return bytes(out), len(replaced)

    contents = {e.name: archive[e.offset : e.offset + e.size] for e in entries}
    contents.update(replaced)
    return bigf_build(entries, contents), len(replaced)
