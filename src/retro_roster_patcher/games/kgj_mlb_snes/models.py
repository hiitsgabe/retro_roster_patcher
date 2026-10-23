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
# The name font holds uppercase letters, digits, `-` (0x25), `.` (0x26) and one
# lowercase letter, the `c` of Mc (0x36), so that `_split_name` can render
# "McGWIRE". There is no other lowercase letter and no apostrophe: 0x27-0x34 are
# position and ordinal labels, 0x35 is another `c`, 0x37-0x3F are "1." to "9.".
# The writer maps anything absent to 0x00, a SPACE, which is why `_split_name`
# strips accents and apostrophes first; the reader renders anything absent as
# "?", which is why the ROM signature check does not test name bytes against
# this table.
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
    "-": 0x25,
    ".": 0x26,
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

PLAYER_LENGTH = 0x20  # 32 bytes per player
TEAM_LENGTH = 0x320  # 800 bytes per team (25 * 32)
AL_TO_NL_GAP = 0xB40  # 2880 bytes between last AL and first NL team
PLAYERS_PER_TEAM = 25  # 15 batters + 5 starters + 5 relievers
BATTERS_PER_TEAM = 15
STARTERS_PER_TEAM = 5
RELIEVERS_PER_TEAM = 5

# The positions the starting batters field, in the order the default selection
# fills them. An NL club fields all but the DH. The 1994 cartridge's own
# lineups hold exactly this set in slots 0-8 (AL) or 0-7 (NL).
LINEUP_POSITIONS: tuple[str, ...] = ("C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH")

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

# The Home Run Derby's own player records follow these 10 bytes (file 0xE451 on
# the retail cartridge): 3 NL batters, 3 AL batters, then the NL and AL derby
# pitcher, each a 32-byte record laid out like a roster's.
HR_DERBY_MARKER = bytes([0x02, 0x2E, 0x37, 0x27, 0x00, 0x0A, 0x23, 0x3B, 0x35, 0xFF])
HR_DERBY_BATTERS_PER_LEAGUE = 3

# The derby's Select Player screen script holds each league's three names as
# text after these 9 bytes (NL list first): per name a cursor code D1 x y and
# the name, up to a D2. `FC lo hi` in a name is one special glyph (an
# apostrophe, "R."). File 0x35E79 and 0x35EC9 on the retail cartridge.
HR_DERBY_LIST = bytes([0xD1, 0x0D, 0x11, 0xF9, 0x0F, 0x08, 0xD2, 0x00, 0x14])

# A second copy of the six names, 14 columns each and centred, follows the
# string " HOMERUN DERBY " in a table of D0-terminated strings found by pointer:
# NL 1-3, one string that is not a name, AL 1-3. File 0x3744C on retail.
HR_DERBY_TITLE = bytes(CHAR_TO_BYTE[ch] for ch in " HOMERUN DERBY ") + b"\xd0"

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

