"""Stat mapper coverage: the provider payload turned into ROM nibbles.

Every assertion is on the resulting integers rather than on the shape of the
record. The attributes are packed as 0-6 nibbles by `rom_writer.encode_nibble`,
so a mapper that returned the right field names carrying the wrong numbers
produces a ROM that loads and plays wrong.

Each rating ranks one of the player's stats against his group (forwards,
defencemen, goalies) in the league fetched and takes the value at that rank of
the cartridge's own spread (`RETAIL`).
"""

import dataclasses
from collections import Counter

import pytest

from retro_roster_patcher.games.nhl94_genesis.models import NHL94GenGoalieAttributes
from retro_roster_patcher.games.nhl94_genesis.stat_mapper import (
    GOALIE_DEFAULTS,
    POSITION_DEFAULTS,
    RETAIL,
    LeagueStats,
    NHL94GenStatMapper,
    on_retail,
)
from retro_roster_patcher.sports.models import Player


@pytest.fixture
def mapper():
    return NHL94GenStatMapper()


@pytest.fixture
def position_defaults_restored():
    """Snapshot `POSITION_DEFAULTS` and put it back afterwards.

    The defect the aliasing tests below guard against is exactly that a mapped
    record can *be* the module constant, so while that defect is present those
    tests really do edit it. Leaving the edit in place would poison every test
    that ran after them, and in a different file.
    """
    saved = {pos: dataclasses.replace(attrs) for pos, attrs in POSITION_DEFAULTS.items()}
    yield
    POSITION_DEFAULTS.update(saved)


def _skater(**kwargs):
    fields = {"id": 1, "name": "PLAYER ONE", "position": "C", "number": 9}
    fields.update(kwargs)
    return Player(**fields)


def _forwards(lines):
    """(player, line) for a league of centres, ids from 1, aged 20-39 and weighing
    170-229 lb in turn."""
    return [
        (_skater(id=i + 1, age=20 + i % 20, weight=170.0 + i % 60), line)
        for i, line in enumerate(lines)
    ]


def _goalies(lines):
    return [
        (_skater(id=i + 1, position="G", age=22 + i % 15), line) for i, line in enumerate(lines)
    ]


def _map(mapper, league_lines, index):
    """Map player `index` of `league_lines` against the league they make."""
    league = LeagueStats.from_lines(league_lines)
    player, line = league_lines[index]
    return mapper.map_player(player, "BOS", line, league).attributes


def test_on_retail_spans_the_cartridges_spread():
    speed = RETAIL["F"]["speed"]  # 0, 18, 113, 126, 89, 13, 5
    assert on_retail(0.0, speed) == 1
    assert on_retail(0.5, speed) == 3
    assert on_retail(0.999, speed) == 6


def test_a_league_as_big_as_the_cartridges_gets_its_exact_spread(mapper):
    # 364 forwards with distinct shots per game: their shot power is spread over
    # 0-6 exactly as the cartridge's 364 forwards' is.
    league_lines = _forwards([{"GP": 82, "S": 82 + i} for i in range(364)])
    league = LeagueStats.from_lines(league_lines)
    got = Counter(
        mapper.map_player(p, "BOS", line, league).attributes.shot_power for p, line in league_lines
    )
    assert [got[v] for v in range(7)] == list(RETAIL["F"]["shot_power"])


