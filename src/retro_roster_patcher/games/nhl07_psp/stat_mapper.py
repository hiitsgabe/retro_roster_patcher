"""Turn provider roster and stat data into NHL 07 records.

Every rating, the three AI biases and the goalie style fields included, ranks
the player's own stats against his group in the league fetched and takes the
value at that rank of the retail disc's own spread (`core.hockey_ratings`,
`SOURCES`, `SPREAD`). A rating his line has no stat for keeps his position's
default, the median of the disc's NHL players at that position.
"""

from __future__ import annotations

from dataclasses import replace

from ...core.hockey import deployment
from ...core.hockey_ratings import League, Sources, Spread, rate
from ...sports.models import Player
from ..nhl05_ps2.stat_mapper import set_birth
from .models import (
    MODERN_NHL_TO_NHL07,
    NAME_FIELD_CHARS,
    NHL07GoalieAttributes,
    NHL07PlayerRecord,
    NHL07SkaterAttributes,
)

# How many players a mapped roster holds. NHL 07 dresses 20 and carries
# scratches; 25 is what a team's ROST slots hold, and anything larger is
# truncated by the slot count in `patcher.patch`.
MAX_PLAYERS = 25

# The shape `select_roster` builds towards: two goalies, four forward lines of
# three, two spare forwards, and seven defencemen.
GOALIES_PER_TEAM = 2
FORWARDS_PER_TEAM = 14
DEFENCEMEN_PER_TEAM = 7
FORWARD_LINES = 4
DEFENCE_PAIRS = 3

# Every other unit is drawn from the even-strength lines and pairs, as on the
# retail NHL 07 ROST: unit flag -> the `L` flag whose holder also takes it.
# `P` power play, `K` penalty kill, `4` four-on-four, `3` five-on-three and `X`
# the extra attackers. Each unit matches this on 104 to 110 of the 110 teams.
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
#: own players by their 2005-06 seasons (rank correlation 0.4 to 0.9; NHL stats
#: API, 402 forwards, 224 defencemen, 62 goalies matched by name). `-` ranks the
#: other way. Shot accuracy and power are scoring more than shooting %; speed,
#: agility, acceleration and deking are points with lightness (a defenceman's
#: with shortness); potential is points, not youth; pressure is ice time with
#: age; injury is games played (higher is sturdier); the three four-bit biases:
#: `PCBI` is low on playmakers and high on hitters, `ODBI` high on power-play
#: scorers, `SPBI` high on shooters. A goalie's ratings follow his workload:
#: wins and games, then save %; potential is youth. No provider counts saves by
#: zone, so the five zones rank wins, save % and age, each on its own spread.
SKATER_SOURCES: dict[str, tuple[str, ...]] = {
    "balance": ("points", "ice_time"),
    "penalty": ("pim",),
    "shot_accuracy": ("goals", "points"),
    "wrist_accuracy": ("goals", "points"),
    "faceoffs": ("faceoffs", "faceoff_pct"),
    "acceleration": ("points", "-size"),
    "speed": ("points", "-size"),
    "potential": ("points",),
    "deking": ("points", "-size"),
    "checking": ("hits", "size", "pim"),
    "toughness": ("pim", "hits", "age"),
    "fighting": ("fights",),
    "pc_bias": ("-assists", "hits"),
    "offence_bias": ("ppg", "points"),
    "shoot_bias": ("shots",),
    "puck_control": ("points", "assists"),
    "agility": ("points", "-size"),
    "hero": ("points", "ice_time"),
    "aggression": ("pim",),
    "pressure": ("ice_time", "age"),
    "passing": ("assists",),
    "endurance": ("ice_time",),
    "injury": ("games",),
    "slap_power": ("goals", "shots"),
    "wrist_power": ("goals", "shots"),
}
SOURCES: Sources = {
    "F": SKATER_SOURCES,
    "D": SKATER_SOURCES
    | {
        "shot_accuracy": ("assists", "points"),
        "wrist_accuracy": ("assists", "points"),
        "faceoffs": ("points",),
        "acceleration": ("points", "-height"),
        "speed": ("points", "-height"),
        "deking": ("points", "-height"),
        "agility": ("points", "-height"),
        "pc_bias": ("-assists", "size"),
        "slap_power": ("points", "shots"),
        "wrist_power": ("points", "shots"),
    },
    # `STYL` and `FLOP` follow nothing a provider counts and keep the defaults:
    # butterfly (2), as every goalie plays today, and the disc's usual 1.
    "G": {
        "breakaway": ("save", "wins"),
        "rebound_ctrl": ("games", "wins"),
        "shot_recovery": ("games", "save"),
        "speed": ("games", "save"),
        "poke_check": ("wins",),
        "intensity": ("games", "wins"),
        "potential": ("-age", "save"),
        "toughness": ("pim",),
        "fighting": ("pim",),
        "positioning": ("games", "age"),
        "paddle_down": ("age",),
        "agility": ("wins", "save"),
        "five_hole": ("wins", "save", "age"),
        "consistency": ("save", "shutouts"),
        "passing": ("assists", "wins"),
        "endurance": ("games",),
        "glove_high": ("wins", "save", "age"),
        "stick_high": ("wins", "save", "age"),
        "glove_low": ("wins", "save", "age"),
        "stick_low": ("wins", "save", "age"),
    },
}

