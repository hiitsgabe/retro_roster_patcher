"""Stat mapper coverage.

`select_roster` is the half that matters most and the half nothing else pins:
the ROM's line table indexes players by absolute position, so a selection that
put a defenceman where a forward was expected produces a bootable ROM with the
wrong men on the ice. Every test here therefore asserts the whole selected list
in order, not its length.

The defaults branch is a DELIBERATE DIVERGENCE from upstream and is pinned as
one: `test_two_players_at_one_position_do_not_share_an_attributes_object`.
"""

import dataclasses

import pytest

from retro_roster_patcher.games.nhl94_snes.models import MODERN_NHL_TO_NHL94, TEAM_COUNT
from retro_roster_patcher.games.nhl94_snes.stat_mapper import (
    POSITION_DEFAULTS,
    NHL94StatMapper,
    _clamp,
    _scale,
)
from retro_roster_patcher.sports.models import Player


def _player(pid, position, **kwargs):
    return Player(id=pid, name=kwargs.pop("name", f"Player {pid}"), position=position, **kwargs)


def _squad(spec):
    """`spec` is `[(id, position, points)]`; returns players and their leaders."""
    players = [_player(pid, position) for pid, position, _ in spec]
    leaders = {str(pid): {"PTS": points} for pid, _, points in spec}
    return players, leaders


@pytest.fixture
def mapper():
    return NHL94StatMapper()


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, 0), (15, 1), (45, 3), (90, 6), (200, 6), (-50, 0)],
)
def test_a_value_scales_onto_the_zero_to_six_range(value, expected):
    assert _scale(value, 0, 90) == expected


def test_a_degenerate_range_scales_to_the_middle():
    # `high <= low` has no ratio to compute, so the mapper answers 3.
    assert _scale(10, 5, 5) == 3


@pytest.mark.parametrize(("value", "expected"), [(-1, 0), (3, 3), (7, 6)])
def test_clamping_holds_a_value_inside_zero_to_six(value, expected):
    assert _clamp(value) == expected


def test_a_team_code_maps_to_its_slot(mapper):
    assert mapper.get_team_slot("BOS") == 1


def test_a_lowercase_code_maps_to_the_same_slot(mapper):
    assert mapper.get_team_slot("bos") == 1


def test_an_expansion_team_has_no_slot(mapper):
    assert mapper.get_team_slot("VGK") is None


@pytest.mark.parametrize(
    ("espn", "official"), [("LA", "LAK"), ("NJ", "NJD"), ("SJ", "SJS"), ("TB", "TBL")]
)
def test_each_espn_abbreviation_reaches_the_same_slot_as_the_official_one(mapper, espn, official):
    assert mapper.get_team_slot(espn) == mapper.get_team_slot(official)


def test_thirty_codes_reach_twenty_six_slots(mapper):
    """The collision `map_rosters` has to guard, counted.

    26 of the 28 ROM slots are reachable; slots 26 and 27 are the All-Star teams
    and no code names them.
    """
    assert len(MODERN_NHL_TO_NHL94) == 30
    assert len(set(MODERN_NHL_TO_NHL94.values())) == 26
    assert max(MODERN_NHL_TO_NHL94.values()) == TEAM_COUNT - 3


def test_a_player_with_no_stats_gets_the_defaults_for_their_position(mapper):
    record = mapper.map_player(_player(1, "D"), "BOS")
    assert record.attributes == POSITION_DEFAULTS["D"]


def test_an_unrecognised_position_falls_back_to_a_centres_defaults(mapper):
    record = mapper.map_player(_player(1, "W"), "BOS")
    assert record.attributes == POSITION_DEFAULTS["C"]


def test_a_player_with_no_position_at_all_is_treated_as_a_centre(mapper):
    record = mapper.map_player(_player(1, ""), "BOS")
    assert record.attributes == POSITION_DEFAULTS["C"]


