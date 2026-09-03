"""Stat mapping for NHL94 SNES patcher.

Maps NHL player stats from ESPN to NHL94's 0-6 attribute scale.
Uses real season stats (G, A, PTS, +/-, PIM, etc.) from the
ESPN team leaders endpoint when available, with position-based
defaults as fallback.
"""

from dataclasses import dataclass, replace

from ...sports.models import Player
from .models import (
    MODERN_NHL_TO_NHL94,
    NHL94PlayerAttributes,
    NHL94PlayerRecord,
)

# Default attributes by position (0-6 scale)
POSITION_DEFAULTS = {
    "C": NHL94PlayerAttributes(
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
    "LW": NHL94PlayerAttributes(
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
    "RW": NHL94PlayerAttributes(
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
    "D": NHL94PlayerAttributes(
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
    "G": NHL94PlayerAttributes(
        speed=2,
        agility=4,
        shot_power=2,
        shot_accuracy=2,
        stick_handling=3,
        pass_accuracy=2,
        off_awareness=2,
        def_awareness=3,
        checking=1,
        endurance=4,
        roughness=1,
        aggression=1,
    ),
}


def _clamp(val: int, lo: int = 0, hi: int = 6) -> int:
    return max(lo, min(hi, val))


def _scale(value: float, low: float, high: float) -> int:
    """Map a value within [low, high] to 0-6 scale."""
    if high <= low:
        return 3
    ratio = (value - low) / (high - low)
    return _clamp(round(ratio * 6))


@dataclass
class NHL94StatMapper:
    """Maps NHL API player data to NHL94 ROM attributes."""

    def map_player(
        self,
        player: Player,
        team_abbrev: str,
        stats: dict | None = None,
    ) -> NHL94PlayerRecord:
        """Map an ESPN Player to NHL94 player record.

        Args:
            player: Player with position C/LW/RW/D/G
            team_abbrev: NHL team abbreviation
            stats: Per-player stats dict from leaders endpoint
                   e.g. {"G": 26, "A": 22, "PTS": 48, ...}
        """
        pos = player.position.upper() if player.position else "C"
        is_goalie = pos == "G"

        if stats:
            attrs = self._map_stats(stats, pos, is_goalie)
        else:
            # DELIBERATE DIVERGENCE, the same one already made in
            # `games/nhl94_genesis/stat_mapper.py`: upstream handed out the
            # module-level `POSITION_DEFAULTS` entry itself.
            # `NHL94PlayerRecord.attributes` is public and
            # `NHL94PlayerAttributes` is a plain mutable dataclass, so every
            # statless player at a position shared one object with every other
            # one *and* with the default, and a single caller assignment
            # rewrote the defaults for the rest of the process. `_map_stats`
            # already builds a fresh instance on the other branch.
            attrs = replace(POSITION_DEFAULTS.get(pos, POSITION_DEFAULTS["C"]))

        jersey = player.number or 1

        # Weight from ESPN data, default 196 lbs
        if player.weight > 0:
            weight_class = self._map_weight(int(player.weight))
        else:
            weight_class = 7

        # Handedness from ESPN data
        hand = 0  # L
        if player.handedness == "R":
            hand = 1

        return NHL94PlayerRecord(
            name=player.name[:14],
            jersey_number=jersey,
            weight_class=weight_class,
            handedness=hand,
            is_goalie=is_goalie,
            attributes=attrs,
        )

    def _map_stats(self, stats: dict, pos: str, is_goalie: bool) -> NHL94PlayerAttributes:
        """Map real ESPN stats to NHL94 attributes.

        Stat ranges used for scaling (per-season):
          G: 0-50, A: 0-70, PTS: 0-120, +/-: -30..+40
          PIM: 0-120, SV%: .880-.930, GAA: 2.0-3.5
        """
        g = float(stats.get("G", 0) or 0)
        a = float(stats.get("A", 0) or 0)
        pts = float(stats.get("PTS", 0) or 0)
        pm = float(stats.get("+/-", 0) or 0)
        pim = float(stats.get("PIM", 0) or 0)

        if is_goalie:
            svp = float(stats.get("SV%", 0) or 0)
            gaa = float(stats.get("GAA", 3.0) or 3.0)
            return NHL94PlayerAttributes(
                speed=2,
                agility=_scale(svp, 0.880, 0.930),
                shot_power=2,
                shot_accuracy=2,
                stick_handling=3,
                pass_accuracy=2,
                off_awareness=2,
                def_awareness=_scale(3.5 - gaa, 0, 1.5),
                checking=1,
                endurance=4,
                roughness=1,
                aggression=1,
            )

        # Skater attributes
        # Points-per-game proxy via raw totals (scale to ~82 game season)
        off_rating = _scale(pts, 0, 90)

        base = POSITION_DEFAULTS.get(pos, POSITION_DEFAULTS["C"])

        return NHL94PlayerAttributes(
            speed=_clamp(base.speed + (1 if pts > 50 else 0)),
            agility=_clamp(base.agility + (1 if pts > 50 else 0)),
            shot_power=_scale(g, 0, 40),
            shot_accuracy=_scale(g, 0, 40),
            stick_handling=off_rating,
            pass_accuracy=_scale(a, 0, 55),
            off_awareness=off_rating,
            def_awareness=_scale(pm + 30, 0, 70),
            checking=base.checking,
            endurance=base.endurance,
            roughness=_scale(pim, 0, 80),
            aggression=_scale(pim, 0, 80),
        )

    def select_roster(
        self,
        players: list[Player],
        stats: dict | None = None,
        num_goalies: int = 2,
        num_forwards: int = 14,
        num_defensemen: int = 7,
    ) -> list[Player]:
        """Build NHL94 roster in ROM order: G, F, D.

        NHL94 ROM player order (goalies FIRST):
          Goalies (2)       - indices 0..1
          Forwards (N)      - indices 2..2+N-1
            Line 1: LW, C, RW
            Line 2: LW, C, RW
            Line 3: LW, C, RW
            (Line 4 + extras if N > 9)
          Defensemen (M)    - indices 2+N..2+N+M-1

        The ROM header's line assignments reference players by
        absolute index and assume this G+F+D ordering.

        Args:
            players: Full team roster from API
            stats: Per-player stats keyed by player ID
            num_goalies: Number of goalie slots (from ROM)
            num_forwards: Number of forward slots (from ROM)
            num_defensemen: Number of defense slots (from ROM)
        """
        stats = stats or {}
        max_players = num_goalies + num_forwards + num_defensemen

        def sort_key(p: Player) -> float:
            """Higher = better. Use PTS for skaters, SV% for goalies."""
            ps = stats.get(str(p.id), {})
            if p.position == "G":
                svp = float(ps.get("SV%", 0) or 0)
                return svp * 1000
            return float(ps.get("PTS", 0) or 0)

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

        # -- Goalies (best first) ------------------------------
        selected_goalies = goalies[:num_goalies]

        # -- Forwards in line order: LW, C, RW per line --------
        all_fwd = sorted(
            centers + left_wings + right_wings,
            key=sort_key,
            reverse=True,
        )

        num_lines = num_forwards // 3
        num_extras = num_forwards - (num_lines * 3)

        forwards = []
        used_fwd = set()
        lw_idx, c_idx, rw_idx = 0, 0, 0

        for _line in range(num_lines):
            # LW
            lw = None
            while lw_idx < len(left_wings):
                if id(left_wings[lw_idx]) not in used_fwd:
                    lw = left_wings[lw_idx]
                    lw_idx += 1
                    break
                lw_idx += 1
            if lw is None:
                for f in all_fwd:
                    if id(f) not in used_fwd:
                        lw = f
                        break
            if lw:
                used_fwd.add(id(lw))
                forwards.append(lw)

            # C
            c = None
            while c_idx < len(centers):
                if id(centers[c_idx]) not in used_fwd:
                    c = centers[c_idx]
                    c_idx += 1
                    break
                c_idx += 1
            if c is None:
                for f in all_fwd:
                    if id(f) not in used_fwd:
                        c = f
                        break
            if c:
                used_fwd.add(id(c))
                forwards.append(c)

            # RW
            rw = None
            while rw_idx < len(right_wings):
                if id(right_wings[rw_idx]) not in used_fwd:
                    rw = right_wings[rw_idx]
                    rw_idx += 1
                    break
                rw_idx += 1
            if rw is None:
                for f in all_fwd:
                    if id(f) not in used_fwd:
                        rw = f
                        break
            if rw:
                used_fwd.add(id(rw))
                forwards.append(rw)

        # Extra forward slots
        extras = [f for f in all_fwd if id(f) not in used_fwd]
        forwards.extend(extras[:num_extras])
        forwards = forwards[:num_forwards]

        # -- Defensemen (sorted by PTS) ------------------------
        defense = defensemen[:num_defensemen]

        # -- Assemble in ROM order: G + F + D ------------------
        selected = selected_goalies + forwards + defense

        # Fill any remaining slots from leftover players
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
        """Map real weight (lbs) to 0-14 weight class."""
        weight_class = (weight_pounds - 140) // 8
        return max(0, min(14, weight_class))

    def get_team_slot(self, team_abbrev: str) -> int | None:
        """Get NHL94 ROM slot for a modern NHL team."""
        return MODERN_NHL_TO_NHL94.get(team_abbrev.upper())