def test_a_volume_shooter_who_rarely_scores_shoots_hard_but_not_true(mapper):
    lines = [{"GP": 82, "S": 120 + i, "G": 12 + i // 4} for i in range(40)]
    lines.append({"GP": 82, "S": 330, "G": 10})
    shooter = _map(mapper, _forwards(lines), 40)
    assert shooter.shot_power >= 5
    assert shooter.shot_accuracy <= 1


def test_a_passer_reads_the_play_better_than_he_handles_the_puck(mapper):
    lines = [{"GP": 82, "G": 10 + i % 15, "A": 15 + i % 20, "PTS": 25 + i % 35} for i in range(40)]
    lines.append({"GP": 82, "G": 4, "A": 70, "PTS": 74})
    passer = _map(mapper, _forwards(lines), 40)
    assert passer.off_awareness >= 5 and passer.pass_accuracy >= 5
    assert passer.stick_handling <= 2


def test_roughness_is_fights_and_aggression_penalty_minutes(mapper):
    lines = [{"GP": 82, "PIM": 20 + i, "majorPenalties": i % 3} for i in range(40)]
    lines.append({"GP": 82, "PIM": 110, "majorPenalties": 0})
    pest = _map(mapper, _forwards(lines), 40)
    assert pest.aggression >= 5
    assert pest.roughness <= 1


def test_without_fight_counts_roughness_keeps_the_position_default(mapper):
    # The NHL API's club stats carry penalty minutes but no fights or majors.
    player = _map(mapper, _forwards([{"GP": 82, "PIM": 20 + i} for i in range(41)]), 40)
    assert player.roughness == POSITION_DEFAULTS["C"].roughness
    assert player.aggression >= 5


def test_a_stat_the_line_lacks_keeps_the_position_default(mapper):
    lines = [{"GP": 82, "G": 20 + i, "PIM": 30 + i} for i in range(20)]
    lines.append({"GP": 82, "G": 25})
    player = _map(mapper, _forwards(lines), 20)
    assert player.aggression == POSITION_DEFAULTS["C"].aggression
    assert player.endurance == POSITION_DEFAULTS["C"].endurance


def test_endurance_is_ice_time_from_either_provider(mapper):
    nhl = [{"GP": 82, "TOI": 600 + 20 * i} for i in range(41)]
    espn = [{"GP": 82, "timeOnIcePerGame": 600 + 20 * i} for i in range(41)]
    assert _map(mapper, _forwards(nhl), 40).endurance >= 5
    assert _map(mapper, _forwards(espn), 0).endurance <= 1


def test_checking_is_hits_where_the_line_has_them_and_size_otherwise(mapper):
    hits = [{"GP": 82, "hits": 20 + 5 * i} for i in range(41)]
    assert _map(mapper, _forwards(hits), 40).checking >= 5
    sized = [(_skater(id=i + 1, weight=170.0 + i), {"GP": 82}) for i in range(41)]
    league = LeagueStats.from_lines(sized)
    heaviest = mapper.map_player(sized[40][0], "BOS", sized[40][1], league).attributes
    lightest = mapper.map_player(sized[0][0], "BOS", sized[0][1], league).attributes
    assert heaviest.checking > lightest.checking


def test_a_few_games_are_not_a_star_season(mapper):
    # Two goals in two games is a better rate than 35 in 82, but not a better
    # season: per-game rates are pulled toward the group's by ten games.
    lines = [{"GP": 82, "G": 5 + i} for i in range(40)]
    lines.append({"GP": 2, "G": 2})
    call_up = _map(mapper, _forwards(lines), 40)
    regular = _map(mapper, _forwards(lines), 30)
    assert call_up.stick_handling < regular.stick_handling


def test_speed_and_agility_favour_young_light_scorers(mapper):
    league_lines = [
        (_skater(id=i + 1, age=19 + i % 18, weight=165.0 + 2 * (i % 30)), {"GP": 82, "PTS": 10 + i})
        for i in range(60)
    ]
    league = LeagueStats.from_lines(league_lines)
    young = _skater(id=99, age=21, weight=170.0)
    old = _skater(id=98, age=37, weight=230.0)
    young_scorer = mapper.map_player(young, "BOS", {"GP": 82, "PTS": 100}, league).attributes
    old_heavy = mapper.map_player(old, "BOS", {"GP": 82, "PTS": 8}, league).attributes
    assert young_scorer.speed > old_heavy.speed
    assert young_scorer.agility > old_heavy.agility


def test_two_stats_averaged_still_spread_like_the_cartridge(mapper):
    # Agility averages the ranks of lightness and points. An average of two ranks
    # bunches in the middle, so the average is ranked again: 364 forwards still
    # get about the cartridge's spread of agility.
    league_lines = [
        (_skater(id=i + 1, weight=160.0 + i), {"GP": 82, "PTS": (i * 7) % 364}) for i in range(364)
    ]
    league = LeagueStats.from_lines(league_lines)
    got = Counter(
        mapper.map_player(p, "BOS", line, league).attributes.agility for p, line in league_lines
    )
    # Within 5 of the cartridge's count at every value (ties in the blend move a
    # few); unranked, the middle value overshot by 28.
    for value, count in enumerate(RETAIL["F"]["agility"]):
        assert abs(got[value] - count) <= 5


def test_youth_is_the_birth_date_where_the_roster_has_one(mapper):
    # The NHL API's rosters carry a birth date and no age.
    league_lines = [
        (_skater(id=i + 1, position="G", birth_date=f"{1985 + i}-06-01"), {"GP": 30, "SV%": 0.9})
        for i in range(20)
    ]
    youngest, oldest = (_map(mapper, league_lines, i) for i in (19, 0))
    assert (youngest.speed, oldest.speed) == (4, 3)


def test_a_goalie_without_age_keeps_the_cartridges_slowest_goalie_speed(mapper):
    record = mapper.map_player(_skater(position="G"), "BOS", {"GP": 30, "SV%": 0.9})
    assert record.attributes.speed == GOALIE_DEFAULTS.speed == 3


def test_a_goalie_ranks_on_save_percentage_goals_against_and_wins(mapper):
    lines = [
        {"GP": 40, "W": 15 + i % 10, "SV%": 0.890 + 0.001 * i, "GAA": 3.2 - 0.02 * i}
        for i in range(30)
    ]
    lines.append({"GP": 60, "W": 42, "SV%": 0.925, "GAA": 2.10})
    best = _map(mapper, _goalies(lines), 30)
    worst = _map(mapper, _goalies(lines), 0)
    assert isinstance(best, NHL94GenGoalieAttributes)
    assert (best.agility, best.def_awareness, best.puck_control) == (6, 6, 6)
    assert best.agility > worst.agility and best.def_awareness > worst.def_awareness
    assert min(best.stick_right, best.stick_left, best.glove_right, best.glove_left) >= 5
    assert best.speed in (3, 4) and worst.speed in (3, 4)


def test_a_goalie_line_without_save_percentage_keeps_the_default_agility(mapper):
    # It used to read a missing save % as 0 and rate the goalie's agility 0.
    lines = [{"GP": 30, "SV%": 0.900 + 0.001 * i, "GAA": 3.0} for i in range(10)]
    lines.append({"GP": 30, "GAA": 2.8})
    goalie = _map(mapper, _goalies(lines), 10)
    assert goalie.agility == GOALIE_DEFAULTS.agility


def test_a_goalie_without_a_stat_line_gets_the_goalie_defaults(mapper):
    record = mapper.map_player(_skater(position="G", number=1), "BOS")
    assert record.attributes == GOALIE_DEFAULTS
    assert record.attributes is not GOALIE_DEFAULTS


def test_one_line_alone_is_ranked_in_the_middle(mapper):
    # Without a league, a line is its own league: every stat it has sits at the
    # middle of the cartridge's spread.
    attrs = mapper.map_player(_skater(), "BOS", {"GP": 82, "G": 30, "S": 200}).attributes
    assert attrs.shot_power == on_retail(0.5, RETAIL["F"]["shot_power"])
    assert attrs.stick_handling == on_retail(0.5, RETAIL["F"]["stick_handling"])


def test_only_the_g_position_is_flagged_as_a_goalie(mapper):
    flags = [
        mapper.map_player(_skater(position=pos), "BOS").is_goalie
        for pos in ("C", "LW", "RW", "D", "G")
    ]
    assert flags == [False, False, False, False, True]


def test_a_lower_case_position_still_reads_as_a_goalie(mapper):
    # `map_player` upper-cases before comparing, and the NHL provider returns
    # position codes as they appear in the payload.
    record = mapper.map_player(_skater(position="g"), "BOS", {"SV%": 0.930, "GAA": 2.00})

    assert record.position == "G"
    assert record.is_goalie is True


def test_weight_is_mapped_in_eight_pound_steps_from_one_hundred_and_forty(mapper):
    # `(lbs - 140) // 8`, clamped 0-14, checked at every boundary that decides a
    # different nibble. 139 is below the floor and would otherwise be -1; 147 is
    # the last pound still in class 0 and 148 the first in class 1; 252 is the
    # first pound in the top class and 253 and 260 are clamped down to it.
    weights = (139.0, 140.0, 147.0, 148.0, 200.0, 252.0, 253.0, 260.0)
    classes = [mapper.map_player(_skater(weight=w), "BOS").weight_class for w in weights]

    assert classes == [0, 0, 0, 1, 7, 14, 14, 14]


def test_a_player_with_no_listed_weight_gets_the_middle_class(mapper):
    # 0.0 is what `Player` defaults to when the provider omits the field, and it
    # must not fall through `_map_weight`, which would answer class 0 — a 140 lb
    # NHL player. The fallback is class 7, roughly 196 lbs.
    record = mapper.map_player(_skater(weight=0.0), "BOS")

    assert record.weight_class == 7


def test_a_fractional_weight_is_truncated_before_the_class_is_computed(mapper):
    # `int()` truncates rather than rounds, so 147.9 stays in class 0. The
    # provider fields are floats.
    classes = [mapper.map_player(_skater(weight=w), "BOS").weight_class for w in (147.9, 148.1)]

    assert classes == [0, 1]


def test_handedness_is_one_for_right_and_zero_for_everything_else(mapper):
    # The nibble is written into the low half of stat byte 4 by
    # `rom_writer._write_player_stats`, so this is a ROM byte, not a label. Only
    # an exact "R" raises it; "L", the empty string a provider omission leaves
    # behind, and a None that has crossed a JSON boundary all mean left.
    hands = [
        mapper.map_player(_skater(handedness=h), "BOS").handedness
        for h in ("R", "L", "", None, "r")
    ]

    assert hands == [1, 0, 0, 0, 0]


def test_two_records_at_one_position_do_not_share_an_attributes_object(mapper):
    first = mapper.map_player(_skater(id=1, name="ONE"), "BOS")
    second = mapper.map_player(_skater(id=2, name="TWO"), "BOS")

    assert (first.attributes is second.attributes) is False
    assert first.attributes == second.attributes


def test_a_mapped_record_never_holds_the_module_default_itself(mapper):
    record = mapper.map_player(_skater(position="D"), "BOS")

    assert (record.attributes is POSITION_DEFAULTS["D"]) is False
    assert record.attributes == POSITION_DEFAULTS["D"]


def test_editing_one_records_attributes_leaves_the_module_default_alone(
    mapper, position_defaults_restored
):
    # `NHL94GenPlayerRecord` is public API and its `attributes` field is a plain
    # mutable dataclass, so one caller assignment is all it takes. While every
    # record shared the constant, that assignment rewrote the defaults for every
    # later player of that position in the process.
    first = mapper.map_player(_skater(position="D", id=1), "BOS")
    second = mapper.map_player(_skater(position="D", id=2), "BOS")

    first.attributes.checking = 6

    assert POSITION_DEFAULTS["D"].checking == 4
    assert second.attributes.checking == 4


def test_an_unknown_position_falls_back_to_centre_without_aliasing_it(mapper):
    # `POSITION_DEFAULTS.get(pos, POSITION_DEFAULTS["C"])` — the fallback arm is
    # a second route to the same shared object. The NHL provider returns "W" for
    # a winger whose side is unrecorded, so this is reachable.
    record = mapper.map_player(_skater(position="W"), "BOS")

    assert record.position == "W"
    assert record.attributes == POSITION_DEFAULTS["C"]
    assert (record.attributes is POSITION_DEFAULTS["C"]) is False


def test_a_player_with_no_jersey_number_is_given_one(mapper):
    # `player.number or 1` — the ROM stores the jersey as BCD and the writer
    # clamps 0 up to 1 anyway, but the record is public API and must not carry a
    # None the writer would compare against.
    numbers = [mapper.map_player(_skater(number=n), "BOS").jersey_number for n in (None, 0, 99, 7)]

    assert numbers == [1, 1, 99, 7]


def test_a_name_longer_than_the_rom_record_is_cut_to_fourteen_characters(mapper):
    record = mapper.map_player(_skater(name="ALEKSANDER BARKOV JR"), "BOS")

    assert record.name == "ALEKSANDER BAR"
    assert len(record.name) == 14
