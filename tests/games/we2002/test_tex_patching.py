"""The ISO9660 / CD-ROM-EDC subsystem behind `RomWriter.flush_tex_patches`.

`we2002/patcher.py` calls `flush_tex_patches` on every patch, and it opens the
user's output image `r+b` and rewrites sectors in place.

WE2002 is copyrighted, so no disc image may enter this repository. Every image
here is built by `_build_iso` out of bytes chosen in this file: a `truncate` to
the full 95-TEX length followed by writes to the six directory sectors and to the
handful of TEX sectors a given test needs. The result is sparse -- a 24 MB
apparent length holding under 100 KB of allocated blocks.

`patch_3d_jersey` and the four in-memory helpers it alone uses --
`_build_tex_dir_map`, `_read_cd_file`, `_write_cd_file_with_edc`,
`_update_iso_dir_size` -- are unreachable from `WE2002Patcher.patch`. They are
still shipped public surface, so they are covered here by direct call rather than
by pretending `patch` drives them.

The `_edc_compute` pins are literals rather than round-trips on purpose. It is a
pure function over bytes, it is the one thing here a console checks, and a disc
whose EDC is wrong is a disc the hardware rejects -- so it is pinned to exact
values that a change of polynomial, of table construction or of shift direction
all move.
"""

import struct

import pytest

from retro_roster_patcher.core.models import MappedRosters
from retro_roster_patcher.games.we2002 import rom_writer as rw
from retro_roster_patcher.games.we2002.models import WETeamRecord
from retro_roster_patcher.games.we2002.patcher import WE2002Patcher
from retro_roster_patcher.games.we2002.rom_writer import (
    _BIN_DIR_LBA,
    _TEX_BASE_LBA,
    _TEX_JERSEY_COLORS,
    _TEX_LBA_STRIDE,
    RomWriter,
    _build_tex_dir_map,
    _build_tex_dir_map_from_dir,
    _edc_compute,
    _find_best_tex_match,
    _read_cd_file,
    _read_cd_file_from_handle,
    _update_iso_dir_size,
    _update_iso_dir_size_in_handle,
    _write_cd_file_with_edc,
    _write_cd_file_with_edc_to_handle,
)

# Mode 2 Form 1 raw sector geometry, as the writer assumes it throughout.
_SECTOR = 2352
_USER_OFF = 24
_USER_LEN = 2048
_EDC_OFF = 2072
_SYNC = b"\x00" + b"\xff" * 10 + b"\x00"

# Two Master League TEX slots with distinct table colours: 66 is the source this
# file patches from and 63 the destination it patches into. Asserted below rather
# than assumed.
_SRC_TEX = 66
_DST_TEX = 63
_SRC_COLOUR = (173, 16, 49)


def _dir_record(name: bytes, size: int, *, be_size=None) -> bytes:
    """One ISO9660 directory record, laid out the way the parser reads it.

    Only the four fields the parser touches carry data: the record length at 0,
    the extent size LE at 10 and BE at 14, the name length at 32 and the name at
    33. Everything else is zero, which is enough — the parser never looks.

    `be_size` puts a different number in the big-endian copy. A conforming disc
    never does that, and no test that patches anything uses it; it exists so that
    one test can show which of the two fields the parser actually reads, which a
    record carrying the same number twice cannot.
    """
    rec = bytearray(33 + len(name))
    struct.pack_into("<I", rec, 10, size)
    struct.pack_into(">I", rec, 14, size if be_size is None else be_size)
    rec[32] = len(name)
    rec[33:] = name
    if len(rec) % 2 == 1:
        rec += b"\x00"
    rec[0] = len(rec)
    return bytes(rec)


def _sector(user: bytes, lba: int) -> bytes:
    """A full 2352-byte Mode 2 Form 1 sector carrying `user`, with a valid EDC."""
    sec = bytearray(_SECTOR)
    sec[0:12] = _SYNC
    sec[12:16] = bytes([lba >> 16 & 0xFF, lba >> 8 & 0xFF, lba & 0xFF, 2])
    sec[16:24] = b"\x01\x02\x03\x04\x01\x02\x03\x04"
    sec[_USER_OFF : _USER_OFF + len(user)] = user
    struct.pack_into("<I", sec, _EDC_OFF, _edc_compute(bytes(sec[16:_EDC_OFF])))
    return bytes(sec)


def _tex_lba(idx: int) -> int:
    return _TEX_BASE_LBA + idx * _TEX_LBA_STRIDE


