"""NHL 2005 portraits (`FE/PORTAPT.VIV`) on hand-built archives.

On the retail disc the patch draws about 740 of them; each try is a picture
encode and a RefPack stream, all in Python, on a handheld too.
"""

from __future__ import annotations

import random

import pytest

from retro_roster_patcher.formats import ea_tdb
from retro_roster_patcher.formats.ea_tdb import BigfEntry, bigf_build, refpack_compress
from retro_roster_patcher.games.nhl05_ps2 import portraits
from tests.formats.test_ssh import shape

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

W, H = portraits.SIZE


def _archive(arts: list[int], seed: int | None = None) -> bytes:
    """A PORTAPT of RefPack'd `NNNN.big` entries, each holding `NNNN.ssh`:
    blank, or noise from `seed` (a big stream, so a new portrait fits)."""
    rng = random.Random(seed)
    contents = {}
    for art in arts:
        ssh_name = portraits.entry_name(art).replace(".big", ".ssh")
        data = bytearray(shape(W, H))
        if seed is not None:
            data[0x40 : 0x40 + W * H] = bytes(rng.randrange(256) for _ in range(W * H))
        inner = bigf_build([BigfEntry(ssh_name, 0, 0)], {ssh_name: bytes(data)})
        contents[portraits.entry_name(art)] = refpack_compress(inner)
    return bigf_build([BigfEntry(name, 0, 0) for name in contents], contents)


def test_a_line_goes_out_every_few_portraits(monkeypatch):
    # 739 portraits ran 4 minutes on a Mac without a line, over the app's
    # 15-minute watchdog on a handheld; the lines keep the job alive.
    monkeypatch.setattr(portraits, "STATUS_EVERY", 2)
    arts = [1, 2, 3, 4, 5]
    face = Image.new("RGBA", (W, H), (30, 60, 90, 255))
    lines: list[str] = []
    new, drawn = portraits.repack(_archive(arts), dict.fromkeys(arts, face), lines.append)
    assert drawn == 5
    assert lines == ["Portraits 2/5...", "Portraits 4/5..."]
    assert portraits.read(new, 3).getpixel((W // 2, H // 2)) == (30, 60, 90, 255)


def test_a_portrait_that_fits_takes_one_compression_pass(monkeypatch):
    archive = _archive([7], seed=1)
    parses: list[bool] = []
    real = ea_tdb._refpack
    monkeypatch.setattr(
        ea_tdb, "_refpack", lambda d, by_saving: parses.append(by_saving) or real(d, by_saving)
    )
    new, drawn = portraits.repack(archive, {7: Image.new("RGBA", (W, H), (1, 2, 3, 255))})
    assert drawn == 1 and parses == [False]
    assert len(new) == len(archive)  # written where it sits
