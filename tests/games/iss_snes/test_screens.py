"""Screen art: slot geometry, the encoder, where streams go, and the patcher's hooks.

The synthetic ROM here carries only what `screens` reads: the list pointers
(with the VRAM word before each), a small stream at each cartridge offset, the
palette rows, the bytes the pokes expect, and 0xFF in the free runs.
"""

from __future__ import annotations

import dataclasses
import os
import random
from pathlib import Path

import pytest

from retro_roster_patcher.core.errors import CapabilityError, RomError
from retro_roster_patcher.core.models import MappedRosters
from retro_roster_patcher.games.iss_snes import screens
from retro_roster_patcher.games.iss_snes.konami import (
    MAX_RAW,
    compress,
    compress_large,
    decompress,
)
from retro_roster_patcher.games.iss_snes.patcher import ISSPatcher
from tests.fixtures import synthetic_iss_rom as fixture

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

ONE_MIB = 0x100000
#: The VRAM word each pointer site's entry loads to, as the cartridge has it.
VRAM_WORDS = {
    0x10025: 0x2000,
    0x1002A: 0x1000,
    0x1017C: 0x2000,
    0x10181: 0x2DC0,
    0x1019A: 0x1000,
    **dict.fromkeys((0x10463, 0x10510, 0x1053A, 0x106E0), 0x3000),
    **dict.fromkeys((0x10468, 0x1051F, 0x10549, 0x106E5), 0x1000),
    **dict.fromkeys((0x1046D, 0x10524, 0x1054E, 0x106EA), 0x1400),
}
SHARED_TITLE_TILES = 0x49CE1


def _put_stream(rom: bytearray, at: int, raw: bytes, sites: tuple[int, ...]) -> None:
    data = compress_large(raw)
    rom[at : at + len(data)] = data
    for site in sites:
        rom[site - 2 : site] = VRAM_WORDS[site].to_bytes(2, "little")
        screens.point(rom, site, at)


def add_screens(rom: bytearray) -> bytearray:
    """`rom` with the cartridge's screen-art structures, in miniature."""
    for lo, hi in screens.FREE:
        rom[lo:hi] = b"\xff" * (hi - lo)
    rom[screens._ROM_SIZE_BYTE] = 0x0A
    for slot in screens.SLOTS:
        for at, retail, _ours in slot.pokes:
            rom[at : at + len(retail)] = retail
        tiles = bytearray(32 * (max(slot.keep) + 1))
        for i in slot.keep:
            if slot.name == "konami":  # the sweep's tiles: anything but blank
                tiles[32 * i : 32 * i + 32] = bytes([i + 1]) * 32
        _put_stream(rom, slot.tiles.retail, bytes(tiles), slot.tiles.pointers)
        _put_stream(rom, slot.tile_map.retail, bytes(2048), slot.tile_map.pointers)
        if slot.blank:
            _put_stream(rom, SHARED_TITLE_TILES, bytes(range(256)) * 2, (slot.blank,))
        for spot in slot.colours:
            colours = b"".join((i * 0x421).to_bytes(2, "little") for i in range(16 - spot.first))
            rom[spot.offset : spot.offset + len(colours)] = colours
    return rom


@pytest.fixture
def rom() -> bytearray:
    return add_screens(bytearray(ONE_MIB))


def _flat_picture(size: tuple[int, int], colours: int, seed: int = 1) -> Image.Image:
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


def _streams(rom: bytes, slot: screens.Slot) -> list[tuple[int, int]]:
    """(offset, length) of each stream the slot's pointers load."""
    out = []
    for site in screens._sites(slot):
        at = screens.target(rom, site)
        out.append((at, int.from_bytes(rom[at : at + 2], "little") & 0x7FFF))
    return out


# --- slot geometry ----------------------------------------------------------


def test_the_slots_are_the_three_screens_with_their_sizes():
    assert screens.IMAGE_SLOT_NAMES == ("konami", "title", "menu")
    sizes = {slot.name: slot.size for slot in screens.SLOTS}
    assert sizes == {"konami": (256, 224), "title": (256, 224), "menu": (256, 256)}


