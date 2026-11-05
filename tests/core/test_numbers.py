"""`shirt_numbers`: distinct shirt numbers within a game's range, the provider's where they fit."""

import pytest

from retro_roster_patcher.core.numbers import shirt_numbers


def test_numbers_in_range_and_not_taken_are_kept():
    assert shirt_numbers([22, 2, 4, 14, 16], 32) == [22, 2, 4, 14, 16]


def test_a_repeat_takes_the_lowest_free_number_and_the_first_to_ask_keeps_his():
    assert shirt_numbers([10, 10, 10], 32) == [10, 1, 2]


def test_zero_negative_and_above_the_top_take_the_lowest_free_numbers_in_order():
    assert shirt_numbers([0, -3, 33, 64, 5], 32) == [1, 2, 3, 4, 5]


def test_a_later_players_own_number_is_not_given_away_to_an_earlier_one_who_has_none():
    # The first has no number and the third wants 1: he gets it, the first takes the next free.
    assert shirt_numbers([0, 10, 1, 10], 32) == [2, 10, 1, 3]


def test_the_top_is_a_parameter():
    assert shirt_numbers([63, 64, 32], 63) == [63, 1, 32]
    assert shirt_numbers([63, 64, 32], 32) == [1, 2, 32]
    assert shirt_numbers([5, 5, 6], 6) == [5, 1, 6]


def test_a_full_squad_of_nobodys_gets_1_to_the_count():
    assert shirt_numbers([0] * 23, 32) == list(range(1, 24))


def test_no_players_get_no_numbers():
    assert shirt_numbers([], 32) == []


def test_more_players_than_numbers_is_refused():
    with pytest.raises(ValueError):
        shirt_numbers([0] * 33, 32)
    assert shirt_numbers([0] * 32, 32) == list(range(1, 33))
