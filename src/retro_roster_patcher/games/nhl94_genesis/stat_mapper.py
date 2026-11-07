"""Maps a player's season onto NHL 94's 0-6 ratings.

Each rating comes from one or two of his stats (`SKATER_SOURCES`,
`GOALIE_SOURCES`), ranked against every player of his group (forwards,
defencemen, goalies) in the league fetched (`LeagueStats`): the rating is the value
at the same rank of the cartridge's own spread of it (`RETAIL`), so the
league's best shooter gets the shot power of the cartridge's best and its
typical one the cartridge's typical 3. A rating none of whose stats the line
has keeps the position default, and a player without a stat line keeps them
all.

Counting stats are ranked per game played, pulled toward the group's own rate
by `SKATER_SHRINK_GAMES` games (two goals in two games is not a 40-goal
season); a shooting % is pulled toward 10% by 50 shots, a goalie's save % and
goals-against average toward a backup's by `core.hockey.SHRINK_GAMES`. Neither
provider measures skating, so speed and agility rank youth and size together
with points.
"""

import bisect
import dataclasses
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from statistics import median

from ...core.hockey import (
    GAA_PRIOR,
    SHRINK_GAMES,
    SV_PRIOR,
    deployment,
    goalie_games,
    shrunk,
    stat,
)
from ...sports.models import Player
from .models import (
    MODERN_NHL_TO_NHL94_GEN,
    NHL94GenGoalieAttributes,
    NHL94GenPlayerAttributes,
    NHL94GenPlayerRecord,
)

# Default attributes by position (0-6 scale)
POSITION_DEFAULTS = {
    "C": NHL94GenPlayerAttributes(
        speed=3,
        agility=3,
        shot_power=3,
        shot_accuracy=3,
        stick_handling=3,
        pass_accuracy=3,
        off_awareness=3,
        def_awareness=2,
        checking=2,
        endurance=3,
        roughness=2,
        aggression=2,
    ),
    "LW": NHL94GenPlayerAttributes(
        speed=3,
        agility=3,
        shot_power=3,
        shot_accuracy=3,
        stick_handling=3,
        pass_accuracy=2,
        off_awareness=3,
        def_awareness=2,
        checking=3,
        endurance=3,
        roughness=3,
        aggression=3,
    ),
    "RW": NHL94GenPlayerAttributes(
        speed=3,
        agility=3,
        shot_power=3,
        shot_accuracy=3,
        stick_handling=3,
        pass_accuracy=2,
        off_awareness=3,
        def_awareness=2,
        checking=3,
        endurance=3,
        roughness=3,
        aggression=3,
    ),
    "D": NHL94GenPlayerAttributes(
        speed=2,
        agility=2,
        shot_power=2,
        shot_accuracy=2,
        stick_handling=2,
        pass_accuracy=3,
        off_awareness=2,
        def_awareness=4,
        checking=4,
        endurance=3,
        roughness=3,
        aggression=3,
    ),
}


#: A goalie with no stat line.
GOALIE_DEFAULTS = NHL94GenGoalieAttributes(
    agility=4,
    speed=3,
    def_awareness=3,
    puck_control=2,
    stick_right=4,
    stick_left=1,
    glove_right=2,
    glove_left=1,
)


#: How many of the cartridge's players (364 forwards, 211 defencemen, 59
#: goalies) have each value 0-6 of each rating.
RETAIL: dict[str, dict[str, tuple[int, ...]]] = {
    "F": {
        "speed": (0, 18, 113, 126, 89, 13, 5),
        "agility": (0, 29, 108, 101, 98, 25, 3),
        "shot_power": (1, 31, 150, 121, 52, 8, 1),
        "shot_accuracy": (8, 37, 90, 129, 67, 28, 5),
        "stick_handling": (0, 26, 124, 117, 75, 20, 2),
        "pass_accuracy": (0, 44, 128, 113, 64, 12, 3),
        "off_awareness": (5, 25, 99, 130, 83, 17, 5),
        "def_awareness": (5, 27, 77, 174, 71, 9, 1),
        "checking": (0, 48, 144, 119, 47, 6, 0),
        "endurance": (3, 26, 66, 129, 115, 18, 7),
        "roughness": (41, 55, 75, 66, 61, 66, 0),
        "aggression": (15, 50, 121, 104, 60, 12, 2),
    },
    "D": {
        "speed": (1, 12, 86, 86, 24, 2, 0),
        "agility": (1, 12, 76, 75, 38, 6, 3),
        "shot_power": (2, 32, 77, 54, 36, 7, 3),
        "shot_accuracy": (51, 94, 54, 11, 0, 1, 0),
        "stick_handling": (1, 16, 89, 65, 35, 3, 2),
        "pass_accuracy": (3, 34, 71, 69, 30, 3, 1),
        "off_awareness": (7, 41, 87, 56, 20, 0, 0),
        "def_awareness": (1, 14, 38, 88, 62, 6, 2),
        "checking": (1, 11, 53, 103, 40, 2, 1),
        "endurance": (0, 15, 40, 76, 68, 10, 2),
        "roughness": (17, 47, 42, 37, 37, 31, 0),
        "aggression": (1, 9, 59, 84, 51, 6, 1),
    },
    "G": {
        "agility": (0, 0, 26, 17, 13, 1, 2),
        "speed": (0, 0, 0, 33, 26, 0, 0),
        "def_awareness": (3, 5, 10, 13, 14, 11, 3),
        "puck_control": (0, 4, 23, 16, 13, 2, 1),
        "stick_right": (0, 4, 28, 12, 13, 1, 1),
        "stick_left": (0, 4, 30, 10, 13, 1, 1),
        "glove_right": (0, 3, 28, 15, 9, 3, 1),
        "glove_left": (0, 2, 28, 14, 12, 2, 1),
    },
}

