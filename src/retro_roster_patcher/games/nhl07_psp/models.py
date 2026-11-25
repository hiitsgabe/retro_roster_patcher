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

`SKATER_FIELDS` and `GOALIE_FIELDS` are every rating field of the retail
disc's SPAI and SGAI (ULUS10131, read 2026-11); the writer refuses a field the
disc's table does not have.

Game id on the disc: ULUS10131 (US). Nothing reads it; see
`rom_reader.NHL07PSPRomReader.validate` for what the signature check looks at.
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

# Modern NHL abbreviation -> STEA INDX. 39 codes onto 32 slots: ESPN says `LA`,
# `NJ`, `SJ` and `TB` where the NHL API says `LAK`, `NJD`, `SJS` and `TBL`, and
# six further codes are relocations reusing an ancestor's slot. Two fetched teams
# can therefore name one slot, which `NHL07PSPPatcher.map_rosters` must guard.
MODERN_NHL_TO_NHL07 = {
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
    "STL": 24,
    "SJS": 25,
    "SJ": 25,
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

# The NHL clubs alone, bounding only the fallback slot list the reader builds
# when it cannot read STEA. The two All-Star slots are absent from that list but
# are still patchable; bound patching by `SLOT_COUNT`, never by this.
TEAM_COUNT = 30

# The number of ROM slots a mapped roster may name at all.
SLOT_COUNT = len(NHL07_TEAM_NAMES)

# The East and West All-Star sides, which Seattle and Vegas take.
ALL_STAR_SLOTS = (30, 31)

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

# The three TDB members of `db.viv`. Lookups through them fold case
# (`bigf_extract`, `bigf_replace_inplace`), so these spellings need not match the
# archive's.
TDB_MASTER = "nhl2007.tdb"
TDB_BIOATT = "nhlbioatt.tdb"
TDB_ROSTER = "nhlrost.tdb"

# The longest name that survives a write to SPBT's `FNME` or `LNME`. The field is
# 20 bytes and `TDBTable.write_record` NUL-pads to fill it, so 20 characters
# would leave no terminator.
# `WEIG` stores pounds above this. Checked on the retail disc: Chara is 110
# (260 lb) and Gionta 25 (175 lb); a raw 194 shows in the game as 344 lb.
WEIGHT_BASE_POUNDS = 150

NAME_FIELD_CHARS = 19

# SPBT's other fields on the retail disc. `ART_` the portrait and face
# (rewritten through `portraits` and `faces`); `YEAD` the draft year less 1959
# (63 undrafted), `POSD` the overall pick and `ROOK` the rookie flag
# (`stat_mapper.set_career`); and the headgear, each value read off the Player
# Editor's equipment page after changing it on one bio of a retail copy:
# `VSCG` a skater's visor and a goalie's helmet and cage (1; 0 none and a
# mask: Hasek, Osgood and Irbe are 1), `VCGP` a skater's visor tint (0 clear,
# 2 red, 3 yellow, 6 orange; 1 to 6 on 14 skaters) and a goalie's glove,
# blocker and pads set (0 the default, 2 "Set 2"), `HBGM` a skater's helmet
# brand (0 CCM, 4 Nike) and a goalie's mask (0 the default). Kept as the bio
# has them: `AUDI` the commentary's name clip (no silent clip is known) and
# `DAUD`, equal to it on every bio; `STIC`, `NECK`, `TUCK`, `SKAT`.
#
# Unidentified: `DVOC`, one bit, equal to `VSCG` on all 3,260 retail bios
# (1,436 hold 1). Tried: set alone on one bio (Couture) and cleared alone on
# another (Hill), the Player Editor's visor and 3D head follow `VSCG` and
# never `DVOC`; perhaps the visor a player gets back on a reset, by its `D`
# like `DAUD`'s, but no screen showed that. Written equal to `VSCG`, as the
# disc has it.


# `YEAD` of a player who was not drafted, or whose draft is not known: the
# Player Editor reads it as "Not available" (1,872 of the retail disc's 3,260 bios).
YEAD_NOT_AVAILABLE = 63

# What a new player wears, where no provider says: a skater a clear visor,
# as every player who entered the NHL since 2013 must; a goalie the default
# mask, not a helmet and cage, and the default pads.
SKATER_WEAR = {"VSCG": 1, "DVOC": 1, "VCGP": 0}
GOALIE_WEAR = {"VSCG": 0, "DVOC": 0, "VCGP": 0, "HBGM": 0}


@dataclass
class NHL07SkaterAttributes:
    """Skater ratings, one SPAI field each (`SKATER_FIELDS`).

    Six-bit fields rated 0-49: every NHL player on the retail disc is 49 or
    less, and the game shows a stored value plus 50. `FIGH` holds 0-3 and the
    three biases 0-15. The defaults stand in for nothing; `stat_mapper`'s
    position defaults are what a player without a stat line gets.
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
    fighting: int = 0  # FIGH, 0-3
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
    #: Unidentified: three four-bit columns (0-15) no screen shows, named
    #: after their column only (`PCBI`, `ODBI`, `SPBI`, read as "bias" from
    #: the shared `BI`). Retail, 864 NHL skaters, rank correlation with their
    #: own disc ratings: `PCBI` (mostly 4-9) -0.80 with skill (shooting,
    #: passing, deking, speed) and +0.42 with checking; `ODBI` (mostly 4-10)
    #: +0.38 with skill and -0.47 with checking; `SPBI` (mostly 4-12) +0.37
    #: with checking and aggression, about 0 with skill and with shooting
    #: over passing. NHL 2005's `PCBI` runs the other way (+0.84 with skill),
    #: so the two discs do not even agree on its direction. Tried: those
    #: correlations, the same against real 2005-06 stats
    #: (`stat_mapper.SKATER_SOURCES`), a web search for the column names
    #: (nothing); not tried: tracing the AI's reads, the EBOOT being
    #: encrypted. Written from the stats that rank the disc's own players
    #: best, on the disc's spread, so the patched players keep the disc's
    #: relation between them and the named ratings.
    pc_bias: int = 7  # PCBI
    offence_bias: int = 8  # ODBI
    shoot_bias: int = 8  # SPBI


@dataclass
class NHL07GoalieAttributes:
    """Goalie ratings, one SGAI field each (`GOALIE_FIELDS`), on the skaters'
    0-49 scale; `FIGH` holds 0-1 on the retail disc and the five style fields
    0-2.

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
    fighting: int = 0  # FIGH
    agility: int = 30  # AGIL
    five_hole: int = 30  # 5HOL
    passing: int = 30  # PASS
    endurance: int = 30  # ENDU
    glove_high: int = 30  # GSH_
    stick_high: int = 30  # SSH_
    glove_low: int = 30  # GSL_
    stick_low: int = 30  # SSL_
    positioning: int = 1  # POSI
    paddle_down: int = 1  # PADL
    style: int = 2  # STYL: 2 butterfly (Kiprusoff), 0 stand-up (Brodeur)
    flop: int = 1  # FLOP
    consistency: int = 2  # CONS


#: Every SPAI field of the retail disc but `INDX`, which keys the row to the
#: bio, and the attribute that holds it.
SKATER_FIELDS = {
    "balance": "BALA",
    "penalty": "PENA",
    "shot_accuracy": "SACC",
    "wrist_accuracy": "WACC",
    "faceoffs": "FACE",
    "acceleration": "ACCE",
    "speed": "SPEE",
    "potential": "POTE",
    "deking": "DEKG",
    "checking": "CHKG",
    "toughness": "TOUG",
    "fighting": "FIGH",
    "pc_bias": "PCBI",
    "offence_bias": "ODBI",
    "shoot_bias": "SPBI",
    "puck_control": "PUCK",
    "agility": "AGIL",
    "hero": "HERO",
    "aggression": "AGGR",
    "pressure": "PRES",
    "passing": "PASS",
    "endurance": "ENDU",
    "injury": "INJU",
    "slap_power": "SPOW",
    "wrist_power": "WPOW",
}
#: Every SGAI field of the retail disc but `INDX`, and its attribute. `SPEE`,
#: `POTE`, `TOUG`, `FIGH`, `AGIL`, `PASS` and `ENDU` are spelled as in SPAI and
#: are this table's own.
GOALIE_FIELDS = {
    "breakaway": "BRKA",
    "rebound_ctrl": "REBC",
    "shot_recovery": "SREC",
    "speed": "SPEE",
    "poke_check": "POKE",
    "intensity": "INTE",
    "potential": "POTE",
    "toughness": "TOUG",
    "fighting": "FIGH",
    "positioning": "POSI",
    "paddle_down": "PADL",
    "agility": "AGIL",
    "five_hole": "5HOL",
    "style": "STYL",
    "flop": "FLOP",
    "consistency": "CONS",
    "passing": "PASS",
    "endurance": "ENDU",
    "glove_high": "GSH_",
    "stick_high": "SSH_",
    "glove_low": "GSL_",
    "stick_low": "SSL_",
}


@dataclass
class NHL07PlayerRecord:
    """One player, reduced to what the three TDB tables take.

    Exactly one of `skater_attrs` and `goalie_attrs` is set, following
    `is_goalie`. Both default to `None` and must not default to a shared mutable
    instance.
    """

    first_name: str = ""
    last_name: str = ""
    position: str = "C"
    jersey_number: int = 1
    handedness: int = 1  # 0 = left, 1 = right
    weight: int = 190  # pounds; the writer stores pounds above WEIGHT_BASE_POUNDS
    height: int = 16  # encoded five-bit HEIG, inches above 54: 16 is 5'10"
    team_index: int = 0
    player_id: int = 0
    is_goalie: bool = False
    photo_url: str = ""  # the provider's head shot, for the portrait
    #: SPBT `SKIN`, the shade the game draws him in (`looks.SKIN_VALUES`);
    #: None leaves the bio record's own, which `looks.dress` never does.
    skin: int | None = None
    # Birth: `INTL` (flag), `STAT`, `CITY`, `YEAR`/`MONT`/`DAY_`, encoded as on
    # NHL 2005, whose codes this disc shares; -1 or "" leaves the disc's value.
    country: int = -1
    state: int = -1
    city: str = ""
    birth_year: int = -1
    birth_month: int = -1
    birth_day: int = -1
    #: `YEAD` (the draft year less 1959, `YEAD_NOT_AVAILABLE` for none), `POSD`
    #: (the overall pick) and `ROOK`; -1 leaves the disc's value.
    draft_year: int = -1
    draft_pick: int = -1
    rookie: int = -1
    #: His NHL seasons, for the career tables (`core.nhl_career`); not a field.
    seasons: list[dict[str, object]] = field(default_factory=list)
    skater_attrs: NHL07SkaterAttributes | None = None
    goalie_attrs: NHL07GoalieAttributes | None = None


@dataclass
class NHL07TeamSlot:
    """One team slot as the reader found it in STEA, or as a fallback name.

    `name` comes from STEA's `NAME` or `CITY` when present, else from
    `NHL07_TEAM_NAMES`. `index` is STEA's own `INDX` value, not the record's
    position: the two need not agree.
    """

    index: int
    name: str
    abbreviation: str


@dataclass
class NHL07RomInfo:
    """What the reader learned about one ISO.

    `size` is the ISO's size on disk, not `db.viv`'s.
    """

    path: str
    size: int = 0
    team_slots: list[NHL07TeamSlot] = field(default_factory=list)
    is_valid: bool = False
