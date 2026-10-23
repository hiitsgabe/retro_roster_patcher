"""Matching a player across providers, and filling photos from the second one."""

import json

from retro_roster_patcher.sports.fotmob import FotmobClient, photo_url
from retro_roster_patcher.sports.headshots import fill_headshots, match_squad
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


def test_json_round_trip_keeps_birth_date():
    from retro_roster_patcher.sports.serde import league_data_from_dict, league_data_to_dict

    data = _league(_p(1, "Hulk", "1986-07-25"))
    back = league_data_from_dict(json.loads(json.dumps(league_data_to_dict(data))))
    assert back.teams[0].players[0].birth_date == "1986-07-25"