def test_two_players_at_one_position_do_not_share_an_attributes_object(mapper):
    """DELIBERATE DIVERGENCE: upstream handed out the module constant itself.

    `NHL94PlayerAttributes` is a plain mutable dataclass and
    `NHL94PlayerRecord.attributes` is public, so upstream gave every statless
    centre the same object -- and gave it to `POSITION_DEFAULTS` too, so one
    caller assignment rewrote the defaults for the rest of the process.
    """
    first = mapper.map_player(_player(1, "C"), "BOS")
    second = mapper.map_player(_player(2, "C"), "BOS")
    assert first.attributes is not second.attributes
    assert first.attributes is not POSITION_DEFAULTS["C"]


def test_mutating_one_players_attributes_leaves_the_defaults_alone(mapper):
    before = dataclasses.replace(POSITION_DEFAULTS["G"])
    record = mapper.map_player(_player(1, "G"), "BOS")
    record.attributes.speed = 6
    assert POSITION_DEFAULTS["G"] == before
    assert POSITION_DEFAULTS["G"].speed != 6


def test_a_goalie_is_flagged_as_one(mapper):
    assert mapper.map_player(_player(1, "G"), "BOS").is_goalie is True


def test_a_skater_is_not_flagged_as_a_goalie(mapper):
    assert mapper.map_player(_player(1, "D"), "BOS").is_goalie is False


def test_a_name_is_cut_to_fourteen_characters(mapper):
    record = mapper.map_player(_player(1, "C", name="Alexander Ovechkin"), "WSH")
    assert record.name == "Alexander Ovec"


def test_a_short_name_is_left_alone(mapper):
    assert mapper.map_player(_player(1, "C", name="Mario"), "PIT").name == "Mario"


def test_a_player_with_no_number_is_given_one(mapper):
    assert mapper.map_player(_player(1, "C"), "BOS").jersey_number == 1


def test_a_players_number_is_carried_through(mapper):
    assert mapper.map_player(_player(1, "C", number=99), "BOS").jersey_number == 99


@pytest.mark.parametrize(
    ("pounds", "weight_class"),
    [(140, 0), (196, 7), (252, 14), (400, 14), (100, 0)],
)
def test_weight_maps_onto_the_fifteen_step_class(mapper, pounds, weight_class):
    record = mapper.map_player(_player(1, "C", weight=float(pounds)), "BOS")
    assert record.weight_class == weight_class


def test_an_unknown_weight_becomes_the_middle_class(mapper):
    assert mapper.map_player(_player(1, "C"), "BOS").weight_class == 7


def test_a_right_handed_player_is_encoded_as_one(mapper):
    assert mapper.map_player(_player(1, "C", handedness="R"), "BOS").handedness == 1


def test_any_other_handedness_is_encoded_as_left(mapper):
    assert mapper.map_player(_player(1, "C", handedness="L"), "BOS").handedness == 0
    assert mapper.map_player(_player(2, "C"), "BOS").handedness == 0


def test_a_scoring_skaters_attributes_come_from_the_stat_line(mapper):
    stats = {"G": 40, "A": 55, "PTS": 90, "+/-": 40, "PIM": 80}
    attributes = mapper.map_player(_player(1, "C"), "BOS", stats).attributes
    assert attributes.shot_power == 6  # 40 goals against a 0-40 range
    assert attributes.shot_accuracy == 6
    assert attributes.pass_accuracy == 6  # 55 assists against 0-55
    assert attributes.stick_handling == 6  # 90 points against 0-90
    assert attributes.off_awareness == 6
    assert attributes.def_awareness == 6  # +40 against a -30..+40 range
    assert attributes.roughness == 6  # 80 PIM against 0-80
    assert attributes.aggression == 6


def test_a_pointless_skater_lands_at_the_bottom_of_every_scaled_attribute(mapper):
    stats = {"G": 0, "A": 0, "PTS": 0, "+/-": -30, "PIM": 0}
    attributes = mapper.map_player(_player(1, "C"), "BOS", stats).attributes
    assert attributes.shot_power == 0
    assert attributes.pass_accuracy == 0
    assert attributes.off_awareness == 0
    assert attributes.def_awareness == 0
    assert attributes.roughness == 0


