"""Screen art slots: export the current art as PNG, import a PNG back into the
blocks and CLUTs.

The disc is the synthetic one `test_flag_atlas` builds; here it carries a
TITLE-like file (three 4bpp blocks and two 16-colour CLUT records) and a
DAT2D-like file holding CLUT records only. Pillow is the optional `images`
extra, so everything that renders is gated on it.
"""

import io
import struct

import pytest

from retro_roster_patcher.core.errors import RomError
from retro_roster_patcher.games.we2002 import flag_atlas as fa
from retro_roster_patcher.games.we2002 import screen_art as sa
from retro_roster_patcher.games.we2002.flag_atlas import lz_compress, lz_decompress
from retro_roster_patcher.games.we2002.screen_art import SLOTS, Block, Piece, Slot
from tests.games.we2002.test_flag_atlas import BASE, _build_disc, _read_file

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def _record(kind, x, y, w, h, ptr):
    return struct.pack("<HHHHHHI", kind, x, y, w, h, 0, ptr)


def _title_like(blocks, cluts, base=BASE):
    """A data file: list 0 = compressed 4bpp blocks, list 1 = raw 16-colour CLUTs.

    `blocks`: [(x, y, raw_bytes)] each 32x128 halfwords; `cluts`: [(x, y, bytes32)].
    """
    header_len = 8
    body = bytearray()
    offsets = []
    for _x, _y, raw in blocks:
        offsets.append(header_len + len(body))
        data = lz_compress(raw)
        body += data + b"\x00" * (-len(data) % 4)
    clut_offsets = []
    for _x, _y, data in cluts:
        clut_offsets.append(header_len + len(body))
        body += data
    list0 = header_len + len(body)
    body += b"".join(
        _record(fa.KIND_IMAGE, x, y, 32, 128, base + off)
        for (x, y, _raw), off in zip(blocks, offsets, strict=True)
    ) + _record(fa.KIND_END, 0, 0, 0, 0, 0)
    list1 = header_len + len(body)
    body += b"".join(
        _record(0x09, x, y, 16, 1, base + off)
        for (x, y, _data), off in zip(cluts, clut_offsets, strict=True)
    ) + _record(fa.KIND_END, 0, 0, 0, 0, 0)
    return struct.pack("<II", base + list0, base + list1) + bytes(body)


def _nibbles(fill):
    """A 32x128 block whose every pixel is index `fill`."""
    return bytes([fill | (fill << 4)]) * (64 * 128)


def _clut(colours):
    return b"".join(
        ((r >> 3) | ((g >> 3) << 5) | ((b >> 3) << 10)).to_bytes(2, "little") for r, g, b in colours
    )


TEST_SLOT = Slot(
    name="test",
    file="TITLE.BIN",
    bpp=4,
    width=256,
    height=256,
    blocks=(Block(960, 0, 0, 0), Block(992, 0, 128, 0), Block(960, 128, 0, 128)),
    pieces=(Piece(0, 0, 128, 128, 0), Piece(128, 0, 128, 128, 0), Piece(0, 128, 128, 128, 0)),
    cluts=((("TITLE.BIN", 0, 491), ("TITLE.BIN", 16, 491)),),
)


@pytest.fixture
def disc(tmp_path):
    palette = [(0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)] + [
        (8 * i, 8 * i, 8 * i) for i in range(12)
    ]
    title = _title_like(
        [(960, 0, _nibbles(1)), (992, 0, _nibbles(2)), (960, 128, _nibbles(3))],
        [(0, 491, _clut(palette)), (16, 491, _clut(palette))],
    )
    path = _build_disc(tmp_path, {"TITLE.BIN": (100, title)}, [(100, BASE, 50)])
    return path, title


def test_export_renders_each_block_through_its_clut(disc):
    path, _title = disc
    png = sa.export_slot(str(path), TEST_SLOT)
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    assert img.size == (256, 256)
    assert img.getpixel((10, 10)) == (248, 0, 0, 255)  # index 1: red (5-bit rounded)
    assert img.getpixel((200, 10)) == (0, 248, 0, 255)  # index 2: green
    assert img.getpixel((10, 200)) == (0, 0, 248, 255)  # index 3: blue
    assert img.getpixel((200, 200)) == (0, 0, 0, 0)  # no block there: transparent


def test_import_writes_the_quantised_image_and_the_palette(disc):
    path, title = disc
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    for x in range(256):
        for y in range(256):
            if x < 128 and y < 128:
                img.putpixel((x, y), (255, 255, 0, 255))  # yellow block
            elif x >= 128 and y < 128:
                img.putpixel((x, y), (0, 255, 255, 255))  # cyan block
    buf = io.BytesIO()
    img.save(buf, "PNG")
    blocks, raws = sa.import_slot(str(path), TEST_SLOT, buf.getvalue())
    assert len(raws) == 2 and all(r.file == "TITLE.BIN" for r in raws)
    assert raws[0].data[:2] == b"\x00\x00"  # index 0 stays transparent
    report = fa.rewrite_blocks(str(path), blocks, raws)
    assert report.files == ["TITLE.BIN"]
    with open(path, "rb") as f:
        entry = fa._disc_files(f)["TITLE.BIN"]
    data = _read_file(path, entry.lba, entry.size)
    parsed = fa.parse_data_file(data, BASE)
    recs = {(r.x, r.y): r for r in parsed.records()}
    clut = data[recs[(0, 491)].ptr - BASE : recs[(0, 491)].ptr - BASE + 32]
    colours = [sa._rgb(int.from_bytes(clut[i : i + 2], "little")) for i in range(0, 32, 2)]
    yellow = next(i for i, c in enumerate(colours) if c[0] > 200 and c[1] > 200 and c[2] < 50)
    cyan = next(i for i, c in enumerate(colours) if c[0] < 50 and c[1] > 200 and c[2] > 200)
    first, _ = lz_decompress(data, recs[(960, 0)].ptr - BASE)
    second, _ = lz_decompress(data, recs[(992, 0)].ptr - BASE)
    third, _ = lz_decompress(data, recs[(960, 128)].ptr - BASE)
    assert first == _nibbles(yellow) and second == _nibbles(cyan)
    assert third == _nibbles(0)  # transparent in the PNG: index 0
    # The second CLUT record received the same palette.
    clut2 = data[recs[(16, 491)].ptr - BASE : recs[(16, 491)].ptr - BASE + 32]
    assert clut2 == clut


