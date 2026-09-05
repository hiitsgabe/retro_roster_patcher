"""`games/mvp_psp/models.py`: the constants and the invariants derived from them.

Three of these pin facts the source relied on and never checked, and one of the
three is what the migration brief called an undeclared invariant duplicated in
two files:

  * slot order. `patcher.py:160` and `rom_reader.py:250` both indexed
    `list(TEAM_HASHES.keys())` by slot number, so the dict's insertion order
    *was* the slot ordering, in two files, with nothing asserting it.
  * `AL_SLOT_COUNT`. Written inline as `team_index < 14` with no explanation.
  * the section table. Nineteen entries, ascending, and every allocation
    positive -- a table whose offsets went backwards would give a section a
    negative allocation and silently write nothing.

Nothing here has been checked against a real disc; no ISO may enter this
repository. These tests pin what the source said, and say so.
"""

from __future__ import annotations

import dataclasses

import pytest

from retro_roster_patcher.games.mvp_psp import models
from retro_roster_patcher.games.mvp_psp.models import (
    AL_SLOT_COUNT,
    ATTR_MAX,
    ATTR_MIN,
    ATTRIB_POS_1B,
    ATTRIB_POS_CF,
    ATTRIB_POS_PITCHER,
    ATTRIB_POS_RELIEVER,
    BATTERS_PER_TEAM,
    BULLPEN_POSITIONS,
    COMPACT_ATTRIB_TABLE,
    DATABASE_BIG_LBA,
    DATABASE_BIG_SIZE,
    HASH_ID_CHARS,
    ISO_SECTOR_SIZE,
    LINEUP_POSITIONS,
    MODERN_MLB_TO_MVP,
    MODIFIED_TABLES,
    MVP_ABBREV_TO_INDEX,
    MVP_TEAM_ABBREVS,
    MVP_TEAM_ORDER,
    NOT_IN_LINEUP,
    PITCHERS_PER_TEAM,
    PLAYER_TABLES,
    PLAYERS_PER_TEAM,
    POS_STRING_TO_NUM,
    RELIEVERS_PER_TEAM,
    ROSTER_TABLE,
    ROTATION_POSITIONS,
    SECTION_ALLOCATIONS,
    SECTION_COUNT,
    SECTION_MAP,
    SELECTION_POSITIONS,
    STARTERS_PER_TEAM,
    TEAM_COUNT,
    TEAM_HASHES,
    MVPPitch,
    MVPPlayerRecord,
    database_big_extent,
)

# The fourteen clubs that played in the American League in 2005, which is the
# season this disc holds. Written out here from the league's own membership, so
# `AL_SLOT_COUNT` is checked against baseball rather than against itself.
AL_CLUBS_2005 = {
    "ANA",
    "OAK",
    "SEA",
    "TEX",
    "CWS",
    "CLE",
    "DET",
    "KC",
    "MIN",
    "BAL",
    "BOS",
    "NYY",
    "TB",
    "TOR",
}


# -- the section table -----------------------------------------------------


def test_the_database_holds_nineteen_sections():
    # Nineteen, not the eighteen the source's docstring claimed in three
    # places. The count is what `analyze_rom` reports as `sections_read`.
    assert SECTION_COUNT == 19


def test_every_section_name_is_distinct():
    assert len({name for _, name in SECTION_MAP}) == SECTION_COUNT


def test_the_section_offsets_ascend():
    offsets = [offset for offset, _ in SECTION_MAP]
    assert offsets == sorted(offsets)


def test_no_two_sections_share_an_offset():
    assert len({offset for offset, _ in SECTION_MAP}) == SECTION_COUNT


def test_the_first_section_starts_at_zero():
    assert SECTION_MAP[0][0] == 0


def test_the_last_section_starts_inside_the_blob():
    assert SECTION_MAP[-1][0] < DATABASE_BIG_SIZE


@pytest.mark.parametrize("name", [name for _, name in SECTION_MAP])
def test_every_section_has_a_positive_allocation(name):
    # A negative or zero allocation would make `rebuild_database_big` write a
    # zero-length slice and zero-fill backwards.
    _, allocation = SECTION_ALLOCATIONS[name]
    assert allocation > 0


