"""`MVPPSPRomReader` against fabricated MVP Baseball PSP images.

Every image here is built by `tests/fixtures/synthetic_mvp_iso.py`. No real ISO
may enter this repository, so nothing below has ever been run against a retail
disc and the column layouts are the fixture's invention.

The reader has four jobs and they fail differently, so they are tested apart:

  * `load`, a seek to a hardcoded offset -- **no ISO 9660 walk at all**, which
    is what makes this game unlike the two NHL disc games;
  * `validate` and `validate_deep`, the shallow header check and the heuristic
    built on top of it;
  * `decompress_section`, whose first section has a different flag byte from
    every other;
  * `parse_csv_section`, the record/header discrimination.

Every read-back goes through the fixture's own `parse_table` and
`decompress_section_at`, which are independent reimplementations, so a bug
shared between the reader and the writer that produced the bytes cannot satisfy
an assertion.
"""

from __future__ import annotations

import os

import pytest

from retro_roster_patcher.formats.ea_tdb import EaTdbError, refpack_compress
from retro_roster_patcher.games.mvp_psp import models as mvp_models
from retro_roster_patcher.games.mvp_psp import rom_reader as mvp_rom_reader
from retro_roster_patcher.games.mvp_psp.models import (
    ATTRIB_FIRST_NAME,
    ATTRIB_SECTION_OFFSET,
    DATABASE_BIG_SIZE,
    SECTION_COUNT,
    TEAM_COUNT,
    TEAM_HASHES,
    MVPRomInfo,
    MVPTeamSlot,
    database_big_extent,
)
from retro_roster_patcher.games.mvp_psp.rom_reader import (
    COMPACT_SECTION_FLAG,
    HASH_ID_DIGITS,
    REFPACK_MAGIC,
    VALID_FIRST_BYTES,
    MVPPSPRomReader,
    _looks_like_record_id,
    _parse_record_body,
)
from tests.fixtures import synthetic_mvp_iso as fixture


@pytest.fixture(autouse=True)
def small_layout(monkeypatch):
    """Put `database.big` 80 KB in rather than 685 MB in, for every test here."""
    fixture.use_small_layout(monkeypatch)


def write_iso(tmp_path, spec=None, *, name="game.iso", lba=fixture.SMALL_LBA):
    path = tmp_path / name
    path.write_bytes(fixture.build_iso(fixture.build_database_big(spec), lba=lba))
    return path


def loaded(tmp_path, spec=None, **kwargs):
    reader = MVPPSPRomReader(str(write_iso(tmp_path, spec, **kwargs)))
    reader.load()
    return reader


def parsed(tmp_path, spec=None):
    reader = loaded(tmp_path, spec)
    reader.decompress_all()
    reader.parse_all()
    return reader


# -- constants -------------------------------------------------------------


def test_the_refpack_magic_is_the_two_header_bytes():
    assert REFPACK_MAGIC == b"\x10\xfb"


def test_the_first_section_uses_a_different_flag_byte():
    assert COMPACT_SECTION_FLAG == 0xC0


def test_validate_accepts_exactly_the_two_first_bytes():
    assert sorted(VALID_FIRST_BYTES) == [0x10, 0xC0]


def test_the_second_section_starts_at_three_hundred_and_twenty_four():
    # `validate` looks for the RefPack magic here, and the constant is derived
    # from `SECTION_MAP` rather than written out a second time.
    assert ATTRIB_SECTION_OFFSET == 324


def test_a_record_id_may_only_hold_lowercase_hex():
    assert HASH_ID_DIGITS == frozenset("0123456789abcdef")


# -- _looks_like_record_id -------------------------------------------------


def test_a_nine_digit_hex_string_is_a_record_id():
    assert _looks_like_record_id("00b87d5f5") is True


def test_a_five_digit_hex_string_is_a_record_id():
    # The source's floor is five, not nine. A shorter id is not something this
    # repository can rule out, and rejecting one would drop a whole record.
    assert _looks_like_record_id("abcde") is True


def test_a_four_digit_hex_string_is_not_a_record_id():
    assert _looks_like_record_id("abcd") is False


def test_an_empty_string_is_not_a_record_id():
    assert _looks_like_record_id("") is False


def test_an_uppercase_hex_string_is_not_a_record_id():
    assert _looks_like_record_id("00B87D5F5") is False


def test_a_string_with_a_space_is_not_a_record_id():
    assert _looks_like_record_id("00b8 7d5f") is False


