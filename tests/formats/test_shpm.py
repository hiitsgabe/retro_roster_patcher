"""The SHPM image container, on hand-built files.

Checked on the retail NHL 07 (PSP) disc as well: all 2,282 portraits rebuild
byte for byte, and the 2,270 8-bit ones decode to the research decoder's pixels
and re-encode unchanged.
"""

from __future__ import annotations

import struct

import pytest

from retro_roster_patcher.formats import shpm
from tests.fixtures import synthetic_shpm as synthetic

_block = synthetic.block
_file = synthetic.shpm_file
PALETTE = synthetic.PALETTE
_eight_bit = synthetic.eight_bit_shape
_thirty_two_bit = synthetic.thirty_two_bit_shape


def test_build_reproduces_what_parse_read():
    data = _file(_eight_bit(), _thirty_two_bit())
    parsed = shpm.parse(data)
    assert [len(s.blocks) for s in parsed.shapes] == [2, 1]
    assert shpm.build(parsed) == data


@pytest.mark.parametrize(("w", "h", "bpp"), [(16, 8, 8), (20, 5, 8), (32, 64, 8), (5, 9, 32)])
def test_swizzle_and_unswizzle_are_inverses(w, h, bpp):
    linear = bytes(i % 251 for i in range(w * h * bpp // 8))
    assert shpm.unswizzle(shpm.swizzle(linear, w, h, bpp), w, h, bpp) == linear


def test_swizzle_stores_16_byte_rows_of_8_row_tiles():
    # 32 bytes wide, 8 rows: tile 0 is columns 0-15 of all 8 rows, then tile 1.
    linear = bytes(range(256))
    swizzled = shpm.swizzle(linear, 32, 8, 8)
    assert swizzled[:16] == linear[:16]
    assert swizzled[16:32] == linear[32:48]
    assert swizzled[128:144] == linear[16:32]


def test_an_eight_bit_shape_decodes_through_its_palette():
    pytest.importorskip("PIL")
    (image,) = shpm.decode_shape(shpm.parse(_file(_eight_bit())).shapes[0])
    assert image.mode == "RGBA" and image.size == (16, 8)
    assert image.getpixel((0, 0)) == (10, 20, 30, 255)
    assert image.getpixel((1, 0)) == (200, 100, 50, 255)
    assert image.getpixel((2, 0)) == (0, 0, 0, 0)


def test_an_unedited_eight_bit_shape_reencodes_byte_for_byte():
    pytest.importorskip("PIL")
    data = _file(_eight_bit())
    parsed = shpm.parse(data)
    (image,) = shpm.decode_shape(parsed.shapes[0])
    shpm.encode_shape(parsed.shapes[0], image)
    assert shpm.build(parsed) == data


def test_a_new_image_with_new_colours_decodes_back_unchanged():
    pytest.importorskip("PIL")
    from PIL import Image

    picture = Image.new("RGBA", (16, 8), (255, 0, 0, 255))
    for x in range(8):
        picture.putpixel((x, 3), (0, 0, 255, 128))
    parsed = shpm.parse(_file(_eight_bit()))
    shpm.encode_shape(parsed.shapes[0], picture)
    (image,) = shpm.decode_shape(shpm.parse(shpm.build(parsed)).shapes[0])
    assert image.tobytes() == picture.tobytes()


def test_a_thirty_two_bit_shape_round_trips():
    pytest.importorskip("PIL")
    data = _file(_thirty_two_bit())
    parsed = shpm.parse(data)
    (image,) = shpm.decode_shape(parsed.shapes[0])
    assert image.getpixel((1, 0)) == (10, 254, 7, 255)
    shpm.encode_shape(parsed.shapes[0], image)
    assert shpm.build(parsed) == data


def test_an_image_of_the_wrong_size_is_refused():
    pytest.importorskip("PIL")
    from PIL import Image

    parsed = shpm.parse(_file(_eight_bit()))
    with pytest.raises(ValueError, match="shape is"):
        shpm.encode_shape(parsed.shapes[0], Image.new("RGBA", (8, 8)))


def test_mip_levels_are_rebuilt_from_the_full_image():
    pytest.importorskip("PIL")
    w, h = 16, 16
    linear = bytes((x // 8) for y in range(h) for x in range(w))
    level0 = shpm.swizzle(linear, w, h, 8)
    level1 = shpm.swizzle(bytes(8 * 8), 8, 8, 8)
    shape = _block(shpm.PIX8, w, h, level0 + level1, last=False, py=1 << 12) + _block(
        shpm.PAL32, 256, 1, PALETTE, last=True
    )
    parsed = shpm.parse(_file(shape))
    full, mip = shpm.decode_shape(parsed.shapes[0])
    shpm.encode_shape(parsed.shapes[0], full)
    _, rebuilt = shpm.decode_shape(parsed.shapes[0])
    assert mip.size == rebuilt.size == (8, 8)
    assert rebuilt.getpixel((0, 0)) == (10, 20, 30, 255)
    assert rebuilt.getpixel((7, 7)) == (200, 100, 50, 255)


@pytest.mark.parametrize(
    "data",
    [b"NOPE" + bytes(20), b"SHPM" + struct.pack("<II", 99, 0) + b"G359", b"SHPM"],
)
def test_malformed_files_are_refused(data):
    with pytest.raises(shpm.ShpmError):
        shpm.parse(data)


def test_a_block_chain_running_past_its_shape_is_refused():
    broken = bytearray(_file(_eight_bit()))
    first = struct.unpack_from("<I", broken, 20)[0]  # the one shape's offset
    broken[first + 1 : first + 4] = (10_000).to_bytes(3, "little")
    with pytest.raises(shpm.ShpmError):
        shpm.parse(bytes(broken))


def test_an_unsupported_pixel_block_is_refused_on_decode():
    pytest.importorskip("PIL")
    shape = _block(0x47, 4, 4, bytes(16), last=True)
    with pytest.raises(shpm.ShpmError, match="0x47"):
        shpm.decode_shape(shpm.parse(_file(shape)).shapes[0])
