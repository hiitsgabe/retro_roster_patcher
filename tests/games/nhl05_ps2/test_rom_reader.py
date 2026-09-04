"""`NHL05PS2RomReader` against fabricated PS2 disc images.

Every image here is built by `tests/fixtures/synthetic_nhl05_iso.py`. No real
ISO may enter this repository, so nothing below has ever been run against a
retail disc and the field layouts are the fixture's invention.

The reader has three jobs and they fail in different ways, so they are tested
apart:

  * the ISO 9660 walk -- PVD, **one** directory, an extent -- which the fixture
    checks against its own independent walk in `iso_read_file`;
  * `validate`, the heuristic that decides whether this is NHL 2005, whose two
    depths make two different strengths of claim;
  * `_read_team_slots`, which reads `FNME`/`SNME`/`ABBR` out of the disc's own
    STEA table, filters on `INDX` and sorts -- four behaviours NHL 07's reader
    does not have, and the only reason two NHL 2005 images render differently
    in `analyze`.
"""

from __future__ import annotations

import pytest

from retro_roster_patcher.formats.ea_tdb import EaTdbError
from retro_roster_patcher.games.nhl05_ps2.models import (
    NHL05_TEAM_INDEX,
    NHL05_TEAM_NAMES,
    TDB_MASTER,
    TDB_ROSTER,
    TEAM_COUNT,
)
from retro_roster_patcher.games.nhl05_ps2.rom_reader import (
    DB_VIV_DIRS,
    DB_VIV_NAME,
    DB_VIV_PATH,
    ISO_SECTOR_SIZE,
    MIN_ISO_SIZE,
    NHL05PS2RomReader,
)
from tests.fixtures import synthetic_nhl05_iso as fixture


def make_iso(tmp_path, spec=None, name="game.iso"):
    """Write a fabricated ISO under `tmp_path` and return its path."""
    path = tmp_path / name
    fixture.write_iso(path, spec)
    return path


def loaded(tmp_path, spec=None, name="game.iso"):
    """A reader over a fresh image, already loaded."""
    reader = NHL05PS2RomReader(str(make_iso(tmp_path, spec, name)))
    reader.load()
    return reader


# -- constants -------------------------------------------------------------


def test_the_archive_sits_one_directory_below_the_root():
    # The whole of the ISO-layer difference from NHL 07, whose path is three
    # directories deep. A one-element tuple, not the three-element one.
    assert DB_VIV_DIRS == ("DB",)


def test_the_iso_path_constant_names_the_archive_file():
    assert DB_VIV_NAME == "DB.VIV"


def test_the_two_derived_constants_rebuild_the_path_they_came_from():
    # The source declared `DB_VIV_PATH` and then wrote the directory list out
    # again at each of three call sites. These two are derived from it, so a
    # change to the path cannot leave a walk behind.
    assert "/".join([*DB_VIV_DIRS, DB_VIV_NAME]) == DB_VIV_PATH


def test_the_sector_size_is_the_shared_modules():
    assert ISO_SECTOR_SIZE == 2048


def test_the_minimum_iso_size_is_twenty_sectors():
    # Stated as arithmetic rather than as 40960 so the two cannot drift. It is
    # the source's number and is deliberately not re-derived from this game's
    # shallower tree; see the constant's own comment.
    assert MIN_ISO_SIZE == ISO_SECTOR_SIZE * 20


def test_the_master_tdb_is_named_for_the_year():
    # The signature `validate(deep=True)` rests on, and the settled answer to
    # the "is this NHL 06?" question: the patcher extracts a file that exists
    # only on an NHL 2005 disc.
    assert TDB_MASTER == "nhl2005.tdb"


def test_the_roster_tdb_is_the_only_other_member():
    assert TDB_ROSTER == "nhlrost.tdb"


# -- loading ---------------------------------------------------------------


def test_loading_a_well_formed_image_succeeds(tmp_path):
    reader = NHL05PS2RomReader(str(make_iso(tmp_path)))
    assert reader.load() is True


