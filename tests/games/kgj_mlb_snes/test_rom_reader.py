"""The ported KGJ MLB reader against synthetic images.

Two properties carry most of this file.

**`validate` is the signature check**, and it is a strict one: exactly 2 097 152
bytes or exactly 2 097 664, plus a 14-byte marker somewhere in the image. No
floor, no band. Every size one byte either side of both is pinned below, because
a `<=` where the source has `!=` would otherwise pass every test here.

**`validate` has a side effect.** It sets `first_team_offset`, and every other
offset in the class is relative to it. The port raises rather than computing
from the 0 that field starts at, which is what upstream did.
"""

import pytest

from retro_roster_patcher.games.kgj_mlb_snes.models import (
    AL_TEAMS,
    AL_TO_NL_GAP,
    FIRST_TEAM_MARKER,
    PLAYER_LENGTH,
    PLAYERS_PER_TEAM,
    TEAM_COUNT,
    TEAM_LENGTH,
)
from retro_roster_patcher.games.kgj_mlb_snes.rom_reader import (
    ROM_SIZE_EXPECTED,
    SMC_HEADER_SIZE,
    TEAM_DATA_SPAN,
    KGJRomReader,
)
from tests.fixtures import synthetic_kgj_rom as fixture


@pytest.fixture
def rom(tmp_path):
    return fixture.write_kgj_rom(tmp_path / "kgj.sfc")


@pytest.fixture
def headered_rom(tmp_path):
    return fixture.write_kgj_rom(tmp_path / "kgj.smc", with_header=True)


def _loaded(path):
    reader = KGJRomReader(str(path))
    assert reader.load() is True
    return reader


def _validated(path):
    reader = _loaded(path)
    assert reader.validate() is True
    return reader


# -- derived constants -------------------------------------------------------


def test_the_team_data_span_is_the_28_blocks_and_the_gap_between_the_leagues():
    # 14 AL blocks of 800 bytes, 2 880 bytes of something else, 14 NL blocks.
    # Retranscribed here rather than imported, so a change to `AL_TO_NL_GAP`
    # breaks this rather than moving with it.
    assert TEAM_DATA_SPAN == 25280


def test_the_expected_rom_size_is_16_mbit():
    assert ROM_SIZE_EXPECTED == 2097152


def test_the_copier_header_is_512_bytes():
    assert SMC_HEADER_SIZE == 512


# -- load --------------------------------------------------------------------


def test_load_answers_false_for_a_file_that_is_not_there(tmp_path):
    reader = KGJRomReader(str(tmp_path / "absent.sfc"))
    assert reader.load() is False


def test_load_leaves_data_none_when_the_file_is_not_there(tmp_path):
    reader = KGJRomReader(str(tmp_path / "absent.sfc"))
    reader.load()
    assert reader.data is None


def test_load_answers_false_for_a_path_that_exists_but_cannot_be_read(tmp_path):
    # A directory: `os.path.exists` is True and `open` raises, which is the
    # branch that catches the filesystem's own errors. `analyze_rom` turns this
    # False into `RomError`, so it is the one path that must not be silent.
    directory = tmp_path / "adirectory"
    directory.mkdir()
    reader = KGJRomReader(str(directory))
    assert reader.load() is False


def test_load_reads_the_whole_file(rom):
    reader = _loaded(rom)
    assert len(reader.data) == fixture.ROM_SIZE


# -- validate ----------------------------------------------------------------


def test_validate_accepts_a_headerless_image(rom):
    reader = _loaded(rom)
    assert reader.validate() is True


def test_validate_accepts_a_headered_image(headered_rom):
    reader = _loaded(headered_rom)
    assert reader.validate() is True


@pytest.mark.parametrize(
    "size",
    [
        ROM_SIZE_EXPECTED - 1,
        ROM_SIZE_EXPECTED + 1,
        ROM_SIZE_EXPECTED + SMC_HEADER_SIZE - 1,
        ROM_SIZE_EXPECTED + SMC_HEADER_SIZE + 1,
        ROM_SIZE_EXPECTED + 256,
        ROM_SIZE_EXPECTED * 2,
    ],
)
def test_validate_refuses_every_size_but_the_two_it_names(tmp_path, size):
    # The marker is present and the data is otherwise well-formed; only the
    # length is wrong. A `>=` in place of the source's `!=` passes the two
    # accepted sizes and fails here.
    body = fixture.build_kgj_rom()
    data = (bytes(body) * 3)[:size]
    path = tmp_path / f"size{size}.sfc"
    path.write_bytes(data)
    reader = _loaded(path)
    assert reader.validate() is False


