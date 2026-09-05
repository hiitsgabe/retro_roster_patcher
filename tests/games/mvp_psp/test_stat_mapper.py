"""`MVPStatMapper`: ESPN rosters and team leaders onto MVP's 0-99 scale.

**This is where the first of the three inherited bugs was**, and it has its own
section. `map_pitcher` ended with an unconditional
`rec.pitches = self._default_pitches(is_starter)` *outside* the `if stats:`
branch, so the velocity and control `_apply_pitcher_stats` had just derived from
strikeouts, WHIP and ERA were discarded and every pitcher in the game shipped
with the same 50/50 arsenal. Twelve lines and four statistics were dead code.

**And the third one is half here**: `Player.weight` was never read, so every
patched player weighed the record's default. The other half is in
`_parse_baseball_squad`, which never filled the field.

Nothing here touches the ROM or the network. The whole module is arithmetic on
two inputs, and `map_rosters` runs on a machine that has never seen the ISO.
"""

from __future__ import annotations

import dataclasses

import pytest

from retro_roster_patcher.games.mvp_psp.models import (
    ATTR_MAX,
    ATTR_MIN,
    BATTERS_PER_TEAM,
    MVP_ABBREV_TO_INDEX,
    PITCH_CHANGEUP,
    PITCH_FASTBALL,
    PITCH_SLIDER,
    PLAYERS_PER_TEAM,
    RELIEVERS_PER_TEAM,
    STARTERS_PER_TEAM,
    MVPPitch,
)
from retro_roster_patcher.games.mvp_psp.stat_mapper import (
    BATS_LEFT,
    BATS_RIGHT,
    BATS_SWITCH,
    DEFAULT_PICKOFF,
    DEFAULT_POSITION,
    DEFAULT_RELIEVER_STAMINA,
    DEFAULT_STARTER_STAMINA,
    PITCHER_NO_STATS_BATTING,
    PITCHER_POSITIONS,
    PITCHER_WITH_STATS_BATTING,
    POSITION_DEFAULTS,
    THROWS_LEFT,
    THROWS_RIGHT,
    UNNAMED,
    MVPStatMapper,
    PositionDefaults,
    _clamp,
    _scale,
    _stat,
)
from retro_roster_patcher.sports.models import Player

MAPPER = MVPStatMapper()


def player(pid=1, name="Ichiro Suzuki", position="RF", number=51, **kwargs):
    return Player(id=pid, name=name, position=position, number=number, **kwargs)


# -- the helpers -----------------------------------------------------------


def test_clamping_leaves_a_value_inside_the_scale():
    assert _clamp(50) == 50


def test_clamping_raises_a_negative_to_the_floor():
    assert _clamp(-1) == ATTR_MIN


def test_clamping_lowers_an_overflow_to_the_ceiling():
    assert _clamp(1000) == ATTR_MAX


def test_the_ceiling_is_ninety_nine_and_not_a_hundred():
    assert _clamp(100) == 99


def test_scaling_the_bottom_of_a_range_gives_zero():
    assert _scale(0.200, 0.200, 0.330) == 0


def test_scaling_the_top_of_a_range_gives_ninety_nine():
    assert _scale(0.330, 0.200, 0.330) == 99


def test_scaling_the_middle_of_a_range_gives_the_middle():
    assert _scale(0.265, 0.200, 0.330) == 50


def test_scaling_below_a_range_clamps_to_zero():
    assert _scale(0.100, 0.200, 0.330) == 0


def test_scaling_above_a_range_clamps_to_ninety_nine():
    assert _scale(0.900, 0.200, 0.330) == 99


def test_an_inverted_range_answers_the_midpoint():
    assert _scale(5, 10, 1) == 50


def test_an_empty_range_answers_the_midpoint():
    assert _scale(5, 7, 7) == 50


def test_a_reported_statistic_is_read():
    assert _stat({"HR": 42}, "HR", 0) == 42.0


def test_an_absent_statistic_takes_the_default():
    assert _stat({}, "HR", 7) == 7.0


def test_a_none_statistic_takes_the_default():
    assert _stat({"HR": None}, "HR", 7) == 7.0


