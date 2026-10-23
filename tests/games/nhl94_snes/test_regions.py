"""The parts of the 2 MiB image each writer owns, at their worst, never meet:
team identity, then screen art, then the head shots in everything left."""

from __future__ import annotations

import random
from itertools import pairwise

from retro_roster_patcher.formats.ea_tdb import refpack_compress
from retro_roster_patcher.games.nhl94_snes import identity, logo, portraits, screens

BANK = 0x8000


def _most(size: int) -> int:
    """The longest `refpack_compress` makes `size` bytes: the 5-byte header, a
    command byte for each 112 literals and the end marker. A copy may cost the
    literals before it one more command byte, but saves at least one itself."""
    return 5 + size + -(-size // 112) + 1


def test_the_regions_follow_one_another_and_the_faces_take_the_rest():
    regions = (identity.REGION, screens.REGION, portraits.REGION)
    assert identity.REGION.start == 0x100000  # past the 1 MiB cartridge
    assert all(a.stop <= b.start for a, b in pairwise(regions))
    assert portraits.REGION.start == screens.REGION.stop
    assert portraits.REGION.stop == identity.TWO_MIB
    assert all(r.start % BANK == 0 and r.stop % BANK == 0 for r in regions)


def test_identity_at_its_worst_stays_in_its_region():
    """`write`'s blobs in its order, each as large as it can be: every pointer
    an earlier patch left in the region carried over, then all 28 slots with
    a logo (center ice of incompressible tiles) and an incompressible archive."""
    tiles = _most(32 * logo.ICE_MAX_TILES)
    layout = 6 + 2 * logo.ICE_TILES[0] * logo.ICE_TILES[1]
    palettes = identity.SLOTS + identity.ARCHIVE_ENTRIES + identity.SLOTS
    carried = [32] * palettes + [tiles, layout] * identity.SLOTS
    logos = [32, tiles, layout] * identity.SLOTS + [_most(identity.ARCHIVE_SIZE)]
    banners_and_portraits = [32, 32] * identity.SLOTS
    region = identity._Region(bytearray(identity.TWO_MIB))
    for size in carried + logos + banners_and_portraits:
        region.put(bytes(size))  # raises past the region's end
    assert region.at <= 0x120000  # four of its twelve banks


def test_each_screen_at_its_worst_fits_its_own_bank_of_the_screens_region():
    """Each slot's streams as `write` builds them, every byte a picture decides
    incompressible and its whole tile budget used; a colour stream at most the
    512 bytes of CGRAM."""
    noise = random.Random(94).randbytes
    plane = bytearray(128 * 128)
    x0, y0 = screens._PLANE_AT
    for row in range(y0, y0 + 28):
        plane[128 * row + x0 : 128 * row + x0 + 32] = noise(32)
    blank_map = screens._MAP_HEAD + bytes(screens._MAP_BYTES)
    pack = refpack_compress
    worst = {
        "boot": [pack(noise(256 * 64)), pack(screens._PLANE_HEAD + plane)]
        + [pack(bytes(0xDD0)), noise(512)],
        "title": [pack(noise(512)), pack(noise(32 * screens._TITLE_ENCODING.budget))]
        + [pack(blank_map), pack(screens._MAP_HEAD + noise(screens._MAP_BYTES))],
        "menu": [pack(noise(32 * screens._MENU_ENCODING.budget))]
        + [pack(screens._MAP_HEAD + noise(screens._MAP_BYTES)), pack(noise(512))]
        + [pack(bytes(32)), pack(blank_map), pack(noise(512))],
    }
    banks = sorted(slot.bank for slot in screens.SLOTS)
    assert banks == list(range(screens.REGION.start, screens.REGION.stop, BANK))
    for name, streams in worst.items():
        assert len(streams) == len(screens.SLOTS_BY_NAME[name].sites)
        assert sum(map(len, streams)) <= 0x4800  # 18 KiB of the bank's 32
