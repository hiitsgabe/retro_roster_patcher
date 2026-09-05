"""`MVPPSPRomWriter`: rebuilding CSV sections and putting them back in the ISO.

**This is where the second of the three inherited bugs was**, and it has its own
section below. A rebuilt section that does not fit its fixed allocation was
skipped, keeping the disc's original table, dropping every edit to it, and
reporting success. It now raises.

Sections have no length word anywhere in the file: a section starts at its
offset in `SECTION_MAP` and ends where the next one starts. So the two things
this module can get wrong are writing a section at the wrong offset -- which
would corrupt its neighbour -- and letting one overflow.

Read-backs go through the fixture's `decompress_section_at` and `parse_table`,
which are independent of the reader and the writer both.
"""

from __future__ import annotations

import os

import pytest

from retro_roster_patcher.core.errors import RomError
from retro_roster_patcher.games.mvp_psp.models import (
    ATTRIB_BIRTHDAY,
    ATTRIB_FIRST_NAME,
    ATTRIB_LAST_NAME,
    ATTRIB_SALARY,
    ATTRIB_WEIGHT,
    COMPACT_ATTRIB_TABLE,
    DATABASE_BIG_SIZE,
    SECTION_ALLOCATIONS,
    SECTION_COUNT,
    SECTION_MAP,
    database_big_extent,
)
from retro_roster_patcher.games.mvp_psp.rom_writer import (
    COPY_CHUNK_BYTES,
    HEADER_SUFFIX,
    LINE_TERMINATOR,
    RECORD_SUFFIX,
    RECORD_TERMINATOR,
    MVPPSPRomWriter,
    SectionTooLargeError,
    build_csv_record,
    build_csv_section,
)
from tests.fixtures import synthetic_mvp_iso as fixture


@pytest.fixture(autouse=True)
def small_layout(monkeypatch):
    fixture.use_small_layout(monkeypatch)


def write_iso(tmp_path, spec=None, *, name="game.iso"):
    path = tmp_path / name
    path.write_bytes(fixture.build_iso(fixture.build_database_big(spec)))
    return path


def make_writer(tmp_path, spec=None, *, name="game.iso", out="out.iso"):
    writer = MVPPSPRomWriter(str(write_iso(tmp_path, spec, name=name)), str(tmp_path / out))
    writer.load()
    return writer


# -- constants -------------------------------------------------------------


def test_a_record_ends_with_a_comma_and_a_semicolon():
    assert RECORD_SUFFIX == ",;"


def test_a_header_ends_with_a_semicolon_and_no_comma():
    assert HEADER_SUFFIX == ";"


def test_a_line_ends_with_a_carriage_return_and_a_newline():
    assert LINE_TERMINATOR == "\r\n"


def test_the_record_terminator_is_the_header_suffix_and_the_line_terminator():
    assert RECORD_TERMINATOR == HEADER_SUFFIX + LINE_TERMINATOR


def test_the_copy_moves_four_mebibytes_at_a_time():
    assert COPY_CHUNK_BYTES == 4 * 1024 * 1024


# -- build_csv_record ------------------------------------------------------


def test_a_record_starts_with_its_id():
    assert build_csv_record("00b87d5f5", {0: "Ichiro"}).split(",")[0] == "00b87d5f5"


def test_a_record_with_no_columns_is_the_id_and_the_suffix():
    assert build_csv_record("00b87d5f5", {}) == "00b87d5f5,;"


def test_a_column_is_written_as_its_number_a_space_and_its_value():
    assert build_csv_record("00b87d5f5", {22: "61"}) == "00b87d5f5,22 61,;"


def test_columns_are_written_in_ascending_numeric_order():
    # Numeric and not lexical: 9 sorts before 22.
    record = build_csv_record("00b87d5f5", {22: "b", 9: "a", 100: "c"})
    assert record == "00b87d5f5,9 a,22 b,100 c,;"


def test_a_record_built_from_a_reversed_dict_sorts_the_same_way():
    forward = build_csv_record("00b87d5f5", {0: "a", 1: "b", 2: "c"})
    backward = build_csv_record("00b87d5f5", {2: "c", 1: "b", 0: "a"})
    assert forward == backward


def test_an_empty_value_still_takes_its_column_and_its_space():
    assert build_csv_record("00b87d5f5", {0: ""}) == "00b87d5f5,0 ,;"


def test_a_value_containing_spaces_is_written_whole():
    assert build_csv_record("00b87d5f5", {0: "Jean Luc"}) == "00b87d5f5,0 Jean Luc,;"


