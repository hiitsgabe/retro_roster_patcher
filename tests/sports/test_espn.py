"""ESPN client against a recorded response. Never touches the network."""

import json

import pytest

from retro_roster_patcher.sports.espn import EspnClient

NHL_TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/teams"
SOCCER_ROSTER_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/teams/83/roster"


def _soccer_roster(*names):
    """Build a soccer roster body whose players are identifiable by name."""
    return json.dumps(
        {
            "athletes": [
                {
                    "id": 100 + i,
                    "displayName": name,
                    "position": {"name": "Midfielder"},
                    "jersey": str(i + 1),
                }
                for i, name in enumerate(names)
            ]
        }
    ).encode()


def _nhl_teams_body(*teams):
    """Build a teams body in ESPN's sports/leagues/teams envelope."""
    return json.dumps(
        {
            "sports": [
                {
                    "leagues": [
                        {
                            "teams": [
                                {"team": {"id": i + 1, "displayName": name, "abbreviation": code}}
                                for i, (code, name) in enumerate(teams)
                            ]
                        }
                    ]
                }
            ]
        }
    ).encode()


def _roster_transport(bodies):
    """Serve a soccer roster chosen by the league code in the requested URL."""

    def transport(url, headers, timeout):
        transport.calls.append(url)
        return bodies[url.split("/soccer/")[1].split("/")[0]]

    transport.calls = []
    return transport


def test_get_nhl_teams_parses_the_recorded_response(tmp_path, replay):
    # The fixture is a recorded response with fixed content, so every claim here is
    # exact rather than approximate. The `len(teams) >= 30` bound and the
    # `all(t.id ...)` / `all(t.name ...)` checks this replaces passed on any
    # non-empty value: they would have stayed green had every team parsed to the
    # wrong id and the wrong name, which is the failure that matters. Indexing
    # positionally pins the parsed order too.
    client = EspnClient(str(tmp_path), transport=replay("espn_nhl_teams.json"))
    teams = client.get_nhl_teams()

    assert len(teams) == 32

    bruins = teams[1]
    assert bruins.id == 1
    assert bruins.name == "Boston Bruins"
    assert bruins.code == "BOS"


def test_get_nhl_teams_truncates_the_two_fields_it_slices(tmp_path, replay):
    # `code` and `short_name` are the only fields `_parse_teams` transforms rather
    # than copies, and the fixture carries exactly one team past each limit: "UTAH"
    # is its only abbreviation longer than 3 characters, and "Golden Knights" its
    # only shortDisplayName longer than 12. Pinning any other team would assert the
    # slices without exercising them.
    client = EspnClient(str(tmp_path), transport=replay("espn_nhl_teams.json"))
    teams = client.get_nhl_teams()

    assert teams[27].code == "UTA"
    assert teams[29].short_name == "Golden Knigh"


def test_results_are_cached_so_the_second_call_skips_the_transport(tmp_path, replay):
    # Asserts the URL too: the replay transport ignores it, so without this nothing
    # in the suite would notice the base URL, sport segment, or path changing.
    transport = replay("espn_nhl_teams.json")
    client = EspnClient(str(tmp_path), transport=transport)
    client.get_nhl_teams()
    client.get_nhl_teams()

    assert transport.calls == [NHL_TEAMS_URL]


def test_a_squad_cached_for_one_league_is_not_served_for_another(tmp_path):
    # ESPN team ids are league-scoped, so the same id names a different club in
    # each competition. The league code has to reach the cache key or the second
    # league is handed the first league's roster and never issues its request.
    transport = _roster_transport(
        {
            "eng.1": _soccer_roster("Alpha One", "Alpha Two", "Alpha Three"),
            "esp.1": _soccer_roster("Beta One", "Beta Two", "Beta Three"),
        }
    )
    client = EspnClient(str(tmp_path), transport=transport)
    english = client.get_squad(83, league_code="eng.1")
    spanish = client.get_squad(83, league_code="esp.1")

    assert [p.name for p in english] == ["Alpha One", "Alpha Two", "Alpha Three"]
    assert [p.name for p in spanish] == ["Beta One", "Beta Two", "Beta Three"]
    assert transport.calls == [
        SOCCER_ROSTER_URL.format(code="eng.1"),
        SOCCER_ROSTER_URL.format(code="esp.1"),
    ]


