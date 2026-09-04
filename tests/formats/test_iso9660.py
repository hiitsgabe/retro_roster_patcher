"""ISO 9660 Mode 1 / 2048 directory reading, against images this module wrote.

Every image comes from `tests.fixtures.synthetic_iso`, which lays records out
from the ECMA-119 field definitions rather than by calling anything under test.
That is the point: a reader checked only against its own writer agrees with any
layout the pair happen to share, including a wrong one.

The layout every test starts from, unless it says otherwise:

    16  PVD
    17  root       -> DIR1 (dir, 18), TOPFILE.BIN (file, 24)
    18  DIR1       -> DIR2 (dir, 19), MID.BIN (file, 25)
    19  DIR2       -> TARGET.BIN (file, 21), ZZPAD.BIN (file, 23)
    21  TARGET.BIN, 2 sectors, then one spare sector before ZZPAD
    23  ZZPAD.BIN
    24  TOPFILE.BIN
    25  MID.BIN

Three properties of it are load-bearing and none is accidental:

- **No LBA equals the depth, the record position, or the file's length in
  sectors.** A walk that returned a constant, or that used a loop counter for an
  extent, cannot satisfy an assertion here.
- **`ZZPAD.BIN` is listed after `TARGET.BIN` in DIR2 and also sits after it on
  the disc**, with a one-sector gap. The gap is 1 and the file is 2 sectors long,
  so "next LBA minus this LBA" and "this file's length" are different numbers.
- **`DIR1` holds both a directory and a file**, so a walk that ignored the
  directory flag would find `MID.BIN` where it wanted `DIR2`.
"""

import ast
import inspect
import io
import pathlib
import struct

import pytest

from retro_roster_patcher.formats import iso9660
from tests.fixtures import synthetic_iso as fx

ROOT_LBA = 17
DIR1_LBA = 18
DIR2_LBA = 19
TARGET_LBA = 21
TARGET_SECTORS = 2
GAP_SECTORS = 1
ZZPAD_LBA = TARGET_LBA + TARGET_SECTORS + GAP_SECTORS  # 23
TOPFILE_LBA = 24
MIDFILE_LBA = 25
TOTAL_SECTORS = 26

TARGET_SIZE = TARGET_SECTORS * fx.SECTOR_SIZE - 7  # not sector-aligned
ZZPAD_SIZE = 1000
TOPFILE_SIZE = 300
MIDFILE_SIZE = 400

TARGET_BYTES = bytes((i * 37 + 11) % 256 for i in range(TARGET_SIZE))
ZZPAD_BYTES = bytes((i * 5 + 200) % 256 for i in range(ZZPAD_SIZE))


def _dir2_records(*, swap: bool = False, target_rec_len: int | None = None) -> list[bytes]:
    records = [
        fx.dir_record(
            b"TARGET.BIN;1", TARGET_LBA, TARGET_SIZE, is_dir=False, rec_len=target_rec_len
        ),
        fx.dir_record(b"ZZPAD.BIN;1", ZZPAD_LBA, ZZPAD_SIZE, is_dir=False),
    ]
    if swap:
        records.reverse()
    return records


