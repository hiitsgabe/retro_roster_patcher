"""`NHL05StatMapper`: provider data in, NHL 2005 attribute records out.

The arithmetic here is the same as `games/nhl07_psp/stat_mapper.py`'s -- measured
by AST comparison, the two source files are identical after renaming and after
`[:19] -> [:15]` -- so this file concentrates on the two places the sameness is
a trap:

  * **the 15-character name limit**, which is NHL 07's 19 minus four, and
  * **`get_team_slot`'s 24/25 swap**, where `SJ` and `STL` change places.

Everything else is covered because a mapper that has been transcribed rather
than shared can drift, and there is no reference to compare it against once the
source is gone.

No test here reads a rating from the module and asserts the module produced it.
Every expected value is either arithmetic written out longhand or a number this
file states, so a formula that returned a constant fails.
"""

from __future__ import annotations

import collections

import pytest

from retro_roster_patcher.games.nhl05_ps2.models import (
    MODERN_NHL_TO_NHL05,
    NAME_FIELD_CHARS,
    NHL05_TEAM_INDEX,
    NHL05PlayerRecord,
)
from retro_roster_patcher.games.nhl05_ps2.stat_mapper import (
    ATTR_MAX,
    ATTR_MIN,
    DEFENCE_PAIRS,
    FORWARD_LINES,
    GOALIE_DEFAULTS,
    GOALIES_PER_TEAM,
    MAX_PLAYERS,
    SKATER_DEFAULTS,
    SPECIAL_TEAMS_UNIT,
    NHL05StatMapper,
    _clamp,
    _defaults_for,
    _scale,
    _stat,
)
from retro_roster_patcher.sports.models import Player

MAPPER = NHL05StatMapper()


def player(**kw) -> Player:
    base: dict = {
        "id": 1,
        "name": "Joe Sakic",
        "position": "C",
        "number": 19,
        "age": 30,
        "photo_url": "",
        "nationality": "",
        "handedness": "R",
        "weight": 195,
    }
    base.update(kw)
    return Player(**base)


# -- helpers ---------------------------------------------------------------


def test_the_attribute_range_is_six_bits():
    assert (ATTR_MIN, ATTR_MAX) == (0, 63)


def test_clamping_leaves_a_value_inside_the_range_alone():
    assert _clamp(31) == 31


def test_clamping_raises_a_value_below_the_floor():
    assert _clamp(-7) == 0


def test_clamping_lowers_a_value_above_the_ceiling():
    assert _clamp(200) == 63


def test_scaling_the_bottom_of_a_window_gives_zero():
    assert _scale(10, 10, 20) == 0


def test_scaling_the_top_of_a_window_gives_the_ceiling():
    assert _scale(20, 10, 20) == 63


def test_scaling_the_middle_of_a_window_rounds_to_the_midpoint():
    # 0.5 * 63 = 31.5, and Python rounds half to even, so 32.
    assert _scale(15, 10, 20) == 32


def test_scaling_below_a_window_clamps_to_zero():
    assert _scale(-100, 10, 20) == 0


def test_scaling_an_empty_window_answers_the_midpoint_rather_than_dividing():
    assert _scale(5, 10, 10) == 32


def test_scaling_an_inverted_window_answers_the_midpoint():
    assert _scale(5, 20, 10) == 32


def test_a_stat_is_read_as_a_float():
    assert _stat({"G": "12"}, "G") == 12.0


def test_a_stat_falls_through_to_the_second_name():
    assert _stat({"Shots": 40}, "SOG", "Shots") == 40.0


def test_a_stat_reported_as_zero_falls_through_to_the_default():
    # `or` and not `in`, matching the source: a genuine zero is indistinguishable
    # from an absent stat, which is the intended reading for every caller here.
    assert _stat({"G": 0}, "G", default=9.0) == 9.0


def test_a_stat_reported_as_none_falls_through():
    assert _stat({"GAA": None}, "GAA", default=3.0) == 3.0


def test_an_absent_stat_gives_the_default():
    assert _stat({}, "PTS") == 0.0