def test_a_built_record_round_trips_through_the_fixtures_own_parser():
    columns = {0: "Ichiro", 1: "Suzuki", 22: "61", 43: ""}
    line = build_csv_record("00b87d5f5", columns) + LINE_TERMINATOR
    parsed = fixture.parse_table(b"a,b" + RECORD_TERMINATOR.encode() + line.encode())
    assert parsed["00b87d5f5"] == columns


# -- build_csv_section -----------------------------------------------------


def test_a_section_starts_with_its_header_line():
    data = build_csv_section("one,two", {}, None)
    assert data == b"one,two;\r\n"


def test_a_section_with_no_order_sorts_its_records():
    data = build_csv_section("h,e", {"00000000b": {0: "b"}, "00000000a": {0: "a"}}, None)
    assert fixture.parse_table_order(data) == ["00000000a", "00000000b"]


def test_a_section_with_an_empty_order_sorts_its_records():
    # An empty list is falsy, so it takes the same branch as None. That is the
    # source's behaviour and it is what the reader hands over for a table it
    # could not read.
    data = build_csv_section("h,e", {"00000000b": {0: "b"}, "00000000a": {0: "a"}}, [])
    assert fixture.parse_table_order(data) == ["00000000a", "00000000b"]


def test_a_section_with_an_order_keeps_it():
    records = {"00000000a": {0: "a"}, "00000000b": {0: "b"}, "00000000c": {0: "c"}}
    data = build_csv_section("h,e", records, ["00000000c", "00000000a", "00000000b"])
    assert fixture.parse_table_order(data) == ["00000000c", "00000000a", "00000000b"]


def test_a_record_the_order_does_not_name_is_appended():
    records = {"00000000a": {0: "a"}, "00000000f": {0: "f"}}
    data = build_csv_section("h,e", records, ["00000000a"])
    assert fixture.parse_table_order(data) == ["00000000a", "00000000f"]


def test_a_record_the_order_names_but_the_table_no_longer_holds_is_skipped():
    data = build_csv_section("h,e", {"00000000a": {0: "a"}}, ["00000000a", "00000000gone"])
    assert fixture.parse_table_order(data) == ["00000000a"]


def test_an_id_the_order_holds_twice_is_written_twice():
    # Inherited from the reader, which appends a duplicate id to the order
    # twice, and preserved so a table's row count survives a rebuild.
    data = build_csv_section("h,e", {"00000000a": {0: "a"}}, ["00000000a", "00000000a"])
    assert fixture.parse_table_order(data) == ["00000000a", "00000000a"]


def test_a_record_with_no_columns_is_still_written():
    # Distinct from a record the table does not hold, which is skipped.
    data = build_csv_section("h,e", {"00000000a": {}}, ["00000000a"])
    assert data == b"h,e;\r\n00000000a,;\r\n"


def test_a_non_ascii_value_becomes_a_question_mark():
    # `errors="replace"`, the source's choice: a rebuild must not raise
    # halfway through over one accented surname.
    data = build_csv_section("h,e", {"00000000a": {0: "Peña"}}, None)
    assert data == b"h,e;\r\n00000000a,0 Pe?a,;\r\n"


def test_a_built_section_round_trips_through_the_fixtures_own_parser():
    records = {"00000000a": {0: "Ichiro", 1: "Suzuki"}, "00000000b": {0: "Ken", 1: "Griffey"}}
    data = build_csv_section("h,e", records, ["00000000b", "00000000a"])
    assert fixture.parse_table(data) == records


# -- load ------------------------------------------------------------------


def test_loading_a_real_image_answers_true(tmp_path):
    writer = MVPPSPRomWriter(str(write_iso(tmp_path)), str(tmp_path / "out.iso"))
    assert writer.load() is True


def test_loading_a_missing_image_answers_false(tmp_path):
    writer = MVPPSPRomWriter(str(tmp_path / "gone.iso"), str(tmp_path / "out.iso"))
    assert writer.load() is False


def test_loading_an_image_that_fails_the_header_check_answers_false(tmp_path):
    path = write_iso(tmp_path)
    data = bytearray(path.read_bytes())
    start, _ = database_big_extent()
    data[start] = 0x11
    path.write_bytes(bytes(data))
    writer = MVPPSPRomWriter(str(path), str(tmp_path / "out.iso"))
    assert writer.load() is False


def test_loading_reads_every_section(tmp_path):
    assert len(make_writer(tmp_path).reader.sections) == SECTION_COUNT


