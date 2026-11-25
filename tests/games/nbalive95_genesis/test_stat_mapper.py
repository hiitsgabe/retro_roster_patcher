"""The NBA Live 95 stat mapper.

Each rating ranks one to three of a player's stats against his group (guards,
forwards, centres) in the league fetched and takes the value at that rank of the
cartridge's own spread (`RETAIL`), so the expectations below are read off
`RETAIL`'s deciles: the league's best is the cartridge's best, its middle the
cartridge's median.
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
    RETAIL,
    LeagueStats,
    NBALive95StatMapper,
    on_retail,
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


def _league(lines, position="SF"):
    """Players 0..n-1 of one position, each with his line."""
    players = [_player(id=i, position=position) for i in range(len(lines))]
    return players, LeagueStats.from_lines(zip(players, lines, strict=True))


def test_the_bottom_of_the_league_gets_the_cartridges_lowest():
    assert on_retail(0.0, RETAIL["G"]["passing"]) == RETAIL["G"]["passing"][0]


def test_the_top_of_the_league_gets_the_cartridges_highest():
    assert on_retail(1.0, RETAIL["G"]["passing"]) == 98


def test_the_middle_of_the_league_gets_the_cartridges_median():
    assert on_retail(0.5, (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)) == 50


def test_a_share_between_two_deciles_is_read_between_them():
    assert on_retail(0.55, (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)) == 55


def test_passing_ranks_assists_onto_the_cartridges_spread(mapper):
    players, league = _league([{"AST": a} for a in (1.0, 3.0, 5.0, 7.0, 9.0)])
    found = [
        mapper.map_player(p, {"AST": a}, league).ratings[PASSING]
        for p, a in zip(players, (1.0, 3.0, 5.0, 7.0, 9.0), strict=True)
    ]
    assert found == sorted(found)
    assert found[-1] > RETAIL["F"]["passing"][5] > found[0]


def test_a_rating_ranks_only_against_its_own_group(mapper):
    """The best-blocking guard is the guards' best even beside centres who block more."""
    guards = [_player(id=i, position="PG") for i in range(3)]
    centres = [_player(id=10 + i, position="C") for i in range(3)]
    lines = [{"BLK": b} for b in (0.1, 0.2, 0.9)] + [{"BLK": b} for b in (1.5, 2.0, 3.0)]
    league = LeagueStats.from_lines(zip(guards + centres, lines, strict=True))
    best_guard = mapper.map_player(guards[2], lines[2], league).ratings[BLOCKS]
    worst_centre = mapper.map_player(centres[0], lines[3], league).ratings[BLOCKS]
    assert best_guard > RETAIL["G"]["blocks"][5]
    assert worst_centre < RETAIL["C"]["blocks"][5]


def test_a_stat_missing_from_a_partial_line_keeps_the_position_default(mapper):
    """ESPN's leaders list a player only under what he leads: no FT% is not a 0."""
    players, league = _league([{"PTS": 30.0}, {"PTS": 10.0, "FT%": 80.0}])
    record = mapper.map_player(players[0], {"PTS": 30.0}, league)
    assert record.ratings[FT] == POSITION_DEFAULTS[POSITION_SF][FT]
    assert record.ratings[OFF_AWARENESS] > POSITION_DEFAULTS[POSITION_SF][OFF_AWARENESS]


def test_a_line_of_nones_keeps_every_default_rather_than_raising(mapper):
    assert (
        _ratings(mapper, {"PTS": None, "AST": None, "FG%": None}) == POSITION_DEFAULTS[POSITION_SF]
    )


def test_a_few_big_games_do_not_outrank_a_full_season(mapper):
    """Per-game counts are pulled toward the group's rate by 20 games."""
    lines = [
        {"gamesPlayed": 2, "AST": 12.0},
        {"gamesPlayed": 70, "AST": 9.0},
        {"gamesPlayed": 70, "AST": 2.0},
    ]
    players, league = _league(lines)
    cameo, regular, _ = (
        mapper.map_player(p, line, league).ratings[PASSING]
        for p, line in zip(players, lines, strict=True)
    )
    assert regular > cameo


def test_a_percentage_is_read_from_makes_and_attempts_when_the_line_has_them(mapper):
    """1 of 1 is no 100% shooter: the group's rate weighs in by 20 free throws."""
    lines = [
        {"freeThrowsMade": 1, "freeThrowsAttempted": 1},
        {"freeThrowsMade": 450, "freeThrowsAttempted": 500},
        {"freeThrowsMade": 300, "freeThrowsAttempted": 500},
    ]
    players, league = _league(lines)
    lucky, shooter, _ = (
        mapper.map_player(p, line, league).ratings[FT]
        for p, line in zip(players, lines, strict=True)
    )
    assert shooter > lucky


