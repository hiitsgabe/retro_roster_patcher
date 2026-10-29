"""Team logos: the pictures in `FE/TLOGOAPT.VIV` the front end draws for a club.

The archive holds one RefPack-compressed BIGF per picture, named by a prefix
and the club's abbreviation (`CS_ANA.big` holds `CS_ANA.ssh` beside its Flash
movie), the abbreviation the archive's own where it differs from the roster
tables' (`SLOT_FILES`). A club has four pictures a crest can take, each drawn
as the disc draws it (`KINDS`): the choose-sides logo and the small one of the
team lists are stored half as tall as they show, and the small one on a white
ground. The wordmark (`T_<abbr>`) is text and stays.

Each picture is written in place, with the most colours that let its member
keep its size (`screens.fitted`); one that fits at none is left as it was.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from typing import TYPE_CHECKING

from ...formats import ssh
from ...formats.ea_tdb import EaTdbError, bigf_parse, refpack_decompress
from . import screens

if TYPE_CHECKING:
    from PIL import Image

ARCHIVE = "FE/TLOGOAPT.VIV"
#: The archive's abbreviation for each slot this game patches, in STEA order:
#: the clubs, then the East and West All-Star sides (which have no `LOGO_EML_`).
SLOT_FILES = (
    "ANA", "ATL", "BOS", "BUF", "CAL", "CAR", "CHI", "COL", "CMB", "DAL",
    "DET", "EDM", "FLA", "LA", "MIN", "MTL", "NAS", "NJD", "NYI", "NYR",
    "OTT", "PHI", "PHO", "PIT", "SJ", "STL", "TB", "TOR", "VAN", "WSH",
    "EAS", "WES",
)  # fmt: skip
#: (member prefix, how many times wider than it shows the picture is stored,
#: whether it has a white ground rather than a transparent one).
KINDS = (
    ("CS_", 2, False),
    ("LOGO_EML_", 1, False),
    ("LOGO_LRG_", 1, False),
    ("LOGO_SML_", 2, True),
)
#: The share of the picture left clear around the crest.
MARGIN = 0.06


def picture(png: bytes, size: tuple[int, int], squash: int, white: bool) -> Image.Image:
    """`png` (a crest) centred in a `size` picture, keeping its shape as it
    shows: fitted to the picture `squash` times taller, then stored squashed."""
    from PIL import Image

    width, height = size
    crest = Image.open(io.BytesIO(png)).convert("RGBA")
    box = (round(width * (1 - 2 * MARGIN)), round(height * squash * (1 - 2 * MARGIN)))
    crest.thumbnail(box, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (width, height * squash), (255, 255, 255, 255 if white else 0))
    canvas.alpha_composite(
        crest, ((width - crest.width) // 2, (height * squash - crest.height) // 2)
    )
    return canvas.resize((width, height), Image.Resampling.LANCZOS) if squash > 1 else canvas


def _dimensions(member: bytes, shape: str) -> tuple[int, int] | None:
    try:
        inner = refpack_decompress(member) if member[:2] == b"\x10\xfb" else member
        entry = next((e for e in bigf_parse(inner) if e.name.lower() == shape.lower()), None)
        if entry is None:
            return None
        return ssh.dimensions(inner[entry.offset : entry.offset + entry.size])
    except (EaTdbError, ssh.SshError, IndexError, ValueError):
        return None


def edits(archive: bytes, crests: Mapping[int, bytes]) -> tuple[bytearray, dict[int, int]]:
    """`archive` with each `{slot: crest png}` drawn into the slot's pictures,
    and how many pictures each slot got."""
    out = bytearray(archive)
    by_name = {e.name.lower(): e for e in bigf_parse(archive)}
    drawn: dict[int, int] = {}
    for slot, png in crests.items():
        if not 0 <= slot < len(SLOT_FILES):
            continue
        for prefix, squash, white in KINDS:
            name = f"{prefix}{SLOT_FILES[slot]}"
            entry = by_name.get(f"{name}.big".lower())
            if entry is None:
                continue
            member = archive[entry.offset : entry.offset + entry.size]
            size = _dimensions(member, f"{name}.ssh")
            if size is None:
                continue
            copy = screens.Copy(ARCHIVE, entry.name, f"{name}.ssh")
            data = screens.fitted(member, copy, picture(png, size, squash, white))
            if data is None:
                continue
            out[entry.offset : entry.offset + entry.size] = data.ljust(entry.size, b"\0")
            drawn[slot] = drawn.get(slot, 0) + 1
    return out, drawn
