"""WE2002 writes each club's formation and each player's position code by role.

The disc keeps one 30-byte formation record per team in a 96-record table: the role byte, the x and
the y of places 2-11 (place 1 is the keeper and has no entry). A national slot's record is the
team's default formation, the one its formation screen opens on and its players take the pitch in.
A player's position is the low three bits of the first byte of his characteristics.

`FORMATION_PRESETS` holds the disc's 16 presets (the tests at the end hold it to the disc's own
tables when RETRO_ROSTER_TEST_ROMS has the image). The mapper picks the preset nearest the club's
formation (variant B when the eleven hold two holding midfielders), cuts the eleven into its lines
by index and seats each line on the preset's places by role. The writer puts the preset into the
national slot's record and, with the club's squad (test_master_league.py), into the Master League
club's.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from retro_roster_patcher.core.models import SlotMapping
from retro_roster_patcher.games.we2002 import patcher as patcher_module
from retro_roster_patcher.games.we2002 import rom_writer, stat_mapper
from retro_roster_patcher.games.we2002.models import WEPlayerRecord, WETeamRecord
from retro_roster_patcher.games.we2002.patcher import WE2002Patcher
from retro_roster_patcher.games.we2002.rom_writer import RomWriter, _encode_player_carat
from retro_roster_patcher.games.we2002.stat_mapper import StatMapper
from retro_roster_patcher.sports.lineup import ROLES, apply_lineup, pick_lineup
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster

SHAPES = ("4-5-1", "4-4-2", "4-3-3", "3-6-1", "3-5-2", "3-4-3", "5-4-1", "5-3-2")
PRESET_NAMES = [shape + variant for shape in SHAPES for variant in "AB"]

# Presets as the disc has them (the gated tests below hold `FORMATION_PRESETS` to the disc): the
# role byte, x and y of places 2-11.
FOUR_FOUR_TWO_A = bytes.fromhex(
    "02 06 07 08 0a 0c 0d 0f 11 13  09 09 0b 0b 12 1a 1a 22 2b 2b  29 3f 13 55 34 1d 4b 34 26 42"
)
FOUR_THREE_THREE_A = bytes.fromhex(
    "02 06 07 08 0a 0e 10 12 14 15  09 09 0b 0b 12 1e 1e 2b 2b 2b  29 3f 13 55 34 28 40 34 20 48"
)
FOUR_THREE_THREE_B = bytes.fromhex(
    "02 06 07 08 09 0b 0f 12 14 15  09 09 0b 0b 12 12 1e 2b 2b 2b  29 3f 13 55 2b 3d 34 34 20 48"
)
THREE_FIVE_TWO_A = bytes.fromhex(
    "02 03 06 0a 0c 0d 0e 10 11 13  09 09 09 12 1a 1a 22 22 2b 2b  20 34 48 34 1b 4d 28 40 26 42"
)
# Wales' retail record (national slot 2): a 5-3-2 with the team's own coordinates.
WALES_RETAIL = bytes.fromhex(
    "02 03 06 07 08 0a 0e 10 11 13  0b 0b 0b 0f 0f 14 1f 1f 2c 29  20 34 48 11 57 32 1e 3e 2a 3e"
)

POSITION_BY_ROLE = {
    "GK": 0,
    "CB": 1,
    **dict.fromkeys(("RB", "LB", "RWB", "LWB"), 2),
    **dict.fromkeys(("CDM", "CM"), 3),
    **dict.fromkeys(("RM", "LM"), 4),
    "CAM": 5,
    "ST": 6,
    **dict.fromkeys(("RW", "LW"), 7),
}
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
BENCH = ("GK", "CB", "CM", "ST", "CB", "CM", "ST", "RB", "LB", "CM", "ST", "LW")
# 4-3-3 with one holding midfielder: P01 GK, P02 RB, P03 CB, P04 CB, P05 LB, P06 CDM, P07 CM,
# P08 CM, P09 LW, P10 ST, P11 RW; the bench is P12-P23.
XI_433 = ("GK", "RB", "CB", "CB", "LB", "CDM", "CM", "CM", "LW", "ST", "RW")

FIXTURE = json.loads(
    (Path(__file__).parents[2] / "fixtures" / "lineups_2026-11-02.json").read_text(encoding="utf-8")
)


# --- helpers ------------------------------------------------------------------------------


def _xi(xi=XI_433, formation="", lineup=True, roles=True, bench=BENCH):
    """P01, P02, ... in the roles given (the eleven, then the bench). `lineup`: the eleven are the
    club's recent starters; `roles=False` leaves every player without a role."""
    extra: dict = {"formation": formation} if formation else {}
    if lineup:
        starters = [{"id": i, "position": CODE[r]} for i, r in enumerate(xi[:11], 1)]
        extra["lineup"] = {"formation": formation, "event": 1, "starters": starters}
    players = [
        Player(id=i, name=f"P{i:02d}", position=GROUP[r], role=r if roles else "")
        for i, r in enumerate([*xi, *bench], 1)
    ]
    return TeamRoster(team=Team(id=100, name="Team", code="TEA"), players=players, extra=extra)


