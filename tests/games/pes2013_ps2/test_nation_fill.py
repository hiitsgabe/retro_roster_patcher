"""PES 2013 (PS2): the ranked national fill.

The fill takes national rows 0-59 (the Classic rows 60-66 stay). A nation the disc has a row
for stays in it; any other goes into a free row with its own name and flag, which the disc
keeps for 144 nationalities: every language overlay lists them (name and code pointers), and a
national row's flag is its nationality, the third word of its team-table entry (both checked
in PCSX2). So a moved nation is a row pointed at its nationality's strings, with its
nationality in that word; nothing is drawn or written in place.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from retro_roster_patcher.core.models import SlotMapping
from retro_roster_patcher.games.pes2013_ps2 import kits as pes_kits
from retro_roster_patcher.games.pes2013_ps2 import patcher as pes_patcher
from retro_roster_patcher.games.pes2013_ps2.models import (
    DATABASE_OVERLAY_ENGLISH,
    NATIONAL_SLOT_BASE,
    PLAYER_ENTRIES,
    PESPlayer,
)
from retro_roster_patcher.games.pes2013_ps2.patcher import PES2013PS2Patcher, cut_squad
from retro_roster_patcher.games.pes2013_ps2.rom_reader import RomReader
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster
from tests.fixtures import synthetic_pes2013_iso as fixture

OVERLAYS = (DATABASE_OVERLAY_ENGLISH, 5)


@pytest.fixture
def iso(tmp_path):
    path = tmp_path / "pes.iso"
    fixture.write_iso(path)
    return path


def _nation(team_id: int, name: str, code: str, players: int = 2) -> TeamRoster:
    groups = ("Goalkeeper", "Defender", "Midfielder", "Forward")
    return TeamRoster(
        team=Team(id=team_id, name=name, code=code, color="C1272D", alternate_color="006233"),
        players=[
            Player(
                id=team_id * 100 + k,
                name=f"{name} Player{k}",
                first_name=name,
                last_name=f"Player{k}",
                position=groups[min(k, 3)],
            )
            for k in range(players)
        ],
    )


MOVED = _nation(470, "Nationality 70", "X70")
OWN = _nation(405, "Nation 5", "N05")
NATIONS = LeagueData(
    league=League(id=2021, name="International Friendlies", season=2026),
    teams=[
        MOVED,
        OWN,
        _nation(400, "Nation 0", "N00"),
        _nation(406, "Nation 6", "N06"),
        _nation(499, "Atlantis", "ATL"),
        # The disc's placeholder 101 carries this code: no flag of its own.
        _nation(498, "Solomon Islands", "SOL"),
    ],
)


def _patch(tmp_path, iso, mapping, out_name="out.iso", **options):
    statuses: list[str] = []
    patcher = PES2013PS2Patcher(tmp_path, on_status=statuses.append)
    rosters = patcher.map_rosters(
        NATIONS, [SlotMapping(slot_index=NATIONAL_SLOT_BASE + r, team_id=t) for r, t in mapping]
    )
    out = tmp_path / out_name
    patcher.patch(rom_path=iso, output_path=out, rosters=rosters, logos=False, **options)
    return out, statuses


def _reader(path) -> RomReader:
    reader = RomReader(str(path))
    assert reader.load()
    return reader


def _name(tables, i):
    return tables.record(i)[:32].decode("utf-16-le").split("\0")[0]


def _row(reader, overlay, row):
    teams = reader.overlay_teams(overlay)
    name, code = teams.nationals[row]
    return name.text, code.text, teams.idents[row]


def test_the_fill_takes_the_60_nations_rows_and_never_a_classic_one(iso, tmp_path):
    patcher = PES2013PS2Patcher(tmp_path)
    assert patcher.supports_nation_fill
    assert patcher.fill_slots(iso) == list(range(NATIONAL_SLOT_BASE, NATIONAL_SLOT_BASE + 60))
    assert patcher.home_only_slots(iso) == []


def test_a_nation_finds_its_own_row_by_name_then_code(iso, tmp_path):
    patcher = PES2013PS2Patcher(tmp_path)
    assert patcher.own_national_slot(iso, Team(id=1, name="Nation 3", code="")) == 1003
    assert patcher.own_national_slot(iso, Team(id=1, name="Elsewhere", code="N07")) == 1007
    # On the disc with a flag but no row of its own: it moves.
    assert patcher.own_national_slot(iso, MOVED.team) is None
    assert patcher.own_national_slot(iso, Team(id=1, name="Atlantis", code="ATL")) is None


def test_the_reader_lists_the_nationalities_and_each_rows_nationality(iso):
    teams = _reader(iso).overlay_teams(5)
    assert len(teams.nationalities) == 144
    assert [s.text for s in teams.nationalities[70]] == ["NACIONALIDADE 70", "X70"]
    assert teams.idents[0] == 0 and teams.idents[59] == 59


def test_a_moved_nation_wears_its_own_name_and_flag_in_every_language(iso, tmp_path):
    out, _ = _patch(tmp_path, iso, [(5, MOVED.team.id)])
    reader, before = _reader(out), _reader(iso)
    assert _row(reader, DATABASE_OVERLAY_ENGLISH, 5) == ("NATIONALITY 70", "X70", 70)
    assert _row(reader, 5, 5) == ("NACIONALIDADE 70", "X70", 70)
    for overlay in OVERLAYS:
        teams, old = reader.overlay_teams(overlay), before.overlay_teams(overlay)
        assert [s.text for n in teams.nationalities for s in n] == [
            s.text for n in old.nationalities for s in n
        ]
        assert _row(reader, overlay, 4) == _row(before, overlay, 4)
    # The Classic and every other row's strings are where they were: nothing written in place.
    assert (
        reader.read("OVER.AFS", 18)[: 0x100 + 67 * 24]
        == before.read("OVER.AFS", 18)[: 0x100 + 67 * 24]
    )


def test_a_nation_on_its_own_row_keeps_the_discs_bytes(iso, tmp_path):
    out, _ = _patch(tmp_path, iso, [(5, OWN.team.id)], kits=False)
    reader, before = _reader(out), _reader(iso)
    for overlay in OVERLAYS:
        assert reader.read("OVER.AFS", overlay) == before.read("OVER.AFS", overlay)


def test_a_row_given_back_to_its_own_nation_shows_it_again(iso, tmp_path):
    moved, _ = _patch(tmp_path, iso, [(5, MOVED.team.id)], "moved.iso")
    out, _ = _patch(tmp_path, moved, [(5, OWN.team.id)])
    for overlay in OVERLAYS:
        assert _row(_reader(out), overlay, 5) == ("NATION 5", "N05", 5)


def test_a_team_without_a_flag_on_the_disc_keeps_the_rows_name_and_flag(iso, tmp_path):
    """As before the fill, for a hand mapping; the fill reaches no such nation on the real
    disc (`test_on_the_disc_the_top_nations_find_their_rows`)."""
    out, statuses = _patch(tmp_path, iso, [(5, 499), (6, 498)])
    reader, before = _reader(out), _reader(iso)
    for row in (5, 6):
        assert _row(reader, DATABASE_OVERLAY_ENGLISH, row) == _row(
            before, DATABASE_OVERLAY_ENGLISH, row
        )
    for name in ("ATLANTIS", "SOLOMON ISLANDS"):
        assert any(name in s and "flag" in s for s in statuses), statuses


def test_a_moved_nation_fields_none_of_the_rows_old_players(iso, tmp_path):
    """Row 0 holds records 1-3 (club 0's, untouched here) and 34-36: the moved nation takes
    two of its own, and neither club 0's players nor the third stays in its squad."""
    out, _ = _patch(tmp_path, iso, [(0, MOVED.team.id)])
    tables = _reader(out).player_tables(PLAYER_ENTRIES[0])
    assert tables.national_squad(0) == [34, 35] + [0] * 21
    assert tables.club_squad(0)[:3] == [1, 2, 3]


def test_a_moved_nation_brings_its_kit_with_kits_off_and_a_home_one_gets_the_discs(
    iso, tmp_path, monkeypatch
):
    reader = _reader(iso)
    disc_kits = reader.kits().nationals[0]
    retail = [pes_kits.Kit.parse(bytes([7]) * pes_kits.KIT_SIZE) for _ in range(4)]
    monkeypatch.setattr(pes_patcher, "retail_national", lambda row: retail)
    # Nation 0 at home, a moved nation in no row with a kit record: the home kit is the disc's.
    out, _ = _patch(tmp_path, iso, [(0, 400)], kits=False)
    assert [k.build() for k in _reader(out).kits().nationals[0]] == [k.build() for k in retail]
    monkeypatch.setattr(pes_patcher, "retail_national", lambda row: disc_kits)
    out, _ = _patch(tmp_path, iso, [(0, MOVED.team.id)], "moved.iso", kits=False)
    moved = _reader(out).kits().nationals[0]
    assert all(k.parametric for k in (moved[1], moved[3]))
    assert [k.build() for k in moved] != [k.build() for k in disc_kits]


def test_a_moved_nation_brings_keeper_kits_of_its_own_and_neutral_kits_without_colours(
    iso, tmp_path
):
    """The fixture's national row 0 has parametric kits, its keepers' lime: a moved nation
    gets keeper kits in other colours (the keeper model kept), and one ESPN gives no colours
    plain white and black kits, never the row owner's."""
    before = _reader(iso).kits().nationals[0]
    out, _ = _patch(tmp_path, iso, [(0, MOVED.team.id)], kits=False)
    kits = _reader(out).kits().nationals[0]
    for k in (pes_kits.GK_HOME, pes_kits.GK_AWAY):
        assert kits[k].rgb(pes_kits.SHIRT) != before[k].rgb(pes_kits.SHIRT)
        assert kits[k].params == before[k].params
    assert kits[pes_kits.GK_HOME].rgb(pes_kits.SHIRT) != kits[pes_kits.GK_AWAY].rgb(pes_kits.SHIRT)
    bare = _nation(471, "Nationality 71", "X71")
    bare.team.color = bare.team.alternate_color = ""
    statuses: list[str] = []
    patcher = PES2013PS2Patcher(tmp_path, on_status=statuses.append)
    data = LeagueData(league=NATIONS.league, teams=[bare])
    rosters = patcher.map_rosters(data, [SlotMapping(slot_index=NATIONAL_SLOT_BASE, team_id=471)])
    out = tmp_path / "bare.iso"
    patcher.patch(rom_path=iso, output_path=out, rosters=rosters, logos=False, kits=False)
    kits = _reader(out).kits().nationals[0]
    assert kits[pes_kits.HOME].rgb(pes_kits.SHIRT) == (255, 255, 255)
    assert kits[pes_kits.AWAY].rgb(pes_kits.SHIRT) == (0, 0, 0)


def test_the_fill_is_not_offered_on_a_disc_without_a_nationality_table(tmp_path):
    iso = tmp_path / "bare.iso"
    overlays = {
        DATABASE_OVERLAY_ENGLISH: fixture.build_overlay("u", nationalities=False),
        5: fixture.build_overlay("b", nationalities=False),
    }
    fixture.write_iso(iso, overlays=overlays)
    statuses: list[str] = []
    patcher = PES2013PS2Patcher(tmp_path, on_status=statuses.append)
    assert patcher.fill_slots(iso) == []
    assert any("nationality table" in s for s in statuses), statuses


def test_blanking_a_moved_row_leaves_the_nationality_names_alone(iso, tmp_path):
    moved, _ = _patch(tmp_path, iso, [(5, MOVED.team.id)], "moved.iso")
    out, _ = _patch(tmp_path, moved, [(0, 400)], blank_unused=True)
    reader = _reader(out)
    for overlay in OVERLAYS:
        teams = reader.overlay_teams(overlay)
        assert teams.nationals[5][0].text == "" and teams.nationals[5][1].text == ""
        assert teams.nationalities[70][1].text == "X70"
        assert teams.nationalities[70][0].text.endswith(" 70")


def test_the_same_fill_twice_writes_the_same_image(iso, tmp_path):
    once, _ = _patch(tmp_path, iso, [(5, MOVED.team.id), (6, 406)], "once.iso")
    twice, _ = _patch(tmp_path, once, [(5, MOVED.team.id), (6, 406)], "twice.iso")
    assert once.read_bytes() == twice.read_bytes()


# -- the disc (RETRO_ROSTER_TEST_ROMS has pes2013.iso) ------------------------------------


@pytest.fixture
def disc():
    root = os.environ.get("RETRO_ROSTER_TEST_ROMS")
    if not root:
        pytest.skip("set RETRO_ROSTER_TEST_ROMS to a directory holding pes2013.iso")
    path = Path(root) / "pes2013.iso"
    if not path.exists():
        pytest.fail(f"RETRO_ROSTER_TEST_ROMS is set but {path} is missing")
    return path


@pytest.mark.real_rom
def test_every_overlay_of_the_disc_lists_the_144_nationalities_rows_0_59_first(disc):
    reader = _reader(disc)
    for overlay in range(5, 20):
        teams = reader.overlay_teams(overlay)
        assert len(teams.nationalities) == 144, overlay
        for row in range(60):
            name, code = teams.nationals[row]
            assert (name.text, code.text) == tuple(s.text for s in teams.nationalities[row])
            assert teams.idents[row] == row
        # The Classic rows wear their nation's flag.
        assert [teams.idents[r] for r in range(60, 67)] == [6, 8, 9, 14, 15, 44, 45]
    english = reader.overlay_teams(DATABASE_OVERLAY_ENGLISH).nationalities
    assert [s.text for s in english[128]] == ["MOROCCO", "MAR"]
    assert [s.text for s in english[101]] == ["0", "FRE"]


@pytest.mark.real_rom
def test_the_packaged_national_kits_are_the_discs(disc):
    nationals = _reader(disc).kits().nationals
    for row in range(60):
        assert [k.build() for k in pes_kits.retail_national(row)] == [
            k.build() for k in nationals[row]
        ], row


@pytest.mark.real_rom
def test_on_the_disc_the_top_nations_find_their_rows(disc, tmp_path):
    patcher = PES2013PS2Patcher(tmp_path)
    found = {
        name: patcher.own_national_slot(disc, Team(id=1, name=name, code=code))
        for name, code in [
            ("England", "ENG"),
            ("France", "FRA"),
            ("Brazil", "BRA"),
            ("Ivory Coast", "CIV"),
            ("Czechia", "CZE"),
            ("United Arab Emirates", "UAE"),
            ("Morocco", "MAR"),
            ("Solomon Islands", "SOL"),
        ]
    }
    assert found == {
        "England": 1006,
        "France": 1008,
        "Brazil": 1045,
        "Ivory Coast": 1034,
        "Czechia": 1004,
        "United Arab Emirates": 1058,
        "Morocco": None,
        "Solomon Islands": None,
    }


def _p(name: str, group: str) -> PESPlayer:
    return PESPlayer(display_name=name, shirt_name=name, position=group)


@pytest.mark.parametrize(
    "bench",
    [
        # As ESPN lists England, France and Brazil: keepers, then defenders, midfielders, forwards.
        "GK GK DF DF DF DF DF MF MF MF FW FW",
        "GK GK DF DF DF MF MF MF MF FW",
        "GK GK DF DF DF DF MF MF FW FW FW",
    ],
)
def test_a_squad_cut_to_17_keeps_one_spare_keeper_and_a_bench_of_every_line(bench):
    groups = {"GK": "Goalkeeper", "DF": "Defender", "MF": "Midfielder", "FW": "Attacker"}
    eleven = [_p("XI GK", "Goalkeeper")] + [_p(f"XI {k}", "Defender") for k in range(10)]
    rest = [_p(f"{code} {k}", groups[code]) for k, code in enumerate(bench.split())]
    cut = cut_squad(eleven + rest, 17)
    assert cut[:11] == eleven and len(cut) == 17
    on_bench = [p.position for p in cut[11:]]
    assert on_bench.count("Goalkeeper") == 1 and on_bench[0] == "Goalkeeper"
    assert {"Defender", "Midfielder", "Attacker"} <= set(on_bench)
    # Each line keeps the provider's order.
    assert [p.display_name for p in cut[11:] if p.position == "Defender"] == [
        p.display_name for p in rest if p.position == "Defender"
    ][: on_bench.count("Defender")]
    assert cut_squad(eleven + rest[:3], 17) == eleven + rest[:3]  # short enough: untouched


@pytest.mark.parametrize("order_as_given", [False, True])
def test_a_national_squad_listed_by_line_keeps_attackers_on_its_bench(tmp_path, order_as_given):
    """ESPN lists a squad of 25 by line, keepers first (England, France, Brazil): the 23 the
    row takes, and the 17 the fill cuts them to, keep an attacker on the bench and at most
    two spare keepers. The same when the squad is mapped as given, as the app maps the fill."""
    lines = ["Goalkeeper"] * 3 + ["Defender"] * 9 + ["Midfielder"] * 9 + ["Forward"] * 4
    team = TeamRoster(
        team=Team(id=480, name="Nation 8", code="N08"),
        players=[
            Player(id=8000 + k, name=f"P{k}", first_name="P", last_name=f"P{k}", position=g)
            for k, g in enumerate(lines)
        ],
    )
    patcher = PES2013PS2Patcher(tmp_path)
    team.players = patcher.suggest_squad_order(team)
    data = LeagueData(league=NATIONS.league, teams=[team])
    mapping = [SlotMapping(slot_index=1008, team_id=480)]
    record = patcher.map_rosters(data, mapping, order_as_given=order_as_given).teams[1008]
    for squad in (record.players, cut_squad(record.players, 17)):
        bench = [p.position for p in squad[11:]]
        assert bench.count("Goalkeeper") <= 2 and "Attacker" in bench, bench
    assert [p.position for p in cut_squad(record.players, 17)[11:]].count("Goalkeeper") == 1


def test_a_keeper_heavy_squad_is_cut_to_its_full_size():
    eleven = [_p("XI GK", "Goalkeeper")] + [_p(f"XI {k}", "Defender") for k in range(10)]
    rest = [_p(f"GK {k}", "Goalkeeper") for k in range(5)] + [
        _p(f"MF {k}", "Midfielder") for k in range(10)
    ]
    cut = cut_squad(eleven + rest, 23)
    assert len(cut) == 23 and cut[:11] == eleven
    assert [p.display_name for p in cut[11:13]] == ["GK 0", "MF 0"]
    assert sum(p.position == "Goalkeeper" for p in cut) == 3  # the spare, then two to fill