def build(
    *,
    pvd_type: int = 1,
    root_size: int = fx.SECTOR_SIZE,
    dir2_declared: int | None = None,
    dir2_swap: bool = False,
    dir2_sectors: int = 1,
    target_rec_len: int | None = None,
    dir2_is_dir: bool = True,
    total_sectors: int = TOTAL_SECTORS,
) -> bytes:
    """The reference image, with one thing at a time allowed to be wrong."""
    dir2_records = _dir2_records(swap=dir2_swap, target_rec_len=target_rec_len)
    dir2 = fx.directory_extent(DIR2_LBA, DIR1_LBA, dir2_records, sectors=dir2_sectors)
    dir2_size = dir2_declared if dir2_declared is not None else len(dir2)

    dir1 = fx.directory_extent(
        DIR1_LBA,
        ROOT_LBA,
        [
            fx.dir_record(b"DIR2", DIR2_LBA, dir2_size, is_dir=dir2_is_dir),
            fx.dir_record(b"MID.BIN;1", MIDFILE_LBA, MIDFILE_SIZE, is_dir=False),
        ],
    )
    root = fx.directory_extent(
        ROOT_LBA,
        ROOT_LBA,
        [
            fx.dir_record(b"DIR1", DIR1_LBA, len(dir1), is_dir=True),
            fx.dir_record(b"TOPFILE.BIN;1", TOPFILE_LBA, TOPFILE_SIZE, is_dir=False),
        ],
    )
    return fx.build_image(
        {
            fx.PVD_SECTOR: fx.pvd(total_sectors, ROOT_LBA, root_size, type_code=pvd_type),
            ROOT_LBA: root,
            DIR1_LBA: dir1,
            DIR2_LBA: dir2,
            TARGET_LBA: TARGET_BYTES,
            ZZPAD_LBA: ZZPAD_BYTES,
        },
        total_sectors,
    )


def handle(image: bytes) -> io.BytesIO:
    return io.BytesIO(image)


# ──────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────


def test_a_mode_1_sector_is_2048_bytes():
    assert iso9660.SECTOR_SIZE == 2048


def test_the_primary_volume_descriptor_follows_16_sectors_of_system_area():
    assert iso9660.PVD_OFFSET == 16 * 2048


def test_the_record_header_is_33_bytes_up_to_and_including_the_name_length():
    # 33 is what `_records` bounds its index of byte +32 by. Restated here as a
    # number rather than as `32 + 1` so that a change to either is visible.
    assert iso9660.RECORD_HEADER_LENGTH == 33


# ──────────────────────────────────────────────────────────────
# read_root
# ──────────────────────────────────────────────────────────────


def test_read_root_returns_the_root_directorys_extent():
    root = iso9660.read_root(handle(build()))
    assert root == iso9660.Extent(lba=ROOT_LBA, size=fx.SECTOR_SIZE)


def test_read_root_reads_the_lba_from_the_pvd_rather_than_assuming_17():
    # The root moves and nothing else does, so a reader with 17 hardcoded fails.
    image = bytearray(build())
    struct.pack_into("<I", image, iso9660.PVD_OFFSET + 156 + 2, 99)
    root = iso9660.read_root(handle(bytes(image)))
    assert root is not None
    assert root.lba == 99


def test_read_root_reads_the_size_from_the_pvd_rather_than_assuming_one_sector():
    root = iso9660.read_root(handle(build(root_size=1234)))
    assert root == iso9660.Extent(lba=ROOT_LBA, size=1234)


def test_read_root_refuses_a_descriptor_that_is_not_the_primary_one():
    assert iso9660.read_root(handle(build(pvd_type=2))) is None


def test_read_root_refuses_an_image_too_short_to_hold_a_pvd():
    short = build()[: iso9660.PVD_OFFSET + 100]
    assert iso9660.read_root(handle(short)) is None


def test_read_root_refuses_an_image_one_byte_short_of_a_whole_pvd_sector():
    # The boundary, not just "short": `len(pvd) < SECTOR_SIZE` and not `<=`.
    short = build()[: iso9660.PVD_OFFSET + fx.SECTOR_SIZE - 1]
    assert iso9660.read_root(handle(short)) is None


def test_read_root_accepts_an_image_that_ends_exactly_at_the_pvd_sector():
    exact = build()[: iso9660.PVD_OFFSET + fx.SECTOR_SIZE]
    root = iso9660.read_root(handle(exact))
    assert root == iso9660.Extent(lba=ROOT_LBA, size=fx.SECTOR_SIZE)


# ──────────────────────────────────────────────────────────────
# Extent arithmetic
# ──────────────────────────────────────────────────────────────


