"""Screen art: slot geometry, the encoders, where streams go, and the patcher's hooks.

The synthetic ROM carries only what `screens` reads: the cartridge's own code
at every load site and poke (copied from the cartridge, not from
`screens.SLOTS`, so a wrong site in the table fails here), a small stream at
each address they load, the boot screen's raw colours and Ron Barr.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from retro_roster_patcher.core.errors import CapabilityError, RomError
from retro_roster_patcher.core.models import MappedRosters
from retro_roster_patcher.formats import snes
from retro_roster_patcher.formats.ea_tdb import refpack_compress
from retro_roster_patcher.games.nhl94_snes import screens
from retro_roster_patcher.games.nhl94_snes.patcher import NHL94SNESPatcher
from tests.fixtures.synthetic_ea_huff import huff
from tests.fixtures.synthetic_snes_rom import build_nhl94_snes_rom

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

ONE_MIB = 0x100000
#: The cartridge's code at each load site (`LDA #bank; STA $0E; LDA #address;
#: STA $0C`, or the boot screen's other way round) and at each poke.
CODE = {
    0x80CBD2: "a9 9a 00 85 0e a9 95 ba 85 0c",
    0x9B91FF: "a9 9a 00 85 0e a9 95 ba 85 0c",
    0x9D8924: "a9 95 00 85 0e a9 ae f1 85 0c",
    0x9D8957: "a9 9a 00 85 0e a9 95 ba 85 0c",
    0x9D8A8A: "a9 9a 00 85 0e a9 46 e9 85 0c",
    0x9D8AC3: "a9 9a 00 85 0e a9 21 e1 85 0c",
    0x9D8C45: "a9 95 00 85 0e a9 ae f1 85 0c",
    0x9D8C6E: "a9 9a 00 85 0e a9 95 ba 85 0c",
    0x9D8C9E: "a9 9a 00 85 0e a9 21 e1 85 0c",
    0x9DB5B6: "a9 9a 00 85 0e a9 a9 cc 85 0c",
    0x9DB64A: "a9 94 00 85 0e a9 00 80 85 0c",
    0x9DB673: "a9 9a 00 85 0e a9 c6 dd 85 0c",
    0x9DB6A5: "a9 9a 00 85 0e a9 8c d8 85 0c",
    0x9DDB29: "a9 95 00 85 0e a9 ae f1 85 0c",
    0x9DDB5C: "a9 9a 00 85 0e a9 95 ba 85 0c",
    0x9DDC1F: "a9 93 00 85 0e a9 00 80 85 0c",
    0x9DDC48: "a9 9a 00 85 0e a9 a1 b7 85 0c",
    0x9DDD00: "a9 9a 00 85 0e a9 21 e1 85 0c",
    0x9FF4E7: "a9 1c ee 85 0c a9 9a 00 85 0e",
    0x9FF54E: "a9 00 9a 85 0c a9 94 00 85 0e",
    0x9FF566: "a9 8d dc 85 0c a9 9a 00 85 0e",
    0x9FF5CA: "a9 ec f8 85 0c a9 94 00 85 0e",
    0x9FF4F1: "a0 30 00",
    0x9FF4F4: "a2 10 00",
    0x9FFA98: "a5 d9",
    0x9FFA9D: "a5 da",
    0x9FFAA2: "a5 db",
    0x9FFAA7: "a5 dc",
    0x9FF8F3: "a0 06 00",
    0x9FF5AB: "00 40 7f",
    0x9FF5EB: "a9 90 01",
    0x9DB52F: "e2 20",
}
BRACKET_SITES = (0x9D8C4A, 0x9D8C73, 0x9D8CA3)
MAP_HEAD = bytes.fromhex("20001c000803")


def _off(address: int) -> int:
    return (address >> 16 & 0x7F) * 0x8000 + (address & 0x7FFF)


def _palette(n: int, seed: int) -> bytes:
    rng = random.Random(seed)
    return b"".join(rng.randrange(0x8000).to_bytes(2, "little") for _ in range(n))


#: Ron Barr's tile 1 at column 20, row 20 of his map: its first row uses his
#: wallpaper blues (1, 2, 3, 5), the rest colour 4.
RON_TILE = bytes([1, 2, 3, 5, 1, 2, 3, 5] + [4] * 56)


def add_screens(rom: bytearray) -> bytearray:
    """`rom` with the cartridge's screen-art structures, in miniature."""
    for address, code in CODE.items():
        data = bytes.fromhex(code)
        rom[_off(address) : _off(address) + len(data)] = data
    ron_map = bytearray(MAP_HEAD + bytes(1792))
    ron_map[6 + 2 * (20 * 32 + 20)] = 1
    menu_tiles = bytearray(104 * 32)
    for i in range(0x60, 0x68):  # the portrait frame: anything but blank
        menu_tiles[32 * i : 32 * i + 32] = bytes([i]) * 32
    streams = {
        0x95F1AE: bytes(menu_tiles),
        0x9ABA95: MAP_HEAD + bytes(1792),
        0x9AE121: _palette(128, 1),
        0x938000: bytes(64),
        0x9AB7A1: MAP_HEAD + bytes(1792),
        0x9AE972: _palette(128, 2),
        0x948000: bytes(4 * 32),
        0x9AD88C: MAP_HEAD + bytes(1792),
        0x9ADDC6: MAP_HEAD + bytes(1792),
        0x9ACCA9: _palette(256, 3),
        0x949A00: bytes(4 * 64),
        0x9ADC8D: bytes.fromhex("800080001000") + bytes(128 * 128),
        0x94F8EC: bytes(0xDD0),
        0x94D6A5: bytes(32) + screens.iss.tile_bytes(RON_TILE),
        0x9AC0CC: bytes(ron_map),
        0x9AE946: _palette(8, 5) + bytes(240),  # 44 bytes before the goalie's
    }
    for address, raw in streams.items():
        data = refpack_compress(raw)
        rom[_off(address) : _off(address) + len(data)] = data
    rom[_off(0x9AEE1C) : _off(0x9AEE1C) + 128] = _palette(64, 4)  # rows 3, 4, 2, 1, raw
    rom[snes.SIZE_BYTE] = 0x0A
    return rom


