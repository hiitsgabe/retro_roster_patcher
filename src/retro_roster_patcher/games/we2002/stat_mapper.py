"""Maps real-world player stats onto WE2002's abilities, on the disc's own spread of each."""

from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from collections.abc import Callable
from itertools import zip_longest

from ...sports.espn import LINEUP_GROUPS
from ...sports.lineup import DEPTH as ROLE_DEPTH
from ...sports.lineup import (
    GROUP_ROLE,
    lineup_role,
    nearest_shape,
    parse_shape,
    seat_line,
    team_formation,
)
from .models import (
    Player,
    PlayerStats,
    TeamRoster,
    WEPlayerAttributes,
    WEPlayerRecord,
    WETeamRecord,
)
from .rom_writer import _PLAYERS_PER_NAT

#: The shapes the disc has presets for, in its order (`nearest_shape` ends a tie on the first).
FORMATIONS = ("4-5-1", "4-4-2", "4-3-3", "3-6-1", "3-5-2", "3-4-3", "5-4-1", "5-3-2")


def _preset(roles: str, x: str, y: str) -> bytes:
    """A preset's 30 bytes, from the hex of its role bytes, its x and its y."""
    return bytes.fromhex(f"{roles} {x} {y}")


#: The disc's 16 formation presets in its order, by name: a shape and a variant, A with one holding
#: midfielder (DH) and B with two. Each is the 30 bytes of a team's formation record: the role byte
#: of places 2-11 (place 1 is the keeper and has none), then their x, then their y. A role byte is
#: a family and a side (2-6 CB, 7-8 SB, 9-11 DH, 12-13 SH, 14-16 OH, 17-19 CF, 20-21 WG); x is
#: the depth, 0 at the team's own goal and 48 the most advanced; y is the width, 0-112, small on
#: the team's left and 52 in the middle. Every preset lists its back line, then its midfield, then
#: its attack. Constants, not read from the image: a test holds them to the disc's tables.
FORMATION_PRESETS: dict[str, bytes] = {
    "4-5-1A": _preset(
        "02 06 07 08 0a 0c 0d 0e 10 12",
        "09 09 0b 0b 12 1a 1a 22 22 2b",
        "29 3f 13 55 34 1d 4b 28 40 34",
    ),
    "4-5-1B": _preset(
        "02 06 07 08 09 0b 0c 0d 0f 12",
        "09 09 0b 0b 12 12 1a 1a 22 2b",
        "29 3f 13 55 2b 3d 1d 4b 34 34",
    ),
    "4-4-2A": _preset(
        "02 06 07 08 0a 0c 0d 0f 11 13",
        "09 09 0b 0b 12 1a 1a 22 2b 2b",
        "29 3f 13 55 34 1d 4b 34 26 42",
    ),
    "4-4-2B": _preset(
        "02 06 07 08 09 0b 0e 10 11 13",
        "09 09 0b 0b 12 12 1e 1e 2b 2b",
        "29 3f 13 55 2b 3d 22 46 26 42",
    ),
    "4-3-3A": _preset(
        "02 06 07 08 0a 0e 10 12 14 15",
        "09 09 0b 0b 12 1e 1e 2b 2b 2b",
        "29 3f 13 55 34 28 40 34 20 48",
    ),
    "4-3-3B": _preset(
        "02 06 07 08 09 0b 0f 12 14 15",
        "09 09 0b 0b 12 12 1e 2b 2b 2b",
        "29 3f 13 55 2b 3d 34 34 20 48",
    ),
    "3-6-1A": _preset(
        "02 03 06 0a 0c 0d 0f 0e 10 12",
        "09 09 09 12 1a 1a 1a 22 22 2b",
        "20 34 48 34 1b 4d 34 28 40 34",
    ),
    "3-6-1B": _preset(
        "02 03 06 09 0b 0c 0d 0e 10 12",
        "09 09 09 12 12 1a 1a 22 22 2b",
        "20 34 48 2b 3d 1b 4d 28 40 34",
    ),
    "3-5-2A": _preset(
        "02 03 06 0a 0c 0d 0e 10 11 13",
        "09 09 09 12 1a 1a 22 22 2b 2b",
        "20 34 48 34 1b 4d 28 40 26 42",
    ),
    "3-5-2B": _preset(
        "02 03 06 09 0b 0c 0d 0f 11 13",
        "09 09 09 12 12 1a 1a 22 2b 2b",
        "20 34 48 2b 3d 1b 4d 34 26 42",
    ),
    "3-4-3A": _preset(
        "02 03 06 0a 0c 0d 0f 12 14 15",
        "09 09 09 12 1a 1a 22 2b 2b 2b",
        "20 34 48 34 1b 4d 34 34 20 48",
    ),
    "3-4-3B": _preset(
        "02 03 06 09 0b 0e 10 12 14 15",
        "09 09 09 12 12 1e 1e 2b 2b 2b",
        "20 34 48 2b 3d 22 46 34 20 48",
    ),
    "5-4-1A": _preset(
        "02 03 06 07 08 0a 0c 0d 0f 12",
        "09 09 09 0c 0c 12 1a 1a 22 2b",
        "20 34 48 11 57 34 1d 4b 34 34",
    ),
    "5-4-1B": _preset(
        "02 03 06 07 08 09 0b 0e 10 12",
        "09 09 09 0c 0c 12 12 1e 1e 2b",
        "20 34 48 11 57 2b 3d 22 46 34",
    ),
    "5-3-2A": _preset(
        "02 03 06 07 08 0a 0e 10 11 13",
        "09 09 09 0c 0c 12 22 22 2b 2b",
        "20 34 48 11 57 34 28 40 26 42",
    ),
    "5-3-2B": _preset(
        "02 03 06 07 08 09 0b 0f 11 13",
        "09 09 09 0c 0c 12 12 22 2b 2b",
        "20 34 48 11 57 2b 3d 34 26 42",
    ),
}