def _map(roster, **kwargs):
    return StatMapper().map_team_with_league_context(roster, [roster], **kwargs)


def _on(record):
    """The names of the players on places 1-11."""
    return [p.last_name for p in record.players[:11]]


def _galo():
    """The Atlético-MG of the 2026-11-02 snapshot: the user's eleven (a 4-2-3-1 the lineup folds to
    a 4-3-3) with their roles; the 19 squad players the lineup gives no role are midfielders."""
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


# --- where a team's record lies -----------------------------------------------------------


@pytest.mark.parametrize(
    ("index", "chunks"),
    [
        (0, [(2_303_700, 30)]),
        (31, [(2_304_630, 30)]),
        (32, [(2_304_660, 20), (2_304_984, 10)]),  # the end of sector 979, then sector 980
        (33, [(2_304_994, 30)]),
        (63, [(2_305_894, 30)]),  # Master League slot 0
        (94, [(2_306_824, 30)]),  # Master League slot 31
    ],
)
def test_a_team_record_lies_where_the_disc_keeps_it(index, chunks):
    assert rom_writer._formation_chunks(index) == chunks


def test_the_95_records_lie_in_sector_user_data_and_never_overlap():
    seen: set[int] = set()
    for index in range(95):
        for offset, size in rom_writer._formation_chunks(index):
            inside = offset % 2352 - 24  # a Mode2/2352 sector: 24 bytes of header, 2048 of data
            assert 0 <= inside and inside + size <= 2048, index
            span = set(range(offset, offset + size))
            assert not seen & span, index
            seen |= span
    assert len(seen) == 95 * 30


@pytest.mark.parametrize("index", [95, 96, -1])
def test_the_default_master_league_record_and_anything_past_the_table_is_never_written(index):
    # The 96th record (index 95) is the default Master League team, after two pad bytes.
    with pytest.raises(ValueError):
        rom_writer._formation_chunks(index)


# --- the writer ---------------------------------------------------------------------------


def _image(tmp_path, tag, write):
    """The bytes of a synthetic image (empty, grown by the writes) after `write(writer)`."""
    path = tmp_path / f"{tag}.bin"
    path.write_bytes(b"")
    write(RomWriter(str(path), str(path)))
    return path.read_bytes()


def _nat(nat_index, formation):
    team = WETeamRecord(name="N", short_name="N", formation=formation)
    return lambda writer: writer.write_nat_team(nat_index, team)


def _differs_only_in(a, b, chunks):
    """Whether `a` and `b` are equal everywhere outside the `(offset, size)` pieces."""
    patched = bytearray(a)
    for offset, size in chunks:
        patched[offset : offset + size] = b[offset : offset + size]
    return bytes(patched) == b


