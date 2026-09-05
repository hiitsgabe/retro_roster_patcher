"""The ISS reader, and the three-part signature check registration needed.

Upstream's `validate_rom` answered True for any existing file of a megabyte or
more, reading no byte of it. `analyze` probes every registered patcher against
one image, so that alone would claim every ROM and every ISO in a user's library.

The replacement is in three pieces, and which entry point each one guards is the
point:

  * `validate_rom` -- the 1 MB floor, kept, and the side effect that sets
    `header_offset`, which every other offset is measured from;
  * `data_fits` -- an ARITHMETIC bound, `MIN_PATCHABLE_SIZE`, which `patcher.py`
    applies to `patch` as well because a file failing it provably cannot be
    patched;
  * `signature_ok` -- a HEURISTIC, three pointer tables dereferenced, which
    guards `analyze_rom` alone.
"""

from __future__ import annotations

import os

import pytest

from retro_roster_patcher.games.iss_snes.models import TEAM_ENUM_ORDER, TOTAL_TEAMS
from retro_roster_patcher.games.iss_snes.rom_reader import (
    BYTE_TO_CHAR,
    ISSRomReader,
    decode_iss_name,
)
from retro_roster_patcher.games.iss_snes.rom_writer import CHAR_TO_BYTE, MIN_PATCHABLE_SIZE
from tests.fixtures import synthetic_iss_rom as fixture


@pytest.fixture
def rom(tmp_path):
    return fixture.write_iss_rom(tmp_path / "iss.sfc")


@pytest.fixture
def headered_rom(tmp_path):
    return fixture.write_iss_rom(tmp_path / "iss.smc", with_header=True)


def test_the_encode_and_decode_tables_are_inverses():
    """The defect the two lazily-filled copies invited, closed by comparison.

    Upstream kept the byte-to-character table in `rom_reader` and the
    character-to-byte table in `rom_writer`, each an empty module-level dict
    filled by its own `_init_encoding()`. Nothing compared them.
    """
    assert {value: key for key, value in CHAR_TO_BYTE.items()} == BYTE_TO_CHAR


def test_the_tables_cover_space_five_punctuation_marks_the_digits_and_both_cases():
    """Six scattered low values -- space and five marks -- then three runs.

    A period, a hyphen, a double quote, an apostrophe and a slash. Nothing else:
    an ampersand, a comma and every accented letter encode to 0x00, which is a
    SPACE, silently.
    """
    assert len(CHAR_TO_BYTE) == 6 + 10 + 26 + 26
    assert sorted(ch for ch in CHAR_TO_BYTE if not ch.isalnum()) == [
        " ",
        '"',
        "'",
        "-",
        ".",
        "/",
    ]


def test_a_character_the_table_has_no_entry_for_is_absent_rather_than_mapped():
    assert "&" not in CHAR_TO_BYTE
    assert "," not in CHAR_TO_BYTE


def test_the_table_agrees_with_the_fixtures_independent_transcription():
    """Every character in the table, encoded by the rule rather than the table."""
    assert {ch: fixture.encode_char(ch) for ch in CHAR_TO_BYTE} == CHAR_TO_BYTE


def test_zero_is_a_space_and_not_a_terminator():
    assert CHAR_TO_BYTE[" "] == 0x00
    assert BYTE_TO_CHAR[0x00] == " "


def test_decoding_strips_the_trailing_space_padding():
    assert decode_iss_name(fixture.encode_name("KLINSMAN")) == "KLINSMAN"
    assert decode_iss_name(fixture.encode_name("PELE")) == "PELE"


def test_decoding_drops_a_byte_the_font_table_does_not_name():
    """No placeholder, so an unmapped byte closes the gap around it.

    That is upstream's behaviour and it is kept: `RomSlot.current_name` is shown
    to a user, and a `?` per unnamed glyph would fill a real ROM's names with
    them.
    """
    assert decode_iss_name(bytes([CHAR_TO_BYTE["A"], 0xFF, CHAR_TO_BYTE["B"]])) == "AB"


def test_decoding_an_all_unmapped_field_gives_an_empty_string():
    assert decode_iss_name(bytes([0xFF] * 8)) == ""


def test_a_one_megabyte_image_passes_the_floor(rom):
    assert ISSRomReader(str(rom)).validate_rom() is True


