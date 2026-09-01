"""Round-tripping rosters through the CSV editing format.

`CsvHandler` has no consumer in `src/` — nothing outside its own module refers
to it — so it is a facility for a caller that wants to hand-edit a roster, not a
step in `patch`. The import side still has to survive whatever a spreadsheet
produces, which is where the interesting cases are: a header that omits a
column, a row that stops short of the header, and a cell that is present and
empty. All three are absences and all three used to behave differently.
"""

import pytest

from retro_roster_patcher.games.we2002.csv_handler import COLUMNS, CsvHandler
from retro_roster_patcher.games.we2002.models import WEPlayerAttributes, WEPlayerRecord


def _write(tmp_path, text):
    path = tmp_path / "league.csv"
    path.write_text(text)
    return path


def test_a_full_row_is_read_into_the_record_it_names(tmp_path):
    # The baseline the absence cases below are measured against: every value
    # here is distinct from the 5 that the defaults use, so a row that silently
    # fell back to defaults would fail this rather than pass it.
    header = ",".join(COLUMNS)
    values = "Utd,John Smith,FW,9,1,2,3,4,6,7,8,9,1,2,3,4,5,6,7"
    path = _write(tmp_path, f"{header}\n{values}\n")

    teams = CsvHandler().import_league(str(path))

    assert [name for name, _ in teams] == ["Utd"]
    player = teams[0][1][0]
    assert player.first_name == "John"
    assert player.last_name == "Smith"
    assert player.position == 3
    assert player.shirt_number == 9
    assert player.attributes.offensive == 1
    assert player.attributes.defensive == 2
    assert player.attributes.aggression == 7


def test_a_header_without_the_attribute_columns_falls_back_to_the_defaults(tmp_path):
    # `csv.DictReader` simply has no key for a column the header never named, so
    # `dict.get`'s default answers. This case always worked, and it is the reason
    # the defaults are not dead code.
    path = _write(tmp_path, "team_name,player_name\nUtd,John Smith\n")

    teams = CsvHandler().import_league(str(path))

    player = teams[0][1][0]
    assert player.attributes == WEPlayerAttributes()
    assert player.attributes.offensive == 5
    assert player.shirt_number == 0
    assert player.position == 2


def test_a_row_shorter_than_the_header_falls_back_to_the_defaults(tmp_path):
    # `DictReader` fills the missing keys with `restval`, which is `None`, so the
    # key exists and `dict.get`'s default never fires. `int(None)` raised
    # `TypeError` straight out of `import_league`.
    header = ",".join(COLUMNS)
    path = _write(tmp_path, f"{header}\nUtd,John Smith,FW,9\n")

    teams = CsvHandler().import_league(str(path))

    player = teams[0][1][0]
    assert player.shirt_number == 9
    assert player.position == 3
    assert player.attributes == WEPlayerAttributes()


def test_an_empty_cell_falls_back_to_the_default(tmp_path):
    # The third absence: the cell is there and holds the empty string, which is
    # what a spreadsheet writes for a blank. `int("")` raised `ValueError`.
    header = ",".join(COLUMNS)
    # `number` and `cur` are blank; every other value is present and none of
    # them is 5, so a fallback that swallowed the whole row would show.
    path = _write(tmp_path, f"{header}\nUtd,John Smith,FW,,1,2,3,4,6,7,8,9,1,2,3,4,8,,7\n")

    teams = CsvHandler().import_league(str(path))

    player = teams[0][1][0]
    assert player.shirt_number == 0
    assert player.attributes.offensive == 1
    assert player.attributes.curve == 5
    # `cur`'s neighbours in the header order, neither of them defaulted.
    assert player.attributes.dribble == 8
    assert player.attributes.aggression == 7


def test_a_cell_that_is_not_a_number_is_still_an_error(tmp_path):
    # Only absence gets a default. A value that is present and unreadable is bad
    # data, and silently rating that player 5 would hide the typo.
    header = ",".join(COLUMNS)
    path = _write(tmp_path, f"{header}\nUtd,John Smith,FW,9,high,2,3,4,6,7,8,9,1,2,3,4,5,6,7\n")

    with pytest.raises(ValueError, match="high"):
        CsvHandler().import_league(str(path))


def test_players_are_grouped_under_their_team_in_first_appearance_order(tmp_path):
    # Three players over two teams, interleaved: a grouping that keyed on the
    # row order rather than the team name would produce three groups.
    header = ",".join(COLUMNS)
    rows = "\n".join(
        [
            "Utd,John Smith,FW,9,1,2,3,4,6,7,8,9,1,2,3,4,5,6,7",
            "City,Ann Jones,GK,1,1,2,3,4,6,7,8,9,1,2,3,4,5,6,7",
            "Utd,Bo Kim,DF,4,1,2,3,4,6,7,8,9,1,2,3,4,5,6,7",
        ]
    )
    path = _write(tmp_path, f"{header}\n{rows}\n")

    teams = CsvHandler().import_league(str(path))

    assert [name for name, _ in teams] == ["Utd", "City"]
    assert [p.last_name for p in teams[0][1]] == ["Smith", "Kim"]
    assert [p.last_name for p in teams[1][1]] == ["Jones"]


def test_a_single_word_name_becomes_the_surname_with_no_forename(tmp_path):
    path = _write(tmp_path, "team_name,player_name\nUtd,Ronaldinho\n")

    teams = CsvHandler().import_league(str(path))

    player = teams[0][1][0]
    assert player.last_name == "Ronaldinho"
    assert player.first_name == ""


def test_an_exported_league_reads_back_as_the_records_it_was_given(tmp_path):
    # The two halves are each other's inverse for everything the format carries.
    # `league_name` is not one of those things — it is documented as "for
    # reference only" and no column holds it — so it cannot come back.
    attrs = WEPlayerAttributes(offensive=9, defensive=1, aggression=7, curve=3)
    players = [
        WEPlayerRecord(
            last_name="Smith", first_name="John", position=3, shirt_number=9, attributes=attrs
        ),
        WEPlayerRecord(last_name="Kim", first_name="Bo", position=0, shirt_number=1),
    ]
    path = tmp_path / "out.csv"

    CsvHandler().export_league("Premier League", [("Utd", players)], str(path))
    teams = CsvHandler().import_league(str(path))

    assert [name for name, _ in teams] == ["Utd"]
    assert teams[0][1] == players
