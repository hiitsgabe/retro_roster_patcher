"""Turn provider roster and stat data into NHL 2005 records.

Every rating, the three AI biases and the goalie style fields included, ranks
the player's own stats against his group in the league fetched and takes the
value at that rank of the retail disc's own spread (`core.hockey_ratings`,
`SOURCES`, `SPREAD`). A rating his line has no stat for keeps his position's
default, the median of the disc's NHL players at that position.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from ...core.hockey import deployment
from ...core.hockey_ratings import League, Sources, Spread, age_of, rate
from ...sports.models import Player
from .models import (
    BIRTH_YEAR_BASE,
    CITY_CHARS,
    COUNTRY_CODES,
    GAME_YEAR,
    MODERN_NHL_TO_NHL05,
    NAME_FIELD_CHARS,
    STATE_CODES,
    YEAD_NOT_AVAILABLE,
    NHL05GoalieAttributes,
    NHL05PlayerRecord,
    NHL05SkaterAttributes,
)

# The most ROST rows the disc gives a club (they ship 30 to 40), and
# `select_roster`'s default size. `patcher.map_rosters` keeps the whole squad:
# a club writes as many as its own rows hold, and the rest become free agents.
MAX_PLAYERS = 40

# The shape `select_roster` builds towards: two goalies, four forward lines of
# three, two spare forwards, and seven defencemen.
GOALIES_PER_TEAM = 2
FORWARDS_PER_TEAM = 14
DEFENCEMEN_PER_TEAM = 7
FORWARD_LINES = 4

# Three even-strength pairs, `L1LD`/`L1RD` through `L3LD`/`L3RD`.
DEFENCE_PAIRS = 3

# Every other unit is drawn from the even-strength lines and pairs, as on the
# retail NHL 07 ROST, which has this game's sixty-four flags (no NHL 2005 disc
# was checked): unit flag -> the `L` flag whose holder also takes it. `P` power
# play, `K` penalty kill, `4` four-on-four, `3` five-on-three and `X` the extra
# attackers. Each unit matches this on 104 to 110 of NHL 07's 110 teams.
UNIT_ROLES = {
    **{f"P1{s}": f"L1{s}" for s in ("LW", "C_", "RW", "LD", "RD")},
    **{f"P2{s}": f"L2{s}" for s in ("LW", "C_", "RW", "LD", "RD")},
    "K1LW": "L3LW",
    "K1C_": "L3C_",
    "K1LD": "L3LD",
    "K1RD": "L3RD",
    "K2LW": "L4LW",
    "K2C_": "L4C_",
    "K2LD": "L1LD",
    "K2RD": "L1RD",
    "41LW": "L1LW",
    "41C_": "L1C_",
    "41LD": "L2LD",
    "41RD": "L2RD",
    "42LW": "L2LW",
    "42C_": "L2C_",
    "42LD": "L3LD",
    "42RD": "L3RD",
    "31C_": "L4C_",
    "31LD": "L2LD",
    "31RD": "L2RD",
    "32C_": "L2C_",
    "32LD": "L1LD",
    "32RD": "L1RD",
    "X1__": "L1LW",
    "X2__": "L2LW",
}

# `H1__`-`H5__` and `S1__`-`S5__` are numbered slots, filled from these roles
# in this order (retail: H on 110 of 110 teams, S on 102). A missing role is
# skipped and the ones after it move up a slot.
SLOT_ROLES = {
    "H": ("L1LW", "L1C_", "L1RW", "L1LD", "L1RD"),
    "S": ("L1LW", "L1RW", "L1C_", "L2LW", "L2RW"),
}

#: The stats each rating ranks (`core.hockey_ratings`), chosen on the retail
#: disc: each is the blend of the provider's stats that best ranks the disc's
#: own players by their 2003-04 seasons (rank correlation 0.3 to 0.9; NHL stats
#: API, 422 forwards, 228 defencemen, 55 goalies matched by name). `-` ranks the
#: other way. Several differ from NHL 07: potential is youth, injury is games
#: missed (higher is frailer), `PCBI` is high on playmakers and toughness is
#: penalties and fights. Aggression, `SPBI`, `PADL` and `FLOP` follow nothing
#: a provider counts (no stat ranks them above 0.25) and keep the defaults, as
#: does `STYL`: butterfly (2), as every goalie plays today. A goalie's ratings
#: follow his workload, wins and games, then save %; no provider counts saves
#: by zone, so the four corner zones rank wins and save %, each on its own
#: spread, and the five-hole games and wins.
SKATER_SOURCES: dict[str, tuple[str, ...]] = {
    "balance": ("points", "ice_time"),
    "penalty": ("pim",),
    "shot_accuracy": ("goals", "points"),
    "wrist_accuracy": ("goals", "points"),
    "faceoffs": ("faceoffs", "faceoff_pct"),
    "acceleration": ("points", "-size"),
    "speed": ("points", "-size"),
    "potential": ("-age",),
    "deking": ("points", "-size"),
    "checking": ("hits", "size", "pim"),
    "toughness": ("pim", "fights"),
    "fighting": ("fights",),
    "pc_bias": ("points", "assists"),
    "offence_bias": ("ppg", "points"),
    "puck_control": ("points", "assists"),
    "agility": ("points", "-size"),
    "hero": ("points", "ice_time"),
    "pressure": ("ice_time", "age"),
    "passing": ("assists",),
    "endurance": ("ice_time",),
    "injury": ("-games",),
    "slap_power": ("goals", "shots"),
    "wrist_power": ("goals", "shots"),
}
SOURCES: Sources = {
    "F": SKATER_SOURCES,
    "D": SKATER_SOURCES
    | {
        "shot_accuracy": ("points", "assists"),
        "wrist_accuracy": ("points", "assists"),
        "faceoffs": ("points",),
        "acceleration": ("points", "-height"),
        "speed": ("points", "-height"),
        "deking": ("points", "-height"),
        "agility": ("points", "-height"),
        "slap_power": ("points", "shots"),
        "wrist_power": ("points", "shots"),
    },
    "G": {
        "breakaway": ("save", "wins"),
        "rebound_ctrl": ("wins", "save"),
        "shot_recovery": ("wins", "games"),
        "speed": ("wins", "games"),
        "poke_check": ("wins", "games"),
        "intensity": ("wins", "save"),
        "potential": ("-age",),
        "toughness": ("pim",),
        "fighting": ("pim",),
        "positioning": ("wins", "games"),
        "agility": ("wins", "save"),
        "five_hole": ("games", "wins"),
        "consistency": ("save",),
        "passing": ("wins", "pim"),
        "endurance": ("games",),
        "glove_high": ("wins", "save"),
        "stick_high": ("wins", "save"),
        "glove_low": ("wins", "save"),
        "stick_low": ("wins", "save"),
    },
}

# fmt: off
#: The retail disc's 640 forwards, 332 defencemen, 89 goalies
#: on NHL rosters: each rating's values at 0%, 5%, ... 100% of them.
SPREAD: Spread = {
    "F": {
        "balance": (0,14,17,19,21,22,23,25,26,27,28,29,30,31,33,34,36,38,40,44,49),
        "penalty": (0,7,9,10,12,14,15,17,18,20,21,23,24,25,27,28,29,32,36,41,49),
        "shot_accuracy": (0,4,9,10,12,13,14,15,15,16,17,18,19,21,23,24,27,29,31,36,45),
        "wrist_accuracy": (0,6,11,14,15,17,18,19,19,20,21,22,23,25,26,29,31,33,36,41,49),
        "faceoffs": (0,0,1,5,7,8,9,9,10,11,12,13,15,17,19,21,24,27,31,37,49),
        "acceleration": (0,13,16,20,21,24,25,26,28,29,31,32,33,34,36,37,40,42,44,47,49),
        "speed": (0,12,18,20,22,23,24,26,28,30,31,32,33,35,36,37,39,41,42,46,49),
        "potential": (1,8,14,17,18,20,21,22,23,23,24,25,26,27,28,30,33,36,39,42,48),
        "deking": (0,8,12,16,17,19,21,22,23,24,26,27,28,30,32,33,35,38,39,43,49),
        "checking": (0,8,12,15,17,19,21,22,24,26,28,30,32,33,35,37,38,41,43,45,49),
        "toughness": (2,5,6,8,9,10,12,13,15,16,18,19,21,22,24,26,29,35,38,41,49),
        "fighting": (0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,1,2,3,3),
        "pc_bias": (0,2,3,5,5,6,6,6,7,7,7,7,8,8,9,9,10,11,12,13,15),
        "offence_bias": (0,3,4,4,5,5,6,6,7,8,8,9,9,10,10,11,11,12,12,13,15),
        "shoot_bias": (0,2,3,4,4,5,5,5,6,7,7,8,8,10,10,11,11,12,13,13,15),
        "puck_control": (0,5,11,14,16,17,18,19,20,22,23,24,26,28,30,31,33,35,39,41,49),
        "agility": (0,13,17,20,22,24,25,26,27,29,30,31,32,33,35,37,38,40,42,45,49),
        "hero": (0,4,9,12,14,15,16,17,18,20,21,23,24,26,28,30,32,33,36,40,49),
        "aggression": (0,7,9,12,14,15,17,18,19,20,22,23,25,26,28,29,31,33,35,38,49),
        "pressure": (0,5,7,9,10,12,13,14,15,17,19,20,22,24,25,28,29,32,35,39,49),
        "passing": (0,9,15,17,18,20,21,21,22,23,24,26,26,28,29,31,33,35,38,41,49),
        "endurance": (0,7,12,14,16,17,19,21,22,23,24,26,27,29,31,34,35,37,40,42,49),
        "injury": (0,0,1,2,3,4,7,9,11,14,17,20,25,25,25,25,25,29,32,38,49),
        "slap_power": (0,5,8,10,11,12,13,14,15,16,17,18,19,20,22,24,26,28,31,37,44),
        "wrist_power": (0,9,13,15,17,18,19,21,21,23,24,25,26,27,29,31,32,35,38,43,49),
    },
    "D": {
        "balance": (2,15,19,22,24,25,26,28,28,29,30,31,32,33,34,36,37,38,41,43,49),
        "penalty": (1,6,8,10,11,13,15,17,18,20,21,23,24,26,27,28,29,32,35,38,44),
        "shot_accuracy": (0,9,10,12,14,15,16,18,19,20,21,22,24,24,26,27,29,31,33,39,47),
        "wrist_accuracy": (0,3,4,5,5,6,6,6,7,7,8,8,8,9,9,10,10,11,13,15,34),
        "faceoffs": (0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,2,3,4,5,9),
        "acceleration": (4,14,16,19,20,21,23,25,26,27,29,30,31,32,33,35,36,37,38,41,49),
        "speed": (5,14,17,19,20,22,24,25,26,27,29,30,31,32,34,35,36,37,38,40,47),
        "potential": (2,9,12,15,17,18,20,22,22,23,24,25,25,26,27,29,30,32,34,40,49),
        "deking": (0,7,11,14,16,18,19,21,22,23,25,26,27,28,30,30,32,33,35,37,45),
        "checking": (5,14,17,20,23,25,26,28,29,30,32,33,35,36,37,38,40,41,42,45,49),
        "toughness": (2,7,9,11,13,14,16,18,19,21,22,24,25,26,28,31,34,36,38,39,47),
        "fighting": (0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,1,1,1,1,2,3),
        "pc_bias": (0,1,2,2,3,3,4,4,5,5,5,6,6,7,7,7,8,8,9,10,13),
        "offence_bias": (1,2,2,2,3,3,3,4,4,4,5,5,6,7,8,8,10,10,11,12,14),
        "shoot_bias": (1,4,5,5,6,6,6,7,7,7,8,9,9,10,10,11,11,12,12,13,15),
        "puck_control": (2,8,10,12,14,16,17,18,19,21,22,23,24,25,27,28,30,31,33,35,44),
        "agility": (5,15,18,20,22,23,25,26,27,28,29,30,32,32,33,34,35,37,39,41,49),
        "hero": (0,0,9,13,15,17,18,19,21,22,23,25,26,28,29,31,32,34,36,38,47),
        "aggression": (0,8,12,14,15,17,18,18,20,21,22,23,25,25,28,29,31,32,34,38,49),
        "pressure": (0,6,7,9,10,12,14,15,17,18,20,23,25,26,27,29,30,32,34,38,47),
        "passing": (0,13,16,18,19,21,22,23,24,26,27,28,28,29,31,32,33,36,38,40,46),
        "endurance": (0,9,14,16,19,21,22,24,25,26,29,30,32,33,35,37,39,42,45,47,49),
        "injury": (0,0,2,2,3,5,8,10,12,15,20,23,25,25,25,25,25,30,34,39,48),
        "slap_power": (0,12,13,16,18,20,21,23,25,25,27,28,30,32,33,35,38,38,41,44,49),
        "wrist_power": (3,8,10,12,14,15,16,18,18,19,21,21,22,24,25,26,28,29,32,36,46),
    },
    "G": {
        "breakaway": (12,15,18,20,22,24,25,25,26,26,27,28,29,30,32,32,33,33,35,37,41),
        "rebound_ctrl": (6,11,14,18,20,22,23,24,25,26,28,30,31,32,33,34,35,37,39,42,46),
        "shot_recovery": (0,6,11,14,17,20,22,24,25,26,28,31,32,33,35,35,38,42,44,47,49),
        "speed": (9,11,13,15,18,20,21,23,26,28,29,30,31,33,34,36,38,39,42,46,49),
        "poke_check": (0,11,13,14,15,15,17,19,20,22,23,24,26,27,28,29,31,35,38,43,47),
        "intensity": (0,8,12,13,17,18,19,20,22,24,25,27,28,29,30,32,33,38,40,44,49),
        "potential": (7,12,16,19,20,23,25,25,27,28,29,30,30,32,33,34,36,38,40,42,45),
        "toughness": (0,0,1,1,3,3,3,4,5,6,8,9,10,11,13,15,18,22,25,34,41),
        "fighting": (0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,1,1,1),
        "positioning": (0,0,0,1,1,1,1,1,1,1,1,1,1,1,2,2,2,2,2,2,2),
        "paddle_down": (0,0,0,0,0,0,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1),
        "agility": (11,15,17,18,19,20,24,26,28,30,33,33,33,34,36,37,39,42,43,46,49),
        "five_hole": (10,13,20,20,22,24,25,25,25,27,28,29,30,31,33,35,36,40,41,44,47),
        "style": (0,0,0,0,0,0,0,0,0,2,2,2,2,2,2,2,2,2,2,2,2),
        "flop": (0,0,0,0,0,0,1,1,1,1,1,1,1,1,1,1,1,2,2,2,2),
        "consistency": (0,0,1,1,1,1,1,1,1,2,2,2,2,2,2,2,2,2,2,2,2),
        "passing": (0,8,10,11,14,17,18,18,19,20,20,21,22,22,24,27,28,30,32,43,49),
        "endurance": (0,10,11,12,14,15,18,20,21,24,24,26,29,31,34,36,38,40,45,48,49),
        "glove_high": (11,15,19,21,25,26,26,28,30,31,31,32,34,35,36,37,39,40,42,45,47),
        "stick_high": (10,12,18,20,22,23,24,25,27,29,30,30,32,33,34,35,36,39,41,44,46),
        "glove_low": (10,13,19,21,24,25,27,29,30,31,31,33,34,35,36,37,38,40,41,42,45),
        "stick_low": (11,13,16,19,20,20,21,23,24,25,26,27,29,30,31,33,35,38,40,43,49),
    },
}
# fmt: on
#: What a skater without a stat line gets, and keeps for a rating his line has
#: no stat for: the median of the disc's NHL players at his position. Copied,
#: never handed out (`_defaults_for`).
SKATER_DEFAULTS = {
    "C": NHL05SkaterAttributes(
        balance=29,
        penalty=20,
        shot_accuracy=17,
        wrist_accuracy=21,
        faceoffs=23,
        acceleration=31,
        speed=32,
        potential=25,
        deking=27,
        checking=24,
        toughness=15,
        fighting=0,
        pc_bias=8,
        offence_bias=8,
        shoot_bias=10,
        puck_control=25,
        agility=31,
        hero=22,
        aggression=23,
        pressure=19,
        passing=26,
        endurance=27,
        injury=14,
        slap_power=16,
        wrist_power=23,
    ),
    "LW": NHL05SkaterAttributes(
        balance=27,
        penalty=21,
        shot_accuracy=16,
        wrist_accuracy=20,
        faceoffs=9,
        acceleration=29,
        speed=28,
        potential=24,
        deking=23,
        checking=30,
        toughness=19,
        fighting=0,
        pc_bias=7,
        offence_bias=7,
        shoot_bias=6,
        puck_control=22,
        agility=28,
        hero=20,
        aggression=20,
        pressure=17,
        passing=23,
        endurance=22,
        injury=20,
        slap_power=17,
        wrist_power=24,
    ),
    "RW": NHL05SkaterAttributes(
        balance=28,
        penalty=22,
        shot_accuracy=18,
        wrist_accuracy=21,
        faceoffs=9,
        acceleration=31,
        speed=32,
        potential=24,
        deking=26,
        checking=30,
        toughness=20,
        fighting=0,
        pc_bias=7,
        offence_bias=9,
        shoot_bias=5,
        puck_control=22,
        agility=30,
        hero=21,
        aggression=23,
        pressure=19,
        passing=23,
        endurance=23,
        injury=17,
        slap_power=18,
        wrist_power=25,
    ),
    "D": NHL05SkaterAttributes(
        balance=30,
        penalty=21,
        shot_accuracy=21,
        wrist_accuracy=8,
        faceoffs=0,
        acceleration=29,
        speed=29,
        potential=24,
        deking=25,
        checking=32,
        toughness=22,
        fighting=0,
        pc_bias=5,
        offence_bias=5,
        shoot_bias=8,
        puck_control=22,
        agility=29,
        hero=23,
        aggression=22,
        pressure=20,
        passing=27,
        endurance=28,
        injury=20,
        slap_power=27,
        wrist_power=21,
    ),
}
GOALIE_DEFAULTS = NHL05GoalieAttributes(
    breakaway=27,
    rebound_ctrl=28,
    shot_recovery=28,
    speed=29,
    poke_check=23,
    intensity=25,
    potential=29,
    toughness=8,
    fighting=0,
    positioning=1,
    paddle_down=1,
    agility=33,
    five_hole=28,
    style=2,
    flop=1,
    consistency=2,
    passing=20,
    endurance=24,
    glove_high=31,
    stick_high=30,
    glove_low=31,
    stick_low=26,
)

# The position `SKATER_DEFAULTS` falls back to: a player whose position string
# the provider spelled some other way gets a centre's faceoffs.
DEFAULT_POSITION = "C"

# `WEIG` is pounds over 150 and `HEIG` inches over 54, as on NHL 07 (checked on
# the retail disc: Chara 6'9", 260 lb is 27 and 110; Gionta 5'7", 175 lb is 13
# and 25). The defaults, 190 lb and 6'0", stand in when the provider has none.
WEIGHT_BASE = 150
HEIGHT_BASE = 54
DEFAULT_WEIGHT = 190 - WEIGHT_BASE
DEFAULT_HEIGHT = 72 - HEIGHT_BASE

# Default jersey number. 1 is a goalie's number and is handed to skaters too; the
# disc's own number is overwritten either way.
DEFAULT_JERSEY = 1

# `HAND`: 0 is left, 1 is right, and right is the default. `HAND` is always
# written, so a provider that reports nothing makes every player right-handed
# rather than leaving the disc's value.
HAND_LEFT = 0
HAND_RIGHT = 1


def set_birth(record: Any, player: Player, game_year: int = GAME_YEAR) -> None:
    """The record's birth fields: flag, place and a birthday that makes the
    game, counting from `game_year`, show the player's real age (from his birth
    date when the provider gives no age, as the NHL API does)."""
    country = COUNTRY_CODES.get(player.nationality.strip())
    if country is not None:
        record.country = country
        # A province or state when the game has it, else the country itself.
        record.state = STATE_CODES.get(player.birth_state.upper(), country)
    record.city = player.birth_city[:CITY_CHARS]
    if age := player.age or age_of(player):
        record.birth_year = max(0, min(63, game_year - int(age) - BIRTH_YEAR_BASE))
    parts = player.birth_date.split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        record.birth_month, record.birth_day = int(parts[1]), int(parts[2])


def set_career(
    record: Any,
    career: Mapping[str, object],
    season: int,
    not_available: int,
    game_year: int = GAME_YEAR,
) -> None:
    """The record's NHL seasons up to `season` (`core.nhl_career` writes them),
    his draft and whether he is a rookie.

    Seasons and draft move into the game's calendar, as his birth year does
    (`set_birth`): `season` becomes the one ending in `game_year`, the last the
    game has. Player Information leaves out every season after that one, from
    its list and its career totals alike (seen in PPSSPP on NHL 07's retail
    rows), and NHL 2005 reads a draft after 2004 as "N/A".

    `YEAD` is the draft year less 1959 and `POSD` the overall pick;
    `not_available` and 0 for a player who was not drafted or whose career
    carries no draft (a provider that does not send one). `ROOK` marks, on the
    NHL 2005 disc, the players whose first NHL season was the one before the
    game's and those yet to play (116 of the 155 it flags, against 53 of the
    906 it does not; NHL 07 carries the same flags two seasons stale): a
    player with no NHL game before `season`, when his career is known.
    """
    shift = game_year - 1 - season if season else 0
    listed = career.get("seasons")
    seasons = [
        dict(s)
        for s in (listed if isinstance(listed, list) else [])
        if isinstance(s, dict) and (not season or int(s.get("season") or 0) <= season)
    ]
    before = sum(
        int(s.get("GP") or 0)
        for s in seasons
        if not s.get("playoffs") and int(s.get("season") or 0) < season
    )
    record.rookie = int("draft_year" in career and before == 0)
    record.seasons = [s | {"season": int(s.get("season") or 0) + shift} for s in seasons]
    year = career.get("draft_year")
    drafted = isinstance(year, int) and year > 0
    record.draft_year = (
        max(0, int(year) + shift - BIRTH_YEAR_BASE)  # type: ignore[call-overload]
        if drafted
        else not_available
    )
    pick = career.get("draft_pick")
    record.draft_pick = int(pick) if drafted and isinstance(pick, int) else 0


def _defaults_for(position: str) -> NHL05SkaterAttributes:
    """A fresh copy of one position's defaults.

    Copied, not shared: the module-level instance must not reach a record, or one
    later mutation rewrites every player on every team.
    """
    base = SKATER_DEFAULTS.get(position, SKATER_DEFAULTS[DEFAULT_POSITION])
    return replace(base)


class NHL05StatMapper:
    """Provider data in, NHL 2005 records out. Holds no state between calls."""

    def map_player(
        self,
        player: Player,
        team_abbrev: str,
        stats: dict | None = None,
        league: League | None = None,
        career: Mapping[str, object] | None = None,
        season: int = 0,
    ) -> NHL05PlayerRecord:
        """Build one `NHL05PlayerRecord` from a provider `Player`.

        The name is split on the *first* space only, so everything after it goes
        to `LNME` and the tail past 15 characters is lost -- four characters
        shorter than NHL 07's field, so a name that survives there is cut here.

        `stats` empty or absent is not an error: it selects the position
        defaults, which is what a player with no games played gets. `league`
        ranks his stats; without one his line is ranked alone.
        `career` (`sports.nhl`'s, or one season built from his line) gives
        his seasons up to `season`, his draft and his rookie flag.
        """
        pos = player.position.upper() if player.position else DEFAULT_POSITION
        is_goalie = pos == "G"

        parts = (player.name or "").split(" ", 1)
        first_name = parts[0] if parts else ""
        last_name = parts[1] if len(parts) > 1 else ""

        jersey = player.number or DEFAULT_JERSEY

        hand = HAND_RIGHT
        if player.handedness == "L":
            hand = HAND_LEFT

        weight = (
            max(1, min(127, int(player.weight) - WEIGHT_BASE))
            if player.weight > 0
            else DEFAULT_WEIGHT
        )
        height = (
            max(1, min(31, int(player.height) - HEIGHT_BASE))
            if player.height > 0
            else DEFAULT_HEIGHT
        )

        record = NHL05PlayerRecord(
            first_name=first_name[:NAME_FIELD_CHARS],
            last_name=last_name[:NAME_FIELD_CHARS],
            position=pos,
            jersey_number=jersey,
            handedness=hand,
            weight=weight,
            height=height,
            team_index=MODERN_NHL_TO_NHL05.get(team_abbrev.upper(), 0),
            player_id=player.id if player.id else 0,
            is_goalie=is_goalie,
            photo_url=player.photo_url,
            nationality=player.nationality,
        )
        set_birth(record, player)
        if career is not None:
            set_career(record, career, season, YEAD_NOT_AVAILABLE)

        attrs: NHL05SkaterAttributes | NHL05GoalieAttributes
        if is_goalie:
            attrs = record.goalie_attrs = replace(GOALIE_DEFAULTS)
        else:
            attrs = record.skater_attrs = _defaults_for(pos)
        if stats:
            rate(
                attrs,
                player,
                stats,
                league or League.from_lines([(player, stats)], SOURCES),
                SPREAD,
            )
        return record

    def select_roster(
        self,
        players: list[Player],
        stats: dict | None = None,
        max_players: int = MAX_PLAYERS,
    ) -> list[Player]:
        """Pick and order a roster: goalies, then forwards, then defence.

        `generate_team_line_flags` and `patcher.patch` both read this order, so
        it is part of the contract. Within each group players sort by production
        -- points for a skater, save percentage for a goalie -- highest first.

        Build forwards line by line rather than by taking the top 14: best
        centre, best left wing and best right wing form line one, and so on for
        four lines, then the rest by points. Taking the top 14 outright would put
        four centres on line one.

        `sort_key` reads `stats` by the player's id as a *string*: both providers
        key their leaders payload that way.
        """
        stats = stats or {}

        def sort_key(p: Player) -> float:
            return deployment(stats.get(str(p.id), {}), p.position == "G")

        def by_position(code: str) -> list[Player]:
            return sorted([p for p in players if p.position == code], key=sort_key, reverse=True)

        centers = by_position("C")
        left_wings = by_position("LW")
        right_wings = by_position("RW")
        defensemen = by_position("D")
        goalies = by_position("G")

        forwards: list[Player] = []
        for i in range(FORWARD_LINES):
            for pool in (centers, left_wings, right_wings):
                if i < len(pool):
                    forwards.append(pool[i])

        # `id()` and not the player's own id: two `Player` objects for the same
        # person are two entries in `players` and each belongs in the pool once.
        used = {id(p) for p in forwards}
        extras = sorted(
            [p for p in centers + left_wings + right_wings if id(p) not in used],
            key=sort_key,
            reverse=True,
        )
        forwards.extend(extras)
        forwards = forwards[:FORWARDS_PER_TEAM]

        selected = goalies[:GOALIES_PER_TEAM] + forwards + defensemen[:DEFENCEMEN_PER_TEAM]

        # Anything left -- an unrecognised position, or a defenceman past the
        # seven -- fills the remaining slots, a third goalie first, then by ice
        # time.
        all_used = {id(p) for p in selected}
        leftover = sorted(
            [p for p in players if id(p) not in all_used],
            key=lambda p: (p.position == "G", sort_key(p)),
            reverse=True,
        )
        remaining = max_players - len(selected)
        if remaining > 0:
            selected.extend(leftover[:remaining])

        return selected[:max_players]

    def get_team_slot(self, team_abbrev: str) -> int | None:
        """The ROM slot for a modern team code, or None if the game has none."""
        return MODERN_NHL_TO_NHL05.get(team_abbrev.upper())

    def dressed(self, players: list[NHL05PlayerRecord]) -> list[bool]:
        """Who dresses, in list order: the first two goalies, twelve forwards and
        six defencemen. Everyone else is a scratch.
        """
        room = {"G": GOALIES_PER_TEAM, "F": FORWARD_LINES * 3, "D": DEFENCE_PAIRS * 2}
        out: list[bool] = []
        for p in players:
            group = "G" if p.is_goalie else "D" if p.position == "D" else "F"
            out.append(room[group] > 0)
            room[group] -= 1
        return out

    def generate_team_line_flags(
        self,
        players: list[NHL05PlayerRecord],
    ) -> list[dict[str, int]]:
        """One flag dict per player, in the order the players were given.

        Only the players `dressed` dresses are lined up, so a scratch is never
        on a line. Lines come from natural positions first and gaps are filled
        with spare centres. A winger is never moved to centre, so a team with no
        centres ices four lines with no centre.

        Every other unit is then copied from the lines and pairs, as
        `UNIT_ROLES` and `SLOT_ROLES` say, so a team short of defencemen gets a
        four-man `H` rather than a forward in a defenceman's slot.

        A player who reaches none of those groups gets an empty dict, which
        `rom_writer.roster_values` turns into all sixty-four flags zeroed.

        Defence pairs are the even-strength `L{pair}LD`/`L{pair}RD`, where the
        retail NHL 07 disc (same ROST schema) starts its first pair.
        """
        result: list[dict[str, int]] = [{} for _ in players]

        dressed = self.dressed(players)
        squad = [(i, p) for i, p in enumerate(players) if dressed[i]]
        goalies = [(i, p) for i, p in squad if p.is_goalie]
        centers = [(i, p) for i, p in squad if p.position == "C"]
        left_w = [(i, p) for i, p in squad if p.position == "LW"]
        right_w = [(i, p) for i, p in squad if p.position == "RW"]
        defense = [(i, p) for i, p in squad if p.position == "D"]

        for gi, (idx, _) in enumerate(goalies[:GOALIES_PER_TEAM]):
            result[idx][f"G{gi + 1}__"] = 1

        c_pool = list(centers)
        lw_pool = list(left_w)
        rw_pool = list(right_w)

        role: dict[str, int] = {}  # `L` flag -> its holder's index
        for line in range(FORWARD_LINES):
            line_num = line + 1

            if c_pool:
                role[f"L{line_num}C_"] = c_pool.pop(0)[0]

            for wing, pool in (("LW", lw_pool), ("RW", rw_pool)):
                source = pool if pool else c_pool
                if source:
                    role[f"L{line_num}{wing}"] = source.pop(0)[0]

        for di, (idx, _) in enumerate(defense[: DEFENCE_PAIRS * 2]):
            role[f"L{di // 2 + 1}{'LD' if di % 2 == 0 else 'RD'}"] = idx

        for flag, idx in role.items():
            result[idx][flag] = 1
        for flag, l_flag in UNIT_ROLES.items():
            if l_flag in role:
                result[role[l_flag]][flag] = 1
        for unit, l_flags in SLOT_ROLES.items():
            for k, idx in enumerate(role[f] for f in l_flags if f in role):
                result[idx][f"{unit}{k + 1}__"] = 1

        return result
