"""`NHL07StatMapper`: provider data onto NHL 07's 0-63 scale.

A faithful port, so almost nothing here is a claim about what the numbers
*should* be -- there is no reference for that and no disc to check against.
What is pinned is what the arithmetic actually does at the ends and in the
middle of each window, that every derived attribute moves with the stat it
claims to come from, and the two structural properties the ported code has to
have and did not: no shared mutable defaults, and a line assignment that fills
four complete forward lines.
"""

from __future__ import annotations

import pytest

from retro_roster_patcher.games.nhl07_psp.models import (
    MODERN_NHL_TO_NHL07,
    NHL07GoalieAttributes,
    NHL07PlayerRecord,
    NHL07SkaterAttributes,
)
from retro_roster_patcher.games.nhl07_psp.stat_mapper import (
    ATTR_MAX,
    DEFAULT_JERSEY,
    DEFAULT_WEIGHT,
    GOALIE_DEFAULTS,
    HAND_LEFT,
    HAND_RIGHT,
    MAX_PLAYERS,
    SKATER_DEFAULTS,
    NHL07StatMapper,
    _clamp,
    _defaults_for,
    _save_percentage,
    _scale,
    _stat,
)
from retro_roster_patcher.sports.models import Player

MAPPER = NHL07StatMapper()


def player(pid=1, name="Connor McDavid", position="C", **kw):
    fields = dict(
        id=pid,
        name=name,
        first_name=name.split(" ")[0],
        last_name=name.split(" ")[-1],
        age=27,
        nationality="CAN",
        position=position,
        number=97,
        photo_url="",
        weight=195.0,
        handedness="L",
    )
    fields.update(kw)
    return Player(**fields)


# -- _clamp and _scale -----------------------------------------------------


def test_clamp_leaves_a_value_inside_the_range():
    assert _clamp(31) == 31


def test_clamp_raises_a_negative_to_zero():
    assert _clamp(-9) == 0


def test_clamp_lowers_an_over_range_value_to_the_six_bit_maximum():
    assert _clamp(400) == ATTR_MAX


def test_the_attribute_maximum_is_six_bits():
    assert ATTR_MAX == 63


def test_scale_maps_the_bottom_of_the_window_to_zero():
    assert _scale(0, 0, 90) == 0


def test_scale_maps_the_top_of_the_window_to_the_maximum():
    assert _scale(90, 0, 90) == 63


def test_scale_maps_the_middle_of_the_window_to_about_half():
    # 45/90 of 63 is 31.5, which rounds to 32 under banker's rounding.
    assert _scale(45, 0, 90) == 32


def test_scale_clamps_a_value_above_the_window():
    assert _scale(200, 0, 90) == 63


def test_scale_clamps_a_value_below_the_window():
    assert _scale(-40, 0, 90) == 0


def test_an_empty_window_answers_the_midpoint():
    # Rather than dividing by zero. 32 and not 31, which is the midpoint of
    # 0..63 rounded up.
    assert _scale(5, 7, 7) == 32


def test_an_inverted_window_answers_the_midpoint():
    assert _scale(5, 9, 4) == 32


def test_a_narrow_window_still_spans_the_whole_scale():
    # The goalie save-percentage window is 0.05 wide, so a scale that lost
    # precision here would flatten every goalie.
    assert _scale(0.905, 0.880, 0.930) == 32


# -- _stat -----------------------------------------------------------------


def test_a_stat_present_and_non_zero_is_returned():
    assert _stat({"G": 21}, "G") == 21.0


def test_a_stat_reported_as_zero_falls_through_to_the_default():
    # `or` and not `in`, matching the source: a genuine zero and an absent stat
    # are the same thing here, and that is deliberate -- a player with no
    # recorded shots should take the position default, not a rating of zero.
    assert _stat({"GAA": 0}, "GAA", default=3.0) == 3.0


def test_a_stat_reported_as_none_falls_through():
    assert _stat({"PTS": None}, "PTS", default=7.0) == 7.0


def test_the_second_name_is_used_when_the_first_is_absent():
    assert _stat({"Shots": 140}, "SOG", "Shots") == 140.0


def test_the_first_name_wins_when_both_are_present():
    assert _stat({"SOG": 140, "Shots": 999}, "SOG", "Shots") == 140.0


def test_a_missing_stat_answers_the_default():
    assert _stat({}, "W", "Wins", default=-1.0) == -1.0


# -- position defaults -----------------------------------------------------


def test_every_position_has_its_own_defaults():
    assert sorted(SKATER_DEFAULTS) == ["C", "D", "LW", "RW"]


def test_a_centre_starts_with_the_best_faceoffs():
    assert SKATER_DEFAULTS["C"].faceoffs == 40


def test_a_defenceman_starts_with_the_worst_faceoffs():
    assert SKATER_DEFAULTS["D"].faceoffs == 15


def test_the_default_fighting_rating_fits_two_bits():
    # `FIGH` is two bits, so 0-3, and `_clamp`'s ceiling of 63 does not apply.
    assert [d.fighting for d in SKATER_DEFAULTS.values() if d.fighting > 3] == []


def test_the_goalie_default_fighting_rating_is_zero():
    assert GOALIE_DEFAULTS.fighting == 0


