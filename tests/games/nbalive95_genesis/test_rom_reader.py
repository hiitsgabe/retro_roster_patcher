"""The ported NBA Live 95 reader, against a synthetic 2 MB Genesis image.

The reader is a faithful copy of an untested upstream, so this file's job is to
pin what it does rather than what it ought to do. Two of its behaviours are
defects and are pinned as such: `validate`'s title test passes unconditionally on
any header that does not mention the NBA, and `ROM_SIZE_MIN` accepts files 491 740
bytes too short to hold the last team's pointer table. `patcher.py` is where both
are refused; here they are only recorded.

Every offset and every field value the assertions compare against comes from
`tests/fixtures/synthetic_nbalive95_rom.py`, which transcribes the layout
independently of `src/`. Reaching into `models.TEAM_ROSTER_ADDRESSES` to locate
what a test then asserts about would move the assertion with the constant.
"""

import pytest

from retro_roster_patcher.games.nbalive95_genesis.models import (
    NBALIVE95_TEAM_ORDER,
    PLAYER_SIZE,
    TEAM_ROSTER_ADDRESSES,
)
from retro_roster_patcher.games.nbalive95_genesis.rom_reader import (
    ROM_SIZE_MAX,
    ROM_SIZE_MIN,
    NBALive95RomReader,
)
from tests.fixtures import synthetic_nbalive95_rom as fixture


@pytest.fixture
def rom(tmp_path):
    return fixture.write_nbalive95_rom(tmp_path / "nbalive95.bin")


def _loaded(path):
    reader = NBALive95RomReader(str(path))
    assert reader.load() is True
    return reader


def test_load_returns_false_for_a_file_that_is_not_there(tmp_path):
    reader = NBALive95RomReader(str(tmp_path / "absent.bin"))
    assert reader.load() is False


def test_load_leaves_data_none_when_the_file_is_not_there(tmp_path):
    reader = NBALive95RomReader(str(tmp_path / "absent.bin"))
    reader.load()
    assert reader.data is None


def test_load_returns_false_rather_than_raising_when_the_file_cannot_be_read(rom):
    """`load` swallows its own OSError, which is why `analyze_rom` may raise for it.

    `Patcher.analyze_rom` promises `RomError` for a file that exists and cannot
    be read, and this False is the only signal the patcher gets to distinguish
    that case.
    """
    rom.chmod(0o000)
    try:
        reader = NBALive95RomReader(str(rom))
        assert reader.load() is False
    finally:
        rom.chmod(0o644)


def test_load_reads_the_whole_file(rom):
    reader = _loaded(rom)
    assert len(reader.data) == fixture.ROM_SIZE


def test_validate_accepts_the_synthetic_image(rom):
    assert _loaded(rom).validate() is True


def test_validate_returns_false_before_load(rom):
    assert NBALive95RomReader(str(rom)).validate() is False


def test_validate_rejects_a_file_under_the_size_floor(tmp_path):
    path = fixture.write_nbalive95_rom(tmp_path / "small.bin", size=ROM_SIZE_MIN - 1)
    assert _loaded(path).validate() is False


def test_validate_accepts_a_file_exactly_at_the_size_floor(tmp_path):
    """The floor is inclusive, and this is the file the DEFECT is about.

    `ROM_SIZE_MIN` is 1 572 864 and the last team's pointer table ends at
    2 064 604, so this image passes and then cannot address twelve of its thirty
    teams. `test_the_size_floor_admits_a_file_whose_last_teams_cannot_be_read`
    below carries that through.
    """
    path = fixture.write_nbalive95_rom(tmp_path / "floor.bin", size=ROM_SIZE_MIN)
    assert _loaded(path).validate() is True


def test_validate_rejects_a_file_over_the_size_ceiling(tmp_path):
    path = tmp_path / "huge.bin"
    path.write_bytes(bytes(fixture.build_nbalive95_rom()))
    with open(path, "r+b") as handle:
        handle.truncate(ROM_SIZE_MAX + 1)
    assert _loaded(path).validate() is False


def test_validate_rejects_a_header_naming_a_later_nba_live(tmp_path):
    path = fixture.write_nbalive95_rom(tmp_path / "96.bin", title="NBA LIVE 96")
    assert _loaded(path).validate() is False