# fmt: off
#: The retail disc's 566 forwards, 298 defencemen, 92 goalies
#: on NHL rosters: each rating's values at 0%, 5%, ... 100% of them.
SPREAD: Spread = {
    "F": {
        "balance": (16,26,28,29,31,31,32,32,34,35,36,36,37,38,39,40,41,43,44,47,49),
        "penalty": (5,9,11,12,13,13,13,13,14,15,16,17,17,18,19,19,21,21,25,27,47),
        "shot_accuracy": (5,15,18,20,21,22,23,25,25,26,26,27,29,30,31,32,34,36,38,44,49),
        "wrist_accuracy": (5,11,15,18,20,21,22,23,24,25,26,26,27,29,30,31,34,35,39,44,49),
        "faceoffs": (9,17,22,25,25,25,25,26,27,27,28,29,31,33,35,36,38,40,41,45,49),
        "acceleration": (15,24,26,29,31,31,32,33,35,36,36,37,38,39,40,42,43,44,45,47,49),
        "speed": (13,24,26,28,29,31,31,32,34,35,36,37,37,38,39,40,42,43,45,47,49),
        "potential": (12,20,22,23,25,25,26,27,28,29,31,31,33,34,36,37,39,41,43,47,49),
        "deking": (5,14,19,21,23,25,26,27,28,30,31,32,34,36,37,37,38,41,42,46,49),
        "checking": (8,16,20,23,25,27,29,31,31,33,34,36,37,38,40,42,42,43,44,47,49),
        "toughness": (9,21,23,25,27,29,31,32,33,34,36,37,38,40,41,42,43,44,45,47,49),
        "fighting": (0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,1,3),
        "pc_bias": (0,1,2,2,3,4,4,4,5,5,6,6,7,7,7,7,8,9,10,11,13),
        "offence_bias": (4,6,7,7,7,8,8,8,8,9,9,9,9,10,10,10,10,11,12,12,15),
        "shoot_bias": (0,2,3,4,5,6,6,7,8,8,9,9,9,10,10,11,12,12,13,13,15),
        "puck_control": (7,16,20,23,25,26,27,28,30,31,32,33,34,35,37,38,39,41,43,47,49),
        "agility": (12,21,25,29,29,30,31,32,33,35,36,36,37,38,40,40,41,42,44,46,49),
        "hero": (5,8,10,11,13,14,15,17,19,20,22,24,25,27,28,30,32,35,38,43,49),
        "aggression": (8,11,13,14,16,17,19,19,21,22,24,25,26,27,29,31,33,36,39,45,49),
        "pressure": (2,18,20,22,23,24,26,27,28,29,31,32,33,34,36,38,39,40,42,46,49),
        "passing": (4,14,18,19,21,23,24,25,26,27,28,30,31,33,34,36,37,40,42,44,48),
        "endurance": (16,25,28,30,31,31,31,32,33,34,35,36,37,38,39,40,41,43,45,48,49),
        "injury": (7,24,28,30,31,33,34,35,36,36,37,38,39,40,41,42,43,45,47,47,49),
        "slap_power": (7,17,20,21,23,24,25,26,27,28,29,30,31,32,33,35,36,38,40,44,49),
        "wrist_power": (8,17,20,22,24,25,25,26,27,28,30,31,32,33,34,36,37,40,41,45,49),
    },
    "D": {
        "balance": (12,26,30,30,31,32,32,34,35,36,37,37,38,39,41,41,42,43,45,46,49),
        "penalty": (5,11,12,13,13,13,14,14,15,15,16,17,18,19,20,21,22,22,24,26,32),
        "shot_accuracy": (5,14,16,18,20,21,23,24,25,27,27,28,30,31,32,33,34,36,39,43,49),
        "wrist_accuracy": (5,5,5,7,8,8,9,10,10,11,12,12,13,14,14,16,17,18,20,24,47),
        "faceoffs": (5,8,10,10,12,13,13,14,15,16,18,19,20,21,22,23,24,25,26,29,36),
        "acceleration": (12,23,25,28,30,31,31,32,33,34,36,36,37,38,39,40,41,42,44,46,48),
        "speed": (9,21,25,26,29,29,31,32,33,34,35,36,37,38,39,40,41,41,43,44,49),
        "potential": (10,21,22,24,25,26,27,29,30,31,32,32,34,35,37,37,39,41,42,46,49),
        "deking": (5,13,17,20,22,23,25,26,27,28,30,32,32,34,35,37,37,38,40,43,49),
        "checking": (15,23,27,29,31,32,33,35,36,37,38,39,40,41,42,43,43,44,45,47,49),
        "toughness": (14,23,25,26,29,31,32,33,34,36,37,38,39,40,41,41,42,43,45,47,49),
        "fighting": (0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,2),
        "pc_bias": (0,3,4,5,5,6,6,6,7,7,8,8,8,9,10,10,11,12,12,13,15),
        "offence_bias": (0,4,5,6,6,6,6,7,7,7,8,8,8,8,9,9,9,10,10,11,15),
        "shoot_bias": (1,3,4,5,6,7,7,8,8,9,9,10,10,10,11,11,12,12,12,13,15),
        "puck_control": (5,17,20,22,24,26,27,29,30,31,31,32,33,34,36,37,37,39,41,42,49),
        "agility": (14,23,25,26,29,30,31,32,32,34,35,36,37,37,38,40,41,42,43,44,49),
        "hero": (5,9,13,14,16,17,18,19,21,23,24,25,27,29,30,32,33,35,37,41,49),
        "aggression": (8,16,19,21,23,24,25,26,26,28,29,30,31,32,32,34,35,37,39,42,48),
        "pressure": (9,19,21,23,25,27,28,30,31,32,33,34,35,36,38,39,40,41,42,46,49),
        "passing": (4,16,19,20,23,25,27,28,29,31,31,32,33,34,36,37,38,40,41,44,45),
        "endurance": (14,30,31,34,35,36,37,38,39,41,42,43,45,45,47,47,48,48,48,49,49),
        "injury": (9,24,26,29,31,32,33,35,36,36,37,38,39,40,41,42,43,45,46,48,49),
        "slap_power": (7,16,19,23,25,26,29,30,31,32,33,35,36,37,38,40,41,42,45,47,49),
        "wrist_power": (5,13,15,17,19,21,23,24,25,26,27,28,29,30,31,32,33,36,38,41,49),
    },
    "G": {
        "breakaway": (10,13,16,18,20,22,24,25,26,28,29,29,32,32,32,33,35,36,37,41,43),
        "rebound_ctrl": (7,13,18,20,20,21,22,25,25,27,30,31,33,33,35,36,37,38,40,41,46),
        "shot_recovery": (6,15,16,18,20,22,24,27,29,31,33,34,35,36,37,37,38,40,41,44,49),
        "speed": (12,18,20,22,23,25,27,28,30,31,33,34,35,36,36,38,38,39,40,42,49),
        "poke_check": (9,12,14,16,17,21,22,23,24,26,27,27,28,30,31,34,35,37,38,39,45),
        "intensity": (0,5,9,12,16,17,18,22,23,25,26,29,30,31,32,33,35,36,41,42,46),
        "potential": (14,17,19,22,23,25,26,28,29,30,31,32,32,35,36,38,39,40,40,42,47),
        "toughness": (0,1,1,3,4,4,5,6,8,10,10,11,12,15,19,20,21,23,27,32,43),
        "fighting": (0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,1,1),
        "positioning": (0,0,0,0,1,1,1,1,1,1,1,1,1,1,1,2,2,2,2,2,2),
        "paddle_down": (0,0,0,0,0,0,0,1,1,1,1,1,1,1,1,1,1,1,1,1,1),
        "agility": (16,18,20,22,23,26,28,30,31,32,33,34,35,36,38,39,40,41,42,44,49),
        "five_hole": (9,15,20,22,24,25,26,27,28,30,31,31,33,33,34,35,36,37,41,43,45),
        "style": (0,0,0,0,0,0,0,2,2,2,2,2,2,2,2,2,2,2,2,2,2),
        "flop": (0,0,0,0,0,0,1,1,1,1,1,1,1,1,1,1,1,2,2,2,2),
        "consistency": (0,1,1,1,1,1,1,1,2,2,2,2,2,2,2,2,2,2,2,2,2),
        "passing": (5,11,11,14,17,19,20,21,22,24,24,25,28,28,30,30,31,33,37,39,49),
        "endurance": (6,11,13,17,18,21,21,25,27,28,30,31,33,35,37,37,38,40,43,45,49),
        "glove_high": (8,16,18,21,22,25,26,28,30,31,32,33,33,35,36,37,38,39,41,43,48),
        "stick_high": (10,13,18,20,21,23,25,26,28,29,29,31,32,33,34,37,37,39,40,44,48),
        "glove_low": (9,14,18,23,24,26,27,29,30,31,32,33,34,35,36,37,38,39,42,44,46),
        "stick_low": (3,15,19,20,21,23,24,25,26,28,29,29,31,32,33,34,36,38,39,42,48),
    },
}
# fmt: on
#: What a skater without a stat line gets, and keeps for a rating his line has
#: no stat for: the median of the disc's NHL players at his position. Copied,
#: never handed out (`_defaults_for`).
SKATER_DEFAULTS = {
    "C": NHL07SkaterAttributes(
        balance=36,
        penalty=15,
        shot_accuracy=26,
        wrist_accuracy=26,
        faceoffs=36,
        acceleration=37,
        speed=36,
        potential=31,
        deking=31,
        checking=32,
        toughness=34,
        fighting=0,
        pc_bias=5,
        offence_bias=9,
        shoot_bias=7,
        puck_control=33,
        agility=36,
        hero=23,
        aggression=22,
        pressure=30,
        passing=31,
        endurance=36,
        injury=37,
        slap_power=29,
        wrist_power=30,
    ),
    "LW": NHL07SkaterAttributes(
        balance=36,
        penalty=16,
        shot_accuracy=26,
        wrist_accuracy=25,
        faceoffs=26,
        acceleration=36,
        speed=35,
        potential=28,
        deking=30,
        checking=38,
        toughness=38,
        fighting=0,
        pc_bias=6,
        offence_bias=9,
        shoot_bias=10,
        puck_control=30,
        agility=35,
        hero=21,
        aggression=25,
        pressure=31,
        passing=26,
        endurance=34,
        injury=36,
        slap_power=28,
        wrist_power=29,
    ),
    "RW": NHL07SkaterAttributes(
        balance=36,
        penalty=17,
        shot_accuracy=27,
        wrist_accuracy=26,
        faceoffs=27,
        acceleration=36,
        speed=36,
        potential=30,
        deking=32,
        checking=35,
        toughness=36,
        fighting=0,
        pc_bias=6,
        offence_bias=9,
        shoot_bias=9,
        puck_control=31,
        agility=36,
        hero=22,
        aggression=24,
        pressure=30,
        passing=28,
        endurance=35,
        injury=37,
        slap_power=30,
        wrist_power=30,
    ),
    "D": NHL07SkaterAttributes(
        balance=37,
        penalty=16,
        shot_accuracy=27,
        wrist_accuracy=12,
        faceoffs=18,
        acceleration=36,
        speed=35,
        potential=32,
        deking=30,
        checking=38,
        toughness=37,
        fighting=0,
        pc_bias=8,
        offence_bias=8,
        shoot_bias=9,
        puck_control=31,
        agility=35,
        hero=24,
        aggression=29,
        pressure=33,
        passing=31,
        endurance=42,
        injury=37,
        slap_power=33,
        wrist_power=27,
    ),
}
GOALIE_DEFAULTS = NHL07GoalieAttributes(
    breakaway=29,
    rebound_ctrl=30,
    shot_recovery=33,
    speed=33,
    poke_check=26,
    intensity=26,
    potential=31,
    toughness=10,
    fighting=0,
    positioning=1,
    paddle_down=1,
    agility=33,
    five_hole=31,
    style=2,
    flop=1,
    consistency=2,
    passing=24,
    endurance=30,
    glove_high=32,
    stick_high=29,
    glove_low=31,
    stick_low=29,
)

