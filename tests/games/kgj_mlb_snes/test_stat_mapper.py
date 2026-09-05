"""The ported KGJ MLB stat mapper.

The scale is 1-10 and the ROM stores it as `value - 1` in a nibble, so every
rating below is a small integer with only ten legal values. That makes it easy
for an assertion to be true by accident: a test that only ever checks 5 passes
against a mapper that returns 5 for everything. Every rating pinned here is
therefore pinned twice, at the top of its range and at the bottom, from inputs
that differ in the one statistic the rating is supposed to read.

Two divergences from upstream live here and are pinned as such:

  * `BATTER_DEFAULTS` and `PITCHER_DEFAULTS` are copied with
    `dataclasses.replace` rather than handed out. Upstream shared one mutable
    object between every stat-less player at a position *and* the table itself.
  * `_is_pitcher` is `is_pitcher`. `patcher.map_rosters` calls it.
"""

import pytest

from retro_roster_patcher.games.kgj_mlb_snes.models import (
    BATTERS_PER_TEAM,
    HAND_LEFT,
    HAND_RIGHT,
    HAND_SWITCH,
    PLAYERS_PER_TEAM,
    RELIEVERS_PER_TEAM,
    STARTERS_PER_TEAM,
)
from retro_roster_patcher.games.kgj_mlb_snes.stat_mapper import (
    BATTER_DEFAULTS,
    PITCHER_DEFAULTS,
    KGJStatMapper,
    _clamp,
    _scale,
)
from retro_roster_patcher.sports.models import Player

STAR_BATTER = {
    "AVG": 0.330,
    "HR": 45,
    "RBI": 130,
    "SB": 50,
    "OPS": 1.0,
    "H": 200,
    "SLG": 0.7,
    "3B": 6,
    "GP": 150,
}
WEAK_BATTER = {
    "AVG": 0.200,
    "HR": 0,
    "RBI": 0,
    "SB": 0,
    "OPS": 0.6,
    "H": 10,
    "SLG": 0.3,
    "3B": 0,
    "GP": 10,
}
ACE = {"ERA": 1.9, "K": 250, "WHIP": 0.9, "W": 20, "QS": 30, "SV": 0}
BATTING_PRACTICE = {"ERA": 7.0, "K": 0, "WHIP": 1.9, "W": 0, "QS": 0, "SV": 0}


@pytest.fixture
def mapper():
    return KGJStatMapper()


def _player(**overrides):
    base = dict(id=1, name="Ken Griffey Jr.", position="CF", number=24)
    base.update(overrides)
    return Player(**base)


# -- the scale ---------------------------------------------------------------


def test_clamp_holds_the_scale_floor_at_one():
    assert _clamp(-4) == 1


def test_clamp_holds_the_scale_ceiling_at_ten():
    assert _clamp(40) == 10


def test_the_bottom_of_a_range_scales_to_one():
    assert _scale(0.0, 0.0, 1.0) == 1


def test_the_top_of_a_range_scales_to_ten():
    assert _scale(1.0, 0.0, 1.0) == 10


def test_the_middle_of_a_range_scales_to_five():
    # round(0.5 * 9) + 1. Python rounds 4.5 to 4, so this is 5 and not 6, and a
    # `math.ceil` would give 6.
    assert _scale(0.5, 0.0, 1.0) == 5


def test_a_value_below_the_range_still_scales_to_one():
    assert _scale(-3.0, 0.0, 1.0) == 1


def test_a_value_above_the_range_still_scales_to_ten():
    assert _scale(4.0, 0.0, 1.0) == 10


def test_a_range_with_no_width_scales_to_the_midpoint():
    assert _scale(5.0, 10.0, 10.0) == 5


def test_an_inverted_range_scales_to_the_midpoint():
    assert _scale(5.0, 10.0, 2.0) == 5


# -- batter ratings from stats -----------------------------------------------


@pytest.mark.parametrize(
    "field,star,weak",
    [("batting", 10, 1), ("power", 9, 2), ("speed", 10, 1)],
)
def test_a_batters_ratings_span_the_scale(mapper, field, star, weak):
    best = getattr(mapper._map_batter_stats(STAR_BATTER, "CF"), field)
    worst = getattr(mapper._map_batter_stats(WEAK_BATTER, "CF"), field)
    assert (best, worst) == (star, weak)


