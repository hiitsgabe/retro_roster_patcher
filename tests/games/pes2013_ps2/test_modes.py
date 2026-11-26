"""PES 2013 (PS2): the Copa Libertadores mode's own 38 clubs, and the Champions League's
picker rows, boot-file pairs and Random pool for this season's participants."""

from __future__ import annotations

import struct

import pytest

from retro_roster_patcher.core.models import SlotMapping
from retro_roster_patcher.games.pes2013_ps2.formations import PRESETS
from retro_roster_patcher.games.pes2013_ps2.kits import KIT_ENTRY, KitTable
from retro_roster_patcher.games.pes2013_ps2.models import (
    DATABASE_OVERLAY_ENGLISH,
    FIRST_CLUB_ENTRY,
    LIBERTADORES_FORMATION,
    LIBERTADORES_ROWS,
    LIBERTADORES_SLOT_BASE,
    TEXT_AFS,
    UCL_POOL,
)
from retro_roster_patcher.games.pes2013_ps2.patcher import PES2013PS2Patcher, shirt_numbers
from retro_roster_patcher.games.pes2013_ps2.rom_reader import RomReader
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster
from tests.fixtures import synthetic_pes2013_iso as fixture

COPA_RECORDS = 10  # club 0 holds records 0-4, club 1 records 5-9


@pytest.fixture
def iso(tmp_path):
    path = tmp_path / "pes.iso"
    fixture.write_iso(path, libertadores_entry=fixture.build_libertadores_entry(count=COPA_RECORDS))
    return path


def _reader(path) -> RomReader:
    reader = RomReader(str(path))
    assert reader.load()
    return reader


def _player(pid, name, position, number):
    return Player(id=pid, name=name, last_name=name.split()[-1], position=position, number=number)


COPA = LeagueData(
    league=League(id=2009, name="Copa Libertadores", season=2026),
    teams=[
        TeamRoster(
            team=Team(
                id=819, name="Flamengo", code="FLA", color="C60000", alternate_color="000000"
            ),
            players=[
                _player(1, "Agustin Rossi", "Goalkeeper", 1),
                _player(2, "Leo Ortiz", "Defender", 3),
                _player(3, "Giorgian de Arrascaeta", "Midfielder", 10),
            ],
        ),
        TeamRoster(
            team=Team(id=12, name="Lanus", code="LAN", color="800000", alternate_color="FFFFFF"),
            players=[_player(4 + i, f"Player {i}", "Midfielder", 7) for i in range(7)],
        ),
    ],
)


def _patch_copa(tmp_path, iso, **options):
    patcher = PES2013PS2Patcher(tmp_path)
    assert patcher.libertadores_slots(iso)[:2] == [2000, 2001]
    rosters = patcher.map_rosters(
        COPA,
        [
            SlotMapping(slot_index=LIBERTADORES_SLOT_BASE, team_id=819),
            SlotMapping(slot_index=LIBERTADORES_SLOT_BASE + 1, team_id=12),
        ],
    )
    out = tmp_path / "out.iso"
    result = patcher.patch(rom_path=iso, output_path=out, rosters=rosters, logos=False, **options)
    return out, result


def test_the_reader_finds_the_copa_clubs_rows_squads_and_numbers(iso):
    reader = _reader(iso)
    tables = reader.libertadores_tables()
    assert tables.copa_squad(0) == [0, 1, 2, 3, 4] and tables.copa_squad(1) == [5, 6, 7, 8, 9]
    assert tables.copa_squad(2) == []
    copa = reader.overlay_teams(DATABASE_OVERLAY_ENGLISH).copa
    assert len(copa) == 38 and copa[0][0].text == "COPA CLUB 0" and copa[37][1].text == "C37"


