"""The ported NBA Live 95 writer, against a synthetic 2 MB Genesis image.

Records here are variable-length -- 69 fixed bytes plus a name -- and packed with
no padding, so how much room a name gets is a property of the *gap to the next
pointer* rather than of the record. `_compute_record_limits` measures all 360
gaps at load time, and most of this file is about what that budget does to a
name that will not fit.

Every read-back goes through `synthetic_nbalive95_rom.decode_player_record`,
which transcribes the layout independently of `src/`, and never through
`NBALive95RomReader`: a decoder built from the reader's own offsets would agree
with any rearrangement of them.
"""

import struct

import pytest

from retro_roster_patcher.games.nbalive95_genesis.models import (
    CHECKSUM_BYPASS_BYTES,
    CHECKSUM_BYPASS_OFFSET,
    NBALive95PlayerRecord,
)
from retro_roster_patcher.games.nbalive95_genesis.rom_writer import (
    FIXED_SIZE,
    NBALive95RomWriter,
    _encode_name_variable,
)
from tests.fixtures import synthetic_nbalive95_rom as fixture


@pytest.fixture
def rom(tmp_path):
    return fixture.write_nbalive95_rom(tmp_path / "nbalive95.bin")


@pytest.fixture
def out(tmp_path):
    return tmp_path / "patched.bin"


def _loaded(rom, out):
    writer = NBALive95RomWriter(str(rom), str(out))
    assert writer.load() is True
    return writer


def _record(**overrides):
    fields = {
        "name_last": "Curry",
        "name_first": "Stephen",
        "jersey": 30,
        "position": 3,
        "height_inches": 74,
        "weight_lbs": 185,
        "experience": 15,
        "skin_color": 2,
        "hair_style": 5,
        "ratings": list(range(10, 26)),
        "season_stats": [0] * 17,
    }
    fields.update(overrides)
    return NBALive95PlayerRecord(**fields)


# -- name encoding ----------------------------------------------------------


def test_a_name_that_fits_keeps_the_whole_first_name():
    assert _encode_name_variable("Curry", "Stephen", 20) == b"Curry\x00Stephen\x00\x00"


def test_a_name_that_fits_exactly_is_still_written_in_full():
    assert _encode_name_variable("Curry", "Stephen", 15) == b"Curry\x00Stephen\x00\x00"


def test_one_byte_short_collapses_the_first_name_to_an_initial():
    assert _encode_name_variable("Curry", "Stephen", 14) == b"Curry\x00S.\x00\x00"


def test_a_budget_too_small_for_the_initial_form_truncates_the_last_name():
    assert _encode_name_variable("Curry", "Stephen", 9) == b"Curr\x00S.\x00\x00"


def test_the_smallest_budget_the_limit_table_can_hold_keeps_one_letter():
    """4 is `_compute_record_limits`'s floor, and it does not fit even `X\\0Y.\\0\\0`."""
    assert _encode_name_variable("Curry", "Stephen", 4) == b"C\x00\x00"


def test_an_absent_first_name_is_simply_omitted_when_the_full_form_fits():
    assert _encode_name_variable("Curry", "", 10) == b"Curry\x00\x00\x00"


def test_an_absent_first_name_shortens_the_last_name_instead_of_writing_an_initial():
    """A tight budget with no first name truncates; it never reaches `A.`."""
    assert _encode_name_variable("Curry", "", 6) == b"C\x00\x00\x00"


def test_the_literal_a_the_encoder_falls_back_to_is_unreachable():
    """DEAD CODE, pinned: `first_bytes[0] if first_bytes else ord("A")` cannot fire.

    The initial form costs `len(last) + 5` bytes and the full form with no first
    name costs `len(last) + 3`, so a budget large enough for the first is always
    large enough for the second -- and truncation, which is the only thing that
    changes `len(last)`, cuts it to `budget - 5` and puts the full form back
    inside the budget. Swept rather than argued, because the argument is exactly
    the kind that turns out to have an edge.

    Budgets start at 3; the test below says what happens under that.
    `_compute_record_limits` floors every budget at 4, so nothing here is
    reachable through the writer.
    """
    produced = [
        _encode_name_variable("X" * length, "", budget)
        for length in range(0, 31)
        for budget in range(3, 65)
    ]
    assert [name for name in produced if b"A." in name] == []
    assert len(produced) == 1922
    # Not vacuous: the same sweep with a first name does reach the initial form.
    with_first = [
        _encode_name_variable("X" * length, "Stephen", budget)
        for length in range(0, 31)
        for budget in range(3, 65)
    ]
    assert len([name for name in with_first if b"S." in name]) == 590


