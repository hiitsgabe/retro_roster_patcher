"""The licensed kit texture packages of PES 2013 (PS2): cipher, layout, painting,
writing. Everything runs on a package built here with the disc's layout (the
fixture ISO has no entry 7243, and giving it one would cost a 15 MB archive).
"""

from __future__ import annotations

import io
import random
import struct
import zlib

import pytest

from retro_roster_patcher.core.errors import RomError
from retro_roster_patcher.games.pes2013_ps2 import kit_textures as kt
from retro_roster_patcher.games.pes2013_ps2.kits import AWAY, GK_AWAY, GK_HOME, HOME, KitTable
from retro_roster_patcher.games.pes2013_ps2.models import NATIONAL_SLOT_BASE, TEXT_AFS
from retro_roster_patcher.games.pes2013_ps2.textures import clut_swizzle
from tests.fixtures import synthetic_pes2013_iso as fixture

KEYS = struct.pack("<4I", 0x12345678, 0x9ABCDEF0, 0x0FEDCBA9, 0x87654321)
FILL = 7


def texture_header(w: int, h: int, size: int, ident: int) -> bytes:
    header = bytearray(128)
    struct.pack_into("<IIII", header, 0, 0x29857294, 1, size, ident)
    struct.pack_into("<HH", header, 0x14, w, h)
    header[0x19] = 0x13
    return bytes(header)


def build_package(fill: int = FILL) -> bytes:
    """The disc's layout: part table, LOD0 with its CLUT, LOD1, LOD2, one colour."""
    palette = bytearray(1024)
    for i in range(256):
        palette[4 * i : 4 * i + 4] = bytes((i, 255 - i, 7, 0x80 if i else 0))
    head = struct.pack("<IIII", 3, 8, 0x20, 0x204A0).ljust(0x20, b"\0")
    lod0 = texture_header(512, 256, 128 + 1024 + 512 * 256, 0x1004)
    lod0 += clut_swizzle(bytes(palette)) + bytes([fill]) * (512 * 256)
    lod1 = texture_header(256, 128, 128 + 256 * 128, 0x1005) + bytes([fill]) * (256 * 128)
    lod2 = texture_header(64, 32, 128 + 64 * 32, 0x1006) + bytes([fill]) * (64 * 32)
    return head + lod0 + lod1 + lod2


def build_entry(payload: bytes, keys: bytes = KEYS, csize: int = 0) -> bytes:
    stream = kt.deflate_to(payload, csize) if csize else zlib.compress(payload, 9)
    header = struct.pack("<BBBBIII", 0, 6, 1, 0, len(stream), len(payload), 0) + keys
    return kt.encrypt_entry(header, stream)


def png(draw) -> bytes:  # type: ignore[no-untyped-def]
    from PIL import Image

    img = Image.new("RGBA", (512, 256), (0, 0, 0, 0))
    draw(img)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def half_red() -> bytes:
    from PIL import ImageDraw

    return png(lambda img: ImageDraw.Draw(img).rectangle((0, 0, 255, 255), fill=(255, 0, 0, 255)))


def noise() -> bytes:
    rng = random.Random(5)

    def fill(img) -> None:  # type: ignore[no-untyped-def]
        img.putdata(
            [
                (rng.randrange(256), rng.randrange(256), rng.randrange(256), 255)
                for _ in range(512 * 256)
            ]
        )

    return png(fill)


def test_the_cipher_round_trips_and_masks_the_stream():
    payload = build_package()
    entry = build_entry(payload)
    assert kt.decrypt_entry(entry) == payload
    assert entry[0x10:0x20] == KEYS
    assert entry[0x20:0x38] != zlib.compress(payload, 9)[:0x18]
    with pytest.raises(RomError, match="keys"):
        kt.decrypt_entry(build_entry(payload, keys=bytes(16)))
    wrong = bytearray(entry)
    wrong[0x30] ^= 0xFF
    with pytest.raises(RomError):
        kt.decrypt_entry(bytes(wrong))


def test_a_package_parses_into_three_linear_levels_and_rebuilds():
    pytest.importorskip("PIL")
    from PIL import Image

    payload = build_package()
    package = kt.KitPackage.parse(payload)
    assert [(lod.width, lod.height) for lod in package.lods] == [(512, 256), (256, 128), (64, 32)]
    assert package.palette[4 * FILL : 4 * FILL + 4] == bytes((FILL, 255 - FILL, 7, 0x80))
    assert set(package.lods[0].pixels) == {FILL} and package.to_bytes() == payload
    img = Image.open(io.BytesIO(package.to_png()))
    assert img.size == (512, 256) and img.getpixel((300, 100)) == (FILL, 255 - FILL, 7, 255)
    with pytest.raises(RomError, match="Not a kit"):
        kt.KitPackage.parse(b"\0" * 64)
    with pytest.raises(RomError, match="not three"):
        kt.KitPackage.parse(payload[:0x204A0])


