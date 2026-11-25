"""Rates a hockey player by ranking his season against his group's.

A game's mapper names, for each rating, the stats it ranks (`Sources`, by group:
forwards F, defencemen D, goalies G) and holds the disc's own spread of that
rating (`Spread`, its players' values at every 5%). The player's rank in his
group, averaged over the rating's stats and ranked again, picks the value at
the same rank of the disc's spread, so a patched roster keeps the game's
distribution. A stat name led by `-` ranks the other way (`-age` is youth).
A rating none of whose stats the player's line has is left out, and keeps the
position default; so is one that ranks season stats when he has only the
bio ones among them (age, size, height), which shade a rating and never make
it. A line of no games played (`GP` 0, the NHL API's free agent without one)
counts as zeros, and rates low.

Skaters' counting stats rank per game played, pulled toward the group's rate
by `SKATER_SHRINK_GAMES` games; goalies' rank as season totals, which on both
EA discs track a starter's workload better than rates do. A goalie's save %
and goals-against average are pulled toward a backup's (`core.hockey`).
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from statistics import median

from ..sports.models import Player
from .hockey import GAA_PRIOR, SV_PRIOR, goalie_games, goalie_starts, shrunk, stat

#: group -> rating -> the stats it ranks.
Sources = Mapping[str, Mapping[str, tuple[str, ...]]]
#: group -> rating -> the disc's values at 0%, 5%, ... 100% of its players.
Spread = Mapping[str, Mapping[str, Sequence[int]]]

#: A counting stat's keys, first found wins: the NHL API's, then ESPN's. A
#: line without fights counts major penalties, then penalty minutes.
COUNTS: dict[str, tuple[str, ...]] = {
    "goals": ("G",),
    "assists": ("A",),
    "points": ("PTS",),
    "shots": ("S", "SOG"),
    "pim": ("PIM",),
    "hits": ("hits",),
    "blocks": ("blockedShots",),
    "fights": ("fightingPenalties", "majorPenalties", "PIM"),
    "ppg": ("PPG", "powerPlayGoals"),
    "gwg": ("GWG", "gameWinningGoals"),
    "faceoffs": ("totalFaceOffs",),
    "wins": ("W",),
    "shutouts": ("SO",),
}
#: The stats a player has without playing.
BIO = frozenset({"age", "size", "height"})
SKATER_SHRINK_GAMES = 20
#: A faceoff % is pulled toward 50% by this many faceoffs.
FACEOFF_PRIOR = 50.0
FACEOFF_SHRINK = 100


def group_of(position: str) -> str:
    """F, D or G."""
    position = (position or "").upper()
    return position if position in ("D", "G") else "F"


def on_spread(share: float, quantiles: Sequence[int]) -> int:
    """The value at `share` (0-1, a player's rank in his group) of a spread."""
    return quantiles[round(min(1.0, max(0.0, share)) * (len(quantiles) - 1))]


def _rank(values: Sequence[float], value: float) -> float:
    """Where `value` ranks among sorted `values`, 0-1, ties at their middle."""
    below = bisect.bisect_left(values, value)
    equal = bisect.bisect_right(values, value) - below
    return (below + equal / 2) / len(values)


def age_of(player: Player) -> float | None:
    """His age in years today: from his birth date, else the provider's age."""
    try:
        born = date.fromisoformat(player.birth_date)
    except (TypeError, ValueError):
        return float(player.age) if player.age else None
    return (date.today() - born).days / 365.25


def _totals(line: Mapping[str, object]) -> dict[str, float]:
    totals = {}
    for name, keys in COUNTS.items():
        for key in keys:
            if (value := stat(line, key)) is not None:
                totals[name] = value
                break
    return totals


def _faceoff_pct(player: Player, line: Mapping[str, object], taken: float | None) -> float | None:
    """FO% as a percentage, pulled toward 50% by the faceoffs taken; None for a
    player who took none. A line with no count (the NHL API's) gives a centre
    his FO% as it is and anyone else 0, below every centre: a winger's FO% is
    over a handful of draws."""
    pct = stat(line, "FO%") or stat(line, "faceoffPercent")
    if taken is None and (player.position or "").upper() != "C":
        return None if pct is None else 0.0
    if not pct:
        return None
    pct = pct * 100 if pct <= 1 else pct
    return pct if taken is None else shrunk(pct, FACEOFF_PRIOR, taken, FACEOFF_SHRINK)


@dataclass
class League:
    """Every fetched player's stats, by group, to rank one player against."""

    sources: Sources
    #: (group, stat) -> every value, sorted.
    values: dict[tuple[str, str], list[float]] = field(default_factory=dict)
    #: (group, counting stat) -> the group's rate per game.
    rates: dict[tuple[str, str], float] = field(default_factory=dict)
    #: group -> its median games played: a line without games is a typical season.
    games: dict[str, float] = field(default_factory=dict)
    #: (group, rating) -> every player's average rank over the rating's stats,
    #: sorted: an average of ranks bunches in the middle, so it is ranked again.
    blends: dict[tuple[str, str], list[float]] = field(default_factory=dict)

    @classmethod
    def from_lines(
        cls, lines: Iterable[tuple[Player, Mapping[str, object]]], sources: Sources
    ) -> League:
        """The league of these (player, stat line) pairs; a pair without a line is left out."""
        lines = [(player, line) for player, line in lines if line]
        sums: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
        played: dict[str, list[float]] = defaultdict(list)
        for player, line in lines:
            group, gp = group_of(player.position), stat(line, "GP")
            if gp:
                played[group].append(gp)
                for name, total in _totals(line).items():
                    sums[group, name][0] += total
                    sums[group, name][1] += gp
        league = cls(
            sources=sources,
            rates={key: total / gp for key, (total, gp) in sums.items() if gp},
            games={group: median(gps) for group, gps in played.items()},
        )
        own = [(group_of(p.position), league.stats(p, line)) for p, line in lines]
        values: dict[tuple[str, str], list[float]] = defaultdict(list)
        for group, stats in own:
            for name, value in stats.items():
                values[group, name].append(value)
        league.values = {key: sorted(v) for key, v in values.items()}
        blends: dict[tuple[str, str], list[float]] = defaultdict(list)
        for group, stats in own:
            for rating, names in sources.get(group, {}).items():
                if (blend := league._blend(group, names, stats)) is not None:
                    blends[group, rating].append(blend)
        league.blends = {key: sorted(v) for key, v in blends.items()}
        return league

    def stats(self, player: Player, line: Mapping[str, object]) -> dict[str, float]:
        """The player's value of every stat a rating may rank, higher the more."""
        group = group_of(player.position)
        totals = _totals(line)
        if stat(line, "GP") == 0:
            totals = dict.fromkeys(COUNTS, 0.0) | totals
        out: dict[str, float] = {}
        if (age := age_of(player)) is not None:
            out["age"] = age
        if player.weight:
            out["size"] = float(player.weight)
        if player.height:
            out["height"] = float(player.height)
        if group == "G":
            n = goalie_games(line)
            if (sv := stat(line, "SV%")) is not None:
                out["save"] = shrunk(sv, SV_PRIOR, n)
            if (gaa := stat(line, "GAA")) is not None:
                out["goals_against"] = -shrunk(gaa, GAA_PRIOR, n)
            if "GP" in line or "W" in line:
                out["games"] = goalie_starts(line)
            return out | totals
        games = stat(line, "GP") or self.games.get(group) or 1.0
        for name, total in totals.items():
            own = total / games
            rate = self.rates.get((group, name), own)
            out[name] = shrunk(own, rate, games, SKATER_SHRINK_GAMES)
        if (gp := stat(line, "GP")) is not None:
            out["games"] = gp
        if ice := stat(line, "TOI") or stat(line, "timeOnIcePerGame"):
            out["ice_time"] = ice
        if (pct := _faceoff_pct(player, line, totals.get("faceoffs"))) is not None:
            out["faceoff_pct"] = pct
        return out

    def _blend(self, group: str, names: Sequence[str], own: Mapping[str, float]) -> float | None:
        """The average rank of the player's values `own` of the stats `names`
        in his group; None when he has none of them, or only bio ones where
        `names` has season stats."""
        played = [name.lstrip("-") for name in names if name.lstrip("-") not in BIO]
        if played and not any(name in own for name in played):
            return None
        shares = []
        for name in names:
            stat_name = name.lstrip("-")
            pool = self.values.get((group, stat_name))
            if stat_name in own and pool:
                share = _rank(pool, own[stat_name])
                shares.append(1 - share if name.startswith("-") else share)
        return sum(shares) / len(shares) if shares else None

    def shares(self, player: Player, line: Mapping[str, object]) -> dict[str, float]:
        """rating -> where the player ranks for it in his group (0-1), for
        every rating his line has a stat of."""
        group = group_of(player.position)
        own = self.stats(player, line)
        out = {}
        for rating, names in self.sources.get(group, {}).items():
            blend = self._blend(group, names, own)
            if blend is not None:
                pool = self.blends.get((group, rating))
                out[rating] = _rank(pool, blend) if pool else blend
        return out


def rate(
    attrs: object,
    player: Player,
    line: Mapping[str, object],
    league: League,
    spread: Spread,
) -> None:
    """Set each rating of `attrs` the player's line has a stat of to the value
    at his rank of the disc's `spread`; the others keep what `attrs` holds."""
    group = group_of(player.position)
    for rating, share in league.shares(player, line).items():
        setattr(attrs, rating, on_spread(share, spread[group][rating]))
