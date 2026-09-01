"""Reader coverage against the synthetic ROM in `tests/fixtures/synthetic_rom.py`.

NHL 94 is copyrighted, so nothing here touches a real image. Every test builds a
1 MB `bytearray` under `tmp_path`; the reader takes a path rather than bytes, so
there is no injection seam and a file has to exist. That costs about 1.3 ms per
test (build, write, load), which is why each test gets its own image instead of
sharing a session-scoped one.

This file's arrival made `tests/` a real package. `tests/__init__.py` is the
load-bearing one: without it pytest prepends `tests/` to `sys.path`, so
`tests.fixtures.synthetic_rom` and `fixtures.synthetic_rom` both resolve — to two
distinct module objects wrapping one file — and `mypy src tests` refuses to run at
all, reporting the same file under two module names. The three leaf `__init__.py`
files below it are convention (matching `tests/core/` and `tests/sports/`) and
defensive: with `tests/__init__.py` in place, pytest 9.1.1 collects a second
`tests/games/we2002/test_rom_reader.py` with or without them, but the
package-qualified module name is what makes that true.

The second half of this file pins defects rather than intended behaviour. The
reader is a faithful port and fidelity is the standing policy, so these tests
assert what it *does*; each carries a comment saying why that is wrong, so a fix
shows up as a deliberate red test rather than a silent behaviour change.
"""

import sys

import pytest

from retro_roster_patcher.games.nhl94_genesis.models import MAX_PLAYERS_PER_TEAM
from retro_roster_patcher.games.nhl94_genesis.rom_reader import NHL94GenesisRomReader
from tests.fixtures import synthetic_rom


def _write(tmp_path, name, rom):
    path = tmp_path / name
    path.write_bytes(bytes(rom))
    return path


def _reader(path):
    reader = NHL94GenesisRomReader(str(path))
    assert reader.load() is True
    return reader


def _loaded_reader(tmp_path):
    return _reader(synthetic_rom.write_nhl94_genesis_rom(tmp_path / "nhl94.bin"))


def _expected_offsets(team_index):
    base = synthetic_rom.team_base(team_index)
    return {
        "players": base + synthetic_rom.SEC_PLAYERS,
        "palettes": base + synthetic_rom.SEC_PALETTES,
        "strings": base + synthetic_rom.SEC_STRINGS,
        "lines": base + synthetic_rom.SEC_LINES,
        "ratings": base + synthetic_rom.SEC_RATINGS,
        "goalies": base + synthetic_rom.SEC_GOALIES,
    }


def _unterminated_rom():
    """A ROM whose team 0 roster has no sentinel and runs to the last byte.

    The tail is a whole number of 18-byte records plus a 16-byte remainder, so the
    final record's name is inside the file while its stat bytes are not.
    """
    rom = synthetic_rom.build_nhl94_genesis_rom()
    start = synthetic_rom.team_base(0) + synthetic_rom.SEC_PLAYERS
    record = synthetic_rom.player_record(0, 0)
    repeats = (len(rom) - start) // len(record) + 1
    rom[start:] = (record * repeats)[: len(rom) - start]
    return rom


# ── The fixture itself ───────────────────────────────────────────────────


def test_the_fixture_is_bound_under_exactly_one_module_name():
    # The mechanism, not a restatement of the import: `tests/` staying off
    # sys.path is what makes `fixtures.synthetic_rom` unreachable, and so makes
    # the second binding impossible rather than merely unwritten. Deleting
    # `tests/__init__.py` turns the second assertion red (and breaks
    # `mypy src tests`); the three leaf `__init__.py` files are not covered here
    # because removing any one of them changes neither pytest nor mypy today.
    assert synthetic_rom.__name__ == "tests.fixtures.synthetic_rom"
    assert [p for p in sys.path if p.endswith("/tests")] == []
    assert "fixtures.synthetic_rom" not in sys.modules


# ── Loading and validation ───────────────────────────────────────────────


def test_the_synthetic_rom_validates(tmp_path):
    assert _loaded_reader(tmp_path).validate() is True


def test_a_missing_file_fails_to_load(tmp_path):
    assert NHL94GenesisRomReader(str(tmp_path / "nope.bin")).load() is False


def test_a_path_that_is_a_directory_fails_to_load(tmp_path):
    # The `os.path.exists` guard passes for a directory, so this is the only way
    # into `load`'s `except Exception` arm — `open(dir, "rb")` raises
    # IsADirectoryError. Without it the branch is never executed and a `return
    # True` there would go unnoticed.
    reader = NHL94GenesisRomReader(str(tmp_path))
    assert reader.load() is False
    assert reader.data is None


def test_a_too_small_file_does_not_validate(tmp_path):
    truncated = tmp_path / "small.bin"
    truncated.write_bytes(b"\x00" * 1024)
    reader = _reader(truncated)
    assert reader.validate() is False


