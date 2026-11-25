"""`stats.rows`: a season line as the disc's statistics tables hold it."""

from __future__ import annotations

from retro_roster_patcher.games.mvp_psp import stats
from retro_roster_patcher.games.mvp_psp.models import (
    PITCHER_STAT_TABLES,
    STAT_TABLES,
    MVPPlayerRecord,
)

# Elly De La Cruz's 2025, as ESPN sends it.
BATTER = {
    "GP": 162, "AB": 629, "R": 102, "H": 166, "2B": 31, "3B": 7, "HR": 22, "RBI": 86,
    "BB": 67, "IBB": 10, "HBP": 2, "SO": 181, "SB": 37, "CS": 8, "SH": 0, "SF": 1,
    "vsL.AB": 199, "vsL.H": 47, "vsL.2B": 6, "vsL.3B": 0, "vsL.HR": 5, "vsL.RBI": 26,
    "vsL.BB": 11, "vsL.HBP": 0, "vsL.SO": 61,
    "vsR.AB": 430, "vsR.H": 119, "vsR.2B": 25, "vsR.3B": 7, "vsR.HR": 17, "vsR.RBI": 60,
    "vsR.BB": 56, "vsR.HBP": 2, "vsR.SO": 120,
    "fielding.gamesPlayed": 158, "fielding.putouts": 217, "fielding.assists": 340,
    "fielding.errors": 26,
    "career.GP": 563, "career.AB": 2194, "career.H": 575, "career.HR": 90, "career.RBI": 285,
    "career.R": 373, "career.SB": 169,
}  # fmt: skip
# Hunter Greene's.
PITCHER = {
    "GP": 19, "GS": 19, "R": 35, "ER": 33, "W": 7, "L": 4, "SV": 0, "BLSV": 0, "CG": 1,
    "SHO": 1, "POA": 2, "IP": 107.2, "AB": 391, "H": 75, "2B": 22, "3B": 1, "HR": 15,
    "BB": 26, "K": 132, "BH": 2, "IBB": 0, "SF": 0, "SH": 1, "SB": 9, "CS": 3,
    "vsL.AB": 190, "vsL.H": 35, "vsL.2B": 11, "vsL.3B": 0, "vsL.HR": 9, "vsL.BB": 15,
    "vsL.HBP": 2, "vsL.SO": 69,
    "vsR.AB": 201, "vsR.H": 40, "vsR.2B": 11, "vsR.3B": 1, "vsR.HR": 6, "vsR.BB": 11,
    "vsR.HBP": 0, "vsR.SO": 63,
    "career.GP": 96, "career.IP": 523.1, "career.ER": 222, "career.W": 27, "career.L": 31,
    "career.SV": 0, "career.K": 650, "career.BB": 188,
}  # fmt: skip


def rows(line, *, pitcher=False):
    record = MVPPlayerRecord(first_name="A", last_name="B", is_pitcher=pitcher, line=dict(line))
    return {table: [row[c] for c in sorted(row)] for table, row in stats.rows(record).items()}


def counts(row):
    return [int(v) for v in row[2:]]


def test_every_row_opens_with_his_name():
    assert all(row[:2] == ["A", "B"] for row in rows(BATTER).values())


def test_a_batter_has_no_row_in_the_pitchers_tables_and_a_pitcher_has_every_row():
    assert set(rows(BATTER)) == set(STAT_TABLES) - set(PITCHER_STAT_TABLES)
    assert set(rows(PITCHER, pitcher=True)) == set(STAT_TABLES)


def test_every_row_is_as_wide_as_its_tables_header():
    for table, row in rows(PITCHER, pitcher=True).items():
        assert len(row) - 2 == len(stats.COLUMNS[table.removesuffix("_lhp").removesuffix("_rhp")])


def test_a_batters_two_sides_add_up_to_his_season():
    got = rows(BATTER)
    left, right = counts(got["lrbatstat_lhp"]), counts(got["lrbatstat_rhp"])
    # 1b 2b 3b hr rbi so bb ibb sh sf hbp tpa
    assert left == [36, 6, 0, 5, 26, 61, 11, 3, 0, 0, 0, 210]
    assert right == [70, 25, 7, 17, 60, 120, 56, 7, 0, 1, 2, 489]
    hits = sum(left[:4]) + sum(right[:4])
    at_bats = left[11] + right[11] - sum(left[i] + right[i] for i in (6, 8, 9, 10))
    assert (hits, at_bats) == (BATTER["H"], BATTER["AB"])


def test_a_batters_season_fielding_and_career():
    got = rows(BATTER)
    assert counts(got["batstat"]) == [162, 102, 37, 8]
    assert counts(got["fieldstat"]) == [158, 217, 340, 26]
    assert counts(got["careerstats"]) == [563, 2194, 575, 90, 285, 373, 169]


def test_a_pitchers_season_writes_thirds_of_an_inning_apart():
    got = rows(PITCHER, pitcher=True)
    # g gs r er w l sv blsv cg sho pk ip pip
    assert counts(got["pitchstat"]) == [19, 19, 35, 33, 7, 4, 0, 0, 1, 1, 2, 107, 2]
    # g ip pip er w l sv so bb
    assert counts(got["pitchcareer"]) == [96, 523, 1, 222, 27, 31, 0, 650, 188]


def test_a_pitchers_sides_are_what_each_hand_of_batter_did():
    got = rows(PITCHER, pitcher=True)
    # so bb ibb hb sacs sh 1b 2b 3b hr tpa
    assert counts(got["lrpitchstat_lhp"]) == [69, 15, 0, 2, 0, 0, 15, 11, 0, 9, 207]
    assert counts(got["lrpitchstat_rhp"]) == [63, 11, 0, 0, 0, 1, 22, 11, 1, 6, 213]


def test_a_pitcher_bats_nothing_and_his_games_are_his_appearances():
    got = rows(PITCHER, pitcher=True)
    assert counts(got["batstat"]) == [19, 0, 0, 0]
    assert counts(got["careerstats"]) == [96, 0, 0, 0, 0, 0, 0]
    assert set(counts(got["lrbatstat_lhp"])) == {0}


def test_a_player_without_a_line_gets_zeros_not_another_mans_numbers():
    for pitcher in (False, True):
        for row in rows({}, pitcher=pitcher).values():
            assert set(row[2:]) == {"0"}


def test_a_leaders_only_line_counts_as_none():
    assert set(rows({"HR": 30.0, "AVG": 0.3})["lrbatstat_rhp"][2:]) == {"0"}


def test_a_line_without_splits_goes_to_the_right_handers_table():
    season = {k: v for k, v in BATTER.items() if not k.startswith("vs")}
    got = rows(season)
    assert set(counts(got["lrbatstat_lhp"])) == {0}
    assert counts(got["lrbatstat_rhp"]) == [106, 31, 7, 22, 86, 181, 67, 10, 0, 1, 2, 699]


def test_without_a_career_the_season_stands_as_the_career():
    season = {k: v for k, v in BATTER.items() if not k.startswith("career")}
    assert counts(rows(season)["careerstats"]) == [162, 629, 166, 22, 86, 102, 37]