def test_a_column_name_is_not_a_record_id():
    assert _looks_like_record_id("firstname") is False


# -- _parse_record_body ----------------------------------------------------


def test_a_column_is_split_on_its_first_space():
    assert _parse_record_body(["22 61"]) == {22: "61"}


def test_a_value_containing_spaces_survives():
    assert _parse_record_body(["0 Jean Luc Picard"]) == {0: "Jean Luc Picard"}


def test_a_column_with_an_empty_value_is_kept():
    # `"0 "` is column 0 holding the empty string, which is different from
    # column 0 being absent, and the value is not stripped.
    assert _parse_record_body(["0 "]) == {0: ""}


def test_a_value_keeps_its_trailing_space():
    assert _parse_record_body(["0 Bo  "]) == {0: "Bo  "}


def test_a_part_with_no_space_is_dropped():
    assert _parse_record_body(["0x"]) == {}


def test_a_part_whose_column_is_not_a_number_is_dropped():
    assert _parse_record_body(["x y"]) == {}


def test_a_blank_part_is_dropped():
    assert _parse_record_body([""]) == {}


def test_a_later_column_wins_a_repeat():
    assert _parse_record_body(["0 first", "0 second"]) == {0: "second"}


def test_several_columns_are_kept_together():
    assert _parse_record_body(["0 Ichiro", "1 Suzuki", "22 61"]) == {
        0: "Ichiro",
        1: "Suzuki",
        22: "61",
    }


# -- load ------------------------------------------------------------------


def test_loading_a_missing_file_answers_false(tmp_path):
    assert MVPPSPRomReader(str(tmp_path / "gone.iso")).load() is False


def test_loading_a_directory_answers_false(tmp_path):
    (tmp_path / "dir.iso").mkdir()
    assert MVPPSPRomReader(str(tmp_path / "dir.iso")).load() is False


def test_loading_an_empty_file_answers_false(tmp_path):
    path = tmp_path / "empty.iso"
    path.write_bytes(b"")
    assert MVPPSPRomReader(str(path)).load() is False


def test_loading_a_file_one_byte_short_of_the_extent_answers_false(tmp_path):
    _, end = database_big_extent()
    path = tmp_path / "short.iso"
    path.write_bytes(fixture.build_iso(fixture.build_database_big())[: end - 1])
    assert MVPPSPRomReader(str(path)).load() is False


def test_loading_a_file_that_ends_exactly_at_the_extent_answers_true(tmp_path):
    # The boundary from the other side, so the check is `<` and not `<=`.
    _, end = database_big_extent()
    path = tmp_path / "exact.iso"
    path.write_bytes(fixture.build_iso(fixture.build_database_big())[:end])
    assert MVPPSPRomReader(str(path)).load() is True


def test_loading_a_real_image_answers_true(tmp_path):
    assert MVPPSPRomReader(str(write_iso(tmp_path))).load() is True


def test_the_loaded_blob_is_the_declared_size(tmp_path):
    assert len(loaded(tmp_path).database_big) == DATABASE_BIG_SIZE


def test_the_loaded_blob_is_the_bytes_the_fixture_put_at_the_extent(tmp_path):
    # Read back with the fixture's own arithmetic, not the reader's.
    path = write_iso(tmp_path)
    expected = fixture.read_database_big(path.read_bytes(), lba=fixture.SMALL_LBA)
    assert loaded(tmp_path).database_big == expected


def test_the_recorded_offset_is_where_the_blob_starts(tmp_path):
    start, _ = database_big_extent()
    assert loaded(tmp_path).database_big_offset == start


def test_an_unloaded_reader_holds_no_blob(tmp_path):
    assert MVPPSPRomReader(str(write_iso(tmp_path))).database_big is None


def test_a_file_that_cannot_be_read_answers_false(tmp_path):
    path = write_iso(tmp_path)
    os.chmod(path, 0o000)
    try:
        answer = MVPPSPRomReader(str(path)).load()
    finally:
        os.chmod(path, 0o644)
    assert answer is False


def test_a_path_the_operating_system_cannot_accept_answers_false(tmp_path):
    # `os.path.exists` is not merely a fast path for `open`. Every other way of
    # not having a file raises `FileNotFoundError`, which is an `OSError` and
    # which the handler below turns into the same False -- so deleting the
    # `exists` check survived the suite. A path holding a NUL byte is the case
    # that separates them: `os.path.exists` answers False for it and `open`
    # raises `ValueError`, which is not an `OSError` and would travel out of
    # `load` past the handler, out of `analyze_rom`, and out of the CLI.
    assert MVPPSPRomReader("no\x00such.iso").load() is False


