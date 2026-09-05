"""Constants and record types for MVP Baseball (PSP, ULUS-10012).

**This game's database is CSV text inside compressed sections**, which is a
third patching model and not the two the rest of this library uses. It is
neither byte offsets into a cartridge (`games/nhl94_genesis`) nor named
bit-packed records in a TDB (`games/nhl05_ps2`). A record here is a line of
ASCII:

    00b87d5f5,0 Vladimir,1 Guerrero,2 27,22 61,;\\r\\n

-- a nine-hex-digit id, then `fieldnum value` pairs separated by commas,
terminated by `,;` and a CRLF. Tables link to each other by those ids: a
`roster` row names a team id and a player id, and the player id is what the
`attrib`, `lrattrib_rhp`, `lrattrib_lhp` and `pitchattrib` tables key on. So the
field-number constants below are not addresses; they are column names, and a
wrong one writes a real column of a real record with the wrong meaning rather
than crashing.

**`database.big` is 19 sections, not 18.** The source's own docstring said 18 in
three places and `SECTION_MAP` has nineteen entries; the count was simply wrong
and it is corrected here. Each section is an independent RefPack stream at a
fixed offset, and the space between one offset and the next is that section's
whole allocation -- there is no length word anywhere, so a section that
recompresses larger than its allocation cannot be stored at all. `rom_writer`
raises rather than silently keeping the original; see `MVPPSPRomWriter`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ──────────────────────────────────────────────────────────────
# Where database.big lives
# ──────────────────────────────────────────────────────────────
#
# A hardcoded LBA, and that is the whole of this game's ISO 9660 interaction:
# two `seek(DATABASE_BIG_LBA * ISO_SECTOR_SIZE)` calls, no primary volume
# descriptor, no directory traversal, no path resolution.
#
# **So this package deliberately does not use `formats/iso9660.py`**, which
# `games/nhl05_ps2` and `games/nhl07_psp` both do. What it shares with them is
# the integer 2048 and nothing else, and wiring it to the shared walker would
# invent a dependency the game does not have -- and would make this patcher
# refuse a valid image whose PVD it could not parse, over a file it never needed
# the PVD to find.
#
# The three numbers have never been checked against a real disc; no ISO may
# enter this repository. They are the source's, carried over unverified, and the
# arithmetic they imply is pinned in `patcher._database_big_extent_fits`.
DATABASE_BIG_LBA = 334832
DATABASE_BIG_SIZE = 386977
ISO_SECTOR_SIZE = 2048


def database_big_extent() -> tuple[int, int]:
    """(first byte, last byte + 1) of `database.big` within the image.

    **A function rather than two module constants, and the reason is testing.**
    `DATABASE_BIG_LBA` puts the blob 685 MB into the file, so any test that
    exercises the copy-and-write path against a real-sized image would move 686
    MB of real bytes per case -- and a sparse file does not help, because
    `MVPPSPRomWriter.copy_iso` reads it and writes what it read. Reading the LBA
    here, at call time, from this module's own global is what lets a test shrink
    the whole stack coherently with one `monkeypatch.setattr`: the reader, the
    writer and the patcher then all agree, where three separately imported
    constants could be patched into disagreement and pass.

    The three numbers are the source's, carried over unverified. No disc may
    enter this repository to check them, and `tests/games/mvp_psp` pins the
    arithmetic they imply -- `[685735936, 686122913)` -- against the unpatched
    constants, on sparse files that are never copied.
    """
    start = DATABASE_BIG_LBA * ISO_SECTOR_SIZE
    return start, start + DATABASE_BIG_SIZE


# ──────────────────────────────────────────────────────────────
# The section table
# ──────────────────────────────────────────────────────────────

# `(offset within database.big, table name)`, ascending. Nineteen entries.
# Offsets are the source's and are likewise unverified against a real disc.
SECTION_MAP: tuple[tuple[int, str], ...] = (
    (0, "attrib_compact"),
    (324, "attrib"),
    (61772, "lrattrib_rhp"),
    (101852, "lrattrib_lhp"),
    (144692, "batstat"),
    (165552, "fieldstat"),
    (188428, "lrbatstat_rhp"),
    (214440, "lrpitchstat_rhp"),
    (229676, "pitchstat"),
    (245436, "lrbatstat_lhp"),
    (274488, "lrpitchstat_lhp"),
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
    last section's is the distance to the end of `database.big`. Derived here so
    the writer does not re-derive it by index arithmetic on a list, which is
    what the source did and what made the "is this the last one" branch easy to
    get wrong.
    """
    result: dict[str, tuple[int, int]] = {}
    for i, (offset, name) in enumerate(SECTION_MAP):
        end = SECTION_MAP[i + 1][0] if i + 1 < len(SECTION_MAP) else DATABASE_BIG_SIZE
        result[name] = (offset, end - offset)
    return result


