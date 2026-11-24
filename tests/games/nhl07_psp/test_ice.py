"""`nhl07_psp.ice`: a relocated club's crest at centre ice, the centre line
and faceoff dot kept."""

import io

import pytest

from retro_roster_patcher.formats import shpm
from retro_roster_patcher.formats.ea_tdb import BigfEntry, bigf_build, bigf_parse
from retro_roster_patcher.games.nhl07_psp import ice, screens
from tests.fixtures import synthetic_shpm as synthetic

PIL = pytest.importorskip("PIL.Image")
ImageDraw = pytest.importorskip("PIL.ImageDraw")

ICE = (237, 237, 237, 255)
BLUE = (160, 170, 198, 255)
RED = (195, 160, 160, 255)
cx, cy = ice.CENTRE


def old_rink(ring=82):
    """A 2006 rink: the blue ring, the centre line and dot, a grey crest
    inside, grey lettering beside the ring, a red sponsor at the bottom."""
    picture = PIL.new("RGBA", (256, 256), ICE)
    pen = ImageDraw.Draw(picture)
    pen.ellipse((cx - ring, cy - ring, cx + ring, cy + ring), outline=BLUE, width=2)
    pen.rectangle((cx - 40, cy - 40, cx + 40, cy + 40), fill=(120, 120, 120, 255))
    pen.rectangle((108, 0, 112, 255), fill=RED)
    pen.ellipse((cx - 2, cy - 2, cx + 2, cy + 2), fill=BLUE)
    pen.rectangle((cx - 60, cy - 95, cx - 30, cy - 88), fill=(120, 120, 120, 255))
    pen.rectangle((20, 220, 60, 250), fill=(200, 60, 60, 255))
    return picture


def crest():
    picture = PIL.new("RGBA", (200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(picture).ellipse((0, 0, 199, 199), fill=(0, 40, 200, 255))
    out = io.BytesIO()
    picture.save(out, "PNG")
    return out.getvalue()


def close(a, b, tolerance=24):
    return all(abs(x - y) <= tolerance for x, y in zip(a[:3], b[:3], strict=True))


def test_the_circle_takes_the_crest_and_the_line_dot_and_ring_stay():
    drawn = ice.draw(old_rink(), crest())
    assert close(drawn.getpixel((cx - 30, cy)), ((237 + 0) // 2, (237 + 40) // 2, (237 + 200) // 2))
    assert close(drawn.getpixel((cx - 82, cy)), BLUE)
    assert drawn.getpixel((110, 5)) == RED
    assert drawn.getpixel((cx, cy)) != ICE
    assert drawn.getpixel((cx - 40, cy - 89)) == ICE
    assert drawn.getpixel((40, 235)) == (200, 60, 60, 255)


def test_an_all_star_rink_takes_a_regular_ring():
    drawn = ice.draw(old_rink(ring=78), crest(), ring=True)
    assert drawn.getpixel((cx - 78, cy)) == ICE
    assert close(drawn.getpixel((cx - 82, cy)), BLUE)


def ice_member(picture):
    """An uncompressed `ICE_<n>.msh`: one 256x256 8-bit shape, `icex`."""
    data = synthetic.shpm_file(synthetic.eight_bit_shape(256, 256), names=("icex",))
    parsed = shpm.parse(data)
    shpm.encode_shape(parsed.shapes[0], picture)
    return shpm.build(parsed)


def test_a_rink_is_redrawn_in_place_by_its_logo_id():
    members = {"ICE_030.msh": ice_member(old_rink()), "ICE_031.msh": ice_member(old_rink())}
    archive = bigf_build([BigfEntry(n, 0, 0) for n in members], members)
    edited, drawn = ice.redraw(archive, {30: crest()})
    assert drawn == [30] and len(edited) == len(archive)
    entries = {e.name: e for e in bigf_parse(edited)}
    for name, changed in (("ICE_030.msh", True), ("ICE_031.msh", False)):
        e = entries[name]
        picture = screens.read_picture(
            edited[e.offset : e.offset + e.size], screens.Copy(ice.ARCHIVE, name, None, "icex")
        )
        assert (picture.getpixel((cx - 30, cy)) != (120, 120, 120, 255)) is changed