def test_defense_comes_from_the_position_default_and_not_from_the_stats(mapper):
    # Nothing in the leaders endpoint measures fielding, so this is the position
    # table plus a games-played bonus. A shortstop rates 8 and a DH rates 2 on
    # identical stat lines.
    short_stop = mapper._map_batter_stats(WEAK_BATTER, "SS").defense
    designated = mapper._map_batter_stats(WEAK_BATTER, "DH").defense
    assert (short_stop, designated) == (8, 2)


def test_more_than_120_games_adds_one_to_defense(mapper):
    assert mapper._map_batter_stats({"GP": 121}, "C").defense == 8


def test_exactly_120_games_does_not(mapper):
    assert mapper._map_batter_stats({"GP": 120}, "C").defense == 7


def test_five_triples_add_one_to_speed(mapper):
    assert mapper._map_batter_stats({"SB": 20, "3B": 5}, "CF").speed == 6


def test_four_triples_do_not(mapper):
    assert mapper._map_batter_stats({"SB": 20, "3B": 4}, "CF").speed == 5


def test_an_unknown_position_falls_back_to_centre_fields_defense(mapper):
    assert mapper._map_batter_stats(WEAK_BATTER, "ZZ").defense == BATTER_DEFAULTS["CF"].defense


def test_a_stat_that_is_present_but_none_falls_back_to_its_default(mapper):
    # ESPN sends nulls. `float(stats.get("SB", 0) or 0)` is what absorbs them,
    # and a `stats.get("SB", 0)` without the `or` would raise here.
    assert mapper._map_batter_stats({"SB": None, "3B": None}, "CF").speed == 1


# -- pitcher ratings from stats ----------------------------------------------


def test_a_pitchers_ratings_span_the_scale(mapper):
    best = mapper._map_pitcher_stats(ACE, True)
    worst = mapper._map_pitcher_stats(BATTING_PRACTICE, True)
    assert (best.speed, worst.speed) == (10, 1)


def test_a_starter_and_a_reliever_read_strikeouts_on_different_ranges(mapper):
    # 60-250 for a starter, 20-90 for a reliever. 155 strikeouts is the middle
    # of the first range and off the top of the second.
    starter = mapper._map_pitcher_stats({"K": 155}, True).speed
    reliever = mapper._map_pitcher_stats({"K": 155}, False).speed
    assert (starter, reliever) == (5, 10)


def test_control_averages_the_whip_and_era_readings(mapper):
    assert mapper._map_pitcher_stats(ACE, True).control == 10


def test_control_bottoms_out_on_a_bad_line(mapper):
    assert mapper._map_pitcher_stats(BATTING_PRACTICE, True).control == 1


def test_a_starters_fatigue_comes_from_quality_starts(mapper):
    assert mapper._map_pitcher_stats({"QS": 15}, True).fatigue == 5


def test_fifteen_wins_add_one_to_a_starters_fatigue(mapper):
    assert mapper._map_pitcher_stats({"QS": 15, "W": 15}, True).fatigue == 6


def test_fourteen_wins_do_not(mapper):
    assert mapper._map_pitcher_stats({"QS": 15, "W": 14}, True).fatigue == 5


def test_a_relievers_fatigue_ignores_quality_starts(mapper):
    assert mapper._map_pitcher_stats({"QS": 30}, False).fatigue == 3


def test_more_than_twenty_saves_add_one_to_a_relievers_fatigue(mapper):
    assert mapper._map_pitcher_stats({"SV": 21}, False).fatigue == 4


def test_exactly_twenty_saves_do_not(mapper):
    assert mapper._map_pitcher_stats({"SV": 20}, False).fatigue == 3


# -- the shared-mutable-default divergence -----------------------------------


def test_a_stat_less_batter_does_not_receive_the_defaults_table_itself(mapper):
    record = mapper.map_batter(_player(position="SS"))
    assert record.batter_attrs is not BATTER_DEFAULTS["SS"]


def test_editing_a_stat_less_batters_ratings_leaves_the_table_alone(mapper):
    record = mapper.map_batter(_player(position="SS"))
    record.batter_attrs.power = 1
    assert BATTER_DEFAULTS["SS"].power == 3


def test_two_stat_less_batters_at_one_position_get_separate_objects(mapper):
    first = mapper.map_batter(_player(position="SS"))
    second = mapper.map_batter(_player(position="SS"))
    first.batter_attrs.speed = 1
    assert second.batter_attrs.speed == 6