SECTION_ALLOCATIONS = _section_allocations()

# The tables this patcher rewrites. **Four, not the seven the source listed.**
#
# The source's `WRITABLE_TABLES` named `attrib`, `lrattrib_rhp`,
# `lrattrib_lhp`, `pitchattrib`, `roster`, `team` and `teamstat`, was never read
# by anything, and disagreed with the code: nothing in the package ever wrote
# `team` or `teamstat`. Dropping the constant and naming what is actually
# written is a labelled change to a table -- see the package docstring.
#
# `roster` is rebuilt wholesale and the other four are merged into per record,
# so this tuple is a documentation aid and a test anchor rather than a control
# flow input; `rom_writer` writes whatever `update_*` marked modified.
PLAYER_TABLES: tuple[str, ...] = ("attrib", "lrattrib_rhp", "lrattrib_lhp", "pitchattrib")
ROSTER_TABLE = "roster"
MODIFIED_TABLES: tuple[str, ...] = (*PLAYER_TABLES, ROSTER_TABLE)

# The first section. It is the only one the patcher never rewrites, and that is
# an inherited defect rather than a decision -- see `rom_writer`.
COMPACT_ATTRIB_TABLE = "attrib_compact"

# ──────────────────────────────────────────────────────────────
# CSV field numbers
# ──────────────────────────────────────────────────────────────
#
# Column numbers within each table, from the section's own header line. The ones
# no code below writes are kept because together they are the only description
# of this file format that exists anywhere in the project, and a future field
# that needs writing is a constant away rather than a re-derivation from a disc
# nobody may commit.

ATTRIB_FIRST_NAME = 0
ATTRIB_LAST_NAME = 1
ATTRIB_JERSEY = 2
ATTRIB_BATS = 3  # 0=R, 1=L, 2=S
ATTRIB_THROWS = 4  # 0=R, 1=L
ATTRIB_PRIMARY_POS = 5
ATTRIB_SECONDARY_POS = 6
ATTRIB_HEIGHT = 9  # inches
ATTRIB_WEIGHT = 10  # pounds
ATTRIB_PLATE_DISCIPLINE = 18
ATTRIB_BUNTING = 19
ATTRIB_STEALING_AGGRESSIVE = 20
ATTRIB_BASERUNNING = 21
ATTRIB_SPEED = 22
ATTRIB_FIELDING = 23
ATTRIB_RANGE = 24
ATTRIB_THROW_STRENGTH = 25
ATTRIB_THROW_ACCURACY = 26
ATTRIB_DURABILITY = 27
ATTRIB_SALARY = 39  # not written
ATTRIB_CONTRACT_LENGTH = 40  # not written
ATTRIB_STARPOWER = 41
ATTRIB_BIRTHDAY = 43  # not written

# `lrattrib_rhp` and `lrattrib_lhp` share a layout: the same player, split by
# the handedness of the pitcher he is facing. Only names, contact and power are
# written; the spray-chart and batted-ball columns are left as the disc has
# them, because nothing in any provider's data could produce them.
LR_FIRST_NAME = 0
LR_LAST_NAME = 1
LR_CONTACT = 2
LR_POWER = 3
LR_SPRAY_UL = 4  # not written
LR_SPRAY_UM = 5  # not written
LR_SPRAY_UR = 6  # not written
LR_SPRAY_CL = 7  # not written
LR_SPRAY_CM = 8  # not written
LR_SPRAY_CR = 9  # not written
LR_SPRAY_LL = 10  # not written
LR_SPRAY_LM = 11  # not written
LR_SPRAY_LR = 12  # not written
LR_FIELD_PCT_LF = 13  # not written
LR_FIELD_PCT_CF = 14  # not written
LR_FIELD_PCT_RF = 15  # not written
LR_HR_PCT = 16  # not written
LR_FB = 17  # not written
LR_LD = 18  # not written
LR_GB = 19  # not written