def test_a_zero_statistic_takes_the_default():
    # `or default` treats a reported zero as an absence. The source's, and
    # right for the keys it is used on: an ERA of exactly 0.00 means too few
    # innings for the number to mean anything.
    assert _stat({"ERA": 0}, "ERA", 4.0) == 4.0


def test_a_statistic_is_answered_as_a_float():
    assert type(_stat({"HR": 42}, "HR", 0)) is float


# -- position defaults -----------------------------------------------------


def test_the_position_defaults_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        POSITION_DEFAULTS["C"].speed = 99


def test_every_lineup_position_has_defaults():
    assert sorted(POSITION_DEFAULTS) == ["1B", "2B", "3B", "C", "CF", "DH", "LF", "RF", "SS"]


def test_a_catcher_throws_harder_than_a_designated_hitter():
    assert POSITION_DEFAULTS["C"].throw_strength > POSITION_DEFAULTS["DH"].throw_strength


def test_a_centre_fielder_is_the_fastest_default():
    fastest = max(POSITION_DEFAULTS.values(), key=lambda d: d.speed)
    assert POSITION_DEFAULTS["CF"] == fastest


def test_the_defaults_type_is_the_frozen_dataclass():
    assert type(POSITION_DEFAULTS["SS"]) is PositionDefaults


def test_an_unrecognised_position_falls_back_to_centre_field():
    assert DEFAULT_POSITION == "CF"


# -- normalisation ---------------------------------------------------------


@pytest.mark.parametrize("code", ["C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH"])
def test_a_lineup_position_normalises_to_itself(code):
    assert MAPPER.normalize_position(code) == code


def test_a_lowercase_position_is_upper_cased():
    assert MAPPER.normalize_position("ss") == "SS"


def test_a_generic_outfielder_becomes_a_centre_fielder():
    assert MAPPER.normalize_position("OF") == "CF"


def test_a_generic_infielder_becomes_a_shortstop():
    assert MAPPER.normalize_position("IF") == "SS"


def test_an_unknown_position_becomes_a_centre_fielder():
    assert MAPPER.normalize_position("QB") == "CF"


def test_an_empty_position_becomes_a_centre_fielder():
    assert MAPPER.normalize_position("") == "CF"


@pytest.mark.parametrize("code", sorted(PITCHER_POSITIONS))
def test_every_pitcher_code_is_recognised(code):
    assert MAPPER.is_pitcher(player(position=code)) is True


def test_a_shortstop_is_not_a_pitcher():
    assert MAPPER.is_pitcher(player(position="SS")) is False


def test_a_lowercase_pitcher_code_is_recognised():
    assert MAPPER.is_pitcher(player(position="sp")) is True


def test_an_empty_position_is_not_a_pitcher():
    assert MAPPER.is_pitcher(player(position="")) is False


# -- handedness ------------------------------------------------------------


def test_a_left_handed_batter_is_one():
    assert MAPPER.map_bat_hand("L") == BATS_LEFT


def test_a_switch_hitter_is_two():
    assert MAPPER.map_bat_hand("S") == BATS_SWITCH


def test_the_providers_other_switch_code_is_also_two():
    assert MAPPER.map_bat_hand("B") == BATS_SWITCH


def test_a_right_handed_batter_is_zero():
    assert MAPPER.map_bat_hand("R") == BATS_RIGHT


def test_an_unknown_batting_hand_is_right_handed():
    assert MAPPER.map_bat_hand("Q") == BATS_RIGHT


def test_no_batting_hand_at_all_is_right_handed():
    assert MAPPER.map_bat_hand(None) == BATS_RIGHT


def test_a_lowercase_batting_hand_is_read():
    assert MAPPER.map_bat_hand("l") == BATS_LEFT


def test_a_left_handed_thrower_is_one():
    assert MAPPER.map_throw_hand("L") == THROWS_LEFT


def test_a_switch_code_throws_right_handed():
    # There is no switch-throwing code, so `S` collapses to right.
    assert MAPPER.map_throw_hand("S") == THROWS_RIGHT


def test_no_throwing_hand_at_all_is_right_handed():
    assert MAPPER.map_throw_hand("") == THROWS_RIGHT