def test_a_copa_club_gets_its_squad_names_numbers_and_country(iso, tmp_path):
    out, result = _patch_copa(tmp_path, iso)
    assert result.teams_patched == 2
    reader = _reader(out)
    for index in (49, 48):
        tables = reader.libertadores_tables(index)
        # Flamengo's three take the first three of its five records, the last two are blanked
        # and leave the squad; Lanus has five records for seven players.
        assert tables.copa_squad(0) == [0, 1, 2]
        names = [tables.record(i)[:32].decode("utf-16-le").rstrip("\0") for i in range(5)]
        assert names[:3] == ["AGUSTIN ROSSI", "LEO ORTIZ", "ARRASCAETA"]
        assert tables.record(3) == bytes(124) and tables.record(4) == bytes(124)
        numbers = tables.parts[2]
        assert list(numbers[:4]) == [0, 2, 9, 0xFF]
        assert tables.copa_squad(1) == [5, 6, 7, 8, 9]
        # Seven men, one number 7: the first keeps it, the rest take the lowest free ones.
        assert list(numbers[32:37]) == [6, 0, 1, 2, 3]
    for overlay in (DATABASE_OVERLAY_ENGLISH, 5):
        teams = reader.overlay_teams(overlay)
        assert [s.text for s in teams.copa[0]] == ["FLAMENGO", "FLA"]
        assert [s.text for s in teams.copa[1]] == ["LANUS", "LAN"]
        assert [s.text for s in teams.copa[2]] == ["COPA CLUB 2", "C02"]


def test_the_copa_names_are_laid_anew_and_a_long_one_is_cut_to_fit(iso, tmp_path):
    long = LeagueData(
        league=COPA.league,
        teams=[
            TeamRoster(
                team=Team(id=819, name="Club Atletico Independiente Rivadavia de Mendoza"),
                players=COPA.teams[0].players,
            )
        ],
    )
    patcher = PES2013PS2Patcher(tmp_path)
    rosters = patcher.map_rosters(long, [SlotMapping(slot_index=2000, team_id=819)])
    out = tmp_path / "long.iso"
    patcher.patch(rom_path=iso, output_path=out, rosters=rosters, logos=False)
    teams = _reader(out).overlay_teams(DATABASE_OVERLAY_ENGLISH)
    # The 38 rows' strings share one room: the long name took what the others left.
    assert teams.copa[0][0].text.startswith("CLUB ATLETICO INDEPENDIENTE")
    assert [s.text for s in teams.copa[37]] == ["COPA CLUB 37", "C37"]


def test_a_copa_club_wears_its_new_kits_and_formation(iso, tmp_path):
    copa = LeagueData(league=COPA.league, teams=[COPA.teams[0]])
    copa.teams[0].extra["formation"] = "4-3-3"
    patcher = PES2013PS2Patcher(tmp_path)
    rosters = patcher.map_rosters(copa, [SlotMapping(slot_index=2000, team_id=819)])
    out = tmp_path / "kits.iso"
    patcher.patch(rom_path=iso, output_path=out, rosters=rosters, logos=False)
    reader = _reader(out)
    table = KitTable.parse(reader.read(TEXT_AFS, KIT_ENTRY))
    assert len(table.libertadores) == 38
    kit = table.libertadores[0][1]
    assert kit.parametric and any(kit.rgb(c)[0] > 190 > 60 > kit.rgb(c)[1] for c in range(6))
    assert not table.libertadores[1][1].parametric  # untouched: the disc's licensed kit
    formations = reader.formations()
    assert formations.get(LIBERTADORES_FORMATION)[:30] in PRESETS.values()
    assert formations.get(LIBERTADORES_FORMATION + 1) == _reader(iso).formations().get(
        LIBERTADORES_FORMATION + 1
    )


def test_shirt_numbers_keep_a_players_own_and_give_a_clash_the_lowest_free():
    from retro_roster_patcher.games.pes2013_ps2.models import PESPlayer

    squad = [PESPlayer("A", "A", number=n) for n in (9, 9, None, 1, 120)]
    assert shirt_numbers(squad) == [8, 0, 1, 2, 3]


# -- the Champions League -------------------------------------------------------------------

CLUBS = LeagueData(
    league=League(id=1, name="Fixture League", season=2026),
    teams=[
        TeamRoster(
            team=Team(id=k, name=name, code=code, color="00FF00", alternate_color="FFFFFF"),
            players=[
                Player(id=100 * k, name="A Keeper", last_name="Keeper", position="Goalkeeper")
            ],
        )
        for k, name, code in ((10, "Arsenal", "ARS"), (11, "Aston Villa", "AVL"))
    ],
)


def _patch_clubs(tmp_path, iso, **options):
    patcher = PES2013PS2Patcher(tmp_path)
    rosters = patcher.map_rosters(
        CLUBS, [SlotMapping(slot_index=0, team_id=10), SlotMapping(slot_index=1, team_id=11)]
    )
    out = tmp_path / "ucl.iso"
    patcher.patch(rom_path=iso, output_path=out, rosters=rosters, logos=False, **options)
    return out


