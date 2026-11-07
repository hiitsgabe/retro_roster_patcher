"""ISS 2000 formations: who stands where inside a line.

The synthetic cartridge holds only what `write_team` reads: the seven formation rows the patcher
copies (`ROWS`, the cartridge's own as far as seating reads them) and room for the records and the
team ratings. The tests that need the real cartridge are gated on RETRO_ROSTER_TEST_ROMS (a
directory holding iss2000.z64).
"""

from __future__ import annotations

import os
import string
import struct
from pathlib import Path

import pytest

from retro_roster_patcher.games.iss2000_n64 import rom as R
from retro_roster_patcher.games.iss2000_n64 import writer as W
from retro_roster_patcher.games.iss2000_n64.mapper import (
    FORMATION_DONORS,
    N64Player,
    N64StatMapper,
    N64Team,
)
from retro_roster_patcher.sports.models import Player, Team, TeamRoster
from tests.fixtures.older_roster import older_roster

SLOT = 69

# The rows of the cartridge's seven donor teams, per record (depth, width, line): the keeper, then
# the defence, midfield and attack, in record order. Further forward is a larger depth, an offset
# inside the line, and the team's right is a larger width.
ROWS = {
    (4, 4, 2): (
        (0, 0, 0),
        (5, -200, 1), (0, -65, 1), (0, 65, 1), (5, 200, 1),
        (-40, -60, 2), (-40, 60, 2), (40, -180, 2), (40, 180, 2),
        (0, -100, 3), (0, 100, 3),
    ),
    (4, 5, 1): (
        (0, 0, 0),
        (-20, -200, 1), (-40, -60, 1), (-40, 60, 1), (-20, 200, 1),
        (-40, -65, 2), (-40, 65, 2), (0, -250, 2), (0, 250, 2), (20, 0, 2),
        (40, 0, 3),
    ),
    (4, 3, 3): (
        (0, 0, 0),
        (30, -200, 1), (0, -65, 1), (0, 65, 1), (30, 200, 1),
        (-30, 0, 2), (20, -175, 2), (20, 175, 2),
        (0, -250, 3), (0, 250, 3), (10, 0, 3),
    ),
    (3, 5, 2): (
        (0, 0, 0),
        (0, -120, 1), (-40, 0, 1), (0, 120, 1),
        (-40, -60, 2), (-40, 60, 2), (0, -240, 2), (0, 240, 2), (60, 0, 2),
        (-40, -100, 3), (0, 80, 3),
    ),
    (3, 6, 1): (
        (0, 0, 0),
        (0, -130, 1), (-50, 0, 1), (0, 130, 1),
        (-20, -100, 2), (-20, 100, 2), (20, -240, 2), (20, 240, 2), (60, -180, 2), (60, 180, 2),
        (20, 0, 3),
    ),
    (3, 4, 3): (
        (0, 0, 0),
        (-10, -126, 1), (-50, 0, 1), (-10, 126, 1),
        (-30, -200, 2), (30, 200, 2), (0, -60, 2), (10, 60, 2),
        (-30, -150, 3), (-60, 150, 3), (-10, 0, 3),
    ),
    (5, 3, 2): (
        (0, 0, 0),
        (18, -202, 1), (0, -101, 1), (-27, 0, 1), (0, 101, 1), (18, 202, 1),
        (18, -176, 2), (-18, 0, 2), (18, 176, 2),
        (0, -101, 3), (0, 101, 3),
    ),
}  # fmt: skip
SIDE = {"LB": -1, "LM": -1, "LW": -1, "RB": 1, "RM": 1, "RW": 1}


def real_rom(test):
    gated = pytest.mark.skipif(
        not os.environ.get("RETRO_ROSTER_TEST_ROMS"),
        reason="set RETRO_ROSTER_TEST_ROMS to a directory holding iss2000.z64",
    )
    return pytest.mark.real_rom(gated(test))


@pytest.fixture
def retail() -> bytes:
    rom = Path(os.environ["RETRO_ROSTER_TEST_ROMS"]) / "iss2000.z64"
    if not rom.exists():
        pytest.fail(f"RETRO_ROSTER_TEST_ROMS is set but {rom} is missing")
    data = rom.read_bytes()
    assert R.is_iss2000(data)
    return data


def cartridge() -> bytearray:
    image = bytearray(W.DEFENCE_TABLE + R.TEAMS)
    for shape, donor in FORMATION_DONORS.items():
        for k, (depth, width, line) in enumerate(ROWS[shape]):
            at = R.FORMATIONS + donor * R.FORMATION_ROW + 6 * k
            struct.pack_into(">hhBB", image, at, depth, width, line, 0)
    return image