#: A place's y is `_MIDDLE` in the middle of the pitch and low on the team's left. The presets'
#: widest places, the wing backs of the 5-back ones (y 17 and 87), are `_HALF_WIDTH` away, which
#: `_side` divides by.
_MIDDLE = 52
_HALF_WIDTH = 35


def _side(y: int) -> float:
    """The side of the pitch a place at width `y` is on, for `seat_line`: -1 at the team's left
    touchline, 0 in the middle and 1 at its right."""
    return (y - _MIDDLE) / _HALF_WIDTH


#: How many of the disc's 1,449 national players of each group store each value 0-7 (shown 12-19)
#: of each ability: its 179 keepers (position code 0), 447 defenders (1-2), 506 midfielders (3-5)
#: and 317 forwards (6-7). A player's ability is the value at his rank in his group of the league
#: fetched, so a patched squad keeps the disc's spread; with nothing to rank, the group's median.
DISC_SPREAD: dict[str, dict[str, tuple[int, ...]]] = {
    "Goalkeeper": {
        "offensive": (73, 97, 6, 2, 1, 0, 0, 0),
        "defensive": (0, 0, 0, 0, 59, 92, 15, 13),
        "body_balance": (0, 0, 0, 0, 13, 99, 60, 7),
        "stamina": (0, 114, 46, 6, 13, 0, 0, 0),
        "speed": (0, 0, 18, 137, 21, 3, 0, 0),
        "acceleration": (0, 17, 68, 48, 36, 5, 5, 0),
        "pass_accuracy": (2, 95, 59, 13, 9, 1, 0, 0),
        "shoot_power": (0, 3, 8, 38, 106, 22, 2, 0),
        "shoot_accuracy": (6, 135, 19, 14, 5, 0, 0, 0),
        "jump_power": (0, 0, 0, 17, 62, 93, 6, 1),
        "heading": (0, 167, 2, 3, 3, 4, 0, 0),
        "technique": (1, 117, 38, 14, 9, 0, 0, 0),
        "dribble": (0, 150, 18, 7, 3, 1, 0, 0),
        "curve": (168, 1, 3, 0, 4, 3, 0, 0),
        "aggression": (0, 13, 46, 20, 57, 38, 5, 0),
        "reflexes": (0, 0, 0, 0, 8, 34, 88, 49),
    },
    "Defender": {
        "offensive": (6, 58, 127, 135, 111, 9, 1, 0),
        "defensive": (1, 0, 12, 68, 197, 132, 32, 5),
        "body_balance": (0, 0, 0, 13, 157, 224, 48, 5),
        "stamina": (0, 0, 1, 31, 267, 124, 22, 2),
        "speed": (0, 0, 1, 131, 239, 70, 4, 2),
        "acceleration": (0, 3, 162, 117, 139, 16, 10, 0),
        "pass_accuracy": (0, 1, 56, 226, 132, 30, 1, 1),
        "shoot_power": (0, 2, 2, 88, 248, 96, 9, 2),
        "shoot_accuracy": (0, 1, 126, 240, 69, 10, 0, 1),
        "jump_power": (0, 0, 7, 110, 147, 155, 23, 5),
        "heading": (0, 107, 31, 137, 78, 83, 9, 2),
        "technique": (0, 0, 16, 195, 196, 39, 1, 0),
        "dribble": (0, 1, 29, 181, 163, 69, 4, 0),
        "curve": (281, 91, 43, 16, 11, 1, 1, 3),
        "aggression": (0, 14, 141, 79, 112, 95, 6, 0),
        "reflexes": (0, 0, 69, 190, 132, 41, 15, 0),
    },
    "Midfielder": {
        "offensive": (0, 0, 7, 113, 279, 88, 18, 1),
        "defensive": (3, 50, 149, 158, 126, 20, 0, 0),
        "body_balance": (0, 0, 2, 28, 257, 201, 18, 0),
        "stamina": (0, 0, 0, 19, 244, 168, 71, 4),
        "speed": (0, 0, 0, 85, 309, 106, 6, 0),
        "acceleration": (0, 0, 131, 143, 180, 33, 19, 0),
        "pass_accuracy": (0, 0, 3, 84, 190, 173, 45, 11),
        "shoot_power": (0, 1, 0, 42, 215, 210, 36, 2),
        "shoot_accuracy": (0, 0, 7, 158, 246, 82, 12, 1),
        "jump_power": (0, 0, 14, 201, 170, 116, 3, 2),
        "heading": (0, 168, 56, 170, 65, 42, 2, 3),
        "technique": (0, 0, 0, 47, 204, 199, 47, 9),
        "dribble": (0, 0, 0, 59, 187, 212, 43, 5),
        "curve": (80, 97, 141, 81, 61, 32, 8, 6),
        "aggression": (0, 0, 47, 46, 217, 178, 17, 1),
        "reflexes": (0, 0, 68, 208, 172, 46, 7, 5),
    },
    "Attacker": {
        "offensive": (0, 0, 0, 0, 49, 204, 51, 13),
        "defensive": (97, 180, 28, 9, 3, 0, 0, 0),
        "body_balance": (0, 0, 0, 12, 155, 123, 24, 3),
        "stamina": (0, 0, 0, 17, 196, 92, 12, 0),
        "speed": (0, 0, 0, 11, 165, 117, 19, 5),
        "acceleration": (0, 0, 23, 39, 152, 45, 52, 6),
        "pass_accuracy": (0, 0, 1, 78, 162, 61, 13, 2),
        "shoot_power": (0, 0, 0, 7, 114, 159, 33, 4),
        "shoot_accuracy": (0, 0, 0, 2, 83, 186, 36, 10),
        "jump_power": (0, 0, 0, 59, 115, 117, 21, 5),
        "heading": (0, 26, 12, 86, 75, 79, 27, 12),
        "technique": (0, 0, 0, 16, 129, 139, 24, 9),
        "dribble": (0, 0, 0, 23, 149, 101, 38, 6),
        "curve": (86, 82, 72, 31, 23, 14, 7, 2),
        "aggression": (0, 0, 0, 2, 63, 206, 42, 4),
        "reflexes": (0, 0, 4, 52, 149, 67, 36, 9),
    },
}

