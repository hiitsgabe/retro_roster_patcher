"""`nhl07_psp.jerseys`: NHL 2005's dressing on NHL 07's SHPM archives."""

import io
import struct

import pytest

from retro_roster_patcher.formats import shpm
from retro_roster_patcher.formats.ea_tdb import (
    BigfEntry,
    bigf_build,
    bigf_extract,
    bigf_parse,
    refpack_compress,
    refpack_decompress,
)
from retro_roster_patcher.games.nhl07_psp import jerseys, screens
from tests.fixtures import synthetic_shpm as synthetic

PIL = pytest.importorskip("PIL.Image")
ImageDraw = pytest.importorskip("PIL.ImageDraw")

BRICK = (140, 30, 40, 255)
SLACK = 64 * 1024


def _archive(members):
    return bigf_build([BigfEntry(n, 0, 0) for n in members], members)


def _shape(picture, name):
    data = synthetic.shpm_file(synthetic.eight_bit_shape(*picture.size), names=(name,))
    parsed = shpm.parse(data)
    shpm.encode_shape(parsed.shapes[0], picture)
    return shpm.build(parsed)


def _packed(data):
    """Compressed, with room after it as a member of the disc has none of:
    the drawn jersey compresses less well than a flat one."""
    return refpack_compress(data) + bytes(SLACK)


def _uniform():
    torso = PIL.new("RGBA", (256, 256), BRICK)
    ImageDraw.Draw(torso).rectangle((110, 170, 150, 220), fill=(240, 240, 240, 255))
    helmet = PIL.new("RGBA", (128, 32), BRICK)
    tors, hel = shpm.parse(_shape(torso, "TORS")), shpm.parse(_shape(helmet, "HEL1"))
    tors.shapes += hel.shapes
    return shpm.build(tors)


def _numbers():
    """A 4-bit shape: only its palette block is read."""
    palette = bytes(c for _ in range(16) for c in BRICK)
    shape = synthetic.block(0x5C, 16, 32, bytes(256), last=False) + synthetic.block(
        shpm.PAL32, 16, 1, palette, last=True
    )
    return synthetic.shpm_file(shape, names=("ARM0",))


def crest():
    picture = PIL.new("RGBA", (200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(picture).ellipse((0, 0, 199, 199), fill=(0, 200, 200, 255))
    out = io.BytesIO()
    picture.save(out, "PNG")
    return out.getvalue()


def test_the_jets_slot_shares_the_coyotes_classics():
    members = {
        part.format(art, n): f"data of {part.format(art, n)}".encode() * 4
        for art, ns in ((1, (0, 1, 2)), (22, (6, 7, 8)))
        for n in ns
        for part in jerseys.PARTS
    }
    edited, _, changed, _ = jerseys.dress(_archive(members), _archive({}), [1], {})
    assert changed == 3
    for part in jerseys.PARTS:
        assert bigf_extract(edited, part.format(1, 0)) == members[part.format(22, 8)]
        assert bigf_extract(edited, part.format(1, 1)) == members[part.format(22, 6)]


def test_utahs_jerseys_are_drawn_black_with_the_crest_in_place():
    members = {}
    for n in (0, 1):
        members[f"uni022_{n}.msh"] = _packed(_uniform())
        members[f"num022_{n}.msh"] = _packed(_numbers())
        members[f"letters_022_{n}.msh"] = _packed(_numbers())
    archive = _archive(members)
    edited, drawn = jerseys.restyle(archive, jerseys.RESTYLED[22], crest())
    assert drawn == 2 and len(edited) == len(archive)
    entries = {e.name: e for e in bigf_parse(edited)}

    def member(name):
        e = entries[name]
        return shpm.parse(refpack_decompress(edited[e.offset : e.offset + e.size]))

    torso = shpm.decode_shape(member("uni022_0.msh").shapes[0])[0]
    assert max(torso.getpixel((20, 120))[:3]) < 60
    assert torso.getpixel((128, 195))[1] > 120
    helmet = shpm.decode_shape(member("uni022_0.msh").shapes[1])[0]
    assert max(helmet.getpixel((5, 5))[:3]) < 60
    palette = next(b for b in member("num022_1.msh").shapes[0].blocks if b[0] == shpm.PAL32)
    assert max(struct.unpack_from("<3B", palette, 16)) < 60


def test_a_menu_picture_is_drawn_at_its_own_size():
    name = "JERS_PHO_00"
    picture = _shape(PIL.new("RGBA", (128, 128), BRICK), "1")
    inner = _archive({f"{name}.apt": b"Apt Data" + bytes(24), f"{name}.msh": picture})
    pictures = _archive({f"{name}.big": refpack_compress(inner) + bytes(SLACK)})
    members = {
        part.format(22, n): _packed(_uniform() if part.startswith("uni") else _numbers())
        for n in (0, 1)
        for part in jerseys.PARTS
    }
    _, edited, changed, drawn = jerseys.dress(_archive(members), pictures, [22], {22: crest()})
    assert (changed, drawn) == (2, 1)
    e = bigf_parse(edited)[0]
    copy = screens.Copy(jerseys.PICTURES, e.name, f"{name}.msh", "1")
    out = screens.read_picture(edited[e.offset : e.offset + e.size], copy)
    assert out.size == (128, 128) and max(out.getpixel((5, 5))[:3]) < 60
