"""Where PES 2013 (PS2, SLUS_219.55) keeps its rosters, and the records this
patcher writes.

Players: `0_TEXT.AFS` entry 47 part 0 (Latin names) and entry 46 part 0
(Japanese names, same stats) hold 4941 records of 124 bytes:

    +0   UTF-16LE display name, 32 bytes
    +32  ASCII shirt name, 16 bytes
    +48  packed abilities, 76 bytes (byte 53 high nibble = registered position)

Squads, in both entries: part 2 = clubs, 139 rows x 32 u16 player index (row =
club index); part 4 = shirt number - 1 per slot; part 1 = nationals, 75 rows x
23 u16; part 3 = national lineup order. The game shows the club table in RAM in
the same order, so a club slot here is the club's row.

Team names: `OVER.AFS` entries 5-19 are `database_<lang>.ovl` overlays, one per
language, each with a table of `{name ptr, code ptr, u32, u32 id}` entries
whose strings sit in place; a new name has to fit the room before the next
string.
"""

from __future__ import annotations

from dataclasses import dataclass, field

BOOT_FILE = "SLUS_219.55"
TEXT_AFS = "0_TEXT.AFS"
OVER_AFS = "OVER.AFS"

#: `0_TEXT.AFS` entries carrying the player table and the squads.
PLAYER_ENTRY_LATIN = 47
PLAYER_ENTRY_JAPANESE = 46
PLAYER_ENTRIES = (PLAYER_ENTRY_LATIN, PLAYER_ENTRY_JAPANESE)
#: Parts inside each of those entries.
PART_PLAYERS = 0
PART_NATIONAL_SQUADS = 1
PART_CLUB_SQUADS = 2
PART_NATIONAL_ORDER = 3
PART_CLUB_NUMBERS = 4

PLAYER_RECORD_SIZE = 124
DISPLAY_NAME_BYTES = 32
DISPLAY_NAME_CHARS = DISPLAY_NAME_BYTES // 2 - 1
SHIRT_NAME_BYTES = 16
SHIRT_NAME_CHARS = SHIRT_NAME_BYTES - 1
POSITION_BYTE = 53

CLUB_COUNT = 139
CLUB_SQUAD_SIZE = 32
NATIONAL_COUNT = 75
NATIONAL_SQUAD_SIZE = 23

#: `0_TEXT.AFS` entries holding the emblem atlases: all teams at 64x64 and 32x32,
#: then the club-only copies the game also reads.
EMBLEM_ENTRIES = (604, 600, 606, 602)

#: `OVER.AFS` entries that are language database overlays, and the English one
#: the slot names are read from.
DATABASE_OVERLAYS = tuple(range(5, 20))
DATABASE_OVERLAY_ENGLISH = 18
#: Offset of the overlay's load address in its "MWo3" header.
OVERLAY_LOAD_ADDRESS_OFFSET = 8
#: Team-table entry: name pointer, code pointer, u32, u32 id (low byte = club index for clubs).
TEAM_ENTRY_SIZE = 16
#: Nationals occupy the first entries of the team table; clubs follow.
FIRST_CLUB_ENTRY = 67
TEAM_CODE_BYTES = 8

#: Registered-position nibble values (byte 53 >> 4) grouped the way ESPN
#: describes positions. Unknown values count as midfielders.
POSITION_GROUPS = {
    0: "Goalkeeper",
    2: "Defender",
    3: "Defender",
    4: "Defender",
    5: "Midfielder",
    7: "Midfielder",
    8: "Midfielder",
    9: "Midfielder",
    10: "Attacker",
    11: "Attacker",
    12: "Attacker",
}


@dataclass
class PESPlayer:
    """One player to write: names only; the record's abilities are kept."""

    display_name: str
    shirt_name: str
    number: int | None = None
    position: str = ""  # ESPN group: Goalkeeper / Defender / Midfielder / Attacker


@dataclass
class PESTeamRecord:
    """A club slot's new identity."""

    name: str
    code: str = ""
    players: list[PESPlayer] = field(default_factory=list)
    team_id: int | None = None
    logo_url: str = ""


@dataclass
class ClubSlot:
    index: int
    name: str
    code: str


@dataclass
class PESRomInfo:
    path: str
    size: int
    is_valid: bool
    clubs: list[ClubSlot] = field(default_factory=list)
    player_count: int = 0