def test_loading_records_the_files_size_on_disk(tmp_path):
    path = make_iso(tmp_path)
    reader = NHL05PS2RomReader(str(path))
    reader.load()
    assert reader.get_info(deep=False).size == path.stat().st_size


def test_loading_a_missing_file_returns_false(tmp_path):
    assert NHL05PS2RomReader(str(tmp_path / "absent.iso")).load() is False


def test_loading_a_file_below_the_size_floor_returns_false(tmp_path):
    path = tmp_path / "tiny.iso"
    path.write_bytes(b"\x00" * (MIN_ISO_SIZE - 1))
    assert NHL05PS2RomReader(str(path)).load() is False


def test_loading_a_file_exactly_at_the_size_floor_gets_past_it(tmp_path):
    # It has no PVD either, so `load` is still False -- but for the next reason
    # along, which is what distinguishes the floor from an accident.
    path = tmp_path / "floor.iso"
    path.write_bytes(b"\x00" * MIN_ISO_SIZE)
    assert NHL05PS2RomReader(str(path)).load() is False


def test_loading_an_unreadable_file_raises_rather_than_answering_false(tmp_path):
    # DELIBERATE DIVERGENCE from the source, which wrapped the body in
    # `except Exception: return False` and turned a revoked read bit into "not
    # this game". `analyze_rom` cannot tell the two apart if the reader has
    # already erased the difference.
    path = make_iso(tmp_path)
    path.chmod(0o000)
    try:
        with pytest.raises(OSError):
            NHL05PS2RomReader(str(path)).load()
    finally:
        path.chmod(0o644)


def test_loading_an_image_whose_pvd_is_not_primary_returns_false(tmp_path):
    assert NHL05PS2RomReader(str(make_iso(tmp_path, fixture.DiscSpec(pvd_type=2)))).load() is False


def test_loading_an_image_whose_db_directory_is_named_otherwise_returns_false(tmp_path):
    spec = fixture.DiscSpec(db_dir_name="XX")
    assert NHL05PS2RomReader(str(make_iso(tmp_path, spec))).load() is False


def test_loading_an_image_whose_db_is_a_file_returns_false(tmp_path):
    # The record is there and names the right extent; only the directory flag
    # is clear. A walk that ignored it would read the archive's own bytes as
    # directory records.
    spec = fixture.DiscSpec(db_is_file=True)
    assert NHL05PS2RomReader(str(make_iso(tmp_path, spec))).load() is False


def test_the_extracted_archive_is_the_one_the_fixtures_own_walk_finds(tmp_path):
    # The reader's walk against a second, independent one. They agree only if
    # both are right about the PVD, the record layout and which endian copy of
    # each number to believe.
    path = make_iso(tmp_path)
    reader = NHL05PS2RomReader(str(path))
    reader.load()
    expected = fixture.iso_read_file(path.read_bytes(), fixture.DB_VIV_ISO_PATH)
    assert reader.get_db_viv() == expected


def test_the_extracted_archive_is_a_bigf(tmp_path):
    assert loaded(tmp_path).get_db_viv()[:4] == b"BIGF"


def test_the_extracted_archive_is_exactly_the_declared_length(tmp_path):
    # Not sector-aligned: a reader that rounded up to a sector would return
    # trailing padding and this equality would fail.
    reader = loaded(tmp_path)
    _, declared_size, _ = reader.find_db_viv_location()
    assert len(reader.get_db_viv()) == declared_size


def test_a_reader_that_never_loaded_has_no_archive(tmp_path):
    assert NHL05PS2RomReader(str(make_iso(tmp_path))).get_db_viv() is None


# -- validate --------------------------------------------------------------


def test_a_well_formed_image_validates_deeply(tmp_path):
    assert loaded(tmp_path).validate(deep=True) is True


def test_a_well_formed_image_validates_shallowly(tmp_path):
    assert loaded(tmp_path).validate(deep=False) is True


