"""Team logos: the crests in `FE/TLOGOAPT.VIV` the front end draws for a club.

The archive holds one RefPack-compressed BIGF per picture, `LOGO_<size>_<abbr>.big`,
holding `LOGO_<size>_<abbr>.msh` (one 8-bit SHPM shape named `1`) beside its
Flash movie. The abbreviation is the archive's own, which differs from the
roster tables' for five clubs (`SLOT_FILES`). A club has two sizes, both on a
transparent ground: `LRG` (64x64, Roster Management and team select) and `MED`
(32x32, the lists). `LOGO_CS_` is a few leagues' choose-sides art, not a club's.

Each picture is written in place, with the most colours that let its member
keep its size (`screens.fitted`), so the archive's directory, and the
`tlogoapt.bh` copies of it in `PRELOAD`, stay valid; one that fits at none is
left as it was.
"""

from __future__ import annotations

from collections.abc import Mapping

from ...core.assets import centred_crest
from ...formats.ea_tdb import bigf_parse
from . import screens

ARCHIVE = "PSP_GAME/USRDIR/FE/TLOGOAPT.VIV"
#: The archive's abbreviation for each slot, in STEA order: the clubs, then the
#: East and West All-Star sides.
SLOT_FILES = (
    "ANA", "ATL", "BOS", "BUF", "CAL", "CAR", "CHI", "COL", "CMB", "DAL",
    "DET", "EDM", "FLA", "LA", "MIN", "MTL", "NAS", "NJD", "NYI", "NYR",
    "OTT", "PHI", "PHO", "PIT", "STL", "SJ", "TB", "TOR", "VAN", "WSH",
    "EAS", "WES",
)  # fmt: skip
SIZES = ("LRG", "MED")


def edits(archive: bytes, crests: Mapping[int, bytes]) -> tuple[bytearray, dict[int, int]]:
    """`archive` with each `{slot: crest png}` drawn into the slot's pictures,
    and how many pictures each slot got."""
    out = bytearray(archive)
    by_name = {e.name.lower(): e for e in bigf_parse(archive)}
    drawn: dict[int, int] = {}
    for slot, png in crests.items():
        if not 0 <= slot < len(SLOT_FILES):
            continue
        for size in SIZES:
            name = f"LOGO_{size}_{SLOT_FILES[slot]}"
            entry = by_name.get(f"{name}.big".lower())
            if entry is None:
                continue
            member = archive[entry.offset : entry.offset + entry.size]
            copy = screens.Copy(ARCHIVE, entry.name, f"{name}.msh", "1")
            old = screens.read_picture(member, copy)
            if old is None:
                continue
            data = screens.fitted(member, copy, centred_crest(png, old.size))
            if data is None:
                continue
            out[entry.offset : entry.offset + entry.size] = data.ljust(entry.size, b"\0")
            drawn[slot] = drawn.get(slot, 0) + 1
    return out, drawn
