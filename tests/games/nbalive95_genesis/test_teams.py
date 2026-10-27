"""NBA Live 95 team blocks: names, ranks, the Slammers' new records, the unlocks."""

import struct

from retro_roster_patcher.games.nbalive95_genesis import teams
from retro_roster_patcher.games.nbalive95_genesis.models import (
    TEAM_ROSTER_ADDRESSES,
    NBALive95PlayerRecord,
)
from retro_roster_patcher.games.nbalive95_genesis.space import FREE, Space


def _image():
    image = bytearray(0x200000)
    for start, end in FREE:
        image[start:end] = b"\xff" * (end - start)
    return image


def _text(image, pointer):
    return bytes(image[pointer : image.index(0, pointer)]).decode()


def test_names_go_to_free_space_and_the_pointers_follow():
    image = _image()
    assert teams.write_names(image, 27, "TOR", "Raptors", Space())
    block = TEAM_ROSTER_ADDRESSES[27]
    nick, city, arena, live = struct.unpack_from(">4I", image, block + 0x30)
    assert (_text(image, nick), _text(image, city), arena) == ("Raptors", "Toronto", 0)
    assert _text(image, live) == "Toronto, ON" and image[block + 0x40 : block + 0x44] == b"tor "
    assert not teams.write_names(image, 27, "ZZZ", "Nobody", Space())


def test_a_nickname_survives_espns_shortened_one():
    assert teams.nickname("Portland Trail Blazers", "Trail Blazer") == "Trail Blazers"
    assert teams.nickname("Utah Jazz", "Jazz") == "Jazz"


def test_ranks_stay_within_what_team_select_shows():
    rosters = {i: [NBALive95PlayerRecord(ratings=[i] * 16)] for i in range(30)}
    ranks = teams.ranks(rosters)
    assert ranks[29] == [1] * 5 and max(r[0] for r in ranks.values()) == teams.MAX_RANK


def test_the_all_star_slots_are_unlocked_as_clubs():
    image = _image()
    for slot in (27, 28):
        for offset, retail, _ in teams.UNLOCK[slot]:
            image[offset : offset + 2] = bytes((0x70, retail))
        teams.unlock(image, slot)
        assert all(image[o + 1] == new for o, _, new in teams.UNLOCK[slot])
