"""The ported NBA Live 95 stat mapper.

Sixteen attributes on a 25-99 scale -- the ROM's field is 0-99, but `_clamp`'s
default floor is 25, so nothing this mapper produces is ever below it. Each
attribute is a linear scaling of one ESPN per-game average inside a fixed
window, except dunking and speed, which are position constants with one bonus
each.

The expected numbers below are computed by hand from the windows the code
documents, not by calling `_scale`: a test that derived its expectation from the
function under test would agree with any window.
"""

import pytest

from retro_roster_patcher.games.nbalive95_genesis.models import (
    POSITION_C,
    POSITION_PF,
    POSITION_PG,
    POSITION_SF,
    POSITION_SG,
)
from retro_roster_patcher.games.nbalive95_genesis.stat_mapper import (
    POSITION_DEFAULTS,
    NBALive95StatMapper,
    _clamp,
    _scale,
)
from retro_roster_patcher.sports.models import Player

# Indices into the 16-byte ratings block, from `models.RATING_NAMES`.
GOALS, THREE_PT, FT, DUNKING = 0, 1, 2, 3
STEALING, BLOCKS, OFF_REB, DEF_REB = 4, 5, 6, 7
PASSING, OFF_AWARENESS, DEF_AWARENESS = 8, 9, 10
SPEED, QUICKNESS, JUMPING, DRIBBLING, STRENGTH = 11, 12, 13, 14, 15


@pytest.fixture
def mapper():
    return NBALive95StatMapper()


def _player(**overrides):
    fields = {
        "id": 1,
        "name": "Michael Jordan",
        "position": "SG",
        "number": 23,
        "age": 30,
        "weight": 0.0,
    }
    fields.update(overrides)
    return Player(**fields)


def _ratings(mapper, stats, position="SF"):
    return mapper.map_player(_player(position=position), stats).ratings


# -- the two scaling primitives ---------------------------------------------


def test_the_clamp_floor_is_twenty_five_and_not_zero():
    """The reason the README calls this a 25-99 scale on a 0-99 field."""
    assert _clamp(0) == 25


def test_the_clamp_ceiling_is_ninety_nine():
    assert _clamp(500) == 99


def test_a_value_inside_the_clamp_passes_through():
    assert _clamp(63) == 63


def test_the_bottom_of_a_window_scales_to_the_floor():
    assert _scale(0.0, 0.0, 100.0) == 25


def test_the_top_of_a_window_scales_to_the_ceiling():
    assert _scale(100.0, 0.0, 100.0) == 99


def test_the_middle_of_a_window_scales_to_the_middle_of_the_range():
    assert _scale(50.0, 0.0, 100.0) == 62


def test_a_value_under_the_window_clamps_up_rather_than_going_negative():
    assert _scale(-50.0, 0.0, 100.0) == 25


def test_a_value_over_the_window_clamps_down():
    assert _scale(500.0, 0.0, 100.0) == 99


def test_a_window_with_no_width_answers_fifty_rather_than_dividing_by_zero():
    assert _scale(7.0, 5.0, 5.0) == 50


def test_an_inverted_window_answers_fifty_too():
    assert _scale(7.0, 9.0, 5.0) == 50


# -- one attribute at a time ------------------------------------------------


def test_shooting_comes_from_field_goal_percentage(mapper):
    """.465 is halfway across the .380-.550 window, and halfway is 62."""
    assert _ratings(mapper, {"FG%": 46.5})[GOALS] == 62


def test_three_point_shooting_comes_from_three_point_percentage(mapper):
    assert _ratings(mapper, {"3P%": 33.5})[THREE_PT] == 62


def test_free_throws_come_from_free_throw_percentage(mapper):
    assert _ratings(mapper, {"FT%": 76.0})[FT] == 62


def test_percentages_are_read_as_hundredths_not_as_fractions(mapper):
    """ESPN publishes 46.5, not 0.465. Reading it as a fraction floors the rating."""
    assert _ratings(mapper, {"FG%": 0.465})[GOALS] == 25


def test_dunking_is_a_position_constant(mapper):
    found = [
        _ratings(mapper, {"PTS": 1}, position=position)[DUNKING]
        for position in ("PG", "SG", "SF", "PF", "C")
    ]
    assert found == [35, 40, 55, 60, 55]


def test_dunking_gains_ten_above_fifty_two_percent_shooting(mapper):
    assert _ratings(mapper, {"FG%": 53.0}, position="SF")[DUNKING] == 65


