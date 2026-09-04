"""`NHL07PSPRomReader` against fabricated PSP disc images.

Every image here is built by `tests/fixtures/synthetic_nhl07_iso.py`. No real
ISO may enter this repository, so nothing below has ever been run against a
retail disc and the field layouts are the fixture's invention.

The reader has three jobs and they fail in different ways, so they are tested
apart:

  * the ISO 9660 walk -- PVD, four directories, an extent -- which the fixture
    checks against its own independent walk in `iso_read_file`;
  * `validate`, the heuristic that decides whether this is NHL 07, whose two
    depths make two different strengths of claim;
  * `_read_team_slots`, which reads names out of the disc's own STEA table and
    is the only reason two NHL 07 images render differently in `analyze`.
"""

from __future__ import annotations

import pytest

from retro_roster_patcher.formats.ea_tdb import EaTdbError
from retro_roster_patcher.games.nhl07_psp.models import (
    NHL07_TEAM_NAMES,
    TDB_BIOATT,
    TDB_MASTER,
    TDB_ROSTER,
    TEAM_COUNT,
)
from retro_roster_patcher.games.nhl07_psp.rom_reader import (
    DB_VIV_DIRS,
    DB_VIV_NAME,
    ISO_SECTOR_SIZE,
    MIN_ISO_SIZE,
    NHL07PSPRomReader,
)
from tests.fixtures import synthetic_nhl07_iso as fixture


def make_iso(tmp_path, spec=None, name="game.iso"):
    """Write a fabricated ISO under `tmp_path` and return its path."""
    path = tmp_path / name
    fixture.write_iso(path, spec)
    return path


def loaded(tmp_path, spec=None, name="game.iso"):
    """A reader over a fresh image, already loaded."""
    reader = NHL07PSPRomReader(str(make_iso(tmp_path, spec, name)))
    reader.load()
    return reader


# -- constants -------------------------------------------------------------


def test_the_iso_path_constant_decomposes_into_the_three_directories_and_the_file():
    # The source declared `DB_VIV_PATH` and then wrote the directory list out
    # again at each of three call sites. These two are derived from it, so a
    # change to the path cannot leave a walk behind.
    assert DB_VIV_DIRS == ("PSP_GAME", "USRDIR", "DB")


def test_the_iso_path_constant_names_the_archive_file():
    assert DB_VIV_NAME == "DB.VIV"


def test_the_minimum_iso_size_is_twenty_sectors():
    # 20 sectors is the PVD at 16 plus the four directories beneath it. Stated
    # as arithmetic rather than as 40960 so the two cannot drift.
    assert MIN_ISO_SIZE == ISO_SECTOR_SIZE * 20


# -- loading ---------------------------------------------------------------


def test_loading_a_well_formed_image_succeeds(tmp_path):
    reader = NHL07PSPRomReader(str(make_iso(tmp_path)))
    assert reader.load() is True


def test_loading_a_missing_file_answers_false(tmp_path):
    reader = NHL07PSPRomReader(str(tmp_path / "absent.iso"))
    assert reader.load() is False


def test_loading_a_file_one_byte_under_the_floor_answers_false(tmp_path):
    path = tmp_path / "short.iso"
    path.write_bytes(b"\x00" * (MIN_ISO_SIZE - 1))
    reader = NHL07PSPRomReader(str(path))
    assert reader.load() is False


def test_loading_a_file_of_exactly_the_floor_gets_past_the_size_check(tmp_path):
    # It still fails, because a block of zeros has no PVD -- but it fails for
    # that reason and not for its size, which is what makes the floor a `<` and
    # not a `<=`. The two are told apart by the size: `_iso_size` is recorded
    # before the walk and stays 0 when the size check refuses.
    path = tmp_path / "floor.iso"
    path.write_bytes(b"\x00" * MIN_ISO_SIZE)
    reader = NHL07PSPRomReader(str(path))
    assert reader.load() is False


def test_a_file_refused_for_its_size_never_records_a_size(tmp_path):
    path = tmp_path / "tiny.iso"
    path.write_bytes(b"\x00" * 16)
    reader = NHL07PSPRomReader(str(path))
    reader.load()
    assert reader.get_info().size == 0


def test_an_unreadable_file_raises_oserror_rather_than_answering_false(tmp_path):
    # DELIBERATE DIVERGENCE from the source, which caught every exception and
    # answered False. `Patcher.analyze_rom` has to tell an unreadable file from
    # a file that is not this game, and it cannot if the reader erased the
    # difference first.
    path = make_iso(tmp_path)
    path.chmod(0o000)
    reader = NHL07PSPRomReader(str(path))
    try:
        with pytest.raises(OSError):
            reader.load()
    finally:
        path.chmod(0o644)


