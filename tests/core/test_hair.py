"""Hair colour from a head shot, on synthetic ESPN-framed pictures."""

import pytest

from retro_roster_patcher.core.hair import COLOURS, hair_colour

pytest.importorskip("PIL")
from tests.fixtures.synthetic_headshot import headshot  # noqa: E402

FACE = (225, 180, 150)


@pytest.mark.parametrize(
    "hair, expected",
    [
        ((22, 16, 12), "black"),
        ((70, 45, 30), "dark_brown"),
        ((85, 58, 40), "brown"),
        ((215, 185, 115), "blond"),
        ((165, 60, 30), "red"),
        ((175, 175, 175), "grey"),
    ],
)
def test_each_hair_colour_is_read(hair, expected):
    assert hair_colour(headshot(FACE, hair=hair)) == expected


def test_a_band_of_skin_is_bald():
    assert hair_colour(headshot(FACE)) == "bald"


def test_a_cut_out_with_no_face_has_no_hair():
    assert hair_colour(headshot((128, 128, 128), hair=(20, 20, 20))) is None


def test_an_opaque_picture_works_too():
    assert hair_colour(headshot(FACE, hair=(22, 16, 12), transparent=False)) == "black"


def test_every_answer_is_a_listed_colour():
    for hair in ((22, 16, 12), (215, 185, 115), None):
        assert hair_colour(headshot(FACE, hair=hair)) in COLOURS
