"""Reader coverage against `tests/fixtures/synthetic_snes_rom.py`.

NHL 94 is copyrighted, so nothing here touches a real image. Every test builds a
1 MB `bytearray` under `tmp_path`; the reader takes a path rather than bytes, so
there is no injection seam and a file has to exist.

The second half of this file pins defects rather than intended behaviour. The
reader is a faithful port and fidelity is the standing policy, so these tests
assert what it *does*; each carries a comment saying why that is wrong, so a fix
shows up as a deliberate red test rather than a silent behaviour change.
"""

import pytest

from retro_roster_patcher.games.nhl94_snes.models import NHL94_TEAM_ORDER, TEAM_COUNT
from retro_roster_patcher.games.nhl94_snes.rom_reader import (
    POINTER_TABLE_FILE_OFFSET,
    ROM_SIZE_NO_HEADER,
    SMC_HEADER_SIZE,
    NHL94SNESRomReader,
    snes_to_file_offset,
)
from tests.fixtures import synthetic_snes_rom as fixture


def _reader(path):
    reader = NHL94SNESRomReader(str(path))
    assert reader.load() is True
    return reader


def _loaded(tmp_path, **kwargs):
    return _reader(fixture.write_nhl94_snes_rom(tmp_path / "nhl94.sfc", **kwargs))


def _write(tmp_path, name, rom):
    path = tmp_path / name
    path.write_bytes(bytes(rom))
    return path


def test_the_fixture_is_bound_under_exactly_one_module_name():
    assert fixture.__name__ == "tests.fixtures.synthetic_snes_rom"


def test_every_team_block_lands_inside_the_one_bank_the_pointers_can_reach():
    """The layout constraint the whole format rests on.

    A team pointer stores 16 bits and the reader ORs bank $9C onto it, so
    `snes_to_file_offset` can only ever name a byte in 0xE0000-0xE7FFF. A block
    placed outside that window would be unreachable however correct the pointer
    written for it, and the fixture would be testing an address the reader can
    never produce.
    """
    window = range(fixture.BANK_WINDOW_START, fixture.BANK_WINDOW_START + fixture.BANK_WINDOW_SIZE)
    outside = [i for i in range(TEAM_COUNT) if fixture.team_base(i) not in window]
    assert outside == []
    last = fixture.team_base(TEAM_COUNT - 1) + fixture.TEAM_BLOCK_STRIDE
    assert last <= fixture.BANK_WINDOW_START + fixture.BANK_WINDOW_SIZE


def test_the_pointer_table_does_not_overlap_the_first_team_block():
    table_end = POINTER_TABLE_FILE_OFFSET + TEAM_COUNT * fixture.POINTER_SIZE
    assert table_end <= fixture.team_base(0)


def test_the_fixtures_team_names_differ_from_the_constant_table_somewhere():
    """Otherwise `current_name` and `display_name` are indistinguishable.

    Slot 20 is the one: "St Louis" in the image, "St. Louis" in the code.
    Without a difference, a reader that read neither and returned the constant
    for both would pass every name assertion in this file.
    """
    assert fixture.CITIES[20] != NHL94_TEAM_ORDER[20]
    assert len(fixture.CITIES) == TEAM_COUNT


def test_a_lorom_address_folds_to_the_bank_window():
    # The documented table address, from `rom_reader`'s own module docstring.
    assert snes_to_file_offset(0x9CA5E7) == POINTER_TABLE_FILE_OFFSET


def test_bit_15_of_a_lorom_address_does_not_change_the_file_offset():
    """`% 0x8000` discards it, so $9C0123 and $9C8123 name the same byte.

    That is what lets the fixture store `base - 0xE0000` in a pointer without
    setting the high bit the real ROM would have.
    """
    assert snes_to_file_offset(0x9C0123) == snes_to_file_offset(0x9C8123)


