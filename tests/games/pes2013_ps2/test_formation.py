"""PES 2013 (PS2) writes each team's formation and each player's position by role.

The disc keeps one 52-byte formation record per team, 197 of them in `0_TEXT.AFS` entry 53,
part 1: the role codes, the x and the y of places 1-10 (the keeper, place 0, has none), then the
attack arrows and the defence of places 0-10. National row r is record r and club c is record
67 + c. `PRESETS` holds the editor's 12 formations in that layout (the tests at the end hold the
records to the disc when RETRO_ROSTER_TEST_ROMS has pes2013.iso).

The mapper cuts the eleven into the formation's lines by index. The patcher picks the preset that
shares most roles with the ten outfield players, seats each line on its places by role, side and
depth, and writes the preset into the team's record. A player's registered position (the high
nibble of byte 53) and its playable flag (bit 7 of byte 54, or of byte 53 + the nibble) follow
his role.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import pytest

from retro_roster_patcher.core.errors import RomError
from retro_roster_patcher.core.models import SlotMapping
from retro_roster_patcher.games.pes2013_ps2 import afs
from retro_roster_patcher.games.pes2013_ps2.formations import (
    FORMATIONS,
    HALF_WIDTH,
    MIDDLE,
    PRESETS,
    FormationTable,
    lines,
    pick_preset,
    place_roles,
    seats,
)
from retro_roster_patcher.games.pes2013_ps2.models import (
    FIRST_CLUB_FORMATION,
    FORMATION_ENTRY,
    FORMATION_PART,
    FORMATION_RECORD,
    FORMATION_TEAMS,
    NATIONAL_SLOT_BASE,
    PLAYER_ENTRIES,
    POSITION_GROUPS,
    TEXT_AFS,
)
from retro_roster_patcher.games.pes2013_ps2.patcher import (
    LINEUP_ROLES,
    ROLE_NIBBLE,
    PES2013PS2Patcher,
    pes_name,
    squad_order,
)
from retro_roster_patcher.games.pes2013_ps2.rom_reader import RomReader
from retro_roster_patcher.games.pes2013_ps2.rom_writer import RomWriter
from retro_roster_patcher.sports.lineup import ROLES, apply_lineup, parse_shape, pick_lineup
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster
from tests.fixtures import synthetic_pes2013_iso as fixture
from tests.fixtures.older_roster import older_roster

EDITOR_ORDER = [
    "4-4-2",
    "4-3-1-2",
    "4-4-1-1",
    "4-2-1-3",
    "4-5-1",
    "4-1-2-3",
    "4-3-3",
    "4-3-2-1",
    "3-4-1-2",
    "3-3-2-2",
    "3-4-3",
    "5-4-1",
]
# What the 4-3-3 preset is on the disc: West Midlands Village's 4-4-2 record (the fixture's
# `CLUB_0_FORMATION`) took these as its first 30 bytes and PCSX2 showed a 4-3-3, CB CB RB LB CMF
# CMF CMF RWF LWF CF, with the same players in the same places (2026-11-03). Its arrows and its
# defence were kept.
AFTER_433 = bytes.fromhex(
    "07 01 09 08 15 13 11 1e 1d 26  09 09 0c 0c 1a 1a 1a 2b 2b 2b  3f 29 57 11 4d 34 1b 48 20 34"
)
FOUR_TWO_ONE_THREE = bytes.fromhex(
    "07 01 09 08 0e 0a 1a 1e 1d 26  09 09 0c 0c 12 12 20 2b 2b 2b  3f 29 57 11 3d 2b 34 48 20 34"
)

GROUP = {
    "GK": "Goalkeeper",
    **dict.fromkeys(("CB", "RB", "LB", "RWB", "LWB"), "Defender"),
    **dict.fromkeys(("CDM", "CM", "CAM", "RM", "LM"), "Midfielder"),
    **dict.fromkeys(("RW", "LW", "ST"), "Attacker"),
}
# The ESPN code a lineup gives each role.
CODE = {
    "GK": "G", "CB": "CD", "RB": "RB", "LB": "LB", "RWB": "RWB", "LWB": "LWB", "CDM": "DM",
    "CM": "CM", "CAM": "AM", "RM": "RM", "LM": "LM", "RW": "RW", "LW": "LW", "ST": "CF",
}  # fmt: skip
XI_433 = ("GK", "CB", "CB", "RB", "LB", "CM", "CM", "CM", "RW", "LW", "ST")
BENCH = ("GK", "CB", "CM", "ST", "CB", "CM", "ST", "RB", "LB", "CM", "ST", "LW")

FIXTURE = json.loads(
    (Path(__file__).parents[2] / "fixtures" / "lineups_2026-11-02.json").read_text(encoding="utf-8")
)


# --- helpers ------------------------------------------------------------------------------


@pytest.fixture
def iso(tmp_path):
    path = tmp_path / "pes.iso"
    fixture.write_iso(path)
    return path


def _team(xi=XI_433, formation="4-3-3", bench=BENCH, lineup=False, roles=True, team_id=7):
    """Players named by role and index ("CB1"), `xi` first and then the bench. `lineup`: the
    first eleven are the club's recent starters; `roles=False` leaves every player without a role
    (a rosters file from before the roles)."""
    ordered = [*xi, *bench]
    players = [
        Player(
            id=100 + i,
            name=f"{role}{i}",
            first_name=role,
            last_name=f"{role}{i}",
            position=GROUP[role],
            role=role if roles else "",
        )
        for i, role in enumerate(ordered)
    ]
    extra: dict = {"formation": formation} if formation else {}
    if lineup:
        starters = [
            {"id": p.id, "position": CODE[role], "place": k}
            for k, (p, role) in enumerate(zip(players[:11], xi, strict=True), 1)
        ]
        extra["lineup"] = {"formation": formation, "event": 1, "starters": starters}
    return TeamRoster(
        team=Team(id=team_id, name="Order FC", code="ORD"), players=players, extra=extra
    )


def _role(name):
    """The role a `_team` player is named after."""
    return name.rstrip("0123456789")


def _league(*rosters):
    return LeagueData(league=League(id=1, name="Fixture League", season=2026), teams=list(rosters))


def _map(tmp_path, roster, slot=0, **kwargs):
    return PES2013PS2Patcher(tmp_path).map_rosters(
        _league(roster), [SlotMapping(slot_index=slot, team_id=roster.team.id)], **kwargs
    )


def _patch(tmp_path, iso, rosters, name="out.iso", **options):
    out = tmp_path / name
    PES2013PS2Patcher(tmp_path).patch(
        rom_path=iso, output_path=out, rosters=rosters, logos=False, kits=False, looks=False,
        **options,
    )  # fmt: skip
    return out


def _read(path):
    reader = RomReader(str(path))
    assert reader.load()
    return reader


def _entry(path, index):
    return _read(path).read(TEXT_AFS, index)


def _name(tables, index):
    return tables.record(index)[:32].decode("utf-16-le").split("\0")[0]


def _stands(out, club=0):
    """The names on squad places 0-10 of a club."""
    tables = _read(out).player_tables(PLAYER_ENTRIES[0])
    return [_name(tables, i) for i in tables.club_squad(club)[:11]]


def _stands_roles(out, club=0):
    """The roles the names on squad places 0-10 of a club are named after."""
    return [_role(name) for name in _stands(out, club)]


def _galo():
    """The Atletico-MG of the 2026-11-02 snapshot: the user's eleven (a 4-2-3-1 the lineup folds to
    a 4-3-3) with their roles; the squad players the lineup gives no role are midfielders."""
    t = FIXTURE["teams"]["7632"]
    name = {int(k): v for k, v in t["names"].items()}
    positions = {int(k): v for k, v in t["positions"].items()}
    lineup = pick_lineup(t["matches"], dict.fromkeys(t["squad"], ""), positions)
    players = [
        Player(id=i, name=name[i], position=GROUP[lineup["roles"].get(str(i), "CM")])
        for i in t["squad"]
    ]
    roster = TeamRoster(team=Team(id=7632, name="Atletico MG", code="CAM"), players=players)
    apply_lineup(roster, lineup)
    return roster, name


GALO_ELEVEN = [
    "Éverson",
    "Ruan",
    "Vitor Hugo",
    "Natanael",
    "Renan Lodi",
    "Alan Franco",
    "Igor Gomes",
    "Fred",
    "Tomás Cuello",
    "Bernard",
    "Thiago Borbas",
]


# --- the presets --------------------------------------------------------------------------


def test_433_preset_is_the_disc_bytes():
    assert PRESETS["4-3-3"] == AFTER_433


def test_the_twelve_presets_are_the_editors_in_its_order_as_thirty_disc_bytes():
    assert list(PRESETS) == EDITOR_ORDER
    assert {len(preset) for preset in PRESETS.values()} == {30}
    assert PRESETS["4-2-1-3"] == FOUR_TWO_ONE_THREE


def _keeps_the_editors_rules(record, team):
    """The depth bands and sides the editor holds a formation to (and the disc's 197 records keep):
    ten different codes in 1-40, the places of a line one after the other, the defence at x up to
    15, the midfield between 16 and 34, the attack from 35 on, left roles at y up to 50 and right
    roles from 54."""
    roles, xs, ys = record[:10], record[10:20], record[20:30]
    assert len(set(roles)) == 10 and all(1 <= r <= 40 for r in roles), team
    bands = [0 if r <= 9 else 1 if r <= 28 else 2 for r in roles]
    assert bands == sorted(bands), team
    for role, x, y in zip(roles, xs, ys, strict=True):
        assert (x <= 15) if role <= 9 else (16 <= x <= 34) if role <= 28 else (x >= 35), team
        assert y <= 50 if role in (8, 15, 22, 29) else True, team
        assert y >= 54 if role in (9, 16, 23, 30) else True, team


@pytest.mark.parametrize("name", EDITOR_ORDER)
def test_a_preset_keeps_the_editors_depth_and_side_rules(name):
    _keeps_the_editors_rules(PRESETS[name], name)


def test_the_formations_are_the_six_shapes_the_presets_cover():
    assert FORMATIONS == ("4-4-2", "4-3-3", "4-5-1", "3-5-2", "3-4-3", "5-4-1")
    assert Counter(lines(p) for p in PRESETS.values()) == {
        (4, 4, 2): 2,
        (4, 3, 3): 3,
        (4, 5, 1): 3,
        (3, 5, 2): 2,
        (3, 4, 3): 1,
        (5, 4, 1): 1,
    }
    assert [parse_shape(s) for s in FORMATIONS] == [
        (4, 4, 2),
        (4, 3, 3),
        (4, 5, 1),
        (3, 5, 2),
        (3, 4, 3),
        (5, 4, 1),
    ]


def test_lines_count_the_codes_by_the_bands_1_to_9_10_to_28_and_29_to_40():
    assert lines(bytes([1, 9, 10, 28, 29, 40, 12, 12, 12, 12])) == (2, 6, 2)
    assert lines(PRESETS["4-3-3"]) == (4, 3, 3)
    assert lines(fixture.CLUB_0_FORMATION) == (4, 4, 2)  # a whole 52-byte record reads the same


READ_BACK = {
    **dict.fromkeys(range(1, 8), "CB"),  # CB, and the sweepers
    8: "LB",
    9: "RB",
    **dict.fromkeys(range(10, 15), "CDM"),
    15: "LWB",
    16: "RWB",
    **dict.fromkeys(range(17, 22), "CM"),
    22: "LM",
    23: "RM",
    **dict.fromkeys(range(24, 29), "CAM"),
    29: "LW",
    30: "RW",
    **dict.fromkeys(range(31, 41), "ST"),  # the second strikers too
}


@pytest.mark.parametrize(("code", "role"), READ_BACK.items())
def test_place_roles_read_a_code_back_as_a_role(code, role):
    assert place_roles(bytes([code] * 10)) == [role] * 10


def test_place_roles_are_the_ten_outfield_places_of_a_record():
    assert place_roles(PRESETS["4-3-3"]) == "CB CB RB LB CM CM CM RW LW ST".split()
    assert place_roles(fixture.CLUB_0_FORMATION)[4] == "CDM"  # the disc's 4-4-2 has a DMF (12)
    assert set(READ_BACK.values()) == set(ROLES) - {"GK"}


# --- picking a preset ---------------------------------------------------------------------


def test_the_galo_eleven_picks_the_four_two_one_three():
    roster, _ = _galo()
    lineup = roster.extra["lineup"]
    roles = [s["role"] for s in lineup["starters"] if s["role"] != "GK"]
    assert sorted(roles) == sorted("CB CB RB LB CDM CDM CAM RW LW ST".split())
    assert pick_preset(roster.extra["formation"], roles) == "4-2-1-3"


def test_a_three_midfielder_433_picks_the_plain_433():
    assert pick_preset("4-3-3", "CB CB RB LB CM CM CM RW LW ST".split()) == "4-3-3"


def test_the_preset_sharing_most_roles_wins_and_a_tie_goes_to_the_editors_order():
    # CDM CM CAM shares nine roles with the 4-2-1-3 (CDM, CAM) and the 4-1-2-3 (CDM, CAM) and
    # eight with the 4-3-3 (CM): the first of the two.
    roles = "CB CB RB LB CDM CM CAM RW LW ST".split()
    assert pick_preset("4-3-3", roles) == "4-2-1-3"
    # Goalkeepers in the list do not count toward any preset.
    assert pick_preset("4-3-3", ["GK", *roles]) == "4-2-1-3"


@pytest.mark.parametrize("name", EDITOR_ORDER)
def test_every_presets_own_place_roles_pick_itself(name):
    shape = "-".join(map(str, lines(PRESETS[name])))
    assert pick_preset(shape, place_roles(PRESETS[name])) == name


def test_without_roles_a_shape_gets_the_first_of_its_presets_in_the_editors_order():
    first = {s: pick_preset(s, []) for s in FORMATIONS}
    assert first == {
        "4-4-2": "4-4-2",
        "4-3-3": "4-2-1-3",
        "4-5-1": "4-4-1-1",
        "3-5-2": "3-4-1-2",
        "3-4-3": "3-4-3",
        "5-4-1": "5-4-1",
    }


def test_a_shape_the_presets_do_not_have_picks_among_the_nearest_ones():
    # A four-line formation is read by its three lines (4-2-3-1 is 4-3-3) ...
    assert pick_preset("4-2-3-1", "CB CB RB LB CM CM CM RW LW ST".split()) == "4-3-3"
    # ... and 5-3-2 is as near 4-4-2 and 4-3-3 as 5-4-1, which keeps the back five.
    assert lines(PRESETS[pick_preset("5-3-2", [])]) == (5, 4, 1)
    assert pick_preset("not a formation", []) == "4-4-2"


# --- seating a line -----------------------------------------------------------------------


def test_a_line_takes_the_places_whose_role_it_plays_whatever_the_order_it_is_given_in():
    # Places 1-4 of a 4-3-3: CB, CB, RB, LB. The fullbacks listed first still take their own.
    assert seats(["RB", "LB", "CB", "CB"], PRESETS["4-3-3"]) == [2, 3, 0, 1]
    assert seats(["CB", "CB", "RB", "LB"], PRESETS["4-3-3"]) == [0, 1, 2, 3]
    assert seats(["LB", "RB", "CB", "CB"], PRESETS["4-3-3"]) == [3, 2, 0, 1]
    # Where side tells the players apart it decides: both right backs keep to the right.
    assert seats(["RB", "RB", "CB", "CB"], PRESETS["4-3-3"]) == [0, 2, 1, 3]


def test_a_player_whose_role_no_place_has_is_seated_by_his_side():
    # The 4-4-2's midfield is four CMF places at y 77, 61, 43 and 27: the right midfielder takes
    # the outermost one on the right and the left midfielder the outermost on the left.
    roles = ["CB", "CB", "RB", "LB", "RM", "LM", "CM", "CM", "ST", "ST"]
    placed = seats(roles, PRESETS["4-4-2"])
    ys = PRESETS["4-4-2"][20:]
    assert (ys[placed[4]], ys[placed[5]]) == (77, 27)
    assert sorted(placed) == list(range(10))


def standing(roles, preset):
    """The role on each of places 1-10 once the ten `roles` are seated on the preset."""
    stand = [""] * len(roles)
    for role, place in zip(roles, seats(roles, PRESETS[preset]), strict=True):
        stand[place] = role
    return stand


@pytest.mark.parametrize(
    ("preset", "listed", "stand"),
    [
        # Lines that seating a role on a place of exactly its own first got wrong: RM, CM, CM, LM
        # on the 4-4-2's four CMF places (y 77, 61, 43, 27), ...
        ("4-4-2", "CB CB RB LB RM CM CM LM ST ST", "CB CB RB LB RM CM CM LM ST ST"),
        # ... the 5-4-1's two wide midfielders on the RMF and LMF places, not the DMF ones, ...
        ("5-4-1", "CB CB CB RB LB RM CM CM LM ST", "CB CB CB RB LB CM CM RM LM ST"),
        # ... the 4-4-1-1's, with the left midfielder on his own place and not a DMF one, ...
        ("4-4-1-1", "CB CB RB LB CDM CM RM LM CAM ST", "CB CB RB LB CDM CM RM LM CAM ST"),
        # ... the 4-5-1's wide midfielders on the wide places, the two CMs on the CMF ones, ...
        ("4-5-1", "CB CB RB LB CDM RM CM CM LM ST", "CB CB RB LB CDM CM CM RM LM ST"),
        # ... and the back four, whichever the full backs are listed before.
        ("4-4-2", "RB LB CB CB RM CM CM LM ST ST", "CB CB RB LB RM CM CM LM ST ST"),
        ("4-4-2", "LB RB CB CB RM CM CM LM ST ST", "CB CB RB LB RM CM CM LM ST ST"),
    ],
)
def test_the_review_lines_stand_the_wide_players_on_the_wide_places(preset, listed, stand):
    assert standing(listed.split(), preset) == stand.split()


@pytest.mark.parametrize(
    ("preset", "listed", "stand"),
    [
        # The 3-4-1-2 puts its wing backs (x 24) a step ahead of its holding pair (x 18) ...
        ("3-4-1-2", "CB CB CB CDM CDM RWB LWB CAM ST ST", "CB CB CB CDM CDM RWB LWB CAM ST ST"),
        ("3-4-1-2", "CB CB CB RWB LWB CDM CDM CAM ST ST", "CB CB CB CDM CDM RWB LWB CAM ST ST"),
        ("3-4-1-2", "CB CB CB LWB CAM CDM RWB CDM ST ST", "CB CB CB CDM CDM RWB LWB CAM ST ST"),
        # ... and the 3-4-3 its own (x 24) ahead of its central midfielders (x 22), the opposite of
        # the order of the roles' depths, but the wing backs are wide and the midfielders are not.
        ("3-4-3", "CB CB CB RWB LWB CM CM RW LW ST", "CB CB CB RWB LWB CM CM RW LW ST"),
        ("3-4-3", "CB CB CB CM CM RWB LWB RW LW ST", "CB CB CB RWB LWB CM CM RW LW ST"),
        ("3-4-3", "CB CB CB LWB CM RWB CM LW RW ST", "CB CB CB RWB LWB CM CM RW LW ST"),
    ],
)
def test_wing_backs_and_central_midfielders_do_not_trade_places(preset, listed, stand):
    assert standing(listed.split(), preset) == stand.split()


def test_the_widest_places_of_the_presets_are_the_touchlines():
    # The full backs' and wing backs' places at y 17 and 87 are 35 from the middle, which is what
    # a place's side is measured against.
    ys = {y for preset in PRESETS.values() for y in preset[20:]}
    assert (min(ys), max(ys)) == (MIDDLE - HALF_WIDTH, MIDDLE + HALF_WIDTH) == (17, 87)


def test_strikers_listed_together_take_the_attack_in_the_given_order():
    # The 4-2-1-3's attack, places 8-10, is RW, LW, ST.
    roles = ["CB"] * 4 + ["CDM", "CDM", "CAM"] + ["ST"] * 3
    assert seats(roles, PRESETS["4-2-1-3"])[7:] == [7, 8, 9]


def test_a_short_squad_seats_the_places_it_has_players_for():
    assert seats(["CB", "CB", "RB", "LB", "CM"], PRESETS["4-3-3"]) == [0, 1, 2, 3, 4]
    assert seats([], PRESETS["4-3-3"]) == []


# --- the table ----------------------------------------------------------------------------


def _parts(entry):
    return afs.Table.parse(afs.unwrap(entry)[1]).parts


def test_formation_table_round_trips_and_touches_only_part_1():
    entry = fixture.build_formation_entry()
    table = FormationTable.parse(entry)
    assert FORMATION_PART == 1 and FORMATION_RECORD == 52 and FORMATION_TEAMS == 197
    assert [table.get(t) for t in range(FORMATION_TEAMS)] == [
        fixture.formation_record(t) for t in range(FORMATION_TEAMS)
    ]
    assert table.get(FIRST_CLUB_FORMATION) == fixture.CLUB_0_FORMATION  # club 0 is record 67

    again = FormationTable.parse(table.build())
    assert [again.get(t) for t in range(FORMATION_TEAMS)] == [table.get(t) for t in range(197)]

    table.set(FIRST_CLUB_FORMATION, PRESETS["4-3-3"])
    built = table.build()
    before, after = _parts(entry), _parts(built)
    assert len(before) == len(after) == 10
    assert [a == b for a, b in zip(before, after, strict=True)] == [
        k != FORMATION_PART for k in range(10)
    ]  # every part but part 1 is the disc's own bytes
    old, new = afs.unwrap(before[1])[1], afs.unwrap(after[1])[1]
    at = FIRST_CLUB_FORMATION * FORMATION_RECORD
    assert new[:at] == old[:at] and new[at + 30 :] == old[at + 30 :]
    assert new[at : at + 30] == PRESETS["4-3-3"] != old[at : at + 30]
    assert built[:4] == entry[:4]  # the entry is still one raw block of the same kind
    assert (
        FormationTable.parse(built).get(FIRST_CLUB_FORMATION)[30:] == fixture.CLUB_0_FORMATION[30:]
    )


def test_a_set_keeps_the_arrows_and_the_defence_and_checks_what_it_is_given():
    table = FormationTable.parse(fixture.build_formation_entry())
    table.set(68, PRESETS["3-4-3"])
    assert table.get(68)[:30] == PRESETS["3-4-3"] and table.get(68)[30:] == bytes([68]) * 22
    assert table.get(67) == fixture.CLUB_0_FORMATION and table.get(69) == bytes([69]) * 52
    with pytest.raises(ValueError):
        table.set(68, PRESETS["3-4-3"][:29])
    for team in (-1, FORMATION_TEAMS):
        with pytest.raises(RomError):
            table.get(team)
        with pytest.raises(RomError):
            table.set(team, PRESETS["3-4-3"])


def test_an_entry_that_is_not_the_formation_table_is_refused():
    for block in (
        b"\0\0",
        fixture.build_kit_entry(),
        fixture.block(fixture.table([b""]), compress=False),
    ):
        with pytest.raises(RomError):
            FormationTable.parse(block)


def test_the_writer_puts_back_entry_53_and_nothing_else(iso, tmp_path):
    out = tmp_path / "out.iso"
    writer = RomWriter(str(iso), str(out))
    writer.copy_iso()
    table = writer.reader.formations()
    table.set(FIRST_CLUB_FORMATION, PRESETS["4-3-3"])
    writer.write_formations(table)

    entry = writer.reader.entry(TEXT_AFS, FORMATION_ENTRY)
    start = writer.reader.extent(TEXT_AFS).offset + entry.offset
    before, after = iso.read_bytes(), out.read_bytes()
    assert len(before) == len(after)
    assert (
        before[:start] == after[:start]
        and before[start + entry.size :] == after[start + entry.size :]
    )
    again = _read(out).formations()
    assert again.get(FIRST_CLUB_FORMATION)[:30] == PRESETS["4-3-3"]
    assert again.get(FIRST_CLUB_FORMATION)[30:] == fixture.CLUB_0_FORMATION[30:]
    assert all(again.get(t) == fixture.formation_record(t) for t in range(197) if t != 67)


# --- a role is a registered position and its playable flag ---------------------------------


def test_lineup_roles_wf_is_10_and_ss_11():
    # The disc: nibble 10 comes with the WF flag (byte 63) in 178 of 178 records, nibble 11 with
    # the SS flag (byte 64) in 274 of 274. ESPN's wide forwards are wing forwards, SS a second
    # striker.
    assert LINEUP_ROLES["SS"] == 11
    assert {LINEUP_ROLES[code] for code in ("LW", "RW", "LF", "RF")} == {10}
    assert POSITION_GROUPS[10] == POSITION_GROUPS[11] == "Attacker"


def test_a_wing_back_register_is_a_defender():
    assert POSITION_GROUPS[6] == "Defender"


def test_each_role_has_the_registered_position_of_its_kind():
    assert ROLE_NIBBLE == {
        "GK": 0, "CB": 3, "RB": 4, "LB": 4, "RWB": 6, "LWB": 6, "CDM": 5, "CM": 7, "RM": 8,
        "LM": 8, "CAM": 9, "RW": 10, "LW": 10, "ST": 12,
    }  # fmt: skip
    assert set(ROLE_NIBBLE) == set(ROLES)


@pytest.mark.parametrize(
    ("nibble", "byte"),
    [(0, 54), (2, 55), (3, 56), (4, 57), (5, 58), (6, 59), (7, 60), (8, 61), (9, 62)]
    + [(10, 63), (11, 64), (12, 65)],
)
def test_set_playable_sets_bit_7_of_the_flag_byte_and_nothing_else(iso, nibble, byte):
    tables = _read(iso).player_tables()
    before = tables.record(5)
    RomWriter.set_playable(tables, 5, nibble)
    after = tables.record(5)
    assert after[byte] == before[byte] | 0x80
    assert after[:byte] == before[:byte] and after[byte + 1 :] == before[byte + 1 :]
    assert tables.record(4) == _read(iso).player_tables().record(4)  # the neighbours are untouched


@pytest.mark.parametrize(
    ("nibble", "byte"),
    [(0, 54), (2, 55), (3, 56), (4, 57), (5, 58), (6, 59), (7, 60), (8, 61), (9, 62)]
    + [(10, 63), (11, 64), (12, 65)],
)
def test_set_playable_leaves_the_roles_flag_the_only_one_lit_and_every_ability_bit(
    iso, nibble, byte
):
    """The record's last occupant played other positions (SS lit for the wingers, in Task 17's
    emulator check): the twelve flags, bit 7 of bytes 54-65, are cleared first. Bits 0-6 of those
    bytes are abilities, and every other byte stays."""
    tables = _read(iso).player_tables()
    players = bytearray(tables.players)
    at = 5 * 124
    for flag in range(54, 66):
        players[at + flag] |= 0x80  # an occupant who played them all
    tables.parts[0] = bytes(players)
    before = tables.record(5)
    RomWriter.set_playable(tables, 5, nibble)
    after = tables.record(5)
    assert [flag for flag in range(54, 66) if after[flag] & 0x80] == [byte]
    assert all(after[i] & 0x7F == before[i] & 0x7F for i in range(124))
    assert after[:54] == before[:54] and after[66:] == before[66:]
    assert tables.record(4) == _read(iso).player_tables().record(4)  # the neighbours are untouched


def test_a_role_writes_its_registered_position_and_its_flag(tmp_path, iso):
    """A left winger gets nibble 10 and byte 63's bit 7; bits 0-6 of it, the low nibble of byte 53
    and every other byte of the record stay."""
    original = _read(iso).player_tables(PLAYER_ENTRIES[0])
    out = _patch(tmp_path, iso, _map(tmp_path, _team(), order_as_given=True))
    for entry in PLAYER_ENTRIES:
        tables = _read(out).player_tables(entry)
        for place, index in enumerate(tables.club_squad(0)[:11]):
            record, disc = tables.record(index), original.record(index)
            role = _role(_name(tables, index))
            nibble = ROLE_NIBBLE[role]
            flag = 54 if nibble == 0 else 53 + nibble
            assert record[53] >> 4 == nibble and record[53] & 15 == disc[53] & 15, (place, role)
            assert record[flag] & 0x80 and record[flag] & 0x7F == disc[flag] & 0x7F
            same = [i for i in range(48, 92) if i not in (53, flag)]  # the look is the writer's
            assert all(record[i] == disc[i] for i in same), (place, role)
    lw = next(i for i in tables.club_squad(0)[:11] if _role(_name(tables, i)) == "LW")
    assert tables.record(lw)[53] >> 4 == 10 and tables.record(lw)[63] == 0x11 | 0x80


# --- the mapper: the readable formation and the eleven -------------------------------------


def test_map_rosters_carries_a_readable_formation_and_every_players_role(tmp_path):
    record = _map(tmp_path, _team(formation="4-2-3-1"), order_as_given=True).teams[0]
    assert record.formation == "4-2-3-1"  # four lines are read by their three (parse_shape)
    assert [p.role_name for p in record.players[:11]] == list(XI_433)
    assert [p.role_name for p in record.players[11:]] == list(BENCH)
    assert all(p.role is not None for p in record.players[:11])  # the nibble still marks starters
    assert all(p.role is None for p in record.players[11:])


@pytest.mark.parametrize("text", ["", "4-4-3", "banana", "4-4"])
def test_an_unreadable_formation_leaves_the_record_without_one(tmp_path, text):
    record = _map(tmp_path, _team(formation=text), order_as_given=True).teams[0]
    assert record.formation == ""


def test_a_role_the_table_does_not_know_is_no_role(tmp_path):
    roster = _team()
    roster.players[3].role = "WIZARD"
    record = _map(tmp_path, roster, order_as_given=True).teams[0]
    assert record.players[3].role_name == ""


def test_exactly_one_keeper_starts_the_first_listed_and_the_outfield_follows_by_index(tmp_path):
    order = ("CB", "GK", "CB", "CB", "CB", "CM", "CM", "CM", "CM", "GK", "ST", "ST", "ST")
    record = _map(tmp_path, _team(order, bench=()), order_as_given=True).teams[0]
    names = [p.display_name for p in record.players]
    assert names[:11] == [
        "GK1",
        "CB0",
        "CB2",
        "CB3",
        "CB4",
        "CM5",
        "CM6",
        "CM7",
        "CM8",
        "ST10",
        "ST11",
    ]
    assert names[11:] == ["GK9", "ST12"]  # the spare keeper and the eleventh outfielder wait
    assert [p.role is not None for p in record.players] == [True] * 11 + [False] * 2


def test_without_a_keeper_the_ten_outfielders_start(tmp_path):
    order = ("CB", "CB", "CB", "CB", "CM", "CM", "CM", "CM", "ST", "ST", "ST")
    record = _map(tmp_path, _team(order, bench=()), order_as_given=True).teams[0]
    assert [p.role is not None for p in record.players] == [True] * 10 + [False]


def test_the_automatic_eleven_goes_back_to_front_by_the_depth_of_its_roles(tmp_path):
    roster, name = _galo()
    lineup = roster.extra["lineup"]
    ordered = squad_order(roster.players, lineup, roster.extra["formation"])
    roles = [p.role for p in ordered[:11]]
    assert roles == "GK RB LB CB CB CDM CDM CAM RW LW ST".split()
    # Without a formation it is the groups' order, as before: the midfielders by lineup place.
    plain = squad_order(roster.players, lineup)
    assert [p.role for p in plain[:11]] == "GK RB LB CB CB CDM RW CDM CAM LW ST".split()
    assert {p.id for p in plain[:11]} == {p.id for p in ordered[:11]}


def test_an_unedited_roster_maps_as_suggested(tmp_path):
    roster, _ = _galo()
    patcher = PES2013PS2Patcher(tmp_path)
    suggested = patcher.suggest_squad_order(roster)
    assert [p.role for p in suggested[:11]] == "GK RB LB CB CB CDM CDM CAM RW LW ST".split()
    given = TeamRoster(team=roster.team, players=suggested, extra=dict(roster.extra))
    auto = _map(tmp_path, roster).teams[0]
    edited = _map(tmp_path, given, order_as_given=True).teams[0]
    assert [p.player_id for p in auto.players] == [p.player_id for p in edited.players]


# --- patch: the record, and the eleven standing on it --------------------------------------


def test_a_433_writes_the_preset_into_the_clubs_record_and_keeps_its_arrows(tmp_path, iso):
    out = _patch(tmp_path, iso, _map(tmp_path, _team(), order_as_given=True))
    table = _read(out).formations()
    assert table.get(FIRST_CLUB_FORMATION)[:30] == PRESETS["4-3-3"]  # club 0 is record 67
    assert table.get(FIRST_CLUB_FORMATION)[30:] == fixture.CLUB_0_FORMATION[30:]
    assert all(table.get(t) == fixture.formation_record(t) for t in range(197) if t != 67)


@pytest.mark.parametrize(
    "xi",
    [XI_433, ("GK", "RB", "LB", "CB", "CB", "CM", "CM", "CM", "ST", "LW", "RW")],
    ids=["places order", "fullbacks first"],
)
def test_in_a_433_each_starter_stands_on_his_roles_place(tmp_path, iso, xi):
    out = _patch(tmp_path, iso, _map(tmp_path, _team(xi), order_as_given=True))
    on = [_role(n) for n in _stands(out)]
    assert on[0] == "GK" and on[1:3] == ["CB", "CB"]
    assert on[3] == "RB" and on[4] == "LB"
    assert on[5:8] == ["CM"] * 3
    assert on[8:] == ["RW", "LW", "ST"]


def test_the_galo_stands_on_the_four_two_one_three_with_the_pivot_deep_and_the_wings_wide(
    tmp_path, iso
):
    roster, _ = _galo()
    out = _patch(tmp_path, iso, _map(tmp_path, roster))
    table = _read(out).formations()
    assert table.get(FIRST_CLUB_FORMATION)[:30] == PRESETS["4-2-1-3"]
    assert _stands(out) == [pes_name(Player(id=0, name=n)) for n in GALO_ELEVEN]


def test_an_edited_order_with_an_edited_formation_cuts_the_lines_by_index(tmp_path, iso):
    ten = "CB CB CB CB CM CM CM CM ST ST".split()
    xi = ("GK", *ten)  # the editor's list, whatever the formation says
    stands = {}
    for shape in FORMATIONS:
        d, m, f = parse_shape(shape)
        mapped = _map(tmp_path, _team(xi, shape), order_as_given=True)
        out = _patch(tmp_path, iso, mapped, shape)
        stands[shape] = on = [_role(n) for n in _stands(out)]
        assert on[0] == "GK"
        assert sorted(on[1 : 1 + d]) == sorted(ten[:d]), shape
        assert sorted(on[1 + d : 1 + d + m]) == sorted(ten[d : d + m]), shape
        assert sorted(on[1 + d + m :]) == sorted(ten[d + m :]), shape
        assert lines(_read(out).formations().get(67)) == (d, m, f)
    # A 3-4-3 reads the fourth centre back as a midfielder, and the fourth midfielder as a forward.
    on = stands["3-4-3"]
    assert on[1:4] == ["CB"] * 3 and "CB" in on[4:8] and on[8:].count("CM") == 1


def test_a_second_club_and_a_national_team_are_written_in_one_pass(tmp_path, iso, monkeypatch):
    writes = []
    original = RomWriter.write_formations
    monkeypatch.setattr(
        RomWriter,
        "write_formations",
        lambda self, table: writes.append(table) or original(self, table),
    )
    patcher = PES2013PS2Patcher(tmp_path)
    league = _league(_team(formation="3-5-2", team_id=7), _team(formation="5-4-1", team_id=8))
    mapped = patcher.map_rosters(
        league,
        [
            SlotMapping(slot_index=1, team_id=7),
            SlotMapping(slot_index=2, team_id=8),
            SlotMapping(slot_index=NATIONAL_SLOT_BASE + 4, team_id=7),
        ],
        order_as_given=True,
    )
    out = _patch(tmp_path, iso, mapped)
    assert len(writes) == 1
    table = _read(out).formations()
    # Club 1 is record 68, club 2 record 69 and national row 4 record 4.
    assert lines(table.get(68)) == (3, 5, 2) and lines(table.get(69)) == (5, 4, 1)
    assert lines(table.get(4)) == (3, 5, 2)
    assert table.get(67) == fixture.CLUB_0_FORMATION  # club 0 was not patched
    assert table.get(5) == bytes([5]) * 52


def test_a_national_team_gets_its_rows_record_and_its_row_in_place_order(tmp_path, iso):
    xi = ("GK", "LB", "RB", "CB", "CB", "CM")  # the fixture's pool has room for six
    team = _team(xi, "4-4-2", bench=())
    mapped = _map(tmp_path, team, slot=NATIONAL_SLOT_BASE, order_as_given=True)
    out = _patch(tmp_path, iso, mapped)
    table = _read(out).formations()
    assert table.get(0)[:30] == PRESETS["4-4-2"] and table.get(1) == bytes([1]) * 52
    for entry in PLAYER_ENTRIES:
        tables = _read(out).player_tables(entry)
        row = tables.national_squad(0)
        # Places 1-4 are CB, CB, RB, LB whatever order the squad was listed in.
        assert [_name(tables, i) for i in row[:6]] == ["GK0", "CB3", "CB4", "RB2", "LB1", "CM5"]
        assert [tables.record(i)[53] >> 4 for i in row[:6]] == [0, 3, 3, 4, 4, 7]
        assert tables.record(row[0])[54] & 0x80  # the keeper's flag
        assert all(tables.record(i)[57] & 0x80 for i in row[3:5])  # the fullbacks'


def test_an_older_roster_is_written_the_preset_of_its_lineups_shape_and_stands_by_its_codes(
    tmp_path, iso
):
    # No role, no extra["formation"]: the lineup's 4-3-3 and its codes (RB LB CM CD-R CD-L ...).
    mapped = _map(tmp_path, older_roster())
    record = mapped.teams[0]
    assert record.formation == "4-3-3"
    out = _patch(tmp_path, iso, mapped)
    written = _read(out).formations().get(FIRST_CLUB_FORMATION)[:30]
    # A 4-3-3 shape; with a holding player the preset nearest the roles may be the 4-1-2-3.
    assert lines(written) == (4, 3, 3)
    on = _stands_roles(out)
    # Every preset's back four is CB, CB, RB and LB, and the front three the wingers and the
    # striker, whoever stood where the lineup listed him.
    assert on[0] == "GK" and on[1:3] == ["CB", "CB"] and on[3:5] == ["RB", "LB"]
    assert sorted(on[8:]) == ["LW", "RW", "ST"]


def test_the_suggested_order_of_an_older_roster_is_the_order_the_game_gets(tmp_path):
    roster = older_roster()
    suggested = PES2013PS2Patcher(tmp_path).suggest_squad_order(roster)
    assert [p.name for p in suggested[:11]] == [
        "GK1", "RB2", "LB3", "CB5", "CB6", "CDM4", "CM7", "CM8", "RW10", "LW11", "ST9",
    ]  # fmt: skip
    auto = _map(tmp_path, roster).teams[0]
    given = TeamRoster(team=roster.team, players=suggested, extra=dict(roster.extra))
    edited = _map(tmp_path, given, order_as_given=True).teams[0]
    assert [p.player_id for p in auto.players] == [p.player_id for p in edited.players]
    assert auto.formation == edited.formation == "4-3-3"


def test_a_roster_with_a_formation_of_its_own_keeps_it_over_its_lineups_shape(tmp_path):
    roster = older_roster()
    roster.extra["formation"] = "3-5-2"
    assert _map(tmp_path, roster).teams[0].formation == "3-5-2"


def test_a_lineup_shape_that_cannot_be_read_leaves_no_formation(tmp_path):
    roster = older_roster(shape="banana")
    assert _map(tmp_path, roster).teams[0].formation == ""


# --- patch: when nothing is written -------------------------------------------------------


def test_a_roster_with_neither_a_formation_nor_a_lineup_patches_as_today(tmp_path, iso):
    xi = ("GK", "CB", "CB", "RB", "LB", "CM", "CM", "RM", "LM", "ST", "ST")
    roster = _team(xi, formation="", bench=("GK", "CB", "CM", "ST"), lineup=False, roles=False)
    mapped = _map(tmp_path, roster)
    assert mapped.teams[0].formation == ""
    assert all(p.role_name == "" for p in mapped.teams[0].players)
    out = _patch(tmp_path, iso, mapped)
    assert _entry(out, FORMATION_ENTRY) == _entry(iso, FORMATION_ENTRY)  # not a byte of it
    # The eleven stand on the records holding their position, as before.
    assert _stands(out) == [
        "GK0",
        "CB1",
        "CB2",
        "RB3",
        "LB4",
        "CM5",
        "CM6",
        "RM7",
        "LM8",
        "ST9",
        "ST10",
    ]
    # No player's registered position or flags changed: bytes 53-65 of every record are the disc's.
    before, after = _read(iso).player_tables(), _read(out).player_tables()
    assert all(
        before.record(i)[53:66] == after.record(i)[53:66] for i in range(before.player_count)
    )


def test_a_lineup_without_a_shape_writes_no_record_but_the_codes_still_give_the_roles(
    tmp_path, iso
):
    xi = ("GK", "CB", "CB", "RB", "LB", "CM", "CM", "RM", "LM", "ST", "ST")
    roster = _team(xi, formation="", bench=("GK", "CB", "CM", "ST"), lineup=True, roles=False)
    mapped = _map(tmp_path, roster)
    assert mapped.teams[0].formation == ""
    assert [p.role_name for p in mapped.teams[0].players[:11]] == list(xi)
    assert all(p.role_name == "" for p in mapped.teams[0].players[11:])  # the bench has no code
    out = _patch(tmp_path, iso, mapped)
    assert _entry(out, FORMATION_ENTRY) == _entry(iso, FORMATION_ENTRY)  # no formation, no record
    tables = _read(out).player_tables()
    assert [tables.record(i)[53] >> 4 for i in tables.club_squad(0)[:11]] == [
        ROLE_NIBBLE[role] for role in _stands_roles(out)
    ]


def test_an_unreadable_formation_writes_no_record_but_the_roles_still_write_positions(
    tmp_path, iso
):
    out = _patch(tmp_path, iso, _map(tmp_path, _team(formation="banana"), order_as_given=True))
    assert _entry(out, FORMATION_ENTRY) == _entry(iso, FORMATION_ENTRY)
    tables = _read(out).player_tables()
    squad = tables.club_squad(0)[:11]
    assert {_role(_name(tables, i)): tables.record(i)[53] >> 4 for i in squad}["LW"] == 10


def test_a_team_with_no_players_keeps_its_formation(tmp_path, iso):
    roster = _team(xi=(), bench=())
    out = _patch(tmp_path, iso, _map(tmp_path, roster, order_as_given=True))
    assert _entry(out, FORMATION_ENTRY) == _entry(iso, FORMATION_ENTRY)


def test_a_club_slot_without_a_formation_record_keeps_its_formation(tmp_path, iso):
    # Clubs 130 to 138 are in the squad table but not in the formation table (67 + 130 = 197).
    statuses = []
    patcher = PES2013PS2Patcher(tmp_path, on_status=statuses.append)
    mapped = patcher.map_rosters(
        _league(_team()), [SlotMapping(slot_index=130, team_id=7)], order_as_given=True
    )
    out = tmp_path / "out.iso"
    patcher.patch(
        rom_path=iso, output_path=out, rosters=mapped, logos=False, kits=False, looks=False
    )
    assert _entry(out, FORMATION_ENTRY) == _entry(iso, FORMATION_ENTRY)
    assert any("130" in s and "formation" in s for s in statuses)


# --- the disc (RETRO_ROSTER_TEST_ROMS has pes2013.iso) ------------------------------------


@pytest.fixture
def disc():
    """The retail image's path."""
    root = os.environ.get("RETRO_ROSTER_TEST_ROMS")
    if not root:
        pytest.skip("set RETRO_ROSTER_TEST_ROMS to a directory holding pes2013.iso")
    path = Path(root) / "pes2013.iso"
    if not path.exists():
        pytest.fail(f"RETRO_ROSTER_TEST_ROMS is set but {path} is missing")
    return path