def test_dunking_gains_nothing_at_exactly_fifty_two_percent(mapper):
    assert _ratings(mapper, {"FG%": 52.0}, position="SF")[DUNKING] == 55


def test_stealing_comes_from_steals_a_game(mapper):
    assert _ratings(mapper, {"STL": 1.15})[STEALING] == 62


def test_blocking_comes_from_blocks_a_game(mapper):
    assert _ratings(mapper, {"BLK": 1.3})[BLOCKS] == 62


def test_offensive_rebounding_comes_from_offensive_rebounds_a_game(mapper):
    assert _ratings(mapper, {"ORPG": 1.9})[OFF_REB] == 62


def test_offensive_rebounding_falls_back_to_the_second_espn_key(mapper):
    assert _ratings(mapper, {"OREB": 1.9})[OFF_REB] == 62


def test_defensive_rebounding_comes_from_defensive_rebounds_a_game(mapper):
    assert _ratings(mapper, {"DRPG": 5.0})[DEF_REB] == 62


def test_defensive_rebounding_falls_back_to_the_second_espn_key(mapper):
    assert _ratings(mapper, {"DREB": 5.0})[DEF_REB] == 62


def test_passing_comes_from_assists_a_game(mapper):
    assert _ratings(mapper, {"AST": 5.5})[PASSING] == 62


def test_offensive_awareness_comes_from_points_a_game(mapper):
    assert _ratings(mapper, {"PTS": 17.5})[OFF_AWARENESS] == 62


def test_defensive_awareness_is_a_composite_of_three_stats(mapper):
    """2 x steals + 1.5 x blocks + 0.5 x defensive rebounds, over a 1.0-12.0 window."""
    assert _ratings(mapper, {"STL": 2.0, "BLK": 1.0, "DRPG": 2.0})[DEF_AWARENESS] == 62


def test_defensive_awareness_weights_steals_above_blocks(mapper):
    """Same total input, distributed two ways: the steal-heavy line rates higher."""
    steals = _ratings(mapper, {"STL": 3.0})[DEF_AWARENESS]
    blocks = _ratings(mapper, {"BLK": 3.0})[DEF_AWARENESS]
    assert steals > blocks


def test_speed_is_a_position_constant(mapper):
    found = [
        _ratings(mapper, {"PTS": 1}, position=position)[SPEED]
        for position in ("PG", "SG", "SF", "PF", "C")
    ]
    assert found == [75, 65, 55, 40, 35]


def test_speed_gains_eight_above_one_point_two_steals(mapper):
    assert _ratings(mapper, {"STL": 1.3}, position="SF")[SPEED] == 63


def test_speed_gains_nothing_at_exactly_one_point_two_steals(mapper):
    assert _ratings(mapper, {"STL": 1.2}, position="SF")[SPEED] == 55


def test_quickness_is_steals_and_assists_together(mapper):
    """2 x steals + 0.5 x assists, over a 1.0-8.0 window."""
    assert _ratings(mapper, {"STL": 1.0, "AST": 5.0})[QUICKNESS] == 62


def test_jumping_comes_from_blocks(mapper):
    assert _ratings(mapper, {"BLK": 2.125})[JUMPING] == 62


def test_jumping_gains_a_flat_five_above_fifty_percent_shooting(mapper):
    """The bonus is added to the input, before scaling, not to the rating."""
    assert _ratings(mapper, {"BLK": 0.5})[JUMPING] == 30
    assert _ratings(mapper, {"BLK": 0.5, "FG%": 60.0})[JUMPING] == 79


def test_dribbling_rewards_assists_and_punishes_turnovers(mapper):
    generous = _ratings(mapper, {"AST": 6.0, "TO": 3.0})[DRIBBLING]
    careless = _ratings(mapper, {"AST": 6.0, "TO": 12.0})[DRIBBLING]
    assert generous > careless


def test_dribbling_assumes_the_worst_turnover_ratio_when_there_are_no_assists(mapper):
    """`to_ratio` defaults to 2.0, which zeroes the bonus term entirely."""
    assert _ratings(mapper, {"TO": 0.0})[DRIBBLING] == 25


def test_dribbling_reads_the_alternate_turnover_key(mapper):
    named = _ratings(mapper, {"AST": 6.0, "TO": 12.0})[DRIBBLING]
    alternate = _ratings(mapper, {"AST": 6.0, "TOPG": 12.0})[DRIBBLING]
    assert named == alternate


