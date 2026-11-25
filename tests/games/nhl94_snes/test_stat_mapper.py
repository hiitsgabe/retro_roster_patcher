"""Stat mapper coverage.

`select_roster` is the half that matters most and the half nothing else pins:
the ROM's line table indexes players by absolute position, so a selection that
put a defenceman where a forward was expected produces a bootable ROM with the
wrong men on the ice. Every test here therefore asserts the whole selected list
in order, not its length.

The defaults branch is a DELIBERATE DIVERGENCE from upstream and is pinned as
one: `test_two_players_at_one_position_do_not_share_an_attributes_object`.
"""

import dataclasses

import pytest

from retro_roster_patcher.games.nhl94_snes.models import (
    MODERN_NHL_TO_NHL94,
    TEAM_COUNT,
    NHL94GoalieAttributes,
    NHL94PlayerAttributes,
)
from retro_roster_patcher.games.nhl94_snes.stat_mapper import (
    GOALIE_DEFAULTS,
    POSITION_DEFAULTS,
    LeagueStats,
    NHL94StatMapper,
)
from retro_roster_patcher.sports.models import Player


def _player(pid, position, **kwargs):
    return Player(id=pid, name=kwargs.pop("name", f"Player {pid}"), position=position, **kwargs)


def _squad(spec):
    """`spec` is `[(id, position, points)]`; returns players and their leaders."""
    players = [_player(pid, position) for pid, position, _ in spec]
    leaders = {str(pid): {"PTS": points} for pid, _, points in spec}
    return players, leaders


@pytest.fixture
def mapper():
    return NHL94StatMapper()


def test_a_team_code_maps_to_its_slot(mapper):
    assert mapper.get_team_slot("BOS") == 1


def test_a_lowercase_code_maps_to_the_same_slot(mapper):
    assert mapper.get_team_slot("bos") == 1


def test_the_four_clubs_the_cartridge_never_had_take_the_new_slots(mapper):
    assert [mapper.get_team_slot(c) for c in ("CBJ", "MIN", "NSH", "UTA")] == [28, 29, 30, 31]
    assert mapper.get_team_slot("ARI") is None


def test_seattle_and_vegas_take_the_all_star_slots(mapper):
    assert mapper.get_team_slot("SEA") == 26
    assert mapper.get_team_slot("VGK") == 27


@pytest.mark.parametrize(
    ("espn", "official"), [("LA", "LAK"), ("NJ", "NJD"), ("SJ", "SJS"), ("TB", "TBL")]
)
def test_each_espn_abbreviation_reaches_the_same_slot_as_the_official_one(mapper, espn, official):
    assert mapper.get_team_slot(espn) == mapper.get_team_slot(official)


def test_thirty_six_codes_reach_all_thirty_two_slots(mapper):
    """The collision `map_rosters` has to guard, counted: four ESPN aliases."""
    assert len(MODERN_NHL_TO_NHL94) == 36
    assert set(MODERN_NHL_TO_NHL94.values()) == set(range(TEAM_COUNT))


def test_a_player_with_no_stats_gets_the_defaults_for_their_position(mapper):
    record = mapper.map_player(_player(1, "D"), "BOS")
    assert record.attributes == POSITION_DEFAULTS["D"]


def test_an_unrecognised_position_falls_back_to_a_centres_defaults(mapper):
    record = mapper.map_player(_player(1, "W"), "BOS")
    assert record.attributes == POSITION_DEFAULTS["C"]


def test_a_player_with_no_position_at_all_is_treated_as_a_centre(mapper):
    record = mapper.map_player(_player(1, ""), "BOS")
    assert record.attributes == POSITION_DEFAULTS["C"]


def test_two_players_at_one_position_do_not_share_an_attributes_object(mapper):
    """DELIBERATE DIVERGENCE: upstream handed out the module constant itself.

    `NHL94PlayerAttributes` is a plain mutable dataclass and
    `NHL94PlayerRecord.attributes` is public, so upstream gave every statless
    centre the same object -- and gave it to `POSITION_DEFAULTS` too, so one
    caller assignment rewrote the defaults for the rest of the process.
    """
    first = mapper.map_player(_player(1, "C"), "BOS")
    second = mapper.map_player(_player(2, "C"), "BOS")
    assert first.attributes is not second.attributes
    assert first.attributes is not POSITION_DEFAULTS["C"]


def test_mutating_one_players_attributes_leaves_the_defaults_alone(mapper):
    before = dataclasses.replace(POSITION_DEFAULTS["D"])
    record = mapper.map_player(_player(1, "D"), "BOS")
    record.attributes.speed = 6
    assert POSITION_DEFAULTS["D"] == before
    assert POSITION_DEFAULTS["D"].speed != 6


