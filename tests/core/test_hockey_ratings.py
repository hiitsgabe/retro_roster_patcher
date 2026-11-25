"""`core.hockey_ratings`: a player's rank in his group onto a disc's spread."""

from __future__ import annotations

from dataclasses import dataclass

from retro_roster_patcher.core.hockey_ratings import League, on_spread, rate
from retro_roster_patcher.sports.models import Player

SOURCES = {
    "F": {"speed": ("points", "-age"), "fighting": ("fights",), "faceoffs": ("faceoff_pct",)},
    "G": {"zones": ("wins", "save")},
}
SPREAD = {"F": {"speed": tuple(range(21)), "fighting": (0,) * 18 + (1, 2, 3)}}


def skater(pid: int, position: str = "C", age: int = 27) -> Player:
    return Player(id=pid, name=f"P {pid}", position=position, age=age)


def test_the_ends_of_the_spread_go_to_the_ends_of_the_group():
    assert on_spread(0.0, (3, 5, 9)) == 3
    assert on_spread(1.0, (3, 5, 9)) == 9
    assert on_spread(0.5, (3, 5, 9)) == 5


def test_more_points_and_youth_rank_higher():
    lines = [(skater(1, age=20), {"GP": 82, "PTS": 90}), (skater(2, age=35), {"GP": 82, "PTS": 30})]
    league = League.from_lines(lines, SOURCES)
    assert league.shares(*lines[0])["speed"] > league.shares(*lines[1])["speed"]


def test_a_line_without_any_of_a_ratings_stats_leaves_it_out():
    league = League.from_lines([(skater(1), {"GP": 82, "PTS": 50})], SOURCES)
    assert "fighting" not in league.shares(Player(id=9, name="X", position="C"), {"GP": 10})


def test_age_alone_does_not_rate_a_rating_that_ranks_a_season_stat():
    # A light or young player with no points is not thereby a fast one.
    lines = [(skater(i), {"GP": 82, "PTS": 40 + i}) for i in range(20)]
    league = League.from_lines(lines, SOURCES)
    assert "speed" not in league.shares(skater(99, age=19), {"dressed": 3.0})


def test_a_line_of_no_games_counts_as_zeros_and_ranks_last():
    lines = [(skater(i), {"GP": 82, "PTS": 40 + i}) for i in range(20)]
    league = League.from_lines(lines, SOURCES)
    unplayed = league.shares(skater(99), {"GP": 0})
    assert unplayed["speed"] < min(league.shares(*pair)["speed"] for pair in lines)


def test_counting_stats_rank_per_game_pulled_toward_the_groups_rate():
    # Two goals in two games is not a 82-goal season.
    lines = [(skater(i), {"GP": 82, "PTS": 40 + i}) for i in range(20)]
    hot_start = (skater(99), {"GP": 2, "PTS": 4})
    league = League.from_lines([*lines, hot_start], SOURCES)
    assert league.stats(*hot_start)["points"] < 2


def test_fights_fall_back_to_majors_then_penalty_minutes():
    league = League.from_lines([], SOURCES)
    assert league.stats(skater(1), {"GP": 10, "fightingPenalties": 3, "PIM": 50})["fights"] == 0.3
    assert league.stats(skater(1), {"GP": 10, "PIM": 50})["fights"] == 5


def test_a_faceoff_percentage_needs_a_count_or_a_centre():
    league = League.from_lines([], SOURCES)
    # No count (the NHL API): a winger's FO% is over a handful of draws.
    assert league.stats(skater(1, "LW"), {"GP": 82, "FO%": 0.6})["faceoff_pct"] == 0
    assert "faceoff_pct" not in league.stats(skater(1, "LW"), {"GP": 82})
    assert league.stats(skater(1, "C"), {"GP": 82, "FO%": 0.6})["faceoff_pct"] == 60
    winger = league.stats(skater(1, "LW"), {"GP": 82, "FO%": 60, "totalFaceOffs": 100})
    assert winger["faceoff_pct"] == 55


def test_goalies_rank_season_totals():
    league = League.from_lines([], SOURCES)
    stats = league.stats(Player(id=1, name="G", position="G"), {"GP": 60, "W": 35, "SV%": 0.915})
    assert (stats["wins"], stats["games"]) == (35, 60)


@dataclass
class Attrs:
    speed: int = -1
    fighting: int = -1


def test_rate_sets_only_the_ratings_the_line_has_a_stat_for():
    lines = [(skater(i), {"GP": 82, "PTS": i}) for i in range(21)]
    league = League.from_lines(lines, SOURCES)
    attrs = Attrs()
    rate(attrs, *lines[-1], league, SPREAD)
    assert attrs.fighting == -1
    assert attrs.speed > 15
