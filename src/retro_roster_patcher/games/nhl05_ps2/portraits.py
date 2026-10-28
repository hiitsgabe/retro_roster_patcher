"""Player portraits: the head shots in `FE/PORTAPT.VIV`, as on NHL 07 (PSP).

The archive is a BIGF of entries named `NNNN.big` by a bio's `ART_`, each a
(RefPack-compressed) BIGF of a small Flash movie and `NNNN.ssh`, one 64x128
8-bit PS2 shape (`formats.ssh`): head and shoulders on transparency. Entry
0000 is the game's silhouette. Which entries to draw is NHL 07's rule
(`nhl07_psp.portraits.portrait_targets`): only an `ART_` one bio alone uses.

A portrait takes the most colours that still fit its entry's slot, so the
archive is normally rewritten in place; otherwise it is rebuilt.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from typing import TYPE_CHECKING

from ...formats import ssh
from ...formats.ea_tdb import (
    EaTdbError,
    bigf_build,
    bigf_parse,
    bigf_replace,
    refpack_compress,
    refpack_decompress,
)
from ..nhl07_psp.portraits import COLOUR_STEPS, SILHOUETTE_ART, entry_name, portrait_targets

if TYPE_CHECKING:
    from PIL import Image

__all__ = [
    "PORTAPT_PATH",
    "SILHOUETTE_ART",
    "crop",
    "entry_name",
    "portrait_targets",
    "read",
    "repack",
]

PORTAPT_PATH = "FE/PORTAPT.VIV"
SIZE = (64, 128)
#: How far down the disc's portraits start the hair, of the height.
_HEADROOM = 0.10


def crop(png: bytes):  # -> Image.Image
    """A provider head shot as a 64x128 portrait: a strip half as wide as it is
    tall, centred on the subject, the head starting `_HEADROOM` down."""
    from PIL import Image

    picture = Image.open(io.BytesIO(png)).convert("RGBA")
    left, top, right, bottom = picture.getchannel("A").getbbox() or (0, 0, *picture.size)
    height = round((bottom - top) / (1 - _HEADROOM))
    width = height // 2
    x0 = (left + right) // 2 - width // 2
    strip = picture.crop((x0, bottom - height, x0 + width, bottom))
    return strip.resize(SIZE, Image.Resampling.LANCZOS)


def _inner(stored: bytes) -> tuple[bytes, bool]:
    """An entry's BIGF and whether it was stored compressed."""
    if stored[:2] == b"\x10\xfb":
        return refpack_decompress(stored), True
    return stored, False


def read(archive: bytes, art: int):  # -> Image.Image | None
    """Entry `art`'s portrait, or None when it is missing or unreadable."""
    try:
        entry = next((e for e in bigf_parse(archive) if e.name == entry_name(art)), None)
        if entry is None:
            return None
        inner, _ = _inner(archive[entry.offset : entry.offset + entry.size])
        shape = next((e for e in bigf_parse(inner) if e.name.endswith(".ssh")), None)
        if shape is None:
            return None
        return ssh.decode(inner[shape.offset : shape.offset + shape.size])
    except (EaTdbError, ssh.SshError, IndexError, ValueError):
        return None


def _entry(stored: bytes, image: Image.Image, colours: int) -> bytes | None:
    """One `NNNN.big` with its portrait replaced; None to skip."""
    try:
        inner, packed = _inner(stored)
        shape = next((e for e in bigf_parse(inner) if e.name.endswith(".ssh")), None)
        if shape is None:
            return None
        data = ssh.encode(inner[shape.offset : shape.offset + shape.size], image, colours)
    except (EaTdbError, ssh.SshError, IndexError, ValueError):
        return None
    out = bigf_replace(inner, shape.name, data)
    return refpack_compress(out, tight=True) if packed else out


def repack(archive: bytes, images: Mapping[int, Image.Image]) -> tuple[bytes, int]:
    """`archive` with the portrait of each `ART_` in `images` replaced, and how
    many went in: in place when each fits its slot, else rebuilt."""
    entries = bigf_parse(archive)
    by_name = {e.name: e for e in entries}
    replaced: dict[str, bytes] = {}
    for art, image in images.items():
        entry = by_name.get(entry_name(art))
        if entry is None:
            continue
        stored = archive[entry.offset : entry.offset + entry.size]
        data = None
        for colours in COLOUR_STEPS:
            data = _entry(stored, image, colours)
            if data is None or len(data) <= len(stored):
                break
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
