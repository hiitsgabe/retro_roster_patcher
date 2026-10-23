"""The KGJ MLB stat mapper.

Every rating is a small integer with only ten legal values, so an assertion is
easily true by accident: a test that only ever checks 5 passes against a mapper
that returns 5 for everything. Each rating pinned here is therefore pinned at
both ends of its scale, from lines that differ in the one statistic it reads,
and its fallback is pinned on its own: one missing stat must not move another
rating.

`BATTER_DEFAULTS` and `PITCHER_DEFAULTS` are copied with `dataclasses.replace`
rather than handed out, so editing one record never edits the table.
"""

import pytest

from retro_roster_patcher.games.kgj_mlb_snes.models import (
    BATTERS_PER_TEAM,
    PLAYERS_PER_TEAM,
    RELIEVERS_PER_TEAM,
    STARTERS_PER_TEAM,
)
from retro_roster_patcher.games.kgj_mlb_snes.stat_mapper import (
    BATTER_DEFAULTS,
    PITCHER_DEFAULTS,
    KGJStatMapper,
    _innings,
    _lin,
)
from retro_roster_patcher.sports.models import Player

# Full ESPN lines, as `get_baseball_athlete_stats` returns their category.
STAR_BATTER = {"PA": 650, "AVG": 0.320, "SLG": 0.640, "SB": 45, "3B": 5, "HR": 45, "RBI": 130}
WEAK_BATTER = {"PA": 400, "AVG": 0.180, "SLG": 0.230, "SB": 0, "3B": 0, "HR": 0, "RBI": 12}
ACE = {"IP": 210.0, "GP": 30, "GS": 30, "K": 280, "BB": 30, "W": 18, "L": 5, "ERA": 1.9}
WILD = {"IP": 150.0, "GP": 30, "GS": 30, "K": 80, "BB": 100, "W": 4, "L": 15, "ERA": 6.5}
CLOSER = {"IP": 60.0, "GP": 60, "GS": 0, "K": 80, "BB": 15, "SV": 41, "ERA": 2.1}
LONG_MAN = {"IP": 100.0, "GP": 40, "GS": 0, "K": 70, "BB": 30, "SV": 0, "ERA": 3.9}


@pytest.fixture
def mapper():
    return KGJStatMapper()


def _player(**overrides):
    base = dict(id=1, name="Ken Griffey Jr.", position="CF", number=24)
    base.update(overrides)
    return Player(**base)


def test_lin_maps_the_ends_of_a_range_to_the_ends_of_the_scale():
    assert (_lin(0.190, 0.190, 0.300, 3, 10), _lin(0.300, 0.190, 0.300, 3, 10)) == (3, 10)


def test_lin_clamps_outside_the_range():
    assert (_lin(0.0, 0.190, 0.300, 3, 10), _lin(1.0, 0.190, 0.300, 3, 10)) == (3, 10)


def test_lin_reads_a_reversed_range_as_less_is_better():
    # Walks: 5.0 per nine is the bottom of CON, 1.8 the top.
    assert (_lin(5.0, 5.0, 1.8, 4, 10), _lin(1.8, 5.0, 1.8, 4, 10)) == (4, 10)


def test_innings_read_espns_thirds():
    assert (_innings(162.2), _innings(60.1), _innings(7.0)) == (162 + 2 / 3, 60 + 1 / 3, 7.0)


@pytest.mark.parametrize(
    "field,star,weak", [("batting", 10, 3), ("power", 10, 2), ("speed", 10, 4)]
)
def test_a_batters_ratings_span_the_scale(mapper, field, star, weak):
    best = getattr(mapper._map_batter_stats(STAR_BATTER, "CF"), field)
    worst = getattr(mapper._map_batter_stats(WEAK_BATTER, "CF"), field)
    assert (best, worst) == (star, weak)


def test_power_reads_isolated_power_not_the_home_run_count(mapper):
    # A .300 hitter slugging .370 has little power, however many he hit.
    singles = dict(STAR_BATTER, AVG=0.300, SLG=0.370)
    assert mapper._map_batter_stats(singles, "CF").power == 2


def test_triples_count_double_toward_speed(mapper):
    base = dict(WEAK_BATTER, PA=600, SB=10)
    assert mapper._map_batter_stats(dict(base, **{"3B": 10}), "CF").speed > (
        mapper._map_batter_stats(base, "CF").speed
    )


