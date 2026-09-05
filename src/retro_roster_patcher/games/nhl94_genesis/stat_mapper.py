"""Maps NHL season stats onto NHL94's 0-6 attribute scale, falling back to
position defaults when a player has no stat line."""

import dataclasses

from ...sports.models import Player
from .models import (
    MODERN_NHL_TO_NHL94_GEN,
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
    "G": NHL94GenPlayerAttributes(
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


class NHL94GenStatMapper:
    def map_player(
        self,
        player: Player,
        team_abbrev: str,
        stats: dict | None = None,
    ) -> NHL94GenPlayerRecord:
        pos = player.position.upper() if player.position else "C"
        is_goalie = pos == "G"

        if stats:
            attrs = self._map_stats(stats, pos, is_goalie)
        else:
            # Copied, not shared: `attributes` is public and mutable, so the module
            # constant must not be handed out.
            attrs = dataclasses.replace(POSITION_DEFAULTS.get(pos, POSITION_DEFAULTS["C"]))

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
        )

    def _map_stats(self, stats: dict, pos: str, is_goalie: bool) -> NHL94GenPlayerAttributes:
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
            return NHL94GenPlayerAttributes(
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

        off_rating = _scale(pts, 0, 90)
        base = POSITION_DEFAULTS.get(pos, POSITION_DEFAULTS["C"])

        return NHL94GenPlayerAttributes(
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