def squad(xi: str, shape: tuple[int, int, int], *, roles: bool = True) -> N64Team:
    """A club whose XI has the roles of `xi` (space separated, in that order), each named by his
    role and his place in the list (GK0, LB1, ...), then eleven bench players. `roles=False`
    drops the roles."""
    players = [
        N64Player(name=f"{r}{i}", number=i + 1, role=r if roles else "")
        for i, r in enumerate(xi.split())
    ]
    players += [N64Player(name=f"B{i}", number=12 + i) for i in range(R.PLAYERS_PER_TEAM - 11)]
    return N64Team(name="Club", short_name="CLB", players=players, formation=shape)


def place(name: str) -> int:
    """The place in the XI list a name carries: LB1 is the second in the list."""
    return int(name.lstrip(string.ascii_uppercase))


def written(image: bytes | bytearray) -> list[str]:
    """The names at the slot's eleven records, in record order."""
    at = [R.record_offset(SLOT, k) + R.NAME for k in range(11)]
    return [R.decode_name(image[o : o + R.NAME_LEN]) for o in at]


def lined_up(shape: tuple[int, int, int], rows: list[list[str]]) -> list[str]:
    """Write a club in `shape` whose XI is the keeper and then `rows` (a list per line); returns
    the names at the records."""
    image = cartridge()
    W.write_team(image, SLOT, squad(" ".join(["GK", *sum(rows, [])]), shape))
    return written(image)


def lines_of(shape: tuple[int, int, int]) -> list[list[str]]:
    """Each line of `shape` as roles with a side, the left one first: LB CB .. RB, LM CM .. RM and
    LW ST .. RW (a lone forward is a ST)."""
    d, m, f = shape
    return [
        ["LB", *["CB"] * (d - 2), "RB"],
        ["LM", *["CM"] * (m - 2), "RM"],
        ["ST"] if f == 1 else ["LW", *["ST"] * (f - 2), "RW"],
    ]


# --- who sits where --------------------------------------------------------------------------


@pytest.mark.parametrize("midfield", ["CDM CDM CAM", "CDM CAM CDM", "CAM CDM CDM"])
def test_the_cam_of_a_433_midfield_takes_its_most_advanced_place(midfield):
    names = lined_up((4, 3, 3), [["LB", "CB", "CB", "RB"], midfield.split(), ["LW", "ST", "RW"]])
    row = ROWS[(4, 3, 3)]
    depth = row[next(k for k, name in enumerate(names) if name.startswith("CAM"))][0]
    assert depth == max(d for d, _, line in row if line == 2) == 20


def test_the_lw_cut_into_a_442_midfield_takes_its_left_place():
    names = lined_up((4, 4, 2), [["LB", "CB", "CB", "RB"], "CDM CAM CDM LW".split(), ["ST", "ST"]])
    row = ROWS[(4, 4, 2)]
    width = row[names.index("LW8")][1]
    assert width == min(w for _, w, line in row if line == 2) == -180


@pytest.mark.parametrize(
    "back", ["CB LB CB RB CB", "LB CB CB CB RB", "RB CB CB CB LB", "CB CB CB RB LB"]
)
def test_the_full_backs_of_a_back_five_stand_at_its_widest_places(back):
    # The 5-3-2's back five is at widths -202, -101, 0, 101 and 202: both -202 and -101 are on the
    # left, and a centre back listed first used to take the touchline place.
    names = lined_up((5, 3, 2), [back.split(), ["CM"] * 3, ["ST"] * 2])
    widths = {name.rstrip("0123456789"): ROWS[(5, 3, 2)][k][1] for k, name in enumerate(names[:6])}
    assert (widths["LB"], widths["RB"]) == (-202, 202)


def test_the_wing_backs_of_a_midfield_of_six_stand_at_its_widest_places():
    # The 3-6-1's midfield: widths +-100 at depth -20, +-240 at 20 and +-180 at 60. The wing backs
    # used to take the inner places (-100 and 100), where they are deepest.
    names = lined_up((3, 6, 1), [["CB"] * 3, "LWB CDM CM CAM CAM RWB".split(), ["ST"]])
    row = ROWS[(3, 6, 1)]
    assert (row[names.index("LWB4")][1], row[names.index("RWB9")][1]) == (-240, 240)
    # The CDM and the CM are on the inner places, the deepest, and the CAMs 60 forward.
    assert [row[names.index(f"{r}{i}")][0] for r, i in (("CDM", 5), ("CM", 6))] == [-20, -20]
    assert [row[names.index(f"CAM{i}")][0] for i in (7, 8)] == [60, 60]