def test_an_archive_that_is_not_a_bigf_fails_the_shallow_check(tmp_path):
    spec = fixture.DiscSpec(archive_magic=b"BIGX")
    assert loaded(tmp_path, spec).validate(deep=False) is False


def test_an_archive_that_is_not_a_bigf_fails_the_deep_check_too(tmp_path):
    spec = fixture.DiscSpec(archive_magic=b"BIGX")
    assert loaded(tmp_path, spec).validate(deep=True) is False


def test_an_archive_without_the_master_tdb_fails_the_deep_check(tmp_path):
    # This is where NHL 2005's heuristic is stronger than NHL 07's: the missing
    # file is `nhl2005.tdb`, which names one year, and not a mirror that is on
    # every EA NHL disc of the era.
    spec = fixture.DiscSpec(master_name=None)
    assert loaded(tmp_path, spec).validate(deep=True) is False


def test_an_archive_without_the_master_tdb_still_passes_the_shallow_check(tmp_path):
    # The asymmetry that makes `deep=False` "barely a signature at all".
    spec = fixture.DiscSpec(master_name=None)
    assert loaded(tmp_path, spec).validate(deep=False) is True


def test_the_deep_check_finds_an_upper_case_member(tmp_path):
    # `bigf_extract` folds case on both sides, which is why the source's retry
    # with `TDB_MASTER.lower()` could never have found anything the first call
    # missed.
    spec = fixture.DiscSpec(master_name="NHL2005.TDB")
    assert loaded(tmp_path, spec).validate(deep=True) is True


def test_the_deep_check_accepts_an_uncompressed_master_tdb(tmp_path):
    # A member stored raw rather than RefPacked. The magic test is a test and
    # not a requirement.
    spec = fixture.DiscSpec(master_payload=fixture.build_master_tdb())
    assert loaded(tmp_path, spec).validate(deep=True) is True


def test_the_deep_check_rejects_a_member_that_is_neither_refpack_nor_a_tdb(tmp_path):
    spec = fixture.DiscSpec(master_payload=b"NOT A TDB AT ALL" * 64)
    assert loaded(tmp_path, spec).validate(deep=True) is False


def test_the_deep_check_rejects_a_member_that_is_only_the_refpack_magic(tmp_path):
    # Two bytes: shorter than the `len(raw) > 5` the RefPack branch needs, and
    # not a TDB magic either, so both tests fail rather than one raising.
    spec = fixture.DiscSpec(master_payload=b"\x10\xfb")
    assert loaded(tmp_path, spec).validate(deep=True) is False


def test_a_reader_that_never_loaded_does_not_validate(tmp_path):
    assert NHL05PS2RomReader(str(make_iso(tmp_path))).validate(deep=True) is False


# -- get_info --------------------------------------------------------------


def test_get_info_reports_the_image_as_valid(tmp_path):
    assert loaded(tmp_path).get_info(deep=True).is_valid is True


def test_get_info_reports_the_path_it_was_given(tmp_path):
    path = make_iso(tmp_path)
    reader = NHL05PS2RomReader(str(path))
    reader.load()
    assert reader.get_info(deep=True).path == str(path)


def test_get_info_on_a_reader_that_never_loaded_reports_zero_size(tmp_path):
    info = NHL05PS2RomReader(str(make_iso(tmp_path))).get_info(deep=True)
    assert info.size == 0


def test_get_info_on_a_reader_that_never_loaded_reports_no_slots(tmp_path):
    info = NHL05PS2RomReader(str(make_iso(tmp_path))).get_info(deep=True)
    assert info.team_slots == []


def test_get_info_on_an_invalid_image_reports_no_slots(tmp_path):
    spec = fixture.DiscSpec(archive_magic=b"BIGX")
    assert loaded(tmp_path, spec).get_info(deep=True).team_slots == []


def test_the_shallow_path_reports_the_hard_coded_club_count(tmp_path):
    assert len(loaded(tmp_path).get_info(deep=False).team_slots) == TEAM_COUNT