def test_slot_two_lands_at_2_303_760_and_nowhere_else(tmp_path):
    plain = _image(tmp_path, "plain", _nat(2, None))
    written = _image(tmp_path, "written", _nat(2, FOUR_THREE_THREE_A))
    assert written[2_303_760:2_303_790] == FOUR_THREE_THREE_A
    assert plain[2_303_760:2_303_790] == bytes(30)
    assert len(plain) == len(written)
    assert _differs_only_in(plain, written, [(2_303_760, 30)])


def test_the_record_that_straddles_a_sector_is_written_in_its_two_pieces(tmp_path):
    record = bytes(range(1, 31))
    plain = _image(tmp_path, "plain", _nat(32, None))
    written = _image(tmp_path, "written", _nat(32, record))
    assert written[2_304_660:2_304_680] == record[:20]
    assert written[2_304_984:2_304_994] == record[20:]
    assert _differs_only_in(plain, written, [(2_304_660, 20), (2_304_984, 10)])


def test_the_last_national_slot_never_reaches_the_default_master_league_record(tmp_path):
    record = bytes(range(1, 31))
    plain = _image(tmp_path, "plain", _nat(62, None))
    written = _image(tmp_path, "written", _nat(62, record))
    assert written[2_305_864:2_305_894] == record
    assert written[2_306_856:2_306_886] == bytes(30)
    assert _differs_only_in(plain, written, [(2_305_864, 30)])


def _club(slot, formation):
    """A Master League club with its 23 players and, when given, its formation."""
    team = WETeamRecord(name="N", short_name="N", formation=formation)
    squad = [WEPlayerRecord("A", "B", 2, 9)] * 23
    return lambda writer: writer.write_team(slot, team, players=squad)


@pytest.mark.parametrize("slot", [0, 2, 31])
def test_a_master_league_clubs_record_lies_at_63_plus_its_slot_and_nowhere_else(tmp_path, slot):
    # Record 63 + slot is the club's (31 reaches 94, never the default team's 95). Without a
    # formation the club keeps the disc's record.
    plain = _image(tmp_path, "plain", _club(slot, None))
    written = _image(tmp_path, "written", _club(slot, FOUR_THREE_THREE_A))
    chunks = rom_writer._formation_chunks(63 + slot)
    assert b"".join(written[o : o + n] for o, n in chunks) == FOUR_THREE_THREE_A
    assert b"".join(plain[o : o + n] for o, n in chunks) == bytes(30)
    assert len(plain) == len(written)
    assert _differs_only_in(plain, written, chunks)


def test_a_master_league_call_without_players_writes_no_formation(tmp_path):
    # The blank path: a club nobody patched keeps the disc's record along with its links.
    team = WETeamRecord(name="N", short_name="N", formation=FOUR_THREE_THREE_A)
    plain = WETeamRecord(name="N", short_name="N")
    with_formation = _image(tmp_path, "with", lambda writer: writer.write_team(2, team))
    without = _image(tmp_path, "without", lambda writer: writer.write_team(2, plain))
    assert with_formation == without


@pytest.mark.parametrize("size", [0, 29, 31])
def test_a_record_that_is_not_30_bytes_is_refused_before_the_image_is_touched(tmp_path, size):
    path = tmp_path / "synthetic.bin"
    path.write_bytes(b"")
    writer = RomWriter(str(path), str(path))
    team = WETeamRecord(name="N", short_name="N", formation=bytes(size))
    with pytest.raises(ValueError):
        writer.write_nat_team(2, team)
    with pytest.raises(ValueError):
        writer.write_team(2, team, players=[WEPlayerRecord("A", "B", 2, 9)] * 23)
    assert path.stat().st_size == 0


# --- a player's position code -------------------------------------------------------------