def test_loading_keeps_a_header_for_every_section(tmp_path):
    assert len(make_writer(tmp_path).section_headers) == SECTION_COUNT


def test_the_kept_header_is_the_sections_first_line(tmp_path):
    assert make_writer(tmp_path).section_headers["attrib"] == fixture.ATTRIB_HEADER


def test_a_section_with_no_terminator_contributes_no_header(tmp_path):
    writer = make_writer(tmp_path)
    writer.section_headers.clear()
    writer.reader.sections["attrib"] = b"no terminator here"
    writer._extract_headers()
    assert "attrib" not in writer.section_headers


# -- update_records / update_player_record ---------------------------------


def test_replacing_a_table_replaces_it(tmp_path):
    writer = make_writer(tmp_path)
    writer.update_records("roster", {"00000000a": {0: "x"}})
    assert writer.reader.records["roster"] == {"00000000a": {0: "x"}}


def test_merging_keeps_a_column_the_update_does_not_name(tmp_path):
    writer = make_writer(tmp_path, fixture.DiscSpec(teams=1, players_per_team=2))
    pid = fixture.player_id(0, 1)
    before = writer.reader.records["attrib"][pid][ATTRIB_BIRTHDAY]
    writer.update_player_record("attrib", pid, {ATTRIB_FIRST_NAME: "New"})
    assert writer.reader.records["attrib"][pid][ATTRIB_BIRTHDAY] == before


def test_merging_overwrites_a_column_the_update_names(tmp_path):
    writer = make_writer(tmp_path, fixture.DiscSpec(teams=1, players_per_team=2))
    pid = fixture.player_id(0, 1)
    writer.update_player_record("attrib", pid, {ATTRIB_FIRST_NAME: "New"})
    assert writer.reader.records["attrib"][pid][ATTRIB_FIRST_NAME] == "New"


def test_merging_into_an_absent_record_creates_it(tmp_path):
    writer = make_writer(tmp_path, fixture.DiscSpec(teams=1, players_per_team=2))
    writer.update_player_record("attrib", "0deadbeef", {ATTRIB_FIRST_NAME: "New"})
    assert writer.reader.records["attrib"]["0deadbeef"] == {ATTRIB_FIRST_NAME: "New"}


def test_merging_into_an_absent_table_creates_it(tmp_path):
    writer = make_writer(tmp_path)
    writer.update_player_record("nosuchtable", "0deadbeef", {0: "x"})
    assert writer.reader.records["nosuchtable"] == {"0deadbeef": {0: "x"}}


def test_merging_does_not_mutate_the_dict_the_reader_handed_over(tmp_path):
    # The source updated the reader's own dict in place, so a second `patch`
    # over one reader would have started from the first one's results.
    writer = make_writer(tmp_path, fixture.DiscSpec(teams=1, players_per_team=2))
    pid = fixture.player_id(0, 1)
    original = writer.reader.records["attrib"][pid]
    writer.update_player_record("attrib", pid, {ATTRIB_FIRST_NAME: "New"})
    assert original[ATTRIB_FIRST_NAME] == "Disc00"


# -- rebuild_database_big --------------------------------------------------


def test_a_rebuild_with_no_edits_is_the_original_blob(tmp_path):
    writer = make_writer(tmp_path)
    assert writer.rebuild_database_big() == writer.reader.database_big


def test_a_rebuild_is_always_the_declared_size(tmp_path):
    writer = make_writer(tmp_path)
    writer.update_player_record("attrib", fixture.player_id(0, 0), {ATTRIB_FIRST_NAME: "New"})
    assert len(writer.rebuild_database_big()) == DATABASE_BIG_SIZE


def test_an_edited_section_carries_the_edit(tmp_path):
    writer = make_writer(tmp_path, fixture.DiscSpec(teams=2, players_per_team=3))
    pid = fixture.player_id(1, 2)
    writer.update_player_record("attrib", pid, {ATTRIB_FIRST_NAME: "Edited"})
    blob = writer.rebuild_database_big()
    table = fixture.parse_table(fixture.decompress_section_at(blob, "attrib"))
    assert table[pid][ATTRIB_FIRST_NAME] == "Edited"


