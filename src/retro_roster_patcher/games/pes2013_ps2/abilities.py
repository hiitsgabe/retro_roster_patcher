"""A PES 2013 player's abilities, bytes 48-91 of his 124-byte record, and how a real player's
season sets them.

The layout is PES 6's option file's (the PES Editor's `Stats.java`, PeterC10/PESEditor on
GitHub), and the disc keeps it: read so, the records of 208 players of nine clubs give the PES
2013 ratings pesmaster lists for them in 91.4% of 6448 values, and 96.6% within 2 (pesmaster
shows a later data pack); height and age in all 208. Every field the patch writes is in
`FIELDS`. The cards pesmaster lists that PES 6 has no flag for were matched to bits by the
players that carry them:
one-touch play 21 of 24 on byte 82 bit 0, outside curve 27 of 28 on bit 1, long throw 13 of 13
on byte 84 bit 7, super-sub 3 of 3 on byte 83 bit 7, turning 31 of 32 on byte 85 bit 5, flip
flap 3 of 3 on byte 85 bit 7, long-range drive 5 of 5 on byte 87 bit 1, roulette 37 of 37 on
byte 87 bit 2.

Not abilities, or not identified, and left as the record has them: bytes 48-49 (the call name
the commentary uses), byte 50 bits 0-2 (edited flags, 0 on the disc), byte 51 bit 0 (set on 119
English-league players; unidentified), the registered position (byte 53 bits 4-7) and the
playable flags (bit 7 of bytes 54-65), which the patcher writes from the role, byte 85 bits 0-4
and 6 (bits 0-2 a 0-7 value that rises with age, teamwork and form; unidentified), byte 87 bit 0
(set with a left foot on 1052 of 1083 left-footers and 8 right-footers; unidentified) and bit 4
(unidentified), byte 89 bit 7 and bytes 90-91 (shirt and body shape), and the look from byte 92
(`models`).

Each rating ranks one or more of the player's stats (`SOURCES`, `KEEPER_SOURCES`) against his
group (goalkeepers, defenders, midfielders, attackers) in the league fetched and takes the value
at that rank of the disc's own spread of it for the group (`spreads`: the 4940 club and national
players of the retail disc, counted by registered position), so the league's best passer gets
the pass accuracy of the disc's best. A flag goes to as large a top of the group as holds it on
the disc. Counting stats rank per 90 minutes, pulled toward the group's rate by
`SHRINK_MINUTES`; a rate (pass accuracy, shots on target) toward the group's by a few attempts.
A field none of whose stats the line has, and every field without a source (the kick motions,
the celebrations, the growth type and the cards nothing measures), takes the median of the
group on the disc (`defaults`), as does every field of a player without a stat line. Height and
weight are the player's own; the stronger foot is the one he takes two thirds of his shots with,
weak-foot use and accuracy rank his shots with the other, and the favoured side is his role's.
"""

from __future__ import annotations

import bisect
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache

from ...core.assets import package_bytes
from ...sports.lineup import slot_side
from ...sports.models import Player, PlayerStats

#: Bytes 54-79 bits 0-6: the 26 ratings, 1-99.
RATINGS = (
    "attack",
    "defence",
    "balance",
    "stamina",
    "top_speed",
    "acceleration",
    "response",
    "agility",
    "dribble_accuracy",
    "dribble_speed",
    "short_pass_accuracy",
    "short_pass_speed",
    "long_pass_accuracy",
    "long_pass_speed",
    "shot_accuracy",
    "shot_power",
    "shot_technique",
    "free_kick_accuracy",
    "swerve",
    "heading",
    "jump",
    "teamwork",
    "technique",
    "aggression",
    "mentality",
    "goalkeeping",
)
#: Bit 7 of bytes 66-79: PES 6's special abilities.
HIGH_SPECIALS = (
    "penalties",
    "centre",
    "dribbling",
    "tactical_dribble",
    "positioning",
    "reaction",
    "playmaking",
    "passing",
    "scoring",
    "one_on_one_scoring",
    "post_player",
    "lines",
    "middle_shooting",
    "side",
)
#: Byte 82 bits 0-7: the rest of them.
LOW_SPECIALS = (
    "one_touch_pass",
    "outside",
    "marking",
    "sliding",
    "covering",
    "d_line_control",
    "penalty_stopper",
    "one_on_one_stopper",
)

