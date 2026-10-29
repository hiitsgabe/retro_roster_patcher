"""Seattle and Vegas on the East and West All-Star sides (slots 30 and 31).

The disc's All-Star rows point at NHL players' bios, so the side takes rows the
clubs left on the hidden team instead. The master TDB built here has a club
whose bios the East side shares, and hidden rows: three free, one a national
team still shows, one a free agent's, one a minor-leaguer's, one spare.
"""

from __future__ import annotations

from retro_roster_patcher.core.models import MappedRosters
from retro_roster_patcher.formats.ea_tdb import TDBFile
from retro_roster_patcher.games.nhl05_ps2.models import (
    NHL05GoalieAttributes,
    NHL05PlayerRecord,
    NHL05SkaterAttributes,
)
from retro_roster_patcher.games.nhl05_ps2.patcher import NHL05PS2Patcher, _MasterTables
from retro_roster_patcher.games.nhl05_ps2.rom_writer import NHL05PS2RomWriter
from tests.fixtures import synthetic_nhl05_iso as fx
from tests.fixtures.synthetic_tdb import TYPE_UINT, FieldSpec, TableSpec, build_tdb, pack_bits

HIDDEN = 63  # the fixture's ROST `TEAM` is 6 bits wide
NATIONAL = 36

#: (team, player id, CAPT) per ROST row, in ROST order.
ROWS = [
    (0, 1, 0),
    (0, 2, 1),
    (0, 3, 2),
    (30, 1, 0),  # the East All-Stars: the club's three players
    (30, 2, 2),
    (30, 3, 1),
    (HIDDEN, 11, 0),  # free goalie
    (HIDDEN, 12, 1),  # free, and a 2004 captain
    (HIDDEN, 13, 0),  # free
    (HIDDEN, 14, 0),  # a national team still shows him
    (NATIONAL, 14, 0),
    (HIDDEN, 15, 0),  # a free agent's
    (HIDDEN, 16, 0),  # a minor-leaguer's
    (HIDDEN, 17, 0),  # free, and not needed
]
GOALIES = {1, 11}
FREE_AGENT, MINOR = 15, 16
IDS = sorted({pid for _, pid, _ in ROWS})


def _table(name, fields, size, rows):
    data = b"".join(pack_bits(fields, row, size) for row in rows)
    return TableSpec(name, list(fields), size, len(rows), len(rows), data)


def master() -> TDBFile:
    play = [{"INDX": 100 + i, "TBLE": 0, "ID__": pid} for i, (_, pid, _) in enumerate(ROWS)]
    play += [{"INDX": 900, "TBLE": 0, "ID__": FREE_AGENT}, {"INDX": 901, "TBLE": 0, "ID__": MINOR}]
    rost = [
        {"TEAM": team, "INDX": 100 + i, "CAPT": capt, "DRES": 1, "JERS": 9}
        for i, (team, _, capt) in enumerate(ROWS)
    ]
    bios = [{"INDX": pid, "FNME": "Old", "LNME": f"Bio{pid}"} for pid in IDS]
    attrs = lambda ids: [{"INDX": pid} for pid in ids]  # noqa: E731
    return TDBFile.parse(
        build_tdb(
            [
                _table("PLAY", fx.PLAY_FIELDS, fx.PLAY_RECORD_SIZE, play),
                _table("ROST", fx.ROST_FIELDS, fx.ROST_RECORD_SIZE, rost),
                _table("SPBT", fx.SPBT_FIELDS, fx.SPBT_RECORD_SIZE, bios),
                _table(
                    "SPAI",
                    fx.SPAI_FIELDS,
                    fx.SPAI_RECORD_SIZE,
                    attrs([p for p in IDS if p not in GOALIES]),
                ),
                _table("SGAI", fx.SGAI_FIELDS, fx.SGAI_RECORD_SIZE, attrs(sorted(GOALIES))),
                _table("FREE", [FieldSpec("INDX", TYPE_UINT, 0, 13)], 2, [{"INDX": 900}]),
                _table(
                    "MINI",
                    [FieldSpec("TEAM", TYPE_UINT, 0, 7), FieldSpec("INDX", TYPE_UINT, 7, 13)],
                    3,
                    [{"TEAM": 0, "INDX": 901}],
                ),
            ]
        )
    )


def seattle() -> list[NHL05PlayerRecord]:
    goalie = NHL05PlayerRecord(
        first_name="Joey",
        last_name="Daccord",
        position="G",
        is_goalie=True,
        goalie_attrs=NHL05GoalieAttributes(),
    )
    skaters = [
        NHL05PlayerRecord(first_name=first, last_name=last, skater_attrs=NHL05SkaterAttributes())
        for first, last in (("Matty", "Beniers"), ("Jared", "McCann"))
    ]
    return [goalie, *skaters]


def write_east(tmp_path) -> TDBFile:
    tdb = master()
    tables = _MasterTables.of(tdb)
    writer = NHL05PS2RomWriter(str(tmp_path / "in.iso"), str(tmp_path / "out.iso"))
    rosters = MappedRosters(game_id="nhl05-ps2", teams={30: seattle()})
    NHL05PS2Patcher(tmp_path / "cache")._write_all_teams(writer, rosters, tables, None, None)
    return tdb


def rows(tdb, name):
    table = tdb.get_table(name)
    return [table.read_record(i) for i in range(table.num_records)]


def test_the_side_plays_on_rows_whose_bios_no_one_else_shows(tmp_path):
    rost = rows(write_east(tmp_path), "ROST")
    assert [i for i, row in enumerate(rost) if row["TEAM"] == 30] == [6, 7, 8]


def test_the_sides_own_rows_leave_for_the_hidden_team(tmp_path):
    rost = rows(write_east(tmp_path), "ROST")
    assert [(r["TEAM"], r["DRES"]) for r in rost[3:6]] == [(HIDDEN, 0)] * 3


def test_a_bio_another_team_a_free_agent_or_the_minors_shows_stays_put(tmp_path):
    rost = rows(write_east(tmp_path), "ROST")
    assert [rost[i]["TEAM"] for i in (9, 10, 11, 12, 13)] == [
        HIDDEN,
        NATIONAL,
        HIDDEN,
        HIDDEN,
        HIDDEN,
    ]


def test_the_players_go_into_those_bios_and_the_clubs_keep_theirs(tmp_path):
    bios = {b["INDX"]: b["LNME"] for b in rows(write_east(tmp_path), "SPBT")}
    assert [bios[p] for p in (11, 12, 13)] == ["Daccord", "Beniers", "McCann"]
    assert [bios[p] for p in (1, 2, 3, 14, 15, 16, 17)] == [
        f"Bio{p}" for p in (1, 2, 3, 14, 15, 16, 17)
    ]


def test_the_side_keeps_one_captain_and_two_alternates_on_skaters(tmp_path):
    rost = rows(write_east(tmp_path), "ROST")
    assert [rost[i]["CAPT"] for i in (6, 7, 8)] == [0, 1, 2]