#: The stats each rating ranks; a rating with several averages their ranks, a
#: stat listed twice counting twice. Speed and agility lean on points (a star
#: is quick), then youth and lightness. Checking ranks size only for a line
#: without hits (the NHL API's).
SKATER_SOURCES: dict[str, tuple[str, ...]] = {
    "speed": ("points", "points", "youth"),
    "agility": ("points", "points", "lightness"),
    "shot_power": ("shots",),
    "shot_accuracy": ("finishing",),
    "stick_handling": ("goals",),
    "pass_accuracy": ("assists",),
    "off_awareness": ("points",),
    "def_awareness": ("plus_minus", "blocks"),
    "checking": ("hits", "size"),
    "endurance": ("ice_time",),
    "roughness": ("majors",),
    "aggression": ("pim",),
}
#: No provider counts saves by zone: the four zones rank save % alone, each on
#: its own spread.
GOALIE_SOURCES: dict[str, tuple[str, ...]] = {
    "agility": ("save",),
    "speed": ("youth",),
    "def_awareness": ("goals_against",),
    "puck_control": ("wins",),
    "stick_right": ("save",),
    "stick_left": ("save",),
    "glove_right": ("save",),
    "glove_left": ("save",),
}

#: The keys a counting stat comes under: the NHL API's, then ESPN's.
COUNTS: dict[str, tuple[str, ...]] = {
    "goals": ("G",),
    "assists": ("A",),
    "points": ("PTS",),
    "plus_minus": ("+/-",),
    "shots": ("S", "SOG"),
    "pim": ("PIM",),
    "hits": ("hits",),
    "blocks": ("blockedShots",),
    "majors": ("majorPenalties",),
    "wins": ("W",),
}
SKATER_SHRINK_GAMES = 20
#: A shooting % is pulled toward the league's 10% by 50 shots' worth.
SPCT_PRIOR = 0.10
SPCT_SHOTS = 50


def _sources_of(group: str) -> dict[str, tuple[str, ...]]:
    return GOALIE_SOURCES if group == "G" else SKATER_SOURCES


def _group(position: str) -> str:
    """F, D or G: the cartridge's spreads are its forwards', defencemen's, goalies'."""
    position = (position or "").upper()
    return position if position in ("D", "G") else "F"


def on_retail(share: float, counts: Sequence[int]) -> int:
    """The value at `share` (0-1, a player's rank in his group) of the
    cartridge's spread `counts` (how many of its players have each value)."""
    target = share * sum(counts)
    running = 0
    for value, count in enumerate(counts):
        running += count
        if running > target:
            return value
    return max(value for value, count in enumerate(counts) if count)


def _youth(player: Player) -> float | None:
    """Higher the younger: the birth date as a day number (the NHL API's rosters
    carry no age), else the birth day his age puts him near."""
    try:
        return float(date.fromisoformat(player.birth_date).toordinal())
    except ValueError:
        return date.today().toordinal() - 365.25 * player.age if player.age else None


def _rank(values: Sequence[float], value: float) -> float:
    """Where `value` ranks among sorted `values`, 0-1, ties at their middle."""
    below = bisect.bisect_left(values, value)
    equal = bisect.bisect_right(values, value) - below
    return (below + equal / 2) / len(values)


def _totals(line: Mapping[str, object]) -> dict[str, float]:
    totals = {}
    for name, keys in COUNTS.items():
        for key in keys:
            if (value := stat(line, key)) is not None:
                totals[name] = value
                break
    return totals


