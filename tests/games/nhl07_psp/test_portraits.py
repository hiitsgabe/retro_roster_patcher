"""Player portraits in `FE/PORTAPT.VIV`, on hand-built archives.

On the retail disc the full patch drew 628 portraits, every one in its own
slot, and the game showed McDavid's head shot on his player screen in PPSSPP.
"""

from __future__ import annotations

import io
import random
import struct

import pytest

from retro_roster_patcher.formats.ea_tdb import bigf_parse
from retro_roster_patcher.games.nhl07_psp import portraits
from tests.fixtures import synthetic_shpm as synthetic

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

W, H = portraits.PORTRAIT_SIZE


def _noisy(seed: int) -> bytes:
    rng = random.Random(seed)
    return synthetic.shpm_file(
        synthetic.eight_bit_shape(W, H, bytes(rng.randrange(256) for _ in range(W * H)))
    )


def _flat() -> bytes:
    return synthetic.shpm_file(synthetic.eight_bit_shape(W, H, bytes(W * H)))


def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _pixels(archive: bytes, art: int) -> bytes:
    image = portraits.read_portrait(archive, art)
    assert image is not None
    return image.tobytes()


def _directory(archive: bytes) -> bytes:
    return archive[: struct.unpack_from(">I", archive, 12)[0]]


def test_a_head_shot_becomes_a_32_by_64_portrait_with_headroom():
    shot = Image.new("RGBA", (336, 336), (0, 0, 0, 0))
    shot.paste((200, 150, 120, 255), (120, 40, 216, 336))  # the subject, to the bottom edge
    portrait = portraits.headshot_portrait(_png(shot))
    assert portrait.size == (W, H) and portrait.mode == "RGBA"
    # EA's hair starts about 9 rows down; the first rows stay clear.
    assert all(portrait.getpixel((x, 0))[3] == 0 for x in range(W))
    assert portrait.getpixel((W // 2, H - 1)) == (200, 150, 120, 255)


def test_a_head_shot_without_transparency_uses_the_whole_picture():
    portrait = portraits.headshot_portrait(_png(Image.new("RGB", (600, 436), (9, 9, 9))))
    assert portrait.size == (W, H)
    assert portrait.getpixel((W // 2, H // 2)) == (9, 9, 9, 255)


def test_a_portrait_reads_back_through_its_art_id():
    archive = synthetic.portapt({0: _flat(), 7: _noisy(1)})
    assert portraits.read_portrait(archive, 7).size == (W, H)
    assert portraits.read_portrait(archive, 8) is None


def test_a_portrait_that_is_not_eight_bit_is_not_read():
    archive = synthetic.portapt({3: synthetic.shpm_file(synthetic.thirty_two_bit_shape(W, H))})
    assert portraits.read_portrait(archive, 3) is None


def test_portraits_that_fit_are_written_where_they_sit():
    archive = synthetic.portapt({0: _flat(), 1: _noisy(1), 2: _noisy(2)})
    face = Image.new("RGBA", (W, H), (30, 60, 90, 255))
    new, drawn = portraits.repack_portraits(archive, {1: face})
    assert drawn == 1
    assert len(new) == len(archive)
    assert _directory(new) == _directory(archive)  # every cached copy stays valid
    assert portraits.read_portrait(new, 1).tobytes() == face.tobytes()
    assert (
        portraits.read_portrait(new, 2).tobytes() == portraits.read_portrait(archive, 2).tobytes()
    )


def test_a_portrait_too_big_for_its_slot_repacks_the_archive():
    archive = synthetic.portapt({0: _flat(), 1: _flat(), 2: _noisy(2)})
    rng = random.Random(5)
    noise = Image.frombytes("RGBA", (W, H), bytes(rng.randrange(256) for _ in range(W * H * 4)))
    new, drawn = portraits.repack_portraits(archive, {1: noise})
    assert drawn == 1
    assert _directory(new) != _directory(archive)
    assert portraits.read_portrait(new, 1) is not None
    assert (
        portraits.read_portrait(new, 2).tobytes() == portraits.read_portrait(archive, 2).tobytes()
    )
    assert [e.name for e in bigf_parse(new)] == [e.name for e in bigf_parse(archive)]


def test_missing_and_unreadable_portraits_are_skipped():
    archive = synthetic.portapt(
        {0: _flat(), 3: synthetic.shpm_file(synthetic.thirty_two_bit_shape(W, H))}
    )
    face = Image.new("RGBA", (W, H), (1, 2, 3, 255))
    new, drawn = portraits.repack_portraits(archive, {3: face, 9: face})
    assert (new, drawn) == (archive, 0)


def test_the_most_colours_that_fit_are_kept():
    # A slot that holds the full-colour encoding gets exactly that encoding.
    archive = synthetic.portapt({1: _noisy(1)})
    (entry,) = bigf_parse(archive)
    original = archive[entry.offset : entry.offset + entry.size]
    face = Image.new("RGBA", (W, H))
    face.putdata([(x * 8, (y // 8) * 30, 128, 255) for y in range(H) for x in range(W)])
    new, _ = portraits.repack_portraits(archive, {1: face})
    full_colour = portraits._repacked_entry(original, face)
    assert full_colour is not None and len(full_colour) <= entry.size
    assert new[entry.offset : entry.offset + len(full_colour)] == full_colour


def test_only_a_face_id_one_bio_uses_is_drawn():
    art_of = {10: 500, 11: 501, 12: 501, 13: 0, 14: 502}
    photos = {
        10: "https://a/10.png",
        11: "https://a/11.png",
        13: "https://a/13.png",
        14: "",
        99: "x",
    }
    # 501 is shared by two bios, 0 is the silhouette, 99 is not a live bio.
    assert portraits.portrait_targets(art_of, photos) == {500: "https://a/10.png", 502: ""}


def test_a_portrait_with_too_few_pixel_bytes_reads_as_none():
    short = synthetic.block(0x5D, W, H, bytes(100), last=False) + synthetic.block(
        0x3B, 256, 1, synthetic.PALETTE, last=True
    )
    archive = synthetic.portapt({0: synthetic.shpm_file(short)})
    assert portraits.read_portrait(archive, 0) is None
    assert portraits.read_portrait(b"not a BIGF", 0) is None