def test_a_pointer_past_the_end_of_the_file_does_not_validate(tmp_path):
    # The rejection happens inside `_read_team_pointer`, which already returns None
    # for `addr >= len(data)`. `validate`'s own `first_ptr >= size` clause is
    # therefore unreachable — deleting it changes nothing — so this pins the
    # behaviour rather than that particular line.
    rom = synthetic_rom.build_nhl94_genesis_rom()
    rom[synthetic_rom.POINTER_TABLE_OFFSET : synthetic_rom.POINTER_TABLE_OFFSET + 4] = (
        b"\xff\xff\xff\xff"
    )
    assert _reader(_write(tmp_path, "bad.bin", rom)).validate() is False


# ── Section offsets ──────────────────────────────────────────────────────


def test_section_offsets_resolve_to_absolute_addresses(tmp_path):
    # Written as literals rather than derived from the fixture constants: this is
    # the one place that pins both the order the six 16-bit section pointers are
    # read in and the fact that each is an offset relative to the team block.
    assert _loaded_reader(tmp_path).get_team_section_offsets(0) == {
        "players": 0x010200,
        "palettes": 0x010080,
        "strings": 0x0100C0,
        "lines": 0x010100,
        "ratings": 0x010060,
        "goalies": 0x010070,
    }


def test_every_team_index_resolves_to_its_own_block(tmp_path):
    reader = _loaded_reader(tmp_path)
    resolved = [reader.get_team_section_offsets(i) for i in range(synthetic_rom.TEAM_COUNT)]
    assert resolved == [_expected_offsets(i) for i in range(synthetic_rom.TEAM_COUNT)]


def test_a_team_index_at_the_count_resolves_to_nothing(tmp_path):
    # `_read_team_pointer` is asserted on directly because it is the only guard
    # that is not masked by another: every public entry point checks
    # `team_index >= TEAM_COUNT` itself first, so removing the check here changes
    # nothing any of them return.
    reader = _loaded_reader(tmp_path)
    assert reader._read_team_pointer(synthetic_rom.TEAM_COUNT) is None
    assert reader.get_team_section_offsets(synthetic_rom.TEAM_COUNT) is None
    assert reader.read_team_roster(synthetic_rom.TEAM_COUNT) == ([], [])
    assert reader.get_team_player_region(synthetic_rom.TEAM_COUNT) == (0, 0)


# ── Team slots ───────────────────────────────────────────────────────────


def test_team_slots_read_the_city_strings(tmp_path):
    info = _loaded_reader(tmp_path).get_info()
    assert info.is_valid is True
    assert info.size == synthetic_rom.ROM_SIZE
    assert len(info.team_slots) == synthetic_rom.TEAM_COUNT
    assert (info.team_slots[0].current_name, info.team_slots[0].display_name) == (
        "Anaheim",
        "Anaheim",
    )
    assert info.team_slots[1].current_name == "Boston"
    # Slot 20 is the discriminating one: "St Louis" comes out of the image and
    # "St. Louis" out of NHL94_GEN_TEAM_ORDER, so a reader that filled
    # `current_name` from the constant table instead of the ROM fails here.
    assert (info.team_slots[20].current_name, info.team_slots[20].display_name) == (
        "St Louis",
        "St. Louis",
    )
    # The last slot, so a loop that stops one team early is caught.
    assert (info.team_slots[25].current_name, info.team_slots[25].display_name) == (
        "Winnipeg",
        "Winnipeg",
    )


def test_a_loaded_rom_that_fails_validation_reports_itself_invalid_with_no_slots(tmp_path):
    # The contract `core.patcher.analyze_rom` depends on, and the only path that
    # runs `get_info`'s main branch with `is_valid` false: a full-size file whose
    # first pointer is out of range. `test_an_empty_file_loads_as_an_empty_bytearray`
    # reaches the `not self.data` early return instead, so without this the
    # `team_slots=[]` arm and the reported `path` never execute with data present.
    rom = synthetic_rom.build_nhl94_genesis_rom()
    rom[synthetic_rom.POINTER_TABLE_OFFSET : synthetic_rom.POINTER_TABLE_OFFSET + 4] = (
        b"\xff\xff\xff\xff"
    )
    path = _write(tmp_path, "not_nhl94.bin", rom)
    info = _reader(path).get_info()
    assert info.path == str(path)
    assert info.size == synthetic_rom.ROM_SIZE
    assert info.is_valid is False
    assert info.team_slots == []