def test_a_percentage_is_read_as_hundredths_without_the_counts(mapper):
    lines = [{"FG%": 40.0}, {"FG%": 55.0}]
    players, league = _league(lines)
    low, high = (
        mapper.map_player(p, line, league).ratings[GOALS]
        for p, line in zip(players, lines, strict=True)
    )
    assert high > low


def test_speed_ranks_lightness_and_assists(mapper):
    players = [
        _player(id=0, position="SF", weight=190.0),
        _player(id=1, position="SF", weight=250.0),
    ]
    lines = [{"AST": 6.0}, {"AST": 1.0}]
    league = LeagueStats.from_lines(zip(players, lines, strict=True))
    quick, slow = (
        mapper.map_player(p, line, league).ratings[SPEED]
        for p, line in zip(players, lines, strict=True)
    )
    # Of two, the better ranks at 3/4 and the other at 1/4 (ties sit at their middle).
    assert (quick, slow) == (
        on_retail(0.75, RETAIL["F"]["speed"]),
        on_retail(0.25, RETAIL["F"]["speed"]),
    )


def test_strength_ranks_rebounds_and_weight(mapper):
    players = [_player(id=0, weight=190.0), _player(id=1, weight=250.0)]
    lines = [{"REB": 2.0}, {"REB": 10.0}]
    league = LeagueStats.from_lines(zip(players, lines, strict=True))
    light, heavy = (
        mapper.map_player(p, line, league).ratings[STRENGTH]
        for p, line in zip(players, lines, strict=True)
    )
    assert heavy > light


def test_stamina_ranks_minutes_and_range_three_point_attempts(mapper):
    lines = [{"MPG": 36.0, "3PA": 8.0}, {"MPG": 12.0, "3PA": 0.5}]
    players, league = _league(lines, position="PG")
    workhorse, cameo = (
        mapper.map_player(p, line, league) for p, line in zip(players, lines, strict=True)
    )
    stamina = RETAIL["G"]["stamina"]
    assert (workhorse.stamina, cameo.stamina) == (
        on_retail(0.75, stamina),
        on_retail(0.25, stamina),
    )
    assert (workhorse.shooting_range, cameo.shooting_range) == (14, 12)


def test_a_full_line_above_the_rest_of_the_league_rates_above_the_median_everywhere(mapper):
    line = {
        "gamesPlayed": 70,
        "PTS": 28.4,
        "REB": 9.1,
        "AST": 7.2,
        "STL": 1.9,
        "BLK": 1.4,
        "FG%": 52.1,
        "3P%": 39.4,
        "FT%": 88.0,
        "ORPG": 2.2,
        "DRPG": 6.9,
        "3PM": 3.1,
        "3PA": 7.0,
        "FTA": 6.0,
        "MPG": 36.5,
        "TO": 3.0,
    }
    weak = {key: value / 4 for key, value in line.items()} | {"gamesPlayed": 70}
    players, league = _league([line, weak])
    record = mapper.map_player(players[0], line, league)
    assert [r > d for r, d in zip(record.ratings, POSITION_DEFAULTS[POSITION_SF], strict=True)] == [
        True
    ] * 16
    assert record.stamina > RETAIL["F"]["stamina"][5]
    assert record.shooting_range > RETAIL["F"]["range"][5]


def test_a_player_with_no_stats_gets_his_groups_median(mapper):
    assert _ratings(mapper, {}) == [
        RETAIL["F"][name][5] for name in RETAIL["F"] if name not in ("stamina", "range")
    ]
    record = mapper.map_player(_player(position="C"), None)
    assert (record.stamina, record.shooting_range) == (
        RETAIL["C"]["stamina"][5],
        RETAIL["C"]["range"][5],
    )


def test_guards_forwards_and_centres_default_differently(mapper):
    rows = [_ratings(mapper, {}, position=name) for name in ("PG", "SG", "SF", "PF", "C")]
    assert rows == [
        POSITION_DEFAULTS[POSITION_PG],
        POSITION_DEFAULTS[POSITION_SG],
        POSITION_DEFAULTS[POSITION_SF],
        POSITION_DEFAULTS[POSITION_PF],
        POSITION_DEFAULTS[POSITION_C],
    ]
    assert len({tuple(row) for row in POSITION_DEFAULTS.values()}) == 3