@pytest.fixture
def rom() -> bytearray:
    return add_screens(bytearray(ONE_MIB))


def _flat_picture(colours: int, tiles: int = 24, seed: int = 1) -> Image.Image:
    """Solid 8x8 tiles, `tiles` patterns in `colours` colours on the SNES's grid:
    encodes exactly in every slot."""
    rng = random.Random(seed)
    palette = [tuple(rng.randrange(1, 32) * 255 // 31 for _ in range(3)) for _ in range(colours)]
    picture = Image.new("RGB", (256, 224))
    for y in range(0, 224, 8):
        for x in range(0, 256, 8):
            colour = palette[(x // 8 * 7 + y // 8 * 3) % tiles % colours]
            picture.paste(colour, (x, y, x + 8, y + 8))
    return picture


def _noise(seed: int = 2) -> Image.Image:
    return Image.frombytes("RGB", (256, 224), random.Random(seed).randbytes(3 * 256 * 224))


def _loaded(rom: bytes | bytearray, slot: screens.Slot, name: str) -> bytes:
    return screens._current(rom, slot, name)


# --- slot geometry ----------------------------------------------------------


def test_the_slots_are_the_three_screens():
    assert screens.IMAGE_SLOT_NAMES == ("boot", "title", "menu")
    assert {slot.size for slot in screens.SLOTS} == {(256, 224)}


@pytest.mark.parametrize("slot", screens.SLOTS, ids=lambda s: s.name)
def test_every_guide_lies_inside_its_picture(slot):
    assert slot.description
    for guide in slot.guides:
        assert guide.label
        assert 0 <= guide.x and guide.x + guide.w <= slot.size[0]
        assert 0 <= guide.y and guide.y + guide.h <= slot.size[1]


# --- round trips --------------------------------------------------------------


@pytest.mark.parametrize("name", screens.IMAGE_SLOT_NAMES)
def test_a_picture_comes_back_out_as_it_went_in(rom, name):
    picture = _flat_picture(12)
    if name == "menu":  # the game shows the left logo box in the right one too
        picture.paste(picture.crop((8, 8, 64, 64)), (192, 8))
    screens.write(rom, {name: picture})
    assert screens.render(rom, screens.SLOTS_BY_NAME[name]).tobytes() == picture.tobytes()


def test_export_writes_one_png_per_slot_past_a_copier_header(rom, tmp_path):
    path = tmp_path / "nhl94.smc"
    path.write_bytes(bytes(512) + bytes(rom))
    paths = screens.export(path, tmp_path / "out")
    assert [p.name for p in paths] == ["boot.png", "title.png", "menu.png"]
    assert {Image.open(p).size for p in paths} == {(256, 224)}


def test_export_reads_the_cartridges_huffman_streams(rom):
    """The cartridge keeps the title's maps (and the goalie, the boot plane) in
    EA Huffman: a map whose first word is tile 1 in row 2 draws that tile there."""
    tile = bytes([0xFF, 0]) * 8 + bytes(16)  # colour 1 everywhere
    data = refpack_compress(bytes(32) + tile)
    rom[_off(0x948000) : _off(0x948000) + len(data)] = data
    head = [("raw", b) for b in bytes.fromhex("20001c000803") + b"\x01\x08\x00"]
    stream = huff([*head, ("run", 1789), ("end", 0)], 6 + 1792)
    rom[_off(0x9AD88C) : _off(0x9AD88C) + len(stream)] = stream
    picture = screens.render(rom, screens.SLOTS_BY_NAME["title"])
    colours = screens._words(screens._stream(rom, 0x9ACCA9))
    assert picture.getpixel((3, 3)) == screens.iss.to_rgb(colours[2 * 16 + 1])
    assert picture.getpixel((11, 3)) == screens.iss.to_rgb(colours[0])


# --- encoding ---------------------------------------------------------------


def test_the_menu_keeps_the_portrait_frame_tiles_and_mirrors_the_logo_box(rom):
    menu = screens.SLOTS_BY_NAME["menu"]
    screens.write(rom, {"menu": _noise()})
    tiles = _loaded(rom, menu, "tiles")
    assert tiles[:32] == bytes(32)
    for i in range(0x60, 0x68):
        assert tiles[32 * i : 32 * i + 32] == bytes([i]) * 32
    picture = screens.render(rom, menu)
    assert picture.crop((192, 8, 248, 64)).tobytes() == picture.crop((8, 8, 64, 64)).tobytes()


def test_a_photo_takes_each_slots_whole_budget_and_no_more(rom):
    notes = screens.write(rom, {name: _noise() for name in screens.IMAGE_SLOT_NAMES})
    assert "boot: 896 tiles merged to 255" in notes
    assert "title: 896 tiles merged to 318" in notes
    assert any(n.startswith("menu: ") and n.endswith(" merged to 355") for n in notes)
    title_words = _loaded(rom, screens.SLOTS_BY_NAME["title"], "map")[6:]
    numbers = {int.from_bytes(title_words[i : i + 2], "little") & 0x3FF for i in range(0, 1792, 2)}
    assert max(numbers) < 320 and not numbers & {0, 256}
    plane = _loaded(rom, screens.SLOTS_BY_NAME["boot"], "map")[6:]
    assert set(plane[50 * 128 + 48 : 50 * 128 + 80]) <= set(range(1, 256))
    assert set(plane[: 50 * 128]) == {0}


def test_the_boot_picture_avoids_the_colours_the_game_fades_or_reloads(rom):
    screens.write(rom, {"boot": _noise()})
    tiles = _loaded(rom, screens.SLOTS_BY_NAME["boot"], "tiles")
    assert not {v // 16 for v in tiles[64:]} & {1, 2, 3, 4, 11, 12}
    assert tiles[:64] == bytes(64)


# --- writing ----------------------------------------------------------------


def test_streams_go_in_the_slots_bank_and_every_site_but_the_brackets_moves(rom):
    before = {a: bytes(rom[_off(a) - 5 : _off(a) + 3]) for a in BRACKET_SITES}
    notes = screens.write(rom, {name: _flat_picture(6) for name in screens.IMAGE_SLOT_NAMES})
    assert "ROM grown to 2 MiB" in notes
    assert len(rom) == 0x200000 and rom[snes.SIZE_BYTE] == 0x0B
    for slot in screens.SLOTS:
        for name, (_retail, pairs) in slot.sites.items():
            aimed = {screens._aimed(rom, pair) for pair in pairs}
            assert len(aimed) == 1
            at = _off(aimed.pop())
            assert slot.bank <= at < slot.bank + 0x8000, (slot.name, name)
    for address, raw in before.items():
        assert bytes(rom[_off(address) - 5 : _off(address) + 3]) == raw


def test_the_pokes_land_and_patching_again_gives_the_same_rom(rom):
    pictures = {"boot": _flat_picture(9), "title": _flat_picture(7), "menu": _flat_picture(5)}
    screens.write(rom, pictures)
    for slot in screens.SLOTS:
        for address, _retail, ours in slot.pokes:
            assert rom[_off(address) : _off(address) + len(ours)] == ours
    first = bytes(rom)
    screens.write(rom, pictures)
    assert bytes(rom) == first


def test_a_foreign_revision_is_refused_before_anything_is_written(rom):
    rom[_off(0x9DB52F)] = 0xEA
    before = bytes(rom)
    with pytest.raises(RomError, match=r"\$9DB52F is not the cartridge's"):
        screens.write(rom, {"menu": _flat_picture(4), "title": _flat_picture(4)})
    assert bytes(rom) == before


def test_a_moved_load_site_is_refused(rom):
    rom[_off(0x9DDB29)] = 0xAD  # LDA abs, not LDA #imm
    with pytest.raises(RomError, match="not the cartridge's"):
        screens.write(rom, {"menu": _flat_picture(4)})


def test_a_load_site_aimed_at_anything_else_is_refused(rom):
    rom[_off(0x9B9205)] = 0x96  # LDA #$BA96, not the menu's map at $9A:BA95
    with pytest.raises(RomError, match=r"\$9B9204 is not the cartridge's"):
        screens.write(rom, {"menu": _flat_picture(4)})


def test_ron_barrs_wallpaper_blues_take_the_pictures_colour_under_them(rom):
    """Matchups draws Ron on the wallpaper's map, the cartridge's blue wallpaper
    drawn into his outline: a one-colour picture turns those four colours into it."""
    picture = Image.new("RGB", (256, 224), (99, 140, 41))
    screens.write(rom, {"menu": picture})
    colours = screens._words(_loaded(rom, screens.SLOTS_BY_NAME["menu"], "ron_colours"))
    retail = screens._words(screens._stream(rom, 0x9AE946))
    shown = screens.iss.to_bgr555(
        screens.render(rom, screens.SLOTS_BY_NAME["menu"]).getpixel((0, 0))
    )
    for i in range(128):
        assert colours[i] == (shown if i in (1, 2, 3, 5) else retail[i]), i


def test_a_rom_smaller_than_the_cartridge_is_refused():
    with pytest.raises(RomError, match="Too small"):
        screens.write(bytearray(0xE8000), {"menu": _flat_picture(2)})


# --- the patcher ----------------------------------------------------------------


@pytest.fixture
def full_rom(tmp_path) -> Path:
    path = tmp_path / "nhl94.sfc"
    path.write_bytes(bytes(add_screens(build_nhl94_snes_rom())))
    return path


def test_the_patcher_lists_the_slots_and_exports_them(tmp_path, full_rom):
    patcher = NHL94SNESPatcher(cache_dir=tmp_path / "cache")
    assert patcher.image_slots == ("boot", "title", "menu")
    written = patcher.export_images(full_rom, tmp_path / "out")
    assert [p.name for p in written] == ["boot.png", "title.png", "menu.png"]


def test_patch_draws_the_images_and_fixes_the_checksum_last(tmp_path, full_rom):
    png = tmp_path / "title.png"
    _flat_picture(6).save(png)
    out = tmp_path / "out.sfc"
    lines: list[str] = []
    NHL94SNESPatcher(cache_dir=tmp_path / "cache", on_status=lines.append).patch(
        rom_path=full_rom,
        output_path=out,
        rosters=MappedRosters(game_id="nhl94-snes", teams={}),
        images={"title": png},
    )
    data = out.read_bytes()
    rendered = screens.render(data, screens.SLOTS_BY_NAME["title"])
    assert rendered.tobytes() == Image.open(png).convert("RGB").tobytes()
    assert "Drawing screen art..." in lines
    assert len(data) == 0x200000
    assert snes.fix_checksum(data) == data


def test_an_unknown_slot_is_refused_before_anything_is_written(tmp_path, full_rom):
    out = tmp_path / "out.sfc"
    with pytest.raises(CapabilityError, match="Unknown image slot 'rink'"):
        NHL94SNESPatcher(cache_dir=tmp_path / "cache").patch(
            rom_path=full_rom,
            output_path=out,
            rosters=MappedRosters(game_id="nhl94-snes", teams={}),
            images={"rink": b"png"},
        )
    assert not out.exists()
