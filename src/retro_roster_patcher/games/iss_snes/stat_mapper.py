"""Maps real-world player stats onto ISS's nine ability fields.

Every field lands inside the range the 390 retail non-Super-Star records use,
never the full 0-15 scale: the kick-off condition takes up to 5 off every field
and speeds floor at 0x1C, so a value below retail plays worse than it reads.
The top value (15, or 7 on the 3-bit fields) is kept for the top few percent.

  * shooting 1-7: goals and shots on target.
  * technique 1-7: pass accuracy, averaged with dribble success when supplied.
  * stamina 7-15: minutes per appearance.
  * kick 2-7: passes per game, else the mean of shooting and technique.
  * offense / defense: the position's retail slot median, moved by up to 2 with
    the (goals + assists) and (tackles + interceptions) per game percentiles,
    ranked within the position so the median player keeps the baseline.
    Defense stays at the baseline when tackles are not supplied.
  * speed: nothing ESPN reports measures pace, so `dash` is the position's
    baseline with an age rule, `ball_dash` one less and `dribble` three less,
    which keeps retail's order dribble <= ball_dash <= dash.

A player with no appearances gets the retail medians, his position's offense
and defense, and the same speed rule.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable

from ...sports.espn import LINEUP_GROUPS
from ...sports.models import Player, PlayerStats, TeamRoster
from .models import (
    PLAYERS_PER_TEAM,
    ISSPlayerAttributes,
    ISSPlayerRecord,
    ISSTeamRecord,
)
from .rom_writer import _to_ascii

#: (percentile floor, value) rows, highest first. Tuned so the league's p10,
#: median and p90 land on retail's.
Table = list[tuple[float, int]]


def _any_unsupplied(stats: PlayerStats, fields: tuple[str, ...]) -> bool:
    return any(name in stats.unsupplied for name in fields)


def _lookup(table: Table, percentile: float) -> int:
    return next((value for floor, value in table if percentile >= floor), table[-1][1])


def _ranks(raw: dict[int, float]) -> dict[int, float]:
    """Percentile of each value: the share below it, ties at their mean rank.

    Ties share the middle of the ranks they span, so a group that is all zero
    (keepers' goals) sits at the 50th percentile rather than at the bottom.
    """
    values = sorted(raw.values())
    n = len(values)
    out = {}
    for pid, value in raw.items():
        below = bisect_left(values, value)
        ties = bisect_right(values, value) - below
        out[pid] = (below + (ties - 1) / 2) / n * 100
    return out


def _per_game(total: Callable[[PlayerStats], float]) -> Callable[[PlayerStats], float]:
    return lambda s: total(s) / max(s.appearances, 1)


class ISSStatMapper:
    SHOOTING_TABLE: Table = [(97, 7), (92, 6), (65, 5), (35, 4), (15, 3), (5, 2), (0, 1)]
    TECHNIQUE_TABLE: Table = [(97, 7), (92, 6), (80, 5), (55, 4), (25, 3), (12, 2), (0, 1)]
    STAMINA_TABLE: Table = [
        (97, 15),
        (92, 14),
        (85, 13),
        (70, 12),
        (55, 11),
        (35, 10),
        (20, 9),
        (8, 8),
        (0, 7),
    ]
    KICK_TABLE: Table = [(97, 7), (85, 6), (65, 5), (40, 4), (15, 3), (0, 2)]
    #: How far offense and defense move off the position baseline.
    SHIFT_TABLE: Table = [(90, 2), (70, 1), (30, 0), (10, -1), (0, -2)]

    #: (defense, offense, dribble, dash, ball_dash) by position: the retail
    #: medians of the slots each position plays (keepers from slot 0 alone).
    POSITION_BASELINES = {
        "Goalkeeper": (12, 2, 5, 6, 5),
        "Defender": (11, 4, 7, 10, 9),
        "Midfielder": (8, 7, 7, 10, 9),
        "Attacker": (4, 10, 8, 11, 10),
    }

    #: No speed below retail's slowest (4), however old the player.
    MIN_SPEED = 4

    POSITION_CODES = {
        "Goalkeeper": 0,
        "Defender": 1,
        "Midfielder": 2,
        "Attacker": 3,
    }

    #: The `PlayerStats` fields each category's formula cannot do without. A
    #: player missing one is dropped from that category's ranking, not ranked on
    #: the zero his record carries. Spell the names exactly as on `PlayerStats`:
    #: a typo can never match `PlayerStats.unsupplied` and the gate silently
    #: stops gating.
    CATEGORY_INPUTS = {
        "shooting": ("goals", "shots_on"),
        "technique": ("passes_accuracy",),
        "stamina": ("minutes", "appearances"),
        "kick": ("passes_total", "appearances"),
        "offense": ("goals", "assists", "appearances"),
        "defense": ("tackles_total", "interceptions", "appearances"),
    }

    #: Inputs a category is *better* with and survives without. ESPN measures
    #: neither dribbling field, and it is the only provider this game has, so
    #: drop the dribbling *term* from the formula rather than the player from the
    #: ranking: `technique` has one other input, and dropping every player leaves
    #: the category empty and hands all 405 the same default rating.
    CATEGORY_OPTIONAL_INPUTS = {
        "technique": ("dribbles_success", "dribbles_attempts"),
    }

    #: Categories ranked within the player's position: offense and defense shift
    #: off a position baseline instead of restating it, and technique (pass
    #: accuracy) and kick (passes per game) would otherwise hand centre-backs
    #: the best touch and the league's top scorers the worst.
    RANKED_BY_POSITION = ("offense", "defense", "technique", "kick")

    def map_team_with_league_context(
        self,
        team_roster: TeamRoster,
        all_rosters: list[TeamRoster],
        order_as_given: bool = False,
    ) -> ISSTeamRecord:
        # Rank only the players some team writes: bench and youth players
        # with stats would push every written fifteen up the scale.
        written = {p.id for r in all_rosters for p in self._fifteen(r, order_as_given)}
        all_stats = {
            pid: s for r in all_rosters for pid, s in r.player_stats.items() if pid in written
        }
        positions = {p.id: p.position for r in all_rosters for p in r.players}

        percentiles = self._compute_percentiles(all_stats, positions)
        best_15 = self._fifteen(team_roster, order_as_given)

        iss_players = [
            ISSPlayerRecord(
                name=rom_name,
                shirt_number=number,
                position=self.POSITION_CODES.get(player.position, 2),
                attributes=self.map_player(
                    player, team_roster.player_stats.get(player.id), percentiles
                ),
                player_id=player.id,
                photo_url=player.photo_url,
                nationality=player.nationality,
            )
            for player, rom_name, number in zip(
                best_15, self._distinct_names(best_15), self._shirt_numbers(best_15), strict=True
            )
        ]

        return ISSTeamRecord(
            name=_to_ascii(team_roster.team.name),
            short_name=_to_ascii(
                team_roster.team.code[:3]
                if team_roster.team.code
                else team_roster.team.name[:3].upper()
            ),
            players=iss_players,
        )

    def map_player(
        self,
        player: Player,
        stats: PlayerStats | None,
        percentiles: dict[str, dict[int, float]],
    ) -> ISSPlayerAttributes:
        defense, offense, dribble, dash, ball_dash = self.POSITION_BASELINES.get(
            player.position, self.POSITION_BASELINES["Midfielder"]
        )
        age = player.age  # 0 when unknown: no adjustment
        step = 0
        if age and age <= 23:
            step = 1
        elif age and age >= 34:
            step = -2
        elif age and age >= 31:
            step = -1
        attrs = ISSPlayerAttributes(
            dribble=max(self.MIN_SPEED, dribble + step),
            dash=max(self.MIN_SPEED, dash + step),
            ball_dash=max(self.MIN_SPEED, ball_dash + step),
            defense=defense,
            offense=offense,
        )
        if not stats or stats.appearances == 0:
            return attrs

        pid = stats.player_id

        def pct(category: str) -> float | None:
            return percentiles.get(category, {}).get(pid)

        # An empty or gated category rates the player at the median.
        attrs.shooting = _lookup(self.SHOOTING_TABLE, percentiles.get("shooting", {}).get(pid, 50))
        attrs.technique = _lookup(
            self.TECHNIQUE_TABLE, percentiles.get("technique", {}).get(pid, 50)
        )
        attrs.stamina = _lookup(self.STAMINA_TABLE, percentiles.get("stamina", {}).get(pid, 50))
        kick = pct("kick")
        attrs.kick = (
            _lookup(self.KICK_TABLE, kick)
            if kick is not None
            else max(2, min(7, (attrs.shooting + attrs.technique + 1) // 2))
        )
        # Keepers keep their baseline: tackles, interceptions and goals say
        # little about them, and no traced code shows what they save with.
        if player.position != "Goalkeeper":
            if (off := pct("offense")) is not None:
                attrs.offense += _lookup(self.SHIFT_TABLE, off)
            if (dfn := pct("defense")) is not None:
                attrs.defense += _lookup(self.SHIFT_TABLE, dfn)
        return attrs

    def _fifteen(self, roster: TeamRoster, order_as_given: bool) -> list[Player]:
        """The fifteen this mapper writes for `roster`: the caller's list as it
        stands when `order_as_given` (slot index is the role in this ROM, so
        index 0 keeps goal), else the selection."""
        if order_as_given:
            return roster.players[:PLAYERS_PER_TEAM]
        return self._select_best_15(roster.players, roster.player_stats, roster.extra.get("lineup"))

    def _technique_value(self, stats: PlayerStats) -> float:
        """Raw technique score: dribble success rate and pass accuracy, averaged.

        Pass accuracy alone when the provider does not measure dribbling.
        """
        if _any_unsupplied(stats, self.CATEGORY_OPTIONAL_INPUTS["technique"]):
            return stats.passes_accuracy
        return (
            (stats.dribbles_success / max(stats.dribbles_attempts, 1)) * 100 + stats.passes_accuracy
        ) / 2

    def _compute_percentiles(
        self, all_stats: dict[int, PlayerStats], positions: dict[int, str] | None = None
    ) -> dict[str, dict[int, float]]:
        """Compute league-wide percentiles for each stat category.

        `n` is per-category and not `len(all_stats)`: a player whose provider did
        not measure one of a category's required inputs is skipped for it. The
        `RANKED_BY_POSITION` categories rank each position (from `positions`)
        on its own.
        """
        if not all_stats:
            return {}

        categories: dict[str, Callable[[PlayerStats], float]] = {
            "shooting": lambda s: s.goals + s.shots_on * 0.3,
            "technique": self._technique_value,
            "stamina": _per_game(lambda s: s.minutes),
            "kick": _per_game(lambda s: s.passes_total),
            "offense": _per_game(lambda s: s.goals + s.assists),
            "defense": _per_game(lambda s: s.tackles_total + s.interceptions),
        }

        percentiles: dict[str, dict[int, float]] = {}
        for cat_name, extract_fn in categories.items():
            groups: dict[str, dict[int, float]] = {}
            for pid, stats in all_stats.items():
                if _any_unsupplied(stats, self.CATEGORY_INPUTS[cat_name]):
                    continue
                group = (positions or {}).get(pid, "")
                if cat_name not in self.RANKED_BY_POSITION:
                    group = ""
                groups.setdefault(group, {})[pid] = extract_fn(stats)
            percentiles[cat_name] = {
                pid: rank for raw in groups.values() for pid, rank in _ranks(raw).items()
            }

        return percentiles

    def _shirt_numbers(self, players: list[Player]) -> list[int]:
        """Unique numbers 1-15, the only ones retail uses. A real number of 15
        or below that is still free stays; everyone else takes the lowest free
        numbers in squad order."""
        kept: list[int | None] = []
        for p in players:
            free = p.number is not None and 1 <= p.number <= PLAYERS_PER_TEAM
            kept.append(p.number if free and p.number not in kept else None)
        spare = iter(n for n in range(1, PLAYERS_PER_TEAM + 1) if n not in kept)
        return [n or next(spare) for n in kept]

    #: The fifteen's starting places by group: slot 0 keeps goal, then a 4-4-2.
    XI_PLACES = (("Goalkeeper", 1), ("Defender", 4), ("Midfielder", 4), ("Attacker", 2))

    #: Where a group's extra starters play when its places are full, nearest
    #: first. Keepers never swap with outfield players.
    SPILL = {
        "Defender": ("Midfielder", "Attacker"),
        "Midfielder": ("Attacker", "Defender"),
        "Attacker": ("Midfielder", "Defender"),
    }

    def _select_best_15(
        self,
        players: list[Player],
        player_stats: dict[int, PlayerStats] | None = None,
        lineup: dict | None = None,
    ) -> list[Player]:
        """The fifteen in ISS's order: the starting eleven, then four subs.

        The eleven is the team's recent starting lineup when `lineup` (what
        `EspnClient.get_lineup` returns) has one: every listed starter plays,
        in his lineup group, and a group with more starters than places (a
        front three) sends the rest to the nearest group with room. Places
        still open go to the players with the most starts this season, in
        their group, then in a neighbouring one, and only then to a player
        who never started, so a forward with no starts never takes a place a
        midfielder with twenty would. Subs are the backup keeper, then the
        best of the rest the same way.
        """
        stats = player_stats or {}
        recent = {int(s["id"]): (i, s) for i, s in enumerate((lineup or {}).get("starters") or [])}

        def group(p: Player) -> str:
            if p.id in recent:
                code = str(recent[p.id][1].get("position", "")).upper()
                return LINEUP_GROUPS.get(code, p.position)
            return p.position

        def key(p: Player) -> tuple[int, int, int, int]:
            s = stats.get(p.id)
            return (
                recent[p.id][0] if p.id in recent else len(recent),
                -(s.lineups if s else 0),
                -(s.appearances if s else 0),
                -(s.minutes if s else 0),
            )

        def started(p: Player) -> bool:
            s = stats.get(p.id)
            return p.id in recent or bool(s and s.lineups)

        pool = sorted(players, key=key)
        places = dict(self.XI_PLACES)
        chosen: dict[str, list[Player]] = {g: [] for g in places}
        taken: set[int] = set()

        def fill(eligible, spill: bool) -> None:
            for p in pool:
                if id(p) in taken or not eligible(p):
                    continue
                home = group(p)
                for target in (home, *(self.SPILL.get(home, ()) if spill else ())):
                    if target in places and len(chosen[target]) < places[target]:
                        chosen[target].append(p)
                        taken.add(id(p))
                        break

        for eligible in (lambda p: p.id in recent, started, lambda p: True):
            fill(eligible, spill=False)
            fill(eligible, spill=True)
        eleven = [p for g, _ in self.XI_PLACES for p in chosen[g]]

        rest = [p for p in pool if id(p) not in taken]
        keepers = [p for p in rest if group(p) == "Goalkeeper"][:1]
        subs = keepers + [p for p in rest if p not in keepers]
        return (eleven + subs)[:PLAYERS_PER_TEAM]

    def _distinct_names(self, players: list[Player]) -> list[str]:
        """Each player's ROM name, told apart within the team. Players the
        surname alone would merge (Gabriel Silva, Lucas Silva) all become
        initial and surname ("G.Silva"), or, when that still merges them (Vitor
        Hugo, Victor Hugo), their first names; one-word names get a digit."""
        names = [self._format_player_name(p) for p in players]
        shared: dict[str, list[int]] = {}
        for i, name in enumerate(names):
            shared.setdefault(name, []).append(i)
        for name, idx in shared.items():
            if len(idx) < 2:
                continue
            words = [(_to_ascii(players[i].name) or name).split() for i in idx]
            others = set(names) - {name}
            picks: list[str | None] = []
            for make in (lambda w: f"{w[0][0]}.{w[-1]}"[:8], lambda w: w[0][:8]):
                picks = [make(w) if len(w) > 1 else None for w in words]
                if None not in picks and len(set(picks)) == len(picks) and not others & set(picks):
                    break
            else:
                picks = [name, *(f"{name[:7]}{k}" for k in range(2, len(idx) + 1))]
            for i, pick in zip(idx, picks, strict=True):
                names[i] = str(pick)
        return names

    def _format_player_name(self, player: Player) -> str:
        """Build a ROM-friendly name from a Player, 8 characters max: the first
        name's initial and the surname ("I.Gomes") when that fits, else the
        surname."""
        display = _to_ascii(player.name) if player.name else ""
        if not display:
            last = _to_ascii(player.last_name) if player.last_name else ""
            return (last or "PLAYER")[:8]

        words = display.split()
        if len(words) == 1:
            return display[:8]

        surname = words[-1]
        if len(surname) + 2 <= 8:
            return f"{words[0][0]}.{surname}"
        return surname[:8]