def test_the_defaults_helper_never_hands_back_the_shared_object():
    # Two of the migrated games shipped one attribute record shared by every
    # player on every team, where a single later mutation rewrote the league.
    assert _defaults_for("C") is not SKATER_DEFAULTS["C"]


def test_two_calls_for_one_position_hand_back_two_objects():
    assert _defaults_for("D") is not _defaults_for("D")


def test_the_copy_carries_the_same_values():
    assert _defaults_for("D") == SKATER_DEFAULTS["D"]


def test_mutating_a_copy_does_not_reach_the_shared_object():
    copy = _defaults_for("LW")
    copy.speed = 1
    assert SKATER_DEFAULTS["LW"].speed == 35


def test_an_unknown_position_falls_back_to_a_centres_defaults():
    assert _defaults_for("F") == SKATER_DEFAULTS["C"]


# -- map_player ------------------------------------------------------------


def test_a_name_is_split_on_the_first_space():
    record = MAPPER.map_player(player(name="Pierre-Luc Dubois"), "CBJ")
    assert record.first_name == "Pierre-Luc"


def test_everything_after_the_first_space_is_the_surname():
    record = MAPPER.map_player(player(name="Ryan Nugent Hopkins"), "EDM")
    assert record.last_name == "Nugent Hopkins"


def test_a_name_with_no_space_leaves_the_surname_empty():
    assert MAPPER.map_player(player(name="Cher"), "BOS").last_name == ""


def test_an_empty_name_leaves_both_names_empty():
    assert MAPPER.map_player(player(name=""), "BOS").first_name == ""


def test_a_name_longer_than_the_field_is_truncated():
    long = "Abcdefghijklmnopqrstuvwxyz"
    assert len(MAPPER.map_player(player(name=f"{long} X"), "BOS").first_name) == 19


def test_a_left_handed_player_maps_to_the_left_code():
    assert MAPPER.map_player(player(handedness="L"), "BOS").handedness == HAND_LEFT


def test_a_right_handed_player_maps_to_the_right_code():
    assert MAPPER.map_player(player(handedness="R"), "BOS").handedness == HAND_RIGHT


def test_an_unreported_hand_defaults_to_right():
    # Which makes every player with no reported hand right-handed on the disc,
    # rather than leaving the disc's own value: `HAND` is always written.
    assert MAPPER.map_player(player(handedness=""), "BOS").handedness == HAND_RIGHT


def test_the_left_and_right_hand_codes_differ():
    assert HAND_LEFT != HAND_RIGHT


def test_a_reported_weight_is_truncated_to_whole_pounds():
    assert MAPPER.map_player(player(weight=213.7), "BOS").weight == 213


def test_an_unreported_weight_takes_the_default():
    assert MAPPER.map_player(player(weight=0.0), "BOS").weight == DEFAULT_WEIGHT


def test_a_reported_number_is_kept():
    assert MAPPER.map_player(player(number=97), "BOS").jersey_number == 97


def test_a_missing_number_takes_the_default():
    assert MAPPER.map_player(player(number=None), "BOS").jersey_number == DEFAULT_JERSEY


def test_a_number_of_zero_takes_the_default():
    # `player.number or DEFAULT_JERSEY`, so a real number 0 -- which the NHL
    # does not issue -- becomes 1.
    assert MAPPER.map_player(player(number=0), "BOS").jersey_number == DEFAULT_JERSEY


def test_the_default_jersey_is_not_a_number_a_test_could_hit_by_accident():
    assert DEFAULT_JERSEY == 1


def test_a_lowercase_position_is_upper_cased():
    assert MAPPER.map_player(player(position="lw"), "BOS").position == "LW"


def test_an_empty_position_becomes_a_centre():
    assert MAPPER.map_player(player(position=""), "BOS").position == "C"


def test_a_goalie_is_flagged_as_one():
    assert MAPPER.map_player(player(position="G"), "BOS").is_goalie is True


def test_a_skater_is_not_flagged_as_a_goalie():
    assert MAPPER.map_player(player(position="D"), "BOS").is_goalie is False


def test_a_goalie_gets_goalie_attributes():
    assert type(MAPPER.map_player(player(position="G"), "BOS").goalie_attrs) is (
        NHL07GoalieAttributes
    )


def test_a_goalie_gets_no_skater_attributes():
    assert MAPPER.map_player(player(position="G"), "BOS").skater_attrs is None


def test_a_skater_gets_skater_attributes():
    assert type(MAPPER.map_player(player(position="D"), "BOS").skater_attrs) is (
        NHL07SkaterAttributes
    )


def test_a_skater_gets_no_goalie_attributes():
    assert MAPPER.map_player(player(position="D"), "BOS").goalie_attrs is None


def test_the_team_index_comes_from_the_abbreviation_table():
    assert MAPPER.map_player(player(), "EDM").team_index == MODERN_NHL_TO_NHL07["EDM"]


def test_a_lowercase_abbreviation_still_finds_its_slot():
    assert MAPPER.map_player(player(), "edm").team_index == MODERN_NHL_TO_NHL07["EDM"]


def test_an_unknown_abbreviation_maps_to_slot_zero():
    # A silent Anaheim, which is why `map_rosters` filters on
    # `get_team_slot(...) is None` before anything reaches here.
    assert MAPPER.map_player(player(), "ZZZ").team_index == 0


def test_a_missing_player_id_becomes_zero():
    assert MAPPER.map_player(player(pid=0), "BOS").player_id == 0


