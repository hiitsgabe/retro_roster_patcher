"""`NHL07StatMapper`: provider data onto NHL 07 records.

The ratings themselves rank through `core.hockey_ratings` (tested there); what
is pinned here is this game's policy against its retail disc -- every SPAI and
SGAI field named, every spread the disc's own -- plus the roster shaping, the
bio fields and no shared mutable defaults.
"""

from __future__ import annotations

import pytest

from retro_roster_patcher.core.hockey_ratings import COUNTS, League
from retro_roster_patcher.games.nhl07_psp.models import (
    GOALIE_FIELDS,
    MODERN_NHL_TO_NHL07,
    SKATER_FIELDS,
    NHL07GoalieAttributes,
    NHL07PlayerRecord,
    NHL07SkaterAttributes,
)
from retro_roster_patcher.games.nhl07_psp.stat_mapper import (
    DEFAULT_HEIGHT,
    DEFAULT_JERSEY,
    DEFAULT_WEIGHT,
    GOALIE_DEFAULTS,
    HAND_LEFT,
    HAND_RIGHT,
    HEIGHT_BASE_INCHES,
    HEIGHT_MAX,
    MAX_PLAYERS,
    SKATER_DEFAULTS,
    SOURCES,
    SPREAD,
    NHL07StatMapper,
    _defaults_for,
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


def test_every_position_has_its_own_defaults():
    assert sorted(SKATER_DEFAULTS) == ["C", "D", "LW", "RW"]


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


def test_a_player_without_a_reported_height_gets_the_default():
    tall = MAPPER.map_player(player(pid=1, position="D"), "BOS")
    small = MAPPER.map_player(player(pid=2, position="G"), "TOR")
    assert [tall.height, small.height] == [DEFAULT_HEIGHT, DEFAULT_HEIGHT]


def test_a_reported_height_is_encoded_as_inches_above_54():
    # Checked on the retail disc: Chara is stored as 27 (6'9"), Gionta as 13
    # (5'7"), and the game shows a stored 16 as 5'10".
    assert HEIGHT_BASE_INCHES == 54
    assert MAPPER.map_player(player(height=73.0), "EDM").height == 19


def test_a_giant_is_clamped_to_the_five_bit_ceiling():
    assert MAPPER.map_player(player(height=200.0), "BOS").height == HEIGHT_MAX


def test_someone_shorter_than_the_base_is_floored_at_zero():
    assert MAPPER.map_player(player(height=50.0), "BOS").height == 0


ELITE = {"G": 40, "A": 55, "PTS": 95, "+/-": 40, "PIM": 80, "SOG": 300, "FO%": 60}


def test_goalies_reported_in_percentages_outrank_every_fraction_goalie():
    # PINS UPSTREAM FIDELITY DELIBERATELY. `select_roster` reads `SV%` raw too,
    # so a roster file mixing the conventions starts the worse goalie: 88.0 is
    # 88 000 against 0.930's 930.
    goalies = [player(pid=1, position="G"), player(pid=2, position="G")]
    stats = {"1": {"SV%": 88.0}, "2": {"SV%": 0.930}}
    assert [p.id for p in MAPPER.select_roster(goalies, stats)] == [1, 2]


def test_goalies_reported_in_one_convention_still_sort_best_first():
    # Within one convention the ordering is right, which is why nothing caught
    # the line above.
    goalies = [player(pid=1, position="G"), player(pid=2, position="G")]
    stats = {"1": {"SV%": 0.880}, "2": {"SV%": 0.920}}
    assert [p.id for p in MAPPER.select_roster(goalies, stats)] == [2, 1]


def test_every_abbreviation_in_the_table_resolves():
    assert [c for c in MODERN_NHL_TO_NHL07 if MAPPER.get_team_slot(c) is None] == []


def test_an_unknown_abbreviation_answers_none():
    assert MAPPER.get_team_slot("XYZ") is None


def test_the_espn_and_nhl_spellings_of_los_angeles_reach_one_slot():
    assert MAPPER.get_team_slot("LA") == MAPPER.get_team_slot("LAK")


def test_the_expansion_teams_take_the_all_star_slots():
    assert (MAPPER.get_team_slot("SEA"), MAPPER.get_team_slot("VGK")) == (30, 31)


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
    # The even-strength `Ln` pairs, where the retail disc starts its first pair;
    # `31LD` is the five-on-three unit.
    result = MAPPER.generate_team_line_flags(records(*(["D"] * 6)))
    assert [f for i in range(6) for f in flags_set(result, i) if f[0] == "L"] == [
        "L1LD",
        "L1RD",
        "L2LD",
        "L2RD",
        "L3LD",
        "L3RD",
    ]


def test_the_first_two_goalies_twelve_forwards_and_six_defencemen_dress():
    # The retail disc dresses exactly that many and scratches the rest.
    team = records(*(["G"] * 3 + ["D"] * 7 + ["C"] * 13))
    expected = [True] * 2 + [False] + [True] * 6 + [False] + [True] * 12 + [False]
    assert MAPPER.dressed(team) == expected


def test_a_scratch_is_never_on_a_line():
    # Thirteen forwards in list order: the fourth right wing is the 13th and a
    # scratch, so the spare fifth centre takes his wing.
    result = MAPPER.generate_team_line_flags(records(*(["C"] * 5 + ["LW"] * 4 + ["RW"] * 4)))
    assert (flags_set(result, 12), flags_set(result, 4)) == ([], ["L4RW"])


def test_a_seventh_defenceman_gets_no_pair():
    result = MAPPER.generate_team_line_flags(records(*(["D"] * 7)))
    assert flags_set(result, 6) == []


def test_a_full_team_gets_every_unit_the_retail_disc_builds_from_its_lines():
    # Line n is C, LW, RW at 3n-3, the pairs start at 12. Boston on the retail
    # disc: Bergeron, L1LW, also holds 41LW, P1LW, H1__, S1__ and X1__.
    result = MAPPER.generate_team_line_flags(records(*(["C", "LW", "RW"] * 4 + ["D"] * 6)))
    assert [flags_set(result, i) for i in range(18)] == [
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
    result = MAPPER.generate_team_line_flags(records("C", "LW", "LW", "D", "D"))
    held = {f: i for i in range(5) for f in flags_set(result, i)}
    assert [held.get(f"H{k}__") for k in range(1, 6)] == [1, 0, 3, 4, None]
    assert [held.get(f"S{k}__") for k in range(1, 6)] == [1, 0, 2, None, None]


def test_a_team_short_of_defencemen_gets_a_four_man_h_unit():
    result = MAPPER.generate_team_line_flags(records("C", "LW", "RW", "D"))
    assigned = sorted(name for r in result for name, v in r.items() if v == 1 and name[0] == "H")
    assert assigned == ["H1__", "H2__", "H3__", "H4__"]


def test_a_player_on_no_line_gets_an_empty_flag_dict():
    # Which `rom_writer.roster_values` turns into all sixty-four flags zeroed -- a
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


#: The retail disc's SPAI and SGAI fields, in its order (ULUS10131).
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
def test_every_rating_has_the_discs_spread_and_ranks_only_stats_there_are(group):
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


def forwards(n=41):
    return [
        (
            player(pid=i, name=f"Forward {i}", weight=220 - i),
            {"GP": 82, "G": i, "A": 2 * i, "PTS": 3 * i, "S": 80 + 5 * i, "TOI": 700 + 15 * i},
        )
        for i in range(n)
    ]


def test_the_leagues_best_and_worst_take_the_ends_of_the_discs_spread():
    lines = forwards()
    league = League.from_lines(lines, SOURCES)
    best = MAPPER.map_player(lines[-1][0], "BOS", lines[-1][1], league).skater_attrs
    worst = MAPPER.map_player(lines[0][0], "BOS", lines[0][1], league).skater_attrs
    for rating in ("passing", "shot_accuracy", "endurance", "speed", "potential"):
        assert getattr(best, rating) == SPREAD["F"][rating][-1], rating
        assert getattr(worst, rating) == SPREAD["F"][rating][0], rating


def test_a_rating_the_line_has_no_stat_for_keeps_the_position_default():
    # ESPN's team leaders list a player only under the categories he leads.
    attrs = MAPPER.map_player(player(), "BOS", {"PIM": 120}).skater_attrs
    assert attrs.passing == SKATER_DEFAULTS["C"].passing
    assert attrs.shot_accuracy == SKATER_DEFAULTS["C"].shot_accuracy
    assert attrs.endurance == SKATER_DEFAULTS["C"].endurance


def test_a_wingers_faceoff_percentage_without_a_count_ranks_below_every_centre():
    # The NHL API sends a winger's FO% over his handful of draws, and no count.
    centres = [(player(pid=i), {"GP": 82, "FO%": 0.40 + i / 100}) for i in range(20)]
    winger = (player(pid=99, position="LW"), {"GP": 82, "FO%": 1.0})
    league = League.from_lines([*centres, winger], SOURCES)
    faceoffs = [
        MAPPER.map_player(p, "BOS", line, league).skater_attrs.faceoffs
        for p, line in [winger, *centres]
    ]
    assert faceoffs[0] < min(faceoffs[1:])


def test_a_playmaker_leans_away_from_the_physical_bias():
    lines = forwards()
    league = League.from_lines(lines, SOURCES)
    playmaker = MAPPER.map_player(lines[-1][0], "BOS", lines[-1][1], league).skater_attrs
    grinder = MAPPER.map_player(lines[0][0], "BOS", lines[0][1], league).skater_attrs
    assert playmaker.pc_bias < grinder.pc_bias
    assert playmaker.offence_bias > grinder.offence_bias


def test_a_busy_goalie_with_a_good_save_rate_outrates_a_backup_and_both_play_butterfly():
    goalies = [
        (
            player(pid=i, name=f"Goalie {i}", position="G"),
            {"GP": 10 + 2 * i, "W": i, "SV%": 0.880 + i / 1000, "GAA": 3.4 - i / 30, "SO": i // 5},
        )
        for i in range(30)
    ]
    league = League.from_lines(goalies, SOURCES)
    starter = MAPPER.map_player(goalies[-1][0], "BOS", goalies[-1][1], league).goalie_attrs
    backup = MAPPER.map_player(goalies[0][0], "BOS", goalies[0][1], league).goalie_attrs
    for rating in ("glove_high", "stick_low", "five_hole", "rebound_ctrl", "endurance"):
        assert getattr(starter, rating) > getattr(backup, rating), rating
    assert starter.style == backup.style == 2


def test_the_birth_fields_show_his_real_age_in_2006():
    record = MAPPER.map_player(player(age=27, nationality="CAN", birth_city="Kingston"), "BOS")
    assert record.birth_year == 2006 - 27 - 1959
    assert (record.country, record.city) == (85, "Kingston")