def test_validate_refuses_a_correctly_sized_image_with_no_marker(tmp_path):
    path = fixture.write_kgj_rom(tmp_path / "nomarker.sfc", place_marker=False)
    reader = _loaded(path)
    assert reader.validate() is False


def test_validate_refuses_an_empty_file(tmp_path):
    path = tmp_path / "empty.sfc"
    path.write_bytes(b"")
    reader = _loaded(path)
    assert reader.validate() is False


def test_validate_before_load_answers_false(tmp_path):
    reader = KGJRomReader(str(tmp_path / "never-loaded.sfc"))
    assert reader.validate() is False


def test_validate_records_the_byte_after_the_marker(rom):
    reader = _validated(rom)
    assert reader.first_team_offset == fixture.MARKER_OFFSET + len(FIRST_TEAM_MARKER)


def test_validate_finds_the_marker_wherever_it_is(tmp_path):
    # There is no fixed offset to the team tables; the whole design is the
    # search. Moving the marker moves everything with it.
    moved = 0x040000
    path = fixture.write_kgj_rom(tmp_path / "moved.sfc", marker_offset=moved)
    reader = _validated(path)
    assert reader.first_team_offset == moved + len(FIRST_TEAM_MARKER)


def test_the_copier_header_shifts_the_recorded_offset_by_exactly_512(rom, headered_rom):
    headerless = _validated(rom)
    headered = _validated(headered_rom)
    assert headered.first_team_offset - headerless.first_team_offset == SMC_HEADER_SIZE


def test_the_search_absorbs_the_header_so_the_same_record_reads_back(rom, headered_rom):
    # This is the property the port's docstrings tell a future editor to
    # preserve: no headered/headerless arithmetic anywhere, because the search
    # already did it.
    headerless = _validated(rom)
    headered = _validated(headered_rom)
    assert headered.read_player(9, 3) == headerless.read_player(9, 3)


def test_validate_leaves_the_offset_at_zero_when_it_fails(tmp_path):
    path = fixture.write_kgj_rom(tmp_path / "nomarker.sfc", place_marker=False)
    reader = _loaded(path)
    reader.validate()
    assert reader.first_team_offset == 0


# -- offsets -----------------------------------------------------------------


def test_get_team_offset_refuses_to_answer_before_validate_succeeds(rom):
    # DELIBERATE DIVERGENCE from upstream, which computed from 0 and read the
    # head of the file as if it were team 0's roster.
    reader = _loaded(rom)
    with pytest.raises(RuntimeError):
        reader.get_team_offset(0)


def test_read_player_refuses_to_answer_before_validate_succeeds(rom):
    reader = _loaded(rom)
    with pytest.raises(RuntimeError):
        reader.read_player(0, 0)


def test_read_team_roster_refuses_to_answer_before_validate_succeeds(rom):
    reader = _loaded(rom)
    with pytest.raises(RuntimeError):
        reader.read_team_roster(0)


def test_team_zero_starts_at_the_recorded_offset(rom):
    reader = _validated(rom)
    assert reader.get_team_offset(0) == reader.first_team_offset


def test_the_al_teams_are_contiguous(rom):
    reader = _validated(rom)
    assert reader.get_team_offset(13) - reader.get_team_offset(12) == TEAM_LENGTH


def test_the_gap_sits_between_the_last_al_team_and_the_first_nl_team(rom):
    reader = _validated(rom)
    step = reader.get_team_offset(AL_TEAMS) - reader.get_team_offset(AL_TEAMS - 1)
    assert step == TEAM_LENGTH + AL_TO_NL_GAP


def test_the_nl_teams_are_contiguous(rom):
    reader = _validated(rom)
    assert reader.get_team_offset(27) - reader.get_team_offset(26) == TEAM_LENGTH