# -- names -----------------------------------------------------------------


def test_a_first_name_is_the_first_word():
    assert MAPPER.first_name("Ken Griffey Jr.") == "Ken"


def test_an_empty_name_gives_the_placeholder_first_name():
    assert MAPPER.first_name("   ") == UNNAMED


def test_a_surname_is_everything_after_the_first_word():
    assert MAPPER.last_name("Jean Luc Picard") == "Luc Picard"


def test_a_generational_suffix_is_dropped_from_a_surname():
    assert MAPPER.last_name("Ken Griffey Jr.") == "Griffey"


@pytest.mark.parametrize("suffix", ["Jr.", "Sr", "II", "III", "IV"])
def test_every_recognised_suffix_is_dropped(suffix):
    assert MAPPER.last_name(f"Cal Ripken {suffix}") == "Ripken"


def test_a_one_word_name_is_its_own_surname():
    assert MAPPER.last_name("Ichiro") == "Ichiro"


def test_a_name_that_is_only_a_first_name_and_a_suffix_keeps_the_suffix():
    # Dropping it would leave the surname empty, which the disc renders as a
    # player with no last name at all.
    assert MAPPER.last_name("Ken Jr.") == "Jr."


def test_an_empty_name_gives_the_placeholder_surname():
    assert MAPPER.last_name("") == UNNAMED


def test_a_suffix_is_matched_case_insensitively():
    assert MAPPER.last_name("Cal Ripken jr") == "Ripken"


# -- team slots ------------------------------------------------------------


def test_a_provider_code_maps_to_its_slot():
    assert MAPPER.get_team_slot("NYY") == MVP_ABBREV_TO_INDEX["NYY"]


def test_a_renamed_club_maps_to_its_2005_slot():
    assert MAPPER.get_team_slot("MIA") == MVP_ABBREV_TO_INDEX["FLA"]


def test_a_lowercase_provider_code_maps():
    assert MAPPER.get_team_slot("lad") == MVP_ABBREV_TO_INDEX["LA"]


def test_an_unknown_provider_code_maps_to_nothing():
    assert MAPPER.get_team_slot("XYZ") is None


def test_the_two_oakland_codes_map_to_one_slot():
    assert MAPPER.get_team_slot("ATH") == MAPPER.get_team_slot("OAK")


def test_a_provider_code_answers_its_game_abbreviation():
    assert MAPPER.get_mvp_abbrev("WSH") == "WAS"


def test_an_unknown_code_answers_no_abbreviation():
    assert MAPPER.get_mvp_abbrev("XYZ") is None


# -- batters ---------------------------------------------------------------


def test_a_batter_with_no_stats_takes_his_positions_speed():
    record = MAPPER.map_batter(player(position="C"))
    assert record.speed == POSITION_DEFAULTS["C"].speed


def test_a_batter_with_no_stats_takes_his_positions_contact_on_both_sides():
    record = MAPPER.map_batter(player(position="1B"))
    assert (record.contact_rhp, record.contact_lhp) == (
        POSITION_DEFAULTS["1B"].contact,
        POSITION_DEFAULTS["1B"].contact,
    )


def test_a_batter_with_no_stats_bunts_at_forty():
    assert MAPPER.map_batter(player()).bunting == 40


def test_a_batter_with_no_stats_is_not_a_pitcher():
    assert MAPPER.map_batter(player()).is_pitcher is False


def test_a_batter_keeps_his_jersey_number():
    assert MAPPER.map_batter(player(number=51)).jersey == 51


def test_a_batter_with_no_jersey_number_wears_zero():
    assert MAPPER.map_batter(player(number=None)).jersey == 0


def test_a_batters_name_is_split():
    record = MAPPER.map_batter(player(name="Ken Griffey Jr."))
    assert (record.first_name, record.last_name) == ("Ken", "Griffey")


def test_a_batter_bats_the_hand_the_provider_reported():
    assert MAPPER.map_batter(player(bats="L", handedness="R")).bats == BATS_LEFT


def test_a_batter_with_no_batting_hand_falls_back_to_his_throwing_hand():
    assert MAPPER.map_batter(player(bats="", handedness="L")).bats == BATS_LEFT