def test_a_budget_of_two_raises_rather_than_returning_a_short_name():
    """Latent defect, pinned. The last-resort branch resizes its own buffer.

    `result[: len(last_bytes)] = last_bytes[: len(result) - 2]` assigns a
    shorter slice to a longer one, and on a `bytearray` that deletes the
    difference -- so a two-byte buffer becomes one byte and `result[-2]` is out
    of range. Unreachable through `write_player`, whose budgets are floored at
    4, and reachable by any other caller.
    """
    with pytest.raises(IndexError):
        _encode_name_variable("Curry", "Stephen", 2)


def test_a_budget_of_two_with_no_last_name_survives():
    """The same branch with nothing to truncate, which is what makes it a resize
    bug rather than a size-two bug."""
    assert _encode_name_variable("", "Stephen", 2) == b"\x00\x00"


def test_two_empty_names_encode_to_three_nulls():
    assert _encode_name_variable("", "", 4) == b"\x00\x00\x00"


def test_a_hyphenated_name_is_truncated_rather_than_abbreviated_away():
    assert (
        _encode_name_variable("Alexander-Walker", "Nickeil", 20) == b"Alexander-Walke\x00N.\x00\x00"
    )


def test_a_non_ascii_name_is_replaced_rather_than_raising():
    assert _encode_name_variable("Doncic", "Luka", 20) == b"Doncic\x00Luka\x00\x00"
    assert _encode_name_variable("Dončić", "Luka", 20) == b"Don?i?\x00Luka\x00\x00"


def test_no_encoding_ever_exceeds_the_budget_it_was_given():
    """Sweeping the budget, because a single over-long return corrupts the next record."""
    lengths = [len(_encode_name_variable("Antetokounmpo", "Giannis", n)) for n in range(4, 40)]
    over = [n for n, length in zip(range(4, 40), lengths, strict=True) if length > n]
    assert over == []
    # And it is not vacuous: some of those budgets really are being filled.
    assert max(lengths) == 23


# -- loading and the budget table -------------------------------------------


def test_load_refuses_an_image_the_reader_rejects(tmp_path, out):
    path = fixture.write_nbalive95_rom(tmp_path / "96.bin", title="NBA LIVE 96")
    assert NBALive95RomWriter(str(path), str(out)).load() is False


def test_load_refuses_a_file_that_is_not_there(tmp_path, out):
    assert NBALive95RomWriter(str(tmp_path / "absent.bin"), str(out)).load() is False


def test_load_copies_the_image_rather_than_aliasing_the_readers(rom, out):
    writer = _loaded(rom, out)
    writer.data[0] = writer.data[0] ^ 0xFF
    assert writer.data[0] != writer.reader.data[0]


def test_the_budget_table_covers_every_one_of_the_three_hundred_and_sixty_slots(rom, out):
    assert len(_loaded(rom, out)._record_limits) == 360


def test_every_budget_is_the_gap_to_the_next_record_less_the_fixed_bytes(rom, out):
    writer = _loaded(rom, out)
    found = [writer._record_limits[(team, slot)] for team in range(30) for slot in range(12)]
    expected = [fixture.name_budget(team, slot) for team in range(30) for slot in range(12)]
    assert found == expected


def test_the_budgets_within_one_team_are_not_all_the_same(rom, out):
    """Guards the test above from passing on a table of one repeated number."""
    writer = _loaded(rom, out)
    assert len({writer._record_limits[(0, slot)] for slot in range(12)}) == 4


