"""Free Agency: today's free agents and the players a club had no row for.

`FREE` lists free agents by PLAY `INDX`, each leading to a bio, so a free
agent is written into a bio a FREE row already points at, as a club's player
is into one of its ROST rows. The master TDB built here has a club, a hidden
row, and six FREE rows: a goalie's bio, three skaters', one a club row also
shows and one a minor-leaguer's.
"""

from __future__ import annotations

from retro_roster_patcher.core.models import MappedRosters
from retro_roster_patcher.formats.ea_tdb import TDBFile
from retro_roster_patcher.games.nhl05_ps2.models import (
    NHL05GoalieAttributes,
    NHL05PlayerRecord,
    NHL05SkaterAttributes,
)
from retro_roster_patcher.games.nhl05_ps2.patcher import (
    FREE_AGENTS,
    NHL05PS2Patcher,
    _MasterTables,
)
from retro_roster_patcher.games.nhl05_ps2.rom_writer import NHL05PS2RomWriter
from retro_roster_patcher.sports.models import League, LeagueData, Player
from tests.fixtures import synthetic_nhl05_iso as fx
from tests.fixtures.synthetic_tdb import TYPE_UINT, FieldSpec, TableSpec, build_tdb, pack_bits

HIDDEN = 63  # the fixture's ROST `TEAM` is 6 bits wide
FREE_TEAM = 50  # what the disc's free agents' bios hold in SPBT `TEAM`

#: (team, player id) per ROST row.
ROWS = [(0, 1), (0, 2), (0, 3), (HIDDEN, 9)]
#: Player id per FREE row: a goalie, three skaters, one a club row shows, one a minor-leaguer's.
FREE_ROWS = [20, 21, 22, 23, 3, 25]
MINOR = 25
GOALIES = {1, 20}
IDS = sorted({pid for _, pid in ROWS} | set(FREE_ROWS))
FREE_FIELDS = [FieldSpec("INDX", TYPE_UINT, 0, 13)]


def _table(name, fields, size, rows):
    data = b"".join(pack_bits(fields, row, size) for row in rows)
    return TableSpec(name, list(fields), size, len(rows), len(rows), data)


def master() -> TDBFile:
    play = [{"INDX": 100 + i, "TBLE": 0, "ID__": pid} for i, (_, pid) in enumerate(ROWS)]
    play += [{"INDX": 900 + i, "TBLE": 0, "ID__": pid} for i, pid in enumerate(FREE_ROWS)]
    play += [{"INDX": 990, "TBLE": 0, "ID__": MINOR}]
    rost = [
        {"TEAM": team, "INDX": 100 + i, "DRES": 1, "JERS": 9} for i, (team, _) in enumerate(ROWS)
    ]
    bios = [
        {"INDX": pid, "FNME": "Old", "LNME": f"Bio{pid}", "TEAM": FREE_TEAM if pid >= 20 else 0}
        for pid in IDS
    ]
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
                _table(
                    "FREE",
                    FREE_FIELDS,
                    2,
                    [{"INDX": 900 + i} for i in range(len(FREE_ROWS))],
                ),
                _table(
                    "MINI",
                    [FieldSpec("TEAM", TYPE_UINT, 0, 7), FieldSpec("INDX", TYPE_UINT, 7, 13)],
                    3,
                    [{"TEAM": 0, "INDX": 990}],
                ),
            ]
        )
    )


def skater(name: str) -> NHL05PlayerRecord:
    return NHL05PlayerRecord(
        first_name="New", last_name=name, position="D", skater_attrs=NHL05SkaterAttributes()
    )


def goalie(name: str) -> NHL05PlayerRecord:
    return NHL05PlayerRecord(
        first_name="New",
        last_name=name,
        position="G",
        is_goalie=True,
        goalie_attrs=NHL05GoalieAttributes(),
    )


def write(tmp_path, players):
    """`_write_free_agents` over `master()`: (tdb, tables, how many went in)."""
    tdb = master()
    tables = _MasterTables.of(tdb)
    writer = NHL05PS2RomWriter(str(tmp_path / "in.iso"), str(tmp_path / "out.iso"))
    count = NHL05PS2Patcher(tmp_path / "cache")._write_free_agents(writer, players, tables)
    return tdb, tables, count


def rows(tdb, name):
    table = tdb.get_table(name)
    return [table.read_record(i) for i in range(table.num_records)]


def bios(tdb):
    return {b["INDX"]: b for b in rows(tdb, "SPBT")}


def test_current_free_agents_take_the_free_agents_bios(tmp_path):
    tdb, _, count = write(tmp_path, [skater("Leddy"), skater("Petry"), skater("Kane")])
    names = bios(tdb)
    assert (count, [names[p]["LNME"] for p in (21, 22, 23)]) == (3, ["Leddy", "Petry", "Kane"])


def test_a_goalie_free_agent_takes_a_goalie_bio(tmp_path):
    tdb, _, _ = write(tmp_path, [skater("Leddy"), goalie("Larsson")])
    assert (bios(tdb)[20]["LNME"], bios(tdb)[21]["LNME"]) == ("Larsson", "Leddy")


def test_goalies_past_the_goalie_bios_are_left_out(tmp_path):
    tdb, _, count = write(tmp_path, [goalie("Larsson"), goalie("Boyko")])
    assert (count, bios(tdb)[20]["LNME"]) == (1, "Larsson")


def test_the_list_keeps_its_rows_and_their_order(tmp_path):
    # Season mode breaks on a FREE with fewer rows (checked in PCSX2): only the
    # bios behind the rows change.
    tdb, _, _ = write(tmp_path, [skater("Leddy")])
    assert [r["INDX"] for r in rows(tdb, "FREE")] == [900 + i for i in range(len(FREE_ROWS))]


