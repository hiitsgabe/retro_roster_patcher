"""Turn ESPN's MLB season lines into where each player ranks, rating by rating.

Each rating ranks one or more of the player's stats (`BATTER_SOURCES`,
`SPLIT_SOURCES`, `PITCHER_SOURCES`) against every player of his group in the
league fetched (`LeagueStats`): a batter against the league's batters, his
fielding against his position's, a split against the batters who swing from
his side against that hand, a pitcher against the starters or the relievers.
`patch` turns the rank (0-1) into the value at that rank of the disc's own
spread of the rating (`spreads`). A rating none of whose stats his line has is
left out, and `patch` writes his position's median: a partial stat line no
longer rates 0.

Rates are counted per plate appearance, per batter faced or per chance and
pulled toward the group's rate by a few dozen of them (`Stat`); a split toward
the player's own season by `SPLIT_SHRINK` at-bats, then toward the group's by
`SEASON_SHRINK` plate appearances. A batter without splits takes his season on
both sides. The ratings the retail disc's own 2004 lines track (its 361
regulars, Spearman): contact the split's average (0.96), power its isolated
power (0.95), home-run share its home runs (0.98), speed and stealing steal
attempts (0.74, 0.81), baserunning steal success and attempts, bunting
sacrifice bunts with speed and few home runs, plate discipline walks (0.61),
durability games (0.87), fielding and throwing accuracy fielding % (0.4-0.5),
range putouts and assists per inning, arm assists per inning (a catcher's
caught-stealing %), pickoff pickoffs per inning (0.4-0.5; with the caught-
stealing % of runners off him), stamina innings per start or appearance (0.96
for relievers), fastball velocity strikeouts (0.47). ESPN measures none of the
batter's chase and miss rates: walks and strikeouts stand in for them.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ...core.hockey import stat
from ...sports.models import Player
from .models import (
    BATTERS_PER_TEAM,
    MODERN_MLB_TO_MVP,
    MVP_ABBREV_TO_INDEX,
    POS_STRING_TO_NUM,
    RELIEVER_ARSENAL,
    RELIEVERS_PER_TEAM,
    SECONDARY_DH,
    SELECTION_POSITIONS,
    STARTER_ARSENAL,
    STARTERS_PER_TEAM,
    MVPPlayerRecord,
)

# The position strings a provider may use, mapped to the nine this game has.
# `OF` and `IF` are the two that collapse: a player listed as a generic
# outfielder becomes a centre fielder and a generic infielder a shortstop.
POSITION_ALIASES: dict[str, str] = {
    "C": "C",
    "1B": "1B",
    "2B": "2B",
    "3B": "3B",
    "SS": "SS",
    "LF": "LF",
    "CF": "CF",
    "RF": "RF",
    "DH": "DH",
    "OF": "CF",
    "IF": "SS",
}

# What an unrecognised position becomes.
DEFAULT_POSITION = "CF"

# The provider position strings that mean "pitcher".
PITCHER_POSITIONS = frozenset({"P", "SP", "RP", "CL", "CP"})

# The one provider position string that means "starting pitcher". Everything
# else in `PITCHER_POSITIONS` is treated as relief, including the bare `P` that
# ESPN uses for most of a staff -- which is why `select_roster` has to top the
# rotation up out of the relief pool.
STARTER_POSITION = "SP"

# Name suffixes dropped from a surname, upper-cased and with any trailing dot
# removed before the comparison.
NAME_SUFFIXES = frozenset({"JR", "SR", "II", "III", "IV"})

# What a player with no name at all is called.
UNNAMED = "Player"

# Batting and throwing hand codes, as the `attrib` table stores them.
BATS_RIGHT = 0
BATS_LEFT = 1
BATS_SWITCH = 2
THROWS_RIGHT = 0
THROWS_LEFT = 1

#: The two hands a batter faces, as the split keys (`vsL.AVG`) and the rating
#: names (`contact_lhp`) spell them.
SIDES = {"lhp": "vsL", "rhp": "vsR"}

#: The ratings of the `attrib` table a batter's line ranks, and their stats; a
#: rating with several averages their ranks, a stat listed twice counting twice.
BATTER_SOURCES: dict[str, tuple[str, ...]] = {
    "plate_discipline": ("walks",),
    "bunting": ("sac_bunts", "steal_attempts", "few_home_runs"),
    "stealing": ("steal_attempts",),
    "baserunning": ("steal_success", "steal_attempts", "runs_scored"),
    "speed": ("steal_attempts", "steal_attempts", "triples"),
    "durability": ("games",),
    "starpower": ("war",),
    "fielding": ("fielding_pct", "dwar"),
    "range": ("range_factor", "dwar"),
    "throw_strength": ("arm",),
    "throw_accuracy": ("fielding_pct",),
}
#: Ranked against his position, not the league's batters.
FIELDING_RATINGS = frozenset({"fielding", "range", "throw_strength", "throw_accuracy"})
FIELDING_STATS = frozenset({"fielding_pct", "range_factor", "arm"})

#: The split-table ratings, each once per side (`contact_lhp`, `contact_rhp`),
#: ranked against the batters who swing from his side against that hand.
#: `zones` is how many of the nine strike-zone cells are hot, `ld` and `gb`
#: the line-drive and ground-ball shares of his balls in play.
SPLIT_SOURCES: dict[str, tuple[str, ...]] = {
    "contact": ("avg",),
    "power": ("iso",),
    "zones": ("avg", "iso"),
    "hr": ("hr_in_play",),
    "ld": ("babip",),
    "gb": ("ground",),
    "chase": ("free_swing",),
    "take": ("walk_rate",),
    "miss": ("strikeout_rate",),
}

#: A pitcher's ratings and their stats. `control` and `movement` rate every
#: pitch, each on its own pitch type's spread.
PITCHER_SOURCES: dict[str, tuple[str, ...]] = {
    "stamina": ("length",),
    "pickoff": ("pickoffs", "runners_caught"),
    "velocity": ("strikeouts",),
    "control": ("few_walks", "low_whip"),
    "movement": ("ground", "low_era"),
    "starpower": ("war",),
}

#: Every rating name a record's `ratings` may hold.
RATINGS = (
    *BATTER_SOURCES,
    *(f"{r}_{side}" for r in SPLIT_SOURCES for side in SIDES),
    *(f"pitch_{r}" for r in PITCHER_SOURCES),
)

#: A split is pulled toward the batter's own season by this many at-bats, and
#: the season toward his group's by this many plate appearances.
SPLIT_SHRINK = 60
SEASON_SHRINK = 150

#: The fielding positions a batter's `pos.XX` at-bats can name.
FIELD_POSITIONS = ("C", "1B", "2B", "3B", "SS", "LF", "CF", "RF")


@dataclass(frozen=True)
class Stat:
    """One stat of one player: `count / out_of`, pulled toward his group's rate
    by `weight` of `out_of`; a plain value when `out_of` is None. `sign` -1
    makes a lower one rank higher."""

    count: float
    out_of: float | None = None
    weight: float = 0.0
    sign: int = 1


def _innings(ip: float) -> float:
    """ESPN writes 162 and 2/3 innings as 162.2."""
    whole = int(ip)
    return whole + round((ip - whole) * 10) / 3


def _get(line: Mapping[str, object], *keys: str) -> float | None:
    """The sum of `keys` in the line, or None when any is missing."""
    values = [stat(line, key) for key in keys]
    return None if None in values else sum(values)  # type: ignore[arg-type]


def _rate(count: float | None, out_of: float | None, weight: float, sign: int = 1) -> Stat | None:
    return Stat(count, out_of, weight, sign) if count is not None and out_of else None


def _split_rate(
    line: Mapping[str, object], prefix: str, count: tuple[str, ...], out_of: tuple[str, ...]
) -> float | None:
    """`count / out_of` in the split `prefix`, pulled toward the season's rate
    by `SPLIT_SHRINK`; the season's when there is no split."""
    season = _get(line, *count), _get(line, *out_of)
    if season[0] is None or not season[1]:
        return None
    own = season[0] / season[1]
    split = (
        _get(line, *(f"{prefix}.{k}" for k in count)),
        _get(line, *(f"{prefix}.{k}" for k in out_of)),
    )
    if split[0] is None or split[1] is None:
        return own
    return (split[0] + own * SPLIT_SHRINK) / (split[1] + SPLIT_SHRINK)