def test_the_last_record_of_a_team_is_measured_by_scanning_for_two_nulls(rom, out):
    """It has no next pointer, so its budget is exactly its own name's length."""
    writer = _loaded(rom, out)
    size = writer._original_record_size(fixture.player_offset(3, 11))
    assert size == fixture.record_size(3, 11)


def test_a_record_with_no_terminator_before_the_end_is_measured_to_the_end(rom, out):
    """The scan's other exit: `pos < len(self.data)` fails first."""
    writer = _loaded(rom, out)
    start = len(writer.data) - 200
    writer.data[start:] = b"\x41" * 200
    assert writer._original_record_size(start) == 200


def test_a_slot_whose_pointer_is_zero_gets_no_budget_entry(rom, out):
    data = bytearray(rom.read_bytes())
    table = fixture.TEAM_ROSTER_ADDRESSES[6]
    data[table + 2 * 4 : table + 3 * 4] = b"\x00\x00\x00\x00"
    rom.write_bytes(bytes(data))
    writer = _loaded(rom, out)
    assert (6, 2) not in writer._record_limits


# -- the checksum bypass ----------------------------------------------------


def test_the_bypass_replaces_the_jump_with_three_nops(rom, out):
    writer = _loaded(rom, out)
    writer.apply_patches()
    patched = writer.data[CHECKSUM_BYPASS_OFFSET : CHECKSUM_BYPASS_OFFSET + 6]
    assert bytes(patched) == b"\x4e\x71\x4e\x71\x4e\x71"


def test_the_bypass_bytes_are_three_whole_nops_and_not_a_reset(rom, out):
    """The reason the offset is 0x690 and not Team-95's 0x691.

    At 0x691 the same six bytes land one byte off and the 68000 decodes 0x4E70,
    RESET, which halts the CPU. This asserts the alignment rather than the
    comment: every 16-bit word of the replacement must be NOP.
    """
    words = [
        struct.unpack_from(">H", CHECKSUM_BYPASS_BYTES, index)[0]
        for index in range(0, len(CHECKSUM_BYPASS_BYTES), 2)
    ]
    assert words == [0x4E71, 0x4E71, 0x4E71]
    assert CHECKSUM_BYPASS_OFFSET % 2 == 0


def test_the_bypass_changes_nothing_outside_its_six_bytes(rom, out):
    writer = _loaded(rom, out)
    before = bytes(writer.data)
    writer.apply_patches()
    changed = [
        index for index, (a, b) in enumerate(zip(before, writer.data, strict=True)) if a != b
    ]
    window = range(CHECKSUM_BYPASS_OFFSET, CHECKSUM_BYPASS_OFFSET + 6)
    assert [index for index in changed if index not in window] == []
    # Five, not six: the first byte of the fixture's `JSR` is already 0x4E, which
    # is also the first byte of a NOP. Pinning the exact set is what makes an
    # off-by-one in the offset visible.
    assert changed == [0x691, 0x692, 0x693, 0x694, 0x695]


def test_the_bypass_does_nothing_before_load(rom, out):
    writer = NBALive95RomWriter(str(rom), str(out))
    writer.apply_patches()
    assert writer.data is None


# -- writing a record -------------------------------------------------------


def test_a_written_record_carries_every_field_the_caller_supplied(rom, out):
    writer = _loaded(rom, out)
    assert writer.write_player(4, 2, _record()) is True
    decoded = fixture.decode_player_record(writer.data, fixture.player_offset(4, 2))
    assert decoded["jersey"] == 30
    assert decoded["position"] == 3
    assert decoded["height_byte"] == 74
    assert decoded["weight_byte"] == 85
    assert decoded["experience"] == 15
    assert decoded["skin"] == 2
    assert decoded["hair"] == 5
    assert decoded["ratings"] == list(range(10, 26))
    assert decoded["last_name"] == "Curry"
    assert decoded["first_name"] == "Stephen"


def test_the_weight_byte_is_the_pounds_less_a_hundred(rom, out):
    writer = _loaded(rom, out)
    writer.write_player(4, 2, _record(weight_lbs=250))
    decoded = fixture.decode_player_record(writer.data, fixture.player_offset(4, 2))
    assert decoded["weight_byte"] == 150


