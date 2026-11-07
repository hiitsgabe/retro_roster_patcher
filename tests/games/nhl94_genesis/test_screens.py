"""NHL 94 (Genesis) screen art: the title and Game Setup's rink, on a synthetic
image laid out like the cartridge (records and the code that loads them)."""

from __future__ import annotations

import io
import struct

import pytest

from retro_roster_patcher.core.errors import CapabilityError, RomError
from retro_roster_patcher.core.models import MappedRosters
from retro_roster_patcher.formats import genesis
from retro_roster_patcher.games.nhl94_genesis import screens
from retro_roster_patcher.games.nhl94_genesis.patcher import NHL94GenesisPatcher
from tests.fixtures import synthetic_rom

Image = pytest.importorskip("PIL.Image")

TITLE, MENU = (screens.SLOTS_BY_NAME[name] for name in ("title", "menu"))


def _cartridge_record(slot) -> bytes:
    """A record like the cartridge's: two tiles, a map using lines 0-2 (title)
    or 0 (menu), and a palette whose every word is its own index + 1."""
    width, height = slot.size[0] // 8, slot.size[1] // 8
    tiles = bytes([0x11] * 32 + [0x23] * 32)
    entries = [(slot.lines[i % len(slot.lines)] << 13) | (i % 2) for i in range(width * height)]
    palette = [i + 1 for i in range(64)]
    return screens.make_record(tiles, palette, width, height, entries)


def _art_rom() -> bytearray:
    """The synthetic NHL 94 image with both records at the cartridge's addresses
    and `movea.l #record,a0` at each place the cartridge loads them."""
    rom = synthetic_rom.build_nhl94_genesis_rom()
    for slot in (TITLE, MENU):
        record = _cartridge_record(slot)
        rom[slot.retail : slot.retail + len(record)] = record
        for site in slot.sites:
            rom[site - 2 : site + 4] = b"\x20\x7c" + struct.pack(">I", slot.retail)
    return rom


def _png(picture) -> bytes:
    out = io.BytesIO()
    picture.save(out, "PNG")
    return out.getvalue()


def _loaded(**pngs):
    return screens.load_images(pngs, lambda line: None)


def test_export_writes_the_title_and_the_menu_at_their_sizes(tmp_path):
    written = screens.export(bytes(_art_rom()), tmp_path)
    assert [p.name for p in written] == ["title.png", "menu.png"]
    assert Image.open(tmp_path / "title.png").size == (256, 224)
    assert Image.open(tmp_path / "menu.png").size == (320, 224)


def test_drawing_the_title_grows_the_image_and_points_both_loads_at_the_new_record():
    rom = _art_rom()
    cartridge = bytes(rom[TITLE.retail : TITLE.retail + 0x1000])
    picture = Image.new("RGB", (256, 224), (200, 40, 40))
    assert screens.draw(rom, _loaded(title=_png(picture)), lambda line: None) == ["title"]
    assert len(rom) == 0x200000
    assert struct.unpack_from(">I", rom, 0x1A4)[0] == 0x1FFFFF
    assert [struct.unpack_from(">I", rom, s)[0] for s in TITLE.sites] == [TITLE.region.start] * 2
    assert bytes(rom[TITLE.retail : TITLE.retail + 0x1000]) == cartridge
    assert [struct.unpack_from(">I", rom, s)[0] for s in MENU.sites] == [MENU.retail]


def test_a_flat_picture_renders_back_in_its_nearest_genesis_colour():
    rom = _art_rom()
    screens.draw(rom, _loaded(menu=_png(Image.new("RGB", (320, 224), (180, 60, 30)))), print)
    shown = screens.render(rom, MENU)
    assert shown.size == (320, 224)
    assert shown.getpixel((100, 100)) == genesis.rgb(genesis.word((180, 60, 30)))