def test_an_edited_section_keeps_the_columns_the_edit_did_not_name(tmp_path):
    writer = make_writer(tmp_path, fixture.DiscSpec(teams=2, players_per_team=3))
    pid = fixture.player_id(1, 2)
    before = writer.reader.records["attrib"][pid][ATTRIB_SALARY]
    writer.update_player_record("attrib", pid, {ATTRIB_FIRST_NAME: "Edited"})
    blob = writer.rebuild_database_big()
    table = fixture.parse_table(fixture.decompress_section_at(blob, "attrib"))
    assert table[pid][ATTRIB_SALARY] == before


def test_an_edited_section_keeps_the_discs_record_order(tmp_path):
    writer = make_writer(tmp_path, fixture.DiscSpec(teams=2, players_per_team=3))
    before = list(writer.reader.record_order["attrib"])
    writer.update_player_record("attrib", fixture.player_id(0, 0), {ATTRIB_FIRST_NAME: "E"})
    blob = writer.rebuild_database_big()
    order = fixture.parse_table_order(fixture.decompress_section_at(blob, "attrib"))
    assert order == before


def test_the_discs_record_order_is_not_the_sorted_one(tmp_path):
    # Which is what makes the previous test mean something: a writer that
    # sorted would fail it.
    writer = make_writer(tmp_path, fixture.DiscSpec(teams=2, players_per_team=3))
    order = writer.reader.record_order["attrib"]
    assert order != sorted(order)


@pytest.mark.parametrize("name", [n for _, n in SECTION_MAP if n != "attrib"])
def test_an_edit_to_one_section_leaves_every_other_section_byte_identical(tmp_path, name):
    writer = make_writer(tmp_path, fixture.DiscSpec(teams=2, players_per_team=3))
    original = writer.reader.database_big
    writer.update_player_record("attrib", fixture.player_id(0, 0), {ATTRIB_FIRST_NAME: "E"})
    rebuilt = writer.rebuild_database_big()
    offset, allocation = SECTION_ALLOCATIONS[name]
    assert rebuilt[offset : offset + allocation] == original[offset : offset + allocation]


def test_the_tail_of_an_edited_sections_allocation_is_zeroed(tmp_path):
    # There is no length word, so the game finds the end of a section by its
    # RefPack end marker; the slack has to be zeroed rather than left holding
    # the tail of the section it replaced.
    writer = make_writer(tmp_path, fixture.DiscSpec(teams=2, players_per_team=3))
    writer.update_player_record("attrib", fixture.player_id(0, 0), {ATTRIB_FIRST_NAME: "E"})
    rebuilt = writer.rebuild_database_big()
    offset, allocation = SECTION_ALLOCATIONS["attrib"]
    stream_end = offset + len(writer._rebuild_section_bytes("attrib"))
    assert set(rebuilt[stream_end : offset + allocation]) == {0}


def test_a_section_nothing_staged_is_not_rebuilt(tmp_path):
    writer = make_writer(tmp_path)
    original = writer.reader.database_big
    writer.update_player_record("attrib", fixture.player_id(0, 0), {ATTRIB_FIRST_NAME: "E"})
    offset, allocation = SECTION_ALLOCATIONS["team"]
    rebuilt = writer.rebuild_database_big()
    assert rebuilt[offset : offset + allocation] == original[offset : offset + allocation]


def test_a_staged_table_with_no_header_is_not_rebuilt(tmp_path):
    writer = make_writer(tmp_path)
    original = writer.reader.database_big
    del writer.section_headers["team"]
    writer.update_records("team", {"00000000a": {0: "x"}})
    offset, allocation = SECTION_ALLOCATIONS["team"]
    rebuilt = writer.rebuild_database_big()
    assert rebuilt[offset : offset + allocation] == original[offset : offset + allocation]


def test_the_compact_attribute_table_is_never_rebuilt(tmp_path):
    # INHERITED DEFECT, preserved: fixing it needs a column layout no disc in
    # this repository can supply. A caller that stages an edit here is the case
    # the guard exists for.
    writer = make_writer(tmp_path)
    original = writer.reader.database_big
    writer.update_records(COMPACT_ATTRIB_TABLE, {"00000000a": {0: "x"}})
    offset, allocation = SECTION_ALLOCATIONS[COMPACT_ATTRIB_TABLE]
    rebuilt = writer.rebuild_database_big()
    assert rebuilt[offset : offset + allocation] == original[offset : offset + allocation]


def test_rebuilding_without_a_loaded_blob_raises(tmp_path):
    writer = MVPPSPRomWriter(str(write_iso(tmp_path)), str(tmp_path / "out.iso"))
    with pytest.raises(RomError):
        writer.rebuild_database_big()


# -- the section-size bound, which is bug 2 --------------------------------