#: Field -> (byte, shift, mask), the value as stored: 1-8 scales stored less one, injury
#: tolerance 0 C, 1 B, 2 A, the height in cm less `HEIGHT_BASE`, the weight in kg, the foot 1
#: for left, the favoured side 0 the foot's own, 1 the other, 2 both.
FIELDS: dict[str, tuple[int, int, int]] = {
    **{name: (54 + i, 0, 0x7F) for i, name in enumerate(RATINGS)},
    **{name: (66 + i, 7, 1) for i, name in enumerate(HIGH_SPECIALS)},
    **{name: (82, i, 1) for i, name in enumerate(LOW_SPECIALS)},
    "foot": (52, 0, 1),
    "free_kick_motion": (52, 1, 0x0F),
    "penalty_motion": (52, 5, 0x07),
    "dribble_motion": (53, 0, 0x03),
    "drop_kick_motion": (53, 2, 0x03),
    "consistency": (80, 0, 0x07),
    "weak_foot_frequency": (80, 3, 0x07),
    "injury_tolerance": (80, 6, 0x03),
    "condition": (81, 0, 0x07),
    "weak_foot_accuracy": (81, 3, 0x07),
    "favoured_side": (81, 6, 0x03),
    "celebration_1": (83, 0, 0x7F),
    "super_sub": (83, 7, 1),
    "celebration_2": (84, 0, 0x7F),
    "long_throw": (84, 7, 1),
    "turning": (85, 5, 1),
    "flip_flap": (85, 7, 1),
    "growth_type": (86, 0, 0xFF),
    "long_range_drive": (87, 1, 1),
    "roulette": (87, 2, 1),
    "height": (88, 0, 0x3F),
    "weight": (89, 0, 0x7F),
}
HEIGHT_BASE = 148
SIDE_OWN, SIDE_OTHER, SIDE_BOTH = 0, 1, 2

#: The stats each field ranks (higher is better); several are averaged by rank, a stat listed
#: twice counting twice. Nothing ESPN sends measures pace, so speed ranks youth and lightness
#: with the fouls a player draws. A field missing here has no source.
SOURCES: dict[str, tuple[str, ...]] = {
    "attack": ("goals", "assists", "shots"),
    "defence": ("tackles_won", "interceptions", "clearances", "blocks"),
    "balance": ("weight", "fouls_drawn"),
    "stamina": ("minutes_per_game", "appearances"),
    "top_speed": ("youth", "lightness", "fouls_drawn"),
    "acceleration": ("youth", "lightness", "dribbles", "fouls_drawn"),
    "response": ("interceptions", "goals", "youth"),
    "agility": ("lightness", "dribbles", "fouls_drawn"),
    "dribble_accuracy": ("dribble_success", "fouls_drawn"),
    "dribble_speed": ("dribbles", "fouls_drawn", "youth"),
    "short_pass_accuracy": ("pass_accuracy",),
    "short_pass_speed": ("passes",),
    "long_pass_accuracy": ("long_ball_accuracy",),
    "long_pass_speed": ("long_balls",),
    "shot_accuracy": ("shot_accuracy",),
    "shot_power": ("shots",),
    "shot_technique": ("finishing", "goals"),
    "free_kick_accuracy": ("free_kicks", "free_kick_goals"),
    "swerve": ("crosses", "cross_accuracy", "free_kicks"),
    "heading": ("headed_goals", "headers", "height"),
    "jump": ("height", "headers"),
    "teamwork": ("assists", "passes", "pass_accuracy"),
    "technique": ("pass_accuracy", "dribble_success", "fouls_drawn"),
    "aggression": ("shots", "crosses", "fouls_drawn"),
    "mentality": ("experience", "starts"),
    "consistency": ("starts",),
    "condition": ("rating",),
    "injury_tolerance": ("appearances",),
    "weak_foot_accuracy": ("weak_foot",),
    "weak_foot_frequency": ("weak_foot",),
    "centre": ("passes", "interceptions"),
    "dribbling": ("dribbles", "fouls_drawn"),
    "tactical_dribble": ("dribble_success", "pass_accuracy"),
    "positioning": ("goals", "shots"),
    "reaction": ("goals", "shot_accuracy"),
    "playmaking": ("assists", "passes"),
    "passing": ("pass_accuracy", "assists"),
    "scoring": ("goals",),
    "one_on_one_scoring": ("finishing",),
    "post_player": ("headed_goals", "weight", "height"),
    "lines": ("goals", "shots"),
    "middle_shooting": ("shots", "free_kicks"),
    "side": ("crosses",),
    "one_touch_pass": ("passes", "pass_accuracy"),
    "marking": ("tackles_won", "interceptions"),
    "sliding": ("tackles",),
    "covering": ("interceptions", "clearances", "blocks"),
    "d_line_control": ("clearances", "minutes"),
}
KEEPER_SOURCES: dict[str, tuple[str, ...]] = {
    "goalkeeping": ("save_rate", "clean_sheets"),
    "response": ("save_rate",),
    "defence": ("save_rate", "clean_sheets"),
    "agility": ("save_rate", "youth"),
    "jump": ("height",),
    "balance": ("weight",),
    "stamina": ("appearances",),
    "top_speed": ("youth",),
    "acceleration": ("youth",),
    "short_pass_accuracy": ("pass_accuracy",),
    "short_pass_speed": ("passes",),
    "long_pass_accuracy": ("long_ball_accuracy",),
    "long_pass_speed": ("long_balls",),
    "mentality": ("experience", "clean_sheets"),
    "consistency": ("starts",),
    "condition": ("rating",),
    "injury_tolerance": ("appearances",),
    "one_on_one_stopper": ("save_rate",),
}