def _build_iso(tmp_path, tex, *, extra_records=(), listed_sizes=None):
    """A sparse synthetic image whose BIN directory lists `tex` (index -> bytes).

    Records are laid down back to front, so with the default two-entry `tex` the
    *destination* record lands in directory sector 1 and not sector 0. That is
    deliberate: `_build_tex_dir_map*` folds `sector_in_dir` into the absolute
    offset it returns, and a record in sector 0 multiplies that term by zero and
    would hide a wrong one.

    `listed_sizes` declares a slot in the directory as shorter than the bytes
    actually lying in its sectors. That is not a malformed disc — it is what a
    slot looks like after anything has shrunk the file inside its fixed 20-sector
    allocation — and it is the only way to tell a copy of a slot onto itself from
    no copy at all, because such a copy re-pads the tail with zeroes.
    """
    path = tmp_path / "synthetic.bin"
    listed_sizes = listed_sizes or {}
    last = _tex_lba(rw._TEX_COUNT - 1) + _TEX_LBA_STRIDE
    per_sector = {0: list(extra_records), 1: []}
    for n, idx in enumerate(sorted(tex, reverse=True)):
        size = listed_sizes.get(idx, len(tex[idx]))
        per_sector[n % 2].append(_dir_record(b"TEX_%02d.BIN;1" % idx, size))
    with open(path, "wb") as f:
        f.truncate(last * _SECTOR)
        for s, records in per_sector.items():
            f.seek((_BIN_DIR_LBA + s) * _SECTOR)
            f.write(_sector(b"".join(records), _BIN_DIR_LBA + s))
        for idx, data in tex.items():
            base = _tex_lba(idx)
            # Format every sector of the slot, not only the ones the file fills:
            # a real disc has no holes, and the writer only ever rewrites the user
            # area and the EDC, never the sync bytes.
            for n in range(_TEX_LBA_STRIDE):
                f.seek((base + n) * _SECTOR)
                f.write(_sector(data[n * _USER_LEN : (n + 1) * _USER_LEN], base + n))
    return path


def _tex_payload(nbytes: int) -> bytes:
    """`nbytes` of non-zero, position-dependent filler.

    No zero bytes, so the zero padding `flush_tex_patches` adds to round a file up
    to a sector boundary is distinguishable from the file's own content.
    """
    return bytes(1 + (i * 7 + i // 251) % 255 for i in range(nbytes))


def _read_sector(path, lba: int) -> bytes:
    with open(path, "rb") as f:
        f.seek(lba * _SECTOR)
        return f.read(_SECTOR)


def _team(colour):
    return WETeamRecord(name="Synthetic", short_name="SYN", kit_home=colour)


def _edc_bitwise(data: bytes) -> int:
    """The same CRC computed one bit at a time, with no lookup table.

    Deliberately a different shape from the implementation: it consumes a byte by
    XOR-ing it into the register and shifting eight times, where `_edc_compute`
    indexes a 256-entry table it builds up front. A table built wrongly, or
    indexed with the wrong byte of the register, agrees with itself and disagrees
    with this.
    """
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xD8018001 if crc & 1 else crc >> 1
    return crc


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"\x01", 0x90910101),
        (b"\x02", 0x91210201),
        (b"A", 0x24904100),
        (b"\x01\x02\x03\x04", 0xD260B723),
        (b"\x04\x03\x02\x01", 0xB52223B5),
        (bytes(range(16)), 0x69C10A08),
        (b"\xff" * 8, 0x23703F00),
    ],
)
def test_the_edc_is_the_cd_rom_polynomial_and_not_some_other_crc32(data, expected):
    # Exact literals, not a round trip: every CRC-32 variant round-trips against
    # itself, so only a fixed value distinguishes 0xD8018001 from 0xEDB88320. The
    # third and fourth cases are the same four bytes in both orders, which is what
    # separates a real CRC from a checksum over the multiset.
    assert _edc_compute(data) == expected


def test_a_full_sector_payload_has_one_pinned_edc():
    # 2056 bytes is exactly the span a sector's EDC covers, so this is the only
    # length the console ever actually feeds the polynomial.
    assert _edc_compute(bytes(range(256)) * 8 + b"12345678") == 0x24D305CC


def test_the_table_driven_edc_agrees_with_a_bitwise_reference_on_a_sector():
    payload = _tex_payload(2056)
    assert _edc_compute(payload) == _edc_bitwise(payload)


def test_the_bitwise_reference_is_not_vacuously_equal_to_everything():
    # Guards the test above from being zero-over-zero: if `_edc_bitwise` returned
    # a constant it would still match a broken `_edc_compute` that did the same.
    assert _edc_bitwise(_tex_payload(2056)) == 0xB36E6497


