"""ESPN squads reduced to ISS 2000's 22-player records.

Each rating ranks the player's season against his position group in the league
fetched (`iss_snes.ranks`: counts per 90 minutes and rates, each pulled toward
the group's own) and takes the value at that rank of the cartridge's own
spread: for each group and each byte, `RETAIL` holds the 2nd, 10th, 50th, 90th
and 98th percentile of the 2,200 national-team players, so a league's median
player rates like a retail median and its best like retail's best. What each
ranks (`SOURCES`):

  determination  goals and goals per shot (the Japanese game's kettei-ryoku,
                 finishing)
  shoot          shots and the share on target (a keeper's goal kick: long
                 balls and their accuracy)
  curve          crosses, cross accuracy and free-kick goals
  speed, dash    no provider measures pace: youth
  stamina        minutes per appearance
  jump           height and headed goals (keepers: height and save rate)
  keep           fouls drawn, dribbles and dribble success
  defence        tackles won, interceptions, clearances and blocks (keepers:
                 save rate and clean sheets)
  passing        passes, pass accuracy and long-ball accuracy
  goalkeeping    keepers: save rate (saves over saves and goals conceded) and
                 clean sheets
  attack, cover  goals, assists and shots; interceptions, tackles, clearances

A rating none of whose stats the player has, and every rating of a player
without a stat line, is his group's median on the cartridge. So is byte 0x2C
(`keeper2`), which is not identified (`rom`).

The special techniques (`TECHNIQUES`, the 23 the Member screen lists) go the
same way: one with a source is given to as large a top of the group as holds
it on the cartridge, and the ones nothing measures (feint, long, loop,
overhead and volley shots, diving header, long throw, both penalty ones, trap)
to nobody. The roles word and the lateral lean are his role's (`ROLES`, `SIDE`),
and the foot the one he takes his shots with (`foot_of`).

The XI is the club's starting eleven when there is one, each starter on the
line he is given, in the cartridge's formation nearest the roster's own (a
fetch sets it to the lineup's folded shape); each line is ordered by the side
ESPN gives each player (L/R suffixes), and the writer then seats it on the
formation's places by role, side and depth (`writer.starting_order`). The
bench is the rest by starts and minutes, a second keeper first.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...core.kits import KitSpec
from ...core.shirt_numbers import shirt_numbers
from ...sports.espn import LINEUP_GROUPS
from ...sports.lineup import (
    lineup_role,
    nearest_shape,
    parse_shape,
    slot_side,
    team_formation,
)
from ...sports.models import Player, PlayerStats, TeamRoster
from ..iss_snes.ranks import Ranks, Spread, place
from ..we2002.stat_mapper import preferred_foot
from . import rom as R

GROUPS = ("GK", "DF", "MF", "FW")
_GROUP_OF = {"Goalkeeper": "GK", "Defender": "DF", "Midfielder": "MF", "Attacker": "FW"}
_POSITION_BITS = {"GK": R.GK, "DF": R.DF, "MF": R.MF, "FW": R.FW}
_LINE = {"GK": 0, "DF": 1, "MF": 2, "FW": 3}

#: Retail spread per group: byte -> (p2, p10, p50, p90, p98). Byte names as
#: `rom.ABILITY_NAMES`, then the keeper bytes and the two awareness bytes.
RETAIL: dict[str, dict[str, Spread]] = {
    "GK": dict(
        determination=(51, 56, 64, 76, 80),
        shoot=(71, 74, 79, 85, 88),
        curve=(51, 56, 65, 74, 79),
        speed=(50, 54, 62, 75, 85),
        dash=(53, 58, 66, 79, 90),
        stamina=(50, 57, 67, 78, 85),
        jump=(54, 64, 76, 89, 94),
        keep=(54, 58, 66, 78, 87),
        defence=(50, 53, 61, 74, 89),
        passing=(51, 56, 65, 76, 79),
        goalkeeping=(70, 74, 81, 89, 94),
        keeper2=(74, 74, 78, 81, 85),
        attack=(33, 39, 47, 60, 64),
        cover=(63, 67, 78, 88, 95),
    ),
    "DF": dict(
        determination=(51, 56, 65, 76, 83),
        shoot=(64, 67, 72, 79, 90),
        curve=(51, 57, 67, 79, 84),
        speed=(50, 55, 65, 87, 91),
        dash=(53, 59, 66, 78, 85),
        stamina=(50, 59, 69, 87, 92),
        jump=(53, 60, 70, 87, 91),
        keep=(53, 58, 67, 81, 90),
        defence=(57, 63, 73, 88, 93),
        passing=(52, 58, 66, 78, 85),
        goalkeeping=(0, 3, 8, 13, 17),
        keeper2=(14, 14, 17, 21, 21),
        attack=(33, 40, 54, 77, 83),
        cover=(63, 68, 78, 88, 94),
    ),
    "MF": dict(
        determination=(56, 61, 70, 86, 92),
        shoot=(65, 68, 73, 90, 94),
        curve=(56, 63, 71, 84, 90),
        speed=(50, 55, 67, 88, 93),
        dash=(54, 59, 68, 82, 89),
        stamina=(52, 60, 70, 89, 94),
        jump=(51, 58, 69, 85, 90),
        keep=(58, 63, 71, 89, 94),
        defence=(56, 62, 70, 79, 89),
        passing=(56, 62, 70, 88, 93),
        goalkeeping=(0, 4, 8, 13, 17),
        keeper2=(10, 14, 17, 21, 21),
        attack=(45, 58, 73, 85, 92),
        cover=(51, 58, 72, 84, 92),
    ),
    "FW": dict(
        determination=(57, 62, 72, 91, 95),
        shoot=(65, 68, 73, 92, 96),
        curve=(52, 56, 64, 77, 82),
        speed=(55, 60, 71, 89, 94),
        dash=(56, 63, 71, 86, 92),
        stamina=(50, 59, 69, 86, 89),
        jump=(51, 56, 69, 90, 94),
        keep=(58, 62, 71, 86, 91),
        defence=(50, 52, 61, 72, 76),
        passing=(51, 57, 65, 78, 88),
        goalkeeping=(0, 3, 8, 13, 17),
        keeper2=(10, 14, 17, 21, 21),
        attack=(61, 67, 77, 90, 94),
        cover=(33, 37, 48, 60, 65),
    ),
}

#: The stats each rating and technique ranks, by group (`iss_snes.ranks`).
_OUTFIELD: dict[str, tuple[str, ...]] = {
    "determination": ("goals", "finishing"),
    "shoot": ("shots", "shot_accuracy"),
    "curve": ("crosses", "cross_accuracy", "free_kick_goals"),
    "speed": ("youth",),
    "dash": ("youth",),
    "stamina": ("minutes_per_game",),
    "jump": ("height", "headed_goals"),
    "keep": ("fouls_drawn", "dribbles", "dribble_success"),
    "defence": ("tackles_won", "interceptions", "clearances", "blocks"),
    "passing": ("passes", "pass_accuracy", "long_ball_accuracy"),
    "attack": ("goals", "assists", "shots"),
    "cover": ("interceptions", "tackles", "clearances"),
    "dribble": ("dribbles", "fouls_drawn"),
    "through_pass": ("assists",),
    "middle_shot": ("shots",),
    "heading": ("headed_goals",),
    "sliding": ("tackles",),
    "post_play": ("headed_goals", "height", "weight"),
    "man_mark": ("tackles_won", "interceptions"),
    "intercept": ("interceptions",),
    "corner_kick": ("crosses", "cross_accuracy"),
    "free_kick": ("free_kick_goals",),
    "one_on_one": ("tackles_won",),
    "long_pass": ("long_balls", "long_ball_accuracy"),
}
SOURCES: dict[str, dict[str, tuple[str, ...]]] = {
    "GK": {
        "shoot": ("long_balls", "long_ball_accuracy"),
        "speed": ("youth",),
        "dash": ("youth",),
        "stamina": ("minutes_per_game",),
        "jump": ("height", "save_rate"),
        "defence": ("save_rate", "clean_sheets"),
        "passing": ("passes", "pass_accuracy"),
        "goalkeeping": ("save_rate", "clean_sheets"),
        "highball": ("height",),
        "one_on_one": ("save_rate",),
    },
    "DF": _OUTFIELD,
    "MF": _OUTFIELD,
    "FW": _OUTFIELD | {"one_on_one": ("finishing",)},
}

#: The cartridge's national-team players per group (a record's highest position bit, a keeper's
#: first), which `TECHNIQUES` counts its holders among.
RETAIL_PLAYERS = {"GK": 207, "DF": 511, "MF": 870, "FW": 612}
#: The special techniques, bits 0-22 of the record's first word in the Member screen's order:
#: name -> (bit, the cartridge's holders among its keepers, defenders, midfielders, forwards).
TECHNIQUES: dict[str, tuple[int, tuple[int, int, int, int]]] = {
    "dribble": (0, (1, 6, 83, 108)),
    "feint": (1, (2, 1, 19, 44)),
    "through_pass": (2, (1, 3, 47, 42)),
    "long_shot": (3, (0, 1, 5, 1)),
    "middle_shot": (4, (2, 4, 47, 95)),
    "loop_shot": (5, (0, 0, 0, 2)),
    "overhead": (6, (0, 0, 0, 2)),
    "volley": (7, (0, 0, 0, 5)),
    "heading": (8, (5, 31, 40, 82)),
    "diving_header": (9, (0, 0, 0, 5)),
    "sliding": (10, (0, 7, 7, 0)),
    "post_play": (11, (0, 0, 1, 8)),
    "man_mark": (12, (4, 34, 17, 0)),
    "intercept": (13, (0, 1, 6, 0)),
    "highball": (14, (25, 0, 0, 0)),
    "corner_kick": (15, (0, 0, 1, 3)),
    "free_kick": (16, (1, 0, 6, 7)),
    "long_throw": (17, (0, 0, 0, 0)),
    "one_on_one": (18, (14, 35, 41, 20)),
    "pk_kicker": (19, (0, 0, 0, 0)),
    "pk_keeper": (20, (1, 0, 0, 0)),
    "long_pass": (21, (0, 1, 13, 1)),
    "trap": (22, (0, 0, 1, 7)),
}

#: The roles word (record 0x08) of each soccer role: the value most of the cartridge's players
#: at the formation place of that role and side hold. Bits 0-9 are the roles he is at home in
#: (sweeper, centre back, stopper, side back, wing back, defensive and offensive half, winger,
#: centre forward, striker), bits 10-15 the left and right of defence, midfield and attack.
ROLES = {
    "GK": 0x0000, "CB": 0x0C06, "LB": 0x1418, "RB": 0x2818, "LWB": 0x1418, "RWB": 0x2818,
    "CDM": 0x3020, "CM": 0x3060, "CAM": 0x3040, "LM": 0x1010, "RM": 0x2010,
    "LW": 0x5090, "RW": 0xA090, "ST": 0xC300,
}  # fmt: skip
#: The same for a player without a role, by group.
GROUP_ROLES = {"GK": 0x0000, "DF": 0x0C06, "MF": 0x3060, "FW": 0xC300}
#: The lateral lean (record 0x31, 0 the team's left to 99 its right): the median of the
#: cartridge's starters on the left flank, in the middle and on the right flank.
SIDE = {"L": 43, "C": 54, "R": 60}

#: Formations on the cartridge: (defenders, midfielders, forwards) -> a team
#: whose formation row and id are copied.
FORMATION_DONORS: dict[tuple[int, int, int], int] = {
    (4, 4, 2): 1,
    (4, 5, 1): 20,
    (4, 3, 3): 52,
    (3, 5, 2): 2,
    (3, 6, 1): 0,
    (3, 4, 3): 37,
    (5, 3, 2): 22,
}

FOOT_RIGHT, FOOT_LEFT, FOOT_BOTH = 76, 20, 50

#: Where a group's extra starters play when its places are full, nearest
#: first (as `iss-snes`). Keepers never swap with outfield players.
_SPILL = {"DF": ("MF", "FW"), "MF": ("FW", "DF"), "FW": ("MF", "DF")}


@dataclass
class N64Player:
    name: str
    number: int = 0
    group: str = "MF"
    position_bits: int = R.MF
    ratings: dict[str, int] = field(default_factory=dict)
    foot: int = FOOT_RIGHT
    height_cm: int = 178
    build: int = 55
    #: (hair colour 0-15, skin 0-8, face 0-85); None keeps the slot's look.
    look: tuple[int, int, int] | None = None
    player_id: int = 0
    photo_url: str = ""
    #: His soccer role (`Player.role`, else the one his lineup code reads as), "" when he has
    #: none: the writer seats a line by it, and writes his roles word and lateral lean from it.
    role: str = ""
    #: The special techniques he has: bit set as `TECHNIQUES` numbers them.
    techniques: int = 0

    @property
    def roles(self) -> int:
        return ROLES.get(self.role, GROUP_ROLES[self.group])

    @property
    def side(self) -> int:
        return SIDE[slot_side(self.role) if self.role else "C"]


@dataclass
class N64Team:
    name: str
    short_name: str
    code: str = ""
    #: The league the club plays in, for its region's label on team select.
    league: str = ""
    #: A national side: never a league's, so never on a region labelled after one.
    national: bool = False
    #: A national side's slot on the cartridge (None: the cartridge lacks it). On that
    #: slot, while it still shows the nation's flag and kits, it keeps the cartridge's
    #: name, flag and kits; anywhere else it is written like a club, in those of its own.
    nation: int | None = None
    players: list[N64Player] = field(default_factory=list)
    #: Starting-XI line counts (DF, MF, FW); None keeps the slot's formation.
    formation: tuple[int, int, int] | None = None
    team_id: int = 0
    logo_url: str = ""
    colours: tuple[tuple[int, int, int], ...] = ()
    kit_home: KitSpec | None = None
    kit_away: KitSpec | None = None


def nearest_formation(shape: tuple[int, int, int]) -> tuple[int, int, int]:
    """The formation of `FORMATION_DONORS` nearest `shape`, by the rule of every game
    (`nearest_shape`): the smallest total difference in players per line, then the nearest number
    of defenders, then the first listed."""
    offered = {f"{d}-{m}-{f}": (d, m, f) for d, m, f in FORMATION_DONORS}
    return offered[nearest_shape("-".join(map(str, shape)), tuple(offered))]


def parse_formation(text: str) -> tuple[int, int, int] | None:
    """`4-2-3-1` as (defenders, midfielders, forwards): the last number is the
    forwards, the first the defenders, everything between midfield."""
    try:
        parts = [int(p) for p in text.split("-")]
    except ValueError:
        return None
    if len(parts) < 3 or sum(parts) != 10:
        return None
    return parts[0], sum(parts[1:-1]), parts[-1]


def short_player_name(player: Player, taken: set[str]) -> str:
    """Surname in 8 characters; an initial and surname when a teammate shares it."""
    last = (player.last_name or player.name.split(" ")[-1]).strip()
    if len(last) > 8 and " " in last:
        last = last.replace(" ", "")  # van de Ven -> vandeVen
    name = last
    if name.lower() in taken and player.first_name:
        name = f"{player.first_name[0]}.{last}"
    return name


def group_of(player: Player) -> str:
    return _GROUP_OF.get(player.position, "MF")


def foot_of(stats: PlayerStats | None) -> int:
    """The foot byte: `we2002`'s rule (`preferred_foot`) on the shots he took with each foot."""
    return (FOOT_RIGHT, FOOT_LEFT, FOOT_BOTH)[preferred_foot(stats)]