# -- position defaults -----------------------------------------------------


def test_every_position_has_defaults():
    assert sorted(SKATER_DEFAULTS) == ["C", "D", "LW", "RW"]


def test_a_centre_takes_the_faceoff_specialists_rating():
    assert SKATER_DEFAULTS["C"].faceoffs == 40


def test_a_defenceman_takes_the_lowest_faceoff_rating():
    assert SKATER_DEFAULTS["D"].faceoffs == 15


def test_the_two_wings_have_identical_defaults():
    assert SKATER_DEFAULTS["LW"] == SKATER_DEFAULTS["RW"]


def test_a_centre_and_a_winger_do_not():
    # Which is what makes the fallback to centre a choice rather than a shrug.
    assert SKATER_DEFAULTS["C"] != SKATER_DEFAULTS["LW"]


def test_an_unknown_position_falls_back_to_centre():
    assert _defaults_for("Rover") == SKATER_DEFAULTS["C"]


def test_reading_defaults_never_hands_out_the_shared_object():
    # A dataclass field defaulting to a mutable instance is one object for every
    # record ever built, and two migrated games shipped that bug.
    assert _defaults_for("C") is not SKATER_DEFAULTS["C"]


def test_two_reads_of_one_positions_defaults_are_separate_objects():
    assert _defaults_for("D") is not _defaults_for("D")


def test_mutating_one_players_defaults_leaves_the_table_alone():
    fresh = _defaults_for("C")
    fresh.faceoffs = 1
    assert SKATER_DEFAULTS["C"].faceoffs == 40


def test_a_goalie_without_stats_gets_a_copy_of_the_goalie_defaults():
    record = MAPPER.map_player(player(position="G"), "COL")
    assert record.goalie_attrs is not GOALIE_DEFAULTS


def test_a_goalie_without_stats_gets_the_goalie_defaults_values():
    record = MAPPER.map_player(player(position="G"), "COL")
    assert record.goalie_attrs == GOALIE_DEFAULTS


# -- map_player: identity fields -------------------------------------------


def test_the_first_name_is_everything_before_the_first_space():
    assert MAPPER.map_player(player(name="Joe Sakic"), "COL").first_name == "Joe"


def test_the_last_name_is_everything_after_the_first_space():
    # Split on the first space only, so a two-word surname stays whole.
    assert (
        MAPPER.map_player(player(name="Ryan Nugent Hopkins"), "EDM").last_name == "Nugent Hopkins"
    )


def test_a_one_word_name_leaves_the_last_name_empty():
    assert MAPPER.map_player(player(name="Pele"), "COL").last_name == ""


def test_an_empty_name_leaves_the_first_name_empty():
    assert MAPPER.map_player(player(name=""), "COL").first_name == ""


def test_the_name_limit_is_fifteen_characters():
    # NHL 07's is 19. This is the only number the two games' mappers differ by.
    assert NAME_FIELD_CHARS == 15


def test_a_fifteen_character_first_name_survives():
    name = "A" * 15
    assert MAPPER.map_player(player(name=f"{name} B"), "COL").first_name == name


def test_a_sixteen_character_first_name_is_cut(tmp_path=None):
    assert MAPPER.map_player(player(name=f"{'A' * 16} B"), "COL").first_name == "A" * 15


def test_a_nineteen_character_last_name_is_cut_to_fifteen():
    # Nineteen is exactly what NHL 07 keeps, so a mapper copied from that game
    # returns all nineteen here.
    assert MAPPER.map_player(player(name="B ABCDEFGHIJKLMNOPQRS"), "COL").last_name == (
        "ABCDEFGHIJKLMNO"
    )


def test_the_jersey_number_comes_from_the_provider():
    assert MAPPER.map_player(player(number=88), "COL").jersey_number == 88


def test_a_player_without_a_number_gets_one():
    assert MAPPER.map_player(player(number=0), "COL").jersey_number == 1


def test_a_left_handed_player_is_encoded_as_zero():
    assert MAPPER.map_player(player(handedness="L"), "COL").handedness == 0


