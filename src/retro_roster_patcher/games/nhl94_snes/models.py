"""Data models for the NHL94 SNES patcher.

NHL 94 (SNES) has 28 teams (26 NHL + 2 All-Star) with ~23 players each.
"""

from dataclasses import dataclass, field

from ...sports.models import (  # noqa: F401
    League,
    LeagueData,
    Player,
    PlayerStats,
    Team,
    TeamRoster,
)

# Team order in the ROM: 26 NHL teams alphabetically, then 2 All-Star teams.
NHL94_TEAM_ORDER = [
    "Anaheim",  # 0
    "Boston",  # 1
    "Buffalo",  # 2
    "Calgary",  # 3
    "Chicago",  # 4
    "Dallas",  # 5
    "Detroit",  # 6
    "Edmonton",  # 7
    "Florida",  # 8
    "Hartford",  # 9 - now Carolina
    "Los Angeles",  # 10
    "Montreal",  # 11
    "New Jersey",  # 12
    "NY Islanders",  # 13
    "NY Rangers",  # 14
    "Ottawa",  # 15
    "Philadelphia",  # 16
    "Pittsburgh",  # 17
    "Quebec",  # 18 - now Colorado
    "San Jose",  # 19
    "St. Louis",  # 20
    "Tampa Bay",  # 21
    "Toronto",  # 22
    "Vancouver",  # 23
    "Washington",  # 24
    "Winnipeg",  # 25
    "All-Star East",  # 26
    "All-Star West",  # 27
]

# Modern NHL team abbreviation → ROM slot index, ESPN variants included.
# Excludes expansion teams absent from NHL94: CBJ, MIN, NSH, SEA, UTA, VGK.
MODERN_NHL_TO_NHL94 = {
    "ANA": 0,
    "BOS": 1,
    "BUF": 2,
    "CGY": 3,
    "CHI": 4,
    "DAL": 5,
    "DET": 6,
    "EDM": 7,
    "FLA": 8,
    "CAR": 9,  # was Hartford
    "LAK": 10,
    "LA": 10,  # ESPN abbreviation
    "MTL": 11,
    "NJD": 12,
    "NJ": 12,  # ESPN abbreviation
    "NYI": 13,
    "NYR": 14,
    "OTT": 15,
    "PHI": 16,
    "PIT": 17,
    "COL": 18,  # was Quebec
    "SJS": 19,
    "SJ": 19,  # ESPN abbreviation
    "STL": 20,
    "TBL": 21,
    "TB": 21,  # ESPN abbreviation
    "TOR": 22,
    "VAN": 23,
    "WSH": 24,
    "WPG": 25,
    # 26 and 27 are All-Star teams (not mapped)
}

TEAM_COUNT = 28
MAX_PLAYERS_PER_TEAM = 25

# Roster composition assumed when the ROM cannot be asked, in the order
# `read_team_player_counts` returns it: (goalies, forwards, defensemen).
DEFAULT_ROSTER_COUNTS = (2, 14, 7)


@dataclass
class NHL94PlayerAttributes:
    """Nibble-packed stats. Each is 0-6, which the game shows as:
    0 = 25, 1 = 35, 2 = 45, 3 = 55, 4 = 65, 5 = 85, 6 = 100
    """

    speed: int = 3
    agility: int = 3
    shot_power: int = 3
    shot_accuracy: int = 3
    stick_handling: int = 3
    pass_accuracy: int = 3
    off_awareness: int = 3
    def_awareness: int = 3
    checking: int = 3
    endurance: int = 3
    # hidden stats
    roughness: int = 2
    aggression: int = 2


@dataclass
class NHL94PlayerRecord:
    name: str  # plain ASCII, variable length
    jersey_number: int = 1  # 1-99, BCD
    weight_class: int = 7  # 0-14 (140-252 lbs via 140 + class*8)
    handedness: int = 0  # 0=L (even), 1=R (odd)
    is_goalie: bool = False
    attributes: NHL94PlayerAttributes = field(default_factory=NHL94PlayerAttributes)


@dataclass
class NHL94TeamRecord:
    """One ROM slot's worth of mapped roster, and the shape it was cut to.

    The header's line assignments index the player list by absolute position --
    forwards at 2, defencemen at `2 + num_forwards` -- so the counts must travel
    with the selection that produced them. `name`, `city` and `acronym` are never
    written to the ROM.
    """

    index: int  # 0-27
    name: str
    city: str
    acronym: str  # 3-letter, e.g. "BOS"
    players: list[NHL94PlayerRecord] = field(default_factory=list)
    num_goalies: int = DEFAULT_ROSTER_COUNTS[0]
    num_forwards: int = DEFAULT_ROSTER_COUNTS[1]
    num_defensemen: int = DEFAULT_ROSTER_COUNTS[2]


@dataclass
class NHL94TeamSlot:
    index: int
    current_name: str
    display_name: str  # name from NHL94_TEAM_ORDER


@dataclass
class NHL94RomInfo:
    path: str
    size: int
    team_slots: list[NHL94TeamSlot] = field(default_factory=list)
    is_valid: bool = False
    has_header: bool = False  # SNES dumps may carry a 512-byte copier header


@dataclass
class NHL94SlotMapping:
    team: Team
    slot_index: int  # 0-27
    slot_name: str