def test_a_high_average_gives_more_contact_than_a_low_one():
    high = MAPPER.map_batter(player(), {"AVG": 0.330, "OBP": 0.420})
    low = MAPPER.map_batter(player(), {"AVG": 0.200, "OBP": 0.280})
    assert high.contact_rhp > low.contact_rhp


def test_contact_against_same_side_pitching_is_five_lower():
    record = MAPPER.map_batter(player(bats="R"), {"AVG": 0.300, "OBP": 0.380})
    assert record.contact_lhp == record.contact_rhp - 5


def test_a_switch_hitter_has_the_same_contact_on_both_sides():
    record = MAPPER.map_batter(player(bats="S"), {"AVG": 0.300, "OBP": 0.380})
    assert record.contact_rhp == record.contact_lhp


def test_a_switch_hitters_contact_is_the_mean_of_the_two_splits():
    # The mean of x and x-5, so two or three points below the stronger side --
    # not equal to it.
    plain = MAPPER.map_batter(player(bats="R"), {"AVG": 0.300, "OBP": 0.380})
    switch = MAPPER.map_batter(player(bats="S"), {"AVG": 0.300, "OBP": 0.380})
    assert switch.contact_rhp == (plain.contact_rhp + plain.contact_lhp) // 2


def test_a_switch_hitter_has_the_same_power_on_both_sides():
    record = MAPPER.map_batter(player(bats="S"), {"HR": 30, "SLG": 0.500})
    assert record.power_rhp == record.power_lhp


def test_home_runs_raise_power():
    many = MAPPER.map_batter(player(), {"HR": 45, "SLG": 0.600})
    few = MAPPER.map_batter(player(), {"HR": 1, "SLG": 0.350})
    assert many.power_rhp > few.power_rhp


def test_stolen_bases_raise_speed():
    fast = MAPPER.map_batter(player(), {"SB": 40})
    slow = MAPPER.map_batter(player(), {"SB": 1})
    assert fast.speed > slow.speed


def test_five_triples_add_five_points_of_speed():
    with_triples = MAPPER.map_batter(player(), {"SB": 10, "3B": 5})
    without = MAPPER.map_batter(player(), {"SB": 10, "3B": 4})
    assert with_triples.speed == without.speed + 5


def test_baserunning_is_five_above_speed():
    record = MAPPER.map_batter(player(), {"SB": 10})
    assert record.baserunning == record.speed + 5


def test_stealing_equals_speed():
    record = MAPPER.map_batter(player(), {"SB": 10})
    assert record.stealing == record.speed


def test_a_slow_batter_bunts_at_thirty():
    record = MAPPER.map_batter(player(), {"SB": 0, "3B": 0})
    assert record.bunting == 30


def test_a_fast_batter_bunts_ten_below_his_speed():
    record = MAPPER.map_batter(player(), {"SB": 40})
    assert record.bunting == record.speed - 10


def test_a_hundred_and_twenty_one_games_adds_five_points_of_fielding():
    many = MAPPER.map_batter(player(position="SS"), {"GP": 121})
    few = MAPPER.map_batter(player(position="SS"), {"GP": 120})
    assert many.fielding == few.fielding + 5


def test_plate_discipline_rises_with_the_gap_between_on_base_and_average():
    patient = MAPPER.map_batter(player(), {"AVG": 0.250, "OBP": 0.370})
    hacker = MAPPER.map_batter(player(), {"AVG": 0.250, "OBP": 0.290})
    assert patient.plate_discipline > hacker.plate_discipline


def test_durability_rises_with_games_played():
    iron = MAPPER.map_batter(player(), {"GP": 155})
    fragile = MAPPER.map_batter(player(), {"GP": 60})
    assert iron.durability > fragile.durability


def test_starpower_rises_with_the_hit_and_home_run_composite():
    star = MAPPER.map_batter(player(), {"H": 200, "HR": 45, "RBI": 150})
    scrub = MAPPER.map_batter(player(), {"H": 20, "HR": 1, "RBI": 5})
    assert star.starpower > scrub.starpower