def test_a_missing_file_fails_the_floor(tmp_path):
    assert ISSRomReader(str(tmp_path / "nope.sfc")).validate_rom() is False


def test_a_file_one_byte_under_the_floor_fails_it(tmp_path):
    path = tmp_path / "short.sfc"
    path.write_bytes(bytes(fixture.build_iss_rom())[: fixture.ROM_SIZE - 1])
    assert ISSRomReader(str(path)).validate_rom() is False


def test_the_floor_is_a_megabyte_and_the_writer_needs_far_less():
    """The two numbers whose distance is why the floor is a heuristic.

    A guard three and a half times larger than the data it protects is not a
    statement about whether the data fits.
    """
    assert MIN_PATCHABLE_SIZE == 296140
    assert fixture.ROM_SIZE == 1048576


def test_validate_rom_sets_no_header_offset_for_a_headerless_image(rom):
    reader = ISSRomReader(str(rom))
    reader.validate_rom()
    assert reader.header_offset == 0


def test_validate_rom_sets_the_copier_header_offset_for_a_headered_image(headered_rom):
    reader = ISSRomReader(str(headered_rom))
    reader.validate_rom()
    assert reader.header_offset == 512


def test_the_header_offset_is_zero_before_validate_rom_runs(rom):
    """The side effect is the only thing that sets it, which is why both entry
    points in `patcher.py` call `validate_rom` first and say so."""
    assert ISSRomReader(str(rom)).header_offset == 0


def test_a_two_megabyte_image_is_headerless(tmp_path):
    path = fixture.write_iss_rom(tmp_path / "big.sfc", size=0x200000)
    reader = ISSRomReader(str(path))
    reader.validate_rom()
    assert reader.header_offset == 0


def test_a_size_that_is_512_past_a_kilobyte_boundary_is_taken_as_headered(tmp_path):
    """The `size % 1024 == 512` fallback, for a size in neither known pair."""
    path = tmp_path / "odd.sfc"
    path.write_bytes(bytes(512) + bytes(fixture.build_iss_rom(size=0x180000)))
    assert os.path.getsize(path) % 1024 == 512
    reader = ISSRomReader(str(path))
    reader.validate_rom()
    assert reader.header_offset == 512


def test_a_size_on_a_kilobyte_boundary_is_taken_as_headerless(tmp_path):
    """The other side of the same fallback."""
    path = tmp_path / "even.sfc"
    path.write_bytes(bytes(fixture.build_iss_rom(size=0x180000)))
    assert os.path.getsize(path) % 1024 == 0
    reader = ISSRomReader(str(path))
    reader.validate_rom()
    assert reader.header_offset == 0


def test_the_arithmetic_minimum_is_the_writers_highest_write():
    """Derived in `rom_writer` from its own constants; retranscribed in the fixture."""
    assert MIN_PATCHABLE_SIZE == fixture.SIZE_ARITHMETIC_MINIMUM


def test_an_image_at_exactly_the_arithmetic_minimum_fits(tmp_path):
    path = fixture.write_iss_rom(tmp_path / "min.sfc", size=MIN_PATCHABLE_SIZE)
    assert ISSRomReader(str(path)).data_fits() is True


def test_an_image_one_byte_under_the_arithmetic_minimum_does_not_fit(tmp_path):
    path = tmp_path / "under.sfc"
    path.write_bytes(bytes(fixture.build_iss_rom(size=MIN_PATCHABLE_SIZE))[:-1])
    assert ISSRomReader(str(path)).data_fits() is False


#: A body one kilobyte-boundary below `MIN_PATCHABLE_SIZE`, and the next one up.
#: Both are multiples of 1024, so both become `% 1024 == 512` once 512 bytes of
#: copier header go in front and `_detect_header` answers True for each.
_SHORT_BODY = 289 * 1024
_LONG_BODY = 290 * 1024


def test_the_copier_header_does_not_count_towards_the_arithmetic_minimum(tmp_path):
    """A file 296 448 bytes long does not fit, because 512 of them are header."""
    assert _SHORT_BODY < MIN_PATCHABLE_SIZE
    path = tmp_path / "headered-short.sfc"
    path.write_bytes(bytes(512) + bytes(fixture.build_iss_rom(size=_SHORT_BODY)))
    assert os.path.getsize(path) == _SHORT_BODY + 512
    assert ISSRomReader(str(path)).data_fits() is False