def test_paint_fills_every_level_from_the_picture():
    pytest.importorskip("PIL")
    package = kt.KitPackage.parse(build_package())
    assert package.paint(half_red()) is package
    assert len(package.to_bytes()) == kt.PACKAGE_SIZE
    for lod in package.lods:
        w, h = lod.width, lod.height
        left, right = lod.pixels[h // 2 * w + w // 4], lod.pixels[h // 2 * w + 3 * w // 4]
        assert left and package.palette[4 * left : 4 * left + 4] == bytes((255, 0, 0, 0x80))
        assert right == 0  # transparent
    assert package.palette[:4] == bytes(4)
    package.paint(noise(), colours=16)
    assert max(package.lods[0].pixels) <= 16
    # A picture of another size is fitted to the texture.
    from PIL import Image

    small = io.BytesIO()
    Image.new("RGBA", (64, 64), (0, 0, 255, 255)).save(small, "PNG")
    package.paint(small.getvalue())
    assert set(package.lods[2].pixels) == {1}


def test_kit_entries_follow_the_package_order():
    assert [kt.kit_entry(0, k) for k in (GK_HOME, GK_AWAY, HOME, AWAY)] == [7243, 7246, 7249, 7252]
    assert kt.kit_entry(17, HOME) == 7453


class Stub:
    """Reader and writer in one: the fixture's kit table, one package entry."""

    def __init__(self, entry: bytes = b"", licensed_nation: bool = False) -> None:
        self.entry = entry
        self.written: tuple[int, bytes] | None = None
        self.table = KitTable.parse(fixture.build_kit_entry())
        if licensed_nation:
            for kit in self.table.nationals[0]:
                kit.params[31] = 0
        self.reader = self

    def kits(self) -> KitTable:
        return self.table

    def read(self, afs: str, index: int) -> bytes:
        assert afs == TEXT_AFS and index == self.index
        return self.entry

    def _write_entry(self, afs: str, index: int, block: bytes) -> None:
        self.written = (index, block)

    @property
    def index(self) -> int:
        return kt.kit_entry(kt.licensed_slots(self)[1], HOME)  # type: ignore[arg-type]  # a stand-in reader


def test_licensed_slots_rank_nations_first_then_clubs():
    stub = Stub()
    assert kt.licensed_slots(stub) == {1: 0}  # the fixture's only licensed club
    assert kt.licensed_slots(Stub(licensed_nation=True)) == {NATIONAL_SLOT_BASE: 0, 1: 1}
    with pytest.raises(RomError, match="parametric"):
        kt.read_kit_png(stub, 0, HOME)


def test_deflate_to_pads_a_stream_to_the_exact_size():
    payload = build_package()
    for size in (4000, 5003, 20000):
        stream = kt.deflate_to(payload, size)
        assert len(stream) == size and zlib.decompress(stream) == payload
    with pytest.raises(RomError, match="available"):
        kt.deflate_to(payload, 100)


def test_write_kit_png_keeps_the_entry_as_it_was_and_cuts_the_palette_to_fit():
    pytest.importorskip("PIL")
    entry = build_entry(build_package(), csize=150_000)
    stub = Stub(entry)
    assert kt.write_kit_png(stub, 1, HOME, half_red()) == 255
    index, block = stub.written
    assert index == kt.kit_entry(0, HOME) and len(block) == len(entry)
    assert block[:0x20] == entry[:0x20]  # sizes and keys as the disc had them
    painted = kt.KitPackage.parse(kt.decrypt_entry(block))
    red = painted.lods[0].pixels[100 * 512 + 100]
    assert painted.palette[4 * red : 4 * red + 4] == bytes((255, 0, 0, 0x80))
    # Noise takes a byte a pixel at 255 colours, more than the stream holds.
    stub = Stub(entry)
    assert kt.write_kit_png(stub, 1, HOME, noise()) < 255 and len(stub.written[1]) == len(entry)
    with pytest.raises(RomError, match="16 colours"):
        kt.write_kit_png(Stub(build_entry(build_package(), csize=2000)), 1, HOME, noise())


def test_the_template_is_the_texture_size():
    pytest.importorskip("PIL")
    from PIL import Image

    img = Image.open(io.BytesIO(kt.template_png()))
    assert img.size == kt.LOD0_SIZE
    for x, y, _w, _h in kt.REGIONS.values():
        assert img.getpixel((x, y)) == (255, 0, 0, 255)