def test_a_right_handed_player_is_encoded_as_one():
    assert MAPPER.map_player(player(handedness="R"), "COL").handedness == 1


def test_a_player_of_unknown_handedness_is_written_right_handed():
    # `HAND` is always written, so this overwrites the disc's own value.
    assert MAPPER.map_player(player(handedness=""), "COL").handedness == 1


def test_the_weight_is_the_providers_pounds():
    assert MAPPER.map_player(player(weight=212), "COL").weight == 212


def test_a_player_without_a_weight_gets_the_league_average():
    assert MAPPER.map_player(player(weight=0), "COL").weight == 190


def test_the_record_carries_no_height_at_all():
    # The dead `HEIG` field. `NHL05PlayerRecord` has no `height`, so the writer
    # has nothing to write and the disc's own value survives.
    assert hasattr(NHL05PlayerRecord(), "height") is False


def test_a_goalie_is_flagged_as_one():
    assert MAPPER.map_player(player(position="G"), "COL").is_goalie is True


def test_a_skater_is_not():
    assert MAPPER.map_player(player(position="D"), "COL").is_goalie is False


def test_a_lower_case_position_is_upper_cased():
    assert MAPPER.map_player(player(position="lw"), "COL").position == "LW"


def test_a_player_with_no_position_becomes_a_centre():
    assert MAPPER.map_player(player(position=""), "COL").position == "C"


def test_a_goalie_gets_goalie_attributes():
    assert MAPPER.map_player(player(position="G"), "COL").skater_attrs is None


def test_a_skater_gets_no_goalie_attributes():
    assert MAPPER.map_player(player(position="C"), "COL").goalie_attrs is None


def test_the_player_id_is_carried_over():
    assert MAPPER.map_player(player(id=8471214), "COL").player_id == 8471214


# -- the team index --------------------------------------------------------


def test_san_jose_takes_slot_twenty_four():
    # **The swap.** NHL 07 puts St. Louis at 24 and San Jose at 25. Copying that
    # table writes the Sharks' roster onto the Blues and back again.
    assert MAPPER.get_team_slot("SJ") == 24


def test_st_louis_takes_slot_twenty_five():
    assert MAPPER.get_team_slot("STL") == 25


def test_the_two_swapped_slots_agree_with_the_index_table():
    assert (NHL05_TEAM_INDEX[24], NHL05_TEAM_INDEX[25]) == ("SJ", "STL")


def test_both_spellings_of_san_jose_reach_the_same_slot():
    assert MAPPER.get_team_slot("SJS") == MAPPER.get_team_slot("SJ")


def test_a_lower_case_code_is_upper_cased():
    assert MAPPER.get_team_slot("stl") == 25


def test_a_code_the_game_does_not_know_has_no_slot():
    assert MAPPER.get_team_slot("ZZZ") is None


def test_the_mapping_table_collapses_thirty_nine_codes_onto_thirty_two_slots():
    # Which is why `map_rosters` has to guard alias collisions. Both games'
    # ported comments said 38; measured, and corrected in both.
    assert (len(MODERN_NHL_TO_NHL05), len(set(MODERN_NHL_TO_NHL05.values()))) == (39, 32)


def test_six_slots_are_named_by_more_than_one_code():
    counts = collections.Counter(MODERN_NHL_TO_NHL05.values())
    assert sorted(slot for slot, n in counts.items() if n > 1) == [1, 13, 17, 22, 24, 26]


def test_the_two_san_jose_spellings_are_one_of_those_collisions():
    assert MODERN_NHL_TO_NHL05["SJ"] == MODERN_NHL_TO_NHL05["SJS"]


def test_the_team_index_reaches_the_record():
    assert MAPPER.map_player(player(), "SJ").team_index == 24


def test_an_unknown_team_code_puts_the_player_on_anaheim():
    # Slot 0, silently. The source's `.get(code, 0)`, kept.
    assert MAPPER.map_player(player(), "ZZZ").team_index == 0