@pytest.mark.parametrize("shape", FORMATION_DONORS)
@pytest.mark.parametrize("order", ["as is", "reversed", "rotated"])
def test_a_side_role_stands_on_its_side_whatever_order_its_line_comes_in(shape, order):
    reorder = {
        "as is": lambda line: line,
        "reversed": lambda line: line[::-1],
        "rotated": lambda line: line[1:] + line[:1],
    }[order]
    names = lined_up(shape, [reorder(line) for line in lines_of(shape)])
    for k, name in enumerate(names):
        role = name.rstrip("0123456789")
        if role in SIDE:
            assert ROWS[shape][k][1] * SIDE[role] > 0, (name, ROWS[shape][k])


@pytest.mark.parametrize("shape", FORMATION_DONORS)
def test_seating_never_moves_a_player_to_another_line(shape):
    # Cut by index: the keeper, then the defenders, the midfielders and the forwards.
    want = [0, *[1] * shape[0], *[2] * shape[1], *[3] * shape[2]]
    names = lined_up(shape, [line[::-1] for line in lines_of(shape)])
    for k, name in enumerate(names):
        assert ROWS[shape][k][2] == want[place(name)], (name, k)


@pytest.mark.parametrize("shape", FORMATION_DONORS)
def test_players_without_a_role_stand_touchline_to_touchline_in_the_order_given(shape):
    image = cartridge()
    xi = " ".join(["GK", *sum(lines_of(shape), [])])
    W.write_team(image, SLOT, squad(xi, shape, roles=False))
    seat = {place(name): k for k, name in enumerate(written(image))}
    for i in range(1, 10):
        a, b = ROWS[shape][seat[i]], ROWS[shape][seat[i + 1]]
        if a[2] == b[2]:
            assert a[1] < b[1], (shape, i)


def test_a_squad_short_of_eleven_is_seated_by_the_roles_it_has():
    image = cartridge()
    team = squad("GK RB LB", (4, 4, 2))
    team.players = team.players[:3]
    W.write_team(image, SLOT, team)
    assert written(image)[:5] == ["GK0", "LB2", "", "", "RB1"]


def test_the_mapper_hands_each_player_his_role_to_the_writer():
    roles = "GK RB CB CB LB CDM CAM CDM RW ST LW".split()
    players = [
        Player(id=i, name=f"P{i}", position="Midfielder", role=role)
        for i, role in enumerate(roles, start=1)
    ]
    roster = TeamRoster(team=Team(id=1, name="Club"), players=players, extra={"formation": "4-3-3"})
    team = N64StatMapper().map_team(roster, {}, order_as_given=True)
    assert [p.role for p in team.players[:11]] == roles


def test_the_mapper_hands_an_older_rosters_players_the_roles_their_lineup_codes_read_as():
    # No formation and no role, only a lineup of a 4-3-3 with ESPN's codes.
    team = N64StatMapper().map_team(older_roster(), {})
    assert team.formation == (4, 3, 3)
    assert [p.role for p in team.players[:11]] == "GK LB CB CB RB CM CDM CM LW ST RW".split()
    assert [p.role for p in team.players[11:]] == [""] * 4  # the bench has no code to read


def test_an_older_rosters_edited_sheet_is_seated_by_its_lineup_roles_over_its_lineups_shape():
    # A bench striker replaces ST9, and the right back is listed before the left back.
    roster = older_roster()
    sheet = {p.name: p for p in roster.players}
    first = "GK1 RB2 CB5 CB6 LB3 CDM4 CM7 CM8 RW10 ST15 LW11".split()
    roster.players = [sheet[n] for n in first] + [p for p in roster.players if p.name not in first]
    team = N64StatMapper().map_team(roster, {}, order_as_given=True)
    assert team.formation == (4, 3, 3)
    image = cartridge()
    W.write_team(image, SLOT, team)
    record = zip(
        R.formation_depths(image, SLOT),
        R.formation_widths(image, SLOT),
        R.formation_lines(image, SLOT),
        strict=True,
    )
    assert tuple(record) == ROWS[(4, 3, 3)]  # the 4-3-3 donor's row is the slot's
    width = {name: ROWS[(4, 3, 3)][k][1] for k, name in enumerate(written(image))}
    assert width["LB3"] < 0 < width["RB2"]  # -200 and 200
    assert width["LW11"] < 0 < width["RW10"]  # -250 and 250


