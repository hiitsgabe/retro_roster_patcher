"""Screen art: the title stadium and the Nintendo boot screen.

The synthetic ROM is the KGJ fixture with what `screens` reads added: the six
directory entries, each a stored (codec 6) resource shaped like the
cartridge's, and the boot map's LDX operand at 0x1DBF1. Its tiles and colours
are distinct per slot so a test sees what was kept and what was replaced. Its
entries stand in for the retail ones `screens` accepts (`_RETAIL`).
"""

from __future__ import annotations

import io
import random
import struct

import pytest

from retro_roster_patcher.core.errors import CapabilityError, RomError
from retro_roster_patcher.core.models import MappedRosters
from retro_roster_patcher.formats import snes
from retro_roster_patcher.games.kgj_mlb_snes import resources, screens
from retro_roster_patcher.games.kgj_mlb_snes.patcher import KGJMLBPatcher
from tests.fixtures import synthetic_kgj_rom as fixture

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

TITLE = screens.SLOTS_BY_NAME["title"]
BOOT = screens.SLOTS_BY_NAME["boot"]
FOUR_MIB = 0x400000
#: Where the synthetic cartridge keeps its six resources (clear of the
#: directory, the derby tables and the team data).
RETAIL_AT = 0x1E0000


def _retail(slot: screens.Slot) -> tuple[bytes, bytes, bytes]:
    """Tiles, map and palette shaped like the cartridge's for `slot`."""
    if slot is BOOT:
        tiles = b"".join(bytes([n & 0xFF, n >> 8]) * 16 for n in range(176))
        # A 4-row strip of tiles 1-128, as the cartridge's map is, in palette row 0.
        strip = b"".join(struct.pack("<H", 1 + i) for i in range(128))
        return tiles, strip, bytes(0x10 + i for i in range(32))
    tiles = bytes(32 * 215)
    palette = bytes((i * 7) & 0xFF for i in range(256))
    return tiles, bytes(28 * 64), palette


def add_screens(body: bytearray) -> bytearray:
    """`body` (headerless) with the screen resources and the boot poke's bytes."""
    at = RETAIL_AT
    for slot in screens.SLOTS:
        for entry, data in zip(
            (slot.tiles, slot.tile_map, slot.palette), _retail(slot), strict=True
        ):
            at = resources.store(body, entry, data, at)
    body[0x1DBF1:0x1DBF3] = b"\x00\x03"
    return body


def _cartridge() -> bytearray:
    return add_screens(fixture.build_kgj_rom())


_ENTRIES = [e for s in screens.SLOTS for e in (s.tiles, s.tile_map, s.palette)]


@pytest.fixture(autouse=True)
def synthetic_retail(monkeypatch):
    """The synthetic cartridge's entries are the ones `screens` takes as retail."""
    cart = _cartridge()
    monkeypatch.setattr(screens, "_RETAIL", {e: resources.entry(cart, e) for e in _ENTRIES})


def _noise(seed: int = 1) -> Image.Image:
    """A picture with a different 8x8 tile everywhere: more than any budget."""
    rng = random.Random(seed)
    return Image.frombytes(
        "RGB", (256, 224), bytes(rng.randrange(256) for _ in range(256 * 224 * 3))
    )


