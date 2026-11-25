"""Where a soccer player ranks in his position group, for the three ISS games.

A game names, per group, the stats each of its abilities ranks (`Sources`); the stats are
`pes2013_ps2.abilities.LeagueStats`'s: counts per 90 minutes and rates, each pulled toward the
group's own, and the bio ones (youth, height, weight). A player's rank for an ability is the mean
of his ranks over those of its stats he has, ranked again among his group's (a mean of ranks
bunches in the middle). An ability none of whose stats he has is left out, and `place` then gives
the group's median on the cartridge: a stat the provider did not supply never counts as a zero.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence

from ...sports.models import Player, PlayerStats
from ..pes2013_ps2.abilities import LeagueStats

#: group -> ability -> the stats it ranks.
Sources = Mapping[str, Mapping[str, tuple[str, ...]]]
#: A cartridge's spread of one ability in one group: its 2nd, 10th, 50th, 90th and 98th percentile.
Spread = tuple[int, int, int, int, int]
_AT = (0.02, 0.10, 0.50, 0.90, 0.98)


def _rank(values: Sequence[float], value: float) -> float:
    """Where `value` ranks among sorted `values`, 0-1, ties at their middle."""
    below = bisect_left(values, value)
    return (below + (bisect_right(values, value) - below) / 2) / len(values)


def place(spread: Spread, share: float | None) -> int:
    """The cartridge's value at rank `share` (0-1) of `spread`, interpolated between its five
    percentiles; its median for a player with no rank."""
    if share is None:
        return spread[2]
    if share <= _AT[0]:
        return spread[0]
    for k in range(1, 5):
        if share <= _AT[k]:
            a, b = _AT[k - 1], _AT[k]
            return round(spread[k - 1] + (spread[k] - spread[k - 1]) * (share - a) / (b - a))
    return spread[4]


class Ranks:
    """Every (player, group, stat line) of the league fetched, to rank one player against."""

    def __init__(
        self, members: Iterable[tuple[Player, str, PlayerStats | None]], sources: Sources
    ) -> None:
        members = list(members)
        self.league = LeagueStats.of(members)
        self.sources = sources
        pools: dict[tuple[str, str], list[float]] = defaultdict(list)
        for player, group, line in members:
            for ability, blend in self._blends(player, group, line).items():
                pools[group, ability].append(blend)
        self.pools = {key: sorted(v) for key, v in pools.items()}

    def _blends(self, player: Player, group: str, line: PlayerStats | None) -> dict[str, float]:
        own = self.league.stats(player, group, line)
        out = {}
        for ability, names in self.sources.get(group, {}).items():
            shares = [
                _rank(self.league.values[group, name], own[name])
                for name in names
                if name in own and self.league.values.get((group, name))
            ]
            if shares:
                out[ability] = sum(shares) / len(shares)
        return out

    def of(self, player: Player, group: str, line: PlayerStats | None) -> dict[str, float]:
        """Ability -> the player's rank (0-1) in `group`; an ability none of whose stats he has
        is left out."""
        return {
            ability: _rank(pool, blend) if (pool := self.pools.get((group, ability))) else blend
            for ability, blend in self._blends(player, group, line).items()
        }