def test_the_allocations_tile_the_whole_blob_without_a_gap():
    total = sum(allocation for _, allocation in SECTION_ALLOCATIONS.values())
    assert total == DATABASE_BIG_SIZE


def test_each_allocation_reaches_exactly_to_the_next_section():
    ends = [offset + allocation for offset, allocation in SECTION_ALLOCATIONS.values()]
    starts = [offset for offset, _ in SECTION_MAP][1:] + [DATABASE_BIG_SIZE]
    assert sorted(ends) == starts


def test_the_last_sections_allocation_runs_to_the_end_of_the_blob():
    offset, allocation = SECTION_ALLOCATIONS[SECTION_MAP[-1][1]]
    assert offset + allocation == DATABASE_BIG_SIZE


def test_the_compact_attribute_table_is_the_first_section():
    assert SECTION_MAP[0][1] == COMPACT_ATTRIB_TABLE


def test_the_four_player_tables_are_the_ones_the_patcher_merges_into():
    assert PLAYER_TABLES == ("attrib", "lrattrib_rhp", "lrattrib_lhp", "pitchattrib")


def test_the_roster_table_is_the_one_rebuilt_wholesale():
    assert ROSTER_TABLE == "roster"


def test_the_modified_tables_are_the_player_tables_plus_the_roster():
    assert MODIFIED_TABLES == (*PLAYER_TABLES, ROSTER_TABLE)


@pytest.mark.parametrize("name", MODIFIED_TABLES)
def test_every_modified_table_is_a_real_section(name):
    assert name in SECTION_ALLOCATIONS


def test_the_compact_attribute_table_is_not_among_the_modified_ones():
    assert COMPACT_ATTRIB_TABLE not in MODIFIED_TABLES


# -- the section offsets, written out ---------------------------------------
#
# The tests above check that the section table is *self-consistent* -- ascending,
# distinct, tiling the blob without a gap. Every one of them still passes if the
# whole table is shifted, and mutation testing moved a single offset by four
# bytes and by one and watched the suite stay green: `SECTION_ALLOCATIONS` is
# derived from `SECTION_MAP`, and `tests/fixtures/synthetic_mvp_iso.py` writes
# its sections at the offsets `SECTION_ALLOCATIONS` gives it, so the fixture
# moves with the code and the round trip closes over the error.
#
# These are the numbers themselves. They are the source's, they have never been
# checked against a real disc, and no disc may enter this repository to check
# them -- so this is not a derivation, it is the record. A change to one of them
# is a change to a disc's layout and has to be a change to this list.

SECTION_OFFSETS = (
    (0, "attrib_compact"),
    (324, "attrib"),
    (61772, "lrattrib_rhp"),
    (101852, "lrattrib_lhp"),
    (144692, "batstat"),
    (165552, "fieldstat"),
    (188428, "lrbatstat_rhp"),
    (214440, "lrpitchstat_rhp"),
    (229676, "pitchstat"),
    (245436, "lrbatstat_lhp"),
    (274488, "lrpitchstat_lhp"),
    (290260, "pitchattrib"),
    (313720, "team"),
    (317176, "teamstat"),
    (317752, "roster"),
    (335616, "careerstats"),
    (366772, "pitchcareer"),
    (384620, "organization"),
    (385608, "manager"),
)


def test_the_section_table_is_the_nineteen_offsets_the_source_had():
    assert SECTION_MAP == SECTION_OFFSETS


def test_the_blob_is_three_hundred_and_eighty_six_thousand_nine_hundred_and_seventy_seven_bytes():
    # The last section's allocation is the distance from 385 608 to here, so
    # this number is half of one section's size and not only the read length.
    assert DATABASE_BIG_SIZE == 386977


@pytest.mark.parametrize(("offset", "name"), SECTION_OFFSETS)
def test_every_section_starts_where_the_source_said(offset, name):
    assert SECTION_ALLOCATIONS[name][0] == offset