def test_a_player_with_no_stats_gets_the_position_defaults():
    assert MAPPER.map_player(player(position="D"), "BOS").skater_attrs == SKATER_DEFAULTS["D"]


def test_a_player_with_no_stats_does_not_get_the_shared_defaults_object():
    assert MAPPER.map_player(player(position="D"), "BOS").skater_attrs is not (SKATER_DEFAULTS["D"])


def test_a_goalie_with_no_stats_does_not_get_the_shared_defaults_object():
    assert MAPPER.map_player(player(position="G"), "BOS").goalie_attrs is not GOALIE_DEFAULTS


def test_two_defaulted_players_do_not_share_one_attribute_record():
    a = MAPPER.map_player(player(pid=1, position="C"), "BOS")
    b = MAPPER.map_player(player(pid=2, position="C"), "BOS")
    assert a.skater_attrs is not b.skater_attrs


def test_mutating_one_defaulted_players_attributes_leaves_the_other_alone():
    a = MAPPER.map_player(player(pid=1, position="C"), "BOS")
    b = MAPPER.map_player(player(pid=2, position="C"), "BOS")
    a.skater_attrs.speed = 0
    assert b.skater_attrs.speed == SKATER_DEFAULTS["C"].speed


def test_a_record_has_no_height_attribute():
    # The source carried one, wrote it from a `Player` attribute that has never
    # existed, and so flattened every patched player to the same height. See
    # `rom_writer.write_player_bio`.
    assert hasattr(NHL07PlayerRecord(), "height") is False


# -- skater stat mapping ---------------------------------------------------

ELITE = {"G": 40, "A": 55, "PTS": 95, "+/-": 40, "PIM": 80, "SOG": 300, "FO%": 60}
POOR = {"G": 1, "A": 1, "PTS": 2, "+/-": -30, "PIM": 0, "SOG": 40, "FO%": 32}


def test_an_elite_scorer_saturates_the_offensive_ratings():
    assert MAPPER.map_player(player(), "BOS", ELITE).skater_attrs.deking == 63


def test_a_poor_scorer_does_not():
    assert MAPPER.map_player(player(), "BOS", POOR).skater_attrs.deking == 1


def test_the_two_scorers_differ_on_every_points_derived_rating():
    elite = MAPPER.map_player(player(), "BOS", ELITE).skater_attrs
    poor = MAPPER.map_player(player(), "BOS", POOR).skater_attrs
    assert [
        name
        for name in ("deking", "puck_control", "hero", "potential")
        if getattr(elite, name) == getattr(poor, name)
    ] == []


def test_penalty_minutes_drive_toughness():
    assert MAPPER.map_player(player(), "BOS", ELITE).skater_attrs.toughness == 63


def test_penalty_minutes_also_drive_aggression():
    # One stat feeding three ratings, which is the source's design and worth
    # naming: `PIM` decides toughness, aggression and fighting on its own.
    assert MAPPER.map_player(player(), "BOS", ELITE).skater_attrs.aggression == 63


def test_penalty_minutes_drive_the_two_bit_fighting_rating():
    assert MAPPER.map_player(player(), "BOS", {"PIM": 120}).skater_attrs.fighting == 3


def test_the_fighting_rating_is_capped_at_three():
    assert MAPPER.map_player(player(), "BOS", {"PIM": 4000}).skater_attrs.fighting == 3


def test_forty_penalty_minutes_is_one_fighting_point():
    assert MAPPER.map_player(player(), "BOS", {"PIM": 40}).skater_attrs.fighting == 1


def test_thirty_nine_penalty_minutes_is_none():
    assert MAPPER.map_player(player(), "BOS", {"PIM": 39}).skater_attrs.fighting == 0


def test_a_reported_faceoff_percentage_overrides_the_position_default():
    record = MAPPER.map_player(player(position="D"), "BOS", {"FO%": 55, "PTS": 1})
    assert record.skater_attrs.faceoffs == _scale(55, 30, 60)


def test_an_unreported_faceoff_percentage_keeps_the_position_default():
    record = MAPPER.map_player(player(position="D"), "BOS", {"PTS": 1})
    assert record.skater_attrs.faceoffs == SKATER_DEFAULTS["D"].faceoffs


def test_the_two_faceoff_answers_differ():
    # Pins the pair above: with the same number either way, neither means
    # anything.
    assert _scale(55, 30, 60) != SKATER_DEFAULTS["D"].faceoffs


def test_a_defenceman_takes_his_checking_from_plus_minus():
    record = MAPPER.map_player(player(position="D"), "BOS", {"+/-": 40, "PTS": 1})
    assert record.skater_attrs.checking == 63


def test_a_forward_keeps_his_positional_checking():
    record = MAPPER.map_player(player(position="C"), "BOS", {"+/-": 40, "PTS": 1})
    assert record.skater_attrs.checking == SKATER_DEFAULTS["C"].checking


def test_a_defenceman_gets_a_balance_bonus():
    record = MAPPER.map_player(player(position="D"), "BOS", {"PTS": 1})
    assert record.skater_attrs.balance == SKATER_DEFAULTS["D"].balance + 3


def test_a_forward_gets_no_balance_bonus():
    record = MAPPER.map_player(player(position="C"), "BOS", {"PTS": 1})
    assert record.skater_attrs.balance == SKATER_DEFAULTS["C"].balance


