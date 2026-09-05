"""`MVPStatMapper`: ESPN rosters and team leaders onto MVP's 0-99 scale.

**This is where the first of the three inherited bugs is**, and it has its own
section. `map_pitcher` ends with an unconditional
`rec.pitches = self.default_pitches(is_starter)` *outside* the `if stats:`
branch, so the velocity and control `_apply_pitcher_stats` has just derived from
strikeouts, WHIP and ERA are discarded and every pitcher in the game ships with
the same 50/50 arsenal. Twelve lines and four statistics are dead code. It is
upstream's, it is preserved deliberately, and the tests in that section say so
one by one -- they drive `_apply_pitcher_stats` where they need the derivation,
because that is the only place it survives.

**And half of the third one is here**: `Player.weight` is not read, so every
patched player is written at `MVPPlayerRecord.weight`'s 190 lb default. Also
upstream's, also preserved; the other half is `patcher._build_attrib_fields`,
which writes the column unconditionally.

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
    MVPPlayerRecord,
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


def derived_arsenal(stats, *, is_starter=True):
    """The arsenal `_apply_pitcher_stats` computes before `map_pitcher` drops it.

    Every velocity and control assertion below goes through here rather than
    through `map_pitcher`, because `map_pitcher` overwrites the result -- see
    the module docstring and the label on that method. Reading these through
    `map_pitcher` would assert a constant.
    """
    record = MVPPlayerRecord(is_pitcher=True)
    return MAPPER._apply_pitcher_stats(record, stats, is_starter).pitches


# -- the numbers, written out ----------------------------------------------
#
# Everything below reads a rating back through the constant that produced it, so
# a whole table of them can be changed together and nothing notices: mutation
# testing exchanged the contact and power columns of the centre fielder's
# defaults, moved `DEFAULT_PICKOFF` by one and lower-cased `UNNAMED`, and the
# suite stayed green through all three. These are the source's numbers, stated
# once more so a change to one is a change to a test.

CENTRE_FIELD_DEFAULTS = PositionDefaults(
    speed=65, fielding=60, arm_range=65, throw_strength=60, throw_accuracy=55, contact=55, power=45
)

DEFAULT_RATINGS = {
    "C": (35, 60, 55, 65, 60, 55, 50),
    "1B": (30, 50, 45, 55, 55, 60, 65),
    "2B": (55, 65, 60, 50, 65, 55, 35),
    "3B": (40, 55, 55, 70, 60, 55, 55),
    "SS": (55, 70, 65, 65, 65, 55, 35),
    "LF": (55, 50, 50, 55, 55, 60, 55),
    "CF": (65, 60, 65, 60, 55, 55, 45),
    "RF": (50, 55, 55, 70, 60, 60, 60),
    "DH": (30, 30, 30, 40, 40, 65, 70),
}


@pytest.mark.parametrize(("position", "ratings"), sorted(DEFAULT_RATINGS.items()))
def test_a_positions_defaults_are_the_ones_the_source_had(position, ratings):
    assert dataclasses.astuple(POSITION_DEFAULTS[position]) == ratings


def test_the_centre_fielders_defaults_name_their_own_fields():
    # The parametrised check above compares tuples, so it would survive the
    # dataclass's own field order being rearranged. This one names them.
    assert POSITION_DEFAULTS["CF"] == CENTRE_FIELD_DEFAULTS


def test_the_nine_lineup_positions_all_have_defaults():
    assert sorted(POSITION_DEFAULTS) == sorted(DEFAULT_RATINGS)


def test_a_player_with_no_name_is_called_player():
    assert UNNAMED == "Player"


def test_every_pitchers_pickoff_rating_is_fifty():
    assert DEFAULT_PICKOFF == 50


def test_no_alias_can_name_a_position_with_no_defaults():
    # This is what makes `map_batter`'s `POSITION_DEFAULTS.get(pos, ...)`
    # fallback unreachable, and the fallback is argued equivalent at the line on
    # the strength of it: every string `normalize_position` can return -- the
    # eleven alias targets and `DEFAULT_POSITION` -- is a key here.
    reachable = set(MAPPER.normalize_position(p) for p in ["C", "OF", "IF", "DH", "nonsense"])
    assert reachable - set(POSITION_DEFAULTS) == set()


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


def test_scaling_seven_tenths_of_a_range_lands_on_sixty_nine():
    # The scale's top is 99 and not 100, and the two agree at both ends and at
    # the midpoint -- 0.7 of a range is where they part, 69.3 rounding to 69
    # where 70.0 rounds to 70. Nothing else here could tell them apart.
    assert _scale(7.0, 0.0, 10.0) == 69


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


def test_a_lowercase_provider_code_answers_its_game_abbreviation():
    # `get_mvp_abbrev` upper-cases its argument, and only this says so:
    # `get_team_slot` has an `.upper()` of its own, and this method has no
    # caller inside the package at all -- it is here for the front ends.
    assert MAPPER.get_mvp_abbrev("wsh") == "WAS"


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


def test_a_batter_with_no_stats_is_averagely_patient():
    # 50, one of the four ratings with no positional default.
    assert MAPPER.map_batter(player()).plate_discipline == 50


def test_a_batter_with_no_stats_is_averagely_durable():
    assert MAPPER.map_batter(player()).durability == 50


def test_a_batter_with_no_stats_runs_the_bases_at_his_own_speed(tmp_path):
    # His speed and not his fielding, and the centre fielder is the position
    # where the two differ by enough to say so: 65 against 60.
    assert MAPPER.map_batter(player(position="CF")).baserunning == 65


def test_a_batter_with_no_stats_steals_at_his_own_speed(tmp_path):
    # Not the flat 50 that every other rating with no positional default takes.
    assert MAPPER.map_batter(player(position="CF")).stealing == 65


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


def test_contact_is_the_average_plus_a_quarter_of_the_on_base_rating():
    # A quarter, and the whole rest of this section is comparisons -- which a
    # third satisfies just as well. The average scales to 50 and the on-base
    # rating to 99, so the two weightings are 74 and 83.
    record = MAPPER.map_batter(player(bats="R"), {"AVG": 0.265, "OBP": 0.420})
    assert record.contact_rhp == 74


def test_power_is_two_thirds_home_runs_and_one_third_slugging():
    # Twenty home runs scale to 44 and a .400 slugging percentage to 20, so
    # `(44 * 2 + 20) // 3` is 36 where a single weighting would give 21.
    record = MAPPER.map_batter(player(bats="R"), {"HR": 20, "SLG": 0.400})
    assert record.power_rhp == 36


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


def test_a_batter_of_exactly_fifty_speed_is_on_the_fast_side_of_the_bunt_rule():
    # `30 if speed < 50`, so a batter at exactly 50 bunts at 40. Twenty stolen
    # bases scale to 49.5, which rounds to 50 and is the only way to stand on
    # the boundary; the two tests above are at 0 and at 99.
    record = MAPPER.map_batter(player(), {"SB": 20})
    assert (record.speed, record.bunting) == (50, 40)


def test_durability_is_games_played_on_the_sixty_to_a_hundred_and_fifty_five_scale():
    # Seventy-two games. The top of the range is 155 and not 156, and 72 is the
    # lowest count at which the two answers differ -- 13 against 12.
    assert MAPPER.map_batter(player(), {"GP": 72}).durability == 13


def test_starpower_weights_hits_lowest_and_home_runs_highest():
    # `h * 0.3 + hr * 2 + rbi * 0.5`, scaled from 20 to 200. 180 hits, 20 home
    # runs and 60 runs batted in make 124, which is 57; exchanging the hit and
    # run-batted-in weights makes 160 and 70.
    record = MAPPER.map_batter(player(), {"H": 180, "HR": 20, "RBI": 60})
    assert record.starpower == 57


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


def test_a_pitcher_with_stats_gets_the_fifty_fifty_arsenal_too():
    # PINS UPSTREAM FIDELITY DELIBERATELY, and this is bug 1: the derivation
    # runs and is then thrown away by the unconditional assignment at the end of
    # `map_pitcher`. Do not delete that line -- it changes `pitchattrib`'s
    # movement, control and velocity columns on every patched pitcher, and no
    # disc has ever checked this port's output.
    record = MAPPER.map_pitcher(
        player(position="SP"), {"K": 250, "WHIP": 0.90, "ERA": 2.0}, is_starter=True
    )
    assert record.pitches == MAPPER.default_pitches(True)


def test_the_arsenal_the_stats_derived_was_not_the_fifty_fifty_one():
    # Pins the test above. Without this, "a pitcher with stats gets the default"
    # is also satisfied by a derivation that happened to produce the default.
    assert derived_arsenal({"K": 250, "WHIP": 0.90, "ERA": 2.0}) != MAPPER.default_pitches(True)


def test_two_pitchers_with_different_statistics_get_the_same_arsenal():
    # PINS UPSTREAM FIDELITY DELIBERATELY: the flattening, stated at the level a
    # disc sees. A Cy Young winner and a replacement-level arm are written with
    # identical movement, control and velocity.
    first = MAPPER.map_pitcher(player(position="SP"), {"K": 250, "WHIP": 0.9}, is_starter=True)
    second = MAPPER.map_pitcher(player(position="SP"), {"K": 90, "WHIP": 1.5}, is_starter=True)
    assert first.pitches == second.pitches


def test_the_two_derivations_those_pitchers_produced_did_differ():
    # Pins the test above: the inputs really do drive different arsenals, so
    # "the same" is the overwrite and not two identical stat lines.
    assert derived_arsenal({"K": 250, "WHIP": 0.9}) != derived_arsenal({"K": 90, "WHIP": 1.5})


def test_a_high_strikeout_pitcher_throws_harder_than_a_low_one():
    assert derived_arsenal({"K": 250})[0].velocity > derived_arsenal({"K": 60})[0].velocity


def test_a_low_whip_pitcher_has_better_control_than_a_high_one():
    sharp = derived_arsenal({"WHIP": 0.90, "ERA": 2.0})
    wild = derived_arsenal({"WHIP": 1.60, "ERA": 6.0})
    assert sharp[0].control > wild[0].control


def test_a_starter_and_a_reliever_read_the_same_strikeouts_on_different_scales():
    # 250 strikeouts is a league-leading starter and 90 a league-leading
    # reliever, so the same 90 is near the bottom of one scale and the top of
    # the other. Every other velocity test here holds the role fixed, so
    # exchanging the two ranges outright survived them.
    starter = derived_arsenal({"K": 90}, is_starter=True)
    reliever = derived_arsenal({"K": 90}, is_starter=False)
    assert (starter[0].velocity, reliever[0].velocity) == (26, 99)


def test_a_pitcher_with_statistics_runs_at_thirty():
    assert MAPPER.map_pitcher(player(position="SP"), {"K": 100}).speed == 30


def test_a_pitcher_without_statistics_runs_at_thirty_five():
    # Five points quicker than one the provider had statistics for, which is
    # the source's and is preserved rather than harmonised.
    assert MAPPER.map_pitcher(player(position="SP")).speed == 35


def test_a_pitcher_without_statistics_fields_at_forty():
    assert MAPPER.map_pitcher(player(position="SP")).fielding == 40


def test_a_pitcher_with_statistics_fields_at_forty_as_well():
    # The one rating the two branches agree on.
    assert MAPPER.map_pitcher(player(position="SP"), {"K": 100}).fielding == 40


def test_a_starters_stamina_comes_off_his_quality_starts():
    # Twenty quality starts on the 5-to-25 range, which is 74 -- above the
    # floor of 40, so this is the scale rather than the clamp.
    record = MAPPER.map_pitcher(player(position="SP"), {"QS": 20}, is_starter=True)
    assert record.stamina == 74


def test_a_starters_starpower_weights_wins_three_times():
    # `w * 3 + k * 0.1 + (6 - era) * 10`, scaled from 10 to 80. Ten wins, a
    # hundred strikeouts and a 4.00 ERA make 60, which is 71.
    record = MAPPER.map_pitcher(
        player(position="SP"), {"W": 10, "K": 100, "ERA": 4.0}, is_starter=True
    )
    assert record.starpower == 71


def test_a_relievers_starpower_weights_saves_three_times():
    # `sv * 3 + k * 0.1 + (4 - era) * 5`, ten saves, fifty strikeouts, a 3.00
    # ERA: 40, which is 42.
    record = MAPPER.map_pitcher(
        player(position="RP"), {"SV": 10, "K": 50, "ERA": 3.0}, is_starter=False
    )
    assert record.starpower == 42


def test_control_reads_the_walks_and_hits_the_right_way_round():
    # The test above moves ERA as well as WHIP, and the ERA term alone orders
    # the two whichever way the WHIP term is read -- so inverting the WHIP
    # subtraction survived it. Here ERA is held and only WHIP moves: a 0.90 WHIP
    # tops its scale and a 1.60 bottoms it, leaving the ERA term to halve alone.
    sharp = derived_arsenal({"WHIP": 0.90, "ERA": 2.0})
    wild = derived_arsenal({"WHIP": 1.60, "ERA": 2.0})
    assert (sharp[0].control, wild[0].control) == (99, 49)


def test_the_derived_velocity_reaches_every_pitch_in_the_arsenal():
    power = derived_arsenal({"K": 250})
    finesse = derived_arsenal({"K": 60})
    assert [p.velocity for p in power] != [p.velocity for p in finesse]


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


# Both tuples are read back through themselves everywhere, so exchanging the
# two entries of either -- turning a pitcher who makes contact more often than
# he hits for power into the reverse -- changed nothing anywhere. These are the
# four numbers, and which of each pair is the contact one.


def test_a_pitcher_without_statistics_makes_contact_at_twenty_five():
    assert MAPPER.map_pitcher(player(position="SP")).contact_rhp == 25


def test_a_pitcher_without_statistics_hits_for_power_at_fifteen():
    assert MAPPER.map_pitcher(player(position="SP")).power_rhp == 15


def test_a_pitcher_with_statistics_makes_contact_at_twenty():
    assert MAPPER.map_pitcher(player(position="SP"), {"K": 100}).contact_rhp == 20


def test_a_pitcher_with_statistics_hits_for_power_at_ten():
    assert MAPPER.map_pitcher(player(position="SP"), {"K": 100}).power_rhp == 10


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


# Each pitch's three numbers, in full, at one velocity and one control. The
# tests around this one compare a pitch with its neighbour, which leaves the
# fastball's `velocity // 2` movement and the changeup's fifteen-point velocity
# drop unstated -- both survived mutation.


def test_a_starters_whole_arsenal_at_sixty_velocity_and_fifty_control():
    arsenal = MAPPER.default_pitches(True, 60, 50)
    assert [(p.type, p.movement, p.control, p.velocity) for p in arsenal] == [
        (PITCH_FASTBALL, 30, 50, 70),
        (PITCH_SLIDER, 35, 45, 55),
        (PITCH_CHANGEUP, 20, 50, 45),
    ]


def test_a_relievers_whole_arsenal_at_sixty_velocity_and_fifty_control():
    arsenal = MAPPER.default_pitches(False, 60, 50)
    assert [(p.type, p.movement, p.control, p.velocity) for p in arsenal] == [
        (PITCH_FASTBALL, 30, 50, 70),
        (PITCH_SLIDER, 30, 45, 55),
    ]


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


def test_a_batter_does_not_carry_the_weight_the_provider_reported():
    # PINS UPSTREAM FIDELITY DELIBERATELY, and this is half of bug 3:
    # `Player.weight` is not read, so the record keeps its 190 lb default and
    # every patched player is written at it. Do not add `weight=...` to the
    # constructor -- it changes `attrib` column 10 on every player, and no disc
    # has ever checked this port's output.
    assert MAPPER.map_batter(player(weight=215.0)).weight == 190


def test_a_pitcher_does_not_carry_it_either():
    assert MAPPER.map_pitcher(player(position="SP", weight=201.0)).weight == 190


def test_two_players_of_different_weights_map_to_the_same_weight():
    # The flattening itself. Both provider figures are real and neither survives.
    heavy = MAPPER.map_batter(player(weight=250.0))
    light = MAPPER.map_batter(player(weight=160.0))
    assert (heavy.weight, light.weight) == (190, 190)


def test_the_provider_really_did_report_those_two_weights():
    # Pins the two tests above: `Player.weight` is populated, so "190" is the
    # mapper dropping a value and not an input that was never there.
    assert [player(weight=250.0).weight, player(weight=160.0).weight] == [250.0, 160.0]


def test_a_player_the_provider_has_no_weight_for_gets_the_same_default():
    assert MAPPER.map_batter(player()).weight == 190


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


def test_on_base_plus_slugging_outweighs_hits_by_a_thousand_to_one():
    # The batting key is `OPS * 1000 + H`, and the factor is what makes it a
    # tie-break on hits rather than a hit count with an on-base adjustment.
    # These two are ordered one way at a thousand and the other at a hundred.
    catchers = [player(pid=820, position="C"), player(pid=821, position="C")]
    stats = {"820": {"OPS": 0.900, "H": 10}, "821": {"OPS": 0.800, "H": 50}}
    assert MAPPER.select_roster(catchers, stats)[0].id == 820


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


def test_a_win_outweighs_a_hundred_innings_in_the_rotation_order():
    # `W * 100 + IP`, and the factor is what makes innings the tie-break rather
    # than the measure. These two are ordered one way at a hundred and the
    # other at ten.
    players = [player(pid=310, position="SP"), player(pid=311, position="SP")]
    stats = {"310": {"W": 12, "IP": 150}, "311": {"W": 11, "IP": 200}}
    assert MAPPER.select_roster(players, stats)[0].id == 310


def test_a_sixth_listed_starter_does_not_lengthen_the_rotation():
    # `starters[:STARTERS_PER_TEAM]`. With no relievers to compete with, a
    # sixth starter is picked up by the bullpen top-up and the squad is the
    # same length either way, so this one gives him a full bullpen to be turned
    # away from: ten players out of eleven, and the sixth starter is the one
    # left out.
    players = [player(pid=320 + i, position="SP") for i in range(6)]
    players += [player(pid=330 + i, position="RP") for i in range(5)]
    assert len(MAPPER.select_roster(players)) == 10


def test_the_sixth_starter_is_the_one_left_out(tmp_path):
    players = [player(pid=320 + i, position="SP") for i in range(6)]
    players += [player(pid=330 + i, position="RP") for i in range(5)]
    assert 325 not in [p.id for p in MAPPER.select_roster(players)]


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