# -- the column numbers, written out ----------------------------------------
#
# A record here is `id,fieldnum value,fieldnum value,...,;`, so these constants
# are column *names* rather than addresses: a wrong one writes a real column of
# a real record with the wrong meaning, and nothing crashes. And a field written
# at the wrong number and read back from the same wrong number passes forever --
# every test in this package that reads a column back reads it through the same
# constant that wrote it, and mutation testing duly walked `ATTRIB_WEIGHT` from
# 10 to 11 and `ROSTER_RH_AL_ORDER` from 3 to 13 without turning anything red.
#
# So the numbers are written out. They came off a real disc that cannot enter
# this repository, they cannot be derived from anything here, and this list is
# the only place they are stated twice.

ATTRIB_COLUMNS = {
    "ATTRIB_FIRST_NAME": 0,
    "ATTRIB_LAST_NAME": 1,
    "ATTRIB_JERSEY": 2,
    "ATTRIB_BATS": 3,
    "ATTRIB_THROWS": 4,
    "ATTRIB_PRIMARY_POS": 5,
    "ATTRIB_SECONDARY_POS": 6,
    "ATTRIB_HEIGHT": 9,
    "ATTRIB_WEIGHT": 10,
    "ATTRIB_PLATE_DISCIPLINE": 18,
    "ATTRIB_BUNTING": 19,
    "ATTRIB_STEALING_AGGRESSIVE": 20,
    "ATTRIB_BASERUNNING": 21,
    "ATTRIB_SPEED": 22,
    "ATTRIB_FIELDING": 23,
    "ATTRIB_RANGE": 24,
    "ATTRIB_THROW_STRENGTH": 25,
    "ATTRIB_THROW_ACCURACY": 26,
    "ATTRIB_DURABILITY": 27,
    "ATTRIB_SALARY": 39,
    "ATTRIB_CONTRACT_LENGTH": 40,
    "ATTRIB_STARPOWER": 41,
    "ATTRIB_BIRTHDAY": 43,
}

LR_COLUMNS = {
    "LR_FIRST_NAME": 0,
    "LR_LAST_NAME": 1,
    "LR_CONTACT": 2,
    "LR_POWER": 3,
    "LR_SPRAY_UL": 4,
    "LR_SPRAY_UM": 5,
    "LR_SPRAY_UR": 6,
    "LR_SPRAY_CL": 7,
    "LR_SPRAY_CM": 8,
    "LR_SPRAY_CR": 9,
    "LR_SPRAY_LL": 10,
    "LR_SPRAY_LM": 11,
    "LR_SPRAY_LR": 12,
    "LR_FIELD_PCT_LF": 13,
    "LR_FIELD_PCT_CF": 14,
    "LR_FIELD_PCT_RF": 15,
    "LR_HR_PCT": 16,
    "LR_FB": 17,
    "LR_LD": 18,
    "LR_GB": 19,
}

PITCHATTRIB_COLUMNS = {
    "PA_FIRST_NAME": 0,
    "PA_LAST_NAME": 1,
    "PA_STAMINA": 2,
    "PA_PICKOFF": 3,
    "PA_PITCH1_MOVEMENT": 4,
    "PA_PITCH1_DESC": 5,
    "PA_PITCH1_CONTROL": 6,
    "PA_PITCH1_VELOCITY": 7,
    "PA_PITCH2_TYPE": 8,
    "PA_PITCH2_MOVEMENT": 9,
    "PA_PITCH2_DESC": 10,
    "PA_PITCH2_CONTROL": 11,
    "PA_PITCH2_VELOCITY": 12,
    "PA_PITCHER_DELIVERY": 28,
}

ROSTER_COLUMNS = {
    "ROSTER_TEAMID": 0,
    "ROSTER_PLAYERID": 1,
    "ROSTER_RH_AL_POS": 2,
    "ROSTER_RH_AL_ORDER": 3,
    "ROSTER_RH_NL_POS": 4,
    "ROSTER_RH_NL_ORDER": 5,
    "ROSTER_LH_AL_POS": 6,
    "ROSTER_LH_AL_ORDER": 7,
    "ROSTER_LH_NL_POS": 8,
    "ROSTER_LH_NL_ORDER": 9,
}

TEAM_COLUMNS = {
    "TEAM_NAME": 0,
    "TEAM_LEAGUE": 1,
    "TEAM_DIVISION": 2,
    "TEAM_ARTID": 3,
}

