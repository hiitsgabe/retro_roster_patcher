"""Stat mapper coverage: the provider payload turned into ROM nibbles.

Every assertion is on the resulting integers rather than on the shape of the
record. The attributes are packed as 0-6 nibbles by `rom_writer.encode_nibble`,
so a mapper that returned the right field names carrying the wrong numbers
produces a ROM that loads and plays wrong.

The goalie arm and `POSITION_DEFAULTS["G"]` agree on ten of the twelve
attributes; only `agility` and `def_awareness` are derived. Inputs here are
chosen so those two land on values the defaults do not carry, otherwise a mapper
that ignored the stats entirely would satisfy the same equality.
"""

import dataclasses

import pytest

from retro_roster_patcher.games.nhl94_genesis.models import NHL94GenPlayerAttributes
from retro_roster_patcher.games.nhl94_genesis.stat_mapper import (
    POSITION_DEFAULTS,
    NHL94GenStatMapper,
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


def test_a_goalies_save_percentage_and_gaa_become_agility_and_def_awareness(mapper):
    # The whole goalie arm, pinned as integers. .920 sits four fifths of the way
    # up the .880-.930 band, so agility is 5; a 2.40 GAA is 1.10 of the 1.50 the
    # band allows below 3.50, so def_awareness is 4. Neither is the 4 and 3 that
    # `POSITION_DEFAULTS["G"]` carries, so a mapper that skipped the stats and
    # returned the defaults cannot satisfy this.
    record = mapper.map_player(
        _skater(position="G", number=31, weight=190.0, handedness="L"),
        "BOS",
        {"SV%": 0.920, "GAA": 2.40},
    )

    assert record.is_goalie is True
    assert record.attributes == NHL94GenPlayerAttributes(
        speed=2,
        agility=5,
        shot_power=2,
        shot_accuracy=2,
        stick_handling=3,
        pass_accuracy=2,
        off_awareness=2,
        def_awareness=3 + 1,
        checking=1,
        endurance=4,
        roughness=1,
        aggression=1,
    )


def test_a_weak_goalie_scales_down_the_same_two_attributes(mapper):
    # The other end of both bands, so the two derived values are a scale rather
    # than a pair of constants: .885 is one tenth up the save-percentage band and
    # a 3.40 GAA leaves 0.10 of 1.50.
    record = mapper.map_player(
        _skater(position="G", number=1),
        "BOS",
        {"SV%": 0.885, "GAA": 3.40},
    )

    assert record.attributes.agility == 1
    assert record.attributes.def_awareness == 0


def test_a_goalie_at_the_top_of_both_bands_saturates_at_six(mapper):
    record = mapper.map_player(
        _skater(position="G", number=1),
        "BOS",
        {"SV%": 0.930, "GAA": 2.00},
    )

    assert record.attributes.agility == 6
    assert record.attributes.def_awareness == 6


def test_a_goalie_stat_line_with_no_save_percentage_clamps_to_zero(mapper):
    # A stats dict that exists but carries neither key is what an off-season or a
    # partial provider response looks like. SV% defaults to 0, which is far below
    # the band and clamps up from -106 to 0; GAA defaults to 3.00, which is a
    # third of the way down the 1.50 band, so def_awareness is 2 and not 0.
    record = mapper.map_player(_skater(position="G", number=1), "BOS", {"PTS": 0})

    assert record.attributes.agility == 0
    assert record.attributes.def_awareness == 2


def test_a_goalie_without_a_stat_line_falls_back_to_the_position_defaults(mapper):
    # No stats at all is the other arm of the branch, and it must not reach
    # `_map_stats`: the defaults carry agility 4 and def_awareness 3, which the
    # goalie arm would only produce for a very particular SV%/GAA pair.
    record = mapper.map_player(_skater(position="G", number=1), "BOS")

    assert record.attributes == POSITION_DEFAULTS["G"]
    assert record.attributes.agility == 4
    assert record.attributes.def_awareness == 3


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