def test_a_goalie_is_flagged_as_one(mapper):
    assert mapper.map_player(_player(1, "G"), "BOS").is_goalie is True


def test_a_skater_is_not_flagged_as_a_goalie(mapper):
    assert mapper.map_player(_player(1, "D"), "BOS").is_goalie is False


def test_a_name_keeps_up_to_nineteen_characters(mapper):
    # The cartridge's longest is "Vladimir Konstantov".
    record = mapper.map_player(_player(1, "C", name="Alexander Ovechkin"), "WSH")
    assert record.name == "Alexander Ovechkin"
    assert mapper.map_player(_player(1, "C", name="Vladimir Konstantov"), "DET").name == (
        "Vladimir Konstantov"
    )


def test_a_longer_name_loses_its_first_name_to_an_initial(mapper):
    record = mapper.map_player(_player(1, "D", name="Oliver Ekman-Larsson"), "TOR")
    assert record.name == "O. Ekman-Larsson"


def test_a_name_is_folded_to_ascii(mapper):
    # The game draws a non-ASCII byte as '?'.
    assert mapper.map_player(_player(1, "C", name="Tim Stützle"), "OTT").name == "Tim Stutzle"
    assert mapper.map_player(_player(1, "D", name="Rémi Poirier"), "NJ").name == "Remi Poirier"


def test_a_short_name_is_left_alone(mapper):
    assert mapper.map_player(_player(1, "C", name="Mario"), "PIT").name == "Mario"


def test_a_player_with_no_number_is_given_one(mapper):
    assert mapper.map_player(_player(1, "C"), "BOS").jersey_number == 1


def test_a_players_number_is_carried_through(mapper):
    assert mapper.map_player(_player(1, "C", number=99), "BOS").jersey_number == 99


@pytest.mark.parametrize(
    ("pounds", "weight_class"),
    [(140, 0), (196, 7), (252, 14), (400, 14), (100, 0)],
)
def test_weight_maps_onto_the_fifteen_step_class(mapper, pounds, weight_class):
    record = mapper.map_player(_player(1, "C", weight=float(pounds)), "BOS")
    assert record.weight_class == weight_class


def test_an_unknown_weight_becomes_the_middle_class(mapper):
    assert mapper.map_player(_player(1, "C"), "BOS").weight_class == 7


def test_a_right_handed_player_is_encoded_as_one(mapper):
    assert mapper.map_player(_player(1, "C", handedness="R"), "BOS").handedness == 1


def test_any_other_handedness_is_encoded_as_left(mapper):
    assert mapper.map_player(_player(1, "C", handedness="L"), "BOS").handedness == 0
    assert mapper.map_player(_player(2, "C"), "BOS").handedness == 0


def _league(mapper, position, lines, index):
    """Map player `index` of `lines` (one per player at `position`) against the
    league they make."""
    pairs = [(_player(i + 1, position, age=20 + i % 15), line) for i, line in enumerate(lines)]
    league = LeagueStats.from_lines(pairs)
    player, line = pairs[index]
    return mapper.map_player(player, "BOS", line, league).attributes


def test_every_skater_rating_comes_from_its_own_stats(mapper):
    """Shot accuracy is not shot power, stick handling not offensive awareness,
    shot bias not aggression, and checking, endurance, speed and agility come
    from the line too."""
    lines = [
        {
            "GP": 82,
            "G": 10 + i % 15,
            "A": 15 + i % 20,
            "PTS": 25 + i % 30,
            "S": 120 + 3 * i,
            "PIM": 20 + i,
            "hits": 50 + i,
            "TOI": 900 + 10 * i,
            "+/-": i % 9 - 4,
        }
        for i in range(40)
    ]
    # Many shots, few goals, many assists, few penalties, many hits, big minutes.
    lines.append(
        {
            "GP": 82,
            "G": 6,
            "A": 60,
            "PTS": 66,
            "S": 330,
            "PIM": 4,
            "hits": 250,
            "TOI": 1500,
            "+/-": 0,
        }
    )
    player = _league(mapper, "C", lines, 40)
    assert player.shot_power >= 5 and player.shot_accuracy <= 1
    assert player.off_awareness >= 5 and player.stick_handling <= 2
    assert player.pass_accuracy >= 5 and player.shot_bias <= 1
    assert player.aggression <= 1 and player.fighting <= 1
    assert player.checking >= 5 and player.endurance >= 5
    assert player.speed >= 4 and player.agility >= 4