#: Counting stats, ranked per 90 minutes: name -> `PlayerStats` field.
COUNTS = {
    "goals": "goals",
    "assists": "assists",
    "shots": "shots_total",
    "passes": "passes_total",
    "tackles": "tackles_total",
    "tackles_won": "tackles_won",
    "interceptions": "interceptions",
    "clearances": "clearances",
    "blocks": "blocks",
    "crosses": "crosses_total",
    "long_balls": "long_balls_total",
    "free_kicks": "free_kick_shots",
    "free_kick_goals": "free_kick_goals",
    "headers": "shots_headed",
    "headed_goals": "headed_goals",
    "fouls_drawn": "fouls_drawn",
    "dribbles": "dribbles_success",
    "saves": "saves",
    "clean_sheets": "clean_sheets",
}
#: Rates: name -> (made, attempted, attempts of the group's rate it is pulled toward by).
#: `passes_accurate`, `shots_on_goal` (saves and goals conceded: ESPN's shots faced is 0) and
#: `weak_foot_shots`, `foot_shots` are worked out (`_field`).
RATES = {
    "pass_accuracy": ("passes_accurate", "passes_total", 100),
    "shot_accuracy": ("shots_on", "shots_total", 10),
    "finishing": ("goals", "shots_total", 10),
    "cross_accuracy": ("crosses_accurate", "crosses_total", 20),
    "long_ball_accuracy": ("long_balls_accurate", "long_balls_total", 30),
    "dribble_success": ("dribbles_success", "dribbles_attempts", 10),
    "save_rate": ("saves", "shots_on_goal", 30),
    "weak_foot": ("weak_foot_shots", "foot_shots", 10),
}
SHRINK_MINUTES = 900
#: Fewest shots with either foot, and the share of them one foot must take, that say which
#: foot is the stronger.
FOOT_SHOTS = 3
FOOT_SHARE = 2 / 3
KEEPER = "Goalkeeper"
GROUPS = ("Goalkeeper", "Defender", "Midfielder", "Attacker")
SPREADS_FILE = "pes2013_abilities.json"


def read(record: bytes) -> dict[str, int]:
    """Every `FIELDS` value of a 124-byte record."""
    return {name: record[at] >> shift & mask for name, (at, shift, mask) in FIELDS.items()}