def test_a_bad_volume_descriptor_type_answers_false(tmp_path):
    reader = NHL07PSPRomReader(str(make_iso(tmp_path, fixture.DiscSpec(pvd_type=2))))
    assert reader.load() is False


def test_a_missing_directory_in_the_path_answers_false(tmp_path):
    reader = NHL07PSPRomReader(str(make_iso(tmp_path, fixture.DiscSpec(db_dir_name="XX"))))
    assert reader.load() is False


def test_the_extracted_archive_matches_an_independent_iso_walk(tmp_path):
    # The fixture's `iso_read_file` is a second implementation of the walk. The
    # two agree only if both are right about where the PVD is, how a directory
    # record is laid out, and which endian copy of each number to believe.
    path = make_iso(tmp_path)
    reader = NHL07PSPRomReader(str(path))
    reader.load()
    expected = fixture.iso_read_file(path.read_bytes(), fixture.DB_VIV_ISO_PATH)
    assert reader.get_db_viv() == expected


def test_the_extracted_archive_is_not_empty(tmp_path):
    # Guards the assertion above from passing on two `None`s or two empty
    # strings, which is what a broken walk on both sides would produce.
    assert len(loaded(tmp_path).get_db_viv() or b"") > 1000


def test_the_extracted_archive_starts_with_the_bigf_magic(tmp_path):
    assert (loaded(tmp_path).get_db_viv() or b"")[:4] == b"BIGF"


def test_get_db_viv_before_load_answers_none(tmp_path):
    reader = NHL07PSPRomReader(str(make_iso(tmp_path)))
    assert reader.get_db_viv() is None


def test_the_recorded_size_is_the_whole_image_not_the_archive(tmp_path):
    path = make_iso(tmp_path)
    reader = NHL07PSPRomReader(str(path))
    reader.load()
    assert reader.get_info().size == path.stat().st_size


def test_a_sparsely_padded_image_loads_and_reports_the_padded_size(tmp_path):
    # A real UMD image is hundreds of megabytes and the patcher touches a few
    # kilobytes of it. `pad_to` inflates the file with `truncate`, so this costs
    # a hole rather than 8 MB of writes.
    path = tmp_path / "big.iso"
    fixture.write_iso(path, fixture.DiscSpec(pad_to=8 * 1024 * 1024))
    reader = NHL07PSPRomReader(str(path))
    reader.load()
    assert reader.get_info().size == 8 * 1024 * 1024


# -- validate --------------------------------------------------------------


def test_a_well_formed_image_validates_deeply(tmp_path):
    assert loaded(tmp_path).validate(deep=True) is True


def test_a_well_formed_image_validates_shallowly(tmp_path):
    assert loaded(tmp_path).validate(deep=False) is True


def test_an_archive_that_is_not_a_bigf_fails_both_depths(tmp_path):
    # `load` answers True: the file is there and the walk found it. Only
    # `validate` looks at what is inside.
    reader = loaded(tmp_path, fixture.DiscSpec(archive_magic=b"BIGX"))
    assert reader.validate(deep=False) is False


def test_an_archive_that_is_not_a_bigf_also_fails_the_deep_check(tmp_path):
    reader = loaded(tmp_path, fixture.DiscSpec(archive_magic=b"BIGX"))
    assert reader.validate(deep=True) is False


def test_an_archive_that_is_not_a_bigf_still_loads(tmp_path):
    reader = loaded(tmp_path, fixture.DiscSpec(archive_magic=b"BIGX"))
    assert reader.get_db_viv() is not None


def test_an_archive_without_the_bio_tdb_fails_the_deep_check(tmp_path):
    reader = loaded(tmp_path, fixture.DiscSpec(bioatt_name=None))
    assert reader.validate(deep=True) is False


def test_an_archive_without_the_bio_tdb_still_passes_the_shallow_check(tmp_path):
    # This is the whole difference between the two depths, and it is what makes
    # the deep one worth its decompression cost: the shallow check accepts an
    # EA archive that has nothing to do with NHL 07.
    reader = loaded(tmp_path, fixture.DiscSpec(bioatt_name=None))
    assert reader.validate(deep=False) is True


def test_validate_before_load_answers_false(tmp_path):
    reader = NHL07PSPRomReader(str(make_iso(tmp_path)))
    assert reader.validate(deep=True) is False


