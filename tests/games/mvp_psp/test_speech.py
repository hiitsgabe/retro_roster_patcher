"""`speech`: whose names the booth says, and the ids nobody's name answers to."""

from __future__ import annotations

import struct

from retro_roster_patcher.games.mvp_psp.speech import Voices, names


def test_the_name_events_keys_are_the_audio_ids_with_a_recorded_name():
    # Three headers: a name (event 89), a star's introduction (3207), a call
    # of something else keyed by a number that is no player's.
    keys = ((89, 739), (3207, 724), (219, 11))
    headers = b"".join(struct.pack("<HH", event, key) + bytes(8) for event, key in keys)
    assert names(headers, b"0,12,\n12,12,\n24,12,\n") == {739, 724}


def test_a_player_takes_the_lowest_id_no_name_answers_to():
    voices = Voices(named={5, 7}, pictures={5, 9}, in_use=[5, 6])
    assert voices.assign(5) == 1  # 5 is a 2005 player's name
    assert not voices.pictured(1)


def test_a_player_with_a_photo_takes_a_nameless_id_the_disc_has_a_picture_for():
    # A picture renamed to another id showed no portrait in PPSSPP.
    voices = Voices(named={5, 7}, pictures={5, 9}, in_use=[5, 6])
    assert voices.spare == 1
    assert voices.assign(5, photo=True) == 9
    assert voices.pictured(9)
    assert voices.assign(7, photo=True) == 1  # none left: one without


def test_an_id_no_name_answers_to_is_kept():
    voices = Voices(named={5, 7}, pictures={5, 9}, in_use=[5, 6])
    assert voices.assign(6, photo=True) == 6
    assert not voices.pictured(6)


def test_the_invented_players_id_is_replaced():
    voices = Voices(named={5}, pictures={5}, in_use=[0, 5])
    assert voices.assign(0) == 1


def test_no_two_players_take_one_id_and_none_takes_one_in_use():
    voices = Voices(named={1, 2, 3, 4, 9}, pictures=set(), in_use=[1, 2, 3, 4, 6])
    assert [voices.assign(n) for n in (1, 2, 3, 4)] == [5, 7, 8, None]


def test_without_a_speech_index_every_id_is_kept():
    assert Voices().assign(42) == 42
