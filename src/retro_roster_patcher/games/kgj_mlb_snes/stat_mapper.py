"""Maps MLB season stats onto KGJ's ratings, on the cartridge's own scales.

Each rating falls back to its position default on its own when the stat it
comes from is missing or rests on too small a sample, so one missing stat never
zeroes a rating. The scales were fitted so a 2026 league spreads over each
rating the way the 1994 cartridge's 700 players do.
"""

import dataclasses
import unicodedata
from collections.abc import Callable
from typing import Any

from ...sports.models import Player
from . import looks
from .models import (
    BATTERS_PER_TEAM,
    LINEUP_POSITIONS,
    MODERN_MLB_TO_KGJ,
    RELIEVERS_PER_TEAM,
    STARTERS_PER_TEAM,
    KGJBatterAttributes,
    KGJPitcherAttributes,
    KGJPlayerRecord,
)

#: Below these a rate stat says more about luck than about the player.
MIN_PA = 100
MIN_IP = 20.0


def _stat(stats: dict, key: str) -> float | None:
    """`stats[key]` as a number, or None when it is missing or not one."""
    value = stats.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _lin(x: float, x0: float, x1: float, lo: int, hi: int) -> int:
    """`x` placed between `x0` (rating `lo`) and `x1` (rating `hi`), clamped.
    `x1 < x0` reads a stat where less is better."""
    return max(lo, min(hi, round(lo + (x - x0) / (x1 - x0) * (hi - lo))))


def _innings(ip: float) -> float:
    """ESPN writes 162 and 2/3 innings as 162.2."""
    whole = int(ip)
    return whole + round((ip - whole) * 10) / 3


# Default batter attributes by position. `map_batter` hands the record a
# `dataclasses.replace(...)` copy, never the instance itself.
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

# FAT on the cartridge's scale: its starters run 5-10 and its relievers 2-5.
PITCHER_DEFAULTS = {
    "SP": KGJPitcherAttributes(speed=6, control=6, fatigue=7),
    "RP": KGJPitcherAttributes(speed=6, control=5, fatigue=3),
}

#: Who fields, by the positions ESPN lists: a catcher, a first baseman, three
#: infielders and three outfielders (`patcher._lineup_positions` spreads each
#: group over its positions). A batter ESPN lists at DH competes for the
#: outfield (and then for DH), so a regular DH is never benched for his label.
_LINEUP_POOLS = (
    (("C",), 1),
    (("1B",), 1),
    (("2B", "3B", "SS", "IF"), 3),
    (("LF", "CF", "RF", "OF", "DH"), 3),
)
#: The catcher's and the infield's pools: in an eight-man lineup the bat that
#: sits comes from the others (first base, the outfield, the DH), so the
#: catcher, second, third and short keep their gloves.
_GLOVES = (_LINEUP_POOLS[0][0], _LINEUP_POOLS[2][0])

#: One of the club's last `LINEUP_GAMES` starts counts as this many season starts
#: when ranking who plays: a regular back from injury beats the part-timer who
#: covered for him, while a September call-up with ten starts still trails a
#: 120-start regular. ponytail: a fixed blend, fit by eye on the 2026 clubs.
RECENT_START_WEIGHT = 10

#: How the game bats every pitcher, whatever his record says (85:D15B): a spare
#: pitcher on the bench gets these, with .200, 1 HR and 5 RBI.
PITCHER_BATTING = KGJBatterAttributes(batting=2, power=1, speed=3, defense=3)