@pytest.mark.parametrize("slot", screens.SLOTS, ids=lambda s: s.name)
def test_every_guide_lies_inside_its_picture(slot):
    assert slot.description
    assert slot.guides
    for guide in slot.guides:
        assert guide.label
        assert 0 <= guide.x and guide.x + guide.w <= slot.size[0]
        assert 0 <= guide.y and guide.y + guide.h <= slot.size[1]


def test_the_tile_budgets_leave_the_screens_their_own_tiles():
    konami, title, menu = screens.SLOTS
    # VRAM $2000-$4FFF; the sweep keeps tiles 0-5 and 16-21 of the logo's set,
    # and its sparkle (four 16x16 frames) tiles 6-13 and 22-29.
    assert (konami.budget, konami.keep) == (768, tuple(range(0, 14)) + tuple(range(16, 30)))
    # Tile 0 stays blank: BG1 draws it over the whole title.
    assert (title.budget, title.keep) == (768, (0,))
    # $3000-$3FFF, which no menu screen overwrote in Mesen.
    assert (menu.budget, menu.first_tile) == (256, 256)


def test_the_free_runs_stay_clear_of_the_roster_writer_and_inside_one_bank():
    for lo, hi in screens.FREE:
        assert lo // 0x8000 == (hi - 1) // 0x8000
        assert hi <= 0x17680 or lo >= 0x18000
        assert hi <= 0x48000 or lo >= 0x48A80


# --- export -----------------------------------------------------------------


@pytest.mark.parametrize("name", screens.IMAGE_SLOT_NAMES)
def test_a_picture_comes_back_out_as_it_went_in(rom, name):
    slot = screens.SLOTS_BY_NAME[name]
    picture = _flat_picture(slot.size, 12)
    written, _notes = screens.write(bytes(rom), {name: picture})
    assert screens.render(written, slot).tobytes() == picture.tobytes()


def test_export_writes_one_png_per_slot(rom, tmp_path):
    path = tmp_path / "iss.sfc"
    path.write_bytes(bytes(rom))
    paths = screens.export(path, tmp_path / "out")
    assert [p.name for p in paths] == ["konami.png", "title.png", "menu.png"]
    assert Image.open(paths[2]).size == (256, 256)


def test_export_reads_past_a_copier_header(rom, tmp_path):
    path = tmp_path / "iss.smc"
    path.write_bytes(bytes(512) + bytes(rom))
    paths = screens.export(path, tmp_path / "out")
    assert len(paths) == 3


def test_the_title_renders_the_shared_tiles_the_cartridge_loads_over_its_own(rom):
    """The entry at 0x10181 loads 15 tiles at $2DC0 (tile 220) after the title's set."""
    title = screens.SLOTS_BY_NAME["title"]
    tiles = screens._vram_tiles(rom, title)
    assert tiles[220] == (bytes(range(256)) * 2)[:32]


# --- encoding ---------------------------------------------------------------


