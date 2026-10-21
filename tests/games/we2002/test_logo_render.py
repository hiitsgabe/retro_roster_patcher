"""`render_logo` needs Pillow, the optional `images` extra."""

import io

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

from retro_roster_patcher.games.we2002.logo_render import render_logo  # noqa: E402


def _png(size=(200, 200), colours=((200, 0, 0, 255), (0, 0, 200, 255))):
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    w, h = size
    draw.ellipse((w // 10, h // 10, w - w // 10, h - h // 10), fill=colours[0])
    draw.rectangle((w // 3, h // 3, 2 * w // 3, 2 * h // 3), fill=colours[1])
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_every_slot_has_its_size_and_indices_stay_in_range():
    images = render_logo(_png())
    for pixels, (w, h) in (
        (images.big, (64, 28)),
        (images.medium, (24, 16)),
        (images.mini, (24, 8)),
    ):
        assert len(pixels) == h and all(len(row) == w for row in pixels)
        assert all(0 <= v <= 15 for row in pixels for v in row)
    assert len(images.palette) == 15


def test_transparent_background_is_index_zero_and_the_logo_is_not():
    images = render_logo(_png())
    assert images.big[0][0] == 0  # a corner, outside the circle
    centre = images.big[14][32]
    assert centre != 0
    # The medium and mini renders draw from the same palette as the big one.
    assert images.medium[8][12] != 0 and images.mini[4][12] != 0


def test_the_palette_carries_the_logo_colours():
    images = render_logo(_png())
    reds = [c for c in images.palette if c[0] > 150 and c[2] < 80]
    blues = [c for c in images.palette if c[2] > 150 and c[0] < 80]
    assert reds and blues


def test_a_fully_transparent_image_renders_as_empty_cells():
    im = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    images = render_logo(buf.getvalue())
    assert all(v == 0 for row in images.big for v in row)


def test_a_wide_logo_is_centred_without_distortion():
    images = render_logo(_png(size=(400, 100), colours=((0, 150, 0, 255), (0, 150, 0, 255))))
    # 400x100 fits 64x28 at 64x16: rows 0-5 and 22-27 stay transparent.
    assert all(v == 0 for v in images.big[0]) and all(v == 0 for v in images.big[27])
    assert any(v for v in images.big[14])


def test_garbage_bytes_raise():
    with pytest.raises(OSError):
        render_logo(b"not a png")