# The position `SKATER_DEFAULTS` falls back to: a player whose position string
# the provider spelled some other way gets a centre's faceoffs.
DEFAULT_POSITION = "C"

# The season the game counts ages from (`nhl05_ps2.stat_mapper.set_birth`,
# whose birth codes this disc shares).
GAME_YEAR = 2006

# Default weight in pounds, about the 2006 league average. The writer stores
# it as `WEIG`, pounds above `WEIGHT_BASE_POUNDS`.
DEFAULT_WEIGHT = 190

# The encoded `HEIG` a player gets when the provider reports no height, 5'10".
DEFAULT_HEIGHT = 16

# `HEIG` is five bits and encodes inches above `HEIGHT_BASE_INCHES`, so the
# scale runs 4'6" to 7'1". Checked on the retail disc: Chara is 27 (6'9") and
# Gionta 13 (5'7").
HEIGHT_BASE_INCHES = 54
HEIGHT_MAX = 31

# Default jersey number. 1 is a goalie's number and is handed to skaters too; the
# disc's own number is overwritten either way.
DEFAULT_JERSEY = 1

# `HAND`: 0 is left, 1 is right, and right is the default. `HAND` is always
# written, so a provider that reports nothing makes every player right-handed
# rather than leaving the disc's value.
HAND_LEFT = 0
HAND_RIGHT = 1