PITCH_BLOCK_SHAPE = {
    "PA_PITCH_STRIDE": 5,
    "PA_PITCH_TYPE_OFFSET": 0,
    "PA_PITCH_MOVEMENT_OFFSET": 1,
    "PA_PITCH_CONTROL_OFFSET": 3,
    "PA_PITCH_VELOCITY_OFFSET": 4,
    "MAX_EXTRA_PITCHES": 3,
}

POSITION_CODES = {
    "ATTRIB_POS_PITCHER": 0,
    "ATTRIB_POS_C": 1,
    "ATTRIB_POS_1B": 2,
    "ATTRIB_POS_2B": 3,
    "ATTRIB_POS_3B": 4,
    "ATTRIB_POS_SS": 5,
    "ATTRIB_POS_LF": 6,
    "ATTRIB_POS_CF": 7,
    "ATTRIB_POS_RF": 8,
    "ATTRIB_POS_RELIEVER": 10,
}

PITCH_TYPE_CODES = {
    "PITCH_FASTBALL": 1,
    "PITCH_SLIDER": 3,
    "PITCH_CHANGEUP": 4,
}

DECLARED_NUMBERS = {
    **ATTRIB_COLUMNS,
    **LR_COLUMNS,
    **PITCHATTRIB_COLUMNS,
    **ROSTER_COLUMNS,
    **TEAM_COLUMNS,
    **PITCH_BLOCK_SHAPE,
    **POSITION_CODES,
    **PITCH_TYPE_CODES,
    "HASH_ID_CHARS": 9,
    "AL_SLOT_COUNT": 14,
    "NOT_IN_LINEUP": -1,
}


@pytest.mark.parametrize(("name", "number"), sorted(DECLARED_NUMBERS.items()))
def test_a_declared_number_is_what_the_source_declared(name, number):
    assert getattr(models, name) == number


@pytest.mark.parametrize(
    "columns", [ATTRIB_COLUMNS, LR_COLUMNS, PITCHATTRIB_COLUMNS, ROSTER_COLUMNS, TEAM_COLUMNS]
)
def test_no_two_columns_of_one_table_share_a_number(columns):
    assert len(set(columns.values())) == len(columns)


def test_the_second_pitch_is_the_base_of_the_repeating_block():
    # `_build_pitchattrib_fields` addresses pitches 2-4 as
    # `PA_PITCH2_TYPE + i * PA_PITCH_STRIDE + offset`, so the four `PA_PITCH2_*`
    # constants have to be that block's first instance or the named ones and the
    # computed ones disagree.
    computed = {
        models.PA_PITCH2_TYPE + models.PA_PITCH_TYPE_OFFSET,
        models.PA_PITCH2_TYPE + models.PA_PITCH_MOVEMENT_OFFSET,
        models.PA_PITCH2_TYPE + models.PA_PITCH_CONTROL_OFFSET,
        models.PA_PITCH2_TYPE + models.PA_PITCH_VELOCITY_OFFSET,
    }
    assert computed == {
        models.PA_PITCH2_TYPE,
        models.PA_PITCH2_MOVEMENT,
        models.PA_PITCH2_CONTROL,
        models.PA_PITCH2_VELOCITY,
    }


def test_the_last_pitch_the_patcher_writes_stops_before_the_delivery_column():
    # Three extra pitches after the fastball puts the last velocity at column
    # 22, and the delivery column is 28, so pitch 5's block (23-27) is the gap.
    last = models.PA_PITCH2_TYPE + (models.MAX_EXTRA_PITCHES - 1) * models.PA_PITCH_STRIDE
    assert last + models.PA_PITCH_VELOCITY_OFFSET == 22


# -- the extent ------------------------------------------------------------


def test_the_extent_starts_where_the_lba_says():
    start, _ = database_big_extent()
    assert start == 685735936


def test_the_extent_ends_after_the_declared_size():
    _, end = database_big_extent()
    assert end == 686122913


def test_the_extent_is_exactly_the_declared_size_long():
    start, end = database_big_extent()
    assert end - start == DATABASE_BIG_SIZE


def test_the_extent_start_is_the_lba_times_the_sector_size():
    start, _ = database_big_extent()
    assert start == DATABASE_BIG_LBA * ISO_SECTOR_SIZE


