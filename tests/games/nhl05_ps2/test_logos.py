"""`nhl05_ps2.logos`: a club's crest over its four front-end logos."""

import io
import os

import pytest

from retro_roster_patcher.formats import ssh
from retro_roster_patcher.formats.ea_tdb import BigfEntry, bigf_build, bigf_parse, refpack_compress
from retro_roster_patcher.games.nhl05_ps2 import logos, screens
from tests.formats.test_ssh import packed_shape, shape

PIL = pytest.importorskip("PIL.Image")


def member(name: str, shape_bytes: bytes) -> bytes:
    """One `<name>.big`: a compressed BIGF of the shape and its movie. The
    pixels are random, so the member is as large as a real logo's."""
    body = bytearray(shape_bytes)
    found = ssh._blocks(shape_bytes)
    body[found.data : found.data + found.size] = os.urandom(found.size)
    return refpack_compress(
        bigf_build(
            [BigfEntry(f"{name}.apt", 0, 0), BigfEntry(f"{name}.ssh", 0, 0)],
            {f"{name}.apt": b"Apt " + bytes(60), f"{name}.ssh": bytes(body)},
        )
    )


def archive(abbr: str = "ANA") -> bytes:
    members = {
        f"CS_{abbr}.big": member(f"CS_{abbr}", packed_shape(256, 128)),
        f"LOGO_EML_{abbr}.big": member(f"LOGO_EML_{abbr}", packed_shape(256, 256)),
        f"LOGO_LRG_{abbr}.big": member(f"LOGO_LRG_{abbr}", packed_shape(128, 128)),
        f"LOGO_SML_{abbr}.big": member(f"LOGO_SML_{abbr}", shape(64, 32)),
        f"T_{abbr}.big": member(f"T_{abbr}", shape(256, 32)),
    }
    return bigf_build([BigfEntry(n, 0, 0) for n in members], members)


def crest(colour=(200, 20, 20, 255)) -> bytes:
    picture = PIL.new("RGBA", (300, 200), (0, 0, 0, 0))
    for y in range(200):
        for x in range(300):
            if (x - 150) ** 2 / 150**2 + (y - 100) ** 2 / 100**2 <= 1:
                picture.putpixel((x, y), colour)
    out = io.BytesIO()
    picture.save(out, "PNG")
    return out.getvalue()


def read(archive_bytes: bytes, name: str):
    entry = next(e for e in bigf_parse(archive_bytes) if e.name == f"{name}.big")
    copy = screens.Copy(logos.ARCHIVE, entry.name, f"{name}.ssh")
    return screens.read_picture(archive_bytes[entry.offset : entry.offset + entry.size], copy)


def test_the_four_logos_take_the_crest_and_the_wordmark_stays():
    original = archive()
    edited, drawn = logos.edits(original, {0: crest()})
    assert drawn == {0: 4} and len(edited) == len(original)
    assert [e.size for e in bigf_parse(bytes(edited))] == [e.size for e in bigf_parse(original)]
    wordmark = next(e for e in bigf_parse(original) if e.name == "T_ANA.big")
    assert (
        edited[wordmark.offset : wordmark.offset + wordmark.size]
        == original[wordmark.offset : wordmark.offset + wordmark.size]
    )


def test_the_large_logo_is_centred_with_a_clear_margin():
    edited, _ = logos.edits(archive(), {0: crest()})
    picture = read(bytes(edited), "LOGO_LRG_ANA")
    box = picture.getchannel("A").point(lambda v: 255 if v >= 128 else 0).getbbox()
    assert box[0] >= 6 and box[1] >= 6 and box[2] <= 122 and box[3] <= 122
    assert abs((box[0] + box[2]) - 128) <= 2 and abs((box[1] + box[3]) - 128) <= 2
    assert picture.getpixel((64, 64))[:3] == pytest.approx((200, 20, 20), abs=12)


def test_the_choose_sides_logo_is_stored_half_as_tall_as_it_shows():
    # A 3:2 crest fitted to the 256x256 it shows as spans the picture's width
    # and, squashed, about a third of its height.
    edited, _ = logos.edits(archive(), {0: crest()})
    box = (
        read(bytes(edited), "CS_ANA")
        .getchannel("A")
        .point(lambda v: 255 if v >= 128 else 0)
        .getbbox()
    )
    assert box[2] - box[0] > 220 and 70 <= box[3] - box[1] <= 90


def test_the_small_logo_sits_on_a_white_ground():
    edited, _ = logos.edits(archive(), {0: crest()})
    picture = read(bytes(edited), "LOGO_SML_ANA")
    assert picture.getpixel((1, 1)) == (255, 255, 255, 255)
    assert picture.getpixel((32, 16))[:3] == pytest.approx((200, 20, 20), abs=12)


def test_a_slot_the_archive_lacks_draws_nothing():
    original = archive("BOS")
    edited, drawn = logos.edits(original, {0: crest(), 99: crest()})
    assert drawn == {} and bytes(edited) == original


def test_a_logo_that_fits_at_no_palette_size_is_left_alone():
    small = {
        "LOGO_LRG_ANA.big": refpack_compress(
            bigf_build(
                [BigfEntry("LOGO_LRG_ANA.ssh", 0, 0)], {"LOGO_LRG_ANA.ssh": packed_shape(128, 128)}
            )
        )
    }
    original = bigf_build([BigfEntry(n, 0, 0) for n in small], small)
    edited, drawn = logos.edits(original, {0: crest()})
    assert drawn == {} and bytes(edited) == original


def test_every_nhl_slot_names_a_file():
    assert len(logos.SLOT_FILES) == 30 and len(set(logos.SLOT_FILES)) == 30
    assert ssh.dimensions(packed_shape(256, 128)) == (256, 128)
