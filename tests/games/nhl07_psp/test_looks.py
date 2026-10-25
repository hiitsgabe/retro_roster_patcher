"""Skin tone from the head shot, cut into the shades NHL 07 draws, and the
no-photo pick weighted like the retail disc."""

from collections import Counter

from retro_roster_patcher.core.headshots import Face
from retro_roster_patcher.games.nhl07_psp import looks
from retro_roster_patcher.games.nhl07_psp.models import NHL07PlayerRecord


def test_a_measured_face_lands_on_the_games_shades():
    angles = (50.0, 30.0, 10.0, -10.0, -30.0, -50.0)
    assert [looks.skin(7, ita) for ita in angles] == [1, 2, 3, 5, 6, 7]


def test_without_a_photo_the_pick_is_stable_and_weighted_like_the_disc():
    picks = [looks.skin(pid, None) for pid in range(3000)]
    assert picks == [looks.skin(pid, None) for pid in range(3000)]
    assert set(picks) <= set(looks.SKIN_VALUES)
    shares = Counter(picks)
    assert (shares[1] + shares[2]) / 3000 > 0.85 and shares[7] < 30


def test_dress_sets_every_records_skin():
    records = [NHL07PlayerRecord(first_name="A", last_name=f"B{i}", player_id=i) for i in range(5)]
    looks.dress(records, {2: Face(-40.0, None), 3: Face(None, "black")})
    assert records[2].skin == 6
    assert all(r.skin in looks.SKIN_VALUES for r in records)