def _blocks() -> Image.Image:
    """Four flat colours on the SNES's 5-bit grid, one per quarter."""
    image = Image.new("RGB", (256, 224))
    for i, colour in enumerate(((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255))):
        image.paste(
            colour, (128 * (i % 2), 112 * (i // 2), 128 * (i % 2) + 128, 112 * (i // 2) + 112)
        )
    return image


def _words(data: bytes) -> list[int]:
    return [w for (w,) in struct.iter_unpack("<H", data)]


# --- slots -------------------------------------------------------------------


def test_the_slots_are_the_title_and_the_boot_screen():
    assert screens.IMAGE_SLOT_NAMES == ("title", "boot")
    assert KGJMLBPatcher.image_slots == ("title", "boot")


@pytest.mark.parametrize("slot", screens.SLOTS, ids=lambda s: s.name)
def test_every_guide_lies_inside_its_picture(slot):
    w, h = slot.size
    assert slot.guides
    for g in slot.guides:
        assert 0 <= g.x and 0 <= g.y and g.x + g.w <= w and g.y + g.h <= h, g


def test_the_budgets_are_what_video_memory_and_a_map_word_allow():
    assert (TITLE.first_tile, TITLE.budget) == (0, 256)
    assert BOOT.first_tile + BOOT.budget == 1024


def test_each_slot_owns_its_part_of_the_screens_region():
    ends = [s.at + 32 * (s.first_tile + s.budget) + 28 * 64 + 256 for s in screens.SLOTS]
    assert 0x300000 <= TITLE.at < ends[0] <= BOOT.at < ends[1] <= FOUR_MIB


# --- writing -----------------------------------------------------------------


def test_a_title_is_stored_in_the_grown_rom_and_its_entries_repointed():
    rom = _cartridge()
    before = bytes(rom)
    notes = screens.write(rom, {"title": _noise()})
    assert len(rom) == FOUR_MIB and rom[snes.SIZE_BYTE] == 0x0C
    assert notes == [notes[0]] and notes[0].startswith("title: ") and notes[0].endswith(" to 256")
    codec, length, at = resources.entry(rom, TITLE.tiles)
    assert (codec, length, at) == (6, 32 * 256, TITLE.at)
    words = _words(resources.load(rom, TITLE.tile_map))
    assert len(words) == 28 * 32
    assert {w & 0x3FF for w in words} <= set(range(256))
    assert {w >> 10 & 7 for w in words} <= {6, 7}
    assert not any(w & 0x2000 for w in words)  # no priority, as the cartridge's
    palette = resources.load(rom, TITLE.palette)
    assert palette[: 32 * 6] == _retail(TITLE)[2][: 32 * 6]
    # Outside the directory and the size byte, the cartridge is untouched.
    rom[snes.SIZE_BYTE] = before[snes.SIZE_BYTE]
    assert rom[:0x200000] == before[:0x30000] + rom[0x30000:0x35000] + before[0x35000:]
    assert resources.entry(rom, BOOT.tiles)[2] < 0x200000


def test_a_boot_picture_keeps_the_first_176_tiles_and_palette_row_0():
    rom = _cartridge()
    retail_tiles, _map, retail_palette = _retail(BOOT)
    screens.write(rom, {"boot": _noise()})
    tiles = resources.load(rom, BOOT.tiles)
    assert tiles[: 32 * 176] == retail_tiles
    assert len(tiles) == 32 * 1024
    words = _words(resources.load(rom, BOOT.tile_map))
    assert len(words) == 28 * 32
    assert {w & 0x3FF for w in words} <= set(range(176, 1024))
    assert {w >> 10 & 7 for w in words} <= {2, 3, 4, 5, 6, 7}
    palette = resources.load(rom, BOOT.palette)
    assert len(palette) == 256 and palette[:32] == retail_palette
    assert rom[0x1DBF1:0x1DBF3] == b"\x00\x00"  # the map loads at row 0 now
    assert resources.entry(rom, BOOT.tiles)[2] == BOOT.at


def test_a_picture_reads_back_as_drawn():
    rom = _cartridge()
    screens.write(rom, {"title": _blocks(), "boot": _blocks()})
    for slot in screens.SLOTS:
        assert screens.render(rom, slot).tobytes() == _blocks().tobytes(), slot.name


def test_the_cartridge_boot_strip_renders_at_row_12():
    rom = _cartridge()
    picture = screens.render(rom, BOOT)
    rows = [picture.crop((0, 8 * r, 256, 8 * r + 8)).getbbox() for r in range(28)]
    assert [r for r, box in enumerate(rows) if box] == [12, 13, 14, 15]


def test_patching_a_patched_rom_again_gives_the_same_rom():
    once = _cartridge()
    screens.write(once, {"title": _noise(2), "boot": _noise(3)})
    twice = bytearray(once)
    screens.write(twice, {"title": _noise(2), "boot": _noise(3)})
    assert twice == once


def test_unknown_boot_code_is_refused_before_anything_is_written():
    rom = _cartridge()
    rom[0x1DBF1:0x1DBF3] = b"\x00\x04"
    before = bytes(rom)
    with pytest.raises(RomError, match="0x1DBF1"):
        screens.write(rom, {"title": _blocks(), "boot": _blocks()})
    assert rom == before


@pytest.mark.parametrize("entry", _ENTRIES)
def test_an_entry_the_cartridge_does_not_have_is_refused_before_anything_is_written(entry):
    rom = _cartridge()
    codec, length, at = resources.entry(rom, entry)
    resources.set_entry(rom, entry, 10, length, at)  # a codec `resources` does not decode
    before = bytes(rom)
    with pytest.raises(RomError, match=f"resource {entry} "):
        screens.write(rom, {"title": _blocks(), "boot": _blocks()})
    assert rom == before


def test_a_stored_entry_outside_its_slots_part_is_refused():
    rom = _cartridge()
    screens.write(rom, {"title": _blocks()})
    _codec, length, _at = resources.entry(rom, TITLE.palette)
    resources.set_entry(rom, TITLE.palette, resources.STORED, length, BOOT.at)
    with pytest.raises(RomError, match=f"resource {TITLE.palette} "):
        screens.write(rom, {"title": _blocks()})


def test_the_retail_entries_are_the_six_the_slots_use():
    assert sorted(screens._RETAIL) == sorted(_ENTRIES)


def test_a_copier_header_is_kept_and_skipped():
    body = _cartridge()
    data = bytearray(b"\x5a" * 512) + body
    screens.draw(data, {"title": _blocks()})
    screens.write(body, {"title": _blocks()})
    assert data == b"\x5a" * 512 + body


# --- images ------------------------------------------------------------------


def test_load_images_refuses_an_unknown_slot_and_a_non_picture(tmp_path):
    with pytest.raises(CapabilityError, match="Slots: title, boot"):
        screens.load_images({"menu": b""}, print)
    with pytest.raises(CapabilityError, match="not a picture"):
        screens.load_images({"boot": b"not a png"}, print)
    png = io.BytesIO()
    _blocks().save(png, "PNG")
    path = tmp_path / "t.png"
    path.write_bytes(png.getvalue())
    assert set(screens.load_images({"title": path, "boot": png.getvalue()}, print)) == {
        "title",
        "boot",
    }


# --- the patcher ---------------------------------------------------------------


@pytest.fixture
def patcher(tmp_path):
    return KGJMLBPatcher(cache_dir=tmp_path / "cache")


def _png(tmp_path, image, name):
    path = tmp_path / name
    image.save(path)
    return path


@pytest.mark.parametrize("header", [0, 512])
def test_patch_draws_the_images_and_fixes_the_checksum(tmp_path, patcher, header):
    rom = tmp_path / "kgj.sfc"
    rom.write_bytes(b"\x00" * header + _cartridge())
    out = tmp_path / "out.sfc"
    images = {
        "title": _png(tmp_path, _blocks(), "t.png"),
        "boot": _png(tmp_path, _noise(), "b.png"),
    }
    patcher.patch(
        rom_path=rom,
        output_path=out,
        rosters=MappedRosters(game_id=patcher.game_id, teams={}),
        images=images,
    )
    data = out.read_bytes()
    assert len(data) == header + FOUR_MIB
    body = data[header:]
    assert screens.render(body, TITLE).tobytes() == _blocks().tobytes()
    assert resources.entry(body, BOOT.tiles)[2] == BOOT.at
    assert bytes(snes.fix_checksum(body)) == body
    # The grown output is itself a ROM the patcher takes.
    again = tmp_path / "again.sfc"
    patcher.patch(
        rom_path=out,
        output_path=again,
        rosters=MappedRosters(game_id=patcher.game_id, teams={}),
        images={"title": images["title"]},
    )
    assert again.read_bytes() == data


def test_patch_refuses_an_unknown_slot_before_writing(tmp_path, patcher):
    rom = tmp_path / "kgj.sfc"
    rom.write_bytes(_cartridge())
    out = tmp_path / "out.sfc"
    with pytest.raises(CapabilityError):
        patcher.patch(
            rom_path=rom,
            output_path=out,
            rosters=MappedRosters(game_id=patcher.game_id, teams={}),
            images={"menu": b""},
        )
    assert not out.exists()


def test_patch_refuses_an_unknown_cartridge_as_a_rom_error(tmp_path, patcher):
    body = _cartridge()
    _codec, length, at = resources.entry(body, TITLE.tiles)
    resources.set_entry(body, TITLE.tiles, 10, length, at)
    rom = tmp_path / "kgj.sfc"
    rom.write_bytes(body)
    out = tmp_path / "out.sfc"
    with pytest.raises(RomError, match="title screen"):
        patcher.patch(
            rom_path=rom,
            output_path=out,
            rosters=MappedRosters(game_id=patcher.game_id, teams={}),
            images={"title": _png(tmp_path, _blocks(), "t.png")},
        )
    assert not out.exists()


def test_export_images_writes_each_slot(tmp_path, patcher):
    rom = tmp_path / "kgj.sfc"
    rom.write_bytes(b"\x00" * 512 + _cartridge())
    written = patcher.export_images(rom, tmp_path / "art")
    assert [p.name for p in written] == ["title.png", "boot.png"]
    assert all(Image.open(p).size == (256, 224) for p in written)
    with pytest.raises(RomError):
        patcher.export_images(tmp_path / "missing.sfc", tmp_path / "art")
