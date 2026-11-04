"""Matching a player across providers; photos and positions taken from the second one."""

import json

from retro_roster_patcher.sports.fotmob import FotmobClient, photo_url
from retro_roster_patcher.sports.headshots import fill_headshots, fotmob_positions, match_squad
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster
from tests.sports.test_fotmob import SQUAD, TABLE, _transport


def _p(id, name, born="", photo=""):
    return Player(id=id, name=name, birth_date=born, photo_url=photo)


def test_a_birth_date_that_is_unique_wins_over_a_different_name():
    found = match_squad([_p(1, "Reiner", "2002-01-01")], [_p(9, "Reinier Jesus", "2002-01-01")])
    assert found[1].id == 9


def test_a_shared_birth_date_is_settled_by_name():
    cands = [_p(8, "Pedro Lemos", "2000-05-05"), _p(9, "Pedro Fachineti", "2000-05-05")]
    assert match_squad([_p(1, "Pedro Fachinetti", "2000-05-05")], cands)[1].id == 9


def test_a_wrong_birth_date_still_finds_the_player_by_name():
    found = match_squad([_p(1, "Gutte", "1999-01-01")], [_p(9, "Gutte", "1999-01-02")])
    assert found[1].id == 9


def test_two_equally_close_names_match_nobody():
    cands = [_p(8, "Samuel Lema"), _p(9, "Samuel Lyma")]
    assert match_squad([_p(1, "Samuel Lima")], cands) == {}


def test_a_dissimilar_name_matches_nobody():
    assert match_squad([_p(1, "Ronaldo Gabriel")], [_p(9, "Gabriel Delfim")]) == {}


def test_a_candidate_is_given_to_one_player_only():
    found = match_squad(
        [_p(1, "Hulk", "1986-07-25"), _p(2, "Hulk", "")], [_p(9, "Hulk", "1986-07-25")]
    )
    assert list(found) == [1]


def _league(*players, team="Atlético-MG"):
    return LeagueData(
        league=League(id=2006, name="Brasileirao", season=2026),
        teams=[TeamRoster(team=Team(id=7632, name=team), players=list(players))],
    )


def _client(tmp_path):
    routes = {"/leagues?id=268": TABLE, "/teams?id=10272": SQUAD}
    return FotmobClient(str(tmp_path), transport=_transport(routes))


def test_fill_gives_photos_to_those_without_and_keeps_existing_ones(tmp_path):
    kept = _p(1, "Hulk", "1986-07-25", photo="https://espn/hulk.png")
    needy = _p(2, "Everson", "1990-07-22")
    data = _league(kept, needy)
    assert fill_headshots(data, _client(tmp_path)) == 1
    assert needy.photo_url == photo_url(268171)
    assert kept.photo_url == "https://espn/hulk.png"


def test_fill_matches_the_team_by_name_across_accents(tmp_path):
    data = _league(_p(2, "Everson", "1990-07-22"), team="Atletico MG")
    assert fill_headshots(data, _client(tmp_path)) == 1


def test_fill_leaves_an_unknown_team_alone_and_says_so(tmp_path):
    said = []
    data = _league(_p(2, "Everson", "1990-07-22"), team="Nobody FC")
    assert fill_headshots(data, _client(tmp_path), said.append) == 0
    assert any("Nobody FC" in m for m in said)


def test_fill_makes_no_request_when_every_player_has_a_photo(tmp_path):
    transport = _transport({})
    client = FotmobClient(str(tmp_path), transport=transport)
    assert fill_headshots(_league(_p(1, "Hulk", photo="x")), client) == 0
    assert transport.calls == []


def test_fill_reads_the_squad_of_the_leagues_season(tmp_path):
    # Keyed by season like the table: a squad cached for one season must not
    # give the next season's newcomers no photo.
    fill_headshots(_league(_p(2, "Everson", "1990-07-22")), _client(tmp_path))
    assert (tmp_path / "fotmob_squad_10272_2026.json").exists()
    assert not (tmp_path / "fotmob_squad_10272_any.json").exists()


def test_fotmob_positions_are_keyed_by_espn_id(tmp_path):
    squad = [_p(223953, "Éverson", "1990-07-22"), _p(55, "Hulk", "1986-07-25")]
    team = Team(id=7632, name="Atlético-MG", short_name="Atletico-MG")
    positions = fotmob_positions(team, 2006, squad, _client(tmp_path))
    assert positions == {223953: ["GK"], 55: ["ST", "LW"]}


def test_a_team_fotmob_does_not_list_has_no_positions(tmp_path):
    squad = [_p(55, "Hulk", "1986-07-25")]
    assert fotmob_positions(Team(id=1, name="Nowhere FC"), 2006, squad, _client(tmp_path)) == {}


def test_a_player_without_a_match_or_positions_is_left_out(tmp_path):
    members = [
        {"id": 7, "name": "Hulk de Souza", "dateOfBirth": "1986-07-25", "positionIdsDesc": "ST"},
        {"id": 8, "name": "Everson", "dateOfBirth": "1990-07-22"},
    ]
    squad_doc = {"squad": {"squad": [{"title": "attackers", "members": members}]}}
    transport = _transport({"/leagues?id=268": TABLE, "/teams?id=10272": squad_doc})
    squad = [
        _p(1, "Hulk", "1986-07-25"),  # matched, has positions
        _p(2, "Éverson", "1990-07-22"),  # matched, FotMob gives him none
        _p(3, "Stranger", "1999-09-09"),  # not on FotMob's squad
    ]
    team = Team(id=7632, name="Atlético-MG")
    client = FotmobClient(str(tmp_path), transport=transport)
    assert fotmob_positions(team, 2006, squad, client) == {1: ["ST"]}


def test_a_failed_squad_request_has_no_positions_and_is_not_repeated(tmp_path):
    transport = _transport({"/leagues?id=268": TABLE})  # no squad route, so that request fails
    client = FotmobClient(str(tmp_path), transport=transport)
    team = Team(id=7632, name="Atlético-MG")
    assert fotmob_positions(team, 2006, [_p(55, "Hulk", "1986-07-25")], client) == {}
    assert sum("/teams?id=10272" in url for url in transport.calls) == 1


def test_json_round_trip_keeps_birth_date():
    from retro_roster_patcher.sports.serde import league_data_from_dict, league_data_to_dict

    data = _league(_p(1, "Hulk", "1986-07-25"))
    back = league_data_from_dict(json.loads(json.dumps(league_data_to_dict(data))))
    assert back.teams[0].players[0].birth_date == "1986-07-25"