@pytest.mark.parametrize("points,bonus", [(0, 0), (30, 0), (31, 3), (50, 3), (51, 5), (200, 5)])
def test_the_speed_bonus_steps_at_thirty_and_fifty_points(points, bonus):
    record = MAPPER.map_player(player(position="C"), "BOS", {"PTS": points})
    assert record.skater_attrs.speed == SKATER_DEFAULTS["C"].speed + bonus


def test_a_player_with_no_shots_is_given_a_ten_per_cent_shooting_rate():
    # And so lands at `_scale(10, 5, 20)`, the middle of the accuracy window,
    # rather than at the bottom.
    record = MAPPER.map_player(player(), "BOS", {"G": 0, "SOG": 0})
    assert record.skater_attrs.shot_accuracy == _scale(10, 5, 20)


def test_shot_accuracy_takes_the_better_of_goals_and_shooting_rate():
    record = MAPPER.map_player(player(), "BOS", {"G": 40, "SOG": 400})
    assert record.skater_attrs.shot_accuracy == 63


def test_wrist_accuracy_sits_two_below_the_goal_rating_when_that_wins():
    record = MAPPER.map_player(player(), "BOS", {"G": 20, "SOG": 400})
    assert record.skater_attrs.wrist_accuracy == _scale(20, 0, 40) - 2


def test_wrist_power_sits_three_below_the_goal_rating():
    record = MAPPER.map_player(player(), "BOS", {"G": 20})
    assert record.skater_attrs.wrist_power == _scale(20, 0, 40) - 3


def test_no_derived_skater_rating_exceeds_the_six_bit_range():
    record = MAPPER.map_player(
        player(), "BOS", {"G": 999, "A": 999, "PTS": 999, "+/-": 999, "PIM": 999, "SOG": 1}
    )
    attrs = record.skater_attrs
    over = [
        name
        for name, value in vars(attrs).items()
        if name != "fighting" and not 0 <= value <= ATTR_MAX
    ]
    assert over == []


def test_no_derived_skater_rating_falls_below_zero():
    record = MAPPER.map_player(player(), "BOS", {"G": 0, "A": 0, "PTS": 0, "+/-": -999, "PIM": 0})
    assert [name for name, value in vars(record.skater_attrs).items() if value < 0] == []


# -- goalie stat mapping ---------------------------------------------------


def test_an_elite_save_percentage_saturates_the_save_ratings():
    record = MAPPER.map_player(player(position="G"), "BOS", {"SV%": 0.930})
    assert record.goalie_attrs.rebound_ctrl == 63


def test_a_poor_save_percentage_bottoms_them_out():
    record = MAPPER.map_player(player(position="G"), "BOS", {"SV%": 0.870})
    assert record.goalie_attrs.rebound_ctrl == 0


def test_all_five_save_zones_move_together():
    record = MAPPER.map_player(player(position="G"), "BOS", {"SV%": 0.905})
    attrs = record.goalie_attrs
    assert [attrs.five_hole, attrs.glove_high, attrs.glove_low] == [32, 32, 32]


def test_the_stick_zones_sit_two_below_the_glove_zones():
    record = MAPPER.map_player(player(position="G"), "BOS", {"SV%": 0.905})
    assert record.goalie_attrs.stick_high == record.goalie_attrs.glove_high - 2


def test_a_low_goals_against_average_raises_the_poke_check():
    assert (
        MAPPER.map_player(player(position="G"), "BOS", {"GAA": 2.0}).goalie_attrs.poke_check == 63
    )


def test_a_high_goals_against_average_lowers_it():
    assert MAPPER.map_player(player(position="G"), "BOS", {"GAA": 3.5}).goalie_attrs.poke_check == 0


def test_an_unreported_goals_against_average_assumes_three():
    a = MAPPER.map_player(player(position="G"), "BOS", {"SV%": 0.9}).goalie_attrs
    b = MAPPER.map_player(player(position="G"), "BOS", {"SV%": 0.9, "GAA": 3.0}).goalie_attrs
    assert a.poke_check == b.poke_check


def test_the_win_bonus_is_one_point_per_four_wins():
    assert MAPPER.map_player(player(position="G"), "BOS", {"W": 20}).goalie_attrs.speed == 25 + 5


def test_the_win_bonus_is_capped_at_ten():
    assert MAPPER.map_player(player(position="G"), "BOS", {"W": 400}).goalie_attrs.speed == 25 + 10


def test_the_alternative_wins_key_is_read():
    assert MAPPER.map_player(player(position="G"), "BOS", {"Wins": 20}).goalie_attrs.speed == 30


def test_a_save_percentage_below_one_is_read_as_a_fraction():
    assert _save_percentage({"SV%": 0.912}) == 0.912


def test_a_save_percentage_above_one_is_read_as_a_percentage():
    assert _save_percentage({"SV%": 91.2}) == 0.912


def test_a_perfect_game_reads_as_one_under_either_convention():
    # The boundary, both sides. 1.0 is a fraction -- reading it as 1% would be
    # absurd -- and 100.0 is the same season written the other way.
    assert [_save_percentage({"SV%": 1.0}), _save_percentage({"SV%": 100.0})] == [1.0, 1.0]


def test_an_absent_save_percentage_is_still_zero():
    assert _save_percentage({}) == 0.0


