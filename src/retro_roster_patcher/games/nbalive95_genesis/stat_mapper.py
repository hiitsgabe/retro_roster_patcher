"""Maps a player's season onto NBA Live 95's ratings.

Each rating comes from one to three of his stats (`SOURCES`), ranked against
every player of his group (guards, forwards, centres) in the league fetched
(`LeagueStats`): the rating is the value at the same rank of the cartridge's own
spread of it (`RETAIL`), so the league's best passer gets the passing of the
cartridge's best and its typical one the cartridge's median. A rating none
of whose stats the line has keeps the group's default (its retail median), and
a player without a stat line keeps them all.

Counting stats are ranked per game, pulled toward the group's own rate by
`SHRINK_GAMES` games; a shooting percentage is pulled toward the group's by
`PCTS`' attempts. ESPN measures no speed, quickness, jumping, dunking or
strength, so those rank the nearest stats it has: lightness and assists,
assists and steals, blocks and offensive rebounds, blocks and free throws
drawn, rebounds and weight. Each pairing is the one that ranks the
cartridge's own 1993-94 lines closest to its ratings.

  - https://github.com/Team-95/rom-edit
"""

import bisect
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from ...core.hockey import shrunk, stat
from ...sports.models import Player
from .models import (
    MODERN_NBA_TO_NBALIVE95,
    POSITION_C,
    POSITION_PF,
    POSITION_PG,
    POSITION_SF,
    POSITION_SG,
    POSITION_TO_BYTE,
    RATING_NAMES,
    NBALive95PlayerRecord,
)

#: The game's five positions, in the order `select_roster` fills a lineup.
#: The positions of pointer slots 0-4, the starting five, as the retail rosters
#: order them (Ewing, Oakley, C. Smith, Starks, Harper). The pre-game intro
#: names each starter by his slot, not by his position byte (checked in
#: genesis_plus_gx, Oct 27 2026).
LINEUP = ("C", "PF", "SF", "SG", "PG")

#: ESPN labels an NBA player G, F or C. A G can play either guard spot and an F
#: either forward spot; `split_positions` picks which by list order.
COMBO_SPOTS = {"G": ("PG", "SG"), "F": ("SF", "PF")}

#: Where a starter of each ESPN label goes among the five: centres first.
_LABEL_ORDER = {"C": 0, "F": 1, "P": 1, "S": 2, "G": 2}

#: The cartridge starts pointer slots 0-4 (`LINEUP`).
STARTERS = 5

GROUP = {POSITION_PG: "G", POSITION_SG: "G", POSITION_SF: "F", POSITION_PF: "F", POSITION_C: "C"}

