"""Maps real-world player stats to ISS's attribute scales.

ISS has a simpler attribute system than WE2002:
- Speed: 1-16 (stored as complex byte encoding)
- Shooting: 1-15 (odd values only, 3-bit encoding)
- Stamina: 1-16 (stored as nibble + 1)
- Technique: 1-15 (odd values only, 3-bit encoding)

Players are mapped from the provider's data using percentile ranking.

`speed` and `stamina` are the same number for every ranked player: both come
from `minutes / appearances` and both go through `_percentile_to_speed`. Leave
it collapsed -- nothing ESPN reports measures pace, and deriving speed from
something else would be a judgement about how the game ought to play applied to
405 players. The collapse belongs to the ranking, not the record: the four
`FALLBACK_ATTRS` rows give speed and stamina different values.
"""

from __future__ import annotations

from ...sports.espn import LINEUP_GROUPS
from ...sports.models import Player, PlayerStats, TeamRoster
from .models import (
    PLAYERS_PER_TEAM,
    ISSPlayerAttributes,
    ISSPlayerRecord,
    ISSTeamRecord,
)
from .rom_writer import _to_ascii


def _any_unsupplied(stats: PlayerStats, fields: tuple[str, ...]) -> bool:
    return any(name in stats.unsupplied for name in fields)


