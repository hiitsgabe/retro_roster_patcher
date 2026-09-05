"""Data models for the NBA Live 95 patcher.

NBA Live 95 (Sega Genesis, 1994).
30 teams (27 NBA + East All-Stars + West All-Stars + Slammers), 12 players per team.
Player records are 93 bytes each with plain ASCII names.

References:
  - https://github.com/Team-95/rom-edit
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

# ROM layout constants
PLAYER_SIZE = 93  # 93 bytes per player
PLAYERS_PER_TEAM = 12
TEAM_COUNT = 30  # 27 NBA + East AS + West AS + Slammers
NBA_TEAM_COUNT = 27  # Only patch NBA teams (not All-Stars/Slammers)

# Base offsets
TEAM_METADATA_BASE = 0x00037ECE  # M_1 through M_29
TEAM_ROSTER_BASE = 0x0003FEB4  # T_1 through T_29

# Each team roster entry has 12 x 4-byte pointers (0x00-0x2C)
TEAM_POINTER_SIZE = 4
TEAM_POINTER_COUNT = 12  # 12 player slots per team

# Checksum bypass: replace JSR $001F9270 (6 bytes) at 0x690 with 3 NOPs.
# The original Team-95 offset 0x691 was misaligned for 68000 and created
# a RESET (0x4E70) instruction that crashed the CPU.
CHECKSUM_BYPASS_OFFSET = 0x00000690
CHECKSUM_BYPASS_BYTES = bytes([0x4E, 0x71, 0x4E, 0x71, 0x4E, 0x71])

JERSEY_DISPLAY_OFFSET = 0x00008E4C
JERSEY_DISPLAY_BYTES = bytes([0x42, 0x40, 0x4E, 0x71])

# Player record field offsets (within 93-byte record)
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

# Position encoding
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

# Modern NBA team abbreviation -> NBA Live 95 ROM slot index.
# Maps current 30 NBA teams to the 27 ROM slots.
# Toronto Raptors, Memphis Grizzlies, New Orleans Pelicans have no ROM slot.
# Franchise moves since 1994:
#   Seattle SuperSonics -> OKC Thunder -> OKC maps to Seattle slot (24)
#   New Jersey Nets -> Brooklyn Nets -> BKN maps to New Jersey slot (16)
#   Washington Bullets -> Washington Wizards -> WAS maps to Washington slot (26)
#   Charlotte Hornets (original) -> current Charlotte Hornets -> CHA maps to slot 2
#
# Ten of these codes are aliases of another: GS/GSW, BKN/NJN, NYK/NY, SA/SAS,
# OKC/SEA, UTA/UTAH and WAS/WSH each name one slot twice. A provider that
# returns both spellings hands `map_rosters` two teams for one slot, which is
# why that method guards before it assigns.
MODERN_NBA_TO_NBALIVE95 = {
    "ATL": 0,  # Atlanta Hawks
    "BOS": 1,  # Boston Celtics
    "CHA": 2,  # Charlotte Hornets
    "CHI": 3,  # Chicago Bulls
    "CLE": 4,  # Cleveland Cavaliers
    "DAL": 5,  # Dallas Mavericks
    "DEN": 6,  # Denver Nuggets
    "DET": 7,  # Detroit Pistons
    "GS": 8,  # Golden State Warriors
    "GSW": 8,  # ESPN alternate
    "HOU": 9,  # Houston Rockets
    "IND": 10,  # Indiana Pacers
    "LAC": 11,  # LA Clippers
    "LAL": 12,  # LA Lakers
    "MIA": 13,  # Miami Heat
    "MIL": 14,  # Milwaukee Bucks
    "MIN": 15,  # Minnesota Timberwolves
    "BKN": 16,  # Brooklyn Nets (was New Jersey Nets)
    "NJN": 16,  # Legacy abbreviation
    "NYK": 17,  # New York Knicks
    "NY": 17,  # ESPN alternate
    "ORL": 18,  # Orlando Magic
    "PHI": 19,  # Philadelphia 76ers
    "PHX": 20,  # Phoenix Suns
    "POR": 21,  # Portland Trail Blazers
    "SAC": 22,  # Sacramento Kings
    "SA": 23,  # San Antonio Spurs
    "SAS": 23,  # ESPN alternate
    "OKC": 24,  # OKC Thunder (was Seattle SuperSonics)
    "SEA": 24,  # Legacy abbreviation
    "UTA": 25,  # Utah Jazz
    "UTAH": 25,  # ESPN alternate
    "WAS": 26,  # Washington Wizards (was Bullets)
    "WSH": 26,  # ESPN alternate
}

# Teams with no ROM slot (expansion after 1994)
NO_SLOT_TEAMS = {"TOR", "MEM", "NOP", "NO"}

# Team metadata offsets (within team metadata block)
# Each team metadata entry is ~80 bytes
TEAM_META_SIZE = 0x50  # 80 bytes per team metadata entry
META_OFF_INITIALS = 0x30  # Team initials string
META_OFF_COURT_NAME = 0x34  # Court name string
META_OFF_LOCATION = 0x38  # Location string
META_OFF_TEAM_NAME = 0x3C  # Team name string
META_OFF_SCORING = 0x45  # Team attribute: scoring
META_OFF_REBOUNDS = 0x46  # Team attribute: rebounds
META_OFF_BALL_CONTROL = 0x47  # Team attribute: ball control
META_OFF_DEFENSE = 0x48  # Team attribute: defense
META_OFF_OVERALL = 0x49  # Team attribute: overall
META_OFF_BG_COLOR = 0x4B  # Background color
META_OFF_BANNER_COLOR = 0x4C  # Banner color
META_OFF_TEXT_COLOR = 0x4D  # Text color


@dataclass
class NBALive95PlayerRecord:
    """Complete player record ready to write to ROM (93 bytes).

    `skin_color` and `hair_style` have no producer and are not expected to get
    one: `stat_mapper.map_player` builds this record without them and ESPN
    publishes no such attribute. 0 is therefore what every mapped record carries,
    and it means "not supplied" rather than "tone 0, style 0".
    `rom_writer.write_player` reads it that way and leaves the image's own byte
    alone, which is a DELIBERATE DIVERGENCE from upstream and from this port
    until now; the argument is at the line there. A direct caller of
    `write_player` may still set either field to a positive value and have it
    written.

    `season_stats` defaults to 17 zeros and those zeros *are* written, which is
    the opposite call on a field of the same shape. See
    `stat_mapper.NBALive95StatMapper.map_player` for why the two differ.
    """

    name_last: str = "PLAYER"
    name_first: str = "A"  # Full first name or initial
    jersey: int = 0
    position: int = POSITION_SF  # 0=C, 1=PF, 2=SF, 3=PG, 4=SG
    height_inches: int = 78  # Total height in inches (e.g. 78 = 6'6")
    weight_lbs: int = 220
    experience: int = 0  # Years in NBA
    skin_color: int = 0  # 0x00-0x03
    hair_style: int = 0  # 0x00-0x26

    # 16 ratings (0-99 scale)
    ratings: list[int] = field(default_factory=lambda: [50] * RATING_COUNT)

    # Season stats (17 x 2-byte values, zeroed for new rosters)
    season_stats: list[int] = field(default_factory=lambda: [0] * STAT_COUNT)


@dataclass
class NBALive95TeamRecord:
    """Complete team record for patching."""

    index: int
    name: str
    players: list[NBALive95PlayerRecord] = field(default_factory=list)


@dataclass
class NBALive95TeamSlot:
    """An existing team slot read from the ROM."""

    index: int
    name: str  # From NBALIVE95_TEAM_ORDER
    first_player: str  # First player name for verification


@dataclass
class NBALive95RomInfo:
    """Information about a loaded NBA Live 95 ROM."""

    path: str
    size: int
    team_slots: list[NBALive95TeamSlot] = field(default_factory=list)
    is_valid: bool = False


@dataclass
class NBALive95SlotMapping:
    """Maps a modern NBA team to an NBA Live 95 ROM slot."""

    team: Team
    slot_index: int
    slot_name: str