def test_a_headered_image_whose_body_clears_the_minimum_fits(tmp_path):
    """The other side of the same subtraction, one kilobyte up."""
    assert _LONG_BODY >= MIN_PATCHABLE_SIZE
    path = tmp_path / "headered-long.sfc"
    path.write_bytes(bytes(512) + bytes(fixture.build_iss_rom(size=_LONG_BODY)))
    assert ISSRomReader(str(path)).data_fits() is True


def test_a_headerless_image_of_the_short_bodys_size_does_not_fit(tmp_path):
    """Without the header the same body is still short, so the pair above is
    about the subtraction and not about the size alone."""
    path = tmp_path / "headerless-short.sfc"
    path.write_bytes(bytes(fixture.build_iss_rom(size=_SHORT_BODY)))
    assert ISSRomReader(str(path)).data_fits() is False


def test_data_fits_answers_the_same_before_and_after_validate_rom(rom):
    """Self-contained: it re-derives the header offset rather than reading the
    field `validate_rom` sets, so no call order can change its answer."""
    fresh = ISSRomReader(str(rom))
    before = fresh.data_fits()
    fresh.validate_rom()
    assert before is fresh.data_fits()


def test_a_missing_file_does_not_fit(tmp_path):
    assert ISSRomReader(str(tmp_path / "nope.sfc")).data_fits() is False


def test_the_arithmetic_minimum_is_far_below_the_floor():
    """The gap the two guards deliberately leave. `patch` accepts this band and
    `analyze` does not; `test_patcher.py` pins that at the entry points."""
    assert MIN_PATCHABLE_SIZE < fixture.ROM_SIZE


def test_the_synthetic_image_passes_the_signature(rom):
    assert ISSRomReader(str(rom)).signature_ok() is True


def test_a_headered_synthetic_image_passes_the_signature(headered_rom):
    assert ISSRomReader(str(headered_rom)).signature_ok() is True


def test_pseudo_random_bytes_of_the_right_size_fail_the_signature(tmp_path):
    """The claim registration depends on. A megabyte of filler is the shape of
    every unrelated ROM in a user's library as far as the size floor is
    concerned."""
    path = tmp_path / "garbage.sfc"
    path.write_bytes(bytes(fixture._filler(fixture.ROM_SIZE)))
    assert ISSRomReader(str(path)).signature_ok() is False


def test_a_zero_filled_image_fails_the_signature(tmp_path):
    path = tmp_path / "zeros.sfc"
    path.write_bytes(bytes(fixture.ROM_SIZE))
    assert ISSRomReader(str(path)).signature_ok() is False


def test_an_all_ones_image_fails_the_signature(tmp_path):
    """0xFF clears both 0x80 bias tests, so this is the case a bias-only check
    would let through: the blob lengths and the budget are what refuse it."""
    path = tmp_path / "ones.sfc"
    path.write_bytes(b"\xff" * fixture.ROM_SIZE)
    assert ISSRomReader(str(path)).signature_ok() is False


def test_a_name_text_pointer_below_the_bias_fails_the_signature(tmp_path):
    """One byte of one of 27 entries. `_decode_p40000` would answer a negative
    file offset for it, and `write_team_name_texts` seeks to it."""
    path = fixture.write_iss_rom(tmp_path / "bias.sfc", break_name_text_bias=True)
    assert ISSRomReader(str(path)).signature_ok() is False


def test_a_name_tile_blob_shorter_than_its_own_header_fails_the_signature(tmp_path):
    """A Konami stream is at least its own two-byte length word."""
    path = fixture.write_iss_rom(tmp_path / "blob.sfc", break_name_tile_bounds=True)
    assert ISSRomReader(str(path)).signature_ok() is False


def test_a_name_tile_blob_running_past_the_end_fails_the_signature(tmp_path):
    """Only reachable near the arithmetic minimum: a P48000 pointer cannot reach
    past 0x4FFFF, so on a 1 MB image no declared length can overrun."""
    path = fixture.write_iss_rom(
        tmp_path / "blob.sfc",
        size=MIN_PATCHABLE_SIZE,
        name_tile_declared_size=0xFFFF,
    )
    assert ISSRomReader(str(path)).signature_ok() is False


def test_the_same_minimum_sized_image_passes_without_that_one_change(tmp_path):
    path = fixture.write_iss_rom(tmp_path / "ok.sfc", size=MIN_PATCHABLE_SIZE)
    assert ISSRomReader(str(path)).signature_ok() is True