def test_a_png_of_another_size_is_scaled_to_the_canvas(disc):
    path, _title = disc
    img = Image.new("RGBA", (64, 64), (255, 255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    blocks, raws = sa.import_slot(str(path), TEST_SLOT, buf.getvalue())
    fa.rewrite_blocks(str(path), blocks, raws)
    with open(path, "rb") as f:
        entry = fa._disc_files(f)["TITLE.BIN"]
    data = _read_file(path, entry.lba, entry.size)
    recs = {(r.x, r.y): r for r in fa.parse_data_file(data, BASE).records()}
    values = set()
    for pos in ((960, 0), (992, 0), (960, 128)):
        raw, _ = lz_decompress(data, recs[pos].ptr - BASE)
        assert len(set(raw)) == 1  # one colour everywhere
        values.add(raw[0] & 0xF)
    assert len(values) == 1 and values != {0}  # the same opaque index in every block


def test_a_clear_piece_is_emptied_whatever_the_png_shows(disc):
    path, _title = disc
    slot = Slot(
        name="test",
        file="TITLE.BIN",
        bpp=4,
        width=256,
        height=256,
        blocks=TEST_SLOT.blocks,
        pieces=(Piece(0, 0, 128, 128, 0), Piece(128, 0, 128, 128, 0, clear=True)),
        cluts=TEST_SLOT.cluts,
    )
    img = Image.new("RGBA", (256, 256), (255, 255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    blocks, raws = sa.import_slot(str(path), slot, buf.getvalue())
    fa.rewrite_blocks(str(path), blocks, raws)
    with open(path, "rb") as f:
        entry = fa._disc_files(f)["TITLE.BIN"]
    data = _read_file(path, entry.lba, entry.size)
    recs = {(r.x, r.y): r for r in fa.parse_data_file(data, BASE).records()}
    first, _ = lz_decompress(data, recs[(960, 0)].ptr - BASE)
    second, _ = lz_decompress(data, recs[(992, 0)].ptr - BASE)
    assert first != _nibbles(0) and second == _nibbles(0)


def test_a_clut_entry_offset_writes_inside_the_record(disc):
    path, _title = disc
    slot = Slot(
        name="test",
        file="TITLE.BIN",
        bpp=4,
        width=256,
        height=256,
        blocks=TEST_SLOT.blocks,
        pieces=(Piece(0, 0, 128, 128, 0),),
        cluts=((("TITLE.BIN", 0, 491, 4),),),  # the palette sits 4 colours in
    )
    img = Image.new("RGBA", (256, 256), (0, 0, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    blocks, raws = sa.import_slot(str(path), slot, buf.getvalue())
    assert raws[0].offset == 8
    fa.rewrite_blocks(str(path), blocks, raws)
    with open(path, "rb") as f:
        entry = fa._disc_files(f)["TITLE.BIN"]
    data = _read_file(path, entry.lba, entry.size)
    recs = {(r.x, r.y): r for r in fa.parse_data_file(data, BASE).records()}
    off = recs[(0, 491)].ptr - BASE
    assert data[off : off + 8] == _clut(
        [(0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)]
    )  # untouched
    assert data[off + 8 : off + 10] == b"\x00\x00"  # the palette's transparent entry
    assert sa._read_clut(str(path), ("TITLE.BIN", 0, 491, 4), 2)[1][2] > 200  # blue, read back


def test_an_unknown_slot_is_refused(disc):
    path, _title = disc
    with pytest.raises(ValueError):
        sa.apply_images(str(path), {"nope": b""})


def test_a_disc_without_the_slot_file_is_refused(tmp_path):
    path = _build_disc(tmp_path, {}, [])
    with pytest.raises(RomError):
        sa.export_slot(str(path), TEST_SLOT)


def test_the_shipped_slots_cover_their_canvases_consistently():
    for slot in SLOTS.values():
        px = 4 if slot.bpp == 4 else 2
        for b in slot.blocks:
            assert b.cx + 32 * px <= slot.width and b.cy + 128 <= slot.height, (slot.name, b)
        for p in slot.pieces:
            assert p.x + p.w <= slot.width and p.y + p.h <= slot.height, (slot.name, p)
            assert p.group < len(slot.cluts)
        assert slot.cluts and all(slot.cluts)


def test_black_stays_opaque_and_zero_is_transparent():
    assert sa._bgr555((0, 0, 0)) == 0x8000
    assert sa._bgr555((255, 255, 255)) == 0x7FFF
    assert sa._rgb(0x7FFF) == (248, 248, 248)