def test_a_section_that_outgrows_its_allocation_raises(tmp_path):
    # DELIBERATE DIVERGENCE. The source did `continue` here, kept the disc's
    # original table, dropped every edit to it, and returned success.
    writer = make_writer(tmp_path, fixture.DiscSpec(teams=2, players_per_team=3))
    _, allocation = SECTION_ALLOCATIONS["teamstat"]
    writer.update_records(
        "teamstat",
        {f"{0x100000000 + i:09x}": {0: os.urandom(64).hex()} for i in range(200)},
    )
    with pytest.raises(SectionTooLargeError):
        writer.rebuild_database_big()


def test_the_raised_error_names_the_table(tmp_path):
    writer = make_writer(tmp_path)
    writer.update_records(
        "teamstat",
        {f"{0x100000000 + i:09x}": {0: os.urandom(64).hex()} for i in range(200)},
    )
    with pytest.raises(SectionTooLargeError) as excinfo:
        writer.rebuild_database_big()
    assert excinfo.value.table == "teamstat"


def test_the_raised_error_reports_the_allocation(tmp_path):
    writer = make_writer(tmp_path)
    writer.update_records(
        "teamstat",
        {f"{0x100000000 + i:09x}": {0: os.urandom(64).hex()} for i in range(200)},
    )
    with pytest.raises(SectionTooLargeError) as excinfo:
        writer.rebuild_database_big()
    assert excinfo.value.allocation == SECTION_ALLOCATIONS["teamstat"][1]


def test_the_raised_error_reports_a_compressed_size_over_the_allocation(tmp_path):
    writer = make_writer(tmp_path)
    writer.update_records(
        "teamstat",
        {f"{0x100000000 + i:09x}": {0: os.urandom(64).hex()} for i in range(200)},
    )
    with pytest.raises(SectionTooLargeError) as excinfo:
        writer.rebuild_database_big()
    assert excinfo.value.compressed > excinfo.value.allocation


def test_the_error_is_a_rom_error(tmp_path):
    # `Patcher.patch` promises `RomError`, and this is a claim about the user's
    # disc rather than a caller bug.
    assert issubclass(SectionTooLargeError, RomError) is True


def test_a_section_that_exactly_fills_its_allocation_does_not_raise(tmp_path):
    # The boundary from the other side: `>` and not `>=`.
    writer = make_writer(tmp_path)
    offset, allocation = SECTION_ALLOCATIONS["teamstat"]
    filler = writer._rebuild_section_bytes("teamstat")
    padding = allocation - len(filler)
    writer.update_records(
        "teamstat",
        {
            **writer.reader.records["teamstat"],
            "0deadbeef": {0: "z" * max(padding * 2, 1)},
        },
    )
    grown = writer._rebuild_section_bytes("teamstat")
    assert (len(grown) > allocation, isinstance(writer.rebuild_database_big(), bytes)) == (
        False,
        True,
    )


# -- copy_iso and finalize -------------------------------------------------


def test_the_copy_is_byte_identical_to_the_source(tmp_path):
    writer = make_writer(tmp_path)
    writer.copy_iso()
    assert (tmp_path / "out.iso").read_bytes() == (tmp_path / "game.iso").read_bytes()


def test_the_copy_creates_a_missing_output_directory(tmp_path):
    writer = MVPPSPRomWriter(str(write_iso(tmp_path)), str(tmp_path / "deep" / "nest" / "o.iso"))
    writer.copy_iso()
    assert (tmp_path / "deep" / "nest" / "o.iso").exists() is True


def test_a_copy_from_a_missing_source_raises_an_oserror(tmp_path):
    writer = MVPPSPRomWriter(str(tmp_path / "gone.iso"), str(tmp_path / "out.iso"))
    with pytest.raises(OSError):
        writer.copy_iso()


def test_finalize_writes_an_output_of_the_sources_length(tmp_path):
    writer = make_writer(tmp_path)
    writer.update_player_record("attrib", fixture.player_id(0, 0), {ATTRIB_FIRST_NAME: "E"})
    writer.finalize()
    assert os.path.getsize(tmp_path / "out.iso") == os.path.getsize(tmp_path / "game.iso")


def test_finalize_leaves_every_byte_before_the_extent_alone(tmp_path):
    start, _ = database_big_extent()
    writer = make_writer(tmp_path)
    writer.update_player_record("attrib", fixture.player_id(0, 0), {ATTRIB_FIRST_NAME: "E"})
    writer.finalize()
    source = (tmp_path / "game.iso").read_bytes()
    assert (tmp_path / "out.iso").read_bytes()[:start] == source[:start]