#: Each rating's deciles (0%, 10% ... 100%) over the cartridge's own 27 NBA
#: rosters, by group: 122 guards, 141 forwards, 61 centres. `range` is the
#: 0-15 level of `models.OFF_RANGE`, `stamina` byte 0x2A.
RETAIL: dict[str, dict[str, tuple[int, ...]]] = {
    "G": {
        "stamina": (68, 75, 75, 77, 80, 80, 83, 86, 88, 90, 96),
        "goals": (62, 70, 72, 75, 76, 77, 78, 80, 83, 86, 95),
        "three_pt": (55, 65, 70, 73, 75, 77, 80, 83, 85, 87, 95),
        "ft": (55, 67, 71, 75, 77, 80, 81, 83, 84, 86, 96),
        "dunking": (50, 55, 60, 62, 65, 67, 72, 74, 77, 85, 97),
        "stealing": (62, 70, 70, 72, 75, 75, 80, 82, 85, 90, 97),
        "blocks": (50, 55, 60, 60, 60, 60, 65, 65, 70, 72, 80),
        "off_reb": (50, 55, 60, 60, 60, 62, 65, 67, 70, 75, 85),
        "def_reb": (60, 62, 65, 65, 65, 67, 68, 70, 72, 75, 80),
        "passing": (64, 70, 72, 75, 75, 77, 80, 85, 87, 90, 98),
        "off_awareness": (60, 65, 67, 70, 70, 75, 75, 80, 82, 87, 95),
        "def_awareness": (60, 65, 67, 70, 72, 75, 75, 75, 80, 85, 95),
        "speed": (75, 80, 80, 82, 85, 85, 85, 88, 88, 90, 95),
        "quickness": (74, 80, 80, 81, 82, 83, 84, 85, 86, 88, 95),
        "jumping": (50, 60, 65, 67, 70, 72, 75, 77, 82, 85, 98),
        "dribbling": (67, 72, 75, 75, 76, 80, 82, 85, 85, 92, 97),
        "strength": (55, 62, 65, 65, 67, 70, 70, 72, 72, 75, 85),
        "range": (9, 10, 11, 12, 12, 12, 13, 13, 14, 15, 15),
    },
    "F": {
        "stamina": (60, 70, 73, 75, 75, 78, 80, 82, 85, 87, 96),
        "goals": (57, 69, 70, 72, 73, 75, 75, 78, 82, 85, 95),
        "three_pt": (55, 60, 60, 60, 60, 63, 65, 70, 75, 82, 95),
        "ft": (54, 59, 62, 67, 69, 72, 73, 75, 78, 80, 91),
        "dunking": (60, 67, 72, 74, 75, 80, 81, 84, 85, 90, 98),
        "stealing": (55, 60, 65, 65, 67, 70, 71, 75, 75, 80, 93),
        "blocks": (60, 65, 67, 70, 70, 72, 73, 75, 79, 84, 95),
        "off_reb": (55, 65, 66, 70, 72, 72, 75, 77, 82, 87, 99),
        "def_reb": (52, 67, 70, 72, 75, 75, 76, 80, 82, 87, 99),
        "passing": (55, 62, 65, 67, 67, 70, 70, 72, 75, 80, 88),
        "off_awareness": (55, 65, 65, 67, 70, 72, 75, 75, 80, 85, 97),
        "def_awareness": (50, 65, 65, 67, 70, 72, 75, 75, 78, 85, 98),
        "speed": (62, 70, 74, 75, 75, 78, 80, 80, 81, 82, 88),
        "quickness": (70, 77, 78, 79, 79, 80, 80, 81, 81, 83, 89),
        "jumping": (60, 65, 70, 72, 75, 75, 78, 80, 85, 90, 98),
        "dribbling": (55, 65, 65, 67, 70, 70, 72, 75, 75, 80, 85),
        "strength": (65, 67, 70, 72, 75, 75, 77, 80, 82, 87, 97),
        "range": (0, 6, 7, 8, 8, 8, 10, 10, 11, 12, 15),
    },
    "C": {
        "stamina": (60, 65, 67, 70, 70, 73, 75, 77, 80, 83, 95),
        "goals": (60, 65, 66, 70, 72, 72, 75, 76, 80, 84, 96),
        "three_pt": (50, 50, 55, 55, 55, 57, 60, 60, 60, 67, 87),
        "ft": (51, 55, 58, 60, 65, 68, 70, 72, 74, 78, 95),
        "dunking": (62, 70, 73, 77, 80, 81, 83, 85, 87, 95, 98),
        "stealing": (55, 60, 60, 60, 62, 65, 65, 67, 72, 75, 90),
        "blocks": (64, 71, 75, 75, 78, 80, 83, 85, 87, 92, 98),
        "off_reb": (60, 70, 72, 72, 75, 75, 79, 82, 85, 87, 98),
        "def_reb": (67, 72, 75, 75, 75, 78, 80, 82, 85, 90, 97),
        "passing": (50, 55, 60, 60, 60, 62, 65, 67, 70, 72, 90),
        "off_awareness": (55, 60, 60, 65, 65, 70, 70, 72, 75, 85, 97),
        "def_awareness": (55, 65, 67, 70, 72, 75, 77, 80, 85, 90, 97),
        "speed": (55, 62, 65, 68, 70, 70, 72, 75, 75, 78, 86),
        "quickness": (68, 71, 73, 75, 77, 78, 78, 79, 79, 80, 89),
        "jumping": (60, 65, 70, 70, 72, 74, 75, 79, 80, 89, 98),
        "dribbling": (55, 55, 60, 60, 62, 65, 65, 67, 70, 72, 82),
        "strength": (60, 72, 75, 75, 77, 80, 83, 85, 85, 90, 97),
        "range": (1, 3, 4, 4, 6, 6, 7, 7, 8, 10, 13),
    },
}