def test_a_repeated_squad_request_is_served_from_the_cache(tmp_path):
    # The other half of the claim above: keying by league must not cost the cache.
    transport = _roster_transport({"eng.1": _soccer_roster("Alpha One", "Alpha Two")})
    client = EspnClient(str(tmp_path), transport=transport)
    first = client.get_squad(83, league_code="eng.1")
    second = client.get_squad(83, league_code="eng.1")

    assert [p.name for p in first] == ["Alpha One", "Alpha Two"]
    assert [p.name for p in second] == ["Alpha One", "Alpha Two"]
    assert transport.calls == [SOCCER_ROSTER_URL.format(code="eng.1")]


def test_a_transport_failure_yields_no_teams_rather_than_crashing(tmp_path):
    def failing(url, headers, timeout):
        raise OSError("no network")

    client = EspnClient(str(tmp_path), transport=failing)
    assert client.get_nhl_teams() == []


@pytest.mark.parametrize(
    "payload",
    [
        b'{"sports": []}',
        b'{"sports": [{"leagues": []}]}',
    ],
)
def test_a_truncated_teams_payload_yields_no_teams_rather_than_an_index_error(tmp_path, payload):
    # `sports[0]` and `leagues[0]` were indexed unguarded, so an empty list at
    # either level raised IndexError out of a method whose contract is to return
    # a list.
    def transport(url, headers, timeout):
        return payload

    client = EspnClient(str(tmp_path), transport=transport)
    assert client.get_nhl_teams() == []


def test_a_payload_that_parses_to_no_teams_is_not_cached(tmp_path):
    # A body carrying zero teams is still a truthy dict, so caching on the body
    # rather than on the parse saved it and served [] for every later call — the
    # failure outlived the run that caused it. Cache what parsed, not what arrived.
    bodies = [
        b'{"sports": [{"leagues": [{"teams": []}]}]}',
        _nhl_teams_body(("BOS", "Boston Bruins"), ("TOR", "Toronto Maple Leafs")),
    ]

    def transport(url, headers, timeout):
        transport.calls.append(url)
        return bodies[min(len(transport.calls) - 1, len(bodies) - 1)]

    transport.calls = []
    client = EspnClient(str(tmp_path), transport=transport)

    assert client.get_nhl_teams() == []
    assert [t.code for t in client.get_nhl_teams()] == ["BOS", "TOR"]
    assert transport.calls == [NHL_TEAMS_URL, NHL_TEAMS_URL]


def test_the_status_callback_reports_the_fetch(tmp_path, replay):
    seen = []
    client = EspnClient(
        str(tmp_path), on_status=seen.append, transport=replay("espn_nhl_teams.json")
    )
    client.get_nhl_teams()

    # No space after "Fetching": the path supplies the separator.
    assert seen == ["Fetching/nhl/teams..."]


def test_a_raising_status_callback_is_not_mistaken_for_a_failed_fetch(tmp_path, replay):
    # Only the request belongs inside the `try`. With the callback in there too, a
    # raising `on_status` was caught as if the fetch had failed and the caller got
    # an empty result — a caller bug silently downgraded to "no data". The
    # transport-failure test above pins the other half: a failing request is still
    # swallowed.
    def raising(message):
        raise RuntimeError("the progress UI went away")

    client = EspnClient(str(tmp_path), on_status=raising, transport=replay("espn_nhl_teams.json"))

    with pytest.raises(RuntimeError):
        client.get_nhl_teams()


# --- the transport seam (helpers live in conftest.py) ---

# Every public method that issues a request, with arguments that reach the wire.
# `get_squad` needs its league code passed: without one the client looks the league
# up in the on-disk cache, finds nothing, and returns before making a request.
NETWORK_CALLS = {
    "get_teams": ((2001,), {}),
    "get_squad": ((359,), {"league_code": "eng.1"}),
    "get_nhl_teams": ((), {}),
    "get_hockey_squad": ((1,), {}),
    "get_hockey_team_leaders": ((1,), {}),
    "get_mlb_teams": ((), {}),
    "get_baseball_squad": ((1,), {}),
    "get_baseball_team_leaders": ((1,), {}),
    "get_nba_teams": ((), {}),
    "get_basketball_squad": ((1,), {}),
    "get_basketball_team_leaders": ((1,), {}),
}