def test_the_shallow_path_reports_the_constant_names_and_not_the_discs(tmp_path):
    # Which is why `analyze_rom` uses the deep path: every NHL 2005 image in a
    # library would otherwise render identically.
    slots = loaded(tmp_path).get_info(deep=False).team_slots
    assert [s.name for s in slots] == NHL05_TEAM_NAMES[:TEAM_COUNT]


def test_the_shallow_path_reports_the_constant_abbreviations(tmp_path):
    slots = loaded(tmp_path).get_info(deep=False).team_slots
    assert [s.abbreviation for s in slots] == [NHL05_TEAM_INDEX[i] for i in range(TEAM_COUNT)]


def test_the_deep_path_reports_names_the_disc_carries(tmp_path):
    slots = loaded(tmp_path).get_info(deep=True).team_slots
    assert slots[0].name == fixture.stea_full_name(0)


def test_the_two_paths_disagree_about_the_names(tmp_path):
    # Guards against zero-over-zero: if the fixture used the real club names,
    # the two previous tests would both pass against a reader that ignored STEA.
    reader = loaded(tmp_path)
    deep = [s.name for s in reader.get_info(deep=True).team_slots]
    shallow = [s.name for s in reader.get_info(deep=False).team_slots]
    assert deep != shallow


# -- team slots ------------------------------------------------------------


def test_the_deep_path_drops_every_stea_record_past_the_club_slots(tmp_path):
    # The fixture's STEA declares 40 records with `INDX` from 39 down to 0, and
    # the reader keeps the 30 whose `INDX` is a club slot. NHL 07's reader has
    # no such filter -- it bounds the loop instead.
    assert len(loaded(tmp_path).get_info(deep=True).team_slots) == 30


def test_the_fixture_declares_more_stea_records_than_survive(tmp_path):
    # The other half of the previous claim: with a 30-record table the filter
    # would be untested and the count would be right by accident.
    assert fixture.STEA_CAPACITY == 40


def test_the_deep_path_returns_the_slots_sorted_by_index(tmp_path):
    # The fixture lays STEA out with `INDX` descending, so an unsorted reader
    # answers 29 down to 0 and this fails.
    slots = loaded(tmp_path).get_info(deep=True).team_slots
    assert [s.index for s in slots] == fixture.STEA_PATCHABLE_INDICES


def test_no_stea_record_sits_at_the_position_its_index_names(tmp_path):
    # What makes the sort and the filter testable at all: a fixture whose
    # `INDX` equalled the record position could not tell a reader that read the
    # field from one that used its loop counter.
    positions = [p for p in range(fixture.STEA_CAPACITY) if fixture.stea_indx_for(p) == p]
    assert positions == []


def test_the_index_comes_from_the_records_own_field(tmp_path):
    # Slot 0 is at STEA position 39, the last record in the table.
    assert fixture.stea_indx_for(fixture.STEA_CAPACITY - 1) == 0


def test_a_slot_takes_its_name_from_fnme(tmp_path):
    slots = {s.index: s for s in loaded(tmp_path).get_info(deep=True).team_slots}
    assert slots[0].name == "Disc Club 00"


def test_a_slot_without_fnme_falls_back_to_snme(tmp_path):
    # NHL 07 reads `NAME` then `CITY`; this game reads `FNME` then `SNME`, and
    # a fixture carrying NHL 07's field names would make every slot fall back to
    # the constant with nothing noticing.
    slots = {s.index: s for s in loaded(tmp_path).get_info(deep=True).team_slots}
    assert slots[fixture.STEA_NO_FNME].name == fixture.stea_short_name(fixture.STEA_NO_FNME)


def test_a_slot_with_neither_name_falls_back_to_the_constant(tmp_path):
    slots = {s.index: s for s in loaded(tmp_path).get_info(deep=True).team_slots}
    assert slots[fixture.STEA_NO_NAME_AT_ALL].name == NHL05_TEAM_NAMES[fixture.STEA_NO_NAME_AT_ALL]