def test_a_stat_less_pitcher_does_not_receive_the_defaults_table_itself(mapper):
    record = mapper.map_pitcher(_player(position="SP"), is_starter=True)
    assert record.pitcher_attrs is not PITCHER_DEFAULTS["SP"]


def test_editing_a_stat_less_pitchers_ratings_leaves_the_table_alone(mapper):
    record = mapper.map_pitcher(_player(position="SP"), is_starter=True)
    record.pitcher_attrs.fatigue = 1
    assert PITCHER_DEFAULTS["SP"].fatigue == 7


def test_a_stat_less_starter_and_reliever_take_different_defaults(mapper):
    starter = mapper.map_pitcher(_player(), is_starter=True)
    reliever = mapper.map_pitcher(_player(), is_starter=False)
    assert (starter.pitcher_attrs.fatigue, reliever.pitcher_attrs.fatigue) == (7, 3)


def test_the_closer_default_row_is_never_selected(mapper):
    # `default_key` is only ever "SP" or "RP". The "CL" row exists and nothing
    # reaches it; this pins that rather than leaving it to be discovered.
    assert PITCHER_DEFAULTS["CL"].speed == 7


# -- map_batter --------------------------------------------------------------


def test_a_mapped_batter_is_not_flagged_as_a_pitcher(mapper):
    assert mapper.map_batter(_player()).is_pitcher is False


def test_a_mapped_batter_keeps_its_normalised_position(mapper):
    assert mapper.map_batter(_player(position="1B")).position == "1B"


def test_a_batter_with_no_number_is_written_as_zero(mapper):
    assert mapper.map_batter(_player(number=None)).jersey_number == 0


def test_a_batter_with_stats_takes_the_batting_average_from_them(mapper):
    assert mapper.map_batter(_player(), STAR_BATTER).batting_avg == 330


def test_a_batter_with_no_stats_takes_the_placeholder_average(mapper):
    assert mapper.map_batter(_player()).batting_avg == 250


def test_a_batter_with_stats_takes_the_home_run_total_from_them(mapper):
    assert mapper.map_batter(_player(), STAR_BATTER).home_runs == 45


def test_a_batter_with_stats_takes_the_rbi_total_from_them(mapper):
    assert mapper.map_batter(_player(), STAR_BATTER).rbi == 130


def test_a_batter_with_no_stats_has_no_home_runs(mapper):
    assert mapper.map_batter(_player()).home_runs == 0


# -- map_pitcher -------------------------------------------------------------


def test_a_mapped_pitcher_is_flagged_as_one(mapper):
    assert mapper.map_pitcher(_player(position="SP")).is_pitcher is True


def test_a_mapped_pitchers_position_is_always_p(mapper):
    assert mapper.map_pitcher(_player(position="RP")).position == "P"


def test_a_left_handed_thrower_gets_pitch_hand_one(mapper):
    assert mapper.map_pitcher(_player(handedness="L")).pitch_hand == 1


def test_a_right_handed_thrower_gets_pitch_hand_zero(mapper):
    assert mapper.map_pitcher(_player(handedness="R")).pitch_hand == 0


def test_a_thrower_of_unknown_hand_gets_pitch_hand_zero(mapper):
    assert mapper.map_pitcher(_player(handedness="")).pitch_hand == 0


def test_a_pitcher_with_stats_takes_the_era_from_them(mapper):
    # 1.90 becomes 190: hundredths, not a float.
    assert mapper.map_pitcher(_player(), ACE).era == 190


def test_a_pitcher_with_no_stats_takes_the_placeholder_era(mapper):
    assert mapper.map_pitcher(_player()).era == 400


def test_a_pitcher_with_stats_takes_the_win_total_from_them(mapper):
    assert mapper.map_pitcher(_player(), ACE).wins == 20


def test_a_pitcher_with_stats_takes_the_save_total_from_them(mapper):
    assert mapper.map_pitcher(_player(), dict(ACE, SV=41)).saves == 41


# -- handedness --------------------------------------------------------------


def test_a_left_handed_batter_gets_the_left_stance_byte(mapper):
    assert mapper.map_batter(_player(bats="L")).bat_hand == HAND_LEFT


def test_a_switch_hitter_gets_the_switch_stance_byte(mapper):
    assert mapper.map_batter(_player(bats="S")).bat_hand == HAND_SWITCH


def test_espns_other_switch_letter_is_also_a_switch_hitter(mapper):
    assert mapper.map_batter(_player(bats="B")).bat_hand == HAND_SWITCH


