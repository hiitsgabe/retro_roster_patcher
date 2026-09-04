"""Team tables, attribute records and the ROM-info type for NHL 07 (PSP).

Roster data lives in TDB tables inside a BIGF archive, `db.viv`, on the ISO --
see `formats/ea_tdb.py` for all three layers. Nothing in this package addresses
a player by byte offset; every write names a four-character TDB field.

The five tables this package touches, and how they chain:

    ROST  one row per roster slot: which team, which jersey, which line flags
    PLAY  ROST.INDX == PLAY.INDX, and PLAY.ID__ is the player's real id
    SPBT  the bios: SPBT.INDX == PLAY.ID__
    SPAI  skater attributes, keyed the same way
    SGAI  goalie attributes, keyed the same way -- and a player's presence here
          rather than in SPAI is what makes his slot a goalie slot
    STEA  the team table, read for `RomSlot.current_name` and nothing else

**None of the field names or widths below has ever been checked against a
retail disc**, here or upstream; no real ISO may enter this repository. They are
transcribed from the source package.

References:
  - Game id on the disc: ULUS10131 (US). Nothing reads it -- see
    `rom_reader.NHL07PSPRomReader.validate` for what the signature check
    actually looks at, and `patcher` for why.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# STEA table INDX -> modern team abbreviation. 30 NHL clubs as of 2006-07, then
# the two All-Star sides, which are real slots and are patchable: `EAS` and
# `WES` take Seattle and Vegas below, since neither existed in 2006.
NHL07_TEAM_INDEX = {
    0: "ANA",
    1: "ATL",
    2: "BOS",
    3: "BUF",
    4: "CGY",
    5: "CAR",
    6: "CHI",
    7: "COL",
    8: "CBJ",
    9: "DAL",
    10: "DET",
    11: "EDM",
    12: "FLA",
    13: "LA",
    14: "MIN",
    15: "MTL",
    16: "NSH",
    17: "NJ",
    18: "NYI",
    19: "NYR",
    20: "OTT",
    21: "PHI",
    22: "PHX",
    23: "PIT",
    24: "STL",
    25: "SJ",
    26: "TB",
    27: "TOR",
    28: "VAN",
    29: "WSH",
    30: "EAS",
    31: "WES",
}

# Modern NHL abbreviation -> STEA INDX, for matching a fetched roster to a slot.
# Both providers' spellings are here, which is why this maps 38 codes onto 32
# slots: ESPN says `LA`, `NJ`, `SJ` and `TB` where the NHL API says `LAK`,
# `NJD`, `SJS` and `TBL`. Six further codes are relocations or expansions that
# reuse an ancestor's slot. **Collapsing 38 keys onto 32 values means two
# fetched teams can name one slot**, which `NHL07PSPPatcher.map_rosters` has to
# guard; the comment there says what goes wrong without it.
MODERN_NHL_TO_NHL07 = {
    "ANA": 0,  # Anaheim Ducks
    "ATL": 1,  # Atlanta Thrashers (now WPG)
    "BOS": 2,  # Boston Bruins
    "BUF": 3,  # Buffalo Sabres
    "CGY": 4,  # Calgary Flames
    "CAR": 5,  # Carolina Hurricanes
    "CHI": 6,  # Chicago Blackhawks
    "COL": 7,  # Colorado Avalanche
    "CBJ": 8,  # Columbus Blue Jackets
    "DAL": 9,  # Dallas Stars
    "DET": 10,  # Detroit Red Wings
    "EDM": 11,  # Edmonton Oilers
    "FLA": 12,  # Florida Panthers
    "LAK": 13,  # Los Angeles Kings
    "LA": 13,  # ESPN abbreviation
    "MIN": 14,  # Minnesota Wild
    "MTL": 15,  # Montreal Canadiens
    "NSH": 16,  # Nashville Predators
    "NJD": 17,  # New Jersey Devils
    "NJ": 17,  # ESPN abbreviation
    "NYI": 18,  # New York Islanders
    "NYR": 19,  # New York Rangers
    "OTT": 20,  # Ottawa Senators
    "PHI": 21,  # Philadelphia Flyers
    "PHX": 22,  # Phoenix Coyotes (now UTA)
    "ARI": 22,  # Arizona Coyotes (became UTA)
    "UTA": 22,  # Utah Hockey Club -> use Phoenix slot
    "PIT": 23,  # Pittsburgh Penguins
    "STL": 24,  # St. Louis Blues
    "SJS": 25,  # San Jose Sharks
    "SJ": 25,  # ESPN abbreviation
    "TBL": 26,  # Tampa Bay Lightning
    "TB": 26,  # ESPN abbreviation
    "TOR": 27,  # Toronto Maple Leafs
    "VAN": 28,  # Vancouver Canucks
    "WSH": 29,  # Washington Capitals
    # Expansion and relocation, mapped to the closest slot.
    "WPG": 1,  # Winnipeg Jets -> use Atlanta slot
    "VGK": 31,  # Vegas -> use the WES All-Star slot
    "SEA": 30,  # Seattle -> use the EAS All-Star slot
}

# Display names by team index, used for `RomSlot.display_name` and for progress
# messages. Distinct across all 32 entries, which `RomSlot.display_name`
# requires: a slot-picking UI lists this field and two identical rows leave the
# user unable to tell them apart.
NHL07_TEAM_NAMES = [
    "Anaheim",  # 0
    "Atlanta",  # 1
    "Boston",  # 2
    "Buffalo",  # 3
    "Calgary",  # 4
    "Carolina",  # 5
    "Chicago",  # 6
    "Colorado",  # 7
    "Columbus",  # 8
    "Dallas",  # 9
    "Detroit",  # 10
    "Edmonton",  # 11
    "Florida",  # 12
    "Los Angeles",  # 13
    "Minnesota",  # 14
    "Montreal",  # 15
    "Nashville",  # 16
    "New Jersey",  # 17
    "NY Islanders",  # 18
    "NY Rangers",  # 19
    "Ottawa",  # 20
    "Philadelphia",  # 21
    "Phoenix",  # 22
    "Pittsburgh",  # 23
    "St. Louis",  # 24
    "San Jose",  # 25
    "Tampa Bay",  # 26
    "Toronto",  # 27
    "Vancouver",  # 28
    "Washington",  # 29
    "East All-Star",  # 30
    "West All-Star",  # 31
]

# The NHL clubs alone. `TEAM_COUNT` bounds the fallback slot list the reader
# builds when it cannot read STEA, so the two All-Star slots are absent from
# that list -- but they are NOT excluded from patching: `MODERN_NHL_TO_NHL07`
# routes Seattle and Vegas to 30 and 31, and `map_rosters` bounds slots by
# `len(NHL07_TEAM_NAMES)` rather than by this.
TEAM_COUNT = 30

# The number of ROM slots a mapped roster may name at all. Every index in
# `MODERN_NHL_TO_NHL07` is below it, and `map_rosters` re-checks because those
# keys can cross a JSON boundary before `patch` sees them.
SLOT_COUNT = len(NHL07_TEAM_NAMES)

# TDB POS_ field code -> position string. The reverse direction is what the
# writer uses; a position the game does not know maps to 0, a centre.
POSITION_MAP = {
    0: "C",
    1: "LW",
    2: "RW",
    3: "D",
    4: "G",
}
POSITION_REVERSE = {v: k for k, v in POSITION_MAP.items()}

# The three TDB members of `db.viv`, spelled as the source spells them. Every
# lookup through them is case-insensitive (`bigf_extract` folds case), and the
# one place the archive's own spelling is needed -- writing back -- reads it out
# of `bigf_parse`, because `bigf_replace_inplace` selects case-insensitively.
TDB_MASTER = "nhl2007.tdb"
TDB_BIOATT = "nhlbioatt.tdb"
TDB_ROSTER = "nhlrost.tdb"

# The longest name that survives a write to SPBT's `FNME` or `LNME`. The field
# is 20 bytes and `TDBTable.write_record` NUL-pads whatever it is given to fill
# it, so a 20-character name would leave no terminator; 19 is the last length
# that does. Both the mapper and the writer truncate to it -- the mapper so the
# caller sees what will be written, the writer so a record built by hand cannot
# get past it.
NAME_FIELD_CHARS = 19


@dataclass
class NHL07SkaterAttributes:
    """Skater ratings on NHL 07's 0-63 scale, one six-bit TDB field each.

    `fighting` is the exception: `FIGH` is two bits, so 0-3, and the default of
    1 is what an unrated skater gets. Every other default is 30, the middle of
    the six-bit range; `stat_mapper.SKATER_DEFAULTS` overrides them per position
    and is what actually reaches a record.
    """

    balance: int = 30  # BALA
    penalty: int = 30  # PENA
    shot_accuracy: int = 30  # SACC
    wrist_accuracy: int = 30  # WACC
    faceoffs: int = 30  # FACE
    acceleration: int = 30  # ACCE
    speed: int = 30  # SPEE
    potential: int = 30  # POTE
    deking: int = 30  # DEKG
    checking: int = 30  # CHKG
    toughness: int = 30  # TOUG
    fighting: int = 1  # FIGH, two bits
    puck_control: int = 30  # PUCK
    agility: int = 30  # AGIL
    hero: int = 30  # HERO
    aggression: int = 30  # AGGR
    pressure: int = 30  # PRES
    passing: int = 30  # PASS
    endurance: int = 30  # ENDU
    injury: int = 30  # INJU
    slap_power: int = 30  # SPOW
    wrist_power: int = 30  # WPOW


@dataclass
class NHL07GoalieAttributes:
    """Goalie ratings on the same 0-63 scale, and the same two-bit `FIGH`.

    `5HOL`, `GSH_`, `SSH_`, `GSL_` and `SSL_` are the five save zones: five-hole,
    glove high, stick high, glove low, stick low.
    """

    breakaway: int = 30  # BRKA
    rebound_ctrl: int = 30  # REBC
    shot_recovery: int = 30  # SREC
    speed: int = 30  # SPEE
    poke_check: int = 30  # POKE
    intensity: int = 30  # INTE
    potential: int = 30  # POTE
    toughness: int = 30  # TOUG
    fighting: int = 1  # FIGH, two bits
    agility: int = 30  # AGIL
    five_hole: int = 30  # 5HOL
    passing: int = 30  # PASS
    endurance: int = 30  # ENDU
    glove_high: int = 30  # GSH_
    stick_high: int = 30  # SSH_
    glove_low: int = 30  # GSL_
    stick_low: int = 30  # SSL_


@dataclass
class NHL07PlayerRecord:
    """One player, reduced to what the three TDB tables take.

    Exactly one of `skater_attrs` and `goalie_attrs` is ever set, and which one
    follows `is_goalie`. Both are `None` by default rather than a shared default
    object: a dataclass field defaulting to a mutable instance is one object for
    every record ever built, and two of the migrated games shipped that bug.

    There is no `height`. `HEIG` is a real five-bit field in SPBT and this
    package deliberately does not write it -- see
    `rom_writer.NHL07PSPRomWriter.write_player_bio`.
    """

    first_name: str = ""
    last_name: str = ""
    position: str = "C"
    jersey_number: int = 1
    handedness: int = 1  # 0 = left, 1 = right
    weight: int = 190  # raw pounds
    team_index: int = 0
    player_id: int = 0
    is_goalie: bool = False
    skater_attrs: NHL07SkaterAttributes | None = None
    goalie_attrs: NHL07GoalieAttributes | None = None


@dataclass
class NHL07TeamSlot:
    """One team slot as the reader found it in STEA, or as a fallback name.

    `name` comes from the disc when STEA has a `NAME` or `CITY` string for the
    slot, and from `NHL07_TEAM_NAMES` when it does not; `index` is STEA's own
    `INDX` value rather than the record's position, because the two need not
    agree.
    """

    index: int
    name: str
    abbreviation: str


@dataclass
class NHL07RomInfo:
    """What the reader learned about one ISO.

    Internal to this package: `NHL07PSPPatcher.analyze_rom` translates it into
    the library's `RomInfo`. `size` is the ISO's size on disk, not `db.viv`'s.
    """

    path: str
    size: int = 0
    team_slots: list[NHL07TeamSlot] = field(default_factory=list)
    is_valid: bool = False