def test_a_failure_that_is_not_an_os_error_travels(tmp_path, monkeypatch):
    """DELIBERATE DIVERGENCE, pinned: `except OSError`, not `except Exception`.

    The source caught everything here, so a bug inside this method reached the
    user as "this is not MVP Baseball". The three tests above cover the
    `OSError` side -- a missing file, a directory, an unreadable file -- and all
    three answer False either way, which is why the narrowing survived until
    something raised through it. `f.seek` refuses a string with a `TypeError`,
    and a `TypeError` here is this module's bug and not the user's disc.
    """
    path = write_iso(tmp_path)
    monkeypatch.setattr(mvp_rom_reader, "database_big_extent", lambda: ("not an offset", 0))
    with pytest.raises(TypeError):
        MVPPSPRomReader(str(path)).load()


def test_a_load_at_a_different_lba_reads_a_different_place(tmp_path, monkeypatch):
    # The extent is read at call time, so moving the LBA moves the read.
    path = tmp_path / "moved.iso"
    path.write_bytes(fixture.build_iso(fixture.build_database_big(), lba=60))
    monkeypatch.setattr(mvp_models, "DATABASE_BIG_LBA", 60)
    reader = MVPPSPRomReader(str(path))
    reader.load()
    assert reader.database_big_offset == 60 * fixture.SECTOR_SIZE


# -- validate --------------------------------------------------------------


def test_an_unloaded_reader_does_not_validate(tmp_path):
    assert MVPPSPRomReader(str(write_iso(tmp_path))).validate() is False


def test_a_disc_with_the_compact_flag_validates(tmp_path):
    assert loaded(tmp_path, fixture.DiscSpec(compact_flag_c0=True)).validate() is True


def test_a_disc_with_an_ordinary_first_section_validates(tmp_path):
    # `validate` accepts both first bytes and `decompress_section` handles both.
    assert loaded(tmp_path, fixture.DiscSpec(compact_flag_c0=False)).validate() is True


def test_a_disc_whose_first_byte_is_neither_does_not_validate(tmp_path):
    reader = loaded(tmp_path)
    reader.database_big = b"\x11" + reader.database_big[1:]
    assert reader.validate() is False


def test_a_disc_missing_the_second_sections_first_magic_byte_does_not_validate(tmp_path):
    reader = loaded(tmp_path)
    blob = bytearray(reader.database_big)
    blob[ATTRIB_SECTION_OFFSET] = 0x11
    reader.database_big = bytes(blob)
    assert reader.validate() is False


def test_a_disc_missing_the_second_sections_second_magic_byte_does_not_validate(tmp_path):
    reader = loaded(tmp_path)
    blob = bytearray(reader.database_big)
    blob[ATTRIB_SECTION_OFFSET + 1] = 0x00
    reader.database_big = bytes(blob)
    assert reader.validate() is False


def test_the_magic_is_checked_at_324_and_not_at_325(tmp_path):
    # A one-byte shift of the whole check would still find `FB` at 325 if it
    # looked at 325 and 326, so the offset is pinned from both sides.
    reader = loaded(tmp_path)
    blob = bytearray(reader.database_big)
    blob[ATTRIB_SECTION_OFFSET + 2] = 0x10
    reader.database_big = bytes(blob)
    assert reader.validate() is True


# -- validate_deep ---------------------------------------------------------


def test_a_disc_carrying_mvp_team_ids_passes_the_deep_check(tmp_path):
    assert loaded(tmp_path).validate_deep() is True


def test_a_disc_whose_team_table_holds_other_ids_fails_the_deep_check(tmp_path):
    # The whole point of the heuristic: another EA disc of the same era has
    # RefPack streams at sector boundaries too, and does not have these ids.
    reader = loaded(tmp_path, fixture.DiscSpec(team_records=False))
    assert reader.validate_deep() is False


def test_a_disc_whose_team_table_holds_other_ids_still_passes_the_shallow_check(tmp_path):
    # Which is what makes the deep check add something rather than repeat it.
    assert loaded(tmp_path, fixture.DiscSpec(team_records=False)).validate() is True


