"""Stat mapping for KGJ MLB patcher.

Maps MLB player stats from ESPN API to KGJ's 1-10 attribute scale.
Uses real season stats (AVG, HR, RBI, SB, ERA, W, SV, etc.) when available,
with position-based defaults as fallback.
"""

import dataclasses

from ...sports.models import Player
from .models import (
    BATTERS_PER_TEAM,
    HAND_LEFT,
    HAND_RIGHT,
    HAND_SWITCH,
    MODERN_MLB_TO_KGJ,
    RELIEVERS_PER_TEAM,
    STARTERS_PER_TEAM,
    KGJBatterAppearance,
    KGJBatterAttributes,
    KGJPitcherAppearance,
    KGJPitcherAttributes,
    KGJPlayerRecord,
)


def _clamp(val: int, lo: int = 1, hi: int = 10) -> int:
    return max(lo, min(hi, val))


def _scale(value: float, low: float, high: float) -> int:
    """Map a value within [low, high] to 1-10 scale."""
    if high <= low:
        return 5
    ratio = (value - low) / (high - low)
    return _clamp(round(ratio * 9) + 1)


# Default batter attributes by position.
#
# Every value here is a mutable `KGJBatterAttributes` instance, and `map_batter`
# hands the record `dataclasses.replace(...)` of it rather than the object
# itself. See the DELIBERATE DIVERGENCE note there.
BATTER_DEFAULTS = {
    "C": KGJBatterAttributes(batting=5, power=5, speed=3, defense=7),
    "1B": KGJBatterAttributes(batting=6, power=7, speed=3, defense=5),
    "2B": KGJBatterAttributes(batting=5, power=3, speed=6, defense=7),
    "3B": KGJBatterAttributes(batting=5, power=5, speed=4, defense=6),
    "SS": KGJBatterAttributes(batting=5, power=3, speed=6, defense=8),
    "LF": KGJBatterAttributes(batting=6, power=5, speed=6, defense=5),
    "CF": KGJBatterAttributes(batting=5, power=4, speed=8, defense=7),
    "RF": KGJBatterAttributes(batting=6, power=6, speed=5, defense=6),
    "DH": KGJBatterAttributes(batting=7, power=7, speed=3, defense=2),
    "IF": KGJBatterAttributes(batting=4, power=3, speed=5, defense=6),
    "OF": KGJBatterAttributes(batting=5, power=4, speed=6, defense=5),
}

PITCHER_DEFAULTS = {
    "SP": KGJPitcherAttributes(speed=6, control=6, fatigue=7),
    "RP": KGJPitcherAttributes(speed=6, control=5, fatigue=3),
    "CL": KGJPitcherAttributes(speed=7, control=6, fatigue=3),
}