def test_a_weight_under_a_hundred_pounds_clamps_to_zero_rather_than_wrapping(rom, out):
    writer = _loaded(rom, out)
    writer.write_player(4, 2, _record(weight_lbs=40))
    decoded = fixture.decode_player_record(writer.data, fixture.player_offset(4, 2))
    assert decoded["weight_byte"] == 0


def test_an_out_of_range_jersey_is_clamped_to_ninety_nine(rom, out):
    writer = _loaded(rom, out)
    writer.write_player(4, 2, _record(jersey=250))
    decoded = fixture.decode_player_record(writer.data, fixture.player_offset(4, 2))
    assert decoded["jersey"] == 99


def test_a_position_outside_the_games_five_is_clamped_to_four(rom, out):
    writer = _loaded(rom, out)
    writer.write_player(4, 2, _record(position=9))
    decoded = fixture.decode_player_record(writer.data, fixture.player_offset(4, 2))
    assert decoded["position"] == 4


def test_ratings_above_ninety_nine_are_clamped(rom, out):
    writer = _loaded(rom, out)
    writer.write_player(4, 2, _record(ratings=[150] * 16))
    decoded = fixture.decode_player_record(writer.data, fixture.player_offset(4, 2))
    assert decoded["ratings"] == [99] * 16


def test_a_short_ratings_list_is_padded_with_fifty(rom, out):
    writer = _loaded(rom, out)
    writer.write_player(4, 2, _record(ratings=[7, 8]))
    decoded = fixture.decode_player_record(writer.data, fixture.player_offset(4, 2))
    assert decoded["ratings"] == [7, 8] + [50] * 14


def test_skin_and_hair_are_clamped_to_the_ranges_the_game_defines(rom, out):
    writer = _loaded(rom, out)
    writer.write_player(4, 2, _record(skin_color=200, hair_style=200))
    decoded = fixture.decode_player_record(writer.data, fixture.player_offset(4, 2))
    assert decoded["skin"] == 3
    assert decoded["hair"] == 0x26


def test_the_season_stats_written_are_the_seventeen_zeros_every_mapped_record_carries(rom, out):
    """INHERITED DEFECT, pinned: the 1994 stat line is destroyed, not preserved.

    The fixture writes 17 non-zero values here, so this assertion fails if the
    write is ever dropped -- which is the repair, and it must be a deliberate
    commit rather than a silent one.
    """
    writer = _loaded(rom, out)
    before = fixture.decode_player_record(writer.data, fixture.player_offset(4, 2))
    assert before["season_stats"] == fixture.player_season_stats(4, 2)
    writer.write_player(4, 2, _record())
    after = fixture.decode_player_record(writer.data, fixture.player_offset(4, 2))
    assert after["season_stats"] == [0] * 17


def test_a_season_stat_the_game_cannot_hold_raises_rather_than_being_clamped(rom, out):
    """Every other numeric field is clamped; this one is packed unchecked.

    Inherited, and unreachable from `map_player`, which always supplies zeros.
    Pinned so a future change that starts supplying real stats finds out here.
    """
    writer = _loaded(rom, out)
    with pytest.raises(struct.error):
        writer.write_player(4, 2, _record(season_stats=[70000] * 17))


def test_the_byte_the_writer_calls_unknown_two_is_zeroed(rom, out):
    writer = _loaded(rom, out)
    assert fixture.player_unknown2(4, 2) != 0
    writer.write_player(4, 2, _record())
    decoded = fixture.decode_player_record(writer.data, fixture.player_offset(4, 2))
    assert decoded["unknown2"] == 0


def test_the_university_byte_is_left_alone(rom, out):
    writer = _loaded(rom, out)
    writer.write_player(4, 2, _record())
    decoded = fixture.decode_player_record(writer.data, fixture.player_offset(4, 2))
    assert decoded["university"] == fixture.player_university(4, 2)


