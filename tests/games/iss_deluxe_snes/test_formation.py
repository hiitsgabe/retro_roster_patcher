"""ISS Deluxe formations: the team's record, the places it gives, who sits where and the code each
player takes.

The synthetic cartridge holds only what `write_team` reads: the pointer table, the 16 default
formations and the retail records of Italy, Brazil and Argentina. The tests that need the real
cartridge are gated on RETRO_ROSTER_TEST_ROMS (a directory holding iss-deluxe.sfc).
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

from retro_roster_patcher.core.models import SlotMapping
from retro_roster_patcher.core.registry import get_patcher
from retro_roster_patcher.games.iss_deluxe_snes import patcher as patcher_module
from retro_roster_patcher.games.iss_deluxe_snes import rom as R
from retro_roster_patcher.games.iss_deluxe_snes.patcher import (
    ROLE_CODE,
    DeluxePlayer,
    DeluxeTeam,
    ISSDeluxePatcher,
    write_team,
)
from retro_roster_patcher.sports.lineup import ROLES
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster

ITALY, BRAZIL, ARGENTINA = 0, 30, 31

# The cartridge's 16 default formations, in id order: the ten slots' (depth, lateral) bytes
# ($82:8134) and their flags ($82:8074).
DEFAULT_POSITIONS = (
    "04e000f5000b0420f8f5f80b08e0080008200000",
    "04e000f5000b042004e000f5000b042000ec0014",
    "04e000f5000b042000e80000001800e800000018",
    "04e000f5000b042000f0001004e000f5000b0420",
    "04ec00000414f8e0f800f82008f0081000ec0014",
    "04ec0000041404e0fcf5fc0b042000e800000018",
    "04ec0000041404e00000042000e0fcf5fc0b0020",
    "04ec0000041400f5000bf0f5f00b00e40000001c",
    "00f5000bf8e0f800f82008f0081000ec00000014",
    "00f5000bfcf5fc0b00e00020fcf5fc0b00e00020",
    "00f5000b00e0fc000020f0f5f00b00e40000001c",
    "04e000f5f800000b0420fcf5fc0b00e000200000",
    "04e000f5f800000b042000ec0000001400ec0014",
    "04e000f5f800000b042000ec001400e40000001c",
    "0000f8e0f800f82008f0081000e000f5000b0020",
    "000000e0fcf5fc0b0020f0f5f00b00e40000001c",
)  # fmt: skip
DEFAULT_FLAGS = (
    "05010105020202020203", "05010105020202020303", "05010105020202030303",
    "01010101020203030303", "01010102020202020303", "01010102020202030303",
    "01010102020203030303", "01010102020303030303", "01010202020202030303",
    "01010202020203030303", "01010202020303030303", "01010101010202020203",
    "01010101010202020303", "01010101010202030303", "01020202020203030303",
    "01020202020303030303",
)  # fmt: skip
# Retail records (bank $8B): their ROM offset, then the id, 20 position bytes and 10 flag bytes.
# Argentina's flags 06 are midfield: bit 2 is the attack arrow, bits 0-1 the line.
RETAIL = {
    ITALY: (0x5EF9E, "0108e000f5000b0820f40000e800181400fcecfc1405010105020202060303"),
    BRAZIL: (0x5F3DB, "0108e000f5000b082000e0f0f5f00b002000f0001005010105060202060303"),
    ARGENTINA: (0x5F37E, "0104e000f5000b0420f40000e80018100000f0001001010101060606060303"),
}
# Brazil's record once the default 4-3-3 is written: the id, the positions, the flags.
BRAZIL_433 = "0204e000f5000b042000e80000001800e80000001805010105020202030303"
# Argentina's skill records in retail: byte 5 (the shirt number less one) and the position code.
ARGENTINA_BYTE_5 = (0, 1, 2, 3, 5, 4, 7, 10, 9, 6, 8)
ARGENTINA_CODES = (1, 3, 2, 2, 2, 3, 4, 4, 4, 6, 6)

#: The squad group each role is in, by default.
GROUP = {
    "GK": "GK", "CB": "DF", "RB": "DF", "LB": "DF", "RWB": "DF", "LWB": "DF",
    "CDM": "MF", "CM": "MF", "CAM": "MF", "RM": "MF", "LM": "MF",
    "RW": "FW", "LW": "FW", "ST": "FW",
}  # fmt: skip
LINE_NAMES = ("GK", "DF", "MF", "FW")
OLD_FOUR_FOUR_TWO = "GK LB CB CB RB LM CM CM RM ST ST"


def cartridge() -> bytearray:
    image = bytearray(R.ROM_SIZE)
    for fid, (positions, flags) in enumerate(zip(DEFAULT_POSITIONS, DEFAULT_FLAGS, strict=True)):
        at = R.DEFAULT_POSITIONS + 20 * fid
        image[at : at + 20] = bytes.fromhex(positions)
        at = R.DEFAULT_FLAGS + 10 * fid
        image[at : at + 10] = bytes.fromhex(flags)
    for team, (at, record) in RETAIL.items():
        struct.pack_into("<H", image, R.FORMATION_PTRS + 2 * team, 0x8000 | at & 0x7FFF)
        image[at : at + 31] = bytes.fromhex(record)
    for k in range(11):  # Argentina's shirt numbers are not in record order
        o = R.record_at(ARGENTINA, k)
        image[o + 4] = ARGENTINA_CODES[k] << 4
        image[o + 5] = ARGENTINA_BYTE_5[k]
    return image


def squad(xi: str, formation: str = "", *, roles: bool = True, groups: str = "") -> DeluxeTeam:
    """A club whose XI has the roles of `xi` (space separated, in that order), each named by his
    role and his place in the list (GK0, LB1, ...), then nine bench midfielders. `groups` gives
    the groups of the XI when they are not those of the roles; `roles=False` drops the roles."""
    given = xi.split()
    group = groups.split() or [GROUP[r] for r in given]
    skills = dict.fromkeys(R.SKILL_NAMES, 5)
    players = [
        DeluxePlayer(f"{r}{i}", g, dict(skills), role=r if roles else "")
        for i, (r, g) in enumerate(zip(given, group, strict=True))
    ]
    players += [DeluxePlayer(f"B{i}", "MF", dict(skills)) for i in range(R.PLAYERS - 11)]
    return DeluxeTeam("Club", players, formation=formation)


def odd_one(role: str, line: int) -> DeluxeTeam:
    """A 4-4-2 XI of centre backs, central midfielders and strikers (a keeper in goal) whose last
    player of `line` has `role` instead, and is named ODD. Everyone is in the group of his line."""
    lines = [["GK"], ["CB"] * 4, ["CM"] * 4, ["ST"] * 2]
    lines[line][-1] = role
    groups = [name for name, n in zip(LINE_NAMES, (1, 4, 4, 2), strict=True) for _ in range(n)]
    team = squad(" ".join(sum(lines, [])), groups=" ".join(groups))
    team.players[sum(len(x) for x in lines[: line + 1]) - 1].name = "ODD"
    return team


def written(image: bytes | bytearray, slot: int) -> tuple[list[str], list[int]]:
    """The 20 names and position codes in record order."""
    names = [R.decode_name(image[R.name_at(slot, k) :][: R.NAME_LEN]) for k in range(R.PLAYERS)]
    codes = [R.nibbles(image[R.record_at(slot, k) :][: R.RECORD])[9] for k in range(R.PLAYERS)]
    return names, codes


def roles_of(names: list[str]) -> list[str]:
    return [n.rstrip("0123456789") for n in names]


def real_rom(test):
    gated = pytest.mark.skipif(
        not os.environ.get("RETRO_ROSTER_TEST_ROMS"),
        reason="set RETRO_ROSTER_TEST_ROMS to a directory holding iss-deluxe.sfc",
    )
    return pytest.mark.real_rom(gated(test))


@pytest.fixture
def retail() -> bytes:
    rom = Path(os.environ["RETRO_ROSTER_TEST_ROMS"]) / "iss-deluxe.sfc"
    if not rom.exists():
        pytest.fail(f"RETRO_ROSTER_TEST_ROMS is set but {rom} is missing")
    data = rom.read_bytes()
    assert R.is_deluxe(data)
    return data


# --- the cartridge's tables ---------------------------------------------------------------


def test_the_sixteen_formations_are_named_in_the_cartridges_id_order():
    assert R.FORMATIONS == tuple(
        "4-5-1 4-4-2 4-3-3 4-2-4 3-5-2 3-4-3 3-3-4 3-2-5 2-5-3 2-4-4 2-3-5 5-4-1 5-3-2 5-2-3 "
        "1-5-4 1-4-5".split()
    )
    assert all(sum(map(int, name.split("-"))) == 10 for name in R.FORMATIONS)


def test_every_formation_the_patcher_offers_is_one_the_cartridge_has():
    assert set(ISSDeluxePatcher.formations) <= set(R.FORMATIONS)


def test_a_position_code_allows_the_lines_the_games_table_gives_it():
    # DATA_81AE81, by line: keeper 0, defence 1, midfield 2, attack 3.
    assert R.ALLOWED_LINES == {
        R.GK: {0}, R.CB: {1}, R.FB: {1, 2}, R.MF: {2}, R.AM: {2, 3}, R.FW: {3},
    }  # fmt: skip


def test_a_teams_record_is_found_through_the_pointer_table():
    image = cartridge()
    assert R.formation_at(image, BRAZIL) == 0x5F3DB
    assert R.formation_at(image, ARGENTINA) == 0x5F37E
    assert R.formation_at(image, ITALY) == 0x5EF9E


def test_the_places_of_retail_brazil_and_argentina_are_a_keeper_four_four_and_two():
    image = cartridge()
    assert R.slot_lines(image, BRAZIL) == [0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3]
    assert R.slot_lines(image, ARGENTINA) == [0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3]


def test_a_places_offsets_are_signed_depth_then_lateral_the_keepers_none():
    offsets = R.slot_offsets(cartridge(), BRAZIL)
    assert len(offsets) == 11
    assert offsets[0] == (0, 0)
    assert offsets[1:5] == [(8, -32), (0, -11), (0, 11), (8, 32)]
    assert offsets[5:7] == [(0, -32), (-16, -11)]


def test_a_default_record_is_the_id_then_its_positions_then_its_flags():
    image = cartridge()
    assert R.default_record(image, 2) == bytes.fromhex(BRAZIL_433)
    assert len(R.default_record(image, 15)) == R.FORMATION_RECORD == 31
    assert R.default_record(image, 15)[0] == 15


# --- the record written -------------------------------------------------------------------


def test_a_433_club_in_brazils_slot_gives_the_cartridges_433_record():
    image = cartridge()
    before = bytes(image)
    write_team(image, BRAZIL, squad("GK LB CB CB RB CM CDM CAM LW ST RW", "4-3-3"))
    assert image[0x5F3DB : 0x5F3DB + 31] == bytes.fromhex(BRAZIL_433)
    # The rest of the pointer table, the records and the default tables stay.
    for lo, hi in (
        (R.DEFAULT_FLAGS, R.DEFAULT_POSITIONS + 320),
        (R.FORMATION_PTRS, 0x5F3DB),
        (0x5F3DB + 31, 0x5F4D3),
    ):
        assert image[lo:hi] == before[lo:hi]


def test_a_shape_the_cartridge_does_not_offer_is_written_as_the_nearest_it_does():
    image = cartridge()
    write_team(image, BRAZIL, squad("GK CB CB CB CM CM CM CM CM CM ST", "3-6-1"))
    assert R.FORMATIONS[image[R.formation_at(image, BRAZIL)]] == "4-5-1"


@pytest.mark.parametrize("formation", ["", "4-4-x", "442", "4-4-3"])
def test_without_a_readable_formation_the_slots_own_record_stays(formation):
    image = cartridge()
    write_team(image, ARGENTINA, squad(OLD_FOUR_FOUR_TWO, formation))
    at = R.formation_at(image, ARGENTINA)
    assert image[at : at + 31] == bytes.fromhex(RETAIL[ARGENTINA][1])


def test_the_formation_is_written_whatever_the_players_groups_say():
    image = cartridge()
    # Four defenders, four midfielders and two forwards by group; the club plays a back three.
    write_team(image, BRAZIL, squad("GK CB CB RB LB CM CM CM RM ST ST", "3-5-2"))
    assert R.FORMATIONS[image[R.formation_at(image, BRAZIL)]] == "3-5-2"
    assert R.slot_lines(image, BRAZIL) == [0, 1, 1, 1, 2, 2, 2, 2, 2, 3, 3]


# --- who sits where -----------------------------------------------------------------------


def test_without_a_formation_each_line_takes_the_slots_places_by_side_and_depth():
    image = cartridge()
    write_team(image, BRAZIL, squad("GK CB RB LB CB RM CM CDM LM ST ST"))
    names, _ = written(image, BRAZIL)
    # Brazil's back four: places 1 and 4 are 32 to the left and right, 2 and 3 the centre.
    assert names[:5] == ["GK0", "LB3", "CB1", "CB4", "RB2"]
    # Its midfield: 5 and 8 are the flanks, 6 and 7 the pair 16 deeper.
    assert names[5:9] == ["LM8", "CM6", "CDM7", "RM5"]
    assert names[9:11] == ["ST9", "ST10"]


def test_in_a_433_the_forwards_take_the_left_the_centre_and_the_right():
    image = cartridge()
    write_team(image, BRAZIL, squad("GK LB CB CB RB CM CDM CAM ST RW LW", "4-3-3"))
    names, _ = written(image, BRAZIL)
    assert names[1:5] == ["LB1", "CB2", "CB3", "RB4"]
    assert names[8:11] == ["LW10", "ST8", "RW9"]


def test_the_deeper_midfielder_takes_the_deeper_place():
    image = cartridge()
    # The 4-5-1's deep midfielders are places 5 and 6 (8 back), its advanced centre place 8.
    write_team(image, BRAZIL, squad("GK LB CB CB RB CAM CDM CM LM RM ST", "4-5-1"))
    names, _ = written(image, BRAZIL)
    assert names[5:10] == ["CDM6", "CM7", "LM8", "CAM5", "RM9"]


def test_a_back_five_keeps_its_full_backs_on_the_flank_places():
    image = cartridge()
    # The 5-3-2's back five stands at laterals -32, -11, 0, 11 and 32.
    write_team(image, BRAZIL, squad("GK CB LB CB RB CB CM CM CM ST ST", "5-3-2"))
    names, _ = written(image, BRAZIL)
    assert names[1:6] == ["LB2", "CB1", "CB3", "CB5", "RB4"]


def test_a_wide_midfielder_takes_the_flank_place_and_not_the_inner_one_on_his_side():
    image = cartridge()
    # The 3-5-2's midfield is at laterals -32, 0 and 32 (places 4-6) and -16 and 16 (places 7-8):
    # -32 and -16 are both on the left, and a centre midfielder listed first took the flank.
    write_team(image, BRAZIL, squad("GK CB CB CB CM LM CM RM CM ST ST", "3-5-2"))
    names, _ = written(image, BRAZIL)
    assert names[4:9] == ["LM5", "CM4", "RM7", "CM6", "CM8"]


def test_the_ten_are_cut_by_index_over_the_formation_not_by_group():
    image = cartridge()
    write_team(image, BRAZIL, squad("GK CB CB RB LB CM CM CM RM ST ST", "3-5-2"))
    names, codes = written(image, BRAZIL)
    assert names[1:4] == ["CB1", "CB2", "RB3"]
    # The defender who plays midfield is on the left flank (-32) and the right midfielder on the
    # right flank (32), the centre midfielders on the middle place and the inner pair.
    assert names[4:9] == ["LB4", "CM5", "RM8", "CM6", "CM7"]
    assert names[9:11] == ["ST9", "ST10"]
    # The defender who plays midfield takes the code that plays both.
    assert codes[4] == R.FB


def test_the_formation_and_not_the_groups_decides_who_plays_up_front():
    image = cartridge()
    # By group this is a 4-4-2 and the right back is the fourth defender; the club plays a 3-4-3,
    # so by index he is the fourth midfielder and the right midfielder a forward. The groups would
    # leave a place up front for him instead.
    write_team(image, BRAZIL, squad(OLD_FOUR_FOUR_TWO, "3-4-3"))
    names, codes = written(image, BRAZIL)
    assert names[1:4] == ["LB1", "CB2", "CB3"]
    assert names[4:8] == ["LM5", "CM6", "CM7", "RB4"]
    assert names[8:11] == ["ST9", "ST10", "RM8"]
    assert (codes[7], codes[10]) == (R.FB, R.AM)  # a defender in midfield, a midfielder in attack


def test_an_edited_order_is_cut_by_index_and_each_line_seated_by_role():
    image = cartridge()
    # The left back is listed last of the defenders and the winger last of the forwards.
    write_team(image, BRAZIL, squad("GK CB CB RB LB CM CM CAM ST ST LW", "4-3-3"))
    names, _ = written(image, BRAZIL)
    assert names[1:5] == ["LB4", "CB1", "CB2", "RB3"]
    assert names[5:8] == ["CM5", "CM6", "CAM7"]
    assert names[8:11] == ["LW10", "ST8", "ST9"]


@pytest.mark.parametrize("formation", ["", "4-4-2"])
def test_the_keeper_takes_place_0_wherever_the_squad_lists_him(formation):
    image = cartridge()
    write_team(image, BRAZIL, squad("CB RB LB CB RM CM CDM LM ST ST GK", formation))
    names, codes = written(image, BRAZIL)
    assert names[0] == "GK10" and codes[0] == R.GK
    assert sorted(names[:11]) == sorted(f"{r}{i}" for i, r in enumerate(
        "CB RB LB CB RM CM CDM LM ST ST GK".split()))  # fmt: skip


def test_a_line_with_too_many_players_hands_the_extra_to_a_line_short_of_them():
    image = cartridge()
    # Three forwards for two places and three midfielders for four: the third forward plays
    # midfield, on the left flank, where the winger's side and depth put him.
    write_team(image, BRAZIL, squad("GK LB CB CB RB LM CM RM ST ST LW"))
    names, _ = written(image, BRAZIL)
    assert names[5:9] == ["LW10", "LM5", "CM6", "RM7"]
    assert names[9:11] == ["ST8", "ST9"]


def test_a_short_squad_writes_the_players_it_has_and_no_more():
    image = cartridge()
    team = squad(OLD_FOUR_FOUR_TWO)
    team.players = team.players[:8]
    assert write_team(image, BRAZIL, team) == 8
    assert write_team(image, BRAZIL, DeluxeTeam("Nobody", [])) == 0


# --- the byte 5 bug -----------------------------------------------------------------------


def test_the_places_follow_the_formation_not_the_shirt_number():
    image = cartridge()
    # Argentina's records number places 4 and 5 as shirts 6 and 5, 8 and 9 as 11 and 10: a 4-4-2
    # by position must still have a defender on place 4, a midfielder on 5 and none up front.
    write_team(image, ARGENTINA, squad(OLD_FOUR_FOUR_TWO))
    names, codes = written(image, ARGENTINA)
    roles = roles_of(names)
    assert roles[0] == "GK" and sorted(roles[1:5]) == ["CB", "CB", "LB", "RB"]
    assert sorted(roles[5:9]) == ["CM", "CM", "LM", "RM"] and roles[9:11] == ["ST", "ST"]
    # Place 5 is 12 deep in the middle, 6 and 7 the flanks, 8 16 forward in the middle.
    assert names[5:9] == ["CM6", "LM5", "RM8", "CM7"]
    assert codes[:11] == [1, 2, 2, 2, 2, 4, 4, 4, 4, 6, 6]


def test_the_shirt_number_is_the_place_counted_from_one():
    image = cartridge()
    write_team(image, ARGENTINA, squad(OLD_FOUR_FOUR_TWO))
    assert [image[R.record_at(ARGENTINA, k) + 5] for k in range(R.PLAYERS)] == list(range(20))


# --- the code each player takes -----------------------------------------------------------


def test_a_role_has_the_code_of_the_lines_it_can_play():
    assert ROLE_CODE == {
        "GK": 1, "CB": 2, "RB": 2, "LB": 2, "RWB": 3, "LWB": 3, "CDM": 3,
        "CM": 4, "RM": 4, "LM": 4, "CAM": 5, "RW": 5, "LW": 5, "ST": 6,
    }  # fmt: skip
    assert set(ROLE_CODE) == set(ROLES)


@pytest.mark.parametrize("role", ROLES)
def test_a_player_on_a_line_his_role_allows_gets_his_roles_code(role):
    for line in R.ALLOWED_LINES[ROLE_CODE[role]]:
        image = cartridge()
        write_team(image, BRAZIL, odd_one(role, line))
        names, codes = written(image, BRAZIL)
        at = names.index("ODD")
        assert R.slot_lines(image, BRAZIL)[at] == line
        assert codes[at] == ROLE_CODE[role], (role, line)


@pytest.mark.parametrize(
    ("role", "line", "code"),
    [
        ("CB", 2, R.FB),  # defence in midfield: the code that plays both
        ("CM", 1, R.FB),
        ("RM", 1, R.FB),
        ("CM", 3, R.AM),  # midfield up front, or the front man in midfield
        ("ST", 2, R.AM),
        ("CB", 3, R.FW),  # no code plays both defence and attack: the line's own
        ("LWB", 3, R.FW),
        ("CDM", 3, R.FW),
        ("ST", 1, R.CB),
        ("CAM", 1, R.CB),
        ("RW", 1, R.CB),
        ("GK", 1, R.CB),  # a keeper outfield
        ("GK", 2, R.MF),
        ("GK", 3, R.FW),
        ("ST", 0, R.GK),  # an outfielder in goal
        ("CB", 0, R.GK),
    ],
)
def test_a_role_on_a_line_it_cannot_play_gets_the_code_that_can(role, line, code):
    image = cartridge()
    write_team(image, BRAZIL, odd_one(role, line))
    names, codes = written(image, BRAZIL)
    at = names.index("ODD")
    assert R.slot_lines(image, BRAZIL)[at] == line
    assert codes[at] == code


def test_a_player_without_a_role_keeps_the_code_of_his_group():
    image = cartridge()
    write_team(image, BRAZIL, squad(OLD_FOUR_FOUR_TWO, roles=False))
    _, codes = written(image, BRAZIL)
    assert codes[:11] == [R.GK, R.CB, R.CB, R.CB, R.CB, R.MF, R.MF, R.MF, R.MF, R.FW, R.FW]
    assert codes[11:] == [R.MF] * 9  # the bench is midfielders


def test_a_roleless_player_out_of_his_line_gets_a_code_that_allows_the_place():
    image = cartridge()
    # Three forwards by group for two places: the third plays midfield, last of the four there.
    write_team(image, BRAZIL, squad("GK LB CB CB RB LM CM RM ST ST LW", roles=False))
    names, codes = written(image, BRAZIL)
    assert names[8] == "LW10"
    assert codes[5:9] == [R.MF, R.MF, R.MF, R.AM]


def test_the_bench_gets_the_code_of_its_role_or_its_group_unguarded():
    image = cartridge()
    team = squad(OLD_FOUR_FOUR_TWO)
    skills = dict(team.players[0].skills)
    team.players[11:14] = [
        DeluxePlayer("Keeper", "GK", dict(skills), role="GK"),
        DeluxePlayer("Winger", "FW", dict(skills), role="LW"),
        DeluxePlayer("Striker", "FW", dict(skills)),
    ]
    write_team(image, BRAZIL, team)
    names, codes = written(image, BRAZIL)
    assert [codes[names.index(n)] for n in ("Keeper", "Winger", "Striker")] == [R.GK, R.AM, R.FW]


@pytest.mark.parametrize("slot", [ITALY, BRAZIL, ARGENTINA])
@pytest.mark.parametrize(
    "xi",
    [
        "GK ST ST ST ST ST ST ST ST ST ST",
        "GK CB CB CB CB CB CB CB CB CB CB",
        "GK LW RW CAM LWB RWB CDM CM LM RM ST",
        "ST GK LB CB CB RB CM CM CAM LW RW",
    ],
)
@pytest.mark.parametrize("formation", ["", "4-4-2", "3-5-2", "5-4-1", "4-2-4"])
def test_every_one_of_the_eleven_has_a_code_that_allows_his_place(slot, xi, formation):
    image = cartridge()
    write_team(image, slot, squad(xi, formation))
    _, codes = written(image, slot)
    lines = R.slot_lines(image, slot)
    assert all(line in R.ALLOWED_LINES[code] for line, code in zip(lines, codes[:11], strict=True))


def test_patching_twice_gives_identical_bytes():
    for slot, team in (
        (BRAZIL, squad("GK RB CB CB LB CAM CDM CM RW ST LW", "4-3-3")),
        (ARGENTINA, squad("GK RB CB CB LB CM CM LM RM ST ST")),
        (ITALY, squad("GK RB CB CB LB CM CM LM RM ST ST", "4-4-2", roles=False)),
    ):
        once = cartridge()
        write_team(once, slot, team)
        twice = bytearray(once)
        write_team(twice, slot, team)
        assert once == twice


# --- from the roster ----------------------------------------------------------------------


def _roster(
    formation: str | None, *, roles: bool = True, xi: str = "GK LB CB CB RB CM CDM CAM LW ST RW"
) -> TeamRoster:
    """A roster listed as `xi` and then nine on the bench, player i named X<i> and in the squad
    group of his role."""
    sheet = f"{xi} GK RB CB CM CAM LM RM ST CB".split()
    position = {"GK": "Goalkeeper", "CB": "Defender", "RB": "Defender", "LB": "Defender"}
    position |= dict.fromkeys(("CM", "CDM", "CAM", "LM", "RM"), "Midfielder")
    players = [
        Player(
            id=i, name=f"X{i}", position=position.get(r, "Attacker"), role=r if roles else "",
        )
        for i, r in enumerate(sheet, start=1)
    ]  # fmt: skip
    extra = {} if formation is None else {"formation": formation}
    return TeamRoster(team=Team(id=1, name="Club"), players=players, extra=extra)


@pytest.mark.parametrize("order_as_given", [False, True])
def test_the_mapped_team_carries_its_formation_and_each_players_role(tmp_path, order_as_given):
    data = LeagueData(league=League(id=1, name="League"), teams=[_roster("4-3-3")])
    patcher = get_patcher("iss-deluxe-snes")(tmp_path)
    mapped = patcher.map_rosters(
        data, [SlotMapping(slot_index=BRAZIL, team_id=1)], order_as_given=order_as_given
    )
    team = mapped.teams[BRAZIL]
    assert team.formation == "4-3-3"
    roles = {p.player_id: p.role for p in team.players}
    assert (roles[2], roles[9], roles[11], roles[1]) == ("LB", "LW", "RW", "GK")
    image = cartridge()
    write_team(image, BRAZIL, team)
    assert R.FORMATIONS[image[R.formation_at(image, BRAZIL)]] == "4-3-3"
    _, codes = written(image, BRAZIL)
    lines = R.slot_lines(image, BRAZIL)
    assert codes[0] == R.GK
    assert all(line in R.ALLOWED_LINES[code] for line, code in zip(lines, codes[:11], strict=True))


def test_a_given_433_sheet_is_written_in_the_433(tmp_path):
    data = LeagueData(league=League(id=1, name="League"), teams=[_roster("4-3-3")])
    patcher = get_patcher("iss-deluxe-snes")(tmp_path)
    team = patcher.map_rosters(
        data, [SlotMapping(slot_index=BRAZIL, team_id=1)], order_as_given=True
    ).teams[BRAZIL]
    image = cartridge()
    write_team(image, BRAZIL, team)
    # Player 1 is the keeper, 2-5 the back four, 6-8 midfield, 9-11 the front three (LW, ST, RW).
    ids = {p.name: p.player_id for p in team.players}
    placed = [ids[R.decode_name(image[R.name_at(BRAZIL, k) :][:8])] for k in range(11)]
    assert placed == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]


def test_a_roster_without_a_formation_or_roles_keeps_its_slots_record(tmp_path):
    data = LeagueData(league=League(id=1, name="League"), teams=[_roster(None, roles=False)])
    patcher = get_patcher("iss-deluxe-snes")(tmp_path)
    team = patcher.map_rosters(
        data, [SlotMapping(slot_index=ARGENTINA, team_id=1)], order_as_given=True
    ).teams[ARGENTINA]
    assert team.formation == "" and {p.role for p in team.players} == {""}
    image = cartridge()
    write_team(image, ARGENTINA, team)
    at = R.formation_at(image, ARGENTINA)
    assert image[at : at + 31] == bytes.fromhex(RETAIL[ARGENTINA][1])
    _, codes = written(image, ARGENTINA)
    lines = R.slot_lines(image, ARGENTINA)
    assert all(line in R.ALLOWED_LINES[code] for line, code in zip(lines, codes[:11], strict=True))


def _mapped(tmp_path, roster: TeamRoster, slot: int = BRAZIL, *, given: bool = True) -> DeluxeTeam:
    data = LeagueData(league=League(id=1, name="League"), teams=[roster])
    patcher = get_patcher("iss-deluxe-snes")(tmp_path)
    mapped = patcher.map_rosters(
        data, [SlotMapping(slot_index=slot, team_id=1)], order_as_given=given
    )
    return mapped.teams[slot]


def test_an_edited_order_keeps_its_places_under_a_formation(tmp_path):
    # The sheet's right back and right midfielder trade places: the right back is the ninth player.
    team = _mapped(tmp_path, _roster("4-4-2", xi="GK LB CB CB RM LM CM CM RB ST ST"))
    assert [p.player_id for p in team.players[:11]] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    # By index over the 4-4-2 he is the fourth midfielder, and the right midfielder a defender.
    assert [p.group for p in team.players[:11]] == ["GK"] + ["DF"] * 4 + ["MF"] * 4 + ["FW"] * 2
    image = cartridge()
    write_team(image, BRAZIL, team)
    names, codes = written(image, BRAZIL)
    assert names[1:9] == ["X2", "X3", "X4", "X5", "X6", "X7", "X8", "X9"]
    assert names.index("X9") in range(5, 9) and names.index("X5") in range(1, 5)
    # Each plays a line his code does not: the one that plays both.
    assert (codes[4], codes[8]) == (R.FB, R.FB)


@pytest.mark.parametrize("order_as_given", [False, True])
@pytest.mark.parametrize(
    ("formation", "xi"),
    # Each XI is listed by position as the shape ISS 2000 reads the written one as (4-3-3, 4-5-1).
    [
        ("4-2-4", "GK LB CB CB RB CM CM CAM LW ST RW"),
        ("5-4-1", "GK LB CB CB RB LM CM CM CAM RM ST"),
    ],
)
def test_the_eleven_are_in_the_lines_of_the_shape_written_not_iss_2000s(
    tmp_path, formation, xi, order_as_given
):
    # ISS 2000 has neither shape: it reads a 4-2-4 as its 4-3-3 and a 5-4-1 as its 4-5-1.
    team = _mapped(tmp_path, _roster(formation, xi=xi), given=order_as_given)
    d, m, f = map(int, formation.split("-"))
    assert [p.group for p in team.players[:11]] == ["GK"] + ["DF"] * d + ["MF"] * m + ["FW"] * f
    # The group is the cartridge's baseline for his skills (no statistics: the median of it), so
    # the left midfielder listed sixth, the fifth defender of a 5-4-1, rates as a defender.
    if formation == "5-4-1":
        spread = patcher_module.RETAIL
        assert team.players[5].skills == {s: spread["DF"][s][2] for s in R.SKILL_NAMES}
        assert team.players[6].skills == {s: spread["MF"][s][2] for s in R.SKILL_NAMES}


def _older_roster(
    formation: str = "4-4-2", codes: str = "G CD-L CD-R RB LB DM CM-R LM RM CF-L CF-R"
) -> TeamRoster:
    """A fetch from before roles and formations: a lineup in ESPN's codes (by default a 4-4-2 with
    the full backs listed after the centre backs) and no role anywhere. Player i is X<i>, and
    nine more are on the bench."""
    given = codes.split()
    group = {"G": "Goalkeeper"}
    group |= dict.fromkeys(("CD-L", "CD-R", "RB", "LB"), "Defender")
    group |= dict.fromkeys(("F", "CF-L", "CF-R"), "Attacker")
    players = [
        Player(id=i, name=f"X{i}", position=group.get(c, "Midfielder"))
        for i, c in enumerate(given, start=1)
    ]
    players += [Player(id=i, name=f"X{i}", position="Midfielder") for i in range(12, 21)]
    starters = [{"id": i, "position": c} for i, c in enumerate(given, start=1)]
    lineup = {"formation": formation, "shape": "4-4-2", "starters": starters}
    return TeamRoster(team=Team(id=1, name="Club"), players=players, extra={"lineup": lineup})


@pytest.mark.parametrize("given", [False, True])
def test_an_older_roster_takes_its_roles_from_its_lineup_codes(tmp_path, given):
    team = _mapped(tmp_path, _older_roster(), given=given)
    roles = {p.player_id: p.role for p in team.players}
    assert [roles[i] for i in range(1, 12)] == [
        "GK", "CB", "CB", "RB", "LB", "CDM", "CM", "LM", "RM", "ST", "ST",
    ]  # fmt: skip
    assert {roles[i] for i in range(12, 21)} == {""}  # the bench has no code to read


def test_an_older_rosters_full_backs_take_the_flanks_and_its_holding_midfielder_his_code(tmp_path):
    team = _mapped(tmp_path, _older_roster())
    image = cartridge()
    write_team(image, BRAZIL, team)  # no formation: Brazil keeps its own 4-4-2 record
    names, codes = written(image, BRAZIL)
    # The left back (X5) takes the left flank place 1 and the right back (X4) the right flank 4.
    assert names[1:5] == ["X5", "X2", "X3", "X4"]
    # The left (X8) and right (X9) midfielders take the flanks, the centre pair the deep places.
    assert names[5:9] == ["X8", "X6", "X7", "X9"]
    # The holding midfielder plays defence or midfield, the bench has its group's code.
    assert codes[6] == R.FB and codes[11:] == [R.MF] * 9


def test_a_players_own_role_beats_the_one_his_lineup_code_reads_as(tmp_path):
    roster = _older_roster()
    roster.players[6].role = "CAM"  # tagged CM-R in the lineup
    team = _mapped(tmp_path, roster)
    assert {p.player_id: p.role for p in team.players}[7] == "CAM"


def test_a_lineup_code_reads_inside_the_lineups_own_formation(tmp_path):
    # ESPN labels the two holding midfielders of a 4-2-3-1 LM and RM, and the wide attackers AM-R
    # and AM-L: in that formation they are CDM, CDM, RW and LW; in a 4-4-2 they would be wide.
    codes = "G RB LB LM CD-R CD-L AM-R RM F AM AM-L"
    team = _mapped(tmp_path, _older_roster("4-2-3-1", codes))
    roles = {p.player_id: p.role for p in team.players}
    by_code = dict(zip(codes.split(), (roles[i] for i in range(1, 12)), strict=True))
    assert by_code == {
        "G": "GK", "RB": "RB", "LB": "LB", "LM": "CDM", "CD-R": "CB", "CD-L": "CB",
        "AM-R": "RW", "RM": "CDM", "F": "ST", "AM": "CAM", "AM-L": "LW",
    }  # fmt: skip


def test_a_lineup_code_that_reads_as_no_known_role_leaves_the_player_without_one(monkeypatch):
    monkeypatch.setattr(patcher_module, "slot_role", lambda formation, code: "SWEEPER")
    assert patcher_module._lineup_role("4-4-2", "LB") == ""
    assert patcher_module._lineup_role("4-4-2", "") == ""  # a player the lineup does not name


# --- the real cartridge -------------------------------------------------------------------


@real_rom
def test_the_literal_tables_above_are_the_cartridges(retail):
    for fid in range(16):
        positions = retail[R.DEFAULT_POSITIONS + 20 * fid :][:20]
        assert positions == bytes.fromhex(DEFAULT_POSITIONS[fid])
        assert retail[R.DEFAULT_FLAGS + 10 * fid :][:10] == bytes.fromhex(DEFAULT_FLAGS[fid])
    for team, (at, record) in RETAIL.items():
        assert R.formation_at(retail, team) == at
        assert retail[at : at + 31] == bytes.fromhex(record)


@real_rom
def test_the_formation_names_are_the_ones_the_team_select_screen_draws(retail):
    # $87:C493: 16 names of 5 tiles, a digit d drawn as tile d + 1 and the dash as 0x26.
    for fid, name in enumerate(R.FORMATIONS):
        tiles = bytes(0x26 if c == "-" else int(c) + 1 for c in name)
        assert retail[0x3C493 + 5 * fid :][:5] == tiles, name


@real_rom
def test_the_allowed_lines_are_the_games_own_table(retail):
    # DATA_81AE81: for each code 0-6, the first and the last line it may play (code 0 none).
    table = {
        code: set(range(retail[0xAE81 + 2 * code], retail[0xAE82 + 2 * code] + 1))
        for code in range(1, 7)
    }
    assert table == R.ALLOWED_LINES


@real_rom
def test_every_record_and_default_has_the_lines_of_its_shape(retail):
    for fid, name in enumerate(R.FORMATIONS):
        d, m, f = map(int, name.split("-"))
        flags = [b & 3 for b in retail[R.DEFAULT_FLAGS + 10 * fid :][:10]]
        assert flags == [1] * d + [2] * m + [3] * f, name
    for team in range(43):
        name = R.FORMATIONS[retail[R.formation_at(retail, team)]]
        d, m, f = map(int, name.split("-"))
        assert R.slot_lines(retail, team) == [0] + [1] * d + [2] * m + [3] * f, (team, name)


@real_rom
def test_the_widest_lateral_of_the_cartridge_is_40_which_is_what_a_side_divides_by(retail):
    # The default formations' flank places are at 32; a few retail records go out to 40.
    laterals = {lat for team in range(43) for _, lat in R.slot_offsets(retail, team)[1:]}
    assert max(abs(lat) for lat in laterals) == patcher_module._TOUCHLINE == 40


@real_rom
def test_retail_brazil_and_argentina_have_the_places_of_a_442(retail):
    assert R.slot_lines(retail, BRAZIL) == [0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3]
    assert R.slot_lines(retail, ARGENTINA) == [0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3]


@real_rom
def test_a_433_club_in_brazils_slot_gives_the_test_vector_on_the_cartridge(retail):
    image = bytearray(retail)
    write_team(image, BRAZIL, squad("GK LB CB CB RB CM CDM CAM LW ST RW", "4-3-3"))
    assert image[0x5F3DB : 0x5F3DB + 31] == bytes.fromhex(BRAZIL_433)
    assert R.slot_lines(image, BRAZIL) == [0, 1, 1, 1, 1, 2, 2, 2, 3, 3, 3]


@real_rom
@pytest.mark.parametrize("slot", range(len(R.TEAMS)))
def test_on_the_cartridge_every_nation_gets_its_players_on_the_lines_of_their_groups(retail, slot):
    # Nine nations number their players differently from their places (byte 5), and the patch
    # used to follow the numbers.
    image = bytearray(retail)
    lines = R.slot_lines(retail, slot)
    xi = " ".join(("GK", "CB", "CM", "ST")[line] for line in lines)
    write_team(image, slot, squad(xi))
    names, codes = written(image, slot)
    assert roles_of(names[:11]) == xi.split()
    assert all(line in R.ALLOWED_LINES[code] for line, code in zip(lines, codes[:11], strict=True))
    again = bytearray(image)
    write_team(again, slot, squad(xi))
    assert again == image


@real_rom
@pytest.mark.parametrize("slot", [BRAZIL, ARGENTINA, 1, 8, 14, ITALY])
@pytest.mark.parametrize("formation", ISSDeluxePatcher.formations)
def test_on_the_cartridge_every_offered_formation_fits_a_team_in_any_slot(retail, slot, formation):
    image = bytearray(retail)
    d, m, f = map(int, formation.split("-"))
    xi = " ".join(["GK"] + ["CB"] * d + ["CM"] * m + ["ST"] * f)
    write_team(image, slot, squad(xi, formation))
    names, codes = written(image, slot)
    lines = R.slot_lines(image, slot)
    assert R.FORMATIONS[image[R.formation_at(image, slot)]] == formation
    assert lines == [0] + [1] * d + [2] * m + [3] * f
    assert roles_of(names[:11]) == xi.split()
    assert all(line in R.ALLOWED_LINES[code] for line, code in zip(lines, codes[:11], strict=True))