#: The stats each rating ranks; a rating with several averages their ranks.
SOURCES: dict[str, tuple[str, ...]] = {
    "goals": ("fg_pct", "points"),
    "three_pt": ("three_pct", "threes"),
    "ft": ("ft_pct",),
    "dunking": ("blocks", "free_throws"),
    "stealing": ("steals",),
    "blocks": ("blocks",),
    "off_reb": ("off_rebounds",),
    "def_reb": ("def_rebounds",),
    "passing": ("assists",),
    "off_awareness": ("points",),
    "def_awareness": ("steals", "blocks", "def_rebounds"),
    "speed": ("lightness", "assists"),
    "quickness": ("assists", "steals"),
    "jumping": ("blocks", "off_rebounds"),
    "dribbling": ("assists", "ball_security"),
    "strength": ("rebounds", "weight"),
    "stamina": ("minutes",),
    "range": ("three_attempts",),
}

#: A counting stat per game: ESPN's average, the leaders endpoint's, else the
#: season total over games played.
COUNTS: dict[str, tuple[str, str, str]] = {
    "points": ("avgPoints", "PTS", "points"),
    "rebounds": ("avgRebounds", "REB", "rebounds"),
    "off_rebounds": ("avgOffensiveRebounds", "ORPG", "offensiveRebounds"),
    "def_rebounds": ("avgDefensiveRebounds", "DRPG", "defensiveRebounds"),
    "assists": ("avgAssists", "AST", "assists"),
    "steals": ("avgSteals", "STL", "steals"),
    "blocks": ("avgBlocks", "BLK", "blocks"),
    "turnovers": ("avgTurnovers", "TO", "turnovers"),
    "threes": ("avgThreePointFieldGoalsMade", "3PM", "threePointFieldGoalsMade"),
    "three_attempts": ("avgThreePointFieldGoalsAttempted", "3PA", "threePointFieldGoalsAttempted"),
    "free_throws": ("avgFreeThrowsAttempted", "FTA", "freeThrowsAttempted"),
    "minutes": ("avgMinutes", "MPG", "minutes"),
}
SHRINK_GAMES = 20
#: A percentage: made, attempted, the percentage itself (0-100) for a line
#: without the counts, and how many attempts' worth of the group's rate it is
#: pulled toward.
PCTS: dict[str, tuple[str, str, str, int]] = {
    "fg_pct": ("fieldGoalsMade", "fieldGoalsAttempted", "FG%", 100),
    "three_pct": ("threePointFieldGoalsMade", "threePointFieldGoalsAttempted", "3P%", 50),
    "ft_pct": ("freeThrowsMade", "freeThrowsAttempted", "FT%", 20),
}

#: A player with no line, or a rating whose stats his line lacks: his group's median.
POSITION_DEFAULTS = {
    pos: [RETAIL[group][name][5] for name in RATING_NAMES] for pos, group in GROUP.items()
}


# ESPN stat names of the ROM's 17 season fields, in `STAT_GAMES`..`STAT_FOULS` order.
SEASON_TOTALS = (
    "gamesPlayed",
    "minutes",
    "fieldGoalsMade",
    "fieldGoalsAttempted",
    "threePointFieldGoalsMade",
    "threePointFieldGoalsAttempted",
    "freeThrowsMade",
    "freeThrowsAttempted",
    "offensiveRebounds",
    "rebounds",
    "assists",
    "steals",
    "turnovers",
    "blocks",
    "points",
    "disqualifications",
    "fouls",
)


def on_retail(share: float, deciles: Sequence[int]) -> int:
    """The value at `share` (0-1, a player's rank in his group) of the
    cartridge's spread, read between its deciles."""
    at = share * (len(deciles) - 1)
    i = min(int(at), len(deciles) - 2)
    return round(deciles[i] + (deciles[i + 1] - deciles[i]) * (at - i))


def _rank(values: Sequence[float], value: float) -> float:
    """Where `value` ranks among sorted `values`, 0-1, ties at their middle."""
    below = bisect.bisect_left(values, value)
    equal = bisect.bisect_right(values, value) - below
    return (below + equal / 2) / len(values)


def _games(line: Mapping[str, object]) -> float | None:
    return stat(line, "gamesPlayed") or stat(line, "GP")


def _per_game(line: Mapping[str, object]) -> dict[str, float]:
    """Each counting stat the line has, per game."""
    games = _games(line)
    out = {}
    for name, (avg, short, total) in COUNTS.items():
        value = stat(line, avg)
        if value is None:
            value = stat(line, short)
        if value is None and games and (count := stat(line, total)) is not None:
            value = count / games
        if value is not None:
            out[name] = value
    return out