def test_the_ten_bytes_before_the_name_are_left_alone(rom, out):
    writer = _loaded(rom, out)
    writer.write_player(4, 2, _record())
    decoded = fixture.decode_player_record(writer.data, fixture.player_offset(4, 2))
    assert decoded["unknown3"] == fixture.player_unknown3(4, 2)


def test_writing_one_record_leaves_the_next_records_first_byte_alone(rom, out):
    """The packing invariant: an over-long name would land on the next jersey."""
    writer = _loaded(rom, out)
    writer.write_player(4, 2, _record(name_last="Gilgeous-Alexander", name_first="Shai"))
    following = fixture.decode_player_record(writer.data, fixture.player_offset(4, 3))
    assert following["jersey"] == fixture.player_jersey(4, 3)
    assert following["last_name"] == fixture.player_last_name(4, 3)


def test_a_name_too_long_for_its_slot_is_shortened_to_the_budget(rom, out):
    writer = _loaded(rom, out)
    budget = fixture.name_budget(4, 0)
    assert budget == 21
    writer.write_player(4, 0, _record(name_last="Gilgeous-Alexander", name_first="Shai"))
    decoded = fixture.decode_player_record(writer.data, fixture.player_offset(4, 0))
    # Both shortenings at once: the surname is cut to `budget - 5` and the
    # forename to an initial, which together come to exactly the budget.
    assert decoded["last_name"] == "Gilgeous-Alexand"
    assert decoded["first_name"] == "S."


def test_writing_to_a_team_past_the_last_one_fails(rom, out):
    assert _loaded(rom, out).write_player(30, 0, _record()) is False


def test_writing_to_a_slot_past_the_twelfth_fails(rom, out):
    assert _loaded(rom, out).write_player(0, 12, _record()) is False


def test_writing_behind_a_zero_pointer_fails(rom, out):
    data = bytearray(rom.read_bytes())
    table = fixture.TEAM_ROSTER_ADDRESSES[8]
    data[table + 4 : table + 8] = b"\x00\x00\x00\x00"
    rom.write_bytes(bytes(data))
    assert _loaded(rom, out).write_player(8, 1, _record()) is False


def test_writing_before_load_fails(rom, out):
    assert NBALive95RomWriter(str(rom), str(out)).write_player(0, 0, _record()) is False


# -- writing a roster -------------------------------------------------------


def test_a_full_roster_reports_twelve_written(rom, out):
    writer = _loaded(rom, out)
    assert writer.write_team_roster(2, [_record(jersey=n) for n in range(12)]) == 12


def test_a_roster_longer_than_the_twelve_slots_is_cut(rom, out):
    writer = _loaded(rom, out)
    assert writer.write_team_roster(2, [_record(jersey=n % 100) for n in range(40)]) == 12


def test_a_roster_shorter_than_the_slots_leaves_the_rest_alone(rom, out):
    writer = _loaded(rom, out)
    assert writer.write_team_roster(2, [_record(), _record()]) == 2
    untouched = fixture.decode_player_record(writer.data, fixture.player_offset(2, 2))
    assert untouched["last_name"] == fixture.player_last_name(2, 2)


def test_an_empty_roster_writes_nothing(rom, out):
    writer = _loaded(rom, out)
    before = bytes(writer.data)
    assert writer.write_team_roster(2, []) == 0
    assert bytes(writer.data) == before


def test_a_roster_for_a_team_past_the_last_one_reports_the_error_return(rom, out):
    assert _loaded(rom, out).write_team_roster(30, [_record()]) == -1


def test_a_roster_written_before_load_reports_the_error_return(rom, out):
    assert NBALive95RomWriter(str(rom), str(out)).write_team_roster(0, [_record()]) == -1


def test_a_roster_skips_the_slots_whose_pointers_are_gone(rom, out):
    data = bytearray(rom.read_bytes())
    table = fixture.TEAM_ROSTER_ADDRESSES[8]
    for slot in (1, 5, 9):
        data[table + slot * 4 : table + slot * 4 + 4] = b"\x00\x00\x00\x00"
    rom.write_bytes(bytes(data))
    writer = _loaded(rom, out)
    assert writer.write_team_roster(8, [_record() for _ in range(12)]) == 9