def test_one_matching_team_id_is_enough(tmp_path):
    # Deliberately not thirty-of-thirty: a disc a previous patcher touched, or
    # a regional variant, should still be recognised.
    reader = loaded(tmp_path, fixture.DiscSpec(team_records=False))
    reader.decompress_all()
    reader.parse_all()
    reader.records["team"][TEAM_HASHES["ANA"]] = {0: "Anaheim Angels"}
    assert reader.validate_deep() is True


def test_the_deep_check_fails_when_the_shallow_one_does(tmp_path):
    reader = loaded(tmp_path)
    reader.database_big = b"\x11" + reader.database_big[1:]
    assert reader.validate_deep() is False


def test_the_deep_check_decompresses_every_section(tmp_path):
    reader = loaded(tmp_path)
    reader.validate_deep()
    assert len(reader.sections) == SECTION_COUNT


def test_the_shallow_check_decompresses_nothing(tmp_path):
    # Which is why one of the two guards `analyze_rom` and the other does not
    # need to: the cheap one costs nothing.
    reader = loaded(tmp_path)
    reader.validate()
    assert reader.sections == {}


# -- decompress_section ----------------------------------------------------


def test_the_first_section_decompresses_through_the_flag_fixup(tmp_path):
    reader = loaded(tmp_path, fixture.DiscSpec(compact_flag_c0=True))
    expected = fixture.decompress_section_at(reader.database_big, "attrib_compact")
    assert reader.decompress_section(0) == expected


def test_the_first_section_decompresses_without_the_fixup_too(tmp_path):
    reader = loaded(tmp_path, fixture.DiscSpec(compact_flag_c0=False))
    expected = fixture.decompress_section_at(reader.database_big, "attrib_compact")
    assert reader.decompress_section(0) == expected


def test_the_two_first_section_flags_decompress_to_the_same_bytes(tmp_path):
    # The fixup is only correct if it is: it rewrites two bytes and passes the
    # rest of the stream through.
    with_flag = loaded(tmp_path, fixture.DiscSpec(compact_flag_c0=True), name="a.iso")
    without = loaded(tmp_path, fixture.DiscSpec(compact_flag_c0=False), name="b.iso")
    assert with_flag.decompress_section(0) == without.decompress_section(0)


def test_a_later_section_decompresses_to_what_the_fixture_put_there(tmp_path):
    reader = loaded(tmp_path)
    offset, _ = mvp_models.SECTION_ALLOCATIONS["attrib"]
    expected = fixture.decompress_section_at(reader.database_big, "attrib")
    assert reader.decompress_section(offset) == expected


def test_a_section_whose_bytes_are_not_refpack_answers_none(tmp_path):
    reader = loaded(tmp_path)
    offset, _ = mvp_models.SECTION_ALLOCATIONS["attrib"]
    blob = bytearray(reader.database_big)
    blob[offset] = 0x00
    reader.database_big = bytes(blob)
    assert reader.decompress_section(offset) is None


def test_an_offset_past_the_end_answers_none(tmp_path):
    assert loaded(tmp_path).decompress_section(DATABASE_BIG_SIZE) is None


def test_an_offset_one_byte_before_the_end_answers_none(tmp_path):
    # One byte is not a header, and the guard is what stops an IndexError.
    assert loaded(tmp_path).decompress_section(DATABASE_BIG_SIZE - 1) is None


def test_a_first_section_flagged_with_neither_byte_is_refused(tmp_path):
    # The fixup is `offset == 0 AND the flag is 0xC0`, and the second half of
    # that is what this pins. Dropping it survived every test here, because for
    # a section already flagged 0x10 the rewrite puts back the two bytes it
    # replaced and is a no-op -- so the only way to see the guard is a first
    # section flagged with neither byte, which must be refused rather than
    # forced into a header it does not have.
    reader = loaded(tmp_path)
    blob = bytearray(reader.database_big)
    blob[0] = 0x42
    reader.database_big = bytes(blob)
    assert reader.decompress_section(0) is None


def test_an_unloaded_reader_decompresses_nothing(tmp_path):
    assert MVPPSPRomReader(str(write_iso(tmp_path))).decompress_section(0) is None


def test_the_compact_flag_fixup_only_applies_at_offset_zero(tmp_path):
    # A 0xC0 byte anywhere else is not a RefPack header and must be refused
    # rather than fixed up.
    reader = loaded(tmp_path)
    offset, _ = mvp_models.SECTION_ALLOCATIONS["attrib"]
    blob = bytearray(reader.database_big)
    blob[offset] = COMPACT_SECTION_FLAG
    reader.database_big = bytes(blob)
    assert reader.decompress_section(offset) is None


