"""Skin tone from a head shot, on synthetic ESPN-framed pictures."""

import io

import pytest

from retro_roster_patcher.core.skin import face_ita, tone

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

from tests.fixtures.synthetic_headshot import headshot  # noqa: E402

LIGHT = (232, 190, 160)
DARK = (95, 60, 40)
CUTS = (30.0, 18.0, 10.0, -10.0, -40.0)


CAP = (20, 30, 70)


def _headshot(face, *, transparent=True, beard=False):
    return headshot(face, cap=CAP, transparent=transparent, beard=beard)


@pytest.mark.parametrize("transparent", [True, False])
def test_a_light_face_measures_light_and_a_dark_one_dark(transparent):
    light = face_ita(_headshot(LIGHT, transparent=transparent))
    dark = face_ita(_headshot(DARK, transparent=transparent))
    assert light is not None and dark is not None
    assert light > 40 and dark < -40
    assert (tone(light, CUTS), tone(dark, CUTS)) == (0, 5)


def test_a_beard_does_not_move_the_measure():
    plain = face_ita(_headshot(LIGHT))
    bearded = face_ita(_headshot(LIGHT, beard=True))
    assert plain is not None and bearded is not None
    assert abs(plain - bearded) < 1


def test_an_empty_picture_has_no_face():
    out = io.BytesIO()
    Image.new("RGBA", (600, 436), (0, 0, 0, 0)).save(out, "PNG")
    assert face_ita(out.getvalue()) is None


def test_a_grey_silhouette_has_no_face():
    # What a provider sends for a player without a photo.
    assert face_ita(_headshot((128, 128, 128))) is None


def test_an_opaque_picture_of_one_colour_has_no_face():
    out = io.BytesIO()
    Image.new("RGB", (300, 200), LIGHT).save(out, "JPEG")
    assert face_ita(out.getvalue()) is None


def test_a_picture_pillow_cannot_read_raises():
    with pytest.raises(Exception):  # noqa: B017 - whatever Pillow raises
        face_ita(b"\x89PNG\r\n\x1a\nnot really")


def test_tone_counts_the_cuts_an_angle_is_below():
    assert [tone(a, CUTS) for a in (60, 30, 29.9, 15, 0, -20, -60)] == [0, 0, 1, 2, 3, 4, 5]
    tones = [tone(float(a), CUTS) for a in range(-90, 91)]
    assert tones == sorted(tones, reverse=True) and set(tones) == set(range(6))


def test_tone_fits_any_number_of_tones():
    assert [tone(a, (0.0,)) for a in (10, -10)] == [0, 1]
    assert tone(5, ()) == 0


def test_a_flash_highlight_on_the_forehead_does_not_lighten_the_measure():
    """Real faces shade smoothly from a lit brow to a shadowed chin, and a
    studio flash adds one flat bright patch over a third of the face box.
    The measure follows the typical skin, not that one dense bright cluster
    (the histogram mode did, and read dark faces as pale)."""

    def shaded(shine):
        picture = Image.new("RGBA", (600, 436), (0, 0, 0, 0))
        draw = ImageDraw.Draw(picture)
        for i in range(19):
            t = i / 18
            colour = (int(150 - 90 * t), int(100 - 62 * t), int(70 - 45 * t), 255)
            draw.rectangle((220, 110 + i * 10, 380, 120 + i * 10), fill=colour)
        if shine:
            draw.ellipse((236, 142, 364, 218), fill=(210, 168, 142, 255))
        draw.rectangle((215, 50, 385, 150), fill=(20, 30, 70, 255))
        draw.rectangle((60, 330, 540, 436), fill=(250, 250, 250, 255))
        out = io.BytesIO()
        picture.save(out, "PNG")
        return face_ita(out.getvalue())

    plain, shiny = shaded(False), shaded(True)
    assert plain is not None and shiny is not None
    assert shiny < 0 and shiny - plain < 30, (plain, shiny)