@pytest.mark.parametrize(
    "field",
    [
        "speed",
        "fielding",
        "arm_range",
        "throw_strength",
        "throw_accuracy",
        "durability",
        "plate_discipline",
        "bunting",
        "baserunning",
        "stealing",
        "starpower",
        "contact_rhp",
        "power_rhp",
        "contact_lhp",
        "power_lhp",
    ],
)
def test_every_derived_batting_rating_stays_on_the_scale(field):
    extreme = MAPPER.map_batter(
        player(),
        {"AVG": 9.9, "OBP": 9.9, "SLG": 9.9, "HR": 999, "SB": 999, "GP": 999, "H": 999, "RBI": 999},
    )
    assert 0 <= getattr(extreme, field) <= ATTR_MAX


# -- pitchers, and bug 1 ---------------------------------------------------


def test_a_pitcher_with_no_stats_gets_the_fifty_fifty_arsenal():
    # Which is what the source gave *every* pitcher, derived or not.
    record = MAPPER.map_pitcher(player(position="SP"), None, is_starter=True)
    assert record.pitches == MAPPER.default_pitches(True)


def test_a_pitcher_with_stats_keeps_the_derived_arsenal():
    # DELIBERATE DIVERGENCE, and this is bug 1. The source overwrote it here.
    record = MAPPER.map_pitcher(
        player(position="SP"), {"K": 250, "WHIP": 0.90, "ERA": 2.0}, is_starter=True
    )
    assert record.pitches != MAPPER.default_pitches(True)


def test_a_high_strikeout_pitcher_throws_harder_than_a_low_one():
    power = MAPPER.map_pitcher(player(position="SP"), {"K": 250}, is_starter=True)
    finesse = MAPPER.map_pitcher(player(position="SP"), {"K": 60}, is_starter=True)
    assert power.pitches[0].velocity > finesse.pitches[0].velocity


def test_a_low_whip_pitcher_has_better_control_than_a_high_one():
    sharp = MAPPER.map_pitcher(player(position="SP"), {"WHIP": 0.90, "ERA": 2.0}, is_starter=True)
    wild = MAPPER.map_pitcher(player(position="SP"), {"WHIP": 1.60, "ERA": 6.0}, is_starter=True)
    assert sharp.pitches[0].control > wild.pitches[0].control


def test_the_derived_velocity_reaches_every_pitch_in_the_arsenal():
    power = MAPPER.map_pitcher(player(position="SP"), {"K": 250}, is_starter=True)
    finesse = MAPPER.map_pitcher(player(position="SP"), {"K": 60}, is_starter=True)
    assert [p.velocity for p in power.pitches] != [p.velocity for p in finesse.pitches]


def test_two_pitchers_with_different_statistics_get_different_arsenals():
    # The zero-over-zero check on bug 1: a fix that derived one arsenal and
    # gave it to everybody would pass every test above but this one.
    first = MAPPER.map_pitcher(player(position="SP"), {"K": 250, "WHIP": 0.9}, is_starter=True)
    second = MAPPER.map_pitcher(player(position="SP"), {"K": 90, "WHIP": 1.5}, is_starter=True)
    assert first.pitches != second.pitches


def test_a_pitcher_with_no_stats_is_a_pitcher():
    assert MAPPER.map_pitcher(player(position="SP")).is_pitcher is True


def test_a_starter_is_positioned_as_a_starting_pitcher():
    assert MAPPER.map_pitcher(player(position="SP"), is_starter=True).primary_position == "SP"


def test_a_reliever_is_positioned_as_a_relief_pitcher():
    assert MAPPER.map_pitcher(player(position="RP"), is_starter=False).primary_position == "RP"


def test_a_starter_with_no_stats_has_more_stamina_than_a_reliever():
    starter = MAPPER.map_pitcher(player(position="SP"), is_starter=True)
    reliever = MAPPER.map_pitcher(player(position="RP"), is_starter=False)
    assert (starter.stamina, reliever.stamina) == (
        DEFAULT_STARTER_STAMINA,
        DEFAULT_RELIEVER_STAMINA,
    )


