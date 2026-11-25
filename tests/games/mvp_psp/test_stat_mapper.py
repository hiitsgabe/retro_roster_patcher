"""`MVPStatMapper`: ESPN rosters and season lines onto where each player ranks."""

from __future__ import annotations

import pytest

from retro_roster_patcher.games.mvp_psp.models import (
    BATTERS_PER_TEAM,
    MVP_ABBREV_TO_INDEX,
    PLAYERS_PER_TEAM,
    RELIEVER_ARSENAL,
    RELIEVERS_PER_TEAM,
    SECONDARY_DH,
    STARTER_ARSENAL,
    STARTERS_PER_TEAM,
    MVPPlayerRecord,
)
from retro_roster_patcher.games.mvp_psp.stat_mapper import (
    BATS_LEFT,
    BATS_RIGHT,
    BATS_SWITCH,
    DEFAULT_POSITION,
    PITCHER_POSITIONS,
    RATINGS,
    THROWS_LEFT,
    THROWS_RIGHT,
    UNNAMED,
    LeagueStats,
    MVPStatMapper,
    _innings,
    _stat,
    batter_stats,
    batting_side,
    secondary_position,
)
from retro_roster_patcher.sports.models import Player

MAPPER = MVPStatMapper()


def player(pid=1, name="Ichiro Suzuki", position="RF", number=51, **kwargs):
    return Player(id=pid, name=name, position=position, number=number, **kwargs)


# Everything below reads a rating back through the constant that produced it, so a
# whole table of them can be changed together and nothing notices. These are the
# source's numbers, stated once more so a change to one is a change to a test.


def test_a_player_with_no_name_is_called_player():
    assert UNNAMED == "Player"


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


def test_an_unrecognised_position_falls_back_to_centre_field():
    assert DEFAULT_POSITION == "CF"


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


def test_a_pitcher_with_no_stats_is_a_pitcher():
    assert MAPPER.map_pitcher(player(position="SP")).is_pitcher is True


def test_a_starter_is_positioned_as_a_starting_pitcher():
    assert MAPPER.map_pitcher(player(position="SP"), is_starter=True).primary_position == "SP"


def test_a_reliever_is_positioned_as_a_relief_pitcher():
    assert MAPPER.map_pitcher(player(position="RP"), is_starter=False).primary_position == "RP"


# Both tuples are read back through themselves everywhere, so exchanging the
# two entries of either -- turning a pitcher who makes contact more often than
# he hits for power into the reverse -- changed nothing anywhere. These are the
# four numbers, and which of each pair is the contact one.


# Each pitch's three numbers, in full, at one velocity and one control. The tests
# around this one compare a pitch with its neighbour, which leaves the fastball's
# `velocity // 2` movement and the changeup's fifteen-point velocity drop unstated.


def test_a_batter_and_a_pitcher_carry_the_provider_weight_and_height():
    assert MAPPER.map_batter(player(weight=215.0)).weight == 215
    pitcher = MAPPER.map_pitcher(player(position="SP", weight=201.0))
    assert pitcher.weight == 201


def test_without_a_provider_weight_the_record_default_stays():
    assert MAPPER.map_batter(player(weight=0.0)).weight == 190


def test_a_player_the_provider_has_no_weight_for_gets_the_same_default():
    assert MAPPER.map_batter(player()).weight == 190


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


# Ranks against the league.


def batter_line(**kw):
    """A regular's season: 600 PA, league-average everything unless given."""
    line = {
        "PA": 600,
        "AB": 540,
        "H": 140,
        "2B": 28,
        "3B": 2,
        "HR": 18,
        "BB": 50,
        "HBP": 5,
        "SO": 130,
        "SB": 8,
        "CS": 3,
        "SH": 1,
        "R": 75,
        "GP": 140,
        "GO": 160,
        "FO": 150,
        "BIPA": 0.29,
        "WAR": 2.0,
    }
    return line | kw


def league_of(*members):
    return LeagueStats.from_lines(members)


def test_the_league_leader_in_steals_ranks_top_in_speed_and_stealing():
    fast, slow = player(1, "A Fast"), player(2, "B Slow")
    lines = {1: batter_line(SB=45, CS=5), 2: batter_line(SB=0, CS=1)}
    others = [(player(10 + i, "C Other"), batter_line(SB=5 + i), False) for i in range(8)]
    league = league_of((fast, lines[1], False), (slow, lines[2], False), *others)
    top = MAPPER.map_batter(fast, lines[1], league).ratings
    bottom = MAPPER.map_batter(slow, lines[2], league).ratings
    assert top["speed"] > 0.9 and top["stealing"] > 0.9
    assert bottom["speed"] < 0.1 and bottom["stealing"] < 0.1


def test_a_split_ranks_contact_on_its_own_side():
    hitter = player(1, "A Platoon", bats="L")
    line = batter_line(**{"vsL.AB": 150, "vsL.H": 30, "vsR.AB": 390, "vsR.H": 130})
    for k in ("2B", "3B", "HR", "BB", "HBP", "SO"):
        line |= {f"vsL.{k}": 0, f"vsR.{k}": 0}
    others = [
        (player(10 + i, "C Other", bats="L"), batter_line(H=120 + 5 * i), False) for i in range(8)
    ]
    league = league_of((hitter, line, False), *others)
    ratings = MAPPER.map_batter(hitter, line, league).ratings
    assert ratings["contact_rhp"] > ratings["contact_lhp"]


def test_a_batter_without_splits_rates_his_season_on_both_sides():
    stats = batter_stats(batter_line())
    assert stats["avg_lhp"] == stats["avg_rhp"]


