"""Per-player looks: stable picks from the player id, inside the ranges the
cartridge draws cleanly, skin from the head shot, no two teammates alike."""

from collections import Counter

from retro_roster_patcher.games.kgj_mlb_snes import looks
from retro_roster_patcher.games.kgj_mlb_snes.models import KGJPlayerRecord

#: One past each of these glitches, resets or hangs the game.
PLATE = dict(
    skin=6,
    head=8,
    hair_color=5,
    body=4,
    legs_size=2,
    legs_stance=5,
    arms_stance=3,
    piece=2,
    piece_set=3,
    sequence=5,
    bat_color=3,
)
MOUND = dict(skin=6, head=5, hair_color=5, body=3, throwing_style=2)


def _record(pid, *, pitcher=False, height=0.0, weight=0.0):
    record = KGJPlayerRecord(player_id=pid, is_pitcher=pitcher)
    looks.fit(record, height, weight)
    return record


def test_a_look_is_the_same_every_time_and_stays_in_range():
    seen = set()
    for pid in range(2000):
        a = _record(pid, pitcher=pid % 3 == 0, height=66 + pid % 14, weight=170 + pid % 90)
        b = _record(pid, pitcher=pid % 3 == 0, height=66 + pid % 14, weight=170 + pid % 90)
        assert a == b
        for name, end in PLATE.items():
            assert 0 <= getattr(a.batter_appearance, name) < end, (name, a)
        assert 1 <= a.batter_appearance.waggle <= 7
        for name, end in MOUND.items():
            assert 0 <= getattr(a.pitcher_appearance, name) < end, (name, a)
        bat, mound = a.batter_appearance, a.pitcher_appearance
        assert bat.legs_size == (bat.body == 3)
        assert (mound.skin, mound.hair_color) == (bat.skin, bat.hair_color)
        seen.add((bat.skin, bat.head, bat.hair_color, bat.legs_stance, bat.arms_stance))
    assert len(seen) > 300  # not one look for everyone


def test_the_picks_are_the_ones_checked_in_mesen():
    # Shea Langeliers (ESPN 42598) on the proof cartridge: tone 0, 5'11", 205 lb.
    record = KGJPlayerRecord(player_id=42598)
    record.batter_appearance.body, record.pitcher_appearance.body = looks.build(42598, 71, 205)
    looks.dress([record], {42598: 0})
    bat, mound = record.batter_appearance, record.pitcher_appearance
    assert (bat.head, bat.hair_color, bat.body, bat.legs_stance, bat.arms_stance) == (4, 3, 0, 1, 2)
    assert (bat.bat_color, bat.waggle, bat.piece, bat.piece_set, bat.sequence) == (0, 7, 0, 2, 2)
    assert (mound.head, mound.body, mound.throwing_style) == (0, 0, 0)


def test_without_a_head_shot_the_skin_is_weighted_like_the_2026_faces():
    tones = Counter(_record(pid).batter_appearance.skin for pid in range(4000))
    assert set(tones) == set(range(6))
    assert tones[0] > 2400 and tones[4] + tones[5] < 400


def test_a_measured_tone_wins_and_dark_tones_have_black_hair():
    record = _record(7)
    looks.dress([record], {7: 5})
    assert (record.batter_appearance.skin, record.pitcher_appearance.skin) == (5, 5)
    assert record.batter_appearance.hair_color == 4


def _hair(pid):
    record = KGJPlayerRecord(player_id=pid)
    looks.dress([record], {pid: 0})
    return record.batter_appearance.hair_color


def test_hair_follows_the_cartridges_weights():
    hair = Counter(_hair(pid) for pid in range(20000))
    weights = (34, 12, 71, 66, 15)
    for value, weight in enumerate(weights):
        assert abs(hair[value] / 20000 - weight / sum(weights)) < 0.02, hair