def test_a_slot_takes_its_abbreviation_from_abbr(tmp_path):
    # Also unlike NHL 07, which always uses the constant.
    slots = {s.index: s for s in loaded(tmp_path).get_info(deep=True).team_slots}
    assert slots[0].abbreviation == "D00"


def test_a_slot_without_abbr_falls_back_to_the_constant(tmp_path):
    slots = {s.index: s for s in loaded(tmp_path).get_info(deep=True).team_slots}
    assert slots[fixture.STEA_NO_ABBR].abbreviation == NHL05_TEAM_INDEX[fixture.STEA_NO_ABBR]


def test_the_discs_abbreviations_are_not_the_constants(tmp_path):
    # Guards the two `ABBR` tests above from being satisfied by a reader that
    # never looked at the field.
    disc = {fixture.stea_abbr(i) for i in range(30)} - {""}
    assert disc & set(NHL05_TEAM_INDEX.values()) == set()


def test_slots_fall_back_to_the_constants_when_stea_is_absent(tmp_path):
    # A master TDB with no STEA at all. `get_tdb` still parses, so this is the
    # `stea is None` branch and not an error path.
    from retro_roster_patcher.formats.ea_tdb import refpack_compress
    from tests.fixtures.synthetic_tdb import build_tdb

    payload = build_tdb([])
    spec = fixture.DiscSpec(
        master_payload=refpack_compress(payload) + b"\x00" * fixture.MEMBER_SLACK
    )
    slots = loaded(tmp_path, spec)._read_team_slots()
    assert [s.name for s in slots] == NHL05_TEAM_NAMES[:TEAM_COUNT]


def test_a_stea_header_overstating_its_live_count_does_not_raise(tmp_path):
    # The bound `formats/ea_tdb.py` hands its consumers: it never checks
    # `currentRecords` against `maxRecords`, so the reader must. Without the
    # `min(num_records, capacity)` this raises `IndexError` out of `analyze`.
    reader = loaded(tmp_path)
    stea = reader.get_tdb(TDB_MASTER).get_table("STEA")
    stea.num_records = stea.capacity + 25
    assert len(reader._read_team_slots()) == 30


def test_a_stea_header_understating_its_live_count_reads_fewer_slots(tmp_path):
    # The other side of the same bound, so the `min` is shown to take either
    # argument. 35 live records of 40 leaves `INDX` 39 down to 5, of which 25
    # are club slots.
    reader = loaded(tmp_path)
    stea = reader.get_tdb(TDB_MASTER).get_table("STEA")
    stea.num_records = 35
    assert [s.index for s in reader._read_team_slots()] == list(range(5, 30))


# -- get_tdb ---------------------------------------------------------------


def test_get_tdb_parses_the_master(tmp_path):
    assert loaded(tmp_path).get_tdb(TDB_MASTER) is not None


def test_get_tdb_parses_the_roster_mirror(tmp_path):
    assert loaded(tmp_path).get_tdb(TDB_ROSTER) is not None


def test_get_tdb_returns_none_for_a_member_the_archive_lacks(tmp_path):
    # `nhlbioatt.tdb` is NHL 07's third member and this game has no such file.
    assert loaded(tmp_path).get_tdb("nhlbioatt.tdb") is None


def test_the_archive_holds_exactly_two_members(tmp_path):
    from retro_roster_patcher.formats.ea_tdb import bigf_parse

    entries = bigf_parse(loaded(tmp_path).get_db_viv())
    assert [e.name for e in entries] == [TDB_MASTER, TDB_ROSTER]


def test_get_tdb_caches_by_the_name_the_caller_asked_for(tmp_path):
    reader = loaded(tmp_path)
    assert reader.get_tdb(TDB_MASTER) is reader.get_tdb(TDB_MASTER)