def test_each_team_pointer_resolves_to_that_teams_block(tmp_path):
    reader = _loaded(tmp_path)
    resolved = [reader._read_team_pointer(i) for i in range(TEAM_COUNT)]
    expected = [fixture.team_base(i) for i in range(TEAM_COUNT)]
    assert resolved == expected
    # Derived, and it rules out the vacuous pass a constant reader would give.
    assert len(set(resolved)) == TEAM_COUNT


def test_a_pointer_past_the_last_team_is_refused(tmp_path):
    reader = _loaded(tmp_path)
    assert reader._read_team_pointer(TEAM_COUNT) is None


def test_a_copier_header_shifts_every_offset_by_512(tmp_path):
    reader = _loaded(tmp_path, with_smc_header=True)
    assert reader.has_header is True
    assert reader.header_offset == SMC_HEADER_SIZE
    assert reader._ptr_table_offset() == SMC_HEADER_SIZE + POINTER_TABLE_FILE_OFFSET
    assert reader._read_team_pointer(7) == SMC_HEADER_SIZE + fixture.team_base(7)


def test_a_headerless_image_is_read_at_face_value(tmp_path):
    reader = _loaded(tmp_path)
    assert reader.has_header is False
    assert reader.header_offset == 0


def test_a_missing_file_fails_to_load(tmp_path):
    reader = NHL94SNESRomReader(str(tmp_path / "absent.sfc"))
    assert reader.load() is False
    assert reader.data is None


def test_a_directory_fails_to_load_rather_than_raising(tmp_path):
    reader = NHL94SNESRomReader(str(tmp_path))
    assert reader.load() is False


def test_a_full_size_image_validates(tmp_path):
    assert _loaded(tmp_path).validate() is True


def test_a_headered_full_size_image_validates(tmp_path):
    assert _loaded(tmp_path, with_smc_header=True).validate() is True


def test_a_tiny_file_does_not_validate(tmp_path):
    reader = _reader(_write(tmp_path, "small.sfc", bytes(4096)))
    assert reader.validate() is False


def test_an_empty_file_does_not_validate(tmp_path):
    reader = _reader(_write(tmp_path, "empty.sfc", b""))
    assert reader.validate() is False


def test_validate_accepts_a_file_of_the_declared_standard_size_that_holds_no_team_data(tmp_path):
    """DEFECT, pinned: `ROM_SIZE_NO_HEADER` is not this game's size.

    NHL '94 (SNES) is an 8 Mbit LoROM, 1 048 576 bytes. `ROM_SIZE_NO_HEADER` is
    649 728, and the pointer table is 927 207 bytes in, so a file of the size
    the constant calls standard validates and then yields no pointer, no roster
    and no write at all -- while upstream's `patch_rom` returned success. The
    patcher works around it: `_pointer_table_fits` is what turns this into a
    refusal. This test pins the reader's own answer, which is still yes.
    """
    reader = _reader(_write(tmp_path, "short.sfc", bytes(ROM_SIZE_NO_HEADER)))
    assert reader.validate() is True
    assert reader._read_team_pointer(0) is None
    assert reader.read_team_roster(0) == ([], [])


def test_get_info_on_an_unloaded_reader_reports_nothing(tmp_path):
    reader = NHL94SNESRomReader(str(tmp_path / "absent.sfc"))
    info = reader.get_info()
    assert info.is_valid is False
    assert info.size == 0
    assert info.team_slots == []


def test_get_info_reads_every_slot_out_of_a_valid_image(tmp_path):
    info = _loaded(tmp_path).get_info()
    assert info.is_valid is True
    assert info.size == fixture.ROM_SIZE
    assert len(info.team_slots) == TEAM_COUNT
    assert [slot.index for slot in info.team_slots] == list(range(TEAM_COUNT))
    assert [slot.current_name for slot in info.team_slots] == fixture.CITIES
    assert [slot.display_name for slot in info.team_slots] == NHL94_TEAM_ORDER


def test_get_info_reads_no_slot_out_of_an_image_that_does_not_validate(tmp_path):
    info = _reader(_write(tmp_path, "small.sfc", bytes(4096))).get_info()
    assert info.is_valid is False
    assert info.team_slots == []