def test_the_title_keeps_the_colours_its_shields_letters_and_credits_use():
    rom = _art_rom()
    picture = Image.effect_noise((256, 224), 90).convert("RGB")
    screens.draw(rom, _loaded(title=_png(picture)), lambda line: None)
    palette, *_ = screens.record(rom, TITLE.region.start)
    kept = [0] + [16 + c for c in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 13, 15)]
    kept += [32 + c for c in (0, 1, 2, 13, 15)] + list(range(48, 64))
    assert [palette[i] for i in kept] == [i + 1 for i in kept]


def test_a_busy_menu_picture_fits_the_cartridges_240_tiles_in_line_0():
    rom = _art_rom()
    picture = Image.effect_noise((320, 224), 120).convert("RGB")
    screens.draw(rom, _loaded(menu=_png(picture)), lambda line: None)
    _palette, width, height, entries, tiles = screens.record(rom, MENU.region.start)
    assert (width, height) == (40, 28)
    assert len(tiles) // 32 <= 240
    assert {e >> 13 for e in entries} == {0}


def test_a_load_that_holds_neither_pointer_is_left_alone():
    rom = _art_rom()
    struct.pack_into(">I", rom, TITLE.sites[1], 0x12345)
    lines = []
    picture = _png(Image.new("RGB", (256, 224), (0, 0, 0)))
    assert screens.draw(rom, _loaded(title=picture), lines.append) == []
    assert struct.unpack_from(">I", rom, TITLE.sites[0])[0] == TITLE.retail
    assert lines == ["Title art kept: the code that loads it is not the cartridge's"]


def test_drawing_again_replaces_the_earlier_picture_in_its_region():
    rom = _art_rom()
    screens.draw(rom, _loaded(title=_png(Image.new("RGB", (256, 224), (255, 0, 0)))), print)
    screens.draw(rom, _loaded(title=_png(Image.new("RGB", (256, 224), (0, 0, 255)))), print)
    assert [struct.unpack_from(">I", rom, s)[0] for s in TITLE.sites] == [TITLE.region.start] * 2
    assert screens.render(rom, TITLE).getpixel((10, 10)) == (0, 0, 255)


def test_an_unknown_slot_is_refused():
    with pytest.raises(CapabilityError, match="Unknown image slot 'boot'"):
        screens.load_images({"boot": b"x"}, lambda line: None)


def test_patch_with_only_a_title_picture_draws_it(tmp_path):
    patcher = NHL94GenesisPatcher(tmp_path / "cache")
    source = tmp_path / "in.md"
    source.write_bytes(bytes(_art_rom()))
    out = tmp_path / "out.md"
    picture = _png(Image.new("RGB", (256, 224), (255, 0, 0)))
    patcher.patch(
        rom_path=source,
        output_path=out,
        rosters=MappedRosters(game_id="nhl94-genesis"),
        images={"title": picture},
    )
    rom = out.read_bytes()
    assert len(rom) == 0x200000
    assert struct.unpack_from(">I", rom, TITLE.sites[0])[0] == TITLE.region.start
    assert screens.render(rom, TITLE).getpixel((0, 0)) == (255, 0, 0)
    # The header checksum covers the grown image.
    words = struct.unpack(f">{(len(rom) - 0x200) // 2}H", rom[0x200:])
    assert struct.unpack_from(">H", rom, 0x18E)[0] == sum(words) & 0xFFFF


def test_the_patcher_declares_and_exports_the_slots(tmp_path):
    patcher = NHL94GenesisPatcher(tmp_path / "cache")
    assert patcher.image_slots == ("title", "menu")
    source = tmp_path / "in.md"
    source.write_bytes(bytes(_art_rom()))
    written = patcher.export_images(source, tmp_path / "art")
    assert [p.name for p in written] == ["title.png", "menu.png"]


def test_exporting_a_missing_rom_is_a_rom_error(tmp_path):
    with pytest.raises(RomError):
        NHL94GenesisPatcher(tmp_path / "cache").export_images(tmp_path / "none.md", tmp_path)
