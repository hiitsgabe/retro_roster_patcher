"""The disc's 54 national teams (national slots 0-53) and the flag each wears.

A nation's flag is a style byte (a shape: three bitmaps in the flag atlas) and the
32 bytes of its slot's colour entry (`rom_writer._NAT_COLOR_OFFSETS`). A patch
rewrites both for every slot it maps, and gives a logo a style nobody untouched
uses, painting over that style's bitmaps; so `assets/we2002_nations.zlib` packages
what the retail disc has (`build`): each nation's style and colour entry, and the
bitmaps of those styles. A nation the fill writes anywhere wears its own flag
from there, over an image an earlier patch wrote too. A real-disc test pins the
package to the disc.

Slots 54-62 are the two all-star sides and the seven classic teams, hidden on the
exhibition grid; they are nobody's nation.
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass
from functools import cache

from ...core.assets import package_bytes
from .flag_atlas import LogoImages, read_styles
from .rom_writer import _NAT_COLOR_OFFSETS, _OFS_BANDIERE_FORMA1

#: The disc's names (its ASCII table, which lists them backwards), ESPN's spelling
#: where it is the same.
NAMES = (
    "Ireland", "Scotland", "Wales", "England", "Portugal", "Spain", "France", "Belgium",
    "Netherlands", "Switzerland", "Italy", "Czecho", "Germany", "Denmark", "Norway", "Sweden",
    "Finland", "Poland", "Slovakia", "Austria", "Hungary", "Slovenia", "Croatia", "Yugoslavia",
    "Romania", "Bulgaria", "Greece", "Turkey", "Ukraine", "Russia", "Morocco", "Tunisia", "Egypt",
    "Nigeria", "Cameroon", "South Africa", "Senegal", "U.States", "Mexico", "Costa Rica",
    "Colombia", "Brazil", "Peru", "Chile", "Paraguay", "Uruguay", "Argentina", "Ecuador", "Japan",
    "Korea", "China", "Iran", "Saudi Arabia", "Australia",
)  # fmt: skip
#: The disc's spelling -> ESPN's (league 2021).
ALIASES = {
    "Ireland": "Republic of Ireland",
    "Czecho": "Czechia",
    "Yugoslavia": "Serbia",
    "Turkey": "Türkiye",
    "U.States": "United States",
    "Korea": "South Korea",
}
#: National slots without a colour entry the writer knows: a flag moved there would
#: draw through the slot's own colours, so the fill leaves them alone.
NO_COLOURS = tuple(n for n in range(len(NAMES)) if n not in _NAT_COLOR_OFFSETS)


@dataclass(frozen=True)
class Flag:
    style: int
    clut: bytes | None  # the slot's 32-byte colour entry; None for a slot without one


def read_flag(f, slot: int) -> Flag:
    """The flag national slot `slot` wears in the open image `f` now."""
    f.seek(_OFS_BANDIERE_FORMA1 + slot)
    style = f.read(1)[0]
    if slot not in _NAT_COLOR_OFFSETS:
        return Flag(style, None)
    clut = b""
    for offset, size in _NAT_COLOR_OFFSETS[slot]:
        f.seek(offset)
        clut += f.read(size)
    return Flag(style, clut)


def build(image_path: str) -> bytes:
    """The package, read off a retail disc."""
    with open(image_path, "rb") as f:
        flags = [read_flag(f, n) for n in range(len(NAMES))]
    styles = read_styles(image_path, sorted({fl.style for fl in flags}))
    doc = {
        "nations": [[fl.style, fl.clut.hex() if fl.clut else ""] for fl in flags],
        "styles": {
            str(s): "".join(f"{v:x}" for rows in (i.big, i.medium, i.mini) for r in rows for v in r)
            for s, i in styles.items()
        },
    }
    return zlib.compress(json.dumps(doc, sort_keys=True).encode(), 9)


@cache
def _doc() -> dict:
    data: dict = json.loads(
        zlib.decompress(package_bytes("retro_roster_patcher.assets", "we2002_nations.zlib"))
    )
    return data


def flag(n: int) -> Flag:
    """Nation `n`'s flag on the retail disc."""
    style, clut = _doc()["nations"][n]
    return Flag(style, bytes.fromhex(clut) if clut else None)


def bitmaps(style: int) -> LogoImages:
    """Style `style`'s three bitmaps on the retail disc (one a nation wears)."""
    values = [int(c, 16) for c in _doc()["styles"][str(style)]]

    def rows(w: int, h: int, at: int) -> list[list[int]]:
        return [values[at + y * w : at + (y + 1) * w] for y in range(h)]

    return LogoImages([], rows(64, 28, 0), rows(24, 16, 64 * 28), rows(24, 8, 64 * 28 + 24 * 16))