def test_a_batter_of_unknown_stance_bats_right(mapper):
    assert mapper.map_batter(_player(bats="")).bat_hand == HAND_RIGHT


def test_the_bats_field_wins_over_the_throwing_hand(mapper):
    # ESPN's baseball roster supplies both; a left-handed thrower who bats right
    # must not be written as a left-handed hitter.
    assert mapper.map_batter(_player(bats="R", handedness="L")).bat_hand == HAND_RIGHT


def test_the_throwing_hand_stands_in_when_the_bats_field_is_empty(mapper):
    assert mapper.map_batter(_player(bats="", handedness="L")).bat_hand == HAND_LEFT


# -- names -------------------------------------------------------------------


def test_a_first_name_is_reduced_to_one_initial(mapper):
    assert mapper._split_name("Ken Griffey") == ("K", "GRIFFEY")


def test_a_generational_suffix_is_skipped(mapper):
    assert mapper._split_name("Ken Griffey Jr.") == ("K", "GRIFFEY")


def test_a_roman_numeral_suffix_is_skipped(mapper):
    assert mapper._split_name("Sammy Sosa III") == ("S", "SOSA")


def test_an_initialled_first_name_keeps_only_its_first_letter(mapper):
    assert mapper._split_name("J.D. Martinez") == ("J", "MARTINEZ")


def test_a_last_name_longer_than_eight_characters_is_cut(mapper):
    assert mapper._split_name("Carl Yastrzemski") == ("C", "YASTRZEM")


def test_a_mc_name_keeps_a_lowercase_c(mapper):
    # The one lowercase letter the ROM font table names exists for this.
    assert mapper._split_name("Mark McGwire") == ("M", "McGWIRE")


def test_a_two_letter_mc_name_is_left_alone(mapper):
    assert mapper._split_name("Bob Mc") == ("B", "MC")


def test_a_one_word_name_becomes_its_own_last_name(mapper):
    assert mapper._split_name("Ichiro") == ("I", "ICHIRO")


def test_an_empty_name_becomes_the_placeholder(mapper):
    assert mapper._split_name("") == ("A", "PLAYER")


def test_a_name_of_only_whitespace_becomes_the_placeholder(mapper):
    assert mapper._split_name("   ") == ("A", "PLAYER")


def test_a_name_of_only_a_suffix_keeps_the_last_word(mapper):
    # Every part after the first is a suffix, so the fallback takes `parts[-1]`.
    assert mapper._split_name("Ken Jr.") == ("K", "JR.")


# -- position normalisation --------------------------------------------------


@pytest.mark.parametrize("position", ["C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH"])
def test_a_lineup_position_passes_through(mapper, position):
    assert mapper._normalize_position(position, is_pitcher=False) == position


@pytest.mark.parametrize("position", ["SP", "RP", "CL", "CP", "P"])
def test_every_pitcher_abbreviation_normalises_to_p(mapper, position):
    assert mapper._normalize_position(position, is_pitcher=False) == "P"


def test_a_lowercase_position_is_upcased_first(mapper):
    assert mapper._normalize_position("ss", is_pitcher=False) == "SS"


def test_an_unknown_position_becomes_an_outfielder(mapper):
    assert mapper._normalize_position("QQ", is_pitcher=False) == "OF"


def test_an_empty_position_becomes_an_outfielder(mapper):
    assert mapper._normalize_position("", is_pitcher=False) == "OF"


def test_the_pitcher_flag_overrides_the_position_string(mapper):
    assert mapper._normalize_position("SS", is_pitcher=True) == "P"


# -- is_pitcher --------------------------------------------------------------


@pytest.mark.parametrize("position", ["P", "SP", "RP", "CL", "CP"])
def test_every_pitching_abbreviation_is_a_pitcher(mapper, position):
    assert mapper.is_pitcher(_player(position=position)) is True


@pytest.mark.parametrize("position", ["C", "1B", "CF", "DH", "OF", ""])
def test_no_lineup_position_is_a_pitcher(mapper, position):
    assert mapper.is_pitcher(_player(position=position)) is False


def test_a_lowercase_pitching_abbreviation_is_still_a_pitcher(mapper):
    assert mapper.is_pitcher(_player(position="sp")) is True