def test_a_team_whose_city_string_is_unreadable_falls_back_to_the_display_name(tmp_path):
    # A length word of zero makes `_read_length_prefixed_string` return "", which
    # is what the `current_name = name or display` fallback exists for. Slot 20 is
    # the vehicle because it is the one slot where the image and the constant table
    # disagree, so the fallback produces a value no other slot could.
    rom = synthetic_rom.build_nhl94_genesis_rom()
    strings = synthetic_rom.team_base(20) + synthetic_rom.SEC_STRINGS
    rom[strings : strings + 2] = b"\x00\x00"
    slots = _reader(_write(tmp_path, "no_city.bin", rom)).get_info().team_slots
    assert (slots[20].current_name, slots[20].display_name) == ("St. Louis", "St. Louis")
    # Neighbours still come from the image, so this is the fallback and not a
    # wholesale switch to the constant table.
    assert (slots[19].current_name, slots[21].current_name) == ("San Jose", "Tampa Bay")


def test_a_city_length_word_is_honoured_up_to_forty_and_rejected_above_it(tmp_path):
    # `length > 40` is what stops a garbage length word from yielding a 65533-byte
    # "name". Both sides of the ceiling are needed: a length of 0xFFFF would be
    # rejected by a `> 400` guard too. At exactly 40 the reader consumes 38 bytes
    # and runs straight through the city into the abbreviation that follows it,
    # which is what the returned string shows.
    rom = synthetic_rom.build_nhl94_genesis_rom()
    for team_index, length in ((19, 40), (20, 41)):
        strings = synthetic_rom.team_base(team_index) + synthetic_rom.SEC_STRINGS
        rom[strings : strings + 2] = length.to_bytes(2, "big")
    slots = _reader(_write(tmp_path, "long_city.bin", rom)).get_info().team_slots
    assert slots[19].current_name == "San Jose\x00\x05SJS"
    assert slots[20].current_name == "St. Louis"


# ── Roster reading ───────────────────────────────────────────────────────


def test_the_roster_region_spans_every_record_and_the_sentinel(tmp_path):
    reader = _loaded_reader(tmp_path)
    assert reader.get_team_player_region(0) == (0x010200, 452)
    # A team other than 0, so a region scan that ignored `team_index` and always
    # started from the first block is caught. `rom_writer.write_team_roster` sizes
    # its in-place patch from exactly this call.
    assert reader.get_team_player_region(25) == (0x029200, 452)


def test_a_player_record_identifies_its_team_and_its_slot(tmp_path):
    # Literal bytes rather than a call back into the fixture: byte 0 is the BCD
    # jersey (slot + 1), byte 1 the team and byte 2 the slot, each packed base 7 so
    # both nibbles stay inside the 0-6 the writer can emit. Team 13 rather than 0
    # so the team byte is non-zero.
    names, stats = _loaded_reader(tmp_path).read_team_roster(13)
    assert (names[0], stats[0]) == ("T13_PL00", b"\x01\x16\x00\x33\x44\x55\x66\x12")
    assert (names[24], stats[24]) == ("T13_PL24", b"\x25\x16\x33\x33\x44\x55\x66\x12")


@pytest.mark.parametrize("team_index", [0, 1, 9, 13, 20, 25])
def test_each_team_reads_its_own_roster_in_slot_order(tmp_path, team_index):
    # Zipped rather than compared as two lists: this is the assertion that pins
    # name-to-stats pairing, so a reader that returned the right names and the
    # right stat blocks rotated by one against each other fails here. Comparing
    # the lists separately would not.
    names, stats = _loaded_reader(tmp_path).read_team_roster(team_index)
    assert (len(names), len(stats)) == (synthetic_rom.ROSTER_PLAYERS,) * 2
    assert list(zip(names, stats, strict=True)) == [
        (synthetic_rom.player_name(team_index, slot), synthetic_rom.player_stats(team_index, slot))
        for slot in range(synthetic_rom.ROSTER_PLAYERS)
    ]


def test_a_length_word_of_two_ends_the_roster(tmp_path):
    # The reader's own header documents the sentinel as "0x0000 followed by
    # 0x0002", and the `length < 3` test is what makes a two-byte length — an empty
    # name — a terminator rather than a zero-length record followed by eight stat
    # bytes. A 0x0000 sentinel alone cannot tell those two readings apart.
    rom = synthetic_rom.build_nhl94_genesis_rom()
    sentinel = (
        synthetic_rom.team_base(0)
        + synthetic_rom.SEC_PLAYERS
        + synthetic_rom.ROSTER_PLAYERS * synthetic_rom.PLAYER_RECORD_SIZE
    )
    rom[sentinel : sentinel + 2] = b"\x00\x02"
    reader = _reader(_write(tmp_path, "empty_name_terminator.bin", rom))
    names, stats = reader.read_team_roster(0)
    assert (len(names), len(stats)) == (25, 25)
    assert reader.get_team_player_region(0) == (0x010200, 452)