def test_extent_offset_is_the_lba_times_the_sector_size():
    assert iso9660.Extent(lba=21, size=99).offset == 21 * 2048


def test_extent_end_is_the_offset_plus_the_size_and_not_sector_aligned():
    # 43008 + 4089, which is not a multiple of 2048 -- the bound both games
    # check is a byte count, not a sector count.
    assert iso9660.Extent(lba=21, size=4089).end == 47097


def test_an_extent_of_zero_length_ends_where_it_begins():
    assert iso9660.Extent(lba=21, size=0).end == 21 * 2048


# ──────────────────────────────────────────────────────────────
# find_entry
# ──────────────────────────────────────────────────────────────


def test_find_entry_finds_a_file_and_reports_its_extent():
    f = handle(build())
    root = iso9660.read_root(f)
    assert root is not None
    entry = iso9660.find_entry(f, root, "TOPFILE.BIN")
    assert entry is not None
    assert entry.extent == iso9660.Extent(lba=TOPFILE_LBA, size=TOPFILE_SIZE)


def test_find_entry_strips_the_version_suffix_from_the_records_name():
    f = handle(build())
    root = iso9660.read_root(f)
    assert root is not None
    entry = iso9660.find_entry(f, root, "TOPFILE.BIN")
    assert entry is not None
    assert entry.name == "TOPFILE.BIN"


def test_find_entry_matches_a_lowercase_request_against_an_uppercase_record():
    f = handle(build())
    root = iso9660.read_root(f)
    assert root is not None
    entry = iso9660.find_entry(f, root, "topfile.bin")
    assert entry is not None
    assert entry.extent.lba == TOPFILE_LBA


def test_find_entry_reports_a_directory_as_a_directory():
    f = handle(build())
    root = iso9660.read_root(f)
    assert root is not None
    entry = iso9660.find_entry(f, root, "DIR1")
    assert entry is not None
    assert entry.is_dir is True


def test_find_entry_reports_a_file_as_not_a_directory():
    f = handle(build())
    root = iso9660.read_root(f)
    assert root is not None
    entry = iso9660.find_entry(f, root, "TOPFILE.BIN")
    assert entry is not None
    assert entry.is_dir is False


def test_find_entry_returns_none_for_a_name_that_is_not_there():
    f = handle(build())
    root = iso9660.read_root(f)
    assert root is not None
    assert iso9660.find_entry(f, root, "ABSENT.BIN") is None


def test_find_entry_does_not_match_the_self_record():
    # `.` has the one-byte name 0x00, which `_clean_name` turns into "\x00".
    # Nothing a caller can ask for matches it, and this pins that the record is
    # walked past rather than treated as the directory's own name.
    f = handle(build())
    root = iso9660.read_root(f)
    assert root is not None
    assert iso9660.find_entry(f, root, "\x00") is not None


def test_the_self_record_points_at_the_directory_that_holds_it():
    # The one place `.` is legible, and it is here to show the previous test's
    # match is the `.` record and not an accident of the padding.
    f = handle(build())
    root = iso9660.read_root(f)
    assert root is not None
    entry = iso9660.find_entry(f, root, "\x00")
    assert entry is not None
    assert entry.extent.lba == ROOT_LBA


def test_find_entry_reports_the_absolute_offset_of_the_record_itself():
    # The `.` and `..` records are 34 bytes each and `DIR1`'s record is next, so
    # `TOPFILE.BIN`'s starts 34 + 34 + 38 bytes into the root extent. That is
    # arithmetic on the fixture's own layout, not on anything the module said.
    f = handle(build())
    root = iso9660.read_root(f)
    assert root is not None
    entry = iso9660.find_entry(f, root, "TOPFILE.BIN")
    assert entry is not None
    dir1_record_length = len(fx.dir_record(b"DIR1", DIR1_LBA, 0, is_dir=True))
    assert entry.record_offset == ROOT_LBA * 2048 + 34 + 34 + dir1_record_length


