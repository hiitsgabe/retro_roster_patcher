"""Data models for the KGJ MLB patcher.

Ken Griffey Jr. Presents Major League Baseball (SNES, 1994).
28 MLB teams (14 AL, 14 NL), 25 players per team (15 batters + 10 pitchers).
Player records are 32 bytes each with custom character encoding.

References:
  - https://github.com/johnz1/ken_griffey_jr_presents_major_league_baseball_tools
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

# Custom character encoding used for player names.
#
# Exactly one lowercase letter is mapped: `c`, at 0x36, so that
# `KGJStatMapper._split_name` can render "McGWIRE" with a small c. The real
# cartridge font certainly holds the rest of the lowercase alphabet -- 1994
# rosters include DeShields and DeLucia -- and this table simply does not
# enumerate them. Two consequences, both live:
#
#  - `rom_writer._encode_char` maps anything absent here to 0x00, a SPACE. So
#    every accent in a modern MLB roster (Acuna, Guerrero, Baez) is written as
#    a blank rather than as a letter, and the name silently loses characters.
#  - `rom_reader._decode_name` renders anything absent here as "?", so a
#    genuine dump's DeShields reads back as "De?SHIELD"-shaped noise. That is
#    why this port's ROM signature check does NOT test name bytes against this
#    table: it would reject real images. See `patcher._team_data_fits`.
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

# Position encoding (values step by 2)
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

# Batting handedness encoding
HAND_RIGHT = 0x00
HAND_LEFT = 0x11
HAND_SWITCH = 0x20

# ROM layout constants
PLAYER_LENGTH = 0x20  # 32 bytes per player
TEAM_LENGTH = 0x320  # 800 bytes per team (25 * 32)
AL_TO_NL_GAP = 0xB40  # 2880 bytes between last AL and first NL team
PLAYERS_PER_TEAM = 25  # 15 batters + 5 starters + 5 relievers
BATTERS_PER_TEAM = 15
STARTERS_PER_TEAM = 5
RELIEVERS_PER_TEAM = 5

# Roster-type nibble written into the high half of record byte 0x19. The reader
# decodes the same byte back: 3 = batter, 1 = starting pitcher, 0 = relief
# pitcher. `KGJRomWriter.write_team_roster` chooses between them purely from the
# slot index, and these three constants are what `patcher.map_rosters` uses to
# stamp each record instead -- see `rom_writer.write_team_roster` for why the
# stamping moved.
ROSTER_TYPE_BATTER = 0x30
ROSTER_TYPE_STARTER = 0x10
ROSTER_TYPE_RELIEVER = 0x00

# Marker to find first team data (14 bytes before team 0)
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

# Modern MLB team abbreviation -> KGJ ROM slot index.
# Maps current 30 teams to the 28 ROM slots.
# Arizona (ARI) and Tampa Bay (TB) didn't exist in 1994 — no ROM slot.
# Montreal Expos became Washington Nationals, California Angels became
# Los Angeles Angels, Florida Marlins became Miami Marlins.
#
# Two slots are named twice: CWS/CHW both mean slot 3 and OAK/ATH both mean
# slot 10. `patcher.map_rosters` guards the collision; see the comment there.
MODERN_MLB_TO_KGJ = {
    "BAL": 0,  # Baltimore Orioles
    "BOS": 1,  # Boston Red Sox
    "LAA": 2,  # Los Angeles Angels (was California Angels)
    "CWS": 3,  # Chicago White Sox
    "CHW": 3,  # ESPN alternate
    "CLE": 4,  # Cleveland Guardians (was Indians)
    "DET": 5,  # Detroit Tigers
    "KC": 6,  # Kansas City Royals
    "MIL": 7,  # Milwaukee Brewers
    "MIN": 8,  # Minnesota Twins
    "NYY": 9,  # New York Yankees
    "OAK": 10,  # Oakland Athletics
    "ATH": 10,  # ESPN abbreviation (Athletics)
    "SEA": 11,  # Seattle Mariners
    "TEX": 12,  # Texas Rangers
    "TOR": 13,  # Toronto Blue Jays
    "ATL": 14,  # Atlanta Braves
    "CHC": 15,  # Chicago Cubs
    "CIN": 16,  # Cincinnati Reds
    "HOU": 17,  # Houston Astros
    "LAD": 18,  # Los Angeles Dodgers
    "WSH": 19,  # Washington Nationals (was Montreal Expos)
    "NYM": 20,  # New York Mets
    "PIT": 21,  # Pittsburgh Pirates
    "STL": 22,  # St. Louis Cardinals
    "SD": 23,  # San Diego Padres
    "SF": 24,  # San Francisco Giants
    "PHI": 25,  # Philadelphia Phillies
    "COL": 26,  # Colorado Rockies
    "MIA": 27,  # Miami Marlins (was Florida Marlins)
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
    """Complete player record ready to write to ROM (32 bytes)."""

    first_initial: str = "A"
    last_name: str = "PLAYER"
    position: str = "CF"
    jersey_number: int = 1
    is_pitcher: bool = False
    bat_hand: int = HAND_RIGHT  # Batting handedness

    # Batter fields
    batter_attrs: KGJBatterAttributes = field(default_factory=KGJBatterAttributes)
    batter_appearance: KGJBatterAppearance = field(default_factory=KGJBatterAppearance)
    batting_avg: int = 250  # e.g. 250 = .250
    home_runs: int = 0
    rbi: int = 0

    # Pitcher fields
    pitcher_attrs: KGJPitcherAttributes = field(default_factory=KGJPitcherAttributes)
    pitcher_appearance: KGJPitcherAppearance = field(default_factory=KGJPitcherAppearance)
    pitch_hand: int = 0  # 0=Right, 1=Left
    wins: int = 0
    losses: int = 0
    era: int = 400  # e.g. 400 = 4.00 ERA
    saves: int = 0

    # Roster type. 0x30=batter, 0x10=starter, 0x00=reliever.
    #
    # DELIBERATE DIVERGENCE from upstream in who sets it. Upstream's
    # `KGJRomWriter.write_team_roster` assigned this field on the caller's own
    # objects from the slot index, mutating records the caller still held.
    # `patcher.map_rosters` stamps it instead, so the record is complete the
    # moment it is built and the writer only reads it.
    roster_type: int = ROSTER_TYPE_BATTER


@dataclass
class KGJTeamRecord:
    """Complete team record."""

    index: int
    name: str
    players: list[KGJPlayerRecord] = field(default_factory=list)


@dataclass
class KGJTeamSlot:
    """An existing team slot read from the ROM."""

    index: int
    name: str  # From KGJ_TEAM_ORDER
    first_player: str  # First player name for verification


@dataclass
class KGJRomInfo:
    """Information about a loaded KGJ ROM."""

    path: str
    size: int
    first_team_offset: int = 0
    team_slots: list[KGJTeamSlot] = field(default_factory=list)
    is_valid: bool = False
    has_header: bool = False