def test_the_pitcher_test_is_public(mapper):
    # DELIBERATE DIVERGENCE: upstream named it `_is_pitcher` and then called it
    # from `patcher.py`, across the module boundary, through the underscore.
    assert hasattr(mapper, "_is_pitcher") is False


# -- team slots --------------------------------------------------------------


def test_a_modern_abbreviation_finds_its_1994_slot(mapper):
    assert mapper.get_team_slot("SEA") == 11


def test_a_relocated_franchise_keeps_the_old_citys_slot(mapper):
    # Montreal Expos became the Washington Nationals.
    assert mapper.get_team_slot("WSH") == 19


def test_the_two_chicago_white_sox_abbreviations_name_one_slot(mapper):
    assert mapper.get_team_slot("CWS") == mapper.get_team_slot("CHW")


def test_the_two_oakland_abbreviations_name_one_slot(mapper):
    assert mapper.get_team_slot("OAK") == mapper.get_team_slot("ATH")


def test_a_lowercase_abbreviation_still_finds_its_slot(mapper):
    assert mapper.get_team_slot("sea") == 11


def test_a_1998_expansion_team_has_no_slot(mapper):
    assert mapper.get_team_slot("ARI") is None


def test_the_other_1998_expansion_team_has_no_slot(mapper):
    assert mapper.get_team_slot("TB") is None


def test_an_abbreviation_from_another_sport_has_no_slot(mapper):
    assert mapper.get_team_slot("ZZZ") is None


# -- roster selection --------------------------------------------------------


def _squad(batters=20, starters=8, relievers=10):
    players = []
    positions = ["C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH"]
    for index in range(batters):
        players.append(
            _player(
                id=index,
                name=f"Bat{index:02d} Last{index:02d}",
                position=positions[index % len(positions)],
            )
        )
    for index in range(starters):
        players.append(
            _player(id=100 + index, name=f"Sta{index:02d} Last{index:02d}", position="SP")
        )
    for index in range(relievers):
        players.append(
            _player(id=200 + index, name=f"Rel{index:02d} Last{index:02d}", position="RP")
        )
    return players


def test_a_full_squad_selects_exactly_the_roster_size(mapper):
    assert len(mapper.select_roster(_squad())) == PLAYERS_PER_TEAM


def test_the_first_fifteen_selected_are_batters(mapper):
    selected = mapper.select_roster(_squad())
    kinds = [mapper.is_pitcher(p) for p in selected[:BATTERS_PER_TEAM]]
    assert kinds == [False] * BATTERS_PER_TEAM


def test_the_next_five_selected_are_the_starting_pitchers(mapper):
    selected = mapper.select_roster(_squad())
    names = [p.name[:3] for p in selected[BATTERS_PER_TEAM : BATTERS_PER_TEAM + STARTERS_PER_TEAM]]
    assert names == ["Sta"] * STARTERS_PER_TEAM


def test_the_last_five_selected_are_the_relievers(mapper):
    selected = mapper.select_roster(_squad())
    names = [p.name[:3] for p in selected[BATTERS_PER_TEAM + STARTERS_PER_TEAM :]]
    assert names == ["Rel"] * RELIEVERS_PER_TEAM


def test_the_lineup_positions_are_filled_before_the_bench(mapper):
    # C, 1B, 2B, 3B, SS, LF, CF, RF, DH in that order, one apiece, then the best
    # remaining bats.
    selected = mapper.select_roster(_squad())
    order = [p.position for p in selected[:9]]
    assert order == ["C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH"]


def test_a_squad_with_too_few_batters_returns_a_short_roster(mapper):
    # The three groups are maxima, not guarantees, and this is what makes slot
    # index and player kind disagree further down the list.
    selected = mapper.select_roster(_squad(batters=6))
    assert len(selected) == 6 + STARTERS_PER_TEAM + RELIEVERS_PER_TEAM


def test_the_groups_concatenate_to_the_flat_roster(mapper):
    batters, starters, relievers = mapper.select_roster_groups(_squad())
    assert batters + starters + relievers == mapper.select_roster(_squad())


def test_the_groups_of_a_full_squad_are_fifteen_five_and_five(mapper):
    batters, starters, relievers = mapper.select_roster_groups(_squad())
    assert [len(batters), len(starters), len(relievers)] == [
        BATTERS_PER_TEAM,
        STARTERS_PER_TEAM,
        RELIEVERS_PER_TEAM,
    ]


