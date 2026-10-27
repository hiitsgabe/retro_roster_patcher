"""ISS Deluxe screen art: slots, where streams go, the pokes, and the patcher's hooks.

The synthetic cartridge carries only what `screens` reads: the list entries
(with the VRAM word before each), a small stream at each cartridge place, the
palette records, the bytes the pokes expect, and 0xFF in the free runs.
"""

from __future__ import annotations

import random

import pytest

from retro_roster_patcher.core.errors import CapabilityError, RomError
from retro_roster_patcher.core.models import MappedRosters
from retro_roster_patcher.games.iss_deluxe_snes import flags, screens
from retro_roster_patcher.games.iss_deluxe_snes import rom as R
from retro_roster_patcher.games.iss_deluxe_snes.patcher import ISSDeluxePatcher
from retro_roster_patcher.games.iss_snes import screens as iss
from retro_roster_patcher.games.iss_snes.konami import compress_large

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

VRAM_WORDS = {0x104FB: 0x2000, 0x10BC3: 0x6000, 0x104EC: 0x1000, 0x11971: 0x4000, 0x1197E: 0}
VRAM_WORDS |= dict.fromkeys((0x10E6F, 0x10E81, 0x1189C, 0x11933), 0x3800)
VRAM_WORDS |= dict.fromkeys((0x10E74, 0x10E86, 0x118A1, 0x11938), 0x1000)
VRAM_WORDS |= {0x11979: 0x2000, 0x11983: 0x1000}
_ELSEWHERE = 0x100000  # the title's BG2 streams, which no slot owns


def _put(rom: bytearray, at: int, raw: bytes, sites: tuple[int, ...]) -> None:
    data = compress_large(raw)
    rom[at : at + len(data)] = data
    for site in sites:
        rom[site - 2 : site] = VRAM_WORDS[site].to_bytes(2, "little")
        iss.point(rom, site, at)


def cartridge() -> bytearray:
    rom = bytearray(R.ROM_SIZE)
    rom[R.TITLE_AT : R.TITLE_AT + len(R.TITLE)] = R.TITLE
    for lo, hi in screens.FREE:
        rom[lo:hi] = b"\xff" * (hi - lo)
    for slot in screens.SLOTS:
        for at, cart, _ours in slot.pokes:
            rom[at : at + len(cart)] = cart
        tiles = bytearray(32 * 30)
        for i in slot.keep:  # the Konami sweep's tiles: anything but blank
            tiles[32 * i : 32 * i + 32] = bytes([i + 1]) * 32
        _put(rom, slot.tiles.retail, bytes(tiles), slot.tiles.pointers)
        _put(rom, slot.tile_map.retail, bytes(2048), slot.tile_map.pointers)
    _put(rom, _ELSEWHERE, bytes(64), (0x11979,))
    _put(rom, _ELSEWHERE + 0x100, bytes(2048), (0x11983,))
    for first, record in screens._TITLE_PALETTES:
        count = 0x120 if first == 0x20 else 0x20
        rom[record : record + 2] = (count - 1).to_bytes(2, "little")
    for at in (*screens._TITLE_CARD_LISTS, screens._TITLE_TM + 2):
        rom[at : at + 3] = b"\x00\x00\xff"  # a destination and the end
    rom[screens._TITLE_TM : screens._TITLE_TM + 2] = b"\x54\x01"
    return rom


@pytest.fixture
def rom() -> bytearray:
    return cartridge()