def test_a_percentage_save_line_would_have_saturated_the_scale():
    # The behaviour being diverged from, stated with the module's own `_scale`
    # so the fix below is a difference and not a claim.
    assert _scale(91.2, 0.880, 0.930) == 63


def test_a_percentage_save_line_now_lands_where_the_fraction_does():
    # 0.912 is 64% of the way up a 0.880-0.930 window, so 40 of 63.
    attrs = MAPPER.map_player(player(position="G"), "BOS", {"SV%": 91.2}).goalie_attrs
    assert [
        attrs.rebound_ctrl,
        attrs.agility,
        attrs.five_hole,
        attrs.glove_high,
        attrs.glove_low,
    ] == [40, 40, 40, 40, 40]


def test_the_three_offset_save_ratings_move_with_it_too():
    # The other three of the eight `SV%` drives, each at its own offset. Under
    # the source all eight were 63 and indistinguishable.
    attrs = MAPPER.map_player(player(position="G"), "BOS", {"SV%": 91.2}).goalie_attrs
    assert [attrs.shot_recovery, attrs.stick_high, attrs.stick_low] == [37, 38, 38]


def test_the_two_conventions_produce_the_same_goalie():
    # Same season, two ways of writing it, one record. Not vacuous: the previous
    # two tests fix the actual numbers, so this cannot be satisfied by a mapper
    # that returns a constant.
    percent = MAPPER.map_player(player(position="G"), "BOS", {"SV%": 91.2}).goalie_attrs
    fraction = MAPPER.map_player(player(position="G"), "BOS", {"SV%": 0.912}).goalie_attrs
    assert percent == fraction


def test_goalies_reported_in_percentages_still_sort_best_first():
    # `select_roster` reads `SV%` too. Dividing by 100 is monotonic, so no
    # ordering moves -- but only because both readings go through one function.
    goalies = [player(pid=1, position="G"), player(pid=2, position="G")]
    stats = {"1": {"SV%": 88.0}, "2": {"SV%": 92.0}}
    assert [p.id for p in MAPPER.select_roster(goalies, stats)] == [2, 1]


def test_a_file_that_mixes_the_two_conventions_still_starts_the_better_goalie():
    """Why the sort key goes through the conversion and not through `_stat`.

    Within one convention the two are interchangeable, since dividing by 100 is
    monotonic. Across two they are not: `91.2` scores 91 200 against `0.930`'s
    930, so the source's key puts the .912 goalie ahead of the .930 one. A
    hand-assembled `--rosters` file is exactly where that mixture appears.
    """
    goalies = [player(pid=1, position="G"), player(pid=2, position="G")]
    stats = {"1": {"SV%": 91.2}, "2": {"SV%": 0.930}}
    assert [p.id for p in MAPPER.select_roster(goalies, stats)] == [2, 1]


def test_a_goalie_never_fights():
    assert MAPPER.map_player(player(position="G"), "BOS", {"SV%": 0.9}).goalie_attrs.fighting == 0


def test_a_goalies_toughness_is_a_constant():
    # INHERITED DEFECT, PRESERVED. Written from no stat at all, which is the
    # source's behaviour: every patched goalie has the same toughness whatever
    # his season looked like. Not fixed because there is nothing to fix it from
    # -- no provider here reports a goalie toughness input and inventing a
    # derivation would be new behaviour dressed as a bug fix. `_map_goalie_stats`
    # carries the argument.
    a = MAPPER.map_player(player(position="G"), "BOS", {"SV%": 0.93, "W": 60}).goalie_attrs
    b = MAPPER.map_player(player(position="G"), "BOS", {"SV%": 0.87, "W": 0}).goalie_attrs
    assert a.toughness == b.toughness


def test_a_goalies_toughness_is_the_one_the_defaults_give_him():
    # And the constant is `GOALIE_DEFAULTS`', so a goalie the provider has no
    # line for and a Vezina winner come out equal on it. Stated against the
    # defaults rather than against `25`, so the two cannot drift apart silently.
    attrs = MAPPER.map_player(player(position="G"), "BOS", {"SV%": 0.93, "W": 60}).goalie_attrs
    assert attrs.toughness == GOALIE_DEFAULTS.toughness


def test_three_goalie_ratings_are_written_from_no_stat_at_all():
    """The defect's real width: toughness is one of three, not one of one.

    Every key `_map_goalie_stats` reads, set to values that move each of the
    other fourteen ratings off its default. These three do not move, and the
    list is asserted whole so a fourth constant appearing here fails rather than
    passing unnoticed.
    """
    attrs = MAPPER.map_player(
        player(position="G"), "BOS", {"SV%": 0.93, "GAA": 1.0, "W": 60}
    ).goalie_attrs
    unmoved = [
        name for name, value in vars(attrs).items() if value == getattr(GOALIE_DEFAULTS, name)
    ]
    assert unmoved == ["toughness", "fighting", "passing"]


def test_the_two_goalies_do_differ_elsewhere():
    # Pins the test above: if every rating were constant, it would be vacuous.
    a = MAPPER.map_player(player(position="G"), "BOS", {"SV%": 0.93, "W": 60}).goalie_attrs
    b = MAPPER.map_player(player(position="G"), "BOS", {"SV%": 0.87, "W": 0}).goalie_attrs
    assert a.agility != b.agility


