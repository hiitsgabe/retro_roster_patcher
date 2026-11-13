"""`nhl05_ps2.ice`: a relocated club's crest at centre ice."""

import io

import pytest

from retro_roster_patcher.formats import ssh
from retro_roster_patcher.formats.ea_tdb import BigfEntry, bigf_build, bigf_parse, refpack_compress
from retro_roster_patcher.games.nhl05_ps2 import ice, screens
from tests.formats.test_ssh import packed_shape

PIL = pytest.importorskip("PIL.Image")
ImageDraw = pytest.importorskip("PIL.ImageDraw")

ICE = (240, 240, 240, 255)
BLUE = (150, 170, 225, 255)
cx, cy = ice.CENTRE


def old_rink():
    """A 2004 rink: the blue ring, a red crest inside, grey letters and a
    dashed grey circle beside the ring, a red sponsor at the bottom."""
    picture = PIL.new("RGBA", (512, 512), ICE)
    pen = ImageDraw.Draw(picture)
    pen.ellipse((cx - 140, cy - 140, cx + 140, cy + 140), outline=BLUE, width=3)
    pen.ellipse((cx - 144, cy - 144, cx + 144, cy + 144), outline=(120, 120, 120, 255), width=1)
    pen.rectangle((cx - 60, cy - 60, cx + 60, cy + 60), fill=(180, 120, 120, 255))
    pen.rectangle((cx - 20, cy - 190, cx + 20, cy - 160), fill=(120, 120, 120, 255))
    pen.rectangle((100, 440, 160, 490), fill=(200, 60, 60, 255))
    return picture


def crest():
    picture = PIL.new("RGBA", (200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(picture).ellipse((0, 0, 199, 199), fill=(0, 40, 200, 255))
    out = io.BytesIO()
    picture.save(out, "PNG")
    return out.getvalue()


def close(a, b, tolerance=24):
    return all(abs(x - y) <= tolerance for x, y in zip(a[:3], b[:3], strict=True))


def test_the_circle_takes_the_crest_faded_and_the_rest_of_the_rink_stays():
    picture = ice.draw(old_rink(), crest())
    assert close(picture.getpixel((cx, cy)), (120, 140, 220))  # half blue, half ice
    assert close(picture.getpixel((cx + 140, cy)), BLUE)  # the ring
    assert close(picture.getpixel((cx, cy - 175)), ICE)  # the letters
    assert close(picture.getpixel((cx - 144, cy)), ICE)  # the dashes beside the ring
    assert close(picture.getpixel((cx + 115, cy)), ICE)  # the old crest
    assert close(picture.getpixel((130, 465)), (200, 60, 60))  # the sponsor


def test_a_rink_is_redrawn_in_place_by_its_logo_id():
    shape = ssh.encode(packed_shape(512, 512, ssh.PACKED_SQUARE), old_rink())
    members = {"ICE_001.ssh": refpack_compress(shape) + bytes(4096), "spot.ssh": b"x" * 40}
    archive = bigf_build([BigfEntry(n, 0, 0) for n in members], members)
    edited, drawn = ice.redraw(archive, {1: crest(), 22: crest()})
    assert drawn == [1] and len(edited) == len(archive)
    entry = next(e for e in bigf_parse(edited) if e.name == "ICE_001.ssh")
    picture = screens.read_picture(
        edited[entry.offset : entry.offset + entry.size], screens.Copy(ice.ARCHIVE, entry.name)
    )
    assert close(picture.getpixel((cx, cy)), (120, 140, 220), 32)


def test_an_all_star_rink_takes_a_regular_seasons_blue_ring():
    rink = old_rink()
    ImageDraw.Draw(rink).ellipse(
        (cx - 140, cy - 140, cx + 140, cy + 140), outline=(202, 161, 167, 255), width=3
    )
    r, width, colour = ice.RING_DRAWN
    assert close(ice.draw(rink, crest()).getpixel((cx + 140, cy)), ICE)  # red: cleared
    ringed = ice.draw(rink, crest(), ring=True)
    assert close(ringed.getpixel((cx + r - width // 2, cy)), colour, 8)
    assert close(ringed.getpixel((cx, cy - r + width // 2)), colour, 8)


def test_a_slots_rink_is_the_one_of_the_arena_it_plays_at():
    from retro_roster_patcher.formats.ea_tdb import TDBFile
    from tests.fixtures.synthetic_tdb import TYPE_UINT, FieldSpec, TableSpec, build_tdb, pack_bits

    def table(name, fields, rows):
        size = sum(f.bit_width for f in fields) // 8
        data = b"".join(pack_bits(fields, row, size) for row in rows)
        return TableSpec(name, fields, size, len(rows), len(rows), data)

    sare = [FieldSpec("INDX", TYPE_UINT, 0, 8), FieldSpec("LOGO", TYPE_UINT, 8, 8)]
    aren = [FieldSpec("INDX", TYPE_UINT, 0, 8), FieldSpec("ID__", TYPE_UINT, 8, 8)]
    arenas = [{"INDX": i, "LOGO": logo} for i, logo in ((1, 1), (24, 24), (25, 25), (51, 31))]
    plays = [{"INDX": 1, "ID__": 1}, {"INDX": 24, "ID__": 25}, {"INDX": 31, "ID__": 51}]
    tdb = TDBFile.parse(build_tdb([table("SARE", sare, arenas), table("AREN", aren, plays)]))
    assert ice.logo_ids(tdb, [1, 24, 31]) == {1: 1, 24: 25, 31: 31}
