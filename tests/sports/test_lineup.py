"""Folding a real formation to three lines, and reading the role, side and line of its places."""

import pytest

from retro_roster_patcher.sports.lineup import (
    DEPTH,
    ROLE_BY_CODE,
    ROLE_OVERRIDES,
    choose_shape,
    code_line,
    fold_formation,
    slot_lines,
    slot_role,
    slot_side,
)


@pytest.mark.parametrize(
    "real, folded",
    [
        ("4-2-3-1", "4-3-3"),
        ("3-4-2-1", "3-4-3"),
        ("4-4-1-1", "4-4-2"),
        ("4-1-4-1", "4-5-1"),
        ("4-3-2-1", "4-3-3"),
        ("4-1-2-1-2", "4-4-2"),
        ("3-4-1-2", "3-5-2"),
        ("4-4-2", "4-4-2"),
        ("5-3-2", "5-3-2"),
        ("", ""),
        ("4-4-x", "4-4-x"),
    ],
)
def test_fold_formation_follows_the_approved_table(real, folded):
    assert fold_formation(real) == folded


@pytest.mark.parametrize("shape", ["4-3-3", "3-4-3", "4-5-1", "5-4-1", "6-3-1"])
def test_a_three_line_formation_stays_as_it_is(shape):
    # 6-3-1 is the one the one-striker rule would move if it reached three lines.
    assert fold_formation(shape) == shape


@pytest.mark.parametrize("text", ["", "4-4-x", "442", "4-6", "4-4-3", "4-2-3-2"])
def test_text_that_is_not_ten_outfield_players_in_three_lines_or_more_is_unusable(text):
    assert fold_formation(text) == text
    assert choose_shape([text]) is None


def test_the_shape_is_the_most_used_of_the_last_five():
    newest_first = ["4-2-3-1", "4-3-3", "4-4-2", "4-4-2", "4-2-3-1", "4-4-2", "4-4-2"]
    assert choose_shape(newest_first) == "4-3-3"


def test_a_tied_shape_goes_to_the_latest_match():
    assert choose_shape(["4-4-2", "4-3-3"]) == "4-4-2"


def test_formations_that_do_not_parse_are_skipped():  # Review Focus 1
    assert choose_shape(["", "4-4-x", "4-4-2"]) == "4-4-2"
    assert choose_shape(["", ""]) is None


def test_formations_that_do_not_parse_do_not_use_up_the_window():
    # The first five of this list would give 4-3-3 if the empty ones counted toward the five.
    assert choose_shape(["", "", "4-4-2", "4-3-3", "4-3-3", "4-4-2", "4-4-2", "4-3-3"]) == "4-4-2"


def test_a_code_reads_inside_its_formation():
    assert slot_role("4-2-3-1", "LM") == "CDM"
    assert slot_role("4-4-2", "LM") == "LM"
    assert slot_role("3-4-2-1", "RM") == "RWB"
    assert slot_role("4-3-3", "CM") == "CDM"
    assert slot_role("4-3-3", "RM") == "CM"
    assert slot_role("4-2-3-1", "AM-R") == "RW"
    assert slot_role("4-4-2", "ZZ") == "CM"


def test_sides_come_from_the_code():
    codes = ("CD-L", "RB", "F", "AM", "LM", "RCF", "G")
    assert [slot_side(c) for c in codes] == ["L", "R", "C", "C", "L", "R", "C"]


def test_places_cut_into_the_folded_shape():
    a4231 = ["GK", "RB", "LB", "CDM", "CB", "CB", "RW", "CDM", "ST", "CAM", "LW"]
    lines4231 = ["GK", "DF", "DF", "MF", "DF", "DF", "FW", "MF", "FW", "MF", "FW"]
    assert slot_lines("4-3-3", a4231) == lines4231
    a3421 = ["GK", "RWB", "LWB", "CB", "CB", "CB", "CM", "CM", "ST", "RW", "LW"]
    lines3421 = ["GK", "MF", "MF", "DF", "DF", "DF", "MF", "MF", "FW", "FW", "FW"]
    assert slot_lines("3-4-3", a3421) == lines3421


def test_places_fall_back_to_depth_buckets_when_the_shape_does_not_fit():
    a3421 = ["GK", "RWB", "LWB", "CB", "CB", "CB", "CM", "CM", "ST", "RW", "LW"]
    by_depth = ["GK", "DF", "DF", "DF", "DF", "DF", "MF", "MF", "FW", "FW", "FW"]
    for shape in ("", "4-4-x", "4-4-1"):
        assert slot_lines(shape, a3421) == by_depth
    # A good shape for ten outfield places does not fit nine.
    assert slot_lines("3-4-3", a3421[:-1]) == by_depth[:-1]


def test_a_formation_without_overrides_still_cuts_to_its_fold():  # Review Focus 2
    codes = ["G", "RB", "LB", "CM-R", "CD-R", "CD-L", "RM", "CM-L", "F", "AM", "LM"]
    lines = slot_lines(fold_formation("4-4-1-1"), [slot_role("4-4-1-1", c) for c in codes])
    assert (lines.count("DF"), lines.count("MF"), lines.count("FW")) == (4, 4, 2)
    assert lines[codes.index("AM")] == "FW"


def test_a_code_alone_has_a_line():
    codes = ("G", "CD-L", "LM", "AM-L", "F", "SUB")
    assert [code_line(c) for c in codes] == ["GK", "DF", "MF", "FW", "FW", None]


def test_every_role_a_code_can_read_as_has_a_depth():
    overridden = {role for roles in ROLE_OVERRIDES.values() for role in roles.values()}
    assert (set(ROLE_BY_CODE.values()) | overridden) <= set(DEPTH)