def test_the_record_offset_names_bytes_that_are_the_records_own_length():
    # Byte 0 of a directory record is its length, so reading the image at the
    # reported offset must find it. This is what makes `record_offset` usable
    # for the length fix-up both games do at +10 and +14.
    image = build()
    f = handle(image)
    root = iso9660.read_root(f)
    assert root is not None
    entry = iso9660.find_entry(f, root, "TOPFILE.BIN")
    assert entry is not None
    expected = len(fx.dir_record(b"TOPFILE.BIN;1", 0, 0, is_dir=False))
    assert image[entry.record_offset] == expected


def test_the_little_endian_length_at_plus_ten_is_the_size_the_entry_reports():
    image = build()
    f = handle(image)
    root = iso9660.read_root(f)
    assert root is not None
    entry = iso9660.find_entry(f, root, "TOPFILE.BIN")
    assert entry is not None
    assert struct.unpack_from("<I", image, entry.record_offset + 10)[0] == TOPFILE_SIZE


def test_the_big_endian_length_at_plus_fourteen_is_the_same_number():
    image = build()
    f = handle(image)
    root = iso9660.read_root(f)
    assert root is not None
    entry = iso9660.find_entry(f, root, "TOPFILE.BIN")
    assert entry is not None
    assert struct.unpack_from(">I", image, entry.record_offset + 14)[0] == TOPFILE_SIZE


def test_find_entry_crosses_a_sector_boundary_within_one_directory():
    # DIR2 spans two sectors, so the padding after its last record is a run of
    # zero-length records. A scan that stopped at the first of them would find
    # `TARGET.BIN` -- so the entry looked for here is one placed deliberately in
    # the *second* sector.
    dir2 = bytearray(fx.directory_extent(DIR2_LBA, DIR1_LBA, _dir2_records(), sectors=2))
    late = fx.dir_record(b"LATE.BIN;1", 40, 4096, is_dir=False)
    dir2[fx.SECTOR_SIZE : fx.SECTOR_SIZE + len(late)] = late
    image = bytearray(build(dir2_sectors=2, dir2_declared=2 * fx.SECTOR_SIZE))
    image[DIR2_LBA * fx.SECTOR_SIZE : DIR2_LBA * fx.SECTOR_SIZE + len(dir2)] = dir2

    f = handle(bytes(image))
    directory = iso9660.walk(f, iso9660.Extent(ROOT_LBA, fx.SECTOR_SIZE), ["DIR1", "DIR2"])
    assert directory is not None
    entry = iso9660.find_entry(f, directory, "LATE.BIN")
    assert entry is not None
    assert entry.extent.lba == 40


def test_find_entry_stops_at_a_sector_boundary_that_is_the_end_of_the_extent():
    # One sector, all padding after the records: the jump to the next boundary
    # would land outside the extent, and the scan must break rather than loop.
    f = handle(build())
    directory = iso9660.walk(f, iso9660.Extent(ROOT_LBA, fx.SECTOR_SIZE), ["DIR1", "DIR2"])
    assert directory is not None
    assert iso9660.find_entry(f, directory, "LATE.BIN") is None


def test_find_entry_ignores_a_record_lying_outside_the_declared_extent():
    # DIR2's declared length stops before `ZZPAD.BIN`'s record. The bytes are
    # still on the disc; the directory does not claim them.
    records = _dir2_records()
    cut = fx.used_length(records) - len(records[-1])
    f = handle(build(dir2_declared=cut))
    directory = iso9660.walk(f, iso9660.Extent(ROOT_LBA, fx.SECTOR_SIZE), ["DIR1", "DIR2"])
    assert directory is not None
    assert iso9660.find_entry(f, directory, "ZZPAD.BIN") is None