@dataclass
class LeagueStats:
    """Every fetched player's stats, by group, to rank one player against."""

    #: (group, stat) -> every value, sorted.
    values: dict[tuple[str, str], list[float]] = field(default_factory=dict)
    #: (group, counting stat) -> the group's rate per game.
    rates: dict[tuple[str, str], float] = field(default_factory=dict)
    #: group -> its median games played: a line without games is a typical season.
    games: dict[str, float] = field(default_factory=dict)
    #: (group, rating) -> every player's average rank over the rating's stats,
    #: sorted, for the ratings that average several: an average of ranks bunches
    #: in the middle, so it is ranked again.
    blends: dict[tuple[str, str], list[float]] = field(default_factory=dict)

    @classmethod
    def from_lines(cls, lines: Iterable[tuple[Player, Mapping[str, object]]]) -> "LeagueStats":
        """The league of these (player, stat line) pairs; a pair without a line is left out."""
        lines = [(player, line) for player, line in lines if line]
        sums: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
        played: dict[str, list[float]] = defaultdict(list)
        for player, line in lines:
            group, gp = _group(player.position), stat(line, "GP")
            if gp:
                played[group].append(gp)
                for name, total in _totals(line).items():
                    sums[group, name][0] += total
                    sums[group, name][1] += gp
        league = cls(
            rates={key: total / gp for key, (total, gp) in sums.items() if gp},
            games={group: median(gps) for group, gps in played.items()},
        )
        values: dict[tuple[str, str], list[float]] = defaultdict(list)
        sources = [league.sources(player, line) for player, line in lines]
        for (player, _line), own in zip(lines, sources, strict=True):
            for name, value in own.items():
                values[_group(player.position), name].append(value)
        league.values = {key: sorted(v) for key, v in values.items()}
        blends: dict[tuple[str, str], list[float]] = defaultdict(list)
        for (player, _line), own in zip(lines, sources, strict=True):
            group = _group(player.position)
            for rating, names in _sources_of(group).items():
                if len(names) > 1 and (blend := league._blend(group, names, own)) is not None:
                    blends[group, rating].append(blend)
        league.blends = {key: sorted(v) for key, v in blends.items()}
        return league

    def sources(self, player: Player, line: Mapping[str, object]) -> dict[str, float]:
        """The player's value of each stat his ratings rank, higher the better."""
        group = _group(player.position)
        totals = _totals(line)
        games = stat(line, "GP") or self.games.get(group) or 1.0
        k = SHRINK_GAMES if group == "G" else SKATER_SHRINK_GAMES

        def rate(name: str) -> float | None:
            total = totals.get(name)
            if total is None:
                return None
            own = total / games
            return shrunk(own, self.rates.get((group, name), own), games, k)

        out: dict[str, float] = {}
        if (youth := _youth(player)) is not None:
            out["youth"] = youth
        if group == "G":
            sv, gaa, n = stat(line, "SV%"), stat(line, "GAA"), goalie_games(line)
            if sv is not None:
                out["save"] = shrunk(sv, SV_PRIOR, n)
            if gaa is not None:
                out["goals_against"] = -shrunk(gaa, GAA_PRIOR, n)
            if (wins := rate("wins")) is not None:
                out["wins"] = wins
            return out
        if player.weight:
            out["lightness"] = -float(player.weight)
        for name in ("goals", "assists", "points", "plus_minus", "shots", "blocks", "pim"):
            if (value := rate(name)) is not None:
                out[name] = value
        if (hits := rate("hits")) is not None:
            out["hits"] = hits
        elif player.weight:
            out["size"] = float(player.weight)
        if (majors := rate("majors")) is not None:
            out["majors"] = majors
        shots, goals = totals.get("shots"), totals.get("goals")
        if shots and goals is not None:
            out["finishing"] = (goals + SPCT_PRIOR * SPCT_SHOTS) / (shots + SPCT_SHOTS)
        ice = stat(line, "TOI") or stat(line, "timeOnIcePerGame")
        if ice:
            out["ice_time"] = ice
        return out

    def _blend(self, group: str, names: Sequence[str], own: Mapping[str, float]) -> float | None:
        """The average rank of the player's values `own` of the stats `names`
        among the group's; None when he has none the group has."""
        shares = [
            _rank(self.values[group, name], own[name])
            for name in names
            if name in own and self.values.get((group, name))
        ]
        return sum(shares) / len(shares) if shares else None

    def rating_share(self, group: str, rating: str, own: Mapping[str, float]) -> float | None:
        """Where a player whose stat values are `own` ranks for `rating` in his
        group (0-1); None when he has none of its stats."""
        blend = self._blend(group, _sources_of(group)[rating], own)
        pool = self.blends.get((group, rating))
        return _rank(pool, blend) if blend is not None and pool else blend