def test_a_truncated_refpack_stream_raises_from_the_format_layer(tmp_path):
    reader = loaded(tmp_path)
    reader.database_big = REFPACK_MAGIC + b"\x00\x00"
    with pytest.raises(EaTdbError):
        reader.decompress_section(0)


def test_a_section_of_exactly_two_bytes_reaches_the_decompressor(tmp_path):
    # The `len(raw) < 2` floor is about what `raw[:2]` can be compared against,
    # not about what `refpack_decompress` can read. Two bytes clear the floor,
    # match the magic, and arrive at a decompressor that needs five, so this is
    # the smallest input that raises rather than answering None. Raising the
    # floor to three would answer None here instead, and nothing said so.
    reader = loaded(tmp_path)
    reader.database_big = REFPACK_MAGIC
    with pytest.raises(EaTdbError):
        reader.decompress_section(0)


# -- decompress_all --------------------------------------------------------


def test_every_section_decompresses(tmp_path):
    reader = loaded(tmp_path)
    reader.decompress_all()
    assert sorted(reader.sections) == sorted(name for _, name in mvp_models.SECTION_MAP)


def test_a_section_that_is_not_refpack_is_left_out_and_the_rest_are_read(tmp_path):
    reader = loaded(tmp_path)
    offset, _ = mvp_models.SECTION_ALLOCATIONS["batstat"]
    blob = bytearray(reader.database_big)
    blob[offset] = 0x00
    reader.database_big = bytes(blob)
    reader.decompress_all()
    assert sorted(reader.sections) == sorted(
        name for _, name in mvp_models.SECTION_MAP if name != "batstat"
    )


def test_a_section_decompressing_to_nothing_is_left_out(tmp_path):
    # `if data:` and not `if data is not None:`, which is the source's, and the
    # difference is a table that compressed an empty string.
    reader = loaded(tmp_path)
    offset, _ = mvp_models.SECTION_ALLOCATIONS["manager"]
    empty = refpack_compress(b"")
    blob = bytearray(reader.database_big)
    blob[offset : offset + len(empty)] = empty
    reader.database_big = bytes(blob)
    reader.decompress_all()
    assert "manager" not in reader.sections


def test_a_section_decompressing_to_nothing_leaves_the_others(tmp_path):
    reader = loaded(tmp_path)
    offset, _ = mvp_models.SECTION_ALLOCATIONS["manager"]
    empty = refpack_compress(b"")
    blob = bytearray(reader.database_big)
    blob[offset : offset + len(empty)] = empty
    reader.database_big = bytes(blob)
    reader.decompress_all()
    assert len(reader.sections) == SECTION_COUNT - 1


def test_the_shallow_get_info_still_reads_the_slots(tmp_path):
    # `deep` chooses which check decides validity, not whether the sections are
    # read. The source had no `deep` and always read them.
    slots = loaded(tmp_path, fixture.DiscSpec(teams=2, players_per_team=3)).get_info().team_slots
    assert slots[1].player_count == 3


# -- parse_csv_section -----------------------------------------------------


def test_parsing_an_absent_section_answers_an_empty_table(tmp_path):
    assert MVPPSPRomReader("/nonexistent").parse_csv_section("attrib") == {}


def test_parsing_an_absent_section_leaves_an_empty_order(tmp_path):
    # The source left the key unset, so `record_order.get(name)` answered None.
    # Both are falsy at the one place it is read; an empty list is the one that
    # cannot raise `KeyError` on a caller that subscripts.
    reader = MVPPSPRomReader("/nonexistent")
    reader.parse_csv_section("attrib")
    assert reader.record_order["attrib"] == []


def test_the_header_line_is_not_a_record(tmp_path):
    reader = parsed(tmp_path, fixture.DiscSpec(teams=1, players_per_team=2))
    assert len(reader.records["attrib"]) == 2


def test_a_parsed_record_matches_the_fixtures_own_parse(tmp_path):
    reader = parsed(tmp_path, fixture.DiscSpec(teams=2, players_per_team=3))
    expected = fixture.parse_table(reader.sections["attrib"])
    assert reader.records["attrib"] == expected