# `pitchattrib`. **Pitch 1 is asymmetric with pitches 2-5**: it is always a
# fastball, so it has no type column and occupies four columns where every later
# pitch occupies five. That is why `PA_PITCH2_TYPE` is the base of the repeating
# block and pitch 1 is addressed by its own three constants.
PA_FIRST_NAME = 0
PA_LAST_NAME = 1
PA_STAMINA = 2
PA_PICKOFF = 3
PA_PITCH1_MOVEMENT = 4
PA_PITCH1_DESC = 5  # not written
PA_PITCH1_CONTROL = 6
PA_PITCH1_VELOCITY = 7
PA_PITCH2_TYPE = 8
PA_PITCH2_MOVEMENT = 9
PA_PITCH2_DESC = 10  # not written
PA_PITCH2_CONTROL = 11
PA_PITCH2_VELOCITY = 12
PA_PITCHER_DELIVERY = 28  # not written

# Columns per pitch in the repeating block, and the offsets within one.
PA_PITCH_STRIDE = 5
PA_PITCH_TYPE_OFFSET = 0
PA_PITCH_MOVEMENT_OFFSET = 1
PA_PITCH_CONTROL_OFFSET = 3
PA_PITCH_VELOCITY_OFFSET = 4

# How many of a pitcher's arsenal reach the repeating block. The source sliced
# `player.pitches[1:4]`, so at most three pitches after the fastball, which
# leaves columns 23-27 (pitch 5) untouched on every player.
MAX_EXTRA_PITCHES = 3

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

# ──────────────────────────────────────────────────────────────
# Position encoding
# ──────────────────────────────────────────────────────────────

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

# Position string -> `attrib` column 5. **`DH` maps to 2 (first base) and `OF`
# to 7 (centre field)**, both the source's, and both lossy in a way worth
# stating: the game has no designated-hitter position code, so a DH is stored as
# a first baseman and will be offered at first base by the game's own lineup
# screen.
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

# What `_build_attrib_fields` writes for a position string the table above does
# not name. 7 is centre field, the source's fallback.
DEFAULT_POS_NUM = ATTRIB_POS_CF

# ──────────────────────────────────────────────────────────────
# Roster shape
# ──────────────────────────────────────────────────────────────

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

# **`_select_position_players` fills positions in a different order from the one
# the lineup is written in**, and that is the source's behaviour, preserved.
# `LINEUP_POSITIONS` puts SS third and 3B fifth; selection asks for 3B before
# SS. The consequence is that a player who qualifies at both is taken as a third
# baseman and then batted third, in the slot labelled SS. Faithful, and named
# here so it is visible rather than buried in two literals that look alike.
SELECTION_POSITIONS: tuple[str, ...] = ("C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH")

# Bench batters get this, and it is the game's own code for "not in the lineup".
BENCH_POSITION = "B"

# Rotation slots 15-19 and bullpen slots 20-24.
ROTATION_POSITIONS: tuple[str, ...] = ("SP1", "SP2", "SP3", "SP4", "SP5")

# **Two `MR` entries and no fifth distinct role.** The source wrote
# `["CP", "SU", "MR", "MR", "LR"]`, so the third and fourth relievers are both
# middle relief. Preserved: the game accepts duplicates here, and inventing a
# fifth role would change which pitcher the CPU warms up.
BULLPEN_POSITIONS: tuple[str, ...] = ("CP", "SU", "MR", "MR", "LR")

# What a player not in the batting order stores in the order column.
NOT_IN_LINEUP = -1

# ──────────────────────────────────────────────────────────────
# Teams
# ──────────────────────────────────────────────────────────────

