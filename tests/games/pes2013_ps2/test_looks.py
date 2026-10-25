"""Skin tone from the head shot, cut into the six colours PES 2013 draws
(byte 115 bits 0-2, ordered by the lightness PCSX2 showed), the East Asian
colour by nationality, and the plain default without a photo."""

from retro_roster_patcher.core.headshots import Face
from retro_roster_patcher.games.pes2013_ps2 import looks
from retro_roster_patcher.games.pes2013_ps2.models import PESLook, PESPlayer


def test_a_measured_face_lands_on_the_games_colours_lightest_first():
    assert [looks.skin("", ita) for ita in (40.0, 22.0, 10.0, -10.0, -40.0)] == [0, 4, 2, 5, 3]


def test_east_asian_nationals_get_colour_one_whatever_the_photo():
    assert looks.skin("Japan", 60.0) == 1 and looks.skin("South Korea", None) == 1


def test_without_a_photo_the_colour_is_the_palest():
    assert looks.skin("Brazil", None) == 0


def test_dress_gives_every_player_a_look():
    players = [
        PESPlayer("A", "A", player_id=1),
        PESPlayer("B", "B", player_id=2, nationality="China"),
    ]
    looks.dress(players, {1: Face(-40.0, None)})
    assert [p.look for p in players] == [PESLook(skin=3), PESLook(skin=1)]
