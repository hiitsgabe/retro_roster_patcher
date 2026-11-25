"""Constants and record types for MVP Baseball (PSP, ULUS-10012).

This game's database is CSV text inside compressed sections. A record is a line
of ASCII:

    00b87d5f5,0 Vladimir,1 Guerrero,2 27,22 61,;\\r\\n

-- a nine-hex-digit id, then `fieldnum value` pairs separated by commas,
terminated by `,;` and a CRLF. Tables link to each other by those ids: a
`roster` row names a team id and a player id, and the player id is what the
`attrib`, `lrattrib_rhp`, `lrattrib_lhp` and `pitchattrib` tables key on. The
field-number constants below are column names, not addresses, so a wrong one
writes a real column of a real record with the wrong meaning rather than
crashing.

`database.big` is an EA compressed archive (`C0 FB`): a directory of 18
files, then each file as an independent RefPack stream at its offset, padded to
four bytes. The directory gives each file's offset and compressed length; the
space between one offset and the next is the file's whole allocation, so a
table that recompresses larger than that cannot be stored at all. `rom_writer`
raises rather than silently keeping the original; see `MVPPSPRomWriter`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# Where `database.big` lives: a hardcoded LBA, and that is the whole of this
# game's ISO 9660 interaction -- two `seek(DATABASE_BIG_LBA * ISO_SECTOR_SIZE)`
# calls, no primary volume descriptor, no directory traversal. Do not wire this
# package to `formats/iso9660.py`: it would refuse a valid image whose PVD it
# could not parse, over a file it never needed the PVD to find.
#
# The three numbers are carried over unverified; the arithmetic they imply is
# pinned in `patcher._database_big_extent_fits`.
DATABASE_BIG_LBA = 334832
DATABASE_BIG_SIZE = 386977
ISO_SECTOR_SIZE = 2048


def database_big_extent() -> tuple[int, int]:
    """(first byte, last byte + 1) of `database.big` within the image.

    A function and not two module constants: reading `DATABASE_BIG_LBA` at call
    time from this module's own global lets one `monkeypatch.setattr` shrink the
    reader, the writer and the patcher coherently. Three separately imported
    constants could be patched into disagreement and still pass.

    Unpatched, the extent is `[685735936, 686122913)`.
    """
    start = DATABASE_BIG_LBA * ISO_SECTOR_SIZE
    return start, start + DATABASE_BIG_SIZE


# `(offset within database.big, table name)`, ascending: the directory, then
# its 18 files in its order (`attrib.dat`, `lhattrib.dat`, ...). The `lh` and
# `rh` files are a player against left- and right-handers: the retail disc's
# Bobby Abreu, a left-handed batter, has 233 plate appearances in
# `lhbstats.dat` and 480 in `rhbstats.dat`, and its Edit Player screen shows
# Manny Ramirez's `lhattrib.dat` contact and power as his VS LHP.
SECTION_MAP: tuple[tuple[int, str], ...] = (
    (0, "directory"),
    (324, "attrib"),
    (61772, "lrattrib_lhp"),
    (101852, "lrattrib_rhp"),
    (144692, "batstat"),
    (165552, "fieldstat"),
    (188428, "lrbatstat_lhp"),
    (214440, "lrpitchstat_lhp"),
    (229676, "pitchstat"),
    (245436, "lrbatstat_rhp"),
    (274488, "lrpitchstat_rhp"),
    (290260, "pitchattrib"),
    (313720, "team"),
    (317176, "teamstat"),
    (317752, "roster"),
    (335616, "careerstats"),
    (366772, "pitchcareer"),
    (384620, "organization"),
    (385608, "manager"),
)

SECTION_COUNT = len(SECTION_MAP)

# The offset of the second section, which is where `validate` looks for a
# RefPack header. Derived rather than written as 324 twice.
ATTRIB_SECTION_OFFSET = SECTION_MAP[1][0]


def _section_allocations() -> dict[str, tuple[int, int]]:
    """`{name: (offset, allocation)}` derived from `SECTION_MAP` alone.

    A section's allocation is the distance to the next section's offset, and the
    last section's is the distance to the end of `database.big`. Derive it here;
    never re-derive it by index arithmetic at a call site.
    """
    result: dict[str, tuple[int, int]] = {}
    for i, (offset, name) in enumerate(SECTION_MAP):
        end = SECTION_MAP[i + 1][0] if i + 1 < len(SECTION_MAP) else DATABASE_BIG_SIZE
        result[name] = (offset, end - offset)
    return result


SECTION_ALLOCATIONS = _section_allocations()

# The tables this patcher rewrites. `roster` is rebuilt wholesale and the other
# four are merged into per record. Not a control-flow input: `rom_writer` writes
# whatever `update_*` marked modified.
PLAYER_TABLES: tuple[str, ...] = ("attrib", "lrattrib_rhp", "lrattrib_lhp", "pitchattrib")
ROSTER_TABLE = "roster"
MODIFIED_TABLES: tuple[str, ...] = (*PLAYER_TABLES, ROSTER_TABLE)

# The archive's directory: `C0 FB`, its length (2 bytes), the file count (2),
# then per file a 3-byte offset, a 3-byte compressed length and a NUL-ended
# name, all big-endian. Not a table: `rom_writer` rewrites the length of each
# file it rewrites.
DIRECTORY_TABLE = "directory"
DIRECTORY_MAGIC = b"\xc0\xfb"

# Column numbers within each table, from the section's own header line (its
# names in the comments where the constant shortens them). The ratings on 0-15
# show on the Edit Player screens as 10 per point to 5, then 5 per point (12 is
# 85); speed, contact, power, stamina and pitch control as stored.

ATTRIB_FIRST_NAME = 0
ATTRIB_LAST_NAME = 1
ATTRIB_JERSEY = 2
ATTRIB_BATS = 3  # 0=R, 1=L, 2=S
ATTRIB_THROWS = 4  # 0=R, 1=L
ATTRIB_PRIMARY_POS = 5
ATTRIB_SECONDARY_POS = 6  # a position code or `SECONDARY_*`; his own when he has none
ATTRIB_AUDIO_ID = 7  # names the player's speech and portrait (`portraits`)
ATTRIB_PHOTO = 8  # 1, or 2 on 26 rookies the disc has no photo of; not written
ATTRIB_HEIGHT = 9  # inches above `HEIGHT_BASE`
ATTRIB_WEIGHT = 10  # pounds above `WEIGHT_BASE`
ATTRIB_BONE_PROFILE = 11  # the 3D body's build, 26-37 (`spreads.Spreads.build`)
ATTRIB_FACE = 12  # 12-16: `looks`
ATTRIB_SKIN_TONE = 13
ATTRIB_HAIR_STYLE = 14
ATTRIB_HAIR_COLOUR = 15
ATTRIB_FACIAL_HAIR = 16
ATTRIB_BODY_TYPE = 17  # 1 Athletic, 2 Heavy
ATTRIB_PLATE_DISCIPLINE = 18
ATTRIB_BUNTING = 19
ATTRIB_STEALING_AGGRESSIVE = 20  # the screen's Stealing Tendency
ATTRIB_BASERUNNING = 21
ATTRIB_SPEED = 22
ATTRIB_FIELDING = 23
ATTRIB_RANGE = 24
ATTRIB_THROW_STRENGTH = 25
ATTRIB_THROW_ACCURACY = 26
ATTRIB_DURABILITY = 27
ATTRIB_BATTING_STANCE = 28  # 0-5 the shared stances ("Open"), the rest a star's own
ATTRIB_SWING_TYPE = 29  # 0 or 1; no screen names it
ATTRIB_BAT_COLOUR = 30  # 30-37 equipment, as the Equipment screen shows it; not written
ATTRIB_BATTING_GLOVE = 31
ATTRIB_WRISTBAND = 32
ATTRIB_ELBOW_GUARD = 33
ATTRIB_SHIN_GUARD = 34
ATTRIB_SOCKS = 35
ATTRIB_CATCHER_MASK = 36
ATTRIB_FIELD_GLOVE_COLOUR = 37
ATTRIB_DITTY = 38  # walk-up music, 0-6; not written
ATTRIB_SALARY = 39  # not written: ESPN's rosters carry a contract for 35 of 1146
ATTRIB_CONTRACT_LENGTH = 40  # not written
ATTRIB_STARPOWER = 41
ATTRIB_HIDDEN = 42  # 1 on the MVP Legends
ATTRIB_BIRTHDAY = 43  # days from `BIRTHDAY_EPOCH`

# The season the disc holds, whose calendar the game ages players by.
DISC_SEASON = 2005
HEIGHT_BASE = 48
WEIGHT_BASE = 100
BIRTHDAY_EPOCH = date(1948, 1, 1)

# `lrattrib_lhp` and `lrattrib_rhp`: the batter against left- and right-handed
# pitching. Columns 0-19 are the same in both; the approach columns after them
# are not (the LHP table has no `lrattrib_chasehardbreak`), so those are named
# by the header, not numbered.
LR_FIRST_NAME = 0
LR_LAST_NAME = 1
LR_CONTACT = 2
LR_POWER = 3
# The 3x3 strike zone, top-left to bottom-right (`lrattrib_hit_ul` ...
# `lrattrib_hit_lr`): 0 cold (blue on the Edit Player list), 1 neither, 2 hot (red).
LR_ZONES = tuple(range(4, 13))
ZONE_COLD = 0
ZONE_HOT = 2
# Where his fly balls go, percent: a right-handed swing pulls them to left.
LR_DIRECTION = (13, 14, 15)  # lf, cf, rf
# His balls in play, percent, summing to 100.
LR_HR_PCT = 16
LR_FB_PCT = 17
LR_LD_PCT = 18
LR_GB_PCT = 19
# How often he chases, takes and misses a fastball, a slow and a hard breaking
# ball, 0-15.
LR_CHASE = ("lrattrib_chasefb", "lrattrib_chaseslowbreak", "lrattrib_chasehardbreak")
LR_TAKE = ("lrattrib_takefb", "lrattrib_takeslowbreak", "lrattrib_takehardbreak")
LR_MISS = ("lrattrib_missfb", "lrattrib_missslowbreak", "lrattrib_misshardbreak")

# `pitchattrib`. Pitch 1 is asymmetric with pitches 2-5: it is always a fastball,
# so it has no type column and occupies four columns where every later pitch
# occupies five. `PA_PITCH2_TYPE` is therefore the base of the repeating block
# and pitch 1 is addressed by its own three constants.
PA_FIRST_NAME = 0
PA_LAST_NAME = 1
PA_STAMINA = 2  # 40-99
PA_PICKOFF = 3  # 0-15
PA_PITCH1_MOVEMENT = 4  # 0 on every retail pitcher
PA_PITCH1_DESC = 5  # 0 on every retail pitcher
PA_PITCH1_CONTROL = 6
PA_PITCH1_VELOCITY = 7  # miles an hour
PA_PITCH2_TYPE = 8
PA_PITCH2_MOVEMENT = 9
PA_PITCH2_DESC = 10
PA_PITCH2_CONTROL = 11
PA_PITCH2_VELOCITY = 12
PA_PITCHER_DELIVERY = 28  # 0-8 shared windups, the rest a pitcher's own ("Schilling")

# Columns per pitch in the repeating block, and the offsets within one. A slot
# without a pitch holds `NO_PITCH` in all five.
PA_PITCH_STRIDE = 5
PA_PITCH_TYPE_OFFSET = 0
PA_PITCH_MOVEMENT_OFFSET = 1  # 0-15
PA_PITCH_DESC_OFFSET = 2  # 0-7 by pitch type; no screen shows it
PA_PITCH_CONTROL_OFFSET = 3
PA_PITCH_VELOCITY_OFFSET = 4  # miles an hour
PA_PITCH_SLOTS = 4  # pitches 2-5
NO_PITCH = "-"

# `roster`. Four (position, batting order) pairs, because the game stores a
# separate lineup for each combination of opposing-pitcher handedness and
# league DH rule.
ROSTER_TEAMID = 0
ROSTER_PLAYERID = 1
ROSTER_RH_AL_POS = 2
ROSTER_RH_AL_ORDER = 3
ROSTER_RH_NL_POS = 4
ROSTER_RH_NL_ORDER = 5
ROSTER_LH_AL_POS = 6
ROSTER_LH_AL_ORDER = 7
ROSTER_LH_NL_POS = 8
ROSTER_LH_NL_ORDER = 9

# `team`. Not written by this patcher; kept as format documentation.
TEAM_NAME = 0
TEAM_LEAGUE = 1
TEAM_DIVISION = 2
TEAM_ARTID = 3

ATTRIB_POS_PITCHER = 0  # a starter
ATTRIB_POS_C = 1
ATTRIB_POS_1B = 2
ATTRIB_POS_2B = 3
ATTRIB_POS_3B = 4
ATTRIB_POS_SS = 5
ATTRIB_POS_LF = 6
ATTRIB_POS_CF = 7
ATTRIB_POS_RF = 8
ATTRIB_POS_RELIEVER = 10  # RP / CP / MR / SU / LR all collapse here

# Position string -> `attrib` column 5. The game has no designated-hitter code,
# so `DH` maps to 2, first base, and is offered at first base by the game's own
# lineup screen; `OF` maps to 7, centre field.
POS_STRING_TO_NUM: dict[str, int] = {
    "P": ATTRIB_POS_PITCHER,
    "SP": ATTRIB_POS_PITCHER,
    "SP1": ATTRIB_POS_PITCHER,
    "SP2": ATTRIB_POS_PITCHER,
    "SP3": ATTRIB_POS_PITCHER,
    "SP4": ATTRIB_POS_PITCHER,
    "SP5": ATTRIB_POS_PITCHER,
    "C": ATTRIB_POS_C,
    "1B": ATTRIB_POS_1B,
    "2B": ATTRIB_POS_2B,
    "3B": ATTRIB_POS_3B,
    "SS": ATTRIB_POS_SS,
    "LF": ATTRIB_POS_LF,
    "CF": ATTRIB_POS_CF,
    "RF": ATTRIB_POS_RF,
    "OF": ATTRIB_POS_CF,
    "DH": ATTRIB_POS_1B,
    "RP": ATTRIB_POS_RELIEVER,
    "CP": ATTRIB_POS_RELIEVER,
    "CL": ATTRIB_POS_RELIEVER,
    "MR": ATTRIB_POS_RELIEVER,
    "SU": ATTRIB_POS_RELIEVER,
    "LR": ATTRIB_POS_RELIEVER,
}

# What `patcher._attrib_fields` writes for a position string the table above does
# not name. 7 is centre field.
DEFAULT_POS_NUM = ATTRIB_POS_CF

BATTERS_PER_TEAM = 15
STARTERS_PER_TEAM = 5
RELIEVERS_PER_TEAM = 5
PITCHERS_PER_TEAM = STARTERS_PER_TEAM + RELIEVERS_PER_TEAM
PLAYERS_PER_TEAM = BATTERS_PER_TEAM + PITCHERS_PER_TEAM
TEAM_COUNT = 30

# The nine batting-order positions, in the order `select_roster` fills them and
# `_slot_to_position` reads them back. Index in this tuple is the roster slot,
# and slot + 1 is the batting order the game stores.
LINEUP_POSITIONS: tuple[str, ...] = ("C", "1B", "2B", "SS", "3B", "LF", "CF", "RF", "DH")

# `_select_position_players` fills positions in a different order from the one
# the lineup is written in: `LINEUP_POSITIONS` puts SS third and 3B fifth, and
# selection asks for 3B before SS, so a player who qualifies at both is taken as
# a third baseman and then batted third, in the slot labelled SS. Preserved
# deliberately; do not collapse the two tuples into one.
SELECTION_POSITIONS: tuple[str, ...] = ("C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH")

# Bench batters get this, and it is the game's own code for "not in the lineup".
BENCH_POSITION = "B"

# Rotation slots 15-19 and bullpen slots 20-24.
ROTATION_POSITIONS: tuple[str, ...] = ("SP1", "SP2", "SP3", "SP4", "SP5")

# Two `MR` entries and no fifth distinct role: the third and fourth relievers are
# both middle relief. Preserved -- the game accepts duplicates here, and a fifth
# role would change which pitcher the CPU warms up.
BULLPEN_POSITIONS: tuple[str, ...] = ("CP", "SU", "MR", "MR", "LR")

# What a player not in the batting order stores in the order column.
NOT_IN_LINEUP = -1

# Slot index -> game abbreviation, and the single source of the slot ordering.
# Never index `TEAM_HASHES` by slot number: relying on that dict's insertion
# order means reordering it silently gives every team another team's roster.
MVP_TEAM_ABBREVS: tuple[str, ...] = (
    "ANA",
    "OAK",
    "SEA",
    "TEX",
    "CWS",
    "CLE",
    "DET",
    "KC",
    "MIN",
    "BAL",
    "BOS",
    "NYY",
    "TB",
    "TOR",
    "ARI",
    "COL",
    "LA",
    "SD",
    "SF",
    "CHC",
    "CIN",
    "HOU",
    "MIL",
    "PIT",
    "STL",
    "ATL",
    "FLA",
    "WAS",
    "NYM",
    "PHI",
)

# The clubs renamed or moved since 2005: slot abbreviation -> `team` columns 1-3
# (location, nickname, abbreviation). The league and division columns stay:
# the schedule and the DH rule follow the slot's league, and the abbreviation
# stays the disc's.
TEAM_RENAMES: dict[str, tuple[str, str, str]] = {
    "ANA": ("Los Angeles", "Angels", "ANA"),
    "OAK": ("Sacramento", "Athletics", "OAK"),
    "CLE": ("Cleveland", "Guardians", "CLE"),
    "TB": ("Tampa Bay", "Rays", "TB"),
    "FLA": ("Miami", "Marlins", "FLA"),
}

# Slot index -> the club's 2005 name, for display only.
MVP_TEAM_ORDER: tuple[str, ...] = (
    "Anaheim Angels",
    "Oakland Athletics",
    "Seattle Mariners",
    "Texas Rangers",
    "Chicago White Sox",
    "Cleveland Indians",
    "Detroit Tigers",
    "Kansas City Royals",
    "Minnesota Twins",
    "Baltimore Orioles",
    "Boston Red Sox",
    "New York Yankees",
    "Tampa Bay Devil Rays",
    "Toronto Blue Jays",
    "Arizona Diamondbacks",
    "Colorado Rockies",
    "Los Angeles Dodgers",
    "San Diego Padres",
    "San Francisco Giants",
    "Chicago Cubs",
    "Cincinnati Reds",
    "Houston Astros",
    "Milwaukee Brewers",
    "Pittsburgh Pirates",
    "St. Louis Cardinals",
    "Atlanta Braves",
    "Florida Marlins",
    "Washington Nationals",
    "New York Mets",
    "Philadelphia Phillies",
)

# Derived, not written out again.
MVP_ABBREV_TO_INDEX: dict[str, int] = {code: i for i, code in enumerate(MVP_TEAM_ABBREVS)}

# Slots 0-13 are the American League and 14-29 the National: `MVP_TEAM_ABBREVS`
# is ordered AL first, and the 2005 leagues were 14 clubs and 16.
#
# It does not pick a `roster` row's lineup columns. On the retail disc every
# club, AL or NL, fills both the AL (DH) and the NL (pitcher bats) pairs, since
# either league's club can play under either rule; the patcher does the same.
AL_SLOT_COUNT = 14

# Game abbreviation -> the nine-hex-digit id the `team` table keys on. Carried
# over unverified; nothing here can confirm them.
TEAM_HASHES: dict[str, str] = {
    "ANA": "00b87d5f5",
    "OAK": "00b880fe0",
    "SEA": "00b88215e",
    "TEX": "00b8825b6",
    "CWS": "00b87db72",
    "CLE": "00b87de39",
    "DET": "00b87e1a2",
    "KC": "000597433",
    "MIN": "00b880869",
    "BAL": "00b87d894",
    "BOS": "00b87da69",
    "NYY": "00b880a85",
    "TB": "00059755b",
    "TOR": "00b8826fa",
    "ARI": "00b87d681",
    "COL": "00b87dea3",
    "LA": "000597452",
    "SD": "00059753c",
    "SF": "00059753e",
    "CHC": "00b87dd93",
    "CIN": "00b87dddf",
    "HOU": "00b87f3f1",
    "MIL": "00b880867",
    "PIT": "00b881532",
    "STL": "00b882338",
    "ATL": "00b87d6c6",
    "FLA": "00b87eaf8",
    "WAS": "00b8831f0",
    "NYM": "00b880a79",
    "PHI": "00b881506",
}

# How many characters a record id has. Every id in `TEAM_HASHES` is nine, and
# the reader synthesises none shorter.
HASH_ID_CHARS = 9

# Provider abbreviation -> game abbreviation. Six clubs have been renamed or
# moved since 2005 and three more have a second spelling one provider uses:
# `ATH` for Oakland, `CHW` for the White Sox.
MODERN_MLB_TO_MVP: dict[str, str] = {
    "LAA": "ANA",  # Anaheim Angels -> Los Angeles Angels
    "OAK": "OAK",
    "ATH": "OAK",  # ESPN's alternate spelling
    "SEA": "SEA",
    "TEX": "TEX",
    "CWS": "CWS",
    "CHW": "CWS",  # ESPN's alternate spelling
    "CLE": "CLE",  # Cleveland Indians -> Guardians
    "DET": "DET",
    "KC": "KC",
    "MIN": "MIN",
    "BAL": "BAL",
    "BOS": "BOS",
    "NYY": "NYY",
    "TB": "TB",  # Tampa Bay Devil Rays -> Rays
    "TOR": "TOR",
    "ARI": "ARI",
    "COL": "COL",
    "LAD": "LA",
    "SD": "SD",
    "SF": "SF",
    "CHC": "CHC",
    "CIN": "CIN",
    "HOU": "HOU",  # moved from the NL Central to the AL West in 2013
    "MIL": "MIL",
    "PIT": "PIT",
    "STL": "STL",
    "ATL": "ATL",
    "MIA": "FLA",  # Florida Marlins -> Miami Marlins
    "WSH": "WAS",
    "NYM": "NYM",
    "PHI": "PHI",
}

# Pitch type codes, as the Edit Player screen's Pitch 2 cycles them. Pitch 1 is
# always the fastball and has no type column.
PITCH_NAMES: dict[int, str] = {
    1: "Changeup",
    2: "Curveball",
    3: "Knuckleball",
    4: "Screwball",
    5: "Sinker",
    6: "Slider",
    7: "Splitter",
    8: "2-Seam Fastball",
    9: "Cutter",
    10: "Circle Change",
    11: "Forkball",
    12: "Knucklecurve",
    13: "Palmball",
    14: "Slurve",
}
# No provider names a pitcher's pitches, so after the fastball he throws the
# retail disc's commonest arsenal for his role: its starters' (30 of 200) and
# its relievers' (22 of 180).
STARTER_ARSENAL = (1, 2, 6, 8)
RELIEVER_ARSENAL = (1, 6)

# `attrib` column 6 codes besides the positions.
SECONDARY_DH = 9
SECONDARY_OF = 11
SECONDARY_IF = 12
SECONDARY_UTILITY = 13


@dataclass
class MVPPlayerRecord:
    """One player, ready to be written into four CSV tables.

    `hash_id` is empty until `patch` assigns one out of the disc's own pool:
    which id a player gets depends on what the disc already holds.

    `height` and `weight` are the provider's, else 6'0" and 190 lb.
    `player_id`, `photo_url` and `nationality` are the provider's, for the
    player's portrait and looks (`looks`, `portraits`); none is a column.

    `ratings` holds where he ranks in his group, 0-1, for each rating the
    mapper had his stats for (`stat_mapper.RATINGS`); `patch` writes the value
    at that rank of the disc's own spread of it (`spreads`), and for a rating
    not in it the disc's median for his position.
    """

    first_name: str = ""
    last_name: str = ""
    jersey: int = 0
    bats: int = 0  # 0=R, 1=L, 2=S
    throws: int = 0  # 0=R, 1=L
    primary_position: str = "CF"
    secondary_position: int | None = None  # `attrib` column 6; None for his position's default
    height: int = 72  # inches
    weight: int = 190  # pounds
    birth_date: str = ""  # ISO; "" keeps the disc's

    ratings: dict[str, float] = field(default_factory=dict)

    is_pitcher: bool = False
    pitch_types: tuple[int, ...] = ()

    # Filled by `map_rosters` from the player's index in the selected roster,
    # not by the mapper: they are facts about the slot, not about the player.
    roster_position: str = ""
    batting_order: int = NOT_IN_LINEUP
    hash_id: str = ""

    player_id: int = 0
    photo_url: str = ""
    nationality: str = ""


@dataclass
class MVPTeamSlot:
    """One of the 30 team slots, as the disc holds it today."""

    index: int
    name: str
    abbrev: str
    player_count: int = 0
    first_player: str = ""


@dataclass
class MVPRomInfo:
    """What the reader learned about one ISO.

    Internal to this package; `patcher.analyze_rom` translates it into the
    library's `RomInfo`.
    """

    path: str
    size: int
    database_big_offset: int = 0
    database_big_size: int = 0
    team_slots: list[MVPTeamSlot] = field(default_factory=list)
    is_valid: bool = False