class N64StatMapper:
    """League-wide ranks, so one club's players are rated against the whole
    league, not their own squad."""

    def __init__(self) -> None:
        #: The line each starter of the last `select_22` plays in.
        self._lines: dict[int, int] = {}

    def league_ranks(self, rosters: list[TeamRoster]) -> dict[int, dict[str, float]]:
        """Player id -> rating or technique -> his rank (0-1) in his group, for those of
        `SOURCES` he has a stat of."""
        members = [(p, group_of(p), r.player_stats.get(p.id)) for r in rosters for p in r.players]
        ranks = Ranks(members, SOURCES)
        return {p.id: ranks.of(p, group, line) for p, group, line in members}

    def map_player(
        self,
        player: Player,
        ranks: dict[str, float],
        name: str,
        stats: PlayerStats | None = None,
    ) -> N64Player:
        group = group_of(player)
        q = RETAIL[group]
        ratings = {key: place(spread, ranks.get(key)) for key, spread in q.items()}
        techniques = 0
        for technique, (bit, holders) in TECHNIQUES.items():
            share = holders[GROUPS.index(group)] / RETAIL_PLAYERS[group]
            if ranks.get(technique, 0.0) > 1 - share:
                techniques |= 1 << bit
        height = round(player.height * 2.54) if player.height else 0
        height = min(max(height or {"GK": 188, "DF": 183, "MF": 177, "FW": 179}[group], 158), 199)
        build = 55
        if player.weight and player.height:
            kg = player.weight * 0.4536
            build = round((kg / max(height - 100, 1) - 0.697) / 0.0042)
            build = min(max(build, 20), 85)
        return N64Player(
            name=name,
            number=player.number or 0,
            group=group,
            position_bits=_POSITION_BITS[group],
            ratings=ratings,
            foot=foot_of(stats),
            height_cm=height,
            build=build,
            player_id=player.id,
            photo_url=player.photo_url,
            role=player.role,
            techniques=techniques,
        )

    def select_22(
        self, roster: TeamRoster, formation: tuple[int, int, int] | None = None
    ) -> tuple[list[Player], tuple[int, int, int] | None]:
        """The XI in formation order (keeper, defenders, midfielders, forwards,
        each line left to right), then the bench; and the formation.

        The same selection as `iss-snes` (`ISSStatMapper._select_best_15`),
        with the places of the club's formation (the nearest the cartridge has
        to the roster's own, else to the lineup's folded shape, else 4-4-2)
        instead of a fixed 4-4-2:
        every starter plays, on the line the lineup gives him (else the group
        his position code belongs to), a group with more starters than places
        sending the rest to the nearest group with room; open places go to the
        season's most frequent starters, in their group, then a neighbouring
        one, and only then to a player who never started. The bench is the
        backup keeper, then the rest the same way.
        """
        stats = roster.player_stats
        lineup = (roster.extra or {}).get("lineup") or {}
        listed = lineup.get("starters") or []
        recent = {int(e["id"]): (i, e) for i, e in enumerate(listed) if "id" in e}
        # The roster's own formation, else its lineup's shape (`team_formation`): an edited
        # formation is the one to fill the lines for. Data fetched before `shape` existed has only
        # the lineup's formation, which `parse_shape` folds.
        shape = parse_shape(team_formation(roster) or "")
        if shape is None and recent:
            shape = parse_shape(lineup.get("shape") or lineup.get("formation") or "")
        # `formation` (defenders, midfielders, forwards) when a game fixes the
        # formation of a slot (ISS Deluxe), else the cartridge's nearest.
        shape = formation or (nearest_formation(shape) if shape else (4, 4, 2))

        def group(p: Player) -> str:
            if p.id in recent:
                entry = recent[p.id][1]
                if entry.get("line") in GROUPS:  # the line the lineup gives him, when it does
                    return entry["line"]
                g = LINEUP_GROUPS.get(str(entry.get("position", "")).upper())
                if g in _GROUP_OF:
                    return _GROUP_OF[g]
            return group_of(p)

        def key(p: Player) -> tuple[int, int, int, int]:
            st = stats.get(p.id)
            return (
                recent[p.id][0] if p.id in recent else len(recent),
                -(st.lineups if st else 0),
                -(st.appearances if st else 0),
                -(st.minutes if st else 0),
            )

        def started(p: Player) -> bool:
            st = stats.get(p.id)
            return p.id in recent or bool(st and st.lineups)

        pool = sorted(roster.players, key=key)
        places = {"GK": 1, "DF": shape[0], "MF": shape[1], "FW": shape[2]}
        chosen: dict[str, list[Player]] = {g: [] for g in places}
        taken: set[int] = set()

        def fill(eligible, spill: bool) -> None:
            for p in pool:
                if p.id in taken or not eligible(p):
                    continue
                home = group(p)
                for target in (home, *(_SPILL.get(home, ()) if spill else ())):
                    if len(chosen[target]) < places[target]:
                        chosen[target].append(p)
                        taken.add(p.id)
                        break

        for eligible in (lambda p: p.id in recent, started, lambda p: True):
            fill(eligible, spill=False)
            fill(eligible, spill=True)

        def side(p: Player) -> int:
            pos = str(recent[p.id][1].get("position", "")) if p.id in recent else ""
            # Flanks (LB, LM, RW...) outside, a centre's -L/-R inside.
            if pos[:1] == "L":
                return -2
            if pos[:1] == "R":
                return 2
            return -1 if pos.endswith("-L") else 1 if pos.endswith("-R") else 0

        xi: list[Player] = []
        self._lines = {}
        for ln, g in enumerate(("GK", "DF", "MF", "FW")):
            for p in sorted(chosen[g], key=side):
                xi.append(p)
                self._lines[p.id] = ln
        rest = [p for p in pool if p.id not in taken]
        keepers = [p for p in rest if group(p) == "GK"][:1]
        bench = keepers + [p for p in rest if p not in keepers]
        return (xi + bench)[: R.PLAYERS_PER_TEAM], shape

    def given_22(
        self, players: list[Player], formation: str | None = None
    ) -> tuple[list[Player], tuple[int, int, int]]:
        """The caller's squad as given, its first 11 the XI: grouped into the
        game's lines (keeper, defence, midfield, attack, the caller's order
        kept inside each), in the formation those counts are nearest.

        With a `formation` that `parse_shape` reads (a four-line one is folded
        to three), the order is the team sheet instead: the keeper, then the
        outfield as given, whatever each player's position. The formation
        nearest it on the cartridge cuts the outfield into lines by index: its
        defenders first, then its midfielders, the rest up front."""
        # Exactly one keeper starts: the first in the list; a second keeper
        # in the first 11 makes way for the next outfielder.
        keepers = [p for p in players if group_of(p) == "GK"][:1]
        outfield = [p for p in players if group_of(p) != "GK"]
        xi_ids = {p.id for p in players[:11] if group_of(p) != "GK"}
        xi_out = [p for p in outfield if p.id in xi_ids]
        xi_out += [p for p in outfield if p.id not in xi_ids]
        # Eleven in all: a keeper listed below the first 11 takes the eleventh outfielder's place.
        xi_out = xi_out[: 11 - len(keepers)]
        wanted = parse_shape(formation or "")
        if wanted is None:
            xi = sorted(keepers + xi_out, key=lambda p: _LINE[group_of(p)])
            lines = [_LINE[group_of(p)] for p in xi]
            shape = nearest_formation((lines.count(1), lines.count(2), lines.count(3)))
        else:
            # Slot 1 is the goal: a squad without keepers puts its first player there.
            shape = nearest_formation(wanted)
            xi = keepers + xi_out
            lines = ([0] + [1] * shape[0] + [2] * shape[1] + [3] * shape[2])[: len(xi)]
        chosen = {p.id for p in xi}
        players = xi + [p for p in players if p.id not in chosen]
        self._lines = {p.id: ln for p, ln in zip(xi, lines, strict=True)}
        return (xi + list(players[11:]))[: R.PLAYERS_PER_TEAM], shape

    def map_team(
        self,
        roster: TeamRoster,
        ranks: dict[int, dict[str, float]],
        *,
        order_as_given: bool = False,
    ) -> N64Team:
        formation: tuple[int, int, int] | None  # as `select_22` is annotated
        # The roster's formation, else (fetched before formations were kept) its lineup's shape:
        # empty, or one `parse_shape` reads.
        picked = team_formation(roster) or ""
        if order_as_given and picked:
            # The roster's formation (the lineup's, or the one picked since) reads the given
            # order: the shortcut below would answer with the lineup's own shape and lines.
            squad, formation = self.given_22(list(roster.players), picked)
        elif order_as_given:
            auto, auto_shape = self.select_22(roster)
            given = list(roster.players)
            if {p.id for p in auto[:11]} == {p.id for p in given[:11]}:
                # The XI is the one `suggest_squad_order` gave: keep its
                # formation and lines, and the caller's bench order.
                xi = {p.id for p in auto[:11]}
                squad = (auto[:11] + [p for p in given if p.id not in xi])[: R.PLAYERS_PER_TEAM]
                formation = auto_shape
            else:
                squad, formation = self.given_22(given)
        else:
            squad, formation = self.select_22(roster)
        taken: set[str] = set()
        surnames = [(p.last_name or p.name.split(" ")[-1]).lower() for p in squad]
        players = []
        for p, surname in zip(squad, surnames, strict=False):
            shared = surnames.count(surname) > 1
            name = short_player_name(p, taken | ({surname} if shared else set()))
            taken.add(surname)
            mapped = self.map_player(p, ranks.get(p.id, {}), name, roster.player_stats.get(p.id))
            # A roster fetched before roles: his lineup code says what he plays.
            mapped.role = mapped.role or lineup_role(p.id, (roster.extra or {}).get("lineup"))
            played = self._lines.get(p.id)
            if played is not None:
                # The line he starts in, as well as his own: a winger ESPN calls
                # a forward who starts in midfield plays MF/FW.
                mapped.position_bits |= (R.GK, R.DF, R.MF, R.FW)[played]
            players.append(mapped)
        # Unique in 1-63, the 6-bit field; ESPN's number where it can be.
        numbers = shirt_numbers((each.number for each in players), 63)
        for each, number in zip(players, numbers, strict=True):
            each.number = number
        team = roster.team
        return N64Team(
            name=team.name,
            short_name=team.short_name or team.name,
            code=team.code,
            players=players,
            formation=formation,
            team_id=team.id,
            logo_url=team.logo_url,
        )