def test_the_city_is_the_first_string_after_the_roster(tmp_path):
    reader = _loaded(tmp_path)
    read = [reader._read_team_city(fixture.team_base(i)) for i in range(TEAM_COUNT)]
    assert read == fixture.CITIES


def test_a_slot_whose_city_string_is_unreadable_falls_back_to_the_constant(tmp_path):
    """`current_name = name or NHL94_TEAM_ORDER[i]`, exercised on slot 20.

    Slot 20 is the one slot where the two differ, so this distinguishes the
    fallback from a reader that returned the constant all along.
    """
    rom = fixture.build_nhl94_snes_rom()
    # A header size that runs past the block leaves the walk with nothing to
    # read, so `_read_length_prefixed_string` answers "".
    rom[fixture.team_base(20)] = 0xFF
    rom[fixture.team_base(20) + 1] = 0xFF
    reader = _reader(_write(tmp_path, "broken.sfc", rom))
    slots = reader.get_info().team_slots
    assert slots[20].current_name == NHL94_TEAM_ORDER[20]
    assert slots[19].current_name == fixture.CITIES[19]


def test_a_roster_reads_back_as_the_names_and_stat_bytes_written(tmp_path):
    reader = _loaded(tmp_path)
    names, stats = reader.read_team_roster(9)
    assert names == [fixture.player_name(9, slot) for slot in range(fixture.ROSTER_PLAYERS)]
    assert stats == [fixture.player_stats(9, slot) for slot in range(fixture.ROSTER_PLAYERS)]


def test_every_team_reads_back_its_own_roster_and_no_other(tmp_path):
    """The one assertion a uniform fixture could not make.

    Each name carries the team index, so a reader that ignored `team_index`, or
    resolved every pointer to one block, fails here and nowhere else.
    """
    reader = _loaded(tmp_path)
    firsts = [reader.read_team_roster(i)[0][0] for i in range(TEAM_COUNT)]
    assert firsts == [fixture.player_name(i, 0) for i in range(TEAM_COUNT)]
    assert len(set(firsts)) == TEAM_COUNT


def test_a_roster_is_read_from_a_headered_image_too(tmp_path):
    reader = _loaded(tmp_path, with_smc_header=True)
    names, _ = reader.read_team_roster(9)
    assert names == [fixture.player_name(9, slot) for slot in range(fixture.ROSTER_PLAYERS)]


def test_reading_a_roster_past_the_last_team_returns_nothing(tmp_path):
    assert _loaded(tmp_path).read_team_roster(TEAM_COUNT) == ([], [])


def test_a_shorter_roster_stops_at_its_terminator(tmp_path):
    reader = _loaded(tmp_path, players_per_team=5)
    names, stats = reader.read_team_roster(2)
    assert len(names) == 5
    assert len(stats) == 5
    assert names[-1] == fixture.player_name(2, 4)


def test_the_forward_and_defence_counts_come_from_byte_17(tmp_path):
    reader = _loaded(tmp_path)
    # Slots 26 and 27 are under the reader's sanity floor and are covered
    # separately; every other slot reports what the image says.
    read = [reader.read_team_player_counts(i) for i in range(26)]
    expected = [(2, forwards, defence) for forwards, defence in fixture.TEAM_FD_COUNTS[:26]]
    assert read == expected
    # Not one uniform triple, which is what would make the equality above hold
    # for a reader that ignored the slot index.
    assert len(set(read)) == 26


def test_the_goalie_count_is_always_two_and_is_never_read_from_the_image(tmp_path):
    reader = _loaded(tmp_path)
    goalies = {reader.read_team_player_counts(i)[0] for i in range(TEAM_COUNT)}
    assert goalies == {2}


def test_too_few_forwards_in_the_image_falls_back_to_the_default(tmp_path):
    # Slot 26 carries 2 forwards, under the reader's floor of 3.
    assert fixture.TEAM_FD_COUNTS[26][0] == 2
    assert _loaded(tmp_path).read_team_player_counts(26) == (2, 14, 7)


