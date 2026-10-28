"""`nhl05_ps2.faces`: the generic face, toned, over each new player's face."""

import os
import struct

import pytest

from retro_roster_patcher.formats import ssh
from retro_roster_patcher.formats.ea_tdb import BigfEntry, bigf_build, bigf_parse, refpack_compress
from retro_roster_patcher.games.nhl05_ps2 import faces

PIL = pytest.importorskip("PIL.Image")


def block(kind: int, width: int, height: int, data: bytes, last: bool) -> bytes:
    head = bytearray(0x20)
    head[0] = kind
    head[1] = 1
    struct.pack_into("<I", head, 4, 0 if last else 0x20 + len(data))
    struct.pack_into("<I", head, 0xC, len(data))
    struct.pack_into("<II", head, 0x18, width, height)
    return bytes(head) + data


def face_file(pixels: bytes | None = None) -> bytes:
    """A `FACE<n>.ssh`: `FAC0`, a 16x16 4-bit thumbnail, and `FAC1`, a tight
    128x128 face, laid out as the disc lays them, with `pixels` as the face's
    data (random when not given, so the file compresses like a real one)."""
    thumb = block(0x01, 16, 16, bytes(128), False) + block(ssh.PALETTE, 16, 1, bytes(64), True)
    face = block(ssh.PACKED_TIGHT, 128, 128, pixels or os.urandom(6144), False)
    face += block(ssh.PALETTE, 256, 1, bytes(1024), True)
    names = [(b"FAC0", thumb), (b"FAC1", face)]
    directory = b""
    offset = 0x10 + sum(8 + len(n) + 1 for n, _ in names)
    offset = (offset + 0xF) & ~0xF
    body = b""
    for name, shape in names:
        directory += struct.pack(">II", offset + len(body), len(shape)) + name + b"\0"
        body += shape
    head = (
        ssh.MAGIC + struct.pack("<I", offset + len(body)) + struct.pack(">I", len(names)) + bytes(4)
    )
    return (head + directory).ljust(offset, b"\0") + body


def generic() -> bytes:
    picture = PIL.new("RGBA", (128, 128), (120, 90, 80, 255))
    for y in range(128):
        for x in range(128):
            if 24 < x < 104 and 20 < y < 110:
                picture.putpixel((x, y), (200 - (y - 60) // 4, 160 - (y - 60) // 4, 140, 255))
    return ssh.encode(face_file(bytes(6144)), picture, shape=faces.FACE)


def archive(members: dict[str, bytes]) -> bytes:
    packed = {name: refpack_compress(data) for name, data in members.items()}
    return bigf_build([BigfEntry(n, 0, 0) for n in packed], packed)


def cheeks(picture):
    from PIL import ImageStat

    means = [ImageStat.Stat(picture.convert("RGB").crop(box)).mean for box in faces.CHEEKS]
    return tuple(round(sum(m[c] for m in means) / 2) for c in range(3))


def test_a_new_players_face_is_the_generic_one_in_his_shade():
    before = archive(
        {"FACE9999.ssh": generic(), "FACE21.ssh": face_file(), "FACE33.ssh": face_file()}
    )
    out, drawn = faces.repack(before, {21: 5, 33: 1})
    assert drawn == 2 and len(out) == len(before)
    assert [e.size for e in bigf_parse(out)] == [e.size for e in bigf_parse(before)]
    dark, pale = faces.read_face(out, 21), faces.read_face(out, 33)
    assert cheeks(dark) == pytest.approx(faces.FACE_TONES[5], abs=10)
    assert cheeks(pale) == pytest.approx(faces.FACE_TONES[1], abs=10)
    assert faces.read_face(out, 9999).tobytes() == faces.read_face(before, 9999).tobytes()


def test_an_art_without_a_face_or_a_generic_is_skipped():
    with_generic = archive({"FACE9999.ssh": generic(), "FACE21.ssh": face_file()})
    assert faces.repack(with_generic, {44: 2}) == (with_generic, 0)
    without = archive({"FACE21.ssh": face_file()})
    assert faces.repack(without, {21: 2}) == (without, 0)


def test_the_thumbnail_follows_the_face():
    before = archive({"FACE9999.ssh": generic(), "FACE21.ssh": face_file()})
    out, _ = faces.repack(before, {21: 5})
    entry = next(e for e in bigf_parse(out) if e.name == "FACE21.ssh")
    from retro_roster_patcher.formats.ea_tdb import refpack_decompress

    data = refpack_decompress(out[entry.offset : entry.offset + entry.size])
    thumb = next(o for name, o, _ in ssh.shapes(data) if name == faces.THUMBNAIL)
    palette = data[thumb + 0x20 + 128 + 0x20 : thumb + 0x20 + 128 + 0x20 + 64]
    assert any(palette) and palette[3] == ssh.OPAQUE


def test_face_targets_keep_only_an_art_one_bio_alone_uses():
    art_of = {0: 7, 1: 7, 2: 9, 3: 0, 4: 12}
    assert faces.face_targets(art_of, {0: 1, 2: 5, 3: 2, 4: 3}) == {9: 5, 12: 3}