def test_the_role_table_covers_the_fourteen_roles():
    assert set(rom_writer._POSITION_BY_ROLE) == set(ROLES)
    assert rom_writer._POSITION_BY_ROLE == POSITION_BY_ROLE


@pytest.mark.parametrize(("role", "code"), POSITION_BY_ROLE.items())
def test_a_role_sets_bits_0_to_2_of_the_first_carat_byte_and_nothing_else(role, code):
    plain = _encode_player_carat(WEPlayerRecord("A", "B", 2, 9))
    carat = _encode_player_carat(WEPlayerRecord("A", "B", 2, 9, role=role))
    assert carat[0] & 7 == code
    assert (carat[0] >> 3, carat[1:]) == (plain[0] >> 3, plain[1:])


@pytest.mark.parametrize(("group", "code"), [(0, 0), (1, 1), (2, 3), (3, 6)])
def test_without_a_role_the_position_is_the_groups_as_before(group, code):
    assert _encode_player_carat(WEPlayerRecord("A", "B", group, 9))[0] & 7 == code


def test_a_role_the_table_does_not_know_falls_back_to_the_group():
    assert _encode_player_carat(WEPlayerRecord("A", "B", 3, 9, role="WIZARD"))[0] & 7 == 6


# --- the presets --------------------------------------------------------------------------


def test_the_presets_are_the_sixteen_the_disc_names_in_its_order():
    assert list(stat_mapper.FORMATION_PRESETS) == PRESET_NAMES
    assert {len(record) for record in stat_mapper.FORMATION_PRESETS.values()} == {30}
    assert stat_mapper.FORMATIONS == SHAPES


@pytest.mark.parametrize("name", PRESET_NAMES)
def test_a_preset_has_the_lines_its_name_says_in_order_and_its_variants_holding_midfielders(name):
    roles, xs, ys = (stat_mapper.FORMATION_PRESETS[name][i : i + 10] for i in (0, 10, 20))
    lines = [0 if r <= 8 else 1 if r <= 16 else 2 for r in roles]  # CB SB | DH SH OH | CF WG
    assert lines == sorted(lines)
    assert "-".join(str(lines.count(line)) for line in range(3)) == name[:-1]
    assert sum(9 <= r <= 11 for r in roles) == (2 if name.endswith("B") else 1)
    assert min(roles) >= 2 and max(roles) <= 21 and max(xs) <= 48 and max(ys) <= 112


# --- the mapper: the shape, the variant and the seats -------------------------------------


def test_the_galo_plays_four_three_three_b_with_each_role_on_the_place_that_asks_for_it():
    roster, name = _galo()
    record = _map(roster)
    assert roster.extra["formation"] == "4-3-3"
    assert record.formation == FOUR_THREE_THREE_B
    on = [name[p.player_id] for p in record.players[:11]]
    assert on[0] == "Éverson"
    assert sorted(on[1:3]) == ["Ruan", "Vitor Hugo"]  # the two CB places
    assert on[3:5] == ["Renan Lodi", "Natanael"]  # LB, RB
    assert sorted(on[5:7]) == ["Alan Franco", "Igor Gomes"]  # the two DH places
    assert on[7:] == ["Fred", "Thiago Borbas", "Bernard", "Tomás Cuello"]  # OH, CF, LW, RW


def test_a_four_three_three_with_one_holding_midfielder_is_the_a_preset_with_the_wingers_last():
    record = _map(_xi(formation="4-3-3"))
    assert record.formation == FOUR_THREE_THREE_A
    on = _on(record)
    assert on[0] == "P01"
    assert on[3:5] == ["P05", "P02"]  # LB, RB
    assert on[5] == "P06"  # the CDM on the DH place
    assert sorted(on[6:8]) == ["P07", "P08"]  # the two CMs on the OH places
    assert on[8:] == ["P10", "P09", "P11"]  # CF, then LW and RW on places 10 and 11


