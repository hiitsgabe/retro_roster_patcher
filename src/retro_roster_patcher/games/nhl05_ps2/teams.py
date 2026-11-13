"""Clubs the disc knows under an old identity (`core.nhl_clubs`): the
Thrashers become the Winnipeg Jets, the Coyotes the Utah Mammoth, and the East
and West All-Star sides Seattle and Vegas.

What is NHL 2005's own: where its strings live (`FE/LOCAL.VIV`, five languages,
rewritten in place), its All-Star sides' string ids, the front end's spelling
of abbreviations (`PHO` for STEA's `PHX`, with `TEAM_ABBR_PHX` added so the
scoreboard finds Utah's) and the arenas Seattle and Vegas play at. The wordmark of
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
)
from ...core.nhl_clubs import rename_strings as _rename_strings
from ...core.nhl_clubs import rename_tables as _rename_tables
from ...formats.ea_tdb import TDBFile
from .logos import SLOT_FILES

__all__ = [
    "ABBREVIATION",
    "ALL_STARS",
    "ALL_STAR_ARENA",
    "ALL_STAR_HOMES",
    "ALL_STAR_ICE",
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
#: The front end's strings of club abbreviations: `TEAM_ABBR_<abbreviation>`
#: (`ea_hash`), the game looking a club's up by its STEA `ABBR`.
ABBREVIATION = "TEAM_ABBR_"
#: The All-Star game's arena (SARE), where all four sides play on the disc,
#: and the centre ice it keeps for the North America and World sides: the 2005
#: All-Star game's (`ICE_032`), its own (`LOGO` 1) being the Thrashers', which
#: becomes the Jets'.
ALL_STAR_ARENA = 30
ALL_STAR_ICE = 32
#: By side: the arena (SARE row) Seattle and Vegas play at instead, a copy of
#: the All-Star game's, with its centre ice and name. No team plays in the two
#: rows taken ("EA SPORTS Arena, Burnaby"), and the 2005 All-Star game's ice
#: comes four times (`ICE_030` to `ICE_033`), the arena using none of them.
ALL_STAR_HOMES: Mapping[int, tuple[int, int, str, str]] = {
    30: (52, 30, "Climate Pledge Arena", "Seattle, WA"),
    31: (51, 31, "T-Mobile Arena", "Las Vegas, NV"),
}

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


def rename_tables(tdb: TDBFile, slots: Iterable[int]) -> list[Rename]:
    """`core.nhl_clubs.rename_tables`, and Seattle and Vegas each at an arena of
    their own (`ALL_STAR_HOMES`, AREN pointing each side at it), so each plays
    on its own centre ice."""
    renames = _rename_tables(tdb, slots)
    sides = [r.slot for r in renames if r.slot in ALL_STAR_HOMES]
    sare, aren = tdb.get_table("SARE"), tdb.get_table("AREN")
    if not sides or sare is None:
        return renames
    rows = {sare.read_record(i).get("INDX"): i for i in range(min(sare.num_records, sare.capacity))}
    if ALL_STAR_ARENA not in rows:
        return renames
    game = sare.read_record(rows[ALL_STAR_ARENA])
    sare.write_record(rows[ALL_STAR_ARENA], {"LOGO": ALL_STAR_ICE})
    homes = {}
    for side in sides:
        arena, logo, name, city = ALL_STAR_HOMES[side]
        if arena in rows:
            sare.write_record(
                rows[arena], {**game, "INDX": arena, "LOGO": logo, "NAME": name, "CITY": city}
            )
            homes[side] = arena
    if aren is not None:
        for i in range(min(aren.num_records, aren.capacity)):
            team = aren.read_record(i).get("INDX")
            if isinstance(team, int) and team in homes:
                aren.write_record(i, {"ID__": homes[team]})
    return renames


def rename_strings(archive: bytes, renames: Iterable[Rename]) -> tuple[bytes, int, list[str]]:
    """`FE/LOCAL.VIV` renamed in place; see `core.nhl_clubs.rename_strings`.
    The front end spells abbreviations as `FE/TLOGOAPT.VIV`'s files do."""
    spellings = dict(enumerate(SLOT_FILES))
    return _rename_strings(archive, renames, ALL_STAR_STRINGS, spellings, ABBREVIATION)