def batter_stats(line: Mapping[str, object]) -> dict[str, Stat]:
    """A batter's stats, by the names `BATTER_SOURCES` and `SPLIT_SOURCES` use
    (a split one suffixed `_lhp` or `_rhp`). A stat the line lacks is absent."""
    pa = stat(line, "PA") or _get(line, "AB", "BB", "HBP")
    singles_and_walks = _get(line, "H", "BB", "HBP")
    if singles_and_walks is not None and (extra := _get(line, "2B", "3B", "HR")) is not None:
        singles_and_walks -= extra
    attempts = _get(line, "SB", "CS")
    on_base = _get(line, "H", "BB", "HBP")
    runs = _get(line, "R")
    if runs is not None and on_base is not None and (hr := stat(line, "HR")) is not None:
        runs, on_base = runs - hr, on_base - hr
    out: dict[str, Stat | None] = {
        "walks": _rate(stat(line, "BB"), pa, 100),
        "sac_bunts": _rate(stat(line, "SH"), pa, 100),
        "few_home_runs": _rate(stat(line, "HR"), pa, 100, -1),
        "triples": _rate(stat(line, "3B"), pa, 100),
        "steal_attempts": _rate(attempts, singles_and_walks, 50),
        "steal_success": _rate(stat(line, "SB"), attempts, 5),
        "runs_scored": _rate(runs, on_base, 50),
        "games": Stat(games) if (games := stat(line, "GP")) is not None else None,
        "war": Stat(war) if (war := stat(line, "WAR")) is not None else None,
    }
    putouts_assists = _get(line, "fielding.putouts", "fielding.assists")
    chances = _get(line, "fielding.putouts", "fielding.assists", "fielding.errors")
    outs = stat(line, "fielding.outsOnField")
    out["fielding_pct"] = _rate(putouts_assists, chances, 50)
    if putouts_assists is not None and outs:
        out["range_factor"] = Stat(putouts_assists * 27, outs, 27 * 30)
    if (dwar := stat(line, "fielding.defWARBR")) is not None and outs:
        out["dwar"] = Stat(dwar)
    caught = stat(line, "fielding.catcherCaughtStealing")
    allowed = stat(line, "fielding.catcherStolenBasesAllowed")
    if caught is not None and allowed is not None and caught + allowed:
        out["arm"] = Stat(caught, caught + allowed, 20)
    elif (assists := stat(line, "fielding.assists")) is not None and outs:
        out["arm"] = Stat(assists * 27, outs, 27 * 30)

    season_hr = [stat(line, k) for k in ("HR", "AB", "SO")]
    for side, prefix in SIDES.items():
        hits = _split_rate(line, prefix, ("H",), ("AB",))
        power = _split_rate(line, prefix, ("2B", "3B", "3B", "HR", "HR", "HR"), ("AB",))
        hr_rate = None
        if None not in season_hr and season_hr[1] > season_hr[2]:  # type: ignore[operator]
            hr_rate = season_hr[0] / (season_hr[1] - season_hr[2])  # type: ignore[operator]
            hr, ab, so = (stat(line, f"{prefix}.{k}") for k in ("HR", "AB", "SO"))
            if hr is not None and ab is not None and so is not None:
                hr_rate = (hr + hr_rate * SPLIT_SHRINK) / (ab - so + SPLIT_SHRINK)
        walks = _split_rate(line, prefix, ("BB",), ("AB", "BB", "HBP"))
        strikeouts = _split_rate(line, prefix, ("SO",), ("AB", "BB", "HBP"))
        # Counted over his season's plate appearances, so a short season is
        # pulled toward the group's rate like any other.
        for name, value, sign in (
            ("avg", hits, 1),
            ("iso", power, 1),
            ("hr_in_play", hr_rate, 1),
            ("walk_rate", walks, 1),
            ("free_swing", walks, -1),
            ("strikeout_rate", strikeouts, 1),
            ("babip", stat(line, "BIPA"), 1),
        ):
            if value is not None and pa:
                out[f"{name}_{side}"] = Stat(value * pa, pa, SEASON_SHRINK, sign)
        out[f"ground_{side}"] = _rate(stat(line, "GO"), _get(line, "GO", "FO"), 50)
    return {name: value for name, value in out.items() if value is not None}