def test_a_mode_one_sector_is_two_thousand_and_forty_eight_bytes():
    assert ISO_SECTOR_SIZE == 2048


def test_the_extent_follows_a_patched_lba(monkeypatch):
    # The whole reason the extent is a function: one `setattr` moves it for the
    # reader, the writer and the patcher together.
    import retro_roster_patcher.games.mvp_psp.models as models

    monkeypatch.setattr(models, "DATABASE_BIG_LBA", 40)
    assert database_big_extent() == (81920, 81920 + DATABASE_BIG_SIZE)


# -- the team tables -------------------------------------------------------


def test_there_are_thirty_team_slots():
    assert TEAM_COUNT == 30


def test_the_abbreviation_table_has_one_entry_per_slot():
    assert len(MVP_TEAM_ABBREVS) == TEAM_COUNT


def test_no_abbreviation_is_repeated():
    assert len(set(MVP_TEAM_ABBREVS)) == TEAM_COUNT


def test_the_name_table_has_one_entry_per_slot():
    assert len(MVP_TEAM_ORDER) == TEAM_COUNT


def test_no_team_name_is_repeated():
    # `RomSlot.display_name` comes from here and a repeated value leaves a user
    # unable to tell two rows of a slot picker apart.
    assert len(set(MVP_TEAM_ORDER)) == TEAM_COUNT


def test_the_index_map_is_derived_from_the_abbreviation_order():
    assert MVP_ABBREV_TO_INDEX == dict(zip(MVP_TEAM_ABBREVS, range(TEAM_COUNT), strict=True))


def test_the_hash_table_names_exactly_the_slots():
    assert sorted(TEAM_HASHES) == sorted(MVP_TEAM_ABBREVS)


def test_the_hash_tables_insertion_order_is_still_the_slot_order():
    # The undeclared invariant. The source relied on this in two files and
    # never checked it; the ordering now comes from `MVP_TEAM_ABBREVS`, and
    # this is what would catch the two drifting apart.
    assert list(TEAM_HASHES) == list(MVP_TEAM_ABBREVS)


def test_no_two_teams_share_a_hash():
    assert len(set(TEAM_HASHES.values())) == TEAM_COUNT


# `MVP_TEAM_ABBREVS` and `MVP_TEAM_ORDER` are two parallel tuples and every test
# that reads a display name reads it back through `MVP_TEAM_ORDER` itself, so
# swapping two of its entries changed nothing anywhere -- mutation testing
# exchanged Oakland and Seattle and the suite stayed green while every UI in the
# library would have shown Oakland's roster under Seattle's name. This is the
# pairing, written out from the 2005 league rather than from either tuple.

CLUB_NAMES_2005 = {
    "ANA": "Anaheim Angels",
    "OAK": "Oakland Athletics",
    "SEA": "Seattle Mariners",
    "TEX": "Texas Rangers",
    "CWS": "Chicago White Sox",
    "CLE": "Cleveland Indians",
    "DET": "Detroit Tigers",
    "KC": "Kansas City Royals",
    "MIN": "Minnesota Twins",
    "BAL": "Baltimore Orioles",
    "BOS": "Boston Red Sox",
    "NYY": "New York Yankees",
    "TB": "Tampa Bay Devil Rays",
    "TOR": "Toronto Blue Jays",
    "ARI": "Arizona Diamondbacks",
    "COL": "Colorado Rockies",
    "LA": "Los Angeles Dodgers",
    "SD": "San Diego Padres",
    "SF": "San Francisco Giants",
    "CHC": "Chicago Cubs",
    "CIN": "Cincinnati Reds",
    "HOU": "Houston Astros",
    "MIL": "Milwaukee Brewers",
    "PIT": "Pittsburgh Pirates",
    "STL": "St. Louis Cardinals",
    "ATL": "Atlanta Braves",
    "FLA": "Florida Marlins",
    "WAS": "Washington Nationals",
    "NYM": "New York Mets",
    "PHI": "Philadelphia Phillies",
}


@pytest.mark.parametrize(("code", "name"), sorted(CLUB_NAMES_2005.items()))
def test_the_slot_an_abbreviation_names_holds_that_clubs_name(code, name):
    assert MVP_TEAM_ORDER[MVP_ABBREV_TO_INDEX[code]] == name