def test_appending_the_stored_edc_drives_the_check_to_zero():
    # The property CD hardware relies on, and the reason the EDC goes at 2072 in
    # little-endian order: a sector whose 2060 bytes from 16 check to zero is a
    # sector the drive accepts.
    payload = _tex_payload(2056)
    stored = struct.pack("<I", _edc_compute(payload))
    assert _edc_compute(payload + stored) == 0


def test_an_all_zero_sector_payload_checks_to_zero():
    # True, and on its own worthless — a body of `return 0` passes it. It is here
    # only to pin that the register starts at zero rather than at 0xFFFFFFFF,
    # which is the other common CRC-32 convention and would make this 0x38FB2284.
    assert _edc_compute(bytes(2056)) == 0


def test_the_source_and_destination_slots_this_file_uses_are_really_distinct():
    # Every end-to-end test below depends on the colour table steering slot 63 at
    # slot 66. If the table changed, those tests would still pass while silently
    # copying nothing, so this is the assertion that would break first.
    assert _TEX_JERSEY_COLORS[_SRC_TEX] == _SRC_COLOUR


def test_the_destination_slot_does_not_already_hold_the_colour_it_asks_for():
    assert _TEX_JERSEY_COLORS[_DST_TEX] == (156, 33, 33)


def test_an_exact_table_colour_selects_its_own_slot():
    assert _find_best_tex_match(_SRC_COLOUR) == _SRC_TEX


def test_a_colour_between_two_entries_selects_the_nearer_one():
    # (0, 130, 48) is one green step off TEX 0's (0, 128, 48) and nowhere near
    # anything else, so squared distance has to be doing real work to land here.
    assert _find_best_tex_match((0, 130, 48)) == 0


def test_black_resolves_to_the_darkest_slot_in_the_table():
    assert _find_best_tex_match((0, 0, 0)) == 82


def test_a_colour_fourteen_slots_share_resolves_to_the_lowest_of_them():
    # (200, 0, 0) is the table's value for slots 2, 5, 7, 9, 13, 14, 20, 21, 27,
    # 30, 32, 43, 49 and 50 — every distance is zero and only the strict `<` in
    # the scan decides. Loosened to `<=` this answers 50, and every other test in
    # this file still passes.
    assert _find_best_tex_match((200, 0, 0)) == 2


def test_an_empty_colour_table_yields_no_match(monkeypatch):
    # `flush_tex_patches` and `patch_3d_jersey` both branch on `best_idx is None`,
    # and with the shipped 95-entry table that branch can never be taken. Emptying
    # the table is the only way to show the guard is wired to anything.
    monkeypatch.setattr(rw, "_TEX_JERSEY_COLORS", {})
    assert _find_best_tex_match((1, 2, 3)) is None


def _dir_blob(records_by_sector):
    blob = bytearray(_USER_LEN * max(records_by_sector) + _USER_LEN)
    for sector, records in records_by_sector.items():
        joined = b"".join(records)
        blob[sector * _USER_LEN : sector * _USER_LEN + len(joined)] = joined
    return blob


def test_a_tex_record_in_a_later_directory_sector_gets_the_sector_folded_in():
    # The record sits at byte 0 of directory sector 2, so the returned offset must
    # be sector 2's user area exactly. A parser that dropped `sector_in_dir` would
    # answer with sector 0's and pass every test that only used sector 0.
    blob = _dir_blob({2: [_dir_record(b"TEX_63.BIN;1", 4096)]})
    _, offsets = _build_tex_dir_map_from_dir(blob)
    assert offsets[63] == (_BIN_DIR_LBA + 2) * _SECTOR + _USER_OFF


def test_the_size_of_a_tex_record_comes_from_its_little_endian_extent_field():
    blob = _dir_blob({0: [_dir_record(b"TEX_07.BIN;1", 0x00010203)]})
    sizes, _ = _build_tex_dir_map_from_dir(blob)
    assert sizes[7] == 0x00010203


def test_the_big_endian_copy_of_the_size_is_not_the_one_that_is_read():
    # The two fields hold the same number on any real disc, so a record that
    # agrees with itself cannot say which one the parser used. This one disagrees.
    blob = _dir_blob({0: [_dir_record(b"TEX_07.BIN;1", 0x00010203, be_size=0x04050607)]})
    sizes, _ = _build_tex_dir_map_from_dir(blob)
    assert sizes[7] == 0x00010203


def test_a_records_offset_within_its_sector_is_carried_through():
    first = _dir_record(b"OTHER.DAT;1", 99)
    blob = _dir_blob({1: [first, _dir_record(b"TEX_63.BIN;1", 4096)]})
    _, offsets = _build_tex_dir_map_from_dir(blob)
    assert offsets[63] == (_BIN_DIR_LBA + 1) * _SECTOR + _USER_OFF + len(first)