def test_get_tdb_hands_out_two_objects_for_two_spellings_of_one_member(tmp_path):
    # Documented rather than desired. Every caller in the package uses the
    # `TDB_*` constants, so it never happens; the cost of it happening is why
    # the docstring exists and why this pins it.
    reader = loaded(tmp_path)
    assert reader.get_tdb(TDB_MASTER) is not reader.get_tdb(TDB_MASTER.upper())


def test_get_tdb_on_a_reader_that_never_loaded_returns_none(tmp_path):
    assert NHL05PS2RomReader(str(make_iso(tmp_path))).get_tdb(TDB_MASTER) is None


def test_get_tdb_raises_for_a_member_that_is_not_a_tdb(tmp_path):
    spec = fixture.DiscSpec(master_payload=b"NOT A TDB AT ALL" * 64)
    with pytest.raises(EaTdbError):
        loaded(tmp_path, spec).get_tdb(TDB_MASTER)


def test_the_master_holds_every_table_the_patcher_needs(tmp_path):
    tdb = loaded(tmp_path).get_tdb(TDB_MASTER)
    assert sorted(tdb.tables) == ["PLAY", "ROST", "SGAI", "SPAI", "SPBT", "STEA"]


def test_the_roster_mirror_holds_only_rost(tmp_path):
    tdb = loaded(tmp_path).get_tdb(TDB_ROSTER)
    assert sorted(tdb.tables) == ["ROST"]


# -- locating the archive on the disc --------------------------------------


def test_find_db_viv_location_reports_the_fixtures_lba(tmp_path):
    lba, _, _ = loaded(tmp_path).find_db_viv_location()
    assert lba == fixture.DB_VIV_SECTOR


def test_find_db_viv_location_reports_the_archives_length(tmp_path):
    reader = loaded(tmp_path)
    _, size, _ = reader.find_db_viv_location()
    assert size == len(reader.get_db_viv())