def test_a_description_pointer_outside_bank_two_fails_the_signature(tmp_path):
    path = fixture.write_iss_rom(tmp_path / "desc.sfc", break_desc_bank=True)
    assert ISSRomReader(str(path)).signature_ok() is False


def test_the_three_break_flags_each_break_exactly_one_condition(tmp_path):
    """Each `break_*` image is otherwise the image that passes, so each failure
    above names its own condition and not some incidental damage."""
    for flag in ("break_name_text_bias", "break_name_tile_bounds", "break_desc_bank"):
        broken = bytes(fixture.build_iss_rom(**{flag: True}))
        clean = bytes(fixture.build_iss_rom())
        differing = [i for i in range(len(clean)) if clean[i] != broken[i]]
        assert len(differing) <= 2, flag


def test_a_name_text_table_pointing_at_the_ceiling_fails_the_signature(tmp_path):
    """`write_team_name_texts`' budget is `0x44478 - min(addrs)`, and a
    non-positive one is not a truncation but a write past the ceiling."""
    path = fixture.write_iss_rom(
        tmp_path / "ceiling.sfc", name_text_base=fixture.MAX_NAME_TEXT_ADDR
    )
    assert ISSRomReader(str(path)).signature_ok() is False


def test_a_name_text_table_just_below_the_ceiling_passes_the_signature(tmp_path):
    """The other side of the same boundary, so the test above pins an edge and
    not merely 'anything unusual is refused'."""
    path = fixture.write_iss_rom(
        tmp_path / "ceiling.sfc", name_text_base=fixture.MAX_NAME_TEXT_ADDR - 1
    )
    assert ISSRomReader(str(path)).signature_ok() is True


def test_an_image_too_short_for_the_writer_fails_the_signature(tmp_path):
    path = tmp_path / "short.sfc"
    path.write_bytes(bytes(fixture.build_iss_rom(size=MIN_PATCHABLE_SIZE))[:-1])
    assert ISSRomReader(str(path)).signature_ok() is False


def test_an_unreadable_file_raises_rather_than_answering_not_this_game(tmp_path):
    """`OSError` is deliberately not caught here. "I cannot read this" and "this
    is a different game" are the two answers `Patcher.analyze_rom` promises to
    keep apart, and swallowing the first would report it as the second.
    `ISSPatcher.analyze_rom` wraps this call in `errors.as_rom_error`."""
    path = fixture.write_iss_rom(tmp_path / "iss.sfc")
    path.chmod(0o000)
    try:
        if os.access(path, os.R_OK):  # pragma: no cover - root ignores the mode
            pytest.skip("running as a user the mode does not restrict")
        with pytest.raises(OSError):
            ISSRomReader(str(path)).signature_ok()
    finally:
        path.chmod(0o600)


def test_the_signature_reads_pointer_tables_the_reader_transcribes_itself(rom):
    """The two copies of each offset, held against each other.

    `rom_reader` retranscribes the three table offsets rather than importing
    them from `rom_writer`, so that moving one moves only one. This is what
    makes the duplication a check instead of a hazard.
    """
    from retro_roster_patcher.games.iss_snes import rom_reader, rom_writer

    assert rom_reader._OFS_TEAM_NAME_TEXT_PTRS == rom_writer._OFS_TEAM_NAME_TEXT_PTRS
    assert rom_reader._OFS_NAME_TILES_PTRS == rom_writer._OFS_NAME_TILES_PTRS
    assert rom_reader._OFS_DESC_PTRS == rom_writer._OFS_DESC_PTRS
    assert rom_reader._MAX_NAME_TEXT_ADDR == rom_writer._MAX_NAME_TEXT_ADDR


def test_the_reader_reads_all_405_names(rom):
    names = ISSRomReader(str(rom)).read_player_names()
    assert len(names) == TOTAL_TEAMS
    assert [len(team) for team in names] == [15] * TOTAL_TEAMS


def test_every_name_encodes_its_own_storage_coordinates(rom):
    names = ISSRomReader(str(rom)).read_player_names()
    expected = [
        [fixture.player_name(team, slot) for slot in range(15)] for team in range(TOTAL_TEAMS)
    ]
    assert names == expected


