"""FotMob client against inline bodies. Never touches the network."""

import json

from retro_roster_patcher.sports.fotmob import FotmobClient, photo_url


def _transport(routes):
    """Serve the body whose key is a substring of the requested URL."""

    def transport(url, headers, timeout):
        transport.calls.append(url)
        for key, body in routes.items():
            if key in url:
                return json.dumps(body).encode()
        raise OSError(f"no route for {url}")

    transport.calls = []
    return transport


TABLE = {
    "table": [
        {
            "data": {
                "table": {
                    "all": [
                        {"id": 10272, "name": "Atlético-MG", "shortName": "Atlético-MG"},
                        {"id": 9770, "name": "Flamengo", "shortName": "Flamengo"},
                    ]
                }
            }
        }
    ]
}

SQUAD = {
    "squad": {
        "squad": [
            {"title": "coach", "members": [{"id": 1, "name": "Boss Man"}]},
            {
                "title": "keepers",
                "members": [
                    {
                        "id": 268171,
                        "name": "Everson",
                        "shirtNumber": 22,
                        "cname": "Brazil",
                        "age": 36,
                        "height": 180,
                        "dateOfBirth": "1990-07-22",
                        "goals": 0,
                        "ycards": 2,
                        "rating": {"num": "6.9"},
                    }
                ],
            },
            {
                "title": "attackers",
                "members": [
                    {"id": 7, "name": "Hulk de Souza", "dateOfBirth": "1986-07-25", "goals": 9}
                ],
            },
        ]
    }
}


def test_get_teams_reads_the_league_table(tmp_path):
    client = FotmobClient(str(tmp_path), transport=_transport({"/leagues?id=268": TABLE}))
    teams = client.get_teams(2006)
    assert [(t.id, t.name) for t in teams] == [(10272, "Atlético-MG"), (9770, "Flamengo")]


def test_get_teams_is_empty_for_an_unmapped_league_without_a_request(tmp_path):
    transport = _transport({})
    assert FotmobClient(str(tmp_path), transport=transport).get_teams(9999) == []
    assert transport.calls == []


def test_a_failed_request_is_an_empty_answer_and_is_not_cached(tmp_path):
    client = FotmobClient(str(tmp_path), transport=_transport({}))
    assert client.get_teams(2006) == []
    assert list(tmp_path.glob("fotmob_*")) == []


def test_get_squad_drops_coaches_and_maps_positions(tmp_path):
    client = FotmobClient(str(tmp_path), transport=_transport({"/teams?id=10272": SQUAD}))
    squad = client.get_squad(10272)
    assert [(p.name, p.position, p.number, p.birth_date) for p in squad] == [
        ("Everson", "Goalkeeper", 22, "1990-07-22"),
        ("Hulk de Souza", "Attacker", None, "1986-07-25"),
    ]
    assert squad[0].photo_url == photo_url(268171)
    assert squad[1].last_name == "Souza"


def test_a_squad_carries_height_in_inches_and_no_weight(tmp_path):
    client = FotmobClient(str(tmp_path), transport=_transport({"/teams?id=10272": SQUAD}))
    everson, hulk = client.get_squad(10272)
    assert (everson.height, everson.weight) == (70.9, 0.0)
    assert (hulk.height, hulk.weight) == (0.0, 0.0)


def test_squad_is_served_from_cache_the_second_time(tmp_path):
    transport = _transport({"/teams?id=10272": SQUAD})
    client = FotmobClient(str(tmp_path), transport=transport)
    client.get_squad(10272)
    client.get_squad(10272)
    assert len(transport.calls) == 1


def test_player_stats_carry_counts_and_name_the_filler(tmp_path):
    client = FotmobClient(str(tmp_path), transport=_transport({"/teams?id=10272": SQUAD}))
    everson, hulk = client.get_player_stats(10272)
    assert (everson.cards_yellow, everson.rating, hulk.goals) == (2, 6.9, 9)
    assert hulk.rating is None
    assert "minutes" in everson.unsupplied and "goals" not in everson.unsupplied


def test_search_teams_keeps_teams_and_dedupes(tmp_path):
    body = [
        {
            "suggestions": [
                {"type": "team", "id": "10272", "name": "Atlético-MG"},
                {"type": "match"},
            ]
        },
        {"suggestions": [{"type": "team", "id": "10272", "name": "Atlético-MG"}]},
    ]
    client = FotmobClient(str(tmp_path), transport=_transport({"/search/suggest": body}))
    assert [(t.id, t.name) for t in client.search_teams("atletico mg")] == [(10272, "Atlético-MG")]
