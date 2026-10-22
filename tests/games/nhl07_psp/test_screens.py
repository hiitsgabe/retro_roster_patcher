"""Screen art slots, on hand-built archives.

On the retail disc every slot exports, and a patch with the app's art wrote
boot 2, title 2, menu 3, loading 8, logo 2, player 2 and each badge 4 copies,
all in place; PPSSPP showed the new boot, title and menu screens.
"""

from __future__ import annotations

import random

import pytest

from retro_roster_patcher.formats.ea_tdb import (
    BigfEntry,
    bigf_build,
    bigf_parse,
    refpack_compress,
)
from retro_roster_patcher.games.nhl07_psp import screens
from tests.fixtures import synthetic_shpm as synthetic

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

FBOOT = "PSP_GAME/USRDIR/PRELOAD/FBOOT.VIV"
BGAPT = "PSP_GAME/USRDIR/FE/BGAPT.VIV"


def _indices(w: int, h: int, seed: int | None) -> bytes:
    """Noise, a different stream per (seed, size), so no shape repeats another."""
    if seed is None:
        return bytes(w * h)
    rng = random.Random(f"{seed}:{w}x{h}")
    return bytes(rng.randrange(256) for _ in range(w * h))


def _menu_big(seed: int | None = 1) -> bytes:
    """A compressed `menu.big`: a movie and `menu.msh` with shapes 1, 11 and 9."""
    msh = synthetic.shpm_file(
        synthetic.eight_bit_shape(64, 32, _indices(64, 32, seed)),
        synthetic.eight_bit_shape(256, 64, _indices(256, 64, seed)),
        synthetic.eight_bit_shape(256, 512, _indices(256, 512, seed)),
        names=("1", "11", "9"),
    )
    inner = {"menu.apt": b"Apt Data:1.0" + bytes(20), "menu.msh": msh}
    return refpack_compress(bigf_build([BigfEntry(n, 0, 0) for n in inner], inner))


def _archive(members: dict[str, bytes]) -> bytes:
    return bigf_build([BigfEntry(n, 0, 0) for n in members], members)


def _directory(archive: bytes) -> list[tuple[str, int, int]]:
    return [(e.name, e.offset, e.size) for e in bigf_parse(archive)]


def _logo_in(archive: bytes) -> Image.Image:
    entry = next(e for e in bigf_parse(archive) if e.name == "menu.big")
    copy = screens.SLOTS_BY_NAME["logo"].copies[0]
    picture = screens.read_picture(archive[entry.offset : entry.offset + entry.size], copy)
    assert picture is not None
    return picture


def test_every_slot_name_is_unique_and_every_copy_names_its_shape():
    assert len(set(screens.IMAGE_SLOT_NAMES)) == len(screens.SLOTS)
    assert all(slot.copies and all(c.shape for c in slot.copies) for slot in screens.SLOTS)


def test_a_slot_is_drawn_into_every_copy_in_place():
    archives = {
        FBOOT: _archive({"x.big": b"\x10\xfb" + bytes(40), "menu.big": _menu_big()}),
        BGAPT: _archive({"menu.big": _menu_big()}),
    }
    logo = Image.new("RGBA", (256, 64), (200, 30, 30, 255))
    edited, written, skipped = screens.plan_writes(archives, {"logo": logo})
    assert (written, skipped) == ({"logo": 2}, [])
    for path in (FBOOT, BGAPT):
        new = bytes(edited[path])
        assert len(new) == len(archives[path])
        assert _directory(new) == _directory(archives[path])  # cached copies stay valid
        assert _logo_in(new).tobytes() == logo.tobytes()


def test_two_slots_in_one_member_both_land():
    archives = {
        FBOOT: _archive({"menu.big": _menu_big()}),
        BGAPT: _archive({"menu.big": _menu_big()}),
    }
    logo = Image.new("RGBA", (256, 64), (0, 0, 255, 255))
    player = Image.new("RGBA", (256, 512), (0, 255, 0, 255))
    edited, written, _ = screens.plan_writes(archives, {"logo": logo, "player": player})
    assert written == {"logo": 2, "player": 2}
    entry = next(e for e in bigf_parse(bytes(edited[FBOOT])) if e.name == "menu.big")
    member = bytes(edited[FBOOT])[entry.offset : entry.offset + entry.size]
    copy = screens.SLOTS_BY_NAME["player"].copies[0]
    assert screens.read_picture(member, copy).tobytes() == player.tobytes()
    assert _logo_in(bytes(edited[FBOOT])).tobytes() == logo.tobytes()


def test_a_picture_that_fits_no_copy_is_skipped_whole():
    # Flat originals compress to almost nothing, so noise cannot fit even at 32 colours.
    archives = {
        FBOOT: _archive({"menu.big": _menu_big(None)}),
        BGAPT: _archive({"menu.big": _menu_big(None)}),
    }
    rng = random.Random(3)
    noise = Image.frombytes(
        "RGBA", (256, 64), bytes(rng.randrange(256) for _ in range(256 * 64 * 4))
    )
    edited, written, skipped = screens.plan_writes(archives, {"logo": noise})
    assert (edited, written, skipped) == ({}, {}, ["logo"])


def test_a_missing_archive_skips_the_slot():
    archives = {FBOOT: _archive({"menu.big": _menu_big()})}
    _, written, skipped = screens.plan_writes(archives, {"logo": Image.new("RGBA", (256, 64))})
    assert (written, skipped) == ({}, ["logo"])


def test_a_picture_of_another_size_is_stretched_to_the_slot():
    picture = screens.to_slot(
        Image.new("RGB", (480, 272), (1, 2, 3)), screens.SLOTS_BY_NAME["menu"]
    )
    assert picture.size == (512, 256) and picture.mode == "RGBA"
    assert picture.getpixel((10, 10)) == (1, 2, 3, 255)


def test_every_archive_a_slot_needs_is_listed():
    assert screens.slot_archives({"logo"}) == {FBOOT, BGAPT}
    assert "PSP_GAME/USRDIR/FE/LOADSCR.VIV" in screens.slot_archives()
