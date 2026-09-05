"""Maps NBA season stats onto NBA Live 95's 25-99 rating scale, falling back to
position defaults when a player has no stat line.

  - https://github.com/Team-95/rom-edit
"""

from ...sports.models import Player
from .models import (
    MODERN_NBA_TO_NBALIVE95,
    NO_SLOT_TEAMS,
    POSITION_C,
    POSITION_PF,
    POSITION_PG,
    POSITION_SF,
    POSITION_SG,
    POSITION_TO_BYTE,
    NBALive95PlayerRecord,
)


def _clamp(val: int, lo: int = 25, hi: int = 99) -> int:
    return max(lo, min(hi, val))


def _scale(value: float, low: float, high: float) -> int:
    """Map a value within [low, high] onto the game's 25-99 scale."""
    if high <= low:
        return 50
    ratio = (value - low) / (high - low)
    return _clamp(round(25 + ratio * 74))


# Default ratings by position, in the ROM's rating order:
# [goals,3pt,FT,dunk,stl,blk,oreb,dreb,pass,oaware,daware,spd,quick,jump,drib,str]
# `map_player` copies each with `list(...)`, so no record shares one.
POSITION_DEFAULTS = {
    POSITION_PG: [50, 50, 65, 35, 55, 30, 30, 35, 75, 60, 55, 75, 75, 45, 75, 35],
    POSITION_SG: [55, 55, 65, 50, 50, 30, 30, 35, 50, 60, 50, 65, 65, 55, 55, 40],
    POSITION_SF: [55, 50, 55, 55, 45, 40, 45, 50, 45, 55, 50, 55, 55, 55, 50, 55],
    POSITION_PF: [55, 35, 55, 60, 35, 50, 55, 65, 35, 55, 55, 45, 45, 55, 35, 70],
    POSITION_C: [55, 30, 55, 55, 30, 65, 55, 70, 35, 55, 60, 40, 40, 50, 30, 80],
}