def test_the_last_team_ends_exactly_at_the_team_data_span(rom):
    reader = _validated(rom)
    end = reader.get_team_offset(TEAM_COUNT - 1) + TEAM_LENGTH
    assert end - reader.first_team_offset == TEAM_DATA_SPAN


def test_player_slots_step_by_the_record_length(rom):
    reader = _validated(rom)
    step = reader.get_player_offset(5, 8) - reader.get_player_offset(5, 7)
    assert step == PLAYER_LENGTH


def test_player_offsets_agree_with_the_fixtures_own_layout(rom):
    reader = _validated(rom)
    expected = fixture.player_offset(20, 17, first_team_offset=reader.first_team_offset)
    assert reader.get_player_offset(20, 17) == expected


# -- names -------------------------------------------------------------------


def test_a_decoded_name_loses_its_padding(rom):
    reader = _validated(rom)
    assert reader._decode_name(fixture.encode_name("GRIFFEY")) == "GRIFFEY"


def test_a_byte_the_encoding_table_does_not_name_decodes_as_a_question_mark(rom):
    # 0x37 is one past the lone lowercase `c`. A real cartridge font holds more
    # than this table names, which is why the port's signature check does not
    # test name bytes.
    reader = _validated(rom)
    assert reader._decode_name(bytes([0x37])) == "?"


def test_the_lone_lowercase_letter_decodes(rom):
    reader = _validated(rom)
    assert reader._decode_name(bytes([0x17, 0x36, 0x11])) == "McG"


# -- read_player -------------------------------------------------------------


def test_a_batter_reads_back_the_name_the_fixture_wrote(rom):
    reader = _validated(rom)
    assert reader.read_player(3, 6)["last_name"] == fixture.player_last_name(3, 6)


def test_a_batter_reads_back_its_first_initial(rom):
    reader = _validated(rom)
    assert reader.read_player(3, 6)["first_initial"] == fixture.player_first_initial(3, 6)


def test_a_batter_reads_back_its_position(rom):
    reader = _validated(rom)
    assert reader.read_player(3, 6)["position"] == fixture.player_position(3, 6)


def test_a_batter_reads_back_its_jersey(rom):
    reader = _validated(rom)
    assert reader.read_player(3, 6)["jersey"] == fixture.player_jersey(3, 6)


def test_a_batter_reads_back_its_bat_hand(rom):
    reader = _validated(rom)
    assert reader.read_player(3, 6)["bat_hand"] == fixture.player_bat_hand(3, 6)


def test_a_batter_is_not_reported_as_a_pitcher(rom):
    reader = _validated(rom)
    assert reader.read_player(3, 6)["is_pitcher"] is False


def test_a_batters_roster_type_nibble_is_three(rom):
    reader = _validated(rom)
    assert reader.read_player(3, 6)["roster_type"] == fixture.ROSTER_TYPE_BATTER


@pytest.mark.parametrize(
    "field,index", [("batting", 0), ("power", 1), ("speed", 2), ("defense", 3)]
)
def test_a_batters_four_attributes_unpack_from_two_bytes(rom, field, index):
    # Every one of the four differs from the others in this record, so a
    # swapped nibble or a transposed byte is visible.
    reader = _validated(rom)
    assert reader.read_player(3, 6)[field] == fixture.player_ratings(3, 6)[index]


def test_a_batting_average_above_255_needs_the_nibble_at_0x19(rom):
    # The high nibble shares byte 0x19 with the roster type. An implementation
    # that read only byte 0x18 would answer 300 & 0xFF = 44.
    reader = _validated(rom)
    assert reader.read_player(3, 6)["batting_avg"] == fixture.player_batting_avg(3, 6)


def test_a_batter_reads_back_its_home_runs(rom):
    reader = _validated(rom)
    assert reader.read_player(3, 6)["home_runs"] == 1 + (3 * 4 + 6) % 50


def test_a_batter_reads_back_its_rbi(rom):
    reader = _validated(rom)
    assert reader.read_player(3, 6)["rbi"] == 1 + (3 * 6 + 6) % 130


def test_a_starting_pitcher_is_reported_as_a_pitcher(rom):
    reader = _validated(rom)
    assert reader.read_player(3, 16)["is_pitcher"] is True