def test_a_parsed_records_first_name_is_the_one_the_fixture_wrote(tmp_path):
    reader = parsed(tmp_path, fixture.DiscSpec(teams=3, players_per_team=4))
    record = reader.records["attrib"][fixture.player_id(2, 3)]
    assert record[ATTRIB_FIRST_NAME] == "Disc02"


def test_record_order_matches_the_fixtures_own_walk(tmp_path):
    reader = parsed(tmp_path, fixture.DiscSpec(teams=2, players_per_team=3))
    assert reader.record_order["attrib"] == fixture.parse_table_order(reader.sections["attrib"])


def test_record_order_is_not_the_sorted_order(tmp_path):
    # If it were, preserving it would be indistinguishable from sorting, and
    # the writer's order-preserving branch would be untested.
    reader = parsed(tmp_path, fixture.DiscSpec(teams=4, players_per_team=5))
    order = reader.record_order["attrib"]
    assert order != sorted(order)


def test_a_duplicate_id_appears_twice_in_the_order(tmp_path):
    # Inherited, and preserved: rebuilding the table keeps the disc's row count.
    spec = fixture.DiscSpec(teams=1, players_per_team=2, duplicate_first_player=True)
    reader = parsed(tmp_path, spec)
    first = fixture.player_id(0, 0)
    assert reader.record_order["attrib"].count(first) == 2


def test_a_duplicate_id_keeps_the_last_field_set(tmp_path):
    spec = fixture.DiscSpec(teams=1, players_per_team=2, duplicate_first_player=True)
    reader = parsed(tmp_path, spec)
    record = reader.records["attrib"][fixture.player_id(0, 0)]
    assert record == {ATTRIB_FIRST_NAME: "Duplicate"}


def test_a_record_with_no_parsable_columns_is_dropped():
    reader = MVPPSPRomReader("/nonexistent")
    reader.sections["t"] = b"a,b;\r\n00b87d5f5,;\r\n"
    assert reader.parse_csv_section("t") == {}


def test_a_line_with_no_comma_is_dropped():
    reader = MVPPSPRomReader("/nonexistent")
    reader.sections["t"] = b"a,b;\r\n00b87d5f5;\r\n"
    assert reader.parse_csv_section("t") == {}


def test_an_id_with_a_trailing_space_is_still_a_record():
    # `parts[0].strip()`, and the line has already been stripped, so the only
    # whitespace this can remove is *inside* the line -- between the id and the
    # first comma. Without the strip the id holds a space,
    # `_looks_like_record_id` refuses it and the whole record is dropped
    # silently, which is not a thing to do to a disc over one byte of padding.
    reader = MVPPSPRomReader("/nonexistent")
    reader.sections["t"] = b"a,b;\r\n00b87d5f5 ,0 x,;\r\n"
    assert reader.parse_csv_section("t") == {"00b87d5f5": {0: "x"}}


def test_an_all_decimal_id_does_not_become_a_column_of_its_own_record():
    # The body starts at `parts[1:]`, and this is the record that shows it.
    # An id is hex, so it is usually not a decimal integer and
    # `_parse_record_body` would throw it away on the `int()` -- which is why
    # feeding it the id survived mutation. An id of digits alone, with the
    # padding space the test above allows, parses: column 123456789 holding the
    # empty string, invented out of the record's own key and written straight
    # back into the table on the next rebuild.
    reader = MVPPSPRomReader("/nonexistent")
    reader.sections["t"] = b"a,b;\r\n123456789 ,0 x,;\r\n"
    assert reader.parse_csv_section("t") == {"123456789": {0: "x"}}


def test_a_record_terminated_without_a_crlf_is_still_read():
    reader = MVPPSPRomReader("/nonexistent")
    reader.sections["t"] = b"a,b;\r\n00b87d5f5,0 x,;"
    assert reader.parse_csv_section("t") == {"00b87d5f5": {0: "x"}}


# -- parse_all -------------------------------------------------------------


def test_parsing_covers_every_decompressed_section(tmp_path):
    reader = parsed(tmp_path)
    assert sorted(reader.records) == sorted(name for _, name in mvp_models.SECTION_MAP)


def test_the_roster_table_holds_one_row_per_disc_player(tmp_path):
    reader = parsed(tmp_path, fixture.DiscSpec(teams=6, players_per_team=7))
    assert len(reader.records["roster"]) == 42


def test_the_pitchattrib_table_holds_only_the_disc_pitchers(tmp_path):
    # Every third disc player, so 6 teams x 3 of 7 slots.
    reader = parsed(tmp_path, fixture.DiscSpec(teams=6, players_per_team=7))
    assert len(reader.records["pitchattrib"]) == 18