def _flat(size: tuple[int, int], colours: int, seed: int = 1) -> Image.Image:
    """Solid 8x8 tiles in `colours` colours on the SNES's grid: encodes exactly."""
    rng = random.Random(seed)
    palette = [tuple(rng.randrange(32) * 255 // 31 for _ in range(3)) for _ in range(colours)]
    picture = Image.new("RGB", size)
    for y in range(0, size[1], 8):
        for x in range(0, size[0], 8):
            picture.paste(palette[(x // 8 * 7 + y // 8 * 3) % colours], (x, y, x + 8, y + 8))
    return picture


def _noise(size: tuple[int, int], seed: int = 2) -> Image.Image:
    return Image.frombytes("RGB", size, random.Random(seed).randbytes(3 * size[0] * size[1]))


def _streams(rom: bytes | bytearray) -> list[tuple[int, int]]:
    return [
        iss._span(rom, iss.target(rom, s)) for slot in screens.SLOTS for s in screens._sites(slot)
    ]


def test_the_patcher_offers_the_three_screens():
    assert ISSDeluxePatcher.image_slots == ("konami", "title", "menu")
    sizes = {s.name: s.size for s in screens.SLOTS}
    assert sizes == {"konami": (256, 224), "title": (256, 224), "menu": (256, 256)}
    for slot in screens.SLOTS:
        for g in slot.guides:
            assert g.x >= 0 and g.y >= 0 and g.x + g.w <= slot.size[0] and g.y + g.h <= slot.size[1]


def test_every_space_lies_in_one_bank_clear_of_bank_af():
    for lo, hi in screens.SPACES:
        assert lo // 0x8000 == (hi - 1) // 0x8000
        bank_af = (flags.BANK_AF & 0x3F) * 0x8000
        assert hi <= bank_af or lo >= bank_af + 0x8000
    ordered = sorted(screens.SPACES)
    assert all(a[1] <= b[0] for a, b in zip(ordered, ordered[1:], strict=False))


@pytest.mark.parametrize("name", ["konami", "title", "menu"])
def test_a_picture_comes_back_out_as_it_went_in(rom, name):
    slot = screens.SLOTS_BY_NAME[name]
    picture = _flat(slot.size, 12)
    assert screens.write(rom, {name: picture}) == []
    assert screens.render(rom, slot).tobytes() == picture.convert("RGB").tobytes()


def test_the_pokes_land_and_a_foreign_revision_is_refused(rom):
    screens.write(rom, {"konami": _flat((256, 224), 4), "title": _flat((256, 224), 4)})
    for name in ("konami", "title"):
        for at, _cart, ours in screens.SLOTS_BY_NAME[name].pokes:
            assert rom[at : at + len(ours)] == ours
    rom[0x21F3] = 0x99
    with pytest.raises(RomError):
        screens.write(rom, {"konami": _flat((256, 224), 4)})


def test_the_title_tiles_load_after_the_brush_in_mode_1(rom):
    screens.write(rom, {"title": _flat((256, 224), 4)})
    assert iss._le16(rom, 0x1196F) == 0x2A80
    assert rom[0x843C] == rom[0x843E] == 1


def test_the_konami_sweep_tiles_are_kept_verbatim(rom):
    screens.write(rom, {"konami": _noise((256, 224))})
    data = iss._stream(rom, iss.target(rom, 0x104FB))
    for i in screens.SLOTS_BY_NAME["konami"].keep:
        assert data[32 * i : 32 * i + 32] == bytes([i + 1]) * 32
    assert iss.target(rom, 0x10BC3) == iss.target(rom, 0x104FB)


def test_the_cpu_wallpaper_gets_the_colours_with_red_and_blue_swapped(rom):
    screens.write(rom, {"menu": _flat((256, 256), 6)})
    blue = [iss._le16(rom, 0x4E4A0 + 2 * i) for i in range(15)]
    red = [iss._le16(rom, 0x4E4C2 + 2 * i) for i in range(15)]
    assert red == [iss._swap_rb(c) for c in blue] and blue != red


def test_photos_shrink_to_fit_and_no_two_streams_overlap(rom):
    pictures = {s.name: _noise(s.size, i) for i, s in enumerate(screens.SLOTS)}
    notes = screens.write(rom, pictures)
    assert any(n.startswith("title:") for n in notes)
    spans = sorted(_streams(rom))
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:], strict=False))
    for start, end in spans:
        assert any(lo <= start and end <= hi for lo, hi in screens.SPACES)
    for slot in screens.SLOTS:
        assert screens.render(rom, slot).size == slot.size


def test_replacing_one_slot_later_keeps_the_others(rom):
    screens.write(rom, {s.name: _noise(s.size, 5) for s in screens.SLOTS})
    before = {
        s: screens.render(rom, screens.SLOTS_BY_NAME[s]).tobytes() for s in ("konami", "title")
    }
    screens.write(rom, {"menu": _flat((256, 256), 3)})
    for name, drawn in before.items():
        assert screens.render(rom, screens.SLOTS_BY_NAME[name]).tobytes() == drawn


def test_an_unknown_slot_is_refused():
    with pytest.raises(CapabilityError):
        screens.load_images({"wallpaper": b""}, print)


def test_export_images_writes_one_png_per_slot(rom, tmp_path):
    path = tmp_path / "dlx.sfc"
    path.write_bytes(bytes(rom))
    written = ISSDeluxePatcher(tmp_path / "cache").export_images(path, tmp_path / "out")
    assert [p.name for p in written] == ["konami.png", "title.png", "menu.png"]
    assert Image.open(written[2]).size == (256, 256)


def test_patch_refuses_an_unknown_slot_before_reading_the_rom(tmp_path):
    patcher = ISSDeluxePatcher(tmp_path / "cache")
    with pytest.raises(CapabilityError):
        patcher.patch(
            rom_path=tmp_path / "missing.sfc",
            output_path=tmp_path / "out.sfc",
            rosters=MappedRosters(game_id=patcher.game_id, teams={}),
            images={"wallpaper": b""},
        )