#: The stats each ability ranks: with several, the average of the player's ranks over those he
#: has. No provider measures pace, and ESPN neither duels nor dribbles: speed ranks youth with
#: attacking output, acceleration youth with shortness, body balance fouls drawn with height, and
#: dribble fouls drawn (with dribbles and duels when a provider has them). Outfield reflexes rank
#: interceptions and the share of games started.
SOURCES: dict[str, tuple[str, ...]] = {
    "offensive": ("attack",),
    "defensive": ("defending", "tackle_pct"),
    "body_balance": ("duels", "fouls_drawn", "height"),
    "stamina": ("minutes",),
    "speed": ("youth", "attack"),
    "acceleration": ("youth", "shortness"),
    "pass_accuracy": ("pass_pct", "long_ball_pct"),
    "shoot_power": ("shots",),
    "shoot_accuracy": ("finishing", "on_target"),
    "jump_power": ("height", "headers"),
    "heading": ("headed_goals", "headers"),
    "technique": ("pass_pct", "dribble_pct", "passes"),
    "dribble": ("dribbles", "fouls_drawn"),
    "curve": ("crosses", "cross_pct", "free_kick_goals"),
    "aggression": ("fouls",),
    "reflexes": ("interceptions", "starts"),
}
#: A keeper's defence ranks goals conceded and clean sheets, his reflexes and jump save %.
KEEPER_SOURCES = SOURCES | {
    "defensive": ("conceded", "clean_sheets"),
    "jump_power": ("height", "save_pct"),
    "reflexes": ("save_pct",),
}