def test_a_starting_pitchers_roster_type_nibble_is_one(rom):
    reader = _validated(rom)
    assert reader.read_player(3, 16)["roster_type"] == fixture.ROSTER_TYPE_STARTER


def test_a_relievers_roster_type_nibble_is_zero(rom):
    reader = _validated(rom)
    assert reader.read_player(3, 22)["roster_type"] == fixture.ROSTER_TYPE_RELIEVER


def test_a_reliever_is_reported_as_a_pitcher(rom):
    # 0 and 1 both mean pitcher; only 3 means batter. A test that used the
    # starter alone would pass against `roster_type == 0` as the pitcher test.
    reader = _validated(rom)
    assert reader.read_player(3, 22)["is_pitcher"] is True


@pytest.mark.parametrize("field,index", [("p_speed", 0), ("p_control", 1), ("p_fatigue", 2)])
def test_a_pitchers_three_attributes_unpack_from_two_bytes(rom, field, index):
    reader = _validated(rom)
    assert reader.read_player(3, 16)[field] == fixture.player_ratings(3, 16)[index]


def test_a_pitcher_reads_back_its_throwing_hand(rom):
    reader = _validated(rom)
    assert reader.read_player(3, 17)["pitch_hand"] == 17 % 2


def test_a_pitcher_reads_back_its_wins(rom):
    reader = _validated(rom)
    assert reader.read_player(3, 16)["wins"] == 1 + (3 + 16) % 30


def test_a_pitcher_reads_back_its_losses(rom):
    reader = _validated(rom)
    assert reader.read_player(3, 16)["losses"] == 1 + (3 * 2 + 16) % 25


def test_an_era_above_255_needs_the_nibble_at_0x1d(rom):
    reader = _validated(rom)
    assert reader.read_player(3, 16)["era"] == fixture.player_era(3, 16)


def test_a_pitcher_reads_back_its_saves(rom):
    reader = _validated(rom)
    assert reader.read_player(3, 16)["saves"] == 1 + (3 * 3 + 16) % 45


def test_a_batter_record_carries_no_pitcher_fields(rom):
    reader = _validated(rom)
    assert "era" not in reader.read_player(3, 6)


def test_a_pitcher_record_carries_no_batter_fields(rom):
    reader = _validated(rom)
    assert "batting_avg" not in reader.read_player(3, 16)


def test_the_first_al_team_and_the_first_nl_team_read_different_records(rom):
    # The AL/NL gap is 2 880 bytes. An implementation that omitted it would read
    # team 14 out of the middle of team 13's block.
    reader = _validated(rom)
    assert reader.read_player(14, 0)["last_name"] == fixture.player_last_name(14, 0)


def test_the_last_slot_of_the_last_team_reads_back(rom):
    reader = _validated(rom)
    assert reader.read_player(27, 24)["last_name"] == fixture.player_last_name(27, 24)


def test_a_record_that_runs_off_the_end_of_the_file_reads_as_empty(tmp_path):
    # The marker is close enough to the end that team 0 fits and team 27 does
    # not. This is the condition `patcher._team_data_fits` refuses up front.
    path = fixture.write_kgj_rom(
        tmp_path / "tight.sfc",
        marker_offset=fixture.ROM_SIZE - fixture.TEAM_DATA_SPAN + 0x400,
    )
    reader = _validated(path)
    assert reader.read_player(27, 24) == {}


def test_read_player_before_load_answers_empty(tmp_path):
    reader = KGJRomReader(str(tmp_path / "absent.sfc"))
    assert reader.read_player(0, 0) == {}


# -- read_team_roster --------------------------------------------------------


def test_a_team_roster_holds_every_slot(rom):
    reader = _validated(rom)
    names, players = reader.read_team_roster(11)
    assert len(players) == PLAYERS_PER_TEAM


def test_a_team_rosters_names_are_initial_dot_last(rom):
    reader = _validated(rom)
    names, _ = reader.read_team_roster(11)
    expected = f"{fixture.player_first_initial(11, 0)}. {fixture.player_last_name(11, 0)}"
    assert names[0] == expected


def test_a_team_rosters_last_name_is_the_last_slots(rom):
    reader = _validated(rom)
    names, _ = reader.read_team_roster(11)
    expected = f"{fixture.player_first_initial(11, 24)}. {fixture.player_last_name(11, 24)}"
    assert names[-1] == expected


