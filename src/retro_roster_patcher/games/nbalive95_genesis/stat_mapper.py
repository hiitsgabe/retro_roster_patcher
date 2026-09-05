"""Stat mapping for NBA Live 95 patcher.

Maps NBA player stats from ESPN API to NBA Live 95's 0-99 attribute scale.
Uses real season stats (PTS, REB, AST, STL, BLK, FG%, 3P%, FT%, etc.)
with position-based defaults as fallback.

References:
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
    """Map a value within [low, high] to 25-99 scale."""
    if high <= low:
        return 50
    ratio = (value - low) / (high - low)
    return _clamp(round(25 + ratio * 74))


# Default ratings by position (25-99 scale)
# [goals,3pt,FT,dunk,stl,blk,oreb,dreb,pass,oaware,daware,spd,quick,jump,drib,str]
#
# Each value is a plain list of ints and `map_player` copies it with `list(...)`
# before it reaches a record, so the shared-mutable-default bug the Genesis and
# SNES NHL 94 ports both had is not present here. Do not "fix" it.
POSITION_DEFAULTS = {
    POSITION_PG: [50, 50, 65, 35, 55, 30, 30, 35, 75, 60, 55, 75, 75, 45, 75, 35],
    POSITION_SG: [55, 55, 65, 50, 50, 30, 30, 35, 50, 60, 50, 65, 65, 55, 55, 40],
    POSITION_SF: [55, 50, 55, 55, 45, 40, 45, 50, 45, 55, 50, 55, 55, 55, 50, 55],
    POSITION_PF: [55, 35, 55, 60, 35, 50, 55, 65, 35, 55, 55, 45, 45, 55, 35, 70],
    POSITION_C: [55, 30, 55, 55, 30, 65, 55, 70, 35, 55, 60, 40, 40, 50, 30, 80],
}


class NBALive95StatMapper:
    """Maps NBA API player data to NBA Live 95 ROM attributes."""

    def map_player(
        self,
        player: Player,
        stats: dict | None = None,
    ) -> NBALive95PlayerRecord:
        """Map an NBA player to an NBA Live 95 player record.

        PRESERVED, and the reasoning is recorded here because it looks from the
        outside like the `skin_color` / `hair_style` defect and is not.

        `season_stats` is never supplied, so every record leaves here with
        `NBALive95PlayerRecord`'s default of 17 zeros and
        `rom_writer.write_player` writes all 34 bytes of them over the 1994
        season line the cartridge shipped with. Three things could be done with
        those bytes and zero is the best of them:

        * **Fill them from the provider.** Not possible. The 17 fields are
          season *totals* -- games, minutes, makes, attempts, rebounds, fouls --
          and ESPN's team-leaders endpoint publishes per-game averages. Without
          a games-played figure, which it does not publish either, no total can
          be recovered from an average; multiplying by a guessed games count
          would put fabricated counting stats in a field the game displays as
          fact.
        * **Leave the ROM's bytes alone**, which is the call this port makes for
          `skin_color` and `hair_style` one commit earlier. Wrong here, and for
          the reason that makes the two fields different. There is no null skin
          tone, so writing 0 asserts something false and leaving the byte
          asserts nothing. There *is* a null season line, and it is 17 zeros:
          a player who has not played this game's season has played 0 games and
          scored 0 points. Leaving the bytes would attribute the previous
          occupant's 1994 totals -- 1 000 points, 400 rebounds -- to whoever now
          holds the slot, which is the one option of the three that puts a
          statement known to be false on the screen.
        * **Write the zeros**, which is what happens: the honest "no season yet"
          for a roster the patch has just replaced.

        So this is a limitation of the format meeting a limitation of the
        provider, not an error in anyone's arithmetic, and repairing it needs a
        provider that publishes season totals rather than a change here.
        `rom_writer.write_player`'s stat clamp is the half of it that *was*
        repairable, and was.
        """
        pos_str = self._normalize_position(player.position)
        pos_byte = POSITION_TO_BYTE.get(pos_str, POSITION_SF)

        last, first = self._split_name(player.name)

        # Height: ESPN provides height string or we default
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
        """Map real NBA stats to 16 ratings (25-99 scale).

        ESPN NBA leaders keys (per-game averages, percentages 0-100):
        PTS, REB, AST, STL, BLK, FG%, 3P%, FT%, ORPG, DRPG,
        3PM, MPG, PER, RAT, DBLDBL, PFPG.
        """
        pts = float(stats.get("PTS", 0) or 0)
        reb = float(stats.get("REB", 0) or 0)
        ast = float(stats.get("AST", 0) or 0)
        stl = float(stats.get("STL", 0) or 0)
        blk = float(stats.get("BLK", 0) or 0)
        oreb = float(stats.get("ORPG", stats.get("OREB", 0)) or 0)
        dreb = float(stats.get("DRPG", stats.get("DREB", 0)) or 0)
        to = float(stats.get("TO", stats.get("TOPG", 0)) or 0)

        # ESPN percentages are 0-100, convert to 0-1
        fg_pct = float(stats.get("FG%", 0) or 0) / 100.0
        three_pct = float(stats.get("3P%", 0) or 0) / 100.0
        ft_pct = float(stats.get("FT%", 0) or 0) / 100.0

        # goals (FG%): 0.380-0.550 -> 25-99
        goals = _scale(fg_pct, 0.380, 0.550)

        # 3pt (3P%): 0.250-0.420 -> 25-99
        three_pt = _scale(three_pct, 0.250, 0.420)

        # FT (FT%): 0.600-0.920 -> 25-99
        ft = _scale(ft_pct, 0.600, 0.920)

        # Dunking: position-based + athleticism
        dunk_base = {
            POSITION_C: 55,
            POSITION_PF: 60,
            POSITION_SF: 55,
            POSITION_SG: 40,
            POSITION_PG: 35,
        }
        dunk = _clamp(dunk_base.get(pos, 45) + (10 if fg_pct > 0.520 else 0))

        # Stealing: STL/game 0.3-2.0 -> 25-99
        stealing = _scale(stl, 0.3, 2.0)

        # Blocks: BLK/game 0.1-2.5 -> 25-99
        blocks = _scale(blk, 0.1, 2.5)

        # Off rebounds: OREB/game 0.3-3.5 -> 25-99
        off_reb = _scale(oreb, 0.3, 3.5)

        # Def rebounds: DREB/game 1.0-9.0 -> 25-99
        def_reb = _scale(dreb, 1.0, 9.0)

        # Passing: AST/game 1.0-10.0 -> 25-99
        passing = _scale(ast, 1.0, 10.0)

        # Offensive awareness: PTS/game
        off_awareness = _scale(pts, 5.0, 30.0)

        # Defensive awareness: STL + BLK + DREB composite
        def_composite = stl * 2 + blk * 1.5 + dreb * 0.5
        def_awareness = _scale(def_composite, 1.0, 12.0)

        # Speed: position-based + steals bonus
        speed_base = {
            POSITION_PG: 75,
            POSITION_SG: 65,
            POSITION_SF: 55,
            POSITION_PF: 40,
            POSITION_C: 35,
        }
        speed_bonus = 8 if stl > 1.2 else 0
        speed = _clamp(speed_base.get(pos, 50) + speed_bonus)

        # Quickness: STL + AST proxy
        quickness_val = stl * 2 + ast * 0.5
        quickness = _scale(quickness_val, 1.0, 8.0)

        # Jumping: BLK + athleticism
        jump_val = blk * 2 + (5 if fg_pct > 0.500 else 0)
        jumping = _scale(jump_val, 0.5, 8.0)

        # Dribbling: AST + low TO ratio
        to_ratio = to / ast if ast > 0 else 2.0
        drib_val = ast * 0.8 + max(0, 2.0 - to_ratio) * 2
        dribbling = _scale(drib_val, 1.0, 10.0)

        # Strength: rebounds + position
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
        """Build NBA Live 95 roster: 12 players.

        Pick top 12 by minutes played, sorted by position:
        2 PG, 2 SG, 2 SF, 2 PF, 2 C + 2 best remaining.

        DELIBERATE DIVERGENCE, in code that says what it does rather than in
        what it does. Upstream opened with

            # Filter out pitchers/non-basketball positions
            eligible = [p for p in players
                        if self._normalize_position(p.position)
                        in ("PG", "SG", "SF", "PF", "C")]
            if not eligible:
                eligible = players

        and the comment was false. `_normalize_position` answers `"SF"` for
        every string it does not recognise, so the membership test is true for
        every player alive and the comprehension is a copy: a squad of twelve
        goalkeepers was selected whole, in full, exactly as a squad of twelve
        guards would be. The `if not eligible` fallback was unreachable with it,
        since an empty comprehension needs an empty `players`, in which case the
        two are the same empty list anyway.

        The filter is replaced by the copy it was, rather than made real. Making
        it real would drop any player whose position string this game does not
        know -- and mapping an unknown position to small forward is a decision
        `map_player` makes on purpose, one line away, for the same input. A
        selection that silently discarded players the mapper is willing to map
        would be a new behaviour introduced under a comment fix, on the argument
        that someone might point a basketball patcher at a hockey roster.

        `list(players)` and not `players` itself: the sort below is in place, and
        upstream's fallback branch handed it the caller's own list.
        """
        stats = stats or {}

        def minutes_sort(p: Player) -> float:
            ps = stats.get(str(p.id), {})
            min_val = float(ps.get("MPG", ps.get("MIN", 0)) or 0)
            pts = float(ps.get("PTS", 0) or 0)
            # Use minutes as primary, points as tiebreaker
            return min_val * 100 + pts

        eligible = list(players)

        eligible.sort(key=minutes_sort, reverse=True)

        # Fill by position: 2 of each
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

        # Fill remaining 2 bench spots with best unused
        for p in eligible:
            if len(selected) >= 12:
                break
            if id(p) not in used:
                selected.append(p)
                used.add(id(p))

        # If we still don't have 12, pad from whoever's left
        for p in players:
            if len(selected) >= 12:
                break
            if id(p) not in used:
                selected.append(p)
                used.add(id(p))

        return selected[:12]

    def _normalize_position(self, position: str) -> str:
        """Normalize ESPN position strings to NBA Live 95 positions."""
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

        # Skip suffixes like Jr., Sr., III, IV
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
        """Estimate height in inches from position.

        ESPN Player model doesn't have height, so use position defaults.
        """
        defaults = {"PG": 74, "SG": 77, "SF": 79, "PF": 81, "C": 83}
        return defaults.get(pos, 78)

    def _estimate_weight(self, pos: str, player: Player) -> int:
        """Estimate weight in lbs from position.

        ESPN Player model has weight field (may be 0).
        """
        if player.weight > 0:
            return int(player.weight)
        defaults = {"PG": 190, "SG": 205, "SF": 220, "PF": 240, "C": 255}
        return defaults.get(pos, 220)

    def get_team_slot(self, team_abbrev: str) -> int | None:
        """Get NBA Live 95 ROM slot for a modern NBA team."""
        code = team_abbrev.upper()
        if code in NO_SLOT_TEAMS:
            return None
        return MODERN_NBA_TO_NBALIVE95.get(code)