class KGJStatMapper:
    def map_batter(self, player: Player, stats: dict | None = None) -> KGJPlayerRecord:
        """`stats` is the player's ESPN batting line (`get_baseball_athlete_stats`).

        A pitcher here is a spare filling a short bench: he is written as an IF
        with the game's own pitcher batting, never as a P with fielder ratings.
        """
        stats = stats or {}
        if self.is_pitcher(player):
            pos = "IF"
            attrs = dataclasses.replace(PITCHER_BATTING)
            avg, hr, rbi = 200, 1, 5
        else:
            pos = self._normalize_position(player.position, is_pitcher=False)
            attrs = self._map_batter_stats(stats, pos)
            measured = _stat(stats, "AVG")
            avg = 250 if measured is None else round(measured * 1000)
            hr = int(_stat(stats, "HR") or 0)
            rbi = int(_stat(stats, "RBI") or 0)

        first, last = self._split_name(player.name)
        record = KGJPlayerRecord(
            first_initial=first,
            last_name=last,
            player_id=player.id,
            photo_url=player.photo_url,
            position=pos,
            jersey_number=player.number or 0,
            is_pitcher=False,
            bats=self._bats(player),
            throws=self._throws(player),
            batter_attrs=attrs,
            batting_avg=avg,
            home_runs=hr,
            rbi=rbi,
        )
        looks.fit(record, player.height, player.weight)
        return record

    def map_pitcher(
        self,
        player: Player,
        stats: dict | None = None,
        is_starter: bool = True,
    ) -> KGJPlayerRecord:
        """`stats` is the player's ESPN pitching line."""
        stats = stats or {}
        era = _stat(stats, "ERA")
        first, last = self._split_name(player.name)
        record = KGJPlayerRecord(
            first_initial=first,
            last_name=last,
            player_id=player.id,
            photo_url=player.photo_url,
            position="P",
            jersey_number=player.number or 0,
            is_pitcher=True,
            bats=self._bats(player),
            throws=self._throws(player),
            pitcher_attrs=self._map_pitcher_stats(stats, is_starter),
            wins=int(_stat(stats, "W") or 0),
            losses=int(_stat(stats, "L") or 0),
            era=400 if era is None else round(era * 100),
            saves=int(_stat(stats, "SV") or 0),
        )
        # His look at the plate and on the mound, both: pitchers bat.
        looks.fit(record, player.height, player.weight)
        return record

    def _map_batter_stats(self, stats: dict, pos: str) -> KGJBatterAttributes:
        """BAT from AVG (.190-.300 = 3-10), POW from isolated power (SLG - AVG,
        .070-.280 = 2-10), SPD from steals plus twice the triples per 600 PA (0
        is about 4, 50 is 10), DEF from the position, +1 past 120 games.
        """
        base = BATTER_DEFAULTS.get(pos, BATTER_DEFAULTS["CF"])
        pa = _stat(stats, "PA") or 0.0
        avg, slg, sb = _stat(stats, "AVG"), _stat(stats, "SLG"), _stat(stats, "SB")
        sample = pa >= MIN_PA
        batting = base.batting
        if sample and avg is not None:
            batting = _lin(avg, 0.190, 0.300, 3, 10)
        power = base.power
        if sample and avg is not None and slg is not None:
            power = _lin(slg - avg, 0.070, 0.280, 2, 10)
        speed = base.speed
        if sample and sb is not None:
            speed = _lin((sb + 2 * (_stat(stats, "3B") or 0)) / pa * 600, -4, 50, 3, 10)
        games = _stat(stats, "GP") or 0
        return KGJBatterAttributes(
            batting=batting,
            power=power,
            speed=speed,
            defense=min(10, base.defense + (games > 120)),
        )

    def _map_pitcher_stats(self, stats: dict, is_starter: bool) -> KGJPitcherAttributes:
        """SPD from K/9 (5.5-11.5 = 4-10), CON from BB/9 (5.0-1.8 = 4-10), FAT
        from innings per game: a starter's 4.5-7.0 = 5-10 (from 3 starts), a
        reliever's 0.9-2.0 = 2-5. FAT sets when he tires, so a starter never
        drops below 5, the cartridge's own floor.
        """
        base = PITCHER_DEFAULTS["SP" if is_starter else "RP"]
        ip = _innings(_stat(stats, "IP") or 0.0)
        if ip < MIN_IP:
            return dataclasses.replace(base)
        k, bb = _stat(stats, "K"), _stat(stats, "BB")
        games, starts = _stat(stats, "GP") or 0, _stat(stats, "GS") or 0
        fatigue = base.fatigue
        if is_starter and starts >= 3:
            fatigue = _lin(ip / games, 4.5, 7.0, 5, 10)
        elif not is_starter and games:
            fatigue = _lin(ip / games, 0.9, 2.0, 2, 5)
        return KGJPitcherAttributes(
            speed=base.speed if k is None else _lin(k / ip * 9, 5.5, 11.5, 4, 10),
            control=base.control if bb is None else _lin(bb / ip * 9, 5.0, 1.8, 4, 10),
            fatigue=fatigue,
        )

    def select_roster(
        self,
        players: list[Player],
        stats: dict | None = None,
    ) -> list[Player]:
        """The three groups of `select_roster_groups`, concatenated. Up to 25:

          [0-14]  = batters: the lineup in batting order, then the bench
          [15-19] = starting pitchers, the ace first
          [20-24] = relief pitchers, closer first

        Those counts are maxima, not guarantees, so on a short squad the list
        index is not the ROM slot. `map_rosters` places the groups itself.
        """
        batters, starters, relievers = self.select_roster_groups(players, stats)
        return batters + starters + relievers

    def select_roster_groups(
        self,
        players: list[Player],
        stats: dict | None = None,
        lineup: list[dict[str, Any]] | None = None,
        lineup_size: int = len(LINEUP_POSITIONS),
    ) -> tuple[list[Player], list[Player], list[Player]]:
        """The roster as `(batters, starters, relievers)`, each already ordered.

        `stats` maps a player id (str) to his ESPN line; `lineup` is what
        `EspnClient.get_baseball_lineup` returns, and `lineup_size` how many
        bat (9 in the AL, 8 in the NL). The group is the one place a player's
        kind is actually known, which is why `map_rosters` takes the groups and
        not the concatenated list.
        """
        stats = stats or {}
        lineup = lineup or []
        recent = {s["id"]: s.get("starts", 0) for s in lineup}

        def num(p: Player, key: str) -> float:
            return _stat(stats.get(str(p.id), {}), key) or 0.0

        pitchers = [p for p in players if self.is_pitcher(p)]
        # The regulars first: whoever started most, this season and lately.
        batters = sorted(
            (p for p in players if not self.is_pitcher(p)),
            key=lambda p: (num(p, "GS") + RECENT_START_WEIGHT * recent.get(p.id, 0), num(p, "PA")),
            reverse=True,
        )

        # ESPN's roster gives SP vs RP. The rotation goes by wins then innings,
        # the bullpen by saves then innings, so the closer and then the most
        # trusted arms come first.
        starters = [p for p in pitchers if (p.position or "").upper() == "SP"]
        relievers = [p for p in pitchers if (p.position or "").upper() != "SP"]
        starters.sort(key=lambda p: num(p, "W") * 1000 + num(p, "IP"), reverse=True)
        relievers.sort(key=lambda p: num(p, "SV") * 1000 + num(p, "IP"), reverse=True)

        selected_starters = starters[:STARTERS_PER_TEAM]
        selected_relievers = relievers[:RELIEVERS_PER_TEAM]

        # top up either group from whatever pitchers are left
        remaining_pitchers = starters[STARTERS_PER_TEAM:] + relievers[RELIEVERS_PER_TEAM:]
        while len(selected_starters) < STARTERS_PER_TEAM and remaining_pitchers:
            selected_starters.append(remaining_pitchers.pop(0))
        while len(selected_relievers) < RELIEVERS_PER_TEAM and remaining_pitchers:
            selected_relievers.append(remaining_pitchers.pop(0))

        selected_batters = self._select_position_players(
            batters, lineup, lineup_size, lambda p: num(p, "OPS")
        )
        return selected_batters, selected_starters, selected_relievers

    def _select_position_players(
        self,
        batters: list[Player],
        lineup: list[dict[str, Any]],
        lineup_size: int,
        ops: Callable[[Player], float],
    ) -> list[Player]:
        """Up to 15 batters: the starters in batting order, then the bench.

        `batters` comes most-used first, so the starters are the regulars, not
        call-ups: the top catcher, first baseman, three infielders and three
        outfielders (`map_rosters` then spreads them over the positions), and
        the next-most-used batter but a catcher as DH. An 8-man lineup sits the
        weakest bat (OPS) of the first baseman, outfielders and DH, the way an
        NL club without a DH would. They bat where the club has lately batted
        them (`lineup`); one it has not, where his OPS ranks among them.
        """

        def pos(p: Player) -> str:
            return self._normalize_position(p.position, False)

        starters: list[Player] = []
        gloves: list[Player] = []
        for positions, count in _LINEUP_POOLS:
            pool = [p for p in batters if pos(p) in positions and p not in starters]
            starters += pool[:count]
            gloves += pool[:count] if positions in _GLOVES else []
        starters += [p for p in batters if p not in starters and pos(p) != "C"][:1]  # the DH
        while len(starters) > lineup_size:
            # Of equal bats the last picked sits: with no stats, the DH.
            bats = [p for p in starters if p not in gloves] or starters
            starters.remove(min(reversed(bats), key=ops))
        spot = {s["id"]: s["order"] for s in lineup}
        by_ops = sorted(starters, key=ops, reverse=True)
        starters.sort(key=lambda p: spot.get(p.id, by_ops.index(p) + 1))
        return (starters + [p for p in batters if p not in starters])[:BATTERS_PER_TEAM]

    def is_pitcher(self, player: Player) -> bool:
        pos = (player.position or "").upper()
        return pos in ("P", "SP", "RP", "CL", "CP")

    def _normalize_position(self, position: str, is_pitcher: bool) -> str:
        """Map an ESPN position string onto one the ROM encodes."""
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

    def _bats(self, player: Player) -> int:
        """Record byte 0x0D's high nibble: 0 right, 1 left, 2 switch ('B')."""
        return {"L": 1, "B": 2, "S": 2}.get((player.bats or "").upper(), 0)

    def _throws(self, player: Player) -> int:
        """0x0D's low nibble (and a pitcher's 0x15): 0 right, 1 left. ESPN's
        occasional 'B' is written as right."""
        return 1 if (player.handedness or "").upper() == "L" else 0

    def _split_name(self, full_name: str) -> tuple[str, str]:
        """Split "First Last" into (initial, surname): 'J.D. Martinez' -> ('J',
        'MARTINEZ'), 'Ken Griffey Jr.' -> ('K', 'GRIFFEY').

        Accents become plain letters and apostrophes go ("O'Hearn" -> OHEARN),
        since the font has neither. The 8-character cap is a hard truncation,
        so "Yastrzemski" writes as "YASTRZEM".
        """
        plain = unicodedata.normalize("NFKD", full_name).encode("ascii", "ignore").decode()
        parts = plain.replace("'", "").strip().split()
        if not parts:
            return "A", "PLAYER"

        first_initial = parts[0][0].upper()

        if len(parts) == 1:
            last = parts[0].upper()[:8]
        else:
            # drop suffixes: Jr., Sr., III, IV
            last_parts = []
            for p in parts[1:]:
                if p.rstrip(".").upper() in ("JR", "SR", "II", "III", "IV"):
                    continue
                last_parts.append(p)
            if last_parts:
                last = last_parts[-1].upper()[:8]
            else:
                last = parts[-1].upper()[:8]

        # "McGWIRE": the charset's one lowercase letter exists for this
        if last.startswith("MC") and len(last) > 2:
            last = "M" + "c" + last[2:]

        return first_initial, last

    def get_team_slot(self, team_abbrev: str) -> int | None:
        return MODERN_MLB_TO_KGJ.get(team_abbrev.upper())