class ISSStatMapper:
    # Map percentile -> ISS shooting/technique (odd 1-15 scale)
    SHOOTING_TABLE = [
        (95, 15),
        (85, 13),
        (70, 11),
        (50, 9),
        (35, 7),
        (20, 5),
        (10, 3),
        (0, 1),
    ]

    # Map percentile -> ISS speed/stamina (1-16 scale)
    SPEED_TABLE = [
        (95, 16),
        (88, 14),
        (75, 12),
        (60, 10),
        (45, 8),
        (30, 6),
        (15, 4),
        (5, 2),
        (0, 1),
    ]

    FALLBACK_ATTRS = {
        "Goalkeeper": dict(speed=6, shooting=3, stamina=8, technique=5),
        "Defender": dict(speed=8, shooting=5, stamina=9, technique=5),
        "Midfielder": dict(speed=8, shooting=7, stamina=10, technique=9),
        "Attacker": dict(speed=10, shooting=11, stamina=7, technique=9),
    }

    POSITION_CODES = {
        "Goalkeeper": 0,
        "Defender": 1,
        "Midfielder": 2,
        "Attacker": 3,
    }

    HAIR_BY_POSITION = {
        "Goalkeeper": 0,  # Short
        "Defender": 0,  # Short
        "Midfielder": 9,  # Mid length
        "Attacker": 4,  # Long straight
    }

    #: The `PlayerStats` fields each category's formula cannot do without. A
    #: player missing one is dropped from that category's ranking, not ranked on
    #: the zero his record carries. Spell the names exactly as on `PlayerStats`:
    #: a typo can never match `PlayerStats.unsupplied` and the gate silently
    #: stops gating.
    CATEGORY_INPUTS = {
        "speed": ("minutes", "appearances"),
        "shooting": ("goals", "shots_on"),
        "stamina": ("minutes", "appearances"),
        "technique": ("passes_accuracy",),
    }

    #: Inputs a category is *better* with and survives without. ESPN measures
    #: neither dribbling field, and it is the only provider this game has, so
    #: drop the dribbling *term* from the formula rather than the player from the
    #: ranking: `technique` has one other input, and dropping every player leaves
    #: the category empty and hands all 405 the same default rating.
    CATEGORY_OPTIONAL_INPUTS = {
        "technique": ("dribbles_success", "dribbles_attempts"),
    }

    def map_team_with_league_context(
        self,
        team_roster: TeamRoster,
        all_rosters: list[TeamRoster],
        order_as_given: bool = False,
    ) -> ISSTeamRecord:
        all_stats = {}
        for roster in all_rosters:
            for pid, ps in roster.player_stats.items():
                all_stats[pid] = ps

        percentiles = self._compute_percentiles(all_stats)

        # `order_as_given`: the caller's list as it stands; slot index is the
        # role in this ROM, so index 0 keeps goal.
        best_15 = (
            team_roster.players[:PLAYERS_PER_TEAM]
            if order_as_given
            else self._select_best_15(
                team_roster.players, team_roster.player_stats, team_roster.extra.get("lineup")
            )
        )

        iss_players = []
        for player, rom_name in zip(best_15, self._distinct_names(best_15), strict=True):
            stats = team_roster.player_stats.get(player.id)
            attrs = self.map_player(player, stats, percentiles)
            hair = self.HAIR_BY_POSITION.get(player.position, 0)
            iss_players.append(
                ISSPlayerRecord(
                    name=rom_name,
                    shirt_number=player.number or 1,
                    position=self.POSITION_CODES.get(player.position, 2),
                    hair_style=hair,
                    is_special=self._is_star_player(player, stats),
                    attributes=attrs,
                )
            )

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
        if not stats or stats.appearances == 0:
            return self._fallback_attributes(player)

        pid = stats.player_id
        return ISSPlayerAttributes(
            speed=self._percentile_to_speed(percentiles.get("speed", {}).get(pid, 50)),
            shooting=self._percentile_to_shooting(percentiles.get("shooting", {}).get(pid, 50)),
            # Same lambda, same table as `speed` above, so the two cannot
            # differ. Keep the categories separate anyway, so the collapse stays
            # visible and a provider that one day measures pace has a slot.
            stamina=self._percentile_to_speed(percentiles.get("stamina", {}).get(pid, 50)),
            technique=self._percentile_to_shooting(percentiles.get("technique", {}).get(pid, 50)),
        )

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
        self, all_stats: dict[int, PlayerStats]
    ) -> dict[str, dict[int, float]]:
        """Compute league-wide percentiles for each stat category.

        `n` is per-category and not `len(all_stats)`: a player whose provider did
        not measure one of a category's required inputs is skipped for it.
        """
        if not all_stats:
            return {}

        categories = {
            "speed": lambda s: s.minutes / max(s.appearances, 1),  # Proxy: endurance
            "shooting": lambda s: s.goals + s.shots_on * 0.3 if s.shots_on else s.goals,
            "stamina": lambda s: s.minutes / max(s.appearances, 1),
            "technique": self._technique_value,
        }

        percentiles: dict[str, dict[int, float]] = {}
        for cat_name, extract_fn in categories.items():
            inputs = self.CATEGORY_INPUTS[cat_name]
            raw_values = {}
            for pid, stats in all_stats.items():
                if _any_unsupplied(stats, inputs):
                    continue
                raw_values[pid] = extract_fn(stats)

            sorted_values = sorted(raw_values.values())
            n = len(sorted_values)
            if n == 0:
                percentiles[cat_name] = {}
                continue

            cat_percentiles = {}
            for pid, value in raw_values.items():
                below = sum(1 for v in sorted_values if v < value)
                cat_percentiles[pid] = (below / n) * 100
            percentiles[cat_name] = cat_percentiles

        return percentiles

    def _percentile_to_shooting(self, percentile: float) -> int:
        for threshold, rating in self.SHOOTING_TABLE:
            if percentile >= threshold:
                return rating
        return 1

    def _percentile_to_speed(self, percentile: float) -> int:
        for threshold, rating in self.SPEED_TABLE:
            if percentile >= threshold:
                return rating
        return 1

    def _fallback_attributes(self, player: Player) -> ISSPlayerAttributes:
        defaults = self.FALLBACK_ATTRS.get(player.position, self.FALLBACK_ATTRS["Midfielder"])
        attrs = ISSPlayerAttributes(**defaults)

        age = player.age
        if age and age < 23:
            attrs.speed = min(16, attrs.speed + 2)
            attrs.stamina = min(16, attrs.stamina + 1)
        elif age and age > 32:
            attrs.speed = max(1, attrs.speed - 2)
            attrs.stamina = max(1, attrs.stamina - 2)
            attrs.technique = min(15, attrs.technique + 2)

        return attrs

    def _is_star_player(self, player: Player, stats: PlayerStats | None) -> bool:
        if not stats:
            return False
        if stats.appearances < 5:
            return False
        goals_per_game = stats.goals / max(stats.appearances, 1)
        assists_per_game = stats.assists / max(stats.appearances, 1)
        return goals_per_game >= 0.5 or assists_per_game >= 0.4

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
        """Build a ROM-friendly name from a Player: surname, 8 characters max."""
        display = _to_ascii(player.name) if player.name else ""
        if not display:
            last = _to_ascii(player.last_name) if player.last_name else ""
            return (last or "PLAYER")[:8]

        words = display.split()
        if len(words) == 1:
            return display[:8]

        surname = words[-1]
        return surname[:8]