def pitcher_stats(line: Mapping[str, object], starter: bool) -> dict[str, Stat]:
    """A pitcher's stats, by the names `PITCHER_SOURCES` uses."""
    faced = stat(line, "TBF")
    ip = _innings(stat(line, "IP") or 0.0)
    games = stat(line, "GS" if starter else "GP")
    balls = _get(line, "GB", "FB")
    out: dict[str, Stat | None] = {
        "strikeouts": _rate(stat(line, "K"), faced, 100),
        "few_walks": _rate(stat(line, "BB"), faced, 100, -1),
        "low_whip": _rate(_get(line, "BB", "H"), ip, 30, -1),
        "low_era": _rate(_get(line, "ER"), ip, 30, -1),
        "ground": _rate(stat(line, "GB"), balls, 50),
        "length": _rate(ip or None, games, 3),
        # ESPN's `pickoffs` is 0 for every pitcher; `POA`, which it calls
        # pickoff attempts, counts 0-7 a season, a pickoff count.
        "pickoffs": _rate(stat(line, "POA"), ip, 30),
        "runners_caught": _rate(stat(line, "CS"), _get(line, "SB", "CS"), 10),
        "war": Stat(war) if (war := stat(line, "WAR")) is not None else None,
    }
    return {name: value for name, value in out.items() if value is not None}