def write(record: bytearray | memoryview, values: Mapping[str, int]) -> None:
    """Put `values` (`FIELDS` names) into a 124-byte record, each clamped to its field; every
    other bit stays."""
    for name, value in values.items():
        at, shift, mask = FIELDS[name]
        record[at] = record[at] & ~(mask << shift) & 0xFF | max(0, min(mask, value)) << shift


@cache
def spreads() -> dict[str, dict[str, list[tuple[int, int]]]]:
    """Group -> field -> the retail disc's (value, players) pairs, lowest value first."""
    raw = json.loads(package_bytes("retro_roster_patcher.assets", SPREADS_FILE))
    return {g: {f: [(v, c) for v, c in pairs] for f, pairs in fs.items()} for g, fs in raw.items()}


def on_disc(share: float, counts: Sequence[tuple[int, int]]) -> int:
    """The value at `share` (0-1, a rank in the group) of the disc's spread `counts`."""
    target = share * sum(c for _, c in counts)
    running = 0
    for value, count in counts:
        running += count
        if running > target:
            return value
    return counts[-1][0]


@cache
def defaults(group: str) -> dict[str, int]:
    """Every field's median on the disc in `group` (a midfielder's for another group)."""
    spread = spreads().get(group) or spreads()["Midfielder"]
    return {name: on_disc(0.5, spread[name]) for name in FIELDS}


def _rank(values: Sequence[float], value: float) -> float:
    """Where `value` ranks among sorted `values`, 0-1, ties at their middle."""
    below = bisect.bisect_left(values, value)
    equal = bisect.bisect_right(values, value) - below
    return (below + equal / 2) / len(values)


def _field(line: PlayerStats, name: str) -> float | None:
    """A `PlayerStats` count, worked out for the three that are not fields; None when the
    provider does not supply it."""
    if name == "passes_accurate":
        if "passes_accuracy" in line.unsupplied or "passes_total" in line.unsupplied:
            return None
        return line.passes_accuracy / 100 * line.passes_total
    if name == "shots_on_goal":
        if "saves" in line.unsupplied or "goals_conceded" in line.unsupplied:
            return None
        return float(line.saves + line.goals_conceded)
    if name in ("weak_foot_shots", "foot_shots"):
        left, right = line.left_foot_shots, line.right_foot_shots
        if {"left_foot_shots", "right_foot_shots"} & set(line.unsupplied):
            return None
        return float(min(left, right) if name == "weak_foot_shots" else left + right)
    return None if name in line.unsupplied else float(getattr(line, name))


def _sources(group: str) -> dict[str, tuple[str, ...]]:
    return KEEPER_SOURCES if group == KEEPER else SOURCES