# ── Pinned defects ───────────────────────────────────────────────────────


def test_validate_accepts_a_rom_that_get_info_then_crashes_on(tmp_path):
    # DEFECT: `validate` only dereferences pointer 0, but `get_info` walks all 26.
    # A pointer that is inside the file yet too close to the end passes validation
    # and then overruns in `_read_u16_be`. `core.patcher` requires `analyze_rom` to
    # answer `RomInfo(is_valid=False)` for a readable non-NHL94 file rather than
    # raise, because `retro-roster analyze` probes every patcher against one ROM,
    # so the orchestrator has to catch this itself.
    rom = synthetic_rom.build_nhl94_genesis_rom()
    rom[synthetic_rom.POINTER_TABLE_OFFSET + 4 : synthetic_rom.POINTER_TABLE_OFFSET + 8] = (
        b"\x00\x0f\xff\xff"
    )
    reader = _reader(_write(tmp_path, "late_pointer.bin", rom))
    assert reader.validate() is True
    with pytest.raises(IndexError):
        reader.get_info()


def test_the_player_region_can_extend_past_the_end_of_the_file(tmp_path):
    # DEFECT: the scan in `get_team_player_region` exits on `offset >= len(data) - 1`
    # and returns that overshot offset as the region end. With no length word below
    # 3 before EOF, the reported region runs two bytes past the image, and the
    # writer sizes its in-place patch from exactly this number.
    reader = _reader(_write(tmp_path, "unterminated.bin", _unterminated_rom()))
    start, size = reader.get_team_player_region(0)
    assert (start, size) == (0x010200, 982530)
    assert start + size == synthetic_rom.ROM_SIZE + 2


def test_an_unterminated_roster_is_read_with_no_size_cap(tmp_path):
    # DEFECT: `models.MAX_PLAYERS_PER_TEAM` is 25 and is never imported by the
    # reader, so `read_team_roster` scans to EOF. One call here yields 54,585 names
    # for a 25-slot team.
    #
    # DEFECT: the two lists come back misaligned. `names.append` happens before the
    # `break` for insufficient stat bytes, so the final record contributes a name
    # with no matching stats and any caller zipping the two silently drops a
    # player's data or pairs the wrong stats to the wrong name.
    names, stats = _reader(
        _write(tmp_path, "unterminated.bin", _unterminated_rom())
    ).read_team_roster(0)
    assert MAX_PLAYERS_PER_TEAM == 25
    assert len(names) == 54585
    assert len(stats) == 54584


def test_a_record_whose_stat_bytes_run_past_the_end_yields_a_name_with_no_stats(tmp_path):
    # DEFECT: the minimal form of the misalignment above — one name, zero stats.
    # `read_team_roster` does not call `validate`, so a file far under 1 MB is read
    # anyway rather than being rejected up front.
    rom = synthetic_rom.build_nhl94_genesis_rom()
    start = synthetic_rom.team_base(0) + synthetic_rom.SEC_PLAYERS
    cut = start + 2 + synthetic_rom.PLAYER_NAME_SIZE + 4  # name complete, stats not
    reader = _reader(_write(tmp_path, "cut.bin", rom[:cut]))
    assert reader.validate() is False
    assert reader.read_team_roster(0) == (["T00_PL00"], [])


def test_a_negative_team_index_reads_the_word_before_the_pointer_table(tmp_path):
    # DEFECT: every bounds check is `team_index >= TEAM_COUNT` only, so a negative
    # index indexes backwards out of the pointer table instead of returning None.
    # 0x0BAD is planted at 0x030A, the four bytes immediately below the table.
    rom = synthetic_rom.build_nhl94_genesis_rom()
    rom[0x030A:0x030E] = b"\x00\x00\x0b\xad"
    reader = _reader(_write(tmp_path, "negative.bin", rom))
    assert reader._read_team_pointer(-1) == 0x0BAD
    assert reader.get_team_section_offsets(-1) == dict.fromkeys(
        ["players", "palettes", "strings", "lines", "ratings", "goalies"], 0x0BAD
    )


def test_an_empty_file_loads_as_an_empty_bytearray(tmp_path):
    # DEFECT: `load` returns True for a zero-byte file and leaves `data` empty,
    # while `NHL94GenesisRomWriter.load` returns False for the same file. A caller
    # that branches on the reader's `load` gets a loaded-but-unusable reader.
    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    reader = NHL94GenesisRomReader(str(empty))
    assert reader.load() is True
    assert reader.data == bytearray()
    assert reader.validate() is False
    info = reader.get_info()
    # `path` is asserted in both `get_info` branches — this is the no-data one —
    # because it is what `analyze_rom` hands back to the caller that asked.
    assert info.path == str(empty)
    assert (info.size, info.is_valid, info.team_slots) == (0, False, [])