# -- skater derivations ----------------------------------------------------


def skater(**stats):
    return MAPPER.map_player(player(position="C"), "COL", stats).skater_attrs


def test_a_ninety_point_season_saturates_the_offensive_scale():
    # `deking` is `off_rating` unclamped, so it is the scale itself.
    assert skater(PTS=90).deking == 63


def test_half_the_offensive_window_is_the_midpoint():
    assert skater(PTS=45).deking == 32


def test_forty_goals_saturates_the_goal_scale():
    assert skater(G=40).slap_power == 63


def test_wrist_power_is_three_below_the_goal_rating():
    assert skater(G=40).wrist_power == 60


def test_fifty_five_assists_saturates_the_passing_scale():
    assert skater(A=55).passing == 63


def test_plus_minus_is_shifted_by_thirty_before_scaling():
    # -30 is the floor of the window, so `pressure` bottoms out there.
    assert skater(**{"+/-": -30}).pressure == 0


def test_plus_forty_saturates_the_plus_minus_scale():
    assert skater(**{"+/-": 40}).pressure == 63


def test_penalty_minutes_drive_toughness():
    assert skater(PIM=80).toughness == 63


def test_penalty_minutes_also_drive_aggression():
    # One stat, three ratings. Toughness and aggression are the same number.
    assert skater(PIM=40).aggression == skater(PIM=40).toughness


def test_forty_penalty_minutes_is_one_fighting_point():
    assert skater(PIM=40).fighting == 1


def test_fighting_is_capped_at_three_because_the_field_is_two_bits():
    assert skater(PIM=4000).fighting == 3


def test_a_player_with_no_shots_is_credited_with_ten_percent_shooting():
    # `_scale(10, 5, 20)` is one third of 63, rounded: 21.
    assert skater(G=0, SOG=0).shot_accuracy == 21


def test_shooting_percentage_is_goals_over_shots():
    # 20 goals on 100 shots is 20%, the top of the window.
    assert skater(G=20, SOG=100).shot_accuracy == 63


def test_a_fifty_point_season_gets_no_speed_boost():
    base = SKATER_DEFAULTS["C"].speed
    assert skater(PTS=50).speed == base + 3


def test_a_fifty_one_point_season_gets_the_larger_boost():
    base = SKATER_DEFAULTS["C"].speed
    assert skater(PTS=51).speed == base + 5


def test_a_thirty_point_season_gets_no_boost_at_all():
    assert skater(PTS=30).speed == SKATER_DEFAULTS["C"].speed


def test_a_defenceman_gets_three_points_of_balance_over_the_default():
    attrs = MAPPER.map_player(player(position="D"), "COL", {"PTS": 10}).skater_attrs
    assert attrs.balance == SKATER_DEFAULTS["D"].balance + 3


def test_a_centre_does_not():
    assert skater(PTS=10).balance == SKATER_DEFAULTS["C"].balance


def test_a_defencemans_checking_comes_from_his_plus_minus():
    attrs = MAPPER.map_player(player(position="D"), "COL", {"+/-": 40}).skater_attrs
    assert attrs.checking == 63


def test_a_centres_checking_stays_at_the_default():
    assert skater(**{"+/-": 40}).checking == SKATER_DEFAULTS["C"].checking


def test_a_faceoff_percentage_replaces_the_position_default():
    assert skater(**{"FO%": 60}).faceoffs == 63


def test_no_faceoff_percentage_leaves_the_position_default():
    assert skater(PTS=1).faceoffs == SKATER_DEFAULTS["C"].faceoffs


# -- goalie derivations ----------------------------------------------------


def goalie(**stats):
    return MAPPER.map_player(player(position="G"), "COL", stats).goalie_attrs


def test_a_save_percentage_at_the_top_of_the_window_saturates():
    assert goalie(**{"SV%": 0.930}).rebound_ctrl == 63


def test_a_save_percentage_at_the_bottom_bottoms_out():
    assert goalie(**{"SV%": 0.880}).rebound_ctrl == 0