def test_every_slot_is_named_by_the_2005_table():
    assert sorted(CLUB_NAMES_2005) == sorted(MVP_TEAM_ABBREVS)


@pytest.mark.parametrize("code", sorted(TEAM_HASHES))
def test_every_team_hash_is_nine_lowercase_hex_digits(code):
    # `rom_reader._looks_like_record_id` rejects an upper-case id as a header
    # line, so a capital here would make the team table unreadable.
    value = TEAM_HASHES[code]
    assert (len(value), set(value) <= set("0123456789abcdef")) == (HASH_ID_CHARS, True)


def test_a_record_id_is_nine_characters():
    assert HASH_ID_CHARS == 9


# -- the league split ------------------------------------------------------


def test_fourteen_slots_are_american_league():
    assert AL_SLOT_COUNT == 14


def test_the_first_fourteen_slots_are_the_2005_american_league():
    # Checked against the league's own 2005 membership, not against the
    # constant, so the constant cannot be right by agreeing with itself.
    assert set(MVP_TEAM_ABBREVS[:AL_SLOT_COUNT]) == AL_CLUBS_2005


def test_no_american_league_club_appears_after_the_boundary():
    assert set(MVP_TEAM_ABBREVS[AL_SLOT_COUNT:]) & AL_CLUBS_2005 == set()


def test_sixteen_slots_are_national_league():
    assert TEAM_COUNT - AL_SLOT_COUNT == 16


# -- provider abbreviations ------------------------------------------------


def test_every_provider_code_maps_to_a_real_slot():
    assert set(MODERN_MLB_TO_MVP.values()) <= set(MVP_TEAM_ABBREVS)


def test_every_slot_is_reachable_from_some_provider_code():
    # If it were not, `requires_slot_mapping=False` would leave a club with no
    # way to be patched at all.
    assert set(MODERN_MLB_TO_MVP.values()) == set(MVP_TEAM_ABBREVS)


def test_two_provider_codes_collapse_onto_oakland():
    assert sorted(k for k, v in MODERN_MLB_TO_MVP.items() if v == "OAK") == ["ATH", "OAK"]


def test_two_provider_codes_collapse_onto_the_white_sox():
    # The other half of what makes the alias-collision guard in `map_rosters`
    # necessary rather than defensive.
    assert sorted(k for k, v in MODERN_MLB_TO_MVP.items() if v == "CWS") == ["CHW", "CWS"]


def test_exactly_two_slots_have_more_than_one_provider_code():
    counts = {code: list(MODERN_MLB_TO_MVP.values()).count(code) for code in MVP_TEAM_ABBREVS}
    assert sorted(code for code, n in counts.items() if n > 1) == ["CWS", "OAK"]


def test_the_relocated_marlins_map_to_florida():
    assert MODERN_MLB_TO_MVP["MIA"] == "FLA"


def test_the_relocated_angels_map_to_anaheim():
    assert MODERN_MLB_TO_MVP["LAA"] == "ANA"


# -- roster shape ----------------------------------------------------------


def test_a_roster_is_twenty_five_players():
    assert PLAYERS_PER_TEAM == 25


def test_a_roster_is_its_batters_plus_its_pitchers():
    assert BATTERS_PER_TEAM + PITCHERS_PER_TEAM == PLAYERS_PER_TEAM


def test_a_staff_is_its_rotation_plus_its_bullpen():
    assert STARTERS_PER_TEAM + RELIEVERS_PER_TEAM == PITCHERS_PER_TEAM


def test_there_are_fifteen_batters():
    assert BATTERS_PER_TEAM == 15


def test_there_are_five_starters():
    assert STARTERS_PER_TEAM == 5


def test_the_lineup_is_nine_deep():
    assert len(LINEUP_POSITIONS) == 9


def test_the_rotation_has_one_entry_per_starter():
    assert len(ROTATION_POSITIONS) == STARTERS_PER_TEAM


def test_the_bullpen_has_one_entry_per_reliever():
    assert len(BULLPEN_POSITIONS) == RELIEVERS_PER_TEAM