def test_the_deep_check_finds_the_bio_tdb_however_the_archive_spells_it(tmp_path):
    # `bigf_extract` folds case on both sides, which is why the source's retry
    # with `TDB_BIOATT.lower()` could never have found anything the first call
    # missed.
    reader = loaded(tmp_path, fixture.DiscSpec(bioatt_name="NHLBIOATT.TDB"))
    assert reader.validate(deep=True) is True


# -- get_tdb ---------------------------------------------------------------


@pytest.mark.parametrize("name", [TDB_MASTER, TDB_BIOATT, TDB_ROSTER])
def test_each_archive_member_parses_as_a_tdb(tmp_path, name):
    assert loaded(tmp_path).get_tdb(name) is not None


def test_the_master_tdb_holds_every_table_the_patcher_needs(tmp_path):
    tdb = loaded(tmp_path).get_tdb(TDB_MASTER)
    assert sorted(tdb.tables) == ["PLAY", "ROST", "SGAI", "SPAI", "SPBT", "STEA"]


def test_the_bio_mirror_holds_only_the_three_tables_it_mirrors(tmp_path):
    tdb = loaded(tmp_path).get_tdb(TDB_BIOATT)
    assert sorted(tdb.tables) == ["SGAI", "SPAI", "SPBT"]


def test_the_roster_mirror_holds_only_rost(tmp_path):
    tdb = loaded(tmp_path).get_tdb(TDB_ROSTER)
    assert sorted(tdb.tables) == ["ROST"]


def test_a_member_the_archive_does_not_hold_answers_none(tmp_path):
    assert loaded(tmp_path).get_tdb("nhl2099.tdb") is None


def test_get_tdb_before_load_answers_none(tmp_path):
    reader = NHL07PSPRomReader(str(make_iso(tmp_path)))
    assert reader.get_tdb(TDB_MASTER) is None


def test_the_same_name_twice_answers_the_same_object(tmp_path):
    # Load-bearing, not an optimisation: `patch` edits the object `get_tdb`
    # returned and then serialises it. A second parse would hand out a table
    # with none of the edits and write the disc back unchanged.
    reader = loaded(tmp_path)
    assert reader.get_tdb(TDB_MASTER) is reader.get_tdb(TDB_MASTER)


def test_a_member_that_is_neither_refpack_nor_a_tdb_raises(tmp_path):
    # `db.viv` is a BIGF and the member is present, so the failure is about the
    # member's own contents -- `EaTdbError`, a `RomError`, which is the library's
    # word for a claim about the user's disc.
    from retro_roster_patcher.formats.ea_tdb import BigfEntry, bigf_build

    path = tmp_path / "junk.iso"
    fixture.write_iso(path)
    reader = NHL07PSPRomReader(str(path))
    reader.load()
    reader._db_viv_data = bigf_build(
        [BigfEntry(name=TDB_MASTER, offset=0, size=0)],
        {TDB_MASTER: b"NOT A TDB AT ALL, NOT EVEN CLOSE"},
    )
    with pytest.raises(EaTdbError):
        reader.get_tdb(TDB_MASTER)


def test_a_member_stored_uncompressed_parses_without_decompression(tmp_path):
    # `db.viv` members need not be RefPacked, so the magic test is a test and
    # not a requirement.
    from retro_roster_patcher.formats.ea_tdb import BigfEntry, bigf_build

    raw = fixture.build_master_tdb()
    path = tmp_path / "plain.iso"
    fixture.write_iso(path)
    reader = NHL07PSPRomReader(str(path))
    reader.load()
    reader._db_viv_data = bigf_build(
        [BigfEntry(name=TDB_MASTER, offset=0, size=0)], {TDB_MASTER: raw}
    )
    assert sorted(reader.get_tdb(TDB_MASTER).tables) == [
        "PLAY",
        "ROST",
        "SGAI",
        "SPAI",
        "SPBT",
        "STEA",
    ]


# -- get_info and the team slots -------------------------------------------


def test_a_valid_image_reports_one_slot_per_stea_record(tmp_path):
    info = loaded(tmp_path).get_info(deep=True)
    assert len(info.team_slots) == fixture.STEA_CAPACITY


def test_slot_names_come_from_the_disc_and_not_from_the_constant(tmp_path):
    # The whole point of the deep path. The fixture's STEA names are
    # `Disc Club NN`, which appears nowhere in `NHL07_TEAM_NAMES`, so a reader
    # that answered the constant fails here.
    info = loaded(tmp_path).get_info(deep=True)
    assert [slot.name for slot in info.team_slots] == [
        fixture.stea_name(i) for i in range(fixture.STEA_CAPACITY)
    ]


