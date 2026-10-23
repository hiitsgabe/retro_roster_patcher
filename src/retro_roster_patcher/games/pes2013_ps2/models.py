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

from ...core.kits import KitSpec

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
#: National teams are slots too: row `r` of the national squad table is slot
#: `NATIONAL_SLOT_BASE + r`. Only the named rows (the first 67, which the
#: overlay team table names) are offered.
NATIONAL_SLOT_BASE = 1000
NATIONAL_NAMED = 67
#: Position nibble written into a record that changes hands between groups.
GROUP_POSITIONS = {"Goalkeeper": 0, "Defender": 3, "Midfielder": 7, "Attacker": 12}

#: `0_TEXT.AFS` entries holding the emblem atlases: all teams at 64x64 and 32x32,
#: then the club-only copies the game also reads.
EMBLEM_ENTRIES = (604, 600, 606, 602)
#: (full atlas, club-only copy) of each size; a pair shares one palette.
EMBLEM_PAIRS = ((604, 606), (600, 602))

#: Screen art the patch can replace: slot name -> texture name in the UI
#: bundles (every occurrence, in every language archive, is rewritten).
IMAGE_SLOTS = {
    "title": "title_bgNS",  # the title screen (512x512)
    "menu": "mo_sub_bgUS",  # behind the main menus (512x512)
    "loading": "loading_bg00",  # the loading screen (256x256)
    "match": "ts_match_bg",  # behind the match screens (512x512)
}
IMAGE_SLOT_NAMES: tuple[str, ...] = tuple(IMAGE_SLOTS)
#: The language archive the exported art is read from.
TEXT_ARCHIVE_PRIMARY = "U_TEXT.AFS"

#: The UI text archives, one per language of the US release.
TEXT_LANGUAGE_AFS = ("U_TEXT.AFS", "B_TEXT.AFS", "F_TEXT.AFS", "M_TEXT.AFS")
#: Entries of those archives whose string tables carry the league names.
LEAGUE_NAME_ENTRIES = (15, 17)
#: The league-name tables list the leagues around "Ligue 1", the one string
#: every language shares: a group's name sits this many strings from it.
LEAGUE_NAME_ANCHOR = "Ligue 1"


@dataclass(frozen=True)
class LeagueGroup:
    """A run of club slots the game shows as one league."""

    first: int
    count: int
    icon: str  # texture name of the team-select icon
    name_offset: int  # strings from "Ligue 1" in the league-name tables

    def holds(self, slot: int) -> bool:
        return self.first <= slot < self.first + self.count


LEAGUE_GROUPS = (
    LeagueGroup(0, 20, "mn_ts_eng_l", -1),
    LeagueGroup(20, 20, "mn_ts_fra_l", 0),
    LeagueGroup(40, 20, "mn_ts_ita_l", 2),
    LeagueGroup(60, 18, "mn_ts_net_l", 3),
    LeagueGroup(78, 20, "mn_ts_spa_l", 4),
)

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
    #: A starter's (one of the first eleven) registered-position nibble: where
    #: the lineup had him, else his group's position; None for a bench player.
    #: Starters take the squad places whose records hold that position, which
    #: is how the club's formation is laid out.
    role: int | None = None


@dataclass
class PESTeamRecord:
    """A club slot's new identity."""

    name: str
    code: str = ""
    players: list[PESPlayer] = field(default_factory=list)
    team_id: int | None = None
    logo_url: str = ""
    #: The league the team came from: written as the slot group's league.
    league_name: str = ""
    league_logo_url: str = ""
    #: A national-team slot (`NATIONAL_SLOT_BASE` and up).
    national: bool = False
    #: Home and away kits to draw from colours (`core.kits.KitSpec`); None keeps the disc's.
    kit_home: KitSpec | None = None
    kit_away: KitSpec | None = None


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
    nationals: list[ClubSlot] = field(default_factory=list)
    player_count: int = 0