def _rank(values: list[float], value: float) -> float:
    """Where `value` ranks among sorted `values`, 0-1, ties at their middle."""
    below = bisect.bisect_left(values, value)
    equal = bisect.bisect_right(values, value) - below
    return (below + equal / 2) / len(values)


def batting_side(bats: int, side: str) -> str:
    """The side a batter swings from against `side`'s hand: a switch hitter
    bats right against a left-hander."""
    if bats == BATS_SWITCH:
        return "R" if side == "lhp" else "L"
    return "L" if bats == BATS_LEFT else "R"


@dataclass
class _Member:
    """One player of the league as `LeagueStats` ranks him."""

    groups: dict[str, str]  # rating kind -> his group of it
    stats: dict[str, Stat]


@dataclass
class LeagueStats:
    """Every fetched player's stats, by group, to rank one player against."""

    #: (group, stat) -> the group's rate, for a stat counted out of something.
    rates: dict[tuple[str, str], float] = field(default_factory=dict)
    #: (group, stat) -> every value, sorted.
    values: dict[tuple[str, str], list[float]] = field(default_factory=dict)
    #: (group, rating) -> every average rank over the rating's stats, sorted:
    #: an average of ranks bunches in the middle, so it is ranked again.
    blends: dict[tuple[str, str], list[float]] = field(default_factory=dict)

    @classmethod
    def from_lines(cls, lines: Iterable[tuple[Player, Mapping[str, object], bool]]) -> LeagueStats:
        """The league of these (player, line, is a starter) triples; a player
        without a line is left out."""
        members = [_member(p, line, starter) for p, line, starter in lines if line]
        league = cls()
        sums: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
        for m in members:
            for name, s in m.stats.items():
                if s.out_of is not None:
                    total = sums[m.groups[_kind(name)], name]
                    total[0] += s.count
                    total[1] += s.out_of
        league.rates = {key: c / n for key, (c, n) in sums.items() if n}
        values: dict[tuple[str, str], list[float]] = defaultdict(list)
        for m in members:
            for name in m.stats:
                values[m.groups[_kind(name)], name].append(league.value(m, name))
        league.values = {key: sorted(v) for key, v in values.items()}
        blends: dict[tuple[str, str], list[float]] = defaultdict(list)
        for m in members:
            for rating, names in _sources(m).items():
                if len(names) > 1 and (blend := league._blend(m, rating, names)) is not None:
                    blends[m.groups[_rating_kind(rating)], rating].append(blend)
        league.blends = {key: sorted(v) for key, v in blends.items()}
        return league

    def value(self, m: _Member, name: str) -> float:
        s = m.stats[name]
        if s.out_of is None:
            return s.sign * s.count
        prior = self.rates.get((m.groups[_kind(name)], name), s.count / s.out_of)
        return s.sign * (s.count + prior * s.weight) / (s.out_of + s.weight)

    def _blend(self, m: _Member, rating: str, names: tuple[str, ...]) -> float | None:
        shares = [
            _rank(pool, self.value(m, name))
            for name in names
            if name in m.stats and (pool := self.values.get((m.groups[_kind(name)], name)))
        ]
        return sum(shares) / len(shares) if shares else None

    def shares(self, player: Player, line: Mapping[str, object], starter: bool) -> dict[str, float]:
        """Where he ranks in his group, 0-1, for each rating his line has a stat of."""
        m = _member(player, line, starter)
        out = {}
        for rating, names in _sources(m).items():
            blend = self._blend(m, rating, names)
            pool = self.blends.get((m.groups[_rating_kind(rating)], rating))
            if blend is not None:
                out[rating] = _rank(pool, blend) if len(names) > 1 and pool else blend
        return out


