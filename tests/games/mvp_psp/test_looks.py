"""MVP Baseball looks: the attrib face, skin, hair and beard columns."""

from retro_roster_patcher.core.headshots import Face
from retro_roster_patcher.games.mvp_psp import looks


def test_a_measured_face_picks_a_head_of_its_complexion():
    pale = looks.columns(Face(55.0, "black", False), "USA", "1")
    assert pale[12] in looks.HEADS["white"][0] and pale[13] == 0 and pale[15] == 5
    dark = looks.columns(Face(-40.0, "black", True), "Dominican Republic", "2")
    assert dark[12] in looks.HEADS["black"][0] and dark[13] == 10
    assert (dark[14], dark[16]) == (7, looks.BEARD)


def test_an_east_asian_player_and_a_player_without_a_photo():
    assert looks.columns(None, "Japan", "3")[13] == looks.EAST_ASIAN_SKIN
    assert looks.columns(None, "USA", "4") == looks.DEFAULT
