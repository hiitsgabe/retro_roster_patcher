"""`formats.gs`: the GS's swizzled memory layout."""

import os

import pytest

from retro_roster_patcher.formats import gs

BYTES_PER_PIXEL = {"CT32": 4, "CT16": 2, "T8": 1, "T4": 1}


@pytest.mark.parametrize(
    ("fmt", "w", "h"), [("CT32", 64, 32), ("CT16", 128, 64), ("T8", 256, 128), ("T4", 256, 256)]
)
def test_an_upload_reads_back_as_it_went_in(fmt, w, h):
    raster = os.urandom(w * h * BYTES_PER_PIXEL[fmt])
    if fmt == "T4":
        raster = bytes(b & 15 for b in raster)
    assert gs.unpack(fmt, gs.pack(fmt, raster, w, h), w, h) == raster


def test_an_image_narrower_than_a_page_still_round_trips():
    raster = os.urandom(32 * 64)
    assert gs.unpack("T8", gs.pack("T8", raster, 32, 64), 32, 64) == raster


def test_pixels_sit_where_the_hardware_puts_them():
    # The first 8-bit column: 0, 4, 16, 20 along the top row, 8 under the first.
    assert [gs.offset("T8", x, 0, 128) for x in range(4)] == [0, 4, 16, 20]
    assert gs.offset("T8", 0, 1, 128) == 8
    # Pages are 8 KiB whatever the format: the next page row starts there.
    assert gs.offset("CT32", 0, 32, 64) == 2048
    assert gs.offset("T8", 0, 64, 128) == 8192
    assert gs.offset("T4", 0, 128, 128) == 16384
    assert gs.offset("T4", 1, 0, 128) == 8


def test_vertically_adjacent_8bit_pixels_share_a_32bit_column():
    # What NHL 2005's packed pictures rely on: 8-bit texels (x, 2r) and
    # (x, 2r + 1) are the same byte of the two rows of one 32-bit column.
    for x in range(16):
        for y in (0, 2):
            assert gs.offset("T8", x, y + 1, 128) - gs.offset("T8", x, y, 128) == 8


def test_a_raster_of_the_wrong_size_is_refused():
    with pytest.raises(ValueError):
        gs.pack("CT32", bytes(10), 64, 32)
