"""Skin tone from the head shot, cut into the shades NHL 07 draws, and the
no-photo pick weighted like the retail disc."""

from retro_roster_patcher.core.headshots import Face
from retro_roster_patcher.games.nhl07_psp import looks
from retro_roster_patcher.games.nhl07_psp.models import NHL07PlayerRecord


def test_a_measured_face_lands_on_the_games_shades_lightest_first():
    """By the disc's own faces: 5 is the darkest (Laraque, Brashear, Weekes),
    6 next (Iginla, Grier, Emery); 7 marks four pale players and is not a
    shade at all."""
    angles = (50.0, 30.0, 10.0, -10.0, -30.0)
    assert [looks.skin(ita) for ita in angles] == [1, 2, 3, 6, 5]


def test_without_a_photo_the_shade_is_the_lightest():
    assert looks.skin(None) == 1


def test_dress_sets_every_records_skin():
    records = [NHL07PlayerRecord(first_name="A", last_name=f"B{i}", player_id=i) for i in range(5)]
    looks.dress(records, {2: Face(-40.0, None), 3: Face(None, "black")})
    assert records[2].skin == 5
    assert all(r.skin in looks.SKIN_VALUES for r in records)
