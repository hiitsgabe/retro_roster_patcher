"""`speech`: whose names the booth says, and the ids nobody's name answers to."""

from __future__ import annotations

import struct

from retro_roster_patcher.games.mvp_psp import portraits
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
    assert voices.moves == {1: 5}  # and its picture goes with him
    assert voices.pictured(1)


def test_an_id_no_name_answers_to_is_kept():
    voices = Voices(named={5, 7}, pictures={5, 9}, in_use=[5, 6])
    assert voices.assign(6) == 6
    assert voices.moves == {}
    assert not voices.pictured(6)


def test_the_invented_players_id_is_replaced_and_brings_no_picture():
    voices = Voices(named={5}, pictures={5}, in_use=[0, 5])
    assert voices.assign(0) == 1
    assert not voices.pictured(1)


def test_no_two_players_take_one_id_and_none_takes_one_in_use():
    voices = Voices(named={1, 2, 3, 4, 9}, pictures=set(), in_use=[1, 2, 3, 4, 6])
    assert [voices.assign(n) for n in (1, 2, 3, 4)] == [5, 7, 8, None]


def test_without_a_speech_index_every_id_is_kept():
    assert Voices().assign(42) == 42


def bigf(entries):
    """A BIGF archive of `{name: data}`."""
    at = 16 + sum(8 + len(name) + 1 for name in entries)
    head, body = b"", b""
    for name, data in entries.items():
        head += struct.pack(">II", at + len(body), len(data)) + name.encode() + b"\0"
        body += data
    sizes = struct.pack("<I", at + len(body)) + struct.pack(">II", len(entries), at)
    return b"BIGF" + sizes + head + body


def test_a_moved_picture_is_renamed_in_place_and_the_silhouette_left_alone(monkeypatch):
    archive = bigf({"0000.msh": b"S" * 8, "0005.msh": b"OLD-FACE", "0006.msh": b"KEPTFACE"})
    monkeypatch.setattr(portraits, "_archives", lambda f: [(1000, archive)])
    writes = portraits.edits(None, {"1": None, "0": None}, lambda _: None, {1: 5})
    patched = bytearray(archive)
    for at, data in writes:
        patched[at - 1000 : at - 1000 + len(data)] = data
    assert len(patched) == len(archive)
    after = {
        e.name: bytes(patched[e.offset : e.offset + e.size])
        for e in portraits.bigf_parse(bytes(patched))
    }
    # No head shot: his picture is the silhouette, not the old player's face.
    assert after == {"0000.msh": b"S" * 8, "0001.msh": b"S" * 8, "0006.msh": b"KEPTFACE"}


def test_a_renamed_picture_takes_its_place_in_the_directorys_name_order(monkeypatch):
    # The game finds a picture by that order: renamed in place, 0012 after
    # 0006 showed no portrait in PPSSPP.
    archive = bigf({"0000.msh": b"S" * 8, "0005.msh": b"OLD-FACE", "0006.msh": b"KEPTFACE"})
    monkeypatch.setattr(portraits, "_archives", lambda f: [(1000, archive)])
    writes = portraits.edits(None, {"12": None}, lambda _: None, {12: 5})
    patched = bytearray(archive)
    for at, data in writes:
        patched[at - 1000 : at - 1000 + len(data)] = data
    assert len(patched) == len(archive)
    entries = portraits.bigf_parse(bytes(patched))
    assert [e.name for e in entries] == ["0000.msh", "0006.msh", "0012.msh"]
    assert bytes(patched[entries[1].offset : entries[1].offset + 8]) == b"KEPTFACE"