#: How far a stat is pulled toward his group's rate, in units of its denominator: a count per game
#: by `GAMES` games, a share by that many attempts.
GAMES = 10
_Stat = tuple[Callable[[PlayerStats], tuple[float, float]], tuple[str, ...], float]


def _per_game(name: str) -> _Stat:
    return (lambda s: (getattr(s, name), s.appearances), (name, "appearances"), GAMES)


def _share(made: str, tried: str, k: float = 20) -> _Stat:
    return (lambda s: (getattr(s, made), getattr(s, tried)), (made, tried), k)


#: Each stat of a season: (numerator, denominator), the `PlayerStats` fields it reads (one the
#: provider did not supply leaves it out), and its pull toward the group's rate.
STATS: dict[str, _Stat] = {
    "attack": (
        lambda s: (s.goals + 0.7 * s.assists + 0.3 * s.shots_on, s.appearances),
        ("goals", "assists", "shots_on", "appearances"),
        GAMES,
    ),
    "defending": (
        lambda s: (s.tackles_total + s.interceptions + s.blocks, s.appearances),
        ("tackles_total", "interceptions", "blocks", "appearances"),
        GAMES,
    ),
    "tackle_pct": _share("tackles_won", "tackles_total", 10),
    "fouls_drawn": _per_game("fouls_drawn"),
    "duels": _share("duels_won", "duels_total"),
    "minutes": (lambda s: (s.minutes, s.appearances), ("minutes", "appearances"), 0),
    "pass_pct": (
        lambda s: (s.passes_accuracy * s.passes_total / 100, s.passes_total),
        ("passes_accuracy", "passes_total"),
        100,
    ),
    "long_ball_pct": _share("long_balls_accurate", "long_balls_total"),
    "passes": _per_game("passes_total"),
    "shots": _per_game("shots_total"),
    "finishing": _share("goals", "shots_total"),
    "on_target": _share("shots_on", "shots_total"),
    "headers": _per_game("shots_headed"),
    "headed_goals": _per_game("headed_goals"),
    "dribble_pct": _share("dribbles_success", "dribbles_attempts"),
    "dribbles": _per_game("dribbles_success"),
    "crosses": _per_game("crosses_total"),
    "cross_pct": _share("crosses_accurate", "crosses_total"),
    # Not free-kick shots: ESPN's freeKickShots is the free kicks he won, his fouls drawn.
    "free_kick_goals": _per_game("free_kick_goals"),
    "fouls": (
        lambda s: (s.fouls_committed + 2 * s.cards_yellow + 5 * s.cards_red, s.appearances),
        ("fouls_committed", "cards_yellow", "cards_red", "appearances"),
        GAMES,
    ),
    "interceptions": _per_game("interceptions"),
    "starts": _share("lineups", "appearances", GAMES),
    # ESPN's shotsFaced is 0 for every keeper, so the shots on target are saves plus goals.
    "save_pct": (
        lambda s: (s.saves, s.saves + s.goals_conceded),
        ("saves", "goals_conceded"),
        30,
    ),
    "conceded": (
        lambda s: (-s.goals_conceded, s.appearances),
        ("goals_conceded", "appearances"),
        GAMES,
    ),
    "clean_sheets": _per_game("clean_sheets"),
}