def test_no_derived_goalie_rating_exceeds_the_six_bit_range():
    record = MAPPER.map_player(player(position="G"), "BOS", {"SV%": 9.9, "GAA": -50, "W": 999})
    over = [
        name
        for name, value in vars(record.goalie_attrs).items()
        if name != "fighting" and not 0 <= value <= ATTR_MAX
    ]
    assert over == []


# -- get_team_slot ---------------------------------------------------------


def test_every_abbreviation_in_the_table_resolves():
    assert [c for c in MODERN_NHL_TO_NHL07 if MAPPER.get_team_slot(c) is None] == []


def test_an_unknown_abbreviation_answers_none():
    assert MAPPER.get_team_slot("XYZ") is None


def test_the_espn_and_nhl_spellings_of_los_angeles_reach_one_slot():
    assert MAPPER.get_team_slot("LA") == MAPPER.get_team_slot("LAK")


def test_the_expansion_teams_take_the_all_star_slots():
    assert (MAPPER.get_team_slot("SEA"), MAPPER.get_team_slot("VGK")) == (30, 31)


# -- select_roster ---------------------------------------------------------


def squad(**counts):
    out = []
    pid = 0
    for position, n in counts.items():
        for _ in range(n):
            pid += 1
            out.append(player(pid=pid, name=f"P{pid} S{pid}", position=position))
    return out


FULL = squad(C=6, LW=6, RW=6, D=8, G=3)
LEADERS = {str(p.id): {"PTS": (p.id * 13) % 97, "SV%": 0.88 + (p.id % 40) / 1000} for p in FULL}


def test_a_full_squad_is_cut_to_the_roster_size():
    assert len(MAPPER.select_roster(FULL, LEADERS)) == MAX_PLAYERS


def test_the_roster_size_is_twenty_five():
    assert MAX_PLAYERS == 25


def test_goalies_come_first():
    selected = MAPPER.select_roster(FULL, LEADERS)
    assert [p.position for p in selected[:2]] == ["G", "G"]


def test_only_two_goalies_are_taken_into_the_line_structure():
    assert [p.position for p in MAPPER.select_roster(FULL, LEADERS)[:3]] == ["G", "G", "C"]


def test_a_third_goalie_still_reaches_the_roster_through_the_leftover_fill():
    # 2 goalies + 14 forwards + 7 defencemen is 23 of the 25 slots, and the two
    # that remain are filled from whoever is left by production -- which for a
    # three-goalie squad includes the third goalie. He gets no `G` line flag
    # (see `test_a_third_goalie_gets_no_slot_at_all`) and so is dressed as a
    # scratch. Not obvious from `select_roster`'s docstring, and it is what
    # decides whether a disc's third goalie row is overwritten.
    assert len([p for p in MAPPER.select_roster(FULL, LEADERS) if p.position == "G"]) == 3


def test_the_third_goalie_is_not_among_the_first_two():
    selected = MAPPER.select_roster(FULL, LEADERS)
    assert [i for i, p in enumerate(selected) if p.position == "G"][2] > 2


def test_the_first_line_is_a_centre_a_left_wing_and_a_right_wing():
    selected = MAPPER.select_roster(FULL, LEADERS)
    assert [p.position for p in selected[2:5]] == ["C", "LW", "RW"]


def test_the_best_centre_by_points_is_the_first_line_centre():
    selected = MAPPER.select_roster(FULL, LEADERS)
    centres = sorted(
        [p for p in FULL if p.position == "C"],
        key=lambda p: LEADERS[str(p.id)]["PTS"],
        reverse=True,
    )
    assert selected[2].id == centres[0].id


def test_the_best_goalie_by_save_percentage_starts():
    selected = MAPPER.select_roster(FULL, LEADERS)
    goalies = sorted(
        [p for p in FULL if p.position == "G"],
        key=lambda p: LEADERS[str(p.id)]["SV%"],
        reverse=True,
    )
    assert selected[0].id == goalies[0].id


def test_at_most_seven_defencemen_are_taken():
    assert len([p for p in MAPPER.select_roster(FULL, LEADERS) if p.position == "D"]) == 7


def test_an_empty_squad_selects_nothing():
    assert MAPPER.select_roster([], LEADERS) == []


def test_a_squad_smaller_than_the_roster_is_taken_whole():
    small = squad(C=2, G=1)
    assert len(MAPPER.select_roster(small, {})) == 3


def test_a_max_players_of_zero_selects_nothing():
    assert MAPPER.select_roster(FULL, LEADERS, max_players=0) == []


def test_a_player_with_an_unrecognised_position_can_still_be_selected():
    # The only route by which he reaches a record at all: he is not in any of
    # the five pools and arrives through the leftover fill.
    odd = squad(C=1, G=1, F=1)
    assert len([p for p in MAPPER.select_roster(odd, {}) if p.position == "F"]) == 1


def test_a_player_with_an_unrecognised_position_is_selected_last():
    odd = squad(C=1, G=1, F=1)
    assert MAPPER.select_roster(odd, {})[-1].position == "F"


def test_selection_is_stable_without_stats():
    assert [p.id for p in MAPPER.select_roster(FULL, {})] == [
        p.id for p in MAPPER.select_roster(FULL, {})
    ]


