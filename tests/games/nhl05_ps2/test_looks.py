"""`nhl05_ps2.looks`: a player's skin tone from his head shot."""

from retro_roster_patcher.core.headshots import Face
from retro_roster_patcher.games.nhl05_ps2 import looks
from retro_roster_patcher.games.nhl05_ps2.models import NHL05PlayerRecord


def test_the_shades_run_from_pale_to_dark_down_the_ita_scale():
    assert [looks.skin(ita) for ita in (60.0, 30.0, 10.0, -10.0, -30.0)] == [1, 2, 3, 6, 5]


def test_without_a_photo_the_plain_default_is_the_first_shade():
    assert looks.skin(None) == looks.DEFAULT_SKIN == 1


def test_an_east_asian_player_takes_richard_parks_shade():
    assert looks.skin(50.0, "KOR") == looks.EAST_ASIAN_SKIN == 4
    assert looks.skin(None, "Japan") == 4


def test_dress_sets_each_record_from_its_measured_face():
    records = [
        NHL05PlayerRecord(player_id=1, nationality="CAN"),
        NHL05PlayerRecord(player_id=2, nationality="CAN"),
        NHL05PlayerRecord(player_id=0),
    ]
    looks.dress(records, {1: Face(-30.0, None)})
    assert [r.skin for r in records] == [5, 1, 1]