def _kind(stat_name: str) -> str:
    """Which of a member's groups a stat is ranked in."""
    if stat_name in FIELDING_STATS:
        return "position"
    for side in SIDES:
        if stat_name.endswith(side):
            return side
    return "league"


def _rating_kind(rating: str) -> str:
    if rating in FIELDING_RATINGS:
        return "position"
    for side in SIDES:
        if rating.endswith(side):
            return side
    return "league"


def _sources(m: _Member) -> dict[str, tuple[str, ...]]:
    if m.groups["league"] in ("SP", "RP"):
        return {f"pitch_{r}": names for r, names in PITCHER_SOURCES.items()}
    out: dict[str, tuple[str, ...]] = dict(BATTER_SOURCES)
    for r, names in SPLIT_SOURCES.items():
        for side in SIDES:
            out[f"{r}_{side}"] = tuple(f"{n}_{side}" for n in names)
    return out


def _member(player: Player, line: Mapping[str, object], starter: bool) -> _Member:
    if MVPStatMapper.is_pitcher(player):
        role = "SP" if starter else "RP"
        return _Member({"league": role, "position": role}, pitcher_stats(line, starter))
    bats = MVPStatMapper.map_bat_hand(player.bats or player.handedness)
    groups = {"league": "B", "position": MVPStatMapper.normalize_position(player.position)}
    groups |= {side: batting_side(bats, side) for side in SIDES}
    return _Member(groups, batter_stats(line))


def secondary_position(line: Mapping[str, object], primary: str) -> int | None:
    """The `attrib` column 6 code of the position he has batted at most besides
    his own (`pos.XX` at-bats), DH when it is only that; None when neither."""
    played = {
        pos: ab
        for pos in FIELD_POSITIONS
        if pos != primary and (ab := stat(line, f"pos.{pos}") or 0) > 0
    }
    if played:
        return POS_STRING_TO_NUM[max(played, key=played.__getitem__)]
    if primary != "DH" and (stat(line, "pos.DH") or 0) > 0:
        return SECONDARY_DH
    return None


def _person(player: Player) -> dict[str, Any]:
    """The record fields about the man, not his ratings: height and weight
    (the provider's, else the record's defaults), birth date, and what his
    looks and portrait are read from."""
    out: dict[str, Any] = {
        "player_id": player.id,
        "photo_url": player.photo_url,
        "nationality": player.nationality,
        "birth_date": player.birth_date or "",
    }
    if player.height > 0:
        out["height"] = int(player.height)
    if player.weight > 0:
        out["weight"] = int(player.weight)
    return out


def _stat(stats: dict[str, Any], key: str, default: float) -> float:
    """One statistic as a float, with `default` for absent, None, empty or zero."""
    return float(stats.get(key, default) or default)