def test_the_rebuild_budget_is_the_gap_to_the_next_file(tmp_path):
    reader = loaded(tmp_path)
    _, size, max_size = reader.find_db_viv_location()
    sectors = -(-size // ISO_SECTOR_SIZE) + fixture.GAP_SECTORS
    assert max_size == sectors * ISO_SECTOR_SIZE


def test_the_rebuild_budget_exceeds_the_archive_by_the_gap_plus_its_own_slack(tmp_path):
    # Restates the same number as a difference rather than a product, so a
    # budget that happened to equal the archive's sector-aligned length -- which
    # is what the no-next-file branch answers -- cannot satisfy both.
    reader = loaded(tmp_path)
    _, size, max_size = reader.find_db_viv_location()
    assert max_size - size == fixture.GAP_SECTORS * ISO_SECTOR_SIZE + (-size % ISO_SECTOR_SIZE)


def test_with_no_next_file_the_budget_collapses_to_the_archives_own_length(tmp_path):
    # A `/DB` directory holding only `DB.VIV`, which a PS2 disc plausibly has.
    # The archive may then grow only into the padding of its own last sector.
    reader = loaded(tmp_path, fixture.DiscSpec(no_pad_file=True))
    _, size, max_size = reader.find_db_viv_location()
    assert max_size == -(-size // ISO_SECTOR_SIZE) * ISO_SECTOR_SIZE


def test_the_collapsed_budget_is_smaller_than_the_one_with_a_next_file(tmp_path):
    with_pad = loaded(tmp_path, name="a.iso").find_db_viv_location()[2]
    without = loaded(tmp_path, fixture.DiscSpec(no_pad_file=True), name="b.iso")
    assert without.find_db_viv_location()[2] < with_pad


def test_the_next_file_is_found_by_position_and_not_by_directory_order(tmp_path):
    # `DB.VIV` listed after the padding file it precedes on the disc. The budget
    # must be the same.
    ordered = loaded(tmp_path, name="a.iso").find_db_viv_location()
    reversed_ = loaded(tmp_path, fixture.DiscSpec(db_viv_last=True), name="b.iso")
    assert reversed_.find_db_viv_location() == ordered


def test_find_db_viv_location_answers_zeroes_when_the_directory_is_missing(tmp_path):
    spec = fixture.DiscSpec(db_dir_name="XX")
    reader = NHL05PS2RomReader(str(make_iso(tmp_path, spec)))
    assert reader.find_db_viv_location() == (0, 0, 0)


def test_find_db_viv_location_answers_zeroes_when_there_is_no_pvd(tmp_path):
    spec = fixture.DiscSpec(pvd_type=2)
    reader = NHL05PS2RomReader(str(make_iso(tmp_path, spec)))
    assert reader.find_db_viv_location() == (0, 0, 0)


def test_the_archive_is_found_when_its_record_ends_flush_with_the_extent(tmp_path):
    # `DB.VIV` last in a directory declared as exactly its records, so the final
    # record ends at `len(data)`. A scan breaking on `>=` rather than `>` loses
    # it. Reached through `find_entry_with_next_lba`, which is the other of the
    # two scans.
    spec = fixture.DiscSpec(db_dir_exact_size=True, db_viv_last=True)
    lba, _, _ = loaded(tmp_path, spec).find_db_viv_location()
    assert lba == fixture.DB_VIV_SECTOR


def test_the_archive_is_extracted_when_its_record_ends_flush_with_the_extent(tmp_path):
    # The same image through `find_entry`, the scan `_extract_db_viv` uses.
    spec = fixture.DiscSpec(db_dir_exact_size=True, db_viv_last=True)
    assert loaded(tmp_path, spec).get_db_viv()[:4] == b"BIGF"


# -- the directory record --------------------------------------------------


def test_the_directory_record_offset_lies_inside_the_db_directory(tmp_path):
    offset = loaded(tmp_path).find_db_viv_dir_entry_offset()
    start = fixture.DB_DIR_SECTOR * ISO_SECTOR_SIZE
    assert start <= offset < start + ISO_SECTOR_SIZE


def test_the_directory_record_offset_names_the_records_own_length(tmp_path):
    # Byte 0 of a directory record is its length. `DB.VIV;1` is eight
    # characters, so 33 + 8 rounded up to an even number is 42.
    path = make_iso(tmp_path)
    reader = NHL05PS2RomReader(str(path))
    reader.load()
    offset = reader.find_db_viv_dir_entry_offset()
    assert path.read_bytes()[offset] == 42


def test_the_little_endian_length_at_plus_ten_is_the_archives_length(tmp_path):
    import struct

    path = make_iso(tmp_path)
    reader = NHL05PS2RomReader(str(path))
    reader.load()
    offset = reader.find_db_viv_dir_entry_offset()
    length = struct.unpack_from("<I", path.read_bytes(), offset + 10)[0]
    assert length == len(reader.get_db_viv())


def test_the_big_endian_length_at_plus_fourteen_is_the_same_number(tmp_path):
    import struct

    path = make_iso(tmp_path)
    reader = NHL05PS2RomReader(str(path))
    reader.load()
    offset = reader.find_db_viv_dir_entry_offset()
    length = struct.unpack_from(">I", path.read_bytes(), offset + 14)[0]
    assert length == len(reader.get_db_viv())


def test_the_directory_record_offset_is_zero_when_the_directory_is_missing(tmp_path):
    spec = fixture.DiscSpec(db_dir_name="XX")
    reader = NHL05PS2RomReader(str(make_iso(tmp_path, spec)))
    assert reader.find_db_viv_dir_entry_offset() == 0


def test_the_directory_record_offset_is_zero_when_there_is_no_pvd(tmp_path):
    spec = fixture.DiscSpec(pvd_type=2)
    reader = NHL05PS2RomReader(str(make_iso(tmp_path, spec)))
    assert reader.find_db_viv_dir_entry_offset() == 0


# -- what the fixture disc actually contains -------------------------------


def test_no_roster_row_holds_the_player_its_position_would_suggest(tmp_path):
    # The fixture's four-hop chain, stated: a ROST row's `INDX` names a PLAY
    # record at a different position, whose `ID__` names an SPBT record at a
    # third. If any of the three were the identity, a patcher that confused them
    # would pass every test in this directory.
    collisions = [
        (t, r)
        for t in range(fixture.TEAM_COUNT)
        for r in range(fixture.ROWS_PER_TEAM)
        if fixture.rost_position(t, r) == fixture.spbt_position(t, r)
    ]
    assert collisions == []


def test_the_roster_identifiers_and_the_player_identifiers_do_not_overlap(tmp_path):
    rost = {fixture.rost_indx_for(t, r) for t in range(4) for r in range(25)}
    player = {fixture.player_id_for(t, r) for t in range(4) for r in range(25)}
    assert rost & player == set()


def test_the_discs_bios_survive_a_read_back_unchanged(tmp_path):
    # The fixture's own decoder against `TDBFile.parse`'s positions. If these
    # disagreed, every "the patcher changed this record" assertion would be
    # measuring the fixture rather than the patcher.
    image = make_iso(tmp_path).read_bytes()
    records = fixture.read_table_records(
        fixture.read_member(image, TDB_MASTER),
        "SPBT",
        fixture.SPBT_FIELDS,
        fixture.SPBT_RECORD_SIZE,
    )
    position = fixture.spbt_position(2, 9)
    assert records[position] == fixture.disc_bio_values(2, 9)


# -- the size floor, and what it is a floor on ------------------------------


def test_the_smallest_well_formed_image_is_exactly_the_floor():
    # The floor is not slack. This game's tree is a PVD at 16, a root at 17, a
    # `DB` directory at 18 and `DB.VIV` at 19, so 20 sectors is the tightest
    # value that admits every real image. NHL 07's tree needs 22 and carries the
    # same constant, where it *is* slack.
    assert len(fixture.build_tiny_iso()) == MIN_ISO_SIZE


def test_the_smallest_well_formed_image_loads(tmp_path):
    # Which is what says the floor accepts everything it should.
    path = tmp_path / "tiny.iso"
    path.write_bytes(fixture.build_tiny_iso())
    assert NHL05PS2RomReader(str(path)).load() is True


def test_one_byte_below_the_floor_is_refused_before_anything_is_read(tmp_path):
    # Kills `if self._iso_size < MIN_ISO_SIZE` -> `if False`. Without the floor
    # the walk still succeeds -- the PVD, both directories and `DB.VIV`'s record
    # are all inside the truncation -- and `f.read` returns a short archive,
    # silently, which is exactly what the floor exists to stop.
    path = tmp_path / "short.iso"
    path.write_bytes(fixture.build_tiny_iso()[: MIN_ISO_SIZE - 1])
    assert NHL05PS2RomReader(str(path)).load() is False


def test_the_directory_record_of_a_truncated_tiny_image_is_still_intact(tmp_path):
    # The other half of the previous claim: the truncation removes one byte of
    # `DB.VIV`, not any part of the tree, so nothing but the floor refuses it.
    image = fixture.build_tiny_iso()[: MIN_ISO_SIZE - 1]
    assert image[fixture.DB_DIR_SECTOR * ISO_SECTOR_SIZE] == 34


def test_an_archive_authored_as_a_directory_is_refused(tmp_path):
    # `DB.VIV` with the directory flag set. A reader that ignored it would hand
    # the archive's own bytes to `bigf_parse` as though they were records.
    spec = fixture.DiscSpec(db_viv_is_dir=True)
    assert NHL05PS2RomReader(str(make_iso(tmp_path, spec))).load() is False


def test_the_same_image_with_the_flag_clear_loads(tmp_path):
    # Guards the previous test against passing for some other reason: one bit
    # of one byte is the whole difference.
    assert NHL05PS2RomReader(str(make_iso(tmp_path))).load() is True