def test_the_selection_is_not_simply_the_input_order():
    # Pins the stability test above: an implementation that returned
    # `players[:25]` would also be stable.
    assert [p.id for p in MAPPER.select_roster(FULL, LEADERS)] != [p.id for p in FULL[:MAX_PLAYERS]]


def test_no_player_is_selected_twice():
    selected = MAPPER.select_roster(FULL, LEADERS)
    assert len({id(p) for p in selected}) == len(selected)


# -- generate_team_line_flags ----------------------------------------------


def records(*positions):
    return [
        NHL07PlayerRecord(position=p, is_goalie=(p == "G"), player_id=i)
        for i, p in enumerate(positions)
    ]


def flags_set(result, index):
    return sorted(name for name, value in result[index].items() if value == 1)


def test_the_first_goalie_gets_the_starting_slot():
    result = MAPPER.generate_team_line_flags(records("G", "G", "C"))
    assert "G1__" in flags_set(result, 0)


def test_the_second_goalie_gets_the_backup_slot():
    result = MAPPER.generate_team_line_flags(records("G", "G", "C"))
    assert flags_set(result, 1) == ["G2__"]


def test_a_third_goalie_gets_no_slot_at_all():
    result = MAPPER.generate_team_line_flags(records("G", "G", "G"))
    assert flags_set(result, 2) == []


def test_four_complete_lines_are_built_from_four_of_each_forward():
    result = MAPPER.generate_team_line_flags(records(*(["C"] * 4 + ["LW"] * 4 + ["RW"] * 4)))
    assigned = [name for r in result for name, v in r.items() if v == 1 and name.startswith("L")]
    assert sorted(assigned) == sorted(
        [f"L{n}{s}" for n in (1, 2, 3, 4) for s in ("C_", "LW", "RW")]
    )


def test_spare_centres_fill_the_wings_when_no_winger_is_left():
    # A team of nine centres still ices four lines, three of them with a centre
    # on each wing.
    result = MAPPER.generate_team_line_flags(records(*(["C"] * 9)))
    assigned = sorted(name for r in result for name, v in r.items() if v == 1 and name[0] == "L")
    assert assigned == ["L1C_", "L1LW", "L1RW", "L2C_", "L2LW", "L2RW", "L3C_", "L3LW", "L3RW"]


def test_a_winger_is_never_moved_to_centre():
    # Only `c_pool` is drawn on twice, so a team with no centres ices four lines
    # with no centre in any of them.
    result = MAPPER.generate_team_line_flags(records(*(["LW"] * 4 + ["RW"] * 4)))
    assert [name for r in result for name, v in r.items() if v == 1 and name.endswith("C_")] == []


def test_a_winger_only_team_still_gets_its_wings_assigned():
    result = MAPPER.generate_team_line_flags(records(*(["LW"] * 4 + ["RW"] * 4)))
    assert len([name for r in result for name, v in r.items() if v == 1 and name[0] == "L"]) == 8


def test_three_defence_pairs_alternate_left_and_right():
    result = MAPPER.generate_team_line_flags(records(*(["D"] * 6)))
    assert [flags_set(result, i)[0] for i in range(6)] == [
        "31LD",
        "31RD",
        "32LD",
        "32RD",
        "33LD",
        "33RD",
    ]


def test_a_seventh_defenceman_gets_no_pair():
    result = MAPPER.generate_team_line_flags(records(*(["D"] * 7)))
    assert flags_set(result, 6) == []


def test_the_power_play_is_line_one_and_the_top_pair():
    result = MAPPER.generate_team_line_flags(records("C", "LW", "RW", "D", "D"))
    assert [i for i in range(5) if "H1__" in flags_set(result, i)] == [0]


def test_all_five_power_play_slots_are_filled_by_a_full_team():
    result = MAPPER.generate_team_line_flags(records(*(["C", "LW", "RW"] * 4 + ["D"] * 6)))
    assigned = sorted(name for r in result for name, v in r.items() if v == 1 and name[0] == "H")
    assert assigned == ["H1__", "H2__", "H3__", "H4__", "H5__"]


def test_all_five_penalty_kill_slots_are_filled_by_a_full_team():
    result = MAPPER.generate_team_line_flags(records(*(["C", "LW", "RW"] * 4 + ["D"] * 6)))
    assigned = sorted(name for r in result for name, v in r.items() if v == 1 and name[0] == "S")
    assert assigned == ["S1__", "S2__", "S3__", "S4__", "S5__"]


def test_a_team_short_of_defencemen_gets_a_four_man_power_play():
    # Positional, not padded with a forward: `pp_candidates` is line one's three
    # forwards plus however many of the top pair exist.
    result = MAPPER.generate_team_line_flags(records("C", "LW", "RW", "D"))
    assigned = sorted(name for r in result for name, v in r.items() if v == 1 and name[0] == "H")
    assert assigned == ["H1__", "H2__", "H3__", "H4__"]


def test_a_player_on_no_line_gets_an_empty_flag_dict():
    # Which `rom_writer.roster_values` turns into all thirty flags zeroed -- a
    # dressed scratch, not a player left where the disc had him.
    result = MAPPER.generate_team_line_flags(records(*(["D"] * 8)))
    assert result[7] == {}


def test_an_empty_team_produces_no_flags():
    assert MAPPER.generate_team_line_flags([]) == []


def test_one_flag_dict_is_produced_per_player():
    players = records(*(["C"] * 3 + ["G"] * 2 + ["D"] * 4))
    assert len(MAPPER.generate_team_line_flags(players)) == len(players)