def test_find_entry_still_finds_the_record_before_the_declared_cut():
    records = _dir2_records()
    cut = fx.used_length(records) - len(records[-1])
    f = handle(build(dir2_declared=cut))
    directory = iso9660.walk(f, iso9660.Extent(ROOT_LBA, fx.SECTOR_SIZE), ["DIR1", "DIR2"])
    assert directory is not None
    entry = iso9660.find_entry(f, directory, "TARGET.BIN")
    assert entry is not None
    assert entry.extent.lba == TARGET_LBA


def test_find_entry_finds_a_record_ending_flush_with_the_end_of_the_extent():
    # Declared length exactly the records, so the last one ends at `len(data)`.
    # `pos + rec_len > len` and not `>=` is what keeps it.
    f = handle(build(dir2_declared=fx.used_length(_dir2_records())))
    directory = iso9660.walk(f, iso9660.Extent(ROOT_LBA, fx.SECTOR_SIZE), ["DIR1", "DIR2"])
    assert directory is not None
    entry = iso9660.find_entry(f, directory, "ZZPAD.BIN")
    assert entry is not None
    assert entry.extent.lba == ZZPAD_LBA


def test_find_entry_stops_at_a_record_claiming_more_bytes_than_the_extent_holds():
    # `TARGET.BIN` is first and declares a length running past the extent, so
    # the scan breaks there and never reaches `ZZPAD.BIN`.
    f = handle(build(dir2_declared=fx.used_length(_dir2_records()), target_rec_len=250))
    directory = iso9660.walk(f, iso9660.Extent(ROOT_LBA, fx.SECTOR_SIZE), ["DIR1", "DIR2"])
    assert directory is not None
    assert iso9660.find_entry(f, directory, "ZZPAD.BIN") is None


def test_a_short_final_record_is_bounded_rather_than_raising_index_error():
    # THE REGRESSION THIS MODULE'S BOUND EXISTS FOR. A record declaring a length
    # under 33 in the last bytes of an extent passes `pos + rec_len > len` and
    # then byte `pos + 32` is outside the data. Upstream raised `IndexError`
    # here, invisibly, under a blanket `except Exception`.
    #
    # 20 bytes of extent past the two mandatory records, and a record claiming
    # to be 4 bytes long, so `pos + 33` is 15 bytes past the end.
    stub = bytearray(20)
    stub[0] = 4
    extent = bytearray(fx.directory_extent(DIR2_LBA, DIR1_LBA, []))
    extent[68 : 68 + len(stub)] = stub
    image = bytearray(build(dir2_declared=68 + len(stub)))
    image[DIR2_LBA * fx.SECTOR_SIZE : DIR2_LBA * fx.SECTOR_SIZE + len(extent)] = extent

    f = handle(bytes(image))
    directory = iso9660.walk(f, iso9660.Extent(ROOT_LBA, fx.SECTOR_SIZE), ["DIR1", "DIR2"])
    assert directory is not None
    assert iso9660.find_entry(f, directory, "ANYTHING") is None


def test_find_entry_over_a_directory_extent_the_image_does_not_reach():
    # A short read is left short and the scan is bounded by what it got, which
    # is how a truncated image answers "not found" rather than raising.
    image = build()[: DIR2_LBA * fx.SECTOR_SIZE]
    f = handle(image)
    assert iso9660.find_entry(f, iso9660.Extent(DIR2_LBA, fx.SECTOR_SIZE), "TARGET.BIN") is None


def test_find_entry_over_an_extent_of_declared_length_zero_finds_nothing():
    f = handle(build())
    assert iso9660.find_entry(f, iso9660.Extent(DIR2_LBA, 0), "TARGET.BIN") is None