def test_mirrored_tiles_share_one_tile_slot(rom):
    menu = screens.SLOTS_BY_NAME["menu"]
    tile = Image.new("RGB", (8, 8), (0, 0, 0))
    tile.paste((255, 0, 0), (0, 0, 5, 2))  # an L in the top-left corner
    tile.paste((255, 0, 0), (0, 0, 2, 7))
    flips = [
        tile,
        tile.transpose(Image.Transpose.FLIP_LEFT_RIGHT),
        tile.transpose(Image.Transpose.FLIP_TOP_BOTTOM),
        tile.transpose(Image.Transpose.ROTATE_180),
    ]
    picture = Image.new("RGB", (256, 256))
    for n in range(32 * 32):
        picture.paste(flips[n % 4], (n % 32 * 8, n // 32 * 8))
    encoded = screens.encode(picture, menu, screens._kept(rom, menu))
    assert encoded.distinct == encoded.used == 1
    words = {int.from_bytes(encoded.words[i : i + 2], "little") for i in range(0, 2048, 2)}
    assert {w >> 14 for w in words} == {0, 1, 2, 3}
    written, _ = screens.write(bytes(rom), {"menu": picture})
    assert screens.render(written, menu).tobytes() == picture.tobytes()


def test_tiles_beyond_the_budget_are_merged_down_to_it(rom):
    menu = screens.SLOTS_BY_NAME["menu"]
    encoded = screens.encode(_noise((256, 256)), menu, screens._kept(rom, menu))
    assert encoded.distinct > 255
    assert encoded.used == 255
    words = encoded.words
    numbers = {int.from_bytes(words[i : i + 2], "little") & 0x3FF for i in range(0, 2048, 2)}
    assert min(numbers) >= 257 and max(numbers) <= 511
    assert len(encoded.tiles) <= 256 * 32


def test_the_merge_folds_the_closest_tiles_together():
    base = bytes([1] * 32 + [2] * 32)
    near = bytes([1] * 31 + [2] + [2] * 32)  # one pixel off `base`
    far = bytes([3, 4] * 32)
    flipped = screens._flipped(base, 2)  # `base` upside down
    colours = {0: [0, 0x7FFF, 0x001F, 0x03E0, 0x7C00] + [0] * 11}
    became = screens._merge([base, near, far, flipped], [0] * 4, [1] * 4, colours, 2)
    assert set(became) | {p for p, _ in became.values()} >= {0, 1, 3}
    assert 2 not in became
    survivors = {0, 1, 2, 3} - set(became)
    assert len(survivors) == 2


def test_a_tile_merged_into_a_flipped_twin_keeps_the_flip():
    base = bytes([1] * 32 + [2] * 32)
    colours = {0: [0, 0x7FFF, 0x001F] + [0] * 13}
    became = screens._merge([base, screens._flipped(base, 2)], [0, 0], [1, 1], colours, 1)
    ((p, (q, flip)),) = became.items()
    assert screens._flipped(base if q == 0 else screens._flipped(base, 2), flip) == (
        base if p == 0 else screens._flipped(base, 2)
    )


def test_without_flips_a_tile_merges_into_its_nearest_unflipped():
    """Mode 7 (NHL '94's boot screen) cannot flip: the upside-down twin is far."""
    base = bytes([1] * 32 + [2] * 32)
    near = bytes([1] * 31 + [2] + [2] * 32)
    colours = {0: [0, 0x7FFF, 0x001F] + [0] * 13}
    patterns = [base, screens._flipped(base, 2), near]
    assert screens._merge(patterns, [0] * 3, [1, 5, 5], colours, 2, flips=False) == {0: (2, 0)}


def test_the_konami_sweep_tiles_are_kept_verbatim(rom):
    konami = screens.SLOTS_BY_NAME["konami"]
    written, _ = screens.write(bytes(rom), {"konami": _flat_picture((256, 224), 10)})
    tiles = screens._stream(written, screens.target(written, konami.tiles.pointers[0]))
    for i in konami.keep:
        assert tiles[32 * i : 32 * i + 32] == bytes([i + 1]) * 32


def test_the_title_keeps_tile_0_blank_and_switches_the_shared_tiles_off(rom):
    title = screens.SLOTS_BY_NAME["title"]
    before = bytes(rom[SHARED_TITLE_TILES : SHARED_TITLE_TILES + 64])
    written, _ = screens.write(bytes(rom), {"title": _flat_picture((256, 224), 9)})
    assert written[0x1017F:0x10181] == b"\x00\x20"
    assert screens._stream(written, screens.target(written, title.blank)) == bytes(32)
    assert screens._vram_tiles(written, title)[0] == bytes(32)
    assert written[SHARED_TITLE_TILES : SHARED_TITLE_TILES + 64] == before


def test_the_2p_wallpaper_gets_the_colours_with_red_and_blue_swapped(rom):
    written, _ = screens.write(bytes(rom), {"menu": _flat_picture((256, 256), 6)})
    one = [int.from_bytes(written[0x2F49D + 2 * i : 0x2F49F + 2 * i], "little") for i in range(15)]
    two = [int.from_bytes(written[0x2F47D + 2 * i : 0x2F47F + 2 * i], "little") for i in range(15)]
    assert two == [(c & 0x3E0) | (c >> 10) | (c & 31) << 10 for c in one]
    assert any(c & 31 != c >> 10 for c in one)


def test_the_sequence_pokes_land_and_a_foreign_revision_is_refused(rom):
    written, _ = screens.write(bytes(rom), {"konami": _flat_picture((256, 224), 4)})
    for at, _retail, ours in screens.SLOTS_BY_NAME["konami"].pokes:
        assert written[at : at + len(ours)] == ours
    rom[0x1E67] ^= 0xFF
    with pytest.raises(RomError, match="not the cartridge's"):
        screens.write(bytes(rom), {"konami": _flat_picture((256, 224), 4)})


# --- space ------------------------------------------------------------------


def test_simple_art_goes_back_where_the_cartridge_kept_it(rom):
    written, notes = screens.write(bytes(rom), {"menu": _flat_picture((256, 256), 6)})
    menu = screens.SLOTS_BY_NAME["menu"]
    assert len(written) == ONE_MIB
    assert notes == []
    assert [at for at, _ in _streams(written, menu)] == [menu.tiles.retail, menu.tile_map.retail]
    for site in menu.tiles.pointers:
        assert screens.target(written, site) == menu.tiles.retail


def test_art_too_big_for_its_place_goes_in_a_free_run(rom, monkeypatch):
    menu = dataclasses.replace(
        screens.SLOTS_BY_NAME["menu"],
        tiles=dataclasses.replace(screens.SLOTS_BY_NAME["menu"].tiles, capacity=0),
    )
    monkeypatch.setitem(screens.SLOTS_BY_NAME, "menu", menu)  # type: ignore[index]
    written, _ = screens.write(bytes(rom), {"menu": _flat_picture((256, 256), 6)})
    assert len(written) == ONE_MIB
    at, length = _streams(written, menu)[0]
    assert any(lo <= at and at + length <= hi for lo, hi in screens.FREE)


def test_a_photo_grows_the_rom_to_2_mib_with_its_streams_in_bank_a0(rom):
    written, notes = screens.write(bytes(rom), {"konami": _noise((256, 224))})
    konami = screens.SLOTS_BY_NAME["konami"]
    assert len(written) == 2 * ONE_MIB
    assert "ROM grown to 2 MiB" in notes
    assert written[0x7FD7] == 0x0B
    assert written[ONE_MIB : konami.bank] == b"\xff" * (konami.bank - ONE_MIB)
    for at, length in _streams(written, konami):
        assert konami.bank <= at < konami.bank + 0x8000
        assert (at & 0x7FFF) + length <= 0x8000  # inside one LoROM bank
    for site in konami.tiles.pointers:
        assert written[site + 2] == 0xA0  # bank $A0, bit 6 clear


def test_the_checksum_matches_the_image(rom):
    written, _ = screens.write(bytes(rom), {"title": _flat_picture((256, 224), 5)})
    total = sum(written[:0x7FDC]) + sum(written[0x7FE0:]) + 0x1FE
    checksum = int.from_bytes(written[0x7FDE:0x7FE0], "little")
    complement = int.from_bytes(written[0x7FDC:0x7FDE], "little")
    assert checksum == total & 0xFFFF
    assert checksum ^ complement == 0xFFFF


def test_repointing_keeps_bit_6_of_the_bank_byte(rom):
    menu = screens.SLOTS_BY_NAME["menu"]
    linked = menu.tiles.pointers[1]
    rom[linked + 2] |= 0x40  # the list-link flag ($80:A270)
    written, _ = screens.write(bytes(rom), {"menu": _noise((256, 256))})
    assert written[linked + 2] & 0x40
    assert written[linked + 2] & ~0x40 == written[menu.tiles.pointers[0] + 2]
    assert screens.target(written, linked) == screens.target(written, menu.tiles.pointers[0])


def test_point_and_target_agree_in_every_bank():
    rom = bytearray(0x10)
    for at in (0x0, 0x7FFF, 0x8000, 0x960B0, 0xFFFFF, 0x100000, 0x1FFFFF):
        for link in (0, 0x40):
            rom[2] = link
            screens.point(rom, 0, at)
            assert screens.target(rom, 0) == at
            assert rom[2] & 0x40 == link and rom[2] & 0x80
            assert rom[1] & 0x80  # a LoROM address is $8000-$FFFF


def test_patching_an_expanded_rom_again_reuses_its_space(rom):
    pictures = {"konami": _noise((256, 224)), "menu": _flat_picture((256, 256), 6)}
    first, _ = screens.write(bytes(rom), pictures)
    again, notes = screens.write(first, pictures)
    assert again == first
    assert "ROM grown to 2 MiB" not in notes


def test_simple_art_on_an_expanded_rom_hands_its_bank_back(rom):
    konami = screens.SLOTS_BY_NAME["konami"]
    first, _ = screens.write(bytes(rom), {"konami": _noise((256, 224))})
    second, _ = screens.write(first, {"konami": _flat_picture((256, 224), 4)})
    assert len(second) == 2 * ONE_MIB
    assert [at for at, _ in _streams(second, konami)] == [
        konami.tiles.retail,
        konami.tile_map.retail,
    ]
    assert second[konami.bank : konami.bank + 0x8000] == b"\xff" * 0x8000


def test_a_rom_smaller_than_the_cartridge_is_refused():
    with pytest.raises(RomError, match="Too small"):
        screens.write(bytes(0x80000), {"menu": _flat_picture((256, 256), 2)})


# --- the long-stream compressor ----------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        bytes(5000),
        bytes(range(256)) * 9,
        bytes(b & 3 for b in random.Random(3).randbytes(3000)),
        bytes([0, 0x20]) * 900,
        bytes(33) + b"\x01" + bytes(40),
        b"",
    ],
    ids=["zeros", "ramp", "four-values", "pairs", "tail", "empty"],
)
def test_compress_large_round_trips_past_the_ring(raw):
    assert decompress(compress_large(raw)) == raw


