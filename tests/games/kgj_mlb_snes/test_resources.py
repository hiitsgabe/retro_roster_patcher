"""The resource archive: directory entries and the codecs, on hand-made streams.

Each expected output is worked out from the format, not produced by the
decoder: a unit's command byte, its masks, then its literals.
"""

from __future__ import annotations

import struct

import pytest

from retro_roster_patcher.games.kgj_mlb_snes import resources

#: Directory fields (transcribed): codec words, offsets from file 0x78000, lengths.
DIR_CODEC, DIR_OFFSET, DIR_LENGTH, BASE = 0x30000, 0x31460, 0x33D20, 0x78000


def _rom(entry: int, codec: int, stream: bytes) -> bytearray:
    rom = bytearray(0x80000)
    at = BASE + 0x100
    rom[at : at + len(stream)] = stream
    struct.pack_into("<H", rom, DIR_CODEC + 2 * entry, codec)
    struct.pack_into("<I", rom, DIR_OFFSET + 4 * entry, at - BASE)
    struct.pack_into("<H", rom, DIR_LENGTH + 2 * entry, len(stream))
    return rom


def _load(codec: int, stream: bytes) -> bytes:
    return resources.load(_rom(7, codec, stream), 7)


def test_entry_reads_the_three_directory_fields():
    rom = _rom(1234, 4, b"\x00" * 17)
    assert resources.entry(rom, 1234) == (4, 17, BASE + 0x100)


def test_store_writes_the_data_and_repoints_the_entry():
    rom = _rom(9, 4, b"\x00" * 17)
    end = resources.store(rom, 9, b"abc", 0x7A000)
    assert end == 0x7A003
    assert resources.entry(rom, 9) == (6, 3, 0x7A000)
    assert resources.load(rom, 9) == b"abc"
    # The offset is a dword: a 4 MiB image's top bank is reachable.
    big = bytearray(0x400000)
    resources.set_entry(big, 9, 6, 2, 0x3FFF00)
    assert struct.unpack_from("<I", big, DIR_OFFSET + 36)[0] == 0x3FFF00 - BASE


def test_a_plain_unit_takes_zero_ff_and_fill_masks_then_literals():
    stream = bytes([0x07, 0x03, 0x00, 0x0C, 0x00, 0x5A, 0xF0, 0x00, *range(1, 9)])
    assert _load(4, stream) == bytes([0, 0, 0xFF, 0xFF, 0x5A, 0x5A, 0x5A, 0x5A, *range(1, 9)])


def test_a_one_plane_unit_draws_two_colours():
    # Set bits colour 2 (high plane), clear bits colour 1 (low plane).
    planes = [0xF0, 0x0F, 0xAA, 0x55, 0x00, 0xFF, 0x81, 0x7E]
    out = _load(4, bytes([0x49, *planes]))
    assert out == b"".join(bytes([~p & 0xFF, p]) for p in planes)


def test_a_one_plane_unit_can_force_rows_clear_or_set():
    # Colour 3 on 0: row 0 forced clear, row 1 forced set, six literal rows.
    out = _load(4, bytes([0x7C, 0x01, 0x02, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66]))
    assert out == bytes(
        [0, 0, 0xFF, 0xFF, 0x11, 0x11, 0x22, 0x22, 0x33, 0x33]
        + [0x44] * 2
        + [0x55] * 2
        + [0x66] * 2
    )


def test_a_reference_unit_copies_flips_and_patches_an_earlier_one():
    first = bytes(range(16))
    stream = (
        b"\x00"
        + first
        + b"\x11\x00\x00"  # unit 0, rows upside down
        + b"\x12\x00\x00"  # unit 0, each byte mirrored
        + b"\x30\x00\x00\x01\x80\xaa\xbb"  # unit 0, bytes 0 and 15 replaced
    )
    out = _load(4, stream)
    assert out[:16] == first
    assert out[16:32] == b"".join(first[2 * r : 2 * r + 2] for r in range(7, -1, -1))
    assert out[32:48] == bytes(int(f"{b:08b}"[::-1], 2) for b in first)
    assert out[48:64] == b"\xaa" + first[1:15] + b"\xbb"


def test_a_rotated_unit_turns_a_quarter():
    # Row 0's leftmost pixel (plane 0) ends up as row 7's leftmost.
    out = _load(4, b"\x80\x80" + bytes(15))
    assert out == bytes(14) + b"\x80\x00"


def test_the_run_length_codecs():
    runs = bytearray(32)
    runs[0] = 0x20  # 0x05 is a run value
    assert (
        _load(0, bytes(runs) + bytes([5, 3, 7, 8, 5, 0]))
        == b"\x05" * 3 + b"\x07\x08" + b"\x05" * 256
    )
    assert _load(2, bytes([0xEE, 0x01, 0xEE, 0x41, 0x04, 0x02])) == b"\x01\x41\x41\x41\x41\x02"
    stream = bytes([0xAA, 0xBB, 0x01, 0x02, 0xAA, 0xBB, 0x34, 0x12, 0x03, 0x05, 0x06])
    assert _load(8, stream) == b"\x01\x02" + b"\x34\x12" * 3 + b"\x05\x06"


def test_the_word_lz_codec_is_refused():
    with pytest.raises(ValueError, match="codec 10"):
        _load(10, b"\x00\x00")
