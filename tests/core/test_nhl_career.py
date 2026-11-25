"""`core.nhl_career`: a rewritten bio shows its new player's seasons, not the old one's."""

from __future__ import annotations

from retro_roster_patcher.core.nhl_career import forget, rewrite, season_line
from retro_roster_patcher.formats.ea_tdb import TDBFile
from tests.fixtures.synthetic_tdb import (
    TYPE_SINT,
    TYPE_STRING,
    TYPE_UINT,
    FieldSpec,
    TableSpec,
    build_tdb,
    pack_bits,
)

# The retail columns, at byte-friendly offsets.
SKATER = [
    FieldSpec("TEAM", TYPE_STRING, 0, 104),
    FieldSpec("PLMN", TYPE_SINT, 104, 7),
    FieldSpec("SHTP", TYPE_UINT, 111, 10),
    FieldSpec("YEAR", TYPE_UINT, 121, 12),
    FieldSpec("SHOT", TYPE_UINT, 133, 9),
    FieldSpec("INDX", TYPE_UINT, 142, 12),
    FieldSpec("GWG_", TYPE_UINT, 154, 4),
    FieldSpec("PTS_", TYPE_UINT, 158, 8),
    FieldSpec("GP__", TYPE_UINT, 166, 8),
    FieldSpec("A___", TYPE_UINT, 174, 7),
    FieldSpec("G___", TYPE_UINT, 181, 7),
]
GOALIE = [
    FieldSpec("SVPC", TYPE_UINT, 0, 10),
    FieldSpec("TEAM", TYPE_STRING, 16, 104),
    FieldSpec("YEAR", TYPE_UINT, 120, 12),
    FieldSpec("INDX", TYPE_UINT, 132, 12),
    FieldSpec("GAA_", TYPE_UINT, 144, 11),
    FieldSpec("MIN_", TYPE_UINT, 155, 13),
    FieldSpec("GA__", TYPE_UINT, 168, 8),
    FieldSpec("SA__", TYPE_UINT, 176, 12),
    FieldSpec("GP__", TYPE_UINT, 188, 8),
    FieldSpec("W___", TYPE_UINT, 196, 6),
]
LINK = [FieldSpec("INDX", TYPE_UINT, 0, 13), FieldSpec("PID_", TYPE_UINT, 13, 13)]


def _table(name, fields, size, rows, capacity=None):
    data = b"".join(pack_bits(fields, row, size) for row in rows)
    capacity = capacity or len(rows)
    spare = bytes(size) * (capacity - len(rows))
    return TableSpec(name, list(fields), size, capacity, len(rows), data + spare)


def stats(capacity=4) -> TDBFile:
    old = [{"INDX": 7, "YEAR": 40 + i, "TEAM": "NYR", "GP__": 80, "G___": 50} for i in range(2)]
    other = [{"INDX": 9, "YEAR": 45, "TEAM": "BOS", "GP__": 60, "G___": 3}]
    return TDBFile.parse(
        build_tdb(
            [
                _table("SCSP", SKATER, 24, old + other, capacity),
                _table("SCPP", SKATER, 24, [{"INDX": 7, "YEAR": 41, "GP__": 6}]),
                _table("SCSG", GOALIE, 26, [{"INDX": 7, "YEAR": 41, "GP__": 6}], 2),
            ]
        )
    )


def rows(tdb: TDBFile, name: str) -> list[dict]:
    table = tdb.get_table(name)
    assert table is not None
    return [table.read_record(i) for i in range(table.num_records)]


MCDAVID = {
    "season": 2025,
    "playoffs": False,
    "team": "EDM",
    "GP": 67,
    "G": 26,
    "A": 74,
    "PTS": 100,
    "+/-": -5,
    "S": 196,
    "GWG": 3,
}


def test_a_rewritten_bio_shows_his_own_seasons_and_the_others_keep_theirs():
    tdb = stats()
    assert rewrite(tdb, {7: (False, [MCDAVID])}) == (1, 0)
    mine = [r for r in rows(tdb, "SCSP") if r["INDX"] == 7]
    assert [(r["TEAM"], r["YEAR"], r["GP__"], r["G___"], r["A___"], r["PTS_"]) for r in mine] == [
        ("EDM", 2026 - 1959, 67, 26, 74, 100)
    ]
    assert [r["G___"] for r in rows(tdb, "SCSP") if r["INDX"] == 9] == [3]
    # The old player's playoffs go too: the new one has none.
    assert rows(tdb, "SCPP") == []


def test_a_minus_is_twos_complement_and_the_shooting_rate_a_whole_percent():
    tdb = stats()
    rewrite(tdb, {7: (False, [MCDAVID])})
    mine = next(r for r in rows(tdb, "SCSP") if r["INDX"] == 7)
    assert mine["PLMN"] == 128 - 5
    assert mine["SHTP"] == round(26 * 100 / 196)


def test_a_player_without_a_season_shows_none_as_a_rookie_does():
    tdb = stats()
    assert rewrite(tdb, {7: (False, [])}) == (0, 0)
    assert [r["INDX"] for r in rows(tdb, "SCSP")] == [9]


def test_a_full_table_keeps_the_newest_seasons():
    tdb = stats(capacity=3)
    seasons = [MCDAVID | {"season": year} for year in (2022, 2023, 2024, 2025)]
    assert rewrite(tdb, {7: (False, seasons)}) == (2, 2)
    assert sorted(r["YEAR"] for r in rows(tdb, "SCSP") if r["INDX"] == 7) == [66, 67]


def test_a_goalies_average_is_cut_to_its_whole_part_as_the_disc_keeps_it():
    tdb = stats()
    season = {"season": 2025, "team": "BOS", "GP": 58, "W": 30, "GA": 150, "SA": 1600, "MIN": 3400}
    rewrite(tdb, {7: (True, [season])})
    mine = rows(tdb, "SCSG")[0]
    assert (mine["GAA_"], mine["SVPC"], mine["MIN_"], mine["W___"]) == (2, 0, 3400, 30)
    # A goalie's bio leaves the skaters' tables as well.
    assert [r["INDX"] for r in rows(tdb, "SCSP")] == [9]


def test_a_stat_line_stands_in_for_a_career_that_was_not_fetched():
    espn = {"GP": 70.0, "G": 30.0, "A": 40.0, "PTS": 70.0, "+/-": 12.0, "SOG": 210.0, "PIM": 20.0}
    assert season_line(espn, 2025, "BOS", False) == {
        "season": 2025,
        "playoffs": False,
        "team": "BOS",
        "GP": 70,
        "G": 30,
        "A": 40,
        "PTS": 70,
        "PIM": 20,
        "+/-": 12,
        "S": 210,
    }
    assert season_line({"GP": 0}, 2025, "BOS", False) is None
    goalie = season_line({"GP": 50, "W": 30, "GA": 120, "GAA": 2.4}, 2025, "BOS", True)
    assert goalie is not None and goalie["MIN"] == 3000


def test_forgetting_drops_his_tournaments_and_unlinks_his_trophies():
    tournaments = TDBFile.parse(
        build_tdb([_table("ITPS", LINK, 4, [{"INDX": 7}, {"INDX": 9}, {"INDX": 7}])])
    )
    awards = TDBFile.parse(build_tdb([_table("SAWA", LINK, 4, [{"PID_": 7}, {"PID_": 9}])]))
    forget(tournaments, awards, [7])
    assert [r["INDX"] for r in rows(tournaments, "ITPS")] == [9]
    assert [r["PID_"] for r in rows(awards, "SAWA")] == [0, 9]
    forget(None, None, [7])