def test_find_entry_decodes_a_non_ascii_name_without_raising():
    # A record whose name is not ASCII is walked past, not fatal. The record
    # after it must still be found, which is the claim.
    weird = fx.dir_record(b"\xff\xfe.BIN;1", 44, 16, is_dir=False)
    records = [weird] + _dir2_records()
    extent = fx.directory_extent(DIR2_LBA, DIR1_LBA, records)
    image = bytearray(build(dir2_declared=fx.used_length(records)))
    image[DIR2_LBA * fx.SECTOR_SIZE : DIR2_LBA * fx.SECTOR_SIZE + len(extent)] = extent

    f = handle(bytes(image))
    directory = iso9660.walk(f, iso9660.Extent(ROOT_LBA, fx.SECTOR_SIZE), ["DIR1", "DIR2"])
    assert directory is not None
    entry = iso9660.find_entry(f, directory, "TARGET.BIN")
    assert entry is not None
    assert entry.extent.lba == TARGET_LBA


def test_find_entry_returns_the_first_of_two_records_with_one_name():
    duplicate = fx.dir_record(b"TARGET.BIN;1", 44, 16, is_dir=False)
    records = _dir2_records() + [duplicate]
    extent = fx.directory_extent(DIR2_LBA, DIR1_LBA, records)
    image = bytearray(build(dir2_declared=fx.used_length(records)))
    image[DIR2_LBA * fx.SECTOR_SIZE : DIR2_LBA * fx.SECTOR_SIZE + len(extent)] = extent

    f = handle(bytes(image))
    directory = iso9660.walk(f, iso9660.Extent(ROOT_LBA, fx.SECTOR_SIZE), ["DIR1", "DIR2"])
    assert directory is not None
    entry = iso9660.find_entry(f, directory, "TARGET.BIN")
    assert entry is not None
    assert entry.extent.lba == TARGET_LBA


# ──────────────────────────────────────────────────────────────
# walk
# ──────────────────────────────────────────────────────────────


def test_walk_descends_two_directories():
    f = handle(build())
    root = iso9660.read_root(f)
    assert root is not None
    assert iso9660.walk(f, root, ["DIR1", "DIR2"]) == iso9660.Extent(
        lba=DIR2_LBA, size=fx.SECTOR_SIZE
    )


def test_walk_descends_one_directory():
    # The depth `nhl05-ps2` uses. Different answer from the two-deep walk, which
    # is what says the parameter is read rather than a constant.
    f = handle(build())
    root = iso9660.read_root(f)
    assert root is not None
    assert iso9660.walk(f, root, ["DIR1"]) == iso9660.Extent(lba=DIR1_LBA, size=fx.SECTOR_SIZE)


def test_walk_with_no_names_returns_the_extent_it_started_from():
    f = handle(build())
    root = iso9660.read_root(f)
    assert root is not None
    assert iso9660.walk(f, root, []) == root


def test_walk_returns_none_when_a_component_is_missing():
    f = handle(build())
    root = iso9660.read_root(f)
    assert root is not None
    assert iso9660.walk(f, root, ["DIR1", "NOPE"]) is None


def test_walk_returns_none_when_a_component_is_a_file_rather_than_a_directory():
    # `MID.BIN` exists in DIR1 and is not a directory. A walk that ignored the
    # flag would read its contents as directory records.
    f = handle(build())
    root = iso9660.read_root(f)
    assert root is not None
    assert iso9660.walk(f, root, ["DIR1", "MID.BIN"]) is None


def test_walk_refuses_a_directory_whose_record_has_the_directory_flag_clear():
    # Same name, same extent, one bit different: the flag is what decides.
    f = handle(build(dir2_is_dir=False))
    root = iso9660.read_root(f)
    assert root is not None
    assert iso9660.walk(f, root, ["DIR1", "DIR2"]) is None


def test_walk_matches_component_names_case_insensitively():
    f = handle(build())
    root = iso9660.read_root(f)
    assert root is not None
    assert iso9660.walk(f, root, ["dir1", "dir2"]) == iso9660.Extent(
        lba=DIR2_LBA, size=fx.SECTOR_SIZE
    )


