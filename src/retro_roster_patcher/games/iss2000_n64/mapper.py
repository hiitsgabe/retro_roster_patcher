"""ESPN squads reduced to ISS 2000's 22-player records.

Ratings are league percentiles placed onto the cartridge's own spread: for
each position group and each byte, `RETAIL` holds the 2nd, 10th, 50th, 90th
and 98th percentile of the 2,200 national-team players, so a league's median
player rates like a retail median and its best like retail's best. What feeds
each percentile:

  determination  minutes per appearance and share of starts
  shoot          goals and shots on target per 90 (keepers: pass accuracy)
  curve          assists and passes per 90
  speed, dash    no pace data anywhere: age (younger is quicker) per position
  stamina        minutes per appearance
  jump           height
  keep           fouls drawn and dribbles per 90
  defence        tackles, interceptions and blocks per 90
  passing        passes per 90 times pass accuracy
  goalkeeping    keepers: share of starts (the first-choice keeper rates best)

A player without statistics gets his group's median.

The XI is the club's last ESPN lineup when there is one, in a formation the
cartridge has (the nearest in defenders, midfielders and forwards); each line
is ordered by the side ESPN gives each player (L/R suffixes). The bench is the
rest by starts and minutes, a second keeper first.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import date

from ...core.kits import KitSpec
from ...sports.espn import LINEUP_GROUPS
from ...sports.models import Player, PlayerStats, TeamRoster
from . import rom as R

GROUPS = ("GK", "DF", "MF", "FW")
_GROUP_OF = {"Goalkeeper": "GK", "Defender": "DF", "Midfielder": "MF", "Attacker": "FW"}
_POSITION_BITS = {"GK": R.GK, "DF": R.DF, "MF": R.MF, "FW": R.FW}
_LINE = {"GK": 0, "DF": 1, "MF": 2, "FW": 3}

#: Retail spread per group: byte -> (p2, p10, p50, p90, p98). Byte names as
#: `rom.ABILITY_NAMES`, then the keeper bytes and the two awareness bytes.
_Q = tuple[int, int, int, int, int]
RETAIL: dict[str, dict[str, _Q]] = {
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
_PCTS = (2.0, 10.0, 50.0, 90.0, 98.0)

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


@dataclass
class N64Team:
    name: str
    short_name: str
    code: str = ""
    #: The league the club plays in, for its region's label on team select.
    league: str = ""
    #: A national side: it keeps the cartridge's name, flag and region label.
    national: bool = False
    players: list[N64Player] = field(default_factory=list)
    #: Starting-XI line counts (DF, MF, FW); None keeps the slot's formation.
    formation: tuple[int, int, int] | None = None
    team_id: int = 0
    logo_url: str = ""
    colours: tuple[tuple[int, int, int], ...] = ()
    kit_home: KitSpec | None = None
    kit_away: KitSpec | None = None


def nearest_formation(shape: tuple[int, int, int]) -> tuple[int, int, int]:
    def distance(f: tuple[int, int, int]) -> tuple[int, tuple[int, int, int]]:
        return sum(abs(a - b) for a, b in zip(f, shape, strict=True)), f

    return min(FORMATION_DONORS, key=distance)


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


def _rank(values: dict[int, float]) -> dict[int, float]:
    ordered = sorted(values.values())
    n = len(ordered)
    out = {}
    for pid, v in values.items():
        lo = bisect_left(ordered, v)
        hi = bisect_right(ordered, v)
        out[pid] = (lo + (hi - lo - 1) / 2) / max(n - 1, 1) * 100
    return out


def _place(q: _Q, pct: float) -> int:
    """Percentile `pct` (0-100) on the retail spread `q`, interpolated."""
    if pct <= _PCTS[0]:
        return q[0]
    for k in range(1, 5):
        if pct <= _PCTS[k]:
            a, b = _PCTS[k - 1], _PCTS[k]
            return round(q[k - 1] + (q[k] - q[k - 1]) * (pct - a) / (b - a))
    return q[4]


def group_of(player: Player) -> str:
    return _GROUP_OF.get(player.position, "MF")


def _per90(stat: float, s: PlayerStats) -> float:
    return stat * 90 / max(s.minutes, 90)


#: Raw scores feeding each rating, from a player's season line.
_FEEDS = {
    "determination": lambda s: (
        s.minutes / max(s.appearances, 1) + 30 * s.lineups / max(s.appearances, 1)
    ),
    "shoot": lambda s: _per90(s.goals * 3 + s.shots_on, s),
    "curve": lambda s: _per90(s.assists * 10, s) + _per90(s.passes_total, s) / 20,
    "stamina": lambda s: s.minutes / max(s.appearances, 1),
    "keep": lambda s: _per90(s.fouls_drawn + s.dribbles_success, s),
    "defence": lambda s: _per90(s.tackles_total + s.interceptions + s.blocks, s),
    "passing": lambda s: _per90(s.passes_total, s) * s.passes_accuracy / 100,
    "attack": lambda s: _per90(s.goals + s.assists, s),
    "cover": lambda s: _per90(s.tackles_total + s.interceptions, s),
    "goalkeeping": lambda s: s.lineups + s.minutes / 90,
}


def _age(player: Player, today: date) -> int:
    if player.age:
        return player.age
    try:
        born = date.fromisoformat(player.birth_date)
    except ValueError:
        return 26
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


class N64StatMapper:
    """League-wide percentiles, so one club's players are rated against the
    whole league, not their own squad."""

    def __init__(self, today: date | None = None) -> None:
        self.today = today or date.today()
        #: The line each starter of the last `select_22` plays in.
        self._lines: dict[int, int] = {}

    def league_percentiles(self, rosters: list[TeamRoster]) -> dict[int, dict[str, float]]:
        out: dict[int, dict[str, float]] = {}
        everyone = [(p, r.player_stats.get(p.id)) for r in rosters for p in r.players]
        for group in GROUPS:
            members = [(p, s) for p, s in everyone if group_of(p) == group]
            if not members:
                continue
            for key, feed in _FEEDS.items():
                raw = {p.id: feed(s) for p, s in members if s and s.appearances}
                for pid, pct in _rank(raw).items():
                    out.setdefault(pid, {})[key] = pct
            ages = {p.id: float(-_age(p, self.today)) for p, _ in members}
            for pid, pct in _rank(ages).items():
                out.setdefault(pid, {})["speed"] = pct
                out[pid]["dash"] = pct
            heights = {p.id: p.height for p, _ in members if p.height}
            for pid, pct in _rank(heights).items():
                out.setdefault(pid, {})["jump"] = pct
        return out

    def map_player(self, player: Player, pcts: dict[str, float], name: str) -> N64Player:
        group = group_of(player)
        q = RETAIL[group]
        ratings = {key: _place(spread, pcts.get(key, 50.0)) for key, spread in q.items()}
        if group != "GK":
            ratings["goalkeeping"] = q["goalkeeping"][2]
            ratings["keeper2"] = q["keeper2"][2]
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
            foot={"L": FOOT_LEFT, "B": FOOT_BOTH}.get(player.handedness, FOOT_RIGHT),
            height_cm=height,
            build=build,
            player_id=player.id,
            photo_url=player.photo_url,
        )

    def select_22(self, roster: TeamRoster) -> tuple[list[Player], tuple[int, int, int] | None]:
        """The XI in formation order (keeper, defenders, midfielders, forwards,
        each line left to right), then the bench; and the formation.

        The same selection as `iss-snes` (`ISSStatMapper._select_best_15`),
        with the places of the club's formation (the nearest the cartridge has
        to its last ESPN lineup's, else 4-4-2) instead of a fixed 4-4-2: every
        recent starter plays, in the group his lineup position belongs to, a
        group with more starters than places sending the rest to the nearest
        group with room; open places go to the season's most frequent
        starters, in their group, then a neighbouring one, and only then to a
        player who never started. The bench is the backup keeper, then the
        rest the same way.
        """
        stats = roster.player_stats
        lineup = (roster.extra or {}).get("lineup") or {}
        listed = lineup.get("starters") or []
        recent = {int(e["id"]): (i, e) for i, e in enumerate(listed) if "id" in e}
        shape = parse_formation(lineup.get("formation", "")) if recent else None
        shape = nearest_formation(shape) if shape else (4, 4, 2)

        def group(p: Player) -> str:
            if p.id in recent:
                g = LINEUP_GROUPS.get(str(recent[p.id][1].get("position", "")).upper())
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

    def given_22(self, players: list[Player]) -> tuple[list[Player], tuple[int, int, int]]:
        """The caller's squad as given, its first 11 the XI: grouped into the
        game's lines (keeper, defence, midfield, attack, the caller's order
        kept inside each), in the formation those counts are nearest."""
        # Exactly one keeper starts: the first in the list; a second keeper
        # in the first 11 makes way for the next outfielder.
        keepers = [p for p in players if group_of(p) == "GK"][:1]
        outfield = [p for p in players if group_of(p) != "GK"]
        xi_ids = {p.id for p in players[:11] if group_of(p) != "GK"}
        xi_out = [p for p in outfield if p.id in xi_ids]
        xi_out += [p for p in outfield if p.id not in xi_ids][: 11 - len(keepers) - len(xi_out)]
        xi = sorted(keepers + xi_out, key=lambda p: _LINE[group_of(p)])
        chosen = {p.id for p in xi}
        players = xi + [p for p in players if p.id not in chosen]
        lines = [_LINE[group_of(p)] for p in xi]
        self._lines = {p.id: ln for p, ln in zip(xi, lines, strict=True)}
        shape = nearest_formation((lines.count(1), lines.count(2), lines.count(3)))
        return (xi + list(players[11:]))[: R.PLAYERS_PER_TEAM], shape

    def map_team(
        self,
        roster: TeamRoster,
        pcts: dict[int, dict[str, float]],
        *,
        order_as_given: bool = False,
    ) -> N64Team:
        if order_as_given:
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
            mapped = self.map_player(p, pcts.get(p.id, {}), name)
            played = self._lines.get(p.id)
            if played is not None:
                # The line he starts in, as well as his own: a winger ESPN calls
                # a forward who starts in midfield plays MF/FW.
                mapped.position_bits |= (R.GK, R.DF, R.MF, R.FW)[played]
            players.append(mapped)
        _numbers(players)
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


def _numbers(players: list[N64Player]) -> None:
    """Shirt numbers unique in 1-63 (the 6-bit field); ESPN's where it can."""
    used: set[int] = set()
    for p in players:
        if 1 <= p.number <= 63 and p.number not in used:
            used.add(p.number)
        else:
            p.number = 0
    free = (n for n in range(1, 64) if n not in used)
    for p in players:
        if not p.number:
            p.number = next(free)
            used.add(p.number)