def test_a_short_batting_group_is_visible_in_the_groups(mapper):
    # The fact the flat list loses. Six batters, so slots 6, 7 and 8 hold
    # starting pitchers while the slot layout calls them batter slots -- and
    # only the group split says which they are.
    batters, starters, relievers = mapper.select_roster_groups(_squad(batters=6))
    assert [len(batters), len(starters), len(relievers)] == [6, 5, 5]


def test_a_short_batting_group_shifts_the_pitchers_down(mapper):
    selected = mapper.select_roster(_squad(batters=6))
    assert mapper.is_pitcher(selected[6]) is True


def test_a_squad_with_too_few_starters_borrows_from_the_relievers(mapper):
    selected = mapper.select_roster(_squad(starters=2, relievers=12))
    borrowed = selected[BATTERS_PER_TEAM : BATTERS_PER_TEAM + STARTERS_PER_TEAM]
    assert [p.name[:3] for p in borrowed] == ["Sta", "Sta", "Rel", "Rel", "Rel"]


def test_a_squad_with_too_few_relievers_borrows_from_the_starters(mapper):
    selected = mapper.select_roster(_squad(starters=12, relievers=2))
    borrowed = selected[BATTERS_PER_TEAM + STARTERS_PER_TEAM :]
    assert [p.name[:3] for p in borrowed] == ["Rel", "Rel", "Sta", "Sta", "Sta"]


def test_a_borrowed_pitcher_is_not_also_selected_in_its_own_group(mapper):
    selected = mapper.select_roster(_squad(starters=2, relievers=12))
    ids = [p.id for p in selected[BATTERS_PER_TEAM:]]
    assert len(set(ids)) == len(ids)


def test_no_player_is_selected_twice(mapper):
    selected = mapper.select_roster(_squad())
    assert len(set(p.id for p in selected)) == PLAYERS_PER_TEAM


def test_a_squad_of_only_pitchers_selects_no_batters(mapper):
    selected = mapper.select_roster(_squad(batters=0))
    assert len(selected) == STARTERS_PER_TEAM + RELIEVERS_PER_TEAM


def test_an_empty_squad_selects_nothing(mapper):
    assert mapper.select_roster([]) == []


def test_batters_are_ranked_by_ops_before_hits(mapper):
    # 0.9 OPS outranks 0.4 OPS whatever the hit totals, because OPS is weighted
    # by 1000 and hits are not.
    players = [
        _player(id=1, name="Low Ops", position="OF"),
        _player(id=2, name="High Ops", position="OF"),
    ]
    stats = {"1": {"OPS": 0.4, "H": 200}, "2": {"OPS": 0.9, "H": 10}}
    selected = mapper.select_roster(players, stats)
    assert [p.id for p in selected] == [2, 1]


def test_hits_break_a_tie_on_ops(mapper):
    players = [
        _player(id=1, name="Few Hits", position="OF"),
        _player(id=2, name="Many Hits", position="OF"),
    ]
    stats = {"1": {"OPS": 0.7, "H": 10}, "2": {"OPS": 0.7, "H": 180}}
    selected = mapper.select_roster(players, stats)
    assert [p.id for p in selected] == [2, 1]


def test_starters_are_ranked_by_wins_before_innings(mapper):
    players = [
        _player(id=1, name="Few Wins", position="SP"),
        _player(id=2, name="Many Wins", position="SP"),
    ]
    stats = {"1": {"W": 3, "IP": 210}, "2": {"W": 18, "IP": 120}}
    selected = mapper.select_roster(players, stats)
    assert [p.id for p in selected] == [2, 1]


def test_relievers_are_ranked_by_saves_before_era(mapper):
    players = [
        _player(id=1, name="No Saves", position="RP"),
        _player(id=2, name="Closer Man", position="RP"),
    ]
    stats = {"1": {"SV": 0, "ERA": 1.0}, "2": {"SV": 40, "ERA": 3.5}}
    selected = mapper.select_roster(players, stats)
    assert [p.id for p in selected] == [2, 1]


def test_a_reliever_with_no_stats_is_scored_on_a_nine_era(mapper):
    # The default is 9.0, so `10 - era` is 1 and a listed 1.00 ERA outranks it.
    players = [
        _player(id=1, name="No Stats", position="RP"),
        _player(id=2, name="Good Era", position="RP"),
    ]
    selected = mapper.select_roster(players, {"2": {"SV": 0, "ERA": 1.0}})
    assert [p.id for p in selected] == [2, 1]