def test_compress_large_is_never_longer_than_compress_on_what_both_take():
    rng = random.Random(5)
    for _ in range(10):
        raw = bytes(rng.choice([0, 0, 1, 2, rng.randrange(256)]) for _ in range(MAX_RAW))
        assert len(compress_large(raw)) <= len(compress(raw))


def test_compress_large_refuses_a_stream_the_header_cannot_count():
    with pytest.raises(ValueError):
        compress_large(random.Random(1).randbytes(0x8000))


# --- the patcher ----------------------------------------------------------------


@pytest.fixture
def full_rom(tmp_path) -> Path:
    path = tmp_path / "iss.sfc"
    path.write_bytes(bytes(add_screens(fixture.build_iss_rom())))
    return path


def test_the_patcher_lists_the_slots_and_exports_them(tmp_path, full_rom):
    patcher = ISSPatcher(cache_dir=tmp_path / "cache")
    assert patcher.image_slots == ("konami", "title", "menu")
    written = patcher.export_images(full_rom, tmp_path / "out")
    assert [p.name for p in written] == ["konami.png", "title.png", "menu.png"]


def test_patch_draws_the_images_after_the_rosters(tmp_path, full_rom):
    png = tmp_path / "menu.png"
    _flat_picture((256, 256), 6).save(png)
    out = tmp_path / "out.sfc"
    lines: list[str] = []
    patcher = ISSPatcher(cache_dir=tmp_path / "cache", on_status=lines.append)
    patcher.patch(
        rom_path=full_rom,
        output_path=out,
        rosters=MappedRosters(game_id="iss-snes", teams={}),
        images={"menu": png},
    )
    rendered = screens.render(out.read_bytes(), screens.SLOTS_BY_NAME["menu"])
    assert rendered.tobytes() == Image.open(png).convert("RGB").tobytes()
    assert "Drawing screen art..." in lines