class NBALive95StatMapper:
    def map_player(
        self,
        player: Player,
        stats: dict | None = None,
    ) -> NBALive95PlayerRecord:
        """`season_stats` is left at its default of 17 zeros, which the writer then
        writes over the cartridge's 1994 season line. The ROM's 17 fields are season
        totals and ESPN publishes per-game averages with no games-played figure, so
        no total can be recovered; zeros are the honest null season for a roster
        that has just been replaced.
        """
        pos_str = self._normalize_position(player.position)
        pos_byte = POSITION_TO_BYTE.get(pos_str, POSITION_SF)

        last, first = self._split_name(player.name)

        height = self._estimate_height(pos_str, player)
        weight = self._estimate_weight(pos_str, player)

        if stats:
            ratings = self._map_stats_to_ratings(stats, pos_byte)
        else:
            ratings = list(POSITION_DEFAULTS.get(pos_byte, POSITION_DEFAULTS[POSITION_SF]))

        return NBALive95PlayerRecord(
            name_last=last,
            name_first=first,
            jersey=player.number or 0,
            position=pos_byte,
            height_inches=height,
            weight_lbs=weight,
            experience=max(0, player.age - 21) if player.age > 0 else 0,
            ratings=ratings,
        )

    def _map_stats_to_ratings(self, stats: dict, pos: int) -> list[int]:
        """Map a stat line onto the 16 ratings.

        ESPN's NBA leaders keys are per-game averages with percentages on 0-100:
        PTS, REB, AST, STL, BLK, FG%, 3P%, FT%, ORPG, DRPG, 3PM, MPG, PER, RAT,
        DBLDBL, PFPG.
        """
        pts = float(stats.get("PTS", 0) or 0)
        reb = float(stats.get("REB", 0) or 0)
        ast = float(stats.get("AST", 0) or 0)
        stl = float(stats.get("STL", 0) or 0)
        blk = float(stats.get("BLK", 0) or 0)
        oreb = float(stats.get("ORPG", stats.get("OREB", 0)) or 0)
        dreb = float(stats.get("DRPG", stats.get("DREB", 0)) or 0)
        to = float(stats.get("TO", stats.get("TOPG", 0)) or 0)

        # ESPN percentages are 0-100
        fg_pct = float(stats.get("FG%", 0) or 0) / 100.0
        three_pct = float(stats.get("3P%", 0) or 0) / 100.0
        ft_pct = float(stats.get("FT%", 0) or 0) / 100.0

        goals = _scale(fg_pct, 0.380, 0.550)

        three_pt = _scale(three_pct, 0.250, 0.420)

        ft = _scale(ft_pct, 0.600, 0.920)

        dunk_base = {
            POSITION_C: 55,
            POSITION_PF: 60,
            POSITION_SF: 55,
            POSITION_SG: 40,
            POSITION_PG: 35,
        }
        dunk = _clamp(dunk_base.get(pos, 45) + (10 if fg_pct > 0.520 else 0))

        stealing = _scale(stl, 0.3, 2.0)

        blocks = _scale(blk, 0.1, 2.5)

        off_reb = _scale(oreb, 0.3, 3.5)

        def_reb = _scale(dreb, 1.0, 9.0)

        passing = _scale(ast, 1.0, 10.0)

        off_awareness = _scale(pts, 5.0, 30.0)

        def_composite = stl * 2 + blk * 1.5 + dreb * 0.5
        def_awareness = _scale(def_composite, 1.0, 12.0)

        speed_base = {
            POSITION_PG: 75,
            POSITION_SG: 65,
            POSITION_SF: 55,
            POSITION_PF: 40,
            POSITION_C: 35,
        }
        speed_bonus = 8 if stl > 1.2 else 0
        speed = _clamp(speed_base.get(pos, 50) + speed_bonus)

        quickness_val = stl * 2 + ast * 0.5
        quickness = _scale(quickness_val, 1.0, 8.0)

        jump_val = blk * 2 + (5 if fg_pct > 0.500 else 0)
        jumping = _scale(jump_val, 0.5, 8.0)

        to_ratio = to / ast if ast > 0 else 2.0
        drib_val = ast * 0.8 + max(0, 2.0 - to_ratio) * 2
        dribbling = _scale(drib_val, 1.0, 10.0)

        strength_val = reb * 0.8 + (1 if pos in (POSITION_C, POSITION_PF) else 0) * 2
        strength = _scale(strength_val, 1.0, 10.0)

        return [
            goals,
            three_pt,
            ft,
            dunk,
            stealing,
            blocks,
            off_reb,
            def_reb,
            passing,
            off_awareness,
            def_awareness,
            speed,
            quickness,
            jumping,
            dribbling,
            strength,
        ]

    def select_roster(
        self,
        players: list[Player],
        stats: dict | None = None,
    ) -> list[Player]:
        """Pick 12 by minutes played: 2 PG, 2 SG, 2 SF, 2 PF, 2 C, then the 2 best
        remaining.

        No position filter, deliberately: `_normalize_position` answers "SF" for
        every string it does not recognise, so one could never exclude anybody.
        """
        stats = stats or {}

        def minutes_sort(p: Player) -> float:
            ps = stats.get(str(p.id), {})
            min_val = float(ps.get("MPG", ps.get("MIN", 0)) or 0)
            pts = float(ps.get("PTS", 0) or 0)
            # minutes first, points as the tiebreaker
            return min_val * 100 + pts

        eligible = list(players)

        eligible.sort(key=minutes_sort, reverse=True)

        position_targets = {"PG": 2, "SG": 2, "SF": 2, "PF": 2, "C": 2}
        selected = []
        used = set()

        for pos, count in position_targets.items():
            filled = 0
            for p in eligible:
                if filled >= count:
                    break
                if id(p) in used:
                    continue
                if self._normalize_position(p.position) == pos:
                    selected.append(p)
                    used.add(id(p))
                    filled += 1

        # the 2 bench spots go to the best unused
        for p in eligible:
            if len(selected) >= 12:
                break
            if id(p) not in used:
                selected.append(p)
                used.add(id(p))

        for p in players:
            if len(selected) >= 12:
                break
            if id(p) not in used:
                selected.append(p)
                used.add(id(p))

        return selected[:12]

    def _normalize_position(self, position: str) -> str:
        """Map an ESPN position string onto one of the game's five."""
        pos = (position or "").upper().strip()
        pos_map = {
            "C": "C",
            "PF": "PF",
            "SF": "SF",
            "PG": "PG",
            "SG": "SG",
            # ESPN combo positions
            "G": "PG",
            "F": "SF",
            "F-C": "PF",
            "C-F": "C",
            "G-F": "SG",
            "F-G": "SF",
        }
        return pos_map.get(pos, "SF")

    def _split_name(self, full_name: str) -> tuple[str, str]:
        """Split 'First Last' into (Lastname, First)."""
        parts = full_name.strip().split()
        if not parts:
            return "Player", "A"

        if len(parts) == 1:
            return parts[0], ""

        first = parts[0]

        # drop suffixes: Jr., Sr., III, IV
        last_parts = []
        for p in parts[1:]:
            if p.rstrip(".").upper() in ("JR", "SR", "II", "III", "IV"):
                continue
            last_parts.append(p)

        if last_parts:
            last = " ".join(last_parts)
        else:
            last = parts[-1]

        return last, first

    def _estimate_height(self, pos: str, player: Player) -> int:
        """Height in inches, guessed from position: ESPN publishes none."""
        defaults = {"PG": 74, "SG": 77, "SF": 79, "PF": 81, "C": 83}
        return defaults.get(pos, 78)

    def _estimate_weight(self, pos: str, player: Player) -> int:
        """Weight in lbs, falling back to a position default when ESPN has none."""
        if player.weight > 0:
            return int(player.weight)
        defaults = {"PG": 190, "SG": 205, "SF": 220, "PF": 240, "C": 255}
        return defaults.get(pos, 220)

    def get_team_slot(self, team_abbrev: str) -> int | None:
        code = team_abbrev.upper()
        if code in NO_SLOT_TEAMS:
            return None
        return MODERN_NBA_TO_NBALIVE95.get(code)
