"""Skin tone from the head shot, cut into the six colours PES 2013 draws
(byte 115 bits 0-2, ordered by the lightness PCSX2 showed), the East Asian
colour by nationality, and the plain default without a photo."""

from retro_roster_patcher.core.headshots import Face
from retro_roster_patcher.games.pes2013_ps2 import looks
from retro_roster_patcher.games.pes2013_ps2.models import PESPlayer


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
    assert [(p.look.skin, p.look.hair, p.look.beard) for p in players] == [
        (3, "black", False),
        (1, "black", False),
    ]


def test_dress_takes_the_hair_colour_and_the_beard_from_the_photo():
    players = [PESPlayer("A", "A", player_id=1), PESPlayer("B", "B", player_id=2)]
    looks.dress(players, {1: Face(40.0, "blond", True), 2: Face(40.0, "bald", False)})
    assert (players[0].look.hair, players[0].look.beard) == ("blond", True)
    assert (players[1].look.hair, players[1].look.style) == ("bald", looks.BALD_STYLE)


def test_without_a_photo_the_look_is_the_plain_default():
    """Pale skin, black hair, no beard: what the user asked for."""
    player = PESPlayer("A", "A", player_id=7)
    looks.dress([player], {})
    assert (player.look.skin, player.look.hair, player.look.beard) == (0, "black", False)


def test_the_hairstyle_is_a_plain_short_one_and_the_same_every_patch():
    players = [PESPlayer(str(i), str(i), player_id=i) for i in range(1, 40)]
    looks.dress(players, {})
    styles = [p.look.style for p in players]
    assert set(styles) <= set(looks.HAIR_STYLES) and len(set(styles)) > 1
    again = [PESPlayer(str(i), str(i), player_id=i) for i in range(1, 40)]
    looks.dress(again, {})
    assert [p.look.style for p in again] == styles


def test_every_hair_colour_has_an_rgb_the_editor_takes():
    assert set(looks.HAIR_RGB) >= {"black", "dark_brown", "brown", "blond", "red", "grey"}
    assert all(0 <= c <= 63 for rgb in looks.HAIR_RGB.values() for c in rgb)


def test_the_disc_player_mapping_is_well_formed():
    """Records of the main table (4941) and, from `LIBERTADORES_BASE`, of the
    South American clubs' table (943)."""
    from retro_roster_patcher.games.pes2013_ps2.models import LIBERTADORES_BASE

    mapping = looks.disc_players()
    assert mapping and all(isinstance(k, int) and isinstance(v, int) for k, v in mapping.items())
    assert all(0 < v < 4941 or 0 <= v - LIBERTADORES_BASE < 943 for v in mapping.values())
    assert any(v >= LIBERTADORES_BASE for v in mapping.values())
    assert len(set(mapping.values())) == len(mapping)  # one disc record per player


def test_dress_names_the_disc_record_of_a_player_already_on_the_disc():
    players = [PESPlayer("A", "A", player_id=1), PESPlayer("B", "B", player_id=2)]
    looks.dress(players, {}, disc_players={2: 1206})
    assert (players[0].look.disc_record, players[1].look.disc_record) == (None, 1206)
