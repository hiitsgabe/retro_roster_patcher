"""Data models for the NHL94 Genesis patcher.

26 teams, the 1993-94 NHL. Each team block is ~1024 bytes: colour palettes, team
attributes, player/goalie counts, line assignments, then the player records.

  - https://forum.nhl94.com/index.php?/topic/26353-how-to-manually-edit-the-team-player-data-nhl-94/
  - https://nhl94.com/html/editing/edit_bin.php
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

# Team order in the original ROM. Pointer table at 0x030E, 4 bytes per entry.
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
    # Slots a patch adds (`identity.expand`); All-Stars East and West move to 32 and 33.
    "Columbus",  # 26
    "Minnesota",  # 27
    "Nashville",  # 28
    "Seattle",  # 29
    "Utah",  # 30
    "Vegas",  # 31
]

# Modern NHL team abbreviation → ROM slot index, ESPN variants included.
MODERN_NHL_TO_NHL94_GEN = {
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
    "CBJ": 26,
    "MIN": 27,
    "NSH": 28,
    "SEA": 29,
    "UTA": 30,
    "VGK": 31,
}

#: The clubs a patch writes, the six new ones included.
TEAM_COUNT = 32
#: The clubs on the cartridge, before `identity.expand`.
CARTRIDGE_TEAMS = 26
MAX_PLAYERS_PER_TEAM = 25


@dataclass
class NHL94GenPlayerAttributes:
    """NHL94 Genesis player attributes (0-6 scale, stored as nibbles).

    14 attributes packed into 7 bytes. Each nibble is 0-6 (except
    weight which uses the full 0-14 range, and the handedness nibble, whose bit
    0 is the hand and bits 1-3 `fighting`).

    `shot_bias` is the nibble editors call roughness: nhl94.com's forum reads it
    as the CPU's lean to shoot (high) or pass (low), the AI weighs it when it has
    the puck ($E32C, $E444), and the cartridge's follows goals minus assists per
    game. No screen shows it.

    `fighting` is named here for the cartridge's own values, its enforcers' the
    highest (Twist and McRae 6, Grimson, Probert and Odjick 5): no screen or
    editor names it. NHL 94 has no fights and reads it where a check injures a
    player ($0144AC): the checker's 6 puts him out for the game, his 0 or 1 for
    the period, and anything between for the game only when the injured player's
    own is odd. A check by a player whose is 0 also adds 3, not 1, to a counter
    ($C46E) that no code found reads.
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
    shot_bias: int = 2
    aggression: int = 2
    fighting: int = 2


@dataclass
class NHL94GenGoalieAttributes:
    """A goalie's nibbles, 0-6 each, as the cartridge lays them out (nhl94.com's
    edit_bin, and all 59 of its goalies): agility, speed, defensive awareness
    (in byte 2's low nibble too), puck control, then stick and glove saves to
    each side. The skater nibbles left over (checking, stick handling, shot
    accuracy) are 0.
    """

    agility: int = 3
    speed: int = 3
    def_awareness: int = 3
    puck_control: int = 3
    stick_right: int = 3
    stick_left: int = 3
    glove_right: int = 3
    glove_left: int = 3


@dataclass
class NHL94GenPlayerRecord:
    """A player record as the ROM stores it.

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
    attributes: NHL94GenPlayerAttributes | NHL94GenGoalieAttributes = field(
        default_factory=NHL94GenPlayerAttributes
    )
    #: The head shot `portraits` draws his card from; "" for the game's drawing.
    photo_url: str = ""


class NHL94GenRoster(list):
    """A slot's player records, and the club they play for (None when unknown)."""

    def __init__(self, players=(), team: Team | None = None) -> None:
        super().__init__(players)
        self.team = team


@dataclass
class NHL94GenTeamRecord:
    index: int  # 0-25
    name: str
    city: str
    acronym: str
    players: list[NHL94GenPlayerRecord] = field(default_factory=list)


@dataclass
class NHL94GenTeamSlot:
    index: int
    current_name: str  # city name read from the ROM
    display_name: str  # name from NHL94_GEN_TEAM_ORDER


@dataclass
class NHL94GenRomInfo:
    path: str
    size: int
    team_slots: list[NHL94GenTeamSlot] = field(default_factory=list)
    is_valid: bool = False


@dataclass
class NHL94GenSlotMapping:
    team: Team
    slot_index: int
    slot_name: str