def test_a_small_sample_keeps_the_position_defaults(mapper):
    # A call-up 6 for 10 is not a BAT 10.
    hot = {"PA": 12, "AVG": 0.600, "SLG": 1.200, "SB": 3}
    attrs = mapper._map_batter_stats(hot, "SS")
    default = BATTER_DEFAULTS["SS"]
    assert (attrs.batting, attrs.power, attrs.speed) == (
        default.batting,
        default.power,
        default.speed,
    )


def test_a_missing_stat_falls_back_alone(mapper):
    # No SB: speed is the position's, while BAT and POW still read the line.
    line = {k: v for k, v in STAR_BATTER.items() if k != "SB"}
    attrs = mapper._map_batter_stats(line, "C")
    assert (attrs.batting, attrs.power, attrs.speed) == (10, 10, BATTER_DEFAULTS["C"].speed)


def test_a_stat_that_is_present_but_none_falls_back_to_its_default(mapper):
    line = dict(STAR_BATTER, SB=None)
    assert mapper._map_batter_stats(line, "CF").speed == BATTER_DEFAULTS["CF"].speed


def test_defense_comes_from_the_position_default_and_not_from_the_stats(mapper):
    short_stop = mapper._map_batter_stats(WEAK_BATTER, "SS").defense
    designated = mapper._map_batter_stats(WEAK_BATTER, "DH").defense
    assert (short_stop, designated) == (8, 2)


def test_more_than_120_games_adds_one_to_defense(mapper):
    assert mapper._map_batter_stats({"GP": 121}, "C").defense == 8


def test_exactly_120_games_does_not(mapper):
    assert mapper._map_batter_stats({"GP": 120}, "C").defense == 7


def test_an_unknown_position_falls_back_to_centre_fields_defense(mapper):
    assert mapper._map_batter_stats(WEAK_BATTER, "ZZ").defense == BATTER_DEFAULTS["CF"].defense


def test_a_pitchers_speed_and_control_span_the_scale(mapper):
    best = mapper._map_pitcher_stats(ACE, True)
    worst = mapper._map_pitcher_stats(WILD, True)
    assert (best.speed, best.control, worst.speed, worst.control) == (10, 10, 4, 4)


def test_a_starters_fatigue_is_on_the_cartridges_starter_scale(mapper):
    # Innings per start: 7 is the top, and a 5-inning starter is not left at
    # FAT 1 to tire after 7 pitches; the cartridge's starters run 5-10.
    ace = mapper._map_pitcher_stats(ACE, True).fatigue
    short = mapper._map_pitcher_stats(dict(ACE, IP=120.0), True).fatigue
    assert (ace, short) == (10, 5)


def test_a_relievers_fatigue_is_on_the_cartridges_reliever_scale(mapper):
    closer = mapper._map_pitcher_stats(CLOSER, False).fatigue
    long_man = mapper._map_pitcher_stats(LONG_MAN, False).fatigue
    assert (closer, long_man) == (2, 5)


def test_a_reliever_in_the_rotation_keeps_the_starter_fatigue_default(mapper):
    # Fewer than three starts says nothing about how long he lasts in one.
    assert mapper._map_pitcher_stats(CLOSER, True).fatigue == PITCHER_DEFAULTS["SP"].fatigue


def test_too_few_innings_keep_every_pitching_default(mapper):
    line = dict(ACE, IP=12.0)
    assert mapper._map_pitcher_stats(line, True) == PITCHER_DEFAULTS["SP"]


def test_a_missing_strikeout_count_falls_back_alone(mapper):
    line = {k: v for k, v in ACE.items() if k != "K"}
    attrs = mapper._map_pitcher_stats(line, True)
    assert (attrs.speed, attrs.control) == (PITCHER_DEFAULTS["SP"].speed, 10)


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


def test_a_mapped_batter_is_not_flagged_as_a_pitcher(mapper):
    assert mapper.map_batter(_player()).is_pitcher is False


def test_a_mapped_batter_keeps_its_normalised_position(mapper):
    assert mapper.map_batter(_player(position="1B")).position == "1B"


def test_a_batter_with_no_number_is_written_as_zero(mapper):
    assert mapper.map_batter(_player(number=None)).jersey_number == 0


def test_a_batter_with_stats_takes_the_batting_average_from_them(mapper):
    assert mapper.map_batter(_player(), STAR_BATTER).batting_avg == 320


def test_a_hitless_batter_shows_zero_and_not_the_placeholder(mapper):
    assert mapper.map_batter(_player(), dict(WEAK_BATTER, AVG=0.0)).batting_avg == 0


