"""`nhl05_ps2.screens`: the pictures drawn into the disc's front-end archives."""

import io
import os

import pytest

from retro_roster_patcher.formats.ea_tdb import BigfEntry, bigf_build, bigf_parse, refpack_compress
from retro_roster_patcher.games.nhl05_ps2 import screens
from tests.formats.test_ssh import packed_shape, shape

PIL = pytest.importorskip("PIL.Image")


def archive(members: dict[str, bytes]) -> bytes:
    return bigf_build([BigfEntry(name, 0, 0) for name in members], members)


def picture(size=(512, 512), seed=0):
    out = PIL.new("RGBA", size)
    w, h = size
    out.putdata(
        [
            ((x * 255) // w, (y * 255) // h, (seed * 60) % 256, 255)
            for y in range(h)
            for x in range(w)
        ]
    )
    return out


def error(a, b) -> float:
    pa, pb = a.convert("RGB").tobytes(), b.convert("RGB").tobytes()
    return sum(abs(x - y) for x, y in zip(pa, pb, strict=True)) / len(pa)


def test_the_menu_slot_is_drawn_into_its_nested_shape_in_place():
    bgapt = archive(
        {"bg0.big": archive({"bg0.apt": b"Apt " + bytes(60), "bg0.ssh": packed_shape(512, 512)})}
    )
    edited, written, skipped = screens.plan_writes({"FE/BGAPT.VIV": bgapt}, {"menu": picture()})
    assert written == {"menu": 1} and skipped == []
    out = edited["FE/BGAPT.VIV"]
    assert len(out) == len(bgapt)
    (member,) = [e for e in bigf_parse(bytes(out)) if e.name == "bg0.big"]
    back = screens.read_picture(
        bytes(out[member.offset : member.offset + member.size]),
        screens.SLOTS_BY_NAME["menu"].copies[0],
    )
    assert back.size == (512, 512) and error(back, picture()) < 6


def test_the_boot_slot_is_drawn_into_both_of_its_compressed_copies():
    # The disc's own boot screen compresses to about as much as any picture.
    plain = bytearray(shape(512, 512))
    plain[0x40 : 0x40 + 512 * 512] = os.urandom(512 * 512)
    boot = refpack_compress(bytes(plain))
    fmisc = archive({"bootscreen.ssh": boot})
    loadscr = archive({"bootscreen.ssh": boot, "engEASO.ssh": boot})
    archives = {"PRELOAD/FMISC.VIV": fmisc, "FE/LOADSCR.VIV": loadscr}
    edited, written, skipped = screens.plan_writes(archives, {"boot": picture(seed=1)})
    assert written == {"boot": 2} and skipped == []
    assert set(edited) == set(archives)
    assert all(len(edited[p]) == len(archives[p]) for p in archives)


def test_a_boot_picture_that_fits_no_palette_size_is_skipped():
    boot = refpack_compress(shape(512, 512))  # all one colour: a tiny member
    archives = {"PRELOAD/FMISC.VIV": archive({"bootscreen.ssh": boot})}
    assert screens.plan_writes(archives, {"boot": picture(seed=1)}) == ({}, {}, ["boot"])


def test_a_slot_whose_copy_is_missing_is_skipped_whole():
    bgapt = archive({"bg1.big": archive({"bg1.ssh": packed_shape(512, 512)})})
    edited, written, skipped = screens.plan_writes({"FE/BGAPT.VIV": bgapt}, {"menu": picture()})
    assert (edited, written, skipped) == ({}, {}, ["menu"])


def test_a_picture_of_another_size_is_stretched_to_the_slot():
    bgapt = archive({"bg0.big": archive({"bg0.ssh": packed_shape(512, 512)})})
    small = picture((64, 32))
    edited, written, _ = screens.plan_writes({"FE/BGAPT.VIV": bgapt}, {"menu": small})
    assert written == {"menu": 1}
    (member,) = bigf_parse(bytes(edited["FE/BGAPT.VIV"]))
    back = screens.read_picture(
        bytes(edited["FE/BGAPT.VIV"][member.offset : member.offset + member.size]),
        screens.SLOTS_BY_NAME["menu"].copies[0],
    )
    assert back.size == (512, 512)


def test_every_slot_names_its_archives():
    assert screens.slot_archives({"menu"}) == {"FE/BGAPT.VIV"}
    assert (
        "FE/PNAPT.VIV" in screens.slot_archives() and "PRELOAD/FMISC.VIV" in screens.slot_archives()
    )
    assert screens.IMAGE_SLOT_NAMES == ("boot", "title", "legal", "menu")


def test_an_image_slot_the_game_lacks_is_refused_before_the_copy(tmp_path):
    from retro_roster_patcher.core.errors import CapabilityError
    from tests.games.nhl05_ps2.test_patcher import build, iso, league

    patcher = build(tmp_path)
    png = io.BytesIO()
    picture((8, 8)).save(png, "PNG")
    with pytest.raises(CapabilityError):
        patcher.patch(
            rom_path=iso(tmp_path),
            output_path=tmp_path / "out.iso",
            rosters=patcher.map_rosters(league()),
            images={"court": png.getvalue()},
        )
    assert not (tmp_path / "out.iso").exists()


def test_the_synthetic_disc_has_no_screen_art_to_export(tmp_path):
    from tests.games.nhl05_ps2.test_patcher import build, iso

    assert build(tmp_path).export_images(iso(tmp_path), tmp_path / "art") == []
