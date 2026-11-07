"""Hockey helpers every NHL mapper shares: a stat that may be missing, a rate
pulled toward its prior by a few games, how many games a goalie's line counts."""

from retro_roster_patcher.core.hockey import (
    NOMINAL_GAMES,
    SHRINK_GAMES,
    goalie_games,
    shrunk,
    stat,
)


def test_a_missing_or_unreadable_stat_is_none_not_zero():
    assert stat({"G": 12}, "G") == 12.0
    assert stat({"G": "7"}, "G") == 7.0
    assert stat({}, "G") is None
    assert stat({"G": None}, "G") is None


def test_a_rate_is_pulled_toward_its_prior_by_shrink_games():
    assert SHRINK_GAMES == 10
    assert shrunk(1.0, 0.9, 10) == 0.95
    assert shrunk(1.0, 0.9, 0) == 0.9
    assert shrunk(None, 0.9, 50) == 0.9


def test_a_goalies_games_are_gp_else_wins_else_one_for_a_save_percentage():
    assert goalie_games({"GP": 30, "W": 12}) == 30
    assert goalie_games({"W": 12, "SV%": 0.91}) == 12
    assert goalie_games({"SV%": 0.91}) == NOMINAL_GAMES == 1
    assert goalie_games({}) == 0