def test_strength_comes_from_total_rebounds(mapper):
    assert _ratings(mapper, {"REB": 5.5}, position="SF")[STRENGTH] == 53


def test_strength_adds_two_for_a_centre_or_a_power_forward(mapper):
    assert _ratings(mapper, {"REB": 5.5}, position="C")[STRENGTH] == 69
    assert _ratings(mapper, {"REB": 5.5}, position="PF")[STRENGTH] == 69


def test_strength_adds_nothing_for_a_guard(mapper):
    assert _ratings(mapper, {"REB": 5.5}, position="PG")[STRENGTH] == 53


def test_a_stat_line_of_nones_is_read_as_zero_rather_than_raising(mapper):
    """ESPN omits a category by sending `null`, and `or 0` is what absorbs it."""
    assert _ratings(mapper, {"PTS": None, "AST": None, "FG%": None})[OFF_AWARENESS] == 25


def test_every_rating_a_stat_line_produces_is_inside_the_field_the_rom_holds(mapper):
    ratings = _ratings(mapper, {"PTS": 40.0, "AST": 15.0, "BLK": 5.0, "FG%": 70.0, "STL": 4.0})
    assert len(ratings) == 16
    assert [r for r in ratings if not 25 <= r <= 99] == []


# -- position defaults ------------------------------------------------------


def test_a_player_with_no_stats_gets_the_position_defaults(mapper):
    assert _ratings(mapper, {}) == POSITION_DEFAULTS[POSITION_SF]


def test_each_position_has_its_own_default_row(mapper):
    rows = [_ratings(mapper, {}, position=name) for name in ("PG", "SG", "SF", "PF", "C")]
    assert rows == [
        POSITION_DEFAULTS[POSITION_PG],
        POSITION_DEFAULTS[POSITION_SG],
        POSITION_DEFAULTS[POSITION_SF],
        POSITION_DEFAULTS[POSITION_PF],
        POSITION_DEFAULTS[POSITION_C],
    ]


def test_the_five_default_rows_are_not_the_same_row(mapper):
    """Guards the test above from passing on five references to one list."""
    assert len({tuple(row) for row in POSITION_DEFAULTS.values()}) == 5


def test_the_default_row_is_copied_and_not_shared_with_the_record(mapper):
    """The shared-mutable-default bug both NHL 94 ports had is absent here.

    `map_player` copies with `list(...)`, so a caller mutating one record's
    ratings cannot reach the module-level table -- and this is asserted rather
    than assumed, because the two sibling ports needed a fix for exactly this.
    """
    record = mapper.map_player(_player(position="C"), None)
    record.ratings[0] = 7
    assert POSITION_DEFAULTS[POSITION_C][0] == 55
    assert mapper.map_player(_player(position="C"), None).ratings[0] == 55


def test_an_empty_stat_dict_is_treated_as_no_stats_at_all(mapper):
    """`if stats:` and not `if stats is not None:`, so `{}` takes the defaults."""
    assert _ratings(mapper, {}) == _ratings(mapper, None)


# -- the rest of the record -------------------------------------------------


def test_the_position_byte_is_the_games_own_encoding(mapper):
    found = [
        mapper.map_player(_player(position=name), None).position
        for name in ("C", "PF", "SF", "PG", "SG")
    ]
    assert found == [POSITION_C, POSITION_PF, POSITION_SF, POSITION_PG, POSITION_SG]


def test_espn_combination_positions_are_folded_onto_the_games_five(mapper):
    found = [mapper._normalize_position(name) for name in ("G", "F", "F-C", "C-F", "G-F", "F-G")]
    assert found == ["PG", "SF", "PF", "C", "SG", "SF"]


def test_a_position_is_normalised_case_insensitively_and_without_padding(mapper):
    assert mapper._normalize_position("  pg  ") == "PG"


def test_an_unknown_position_becomes_a_small_forward(mapper):
    assert mapper._normalize_position("QB") == "SF"


def test_an_empty_position_becomes_a_small_forward(mapper):
    assert mapper._normalize_position("") == "SF"


def test_a_two_part_name_splits_into_surname_and_forename(mapper):
    assert mapper._split_name("Michael Jordan") == ("Jordan", "Michael")


def test_a_one_part_name_has_no_forename(mapper):
    assert mapper._split_name("Nene") == ("Nene", "")


def test_an_empty_name_becomes_the_placeholder(mapper):
    assert mapper._split_name("   ") == ("Player", "A")


def test_a_compound_surname_keeps_both_parts(mapper):
    assert mapper._split_name("Karl Anthony Towns") == ("Anthony Towns", "Karl")


