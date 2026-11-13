"""`nhl05_ps2.jerseys`: the Jets wear the classics the Coyotes' slot carries."""

import io
import struct

import pytest

from retro_roster_patcher.formats import ssh
from retro_roster_patcher.formats.ea_tdb import (
    BigfEntry,
    TDBFile,
    bigf_build,
    bigf_extract,
    bigf_parse,
    refpack_compress,
    refpack_decompress,
)
from retro_roster_patcher.games.nhl05_ps2 import jerseys, screens
from tests.fixtures.synthetic_tdb import TYPE_UINT, FieldSpec, TableSpec, build_tdb, pack_bits
from tests.formats.test_ssh import packed_shape, shape

PIL = pytest.importorskip("PIL.Image")
ImageDraw = pytest.importorskip("PIL.ImageDraw")


def uniforms(arts_and_jerseys):
    """A `JERSEYS.VIV`: each jersey's three members, their bytes naming them."""
    members = {
        part.format(art, n): f"data of {part.format(art, n)}".encode() * 4
        for art, n in arts_and_jerseys
        for part in jerseys.PARTS
    }
    return bigf_build([BigfEntry(n, 0, 0) for n in members], members)


def test_a_shared_jersey_reads_the_other_jerseys_data_and_nothing_moves():
    archive = uniforms([(1, 0), (1, 1), (22, 6), (22, 8)])
    edited, changed = jerseys.share(archive, {1: {0: (22, 8), 1: (22, 6)}})
    assert changed == 2 and len(edited) == len(archive)
    for part in jerseys.PARTS:
        assert bigf_extract(edited, part.format(1, 0)) == bigf_extract(archive, part.format(22, 8))
        assert bigf_extract(edited, part.format(1, 1)) == bigf_extract(archive, part.format(22, 6))
        assert bigf_extract(edited, part.format(22, 8)) == bigf_extract(archive, part.format(22, 8))
    # Only the directory changed: every member's bytes are where they were.
    first = min(e.offset for e in bigf_parse(archive))
    assert edited[first:] == archive[first:]


def test_a_jersey_either_side_lacks_a_member_of_stays():
    members = {
        "uni001_0.ssh": b"a" * 40,
        "num001_0.ssh": b"b" * 40,
        "letters_001_0.ssh": b"c" * 40,
        "uni022_8.ssh": b"d" * 40,  # no numbers or letters
    }
    archive = bigf_build([BigfEntry(n, 0, 0) for n in members], members)
    assert jerseys.share(archive, {1: {0: (22, 8)}}) == (archive, 0)


def picture_member(name, colour):
    """One `JERS_<abbr>_<nn>.big`: a compressed BIGF of the movie and the
    jersey picture (the front end's two-bit packing), drawn in `colour`."""
    empty = packed_shape(64, 64, ssh.PACKED_FINE)
    shape = ssh.encode(empty, PIL.new("RGBA", (64, 64), colour))
    return refpack_compress(
        bigf_build(
            [BigfEntry(f"{name}.apt", 0, 0), BigfEntry(f"{name}.ssh", 0, 0)],
            {f"{name}.apt": b"Apt " + bytes(60), f"{name}.ssh": shape},
        )
    )


def pictures():
    members = {
        "JERS_ATL_00.big": picture_member("JERS_ATL_00", (200, 30, 30, 255)),
        "JERS_PHO_08.big": picture_member("JERS_PHO_08", (30, 40, 200, 255)),
    }
    return bigf_build([BigfEntry(n, 0, 0) for n in members], members)


def read(archive, name):
    entry = next(e for e in bigf_parse(archive) if e.name == f"{name}.big")
    copy = screens.Copy(jerseys.PICTURES, entry.name, f"{name}.ssh")
    return screens.read_picture(archive[entry.offset : entry.offset + entry.size], copy)


def test_a_menu_picture_is_redrawn_from_the_jersey_it_now_wears():
    archive = pictures()
    edited, drawn = jerseys.redraw_pictures(archive, {("ATL", 0): ("PHO", 8)})
    assert drawn == 1 and len(edited) == len(archive)
    r, g, b, _ = read(edited, "JERS_ATL_00").getpixel((32, 32))
    assert b > 150 and r < 80
    assert read(edited, "JERS_PHO_08").getpixel((32, 32)) == read(archive, "JERS_PHO_08").getpixel(
        (32, 32)
    )