def test_a_percentage_reported_as_a_whole_number_saturates_too():
    # The inherited roughness, pinned: `91.2` and `0.999` are indistinguishable
    # to this scale, and nothing here can tell which convention arrived.
    assert goalie(**{"SV%": 91.2}).rebound_ctrl == 63


def test_shot_recovery_is_three_below_the_save_rating():
    assert goalie(**{"SV%": 0.930}).shot_recovery == 60


def test_a_low_goals_against_average_saturates_the_inverted_scale():
    assert goalie(GAA=2.0, W=0).poke_check == 63


def test_a_high_goals_against_average_bottoms_it_out():
    assert goalie(GAA=3.5, W=0).poke_check == 0


def test_forty_wins_is_the_maximum_bonus():
    assert goalie(W=40).endurance == 45


def test_wins_above_forty_add_nothing_more():
    assert goalie(W=82).endurance == 45


def test_four_wins_is_one_point_of_bonus():
    assert goalie(W=4).endurance == 36


def test_a_goalies_toughness_is_a_constant():
    assert goalie(**{"SV%": 0.930}).toughness == 25


def test_a_goalie_never_fights():
    assert goalie(PIM=400).fighting == 0


def test_the_two_high_save_zones_differ_by_two():
    attrs = goalie(**{"SV%": 0.930})
    assert attrs.glove_high - attrs.stick_high == 2


def test_the_two_low_save_zones_differ_by_two():
    attrs = goalie(**{"SV%": 0.930})
    assert attrs.glove_low - attrs.stick_low == 2


# -- select_roster ---------------------------------------------------------


def squad(counts: dict[str, int]) -> list[Player]:
    """One player per requested position, ids running from 1."""
    out: list[Player] = []
    for pos, n in counts.items():
        for i in range(n):
            out.append(player(id=len(out) + 1, name=f"{pos}{i} Last", position=pos))
    return out


def test_a_full_roster_is_capped_at_the_maximum():
    players = squad({"C": 8, "LW": 8, "RW": 8, "D": 10, "G": 4})
    assert len(MAPPER.select_roster(players, {})) == MAX_PLAYERS


def test_the_default_maximum_is_twenty_five():
    assert MAX_PLAYERS == 25


def test_goalies_come_first():
    players = squad({"C": 5, "G": 3})
    selected = MAPPER.select_roster(players, {})
    assert [p.position for p in selected[:GOALIES_PER_TEAM]] == ["G", "G"]


def test_only_two_goalies_reach_the_goalie_slots():
    # The other four are not dropped: they fall through to the leftover fill at
    # the end, behind every forward and defenceman. So the count of goalies in
    # the result is not 2, and asserting that it were would be asserting the
    # wrong thing about a real behaviour.
    players = squad({"C": 5, "G": 6})
    selected = MAPPER.select_roster(players, {})
    assert [p.position for p in selected[:7]] == ["G", "G", "C", "C", "C", "C", "C"]


def test_the_surplus_goalies_are_appended_as_leftovers():
    players = squad({"C": 5, "G": 6})
    selected = MAPPER.select_roster(players, {})
    assert [p.position for p in selected[7:]] == ["G", "G", "G", "G"]


def test_the_forward_lines_alternate_centre_wing_wing():
    players = squad({"C": 4, "LW": 4, "RW": 4})
    selected = MAPPER.select_roster(players, {})
    assert [p.position for p in selected[:6]] == ["C", "LW", "RW", "C", "LW", "RW"]


def test_defence_comes_after_every_forward():
    players = squad({"C": 4, "LW": 4, "RW": 4, "D": 7, "G": 2})
    selected = MAPPER.select_roster(players, {})
    assert [p.position for p in selected[-7:]] == ["D"] * 7


def test_at_most_seven_defencemen_are_taken_before_the_leftovers():
    players = squad({"D": 12, "G": 2})
    selected = MAPPER.select_roster(players, {}, max_players=9)
    assert len(selected) == 9