def test_a_file_that_is_not_a_tex_file_is_not_collected():
    blob = _dir_blob({0: [_dir_record(b"OTHER.DAT;1", 99)]})
    sizes, _ = _build_tex_dir_map_from_dir(blob)
    assert sizes == {}


def test_a_tex_index_at_or_past_the_slot_count_is_rejected():
    # 95 is exactly `_TEX_COUNT`, so this pins the boundary rather than some
    # number safely past it.
    blob = _dir_blob({0: [_dir_record(b"TEX_95.BIN;1", 4096)]})
    sizes, _ = _build_tex_dir_map_from_dir(blob)
    assert sizes == {}


def test_the_last_slot_below_the_count_is_accepted():
    blob = _dir_blob({0: [_dir_record(b"TEX_94.BIN;1", 4096)]})
    sizes, _ = _build_tex_dir_map_from_dir(blob)
    assert sizes == {94: 4096}


def test_a_tex_name_whose_index_is_not_a_number_is_skipped_without_raising():
    blob = _dir_blob({0: [_dir_record(b"TEX_XY.BIN;1", 4096)]})
    sizes, _ = _build_tex_dir_map_from_dir(blob)
    assert sizes == {}


def test_a_zero_length_record_jumps_to_the_next_sector_boundary():
    # ISO9660 pads the tail of every directory sector with zeroes, and the parser
    # has to skip to the next 2048 boundary rather than read the padding as a
    # record. Only the second sector's entry can be found at all if it does.
    blob = _dir_blob({0: [_dir_record(b"OTHER.DAT;1", 99)], 1: [_dir_record(b"TEX_63.BIN;1", 512)]})
    sizes, _ = _build_tex_dir_map_from_dir(blob)
    assert sizes == {63: 512}


def test_the_whole_image_parser_agrees_with_the_pre_read_directory_parser(tmp_path):
    # `_build_tex_dir_map` reads the directory sectors out of a whole image and
    # `_build_tex_dir_map_from_dir` is handed them; only the former is reachable
    # from `patch`. Same records, same answer, both fields.
    path = _build_iso(tmp_path, {_DST_TEX: _tex_payload(400), _SRC_TEX: _tex_payload(3064)})
    rom = path.read_bytes()
    from_dir = bytearray()
    for s in range(6):
        off = (_BIN_DIR_LBA + s) * _SECTOR + _USER_OFF
        from_dir.extend(rom[off : off + _USER_LEN])
    assert _build_tex_dir_map(rom) == _build_tex_dir_map_from_dir(from_dir)


def test_the_whole_image_parser_finds_both_slots_the_directory_lists(tmp_path):
    path = _build_iso(tmp_path, {_DST_TEX: _tex_payload(400), _SRC_TEX: _tex_payload(3064)})
    sizes, _ = _build_tex_dir_map(path.read_bytes())
    assert sizes == {_DST_TEX: 400, _SRC_TEX: 3064}


def test_reading_a_file_over_a_handle_skips_the_sector_headers(tmp_path):
    # 3064 bytes spans two sectors, so a reader that ignored the 304 bytes of
    # per-sector overhead would return the right length and the wrong second half.
    payload = _tex_payload(3064)
    path = _build_iso(tmp_path, {_SRC_TEX: payload})
    with open(path, "rb") as f:
        assert _read_cd_file_from_handle(f, _tex_lba(_SRC_TEX), 3064) == payload


def test_reading_stops_at_the_requested_length_inside_a_sector(tmp_path):
    payload = _tex_payload(3064)
    path = _build_iso(tmp_path, {_SRC_TEX: payload})
    with open(path, "rb") as f:
        assert _read_cd_file_from_handle(f, _tex_lba(_SRC_TEX), 2100) == payload[:2100]


def test_the_in_memory_reader_agrees_with_the_handle_reader(tmp_path):
    # `_read_cd_file` is the unreachable twin; same image, same bytes.
    payload = _tex_payload(3064)
    path = _build_iso(tmp_path, {_SRC_TEX: payload})
    assert _read_cd_file(path.read_bytes(), _tex_lba(_SRC_TEX), 3064) == payload


def test_writing_over_a_handle_lands_the_second_sectors_bytes_at_its_user_area(tmp_path):
    path = _build_iso(tmp_path, {_DST_TEX: b""})
    payload = _tex_payload(4096)
    with open(path, "r+b") as f:
        _write_cd_file_with_edc_to_handle(f, _tex_lba(_DST_TEX), payload)
    second = _read_sector(path, _tex_lba(_DST_TEX) + 1)
    assert second[_USER_OFF : _USER_OFF + _USER_LEN] == payload[2048:]