@dataclass
class LeagueStats:
    """Every fetched player's stats, by group, to rank one player against."""

    #: (group, count or rate) -> the group's rate: per 90 minutes, or made per attempt.
    rates: dict[tuple[str, str], float] = field(default_factory=dict)
    #: (group, stat) -> every value, sorted.
    values: dict[tuple[str, str], list[float]] = field(default_factory=dict)
    #: (group, field) -> every player's average rank over the field's stats, sorted, for the
    #: fields that average several (an average of ranks bunches in the middle).
    blends: dict[tuple[str, str], list[float]] = field(default_factory=dict)

    @classmethod
    def of(cls, lines: Iterable[tuple[Player, str, PlayerStats | None]]) -> LeagueStats:
        """The league of these (player, group, stat line) triples; a player without a line,
        or without a minute in it, counts for his height, weight and age alone."""
        lines = list(lines)
        sums: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
        for _player, group, line in lines:
            if not _played(line):
                continue
            for name, attr in COUNTS.items():
                if (total := _field(line, attr)) is not None:
                    sums[group, name][0] += total
                    sums[group, name][1] += line.minutes / 90
            for name, (made, tried, _k) in RATES.items():
                m, t = _field(line, made), _field(line, tried)
                if m is not None and t:
                    sums[group, name][0] += m
                    sums[group, name][1] += t
        league = cls(rates={key: s / n for key, (s, n) in sums.items() if n})
        owns = [league.stats(player, group, line) for player, group, line in lines]
        values: dict[tuple[str, str], list[float]] = defaultdict(list)
        for (_player, group, _line), own in zip(lines, owns, strict=True):
            for name, value in own.items():
                values[group, name].append(value)
        league.values = {key: sorted(v) for key, v in values.items()}
        blends: dict[tuple[str, str], list[float]] = defaultdict(list)
        for (_player, group, _line), own in zip(lines, owns, strict=True):
            for name, stats in _sources(group).items():
                if len(stats) > 1 and (blend := league._blend(group, stats, own)) is not None:
                    blends[group, name].append(blend)
        league.blends = {key: sorted(v) for key, v in blends.items()}
        return league

    def stats(self, player: Player, group: str, line: PlayerStats | None) -> dict[str, float]:
        """The player's value of each stat a field ranks, higher the better."""
        out: dict[str, float] = {}
        if player.age:
            out["youth"], out["experience"] = -float(player.age), float(player.age)
        if player.height:
            out["height"] = player.height
        if player.weight:
            out["weight"], out["lightness"] = player.weight, -player.weight
        if not _played(line):
            return out
        nineties = line.minutes / 90
        k = SHRINK_MINUTES / 90
        for name, attr in COUNTS.items():
            if (total := _field(line, attr)) is not None:
                prior = self.rates.get((group, name), total / nineties)
                out[name] = (total + prior * k) / (nineties + k)
        for name, (made, tried, k) in RATES.items():
            m, t = _field(line, made), _field(line, tried)
            if m is not None and t:
                prior = self.rates.get((group, name), m / t)
                out[name] = (m + prior * k) / (t + k)
        out["minutes"] = float(line.minutes)
        out["appearances"] = float(line.appearances)
        out["minutes_per_game"] = line.minutes / line.appearances
        if "lineups" not in line.unsupplied:
            out["starts"] = line.lineups / line.appearances
        if line.rating:
            out["rating"] = line.rating
        return out

    def _blend(self, group: str, names: Sequence[str], own: Mapping[str, float]) -> float | None:
        shares = [
            _rank(self.values[group, name], own[name])
            for name in names
            if name in own and self.values.get((group, name))
        ]
        return sum(shares) / len(shares) if shares else None

    def share(self, group: str, name: str, own: Mapping[str, float]) -> float | None:
        """Where a player whose stats are `own` ranks for field `name` in his group (0-1);
        None when he has none of its stats."""
        blend = self._blend(group, _sources(group).get(name, ()), own)
        pool = self.blends.get((group, name))
        return _rank(pool, blend) if blend is not None and pool else blend

    def abilities(
        self, player: Player, group: str, line: PlayerStats | None, role: str = ""
    ) -> dict[str, int]:
        """The fields the player's own data sets, as stored; a field it does not is left out
        (`defaults` has it)."""
        group = group if group in GROUPS else "Midfielder"
        spread = spreads()[group]
        own = self.stats(player, group, line)
        out: dict[str, int] = {}
        for name in _sources(group):
            if (share := self.share(group, name, own)) is not None:
                out[name] = on_disc(share, spread[name])
        if player.height:
            out["height"] = round(player.height * 2.54) - HEIGHT_BASE
        if player.weight:
            out["weight"] = round(player.weight * 0.45359237)
        if line is not None and "left_foot_shots" not in line.unsupplied:
            left, right = line.left_foot_shots, line.right_foot_shots
            if left + right >= FOOT_SHOTS and max(left, right) >= FOOT_SHARE * (left + right):
                out["foot"] = int(left > right)
        side = slot_side(role) if role else ""
        if side == "C":
            out["favoured_side"] = SIDE_BOTH
        elif side:
            foot = out.get("foot", defaults(group)["foot"])
            out["favoured_side"] = SIDE_OWN if (side == "L") == bool(foot) else SIDE_OTHER
        return out


def _played(line: PlayerStats | None) -> bool:
    return line is not None and line.minutes > 0 and line.appearances > 0
