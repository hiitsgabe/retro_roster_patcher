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
# Seattle and Vegas take the two All-Star slots, which the game offers in
# exhibition only (they are in no playoff seed table). Left out: CBJ, MIN, NSH, UTA.
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
    "SEA": 26,  # was All-Stars East
    "VGK": 27,  # was All-Stars West
}

TEAM_COUNT = 28
MAX_PLAYERS_PER_TEAM = 25

# Roster composition assumed when the ROM cannot be asked, in the order
# `read_team_player_counts` returns it: (goalies, forwards, defensemen).
DEFAULT_ROSTER_COUNTS = (2, 14, 7)

# The cartridge's own (forwards, defencemen) per slot, its byte 17. A patch
# writes what it filled there, so `analyze_rom` reports the larger of the two:
# a slot one season's data left short is filled again by the next.
RETAIL_ROSTER_COUNTS = [
    (9, 7),  # Anaheim
    (15, 7),
    (13, 8),
    (12, 9),
    (14, 8),
    (13, 9),
    (15, 8),
    (14, 8),
    (9, 6),  # Florida
    (14, 6),
    (12, 9),
    (14, 8),
    (15, 7),
    (13, 8),
    (14, 7),
    (15, 8),
    (13, 7),
    (13, 9),
    (13, 8),
    (12, 8),
    (13, 9),
    (14, 7),
    (14, 8),
    (14, 8),
    (14, 8),
    (15, 8),
    (14, 8),
    (14, 8),
]


@dataclass
class NHL94PlayerAttributes:
    """A skater's nibble-packed stats. Each is 0-6, which the Team Roster shows
    as 18 x n squashed below 50 (v / 2 + 25): 0 = 25, 1 = 34, 2 = 43, 3 = 54,
    4 = 72, 5 = 90, 6 = 100, then +-9 from the per-game hot/cold modifier.
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
class NHL94GoalieAttributes:
    """A goalie's nibbles, 0-6 each, named as the game's own goalie columns
    ($80:D8CD) name them. The overall is (9 x agility + 9 x awareness + 9 x puck
    control + 2 x each save zone) / 2; the save routine ($9E:F098) picks one of
    the four zones by the shot's side. The skater nibbles left over (checking,
    stick handling, shot accuracy) are never read for a goalie and stay 0, as on
    the cartridge.
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
class NHL94PlayerRecord:
    name: str  # plain ASCII, variable length
    jersey_number: int = 1  # 1-99, BCD
    weight_class: int = 7  # 0-14 (140-252 lbs via 140 + class*8)
    handedness: int = 0  # 0=L (even), 1=R (odd); a goalie's catching hand
    is_goalie: bool = False
    attributes: NHL94PlayerAttributes | NHL94GoalieAttributes = field(
        default_factory=NHL94PlayerAttributes
    )
    photo_url: str = ""  # the head shot `portraits` draws; "" for the game's drawing


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
    #: Header byte 14, attack (goals for) high and leakiness (goals against) low,
    #: 0-7 each: how the game simulates this team's scores in the other games of
    #: the night. None keeps the cartridge's byte.
    strength: int | None = None
    #: The club behind the slot: `identity` writes its name, colours and logo.
    #: None keeps the cartridge's.
    team: Team | None = None


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
