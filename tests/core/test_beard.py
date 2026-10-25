"""A beard read from a head shot: dark, hair-like pixels below the face box."""

import pytest

pytest.importorskip("PIL")

from retro_roster_patcher.core.beard import beard, beard_of  # noqa: E402
from retro_roster_patcher.core.skin import _frame, face_lab  # noqa: E402
from tests.fixtures.synthetic_headshot import headshot  # noqa: E402

FACE = (225, 180, 150)
DARK_FACE = (95, 60, 40)


def test_a_beard_is_found_below_the_face():
    assert beard(headshot(FACE, hair=(22, 16, 12), beard=True)) is True


def test_a_clean_shaven_face_has_none():
    assert beard(headshot(FACE, hair=(22, 16, 12))) is False


def test_a_dark_clean_shaven_face_has_none():
    assert beard(headshot(DARK_FACE, hair=(10, 8, 6))) is False


def test_a_picture_without_a_face_says_nothing():
    assert beard(headshot((128, 128, 128))) is None


def test_beard_of_reads_the_frame_the_other_measures_share():
    png = headshot(FACE, hair=(22, 16, 12), beard=True)
    frame = _frame(png)
    assert frame is not None
    skin = face_lab(frame)
    assert skin is not None
    assert beard_of(frame, skin) is True