def test_dress_gives_the_jets_slot_the_classics():
    archive = uniforms([(1, 0), (1, 1), (1, 2), (22, 6), (22, 7), (22, 8)])
    jersey_archive, picture_archive, shared, drawn = jerseys.dress(archive, pictures(), [1], {})
    assert (shared, drawn) == (3, 1)  # the fixture has one menu picture
    assert bigf_extract(jersey_archive, "uni001_1.ssh") == bigf_extract(archive, "uni022_6.ssh")
    assert jerseys.dress(archive, pictures(), [0, 22], {})[2:] == (0, 0)  # Utah needs a crest


# -- Utah, drawn from the Coyotes' jerseys -------------------------------------

BRICK = (140, 38, 51, 255)
WHITE = (235, 235, 235, 255)


def multi(blocks):
    """A `ShpS` of named shapes, each a pixel block and its palette."""
    directory = b"".join(b"\0" * 8 + name.encode() + b"\0" for name, _ in blocks)
    start = 0x10 + len(directory)
    start += -start % 16
    head = bytearray(ssh.MAGIC + bytes(12) + directory + bytes(start - 0x10 - len(directory)))
    struct.pack_into(">I", head, 8, len(blocks))
    body, pos = b"", 0x10
    for name, block in blocks:
        struct.pack_into(">II", head, pos, start + len(body), len(block))
        pos += 8 + len(name) + 1
        body += block
    return bytes(head) + body


def four_bit(colours):
    """A 4-bit 16x16 pixel block with a 16-colour palette."""
    pixels = bytearray(0x20)
    pixels[0] = 0x01
    struct.pack_into("<I", pixels, 4, 0x20 + 128)
    struct.pack_into("<II", pixels, 0x18, 16, 16)
    palette = bytearray(0x20)
    palette[0] = ssh.PALETTE
    return bytes(pixels) + bytes(128) + bytes(palette) + b"".join(bytes(c) for c in colours)


def coyotes_home():
    """A brick jersey: a white crest on the chest, white letters on the pants."""
    torso = PIL.new("RGBA", (512, 512), BRICK)
    pen = ImageDraw.Draw(torso)
    x0, y0, x1, y1 = jerseys.CHEST
    pen.rectangle((x0 + 20, y0 + 20, x1 - 20, y1 - 20), fill=WHITE)
    for box in jerseys.PANTS:
        pen.rectangle((box[0] + 10, box[1] + 8, box[0] + 30, box[1] + 16), fill=WHITE)
    return torso


def uniform_member():
    blocks = [("TORS", ssh.encode(packed_shape(512, 512, ssh.PACKED_TIGHT), coyotes_home())[0x20:])]
    blocks.append(("TORL", ssh.encode(shape(64, 64), PIL.new("RGBA", (64, 64), BRICK))[0x20:]))
    for n in range(1, 6):
        helmet = ssh.encode(
            packed_shape(64, 16, ssh.PACKED_TIGHT), PIL.new("RGBA", (64, 16), BRICK)
        )
        blocks.append((f"HEL{n}", helmet[0x20:]))
    return multi(blocks)


def utah_archive(art=22):
    numbers = multi([("NMA0", four_bit([BRICK] * 8 + [WHITE] * 8))])
    members = {
        f"uni{art:03d}_1.ssh": uniform_member(),
        f"num{art:03d}_1.ssh": numbers,
        f"letters_{art:03d}_1.ssh": numbers,
    }
    packed = {name: refpack_compress(data) + bytes(4096) for name, data in members.items()}
    return bigf_build([BigfEntry(n, 0, 0) for n in packed], packed)


def utah_crest():
    picture = PIL.new("RGBA", (120, 80), (0, 0, 0, 0))
    ImageDraw.Draw(picture).ellipse((0, 0, 119, 79), fill=(110, 170, 225, 255))
    out = io.BytesIO()
    picture.save(out, "PNG")
    return out.getvalue()


def test_the_template_colour_turns_to_the_clubs_and_the_rest_stays():
    picture = PIL.new("RGBA", (3, 1))
    picture.putdata([BRICK, WHITE, (40, 40, 200, 128)])
    out = jerseys.recolour(picture, 352 / 360, (0, 0, 0))
    assert max(out.getpixel((0, 0))[:3]) < 40  # black, its folds kept
    assert out.getpixel((1, 0)) == WHITE and out.getpixel((2, 0)) == (40, 40, 200, 128)