def test_an_unknown_slot_is_refused_before_anything_is_written(tmp_path, full_rom):
    out = tmp_path / "out.sfc"
    with pytest.raises(CapabilityError, match="Unknown image slot 'stadium'"):
        ISSPatcher(cache_dir=tmp_path / "cache").patch(
            rom_path=full_rom,
            output_path=out,
            rosters=MappedRosters(game_id="iss-snes", teams={}),
            images={"stadium": b"png"},
        )
    assert not out.exists()


def test_a_picture_pillow_cannot_read_is_refused(tmp_path, full_rom):
    with pytest.raises(CapabilityError, match="not a picture"):
        ISSPatcher(cache_dir=tmp_path / "cache").patch(
            rom_path=full_rom,
            output_path=tmp_path / "out.sfc",
            rosters=MappedRosters(game_id="iss-snes", teams={}),
            images={"title": b"not a png"},
        )


# --- the cartridge --------------------------------------------------------------


@pytest.mark.real_rom
@pytest.mark.skipif(
    not os.environ.get("RETRO_ROSTER_TEST_ROMS"),
    reason="set RETRO_ROSTER_TEST_ROMS to a directory holding iss.sfc",
)
def test_the_cartridges_own_art_survives_a_round_trip(tmp_path):
    rom = Path(os.environ["RETRO_ROSTER_TEST_ROMS"]) / "iss.sfc"
    if not rom.exists():
        pytest.fail(f"RETRO_ROSTER_TEST_ROMS is set but {rom} is missing")
    data = rom.read_bytes()
    originals = {slot.name: screens.render(data, slot) for slot in screens.SLOTS}
    written, _ = screens.write(data, originals)
    for slot in screens.SLOTS:
        again = screens.render(written, slot)
        diff = sum(
            abs(a - b) for a, b in zip(again.tobytes(), originals[slot.name].tobytes(), strict=True)
        )
        assert diff / len(again.tobytes()) < 4, slot.name