def test_a_starters_stamina_never_falls_below_forty():
    # The floor exists so a starter with no quality starts still goes six
    # innings rather than being pulled in the second.
    assert MAPPER.map_pitcher(player(position="SP"), {"QS": 0.1}, is_starter=True).stamina == 40


def test_quality_starts_raise_stamina():
    workhorse = MAPPER.map_pitcher(player(position="SP"), {"QS": 25}, is_starter=True)
    assert workhorse.stamina > 40


def test_a_fifteen_win_season_adds_five_points_of_stamina():
    with_wins = MAPPER.map_pitcher(player(position="SP"), {"QS": 15, "W": 15}, is_starter=True)
    without = MAPPER.map_pitcher(player(position="SP"), {"QS": 15, "W": 14}, is_starter=True)
    assert with_wins.stamina == without.stamina + 5


def test_a_closer_with_saves_has_more_stamina_than_one_without():
    closer = MAPPER.map_pitcher(player(position="CP"), {"SV": 21}, is_starter=False)
    middle = MAPPER.map_pitcher(player(position="RP"), {"SV": 20}, is_starter=False)
    assert closer.stamina == middle.stamina + 5


def test_every_pitcher_picks_off_at_fifty():
    assert MAPPER.map_pitcher(player(position="SP"), {"K": 100}).pickoff == DEFAULT_PICKOFF


def test_a_pitcher_without_stats_hits_slightly_better_than_one_with():
    # The source's, and the difference is small enough to be an accident
    # rather than a judgement, so it is preserved rather than harmonised.
    without = MAPPER.map_pitcher(player(position="SP"))
    with_stats = MAPPER.map_pitcher(player(position="SP"), {"K": 100})
    assert (without.contact_rhp, with_stats.contact_rhp) == (
        PITCHER_NO_STATS_BATTING[0],
        PITCHER_WITH_STATS_BATTING[0],
    )


def test_a_winning_starter_has_more_starpower_than_a_losing_one():
    ace = MAPPER.map_pitcher(player(position="SP"), {"W": 20, "K": 250, "ERA": 2.0})
    filler = MAPPER.map_pitcher(player(position="SP"), {"W": 2, "K": 40, "ERA": 6.0})
    assert ace.starpower > filler.starpower


# -- the arsenal -----------------------------------------------------------


def test_a_starter_gets_three_pitches():
    assert len(MAPPER.default_pitches(True)) == 3


def test_a_reliever_gets_two_pitches():
    assert len(MAPPER.default_pitches(False)) == 2


def test_the_first_pitch_is_always_a_fastball():
    assert MAPPER.default_pitches(True)[0].type == PITCH_FASTBALL


def test_a_starters_second_pitch_is_a_slider():
    assert MAPPER.default_pitches(True)[1].type == PITCH_SLIDER


def test_a_starters_third_pitch_is_a_changeup():
    assert MAPPER.default_pitches(True)[2].type == PITCH_CHANGEUP


def test_a_relievers_second_pitch_is_a_slider():
    assert MAPPER.default_pitches(False)[1].type == PITCH_SLIDER


def test_the_fastball_is_the_hardest_pitch():
    pitches = MAPPER.default_pitches(True, 60, 60)
    assert pitches[0].velocity == max(p.velocity for p in pitches)


def test_the_changeup_is_the_softest_pitch():
    pitches = MAPPER.default_pitches(True, 60, 60)
    assert pitches[2].velocity == min(p.velocity for p in pitches)


def test_the_fastball_is_ten_above_the_supplied_velocity():
    assert MAPPER.default_pitches(True, 60, 50)[0].velocity == 70


def test_the_slider_trades_five_points_of_control_for_movement():
    pitches = MAPPER.default_pitches(True, 60, 50)
    assert (pitches[1].control, pitches[1].movement) == (45, 35)


def test_the_changeup_keeps_the_fastballs_control():
    pitches = MAPPER.default_pitches(True, 60, 50)
    assert pitches[2].control == pitches[0].control


def test_a_starters_slider_moves_more_than_a_relievers():
    assert MAPPER.default_pitches(True, 60, 50)[1].movement == (
        MAPPER.default_pitches(False, 60, 50)[1].movement + 5
    )