def test_each_slot_of_a_roster_lands_on_its_own_record(rom, out):
    writer = _loaded(rom, out)
    writer.write_team_roster(2, [_record(name_last=f"NAME{n:02d}", jersey=n) for n in range(12)])
    found = [
        fixture.decode_player_record(writer.data, fixture.player_offset(2, slot))["last_name"]
        for slot in range(12)
    ]
    assert found == [f"NAME{n:02d}" for n in range(12)]


# -- the header checksum and finalize ---------------------------------------


def _expected_checksum(data: bytes) -> int:
    """The documented sum, computed here rather than by calling the writer."""
    total = 0
    for index in range(0x200, len(data) - 1, 2):
        total += (data[index] << 8) | data[index + 1]
    return total & 0xFFFF


def test_finalize_writes_the_recomputed_header_checksum(rom, out):
    writer = _loaded(rom, out)
    writer.write_team_roster(0, [_record()])
    assert writer.finalize() is True
    written = out.read_bytes()
    assert struct.unpack_from(">H", written, 0x18E)[0] == _expected_checksum(written)


def test_the_checksum_the_fixture_shipped_was_not_already_correct(rom, out):
    """Guards the test above from passing on an image that needed no change."""
    original = rom.read_bytes()
    assert struct.unpack_from(">H", original, 0x18E)[0] == fixture.CHECKSUM_FILLER
    assert struct.unpack_from(">H", original, 0x18E)[0] != _expected_checksum(original)


def test_the_checksum_covers_the_bytes_a_patch_changes(rom, out):
    """Two runs that differ only in what was written must differ in the word."""
    first = _loaded(rom, out)
    first.write_team_roster(0, [_record(jersey=1)])
    first.finalize()
    second_path = out.with_name("second.bin")
    second = _loaded(rom, second_path)
    second.write_team_roster(0, [_record(jersey=99)])
    second.finalize()
    a = struct.unpack_from(">H", out.read_bytes(), 0x18E)[0]
    b = struct.unpack_from(">H", second_path.read_bytes(), 0x18E)[0]
    assert a != b


def test_the_checksum_is_not_recomputed_for_a_file_shorter_than_the_region(rom, out):
    writer = _loaded(rom, out)
    writer.data = bytearray(0x100)
    writer._fix_checksum()
    assert bytes(writer.data) == bytes(0x100)


def test_finalize_creates_the_output_directory(rom, tmp_path):
    target = tmp_path / "nested" / "deeper" / "patched.bin"
    writer = _loaded(rom, target)
    assert writer.finalize() is True
    assert target.is_file() is True


def test_finalize_writes_an_image_the_same_length_as_the_input(rom, out):
    writer = _loaded(rom, out)
    writer.write_team_roster(0, [_record() for _ in range(12)])
    writer.finalize()
    assert len(out.read_bytes()) == fixture.ROM_SIZE


def test_finalize_before_load_reports_failure(rom, out):
    assert NBALive95RomWriter(str(rom), str(out)).finalize() is False


def test_finalize_reports_failure_when_the_output_path_cannot_be_written(rom, tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        writer = _loaded(rom, locked / "patched.bin")
        assert writer.finalize() is False
    finally:
        locked.chmod(0o700)


def test_a_finalized_image_differs_from_the_input_only_where_a_patch_landed(rom, out):
    """The whole write path, bounded: bypass, one record, checksum, nothing else."""
    writer = _loaded(rom, out)
    writer.apply_patches()
    writer.write_player(0, 0, _record())
    writer.finalize()
    before = rom.read_bytes()
    after = out.read_bytes()
    changed = [index for index, (a, b) in enumerate(zip(before, after, strict=True)) if a != b]
    record = fixture.player_offset(0, 0)
    allowed = set(range(CHECKSUM_BYPASS_OFFSET, CHECKSUM_BYPASS_OFFSET + 6))
    allowed |= {0x18E, 0x18F}
    allowed |= set(range(record, record + FIXED_SIZE + fixture.name_budget(0, 0)))
    assert [index for index in changed if index not in allowed] == []
    assert len(changed) > 6