@pytest.mark.parametrize(("name", "priority"), [("title", 1), ("konami", 0)])
def test_the_map_words_carry_the_slots_priority_bit(rom, name, priority):
    """The title's tiles draw in front (bit 13), as the cartridge's do; the
    Konami logo's behind its sweep."""
    slot = screens.SLOTS_BY_NAME[name]
    encoded = screens.encode(_flat_picture(slot.size, 4), slot, screens._kept(rom, slot))
    words = [
        int.from_bytes(encoded.words[i : i + 2], "little") for i in range(0, len(encoded.words), 2)
    ]
    assert {w >> 13 & 1 for w in words} == {priority}


def test_a_stream_in_a_free_run_survives_another_slots_write(rom, monkeypatch):
    """The menu's stream moved to a free run must not be written over when the
    Konami logo then needs room."""
    menu = dataclasses.replace(
        screens.SLOTS_BY_NAME["menu"],
        tiles=dataclasses.replace(screens.SLOTS_BY_NAME["menu"].tiles, capacity=0),
    )
    monkeypatch.setitem(screens.SLOTS_BY_NAME, "menu", menu)  # type: ignore[index]
    first, _ = screens.write(bytes(rom), {"menu": _flat_picture((256, 256), 6)})
    at, length = _streams(first, menu)[0]
    kept = first[at : at + length]
    konami = dataclasses.replace(
        screens.SLOTS_BY_NAME["konami"],
        tiles=dataclasses.replace(screens.SLOTS_BY_NAME["konami"].tiles, capacity=0),
    )
    monkeypatch.setitem(screens.SLOTS_BY_NAME, "konami", konami)  # type: ignore[index]
    second, _ = screens.write(first, {"konami": _flat_picture((256, 224), 3)})
    assert _streams(second, menu)[0] == (at, length)
    assert second[at : at + length] == kept


def test_the_title_switches_off_the_cartridges_haze(rom):
    written, _ = screens.write(bytes(rom), {"title": _flat_picture((256, 224), 4)})
    assert written[0x80E7] == 0x00


def test_the_bank_81_run_stops_before_the_match_reads_ffe4(rom):
    assert all(not (lo <= 0xFFF2 < hi) for lo, hi in screens.FREE)