def test_too_few_defencemen_in_the_image_falls_back_to_the_default(tmp_path):
    # Slot 27 carries 1 defenceman, under the reader's floor of 2.
    assert fixture.TEAM_FD_COUNTS[27][1] == 1
    assert _loaded(tmp_path).read_team_player_counts(27) == (2, 14, 7)


def test_the_low_edge_of_the_defence_test_is_accepted(tmp_path):
    # Slot 25 carries exactly 2, which passes. Pinning the boundary is what
    # tells `>= 2` apart from `> 2`.
    assert fixture.TEAM_FD_COUNTS[25][1] == 2
    assert _loaded(tmp_path).read_team_player_counts(25) == (2, 14, 2)


def test_counts_past_the_last_team_fall_back_to_the_default(tmp_path):
    assert _loaded(tmp_path).read_team_player_counts(TEAM_COUNT) == (2, 14, 7)


def test_counts_from_an_unloaded_reader_fall_back_to_the_default(tmp_path):
    reader = NHL94SNESRomReader(str(tmp_path / "absent.sfc"))
    assert reader.read_team_player_counts(0) == (2, 14, 7)


@pytest.mark.parametrize(
    ("length", "expected"),
    [
        (0, ("", 0)),  # below the reader's floor of 2
        (1, ("", 0)),
        (41, ("", 0)),  # above its ceiling of 40
    ],
)
def test_a_length_word_outside_the_readers_bounds_reads_as_nothing(tmp_path, length, expected):
    rom = fixture.build_nhl94_snes_rom()
    offset = fixture.team_base(0) + fixture.TEAM_HEADER_SIZE
    rom[offset] = length & 0xFF
    rom[offset + 1] = (length >> 8) & 0xFF
    assert _reader(_write(tmp_path, "x.sfc", rom))._read_length_prefixed_string(offset) == expected


def test_a_length_word_at_the_ceiling_is_read(tmp_path):
    """40 is accepted and 41 is not, so the boundary is pinned from both sides."""
    rom = fixture.build_nhl94_snes_rom()
    offset = fixture.team_base(0) + fixture.TEAM_HEADER_SIZE
    rom[offset : offset + 2] = (40).to_bytes(2, "little")
    rom[offset + 2 : offset + 40] = b"A" * 38
    assert _reader(_write(tmp_path, "x.sfc", rom))._read_length_prefixed_string(offset) == (
        "A" * 38,
        40,
    )


def test_a_string_running_past_the_end_of_the_file_reads_as_nothing(tmp_path):
    rom = fixture.build_nhl94_snes_rom()
    offset = len(rom) - 4
    rom[offset : offset + 2] = (30).to_bytes(2, "little")
    assert _reader(_write(tmp_path, "x.sfc", rom))._read_length_prefixed_string(offset) == ("", 0)


def test_a_string_that_starts_past_the_end_of_the_file_reads_as_nothing(tmp_path):
    reader = _loaded(tmp_path)
    assert reader._read_length_prefixed_string(len(reader.data or b"")) == ("", 0)


def test_a_non_ascii_byte_in_a_name_is_replaced_rather_than_raising(tmp_path):
    rom = fixture.build_nhl94_snes_rom()
    offset = fixture.team_base(0) + fixture.TEAM_HEADER_SIZE
    rom[offset + 2] = 0xFF
    names, _ = _reader(_write(tmp_path, "x.sfc", rom)).read_team_roster(0)
    assert names[0] == "�" + fixture.player_name(0, 0)[1:]


def test_the_header_size_word_is_where_the_player_records_begin(tmp_path):
    reader = _loaded(tmp_path)
    base = fixture.team_base(4)
    assert reader._skip_team_header(base) == base + fixture.TEAM_HEADER_SIZE


def test_a_header_word_at_the_very_end_of_the_file_leaves_the_offset_alone(tmp_path):
    reader = _loaded(tmp_path)
    end = len(reader.data or b"")
    assert reader._skip_team_header(end - 1) == end - 1
