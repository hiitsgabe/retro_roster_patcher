"""Maps NHL season stats onto NHL94's 0-6 attribute scale, falling back to
position defaults when a player has no stat line, and per stat when the line
lacks one (ESPN's team leaders list only each category's top players)."""

from dataclasses import dataclass, replace

from ...core.text import to_ascii
from ...sports.models import Player
from .models import (
    MODERN_NHL_TO_NHL94,
    NHL94GoalieAttributes,
    NHL94PlayerAttributes,
    NHL94PlayerRecord,
)
from .rom_writer import MAX_NAME_LENGTH, abbreviate

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
}


def _clamp(val: int, lo: int = 0, hi: int = 6) -> int:
    return max(lo, min(hi, val))


def _scale(value: float, low: float, high: float) -> int:
    """Map a value within [low, high] onto the game's 0-6 scale."""
    if high <= low:
        return 3
    ratio = (value - low) / (high - low)
    return _clamp(round(ratio * 6))


def _stat(stats: dict, key: str) -> float | None:
    """`stats[key]` as a number, or None when the line has no such stat."""
    try:
        return float(stats[key])
    except (KeyError, TypeError, ValueError):
        return None


# A goalie's save % and goals-against average are pulled toward these, a
# little under the league's norm, by `SHRINK_GAMES` games' worth: a backup's
# one-game 1.000 rates as a backup rather than the best goalie in the league.
SV_PRIOR = 0.895
GAA_PRIOR = 3.1
SHRINK_GAMES = 10

# The save % and goals-against ranges spread over 0-6. On 2025-26's numbers
# they put the starters' mean overall at 68 and the backups' near 50-60, where
# the cartridge has 67 and 53.
SV_RANGE = (0.865, 0.915)
GAA_RANGE = (3.6, 2.4)


#: Games a goalie's own numbers count for when his line has a save % but no
#: games or wins (ESPN lists wins for its top goalies only): enough to move his
#: rating a step and to rank him among such backups, not to make a relief
#: appearance's 1.000 a star.
NOMINAL_GAMES = 1


def _starts(stats: dict) -> float:
    """How much a goalie played: GP when the provider sends it (NHL API), else
    W (ESPN's leaders carry wins, not games)."""
    return (_stat(stats, "GP") if "GP" in stats else _stat(stats, "W")) or 0.0


def _weight(stats: dict) -> float:
    """The games a goalie's save % and goals-against count for against the prior."""
    return _starts(stats) or (NOMINAL_GAMES if _stat(stats, "SV%") is not None else 0.0)


def _shrunk(value: float | None, prior: float, games: float) -> float:
    if value is None:
        return prior
    return (value * games + prior * SHRINK_GAMES) / (games + SHRINK_GAMES)


def goalie_rank(stats: dict) -> tuple[float, float]:
    """Higher starts first: the game always starts the first goalie, so the
    starter is the one who played most, then the better (shrunk) save %."""
    return _starts(stats), _shrunk(_stat(stats, "SV%"), SV_PRIOR, _weight(stats))


def display_name(name: str) -> str:
    """The name folded to ASCII, at most `MAX_NAME_LENGTH` characters: a longer
    one loses its first name to an initial first."""
    name = " ".join(to_ascii(name).split())
    if len(name) > MAX_NAME_LENGTH:
        name = abbreviate(name)
    return name[:MAX_NAME_LENGTH]


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

        attrs: NHL94PlayerAttributes | NHL94GoalieAttributes
        if is_goalie:
            attrs = self._map_goalie(stats or {})
        elif stats:
            attrs = self._map_stats(stats, pos)
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
            name=display_name(player.name),
            jersey_number=jersey,
            weight_class=weight_class,
            handedness=hand,
            is_goalie=is_goalie,
            attributes=attrs,
            photo_url=player.photo_url,
        )

    def _map_stats(self, stats: dict, pos: str) -> NHL94PlayerAttributes:
        """Per-season stat ranges used for scaling:

        G: 0-40, A: 0-55, PTS: 0-90, +/-: -30..+40, PIM: 0-80

        A stat the line lacks keeps the position's default for what it drives.
        """
        base = POSITION_DEFAULTS.get(pos, POSITION_DEFAULTS["C"])
        g, a, pts, pm, pim = (_stat(stats, k) for k in ("G", "A", "PTS", "+/-", "PIM"))

        def scaled(value: float | None, low: float, high: float, default: int) -> int:
            return default if value is None else _scale(value, low, high)

        bonus = 1 if pts is not None and pts > 50 else 0
        return NHL94PlayerAttributes(
            speed=_clamp(base.speed + bonus),
            agility=_clamp(base.agility + bonus),
            shot_power=scaled(g, 0, 40, base.shot_power),
            shot_accuracy=scaled(g, 0, 40, base.shot_accuracy),
            stick_handling=scaled(pts, 0, 90, base.stick_handling),
            pass_accuracy=scaled(a, 0, 55, base.pass_accuracy),
            off_awareness=scaled(pts, 0, 90, base.off_awareness),
            def_awareness=scaled(None if pm is None else pm + 30, 0, 70, base.def_awareness),
            checking=base.checking,
            endurance=base.endurance,
            roughness=scaled(pim, 0, 80, base.roughness),
            aggression=scaled(pim, 0, 80, base.aggression),
        )

    def _map_goalie(self, stats: dict) -> NHL94GoalieAttributes:
        """Agility and the four save zones from save %, awareness from
        goals-against, both shrunk by games played (`SV_RANGE`, `GAA_RANGE`).
        Puck control follows the two, and speed is 3 or 4, the cartridge's goalie
        range.
        """
        games = _weight(stats)
        agility = _scale(_shrunk(_stat(stats, "SV%"), SV_PRIOR, games), *SV_RANGE)
        gaa = _shrunk(_stat(stats, "GAA"), GAA_PRIOR, games)
        awareness = _scale(GAA_RANGE[0] - gaa, 0, GAA_RANGE[0] - GAA_RANGE[1])
        return NHL94GoalieAttributes(
            agility=agility,
            speed=4 if agility + awareness >= 7 else 3,
            def_awareness=awareness,
            puck_control=(agility + awareness + 1) // 2,
            stick_right=agility,
            stick_left=agility,
            glove_right=agility,
            glove_left=agility,
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

        Goalies (2)       - indices 0..1, most starts first
        Forwards (N)      - indices 2..2+N-1, LW/C/RW per line
        Defencemen (M)    - indices 2+N..2+N+M-1

        A position with fewer players than asked for gives a shorter block.

        The header's line assignments reference players by absolute index and
        assume exactly this ordering.
        """
        stats = stats or {}

        def sort_key(p: Player) -> float:
            """Higher is better: PTS for skaters."""
            return _stat(stats.get(str(p.id), {}), "PTS") or 0.0

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
            key=lambda p: goalie_rank(stats.get(str(p.id), {})),
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

        # ROM order: G, then F, then D. A short block stays short: a player
        # topped up after the defencemen would be read as one.
        return selected_goalies + forwards + defense

    def _map_weight(self, weight_pounds: int) -> int:
        """Weight class = (lbs - 140) / 8, clamped to 0-14."""
        weight_class = (weight_pounds - 140) // 8
        return max(0, min(14, weight_class))

    def get_team_slot(self, team_abbrev: str) -> int | None:
        return MODERN_NHL_TO_NHL94.get(team_abbrev.upper())