def test_no_disc_slot_name_collides_with_the_hardcoded_names(tmp_path):
    # Pins the assertion above as non-trivial: if the fixture ever used a real
    # club name, "came from the disc" would stop being distinguishable.
    info = loaded(tmp_path).get_info(deep=True)
    assert [s.name for s in info.team_slots if s.name in NHL07_TEAM_NAMES] == []


def test_slot_indices_come_from_the_stea_indx_field(tmp_path):
    info = loaded(tmp_path).get_info(deep=True)
    assert [slot.index for slot in info.team_slots] == list(range(fixture.STEA_CAPACITY))


def test_slot_abbreviations_come_from_the_index_table(tmp_path):
    info = loaded(tmp_path).get_info(deep=True)
    assert [slot.abbreviation for slot in info.team_slots[:5]] == [
        "ANA",
        "ATL",
        "BOS",
        "BUF",
        "CGY",
    ]


def test_the_shallow_path_answers_the_hardcoded_club_names(tmp_path):
    # Which is why `analyze_rom` does not use it: every NHL 07 image in a
    # library would render identically.
    info = loaded(tmp_path).get_info(deep=False)
    assert [slot.name for slot in info.team_slots] == NHL07_TEAM_NAMES[:TEAM_COUNT]


def test_the_shallow_path_lists_only_the_nhl_clubs(tmp_path):
    info = loaded(tmp_path).get_info(deep=False)
    assert len(info.team_slots) == TEAM_COUNT


def test_an_invalid_image_reports_no_slots_at_all(tmp_path):
    info = loaded(tmp_path, fixture.DiscSpec(archive_magic=b"BIGX")).get_info(deep=True)
    assert info.team_slots == []


def test_an_invalid_image_reports_is_valid_false(tmp_path):
    info = loaded(tmp_path, fixture.DiscSpec(archive_magic=b"BIGX")).get_info(deep=True)
    assert info.is_valid is False


def test_get_info_before_load_reports_a_zero_size(tmp_path):
    reader = NHL07PSPRomReader(str(make_iso(tmp_path)))
    assert reader.get_info().size == 0


def test_get_info_before_load_reports_is_valid_false(tmp_path):
    reader = NHL07PSPRomReader(str(make_iso(tmp_path)))
    assert reader.get_info().is_valid is False


def test_an_image_whose_master_tdb_is_missing_falls_back_to_the_club_names(tmp_path):
    # No master TDB means no STEA, and the fallback is the constant. `analyze`
    # still lists slots, because `nhlbioatt.tdb` validated -- and `patch` is
    # where the missing master becomes an error.
    info = loaded(tmp_path, fixture.DiscSpec(master_name=None)).get_info(deep=True)
    assert [slot.name for slot in info.team_slots] == NHL07_TEAM_NAMES[:TEAM_COUNT]


def test_a_stea_record_with_an_empty_name_falls_back_to_the_positional_name(tmp_path):
    # A slot whose disc name is blank takes the constant for that position,
    # which is the source's `if not name:` branch.
    from retro_roster_patcher.formats.ea_tdb import TDBFile

    reader = loaded(tmp_path)
    stea = reader.get_tdb(TDB_MASTER).get_table("STEA")
    stea.write_record(3, {"NAME": "", "CITY": ""})
    assert isinstance(reader.get_tdb(TDB_MASTER), TDBFile)
    slots = reader._read_team_slots()
    assert slots[3].name == NHL07_TEAM_NAMES[3]


def test_a_stea_record_with_no_name_but_a_city_takes_the_city(tmp_path):
    reader = loaded(tmp_path)
    reader.get_tdb(TDB_MASTER).get_table("STEA").write_record(4, {"NAME": ""})
    assert reader._read_team_slots()[4].name == "Town 04"


def test_a_live_count_over_the_allocation_is_bounded_by_the_allocation(tmp_path):
    # `formats/ea_tdb.py` deliberately never checks `currentRecords` against
    # `maxRecords` and hands the bound to its consumers. Without it,
    # `read_record` raises `IndexError` out of `analyze_rom`, which the source
    # absorbed with a per-record `except Exception: continue`.
    reader = loaded(tmp_path)
    stea = reader.get_tdb(TDB_MASTER).get_table("STEA")
    stea.num_records = stea.capacity + 500
    assert len(reader._read_team_slots()) == fixture.STEA_CAPACITY


