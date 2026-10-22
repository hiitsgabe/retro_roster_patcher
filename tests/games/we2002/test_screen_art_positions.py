"""Screen-art blocks are rewritten by VRAM position, not by content."""

import io
import shutil
from pathlib import Path

import pytest

from retro_roster_patcher.games.we2002 import flag_atlas as fa
from retro_roster_patcher.games.we2002 import screen_art as sa
from tests.games.we2002.test_screen_art import TEST_SLOT, disc  # noqa: F401

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def _png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _apply(path, img):
    blocks, raws = sa.import_slot(path, TEST_SLOT, _png(img))
    assert all(b.at is not None for b in blocks)
    fa.rewrite_blocks(path, blocks, raws)


def test_two_identical_blocks_get_their_own_replacements(disc):  # noqa: F811
    path, _title = disc
    blank = Image.new("RGBA", (TEST_SLOT.width, TEST_SLOT.height), (0, 0, 0, 0))
    _apply(path, blank)  # every block now decodes to the same empty bytes
    img = blank.copy()
    p = TEST_SLOT.pieces[1]
    for y in range(p.y, p.y + p.h):
        for x in range(p.x, p.x + p.w):
            img.putpixel((x, y), (255, 0, 0, 255))
    _apply(path, img)  # content matching would paint every block red
    out = Image.open(io.BytesIO(sa.export_slot(path, TEST_SLOT))).convert("RGBA")
    assert out.getpixel((p.x + 1, p.y + 1))[3] == 255
    other = TEST_SLOT.pieces[0]
    assert out.getpixel((other.x + 1, other.y + 1))[3] == 0


def test_every_guide_lies_inside_its_canvas():
    for slot in sa.SLOTS.values():
        for g in slot.guides:
            assert 0 <= g.x and g.x + g.w <= slot.width, (slot.name, g)
            assert 0 <= g.y and g.y + g.h <= slot.height, (slot.name, g)
            assert g.label
    assert sa.SLOTS["boot"].guides, "the boot screen has measured guides"


def test_a_community_translation_that_breaks_the_menu_art_is_backed_out(disc, tmp_path):  # noqa: F811
    """The English PPF writes absolute offsets; on a disc it was not made for
    the screen files stop parsing, so the output is restored and the
    translation reported as skipped."""
    from retro_roster_patcher.games.we2002.patcher import WE2002Patcher
    from tests.games.we2002.test_ppf import _ppf1_patch

    rom, _title = disc
    rom = Path(rom)
    # A patch that zeroes TITLE.BIN's pointer table (its first 8 user bytes).
    from retro_roster_patcher.games.we2002.flag_atlas import _disc_files

    with open(rom, "rb") as f:
        entry = _disc_files(f)["TITLE.BIN"]
    offset = entry.lba * 2352 + 24
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "w202-english.ppf").write_bytes(
        _ppf1_patch("bad", offset.to_bytes(4, "little") + bytes([8]) + bytes(8))
    )
    out = tmp_path / "out.bin"
    shutil.copy2(rom, out)
    lines: list[str] = []
    patcher = WE2002Patcher(tmp_path / "cache", assets_dir=assets, on_status=lines.append)
    patcher._apply_translation(rom, out, "en", None)
    assert out.read_bytes() == rom.read_bytes()
    assert any("translation skipped" in line and "TITLE.BIN" in line for line in lines)