def test_walk_carries_the_declared_size_of_the_directory_it_reaches():
    # DIR1's record declares DIR2's length, and `walk` must return that rather
    # than assume a sector.
    f = handle(build(dir2_declared=777))
    root = iso9660.read_root(f)
    assert root is not None
    assert iso9660.walk(f, root, ["DIR1", "DIR2"]) == iso9660.Extent(lba=DIR2_LBA, size=777)


# ──────────────────────────────────────────────────────────────
# find_entry_with_next_lba
# ──────────────────────────────────────────────────────────────


def _dir2(image: bytes) -> tuple[io.BytesIO, iso9660.Extent]:
    f = handle(image)
    directory = iso9660.walk(f, iso9660.Extent(ROOT_LBA, fx.SECTOR_SIZE), ["DIR1", "DIR2"])
    assert directory is not None
    return f, directory


def test_find_entry_with_next_lba_reports_the_entrys_own_extent():
    f, directory = _dir2(build())
    found = iso9660.find_entry_with_next_lba(f, directory, "TARGET.BIN")
    assert found is not None
    assert found[0].extent == iso9660.Extent(lba=TARGET_LBA, size=TARGET_SIZE)


def test_find_entry_with_next_lba_reports_the_next_files_lba():
    f, directory = _dir2(build())
    found = iso9660.find_entry_with_next_lba(f, directory, "TARGET.BIN")
    assert found is not None
    assert found[1] == ZZPAD_LBA


def test_the_gap_to_the_next_file_is_larger_than_the_files_own_length():
    # Guards against zero-over-zero: with a gap of 0 the next LBA would equal
    # the sector-aligned end of `TARGET.BIN` and a reader that returned either
    # would pass. Here they differ by exactly `GAP_SECTORS`.
    f, directory = _dir2(build())
    found = iso9660.find_entry_with_next_lba(f, directory, "TARGET.BIN")
    assert found is not None
    assert found[1] - (TARGET_LBA + TARGET_SECTORS) == GAP_SECTORS


def test_find_entry_with_next_lba_sorts_by_position_not_by_directory_order():
    # `ZZPAD.BIN` is listed first and still sits later on the disc, so the
    # answer must be the same as when it is listed last.
    f, directory = _dir2(build(dir2_swap=True))
    found = iso9660.find_entry_with_next_lba(f, directory, "TARGET.BIN")
    assert found is not None
    assert found[1] == ZZPAD_LBA


def test_find_entry_with_next_lba_reports_zero_for_the_last_file_on_the_disc():
    f, directory = _dir2(build())
    found = iso9660.find_entry_with_next_lba(f, directory, "ZZPAD.BIN")
    assert found is not None
    assert found[1] == 0


def test_find_entry_with_next_lba_excludes_the_self_record():
    # `.` points at DIR2 itself, at LBA 19, below both files. Sorted in, it
    # would be the entry preceding `TARGET.BIN` -- and `ZZPAD.BIN`'s next LBA
    # would stop being 0. This is the claim `name_len > 1` makes.
    f, directory = _dir2(build())
    assert iso9660.find_entry_with_next_lba(f, directory, "\x00") is None


def test_find_entry_with_next_lba_returns_none_for_a_name_that_is_not_there():
    f, directory = _dir2(build())
    assert iso9660.find_entry_with_next_lba(f, directory, "ABSENT.BIN") is None


def test_find_entry_with_next_lba_includes_a_subdirectory_as_a_neighbour():
    # A subdirectory's extent occupies the disc as much as a file's. DIR1 holds
    # DIR2 at 18 and MID.BIN at 25, so DIR2 is what precedes MID.BIN -- and a
    # rewrite of something at 18 that grew would land on nothing at all if
    # directories were excluded from the sort.
    f = handle(build())
    found = iso9660.find_entry_with_next_lba(f, iso9660.Extent(DIR1_LBA, fx.SECTOR_SIZE), "DIR2")
    assert found is not None
    assert found[1] == MIDFILE_LBA