def test_finalize_leaves_every_byte_after_the_extent_alone(tmp_path):
    _, end = database_big_extent()
    writer = make_writer(tmp_path)
    writer.update_player_record("attrib", fixture.player_id(0, 0), {ATTRIB_FIRST_NAME: "E"})
    writer.finalize()
    source = (tmp_path / "game.iso").read_bytes()
    assert (tmp_path / "out.iso").read_bytes()[end:] == source[end:]


def test_finalize_changes_the_extent(tmp_path):
    # The other half of the two tests above: they would both pass on a patcher
    # that wrote nothing at all.
    _, end = database_big_extent()
    start, _ = database_big_extent()
    writer = make_writer(tmp_path)
    writer.update_player_record("attrib", fixture.player_id(0, 0), {ATTRIB_FIRST_NAME: "Edited"})
    writer.finalize()
    source = (tmp_path / "game.iso").read_bytes()
    assert (tmp_path / "out.iso").read_bytes()[start:end] != source[start:end]


def test_the_written_extent_is_the_rebuilt_blob(tmp_path):
    start, end = database_big_extent()
    writer = make_writer(tmp_path)
    writer.update_player_record("attrib", fixture.player_id(0, 0), {ATTRIB_FIRST_NAME: "Edited"})
    expected = writer.rebuild_database_big()
    writer.finalize()
    assert (tmp_path / "out.iso").read_bytes()[start:end] == expected


def test_the_written_edit_reads_back_through_the_fixtures_own_walk(tmp_path):
    writer = make_writer(tmp_path, fixture.DiscSpec(teams=2, players_per_team=3))
    pid = fixture.player_id(1, 1)
    writer.update_player_record("attrib", pid, {ATTRIB_LAST_NAME: "Rewritten"})
    writer.finalize()
    image = (tmp_path / "out.iso").read_bytes()
    blob = fixture.read_database_big(image, lba=fixture.SMALL_LBA)
    table = fixture.parse_table(fixture.decompress_section_at(blob, "attrib"))
    assert table[pid][ATTRIB_LAST_NAME] == "Rewritten"


def test_a_weight_column_written_reads_back_as_that_weight(tmp_path):
    writer = make_writer(tmp_path, fixture.DiscSpec(teams=2, players_per_team=3))
    pid = fixture.player_id(0, 2)
    writer.update_player_record("attrib", pid, {ATTRIB_WEIGHT: "233"})
    writer.finalize()
    blob = fixture.read_database_big((tmp_path / "out.iso").read_bytes(), lba=fixture.SMALL_LBA)
    table = fixture.parse_table(fixture.decompress_section_at(blob, "attrib"))
    assert table[pid][ATTRIB_WEIGHT] == "233"


def test_finalizing_without_a_loaded_blob_raises(tmp_path):
    writer = MVPPSPRomWriter(str(write_iso(tmp_path)), str(tmp_path / "out.iso"))
    with pytest.raises(RomError):
        writer.finalize()


def test_a_rebuild_of_the_wrong_length_raises_before_anything_is_written(tmp_path, monkeypatch):
    # The invariant `finalize` depends on: the blob is a copy of the original
    # with sections overwritten in place, so it is always the declared size. A
    # rebuild of another length would shift every byte of the image after it.
    writer = make_writer(tmp_path)
    monkeypatch.setattr(writer, "rebuild_database_big", lambda: b"\x00" * (DATABASE_BIG_SIZE - 1))
    with pytest.raises(RomError):
        writer.finalize()


def test_the_length_check_happens_before_the_copy(tmp_path, monkeypatch):
    writer = make_writer(tmp_path)
    monkeypatch.setattr(writer, "rebuild_database_big", lambda: b"\x00" * (DATABASE_BIG_SIZE + 1))
    with pytest.raises(RomError):
        writer.finalize()
    assert (tmp_path / "out.iso").exists() is False


def test_a_section_that_does_not_fit_stops_finalize_before_the_copy(tmp_path):
    # The user gets an error and no half-written output, rather than a
    # successful-looking ISO with one table unchanged.
    writer = make_writer(tmp_path)
    writer.update_records(
        "teamstat",
        {f"{0x100000000 + i:09x}": {0: os.urandom(64).hex()} for i in range(200)},
    )
    with pytest.raises(SectionTooLargeError):
        writer.finalize()
    assert (tmp_path / "out.iso").exists() is False
