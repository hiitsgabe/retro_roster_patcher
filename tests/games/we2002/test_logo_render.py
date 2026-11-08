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


def test_the_stadium_rendering_sits_on_white_and_shares_the_menu_palette():
    from retro_roster_patcher.games.we2002.logo_render import COLOURS, render_logos

    menu, stadium = render_logos(_png())
    assert menu.palette == stadium.palette and menu.palette[-1] == (255, 255, 255)
    assert menu.big[0][0] == 0 and stadium.big[0][0] == COLOURS  # the corner: see-through vs white
    assert menu.big[14][32] == stadium.big[14][32] != 0  # the logo itself is the same
    assert all(v != 0 for row in stadium.big for v in row)
    assert all(v != 0 for row in stadium.mini for v in row)


def _strokes_png(colour=(200, 0, 0, 255)):
    """A 500 px crest drawn in 10 px strokes, like Liverpool's or Forest's line art."""
    im = Image.new("RGBA", (500, 500), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    draw.ellipse((20, 20, 480, 480), outline=colour, width=10)
    for x in range(100, 420, 60):
        draw.line((x, 80, x + 40, 420), fill=colour, width=10)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _ink(png: bytes, size: tuple[int, int]) -> int:
    """The pixels the strokes physically cover once `png` is fitted to `size` (box filter)."""
    source = Image.open(io.BytesIO(png)).convert("RGBA")
    source = source.crop(source.getchannel("A").getbbox())
    scale = min(size[0] / source.width, size[1] / source.height)
    inner = (round(source.width * scale), round(source.height * scale))
    alpha = source.resize(inner, Image.Resampling.BOX).getchannel("A").tobytes()
    return sum(1 for a in alpha if a)


def test_thin_strokes_survive_the_downsize():
    """Liverpool, Forest and Spurs drew as a few dots: an alpha cut of 128 dropped
    their thin strokes at 24x16 and 64x28 (issue #4)."""
    png = _strokes_png()
    images = render_logo(png)
    for pixels, size in ((images.medium, (24, 16)), (images.big, (64, 28))):
        drawn = sum(1 for row in pixels for v in row if v)
        assert drawn >= 0.6 * _ink(png, size), size


def test_a_dark_crest_gets_a_white_outline_on_the_menus():
    """Spurs' navy crest vanished on the navy team grid and VS banner."""
    from retro_roster_patcher.games.we2002.logo_render import COLOURS, render_logos

    png = _png(colours=((19, 34, 87, 255), (19, 34, 87, 255)))
    menu, stadium = render_logos(png)
    assert menu.palette[COLOURS - 1] == (255, 255, 255)
    for pixels in (menu.big, menu.medium):
        h, w = len(pixels), len(pixels[0])
        ink = {(x, y) for y in range(h) for x in range(w) if pixels[y][x] not in (0, COLOURS)}
        ring = {
            (x + dx, y + dy)
            for x, y in ink
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
            if 0 <= x + dx < w and 0 <= y + dy < h
        } - ink
        assert ring and all(pixels[y][x] == COLOURS for x, y in ring)
        assert pixels[0][0] == 0  # outside the outline stays see-through
    assert stadium.big[0][0] == COLOURS  # the stadium copy is unchanged: white ground


def test_a_bright_crest_gets_no_outline():
    from retro_roster_patcher.games.we2002.logo_render import COLOURS

    images = render_logo(_png(colours=((200, 16, 46, 255), (246, 200, 40, 255))))  # red and gold
    assert all(v != COLOURS for row in images.big for v in row)