def test_a_batter_with_no_stats_takes_the_placeholder_average(mapper):
    assert mapper.map_batter(_player()).batting_avg == 250


def test_a_batter_with_stats_takes_the_home_run_total_from_them(mapper):
    assert mapper.map_batter(_player(), STAR_BATTER).home_runs == 45


def test_a_batter_with_stats_takes_the_rbi_total_from_them(mapper):
    assert mapper.map_batter(_player(), STAR_BATTER).rbi == 130


def test_a_batter_with_no_stats_has_no_home_runs(mapper):
    assert mapper.map_batter(_player()).home_runs == 0


def test_a_mapped_pitcher_is_flagged_as_one(mapper):
    assert mapper.map_pitcher(_player(position="SP")).is_pitcher is True


def test_a_mapped_pitchers_position_is_always_p(mapper):
    assert mapper.map_pitcher(_player(position="RP")).position == "P"


def test_a_pitcher_with_stats_takes_the_era_from_them(mapper):
    # 1.90 becomes 190: hundredths, not a float.
    assert mapper.map_pitcher(_player(), ACE).era == 190


def test_a_pitcher_with_no_stats_takes_the_placeholder_era(mapper):
    assert mapper.map_pitcher(_player()).era == 400


def test_a_pitcher_with_stats_takes_the_win_total_from_them(mapper):
    assert mapper.map_pitcher(_player(), ACE).wins == 18


def test_a_pitcher_with_stats_takes_the_save_total_from_them(mapper):
    assert mapper.map_pitcher(_player(), dict(ACE, SV=41)).saves == 41


def test_a_pitcher_with_stats_takes_the_loss_total_from_them(mapper):
    assert mapper.map_pitcher(_player(), ACE).losses == 5


@pytest.mark.parametrize("bats,code", [("R", 0), ("L", 1), ("B", 2), ("S", 2), ("", 0)])
def test_bats_is_right_left_or_switch(mapper, bats, code):
    assert mapper.map_batter(_player(bats=bats)).bats == code


@pytest.mark.parametrize("throws,code", [("R", 0), ("L", 1), ("B", 0), ("", 0)])
def test_throws_is_right_or_left_and_espns_both_is_right(mapper, throws, code):
    assert mapper.map_batter(_player(handedness=throws)).throws == code


def test_a_left_handed_hitter_who_throws_right_keeps_both(mapper):
    # The writer used to throw with the batting hand: a left-handed catcher.
    record = mapper.map_batter(_player(position="C", bats="L", handedness="R"))
    assert (record.bats, record.throws) == (1, 0)


def test_a_pitcher_carries_both_hands_too(mapper):
    record = mapper.map_pitcher(_player(position="SP", bats="R", handedness="L"))
    assert (record.bats, record.throws) == (0, 1)


def test_a_spare_pitcher_on_the_bench_bats_like_the_games_pitchers(mapper):
    # Not a 'P' with DEF 7: an IF with the game's own pitcher batting.
    record = mapper.map_batter(_player(position="RP"), dict(STAR_BATTER))
    attrs = record.batter_attrs
    assert (record.position, record.is_pitcher) == ("IF", False)
    assert (attrs.batting, attrs.power, attrs.speed, attrs.defense) == (2, 1, 3, 3)
    assert (record.batting_avg, record.home_runs, record.rbi) == (200, 1, 5)


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


def test_an_accent_becomes_its_plain_letter(mapper):
    assert mapper._split_name("Ronald Acuña Jr.") == ("R", "ACUNA")


def test_an_apostrophe_is_dropped(mapper):
    assert mapper._split_name("Ryan O'Hearn") == ("R", "OHEARN")


def test_a_hyphen_is_kept(mapper):
    assert mapper._split_name("Isiah Kiner-Falefa") == ("I", "KINER-FA")


def test_a_name_of_only_a_suffix_keeps_the_last_word(mapper):
    # Every part after the first is a suffix, so the fallback takes `parts[-1]`.
    assert mapper._split_name("Ken Jr.") == ("K", "JR.")


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


def test_the_1998_expansion_teams_take_the_all_star_slots(mapper):
    # Arizona the NL All-Stars' (28), under either abbreviation; Tampa Bay the AL's.
    assert [mapper.get_team_slot(code) for code in ("ARI", "AZ", "TB")] == [28, 28, 29]


def test_an_abbreviation_from_another_sport_has_no_slot(mapper):
    assert mapper.get_team_slot("ZZZ") is None


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