def test_a_generational_suffix_is_dropped_from_the_surname(mapper):
    assert mapper._split_name("Gary Payton II") == ("Payton", "Gary")


def test_a_junior_suffix_is_dropped_too(mapper):
    assert mapper._split_name("Jaren Jackson Jr.") == ("Jackson", "Jaren")


def test_a_name_that_is_nothing_but_a_suffix_keeps_the_suffix(mapper):
    """An oddity of the fallback, pinned: with every part dropped it takes the last."""
    assert mapper._split_name("John Jr.") == ("Jr.", "John")


def test_height_is_a_position_default_because_espn_reports_none(mapper):
    found = [
        mapper.map_player(_player(position=name), None).height_inches
        for name in ("PG", "SG", "SF", "PF", "C")
    ]
    assert found == [74, 77, 79, 81, 83]


def test_weight_is_espns_when_it_reports_one(mapper):
    assert mapper.map_player(_player(weight=243.7), None).weight_lbs == 243


def test_weight_is_a_position_default_when_espn_reports_zero(mapper):
    found = [
        mapper.map_player(_player(position=name, weight=0.0), None).weight_lbs
        for name in ("PG", "SG", "SF", "PF", "C")
    ]
    assert found == [190, 205, 220, 240, 255]


def test_experience_is_years_past_twenty_one(mapper):
    assert mapper.map_player(_player(age=30), None).experience == 9


def test_a_player_younger_than_twenty_one_has_no_experience(mapper):
    assert mapper.map_player(_player(age=19), None).experience == 0


def test_an_unknown_age_gives_no_experience(mapper):
    assert mapper.map_player(_player(age=0), None).experience == 0


def test_a_missing_jersey_number_becomes_zero(mapper):
    assert mapper.map_player(_player(number=None), None).jersey == 0


def test_a_mapped_record_carries_no_season_stats_of_its_own(mapper):
    """INHERITED DEFECT, pinned: 17 zeros, which the writer puts over the ROM's."""
    assert mapper.map_player(_player(), {"PTS": 30.0}).season_stats == [0] * 17


def test_a_mapped_record_carries_no_appearance_at_all(mapper):
    """ESPN publishes neither, so both stay at the "not supplied" default.

    `rom_writer.write_player` reads that 0 as "not supplied" and leaves the
    image's own byte alone; it used to write it, and every patched player came
    out with the same skin and the same hair.
    """
    record = mapper.map_player(_player(), {"PTS": 30.0})
    assert record.skin_color == 0
    assert record.hair_style == 0


# -- roster selection -------------------------------------------------------


def _squad(positions, minutes=None):
    minutes = minutes or {}
    players = [
        Player(id=index, name=f"Player {index}", position=position, number=index)
        for index, position in enumerate(positions)
    ]
    stats = {str(index): {"MPG": minutes.get(index, 0)} for index in range(len(players))}
    return players, stats


def test_a_roster_is_cut_to_twelve(mapper):
    players, stats = _squad(["PG", "SG", "SF", "PF", "C"] * 5)
    assert len(mapper.select_roster(players, stats)) == 12


def test_a_very_large_squad_is_still_cut_to_twelve(mapper):
    """The trailing `selected[:12]` is REDUNDANT and this says so.

    Ten come from the position pass and both fill loops test `len(selected) >=
    12` before every append, so the list can never exceed twelve and the slice
    can never truncate. A mutation to `selected[:13]` survives the whole suite
    for exactly that reason: it is an equivalent mutant, not a hole. Kept here
    because "cut to twelve" is the behaviour a caller depends on however it is
    achieved.
    """
    players, stats = _squad(["PG", "SG", "SF", "PF", "C"] * 20)
    assert len(mapper.select_roster(players, stats)) == 12


def test_a_roster_takes_two_of_each_position_first(mapper):
    players, stats = _squad(["PG", "SG", "SF", "PF", "C"] * 5)
    chosen = mapper.select_roster(players, stats)
    counted = [
        len([p for p in chosen[:10] if mapper._normalize_position(p.position) == name])
        for name in ("PG", "SG", "SF", "PF", "C")
    ]
    assert counted == [2, 2, 2, 2, 2]