def _group(player: Player) -> str:
    """G, F or C: the cartridge's spreads are its guards', forwards', centres'."""
    position = NBALive95StatMapper()._normalize_position(player.position)
    return GROUP[POSITION_TO_BYTE[position]]


@dataclass
class LeagueStats:
    """Every fetched player's stats, by group, to rank one player against."""

    #: (group, stat) -> every value, sorted.
    values: dict[tuple[str, str], list[float]] = field(default_factory=dict)
    #: (group, counting stat or percentage) -> the group's rate.
    rates: dict[tuple[str, str], float] = field(default_factory=dict)
    #: (group, rating) -> every player's average rank over the rating's stats,
    #: sorted: an average of ranks bunches in the middle, so it is ranked again.
    blends: dict[tuple[str, str], list[float]] = field(default_factory=dict)

    @classmethod
    def from_lines(cls, lines: Iterable[tuple[Player, Mapping[str, object]]]) -> "LeagueStats":
        """The league of these (player, stat line) pairs; a pair without a line is left out."""
        lines = [(player, line) for player, line in lines if line]
        sums: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
        for player, line in lines:
            group = _group(player)
            for name, value in _per_game(line).items():
                sums[group, name][0] += value
                sums[group, name][1] += 1
            for name, (made, tried, _pct, _weight) in PCTS.items():
                if (m := stat(line, made)) is not None and (a := stat(line, tried)):
                    sums[group, name][0] += m
                    sums[group, name][1] += a
        league = cls(rates={key: total / n for key, (total, n) in sums.items() if n})
        own = [league.sources(player, line) for player, line in lines]
        values: dict[tuple[str, str], list[float]] = defaultdict(list)
        for (player, _line), mine in zip(lines, own, strict=True):
            for name, value in mine.items():
                values[_group(player), name].append(value)
        league.values = {key: sorted(v) for key, v in values.items()}
        blends: dict[tuple[str, str], list[float]] = defaultdict(list)
        for (player, _line), mine in zip(lines, own, strict=True):
            group = _group(player)
            for rating, names in SOURCES.items():
                if len(names) > 1 and (blend := league._blend(group, names, mine)) is not None:
                    blends[group, rating].append(blend)
        league.blends = {key: sorted(v) for key, v in blends.items()}
        return league

    def sources(self, player: Player, line: Mapping[str, object]) -> dict[str, float]:
        """The player's value of each stat his ratings rank, higher the better."""
        group, games = _group(player), _games(line)
        out = {
            name: shrunk(value, self.rates.get((group, name), value), games, SHRINK_GAMES)
            if games
            else value
            for name, value in _per_game(line).items()
        }
        for name, (made, tried, pct, weight) in PCTS.items():
            m, a = stat(line, made), stat(line, tried)
            if m is not None and a is not None:
                prior = self.rates.get((group, name), m / a if a else 0.0)
                out[name] = (m + prior * weight) / (a + weight)
            elif (value := stat(line, pct)) is not None:
                out[name] = value / 100
        if "assists" in out and "turnovers" in out:
            out["ball_security"] = out["assists"] / max(out["turnovers"], 0.1)
        if player.weight:
            out["weight"] = float(player.weight)
            out["lightness"] = -float(player.weight)
        return out

    def _blend(self, group: str, names: Sequence[str], own: Mapping[str, float]) -> float | None:
        """The average rank of the player's values `own` of the stats `names`
        among the group's; None when he has none the group has."""
        shares = [
            _rank(self.values[group, name], own[name])
            for name in names
            if name in own and self.values.get((group, name))
        ]
        return sum(shares) / len(shares) if shares else None

    def rating_share(self, group: str, rating: str, own: Mapping[str, float]) -> float | None:
        """Where a player whose stat values are `own` ranks for `rating` in his
        group (0-1); None when he has none of its stats."""
        blend = self._blend(group, SOURCES[rating], own)
        pool = self.blends.get((group, rating))
        return _rank(pool, blend) if blend is not None and pool else blend