def test_an_older_rosters_edited_sheet_is_cut_by_index_over_its_lineups_shape():
    # The sheet lists CDM4 among the four defenders and LB3 first of the three midfielders, and a
    # bench striker for ST9: by index over the lineup's 4-3-3 they play there, whatever their
    # groups say (by their groups it would be the other way round).
    roster = older_roster()
    sheet = {p.name: p for p in roster.players}
    first = "GK1 RB2 CB5 CB6 CDM4 LB3 CM7 CM8 RW10 ST15 LW11".split()
    roster.players = [sheet[n] for n in first] + [p for p in roster.players if p.name not in first]
    team = N64StatMapper().map_team(roster, {}, order_as_given=True)
    assert team.formation == (4, 3, 3)
    image = cartridge()
    W.write_team(image, SLOT, team)
    line = dict(zip(written(image), R.formation_lines(image, SLOT), strict=True))
    assert (line["CDM4"], line["LB3"]) == (1, 2)


def test_a_roster_with_a_formation_of_its_own_keeps_it_over_its_lineups_shape():
    roster = older_roster()
    roster.extra["formation"] = "3-5-2"
    assert N64StatMapper().map_team(roster, {}, order_as_given=True).formation == (3, 5, 2)


def test_a_roster_with_neither_a_formation_nor_a_lineup_maps_by_the_groups_it_always_did():
    roster = older_roster()
    del roster.extra["lineup"]
    team = N64StatMapper().map_team(roster, {}, order_as_given=True)
    squad, shape = N64StatMapper().given_22(list(roster.players))
    assert team.formation == shape
    assert [p.player_id for p in team.players] == [p.id for p in squad]
    assert {p.role for p in team.players} == {""}


# --- the real cartridge ----------------------------------------------------------------------


def across(image: bytes, team: int, line: int, coordinate) -> list[int]:
    """The records of `team`'s `line`, from the smallest `coordinate` of the row to the largest
    (`R.formation_widths` or `R.formation_depths`)."""
    lines, values = R.formation_lines(image, team), coordinate(image, team)
    return sorted((k for k in range(11) if lines[k] == line), key=values.__getitem__)


@real_rom
def test_the_literal_rows_are_the_cartridges(retail):
    for shape, donor in FORMATION_DONORS.items():
        row = zip(
            R.formation_depths(retail, donor),
            R.formation_widths(retail, donor),
            R.formation_lines(retail, donor),
            strict=True,
        )
        assert tuple(row) == ROWS[shape], shape


@real_rom
def test_the_widest_place_of_the_cartridge_is_262_which_is_what_a_side_divides_by(retail):
    widths = [abs(w) for team in range(R.TEAMS) for w in R.formation_widths(retail, team)]
    assert max(widths) == W.TOUCHLINE == 262
    assert max(abs(w) for row in ROWS.values() for _, w, _ in row) == 250  # the donors' wingers


@real_rom
def test_the_teams_left_is_the_negative_width(retail):
    # The left-footed players (record byte 0x2A below 35) are the ones who play on the left. Of the
    # 106 teams' defences and midfields, 42 have one at the smallest width of the line and 10 at
    # the largest.
    def left_footed(team, k):
        return retail[R.record_offset(team, k) + R.FOOT] < 35

    smallest = largest = 0
    for team in range(R.TEAMS):
        for line in (1, 2):
            line_up = across(retail, team, line, R.formation_widths)
            smallest += left_footed(team, line_up[0])
            largest += left_footed(team, line_up[-1])
    assert smallest > 2 * largest


@real_rom
def test_further_forward_is_the_larger_depth(retail):
    # Record bytes 0x2F and 0x30 rate how much a player attacks and how much he covers. The player
    # at a team's most advanced midfield place leans further towards attack than the one at its
    # deepest in 82 teams, and less in 22.
    def lean(team, k):
        o = R.record_offset(team, k)
        return retail[o + 0x2F] - retail[o + 0x30]

    more = fewer = 0
    for team in range(R.TEAMS):
        deepest, *_, furthest = across(retail, team, 2, R.formation_depths)
        more += lean(team, furthest) > lean(team, deepest)
        fewer += lean(team, furthest) < lean(team, deepest)
    assert more > 2 * fewer


@real_rom
def test_the_full_backs_of_a_back_four_stand_at_the_width_extremes(retail):
    # Byte 5 of a slot is its role, and 4 is the full back's. Of the 106 teams, 77 play a back
    # four, and in all of them the two widest places of the line are the full backs'.
    def role(team, k):
        return retail[R.FORMATIONS + team * R.FORMATION_ROW + 6 * k + 5]

    backs = 0
    for team in range(R.TEAMS):
        line_up = across(retail, team, 1, R.formation_widths)
        if len(line_up) == 4:
            backs += 1
            assert (role(team, line_up[0]), role(team, line_up[-1])) == (4, 4), team
    assert backs == 77