def test_validate_accepts_a_header_naming_no_game_at_all(tmp_path):
    """DEFECT, pinned rather than fixed.

    The title test is `if "NBA" in title and "95" not in title`, so a header that
    never mentions the NBA skips it entirely. That is every non-EA Genesis
    cartridge, and it is why `patcher._looks_like_nbalive95` exists.
    """
    path = fixture.write_nbalive95_rom(tmp_path / "sonic.bin", title="SONIC THE HEDGEHOG")
    assert _loaded(path).validate() is True


def test_validate_rejects_a_zero_pointer_for_the_first_player(rom):
    data = bytearray(rom.read_bytes())
    table = fixture.TEAM_ROSTER_ADDRESSES[0]
    data[table : table + 4] = b"\x00\x00\x00\x00"
    rom.write_bytes(bytes(data))
    assert _loaded(rom).validate() is False


def test_validate_rejects_a_first_pointer_that_runs_past_the_end(rom):
    data = bytearray(rom.read_bytes())
    table = fixture.TEAM_ROSTER_ADDRESSES[0]
    data[table : table + 4] = (fixture.ROM_SIZE - PLAYER_SIZE + 1).to_bytes(4, "big")
    rom.write_bytes(bytes(data))
    assert _loaded(rom).validate() is False


def test_validate_rejects_a_name_field_with_too_little_ascii(rom):
    """Two printable bytes is one short of the reader's own floor of three."""
    data = bytearray(rom.read_bytes())
    offset = fixture.player_offset(0, 0) + fixture.OFF_NAME
    data[offset : offset + 24] = b"AB" + bytes(22)
    rom.write_bytes(bytes(data))
    assert _loaded(rom).validate() is False


def test_validate_accepts_a_name_field_with_exactly_the_ascii_floor(rom):
    data = bytearray(rom.read_bytes())
    offset = fixture.player_offset(0, 0) + fixture.OFF_NAME
    data[offset : offset + 24] = b"ABC" + bytes(21)
    rom.write_bytes(bytes(data))
    assert _loaded(rom).validate() is True


def test_the_team_address_table_is_the_one_the_fixture_transcribed(rom):
    """Both copies of the 30 literals, held equal.

    The fixture writes its pointer tables at its own transcription of these
    addresses, so every offset assertion in this file rests on the two agreeing.
    Comparing them directly is what turns a silent mismatch into one failure
    here instead of thirty confusing ones elsewhere.
    """
    assert TEAM_ROSTER_ADDRESSES == fixture.TEAM_ROSTER_ADDRESSES


def test_every_team_roster_offset_is_its_hardcoded_address(rom):
    reader = _loaded(rom)
    found = [reader._get_team_roster_offset(index) for index in range(30)]
    assert found == fixture.TEAM_ROSTER_ADDRESSES


def test_the_address_table_jumps_between_team_17_and_team_18(rom):
    """The gap the module docstring warns about, asserted rather than described."""
    reader = _loaded(rom)
    gap = reader._get_team_roster_offset(18) - reader._get_team_roster_offset(17)
    assert gap == 0x1B0400


def test_a_negative_team_index_gets_offset_zero(rom):
    assert _loaded(rom)._get_team_roster_offset(-1) == 0


def test_a_team_index_past_the_table_gets_offset_zero(rom):
    assert _loaded(rom)._get_team_roster_offset(30) == 0


def test_every_player_pointer_dereferences_to_the_record_the_fixture_placed(rom):
    reader = _loaded(rom)
    found = [reader._get_player_offset(team, slot) for team in range(30) for slot in range(12)]
    expected = [fixture.player_offset(team, slot) for team in range(30) for slot in range(12)]
    assert found == expected


def test_all_three_hundred_and_sixty_records_are_at_distinct_offsets(rom):
    reader = _loaded(rom)
    found = {reader._get_player_offset(team, slot) for team in range(30) for slot in range(12)}
    assert len(found) == 360


def test_a_zero_pointer_reads_as_no_player(rom):
    data = bytearray(rom.read_bytes())
    table = fixture.TEAM_ROSTER_ADDRESSES[5]
    data[table + 3 * 4 : table + 4 * 4] = b"\x00\x00\x00\x00"
    rom.write_bytes(bytes(data))
    assert _loaded(rom)._get_player_offset(5, 3) == 0