class NHL94GenStatMapper:
    def map_player(
        self,
        player: Player,
        team_abbrev: str,
        stats: Mapping[str, object] | None = None,
        league: LeagueStats | None = None,
    ) -> NHL94GenPlayerRecord:
        """`league` ranks his stats; without one his line is ranked alone."""
        pos = player.position.upper() if player.position else "C"
        is_goalie = pos == "G"

        # Copied, not shared: `attributes` is public and mutable, so the module
        # constants must not be handed out.
        attrs: NHL94GenPlayerAttributes | NHL94GenGoalieAttributes
        if is_goalie:
            attrs = dataclasses.replace(GOALIE_DEFAULTS)
        else:
            attrs = dataclasses.replace(POSITION_DEFAULTS.get(pos, POSITION_DEFAULTS["C"]))
        if stats:
            self._rate(attrs, player, stats, league or LeagueStats.from_lines([(player, stats)]))

        jersey = player.number or 1

        if player.weight > 0:
            weight_class = self._map_weight(int(player.weight))
        else:
            weight_class = 7  # 196 lbs

        hand = 0  # 0=L, 1=R
        if player.handedness == "R":
            hand = 1

        return NHL94GenPlayerRecord(
            name=player.name[:14],
            position=pos,
            jersey_number=jersey,
            weight_class=weight_class,
            handedness=hand,
            is_goalie=is_goalie,
            attributes=attrs,
            photo_url=player.photo_url,
        )

    def _rate(
        self,
        attrs: NHL94GenPlayerAttributes | NHL94GenGoalieAttributes,
        player: Player,
        line: Mapping[str, object],
        league: LeagueStats,
    ) -> None:
        """Set each rating whose stats the line has from their rank in `league`."""
        group = _group(player.position)
        own = league.sources(player, line)
        for rating in _sources_of(group):
            if (share := league.rating_share(group, rating, own)) is not None:
                setattr(attrs, rating, on_retail(share, RETAIL[group][rating]))

    def select_roster(
        self,
        players: list[Player],
        stats: dict | None = None,
        max_players: int = 23,
    ) -> list[Player]:
        """Player order is what sets the starting lines:

        F1-F3 = Line 1 (C, LW, RW)
        F4-F6 = Line 2
        F7-F9 = Line 3
        F10-F12 = Line 4
        F13-F14 = Extra forwards
        D1-D2 = Pair 1  ...  D7 = Extra D
        G1 = Starter, G2 = Backup
        """
        stats = stats or {}

        def sort_key(p: Player) -> float:
            return deployment(stats.get(str(p.id), {}), p.position == "G")

        centers = sorted(
            [p for p in players if p.position == "C"],
            key=sort_key,
            reverse=True,
        )
        left_wings = sorted(
            [p for p in players if p.position == "LW"],
            key=sort_key,
            reverse=True,
        )
        right_wings = sorted(
            [p for p in players if p.position == "RW"],
            key=sort_key,
            reverse=True,
        )
        defensemen = sorted(
            [p for p in players if p.position == "D"],
            key=sort_key,
            reverse=True,
        )
        goalies = sorted(
            [p for p in players if p.position == "G"],
            key=sort_key,
            reverse=True,
        )

        # four forward lines, C/LW/RW each
        forwards = []
        for i in range(4):
            c = centers[i] if i < len(centers) else None
            lw = left_wings[i] if i < len(left_wings) else None
            rw = right_wings[i] if i < len(right_wings) else None
            for p in (c, lw, rw):
                if p is not None:
                    forwards.append(p)

        used = set(id(p) for p in forwards)
        extras = sorted(
            [p for p in centers + left_wings + right_wings if id(p) not in used],
            key=sort_key,
            reverse=True,
        )
        forwards.extend(extras)
        forwards = forwards[:14]

        defense = defensemen[:7]
        goalies = goalies[:2]

        # ROM order: goalies, then forwards, then defence
        selected = goalies + forwards + defense

        all_used = set(id(p) for p in selected)
        leftover = sorted(
            [p for p in players if id(p) not in all_used],
            key=sort_key,
            reverse=True,
        )
        remaining = max_players - len(selected)
        if remaining > 0:
            selected.extend(leftover[:remaining])

        return selected[:max_players]

    def _map_weight(self, weight_pounds: int) -> int:
        """Weight class = (lbs - 140) / 8, clamped to 0-14."""
        weight_class = (weight_pounds - 140) // 8
        return max(0, min(14, weight_class))

    def get_team_slot(self, team_abbrev: str) -> int | None:
        return MODERN_NHL_TO_NHL94_GEN.get(team_abbrev.upper())
