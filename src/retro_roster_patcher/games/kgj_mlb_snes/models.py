"""Data models for the KGJ MLB patcher.

28 MLB teams (14 AL, 14 NL), 25 players each (15 batters + 10 pitchers). A player
record is 32 bytes and names use a custom character encoding.

  - https://github.com/johnz1/ken_griffey_jr_presents_major_league_baseball_tools
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

# Custom character encoding used for player names.
#
# Exactly one lowercase letter is mapped: `c`, at 0x36, so that `_split_name` can
# render "McGWIRE". The cartridge font holds the rest of the lowercase alphabet --
# 1994 rosters include DeShields -- and this table simply does not enumerate them.
# Two live consequences: the writer maps anything absent to 0x00, a SPACE, so
# accents in a modern roster (Acuna, Baez) are written as blanks; and the reader
# renders anything absent as "?", which is why the ROM signature check does not
# test name bytes against this table.
CHAR_TO_BYTE = {
    " ": 0x00,
    "0": 0x01,
    "1": 0x02,
    "2": 0x03,
    "3": 0x04,
    "4": 0x05,
    "5": 0x06,
    "6": 0x07,
    "7": 0x08,
    "8": 0x09,
    "9": 0x0A,
    "A": 0x0B,
    "B": 0x0C,
    "C": 0x0D,
    "D": 0x0E,
    "E": 0x0F,
    "F": 0x10,
    "G": 0x11,
    "H": 0x12,
    "I": 0x13,
    "J": 0x14,
    "K": 0x15,
    "L": 0x16,
    "M": 0x17,
    "N": 0x18,
    "O": 0x19,
    "P": 0x1A,
    "Q": 0x1B,
    "R": 0x1C,
    "S": 0x1D,
    "T": 0x1E,
    "U": 0x1F,
    "V": 0x20,
    "W": 0x21,
    "X": 0x22,
    "Y": 0x23,
    "Z": 0x24,
    "c": 0x36,
}
BYTE_TO_CHAR = {v: k for k, v in CHAR_TO_BYTE.items()}

# Position encoding, stepping by 2
POSITION_TO_BYTE = {
    "P": 0x00,
    "C": 0x02,
    "LF": 0x04,
    "CF": 0x06,
    "RF": 0x08,
    "3B": 0x0A,
    "SS": 0x0C,
    "2B": 0x0E,
    "1B": 0x10,
    "DH": 0x12,
    "IF": 0x14,
    "OF": 0x16,
}
BYTE_TO_POSITION = {v: k for k, v in POSITION_TO_BYTE.items()}

HAND_RIGHT = 0x00
HAND_LEFT = 0x11
HAND_SWITCH = 0x20

PLAYER_LENGTH = 0x20  # 32 bytes per player
TEAM_LENGTH = 0x320  # 800 bytes per team (25 * 32)
AL_TO_NL_GAP = 0xB40  # 2880 bytes between last AL and first NL team
PLAYERS_PER_TEAM = 25  # 15 batters + 5 starters + 5 relievers
BATTERS_PER_TEAM = 15
STARTERS_PER_TEAM = 5
RELIEVERS_PER_TEAM = 5

# Roster-type nibble in the high half of record byte 0x19: 3 = batter, 1 =
# starting pitcher, 0 = relief pitcher. Which one a record gets is decided from
# the slot index alone; see `patcher._roster_type_for_slot`.
ROSTER_TYPE_BATTER = 0x30
ROSTER_TYPE_STARTER = 0x10
ROSTER_TYPE_RELIEVER = 0x00

# 14 bytes immediately before team 0, used to locate the team tables
FIRST_TEAM_MARKER = bytes(
    [
        0x81,
        0x81,
        0x81,
        0x81,
        0x9F,
        0x9F,
        0x90,
        0x90,
        0x90,
        0x90,
        0x90,
        0x90,
        0xF0,
        0xF0,
    ]
)

# Team order in ROM: 0-13 = AL, 14-27 = NL (1994 MLB)
TEAM_COUNT = 28
AL_TEAMS = 14
NL_TEAMS = 14

KGJ_TEAM_ORDER = [
    # American League (0-13)
    "Baltimore Orioles",  # 0
    "Boston Red Sox",  # 1
    "California Angels",  # 2
    "Chicago White Sox",  # 3
    "Cleveland Indians",  # 4
    "Detroit Tigers",  # 5
    "Kansas City Royals",  # 6
    "Milwaukee Brewers",  # 7
    "Minnesota Twins",  # 8
    "New York Yankees",  # 9
    "Oakland Athletics",  # 10
    "Seattle Mariners",  # 11
    "Texas Rangers",  # 12
    "Toronto Blue Jays",  # 13
    # National League (14-27)
    "Atlanta Braves",  # 14
    "Chicago Cubs",  # 15
    "Cincinnati Reds",  # 16
    "Houston Astros",  # 17
    "Los Angeles Dodgers",  # 18
    "Montreal Expos",  # 19
    "New York Mets",  # 20
    "Pittsburgh Pirates",  # 21
    "St. Louis Cardinals",  # 22
    "San Diego Padres",  # 23
    "San Francisco Giants",  # 24
    "Philadelphia Phillies",  # 25
    "Colorado Rockies",  # 26
    "Florida Marlins",  # 27
]

# Modern MLB team abbreviation -> ROM slot index. The 30 current teams onto 28
# slots; Arizona and Tampa Bay did not exist in 1994 and have none.
#
# CWS/CHW both mean slot 3 and OAK/ATH both mean slot 10, so `map_rosters` guards
# the collision.
MODERN_MLB_TO_KGJ = {
    "BAL": 0,
    "BOS": 1,
    "LAA": 2,  # was California
    "CWS": 3,
    "CHW": 3,
    "CLE": 4,  # was the Indians
    "DET": 5,
    "KC": 6,
    "MIL": 7,
    "MIN": 8,
    "NYY": 9,
    "OAK": 10,
    "ATH": 10,
    "SEA": 11,
    "TEX": 12,
    "TOR": 13,
    "ATL": 14,
    "CHC": 15,
    "CIN": 16,
    "HOU": 17,
    "LAD": 18,
    "WSH": 19,  # was Montreal
    "NYM": 20,
    "PIT": 21,
    "STL": 22,
    "SD": 23,
    "SF": 24,
    "PHI": 25,
    "COL": 26,
    "MIA": 27,  # was Florida
}


@dataclass
class KGJBatterAttributes:
    """Batter ratings (1-10 scale)."""

    batting: int = 5  # BAT — contact/hitting ability
    power: int = 5  # POW — home run power
    speed: int = 5  # SPD — baserunning speed
    defense: int = 5  # DEF — fielding ability


@dataclass
class KGJPitcherAttributes:
    """Pitcher ratings (1-10 scale)."""

    speed: int = 5  # SPD — fastball velocity
    control: int = 5  # CON — pitch accuracy
    fatigue: int = 5  # FAT — stamina


@dataclass
class KGJBatterAppearance:
    """Visual appearance for a batter at the plate."""

    skin: int = 0  # 0-5: White, Tan, Very Tan, Light Black, Black, Dark Black
    head: int = 0  # 0-7: hair/facial hair combos
    hair_color: int = 4  # 0-5: Blonde/Brown, Red, Brown, Bald, Black, Blonde
    body: int = 1  # 0-7: build/stance
    legs_size: int = 0  # 0-1: Average, Small
    legs_stance: int = 0  # 0-4: various stances
    arms_stance: int = 0  # 0-2: bat position


@dataclass
class KGJPitcherAppearance:
    """Visual appearance for a pitcher on the mound."""

    skin: int = 0  # 0-5: same as batter
    head: int = 0  # 0-4: hair/facial hair combos
    hair_color: int = 4  # 0-4: Blonde, Red, Blonde/Brown, Brown, Black
    body: int = 0  # 0-2: Average, Fat, Tall
    throwing_style: int = 0  # 0=Overhand, 1=Sidearm


@dataclass
class KGJPlayerRecord:
    """A 32-byte player record."""

    first_initial: str = "A"
    last_name: str = "PLAYER"
    position: str = "CF"
    jersey_number: int = 1
    is_pitcher: bool = False
    bat_hand: int = HAND_RIGHT

    batter_attrs: KGJBatterAttributes = field(default_factory=KGJBatterAttributes)
    batter_appearance: KGJBatterAppearance = field(default_factory=KGJBatterAppearance)
    batting_avg: int = 250  # e.g. 250 = .250
    home_runs: int = 0
    rbi: int = 0

    pitcher_attrs: KGJPitcherAttributes = field(default_factory=KGJPitcherAttributes)
    pitcher_appearance: KGJPitcherAppearance = field(default_factory=KGJPitcherAppearance)
    pitch_hand: int = 0  # 0=Right, 1=Left
    wins: int = 0
    losses: int = 0
    era: int = 400  # e.g. 400 = 4.00 ERA
    saves: int = 0

    # 0x30 = batter, 0x10 = starter, 0x00 = reliever. Stamped by
    # `patcher.map_rosters`; the writer only reads it.
    roster_type: int = ROSTER_TYPE_BATTER


@dataclass
class KGJTeamRecord:
    index: int
    name: str
    players: list[KGJPlayerRecord] = field(default_factory=list)


@dataclass
class KGJTeamSlot:
    index: int
    name: str  # from KGJ_TEAM_ORDER
    first_player: str


@dataclass
class KGJRomInfo:
    path: str
    size: int
    first_team_offset: int = 0
    team_slots: list[KGJTeamSlot] = field(default_factory=list)
    is_valid: bool = False
    has_header: bool = False
