"""`formats.ssh`: an 8-bit PS2 shape survives encode then decode."""

import struct

import pytest

from retro_roster_patcher.formats import ssh

PIL = pytest.importorskip("PIL.Image")


def shape(width: int, height: int) -> bytes:
    """A one-shape `ShpS`: header, 8-bit pixel block, then a palette block."""
    head = bytearray(0x20)
    head[:4] = ssh.MAGIC
    struct.pack_into(">I", head, 0x10, 0x20)
    pixels = bytearray(0x20)
    pixels[0] = ssh.PIXELS
    struct.pack_into("<I", pixels, 4, 0x20 + width * height)
    struct.pack_into("<II", pixels, 0x18, width, height)
    pixels += bytes(width * height)
    palette = bytearray(0x20)
    palette[0] = ssh.PALETTE
    return head + bytes(pixels) + bytes(palette) + bytes(1024)


def test_a_picture_round_trips_through_encode_and_decode():
    picture = PIL.new("RGBA", (4, 2), (0, 0, 0, 0))
    picture.putpixel((0, 0), (255, 0, 0, 255))
    picture.putpixel((3, 1), (0, 0, 255, 255))
    out = ssh.decode(ssh.encode(shape(4, 2), picture))
    assert out.getpixel((0, 0)) == (255, 0, 0, 255)
    assert out.getpixel((3, 1)) == (0, 0, 255, 255)
    assert out.getpixel((1, 0))[3] == 0


def test_not_a_shape_is_refused():
    with pytest.raises(ssh.SshError):
        ssh.decode(b"nope" + bytes(64))