def _defaults_for(position: str) -> NHL07SkaterAttributes:
    """A fresh copy of one position's defaults.

    Copied, not shared: the module-level instance must not reach a record, or one
    later mutation rewrites every player on every team.
    """
    base = SKATER_DEFAULTS.get(position, SKATER_DEFAULTS[DEFAULT_POSITION])
    return replace(base)


class NHL07StatMapper:
    """Provider data in, NHL 07 records out. Holds no state between calls."""

    def map_player(
        self,
        player: Player,
        team_abbrev: str,
        stats: dict | None = None,
        league: League | None = None,
    ) -> NHL07PlayerRecord:
        """Build one `NHL07PlayerRecord` from a provider `Player`.

        The name is split on the *first* space only, so everything after it goes
        to `LNME` and the tail past 19 characters is lost.

        `stats` empty or absent is not an error: it selects the position
        defaults, which is what a player with no games played gets. `league`
        ranks his stats; without one his line is ranked alone.
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

        weight = int(player.weight) if player.weight > 0 else DEFAULT_WEIGHT

        height = DEFAULT_HEIGHT
        if player.height > 0:
            height = max(0, min(HEIGHT_MAX, round(player.height) - HEIGHT_BASE_INCHES))

        record = NHL07PlayerRecord(
            first_name=first_name[:NAME_FIELD_CHARS],
            last_name=last_name[:NAME_FIELD_CHARS],
            position=pos,
            jersey_number=jersey,
            handedness=hand,
            weight=weight,
            height=height,
            team_index=MODERN_NHL_TO_NHL07.get(team_abbrev.upper(), 0),
            player_id=player.id if player.id else 0,
            is_goalie=is_goalie,
            photo_url=player.photo_url or "",
        )
        set_birth(record, player, GAME_YEAR)

        attrs: NHL07SkaterAttributes | NHL07GoalieAttributes
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
        return MODERN_NHL_TO_NHL07.get(team_abbrev.upper())

    def dressed(self, players: list[NHL07PlayerRecord]) -> list[bool]:
        """Who dresses, in list order: the first two goalies, twelve forwards and
        six defencemen. Everyone else is a scratch, as on the retail disc.
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
        players: list[NHL07PlayerRecord],
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
        retail disc starts its first pair (Chara/Leetch for Boston).
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