def test_a_partial_line_rates_only_what_it_has():
    """ESPN's leaders list a player under the categories he leads: a line
    without games played leaves durability to the position default, not 0."""
    ratings = MAPPER.map_batter(player(), {"HR": 30.0, "AVG": 0.3}).ratings
    assert "durability" not in ratings
    assert "speed" not in ratings


def test_a_player_without_a_line_has_no_ratings():
    assert MAPPER.map_batter(player()).ratings == {}
    assert MAPPER.map_pitcher(player(position="SP")).ratings == {}


def test_fielding_ranks_against_his_own_position():
    fielding = {"fielding.putouts": 100, "fielding.assists": 300, "fielding.outsOnField": 3000}
    good = player(1, "A Good", position="SS")
    poor = player(2, "B Poor", position="SS")
    first = player(3, "C First", position="1B")
    lines = {
        1: batter_line(**fielding, **{"fielding.errors": 2}),
        2: batter_line(**fielding, **{"fielding.errors": 30}),
        3: batter_line(**fielding, **{"fielding.errors": 40}),
    }
    league = league_of((good, lines[1], False), (poor, lines[2], False), (first, lines[3], False))
    assert MAPPER.map_batter(good, lines[1], league).ratings["fielding"] > 0.5
    # Alone at first base, he is his position's middle, whatever the shortstops did.
    assert MAPPER.map_batter(first, lines[3], league).ratings["throw_accuracy"] == 0.5


def test_a_strikeout_pitcher_ranks_top_in_velocity_and_a_wild_one_bottom_in_control():
    def line(k, bb):
        return {"TBF": 700, "IP": 170.0, "K": k, "BB": bb, "H": 150, "ER": 70, "GS": 30, "GP": 30}

    ace, wild = player(1, "A Ace", position="SP"), player(2, "B Wild", position="SP")
    others = [(player(10 + i, "C Other", position="SP"), line(150 + i, 50), True) for i in range(6)]
    league = league_of((ace, line(260, 40), True), (wild, line(150, 110), True), *others)
    assert MAPPER.map_pitcher(ace, line(260, 40), league=league).ratings["pitch_velocity"] > 0.9
    assert MAPPER.map_pitcher(wild, line(150, 110), league=league).ratings["pitch_control"] < 0.1


def test_espn_writes_two_thirds_of_an_inning_as_point_two():
    assert _innings(162.2) == pytest.approx(162 + 2 / 3)


def test_every_rating_a_record_holds_is_a_known_one():
    ratings = MAPPER.map_batter(player(), batter_line()).ratings
    assert set(ratings) <= set(RATINGS)
    assert all(0 <= share <= 1 for share in ratings.values())


# The rest of the record.


def test_a_starter_throws_the_disc_starters_arsenal_and_a_reliever_the_relievers():
    assert MAPPER.map_pitcher(player(position="SP")).pitch_types == STARTER_ARSENAL
    reliever = MAPPER.map_pitcher(player(position="RP"), is_starter=False)
    assert reliever.pitch_types == RELIEVER_ARSENAL


def test_a_pitchers_second_position_is_the_other_role():
    assert MAPPER.map_pitcher(player(position="SP")).secondary_position == 10
    assert MAPPER.map_pitcher(player(position="RP"), is_starter=False).secondary_position == 0


def test_a_batters_second_position_is_where_else_he_batted_most():
    line = {"pos.SS": 500, "pos.2B": 40, "pos.3B": 60, "pos.DH": 80}
    assert secondary_position(line, "SS") == 4  # third base


def test_a_batter_who_only_also_dhd_is_a_designated_hitter_second():
    assert secondary_position({"pos.RF": 400, "pos.DH": 100}, "RF") == SECONDARY_DH


def test_a_batter_with_no_other_position_has_no_second_one():
    assert secondary_position({"pos.C": 300}, "C") is None
    assert secondary_position({}, "C") is None


def test_a_switch_hitter_bats_right_against_a_left_hander():
    assert batting_side(BATS_SWITCH, "lhp") == "R"
    assert batting_side(BATS_SWITCH, "rhp") == "L"
    assert batting_side(BATS_LEFT, "lhp") == "L"


def test_a_batter_carries_his_birth_date():
    assert MAPPER.map_batter(player(birth_date="1996-11-26")).birth_date == "1996-11-26"


def test_the_record_type_is_the_models_one():
    assert isinstance(MAPPER.map_batter(player()), MVPPlayerRecord)


def test_a_player_who_throws_with_both_hands_throws_right_where_he_must():
    # The game has no code for both. Carlos Cortes bats left and throws left
    # in the outfield; an infielder or a pitcher who throws with both is
    # right-handed.
    assert MAPPER.map_throw_hand("B", "L", "RF") == THROWS_LEFT
    assert MAPPER.map_throw_hand("B", "L", "2B") == THROWS_RIGHT
    assert MAPPER.map_throw_hand("B", "B", "RF") == THROWS_RIGHT
    assert MAPPER.map_throw_hand("B") == THROWS_RIGHT
    cortes = player(position="RF", bats="L", handedness="B")
    assert MAPPER.map_batter(cortes).throws == THROWS_LEFT
    switch = player(position="SP", bats="L", handedness="B")
    assert MAPPER.map_pitcher(switch).throws == THROWS_RIGHT


def test_a_record_carries_its_season_line():
    line = batter_line()
    assert MAPPER.map_batter(player(), line).line == line
    assert MAPPER.map_batter(player()).line == {}