def test_a_team_index_past_the_league_reads_nothing(rom):
    reader = _validated(rom)
    assert reader.read_team_roster(TEAM_COUNT) == ([], [])


def test_read_team_roster_before_load_reads_nothing(tmp_path):
    reader = KGJRomReader(str(tmp_path / "absent.sfc"))
    assert reader.read_team_roster(0) == ([], [])


def test_a_roster_stops_at_the_first_record_that_runs_off_the_end(tmp_path):
    # `read_team_roster` breaks rather than skipping, so a partially addressable
    # team yields a short list and not a list with holes. With the marker 1 024
    # bytes past the last position it fits at, team 26's block starts 562 bytes
    # before the end of the file, which is 17 whole records and part of an
    # eighteenth.
    path = fixture.write_kgj_rom(
        tmp_path / "tight.sfc",
        marker_offset=fixture.ROM_SIZE - fixture.TEAM_DATA_SPAN + 0x400,
    )
    reader = _validated(path)
    _, players = reader.read_team_roster(26)
    assert len(players) == 17


def test_a_team_whose_first_record_is_off_the_end_reads_nothing(tmp_path):
    path = fixture.write_kgj_rom(
        tmp_path / "tight.sfc",
        marker_offset=fixture.ROM_SIZE - fixture.TEAM_DATA_SPAN + 0x400,
    )
    reader = _validated(path)
    _, players = reader.read_team_roster(27)
    assert players == []


# -- get_info ----------------------------------------------------------------


def test_get_info_reports_a_headerless_image_as_valid(rom):
    reader = _loaded(rom)
    assert reader.get_info().is_valid is True


def test_get_info_reports_the_files_real_size(rom):
    reader = _loaded(rom)
    assert reader.get_info().size == fixture.ROM_SIZE


def test_get_info_reports_no_header_on_a_headerless_image(rom):
    reader = _loaded(rom)
    assert reader.get_info().has_header is False


def test_get_info_reports_a_header_on_a_headered_image(headered_rom):
    reader = _loaded(headered_rom)
    assert reader.get_info().has_header is True


def test_get_info_reports_the_marker_derived_offset(rom):
    reader = _loaded(rom)
    expected = fixture.MARKER_OFFSET + len(FIRST_TEAM_MARKER)
    assert reader.get_info().first_team_offset == expected


def test_get_info_reports_one_slot_per_team(rom):
    reader = _loaded(rom)
    assert len(reader.get_info().team_slots) == TEAM_COUNT


def test_a_slot_carries_the_teams_name_from_the_1994_order(rom):
    reader = _loaded(rom)
    assert reader.get_info().team_slots[10].name == "Oakland Athletics"


def test_a_slot_carries_the_first_player_read_out_of_the_image(rom):
    reader = _loaded(rom)
    expected = f"{fixture.player_first_initial(10, 0)}. {fixture.player_last_name(10, 0)}"
    assert reader.get_info().team_slots[10].first_player == expected


def test_two_slots_carry_different_first_players(rom):
    # Every record encodes its own (team, slot), so a reader that ignored
    # `team_index` would give all 28 slots the same name.
    reader = _loaded(rom)
    slots = reader.get_info().team_slots
    assert slots[10].first_player != slots[11].first_player


def test_an_invalid_image_reports_no_slots(tmp_path):
    path = fixture.write_kgj_rom(tmp_path / "nomarker.sfc", place_marker=False)
    reader = _loaded(path)
    assert reader.get_info().team_slots == []


def test_an_invalid_image_still_reports_its_real_size(tmp_path):
    path = fixture.write_kgj_rom(tmp_path / "nomarker.sfc", place_marker=False)
    reader = _loaded(path)
    assert reader.get_info().size == fixture.ROM_SIZE


def test_get_info_before_load_reports_size_zero(tmp_path):
    reader = KGJRomReader(str(tmp_path / "absent.sfc"))
    assert reader.get_info().size == 0


def test_get_info_before_load_reports_the_path_it_was_given(tmp_path):
    path = tmp_path / "absent.sfc"
    reader = KGJRomReader(str(path))
    assert reader.get_info().path == str(path)