def test_with_no_lineup_or_stats_the_starters_bat_in_position_order(mapper):
    # One apiece of C, 1B, ... DH, then the rest: OPS ties keep that order.
    selected = mapper.select_roster(_squad())
    order = [p.position for p in selected[:9]]
    assert order == ["C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH"]


def test_the_starters_bat_in_the_clubs_recent_order(mapper):
    # The catcher does not lead off just because C comes first.
    squad = _squad()
    lineup = [{"id": i, "order": 9 - i} for i in range(9)]
    batters, _, _ = mapper.select_roster_groups(squad, {}, lineup)
    assert [p.position for p in batters[:9]] == [
        "DH",
        "RF",
        "CF",
        "LF",
        "SS",
        "3B",
        "2B",
        "1B",
        "C",
    ]


def test_a_starter_missing_from_the_recent_games_bats_by_his_ops(mapper):
    # The 2B (id 2) did not play lately; his .950 OPS is the best of the nine.
    squad = _squad()
    lineup = [{"id": i, "order": i + 1.0} for i in range(9) if i != 2]
    stats = {"2": {"OPS": 0.950}}
    batters, _, _ = mapper.select_roster_groups(squad, stats, lineup)
    assert [p.id for p in batters[:3]] == [0, 2, 1]


def test_an_eight_man_lineup_sits_the_dh(mapper):
    batters, _, _ = mapper.select_roster_groups(_squad(), {}, [], lineup_size=8)
    assert "DH" not in [p.position for p in batters[:8]]
    assert batters[8].position == "DH"


def _regulars(*positions):
    """A club's regulars, ids 0 on: 150 starts, a .750 OPS, all ten recent games."""
    players = [_player(id=i, name=f"Reg{i} X", position=pos) for i, pos in enumerate(positions)]
    stats = {str(i): {"GS": 150, "OPS": 0.750} for i in range(len(positions))}
    lineup = [{"id": i, "order": i + 1.0, "starts": 10} for i in range(len(positions))]
    return players, stats, lineup


def _starting(batters, size):
    return {p.id for p in batters[:size]}


def test_regulars_start_over_a_call_up_who_batted_lately(mapper):
    # The call-up (id 8) started the last ten games, worth 100 season starts:
    # still short of the 120 the third outfielder has, who started none of
    # them. ESPN lists two regulars at CF, so position labels alone would
    # start the call-up in RF.
    players, stats, lineup = _regulars("C", "1B", "2B", "3B", "SS", "LF", "CF", "CF")
    players.append(_player(id=8, name="Call Up", position="RF"))
    stats |= {"7": {"GS": 120, "OPS": 0.750}, "8": {"GS": 9, "OPS": 0.750}}
    lineup = [*lineup[:7], {"id": 8, "order": 2.0, "starts": 10}]
    batters, _, _ = mapper.select_roster_groups(players, stats, lineup, lineup_size=8)
    assert _starting(batters, 8) == set(range(8))


def test_a_regular_back_lately_starts_over_the_stand_in_with_more_season_starts(mapper):
    # The shortstop (id 7) missed time but started all ten recent games; the
    # third baseman who covered (id 8) has more season starts, six lately.
    players, stats, lineup = _regulars("C", "1B", "2B", "3B", "LF", "CF", "RF")
    players += [_player(id=7, name="Back Short", position="SS")]
    players += [_player(id=8, name="Stand In", position="3B")]
    stats |= {"7": {"GS": 104, "OPS": 0.700}, "8": {"GS": 124, "OPS": 0.700}}
    lineup += [{"id": 7, "order": 1.0, "starts": 10}, {"id": 8, "order": 6.0, "starts": 6}]
    batters, _, _ = mapper.select_roster_groups(players, stats, lineup, lineup_size=8)
    assert 7 in _starting(batters, 8)
    assert 8 not in _starting(batters, 8)


def test_an_nl_clubs_regular_dh_bats_and_the_weakest_bat_sits(mapper):
    # ESPN lists the club's most-used batter (id 7) at DH. Without a DH he
    # plays the field, and the part-time right fielder's weaker bat sits.
    players, stats, lineup = _regulars("C", "1B", "2B", "3B", "SS", "LF", "CF", "DH")
    players.append(_player(id=8, name="Part Timer", position="RF"))
    stats |= {"7": {"GS": 155, "OPS": 0.857}, "8": {"GS": 60, "OPS": 0.700}}
    batters, _, _ = mapper.select_roster_groups(players, stats, lineup, lineup_size=8)
    assert _starting(batters, 8) == set(range(8))
    assert batters[8].id == 8