def test_a_partial_line_keeps_the_positions_default_for_what_it_lacks(mapper):
    """ESPN's leaders give some skaters only one category: the stats there are
    used, the rest stay the position's, not 0 nor the team's lowest."""
    base = POSITION_DEFAULTS["LW"]
    lines = [{"GP": 82, "A": 10 + i} for i in range(40)]
    lines.append({"GP": 82, "A": 70})
    attributes = _league(mapper, "LW", lines, 40)
    assert isinstance(attributes, NHL94PlayerAttributes)
    assert attributes.pass_accuracy >= 5
    for name in ("shot_power", "shot_accuracy", "off_awareness", "def_awareness", "aggression"):
        assert getattr(attributes, name) == getattr(base, name)


def test_a_goalie_is_rated_from_his_season(mapper):
    """Save % gives defensive awareness; saves, wins and goals against the rest."""
    lines = [
        {"GP": 40, "W": 15 + i % 10, "SV": 900 + 10 * i, "SV%": 0.890 + 0.001 * i, "GAA": 3.2}
        for i in range(30)
    ]
    lines.append({"GP": 62, "W": 40, "SV": 1750, "SV%": 0.925, "GAA": 2.1, "SO": 6})
    star = _league(mapper, "G", lines, 30)
    worst = _league(mapper, "G", lines, 0)
    assert isinstance(star, NHL94GoalieAttributes)
    assert (star.agility, star.def_awareness, star.puck_control) == (6, 6, 6)
    assert min(star.stick_right, star.stick_left, star.glove_right, star.glove_left) >= 5
    assert star.speed == 4 and worst.speed == 3
    assert worst.def_awareness < star.def_awareness and worst.puck_control < star.puck_control


def test_a_goalie_line_without_save_percentage_keeps_the_default_awareness(mapper):
    lines = [{"GP": 30, "SV%": 0.900 + 0.001 * i, "GAA": 3.0} for i in range(10)]
    lines.append({"GP": 30, "GAA": 2.8})
    assert _league(mapper, "G", lines, 10).def_awareness == GOALIE_DEFAULTS.def_awareness


def test_a_goalie_without_a_stat_line_gets_the_goalie_defaults(mapper):
    record = mapper.map_player(_player(1, "G"), "BOS")
    assert record.attributes == GOALIE_DEFAULTS
    assert record.attributes is not GOALIE_DEFAULTS


def test_a_goalie_is_never_scored_on_the_skater_scale(mapper):
    """Every skater-only stat is ignored, so a 90-point goalie stays a goalie."""
    attributes = mapper.map_player(_player(1, "G"), "BOS", {"PTS": 90, "G": 40}).attributes
    assert attributes == GOALIE_DEFAULTS


def test_a_null_stat_value_is_read_as_missing_rather_than_raising(mapper):
    attributes = mapper.map_player(_player(1, "C"), "BOS", {"PTS": None, "G": None}).attributes
    assert attributes.off_awareness == POSITION_DEFAULTS["C"].off_awareness


def test_an_empty_stat_dict_falls_through_to_the_defaults(mapper):
    assert mapper.map_player(_player(1, "D"), "BOS", {}).attributes == POSITION_DEFAULTS["D"]


def test_the_roster_is_ordered_goalies_then_forwards_then_defencemen(mapper):
    """The whole selected list, in order, against the ROM's own layout.

    Two lines of LW/C/RW are taken best-first at each position, so the order is
    LW1 C1 RW1 LW2 C2 RW2 -- not the six best forwards.
    """
    players, leaders = _squad(
        [
            (10, "G", 0),
            (11, "G", 0),
            (12, "G", 0),
            (20, "LW", 60),
            (21, "LW", 40),
            (22, "LW", 20),
            (30, "C", 90),
            (31, "C", 70),
            (32, "C", 10),
            (40, "RW", 80),
            (41, "RW", 50),
            (42, "RW", 30),
            (50, "D", 45),
            (51, "D", 35),
            (52, "D", 25),
            (53, "D", 15),
        ]
    )
    leaders["10"].update({"SV%": 0.880, "W": 30})
    leaders["11"].update({"SV%": 0.900, "W": 12})
    leaders["12"].update({"SV%": 1.000})
    selected = mapper.select_roster(
        players, leaders, num_goalies=2, num_forwards=6, num_defensemen=3
    )
    assert [p.id for p in selected] == [10, 11, 20, 30, 40, 21, 31, 41, 50, 51, 52]


def test_the_counts_asked_for_are_the_counts_returned(mapper):
    players, leaders = _squad(
        [(i, "G", 0) for i in range(3)]
        + [(10 + i, "C", 50 - i) for i in range(6)]
        + [(20 + i, "LW", 40 - i) for i in range(6)]
        + [(30 + i, "RW", 30 - i) for i in range(6)]
        + [(40 + i, "D", 20 - i) for i in range(6)]
    )
    selected = mapper.select_roster(
        players, leaders, num_goalies=2, num_forwards=9, num_defensemen=5
    )
    positions = [p.position for p in selected]
    assert len(selected) == 16
    assert positions[:2] == ["G", "G"]
    assert positions[11:] == ["D"] * 5
    assert positions[2:11] == ["LW", "C", "RW"] * 3