# Modern MLB team abbreviation -> ROM slot index. The 28 clubs of 1994 hold
# slots 0-27; Arizona and Tampa Bay, who did not exist in 1994, take the two
# All-Star teams, slots 28 (NL) and 29 (AL), and play only the All-Star Game
# (`expansion`).
#
# Houston and Milwaukee trade slots, so each plays in its league of today: the
# slot's league decides the DH, the team-select page and the standings (the
# patch writes the team's own name, logo and colours over the slot's).
#
# CWS/CHW both mean slot 3, OAK/ATH slot 10 and ARI/AZ slot 28, so `map_rosters`
# guards the collision.
MODERN_MLB_TO_KGJ = {
    "BAL": 0,
    "BOS": 1,
    "LAA": 2,  # was California
    "CWS": 3,
    "CHW": 3,
    "CLE": 4,  # was the Indians
    "DET": 5,
    "KC": 6,
    "HOU": 7,  # was Milwaukee
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
    "MIL": 17,  # was Houston
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
    "ARI": 28,  # the NL All-Stars
    "AZ": 28,
    "TB": 29,  # the AL All-Stars
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
    """How a player looks at the plate and on his Line Up card (record bytes
    0x0E-0x14; pitchers bat too). The game builds him from parts these pick
    (loader $85:D037), and a value past each range glitches, resets or hangs
    the game, which is why `looks.write` clamps every one of them.
    """

    # 0x0E hi. Light to dark; also colours him as a fielder ($85:CE32).
    skin: int = 0  # 0-5
    # 0x0E lo. 0 short hair, 1 + mustache, 3 + goatee, 4 longer hair,
    # 5 + mustache, 6 + beard, 7 long hair. 2 draws what 6 does; on the small
    # build 2 and 3 draw 6 and 7 draws 4 ($85:D22F). A #24 on Seattle or the AL
    # All-Stars gets Griffey's own head ($85:D1B4) unless `looks.free_number_24`.
    head: int = 0  # 0-7
    # 0x0F hi. Hair and facial hair: 0 blonde, 1 red, 2 brown, 3 dark brown, 4 black.
    hair_color: int = 4  # 0-4
    # 0x0F lo. 0 average, 1 tall torso, 2 big, 3 small (which forces legs size 1).
    body: int = 1  # 0-3
    legs_size: int = 0  # 0x10 hi. 0 normal, 1 short; ignored on build 3
    # 0x10 lo. 0 slightly bent, 1 straight and wide, 2 knees together,
    # 3 deep crouch, 4 front leg straight.
    legs_stance: int = 0  # 0-4
    # 0x11 lo (its high nibble is never read). 0 bat upright, 1 hands low and
    # back, 2 bat over the shoulder.
    arms_stance: int = 0  # 0-2
    # 0x12 hi. Which of the arms stance's eight waggles; 0 stops the waggle.
    waggle: int = 1  # 1-7
    # 0x12 lo, 0x13 hi. Sprite pieces of the at-bat animation, which the game
    # remaps by build; 0x12 lo is only tested for zero.
    piece: int = 0  # 0-1
    piece_set: int = 1  # 0-2
    sequence: int = 0  # 0x13 lo. The at-bat sequence, 0-4
    bat_color: int = 0  # 0x14 hi (its low nibble is never read). 0 ash, 1 brown, 2 black


@dataclass
class KGJPitcherAppearance:
    """How a pitcher looks on the mound and on his Bullpen card (record bytes
    0x15-0x17, loader $85:D428)."""

    # 0x15 lo. The batter's tones, each lighter on the mound, where 2 and 3 are
    # equally light (3 is greyer).
    skin: int = 0  # 0-5
    head: int = 0  # 0x16 hi. 0 plain, 1 beard, 2 long hair, 3 mustache, 4 glasses
    hair_color: int = 4  # 0x16 lo. The batter's colours, 0 blonde to 4 black
    body: int = 0  # 0x17 hi. 0 average, 1 heavy, 2 tall
    # 0x17 lo ($1335). 0 overhand, anything else sidearm; it changes the pitch's timing.
    throwing_style: int = 0  # 0-1


@dataclass
class KGJPlayerRecord:
    """A 32-byte player record. Every byte the game reads goes through one
    accessor (85:D738), which is how the layout below was decoded:

      0x00      first initial; 0x01-0x08 surname
      0x09      position (`POSITION_TO_BYTE`). A starter's is where he fields;
                on the bench the game ignores it and a sub takes the slot's
      0x0A      jersey number
      0x0B      batter: BAT | POW; pitcher: SPD | CON (each rating - 1)
      0x0C      batter: SPD | DEF; pitcher: the whole byte is FAT - 1. He tires
                from (byte + 1) * 7 pitches and is worst from (byte + 1) * 9
      0x0D      bats (0 R, 1 L, 2 switch) | throws (0 R, 1 L). Pitchers too: they
                bat, and a fielder throws with the low nibble
      0x0E-0x14 batter looks (`KGJBatterAppearance`): skin | head, hair |
                build, legs size | stance, unused | arms, waggle | piece,
                piece set | sequence, bat colour | unused
      0x15-0x17 pitcher only: throws | skin, head | hair, build | delivery
                (`KGJPitcherAppearance`)
      0x18-0x1F four 12-bit stats, each a low byte then a nibble holding bits
                8-11: batter AVG, HR, RBI and 0; pitcher W, L, ERA, saves. The
                high nibble of 0x19, 0x1B, 0x1D and 0x1F is the stat's digit
                count minus one, as the cartridge has it (AVG always 3, a
                batter's saves 0xF). The game never reads those four nibbles.

    Nothing in the record says pitcher or batter, starter or reliever: the game
    goes by the slot (15 and up pitch, 20 and up relieve), and a pitcher bats
    with fixed ratings whatever his record holds.
    """

    first_initial: str = "A"
    last_name: str = "PLAYER"
    # The provider's: `looks` picks his parts from the id, and his skin from the
    # head shot at `photo_url` when there is one.
    player_id: int = 0
    photo_url: str = ""
    position: str = "CF"
    jersey_number: int = 1
    is_pitcher: bool = False
    bats: int = 0  # 0 = right, 1 = left, 2 = switch
    throws: int = 0  # 0 = right, 1 = left

    batter_attrs: KGJBatterAttributes = field(default_factory=KGJBatterAttributes)
    batter_appearance: KGJBatterAppearance = field(default_factory=KGJBatterAppearance)
    batting_avg: int = 250  # e.g. 250 = .250
    home_runs: int = 0
    rbi: int = 0

    pitcher_attrs: KGJPitcherAttributes = field(default_factory=KGJPitcherAttributes)
    pitcher_appearance: KGJPitcherAppearance = field(default_factory=KGJPitcherAppearance)
    wins: int = 0
    losses: int = 0
    era: int = 400  # e.g. 400 = 4.00 ERA
    saves: int = 0


@dataclass
class KGJTeamRecord:
    index: int
    name: str
    # In slot order; `None` leaves that slot's ROM record as it is.
    players: list[KGJPlayerRecord | None] = field(default_factory=list)
    # The club's identity, which `identity.write` puts on the slot: empty keeps
    # the cartridge's. `city` and `nickname` as the game prints them (capitals,
    # no punctuation), `code` the provider's abbreviation, `colors` its primary
    # and alternate colour, and the logo from `assets_dir/logos/<team_id>.png`,
    # else `logo_url`.
    city: str = ""
    nickname: str = ""
    code: str = ""
    colors: tuple[tuple[int, int, int], ...] = ()
    team_id: int | None = None
    logo_url: str = ""


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