@pytest.mark.parametrize("starter", [True, False])
@pytest.mark.parametrize("velocity", [0, 1, 50, 98, 99])
@pytest.mark.parametrize("control", [0, 1, 50, 98, 99])
def test_every_generated_pitch_stays_on_the_scale(starter, velocity, control):
    pitches = MAPPER.default_pitches(starter, velocity, control)
    off_scale = [
        p for p in pitches if not (0 <= p.control <= ATTR_MAX and 0 <= p.velocity <= ATTR_MAX)
    ]
    assert off_scale == []


def test_a_generated_pitch_is_the_frozen_record_type():
    assert type(MAPPER.default_pitches(True)[0]) is MVPPitch


# -- weight, which is half of bug 3 ----------------------------------------


def test_a_batter_carries_the_weight_the_provider_reported():
    # DELIBERATE DIVERGENCE. The source never read `Player.weight`, so every
    # patched player weighed the record's 190 lb default.
    assert MAPPER.map_batter(player(weight=215.0)).weight == 215


def test_a_pitcher_carries_the_weight_the_provider_reported():
    assert MAPPER.map_pitcher(player(position="SP", weight=201.0)).weight == 201


def test_two_players_of_different_weights_map_to_different_weights():
    # The zero-over-zero check: a mapper that stamped one constant on everyone
    # would pass a single-player assertion against that constant.
    heavy = MAPPER.map_batter(player(weight=250.0))
    light = MAPPER.map_batter(player(weight=160.0))
    assert (heavy.weight, light.weight) == (250, 160)


def test_a_player_the_provider_has_no_weight_for_carries_zero():
    # Zero is what makes the writer leave the disc's own weight alone.
    assert MAPPER.map_batter(player()).weight == 0


def test_a_fractional_weight_is_truncated_to_pounds():
    assert MAPPER.map_batter(player(weight=199.7)).weight == 199


# -- roster selection ------------------------------------------------------


def squad(batters=15, starters=5, relievers=5, base=1000):
    """A squad with a known shape, every player identifiable by id."""
    positions = (
        ["C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH"][:batters]
        + ["LF"] * max(batters - 9, 0)
        + ["SP"] * starters
        + ["RP"] * relievers
    )
    return [
        player(pid=base + i, position=p, name=f"Given{base + i} Family{base + i}")
        for i, p in enumerate(positions)
    ]


def test_a_full_squad_selects_twenty_five():
    assert len(MAPPER.select_roster(squad())) == PLAYERS_PER_TEAM


def test_a_short_squad_selects_what_it_has():
    assert len(MAPPER.select_roster(squad(batters=4, starters=2, relievers=1))) == 7


def test_an_empty_squad_selects_nobody():
    assert MAPPER.select_roster([]) == []


def test_the_first_fifteen_selected_are_batters():
    selected = MAPPER.select_roster(squad())
    assert [MAPPER.is_pitcher(p) for p in selected[:BATTERS_PER_TEAM]] == [False] * 15


def test_the_next_five_selected_are_pitchers():
    selected = MAPPER.select_roster(squad())
    pitchers = selected[BATTERS_PER_TEAM : BATTERS_PER_TEAM + STARTERS_PER_TEAM]
    assert [MAPPER.is_pitcher(p) for p in pitchers] == [True] * 5


def test_the_last_five_selected_are_pitchers():
    selected = MAPPER.select_roster(squad())
    assert [MAPPER.is_pitcher(p) for p in selected[-RELIEVERS_PER_TEAM:]] == [True] * 5


def test_the_best_player_at_a_position_takes_that_position():
    # Not "the best batter bats first": positions are filled in
    # `SELECTION_POSITIONS` order, so slot 0 is the best catcher rather than
    # the best hitter on the team.
    catchers = [player(pid=800 + i, position="C") for i in range(3)]
    stats = {"800": {"OPS": 0.500}, "801": {"OPS": 1.200}, "802": {"OPS": 0.600}}
    assert MAPPER.select_roster(catchers, stats)[0].id == 801