def test_a_flat_four_four_two_is_four_four_two_a():
    xi = ("GK", "RB", "CB", "CB", "LB", "RM", "CM", "CM", "LM", "ST", "ST")
    record = _map(_xi(xi, "4-4-2"))
    assert record.formation == FOUR_FOUR_TWO_A
    on = _on(record)
    assert on[6:8] == ["P09", "P06"]  # the SH places: LM on the left, RM on the right
    assert sorted(on[5:6] + on[8:9]) == ["P07", "P08"]  # the CMs: the DH and the OH places
    assert sorted(on[9:]) == ["P10", "P11"]


def test_a_three_five_two_is_three_five_two_a_with_the_wing_backs_wide_and_the_pivot_deep():
    xi = ("GK", "CB", "CB", "CB", "LWB", "CDM", "CM", "CM", "RWB", "ST", "ST")
    record = _map(_xi(xi, "3-5-2"))
    assert record.formation == THREE_FIVE_TWO_A
    on = _on(record)
    assert sorted(on[1:4]) == ["P02", "P03", "P04"]
    assert on[4:7] == ["P06", "P05", "P09"]  # DH: the CDM; SH left and right: LWB, RWB
    assert sorted(on[7:9]) == ["P07", "P08"]  # OH, OH
    assert sorted(on[9:]) == ["P10", "P11"]


def test_four_two_three_one_reads_as_four_three_three_and_two_holding_midfielders_make_it_b():
    xi = ("GK", "RB", "CB", "CB", "LB", "CDM", "CDM", "RW", "CAM", "LW", "ST")
    record = _map(_xi(xi, "4-2-3-1"))
    assert record.formation == FOUR_THREE_THREE_B
    on = _on(record)
    assert sorted(on[5:7]) == ["P06", "P07"] and on[7] == "P09"
    assert on[8:] == ["P11", "P10", "P08"]  # CF, then LW and RW


def test_three_holding_midfielders_still_make_the_b_preset():
    xi = ("GK", "CB", "CB", "CB", "CB", "CDM", "CDM", "CDM", "LW", "ST", "RW")
    assert _map(_xi(xi, "4-3-3")).formation == FOUR_THREE_THREE_B


def test_the_nearest_shape_the_disc_has_stands_in_for_one_it_lacks():
    xi = ("GK", "RB", "CB", "CB", "LB", "CM", "CM", "LW", "ST", "ST", "RW")
    assert _map(_xi(xi, "4-2-4")).formation == FOUR_THREE_THREE_A


def test_a_formation_written_with_spaces_around_it_reads():
    assert _map(_xi(formation=" 4-3-3 ")).formation == FOUR_THREE_THREE_A


@pytest.mark.parametrize("formation", ["", "attack", "4-4-1", "4-4", " "])
def test_a_formation_the_game_cannot_read_leaves_the_order_and_writes_no_record(formation):
    roster = _xi()
    if formation:
        roster.extra["formation"] = formation
    record = _map(roster)
    assert record.formation is None
    assert [p.player_id for p in record.players] == [
        p.id for p in StatMapper()._select_best_23(roster.players, {}, roster.extra["lineup"])
    ]


@pytest.mark.parametrize(
    ("y", "side"),
    [(0, "L"), (44, "L"), (45, "C"), (52, "C"), (59, "C"), (60, "R"), (112, "R")],
)
def test_a_place_is_on_the_left_below_y_45_and_on_the_right_above_59(y, side):
    assert stat_mapper._side(y) == side


def test_midfield_and_attack_are_seated_by_role_whatever_order_they_are_given_in():
    # Listed as the user dragged them: the back four in order, then CM CDM CM, then RW ST LW.
    roster = _xi(formation="4-3-3")
    roster.players = [roster.players[i] for i in (0, 2, 3, 4, 1, 6, 5, 7, 10, 9, 8, *range(11, 23))]
    on = _on(_map(roster, order_as_given=True))
    assert on[:5] == ["P01", "P03", "P04", "P05", "P02"]
    assert on[5:] == ["P06", "P07", "P08", "P10", "P09", "P11"]  # DH, OH, OH, CF, LW, RW


