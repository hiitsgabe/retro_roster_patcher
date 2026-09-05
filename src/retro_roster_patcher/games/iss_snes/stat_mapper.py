"""Maps real-world player stats to ISS's attribute scales.

ISS has a simpler attribute system than WE2002:
- Speed: 1-16 (stored as complex byte encoding)
- Shooting: 1-15 (odd values only, 3-bit encoding)
- Stamina: 1-16 (stored as nibble + 1)
- Technique: 1-15 (odd values only, 3-bit encoding)

Players are mapped from the provider's data using percentile ranking.

**`speed` and `stamina` are equal for every player the provider measured.**
Both are computed from `minutes / appearances` by the same lambda in
`_compute_percentiles`, over the same skip predicate -- `CATEGORY_INPUTS` names
the same two fields for both -- and both are then mapped by
`_percentile_to_speed`, so on that path the two numbers cannot differ. Upstream's
own comment on the first of them says "Proxy: endurance", which is what stamina
is; ISS speed is not derived from anything the provider reports. The four
attributes therefore carry three degrees of freedom there.

*Not* on the other path, and this docstring used to say "for every player" and
be wrong about it. A player with no stats, or with no appearances, gets
`_fallback_attributes`, and all four of `FALLBACK_ATTRS`' rows give speed and
stamina different values -- a goalkeeper 6 and 8, a defender 8 and 9, a
midfielder 8 and 10, an attacker 10 and 7 -- which the age adjustment then moves
by different amounts again. So the collapse is a property of the ranking, not of
the record.

PRESERVED, and not for want of looking. There is nothing to build an ISS speed
out of. `PlayerStats` carries twenty fields and ESPN, the only provider this
game has, fills every one of them except duels and dribbles -- see
`SOCCER_UNSUPPLIED_STATS` -- and none of what remains measures pace: goals,
assists, shots, passes, pass accuracy, tackles, interceptions, blocks, fouls
either way, cards, appearances, minutes, lineups. Dribbles are the one field on
the object that would come close and they are exactly one of the two ESPN does
not report. Deriving speed from anything else on that list would be a judgement
about how the game ought to play, dressed as a defect fix, applied to 405
players on every patched cartridge and checkable by nobody.

`_speed_to_rom`'s own defect, which sent the value 8 to the slowest byte in the
game, is a separate thing and is fixed; see `rom_writer`.
"""

from __future__ import annotations

from ...sports.models import Player, PlayerStats, TeamRoster
from .models import (
    PLAYERS_PER_TEAM,
    ISSPlayerAttributes,
    ISSPlayerRecord,
    ISSTeamRecord,
)
from .rom_writer import _to_ascii


def _any_unsupplied(stats: PlayerStats, fields: tuple[str, ...]) -> bool:
    """Did the provider that built `stats` decline to measure any of `fields`?"""
    return any(name in stats.unsupplied for name in fields)


