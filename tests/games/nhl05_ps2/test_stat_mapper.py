"""`NHL05StatMapper`: provider data in, NHL 2005 attribute records out.

The ratings rank through `core.hockey_ratings` (tested there); this file pins
this game's policy against its retail disc -- every SPAI and SGAI field named,
every spread the disc's own -- plus the two places NHL 07's sameness is a trap:

  * **the 15-character name limit**, which is NHL 07's 19 minus four, and
  * **`get_team_slot`'s 24/25 swap**, where `SJ` and `STL` change places.
"""

from __future__ import annotations

import collections

import pytest

from retro_roster_patcher.core.hockey_ratings import COUNTS, League
from retro_roster_patcher.games.nhl05_ps2.models import (
    GOALIE_FIELDS,
    MODERN_NHL_TO_NHL05,
    NAME_FIELD_CHARS,
    NHL05_TEAM_INDEX,
    SKATER_FIELDS,
    NHL05PlayerRecord,
)
from retro_roster_patcher.games.nhl05_ps2.rom_writer import LINE_FLAGS
from retro_roster_patcher.games.nhl05_ps2.stat_mapper import (
    DEFAULT_HEIGHT,
    DEFENCE_PAIRS,
    FORWARD_LINES,
    GOALIE_DEFAULTS,
    GOALIES_PER_TEAM,
    MAX_PLAYERS,
    SKATER_DEFAULTS,
    SOURCES,
    SPREAD,
    NHL05StatMapper,
    _defaults_for,
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


def test_every_position_has_defaults():
    assert sorted(SKATER_DEFAULTS) == ["C", "D", "LW", "RW"]


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
    before = SKATER_DEFAULTS["C"].faceoffs
    fresh = _defaults_for("C")
    fresh.faceoffs = before + 1
    assert SKATER_DEFAULTS["C"].faceoffs == before


def test_a_goalie_without_stats_gets_a_copy_of_the_goalie_defaults():
    record = MAPPER.map_player(player(position="G"), "COL")
    assert record.goalie_attrs is not GOALIE_DEFAULTS


def test_a_goalie_without_stats_gets_the_goalie_defaults_values():
    record = MAPPER.map_player(player(position="G"), "COL")
    assert record.goalie_attrs == GOALIE_DEFAULTS


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


def test_the_weight_is_the_providers_pounds_over_150():
    assert MAPPER.map_player(player(weight=212), "COL").weight == 62


def test_a_player_without_a_weight_gets_the_league_average():
    assert MAPPER.map_player(player(weight=0), "COL").weight == 40


def test_every_mapped_player_gets_the_same_constant_height():
    # Without a provider height, every player gets the 6'0" default.
    heights = [
        MAPPER.map_player(player(id=1, position="D"), "COL").height,
        MAPPER.map_player(player(id=2, position="G"), "BOS").height,
    ]
    assert heights == [DEFAULT_HEIGHT, DEFAULT_HEIGHT]


def test_a_reported_height_is_inches_over_54():
    assert MAPPER.map_player(player(height=73.0), "COL").height == 19


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


def test_a_file_that_mixes_the_two_conventions_starts_the_worse_goalie():
    # PINS UPSTREAM FIDELITY DELIBERATELY. `select_roster`'s goalie key reads
    # `SV%` raw as well, so `91.2` scores 91 200 against `0.930`'s 930 and the
    # worse of the two is put in net.
    goalies = [player(id=1, position="G"), player(id=2, position="G")]
    stats = {"1": {"SV%": 91.2}, "2": {"SV%": 0.930}}
    assert [p.id for p in MAPPER.select_roster(goalies, stats)] == [1, 2]


def test_within_one_convention_the_better_goalie_still_starts():
    # Which is why nothing caught the line above.
    goalies = [player(id=1, position="G"), player(id=2, position="G")]
    stats = {"1": {"SV%": 0.880}, "2": {"SV%": 0.930}}
    assert [p.id for p in MAPPER.select_roster(goalies, stats)] == [2, 1]


def squad(counts: dict[str, int]) -> list[Player]:
    """One player per requested position, ids running from 1."""
    out: list[Player] = []
    for pos, n in counts.items():
        for i in range(n):
            out.append(player(id=len(out) + 1, name=f"{pos}{i} Last", position=pos))
    return out


def test_a_full_roster_is_capped_at_the_maximum():
    players = squad({"C": 10, "LW": 10, "RW": 10, "D": 12, "G": 4})
    assert len(MAPPER.select_roster(players, {})) == MAX_PLAYERS


def test_the_default_maximum_is_the_discs_largest_roster_of_forty():
    assert MAX_PLAYERS == 40


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
    assert [f for d in flags for f in d if f[0] == "L" and f.endswith("C_")] == ["L1C_", "L2C_"]


def test_a_spare_centre_is_moved_to_the_wing_when_the_wingers_run_out():
    flags = MAPPER.generate_team_line_flags(records({"C": 3}))
    assert flags[1]["L1LW"] == 1


def test_a_winger_is_never_moved_to_centre():
    # Only `c_pool` is drawn on twice, so a team with no centres ices four lines
    # with no centre at all.
    flags = MAPPER.generate_team_line_flags(records({"LW": 4}))
    assert [f for d in flags for f in d if f.endswith("C_")] == []


def test_the_first_defence_pair_is_the_even_strength_pair_one():
    # `L1LD`/`L1RD`, where the retail NHL 07 disc (same ROST schema) starts its
    # first pair. `31LD` is the five-on-three unit.
    flags = MAPPER.generate_team_line_flags(records({"D": 2}))
    assert [flags[0].get("L1LD"), flags[1].get("L1RD")] == [1, 1]


def test_the_second_and_third_pairs_follow():
    flags = MAPPER.generate_team_line_flags(records({"D": 6}))
    assert [f for d in flags[2:] for f in d if f[0] == "L"] == ["L2LD", "L2RD", "L3LD", "L3RD"]


def test_every_flag_a_full_team_gets_is_one_the_game_has():
    # The writer drops a name the game lacks, so a misspelt flag never lands.
    team = records({"G": 2, "C": 4, "LW": 4, "RW": 4, "D": 6})
    flags = MAPPER.generate_team_line_flags(team)
    assert [f for d in flags for f in d if f not in LINE_FLAGS] == []


def test_the_first_two_goalies_twelve_forwards_and_six_defencemen_dress():
    team = records({"G": 3, "D": 7, "C": 13})
    expected = [True] * 2 + [False] + [True] * 6 + [False] + [True] * 12 + [False]
    assert MAPPER.dressed(team) == expected


def test_a_scratch_is_never_on_a_line():
    # Thirteen forwards in list order: the fourth right wing is the 13th and a
    # scratch, so the spare fifth centre takes his wing.
    team = records({"C": 5, "LW": 4, "RW": 4})
    flags = MAPPER.generate_team_line_flags(team)
    assert (flags[12], flags[4]) == ({}, {"L4RW": 1})


def test_three_defence_pairs_are_generated():
    assert DEFENCE_PAIRS == 3


def test_a_seventh_defenceman_gets_no_pair():
    flags = MAPPER.generate_team_line_flags(records({"D": 7}))
    assert [f for f in flags[6] if f.endswith(("LD", "RD"))] == []


def test_a_full_team_gets_every_unit_the_retail_nhl07_disc_builds_from_its_lines():
    # Line n is C, LW, RW at 3n-3, the pairs start at 12. The same ROST schema
    # as the retail NHL 07 disc, whose Boston LW1 also holds 41LW, P1LW, H1__,
    # S1__ and X1__.
    team = [
        NHL05PlayerRecord(position=p, is_goalie=False) for p in ["C", "LW", "RW"] * 4 + ["D"] * 6
    ]
    flags = MAPPER.generate_team_line_flags(team)
    assert [sorted(d) for d in flags] == [
        ["41C_", "H2__", "L1C_", "P1C_", "S3__"],
        ["41LW", "H1__", "L1LW", "P1LW", "S1__", "X1__"],
        ["H3__", "L1RW", "P1RW", "S2__"],
        ["32C_", "42C_", "L2C_", "P2C_"],
        ["42LW", "L2LW", "P2LW", "S4__", "X2__"],
        ["L2RW", "P2RW", "S5__"],
        ["K1C_", "L3C_"],
        ["K1LW", "L3LW"],
        ["L3RW"],
        ["31C_", "K2C_", "L4C_"],
        ["K2LW", "L4LW"],
        ["L4RW"],
        ["32LD", "H4__", "K2LD", "L1LD", "P1LD"],
        ["32RD", "H5__", "K2RD", "L1RD", "P1RD"],
        ["31LD", "41LD", "L2LD", "P2LD"],
        ["31RD", "41RD", "L2RD", "P2RD"],
        ["42LD", "K1LD", "L3LD"],
        ["42RD", "K1RD", "L3RD"],
    ]


def test_a_missing_role_is_skipped_and_the_later_slots_move_up():
    # One centre and two left wings: no right wing on either line, so H and S
    # close up over L1RW (and S over L2RW).
    flags = MAPPER.generate_team_line_flags(records({"C": 1, "LW": 2, "D": 2}))
    held = {f: i for i, d in enumerate(flags) for f in d}
    assert [held.get(f"H{k}__") for k in range(1, 6)] == [1, 0, 3, 4, None]
    assert [held.get(f"S{k}__") for k in range(1, 6)] == [1, 0, 2, None, None]


def test_a_team_short_of_defencemen_ices_a_four_man_h_unit():
    flags = MAPPER.generate_team_line_flags(records({"C": 1, "LW": 1, "RW": 1, "D": 1}))
    assert sum(1 for d in flags for f in d if f.startswith("H")) == 4


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


@pytest.mark.parametrize(
    ("nationality", "state", "flag", "place"),
    [("FIN", "", 18, 18), ("CAN", "ON", 85, 56), ("USA", "AZ", 86, 86), ("XYZ", "MN", -1, -1)],
)
def test_birth_fields_take_the_games_place_codes(nationality, state, flag, place):
    # Arizona is not in the game's list, so the state falls back to the country;
    # a country it lacks leaves the disc's own values.
    p = player(
        nationality=nationality,
        birth_state=state,
        birth_city="Helsinki",
        age=30,
        birth_date="1995-02-06",
    )
    record = MAPPER.map_player(p, "ANA", {})
    assert (record.country, record.state) == (flag, place)
    assert (record.city, record.birth_year, record.birth_month, record.birth_day) == (
        "Helsinki",
        15,  # 2004 - 30 - 1959: the game counts ages from 2004
        2,
        6,
    )


def test_ice_time_not_a_mixed_season_points_total_orders_the_centres():
    # Twelve games of this season against a full previous one: the star has
    # fewer points but plays more a night, and centres the first line.
    star = player(id=1, name="Connor McDavid", position="C")
    depth = player(id=2, name="Ryan Nugent-Hopkins", position="C")
    stats = {
        "1": {"GP": 12, "PTS": 34, "timeOnIcePerGame": 1432.8},
        "2": {"GP": 72, "PTS": 56, "timeOnIcePerGame": 1143.8},
    }
    assert [p.id for p in MAPPER.select_roster([depth, star], stats)] == [1, 2]


#: The retail disc's SPAI and SGAI fields, in its order (SLUS-21001).
RETAIL_SPAI = (
    "BALA PENA SACC WACC FACE ACCE SPEE POTE DEKG CHKG TOUG FIGH PCBI ODBI SPBI PUCK AGIL "
    "HERO AGGR PRES PASS ENDU INJU SPOW WPOW INDX"
).split()
RETAIL_SGAI = (
    "BRKA REBC SREC SPEE POKE INTE POTE TOUG FIGH POSI PADL AGIL 5HOL STYL FLOP CONS PASS "
    "ENDU INDX GSH_ SSH_ GSL_ SSL_"
).split()
STATS = set(COUNTS) | {"age", "size", "height", "save", "goals_against", "games"}
STATS |= {"ice_time", "faceoff_pct"}


def test_every_retail_ratings_field_but_the_key_is_named():
    assert sorted(SKATER_FIELDS.values()) == sorted(f for f in RETAIL_SPAI if f != "INDX")
    assert sorted(GOALIE_FIELDS.values()) == sorted(f for f in RETAIL_SGAI if f != "INDX")


@pytest.mark.parametrize("group", ["F", "D", "G"])
def test_every_rating_has_the_discs_spread_on_0_to_49(group):
    # The 0-63 scale the writer used to fill: the disc's NHL players top out at 49.
    fields = GOALIE_FIELDS if group == "G" else SKATER_FIELDS
    assert set(SPREAD[group]) == set(fields)
    assert set(SOURCES[group]) <= set(fields)
    for quantiles in SPREAD[group].values():
        assert len(quantiles) == 21 and list(quantiles) == sorted(quantiles)
        assert 0 <= quantiles[0] and quantiles[-1] <= 49
    for names in SOURCES[group].values():
        assert {name.lstrip("-") for name in names} <= STATS


def test_a_centre_has_the_best_default_faceoffs_and_a_defenceman_the_worst():
    by_faceoffs = sorted(SKATER_DEFAULTS, key=lambda pos: SKATER_DEFAULTS[pos].faceoffs)
    assert (by_faceoffs[0], by_faceoffs[-1]) == ("D", "C")


def test_the_leagues_best_and_worst_take_the_ends_of_the_discs_spread():
    lines = [
        (
            player(id=i, name=f"Forward {i}", age=36 - i // 2),
            {"GP": 82 - i, "G": i, "A": 2 * i, "PTS": 3 * i, "S": 80 + 5 * i, "TOI": 700 + 15 * i},
        )
        for i in range(41)
    ]
    league = League.from_lines(lines, SOURCES)
    best = MAPPER.map_player(lines[-1][0], "COL", lines[-1][1], league).skater_attrs
    worst = MAPPER.map_player(lines[0][0], "COL", lines[0][1], league).skater_attrs
    # On this disc potential is youth, and injury is games missed.
    for rating in ("passing", "shot_accuracy", "endurance", "potential", "injury"):
        assert getattr(best, rating) == SPREAD["F"][rating][-1], rating
        assert getattr(worst, rating) == SPREAD["F"][rating][0], rating


def test_a_rating_the_line_has_no_stat_for_keeps_the_position_default():
    attrs = MAPPER.map_player(player(), "COL", {"PIM": 120}).skater_attrs
    assert attrs.passing == SKATER_DEFAULTS["C"].passing
    assert attrs.aggression == SKATER_DEFAULTS["C"].aggression


def test_a_busy_goalie_with_a_good_save_rate_outrates_a_backup_and_both_play_butterfly():
    goalies = [
        (
            player(id=i, name=f"Goalie {i}", position="G"),
            {"GP": 10 + 2 * i, "W": i, "SV%": 0.880 + i / 1000, "GAA": 3.4 - i / 30},
        )
        for i in range(30)
    ]
    league = League.from_lines(goalies, SOURCES)
    starter = MAPPER.map_player(goalies[-1][0], "COL", goalies[-1][1], league).goalie_attrs
    backup = MAPPER.map_player(goalies[0][0], "COL", goalies[0][1], league).goalie_attrs
    for rating in ("glove_high", "stick_low", "five_hole", "rebound_ctrl", "endurance"):
        assert getattr(starter, rating) > getattr(backup, rating), rating
    assert starter.style == backup.style == 2
