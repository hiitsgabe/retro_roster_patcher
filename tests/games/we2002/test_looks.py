"""Per-player looks: skin and hair from the head shot, the rest stable picks
from the player, inside the values the retail disc uses."""

from collections import Counter

from retro_roster_patcher.core.headshots import Face
from retro_roster_patcher.games.we2002 import looks
from retro_roster_patcher.games.we2002.models import WEPlayerRecord


def _record(pid, **fields):
    return WEPlayerRecord(
        last_name=f"L{pid}",
        first_name=f"F{pid}",
        position=2,
        shirt_number=1,
        player_id=pid,
        **fields,
    )


def _dressed(pid, face=None, **fields):
    record = _record(pid, **fields)
    looks.dress([record], {pid: face} if face else None)
    return record


def test_a_look_is_the_same_every_time_and_stays_in_range():
    for pid in range(500):
        fields = dict(height_cm=160 + pid % 45, age=18 + pid % 22)
        a, b = _dressed(pid, **fields), _dressed(pid, **fields)
        assert a == b
        look = a.look
        assert 0 <= look.skin <= 3 and 0 <= look.hair_style <= 31, look
        assert look.hair_colour in (0, 1, 7) and 0 <= look.beard <= 6, look
        assert 0 <= look.beard_colour <= 7 and 3 <= look.build <= 6, look
        assert 155 <= a.height_cm <= 202 and 18 <= a.age <= 40


def test_two_unknown_players_do_not_share_a_look():
    records = [
        WEPlayerRecord(last_name=f"L{i}", first_name="", position=2, shirt_number=i + 1)
        for i in range(20)
    ]
    looks.dress(records)
    assert len({(r.look.hair_style, r.look.build, r.look.hair_colour) for r in records}) > 1


def test_east_asian_nationals_get_tone_one_whatever_the_photo():
    assert _dressed(7, Face(60.0, "black"), nationality="Japan").look.skin == 1
    assert _dressed(7, None, nationality="South Korea").look.skin == 1


def test_a_measured_tone_wins_over_the_pick_and_the_cuts_are_the_specs():
    assert [_dressed(7, Face(ita, None)).look.skin for ita in (40.0, 0.0, -30.0)] == [0, 2, 3]


def test_hair_colour_follows_the_photo_and_bald_takes_the_bald_style():
    assert _dressed(7, Face(40.0, "blond")).look.hair_colour == 7
    assert _dressed(7, Face(40.0, "black")).look.hair_colour == 0
    bald = _dressed(7, Face(40.0, "bald")).look
    assert (bald.hair_colour, bald.hair_style) == (0, looks.BALD_STYLE)


def test_without_a_photo_hair_is_weighted_like_the_disc():
    pale = Counter(_dressed(pid, Face(60.0, None)).look.hair_colour for pid in range(2000))
    assert 0.10 < pale[7] / 2000 < 0.22 and pale[1] > pale[0] > pale[7]
    dark = Counter(_dressed(pid, Face(-50.0, None)).look.hair_colour for pid in range(2000))
    assert dark[0] / 2000 > 0.6 and dark[7] < 20


def test_build_comes_from_height():
    assert [_dressed(7, height_cm=h).look.build for h in (190, 184, 178, 170)] == [3, 4, 5, 6]
    assert _dressed(7).look.build in (3, 4, 5, 6)


def test_beard_colour_is_the_hair_colour():
    for pid in range(200):
        look = _dressed(pid).look
        assert look.beard_colour == look.hair_colour


def test_missing_height_and_age_are_filled():
    assert 155 <= _dressed(7).height_cm <= 202
    assert _dressed(7).age == 25
    assert _dressed(7, age=33, height_cm=177).age == 33
