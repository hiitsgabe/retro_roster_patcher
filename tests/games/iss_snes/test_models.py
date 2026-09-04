"""The two disjoint 27-entry team orders, and the one place that translates.

`name_storage_index` is the whole subject of this file. ISS stores player
*names* in one order and player *data*, kits, flags and colours in another, and
the two differ by a single element: Scotland is at index 5 of the enum order and
at index 24 of the name order, so every slot between them shifts by one. Get it
wrong and fifteen names land on the wrong national side, for twenty of the
twenty-seven slots, while every other byte the patcher writes stays correct.

The fixture module holds an independent transcription of both lists, and every
assertion below is against that transcription rather than against the module
under test, so a permutation applied to both lists in `models.py` still fails
here.
"""

from __future__ import annotations

import pytest

from retro_roster_patcher.games.iss_snes.models import (
    HAIR_STYLES,
    PLAYERS_PER_TEAM,
    TEAM_ENUM_ORDER,
    TEAM_NAME_ORDER,
    TOTAL_TEAMS,
    ISSPlayerAttributes,
    ISSPlayerRecord,
    ISSTeamRecord,
    name_storage_index,
)
from tests.fixtures import synthetic_iss_rom as fixture


def test_there_are_twenty_seven_slots_in_each_order():
    assert len(TEAM_ENUM_ORDER) == TOTAL_TEAMS
    assert len(TEAM_NAME_ORDER) == TOTAL_TEAMS


def test_the_enum_order_matches_the_fixtures_independent_transcription():
    assert TEAM_ENUM_ORDER == fixture.TEAM_ENUM_ORDER


def test_the_name_order_matches_the_fixtures_independent_transcription():
    assert TEAM_NAME_ORDER == fixture.TEAM_NAME_ORDER


def test_the_two_orders_hold_the_same_twenty_seven_names():
    """What makes every `name_storage_index` lookup total.

    `_NAME_ORDER_INDEX` is built with `TEAM_NAME_ORDER.index(...)`, which raises
    for a name the second list does not hold -- at import time, so the whole
    package would fail to load.
    """
    assert set(TEAM_ENUM_ORDER) == set(TEAM_NAME_ORDER)


def test_no_slot_name_is_repeated():
    """`RomSlot.display_name` is filled from this list and has to be distinct."""
    assert len(set(TEAM_ENUM_ORDER)) == TOTAL_TEAMS


def test_the_two_orders_are_not_the_same_list():
    """The premise of this whole file. Without this the tests below are vacuous."""
    assert TEAM_ENUM_ORDER != TEAM_NAME_ORDER


def test_scotland_is_the_only_slot_the_two_orders_disagree_about_by_more_than_one():
    """Exactly one name moves; everything after it shifts by one and no more.

    Stated as the set of slots whose translation is the identity: the five
    before Scotland, and the two after where it lands. The nineteen between them
    shift down by one. A change that moved a *second* team would leave this set
    the wrong size.
    """
    identity = {i for i in range(TOTAL_TEAMS) if name_storage_index(i) == i}
    assert identity == {0, 1, 2, 3, 4, 25, 26}


def test_the_nineteen_slots_between_shift_down_by_exactly_one():
    shifted = {i: name_storage_index(i) for i in range(6, 25)}
    assert shifted == {i: i - 1 for i in range(6, 25)}


def test_scotland_moves_from_slot_five_to_storage_index_twenty_four():
    assert TEAM_ENUM_ORDER[5] == "Scotland"
    assert name_storage_index(5) == 24
    assert TEAM_NAME_ORDER[24] == "Scotland"


def test_wales_moves_down_one_because_scotland_left():
    assert TEAM_ENUM_ORDER[6] == "Wales"
    assert name_storage_index(6) == 5


@pytest.mark.parametrize("slot", range(TOTAL_TEAMS))
def test_every_slot_translates_to_the_position_of_its_own_name(slot):
    """Derived from the fixture's lists, not from the function being tested."""
    assert name_storage_index(slot) == fixture.name_storage_index(slot)


def test_the_translation_is_a_permutation():
    """No two slots share a storage index, so no team can overwrite another."""
    assert sorted(name_storage_index(i) for i in range(TOTAL_TEAMS)) == list(range(TOTAL_TEAMS))


def test_a_slot_past_the_last_one_raises_rather_than_answering_an_offset():
    with pytest.raises(IndexError):
        name_storage_index(TOTAL_TEAMS)


def test_a_negative_slot_would_wrap_and_is_the_callers_to_bound():
    """Python's negative indexing, documented here so it is not a surprise.

    Both call sites bound their slot before they get here -- `map_rosters`
    raises `MappingError` and `patch` filters -- so this is a statement about
    where the guard lives, not an accepted hole.
    """
    assert name_storage_index(-1) == name_storage_index(TOTAL_TEAMS - 1)


def test_fifteen_players_a_side():
    assert PLAYERS_PER_TEAM == 15


def test_the_hair_style_table_has_eleven_entries():
    """`write_player_data` clamps to `len(HAIR_STYLES) - 1`, so this is the 10."""
    assert len(HAIR_STYLES) == 11


def test_a_player_record_defaults_to_its_own_attributes_object():
    """`default_factory`, not a shared instance handed to every record."""
    first = ISSPlayerRecord(name="A")
    second = ISSPlayerRecord(name="B")
    first.attributes.speed = 1
    assert second.attributes.speed == 8


def test_a_team_record_defaults_to_its_own_player_list():
    first = ISSTeamRecord(name="A", short_name="AAA")
    second = ISSTeamRecord(name="B", short_name="BBB")
    first.players.append(ISSPlayerRecord(name="X"))
    assert second.players == []


def test_a_team_record_defaults_to_its_own_flag_colour_list():
    first = ISSTeamRecord(name="A", short_name="AAA")
    second = ISSTeamRecord(name="B", short_name="BBB")
    first.flag_colors.append((1, 2, 3))
    assert second.flag_colors == []


def test_the_default_attributes_are_the_midpoints_of_the_two_scales():
    attrs = ISSPlayerAttributes()
    assert attrs.speed == 8
    assert attrs.stamina == 8
    assert attrs.shooting == 7
    assert attrs.technique == 7