def test_the_best_scorer_gets_the_first_line():
    players = squad({"C": 3})
    stats = {"1": {"PTS": 10}, "2": {"PTS": 90}, "3": {"PTS": 50}}
    assert MAPPER.select_roster(players, stats)[0].id == 2


def test_the_best_save_percentage_gets_the_starting_job():
    players = squad({"G": 3})
    stats = {"1": {"SV%": 0.900}, "2": {"SV%": 0.880}, "3": {"SV%": 0.930}}
    assert MAPPER.select_roster(players, stats)[0].id == 3


def test_a_player_with_an_unknown_position_is_kept_as_a_leftover():
    players = squad({"C": 1, "Rover": 1})
    assert len(MAPPER.select_roster(players, {})) == 2


def test_an_unknown_position_sorts_after_the_recognised_ones():
    players = squad({"C": 1, "Rover": 1})
    assert MAPPER.select_roster(players, {})[-1].position == "Rover"


def test_an_empty_squad_selects_nothing():
    assert MAPPER.select_roster([], {}) == []


def test_a_maximum_of_zero_selects_nothing():
    assert MAPPER.select_roster(squad({"C": 5}), {}, max_players=0) == []


def test_no_player_is_selected_twice():
    players = squad({"C": 8, "LW": 8, "RW": 8, "D": 10, "G": 4})
    selected = MAPPER.select_roster(players, {})
    assert len({id(p) for p in selected}) == len(selected)


# -- generate_team_line_flags ----------------------------------------------


def records(counts: dict[str, int]) -> list[NHL05PlayerRecord]:
    out: list[NHL05PlayerRecord] = []
    for pos, n in counts.items():
        for _ in range(n):
            out.append(NHL05PlayerRecord(position=pos, is_goalie=pos == "G"))
    return out


def test_one_flag_dict_per_player():
    assert len(MAPPER.generate_team_line_flags(records({"C": 3}))) == 3


def test_the_first_goalie_starts():
    flags = MAPPER.generate_team_line_flags(records({"G": 2}))
    assert flags[0] == {"G1__": 1}


def test_the_second_goalie_backs_up():
    flags = MAPPER.generate_team_line_flags(records({"G": 2}))
    assert flags[1] == {"G2__": 1}


def test_a_third_goalie_gets_nothing():
    flags = MAPPER.generate_team_line_flags(records({"G": 3}))
    assert flags[2] == {}


def test_only_two_goalies_dress():
    assert GOALIES_PER_TEAM == 2


def test_four_forward_lines_are_built():
    assert FORWARD_LINES == 4


def test_the_first_centre_takes_the_first_line():
    flags = MAPPER.generate_team_line_flags(records({"C": 4}))
    assert flags[0]["L1C_"] == 1


def test_the_fourth_centre_takes_the_fourth_line():
    # Four of each: with only centres, the spare ones are moved to the wings
    # and the fourth line never gets a centre at all.
    team = records({"C": 4, "LW": 4, "RW": 4})
    flags = MAPPER.generate_team_line_flags(team)
    assert flags[3]["L4C_"] == 1


def test_a_team_of_only_centres_ices_fewer_lines_than_it_has_players():
    # The consequence of the same rule, stated so the test above cannot be read
    # as "the fourth centre always centres the fourth line".
    flags = MAPPER.generate_team_line_flags(records({"C": 4}))
    assert [f for d in flags for f in d if f.endswith("C_")] == ["L1C_", "L2C_"]


def test_a_spare_centre_is_moved_to_the_wing_when_the_wingers_run_out():
    flags = MAPPER.generate_team_line_flags(records({"C": 3}))
    assert flags[1]["L1LW"] == 1


def test_a_winger_is_never_moved_to_centre():
    # Only `c_pool` is drawn on twice, so a team with no centres ices four lines
    # with no centre at all.
    flags = MAPPER.generate_team_line_flags(records({"LW": 4}))
    assert [f for d in flags for f in d if f.endswith("C_")] == []


