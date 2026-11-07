"""The retail disc takes forty-player squads and today's free agents.

Gated on RETRO_ROSTER_TEST_ROMS, a directory holding `NHL 2005 (USA) (En,Fr).iso`:
no disc enters this repository. The squads and free agents are synthetic and
have no photos, and portraits, looks and logos are off, so nothing is fetched.
It copies the 4.6 GB image into the test's temporary directory.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from retro_roster_patcher.core.models import MappedRosters
from retro_roster_patcher.games.nhl05_ps2.models import (
    PATCHABLE_SLOT_COUNT,
    TDB_MASTER,
    NHL05GoalieAttributes,
    NHL05PlayerRecord,
    NHL05SkaterAttributes,
)
from retro_roster_patcher.games.nhl05_ps2.patcher import (
    FREE_AGENTS,
    NHL05PS2Patcher,
    _live_records,
    _MasterTables,
)
from retro_roster_patcher.games.nhl05_ps2.rom_reader import NHL05PS2RomReader

DISC = "NHL 2005 (USA) (En,Fr).iso"


def real_rom(test):
    gated = pytest.mark.skipif(
        not os.environ.get("RETRO_ROSTER_TEST_ROMS"),
        reason=f"set RETRO_ROSTER_TEST_ROMS to a directory holding {DISC}",
    )
    return pytest.mark.real_rom(gated(test))


@pytest.fixture(scope="module")
def retail() -> Path:
    disc = Path(os.environ["RETRO_ROSTER_TEST_ROMS"]) / DISC
    if not disc.exists():
        pytest.fail(f"RETRO_ROSTER_TEST_ROMS is set but {disc} is missing")
    return disc


def player(tag: str, position: str) -> NHL05PlayerRecord:
    goalie = position == "G"
    return NHL05PlayerRecord(
        first_name="Test",
        last_name=tag,
        position=position,
        is_goalie=goalie,
        goalie_attrs=NHL05GoalieAttributes() if goalie else None,
        skater_attrs=None if goalie else NHL05SkaterAttributes(),
    )


def squad(slot: int) -> list[NHL05PlayerRecord]:
    """45 players: 4 goalies, 30 forwards, 11 defencemen."""
    positions = ["G"] * 4 + ["C", "LW", "RW"] * 10 + ["D"] * 11
    return [player(f"T{slot}P{i}", pos) for i, pos in enumerate(positions)]


def master(path: Path) -> _MasterTables:
    reader = NHL05PS2RomReader(str(path))
    assert reader.load()
    tdb = reader.get_tdb(TDB_MASTER)
    assert tdb is not None
    return _MasterTables.of(tdb)


@pytest.fixture(scope="module")
def patched(retail, tmp_path_factory):
    out = tmp_path_factory.mktemp("nhl05") / "patched.iso"
    teams = {slot: squad(slot) for slot in range(PATCHABLE_SLOT_COUNT)}
    teams[FREE_AGENTS] = [player(f"FA{i}", "G" if i % 10 == 0 else "D") for i in range(150)]
    rosters = MappedRosters(game_id="nhl05-ps2", teams=teams)
    patcher = NHL05PS2Patcher(out.parent / "cache")
    patcher.patch(
        rom_path=retail,
        output_path=out,
        rosters=rosters,
        portraits=False,
        looks=False,
        logos=False,
    )
    return master(retail), master(out)


def rows_by_team(tables: _MasterTables) -> dict[int, list[str]]:
    """Each team's ROST rows, as their bios' last names."""
    rost, spbt = tables.rost, tables.tdb.get_table("SPBT")
    out: dict[int, list[str]] = {}
    for i in _live_records(rost):
        row = rost.read_record(i)
        bio = tables.spbt_by_indx.get(tables.player(row.get("INDX")))
        name = spbt.read_record(bio).get("LNME") if bio is not None else ""
        out.setdefault(row.get("TEAM"), []).append(name)
    return out


@real_rom
def test_every_club_row_holds_a_player_of_this_patch(patched):
    _, after = patched
    teams = rows_by_team(after)
    assert all(name.startswith(f"T{slot}P") for slot in range(30) for name in teams.get(slot, []))


@real_rom
def test_every_club_keeps_a_full_roster(patched):
    # 45 players each, more than any club's 30-40 rows: a club may be cut so
    # that Seattle and Vegas find rows, never under 23.
    before, after = patched
    shipped, kept = rows_by_team(before), rows_by_team(after)
    assert all(len(kept[slot]) >= min(23, len(shipped[slot])) for slot in range(30))


@real_rom
def test_seattle_and_vegas_take_their_sides(patched):
    _, after = patched
    teams = rows_by_team(after)
    assert [
        len(teams[s]) >= 23 and all(n.startswith(f"T{s}P") for n in teams[s]) for s in (30, 31)
    ] == [
        True,
        True,
    ]


@real_rom
def test_free_agency_lists_only_players_this_patch_wrote(patched):
    _, after = patched
    free = after.tdb.get_table("FREE")
    spbt = after.tdb.get_table("SPBT")
    names = [
        spbt.read_record(after.spbt_by_indx[after.player(free.read_record(i).get("INDX"))]).get(
            "LNME"
        )
        for i in _live_records(free)
    ]
    assert names and all(n.startswith(("FA", "T")) for n in names)