def test_rows_no_current_player_reaches_keep_their_2004_free_agent(tmp_path):
    tdb, _, _ = write(tmp_path, [skater("Leddy")])
    names = bios(tdb)
    assert [names[p]["LNME"] for p in (20, 22, 23)] == ["Bio20", "Bio22", "Bio23"]


def test_a_free_agents_bio_keeps_its_team_field(tmp_path):
    tdb, _, _ = write(tmp_path, [skater("Leddy")])
    assert bios(tdb)[21]["TEAM"] == FREE_TEAM


def test_a_bio_a_club_or_the_minors_shows_is_never_written(tmp_path):
    tdb, _, count = write(tmp_path, [skater(f"S{i}") for i in range(5)])
    names = bios(tdb)
    assert (count, names[3]["LNME"], names[MINOR]["LNME"]) == (3, "Bio3", f"Bio{MINOR}")


def test_the_players_who_found_no_place_are_counted(tmp_path):
    lines = []
    tdb = master()
    writer = NHL05PS2RomWriter(str(tmp_path / "in.iso"), str(tmp_path / "out.iso"))
    patcher = NHL05PS2Patcher(tmp_path / "cache", on_status=lines.append)
    patcher._write_free_agents(writer, [skater(f"S{i}") for i in range(5)], _MasterTables.of(tdb))
    assert lines == [
        "Free agents: 3 of today's players; 3 left from the 2004 disc; 2 found no place"
    ]


def test_a_2004_free_agent_still_listed_lends_no_portrait(tmp_path):
    _, tables, _ = write(tmp_path, [skater("Leddy")])
    assert tables.dropped == []


def test_a_disc_without_free_agents_writes_none(tmp_path):
    tdb = master()
    del tdb.tables["FREE"]
    writer = NHL05PS2RomWriter(str(tmp_path / "in.iso"), str(tmp_path / "out.iso"))
    patcher = NHL05PS2Patcher(tmp_path / "cache")
    assert patcher._write_free_agents(writer, [skater("Leddy")], _MasterTables.of(tdb)) == 0


def league_with_free_agents(players, lines=None):
    from dataclasses import asdict

    return LeagueData(
        league=League(
            id=0,
            name="NHL",
            extra={"free_agents": {"players": [asdict(p) for p in players], "stats": lines or {}}},
        ),
        teams=[],
    )


def test_mapping_puts_the_free_agents_under_their_own_key(tmp_path):
    data = league_with_free_agents([Player(id=7, name="Nick Leddy", position="D")])
    mapped = NHL05PS2Patcher(tmp_path / "cache").map_rosters(data)
    assert [r.last_name for r in mapped.teams[FREE_AGENTS]] == ["Leddy"]


def test_a_free_agents_line_rates_him(tmp_path):
    data = league_with_free_agents(
        [Player(id=7, name="Nick Leddy", position="D")], {"7": {"PTS": 90, "GP": 82}}
    )
    mapped = NHL05PS2Patcher(tmp_path / "cache").map_rosters(data)
    assert mapped.teams[FREE_AGENTS][0].skater_attrs.deking == 63


def test_a_league_without_free_agents_maps_none(tmp_path):
    data = LeagueData(league=League(id=0, name="NHL"), teams=[])
    assert FREE_AGENTS not in NHL05PS2Patcher(tmp_path / "cache").map_rosters(data).teams


def test_the_players_left_without_a_row_come_before_todays_free_agents(tmp_path, monkeypatch):
    # The fixture disc has 25 rows a team (2 goalie rows): 26 players for
    # Anaheim leave the last skater without a row.
    seen = []
    monkeypatch.setattr(
        NHL05PS2Patcher,
        "_write_free_agents",
        lambda self, writer, players, *_: seen.extend(p.last_name for p in players) or 0,
    )
    club = [goalie("G0"), goalie("G1"), *(skater(f"S{i}") for i in range(24))]
    rosters = MappedRosters(game_id="nhl05-ps2", teams={0: club, FREE_AGENTS: [skater("Leddy")]})
    source = tmp_path / "game.iso"
    fx.write_iso(source)
    patcher = NHL05PS2Patcher(tmp_path / "cache")
    patcher.patch(rom_path=source, output_path=tmp_path / "out.iso", rosters=rosters, looks=False)
    assert seen == ["S23", "Leddy"]


ART_FIELDS = [FieldSpec("INDX", TYPE_UINT, 0, 16), FieldSpec("ART_", TYPE_UINT, 16, 16)]


def test_portraits_go_to_each_teams_first_players_first(tmp_path):
    # Two portraits free for three players without one: the two teams' first
    # players take them, whatever order the teams were written in.
    spbt = TDBFile.parse(
        build_tdb(
            [
                _table(
                    "SPBT",
                    ART_FIELDS,
                    4,
                    [{"INDX": i, "ART_": art} for i, art in enumerate([7, 8, 9, 10, 900, 901])],
                )
            ]
        )
    ).get_table("SPBT")
    writer = NHL05PS2RomWriter(str(tmp_path / "in.iso"), str(tmp_path / "out.iso"))
    # Bios 0-2 written, team A's second player (bio 0) before team B's first (1)
    # and team A's first (2); bios 4 and 5 were dropped and have portraits.
    writer.photos = {0: "a2", 1: "b1", 2: "a1"}
    writer.ranks = {0: 1, 1: 0, 2: 0}
    names = {f"{art:04d}.big" for art in (900, 901)}
    NHL05PS2Patcher._lend(spbt, names, [4, 5], writer)
    arts = [spbt.read_record(i)["ART_"] for i in range(6)]
    assert (arts[0], sorted(arts[1:3]), arts[4:]) == (7, [900, 901], [0, 0])