def test_writing_over_a_handle_leaves_a_checkable_edc_in_the_second_sector(tmp_path):
    path = _build_iso(tmp_path, {_DST_TEX: b""})
    with open(path, "r+b") as f:
        _write_cd_file_with_edc_to_handle(f, _tex_lba(_DST_TEX), _tex_payload(4096))
    second = _read_sector(path, _tex_lba(_DST_TEX) + 1)
    assert _edc_compute(second[16 : _EDC_OFF + 4]) == 0


def test_writing_over_a_handle_does_not_disturb_the_sync_bytes(tmp_path):
    path = _build_iso(tmp_path, {_DST_TEX: b""})
    with open(path, "r+b") as f:
        _write_cd_file_with_edc_to_handle(f, _tex_lba(_DST_TEX), _tex_payload(4096))
    assert _read_sector(path, _tex_lba(_DST_TEX))[:12] == _SYNC


def test_writing_over_a_handle_stops_at_the_end_of_the_data(tmp_path):
    # 4096 bytes is two sectors exactly; the third must still hold what the image
    # was built with, or the loop is running one sector long.
    original = _tex_payload(_USER_LEN * 3)
    path = _build_iso(tmp_path, {_DST_TEX: original})
    with open(path, "r+b") as f:
        _write_cd_file_with_edc_to_handle(f, _tex_lba(_DST_TEX), bytes(4096))
    third = _read_sector(path, _tex_lba(_DST_TEX) + 2)
    assert third[_USER_OFF : _USER_OFF + _USER_LEN] == original[_USER_LEN * 2 :]


def test_the_in_memory_writer_produces_the_same_image_as_the_handle_writer(tmp_path):
    # `_write_cd_file_with_edc` is the unreachable twin.
    payload = _tex_payload(4096)
    path = _build_iso(tmp_path, {_DST_TEX: b""})
    rom = bytearray(path.read_bytes())
    _write_cd_file_with_edc(rom, _tex_lba(_DST_TEX), payload)
    with open(path, "r+b") as f:
        _write_cd_file_with_edc_to_handle(f, _tex_lba(_DST_TEX), payload)
    assert bytes(rom) == path.read_bytes()


def test_the_new_size_is_written_little_endian_at_offset_ten(tmp_path):
    # 0x00010203 reads back as 0x03020100 the other way round, so this is a size
    # that cannot pass with the two fields swapped.
    path = _build_iso(tmp_path, {_DST_TEX: _tex_payload(400), _SRC_TEX: _tex_payload(3064)})
    writer = RomWriter(str(path), str(path))
    writer._ensure_tex_cache()
    offset = writer._tex_dir_offsets[_DST_TEX]
    with open(path, "r+b") as f:
        _update_iso_dir_size_in_handle(f, offset, 0x00010203)
    with open(path, "rb") as f:
        f.seek(offset + 10)
        assert struct.unpack("<I", f.read(4))[0] == 0x00010203


def test_the_new_size_is_also_written_big_endian_at_offset_fourteen(tmp_path):
    path = _build_iso(tmp_path, {_DST_TEX: _tex_payload(400), _SRC_TEX: _tex_payload(3064)})
    writer = RomWriter(str(path), str(path))
    writer._ensure_tex_cache()
    offset = writer._tex_dir_offsets[_DST_TEX]
    with open(path, "r+b") as f:
        _update_iso_dir_size_in_handle(f, offset, 0x00010203)
    with open(path, "rb") as f:
        f.seek(offset + 14)
        assert struct.unpack(">I", f.read(4))[0] == 0x00010203