class NBALive95StatMapper:
    def map_player(
        self,
        player: Player,
        stats: Mapping[str, object] | None = None,
        league: LeagueStats | None = None,
    ) -> NBALive95PlayerRecord:
        """`league` ranks his stats; without one his line is ranked alone.

        `season_stats` are the player's season totals from the line's ESPN
        stat names (`SEASON_TOTALS`, in the ROM's field order), zeros for a
        player with no line: the writer puts them over the cartridge's 1994
        season.
        """
        pos_str = self._normalize_position(player.position)
        pos_byte = POSITION_TO_BYTE.get(pos_str, POSITION_SF)
        group = GROUP[pos_byte]

        last, first = self._split_name(player.name)

        rated = {name: RETAIL[group][name][5] for name in SOURCES}
        if stats:
            league = league or LeagueStats.from_lines([(player, stats)])
            own = league.sources(player, stats)
            for name in SOURCES:
                if (share := league.rating_share(group, name, own)) is not None:
                    rated[name] = on_retail(share, RETAIL[group][name])

        return NBALive95PlayerRecord(
            name_last=last,
            name_first=first,
            jersey=player.number or 0,
            position=pos_byte,
            height_inches=self._estimate_height(pos_str, player),
            weight_lbs=self._estimate_weight(pos_str, player),
            experience=player.experience,
            ratings=[rated[name] for name in RATING_NAMES],
            stamina=rated["stamina"],
            shooting_range=rated["range"],
            season_stats=[min(0xFFFF, round(stat(stats or {}, n) or 0)) for n in SEASON_TOTALS],
            player_id=player.id,
            photo_url=player.photo_url,
        )

    def select_roster(
        self,
        players: list[Player],
        stats: dict | None = None,
        starters: dict | None = None,
    ) -> list[Player]:
        """Pick 12 by minutes played: the busiest at each of PG, SG, SF, PF and C,
        then the next busiest at each, then the 2 best remaining. The first five
        hold one player per position where the squad has one.

        A G fills either guard spot and an F either forward spot, which
        `split_positions` then writes as PG and SG, SF and PF.

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

        if starters:
            return self._from_starters(eligible, starters)

        selected = []
        used = set()

        for _ in range(2):
            for pos in LINEUP:
                for p in eligible:
                    if id(p) not in used and pos in self._spots(p.position):
                        selected.append(p)
                        used.add(id(p))
                        break

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

    def _from_starters(self, by_minutes: list[Player], starters: dict) -> list[Player]:
        """The five who started most of the club's recent games (`starters`,
        starts by player id as a string; minutes break a tie) in the slots
        `LINEUP` names, centre to point guard: centres, then forwards, then
        guards, each the tallest first; then the rest by minutes, to twelve."""
        first = sorted(by_minutes, key=lambda p: -int(starters.get(str(p.id), 0)))[:STARTERS]
        first = [p for p in first if starters.get(str(p.id))] or by_minutes[:STARTERS]
        first.sort(key=lambda p: (_LABEL_ORDER.get(self._label(p), 1), -p.height))
        rest = [p for p in by_minutes if p not in first]
        return (first + rest)[:12]

    def split_positions(self, players: list[Player]) -> list[Player]:
        """`players` with each starter given his slot's position (`LINEUP`:
        the game plays and introduces him there) and each G and F on the bench
        one of its two spots, the first G a PG, the next an SG and so on
        alternating, the first F an SF, the next a PF.
        """
        out = [replace(p, position=LINEUP[i]) for i, p in enumerate(players[:STARTERS])]
        seen: Counter[str] = Counter()
        for p in players[STARTERS:]:
            label = (p.position or "").upper().strip()
            if label in COMBO_SPOTS:
                p = replace(p, position=COMBO_SPOTS[label][seen[label] % 2])
                seen[label] += 1
            out.append(p)
        return out

    @staticmethod
    def _label(p: Player) -> str:
        """ESPN's C, F or G for the player, its first letter for a combo."""
        return (p.position or "F").upper().strip()[:1]

    def _spots(self, position: str) -> tuple[str, ...]:
        """The game positions a label can fill."""
        label = (position or "").upper().strip()
        return COMBO_SPOTS.get(label, (self._normalize_position(label),))

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
        """Height in inches: the provider's, else a guess from the position."""
        if player.height > 0:
            return int(player.height)
        defaults = {"PG": 74, "SG": 77, "SF": 79, "PF": 81, "C": 83}
        return defaults.get(pos, 78)

    def _estimate_weight(self, pos: str, player: Player) -> int:
        """Weight in lbs, falling back to a position default when ESPN has none."""
        if player.weight > 0:
            return int(player.weight)
        defaults = {"PG": 190, "SG": 205, "SF": 220, "PF": 240, "C": 255}
        return defaults.get(pos, 220)

    def get_team_slot(self, team_abbrev: str) -> int | None:
        return MODERN_NBA_TO_NBALIVE95.get(team_abbrev.upper())