@pytest.mark.real_rom
def test_all_197_records_of_the_disc_are_valid_formations(disc):
    table = _read(disc).formations()
    shapes = Counter()
    for team in range(FORMATION_TEAMS):
        record = table.get(team)
        _keeps_the_editors_rules(record, team)
        assert max(record[10:20]) <= 48 and max(record[20:30]) <= 112, team
        shapes[lines(record)] += 1
        assert sum(lines(record)) == 10 and len(place_roles(record)) == 10
    assert shapes[(4, 5, 1)] == 76 and shapes[(4, 4, 2)] == 65 and shapes[(4, 3, 3)] == 42


@pytest.mark.real_rom
def test_the_fixtures_record_is_the_discs_and_the_433_preset_is_what_the_patch_wrote(disc):
    table = _read(disc).formations()
    wmv = table.get(FIRST_CLUB_FORMATION + 1)  # West Midlands Village, club 1
    assert wmv == fixture.CLUB_0_FORMATION
    table.set(FIRST_CLUB_FORMATION + 1, PRESETS["4-3-3"])
    assert table.get(FIRST_CLUB_FORMATION + 1) == AFTER_433 + wmv[30:]


@pytest.mark.real_rom
def test_a_preset_in_every_record_still_fits_the_entrys_room(disc):
    writer = RomWriter(str(disc), str(disc))
    assert writer.reader.load()
    table = writer.reader.formations()
    for team in range(FORMATION_TEAMS):
        table.set(team, PRESETS[EDITOR_ORDER[team % 12]])
    assert len(table.build()) <= writer.room(TEXT_AFS, FORMATION_ENTRY)


@pytest.mark.real_rom
def test_every_registered_position_of_the_disc_has_its_flag_on(disc):
    tables = _read(disc).player_tables()
    counted = 0
    for i in range(1, tables.player_count):
        record = tables.record(i)
        if not record[:32].strip(b"\0"):
            continue  # an unused record
        nibble = record[53] >> 4
        assert record[54 if nibble == 0 else 53 + nibble] & 0x80, (i, nibble)
        counted += 1
    assert counted > 4900