def test_a_pointer_whose_record_would_run_past_the_end_reads_as_no_player(rom):
    data = bytearray(rom.read_bytes())
    table = fixture.TEAM_ROSTER_ADDRESSES[5]
    data[table + 3 * 4 : table + 4 * 4] = (fixture.ROM_SIZE - PLAYER_SIZE + 1).to_bytes(4, "big")
    rom.write_bytes(bytes(data))
    assert _loaded(rom)._get_player_offset(5, 3) == 0


def test_a_pointer_whose_record_ends_exactly_at_the_end_is_accepted(rom):
    data = bytearray(rom.read_bytes())
    table = fixture.TEAM_ROSTER_ADDRESSES[5]
    last = fixture.ROM_SIZE - PLAYER_SIZE
    data[table + 3 * 4 : table + 4 * 4] = last.to_bytes(4, "big")
    rom.write_bytes(bytes(data))
    assert _loaded(rom)._get_player_offset(5, 3) == last


def test_the_size_floor_admits_a_file_whose_last_teams_cannot_be_read(tmp_path):
    """DEFECT, carried over: `validate` says yes and twelve teams are unreachable.

    Teams 0-17 keep their pointer tables below 0x44B24; team 18's begins at
    0x1F4EF4, past the end of a `ROM_SIZE_MIN` file. Upstream patched the first
    eighteen, skipped the rest and returned success.
    """
    path = fixture.write_nbalive95_rom(tmp_path / "floor.bin", size=ROM_SIZE_MIN)
    reader = _loaded(path)
    assert reader.validate() is True
    assert reader._get_player_offset(17, 0) == fixture.player_offset(17, 0)
    assert reader._get_player_offset(18, 0) == 0
    unreadable = [team for team in range(30) if reader._get_player_offset(team, 0) == 0]
    assert unreadable == list(range(18, 30))


def test_a_name_with_no_null_is_all_last_name(rom):
    assert _loaded(rom)._decode_name(b"JORDAN") == ("JORDAN", "")


def test_a_name_split_by_one_null_yields_both_halves(rom):
    assert _loaded(rom)._decode_name(b"JORDAN\x00MICHAEL") == ("JORDAN", "MICHAEL")


def test_the_first_name_stops_at_the_second_null(rom):
    assert _loaded(rom)._decode_name(b"JORDAN\x00M.\x00\x00GARBAGE") == ("JORDAN", "M.")


def test_a_leading_null_yields_an_empty_last_name(rom):
    assert _loaded(rom)._decode_name(b"\x00MICHAEL\x00\x00") == ("", "MICHAEL")


def test_non_printable_bytes_are_dropped_from_a_decoded_name(rom):
    assert _loaded(rom)._decode_name(b"JO\x01RD\xffAN\x00M\x02IKE\x00") == ("JORDAN", "MIKE")


def test_the_printable_range_stops_below_delete(rom):
    """0x7E is the last byte kept and 0x7F is not, on both halves of the name."""
    assert _loaded(rom)._decode_name(b"JO\x7fRDAN\x00MI\x7fKE\x00") == ("JORDAN", "MIKE")
    assert _loaded(rom)._decode_name(b"JO~RDAN\x00MI~KE\x00") == ("JO~RDAN", "MI~KE")


def test_surrounding_spaces_are_stripped_from_a_decoded_name(rom):
    assert _loaded(rom)._decode_name(b"  JORDAN \x00 MIKE \x00") == ("JORDAN", "MIKE")


def test_a_record_reads_back_every_field_the_fixture_wrote(rom):
    player = _loaded(rom).read_player(7, 4)
    assert player["last_name"] == fixture.player_last_name(7, 4)
    assert player["first_name"] == fixture.player_first_name(7, 4)
    assert player["jersey"] == fixture.player_jersey(7, 4)
    assert player["position_byte"] == fixture.player_position(7, 4)
    assert player["experience"] == fixture.player_experience(7, 4)
    assert player["skin_color"] == fixture.player_skin(7, 4)
    assert player["hair_style"] == fixture.player_hair(7, 4)
    assert player["ratings"] == fixture.player_ratings(7, 4)
    assert player["season_stats"] == fixture.player_season_stats(7, 4)
    assert player["offset"] == fixture.player_offset(7, 4)


def test_height_is_reported_five_inches_above_the_stored_byte(rom):
    player = _loaded(rom).read_player(7, 4)
    assert player["height_inches"] == fixture.player_height_byte(7, 4) + 5


def test_weight_is_reported_a_hundred_pounds_above_the_stored_byte(rom):
    player = _loaded(rom).read_player(7, 4)
    assert player["weight_lbs"] == fixture.player_weight_byte(7, 4) + 100