def test_no_record_id_equals_its_position_in_the_table(tmp_path):
    # The fixture's whole id scheme exists for this: an index bug that returned
    # a position where an id belongs would otherwise be invisible.
    reader = parsed(tmp_path, fixture.DiscSpec(teams=3, players_per_team=4))
    order = reader.record_order["attrib"]
    assert [i for i, rid in enumerate(order) if int(rid, 16) == i] == []


# -- get_info --------------------------------------------------------------


def test_an_unloaded_reader_reports_a_zero_sized_rom(tmp_path):
    info = MVPPSPRomReader(str(write_iso(tmp_path))).get_info()
    assert info.size == 0


def test_an_unloaded_reader_reports_an_invalid_rom(tmp_path):
    assert MVPPSPRomReader(str(write_iso(tmp_path))).get_info().is_valid is False


def test_get_info_answers_the_readers_own_type(tmp_path):
    assert type(loaded(tmp_path).get_info()) is MVPRomInfo


def test_get_info_reports_the_file_size(tmp_path):
    path = write_iso(tmp_path)
    reader = MVPPSPRomReader(str(path))
    reader.load()
    assert reader.get_info().size == os.path.getsize(path)


def test_get_info_reports_the_blob_size(tmp_path):
    assert loaded(tmp_path).get_info().database_big_size == DATABASE_BIG_SIZE


def test_get_info_reports_the_blob_offset(tmp_path):
    start, _ = database_big_extent()
    assert loaded(tmp_path).get_info().database_big_offset == start


def test_a_shallow_get_info_lists_every_slot(tmp_path):
    assert len(loaded(tmp_path).get_info().team_slots) == TEAM_COUNT


def test_a_deep_get_info_on_another_game_lists_no_slots(tmp_path):
    reader = loaded(tmp_path, fixture.DiscSpec(team_records=False))
    assert reader.get_info(deep=True).team_slots == []


def test_a_slot_carries_the_readers_own_type(tmp_path):
    assert type(loaded(tmp_path).get_info().team_slots[0]) is MVPTeamSlot


def test_slot_indices_run_from_zero_to_twenty_nine(tmp_path):
    slots = loaded(tmp_path).get_info().team_slots
    assert [s.index for s in slots] == list(range(TEAM_COUNT))


def test_slot_abbreviations_follow_the_declared_slot_order(tmp_path):
    slots = loaded(tmp_path).get_info().team_slots
    assert [s.abbrev for s in slots] == list(mvp_models.MVP_TEAM_ABBREVS)


def test_slot_names_follow_the_declared_team_order(tmp_path):
    slots = loaded(tmp_path).get_info().team_slots
    assert [s.name for s in slots] == list(mvp_models.MVP_TEAM_ORDER)


def test_a_populated_slot_reports_its_player_count(tmp_path):
    slots = loaded(tmp_path, fixture.DiscSpec(teams=5, players_per_team=9)).get_info().team_slots
    assert slots[4].player_count == 9


def test_an_unpopulated_slot_reports_no_players(tmp_path):
    slots = loaded(tmp_path, fixture.DiscSpec(teams=5, players_per_team=9)).get_info().team_slots
    assert slots[5].player_count == 0


def test_a_populated_slot_names_its_first_player(tmp_path):
    slots = loaded(tmp_path, fixture.DiscSpec(teams=5, players_per_team=9)).get_info().team_slots
    assert slots[3].first_player == "Disc03 Player00"


def test_an_unpopulated_slot_names_nobody(tmp_path):
    slots = loaded(tmp_path, fixture.DiscSpec(teams=5, players_per_team=9)).get_info().team_slots
    assert slots[29].first_player == ""


def test_two_populated_slots_name_different_players(tmp_path):
    # `RomSlot.current_name` comes from here, so two MVP ISOs with different
    # rosters must render differently.
    slots = loaded(tmp_path, fixture.DiscSpec(teams=5, players_per_team=9)).get_info().team_slots
    assert slots[0].first_player != slots[1].first_player


def test_a_slot_whose_player_has_no_attrib_record_names_nobody(tmp_path):
    reader = parsed(tmp_path, fixture.DiscSpec(teams=2, players_per_team=3))
    reader.records["attrib"].clear()
    assert reader.get_info().team_slots[0].first_player == ""
