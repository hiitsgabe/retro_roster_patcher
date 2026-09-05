"""Team tables, attribute records and the ROM-info type for NHL 2005 (PS2).

Roster data lives in TDB tables inside a BIGF archive, `DB.VIV`, on the ISO --
see `formats/ea_tdb.py` for all three layers. Nothing in this package addresses
a player by byte offset; every write names a four-character TDB field.

The six tables this package touches, and how they chain:

    ROST  one row per roster slot: which team, which jersey, which line flags
    PLAY  ROST.INDX == PLAY.INDX, and PLAY.ID__ is the player's real id
    SPBT  the bios: SPBT.INDX == PLAY.ID__
    SPAI  skater attributes, keyed the same way
    SGAI  goalie attributes, keyed the same way -- and a player's presence here
          rather than in SPAI is what makes his slot a goalie slot
    STEA  the team table, read for `RomSlot.current_name` and nothing else

The field names and widths below are transcribed from the source package and
have never been checked against a retail disc.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# STEA table INDX -> modern team abbreviation. The 30 NHL clubs of 2004-05, then
# the two All-Star sides.
#
# `SJ` is 24 and `STL` is 25, the opposite of NHL 07. Do not copy that game's
# order over: swapping them writes the Sharks' roster onto the Blues and the
# Blues' onto the Sharks, which no size check or count would notice.
NHL05_TEAM_INDEX = {
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
    24: "SJ",
    25: "STL",
    26: "TB",
    27: "TOR",
    28: "VAN",
    29: "WSH",
    30: "EAS",
    31: "WES",
}

# Modern NHL abbreviation -> STEA INDX. 39 codes onto 32 slots: ESPN says `LA`,
# `NJ`, `SJ` and `TB` where the NHL API says `LAK`, `NJD`, `SJS` and `TBL`, and
# six further codes are relocations reusing an ancestor's slot. Two fetched teams
# can therefore name one slot, which `NHL05PS2Patcher.map_rosters` must guard.
#
# `SEA` and `VGK` name the All-Star slots and are dead entries: this game patches
# slots 0-29 only. See `PATCHABLE_SLOT_COUNT`.
MODERN_NHL_TO_NHL05 = {
    "ANA": 0,
    "ATL": 1,
    "BOS": 2,
    "BUF": 3,
    "CGY": 4,
    "CAR": 5,
    "CHI": 6,
    "COL": 7,
    "CBJ": 8,
    "DAL": 9,
    "DET": 10,
    "EDM": 11,
    "FLA": 12,
    "LAK": 13,
    "LA": 13,
    "MIN": 14,
    "MTL": 15,
    "NSH": 16,
    "NJD": 17,
    "NJ": 17,
    "NYI": 18,
    "NYR": 19,
    "OTT": 20,
    "PHI": 21,
    "PHX": 22,
    "ARI": 22,
    "UTA": 22,
    "PIT": 23,
    "SJS": 24,
    "SJ": 24,
    "STL": 25,
    "TBL": 26,
    "TB": 26,
    "TOR": 27,
    "VAN": 28,
    "WSH": 29,
    # Expansion and relocation, mapped to the closest slot.
    "WPG": 1,
    "VGK": 31,
    "SEA": 30,
}

# Display names by team index. Keep all 32 distinct: a slot-picking UI lists this
# field and identical rows are indistinguishable.
NHL05_TEAM_NAMES = [
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
    "San Jose",  # 24
    "St. Louis",  # 25
    "Tampa Bay",  # 26
    "Toronto",  # 27
    "Vancouver",  # 28
    "Washington",  # 29
    "East All-Star",  # 30
    "West All-Star",  # 31
]

# The NHL clubs alone, and also every slot this game patches: the two All-Star
# slots are read and listed but never written. Do not raise this to 32; whether
# NHL 2005's All-Star ROST rows have the structure the writer needs is unknown.
TEAM_COUNT = 30
PATCHABLE_SLOT_COUNT = TEAM_COUNT

# The slots the game has a name for, All-Star sides included. Display only, never
# a bound on what to patch.
NAMED_SLOT_COUNT = len(NHL05_TEAM_NAMES)

# TDB POS_ field code -> position string. A position the game does not know maps
# to 0, a centre.
POSITION_MAP = {
    0: "C",
    1: "LW",
    2: "RW",
    3: "D",
    4: "G",
}
POSITION_REVERSE = {v: k for k, v in POSITION_MAP.items()}

# The two TDB members of `DB.VIV`. There is no `nhlbioatt.tdb` mirror on this
# game, so SPBT/SPAI/SGAI are written once each. `nhl2005.tdb` is also this
# game's signature, being year-specific; see `rom_reader.validate`.
TDB_MASTER = "nhl2005.tdb"
TDB_ROSTER = "nhlrost.tdb"

# SPBT's `FNME` and `LNME` are 128-bit fields: 16 ASCII bytes each.
NAME_FIELD_BYTES = 16

# The longest name that survives a write: `TDBTable.write_record` NUL-pads to
# fill the field, so 16 characters would leave no terminator. Derive it here;
# never re-spell it as a literal 15.
NAME_FIELD_CHARS = NAME_FIELD_BYTES - 1


@dataclass
class NHL05SkaterAttributes:
    """Skater ratings on NHL 2005's 0-63 scale, one six-bit TDB field each.

    `FIGH` is the exception at two bits, so 0-3. Every other default is 30, the
    middle of the six-bit range; `stat_mapper.SKATER_DEFAULTS` overrides them per
    position.
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
class NHL05GoalieAttributes:
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
class NHL05PlayerRecord:
    """One player, reduced to what the three TDB tables take.

    Exactly one of `skater_attrs` and `goalie_attrs` is set, following
    `is_goalie`. Both default to `None` and must not default to a shared mutable
    instance.

    Upstream behaviour, known wrong, preserved deliberately: `height` is the
    encoded five-bit `HEIG` and nothing ever overrides this default, because
    `stat_mapper.map_player` derives it from a `Player.height` no provider model
    here has. Every patched player is written at 16, about 5'10".
    """

    first_name: str = ""
    last_name: str = ""
    position: str = "C"
    jersey_number: int = 1
    handedness: int = 1  # 0 = left, 1 = right
    weight: int = 190  # raw pounds
    height: int = 16  # encoded five-bit HEIG, ~5'10"
    team_index: int = 0
    player_id: int = 0
    is_goalie: bool = False
    skater_attrs: NHL05SkaterAttributes | None = None
    goalie_attrs: NHL05GoalieAttributes | None = None


@dataclass
class NHL05TeamSlot:
    """One team slot as the reader found it in STEA, or as a fallback name.

    `name` comes from STEA's `FNME` or `SNME` when present, else from
    `NHL05_TEAM_NAMES`; `abbreviation` prefers STEA's `ABBR`.

    `index` is STEA's own `INDX` value, not the record's position: STEA is
    reported to hold 94 records for 32 slots, so the two do not agree.
    """

    index: int
    name: str
    abbreviation: str


@dataclass
class NHL05RomInfo:
    """What the reader learned about one ISO.

    `size` is the ISO's size on disk, not `DB.VIV`'s.
    """

    path: str
    size: int = 0
    team_slots: list[NHL05TeamSlot] = field(default_factory=list)
    is_valid: bool = False