# --- the eleven: one keeper, first --------------------------------------------------------


def _keepers(record):
    return [i for i, p in enumerate(record.players[:11]) if p.position == 0]


def test_exactly_one_keeper_starts_and_he_is_first_in_the_suggested_eleven():
    roster = _xi(formation="4-3-3")
    roster.extra["lineup"]["starters"][1] = {"id": 12, "position": "G"}  # the bench keeper starts
    record = _map(roster)
    assert _keepers(record) == [0]
    assert record.players[0].player_id == 1


def test_exactly_one_keeper_starts_and_he_is_first_in_a_given_order():
    # The first keeper listed is in goal, wherever the list puts him; the second sits.
    roles = ("ST", "CB", "GK", "CB", "LB", "RB", "GK", "CM", "CM", "CDM", "LW", "RW")
    roster = _xi(roles, "4-3-3", lineup=False, bench=("CB", "CM", "ST", "ST"))
    record = _map(roster, order_as_given=True)
    assert _keepers(record) == [0]
    assert record.players[0].player_id == 3
    assert 7 in [p.player_id for p in record.players[11:]]  # the second keeper is on the bench
    assert len(record.players) == 16


def test_a_squad_without_a_keeper_puts_its_first_player_in_goal():
    xi = ("CB", "CB", "LB", "RB", "CM", "CM", "CM", "ST", "ST", "LW", "RW")
    record = _map(_xi(xi, "4-3-3", lineup=False, bench=()), order_as_given=True)
    assert record.players[0].player_id == 1
    assert len(record.players) == 11


# --- an edited order ----------------------------------------------------------------------

# The user moved P10 (ST) and P07 (CM) to the top of the outfield and the winger P08 down.
EDITED = [0, 9, 6, 2, 3, 5, 4, 1, 7, 8, 10, *range(11, 23)]


def test_an_edited_order_keeps_its_lines_by_index_and_seats_inside_them():
    roster = _xi(formation="4-3-3")
    roster.players = [roster.players[i] for i in EDITED]
    record = _map(roster, order_as_given=True)
    on = _on(record)
    # Outfield index 0-3 is the back line, 4-6 midfield, 7-9 attack, whoever stands there.
    assert sorted(on[1:5]) == ["P03", "P04", "P07", "P10"]
    assert sorted(on[5:8]) == ["P02", "P05", "P06"]
    assert sorted(on[8:]) == ["P08", "P09", "P11"]
    assert record.formation == FOUR_THREE_THREE_A  # one CDM


def test_changing_the_formation_keeps_the_order_and_cuts_its_lines_again():
    roster = _xi(formation="3-5-2")
    roster.players = [roster.players[i] for i in EDITED]
    record = _map(roster, order_as_given=True)
    on = _on(record)
    assert record.formation == THREE_FIVE_TWO_A
    assert sorted(on[1:4]) == ["P03", "P07", "P10"]
    assert sorted(on[4:9]) == ["P02", "P04", "P05", "P06", "P08"]
    assert sorted(on[9:]) == ["P09", "P11"]


def test_the_variant_counts_the_holding_midfielders_among_the_eleven_the_user_chose():
    roster = _xi(formation="4-3-3")
    roster.players[13].role = "CDM"  # a bench CM: not among the eleven
    assert _map(roster, order_as_given=True).formation == FOUR_THREE_THREE_A
    roster.players[6].role = "CDM"  # P07, one of the eleven
    assert _map(roster, order_as_given=True).formation == FOUR_THREE_THREE_B


def test_the_bench_follows_the_eleven_in_the_order_given():
    roster = _xi(formation="4-3-3")
    roster.players = roster.players[:11] + roster.players[11:][::-1]
    record = _map(roster, order_as_given=True)
    assert [p.player_id for p in record.players[11:]] == list(range(23, 11, -1))