#: Preferred foot (0 right, 1 left, 2 both) needs this many shots with either foot; below it, or
#: without the two counts, a player is right-footed like 81% of the disc's. Both feet when the
#: weaker one took `BOTH_FEET` of them (2.6% of the disc's players are two-footed).
FOOT_SHOTS = 10
BOTH_FEET = 0.4


def _group(player: Player) -> str:
    return player.position if player.position in DISC_SPREAD else "Midfielder"


def _raw(stats: PlayerStats | None) -> dict[str, tuple[float, float, float]]:
    """(numerator, denominator, pull) of each stat the season has; none without a game."""
    if not stats or stats.appearances <= 0:
        return {}
    return {
        name: (*fn(stats), k)
        for name, (fn, fields, k) in STATS.items()
        if not set(fields) & set(stats.unsupplied)
    }


def _rank(values: list[float], value: float) -> float:
    """Where `value` ranks among sorted `values`, 0-1, ties at their middle."""
    below = bisect_left(values, value)
    return (below + (bisect_right(values, value) - below) / 2) / len(values)


def on_spread(share: float, counts: tuple[int, ...]) -> int:
    """The value at `share` (0-1) of the disc's spread `counts` (its players per value)."""
    target, running = share * sum(counts), 0
    for value, count in enumerate(counts):
        running += count
        if running > target:
            return value
    return max(value for value, count in enumerate(counts) if count)


def preferred_foot(stats: PlayerStats | None) -> int:
    """0 right, 1 left, 2 both, from the shots he took with each foot (`FOOT_SHOTS`)."""
    if not stats or {"left_foot_shots", "right_foot_shots"} & set(stats.unsupplied):
        return 0
    left, right = stats.left_foot_shots, stats.right_foot_shots
    if left + right < FOOT_SHOTS:
        return 0
    if min(left, right) >= BOTH_FEET * (left + right):
        return 2
    return 1 if left > right else 0


class League:
    """Every fetched player's stats, by group (`DISC_SPREAD`), to rank one player's against."""

    def __init__(self, rosters: list[TeamRoster]):
        pairs = [(p, r.player_stats.get(p.id)) for r in rosters for p in r.players]
        totals: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
        for player, stats in pairs:
            for name, (num, den, _k) in _raw(stats).items():
                totals[_group(player), name][0] += num
                totals[_group(player), name][1] += den
        self.rates = {key: num / den if den else 0.0 for key, (num, den) in totals.items()}
        own = [(player, self.sources(player, stats)) for player, stats in pairs]
        values: dict[tuple[str, str], list[float]] = defaultdict(list)
        for player, mine in own:
            for name, value in mine.items():
                values[_group(player), name].append(value)
        self.values = {key: sorted(v) for key, v in values.items()}
        blends: dict[tuple[str, str], list[float]] = defaultdict(list)
        for player, mine in own:
            for ability in SOURCES:
                if (blend := self._blend(player, ability, mine)) is not None:
                    blends[_group(player), ability].append(blend)
        self.blends = {key: sorted(v) for key, v in blends.items()}

    def sources(self, player: Player, stats: PlayerStats | None) -> dict[str, float]:
        """The player's value of each stat he has, higher the better: counts per game and
        shares pulled toward his group's rate, and youth, height and shortness from his bio."""
        out: dict[str, float] = {}
        if player.age > 0:
            out["youth"] = -player.age
        if player.height > 0:
            out["height"], out["shortness"] = player.height, -player.height
        group = _group(player)
        for name, (num, den, k) in _raw(stats).items():
            rate = self.rates.get((group, name), 0.0)
            out[name] = (num + rate * k) / (den + k) if den + k else rate
        return out

    def _blend(self, player: Player, ability: str, mine: dict[str, float]) -> float | None:
        group = _group(player)
        names = (KEEPER_SOURCES if group == "Goalkeeper" else SOURCES)[ability]
        shares = [
            _rank(self.values[group, name], mine[name])
            for name in names
            if name in mine and self.values.get((group, name))
        ]
        return sum(shares) / len(shares) if shares else None

    def rating(self, player: Player, ability: str, mine: dict[str, float]) -> int:
        """The disc's value (0-7) at the player's rank in his group for `ability`; the group's
        median when he has none of its stats."""
        blend = self._blend(player, ability, mine)
        pool = self.blends.get((_group(player), ability))
        share = 0.5 if blend is None else _rank(pool, blend) if pool else blend
        return on_spread(share, DISC_SPREAD[_group(player)][ability])