# Public members that answer from module constants and never make a request.
OFFLINE_MEMBERS = {"get_featured_leagues", "get_leagues", "get_player_stats"}


def test_the_leak_guard_covers_every_public_member(assert_public_members_are_classified):
    assert_public_members_are_classified(EspnClient, NETWORK_CALLS, OFFLINE_MEMBERS)


def test_no_call_site_falls_back_to_the_default_transport(tmp_path, assert_no_transport_leak):
    assert_no_transport_leak(EspnClient, NETWORK_CALLS, str(tmp_path))


# --- the leaders parse loop (`_extract_pid` and its caller) ---

# Every leaders test above this line drove `get_hockey_team_leaders` with `{}`,
# so `data.get("categories", [])` was empty and the loop body — the whole of the
# parse, including `_extract_pid` — never ran once. NHL94's `fetch` calls this
# method for the `espn` provider on every team, so the parse is on the live path.

LEADERS_URL = (
    "https://sports.core.api.espn.com/v2/sports/hockey/leagues/nhl"
    "/seasons/2026/types/2/teams/5/leaders"
)


def _leaders_body(categories):
    """Build a leaders body from `[(abbreviation, [(athlete, value), ...]), ...]`."""
    return json.dumps(
        {
            "categories": [
                {
                    "abbreviation": abbrev,
                    "leaders": [{"athlete": athlete, "value": value} for athlete, value in entries],
                }
                for abbrev, entries in categories
            ]
        }
    ).encode()


def _body_transport(body):
    def transport(url, headers, timeout):
        transport.calls.append(url)
        return body

    transport.calls = []
    return transport


def _leaders(tmp_path, categories):
    transport = _body_transport(_leaders_body(categories))
    client = EspnClient(str(tmp_path), transport=transport)
    return client.get_hockey_team_leaders(5), transport


def test_an_athlete_id_becomes_a_string_key(tmp_path):
    # ESPN sends the id as a JSON number here and as a string elsewhere; the stat
    # dict is keyed by str either way, and NHL94's mapper looks players up by the
    # string form.
    stats, _ = _leaders(tmp_path, [("G", [({"id": 4024123}, 26)])])
    assert stats == {"4024123": {"G": 26}}


def test_two_categories_merge_into_one_stat_dict_per_player(tmp_path):
    # The branch that reuses an existing `stats[pid]` instead of replacing it.
    # With one category it cannot be told from a plain assignment.
    stats, _ = _leaders(
        tmp_path,
        [("G", [({"id": 1}, 26)]), ("A", [({"id": 1}, 22)])],
    )
    assert stats == {"1": {"G": 26, "A": 22}}


def test_two_players_in_one_category_stay_separate(tmp_path):
    stats, _ = _leaders(tmp_path, [("G", [({"id": 1}, 26), ({"id": 2}, 9)])])
    assert stats == {"1": {"G": 26}, "2": {"G": 9}}


def test_an_athlete_given_only_as_a_reference_link_is_resolved(tmp_path):
    # The core API returns unexpanded `$ref` links for most athletes, so this is
    # the common shape and not the exotic one.
    ref = {"$ref": "http://sports.core.api.espn.com/v2/.../athletes/4024123?lang=en&region=us"}
    stats, _ = _leaders(tmp_path, [("G", [(ref, 26)])])
    assert stats == {"4024123": {"G": 26}}


def test_a_reference_link_with_no_query_string_still_resolves(tmp_path):
    ref = {"$ref": "http://sports.core.api.espn.com/v2/.../athletes/4024123"}
    stats, _ = _leaders(tmp_path, [("G", [(ref, 26)])])
    assert stats == {"4024123": {"G": 26}}


def test_an_explicit_id_wins_over_a_reference_link(tmp_path):
    ref = {"id": 7, "$ref": "http://example.invalid/athletes/4024123"}
    stats, _ = _leaders(tmp_path, [("G", [(ref, 26)])])
    assert stats == {"7": {"G": 26}}


def test_a_link_that_points_at_something_other_than_an_athlete_is_skipped(tmp_path):
    ref = {"$ref": "http://sports.core.api.espn.com/v2/.../teams/5?lang=en"}
    stats, _ = _leaders(tmp_path, [("G", [(ref, 26)])])
    assert stats == {}