def test_the_names_are_read_through_the_copier_header(headered_rom):
    reader = ISSRomReader(str(headered_rom))
    reader.validate_rom()
    assert reader.read_player_names()[0][0] == fixture.player_name(0, 0)


def test_without_validate_rom_a_headered_image_reads_the_wrong_bytes(headered_rom):
    """The side effect, made visible. `read_player_names` uses
    `self.header_offset`, so on a headered image it is 512 bytes out until
    `validate_rom` has run -- which is why `get_rom_info` calls it first."""
    assert ISSRomReader(str(headered_rom)).read_player_names()[0][0] != fixture.player_name(0, 0)


def test_slot_five_reports_scotlands_players_and_not_slot_fives(rom):
    """The translation, at the reader end. Storage index 24 holds them."""
    slots = ISSRomReader(str(rom)).read_team_slots()
    assert slots[5].name == "Scotland"
    assert slots[5].first_player == fixture.player_name(24, 0)


def test_slot_six_reports_wales_players_from_storage_index_five(rom):
    slots = ISSRomReader(str(rom)).read_team_slots()
    assert slots[6].name == "Wales"
    assert slots[6].first_player == fixture.player_name(5, 0)


@pytest.mark.parametrize("slot", range(TOTAL_TEAMS))
def test_every_slot_reports_the_first_player_of_its_own_storage_block(rom, slot):
    slots = ISSRomReader(str(rom)).read_team_slots()
    assert slots[slot].first_player == fixture.player_name(fixture.name_storage_index(slot), 0)


def test_the_slots_carry_the_enum_order_names(rom):
    slots = ISSRomReader(str(rom)).read_team_slots()
    assert [slot.name for slot in slots] == TEAM_ENUM_ORDER


def test_the_slots_are_indexed_in_order(rom):
    slots = ISSRomReader(str(rom)).read_team_slots()
    assert [slot.index for slot in slots] == list(range(TOTAL_TEAMS))


def test_no_two_slots_report_the_same_first_player(rom):
    """A reader that ignored the team index would give 27 identical strings."""
    slots = ISSRomReader(str(rom)).read_team_slots()
    assert len({slot.first_player for slot in slots}) == TOTAL_TEAMS


def test_get_rom_info_reports_a_valid_image(rom):
    info = ISSRomReader(str(rom)).get_rom_info()
    assert info.is_valid is True
    assert info.size == fixture.ROM_SIZE
    assert len(info.team_slots) == TOTAL_TEAMS


def test_get_rom_info_reports_the_copier_header(headered_rom):
    info = ISSRomReader(str(headered_rom)).get_rom_info()
    assert info.has_header is True
    assert info.size == fixture.ROM_SIZE + 512


def test_get_rom_info_reports_no_header_on_a_headerless_image(rom):
    assert ISSRomReader(str(rom)).get_rom_info().has_header is False


def test_get_rom_info_reads_no_slots_from_an_image_that_fails_the_signature(tmp_path):
    path = tmp_path / "garbage.sfc"
    path.write_bytes(bytes(fixture._filler(fixture.ROM_SIZE)))
    info = ISSRomReader(str(path)).get_rom_info()
    assert info.is_valid is False
    assert info.team_slots == []


def test_get_rom_info_reports_the_real_size_of_an_invalid_image(tmp_path):
    """Size is not zeroed by invalidity: it is what the file is, and the CLI
    prints it next to `is_valid: false`."""
    path = tmp_path / "garbage.sfc"
    path.write_bytes(bytes(fixture._filler(fixture.ROM_SIZE)))
    assert ISSRomReader(str(path)).get_rom_info().size == fixture.ROM_SIZE


def test_get_rom_info_reports_size_zero_for_a_missing_file(tmp_path):
    info = ISSRomReader(str(tmp_path / "nope.sfc")).get_rom_info()
    assert info.size == 0
    assert info.is_valid is False


def test_get_rom_info_refuses_an_image_in_the_band_between_the_two_guards(tmp_path):
    """Large enough for the writer, too small for the floor. `analyze` refuses
    it; `test_patcher.py` shows `patch` does not."""
    path = fixture.write_iss_rom(tmp_path / "band.sfc", size=MIN_PATCHABLE_SIZE)
    assert ISSRomReader(str(path)).data_fits() is True
    assert ISSRomReader(str(path)).get_rom_info().is_valid is False