def test_the_first_defence_pair_is_the_first_even_strength_pair():
    # DELIBERATE DIVERGENCE, at the layer that produces it. The source emitted
    # `31LD`/`31RD` here -- NHL 07's spelling, copied into a game whose `3n`
    # family is a five-on-three unit. `L1LD`/`L1RD` is the `L` family, the same
    # one this function already puts line one's forwards on.
    flags = MAPPER.generate_team_line_flags(records({"D": 2}))
    assert [flags[0].get("L1LD"), flags[1].get("L1RD")] == [1, 1]


def test_the_second_defence_pair_follows():
    flags = MAPPER.generate_team_line_flags(records({"D": 4}))
    assert [flags[2].get("L2LD"), flags[3].get("L2RD")] == [1, 1]


def test_the_third_defence_pair_is_emitted_under_a_name_the_game_has():
    # The pair the source lost. It emitted `33LD`/`33RD`, which NHL 2005's ROST
    # does not name, so `rom_writer.roster_values` dropped the key and no team
    # ever iced a third pair. `L3LD`/`L3RD` the game does have.
    flags = MAPPER.generate_team_line_flags(records({"D": 6}))
    assert [flags[4].get("L3LD"), flags[5].get("L3RD")] == [1, 1]


def test_no_defence_flag_uses_the_sources_numbered_spelling():
    # The assertion that fails if a port audit "restores" `3{pair}{side}`.
    # Stated over every dict the call returns rather than over the two indices
    # the tests above name, so a half-reverted loop fails here too.
    flags = MAPPER.generate_team_line_flags(records({"D": 6}))
    assert [f for d in flags for f in d if f[0] == "3"] == []


def test_a_defence_pair_carries_the_same_prefix_as_its_lines_centre():
    # The reason this divergence is safe without a real disc: a line's centre
    # and its defencemen have to name the same situation, and this one function
    # picks the prefix for both. `L1C_` and `L1LD` agree; `L1C_` and `31LD` did
    # not, whatever `3n` turns out to denote.
    flags = MAPPER.generate_team_line_flags(records({"C": 1, "D": 1}))
    assert sorted({f[:2] for d in flags for f in d if not f.endswith("__")}) == ["L1"]


def test_three_defence_pairs_are_generated():
    assert DEFENCE_PAIRS == 3


def test_a_seventh_defenceman_gets_no_pair():
    flags = MAPPER.generate_team_line_flags(records({"D": 7}))
    assert [f for f in flags[6] if f.endswith(("LD", "RD"))] == []


def test_the_power_play_takes_line_one_and_the_top_pair():
    flags = MAPPER.generate_team_line_flags(records({"C": 1, "LW": 1, "RW": 1, "D": 2}))
    assert [i for i, d in enumerate(flags) if any(f.startswith("H") for f in d)] == [0, 1, 2, 3, 4]


def test_the_power_play_is_five_players():
    assert SPECIAL_TEAMS_UNIT == 5


def test_a_team_short_of_defencemen_ices_a_four_man_power_play():
    flags = MAPPER.generate_team_line_flags(records({"C": 1, "LW": 1, "RW": 1, "D": 1}))
    assert sum(1 for d in flags for f in d if f.startswith("H")) == 4


def test_the_penalty_kill_takes_line_two_and_the_next_pair():
    flags = MAPPER.generate_team_line_flags(records({"C": 2, "LW": 2, "RW": 2, "D": 4}))
    assert sum(1 for d in flags for f in d if f.startswith("S")) == 5


def test_a_player_on_no_unit_gets_an_empty_dict():
    # Which `roster_values` turns into all sixty-four flags zeroed: a dressed
    # scratch, not a player left on the line the disc had him on.
    flags = MAPPER.generate_team_line_flags(records({"C": 4, "D": 8}))
    assert flags[-1] == {}


def test_an_empty_team_produces_no_flag_dicts():
    assert MAPPER.generate_team_line_flags([]) == []


@pytest.mark.parametrize("count", [0, 1, 5, 12, 25, 40])
def test_every_team_size_produces_one_dict_per_player(count):
    team = records({"C": count})
    assert len(MAPPER.generate_team_line_flags(team)) == count