def test_rewriting_a_directory_record_repairs_the_sectors_edc(tmp_path):
    path = _build_iso(tmp_path, {_DST_TEX: _tex_payload(400), _SRC_TEX: _tex_payload(3064)})
    writer = RomWriter(str(path), str(path))
    writer._ensure_tex_cache()
    offset = writer._tex_dir_offsets[_DST_TEX]
    with open(path, "r+b") as f:
        _update_iso_dir_size_in_handle(f, offset, 0x00010203)
    sector = _read_sector(path, offset // _SECTOR)
    assert _edc_compute(sector[16 : _EDC_OFF + 4]) == 0


def test_the_in_memory_size_update_produces_the_same_image(tmp_path):
    # `_update_iso_dir_size` is the unreachable twin.
    path = _build_iso(tmp_path, {_DST_TEX: _tex_payload(400), _SRC_TEX: _tex_payload(3064)})
    writer = RomWriter(str(path), str(path))
    writer._ensure_tex_cache()
    offset = writer._tex_dir_offsets[_DST_TEX]
    rom = bytearray(path.read_bytes())
    _update_iso_dir_size(rom, offset, 0x00010203)
    with open(path, "r+b") as f:
        _update_iso_dir_size_in_handle(f, offset, 0x00010203)
    assert bytes(rom) == path.read_bytes()


def test_a_missing_output_file_leaves_an_empty_cache(tmp_path):
    writer = RomWriter(str(tmp_path / "absent.bin"), str(tmp_path / "absent.bin"))
    writer._ensure_tex_cache()
    assert writer._tex_cache == {}


def test_the_cache_holds_the_original_bytes_of_every_listed_slot(tmp_path):
    payload = _tex_payload(3064)
    path = _build_iso(tmp_path, {_DST_TEX: _tex_payload(400), _SRC_TEX: payload})
    writer = RomWriter(str(path), str(path))
    writer._ensure_tex_cache()
    assert writer._tex_cache[_SRC_TEX] == payload


def test_the_cache_holds_exactly_the_slots_the_directory_lists(tmp_path):
    path = _build_iso(tmp_path, {_DST_TEX: _tex_payload(400), _SRC_TEX: _tex_payload(3064)})
    writer = RomWriter(str(path), str(path))
    writer._ensure_tex_cache()
    assert sorted(writer._tex_cache) == [_DST_TEX, _SRC_TEX]


def test_a_second_call_does_not_re_read_the_image(tmp_path):
    # The guard that makes the cache a cache. `flush_tex_patches` calls it after
    # `write_team` has already rewritten parts of the image, so re-reading would
    # feed it slots that are no longer original.
    payload = _tex_payload(3064)
    path = _build_iso(tmp_path, {_DST_TEX: _tex_payload(400), _SRC_TEX: payload})
    writer = RomWriter(str(path), str(path))
    writer._ensure_tex_cache()
    with open(path, "r+b") as f:
        _write_cd_file_with_edc_to_handle(f, _tex_lba(_SRC_TEX), bytes(4096))
    writer._ensure_tex_cache()
    assert writer._tex_cache[_SRC_TEX] == payload


def _queued_writer(tmp_path, *, tex=None):
    """A writer over a synthetic image with one TEX patch queued by `write_team`.

    The queue is filled the way `patch` fills it — through `write_team`, which
    appends `(63 + slot_index, team.kit_home)` — rather than by poking the list,
    so the index arithmetic is under test too.
    """
    if tex is None:
        tex = {_DST_TEX: _tex_payload(400), _SRC_TEX: _tex_payload(3064)}
    path = _build_iso(tmp_path, tex)
    writer = RomWriter(str(path), str(path))
    writer.write_team(_DST_TEX - 63, _team(_SRC_COLOUR), players=None, include_flag=False)
    return path, writer


def test_write_team_queues_the_master_league_slots_tex_index(tmp_path):
    _, writer = _queued_writer(tmp_path)
    assert writer._pending_tex_patches == [(_DST_TEX, _SRC_COLOUR)]


def test_flushing_copies_the_source_slots_bytes_into_the_destination(tmp_path):
    path, writer = _queued_writer(tmp_path)
    writer.flush_tex_patches()
    with open(path, "rb") as f:
        copied = _read_cd_file_from_handle(f, _tex_lba(_DST_TEX), 3064)
    assert copied == _tex_payload(3064)


def test_flushing_zero_pads_the_destination_to_the_sector_boundary(tmp_path):
    # 3064 bytes is 1016 short of two sectors. The filler has no zero bytes, so a
    # writer that left the destination's old content in the tail would show it.
    path, writer = _queued_writer(tmp_path)
    writer.flush_tex_patches()
    second = _read_sector(path, _tex_lba(_DST_TEX) + 1)
    assert second[_USER_OFF + 1016 : _USER_OFF + _USER_LEN] == bytes(1032)


def test_flushing_leaves_every_touched_sector_with_a_checkable_edc(tmp_path):
    path, writer = _queued_writer(tmp_path)
    writer.flush_tex_patches()
    first = _read_sector(path, _tex_lba(_DST_TEX))
    second = _read_sector(path, _tex_lba(_DST_TEX) + 1)
    assert _edc_compute(first[16 : _EDC_OFF + 4]) == 0
    assert _edc_compute(second[16 : _EDC_OFF + 4]) == 0


def test_flushing_grows_the_destinations_directory_size_to_the_sources(tmp_path):
    # The destination was listed as 400 bytes and the source is 3064. Without this
    # the game reads 400 bytes of a 3064-byte texture.
    path, writer = _queued_writer(tmp_path)
    writer.flush_tex_patches()
    with open(path, "rb") as f:
        f.seek(writer._tex_dir_offsets[_DST_TEX] + 10)
        assert struct.unpack("<I", f.read(4))[0] == 3064


def test_flushing_records_the_new_size_big_endian_too(tmp_path):
    path, writer = _queued_writer(tmp_path)
    writer.flush_tex_patches()
    with open(path, "rb") as f:
        f.seek(writer._tex_dir_offsets[_DST_TEX] + 14)
        assert struct.unpack(">I", f.read(4))[0] == 3064


def test_flushing_empties_the_queue(tmp_path):
    _, writer = _queued_writer(tmp_path)
    writer.flush_tex_patches()
    assert writer._pending_tex_patches == []


def test_flushing_twice_is_not_a_second_copy_of_a_moving_target(tmp_path):
    # The second flush has an empty queue and must be a no-op; if it were not, it
    # would now read the destination it just overwrote.
    path, writer = _queued_writer(tmp_path)
    writer.flush_tex_patches()
    after_first = path.read_bytes()
    writer.flush_tex_patches()
    assert path.read_bytes() == after_first


def test_an_empty_queue_leaves_the_image_untouched(tmp_path):
    path = _build_iso(tmp_path, {_DST_TEX: _tex_payload(400), _SRC_TEX: _tex_payload(3064)})
    before = path.read_bytes()
    writer = RomWriter(str(path), str(path))
    writer.flush_tex_patches()
    assert path.read_bytes() == before


def test_a_missing_output_file_keeps_the_queue_for_later(tmp_path):
    # The guard is `not queue or not exists`, and it returns before the branch
    # that clears the queue, so a writer whose output vanished still holds them.
    path, writer = _queued_writer(tmp_path)
    path.unlink()
    writer.flush_tex_patches()
    assert writer._pending_tex_patches == [(_DST_TEX, _SRC_COLOUR)]


def test_a_directory_listing_no_tex_files_discards_the_queue(tmp_path):
    # Nothing can be patched, and holding the queue would only make a later flush
    # try again against the same directory. This is the branch that clears it.
    path = _build_iso(tmp_path, {}, extra_records=(_dir_record(b"OTHER.DAT;1", 99),))
    writer = RomWriter(str(path), str(path))
    writer.write_team(0, _team(_SRC_COLOUR), players=None, include_flag=False)
    writer.flush_tex_patches()
    assert writer._pending_tex_patches == []


def test_a_directory_listing_no_tex_files_writes_nothing_to_the_slot(tmp_path):
    path = _build_iso(tmp_path, {}, extra_records=(_dir_record(b"OTHER.DAT;1", 99),))
    writer = RomWriter(str(path), str(path))
    writer.write_team(0, _team(_SRC_COLOUR), players=None, include_flag=False)
    before = _read_sector(path, _tex_lba(_DST_TEX))
    writer.flush_tex_patches()
    assert _read_sector(path, _tex_lba(_DST_TEX)) == before


def test_a_queued_slot_the_directory_does_not_list_is_skipped(tmp_path):
    # Slot 63 is queued but only slot 66 is in the directory, so there is nothing
    # to resize and nothing to overwrite.
    path, writer = _queued_writer(tmp_path, tex={_SRC_TEX: _tex_payload(3064)})
    before = _read_sector(path, _tex_lba(_DST_TEX))
    writer.flush_tex_patches()
    assert _read_sector(path, _tex_lba(_DST_TEX)) == before


def test_a_slot_that_is_already_its_own_best_match_is_left_alone(tmp_path):
    """Asking slot 66 for slot 66's own colour: the `best_idx == tex_index` branch.

    The slot holds 4096 bytes but its directory record claims 3064, so a copy of
    the slot onto itself would read back 3064 bytes and re-pad the remaining 1032
    with zeroes. Without that gap between the listed size and the sectors, source
    and destination are the same bytes and dropping the branch is invisible —
    which is exactly what the first version of this test failed to notice.
    """
    path = _build_iso(
        tmp_path,
        {_DST_TEX: _tex_payload(400), _SRC_TEX: _tex_payload(4096)},
        listed_sizes={_SRC_TEX: 3064},
    )
    writer = RomWriter(str(path), str(path))
    writer.write_team(_SRC_TEX - 63, _team(_SRC_COLOUR), players=None, include_flag=False)
    before = _read_sector(path, _tex_lba(_SRC_TEX) + 1)
    writer.flush_tex_patches()
    assert _read_sector(path, _tex_lba(_SRC_TEX) + 1) == before


def test_the_one_shot_jersey_patch_copies_the_matched_slot(tmp_path):
    path = _build_iso(tmp_path, {_DST_TEX: _tex_payload(400), _SRC_TEX: _tex_payload(3064)})
    writer = RomWriter(str(path), str(path))
    writer.patch_3d_jersey(_DST_TEX, _SRC_COLOUR)
    with open(path, "rb") as f:
        assert _read_cd_file_from_handle(f, _tex_lba(_DST_TEX), 3064) == _tex_payload(3064)


def test_the_one_shot_jersey_patch_resizes_the_directory_entry(tmp_path):
    path = _build_iso(tmp_path, {_DST_TEX: _tex_payload(400), _SRC_TEX: _tex_payload(3064)})
    writer = RomWriter(str(path), str(path))
    writer.patch_3d_jersey(_DST_TEX, _SRC_COLOUR)
    with open(path, "rb") as f:
        f.seek(writer._tex_dir_offsets[_DST_TEX] + 10)
        assert struct.unpack("<I", f.read(4))[0] == 3064


def test_the_one_shot_jersey_patch_writes_nothing_for_a_slot_of_its_own_colour(tmp_path):
    # Listed short of what the sectors hold, for the same reason as the
    # `flush_tex_patches` twin of this test: with the two equal, a copy of the
    # slot onto itself and no copy at all produce identical images.
    path = _build_iso(
        tmp_path,
        {_DST_TEX: _tex_payload(400), _SRC_TEX: _tex_payload(4096)},
        listed_sizes={_SRC_TEX: 3064},
    )
    writer = RomWriter(str(path), str(path))
    before = path.read_bytes()
    writer.patch_3d_jersey(_SRC_TEX, _SRC_COLOUR)
    assert path.read_bytes() == before


def test_the_one_shot_jersey_patch_does_no_io_for_a_missing_file(tmp_path):
    absent = tmp_path / "absent.bin"
    writer = RomWriter(str(absent), str(absent))
    writer.patch_3d_jersey(_DST_TEX, _SRC_COLOUR)
    assert absent.exists() is False


def test_a_real_patch_run_rewrites_the_slots_tex_sectors(tmp_path):
    """The claim `test_patcher.py`'s `FakeRomWriter` cannot make.

    `patch` is given the same path for input and output, which takes
    `RomWriter.__init__`'s documented in-place branch and skips the `shutil.copy2`
    of a 101 MB file; without that the copy materialises every hole and this test
    writes 101 MB to disk instead of 100 KB. The image is padded to 101 MB because
    `RomReader.validate_rom` rejects anything under 100 MB and `patch` applies it.
    """
    path = _build_iso(tmp_path, {_DST_TEX: _tex_payload(400), _SRC_TEX: _tex_payload(3064)})
    with open(path, "r+b") as f:
        f.truncate(101 * 1024 * 1024)

    patcher = WE2002Patcher(cache_dir=tmp_path / "cache")
    rosters = MappedRosters(game_id="we2002", teams={_DST_TEX - 63: _team(_SRC_COLOUR)})
    patcher.patch(rom_path=path, output_path=path, rosters=rosters)

    with open(path, "rb") as f:
        assert _read_cd_file_from_handle(f, _tex_lba(_DST_TEX), 3064) == _tex_payload(3064)


def test_a_real_patch_run_leaves_the_rewritten_sector_checkable(tmp_path):
    path = _build_iso(tmp_path, {_DST_TEX: _tex_payload(400), _SRC_TEX: _tex_payload(3064)})
    with open(path, "r+b") as f:
        f.truncate(101 * 1024 * 1024)

    patcher = WE2002Patcher(cache_dir=tmp_path / "cache")
    rosters = MappedRosters(game_id="we2002", teams={_DST_TEX - 63: _team(_SRC_COLOUR)})
    patcher.patch(rom_path=path, output_path=path, rosters=rosters)

    sector = _read_sector(path, _tex_lba(_DST_TEX))
    assert _edc_compute(sector[16 : _EDC_OFF + 4]) == 0


def test_a_real_patch_run_resizes_the_directory_entry(tmp_path):
    path = _build_iso(tmp_path, {_DST_TEX: _tex_payload(400), _SRC_TEX: _tex_payload(3064)})
    with open(path, "r+b") as f:
        f.truncate(101 * 1024 * 1024)

    patcher = WE2002Patcher(cache_dir=tmp_path / "cache")
    rosters = MappedRosters(game_id="we2002", teams={_DST_TEX - 63: _team(_SRC_COLOUR)})
    patcher.patch(rom_path=path, output_path=path, rosters=rosters)

    # Re-read the directory through a fresh writer rather than through
    # `_build_tex_dir_map`, which would pull all 101 MB into memory to find one
    # four-byte field.
    reader = RomWriter(str(path), str(path))
    reader._ensure_tex_cache()
    with open(path, "rb") as f:
        f.seek(reader._tex_dir_offsets[_DST_TEX] + 10)
        assert struct.unpack("<I", f.read(4))[0] == 3064