def test_two_players_never_share_one_flag_dict():
    result = MAPPER.generate_team_line_flags(records("C", "C"))
    assert result[0] is not result[1]


# -- holes mutation testing found ------------------------------------------


def test_the_selected_roster_is_two_goalies_then_fourteen_forwards_then_defence():
    # Kills `forwards[:FORWARDS_PER_TEAM]` -> `[:13]` and
    # `defensemen[:DEFENCEMEN_PER_TEAM]` -> `[:6]`. Counting positions across
    # the whole 25 does not see either: the leftover fill puts the dropped
    # player straight back. The block *boundaries* are what move.
    selected = MAPPER.select_roster(FULL, LEADERS)
    assert [p.position for p in selected[2:16]] == ["C", "LW", "RW"] * 4 + ["C", "LW"]


def test_the_defence_block_starts_at_the_seventeenth_slot():
    selected = MAPPER.select_roster(FULL, LEADERS)
    assert [p.position for p in selected[16:23]] == ["D"] * 7


def test_the_four_forward_lines_are_built_before_the_spare_forwards():
    # Kills `for i in range(FORWARD_LINES)` -> `range(3)` inside `select_roster`.
    # Three lines still yield fourteen forwards -- the fourth line's three come
    # back through `extras` -- but in a different order, and the order is what
    # `generate_team_line_flags` and `patch` both read afterwards.
    selected = MAPPER.select_roster(FULL, LEADERS)
    ranked = {
        code: sorted(
            [p for p in FULL if p.position == code],
            key=lambda p: LEADERS[str(p.id)]["PTS"],
            reverse=True,
        )
        for code in ("C", "LW", "RW")
    }
    expected = [ranked[code][line].id for line in range(4) for code in ("C", "LW", "RW")]
    assert [p.id for p in selected[2:14]] == expected


def test_the_penalty_kill_takes_line_two_and_not_line_one():
    # Kills `pk_candidates = fwd_line_indices[3:6]` -> `[2:6]`. The earlier test
    # asserted only that S1 through S5 were assigned to somebody, which holds
    # for any five players.
    result = MAPPER.generate_team_line_flags(records(*(["C", "LW", "RW"] * 4 + ["D"] * 6)))
    assert [i for i in range(18) if "S1__" in flags_set(result, i)] == [3]


def test_the_penalty_kill_ends_with_the_second_defence_pair():
    result = MAPPER.generate_team_line_flags(records(*(["C", "LW", "RW"] * 4 + ["D"] * 6)))
    assert [i for i in range(18) if "S5__" in flags_set(result, i)] == [15]


def test_the_power_play_starts_with_the_first_line_centre():
    result = MAPPER.generate_team_line_flags(records(*(["C", "LW", "RW"] * 4 + ["D"] * 6)))
    assert [i for i in range(18) if "H1__" in flags_set(result, i)] == [0]


def test_get_team_slot_folds_the_case_of_the_abbreviation():
    # Kills `MODERN_NHL_TO_NHL07.get(team_abbrev.upper())` -> no `.upper()`.
    # The lowercase test above goes through `map_player`, which folds case in a
    # different expression.
    assert MAPPER.get_team_slot("edm") == MODERN_NHL_TO_NHL07["EDM"]


def test_the_selection_size_is_twenty_five_players():
    # Kills `MAX_PLAYERS = 25` -> 24; the cap test above compares the count
    # against the constant, which is the constant checked against itself.
    assert MAX_PLAYERS == 25


#: A squad whose fourth-ranked centre outranks every remaining winger, and
#: whose fifth and sixth centres outrank them too. That is what separates
#: "build four lines, then fill from what is left" from "build three lines,
#: then fill from what is left": with three lines the fill takes C4 and C5,
#: with four it takes the fourth-ranked winger of each side first.
_TAILORED_POINTS = {
    "C": [90, 80, 70, 60, 59, 58],
    "LW": [89, 79, 69, 57, 56, 55],
    "RW": [88, 78, 68, 54, 53, 52],
}


def _tailored_squad():
    players, leaders = [], {}
    pid = 0
    for code, points in _TAILORED_POINTS.items():
        for value in points:
            pid += 1
            players.append(player(pid=pid, name=f"F{pid} L{pid}", position=code))
            leaders[str(pid)] = {"PTS": value}
    return players, leaders


def test_the_fourth_forward_line_is_built_before_any_spare_forward():
    # Kills `for i in range(FORWARD_LINES)` -> `range(3)` inside
    # `select_roster`. Three lines still yield fourteen forwards, so no count
    # sees it; the order does. Slots 11-13 are line four under the real code and
    # the three highest-scoring leftovers under the mutant, and the points above
    # are chosen so those two answers differ.
    players, leaders = _tailored_squad()
    selected = MAPPER.select_roster(players, leaders, max_players=25)
    assert [leaders[str(p.id)]["PTS"] for p in selected[9:12]] == [60, 57, 54]


def test_the_three_highest_leftovers_would_be_three_centres():
    # Pins the test above as discriminating: if the leftovers happened to be
    # line four, the mutant would produce the same list and the assertion would
    # hold for both.
    assert sorted(_TAILORED_POINTS["C"][3:], reverse=True)[:3] == [60, 59, 58]