def test_build_comes_from_height_and_weight():
    assert looks.build(1, 72, 180) == (3, 0)  # under 188 lb: small
    assert looks.build(1, 73, 245) == (2, 1)  # BMI 32: big, heavy on the mound
    assert looks.build(1, 75, 215) == (1, 0)  # 6'3": tall torso
    assert looks.build(1, 71, 200) == (0, 0)
    assert looks.build(1, 78, 225) == (1, 2)  # 6'6": tall on the mound


def test_without_height_and_weight_the_build_is_still_varied():
    builds = Counter(looks.build(pid, 0, 210) for pid in range(3000))
    assert {plate for plate, _ in builds} == {0, 1, 2, 3}
    assert {mound for _, mound in builds} == {0, 1, 2}


def test_dress_keeps_the_build():
    record = _record(3, height=78, weight=260)
    looks.dress([record], {3: 2})
    assert (record.batter_appearance.body, record.pitcher_appearance.body) == (2, 1)


def test_a_teammate_whose_card_would_match_is_picked_again():
    # Two ids whose first picks draw the same card.
    first = {}
    for pid in range(100000):
        card = looks._card(_record(pid, height=72, weight=200))
        if card in first:
            twin = pid
            break
        first[card] = pid
    a, b = _record(first[card], height=72, weight=200), _record(twin, height=72, weight=200)
    assert looks._card(a) == looks._card(b)
    looks.dress([a, b])
    assert looks._card(a) == card  # the first keeps his
    assert looks._card(b) != card
    assert (b.batter_appearance.skin, b.batter_appearance.body) == (
        a.batter_appearance.skin,
        a.batter_appearance.body,
    )


def test_a_team_of_light_batters_gets_fifteen_different_cards():
    team = [_record(1000 + i, height=72, weight=200) for i in range(15)]
    looks.dress(team, {r.player_id: 0 for r in team})
    assert len({looks._card(r) for r in team}) == 15


def test_a_head_that_draws_like_another_counts_as_that_one():
    a, b = KGJPlayerRecord(), KGJPlayerRecord()
    a.batter_appearance.head, b.batter_appearance.head = 2, 6
    assert looks._card(a) == looks._card(b)
    a.batter_appearance.body = b.batter_appearance.body = 3
    a.batter_appearance.head, b.batter_appearance.head = 7, 4
    assert looks._card(a) == looks._card(b)


def test_write_leaves_the_nibbles_the_game_never_reads():
    record = _record(11, pitcher=True)
    raw = bytearray(range(0x80, 0xA0))
    looks.write(raw, 0, record)
    assert raw[0x11] >> 4 == 0x91 >> 4 and raw[0x14] & 15 == 0x94 & 15
    assert raw[0x15] >> 4 == 0x95 >> 4  # the throwing hand
    assert raw[0x0E] == record.batter_appearance.skin << 4 | record.batter_appearance.head
    assert raw[:0x0E] == bytes(range(0x80, 0x8E)) and raw[0x18:] == bytes(range(0x98, 0xA0))


def test_write_zeroes_a_batters_mound_bytes():
    raw = bytearray(b"\xff" * 32)
    looks.write(raw, 0, _record(12))
    assert raw[0x15:0x18] == bytes(3)


#: $85:D1B4 as the retail cartridge has it, the jersey compared with 24 last.
GRIFFEY = bytes.fromhex("ae4618bdd012c93a00f005c93200d016adf512c91800d00e")


def test_number_24_is_freed_from_griffeys_head():
    image = bytearray(b"\0" * 64 + GRIFFEY + b"\0" * 64)
    assert looks.free_number_24(image) is True
    assert image[64 + GRIFFEY.index(b"\xc9\x18") + 1] == 0xFF
    once = bytes(image)
    assert looks.free_number_24(image) is True and image == once
    assert len(image) == 128 + len(GRIFFEY)


def test_an_image_without_the_rule_is_left_alone():
    image = bytearray(256)
    assert looks.free_number_24(image) is False
    assert image == bytearray(256)