def test_the_default_row_is_copied_and_not_shared_with_the_record(mapper):
    record = mapper.map_player(_player(position="C"), None)
    record.ratings[0] = 7
    assert POSITION_DEFAULTS[POSITION_C][0] == RETAIL["C"]["goals"][5]
    assert mapper.map_player(_player(position="C"), None).ratings[0] == RETAIL["C"]["goals"][5]


def test_an_empty_stat_dict_is_treated_as_no_stats_at_all(mapper):
    assert _ratings(mapper, {}) == _ratings(mapper, None)


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


def test_height_is_espns_in_inches(mapper):
    assert mapper.map_player(_player(height=83.0), None).height_inches == 83


def test_height_is_a_position_default_when_espn_reports_none(mapper):
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


def test_experience_is_espns_years_in_the_league(mapper):
    assert mapper.map_player(_player(age=30, experience=11), None).experience == 11


def test_a_rookie_has_no_experience(mapper):
    assert mapper.map_player(_player(age=19), None).experience == 0


def test_a_missing_jersey_number_becomes_zero(mapper):
    assert mapper.map_player(_player(number=None), None).jersey == 0


def test_a_leaders_line_carries_no_season_totals(mapper):
    """The leaders endpoint's per-game averages are not the totals the ROM keeps."""
    assert mapper.map_player(_player(), {"PTS": 30.0}).season_stats == [0] * 17


def test_a_season_line_writes_its_totals_in_the_roms_order(mapper):
    line = {"gamesPlayed": 70, "minutes": 2400, "points": 1800.4, "fouls": 150}
    stats = mapper.map_player(_player(), line).season_stats
    assert (stats[0], stats[1], stats[14], stats[16]) == (70, 2400, 1800, 150)


def test_a_mapped_record_carries_no_appearance_at_all(mapper):
    """`looks.dress` sets skin and head from the head shot later; the mapper leaves 0."""
    record = mapper.map_player(_player(), {"PTS": 30.0})
    assert record.skin_color == 0
    assert record.hair_style == 0


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

    Ten come from the position pass and both fill loops test `len(selected) >= 12`
    before every append, so the list can never exceed twelve and the slice can never
    truncate. Kept because "cut to twelve" is the behaviour a caller depends on
    however it is achieved.
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


def test_the_first_ten_are_two_rounds_of_the_five_positions(mapper):
    """The first five hold one player per position, and so do the next five."""
    players, stats = _squad(["PG", "SG", "SF", "PF", "C"] * 5)
    chosen = mapper.select_roster(players, stats)
    assert [p.position for p in chosen[:10]] == ["C", "PF", "SF", "SG", "PG"] * 2


def test_espn_guards_and_forwards_fill_both_spots_in_the_first_five(mapper):
    """ESPN's labels are G, F and C: two of each G and F start, with a C."""
    players, stats = _squad(["G"] * 4 + ["F"] * 4 + ["C"] * 2 + ["G", "F"])
    chosen = mapper.select_roster(players, stats)
    assert [p.position for p in chosen[:5]] == ["C", "F", "F", "G", "G"]
    found = [p.position for p in mapper.split_positions(chosen)[:5]]
    assert found == ["C", "PF", "SF", "SG", "PG"]


def test_a_squad_with_no_centre_still_starts_the_other_four_positions(mapper):
    players, stats = _squad(["G"] * 4 + ["F"] * 4)
    chosen = mapper.select_roster(players, stats)
    assert [p.position for p in chosen[:5]] == ["F", "F", "G", "G", "F"]


def test_a_first_five_of_two_g_two_f_and_a_c_splits_into_the_five_positions(mapper):
    players, _ = _squad(["C", "F", "G", "F", "G"])
    found = [p.position for p in mapper.split_positions(players)]
    assert found == ["C", "PF", "SF", "SG", "PG"]


def test_the_bench_alternates_guards_and_forwards_afresh(mapper):
    """The bench counts from its own first G and F, not the starters' last."""
    players, _ = _squad(["G", "F", "F", "C", "C", "G", "G", "G", "F", "F"])
    found = [p.position for p in mapper.split_positions(players)]
    assert found == ["C", "PF", "SF", "SG", "PG", "PG", "SG", "PG", "SF", "PF"]


def test_splitting_leaves_other_labels_and_the_callers_players_alone(mapper):
    players, _ = _squad(["C"] * 5 + ["pg", "G-F", "F-C", "Goalkeeper", "g"])
    found = mapper.split_positions(players)
    assert [p.position for p in found[5:]] == ["pg", "G-F", "F-C", "Goalkeeper", "PG"]
    assert found[5] is players[5]
    assert players[9].position == "g"


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
    assert [chosen[4].id, chosen[9].id] == [0, 1]  # PG is the fifth slot
    assert [p.id for p in chosen[10:]] == [11, 10]