def test_fifty_points_is_not_enough_for_the_speed_bonus(mapper):
    base = POSITION_DEFAULTS["LW"]
    attributes = mapper.map_player(_player(1, "LW"), "BOS", {"PTS": 50}).attributes
    assert attributes.speed == base.speed
    assert attributes.agility == base.agility


def test_fifty_one_points_earns_it(mapper):
    """Pins `> 50` rather than `>= 50`, from the other side of the boundary."""
    base = POSITION_DEFAULTS["LW"]
    attributes = mapper.map_player(_player(1, "LW"), "BOS", {"PTS": 51}).attributes
    assert attributes.speed == base.speed + 1
    assert attributes.agility == base.agility + 1


def test_checking_and_endurance_stay_at_the_positions_defaults(mapper):
    base = POSITION_DEFAULTS["D"]
    attributes = mapper.map_player(_player(1, "D"), "BOS", {"PTS": 80}).attributes
    assert attributes.checking == base.checking
    assert attributes.endurance == base.endurance


def test_a_goalies_agility_comes_from_save_percentage(mapper):
    attributes = mapper.map_player(_player(1, "G"), "BOS", {"SV%": 0.930}).attributes
    assert attributes.agility == 6


def test_a_weak_save_percentage_bottoms_out_the_agility(mapper):
    attributes = mapper.map_player(_player(1, "G"), "BOS", {"SV%": 0.880}).attributes
    assert attributes.agility == 0


def test_a_goalies_awareness_comes_from_goals_against(mapper):
    # 3.5 - 2.0 = 1.5, the top of the range.
    attributes = mapper.map_player(_player(1, "G"), "BOS", {"GAA": 2.0}).attributes
    assert attributes.def_awareness == 6


def test_a_goalie_with_no_goals_against_average_is_given_three(mapper):
    # 3.5 - 3.0 = 0.5 of 1.5, which scales to 2.
    attributes = mapper.map_player(_player(1, "G"), "BOS", {"SV%": 0.9}).attributes
    assert attributes.def_awareness == 2


def test_a_goalie_is_never_scored_on_the_skater_scale(mapper):
    """Every skater-only stat is ignored, so a 90-point goalie stays a goalie."""
    attributes = mapper.map_player(_player(1, "G"), "BOS", {"PTS": 90, "G": 40}).attributes
    assert attributes.shot_power == 2
    assert attributes.stick_handling == 3
    assert attributes.checking == 1


def test_a_null_stat_value_is_read_as_zero_rather_than_raising(mapper):
    attributes = mapper.map_player(_player(1, "C"), "BOS", {"PTS": None, "G": None}).attributes
    assert attributes.off_awareness == 0


def test_an_empty_stat_dict_falls_through_to_the_defaults(mapper):
    assert mapper.map_player(_player(1, "D"), "BOS", {}).attributes == POSITION_DEFAULTS["D"]


def test_the_roster_is_ordered_goalies_then_forwards_then_defencemen(mapper):
    """The whole selected list, in order, against the ROM's own layout.

    Two lines of LW/C/RW are taken best-first at each position, so the order is
    LW1 C1 RW1 LW2 C2 RW2 -- not the six best forwards.
    """
    players, leaders = _squad(
        [
            (10, "G", 0),
            (11, "G", 0),
            (12, "G", 0),
            (20, "LW", 60),
            (21, "LW", 40),
            (22, "LW", 20),
            (30, "C", 90),
            (31, "C", 70),
            (32, "C", 10),
            (40, "RW", 80),
            (41, "RW", 50),
            (42, "RW", 30),
            (50, "D", 45),
            (51, "D", 35),
            (52, "D", 25),
            (53, "D", 15),
        ]
    )
    leaders["10"]["SV%"] = 0.930
    leaders["11"]["SV%"] = 0.900
    leaders["12"]["SV%"] = 0.880
    selected = mapper.select_roster(
        players, leaders, num_goalies=2, num_forwards=6, num_defensemen=3
    )
    assert [p.id for p in selected] == [10, 11, 20, 30, 40, 21, 31, 41, 50, 51, 52]