def test_the_bullpen_names_middle_relief_twice():
    # Preserved from the source. The game accepts the duplicate and inventing a
    # fifth role would change which pitcher the CPU warms up.
    assert BULLPEN_POSITIONS.count("MR") == 2


def test_the_bullpen_has_four_distinct_roles():
    assert len(set(BULLPEN_POSITIONS)) == 4


def test_selection_and_lineup_order_hold_the_same_positions():
    assert sorted(SELECTION_POSITIONS) == sorted(LINEUP_POSITIONS)


def test_selection_fills_third_base_before_shortstop():
    assert SELECTION_POSITIONS[3] == "3B"


def test_the_lineup_bats_the_shortstop_where_selection_took_the_third_baseman():
    # The two orders disagree at exactly indices 3 and 4, and the disagreement
    # is the source's. A player who qualifies at both is taken as a third
    # baseman and then batted in the slot labelled shortstop.
    assert LINEUP_POSITIONS[3] == "SS"


def test_the_two_orders_agree_everywhere_else():
    differing = [
        i
        for i, (a, b) in enumerate(zip(LINEUP_POSITIONS, SELECTION_POSITIONS, strict=True))
        if a != b
    ]
    assert differing == [3, 4]


def test_a_player_outside_the_lineup_stores_minus_one():
    assert NOT_IN_LINEUP == -1


# -- position codes --------------------------------------------------------


def test_the_scale_runs_from_zero():
    assert ATTR_MIN == 0


def test_the_scale_runs_to_ninety_nine():
    assert ATTR_MAX == 99


def test_the_designated_hitter_is_stored_as_a_first_baseman():
    # The game has no DH position code, so a DH is offered at first base by the
    # game's own lineup screen. Lossy, and the source's.
    assert POS_STRING_TO_NUM["DH"] == ATTRIB_POS_1B


def test_a_generic_outfielder_is_stored_as_a_centre_fielder():
    assert POS_STRING_TO_NUM["OF"] == ATTRIB_POS_CF


@pytest.mark.parametrize("slot", ROTATION_POSITIONS)
def test_every_rotation_slot_is_stored_as_a_starting_pitcher(slot):
    assert POS_STRING_TO_NUM[slot] == ATTRIB_POS_PITCHER


@pytest.mark.parametrize("role", sorted(set(BULLPEN_POSITIONS)))
def test_every_bullpen_role_is_stored_as_a_reliever(role):
    assert POS_STRING_TO_NUM[role] == ATTRIB_POS_RELIEVER


def test_every_lineup_position_has_a_position_code():
    assert [p for p in LINEUP_POSITIONS if p not in POS_STRING_TO_NUM] == []


def test_the_pitcher_and_reliever_codes_are_distinct():
    assert ATTRIB_POS_PITCHER != ATTRIB_POS_RELIEVER


# -- record types ----------------------------------------------------------


def test_a_pitch_is_frozen():
    pitch = MVPPitch(type=1, movement=2, control=3, velocity=4)
    with pytest.raises(dataclasses.FrozenInstanceError):
        pitch.velocity = 5


def test_a_pitch_carries_its_four_values():
    pitch = MVPPitch(type=1, movement=2, control=3, velocity=4)
    assert (pitch.type, pitch.movement, pitch.control, pitch.velocity) == (1, 2, 3, 4)


def test_a_player_record_has_no_height():
    # The field is gone with the write. `_build_attrib_fields` says why.
    assert hasattr(MVPPlayerRecord(), "height") is False


def test_a_player_record_defaults_to_no_weight():
    # Zero means "the provider did not say", which is what makes the writer
    # leave the disc's own weight alone.
    assert MVPPlayerRecord().weight == 0


def test_a_player_record_defaults_to_being_out_of_the_lineup():
    assert MVPPlayerRecord().batting_order == NOT_IN_LINEUP


def test_a_player_record_starts_with_no_hash_id():
    # `patch` assigns it out of the disc's own pool; the mapper cannot know it.
    assert MVPPlayerRecord().hash_id == ""


def test_two_player_records_do_not_share_an_arsenal():
    first = MVPPlayerRecord()
    second = MVPPlayerRecord()
    first.pitches.append(MVPPitch(type=1, movement=1, control=1, velocity=1))
    assert second.pitches == []