def test_an_nl_lineup_sits_a_bat_and_never_the_catcher_or_an_infielder(mapper):
    # The catcher and the middle infield have the weakest bats; the
    # outfielder with the weakest of the rest sits.
    players, stats, lineup = _regulars("C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH")
    for i in (0, 2, 3, 4):
        stats[str(i)]["OPS"] = 0.500
    stats["6"]["OPS"] = 0.650
    batters, _, _ = mapper.select_roster_groups(players, stats, lineup, lineup_size=8)
    assert _starting(batters, 8) == set(range(9)) - {6}


def test_an_al_clubs_second_dh_plays_over_a_part_timer(mapper):
    # Two regulars ESPN lists at DH both start, one in the outfield, rather
    # than the part-time right fielder.
    players, stats, lineup = _regulars("C", "1B", "2B", "3B", "SS", "LF", "CF", "DH", "DH")
    players.append(_player(id=9, name="Part Timer", position="RF"))
    stats["9"] = {"GS": 37}
    batters, _, _ = mapper.select_roster_groups(players, stats, lineup, lineup_size=9)
    assert _starting(batters, 9) == set(range(9))


def test_the_bench_is_ranked_by_starts_then_plate_appearances(mapper):
    players = [
        _player(id=1, name="Few Starts", position="OF"),
        _player(id=2, name="Many Starts", position="OF"),
        _player(id=3, name="Same Starts More Pa", position="OF"),
    ]
    stats = {"1": {"GS": 10, "PA": 500}, "2": {"GS": 90, "PA": 300}, "3": {"GS": 10, "PA": 600}}
    selected = mapper.select_roster(players, stats)
    assert [p.id for p in selected] == [2, 3, 1]


def test_starters_are_ranked_by_wins_before_innings(mapper):
    players = [
        _player(id=1, name="Few Wins", position="SP"),
        _player(id=2, name="Many Wins", position="SP"),
    ]
    stats = {"1": {"W": 3, "IP": 210}, "2": {"W": 18, "IP": 120}}
    selected = mapper.select_roster(players, stats)
    assert [p.id for p in selected] == [2, 1]


def test_relievers_are_ranked_by_saves_then_innings(mapper):
    players = [
        _player(id=1, name="No Saves", position="RP"),
        _player(id=2, name="Closer Man", position="RP"),
        _player(id=3, name="Workhorse", position="RP"),
    ]
    stats = {"1": {"IP": 10.0}, "2": {"SV": 40, "IP": 60.0}, "3": {"IP": 80.0}}
    selected = mapper.select_roster(players, stats)
    assert [p.id for p in selected] == [2, 3, 1]


def test_the_dh_is_the_next_most_used_batter_but_not_the_backup_catcher(mapper):
    players, stats, lineup = _regulars("C", "1B", "2B", "3B", "SS", "LF", "CF", "RF")
    players += [_player(id=8, name="Backup Catcher", position="C")]
    players += [_player(id=9, name="Bench Bat", position="OF")]
    stats |= {"8": {"GS": 100}, "9": {"GS": 50}}
    batters, _, _ = mapper.select_roster_groups(players, stats, lineup, lineup_size=9)
    assert _starting(batters, 9) == set(range(8)) | {9}
    assert batters[9].id == 8


def test_two_batters_get_two_looks_and_keep_their_id_and_photo(mapper):
    a = mapper.map_batter(_player(id=101, photo_url="https://x/101.png"))
    b = mapper.map_batter(_player(id=102))
    assert (a.player_id, a.photo_url, b.photo_url) == (101, "https://x/101.png", "")
    assert a.batter_appearance != b.batter_appearance


def test_a_batters_build_comes_from_his_height_and_weight(mapper):
    small = mapper.map_batter(_player(height=70.0, weight=175.0))
    big = mapper.map_batter(_player(height=73.0, weight=250.0))
    assert (small.batter_appearance.body, small.batter_appearance.legs_size) == (3, 1)
    assert big.batter_appearance.body == 2


def test_a_pitcher_bats_with_his_mound_skin_and_hair(mapper):
    for pid in range(30):
        record = mapper.map_pitcher(_player(id=pid, position="SP"))
        bat, mound = record.batter_appearance, record.pitcher_appearance
        assert (bat.skin, bat.hair_color) == (mound.skin, mound.hair_color)