def test_the_suggested_order_is_the_order_the_game_gets(tmp_path):
    roster, _ = _galo()
    patcher = WE2002Patcher(cache_dir=tmp_path / "cache")
    data = LeagueData(league=League(id=1, name="L", country="C", season=2026), teams=[roster])
    mapping = [SlotMapping(slot_index=0, team_id=roster.team.id)]
    automatic = patcher.map_rosters(data, mapping).teams[0]
    roster.players = patcher.suggest_squad_order(roster)
    given = patcher.map_rosters(data, mapping, order_as_given=True).teams[0]
    assert [p.player_id for p in given.players] == [p.player_id for p in automatic.players]
    assert given.formation == automatic.formation == FOUR_THREE_THREE_B


# --- the records the mapper hands the writer ----------------------------------------------


def test_each_record_carries_its_players_role():
    record = _map(_xi(formation="4-3-3"))
    assert [p.role for p in record.players[:11]] == [
        "GK", "CB", "CB", "LB", "RB", "CDM", "CM", "CM", "ST", "LW", "RW",
    ]  # fmt: skip


def test_a_roster_with_roles_but_no_formation_keeps_its_order_and_takes_codes_by_role():
    xi = ("GK", "RB", "CB", "CB", "LB", "RM", "CM", "CM", "LM", "ST", "ST")
    roster = _xi(xi)
    record = _map(roster)
    assert record.formation is None
    assert [p.player_id for p in record.players] == [
        p.id for p in StatMapper()._select_best_23(roster.players, {}, roster.extra["lineup"])
    ]
    role_of = {p.id: p.role for p in roster.players}
    assert [_encode_player_carat(p)[0] & 7 for p in record.players] == [
        POSITION_BY_ROLE[role_of[p.player_id]] for p in record.players
    ]


def test_a_roster_with_a_formation_but_no_roles_reads_each_player_as_his_group():
    xi = ("GK", "RB", "CB", "CB", "LB", "CM", "CM", "CM", "CM", "ST", "ST")
    record = _map(_xi(xi, "4-4-2", roles=False))
    assert record.formation == FOUR_FOUR_TWO_A
    assert {p.role for p in record.players} == {""}
    codes = [_encode_player_carat(p)[0] & 7 for p in record.players[:11]]
    assert codes == [0, 1, 1, 1, 1, 3, 3, 3, 3, 6, 6]


# --- an older roster ----------------------------------------------------------------------

#: What the mapper gave this roster before formations and roles existed.
OLDER_SQUAD = [2, 10, 9, 8, 11, 19, 18, 17, 24, 22, 23, 0, 3, 12, 20, 1, 4, 13, 21, 5, 25, 26, 6]


def _older_roster():
    groups = ["Goalkeeper"] * 3 + ["Defender"] * 9 + ["Midfielder"] * 8 + ["Attacker"] * 7
    starters = [
        (2, "G"), (11, "RB"), (10, "CD"), (9, "CD"), (8, "LB"), (19, "DM"),
        (18, "CM"), (17, "CM"), (24, "LW"), (23, "CF"), (22, "RW"),
    ]  # fmt: skip
    lineup = {"formation": "4-3-3", "event": 1}
    lineup["starters"] = [{"id": i, "position": c} for i, c in starters]
    return TeamRoster(
        team=Team(id=100, name="Team", code="TEA"),
        players=[Player(id=i, name=f"P{i:02d}", position=g) for i, g in enumerate(groups)],
        extra={"lineup": lineup},
    )


def test_an_older_roster_gives_the_same_23_and_no_formation_bytes():
    # Fetched before roles and formations existed: neither a role nor extra["formation"].
    record = _map(_older_roster())
    assert [p.player_id for p in record.players] == OLDER_SQUAD
    assert record.formation is None
    assert {p.role for p in record.players} == {""}