def test_a_forward_count_that_is_not_a_multiple_of_three_gets_extras(mapper):
    """7 forwards is two full lines plus one, and the extra is the best left."""
    players, leaders = _squad(
        [(10 + i, "C", 100 - i) for i in range(4)]
        + [(20 + i, "LW", 90 - i) for i in range(4)]
        + [(30 + i, "RW", 80 - i) for i in range(4)]
    )
    selected = mapper.select_roster(
        players, leaders, num_goalies=0, num_forwards=7, num_defensemen=0
    )
    assert [p.id for p in selected] == [20, 10, 30, 21, 11, 31, 12]


def test_a_missing_position_is_filled_from_the_best_forward_left(mapper):
    """No left wings at all, so every LW slot takes the best unused forward."""
    players, leaders = _squad(
        [(10 + i, "C", 100 - i) for i in range(3)] + [(30 + i, "RW", 80 - i) for i in range(3)]
    )
    selected = mapper.select_roster(
        players, leaders, num_goalies=0, num_forwards=6, num_defensemen=0
    )
    assert [p.id for p in selected] == [10, 11, 30, 12, 31, 32]


def test_a_short_block_stays_short(mapper):
    """Fewer players at a position than asked for gives a shorter block, never
    a spare forward or goalie after the defencemen, where the ROM's line table
    would call him a defenceman."""
    players, leaders = _squad(
        [(1, "G", 0), (10, "C", 90), (11, "C", 70), (12, "C", 50), (20, "D", 40)]
    )
    selected = mapper.select_roster(
        players, leaders, num_goalies=2, num_forwards=1, num_defensemen=2
    )
    assert [p.id for p in selected] == [1, 10, 20]


def test_the_goalie_who_started_most_is_the_starter(mapper):
    """ESPN's wins, then save %: a backup with one perfect game sits."""
    players, leaders = _squad([(1, "G", 0), (2, "G", 0), (3, "G", 0)])
    leaders["1"] = {"SV%": 1.0}
    leaders["2"] = {"SV%": 0.908, "W": 31}
    leaders["3"] = {"SV%": 0.920, "W": 9}
    selected = mapper.select_roster(players, leaders, 2, 0, 0)
    assert [p.id for p in selected] == [2, 3]


def test_backups_without_wins_rank_by_their_own_save_percentage(mapper):
    """Montreal's case: neither backup is on ESPN's wins list, so the better
    save % is G2, not whoever the roster lists first; a goalie with no line at
    all sits behind one with a good line."""
    players, leaders = _squad([(1, "G", 0), (2, "G", 0), (3, "G", 0), (4, "G", 0)])
    leaders["1"] = {"SV%": 0.872}
    leaders["2"] = {"SV%": 0.901, "W": 29}
    leaders["3"] = {}
    leaders["4"] = {"SV%": 0.908}
    selected = mapper.select_roster(players, leaders, 4, 0, 0)
    assert [p.id for p in selected] == [2, 4, 3, 1]


def test_games_played_rank_goalies_when_the_provider_sends_them(mapper):
    players, leaders = _squad([(1, "G", 0), (2, "G", 0)])
    leaders["1"] = {"SV%": 0.941, "GP": 3, "W": 3}
    leaders["2"] = {"SV%": 0.928, "GP": 8, "W": 2}
    assert [p.id for p in mapper.select_roster(players, leaders, 2, 0, 0)] == [2, 1]


def test_the_selection_never_exceeds_the_sum_of_the_three_counts(mapper):
    players, leaders = _squad([(i, "C", i) for i in range(40)])
    selected = mapper.select_roster(
        players, leaders, num_goalies=2, num_forwards=14, num_defensemen=7
    )
    assert len(selected) == 14


def test_no_player_is_selected_twice(mapper):
    players, leaders = _squad(
        [(i, "G", 0) for i in range(4)]
        + [(10 + i, "C", i) for i in range(10)]
        + [(30 + i, "D", i) for i in range(10)]
    )
    selected = mapper.select_roster(
        players, leaders, num_goalies=2, num_forwards=14, num_defensemen=7
    )
    assert len(selected) == len({id(p) for p in selected})
    assert len(selected) == 2 + 10 + 7


def test_an_empty_squad_selects_nobody(mapper):
    assert mapper.select_roster([], {}) == []


def test_selection_without_any_stats_still_returns_a_roster(mapper):
    players = [_player(i, "C") for i in range(5)]
    assert len(mapper.select_roster(players, None, 1, 3, 1)) == 3