def test_a_squad_smaller_than_twelve_is_returned_whole(mapper):
    players, stats = _squad(["PG", "SG", "C"])
    assert len(mapper.select_roster(players, stats)) == 3


def test_an_empty_squad_selects_nobody(mapper):
    assert mapper.select_roster([], {}) == []


def test_selection_works_with_no_stats_at_all(mapper):
    players, _ = _squad(["PG", "SG", "SF", "PF", "C"])
    assert len(mapper.select_roster(players, None)) == 5


def test_a_squad_of_twelve_goalkeepers_is_selected_whole(mapper):
    """DELIBERATE DIVERGENCE, in the code's account of itself.

    Upstream's opening comprehension was commented "filter out
    pitchers/non-basketball positions" and could not: `_normalize_position`
    answers `"SF"` for anything it does not recognise, so its membership test
    was true for everyone. The comment is gone and the behaviour is not, which
    is what this pins.
    """
    players, stats = _squad(["Goalkeeper"] * 12)
    assert len(mapper.select_roster(players, stats)) == 12


def test_thirteen_goalkeepers_are_cut_by_minutes_and_not_by_arrival(mapper):
    """A count cannot tell a real filter from no filter, so this asserts the
    twelve.

    Twelve is what a squad of goalkeepers returns either way: a filter that
    dropped them all would leave `eligible` empty, and the final pad loop --
    which reads `players` and not `eligible` -- would put twelve of them back.
    What the filter *would* change is which twelve and in what order. Here the
    busiest twelve of thirteen come back busiest-first; under a real filter it
    would be `players[:12]` in the order they arrived, which is the other twelve
    and the other order.
    """
    players, stats = _squad(["Goalkeeper"] * 13, minutes={i: (i + 1) * 10.0 for i in range(13)})
    assert [p.id for p in mapper.select_roster(players, stats)] == [
        12,
        11,
        10,
        9,
        8,
        7,
        6,
        5,
        4,
        3,
        2,
        1,
    ]


def test_every_unrecognised_position_still_normalises_into_the_membership_set(mapper):
    """Why a real filter could not have been written from this predicate.

    Whatever `_normalize_position` is handed, its answer is one of the five the
    old test asked about -- so the test was true by construction.
    """
    answers = {
        mapper._normalize_position(text)
        for text in ("Goalkeeper", "Pitcher", "", "  ", "LW", "QB", "SF", "g-f", "?7", "Forward")
    }
    assert answers <= {"PG", "SG", "SF", "PF", "C"}
    assert "SF" in answers


def test_selection_does_not_reorder_the_list_it_was_handed(mapper):
    """`eligible.sort` is in place, and upstream's dead fallback aliased it to
    the caller's own list."""
    players, stats = _squad(["C", "PG", "SG"], minutes={0: 1.0, 1: 40.0, 2: 20.0})
    before = [p.position for p in players]
    mapper.select_roster(players, stats)
    assert [p.position for p in players] == before
    assert before == ["C", "PG", "SG"]


def test_selection_still_orders_by_minutes_within_a_position_after_the_copy(mapper):
    """The copy must not cost the sort: the busiest centre still comes first."""
    players, stats = _squad(["C", "C", "C"], minutes={0: 3.0, 1: 30.0, 2: 12.0})
    assert [p.id for p in mapper.select_roster(players, stats)] == [1, 2, 0]


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


def test_a_team_that_did_not_exist_in_1994_takes_an_all_star_slot_or_none(mapper):
    found = [mapper.get_team_slot(code) for code in ("TOR", "MEM", "NOP", "NO")]
    assert found == [27, 28, None, None]


def test_an_unknown_code_has_no_slot(mapper):
    assert mapper.get_team_slot("ZZZ") is None


def test_recent_starters_go_centre_forwards_guards_each_tallest_first(mapper):
    players, stats = _squad(["G", "G", "F", "F", "C", "G", "F"])
    for p, h in zip(players, (75, 77, 80, 85, 84, 76, 79), strict=True):
        p.height = h
    starters = {str(p.id): 9 for p in players[2:7]}
    chosen = mapper.select_roster(players, stats, starters)
    assert [p.id for p in chosen[:5]] == [players[i].id for i in (4, 3, 2, 6, 5)]