def test_an_athlete_that_is_not_an_object_is_skipped(tmp_path):
    stats, _ = _leaders(tmp_path, [("G", [("4024123", 26)])])
    assert stats == {}


def test_an_athlete_with_neither_an_id_nor_a_link_is_skipped(tmp_path):
    stats, _ = _leaders(tmp_path, [("G", [({"displayName": "Someone"}, 26)])])
    assert stats == {}


def test_one_unusable_entry_does_not_cost_the_usable_one_beside_it(tmp_path):
    # `continue`, not `break`: a leaders list is one JSON array and a single
    # unresolvable athlete in it must not truncate the team's stats.
    stats, _ = _leaders(tmp_path, [("G", [({}, 26), ({"id": 2}, 9)])])
    assert stats == {"2": {"G": 9}}


def test_a_category_with_no_abbreviation_is_keyed_by_the_empty_string(tmp_path):
    body = json.dumps({"categories": [{"leaders": [{"athlete": {"id": 1}, "value": 3}]}]}).encode()
    client = EspnClient(str(tmp_path), transport=_body_transport(body))
    assert client.get_hockey_team_leaders(5) == {"1": {"": 3}}


def test_an_entry_with_no_value_is_recorded_as_zero(tmp_path):
    body = json.dumps({"categories": [{"abbreviation": "G", "leaders": [{"athlete": {"id": 1}}]}]})
    client = EspnClient(str(tmp_path), transport=_body_transport(body.encode()))
    assert client.get_hockey_team_leaders(5) == {"1": {"G": 0}}


def test_the_leaders_request_names_the_season_and_the_team(tmp_path):
    _, transport = _leaders(tmp_path, [("G", [({"id": 1}, 26)])])
    assert transport.calls == [LEADERS_URL]


def test_a_second_call_is_served_from_the_parsed_stats_on_disk(tmp_path):
    # What is cached is the parsed `stats`, not the body — so a cache hit skips
    # `_extract_pid` entirely and must still answer with the same dict.
    transport = _body_transport(_leaders_body([("G", [({"id": 1}, 26)])]))
    client = EspnClient(str(tmp_path), transport=transport)
    client.get_hockey_team_leaders(5)
    second = client.get_hockey_team_leaders(5)
    assert second == {"1": {"G": 26}}
    assert transport.calls == [LEADERS_URL]


def test_a_payload_that_parses_to_no_stats_is_asked_for_again(tmp_path):
    transport = _body_transport(_leaders_body([("G", [({}, 26)])]))
    client = EspnClient(str(tmp_path), transport=transport)
    client.get_hockey_team_leaders(5)
    client.get_hockey_team_leaders(5)
    assert transport.calls == [LEADERS_URL, LEADERS_URL]


def test_a_payload_that_parses_to_no_stats_leaves_no_file_behind(tmp_path):
    # `if stats:`. Dropping that guard is invisible from the return value — an
    # empty dict is falsy, so `_load_cache`'s `if cached:` rejects it and the next
    # call goes to the wire either way. The file itself is the only trace, so the
    # file is what this asserts.
    body = _leaders_body([("G", [({}, 26)])])
    client = EspnClient(str(tmp_path), transport=_body_transport(body))
    client.get_hockey_team_leaders(5)
    assert sorted(p.name for p in tmp_path.iterdir()) == []


def test_an_athlete_sent_as_a_list_is_skipped_rather_than_crashing(tmp_path):
    # The `isinstance(athlete, dict)` guard, pinned against something that is not
    # a string: a guard written as "not a str" would let this through and then
    # raise `AttributeError` on `.get` in the middle of a team's stats.
    stats, _ = _leaders(tmp_path, [("G", [([], 26)])])
    assert stats == {}


def test_an_athlete_sent_as_null_is_skipped_rather_than_crashing(tmp_path):
    body = json.dumps(
        {"categories": [{"abbreviation": "G", "leaders": [{"athlete": None, "value": 26}]}]}
    ).encode()
    client = EspnClient(str(tmp_path), transport=_body_transport(body))
    assert client.get_hockey_team_leaders(5) == {}
