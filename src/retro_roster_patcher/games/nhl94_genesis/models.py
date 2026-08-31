"""Data models for the NHL94 Genesis patcher.

NHL 94 Genesis has 26 teams (all NHL teams from 1993-94 season).
Each team block is ~1024 bytes containing color palettes, team attributes,
player/goalie counts, line assignments, and player records.

References:
  - https://forum.nhl94.com/index.php?/topic/26353-how-to-manually-edit-the-team-player-data-nhl-94/
  - https://nhl94.com/html/editing/edit_bin.php
"""

from dataclasses import dataclass, field

# Re-export shared sports models
from ...sports.models import (  # noqa: F401
    League,
    LeagueData,
    Player,
    PlayerStats,
    Team,
    TeamRoster,
)

# Team order in the original NHL 94 Genesis ROM (26 teams)
# Pointer table at 0x030E, 4 bytes per entry
NHL94_GEN_TEAM_ORDER = [
    "Anaheim",  # 0  - Mighty Ducks of Anaheim
    "Boston",  # 1
    "Buffalo",  # 2
    "Calgary",  # 3
    "Chicago",  # 4
    "Dallas",  # 5
    "Detroit",  # 6
    "Edmonton",  # 7
    "Florida",  # 8
    "Hartford",  # 9  - now Carolina Hurricanes
    "Los Angeles",  # 10
    "Montreal",  # 11
    "New Jersey",  # 12
    "NY Islanders",  # 13
    "NY Rangers",  # 14
    "Ottawa",  # 15
    "Philadelphia",  # 16
    "Pittsburgh",  # 17
    "Quebec",  # 18 - now Colorado Avalanche
    "San Jose",  # 19
    "St. Louis",  # 20
    "Tampa Bay",  # 21
    "Toronto",  # 22
    "Vancouver",  # 23
    "Washington",  # 24
    "Winnipeg",  # 25 - now Winnipeg Jets (returned 2011)
]

# Modern NHL team abbreviation → NHL94 Genesis ROM slot index.
# Includes ESPN abbreviation variants.
# Excludes expansion teams not in NHL94: CBJ, MIN, NSH, SEA, UTA, VGK.
MODERN_NHL_TO_NHL94_GEN = {
    "ANA": 0,  # Anaheim Ducks
    "BOS": 1,  # Boston Bruins
    "BUF": 2,  # Buffalo Sabres
    "CGY": 3,  # Calgary Flames
    "CHI": 4,  # Chicago Blackhawks
    "DAL": 5,  # Dallas Stars
    "DET": 6,  # Detroit Red Wings
    "EDM": 7,  # Edmonton Oilers
    "FLA": 8,  # Florida Panthers
    "CAR": 9,  # Carolina Hurricanes (was Hartford)
    "LAK": 10,  # Los Angeles Kings
    "LA": 10,  # ESPN abbreviation
    "MTL": 11,  # Montreal Canadiens
    "NJD": 12,  # New Jersey Devils
    "NJ": 12,  # ESPN abbreviation
    "NYI": 13,  # New York Islanders
    "NYR": 14,  # New York Rangers
    "OTT": 15,  # Ottawa Senators
    "PHI": 16,  # Philadelphia Flyers
    "PIT": 17,  # Pittsburgh Penguins
    "COL": 18,  # Colorado Avalanche (was Quebec)
    "SJS": 19,  # San Jose Sharks
    "SJ": 19,  # ESPN abbreviation
    "STL": 20,  # St. Louis Blues
    "TBL": 21,  # Tampa Bay Lightning
    "TB": 21,  # ESPN abbreviation
    "TOR": 22,  # Toronto Maple Leafs
    "VAN": 23,  # Vancouver Canucks
    "WSH": 24,  # Washington Capitals
    "WPG": 25,  # Winnipeg Jets
}

TEAM_COUNT = 26
MAX_PLAYERS_PER_TEAM = 25


@dataclass
class NHL94GenPlayerAttributes:
    """NHL94 Genesis player attributes (0-6 scale, stored as nibbles).

    14 attributes packed into 7 bytes. Each nibble is 0-6 (except
    weight which uses the full 0-14 range, and handedness which is 0/1).
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
    roughness: int = 2
    aggression: int = 2


@dataclass
class NHL94GenPlayerRecord:
    """Complete player record ready to write to ROM.

    In the ROM, each player record is:
      [2 bytes] name length (LE, includes itself)
      [N bytes] player name (ASCII)
      [1 byte]  jersey number (BCD: high=tens, low=ones)
      [7 bytes] 14 attributes packed as nibbles
    """

    name: str
    position: str = "C"  # C, LW, RW, D, G
    jersey_number: int = 1  # 1-99
    weight_class: int = 7  # 0-14 (140 + class*8 = lbs)
    handedness: int = 0  # 0=L (even nibble), 1=R (odd nibble)
    is_goalie: bool = False
    attributes: NHL94GenPlayerAttributes = field(default_factory=NHL94GenPlayerAttributes)


@dataclass
class NHL94GenTeamRecord:
    """Complete team record ready to write to ROM."""

    index: int  # 0-25
    name: str
    city: str
    acronym: str
    players: list[NHL94GenPlayerRecord] = field(default_factory=list)


@dataclass
class NHL94GenTeamSlot:
    """An existing team slot read from the ROM."""

    index: int
    current_name: str  # City name read from ROM
    display_name: str  # Name from NHL94_GEN_TEAM_ORDER


@dataclass
class NHL94GenRomInfo:
    """Information about a loaded NHL94 Genesis ROM."""

    path: str
    size: int
    team_slots: list[NHL94GenTeamSlot] = field(default_factory=list)
    is_valid: bool = False


@dataclass
class NHL94GenSlotMapping:
    """Maps a modern NHL team to an NHL94 Genesis ROM slot."""

    team: Team
    slot_index: int
    slot_name: str
