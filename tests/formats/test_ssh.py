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


def packed_shape(width: int, height: int) -> bytes:
    """A one-shape packed `ShpS` (kind 8): the seven-bit image and the plane."""
    head = bytearray(0x20)
    head[:4] = ssh.MAGIC
    struct.pack_into(">I", head, 0x10, 0x20)
    size = width * height * 5 // 8
    pixels = bytearray(0x20)
    pixels[0] = ssh.PACKED
    struct.pack_into("<I", pixels, 4, 0x20 + size)
    struct.pack_into("<I", pixels, 0xC, size)
    struct.pack_into("<II", pixels, 0x18, width, height)
    pixels += bytes(size)
    palette = bytearray(0x20)
    palette[0] = ssh.PALETTE
    return bytes(head) + bytes(pixels) + bytes(palette) + bytes(1024)


def test_a_packed_picture_round_trips_in_pairs_of_shades():
    # A gradient with a transparent corner: the picture comes back close, the
    # file keeps its size, and the plane adds a shade to every second row.
    picture = PIL.new("RGBA", (64, 64))
    picture.putdata(
        [(x * 4, y * 4, 128, 0 if x < 8 and y < 8 else 255) for y in range(64) for x in range(64)]
    )
    data = ssh.encode(packed_shape(64, 64), picture)
    assert len(data) == len(packed_shape(64, 64))
    out = ssh.decode(data)
    assert out.getpixel((0, 0))[3] == 0 and out.getpixel((40, 40))[3] == 255
    error = sum(
        abs(a - b)
        for y in range(64)
        for x in range(8, 64)
        for a, b in zip(out.getpixel((x, y))[:3], picture.getpixel((x, y))[:3], strict=True)
    ) / (56 * 64 * 3)
    assert error < 6
    indices, _, _ = ssh.indices(data)
    assert all(
        indices[y * 64 + x] >> 1 == indices[(y + 1) * 64 + x] >> 1
        for y in range(0, 64, 2)
        for x in range(64)
    )
    assert any(
        indices[y * 64 + x] != indices[(y + 1) * 64 + x] for y in range(0, 64, 2) for x in range(64)
    )


def test_a_short_packed_shape_is_refused():
    with pytest.raises(ssh.SshError):
        ssh.decode(packed_shape(64, 64)[:2000])