def test_a_live_count_under_the_allocation_bounds_the_slot_list(tmp_path):
    # The other direction, and the one that is not about malformed files: a slot
    # past the live count holds whatever the last roster left there.
    reader = loaded(tmp_path)
    reader.get_tdb(TDB_MASTER).get_table("STEA").num_records = 6
    assert len(reader._read_team_slots()) == 6


# -- locating db.viv on the disc -------------------------------------------


def test_the_archive_is_located_at_the_sector_the_fixture_placed_it(tmp_path):
    db_lba, _, _ = loaded(tmp_path).find_db_viv_location()
    assert db_lba == fixture.DB_VIV_SECTOR


def test_the_located_size_is_the_archive_the_reader_extracted(tmp_path):
    reader = loaded(tmp_path)
    _, db_size, _ = reader.find_db_viv_location()
    assert db_size == len(reader.get_db_viv() or b"")


def test_the_rebuild_budget_is_the_sector_gap_to_the_next_file(tmp_path):
    # The next file's LBA minus this one's, times the sector size. This is the
    # only thing standing between a grown archive and the file after it.
    reader = loaded(tmp_path)
    _, db_size, max_size = reader.find_db_viv_location()
    sectors = -(-db_size // ISO_SECTOR_SIZE) + fixture.GAP_SECTORS
    assert max_size == sectors * ISO_SECTOR_SIZE


def test_the_rebuild_budget_leaves_room_for_the_archive_to_grow(tmp_path):
    reader = loaded(tmp_path)
    _, db_size, max_size = reader.find_db_viv_location()
    assert max_size - db_size == fixture.GAP_SECTORS * ISO_SECTOR_SIZE + (
        -db_size % ISO_SECTOR_SIZE
    )


def test_locating_the_archive_in_a_file_with_no_pvd_answers_zeroes(tmp_path):
    reader = NHL07PSPRomReader(str(make_iso(tmp_path, fixture.DiscSpec(pvd_type=2))))
    assert reader.find_db_viv_location() == (0, 0, 0)


def test_the_directory_record_offset_points_at_a_record_naming_the_archive(tmp_path):
    # The writer seeks to this offset + 10 and + 14 to correct the two length
    # fields. Checked by decoding the record the offset lands on, rather than by
    # comparing against a number this test also computed.
    path = make_iso(tmp_path)
    reader = NHL07PSPRomReader(str(path))
    reader.load()
    offset = reader.find_db_viv_dir_entry_offset()
    image = path.read_bytes()
    name_len = image[offset + 32]
    assert image[offset + 33 : offset + 33 + name_len] == b"DB.VIV;1"


def test_the_directory_record_offset_holds_the_archives_length(tmp_path):
    path = make_iso(tmp_path)
    reader = NHL07PSPRomReader(str(path))
    reader.load()
    offset = reader.find_db_viv_dir_entry_offset()
    import struct

    stated = struct.unpack_from("<I", path.read_bytes(), offset + 10)[0]
    assert stated == len(reader.get_db_viv() or b"")


def test_the_directory_record_offset_in_a_file_with_no_pvd_answers_zero(tmp_path):
    reader = NHL07PSPRomReader(str(make_iso(tmp_path, fixture.DiscSpec(pvd_type=2))))
    assert reader.find_db_viv_dir_entry_offset() == 0


def test_a_directory_record_shorter_than_its_fixed_part_ends_the_scan(tmp_path):
    # IMPROVEMENT over the source, which tested `pos + rec_len <= len(extent)`
    # and then indexed byte 32 of the record regardless. A record claiming a
    # length under 33 in the last bytes of an extent raised `IndexError` there,
    # invisibly, under a blanket `except Exception`. Here it ends the scan, so
    # the entry is simply not found.
    path = make_iso(tmp_path)
    image = bytearray(path.read_bytes())
    start = fixture.DB_DIR_SECTOR * ISO_SECTOR_SIZE
    # Truncate the extent to just past the `.` and `..` records and leave a
    # two-byte record claiming to be two bytes long.
    image[start + 34 + 34] = 2
    image[start + 34 + 34 + 1] = 0
    for i in range(start + 34 + 34 + 2, start + ISO_SECTOR_SIZE):
        image[i] = 0
    path.write_bytes(bytes(image))
    reader = NHL07PSPRomReader(str(path))
    assert reader.load() is False


def test_the_archive_is_found_when_its_directory_record_is_reached_normally(tmp_path):
    # The control for the test above: the same image, unmodified, does find it.
    # Without this the previous test passes for a fixture that never worked.
    assert NHL07PSPRomReader(str(make_iso(tmp_path))).load() is True
