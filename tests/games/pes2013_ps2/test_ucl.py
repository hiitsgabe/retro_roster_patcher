"""PES 2013 (PS2): the Champions League's "UEFA Champions League" picker group.

The mode offers every club of slots 0-125; its first group is 18 extra rows of every
language overlay's team table (326-343), each carrying a club's id word (the crest follows
it) and mostly that club's name and code pointers. Their kits are entry 54 part 7, one club
record per row: the clubs' UCL kits. A patch keeps each row with its club: the row points at
the club's (renamed) strings, and wears the club's new kits.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from retro_roster_patcher.core.models import SlotMapping
from retro_roster_patcher.games.pes2013_ps2.kits import KIT_ENTRY, KitTable
from retro_roster_patcher.games.pes2013_ps2.models import (
    DATABASE_OVERLAY_ENGLISH,
    DATABASE_OVERLAYS,
    TEXT_AFS,
    UCL_ROWS,
)
from retro_roster_patcher.games.pes2013_ps2.patcher import PES2013PS2Patcher
from retro_roster_patcher.games.pes2013_ps2.rom_reader import RomReader
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster
from tests.fixtures import synthetic_pes2013_iso as fixture

OVERLAYS = (DATABASE_OVERLAY_ENGLISH, 5)


@pytest.fixture
def iso(tmp_path):
    path = tmp_path / "pes.iso"
    fixture.write_iso(path)
    return path


def _reader(path) -> RomReader:
    reader = RomReader(str(path))
    assert reader.load()
    return reader


def _row_strings(reader, overlay, row):
    teams = reader.overlay_teams(overlay)
    data = reader.read("OVER.AFS", overlay)
    at = teams.table_offset + row * 16
    name_ptr = int.from_bytes(data[at : at + 4], "little") - teams.load_address
    code_ptr = int.from_bytes(data[at + 4 : at + 8], "little") - teams.load_address
    return tuple(data[p : p + 24].split(b"\0")[0].decode() for p in (name_ptr, code_ptr))


LEAGUE = LeagueData(
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


def _patch(tmp_path, iso, **options):
    patcher = PES2013PS2Patcher(tmp_path)
    rosters = patcher.map_rosters(
        LEAGUE, [SlotMapping(slot_index=0, team_id=10), SlotMapping(slot_index=1, team_id=11)]
    )
    out = tmp_path / "out.iso"
    patcher.patch(rom_path=iso, output_path=out, rosters=rosters, logos=False, **options)
    return out


def test_the_reader_lists_the_ucl_rows_by_club(iso):
    for overlay in OVERLAYS:
        assert _reader(iso).overlay_teams(overlay).ucl == {326: 1, 327: 0}


def test_the_kit_table_reads_and_builds_the_ucl_part():
    table = KitTable.parse(fixture.build_kit_entry())
    assert len(table.ucl) == 2 and not table.ucl[0][1].parametric
    table.ucl[1] = table.clubs[0]
    again = KitTable.parse(table.build())
    assert [k.build() for k in again.ucl[1]] == [k.build() for k in table.clubs[0]]
    assert [k.build() for k in again.ucl[0]] == [k.build() for k in table.ucl[0]]


def test_a_patched_club_keeps_its_ucl_row_name_and_kits(iso, tmp_path):
    out = _patch(tmp_path, iso)
    reader = _reader(out)
    for overlay in OVERLAYS:
        # Row 326 shares club 1's strings; row 327 had strings of its own and is re-pointed.
        assert _row_strings(reader, overlay, 326) == ("ASTON VILLA", "AVL")
        assert _row_strings(reader, overlay, 327) == ("ARSENAL", "ARS")
        assert reader.overlay_teams(overlay).ucl == {326: 1, 327: 0}
    table = KitTable.parse(reader.read(TEXT_AFS, KIT_ENTRY))
    for row, club in ((326, 1), (327, 0)):
        ucl = [k.build() for k in table.ucl[row - UCL_ROWS.start]]
        assert ucl == [k.build() for k in table.clubs[club]]
        assert table.ucl[row - UCL_ROWS.start][1].parametric  # the new green kit


def test_without_new_kits_the_ucl_kits_stay(iso, tmp_path):
    before = KitTable.parse(_reader(iso).read(TEXT_AFS, KIT_ENTRY))
    after = KitTable.parse(_reader(_patch(tmp_path, iso, kits=False)).read(TEXT_AFS, KIT_ENTRY))
    assert [[k.build() for k in r] for r in after.ucl] == [
        [k.build() for k in r] for r in before.ucl
    ]


# -- the disc (RETRO_ROSTER_TEST_ROMS has pes2013.iso) ------------------------------------

DISC_UCL = (7, 29, 32, 48, 50, 61, 80, 92, 95, 103, 104, 105, 107, 113, 111, 118, 123, 124)


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
def test_on_the_disc_every_overlay_has_the_18_ucl_rows_and_part_7_their_kits(disc):
    reader = _reader(disc)
    for overlay in DATABASE_OVERLAYS:
        ucl = reader.overlay_teams(overlay).ucl
        assert tuple(ucl[row] for row in UCL_ROWS) == DISC_UCL, overlay
    table = KitTable.parse(reader.read(TEXT_AFS, KIT_ENTRY))
    assert len(table.ucl) == len(UCL_ROWS)
    assert table.build()  # round trip of all three parts