def _pairs(reader):
    return reader.ucl_pairs()[1]


def test_the_reader_finds_the_pairs_and_the_random_pool(iso):
    reader = _reader(iso)
    assert reader.ucl_pairs() == (
        fixture.UCL_PAIRS_AT,
        [FIRST_CLUB_ENTRY + c for _, c in fixture.UCL_PAIRS],
    )
    assert reader.ucl_pool() == (fixture.UCL_POOL_AT, fixture.UCL_POOL_ROWS)


def test_this_seasons_participants_take_the_picker_rows_pairs_and_random_pool(iso, tmp_path):
    # Aston Villa (slot 1) ranks first, Arsenal (slot 0) second; 99 is not on the disc.
    out = _patch_clubs(tmp_path, iso, champions=[11, 99, 10])
    reader = _reader(out)
    assert reader.overlay_teams(DATABASE_OVERLAY_ENGLISH).ucl == {326: 1, 327: 0}
    assert _pairs(reader)[:2] == [FIRST_CLUB_ENTRY + 1, FIRST_CLUB_ENTRY + 0]
    offset, pool = reader.ucl_pool()
    assert pool[:2] == [FIRST_CLUB_ENTRY + 1, FIRST_CLUB_ENTRY + 0]
    assert pool[2:] == fixture.UCL_POOL_ROWS[: UCL_POOL - 2] and len(pool) == UCL_POOL


def test_without_the_participants_the_pairs_and_the_pool_stay(iso, tmp_path):
    out = _patch_clubs(tmp_path, iso)
    reader = _reader(out)
    assert _pairs(reader) == _pairs(_reader(iso))
    assert reader.ucl_pool() == _reader(iso).ucl_pool()


def test_a_picker_row_carries_its_clubs_whole_team_entry(iso, tmp_path):
    out = _patch_clubs(tmp_path, iso, champions=[10, 11])
    reader = _reader(out)
    teams = reader.overlay_teams(DATABASE_OVERLAY_ENGLISH)
    data = reader.read("OVER.AFS", DATABASE_OVERLAY_ENGLISH)

    def entry(row):
        at = teams.table_offset + row * 16
        return struct.unpack_from("<IIII", data, at)

    assert entry(326) == entry(FIRST_CLUB_ENTRY + 0) and entry(327) == entry(FIRST_CLUB_ENTRY + 1)
    assert teams.ucl == {326: 0, 327: 1}
    assert [s.text for s in teams.clubs[0]] == ["ARSENAL", "ARS"]
    assert LIBERTADORES_ROWS.start == 237  # the Copa rows sit between the clubs and these


# -- the disc (RETRO_ROSTER_TEST_ROMS has pes2013.iso) ------------------------------------


@pytest.fixture
def disc():
    import os
    from pathlib import Path

    root = os.environ.get("RETRO_ROSTER_TEST_ROMS")
    if not root:
        pytest.skip("set RETRO_ROSTER_TEST_ROMS to a directory holding pes2013.iso")
    path = Path(root) / "pes2013.iso"
    if not path.exists():
        pytest.fail(f"RETRO_ROSTER_TEST_ROMS is set but {path} is missing")
    return path


@pytest.mark.real_rom
def test_on_the_disc_the_copa_and_the_champions_league_tables_are_found(disc):
    reader = _reader(disc)
    for index in (49, 48):
        tables = reader.libertadores_tables(index)
        assert tables.player_count == 943
        assert sum(len(tables.copa_squad(k)) for k in range(38)) == 943
        assert tables.parts[2][12 * 32 : 12 * 32 + 3] == bytes((0, 1, 5))  # Santos: 1, 2, 6
    for overlay in range(5, 20):
        copa = reader.overlay_teams(overlay)
        assert len(copa.copa) == 38 and copa.copa_room is not None
    assert reader.overlay_teams(DATABASE_OVERLAY_ENGLISH).copa[12][0].text == "SANTOS FC"
    assert len(KitTable.parse(reader.read(TEXT_AFS, KIT_ENTRY)).libertadores) == 38
    assert reader.ucl_pairs()[1][:2] == [FIRST_CLUB_ENTRY + 7, FIRST_CLUB_ENTRY + 29]
    pool = reader.ucl_pool()[1]
    assert len(pool) == UCL_POOL and pool[:3] == [FIRST_CLUB_ENTRY + 2, 147, FIRST_CLUB_ENTRY + 7]
