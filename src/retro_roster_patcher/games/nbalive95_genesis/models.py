"""Data models for the NBA Live 95 patcher.

30 teams (27 NBA + East All-Stars + West All-Stars + Slammers), 12 players each.
A player record is 93 bytes with a plain ASCII name.

  - https://github.com/Team-95/rom-edit
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

PLAYER_SIZE = 93
PLAYERS_PER_TEAM = 12
TEAM_COUNT = 30  # 27 NBA + East AS + West AS + Slammers
NBA_TEAM_COUNT = 27  # the All-Stars and Slammers are never patched

TEAM_METADATA_BASE = 0x00037ECE  # M_1 through M_29
TEAM_ROSTER_BASE = 0x0003FEB4  # T_1 through T_29

# Each team roster entry is 12 x 4-byte pointers, 0x00-0x2C
TEAM_POINTER_SIZE = 4
TEAM_POINTER_COUNT = 12

# Checksum bypass: replace JSR $001F9270 (6 bytes) at 0x690 with 3 NOPs.
# The original Team-95 offset 0x691 was misaligned for 68000 and created
# a RESET (0x4E70) instruction that crashed the CPU.
CHECKSUM_BYPASS_OFFSET = 0x00000690
CHECKSUM_BYPASS_BYTES = bytes([0x4E, 0x71, 0x4E, 0x71, 0x4E, 0x71])

JERSEY_DISPLAY_OFFSET = 0x00008E4C
JERSEY_DISPLAY_BYTES = bytes([0x42, 0x40, 0x4E, 0x71])

# Field offsets within the 93-byte player record
OFF_JERSEY = 0x00  # 1 byte
OFF_POSITION = 0x01  # 1 byte (0=C, 1=PF, 2=SF, 3=PG, 4=SG)
OFF_HEIGHT = 0x02  # 1 byte (value + 5 = inches)
OFF_WEIGHT = 0x03  # 1 byte (value + 100 = lbs)
OFF_EXPERIENCE = 0x04  # 1 byte (years)
OFF_UNIVERSITY = 0x05  # 1 byte (index)
OFF_SKIN = 0x06  # 1 byte (0x00-0x03)
OFF_HAIR = 0x07  # 1 byte (0x00-0x26)
OFF_STATS = 0x08  # 34 bytes (17 x 2-byte BE stats)
OFF_UNKNOWN2 = 0x2A  # 1 byte
OFF_RATINGS = 0x2B  # 16 bytes (16 x 1 byte ratings)
OFF_UNKNOWN3 = 0x3B  # 10 bytes
OFF_NAME = 0x45  # 24 bytes ("LASTNAME\0FIRST" ASCII)

NAME_LENGTH = 24

POSITION_C = 0
POSITION_PF = 1
POSITION_SF = 2
POSITION_PG = 3
POSITION_SG = 4

POSITION_TO_BYTE = {
    "C": POSITION_C,
    "PF": POSITION_PF,
    "SF": POSITION_SF,
    "PG": POSITION_PG,
    "SG": POSITION_SG,
}
BYTE_TO_POSITION = {v: k for k, v in POSITION_TO_BYTE.items()}

# Rating indices within the 16-byte ratings block
RATING_NAMES = [
    "goals",  # 0  - FG shooting
    "three_pt",  # 1  - 3-point shooting
    "ft",  # 2  - Free throw
    "dunking",  # 3  - Dunking ability
    "stealing",  # 4  - Steal ability
    "blocks",  # 5  - Shot blocking
    "off_reb",  # 6  - Offensive rebounding
    "def_reb",  # 7  - Defensive rebounding
    "passing",  # 8  - Passing/assists
    "off_awareness",  # 9  - Offensive awareness
    "def_awareness",  # 10 - Defensive awareness
    "speed",  # 11 - Speed
    "quickness",  # 12 - Quickness
    "jumping",  # 13 - Jumping/vertical
    "dribbling",  # 14 - Ball handling
    "strength",  # 15 - Physical strength
]
RATING_COUNT = 16

# Season stat indices (17 x 2-byte BE fields at OFF_STATS)
STAT_GAMES = 0
STAT_MIN = 1
STAT_FGM = 2
STAT_FGA = 3
STAT_3PM = 4
STAT_3PA = 5
STAT_FTM = 6
STAT_FTA = 7
STAT_OREB = 8
STAT_REB = 9
STAT_AST = 10
STAT_STL = 11
STAT_TO = 12
STAT_BLK = 13
STAT_PTS = 14
STAT_FOULEDOUT = 15
STAT_FOULS = 16
STAT_COUNT = 17

# Hardcoded team roster addresses (from Team-95/rom-edit ConstantsTeam.h)
# These are NOT evenly spaced — there's a gap between team 17 and 18.
TEAM_ROSTER_ADDRESSES = [
    0x0003FEB4,  # 0  Atlanta
    0x0004031A,  # 1  Boston
    0x00040788,  # 2  Charlotte
    0x00040C1A,  # 3  Chicago
    0x00041084,  # 4  Cleveland
    0x000414FE,  # 5  Dallas
    0x00041976,  # 6  Denver
    0x00041E12,  # 7  Detroit
    0x00042282,  # 8  Golden State
    0x00042712,  # 9  Houston
    0x00042B80,  # 10 Indiana
    0x00043004,  # 11 LA Clippers
    0x0004349A,  # 12 LA Lakers
    0x0004390E,  # 13 Miami
    0x00043D76,  # 14 Milwaukee
    0x000441D4,  # 15 Minnesota
    0x00044658,  # 16 New Jersey
    0x00044AF4,  # 17 New York
    0x001F4EF4,  # 18 Orlando
    0x001F5384,  # 19 Philadelphia
    0x001F5810,  # 20 Phoenix
    0x001F5C84,  # 21 Portland
    0x001F612A,  # 22 Sacramento
    0x001F65A6,  # 23 San Antonio
    0x001F6A2C,  # 24 Seattle
    0x001F6EA8,  # 25 Utah
    0x001F7328,  # 26 Washington
    0x001F77A4,  # 27 East All-Stars
    0x001F7C2A,  # 28 West All-Stars
    0x001F80AC,  # 29 Slammers
]

# Team order in ROM (27 NBA + 3 special)
NBALIVE95_TEAM_ORDER = [
    "Atlanta Hawks",  # 0
    "Boston Celtics",  # 1
    "Charlotte Hornets",  # 2
    "Chicago Bulls",  # 3
    "Cleveland Cavaliers",  # 4
    "Dallas Mavericks",  # 5
    "Denver Nuggets",  # 6
    "Detroit Pistons",  # 7
    "Golden State Warriors",  # 8
    "Houston Rockets",  # 9
    "Indiana Pacers",  # 10
    "LA Clippers",  # 11
    "LA Lakers",  # 12
    "Miami Heat",  # 13
    "Milwaukee Bucks",  # 14
    "Minnesota Timberwolves",  # 15
    "New Jersey Nets",  # 16
    "New York Knicks",  # 17
    "Orlando Magic",  # 18
    "Philadelphia 76ers",  # 19
    "Phoenix Suns",  # 20
    "Portland Trail Blazers",  # 21
    "Sacramento Kings",  # 22
    "San Antonio Spurs",  # 23
    "Seattle SuperSonics",  # 24
    "Utah Jazz",  # 25
    "Washington Bullets",  # 26
    "East All-Stars",  # 27
    "West All-Stars",  # 28
    "Slammers",  # 29
]

# Modern NBA team abbreviation -> ROM slot index. The 30 current teams onto 27
# slots; Toronto, Memphis and New Orleans have none.
#
# GS/GSW, BKN/NJN, NYK/NY, SA/SAS, OKC/SEA, UTA/UTAH and WAS/WSH each name one
# slot twice, so a provider returning both spellings hands `map_rosters` two teams
# for one slot, which is why that method guards before it assigns.
MODERN_NBA_TO_NBALIVE95 = {
    "ATL": 0,
    "BOS": 1,
    "CHA": 2,
    "CHI": 3,
    "CLE": 4,
    "DAL": 5,
    "DEN": 6,
    "DET": 7,
    "GS": 8,
    "GSW": 8,
    "HOU": 9,
    "IND": 10,
    "LAC": 11,
    "LAL": 12,
    "MIA": 13,
    "MIL": 14,
    "MIN": 15,
    "BKN": 16,  # was New Jersey
    "NJN": 16,
    "NYK": 17,
    "NY": 17,
    "ORL": 18,
    "PHI": 19,
    "PHX": 20,
    "POR": 21,
    "SAC": 22,
    "SA": 23,
    "SAS": 23,
    "OKC": 24,  # was Seattle
    "SEA": 24,
    "UTA": 25,
    "UTAH": 25,
    "WAS": 26,  # was the Bullets
    "WSH": 26,
}

# Expansion teams with no ROM slot
NO_SLOT_TEAMS = {"TOR", "MEM", "NOP", "NO"}

# Field offsets within an 80-byte team metadata entry
TEAM_META_SIZE = 0x50
META_OFF_INITIALS = 0x30  # initials string
META_OFF_COURT_NAME = 0x34  # court name string
META_OFF_LOCATION = 0x38  # location string
META_OFF_TEAM_NAME = 0x3C  # team name string
META_OFF_SCORING = 0x45
META_OFF_REBOUNDS = 0x46
META_OFF_BALL_CONTROL = 0x47
META_OFF_DEFENSE = 0x48
META_OFF_OVERALL = 0x49
META_OFF_BG_COLOR = 0x4B
META_OFF_BANNER_COLOR = 0x4C
META_OFF_TEXT_COLOR = 0x4D


@dataclass
class NBALive95PlayerRecord:
    """A 93-byte player record.

    `skin_color` and `hair_style` have no producer, so every mapped record carries
    0 -- tone 0 of 4 and style 0 of 39, not a "not supplied" code -- and the writer
    writes both bytes anyway. Upstream's behaviour, known wrong, preserved for byte
    fidelity. `season_stats` is 17 zeros and those are written too.
    """

    name_last: str = "PLAYER"
    name_first: str = "A"  # full first name or initial
    jersey: int = 0
    position: int = POSITION_SF  # 0=C, 1=PF, 2=SF, 3=PG, 4=SG
    height_inches: int = 78  # 78 = 6'6"
    weight_lbs: int = 220
    experience: int = 0  # years in the NBA
    skin_color: int = 0  # 0x00-0x03
    hair_style: int = 0  # 0x00-0x26

    # 0-99 scale
    ratings: list[int] = field(default_factory=lambda: [50] * RATING_COUNT)

    # 17 x 2-byte values, zeroed for new rosters
    season_stats: list[int] = field(default_factory=lambda: [0] * STAT_COUNT)


@dataclass
class NBALive95TeamRecord:
    index: int
    name: str
    players: list[NBALive95PlayerRecord] = field(default_factory=list)


@dataclass
class NBALive95TeamSlot:
    index: int
    name: str  # from NBALIVE95_TEAM_ORDER
    first_player: str


@dataclass
class NBALive95RomInfo:
    path: str
    size: int
    team_slots: list[NBALive95TeamSlot] = field(default_factory=list)
    is_valid: bool = False


@dataclass
class NBALive95SlotMapping:
    team: Team
    slot_index: int
    slot_name: str
