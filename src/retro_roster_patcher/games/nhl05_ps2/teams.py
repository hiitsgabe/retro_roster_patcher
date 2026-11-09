"""Clubs the disc knows under an old identity (`core.nhl_clubs`): the
Thrashers become the Winnipeg Jets, the Coyotes the Utah Mammoth, and the East
and West All-Star sides Seattle and Vegas.

What is NHL 2005's own: where its strings live (`FE/LOCAL.VIV`, five languages,
rewritten in place) and its All-Star sides' string ids. The wordmark of
`FE/TLOGOAPT.VIV` is `wordmarks`'.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ...core.nhl_clubs import (
    ALL_STARS,
    MARKS,
    RELOCATED,
    Identity,
    Rename,
    plain,
    rename_tables,
)
from ...core.nhl_clubs import rename_strings as _rename_strings

__all__ = [
    "ALL_STARS",
    "ALL_STAR_STRINGS",
    "LOCAL_ARCHIVE",
    "MARKS",
    "RELOCATED",
    "Identity",
    "Rename",
    "plain",
    "rename_strings",
    "rename_tables",
]

LOCAL_ARCHIVE = "FE/LOCAL.VIV"

#: The All-Star sides' names in `front<language>.bin`, by string id and the part
#: of the name each holds: the full name, the abbreviation, the city in three
#: lists and the nickname in two (frontEnglish.bin entries 1095-1096,
#: 1151-1152, 1207-1208, 4250-4253 and 4919-4922). The ids are the same in all
#: five languages and the texts are not (French splits "All Stars de l'Est"
#: into "All Stars" and "de l'Est"), and "All-Stars" is every side's nickname.
ALL_STAR_STRINGS: Mapping[int, Mapping[int, str]] = {
    30: {
        0x3B9E15F1: "full",
        0xC49CFB94: "abbr",
        0xFA83B68D: "city",
        0x2EAEFF68: "city",
        0x2EAF0E28: "city",
        0x2EC117E9: "nick",
        0x2EC126A9: "nick",
    },
    31: {
        0xB6A3CD83: "full",
        0xC49D48AA: "abbr",
        0xFA8403A3: "city",
        0x2EAEFF7A: "city",
        0x2EAF5B3E: "city",
        0x2EC117FB: "nick",
        0x2EC173BF: "nick",
    },
}


def rename_strings(archive: bytes, renames: Iterable[Rename]) -> tuple[bytes, int, list[str]]:
    """`FE/LOCAL.VIV` renamed in place; see `core.nhl_clubs.rename_strings`."""
    return _rename_strings(archive, renames, ALL_STAR_STRINGS)