def test_the_first_ten_are_ordered_by_position_and_not_by_minutes(mapper):
    """`position_targets` is walked PG, SG, SF, PF, C, so the block order is fixed."""
    players, stats = _squad(["PG", "SG", "SF", "PF", "C"] * 5)
    chosen = mapper.select_roster(players, stats)
    assert [mapper._normalize_position(p.position) for p in chosen[:10]] == [
        "PG",
        "PG",
        "SG",
        "SG",
        "SF",
        "SF",
        "PF",
        "PF",
        "C",
        "C",
    ]


def test_within_a_position_the_busier_player_is_picked(mapper):
    players, stats = _squad(["PG", "PG", "PG"], minutes={0: 5, 1: 34, 2: 20})
    chosen = mapper.select_roster(players, stats)
    assert [p.id for p in chosen[:2]] == [1, 2]


def test_points_break_a_tie_on_minutes(mapper):
    players, _ = _squad(["PG", "PG"])
    stats = {"0": {"MPG": 30, "PTS": 4}, "1": {"MPG": 30, "PTS": 22}}
    assert [p.id for p in mapper.select_roster(players, stats)] == [1, 0]


def test_the_alternate_minutes_key_is_read(mapper):
    players, _ = _squad(["PG", "PG"])
    stats = {"0": {"MIN": 2}, "1": {"MIN": 38}}
    assert [p.id for p in mapper.select_roster(players, stats)] == [1, 0]


def test_the_last_two_spots_go_to_the_busiest_players_left(mapper):
    # The two starting point guards outplay the two reserves, so the reserves
    # survive the position pass and meet each other in the bench pass.
    players, stats = _squad(
        ["PG", "PG", "SG", "SG", "SF", "SF", "PF", "PF", "C", "C", "PG", "PG"],
        minutes={0: 40, 1: 39, 10: 5, 11: 20},
    )
    chosen = mapper.select_roster(players, stats)
    assert [p.id for p in chosen[:2]] == [0, 1]
    assert [p.id for p in chosen[10:]] == [11, 10]


def test_a_squad_smaller_than_twelve_is_returned_whole(mapper):
    players, stats = _squad(["PG", "SG", "C"])
    assert len(mapper.select_roster(players, stats)) == 3


def test_an_empty_squad_selects_nobody(mapper):
    assert mapper.select_roster([], {}) == []


def test_selection_works_with_no_stats_at_all(mapper):
    players, _ = _squad(["PG", "SG", "SF", "PF", "C"])
    assert len(mapper.select_roster(players, None)) == 5


def test_the_eligibility_filter_filters_nobody(mapper):
    """The comment says it drops non-basketball positions; it cannot.

    `_normalize_position` answers `"SF"` for anything it does not recognise, so
    the membership test the filter runs is true for every player. Pinned as a
    comment-accuracy defect: a squad of goalkeepers is selected in full.
    """
    players, stats = _squad(["Goalkeeper"] * 12)
    assert len(mapper.select_roster(players, stats)) == 12


# -- slot lookup ------------------------------------------------------------


def test_a_team_code_maps_to_its_rom_slot(mapper):
    assert mapper.get_team_slot("BOS") == 1


def test_a_lower_case_code_maps_to_the_same_slot(mapper):
    assert mapper.get_team_slot("bos") == 1


def test_the_espn_alias_and_the_standard_code_reach_one_slot(mapper):
    pairs = [("GS", "GSW"), ("NYK", "NY"), ("SA", "SAS"), ("UTA", "UTAH"), ("WAS", "WSH")]
    found = [(mapper.get_team_slot(a), mapper.get_team_slot(b)) for a, b in pairs]
    assert found == [(8, 8), (17, 17), (23, 23), (25, 25), (26, 26)]


def test_a_relocated_franchise_maps_to_the_city_the_rom_knows(mapper):
    assert mapper.get_team_slot("OKC") == 24
    assert mapper.get_team_slot("BKN") == 16


def test_a_team_that_did_not_exist_in_1994_has_no_slot(mapper):
    found = [mapper.get_team_slot(code) for code in ("TOR", "MEM", "NOP", "NO")]
    assert found == [None, None, None, None]


def test_an_unknown_code_has_no_slot(mapper):
    assert mapper.get_team_slot("ZZZ") is None


def test_no_team_maps_to_the_all_star_or_slammers_slots(mapper):
    """Slots 27-29 exist and nothing reaches them, which is why `map_rosters`
    caps at 27 rather than 30."""
    from retro_roster_patcher.games.nbalive95_genesis.models import MODERN_NBA_TO_NBALIVE95

    assert [slot for slot in MODERN_NBA_TO_NBALIVE95.values() if slot >= 27] == []