def test_an_older_roster_keeps_the_codes_of_its_groups():
    record = _map(_older_roster())
    assert [_encode_player_carat(p)[0] & 7 for p in record.players] == [
        {0: 0, 1: 1, 2: 3, 3: 6}[p.position] for p in record.players
    ]


def test_an_older_roster_in_the_callers_order_is_still_the_first_23_as_given():
    record = _map(_older_roster(), order_as_given=True)
    assert [p.player_id for p in record.players] == list(range(23))
    assert record.formation is None


# --- the patcher --------------------------------------------------------------------------


def test_the_patcher_offers_the_eight_shapes_the_disc_has():
    assert WE2002Patcher.writes_formation is True
    assert WE2002Patcher.formations == SHAPES


def test_patch_writes_the_formation_into_both_records_and_points_the_club_at_the_squad(
    tmp_path, monkeypatch
):
    # A Master League slot (2) is written to both tables: its national slot holds the squad and
    # the record, and the club's 23 links and record (63 + 2) follow.
    monkeypatch.setattr(patcher_module, "apply_ppf", lambda *args, **kwargs: "")
    rom = tmp_path / "in.bin"
    with rom.open("wb") as handle:
        handle.write(b"\x00" + b"\xff" * 10 + b"\x00")  # a raw sector's sync pattern
        handle.truncate(100 * 1024 * 1024)
    patcher = WE2002Patcher(cache_dir=tmp_path / "cache")
    roster, _ = _galo()
    data = LeagueData(league=League(id=1, name="L", country="C", season=2026), teams=[roster])
    mapped = patcher.map_rosters(data, [SlotMapping(slot_index=2, team_id=roster.team.id)])
    out = tmp_path / "out.bin"

    patcher.patch(rom_path=rom, output_path=out, rosters=mapped, kits=False, logos=False)

    image = out.read_bytes()
    assert image[2_303_760:2_303_790] == FOUR_THREE_THREE_B
    assert image[2_305_954:2_305_984] == FOUR_THREE_THREE_B  # record 63 + 2
    links = b"".join(image[o : o + n] for o, n in rom_writer._link_chunks(2))
    assert links == bytes(b for place in range(23) for b in (2, place))
    squad = mapped.teams[2].players
    fielded = [
        b"".join(image[o : o + n] for o, n in rom_writer._nat_nome_chunks(2 * 23 + place))
        for place in range(len(squad))
    ]
    assert fielded == [rom_writer._encode_player_name(p.last_name) for p in squad]


# --- the disc (the `disc` fixture: RETRO_ROSTER_TEST_ROMS has we2002.bin, Track 1) -----------


@pytest.mark.real_rom
def test_the_presets_are_the_discs_own_tables(disc):
    # Names: 16 slots of 8 bytes (6 characters) at 4_822_152, preset 15 first. Roles: 11 bytes
    # each (the keeper's 1 first) at 374_188. X then y: 20 bytes each at 374_780.
    on_disc = {}
    for k in range(16):
        name = disc(4_822_152 + 8 * (15 - k), 6).decode("ascii")
        roles = disc(374_188 + 11 * k, 11)
        assert roles[0] == 1, name
        on_disc[name] = roles[1:] + disc(374_780 + 20 * k, 20)
    assert list(on_disc.items()) == list(stat_mapper.FORMATION_PRESETS.items())


@pytest.mark.real_rom
def test_all_95_records_the_writer_can_address_are_the_discs_teams(disc):
    for index in range(95):
        record = b"".join(disc(o, n) for o, n in rom_writer._formation_chunks(index))
        roles, xs, ys = record[:10], record[10:20], record[20:]
        assert all(2 <= r <= 21 for r in roles), index
        assert max(xs) <= 48 and max(ys) <= 112, index


@pytest.mark.real_rom
def test_wales_retail_record_is_where_the_writer_puts_slot_two(disc):
    assert disc(2_303_760, 30) == WALES_RETAIL