def test_the_torso_takes_the_crest_and_loses_the_old_art():
    torso = jerseys.restyle_torso(coyotes_home(), jerseys.RESTYLED[22], utah_crest())
    x0, y0, x1, y1 = jerseys.CHEST
    r, g, b, _ = torso.getpixel(((x0 + x1) // 2, (y0 + y1) // 2))
    assert b > 180 and r < 140  # the crest
    assert max(torso.getpixel((x0 + 22, y0 + 22))[:3]) < 60  # the old crest's corner, now fabric
    box = jerseys.PANTS[0]
    assert max(torso.getpixel((box[0] + 20, box[1] + 12))[:3]) < 60  # the letters


def test_utahs_jersey_and_numbers_are_drawn_in_place():
    archive = utah_archive()
    style = jerseys.Restyle(22, "PHO", (1,), 352 / 360, (0, 0, 0))
    edited, drawn = jerseys.restyle(archive, style, utah_crest())
    assert drawn == 1 and len(edited) == len(archive)
    uniform = refpack_decompress(bigf_extract(edited, "uni022_1.ssh"))
    assert max(ssh.decode(uniform, "HEL3").getpixel((5, 5))[:3]) < 60
    numbers = refpack_decompress(bigf_extract(edited, "num022_1.ssh"))
    pal = len(numbers) - 64
    assert max(numbers[pal : pal + 3]) < 60 and numbers[pal + 32 : pal + 35] == bytes(WHITE[:3])


def test_utah_offers_its_two_drawn_jerseys_alone():
    fields = [FieldSpec("INDX", TYPE_UINT, 0, 8), FieldSpec("JRSY", TYPE_UINT, 8, 4)]
    rows = [{"INDX": 1, "JRSY": 3}, {"INDX": 22, "JRSY": 9}]
    data = b"".join(pack_bits(fields, row, 2) for row in rows)
    tdb = TDBFile.parse(build_tdb([TableSpec("STEA", fields, 2, 2, 2, data)]))
    jerseys.limit(tdb, [1, 22])
    assert [tdb.get_table("STEA").read_record(i)["JRSY"] for i in range(2)] == [3, 2]


# -- Seattle and Vegas, drawn from the All-Star sides' jerseys -----------------

RED = (90, 0, 7, 255)
CREAM = (215, 205, 190, 255)


def eastern_home():
    """The East's red home jersey: cream "EASTERN" down the front (one block
    per letter, on the line the disc's run along), and the laces."""
    torso = PIL.new("RGBA", (512, 512), RED)
    pen = ImageDraw.Draw(torso)
    for x, y in ((192, 332), (210, 355), (229, 372), (249, 392), (269, 412), (292, 432)):
        pen.rectangle((x - 7, y - 11, x + 7, y + 11), fill=CREAM)
    pen.rectangle((246, 300, 262, 330), fill=CREAM)  # the laces
    return torso


def test_the_all_star_front_loses_its_lettering_and_takes_the_crest():
    torso = jerseys.restyle_torso(eastern_home(), jerseys.RESTYLED[30], utah_crest())
    for x, y in ((192, 332), (292, 432)):  # letters, now the club's navy
        r, g, b, _ = torso.getpixel((x, y))
        assert b > r + 20 and max(r, g, b) < 110, (x, y)
    x0, y0, x1, y1 = jerseys.CHEST
    r, g, b, _ = torso.getpixel(((x0 + x1) // 2, (y0 + y1) // 2))
    assert b > 180 and r > 90  # the crest
    assert torso.getpixel((254, 305))[:3] == CREAM[:3]  # the laces stay


def test_dress_draws_seattle_and_vegas_from_the_all_star_jerseys():
    for slot, art in ((30, 30), (31, 31)):
        archive = utah_archive(art)
        edited, _, drawn, _ = jerseys.dress(archive, pictures(), [slot], {slot: utah_crest()})
        assert drawn == 1 and len(edited) == len(archive)
        assert bigf_extract(edited, f"uni{art:03d}_1.ssh") != bigf_extract(
            archive, f"uni{art:03d}_1.ssh"
        )