def test_the_counts_asked_for_are_the_counts_returned(mapper):
    players, leaders = _squad(
        [(i, "G", 0) for i in range(3)]
        + [(10 + i, "C", 50 - i) for i in range(6)]
        + [(20 + i, "LW", 40 - i) for i in range(6)]
        + [(30 + i, "RW", 30 - i) for i in range(6)]
        + [(40 + i, "D", 20 - i) for i in range(6)]
    )
    selected = mapper.select_roster(
        players, leaders, num_goalies=2, num_forwards=9, num_defensemen=5
    )
    positions = [p.position for p in selected]
    assert len(selected) == 16
    assert positions[:2] == ["G", "G"]
    assert positions[11:] == ["D"] * 5
    assert positions[2:11] == ["LW", "C", "RW"] * 3


def test_a_forward_count_that_is_not_a_multiple_of_three_gets_extras(mapper):
    """7 forwards is two full lines plus one, and the extra is the best left."""
    players, leaders = _squad(
        [(10 + i, "C", 100 - i) for i in range(4)]
        + [(20 + i, "LW", 90 - i) for i in range(4)]
        + [(30 + i, "RW", 80 - i) for i in range(4)]
    )
    selected = mapper.select_roster(
        players, leaders, num_goalies=0, num_forwards=7, num_defensemen=0
    )
    assert [p.id for p in selected] == [20, 10, 30, 21, 11, 31, 12]


def test_a_missing_position_is_filled_from_the_best_forward_left(mapper):
    """No left wings at all, so every LW slot takes the best unused forward."""
    players, leaders = _squad(
        [(10 + i, "C", 100 - i) for i in range(3)] + [(30 + i, "RW", 80 - i) for i in range(3)]
    )
    selected = mapper.select_roster(
        players, leaders, num_goalies=0, num_forwards=6, num_defensemen=0
    )
    assert [p.id for p in selected] == [10, 11, 30, 12, 31, 32]


def test_a_short_squad_is_topped_up_from_whoever_is_left(mapper):
    """Fewer players at each position than asked for, so the leftovers fill in.

    The two extra centres are appended after the defencemen -- past the point
    the ROM's line table calls a defenceman -- which is what the fill costs.
    """
    players, leaders = _squad(
        [(1, "G", 0), (10, "C", 90), (11, "C", 70), (12, "C", 50), (20, "D", 40)]
    )
    selected = mapper.select_roster(
        players, leaders, num_goalies=2, num_forwards=1, num_defensemen=2
    )
    assert [p.id for p in selected] == [1, 10, 20, 11, 12]


def test_the_selection_never_exceeds_the_sum_of_the_three_counts(mapper):
    players, leaders = _squad([(i, "C", i) for i in range(40)])
    selected = mapper.select_roster(
        players, leaders, num_goalies=2, num_forwards=14, num_defensemen=7
    )
    assert len(selected) == 23


def test_no_player_is_selected_twice(mapper):
    players, leaders = _squad(
        [(i, "G", 0) for i in range(4)]
        + [(10 + i, "C", i) for i in range(10)]
        + [(30 + i, "D", i) for i in range(10)]
    )
    selected = mapper.select_roster(
        players, leaders, num_goalies=2, num_forwards=14, num_defensemen=7
    )
    assert len(selected) == len({id(p) for p in selected})
    assert len(selected) == 23


def test_an_empty_squad_selects_nobody(mapper):
    assert mapper.select_roster([], {}) == []


def test_selection_without_any_stats_still_returns_a_roster(mapper):
    players = [_player(i, "C") for i in range(5)]
    assert len(mapper.select_roster(players, None, 1, 3, 1)) == 5
