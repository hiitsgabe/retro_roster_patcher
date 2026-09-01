"""Maps real-world player stats to WE2002's 1-9 attribute scale."""

from .models import (
    Player,
    PlayerStats,
    TeamRoster,
    WEPlayerAttributes,
    WEPlayerRecord,
    WETeamRecord,
)


class StatMapper:
    """Maps real-world player stats to WE2002's 1-9 attribute scale."""

    PERCENTILE_TABLE = [
        (95, 9),
        (85, 8),
        (70, 7),
        (50, 6),
        (35, 5),
        (20, 4),
        (10, 3),
        (3, 2),
        (0, 1),
    ]

    FALLBACK_ATTRS = {
        "Goalkeeper": dict(
            offensive=2,
            defensive=7,
            body_balance=6,
            stamina=6,
            speed=4,
            acceleration=4,
            pass_accuracy=5,
            shoot_power=3,
            shoot_accuracy=2,
            jump_power=7,
            heading=5,
            technique=4,
            dribble=3,
            curve=3,
            aggression=4,
        ),
        "Defender": dict(
            offensive=3,
            defensive=7,
            body_balance=6,
            stamina=6,
            speed=5,
            acceleration=5,
            pass_accuracy=5,
            shoot_power=4,
            shoot_accuracy=3,
            jump_power=6,
            heading=6,
            technique=4,
            dribble=3,
            curve=3,
            aggression=6,
        ),
        "Midfielder": dict(
            offensive=5,
            defensive=5,
            body_balance=5,
            stamina=7,
            speed=5,
            acceleration=5,
            pass_accuracy=7,
            shoot_power=5,
            shoot_accuracy=5,
            jump_power=5,
            heading=5,
            technique=6,
            dribble=6,
            curve=5,
            aggression=5,
        ),
        "Attacker": dict(
            offensive=7,
            defensive=3,
            body_balance=5,
            stamina=5,
            speed=6,
            acceleration=6,
            pass_accuracy=5,
            shoot_power=7,
            shoot_accuracy=7,
            jump_power=5,
            heading=5,
            technique=6,
            dribble=6,
            curve=5,
            aggression=5,
        ),
    }

    # Position code mapping
    POSITION_CODES = {
        "Goalkeeper": 0,
        "Defender": 1,
        "Midfielder": 2,
        "Attacker": 3,
    }

    def map_team_with_league_context(
        self,
        team_roster: TeamRoster,
        all_rosters: list[TeamRoster],
    ) -> WETeamRecord:
        """Map team using league-wide percentile normalization."""
        # Collect all player stats for percentile computation
        all_stats = {}
        for roster in all_rosters:
            for pid, ps in roster.player_stats.items():
                all_stats[pid] = ps

        percentiles = self._compute_percentiles(all_stats)

        # Select best 22 players, starters first
        best_22 = self._select_best_22(team_roster.players, team_roster.player_stats)

        # Map each player
        we_players = []
        for player in best_22:
            stats = team_roster.player_stats.get(player.id)
            attrs = self.map_player(player, stats, percentiles)
            rom_last, rom_first = self._format_player_name(player)
            we_players.append(
                WEPlayerRecord(
                    last_name=rom_last,
                    first_name=rom_first,
                    position=self.POSITION_CODES.get(player.position, 2),
                    shirt_number=player.number or 0,
                    attributes=attrs,
                )
            )

        from .rom_writer import _to_ascii

        return WETeamRecord(
            name=self._truncate_name(team_roster.team.name, 24),
            short_name=_to_ascii(
                team_roster.team.code[:3]
                if team_roster.team.code
                else team_roster.team.name[:3].upper()
            ),
            players=we_players,
        )

    def map_player(
        self,
        player: Player,
        stats: PlayerStats | None,
        percentiles: dict[str, dict[int, float]],
    ) -> WEPlayerAttributes:
        """Convert a real player's stats to WE2002 format."""
        if not stats or stats.appearances == 0:
            return self._fallback_attributes(player)

        pid = stats.player_id
        attrs = WEPlayerAttributes(
            offensive=self._percentile_to_rating(percentiles.get("offensive", {}).get(pid, 50)),
            defensive=self._percentile_to_rating(percentiles.get("defensive", {}).get(pid, 50)),
            body_balance=self._percentile_to_rating(
                percentiles.get("body_balance", {}).get(pid, 50)
            ),
            stamina=self._percentile_to_rating(percentiles.get("stamina", {}).get(pid, 50)),
            speed=self._estimate_speed(player),
            acceleration=self._estimate_speed(player),
            pass_accuracy=self._percentile_to_rating(
                percentiles.get("pass_accuracy", {}).get(pid, 50)
            ),
            shoot_power=self._percentile_to_rating(percentiles.get("shoot_power", {}).get(pid, 50)),
            shoot_accuracy=self._percentile_to_rating(
                percentiles.get("shoot_accuracy", {}).get(pid, 50)
            ),
            jump_power=self._estimate_jump(player),
            heading=self._estimate_heading(player),
            technique=self._percentile_to_rating(percentiles.get("technique", {}).get(pid, 50)),
            dribble=self._percentile_to_rating(percentiles.get("dribble", {}).get(pid, 50)),
            curve=self._estimate_curve(player),
            aggression=self._percentile_to_rating(percentiles.get("aggression", {}).get(pid, 50)),
        )
        return self._apply_position_adjustments(attrs, player.position)

    def _compute_percentiles(
        self, all_stats: dict[int, PlayerStats]
    ) -> dict[str, dict[int, float]]:
        """Compute league-wide percentiles for each stat category."""
        if not all_stats:
            return {}

        # Define how to compute each category's raw value
        categories = {
            "offensive": lambda s: (
                s.goals + s.assists * 0.7 + (s.shots_on * 0.3 if s.shots_on else 0)
            ),
            "defensive": lambda s: s.tackles_total + s.interceptions + s.blocks,
            "body_balance": lambda s: (s.duels_won / max(s.duels_total, 1)) * 100,
            "stamina": lambda s: s.minutes / max(s.appearances, 1),
            "pass_accuracy": lambda s: s.passes_accuracy,
            "shoot_power": lambda s: s.shots_total + s.goals,
            "shoot_accuracy": lambda s: (s.goals / max(s.shots_total, 1)) * 100,
            "technique": lambda s: (s.dribbles_success / max(s.dribbles_attempts, 1)) * 100,
            "dribble": lambda s: s.dribbles_success,
            "aggression": lambda s: s.fouls_committed + s.cards_yellow * 2 + s.cards_red * 5,
        }

        percentiles: dict[str, dict[int, float]] = {}
        for cat_name, extract_fn in categories.items():
            raw_values = {}
            for pid, stats in all_stats.items():
                raw_values[pid] = extract_fn(stats)

            sorted_values = sorted(raw_values.values())
            n = len(sorted_values)
            if n == 0:
                percentiles[cat_name] = {}
                continue

            cat_percentiles = {}
            for pid, value in raw_values.items():
                # Count how many values are below this one
                below = sum(1 for v in sorted_values if v < value)
                cat_percentiles[pid] = (below / n) * 100
            percentiles[cat_name] = cat_percentiles

        return percentiles

    def _percentile_to_rating(self, percentile: float) -> int:
        """Map percentile to 1-9 rating."""
        for threshold, rating in self.PERCENTILE_TABLE:
            if percentile >= threshold:
                return rating
        return 1

    def _apply_position_adjustments(
        self, attrs: WEPlayerAttributes, position: str
    ) -> WEPlayerAttributes:
        """Apply position-based stat adjustments."""
        if position == "Goalkeeper":
            attrs.defensive = min(9, attrs.defensive + 2)
            attrs.jump_power = min(9, attrs.jump_power + 2)
            attrs.offensive = min(4, attrs.offensive)
            attrs.shoot_accuracy = min(3, attrs.shoot_accuracy)
        elif position == "Defender":
            attrs.defensive = min(9, attrs.defensive + 1)
            attrs.heading = min(9, attrs.heading + 1)
        elif position == "Midfielder":
            attrs.pass_accuracy = min(9, attrs.pass_accuracy + 1)
            attrs.technique = min(9, attrs.technique + 1)
            attrs.stamina = min(9, attrs.stamina + 1)
        elif position == "Attacker":
            attrs.offensive = min(9, attrs.offensive + 1)
            attrs.shoot_accuracy = min(9, attrs.shoot_accuracy + 1)
            attrs.shoot_power = min(9, attrs.shoot_power + 1)

        # Clamp all values to [1, 9]
        for field_name in [
            "offensive",
            "defensive",
            "body_balance",
            "stamina",
            "speed",
            "acceleration",
            "pass_accuracy",
            "shoot_power",
            "shoot_accuracy",
            "jump_power",
            "heading",
            "technique",
            "dribble",
            "curve",
            "aggression",
        ]:
            val = getattr(attrs, field_name)
            setattr(attrs, field_name, max(1, min(9, val)))

        return attrs

    def _fallback_attributes(self, player: Player) -> WEPlayerAttributes:
        """Generate attributes from position + age when no stats available."""
        defaults = self.FALLBACK_ATTRS.get(player.position, self.FALLBACK_ATTRS["Midfielder"])
        attrs = WEPlayerAttributes(**defaults)

        age = player.age
        if age < 23:
            attrs.speed = min(9, attrs.speed + 1)
            attrs.acceleration = min(9, attrs.acceleration + 1)
            attrs.stamina = min(9, attrs.stamina + 1)
            attrs.technique = max(1, attrs.technique - 1)
        elif 31 <= age <= 33:
            attrs.speed = max(1, attrs.speed - 1)
            attrs.acceleration = max(1, attrs.acceleration - 1)
            attrs.stamina = max(1, attrs.stamina - 1)
            attrs.technique = min(9, attrs.technique + 1)
        elif age > 33:
            attrs.speed = max(1, attrs.speed - 2)
            attrs.stamina = max(1, attrs.stamina - 2)
            attrs.technique = min(9, attrs.technique + 1)

        return attrs

    def _estimate_speed(self, player: Player) -> int:
        """Estimate speed from position and age heuristics."""
        base = {"Goalkeeper": 4, "Defender": 5, "Midfielder": 5, "Attacker": 6}
        val = base.get(player.position, 5)
        if player.age < 25:
            val += 1
        elif player.age > 32:
            val -= 1
        return max(1, min(9, val))

    def _estimate_jump(self, player: Player) -> int:
        """Estimate jump power from position."""
        base = {"Goalkeeper": 7, "Defender": 6, "Midfielder": 5, "Attacker": 5}
        return base.get(player.position, 5)

    def _estimate_heading(self, player: Player) -> int:
        """Estimate heading from position."""
        base = {"Goalkeeper": 5, "Defender": 6, "Midfielder": 5, "Attacker": 5}
        return base.get(player.position, 5)

    def _estimate_curve(self, player: Player) -> int:
        """Estimate curve from position (wingers/playmakers higher)."""
        base = {"Goalkeeper": 3, "Defender": 3, "Midfielder": 5, "Attacker": 5}
        return base.get(player.position, 4)

    def _select_best_22(
        self,
        players: list[Player],
        player_stats: dict[int, PlayerStats] | None = None,
    ) -> list[Player]:
        """Select best 22 players ordered with starting XI first.

        Uses games.lineups (starting XI count) from API stats to sort
        players by importance within each position.  The first 11 slots
        form the default starting lineup (1 GK, 4 DF, 4 MF, 2 FW).
        """
        stats = player_stats or {}

        def _sort_key(p: Player) -> tuple:
            """Higher lineups first, then appearances, then minutes."""
            s = stats.get(p.id)
            if s:
                return (-s.lineups, -s.appearances, -s.minutes)
            return (0, 0, 0)

        # Squad targets: total roster composition
        squad_targets = {
            "Goalkeeper": 3,
            "Defender": 7,
            "Midfielder": 6,
            "Attacker": 6,
        }

        # Sort players by lineups within each position
        by_position: dict[str, list[Player]] = {}
        for p in players:
            by_position.setdefault(p.position, []).append(p)
        for pos in by_position:
            by_position[pos].sort(key=_sort_key)

        # Pick the full 22-man squad (sorted by importance within position)
        squad = []
        for pos, count in squad_targets.items():
            available = by_position.get(pos, [])
            squad.extend(available[:count])

        # Fill if under 22
        remaining = [p for p in players if p not in squad]
        remaining.sort(key=_sort_key)
        while len(squad) < 22 and remaining:
            squad.append(remaining.pop(0))

        # Now split into starting XI (first 11) and bench (rest 11)
        # Starting XI: 1 GK, 4 DF, 4 MF, 2 FW (default 4-4-2)
        xi_targets = {"Goalkeeper": 1, "Defender": 4, "Midfielder": 4, "Attacker": 2}
        squad_by_pos: dict[str, list[Player]] = {}
        for p in squad:
            squad_by_pos.setdefault(p.position, []).append(p)

        starting = []
        for pos in ["Goalkeeper", "Defender", "Midfielder", "Attacker"]:
            available = squad_by_pos.get(pos, [])
            count = xi_targets[pos]
            starting.extend(available[:count])

        bench = [p for p in squad if p not in starting]
        return (starting + bench)[:22]

    def _format_player_name(self, player: Player) -> tuple:
        """Build ROM-friendly (last_name, first_name) from a Player.

        Prefers the display name (the player's preferred/known name) and falls
        back to `last_name` when there is none. The game displays at most 8
        characters for player names.
        Rules:
        - Single word (mononym): use as-is → "HULK", "NEYMAR"
        - Two+ words: "F. Surname" → "V. Hugo", "G. Pique", "R. Veiga"

        Each of the three provider strings is stripped after conversion and
        before it is tested, so that a field holding nothing but whitespace
        counts as absent and the fallback behind it is reached. This is a
        DELIBERATE DIVERGENCE from the upstream this was ported from, whose body
        is otherwise byte-identical and which tested the unstripped strings for
        truthiness. That cost it three things:

          * a whitespace-only `display` skipped the `last_name` fallback, then
            `" ".split()` returned `[]` and `words[-1]` raised `IndexError` out
            of `map_rosters`, so one player aborted the whole patch;
          * where `last_name` held a usable surname it was unreachable, so the
            correct answer was discarded along with the run;
          * `"  Neymar  "` kept its padding through `display[:8]`, giving
            `"  Neyma"` — two of the eight ROM characters spent on spaces — and
            a whitespace `first_name` beat the `words[0]` fallback the same way.

        The reachable case is not a malformed payload. `_to_ascii` drops every
        character it cannot render and keeps the spaces between them, so any
        display name of two or more words in a non-Latin script — Cyrillic,
        Arabic, Hangul — arrives here as `" "`. A single-word one arrives as
        `""` and always took the fallback correctly, which is why this went
        unnoticed. `api_football` passes `name` through as `p.get("name", "")`
        with no cleaning of its own.

        The order matters and is not interchangeable: stripping the provider
        string before conversion would not help, because `"Ivanov Petrov"` in
        Cyrillic is not whitespace until `_to_ascii` has run.

        Do NOT "restore" the unstripped version in a port audit.

        The three `.strip()` calls are the whole divergence; every other line is
        upstream's. Nothing further is needed below them, because a stripped
        single-word `display` is its own `words[0]`, and a stripped non-empty
        `display` holds at least one non-whitespace character — and
        `str.split()` with no argument never yields an empty string — so `words`
        and `words[0]` are both non-empty by the time they are indexed.
        """
        from .rom_writer import _to_ascii

        display = _to_ascii(player.name).strip() if player.name else ""
        first = _to_ascii(player.first_name).strip() if player.first_name else ""

        if not display:
            last = _to_ascii(player.last_name).strip() if player.last_name else ""
            return (last or "")[:8], first[:8]

        words = display.split()

        if len(words) == 1:
            # Mononym: "HULK", "NEYMAR", "ENDRICK"
            return display[:8], ""

        # Multi-word: "F. Surname" — e.g. "V. Hugo", "G. Pique"
        surname = words[-1]
        initial = words[0][0]
        jersey = f"{initial}. {surname}"
        return jersey[:8], (first or words[0])[:8]

    def _truncate_name(self, name: str, max_bytes: int) -> str:
        """Smart truncation: strip diacritics and abbreviate if too long."""
        if not name:
            return ""
        from .rom_writer import _to_ascii

        ascii_name = _to_ascii(name)
        if len(ascii_name) <= max_bytes:
            return ascii_name
        return ascii_name[:max_bytes]
