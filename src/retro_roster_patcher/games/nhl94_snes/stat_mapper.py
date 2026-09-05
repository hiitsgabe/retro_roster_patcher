"""Maps NHL season stats onto NHL94's 0-6 attribute scale, falling back to
position defaults when a player has no stat line."""

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
    """Map a value within [low, high] onto the game's 0-6 scale."""
    if high <= low:
        return 3
    ratio = (value - low) / (high - low)
    return _clamp(round(ratio * 6))


@dataclass
class NHL94StatMapper:
    def map_player(
        self,
        player: Player,
        team_abbrev: str,
        stats: dict | None = None,
    ) -> NHL94PlayerRecord:
        pos = player.position.upper() if player.position else "C"
        is_goalie = pos == "G"

        if stats:
            attrs = self._map_stats(stats, pos, is_goalie)
        else:
            # Copied, not shared: `attributes` is public and mutable, so the module
            # constant must not be handed out.
            attrs = replace(POSITION_DEFAULTS.get(pos, POSITION_DEFAULTS["C"]))

        jersey = player.number or 1

        if player.weight > 0:
            weight_class = self._map_weight(int(player.weight))
        else:
            weight_class = 7  # 196 lbs

        hand = 0  # 0=L, 1=R
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
        """Per-season stat ranges used for scaling:

        G: 0-50, A: 0-70, PTS: 0-120, +/-: -30..+40
        PIM: 0-120, SV%: .880-.930, GAA: 2.0-3.5
        """
        g = float(stats.get("G", 0) or 0)
        a = float(stats.get("A", 0) or 0)
        pts = float(stats.get("PTS", 0) or 0)
        pm = float(stats.get("+/-", 0) or 0)
        pim = float(stats.get("PIM", 0) or 0)

        if is_goalie:
            # Upstream's behaviour, known wrong, preserved for byte fidelity: `SV%` is
            # read as a bare float against a fraction window, so a feed reporting `91.2`
            # rather than `0.912` saturates `agility` at 6. Do not add a units guard.
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

        # points as a rate proxy, scaled against an ~82-game season
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
        """Build the roster in ROM order, goalies first:

        Goalies (2)       - indices 0..1
        Forwards (N)      - indices 2..2+N-1, LW/C/RW per line
        Defencemen (M)    - indices 2+N..2+N+M-1

        The header's line assignments reference players by absolute index and
        assume exactly this ordering.
        """
        stats = stats or {}
        max_players = num_goalies + num_forwards + num_defensemen

        def sort_key(p: Player) -> float:
            """Higher is better: PTS for skaters, SV% for goalies."""
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

        selected_goalies = goalies[:num_goalies]

        # forwards in line order, LW/C/RW per line
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

        extras = [f for f in all_fwd if id(f) not in used_fwd]
        forwards.extend(extras[:num_extras])
        forwards = forwards[:num_forwards]

        defense = defensemen[:num_defensemen]

        # ROM order: G, then F, then D
        selected = selected_goalies + forwards + defense

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
        return MODERN_NHL_TO_NHL94.get(team_abbrev.upper())