def test_the_best_hitter_on_the_team_does_not_displace_a_catcher():
    # The other half, and the reason the test above is worded as it is.
    players = [player(pid=810, position="C"), player(pid=811, position="RF")]
    stats = {"810": {"OPS": 0.400}, "811": {"OPS": 1.300}}
    assert MAPPER.select_roster(players, stats)[0].id == 810


def test_one_player_is_taken_per_lineup_position_first():
    players = [player(pid=100 + i, position=p) for i, p in enumerate(["C", "C", "C", "1B"])]
    selected = MAPPER.select_roster(players)
    assert [p.position for p in selected[:2]] == ["C", "1B"]


def test_a_position_nobody_plays_is_skipped_rather_than_filled():
    players = [player(pid=100 + i, position="C") for i in range(3)]
    assert [p.position for p in MAPPER.select_roster(players)] == ["C", "C", "C"]


def test_the_bench_takes_the_best_of_what_is_left():
    players = squad(batters=12, starters=0, relievers=0)
    stats = {str(p.id): {"OPS": 0.1 * (i + 1), "H": i} for i, p in enumerate(players)}
    selected = MAPPER.select_roster(players, stats)
    assert len(selected) == 12


def test_no_batter_is_selected_twice():
    selected = MAPPER.select_roster(squad())
    ids = [p.id for p in selected]
    assert len(set(ids)) == len(ids)


def test_the_rotation_is_topped_up_from_the_relief_pool():
    # ESPN lists most of a staff as a bare `P`, so a squad with no `SP` at all
    # still has to fill five rotation slots.
    players = [player(pid=200 + i, position="P") for i in range(8)]
    selected = MAPPER.select_roster(players)
    assert len(selected) == 8


def test_the_bullpen_is_filled_before_the_rotation():
    # The opposite of what the code's shape suggests, and inherited: the two
    # slices happen before either top-up loop, so a staff ESPN lists entirely
    # as `P` puts its five best in the bullpen and the sixth in the rotation.
    players = [player(pid=200 + i, position="P") for i in range(6)]
    selected = MAPPER.select_roster(players)
    assert [p.id for p in selected[-5:]] == [200, 201, 202, 203, 204]


def test_the_rotation_takes_the_pitcher_the_bullpen_did_not():
    players = [player(pid=200 + i, position="P") for i in range(6)]
    selected = MAPPER.select_roster(players)
    assert selected[0].id == 205


def test_a_staff_of_five_bare_pitchers_leaves_the_rotation_empty():
    # The sharp end of the same behaviour: five is exactly the bullpen's
    # slice, so nothing is left over to top the rotation up with.
    players = [player(pid=210 + i, position="P") for i in range(5)]
    assert len(MAPPER.select_roster(players)) == 5


def test_a_starter_with_more_wins_is_taken_first():
    players = [player(pid=300, position="SP"), player(pid=301, position="SP")]
    stats = {"300": {"W": 5, "IP": 200}, "301": {"W": 20, "IP": 200}}
    assert MAPPER.select_roster(players, stats)[0].id == 301


def test_a_reliever_with_more_saves_is_taken_first():
    players = [player(pid=400, position="RP"), player(pid=401, position="RP")]
    stats = {"400": {"SV": 2, "ERA": 2.0}, "401": {"SV": 40, "ERA": 3.0}}
    assert MAPPER.select_roster(players, stats)[0].id == 401


def test_two_saveless_relievers_are_ordered_by_earned_run_average():
    players = [player(pid=500, position="RP"), player(pid=501, position="RP")]
    stats = {"500": {"ERA": 5.0}, "501": {"ERA": 1.5}}
    assert MAPPER.select_roster(players, stats)[0].id == 501


def test_two_players_with_the_same_id_are_two_roster_entries():
    # Identity and not equality decides, which is the source's behaviour and
    # the only one available: `Player` is not hashable.
    duplicate = [player(pid=600, position="C"), player(pid=600, position="C")]
    assert len(MAPPER.select_roster(duplicate)) == 2


def test_selection_never_returns_more_batters_than_the_roster_holds():
    players = [player(pid=700 + i, position="LF") for i in range(40)]
    assert len(MAPPER.select_roster(players)) == BATTERS_PER_TEAM