class KGJStatMapper:
    """Maps MLB API player data to KGJ ROM attributes."""

    def map_batter(
        self,
        player: Player,
        stats: dict | None = None,
    ) -> KGJPlayerRecord:
        """Map an MLB batter to a KGJ player record."""
        pos = self._normalize_position(player.position, is_pitcher=False)
        hand = self._map_bat_hand(player.bats or player.handedness)

        if stats:
            attrs = self._map_batter_stats(stats, pos)
            avg = int(float(stats.get("AVG", 0.250) or 0.250) * 1000)
            hr = int(float(stats.get("HR", 0) or 0))
            rbi = int(float(stats.get("RBI", 0) or 0))
        else:
            # DELIBERATE DIVERGENCE: `dataclasses.replace` where upstream wrote
            # `BATTER_DEFAULTS.get(pos, BATTER_DEFAULTS["CF"])`. That handed the
            # module-level object itself to the record, so every stat-less
            # shortstop in all 28 teams shared one mutable
            # `KGJBatterAttributes` -- and shared it with `BATTER_DEFAULTS`, so
            # a single `record.batter_attrs.power = 9` rewrote the default for
            # the rest of the process. The `if stats` branch above already
            # builds a fresh instance, so only this branch aliased.
            attrs = dataclasses.replace(BATTER_DEFAULTS.get(pos, BATTER_DEFAULTS["CF"]))
            avg = 250
            hr = 0
            rbi = 0

        first, last = self._split_name(player.name)

        return KGJPlayerRecord(
            first_initial=first,
            last_name=last,
            position=pos,
            jersey_number=player.number or 0,
            is_pitcher=False,
            bat_hand=hand,
            batter_attrs=attrs,
            batter_appearance=self._default_batter_appearance(),
            batting_avg=avg,
            home_runs=hr,
            rbi=rbi,
        )

    def map_pitcher(
        self,
        player: Player,
        stats: dict | None = None,
        is_starter: bool = True,
    ) -> KGJPlayerRecord:
        """Map an MLB pitcher to a KGJ player record."""
        hand = self._map_bat_hand(player.bats or player.handedness)
        pitch_hand = 1 if player.handedness == "L" else 0

        if stats:
            attrs = self._map_pitcher_stats(stats, is_starter)
            wins = int(float(stats.get("W", 0) or 0))
            losses = int(float(stats.get("L", 0) or 0))
            era_val = float(stats.get("ERA", 4.00) or 4.00)
            era = int(era_val * 100)
            saves = int(float(stats.get("SV", 0) or 0))
        else:
            default_key = "SP" if is_starter else "RP"
            # DELIBERATE DIVERGENCE, the same one as in `map_batter` and for the
            # same reason: upstream wrote `PITCHER_DEFAULTS[default_key]`, so
            # every stat-less starter in the league shared one object with the
            # `"SP"` entry of the table. Note that `"CL"` is in the table and
            # nothing selects it -- `default_key` is only ever `"SP"` or `"RP"`.
            attrs = dataclasses.replace(PITCHER_DEFAULTS[default_key])
            wins = 0
            losses = 0
            era = 400
            saves = 0

        first, last = self._split_name(player.name)

        return KGJPlayerRecord(
            first_initial=first,
            last_name=last,
            position="P",
            jersey_number=player.number or 0,
            is_pitcher=True,
            bat_hand=hand,
            pitcher_attrs=attrs,
            pitcher_appearance=self._default_pitcher_appearance(),
            pitch_hand=pitch_hand,
            wins=wins,
            losses=losses,
            era=era,
            saves=saves,
        )

    def _map_batter_stats(self, stats: dict, pos: str) -> KGJBatterAttributes:
        """Map real batting stats to 1-10 ratings.

        Stat ranges for scaling (per-season):
          AVG: .200-.330, HR: 0-45, RBI: 0-130, SB: 0-50
          OPS: .600-1.000, H: 0-200

        `rbi` and `hits` are read out of `stats` and then used by nothing: the
        four ratings below are computed from AVG/OPS, HR/SLG, SB/3B and the
        position default. That is upstream's code unchanged.
        """
        avg = float(stats.get("AVG", 0.250) or 0.250)
        hr = float(stats.get("HR", 0) or 0)
        rbi = float(stats.get("RBI", 0) or 0)  # noqa: F841
        sb = float(stats.get("SB", 0) or 0)
        ops = float(stats.get("OPS", 0.700) or 0.700)
        hits = float(stats.get("H", 0) or 0)  # noqa: F841

        # BAT: based on batting average and OPS
        bat = _scale((avg * 0.6 + ops * 0.4 / 3), 0.200, 0.330)

        # POW: based on home runs and slugging
        slg = float(stats.get("SLG", 0.400) or 0.400)
        pow_r = _scale((hr / 45 * 0.7 + slg * 0.3), 0.0, 1.0)

        # SPD: based on stolen bases
        spd = _scale(sb, 0, 40)
        # Boost if many triples
        triples = float(stats.get("3B", 0) or 0)
        if triples >= 5:
            spd = _clamp(spd + 1)

        # DEF: use position default + small adjustment for experience
        base_def = BATTER_DEFAULTS.get(pos, BATTER_DEFAULTS["CF"]).defense
        games = float(stats.get("GP", 0) or 0)
        def_bonus = 1 if games > 120 else 0
        def_r = _clamp(base_def + def_bonus)

        return KGJBatterAttributes(
            batting=bat,
            power=pow_r,
            speed=spd,
            defense=def_r,
        )

    def _map_pitcher_stats(self, stats: dict, is_starter: bool) -> KGJPitcherAttributes:
        """Map real pitching stats to 1-10 ratings.

        ESPN leaders provides: ERA, W, K, SV, WHIP, QS, OBA, HLD.
        Does NOT provide K/9, BB/9, IP, GS.
        """
        era = float(stats.get("ERA", 4.00) or 4.00)
        k = float(stats.get("K", 0) or 0)
        whip = float(stats.get("WHIP", 1.30) or 1.30)
        w = float(stats.get("W", 0) or 0)
        qs = float(stats.get("QS", 0) or 0)

        # SPD: strikeout total as proxy for velocity/dominance
        if is_starter:
            spd = _scale(k, 60, 250)
        else:
            spd = _scale(k, 20, 90)

        # CON: based on WHIP (lower = better control) and ERA
        con_from_whip = _scale(1.60 - whip, 0.0, 0.70)
        con_from_era = _scale(6.0 - era, 0.0, 4.0)
        con = _clamp((con_from_whip + con_from_era) // 2)

        # FAT: based on quality starts (starters) or wins+saves (relievers)
        if is_starter:
            fat = _scale(qs, 5, 25)
            # Boost for high-win starters
            if w >= 15:
                fat = _clamp(fat + 1)
        else:
            # Relievers have low fatigue
            sv = float(stats.get("SV", 0) or 0)
            fat = _clamp(3 + (1 if sv > 20 else 0))

        return KGJPitcherAttributes(speed=spd, control=con, fatigue=fat)

    def select_roster(
        self,
        players: list[Player],
        stats: dict | None = None,
    ) -> list[Player]:
        """The three groups of `select_roster_groups`, concatenated.

        Returns up to 25 players ordered:
          [0-14]  = batters (C, 1B, 2B, 3B, SS, LF, CF, RF, DH + bench)
          [15-19] = starting pitchers (sorted by wins)
          [20-24] = relief pitchers (closer first, then setup)

        Those three counts are the *maxima*, not guarantees, and where they are
        not met the slot index stops saying what kind of player is in it.
        `patcher.map_rosters` reads the kind off the index anyway, as upstream
        did, and `patcher._roster_type_for_slot` records why that is kept.
        `select_roster_groups` is the caller-facing way to get the fact itself.
        """
        batters, starters, relievers = self.select_roster_groups(players, stats)
        return batters + starters + relievers

    def select_roster_groups(
        self,
        players: list[Player],
        stats: dict | None = None,
    ) -> tuple[list[Player], list[Player], list[Player]]:
        """The roster as `(batters, starters, relievers)`, each already ordered.

        DELIBERATE DIVERGENCE in shape only, and it changes no written byte: the
        source had just the concatenated list, which `select_roster` still
        answers by joining these three. Nothing in the patch path calls this.

        It exists because the group is the one place a player's kind is actually
        known. Callers recover the kind by comparing a slot index against
        `BATTERS_PER_TEAM` and `STARTERS_PER_TEAM`, which is correct only while
        all three groups are full: a team with 12 non-pitchers puts its first
        three starting pitchers in slots 12, 13 and 14 and the index calls all
        three batters. `patcher.map_rosters` does exactly that, on purpose, and
        `patcher._roster_type_for_slot` carries the argument for keeping it.
        This method is what a caller that needs the fact rather than upstream's
        bytes should ask.

        The three groups are the same players in the same order the source
        produced, so for any roster at all -- not only a full one --
        concatenating them is byte-for-byte the source's answer.
        """
        stats = stats or {}

        pitchers = [p for p in players if self.is_pitcher(p)]
        batters = [p for p in players if not self.is_pitcher(p)]

        # Sort batters by OPS or hits
        def batter_sort(p: Player) -> float:
            ps = stats.get(str(p.id), {})
            ops = float(ps.get("OPS", 0) or 0)
            hits = float(ps.get("H", 0) or 0)
            return ops * 1000 + hits

        # Sort pitchers
        def starter_sort(p: Player) -> float:
            ps = stats.get(str(p.id), {})
            w = float(ps.get("W", 0) or 0)
            ip = float(ps.get("IP", 0) or 0)
            return w * 100 + ip

        def reliever_sort(p: Player) -> float:
            ps = stats.get(str(p.id), {})
            sv = float(ps.get("SV", 0) or 0)
            era = float(ps.get("ERA", 9.0) or 9.0)
            return sv * 100 + (10 - era)

        # Separate starters from relievers using position abbreviation
        # ESPN roster gives SP vs RP; stats (GS/IP) aren't in leaders endpoint
        starters = []
        relievers = []
        for p in pitchers:
            pos = (p.position or "").upper()
            if pos == "SP":
                starters.append(p)
            else:
                relievers.append(p)

        starters.sort(key=starter_sort, reverse=True)
        relievers.sort(key=reliever_sort, reverse=True)

        # Take top 5 starters, 5 relievers
        selected_starters = starters[:STARTERS_PER_TEAM]
        selected_relievers = relievers[:RELIEVERS_PER_TEAM]

        # Fill if we don't have enough
        remaining_pitchers = [
            p for p in starters[STARTERS_PER_TEAM:] + relievers[RELIEVERS_PER_TEAM:]
        ]
        while len(selected_starters) < STARTERS_PER_TEAM and remaining_pitchers:
            selected_starters.append(remaining_pitchers.pop(0))
        while len(selected_relievers) < RELIEVERS_PER_TEAM and remaining_pitchers:
            selected_relievers.append(remaining_pitchers.pop(0))

        # Select batters by position
        batters.sort(key=batter_sort, reverse=True)
        selected_batters = self._select_position_players(batters)

        return selected_batters, selected_starters, selected_relievers

    def _select_position_players(self, batters: list[Player]) -> list[Player]:
        """Select 15 batters filling required positions.

        Order: C, 1B, 2B, 3B, SS, LF, CF, RF, DH, then bench.
        """
        by_pos: dict[str, list[Player]] = {}
        for p in batters:
            pos = self._normalize_position(p.position, is_pitcher=False)
            by_pos.setdefault(pos, []).append(p)

        lineup_order = ["C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH"]
        selected = []
        used = set()

        # Fill each position with the best available
        for pos in lineup_order:
            candidates = by_pos.get(pos, [])
            for c in candidates:
                if id(c) not in used:
                    selected.append(c)
                    used.add(id(c))
                    break
            else:
                # No player at this position — will fill from bench later
                pass

        # Fill remaining slots with best unused batters
        for p in batters:
            if len(selected) >= BATTERS_PER_TEAM:
                break
            if id(p) not in used:
                selected.append(p)
                used.add(id(p))

        return selected[:BATTERS_PER_TEAM]

    def is_pitcher(self, player: Player) -> bool:
        """Check if a player is a pitcher.

        DELIBERATE DIVERGENCE, rename only: upstream named this `_is_pitcher`
        and then called `self.mapper._is_pitcher(player)` from `patcher.py`, so
        the leading underscore said "private" while the only other module in the
        package reached across for it. `patcher.map_rosters` still needs it --
        `select_roster`'s output length is not a reliable substitute, see its
        docstring -- so the name is corrected rather than the call.
        """
        pos = (player.position or "").upper()
        return pos in ("P", "SP", "RP", "CL", "CP")

    def _normalize_position(self, position: str, is_pitcher: bool) -> str:
        """Normalize ESPN position strings to KGJ positions."""
        pos = (position or "").upper()
        if is_pitcher:
            return "P"

        pos_map = {
            "C": "C",
            "1B": "1B",
            "2B": "2B",
            "3B": "3B",
            "SS": "SS",
            "LF": "LF",
            "CF": "CF",
            "RF": "RF",
            "DH": "DH",
            "OF": "OF",
            "IF": "IF",
            # ESPN sometimes uses these
            "SP": "P",
            "RP": "P",
            "CL": "P",
            "CP": "P",
            "P": "P",
        }
        return pos_map.get(pos, "OF")

    def _map_bat_hand(self, handedness: str | None) -> int:
        """Map batting handedness string to ROM value."""
        if not handedness:
            return HAND_RIGHT
        h = handedness.upper()
        if h == "L":
            return HAND_LEFT
        if h in ("S", "B"):
            return HAND_SWITCH
        return HAND_RIGHT

    def _split_name(self, full_name: str) -> tuple[str, str]:
        """Split 'First Last' into (initial, last_name).

        Handles names like 'J.D. Martinez' -> ('J', 'MARTINEZ')
        and 'Ken Griffey Jr.' -> ('K', 'GRIFFEY')

        The 8-character cap is a hard truncation with no ellipsis, so
        "Yastrzemski" writes as "YASTRZEM". `models.CHAR_TO_BYTE` then drops
        anything it does not map, accents included.
        """
        parts = full_name.strip().split()
        if not parts:
            return "A", "PLAYER"

        first_initial = parts[0][0].upper()

        if len(parts) == 1:
            last = parts[0].upper()[:8]
        else:
            # Use last meaningful part (skip Jr., Sr., III, etc.)
            last_parts = []
            for p in parts[1:]:
                if p.rstrip(".").upper() in ("JR", "SR", "II", "III", "IV"):
                    continue
                last_parts.append(p)
            if last_parts:
                last = last_parts[-1].upper()[:8]
            else:
                last = parts[-1].upper()[:8]

        # Handle Mc/Mac names — use lowercase c
        if last.startswith("MC") and len(last) > 2:
            last = "M" + "c" + last[2:]

        return first_initial, last

    def _default_batter_appearance(self) -> KGJBatterAppearance:
        return KGJBatterAppearance()

    def _default_pitcher_appearance(self) -> KGJPitcherAppearance:
        return KGJPitcherAppearance()

    def get_team_slot(self, team_abbrev: str) -> int | None:
        """Get KGJ ROM slot for a modern MLB team."""
        return MODERN_MLB_TO_KGJ.get(team_abbrev.upper())