# Slot index -> game abbreviation. **This tuple is the single source of the
# slot ordering.** The source carried the same ordering three times -- as the
# insertion order of `TEAM_HASHES`, as the insertion order of the byte-identical
# duplicate `TEAM_ABBREV_TO_HASH`, and as the values of `MVP_ABBREV_TO_INDEX` --
# and then two files, `patcher.py:160` and `rom_reader.py:250`, indexed
# `list(TEAM_HASHES.keys())` by slot number, relying on dict insertion order
# with nothing asserting the three agreed. They did agree; that was luck, and
# reordering the hash table would have silently given every team another team's
# roster.
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

# **Slots 0-13 are the American League and 14-29 the National.** The source
# wrote the boundary inline as `team_index < 14` with no explanation, which
# reads like an arbitrary constant and is not: `MVP_TEAM_ABBREVS` is ordered AL
# first, and the 2005 leagues were 14 clubs and 16. The number is checked
# against that ordering by a test rather than left to agree by accident.
#
# What it decides is which of the four (position, order) column pairs in a
# `roster` row carries the real batting order: an AL club bats its lineup in the
# AL columns and stores -1 in the NL ones, because the NL had no designated
# hitter in 2005 and the game keeps a separate lineup for each rule.
AL_SLOT_COUNT = 14

# Game abbreviation -> the nine-hex-digit id the `team` table keys on. Carried
# over unverified: these came out of a real disc that cannot enter this
# repository, and nothing here can confirm them.
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

# ──────────────────────────────────────────────────────────────
# Record types
# ──────────────────────────────────────────────────────────────

# Every rating in this game is on 0-99. There is no other scale in the package.
ATTR_MIN = 0
ATTR_MAX = 99

# Pitch type codes. Only these three are ever produced, and the fastball code is
# written for pitch 1 only as a placeholder the game ignores -- column 8 is the
# first type column and pitch 1 has none.
PITCH_FASTBALL = 1
PITCH_SLIDER = 3
PITCH_CHANGEUP = 4


@dataclass(frozen=True)
class MVPPitch:
    """One entry in a pitcher's arsenal.

    A frozen dataclass where the source used `dict[str, int]` with four keys and
    read them back through `.get(key, 50)`. The defaults were unreachable --
    `_default_pitches` is the only producer and always sets all four -- so
    naming the fields loses nothing and makes a typo a type error instead of a
    silent 50.
    """

    type: int
    movement: int
    control: int
    velocity: int


@dataclass
class MVPPlayerRecord:
    """One player, ready to be written into four CSV tables.

    `hash_id` is empty until `patch` assigns one out of the disc's own pool: the
    mapper cannot know it, because which id a player gets depends on what the
    disc already holds. Everything else comes from the provider and the mapper.

    UPSTREAM BEHAVIOUR, KNOWN WRONG, PRESERVED DELIBERATELY -- neither `height`
    nor `weight` has a producer. Nothing in this package, and nothing in the
    source package it came from, ever assigns either, so every player written to
    a disc is 6'0" and 190 lb. Both are written unconditionally; see
    `patcher._build_attrib_fields`.
    """

    first_name: str = ""
    last_name: str = ""
    jersey: int = 0
    bats: int = 0  # 0=R, 1=L, 2=S
    throws: int = 0  # 0=R, 1=L
    primary_position: str = "CF"
    secondary_position: str = ""
    height: int = 72  # inches; see the class docstring -- nothing sets this
    weight: int = 190  # pounds; see the class docstring -- nothing sets this either

    speed: int = 50
    fielding: int = 50
    arm_range: int = 50
    throw_strength: int = 50
    throw_accuracy: int = 50
    durability: int = 50
    plate_discipline: int = 50
    bunting: int = 50
    baserunning: int = 50
    stealing: int = 50
    starpower: int = 50

    contact_rhp: int = 50
    power_rhp: int = 50
    contact_lhp: int = 50
    power_lhp: int = 50

    is_pitcher: bool = False
    stamina: int = 50
    pickoff: int = 50
    pitches: list[MVPPitch] = field(default_factory=list)

    # Filled by `map_rosters` from the player's index in the selected roster,
    # not by the mapper: they are facts about the slot, not about the player.
    roster_position: str = ""
    batting_order: int = NOT_IN_LINEUP
    hash_id: str = ""


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