def test_find_entry_with_next_lba_matches_case_insensitively():
    f, directory = _dir2(build())
    found = iso9660.find_entry_with_next_lba(f, directory, "target.bin")
    assert found is not None
    assert found[0].extent.lba == TARGET_LBA


def test_find_entry_with_next_lba_carries_the_record_offset():
    # The same record `find_entry` reports, reached by a different scan.
    f, directory = _dir2(build())
    plain = iso9660.find_entry(f, directory, "TARGET.BIN")
    found = iso9660.find_entry_with_next_lba(f, directory, "TARGET.BIN")
    assert plain is not None
    assert found is not None
    assert found[0] == plain


# ──────────────────────────────────────────────────────────────
# Reading a located file
# ──────────────────────────────────────────────────────────────


def test_the_located_extent_names_the_bytes_the_file_was_built_from():
    # End to end: PVD, two directories, a file, and the bytes at the reported
    # offset are the ones the fixture wrote. `TARGET_SIZE` is deliberately not
    # sector-aligned, so a reader that rounded up would read seven bytes of
    # padding and fail this.
    image = build()
    f = handle(image)
    root = iso9660.read_root(f)
    assert root is not None
    directory = iso9660.walk(f, root, ["DIR1", "DIR2"])
    assert directory is not None
    entry = iso9660.find_entry(f, directory, "TARGET.BIN")
    assert entry is not None
    assert image[entry.extent.offset : entry.extent.end] == TARGET_BYTES


def test_the_next_file_on_the_disc_is_untouched_by_reading_this_one():
    image = build()
    assert image[ZZPAD_LBA * 2048 : ZZPAD_LBA * 2048 + ZZPAD_SIZE] == ZZPAD_BYTES


# ──────────────────────────────────────────────────────────────
# The module's place in the package
# ──────────────────────────────────────────────────────────────


def test_the_module_declares_no_public_surface():
    # `formats/` is not public API and states that it declares no `__all__`.
    assert hasattr(iso9660, "__all__") is False


def _imported_modules() -> set[str]:
    """Every module this one imports, parsed rather than matched as text.

    A substring search over the source would also hit the word `games` in a
    docstring, which is where this module's several references to `games/we2002`
    and `games/nhl05_ps2` live.
    """
    assert iso9660.__file__ is not None
    tree = ast.parse(pathlib.Path(iso9660.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if type(node) is ast.Import:
            names.update(alias.name for alias in node.names)
        elif type(node) is ast.ImportFrom and node.module is not None:
            names.add(node.module)
    return names


def test_the_module_imports_nothing_but_the_standard_library():
    # `formats/__init__.py` forbids reaching into `games/`, and this library has
    # zero runtime dependencies. Both are one assertion: every import here is a
    # stdlib top-level name.
    assert _imported_modules() == {
        "__future__",
        "struct",
        "collections.abc",
        "dataclasses",
        "typing",
    }


def test_the_module_imports_nothing_relative():
    # A relative import has `node.module` of None or a non-empty `level`, and
    # neither would appear above. Stated separately because the set comparison
    # would silently pass if `_imported_modules` dropped them.
    assert iso9660.__file__ is not None
    tree = ast.parse(pathlib.Path(iso9660.__file__).read_text(encoding="utf-8"))
    relative = [node for node in ast.walk(tree) if type(node) is ast.ImportFrom and node.level != 0]
    assert relative == []


@pytest.mark.parametrize("name", ["read_root", "find_entry", "walk", "find_entry_with_next_lba"])
def test_every_public_helper_takes_a_file_object_and_never_a_path(name):
    # The design decision this module exists to record: `BinaryIO`, so a test
    # can hand it an `io.BytesIO` and no real image is needed. A `Path`
    # parameter would have made every test need a file on disk.
    #
    # `from __future__ import annotations` leaves annotations as strings, which
    # is why this compares text rather than a type object.
    first = list(inspect.signature(getattr(iso9660, name)).parameters)[0]
    assert getattr(iso9660, name).__annotations__[first] == "BinaryIO"