class ISSStatMapper:
    """Maps real-world player stats to ISS attribute scales."""

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

    #: The `PlayerStats` fields each category's formula cannot do without.
    #:
    #: The same instrument `games/we2002/stat_mapper.py` uses, and the same trap:
    #: a name misspelt here can never be found in `PlayerStats.unsupplied`, so
    #: the gate silently stops gating. `tests/games/iss_snes/test_stat_mapper.py`
    #: asserts every key is a category of `_compute_percentiles` and every value
    #: is a field of `PlayerStats`.
    #:
    #: A player one of these is filler for is left out of that category's
    #: ranking entirely rather than ranked on the zero his record carries, which
    #: would place him below every measured player and drag the denominator the
    #: measured ones are ranked against.
    CATEGORY_INPUTS = {
        "speed": ("minutes", "appearances"),
        "shooting": ("goals", "shots_on"),
        "stamina": ("minutes", "appearances"),
        "technique": ("passes_accuracy",),
    }

    #: Inputs a category is *better* with and survives without.
    #:
    #: ESPN measures none of `dribbles_success` / `dribbles_attempts` -- see
    #: `SOCCER_UNSUPPLIED_STATS` in `sports/espn.py` -- and it is the only
    #: provider this game has. The WE2002 answer, dropping the player from the
    #: category, is wrong here: `technique` has exactly one other input, so
    #: dropping every player leaves the category empty and `map_player`'s
    #: `.get(pid, 50)` gives all 405 of them the same rating. That is strictly
    #: worse than what upstream did.
    #:
    #: So the dribbling term is dropped from the *formula* instead, and the
    #: player keeps his place in the ranking on pass accuracy alone. Under ESPN
    #: that produces the identical ROM: upstream's expression is
    #: `(0 + passes_accuracy) / 2`, this one is `passes_accuracy`, and a
    #: percentile is a rank, which a strictly increasing transform does not
    #: move. What changes is that the code now says the half is missing.
    #:
    #: DECLARED QUALITY REGRESSION, not fixed here and not fixable here: with
    #: this provider `technique` is 100% pass-accuracy-driven, so a winger who
    #: beats his man and a centre-back who plays five-yard passes are separated
    #: by nothing the game calls technique.
    CATEGORY_OPTIONAL_INPUTS = {
        "technique": ("dribbles_success", "dribbles_attempts"),
    }

    def map_team_with_league_context(
        self,
        team_roster: TeamRoster,
        all_rosters: list[TeamRoster],
    ) -> ISSTeamRecord:
        """Map team using league-wide percentile normalization."""
        all_stats = {}
        for roster in all_rosters:
            for pid, ps in roster.player_stats.items():
                all_stats[pid] = ps

        percentiles = self._compute_percentiles(all_stats)

        # Select best 15 players
        best_15 = self._select_best_15(team_roster.players, team_roster.player_stats)

        iss_players = []
        for player in best_15:
            stats = team_roster.player_stats.get(player.id)
            attrs = self.map_player(player, stats, percentiles)
            rom_name = self._format_player_name(player)
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
        """Convert a real player's stats to ISS format."""
        if not stats or stats.appearances == 0:
            return self._fallback_attributes(player)

        pid = stats.player_id
        return ISSPlayerAttributes(
            speed=self._percentile_to_speed(percentiles.get("speed", {}).get(pid, 50)),
            shooting=self._percentile_to_shooting(percentiles.get("shooting", {}).get(pid, 50)),
            # The same lambda, the same skip predicate and the same table as
            # `speed` above, so this line and that one cannot produce different
            # numbers. PRESERVED rather than unified into one call: the two
            # categories stay separate so that a provider which one day measures
            # something ISS could call speed has a place to be read, and so that
            # the collapse is visible in the code rather than hidden behind a
            # shared variable. See the module docstring for why nothing this
            # provider reports is that measurement.
            stamina=self._percentile_to_speed(percentiles.get("stamina", {}).get(pid, 50)),
            technique=self._percentile_to_shooting(percentiles.get("technique", {}).get(pid, 50)),
        )

    def _technique_value(self, stats: PlayerStats) -> float:
        """Raw technique score: dribble success rate and pass accuracy, averaged.

        Falls back to pass accuracy alone when the provider does not measure
        dribbling. See `CATEGORY_OPTIONAL_INPUTS`.
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

        `n` is per-category and not `len(all_stats)`, because a player whose
        provider did not measure one of a category's required inputs is skipped
        for that category. With a provider that measures everything nothing is
        skipped and this is the same computation upstream ran.
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
        """Map percentile to ISS shooting/technique scale (odd 1-15)."""
        for threshold, rating in self.SHOOTING_TABLE:
            if percentile >= threshold:
                return rating
        return 1

    def _percentile_to_speed(self, percentile: float) -> int:
        """Map percentile to ISS speed/stamina scale (1-16)."""
        for threshold, rating in self.SPEED_TABLE:
            if percentile >= threshold:
                return rating
        return 1

    def _fallback_attributes(self, player: Player) -> ISSPlayerAttributes:
        """Generate attributes from position when no stats available."""
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
        """Determine if a player should be marked as 'special' (star player)."""
        if not stats:
            return False
        # Top performers: high goals or assists relative to appearances
        if stats.appearances < 5:
            return False
        goals_per_game = stats.goals / max(stats.appearances, 1)
        assists_per_game = stats.assists / max(stats.appearances, 1)
        return goals_per_game >= 0.5 or assists_per_game >= 0.4

    def _select_best_15(
        self,
        players: list[Player],
        player_stats: dict[int, PlayerStats] | None = None,
    ) -> list[Player]:
        """Select best 15 players ordered for ISS starting lineup.

        ISS uses the first 11 as starters, last 4 as subs.
        Starting 11 (4-4-2): 1 GK, 4 DF, 4 MF, 2 FW.
        Subs: 1 GK + best remaining (typically 1 MF, 2 FW).
        """
        stats = player_stats or {}

        def _sort_key(p: Player) -> tuple[int, int, int]:
            s = stats.get(p.id)
            if s:
                return (-s.lineups, -s.appearances, -s.minutes)
            return (0, 0, 0)

        by_position: dict[str, list[Player]] = {}
        for p in players:
            by_position.setdefault(p.position, []).append(p)
        for pos in by_position:
            by_position[pos].sort(key=_sort_key)

        gks = by_position.get("Goalkeeper", [])
        dfs = by_position.get("Defender", [])
        mfs = by_position.get("Midfielder", [])
        fws = by_position.get("Attacker", [])

        # Starting 11: 1 GK + 4 DF + 4 MF + 2 FW
        starters = []
        starters.extend(gks[:1])  # 1 GK
        starters.extend(dfs[:4])  # 4 DF
        starters.extend(mfs[:4])  # 4 MF
        starters.extend(fws[:2])  # 2 FW

        # Subs: backup GK first, then best remaining outfield
        subs = []
        subs.extend(gks[1:2])  # backup GK

        remaining = (
            dfs[4:]
            + mfs[4:]
            + fws[2:]
            + gks[2:]
            + [
                p
                for p in players
                if p.position not in by_position or p not in gks + dfs + mfs + fws
            ]
        )
        remaining.sort(key=_sort_key)
        subs.extend(remaining)

        squad = starters + subs

        # Fill any gaps if not enough players in certain positions
        used = set(id(p) for p in squad)
        extras = [p for p in players if id(p) not in used]
        extras.sort(key=_sort_key)
        squad.extend(extras)

        return squad[:PLAYERS_PER_TEAM]

    def _format_player_name(self, player: Player) -> str:
        """Build ROM-friendly 8-char name from a Player.

        ISS names are 8 characters max with ISS custom encoding.
        """
        display = _to_ascii(player.name) if player.name else ""
        if not display:
            last = _to_ascii(player.last_name) if player.last_name else ""
            return (last or "PLAYER")[:8]

        words = display.split()
        if len(words) == 1:
            return display[:8]

        # Use surname, capitalize
        surname = words[-1]
        return surname[:8]