def test_the_position_byte_is_rendered_as_this_games_own_name(rom):
    data = bytearray(rom.read_bytes())
    data[fixture.player_offset(2, 1) + fixture.OFF_POSITION] = 3
    rom.write_bytes(bytes(data))
    assert _loaded(rom).read_player(2, 1)["position"] == "PG"


def test_a_position_byte_this_game_does_not_define_is_rendered_with_a_question_mark(rom):
    data = bytearray(rom.read_bytes())
    data[fixture.player_offset(2, 1) + fixture.OFF_POSITION] = 7
    rom.write_bytes(bytes(data))
    assert _loaded(rom).read_player(2, 1)["position"] == "?7"


def test_reading_a_player_before_load_gives_an_empty_dict(rom):
    assert NBALive95RomReader(str(rom)).read_player(0, 0) == {}


def test_reading_a_player_behind_a_zero_pointer_gives_an_empty_dict(rom):
    data = bytearray(rom.read_bytes())
    table = fixture.TEAM_ROSTER_ADDRESSES[3]
    data[table : table + 4] = b"\x00\x00\x00\x00"
    rom.write_bytes(bytes(data))
    assert _loaded(rom).read_player(3, 0) == {}


def test_a_full_roster_reads_back_twelve_players_in_slot_order(rom):
    roster = _loaded(rom).read_team_roster(11)
    assert [p["last_name"] for p in roster] == [
        fixture.player_last_name(11, slot) for slot in range(12)
    ]


def test_a_roster_stops_at_the_first_slot_it_cannot_read(rom):
    data = bytearray(rom.read_bytes())
    table = fixture.TEAM_ROSTER_ADDRESSES[11]
    data[table + 5 * 4 : table + 6 * 4] = b"\x00\x00\x00\x00"
    rom.write_bytes(bytes(data))
    assert len(_loaded(rom).read_team_roster(11)) == 5


def test_a_roster_past_the_last_team_is_empty(rom):
    assert _loaded(rom).read_team_roster(30) == []


def test_get_info_before_load_reports_a_size_of_zero(rom):
    """DEFECT, carried over: an unread 2 MB file is described as 0 bytes.

    `analyze_rom` never returns this -- it raises `RomError` when `load` fails --
    which is the divergence this reader's behaviour forced.
    """
    info = NBALive95RomReader(str(rom)).get_info()
    assert info.size == 0
    assert info.is_valid is False


def test_get_info_reports_the_file_size(rom):
    assert _loaded(rom).get_info().size == fixture.ROM_SIZE


def test_get_info_reports_thirty_slots(rom):
    assert len(_loaded(rom).get_info().team_slots) == 30


def test_every_slot_name_comes_from_the_games_own_team_order(rom):
    slots = _loaded(rom).get_info().team_slots
    assert [slot.name for slot in slots] == NBALIVE95_TEAM_ORDER


def test_every_slot_reports_its_first_player_as_first_then_last(rom):
    slots = _loaded(rom).get_info().team_slots
    expected = [
        f"{fixture.player_first_name(index, 0)} {fixture.player_last_name(index, 0)}"
        for index in range(30)
    ]
    assert [slot.first_player for slot in slots] == expected


def test_a_slot_with_only_a_last_name_reports_that_alone(rom):
    data = bytearray(rom.read_bytes())
    offset = fixture.player_offset(4, 0) + fixture.OFF_NAME
    data[offset : offset + 24] = b"PIPPEN\x00\x00" + bytes(16)
    rom.write_bytes(bytes(data))
    assert _loaded(rom).get_info().team_slots[4].first_player == "PIPPEN"


def test_a_slot_whose_record_cannot_be_read_reports_no_first_player(rom):
    data = bytearray(rom.read_bytes())
    table = fixture.TEAM_ROSTER_ADDRESSES[9]
    data[table : table + 4] = b"\x00\x00\x00\x00"
    rom.write_bytes(bytes(data))
    assert _loaded(rom).get_info().team_slots[9].first_player == ""


def test_an_invalid_image_reports_no_slots_at_all(tmp_path):
    path = fixture.write_nbalive95_rom(tmp_path / "96.bin", title="NBA LIVE 96")
    info = _loaded(path).get_info()
    assert info.is_valid is False
    assert info.team_slots == []