class MVPStatMapper:
    """Maps ESPN player data onto `MVPPlayerRecord`. Stateless."""

    def map_batter(
        self,
        player: Player,
        stats: Mapping[str, object] | None = None,
        league: LeagueStats | None = None,
    ) -> MVPPlayerRecord:
        """One position player; `league` ranks his line, without one it is ranked alone."""
        stats = stats or {}
        pos = self.normalize_position(player.position)
        return MVPPlayerRecord(
            first_name=self.first_name(player.name),
            last_name=self.last_name(player.name),
            jersey=player.number or 0,
            bats=self.map_bat_hand(player.bats or player.handedness),
            throws=self.map_throw_hand(player.handedness),
            primary_position=pos,
            secondary_position=secondary_position(stats, pos),
            ratings=self._shares(player, stats, False, league),
            is_pitcher=False,
            **_person(player),
        )

    def map_pitcher(
        self,
        player: Player,
        stats: Mapping[str, object] | None = None,
        *,
        is_starter: bool = True,
        league: LeagueStats | None = None,
    ) -> MVPPlayerRecord:
        """One pitcher; a starter is listed with relief as his second position
        and a reliever with starting, as on the retail disc."""
        stats = stats or {}
        return MVPPlayerRecord(
            first_name=self.first_name(player.name),
            last_name=self.last_name(player.name),
            jersey=player.number or 0,
            bats=self.map_bat_hand(player.bats or player.handedness),
            throws=self.map_throw_hand(player.handedness),
            primary_position=STARTER_POSITION if is_starter else "RP",
            secondary_position=POS_STRING_TO_NUM["RP" if is_starter else "SP"],
            ratings=self._shares(player, stats, is_starter, league),
            is_pitcher=True,
            pitch_types=STARTER_ARSENAL if is_starter else RELIEVER_ARSENAL,
            **_person(player),
        )

    @staticmethod
    def _shares(
        player: Player, stats: Mapping[str, object], starter: bool, league: LeagueStats | None
    ) -> dict[str, float]:
        if not stats:
            return {}
        league = league or LeagueStats.from_lines([(player, stats, starter)])
        return league.shares(player, stats, starter)

    def select_roster(
        self,
        players: list[Player],
        stats: dict[str, Any] | None = None,
    ) -> list[Player]:
        """Order a squad into the 25 slots the game has, best first within each group.

            [0-14]  batters -- one per lineup position, then the best of the rest
            [15-19] the rotation
            [20-24] the bullpen, closer first

        Shorter than 25 when the squad is: a team the provider gave nine players
        for produces nine, and `patch` writes nine.

        Each sort key is a single composite number: OPS x 1000 + hits for a
        batter, wins x 100 + innings for a starter, saves x 100 + (10 - ERA) for
        a reliever. The reliever key's last term breaks ties between pitchers
        with no saves and may go negative above an ERA of 10 without
        disordering anything, being bounded by the 100-point save step.
        """
        stats = stats or {}
        pitchers = [p for p in players if self.is_pitcher(p)]
        batters = [p for p in players if not self.is_pitcher(p)]

        def batter_sort(p: Player) -> float:
            ps = stats.get(str(p.id), {})
            return _stat(ps, "OPS", 0) * 1000 + _stat(ps, "H", 0)

        def starter_sort(p: Player) -> float:
            ps = stats.get(str(p.id), {})
            return _stat(ps, "W", 0) * 100 + _stat(ps, "IP", 0)

        def reliever_sort(p: Player) -> float:
            ps = stats.get(str(p.id), {})
            return _stat(ps, "SV", 0) * 100 + (10 - _stat(ps, "ERA", 9.0))

        starters = [p for p in pitchers if (p.position or "").upper() == STARTER_POSITION]
        relievers = [p for p in pitchers if (p.position or "").upper() != STARTER_POSITION]
        starters.sort(key=starter_sort, reverse=True)
        relievers.sort(key=reliever_sort, reverse=True)

        selected_starters = starters[:STARTERS_PER_TEAM]
        selected_relievers = relievers[:RELIEVERS_PER_TEAM]

        # Whichever group came up short takes from whatever is left over.
        #
        # Inherited and preserved: the bullpen is filled first and the rotation
        # gets the remainder, because both slices above happen before either
        # top-up loop. ESPN lists most of a staff as a bare `P`, which this
        # treats as relief, so a squad of five such pitchers puts all five in
        # the bullpen and leaves the rotation empty.
        #
        # The order of the two slices, and of the two loops, cannot matter: a
        # loop runs only when its own group is short, which is exactly when that
        # group's surplus slice is empty, so at most one slice is non-empty on
        # any run where either loop pops anything.
        remaining = starters[STARTERS_PER_TEAM:] + relievers[RELIEVERS_PER_TEAM:]
        while len(selected_starters) < STARTERS_PER_TEAM and remaining:
            selected_starters.append(remaining.pop(0))
        while len(selected_relievers) < RELIEVERS_PER_TEAM and remaining:
            selected_relievers.append(remaining.pop(0))

        batters.sort(key=batter_sort, reverse=True)
        return self._select_position_players(batters) + selected_starters + selected_relievers

    def _select_position_players(self, batters: list[Player]) -> list[Player]:
        """Fifteen batters: one per lineup position first, then the best left over.

        `batters` arrives sorted best-first, so the player taken for a position
        is the best available at it. A position nobody plays is skipped rather
        than filled, and the bench then runs longer.

        Identity, not equality, decides whether a player has been used: two
        distinct `Player` objects sharing an id are two roster entries here, and
        comparing by id would silently drop a provider's duplicate entry.

        Positions are filled in `SELECTION_POSITIONS` order, which is not the
        order the lineup is written in; `models` says why.
        """
        by_pos: dict[str, list[Player]] = {}
        for p in batters:
            by_pos.setdefault(self.normalize_position(p.position), []).append(p)

        selected: list[Player] = []
        used: set[int] = set()

        for pos in SELECTION_POSITIONS:
            for candidate in by_pos.get(pos, []):
                if id(candidate) not in used:
                    selected.append(candidate)
                    used.add(id(candidate))
                    break

        for p in batters:
            # `>=`, not `>`: the bench is fifteen deep, and stopping at sixteen
            # to have the slice below throw the sixteenth away is not the rule.
            if len(selected) >= BATTERS_PER_TEAM:
                break
            if id(p) not in used:
                selected.append(p)
                used.add(id(p))

        # Redundant with the `>=` above, and kept: deleting either leaves the
        # other carrying a bound it does not state.
        return selected[:BATTERS_PER_TEAM]

    @staticmethod
    def is_pitcher(player: Player) -> bool:
        return (player.position or "").upper() in PITCHER_POSITIONS

    @staticmethod
    def normalize_position(position: str) -> str:
        """A provider's position string as one of this game's nine."""
        return POSITION_ALIASES.get((position or "").upper(), DEFAULT_POSITION)

    @staticmethod
    def map_bat_hand(handedness: str | None) -> int:
        """0 right, 1 left, 2 switch. Anything unrecognised is right-handed."""
        if not handedness:
            return BATS_RIGHT
        h = handedness.upper()
        if h == "L":
            return BATS_LEFT
        if h in ("S", "B"):
            return BATS_SWITCH
        return BATS_RIGHT

    @staticmethod
    def map_throw_hand(handedness: str | None) -> int:
        """0 right, 1 left. There is no switch-throwing code."""
        if not handedness:
            return THROWS_RIGHT
        return THROWS_LEFT if handedness.upper() == "L" else THROWS_RIGHT

    @staticmethod
    def first_name(full_name: str) -> str:
        parts = full_name.strip().split()
        return parts[0] if parts else UNNAMED

    @staticmethod
    def last_name(full_name: str) -> str:
        """Everything after the first word, minus a generational suffix.

        A one-word name is its own surname. A name that is *only* a first name
        and a suffix -- "Ken Jr." -- keeps the last word, because dropping it
        would leave the surname empty.
        """
        parts = full_name.strip().split()
        # Redundant with the suffix filter below, and kept: "a one-word name is
        # its own surname" is the rule, and reaching it through an empty filter
        # result is not.
        if len(parts) <= 1:
            return parts[0] if parts else UNNAMED
        kept = [p for p in parts[1:] if p.rstrip(".").upper() not in NAME_SUFFIXES]
        return " ".join(kept) if kept else parts[-1]

    @staticmethod
    def get_team_slot(team_abbrev: str) -> int | None:
        """The ROM slot a provider's team abbreviation maps to, or None."""
        mvp_abbrev = MODERN_MLB_TO_MVP.get(team_abbrev.upper())
        # Keep the guard and keep the bare `.get`: together they say an unknown
        # club has no slot rather than slot zero, and slot zero is Anaheim.
        if mvp_abbrev is None:
            return None
        return MVP_ABBREV_TO_INDEX.get(mvp_abbrev)

    @staticmethod
    def get_mvp_abbrev(team_abbrev: str) -> str | None:
        return MODERN_MLB_TO_MVP.get(team_abbrev.upper())