class StatMapper:
    POSITION_CODES = {
        "Goalkeeper": 0,
        "Defender": 1,
        "Midfielder": 2,
        "Attacker": 3,
    }

    def map_team_with_league_context(
        self,
        team_roster: TeamRoster,
        all_rosters: list[TeamRoster],
        order_as_given: bool = False,
    ) -> WETeamRecord:
        """Map team using league-wide percentile normalization.

        `order_as_given`: the squad is the caller's list as it stands (first 23,
        a national squad's places) instead of the stat-ranked best 23.

        A team with a readable formation (`team_formation`: its `extra["formation"]`, else
        its lineup's shape) is written the disc's preset nearest it (`FORMATIONS`, variant
        B when the eleven hold two or more CDMs, else A) and its eleven are ordered for it:
        the keeper, then the ten outfield players cut into the preset's lines by index and
        each line seated on the preset's places by role (`_seated`). The ten are the
        suggested eleven's (`_select_best_23`) or, with `order_as_given`, the caller's in
        the order given (`_given`), so an edited order or formation keeps its lines by
        index. Without a readable formation the order is the 4-4-2's and the record has no
        formation. A player with no role has the one his lineup code reads as.
        """
        league = League(all_rosters)

        formation = team_formation(team_roster) or ""
        lineup = team_roster.extra.get("lineup")
        lined = bool(formation)
        if order_as_given:
            squad = (
                self._given(team_roster.players)
                if lined
                else team_roster.players[:_PLAYERS_PER_NAT]
            )
        else:
            squad = self._select_best_23(
                team_roster.players,
                team_roster.player_stats,
                lineup,
                formation,
            )
        preset = None
        if lined:
            preset = nearest_shape(formation, FORMATIONS) + (
                "B" if sum(self._role(p, lineup) == "CDM" for p in squad[:11]) >= 2 else "A"
            )
            squad = self._seated(squad, preset, lineup)

        we_players = []
        for player in squad:
            stats = team_roster.player_stats.get(player.id)
            attrs = self.map_player(player, stats, league)
            rom_last, rom_first = self._format_player_name(player)
            we_players.append(
                WEPlayerRecord(
                    last_name=rom_last,
                    first_name=rom_first,
                    position=self.POSITION_CODES.get(player.position, 2),
                    shirt_number=player.number or 0,
                    attributes=attrs,
                    player_id=player.id or 0,
                    photo_url=player.photo_url or "",
                    nationality=player.nationality or "",
                    height_cm=round(player.height * 2.54) if player.height > 0 else 0,
                    age=player.age or 0,
                    role=player.role or lineup_role(player.id, lineup),
                    foot=preferred_foot(stats),
                )
            )

        from .rom_writer import _to_ascii

        return WETeamRecord(
            name=self._truncate_name(team_roster.team.name, 24),
            short_name=_to_ascii(
                team_roster.team.code[:3]
                if team_roster.team.code
                else team_roster.team.name[:3].upper()
            ),
            players=we_players,
            formation=FORMATION_PRESETS[preset] if preset else None,
        )

    def map_player(
        self, player: Player, stats: PlayerStats | None, league: League
    ) -> WEPlayerAttributes:
        """His sixteen abilities, each ranked against his group in `league` (`SOURCES`) onto the
        disc's spread of it (`DISC_SPREAD`), shown 1-8 (the stored 0-7 plus one)."""
        mine = league.sources(player, stats)
        return WEPlayerAttributes(
            **{ability: league.rating(player, ability, mine) + 1 for ability in SOURCES}
        )

    #: The ten outfield places of the first 11 slots, after the keeper's: a 4-4-2.
    XI_LINES = (("Defender", 4), ("Midfielder", 4), ("Attacker", 2))
    #: The bench by line: with the eleven's places, a 3 / 7 / 6 / 6 squad. Per
    #: place, not per player: an eleven of six midfielders still gets two
    #: midfield subs.
    BENCH_PLACES = (("Goalkeeper", 2), ("Defender", 3), ("Midfielder", 2), ("Attacker", 4))
    #: ESPN lineup codes from the back to the front. The ten outfielders take
    #: the 4-4-2 in this order, so a line with more players than the 4-4-2 has
    #: moves its most advanced one up (a back five's wing-back to midfield)
    #: and a short line takes the next line's deepest (a back three a holding
    #: or wide midfielder), rather than a forward playing at the back.
    DEPTH = (
        ("SW", "CD", "CD-L", "CD-R", "LB", "RB", "LWB", "RWB")
        + ("DM", "LM", "RM", "CM", "CM-L", "CM-R", "AM-L", "AM-R", "AM")
        + ("LW", "RW", "LF", "RF", "CF-L", "CF-R", "LCF", "RCF", "F", "CF")
    )
    #: Where a player with no lineup code stands in his line.
    LINE_CODES = {"Defender": "CD", "Midfielder": "CM", "Attacker": "CF"}

    def _select_best_23(
        self,
        players: list[Player],
        player_stats: dict[int, PlayerStats] | None = None,
        lineup: dict | None = None,
        formation: str | None = None,
    ) -> list[Player]:
        """The 23 (a national squad's places, which a Master League club of the
        same slot number fields too): the starting eleven, then the bench.

        The eleven is the keeper, then ten outfielders in the 4-4-2 of the
        first 11 slots. When `lineup` (what `EspnClient.get_lineup` returns)
        has the team's recent starters, every one of them plays (a second
        keeper sits), back to front by his lineup position (`DEPTH`). Places
        still open go to the season's most frequent starters, from a line the
        lineup left short first. The bench then takes up to 2 keepers,
        3 defenders, 2 midfielders and 4 forwards (`BENCH_PLACES`), recent
        starters and regulars first, one of each line in turn so the first few
        subs cover every line; the best of the rest fill what is left.

        With a `formation` that `parse_shape` reads, the eleven's outfielders
        go back to front by the depth of their roles (`ROLE_DEPTH`; a player
        with none reads as the one his lineup code gives him, else his
        group's) and then by lineup position, so cutting them by index over
        the formation's lines puts each in the line he plays. The players
        picked are the same. The order is also the one
        `map_team_with_league_context` writes.
        """
        stats = player_stats or {}
        recent = {int(s["id"]): (i, s) for i, s in enumerate((lineup or {}).get("starters") or [])}

        def code(p: Player) -> str:
            return str(recent[p.id][1].get("position", "")).upper() if p.id in recent else ""

        def group(p: Player) -> str:
            return LINEUP_GROUPS.get(code(p), p.position)

        def depth(p: Player) -> int:
            c = code(p)
            return self.DEPTH.index(c if c in self.DEPTH else self.LINE_CODES.get(group(p), "CM"))

        def key(p: Player) -> tuple[int, int, int, int]:
            """Recent starters first, then lineups, appearances and minutes."""
            s = stats.get(p.id)
            return (
                recent[p.id][0] if p.id in recent else len(recent),
                -(s.lineups if s else 0),
                -(s.appearances if s else 0),
                -(s.minutes if s else 0),
            )

        # Stable, so players the key ties keep the provider's order.
        pool = sorted(players, key=key)
        keeper = [p for p in pool if group(p) == "Goalkeeper"][:1]
        outfield = [p for p in pool if p.id in recent and group(p) != "Goalkeeper"][:10]
        short = Counter(dict(self.XI_LINES)) - Counter(group(p) for p in outfield)
        for p in pool:
            if len(outfield) < 10 and short[group(p)] > 0 and p not in outfield:
                outfield.append(p)
                short[group(p)] -= 1
        spare = [p for p in pool if group(p) != "Goalkeeper" and p not in outfield]
        outfield += spare[: 10 - len(outfield)]
        lined = parse_shape(str(formation or "")) is not None

        def line_key(p: Player) -> tuple[int, int]:
            return (ROLE_DEPTH[self._role(p, lineup)] if lined else 0, depth(p))

        eleven = keeper + sorted(outfield, key=line_key)

        rest = [p for p in pool if p not in eleven]
        subs = [[p for p in rest if group(p) == g][:n] for g, n in self.BENCH_PLACES]
        bench = [p for row in zip_longest(*subs) for p in row if p is not None]
        bench += [p for p in rest if p not in bench]
        return (eleven + bench)[:_PLAYERS_PER_NAT]

    def _role(self, player: Player, lineup: dict | None = None) -> str:
        """His role, else the one his lineup code reads as (`lineup_role`), else the one his squad
        group gives (`GROUP_ROLE`)."""
        if player.role in ROLE_DEPTH:
            return player.role
        return lineup_role(player.id, lineup) or GROUP_ROLE.get(player.position, "CM")

    def _given(self, players: list[Player]) -> list[Player]:
        """The caller's list as the squad, for a team with a readable formation: the first
        goalkeeper listed in goal, then the first ten outfield players in the order given (the
        formation cuts them into lines by index, whatever their positions), then everyone else
        in the order given, a second keeper among them, cut at 23. A list with no goalkeeper
        keeps its first player in goal."""
        at = range(len(players))
        goal = next((i for i in at if players[i].position == "Goalkeeper"), 0)
        outfield = [i for i in at if i != goal and players[i].position != "Goalkeeper"]
        eleven = ([goal] if players else []) + outfield[:10]
        order = eleven + [i for i in at if i not in eleven]
        return [players[i] for i in order][:_PLAYERS_PER_NAT]

    def _seated(self, squad: list[Player], preset: str, lineup: dict | None = None) -> list[Player]:
        """`squad` (the keeper, ten outfield players in line order, then the rest) with each line
        of the ten on the places of the disc's `preset`, which is place k + 2 for the k-th.

        The ten are cut into the preset's lines by index and each line takes that line's places by
        role (`seat_line`): first by side, a full back on the widest place on his side (a place's
        side is its y's distance from the middle, `_side`), and then a deeper role on a deeper
        place, the place's x. A player with no role reads as the one his lineup code gives him,
        else as his group's. A squad of fewer than eleven seats the places it has players for."""
        record = FORMATION_PRESETS[preset]
        xs, ys = record[10:20], record[20:]
        defenders, midfielders, _ = map(int, preset[:-1].split("-"))
        out = list(squad)
        edges = (0, defenders, defenders + midfielders, 10)
        for first, end in zip(edges, edges[1:], strict=False):
            at = list(range(first, min(end, len(squad) - 1)))
            places = [(_side(ys[k]), xs[k]) for k in at]
            seats = seat_line([self._role(squad[1 + k], lineup) for k in at], places)
            for k, seat in zip(at, seats, strict=True):
                out[1 + at[seat]] = squad[1 + k]
        return out

    def _format_player_name(self, player: Player) -> tuple:
        """Build ROM-friendly (last_name, first_name) from a Player.

        The game displays at most 8 characters. A mononym is used as-is
        ("HULK", "NEYMAR"); two or more words become "F. Surname" ("V. Hugo",
        "G. Pique").

        Strip each provider string AFTER `_to_ascii` and before testing it:
        `_to_ascii` drops unrenderable characters but keeps the spaces between
        them, so a multi-word non-Latin name arrives as `" "`. Without the
        strip it is truthy, the fallback is skipped, and `words[-1]` raises
        `IndexError`. Stripping before conversion does not work — Cyrillic is
        not whitespace until `_to_ascii` has run.
        """
        from .rom_writer import _to_ascii

        display = _to_ascii(player.name).strip() if player.name else ""
        first = _to_ascii(player.first_name).strip() if player.first_name else ""

        if not display:
            last = _to_ascii(player.last_name).strip() if player.last_name else ""
            return (last or "")[:8], first[:8]

        words = display.split()

        if len(words) == 1:
            # Mononym: "HULK", "NEYMAR", "ENDRICK"
            return display[:8], ""

        # Multi-word: "F. Surname" — e.g. "V. Hugo", "G. Pique"
        surname = words[-1]
        initial = words[0][0]
        jersey = f"{initial}. {surname}"
        return jersey[:8], (first or words[0])[:8]

    def _truncate_name(self, name: str, max_bytes: int) -> str:
        """Fold to ASCII, then hard-truncate to `max_bytes`."""
        if not name:
            return ""
        from .rom_writer import _to_ascii

        ascii_name = _to_ascii(name)
        if len(ascii_name) <= max_bytes:
            return ascii_name
        return ascii_name[:max_bytes]
