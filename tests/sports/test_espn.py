"""ESPN client against a recorded response. Never touches the network."""

import inspect
import json
from dataclasses import fields
from datetime import datetime

import pytest

from retro_roster_patcher.games.we2002.stat_mapper import StatMapper
from retro_roster_patcher.sports import espn
from retro_roster_patcher.sports.api_football import ApiFootballClient
from retro_roster_patcher.sports.espn import EspnClient
from retro_roster_patcher.sports.models import PlayerStats
from tests.sports.conftest import FIXTURES

NHL_TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/teams"
SOCCER_ROSTER_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/teams/83/roster"
HOCKEY_ROSTER_URL = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/teams/5/roster"


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


def _hockey_roster(*names):
    """Build an NHL roster body in ESPN's position-grouped envelope."""
    return json.dumps(
        {
            "athletes": [
                {
                    "position": "Centers",
                    "items": [
                        {
                            "id": 200 + i,
                            "displayName": name,
                            "position": {"abbreviation": "C"},
                            "jersey": str(i + 1),
                        }
                        for i, name in enumerate(names)
                    ],
                }
            ]
        }
    ).encode()


def _grouped_roster(*names):
    """A roster body in the position-grouped envelope ESPN uses for MLB."""
    return json.dumps(
        {
            "athletes": [
                {
                    "position": "Pitchers",
                    "items": [
                        {"id": 400 + i, "displayName": name, "jersey": str(i + 1)}
                        for i, name in enumerate(names)
                    ],
                }
            ]
        }
    ).encode()


def _flat_roster(*names):
    """A roster body in the flat envelope ESPN uses for the NBA."""
    return json.dumps(
        {
            "athletes": [
                {"id": 500 + i, "displayName": name, "jersey": str(i + 1)}
                for i, name in enumerate(names)
            ]
        }
    ).encode()


def _sequence_transport(*bodies):
    """Serve each body in turn, repeating the last, and log the URLs asked for.

    `conftest.replay` serves one recorded body forever, which cannot tell a cache
    hit from a second identical response. These tests need consecutive requests
    to the same URL to differ, so that a cached answer is distinguishable from a
    re-fetched one by its content and not only by the call count.
    """

    def transport(url, headers, timeout):
        transport.calls.append(url)
        return bodies[min(len(transport.calls) - 1, len(bodies) - 1)]

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


# --- the season in the squad key ---
#
# The ESPN roster endpoints take no season and answer with the squad as it stands
# today, so the season reaches the cache key and nothing else. Without it the key
# has no time coordinate at all: nothing the caller can vary invalidates it, so
# the first fetch a user ever ran was replayed for every later season, with no
# network call, and reported as a success for the season that was asked for.
#
# Every test below drives two seasons whose correct answers differ, because a
# single season proves nothing about a key that ignores the season, and pairs
# them with a repeat of the first — a key that had grown *too* specific would
# re-request that one, and losing the cache is a real cost on a per-team endpoint
# a league fetch calls dozens of times.


def test_two_seasons_get_two_hockey_squads_and_a_repeat_gets_the_cache(tmp_path):
    transport = _sequence_transport(
        _hockey_roster("Mats Sundin"),
        _hockey_roster("Auston Matthews"),
    )
    client = EspnClient(str(tmp_path), transport=transport)

    first = client.get_hockey_squad(5, 2024)
    second = client.get_hockey_squad(5, 2026)
    repeat = client.get_hockey_squad(5, 2024)

    assert [p.name for p in first] == ["Mats Sundin"]
    assert [p.name for p in second] == ["Auston Matthews"]
    # Not "Auston Matthews": 2024 keeps its own answer rather than being
    # overwritten by the later season's.
    assert [p.name for p in repeat] == ["Mats Sundin"]
    # Two requests for three calls, and both to the same URL — the season is not
    # on the wire, only in the key. One request would be the collapsed key; three
    # would be no cache at all.
    assert transport.calls == [HOCKEY_ROSTER_URL, HOCKEY_ROSTER_URL]


def test_the_hockey_squad_key_names_the_season_on_disk(tmp_path):
    """The file name is the whole mechanism, so it is asserted directly.

    The test above would also pass for a key built from something else that
    happened to vary per call — a counter, or the request ordinal.
    """
    transport = _sequence_transport(_hockey_roster("Mats Sundin"))
    client = EspnClient(str(tmp_path), transport=transport)
    client.get_hockey_squad(5, 2024)

    assert sorted(f.name for f in tmp_path.iterdir()) == ["espn_hockey_squad_5_2024.json"]


def test_a_hockey_squad_asked_for_without_a_season_gets_its_own_key(tmp_path):
    """`season=None` is a bucket, not a wildcard.

    `get_hockey_squad` is on the public client surface with the season optional,
    so an omitted season must not be served a named season's answer, nor serve
    its own to one.
    """
    transport = _sequence_transport(
        _hockey_roster("No Season"),
        _hockey_roster("Season 2024"),
    )
    client = EspnClient(str(tmp_path), transport=transport)

    anonymous = client.get_hockey_squad(5)
    named = client.get_hockey_squad(5, 2024)

    assert [p.name for p in anonymous] == ["No Season"]
    assert [p.name for p in named] == ["Season 2024"]
    assert sorted(f.name for f in tmp_path.iterdir()) == [
        "espn_hockey_squad_5_2024.json",
        "espn_hockey_squad_5_any.json",
    ]


@pytest.mark.parametrize(
    ("method", "body", "stem"),
    [
        ("get_baseball_squad", _grouped_roster, "espn_baseball_squad"),
        ("get_basketball_squad", _flat_roster, "espn_basketball_squad"),
    ],
)
def test_the_other_two_sports_squad_keys_carry_the_season_too(tmp_path, method, body, stem):
    """The same fix on the two sports no registered game reaches yet.

    `EspnClient` is a public client, not private to NHL94: leaving two of its
    four squad methods keyed without a season would leave the next game that
    wants MLB or NBA rosters with the bug this round removed, and no test to
    notice. The bodies differ because the parsers do — baseball groups its
    athletes by role and basketball returns a flat list — so one shared fixture
    would silently parse to nothing for one of them.
    """
    transport = _sequence_transport(body("Alpha"), body("Beta"))
    client = EspnClient(str(tmp_path), transport=transport)

    first = getattr(client, method)(7, 2024)
    second = getattr(client, method)(7, 2023)
    repeat = getattr(client, method)(7, 2024)

    assert [p.name for p in first] == ["Alpha"]
    assert [p.name for p in second] == ["Beta"]
    assert [p.name for p in repeat] == ["Alpha"]
    assert sorted(f.name for f in tmp_path.iterdir()) == [
        f"{stem}_7_2023.json",
        f"{stem}_7_2024.json",
    ]


def test_two_seasons_get_two_soccer_squads_without_losing_the_league_code(tmp_path):
    """The season joins the league code in the key; it does not replace it.

    Two seasons and two league codes in one run, because the code was already
    load-bearing — ESPN team ids are league-scoped — and a key rebuilt around the
    season could have dropped it.
    """
    transport = _sequence_transport(
        _soccer_roster("Alpha 2024"),
        _soccer_roster("Alpha 2023"),
        _soccer_roster("Beta 2024"),
    )
    client = EspnClient(str(tmp_path), transport=transport)

    eng_2024 = client.get_squad(83, league_code="eng.1", season=2024)
    eng_2023 = client.get_squad(83, league_code="eng.1", season=2023)
    esp_2024 = client.get_squad(83, league_code="esp.1", season=2024)

    assert [p.name for p in eng_2024] == ["Alpha 2024"]
    assert [p.name for p in eng_2023] == ["Alpha 2023"]
    assert [p.name for p in esp_2024] == ["Beta 2024"]
    assert sorted(f.name for f in tmp_path.iterdir()) == [
        "espn_squad_eng.1_83_2023.json",
        "espn_squad_eng.1_83_2024.json",
        "espn_squad_esp.1_83_2024.json",
    ]


def test_the_season_get_teams_was_given_and_dropped_now_reaches_its_key(tmp_path):
    """`get_teams` always took a `season` and used it for nothing at all.

    Neither the request nor the key carried it, so a caller that asked for two
    seasons got one answer and no sign that its argument had been ignored.
    """
    transport = _sequence_transport(
        _nhl_teams_body(("BOS", "Boston Bruins")),
        _nhl_teams_body(("QUE", "Quebec Nordiques")),
    )
    client = EspnClient(str(tmp_path), transport=transport)

    modern = client.get_teams(2001, 2024)
    historical = client.get_teams(2001, 1994)

    assert [t.name for t in modern] == ["Boston Bruins"]
    assert [t.name for t in historical] == ["Quebec Nordiques"]
    assert sorted(f.name for f in tmp_path.iterdir()) == [
        "espn_teams_2001_1994.json",
        "espn_teams_2001_2024.json",
    ]


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
    # Reaches the wire for the leaders document; the empty body it gets back
    # names no athletes, so no per-athlete request follows.
    "get_player_stats": ((359, 2025), {"league_code": "eng.1"}),
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
OFFLINE_MEMBERS = {"get_featured_leagues", "get_leagues"}


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


# --- soccer per-player statistics, from ESPN's core API ---
#
# `get_player_stats` returned `[]` under the docstring "ESPN doesn't provide
# historical stats". It does: the same core API the three leaders methods above
# already use serves soccer, keyless, and `tests/fixtures/api/record.py` carries
# the two URLs these fixtures were recorded from. There is no bulk endpoint —
# `/athletes` on a team is a 404 — so it is the team's leaders document to
# enumerate the athletes, then one statistics document each.

SOCCER_LEADERS_URL = (
    "https://sports.core.api.espn.com/v2/sports/soccer/leagues/eng.1"
    "/seasons/2025/types/1/teams/364/leaders"
)

# The 25 athletes the recorded leaders document names, in first-seen order across
# its twelve categories. Spelled out because `get_player_stats` requests one
# document per athlete in this order, and the URL list is the only thing that can
# show it asked for the right ones.
RECORDED_ATHLETES = [
    304901,
    249524,
    173896,
    157892,
    257206,
    274632,
    303748,
    235662,
    249299,
    323110,
    234306,
    379588,
    251634,
    190257,
    104943,
    196876,
    281119,
    102053,
    250183,
    274742,
    152479,
    356075,
    194121,
    280806,
    414255,
]

# Which of ESPN's four statistics categories each field the parser reads lives
# in. The recorded document is the authority; this is what lets a synthetic body
# put a value where the parser will look for it.
_CATEGORY_OF = {
    "appearances": "general",
    "minutes": "general",
    "starts": "general",
    "passPct": "general",
    "foulsCommitted": "general",
    "foulsSuffered": "general",
    "yellowCards": "general",
    "redCards": "general",
    "avgRatingFromCorrespondent": "general",
    "avgRatingFromEditor": "general",
    "totalGoals": "offensive",
    "goalAssists": "offensive",
    "totalShots": "offensive",
    "shotsOnTarget": "offensive",
    "totalPasses": "offensive",
    "totalTackles": "defensive",
    "interceptions": "defensive",
    "blockedShots": "defensive",
}


def _soccer_stats_url(athlete_id, season=2025, team_id=364, code="eng.1"):
    return (
        f"https://sports.core.api.espn.com/v2/sports/soccer/leagues/{code}"
        f"/seasons/{season}/types/1/teams/{team_id}/athletes/{athlete_id}/statistics"
    )


def _soccer_leaders_body(*athlete_ids, categories=1):
    """A leaders body naming these athletes, by `$ref`, in every category."""
    return json.dumps(
        {
            "categories": [
                {
                    "abbreviation": f"C{c}",
                    "leaders": [
                        {
                            "athlete": {
                                "$ref": "http://sports.core.api.espn.com/v2/sports/soccer"
                                f"/leagues/eng.1/seasons/2025/athletes/{aid}?lang=en&region=us"
                            },
                            "value": 1.0,
                        }
                        for aid in athlete_ids
                    ],
                }
                for c in range(categories)
            ]
        }
    ).encode()


def _athlete_stats_body(**stats):
    """A statistics document in ESPN's `splits.categories[].stats[]` shape."""
    groups = {}
    for name, value in stats.items():
        groups.setdefault(_CATEGORY_OF[name], []).append({"name": name, "value": value})
    return json.dumps(
        {
            "splits": {
                "categories": [{"name": name, "stats": entries} for name, entries in groups.items()]
            }
        }
    ).encode()


def _soccer_transport(leaders_body, by_athlete, fallback=b"{}"):
    """Serve the leaders body for the leaders URL and a document per athlete."""

    def transport(url, headers, timeout):
        transport.calls.append(url)
        if url.endswith("/leaders"):
            return leaders_body
        athlete_id = int(url.split("/athletes/")[1].split("/")[0])
        return by_athlete.get(athlete_id, fallback)

    transport.calls = []
    return transport


def _recorded_transport():
    """Replay the two recorded documents: the real leaders list, one real athlete."""
    leaders = (FIXTURES / "espn_soccer_leaders.json").read_bytes()
    athlete = (FIXTURES / "espn_soccer_athlete_stats.json").read_bytes()

    def transport(url, headers, timeout):
        transport.calls.append(url)
        return leaders if url.endswith("/leaders") else athlete

    transport.calls = []
    return transport


# --- the recorded documents ---


def test_the_recorded_statistics_document_becomes_one_player_stats(tmp_path):
    # Field for field against the document `record.py` fetched, so that a
    # provider renaming `totalGoals` or moving it between categories fails here
    # rather than showing up as a league of strikers who never scored.
    client = EspnClient(str(tmp_path), transport=_recorded_transport())
    stats = client.get_player_stats(364, 2025, league_code="eng.1")

    assert stats[0].player_id == 304901
    assert stats[0].appearances == 28
    assert stats[0].minutes == 1810
    assert stats[0].lineups == 21
    assert stats[0].goals == 11
    assert stats[0].assists == 4
    assert stats[0].shots_total == 65
    assert stats[0].shots_on == 19
    assert stats[0].passes_total == 400
    assert stats[0].tackles_total == 11
    assert stats[0].interceptions == 3
    assert stats[0].blocks == 17
    assert stats[0].fouls_committed == 35
    assert stats[0].fouls_drawn == 12
    assert stats[0].cards_yellow == 0
    assert stats[0].cards_red == 0


def test_the_pass_percentage_arrives_as_a_fraction_and_is_stored_as_a_percentage(tmp_path):
    # `general.passPct` reads `0.768` in the recorded document where
    # `displayValue` says `"0.8"`; `PlayerStats.passes_accuracy` is declared a
    # percentage and API-Football fills it with one.
    #
    # This is the one field whose unit cannot be caught by any rating assertion:
    # `pass_accuracy` is percentiled league-wide, so scaling every player by the
    # same hundred leaves the ranking, and therefore every rating in the game,
    # exactly as it was. Only the concrete number shows it.
    client = EspnClient(str(tmp_path), transport=_recorded_transport())
    assert client.get_player_stats(364, 2025, league_code="eng.1")[0].passes_accuracy == 76.8


def test_the_recorded_document_leaves_the_rating_unset(tmp_path):
    # All four `avgRatingFrom*` fields read 0.0 for soccer. `api_football`
    # renders an absent rating as `None`, so this one does too rather than
    # claiming every player was rated zero out of ten.
    client = EspnClient(str(tmp_path), transport=_recorded_transport())
    assert client.get_player_stats(364, 2025, league_code="eng.1")[0].rating is None


def test_the_recorded_leaders_document_names_twenty_five_athletes_once_each(tmp_path):
    # Twelve categories of twenty-five entries name the same players over and
    # over. Without the deduplication this is 300 statistics requests a team.
    client = EspnClient(str(tmp_path), transport=_recorded_transport())
    assert client._soccer_stat_athletes(364, "eng.1", 2025) == RECORDED_ATHLETES


def test_the_requests_are_the_leaders_document_and_then_one_per_athlete(tmp_path):
    transport = _recorded_transport()
    client = EspnClient(str(tmp_path), transport=transport)
    client.get_player_stats(364, 2025, league_code="eng.1")

    assert transport.calls[0] == SOCCER_LEADERS_URL
    assert transport.calls[1] == _soccer_stats_url(304901)
    assert transport.calls == [SOCCER_LEADERS_URL] + [
        _soccer_stats_url(aid) for aid in RECORDED_ATHLETES
    ]


# --- what the records say they do not measure ---


def test_every_record_declares_the_four_stats_espn_never_reports(tmp_path):
    client = EspnClient(str(tmp_path), transport=_recorded_transport())
    stats = client.get_player_stats(364, 2025, league_code="eng.1")
    assert stats[0].unsupplied == (
        "duels_total",
        "duels_won",
        "dribbles_attempts",
        "dribbles_success",
    )
    assert {s.unsupplied for s in stats} == {stats[0].unsupplied}


def test_the_four_names_are_fields_that_exist_on_player_stats():
    # A misspelt name here matches no field, so the mapper goes on rating the
    # filler zero and the collapse it exists to prevent comes back silently.
    declared = {f.name for f in fields(PlayerStats)}
    assert (set(espn.SOCCER_UNSUPPLIED_STATS) - declared) == set()


def test_the_declared_absences_are_exactly_the_fields_left_at_zero(tmp_path):
    # The filler values and the declaration have to agree: a field named absent
    # but actually filled would throw a measurement away, and a field left at
    # zero and not named is the original bug.
    client = EspnClient(str(tmp_path), transport=_recorded_transport())
    stats = client.get_player_stats(364, 2025, league_code="eng.1")[0]
    assert stats.duels_total == 0
    assert stats.duels_won == 0
    assert stats.dribbles_attempts == 0
    assert stats.dribbles_success == 0


def test_the_client_and_the_mapper_agree_on_which_categories_are_orphaned():
    # The two constants are declared in different packages and neither imports
    # the other. This is what keeps them describing the same three attributes.
    orphaned = {
        category
        for category, inputs in StatMapper.CATEGORY_INPUTS.items()
        if set(inputs) & set(espn.SOCCER_UNSUPPLIED_STATS)
    }
    assert orphaned == {"body_balance", "technique", "dribble"}


# --- the records differ where the documents differ ---


def test_three_athletes_with_three_documents_get_three_different_records(tmp_path):
    # A fixture where every player scores the same cannot tell a working parser
    # from one that returns a constant, and the recorded fixture is a single
    # athlete replayed for all 25. These three differ in every field asserted.
    transport = _soccer_transport(
        _soccer_leaders_body(1, 2, 3),
        {
            1: _athlete_stats_body(
                appearances=30.0, minutes=2700.0, starts=30.0, totalGoals=24.0, passPct=0.83
            ),
            2: _athlete_stats_body(
                appearances=20.0, minutes=900.0, starts=8.0, totalGoals=3.0, passPct=0.71
            ),
            3: _athlete_stats_body(
                appearances=5.0, minutes=140.0, starts=1.0, totalGoals=0.0, passPct=0.6
            ),
        },
    )
    client = EspnClient(str(tmp_path), transport=transport)
    stats = client.get_player_stats(364, 2025, league_code="eng.1")

    assert [s.player_id for s in stats] == [1, 2, 3]
    assert [s.appearances for s in stats] == [30, 20, 5]
    assert [s.minutes for s in stats] == [2700, 900, 140]
    assert [s.lineups for s in stats] == [30, 8, 1]
    assert [s.goals for s in stats] == [24, 3, 0]
    assert [s.passes_accuracy for s in stats] == [83.0, 71.0, 60.0]


def test_a_populated_rating_field_is_kept(tmp_path):
    # The zero the recorded document carries is not a rule about the endpoint, so
    # a feed that starts filling one of the four is not discarded.
    transport = _soccer_transport(
        _soccer_leaders_body(1, 2),
        {
            1: _athlete_stats_body(appearances=10.0, avgRatingFromCorrespondent=7.4),
            2: _athlete_stats_body(appearances=10.0, avgRatingFromCorrespondent=0.0),
        },
    )
    client = EspnClient(str(tmp_path), transport=transport)
    stats = client.get_player_stats(364, 2025, league_code="eng.1")
    assert stats[0].rating == 7.4
    assert stats[1].rating is None


def test_a_later_rating_field_is_used_when_the_first_is_empty(tmp_path):
    transport = _soccer_transport(
        _soccer_leaders_body(1),
        {1: _athlete_stats_body(avgRatingFromCorrespondent=0.0, avgRatingFromEditor=6.9)},
    )
    client = EspnClient(str(tmp_path), transport=transport)
    assert client.get_player_stats(364, 2025, league_code="eng.1")[0].rating == 6.9


# --- documents that are not there or not usable ---


def test_an_athlete_whose_document_has_no_categories_yields_no_record(tmp_path):
    # A player with twenty zeroes is not the same as a player with no record:
    # `map_player` reads `appearances == 0` and falls back, but every other
    # consumer would see a full season of nothing.
    transport = _soccer_transport(
        _soccer_leaders_body(1, 2),
        {1: _athlete_stats_body(appearances=30.0, totalGoals=9.0)},
        fallback=json.dumps({"splits": {"categories": []}}).encode(),
    )
    client = EspnClient(str(tmp_path), transport=transport)
    stats = client.get_player_stats(364, 2025, league_code="eng.1")
    assert [s.player_id for s in stats] == [1]
    assert transport.calls == [
        SOCCER_LEADERS_URL,
        _soccer_stats_url(1),
        _soccer_stats_url(2),
    ]


def test_a_null_stat_value_costs_that_field_and_not_the_record(tmp_path):
    transport = _soccer_transport(
        _soccer_leaders_body(1),
        {1: _athlete_stats_body(appearances=30.0, minutes=None, totalGoals=9.0)},
    )
    client = EspnClient(str(tmp_path), transport=transport)
    stats = client.get_player_stats(364, 2025, league_code="eng.1")
    assert stats[0].minutes == 0
    assert stats[0].goals == 9


def test_a_leaders_document_that_fails_to_arrive_yields_no_stats(tmp_path):
    def failing(url, headers, timeout):
        raise OSError("connection reset")

    client = EspnClient(str(tmp_path), transport=failing)
    assert client.get_player_stats(364, 2025, league_code="eng.1") == []


def test_stats_for_a_team_in_no_known_league_are_not_requested(tmp_path):
    # No league code, and nothing cached to resolve one from, so there is no URL
    # to build. Asserted through the call log: returning `[]` after asking for a
    # malformed URL would look the same from the outside.
    transport = _soccer_transport(_soccer_leaders_body(1), {})
    client = EspnClient(str(tmp_path), transport=transport)
    assert client.get_player_stats(99999, 2025) == []
    assert transport.calls == []


# --- caching, which is what makes 500 requests a league viable ---


def test_a_second_fetch_of_the_same_team_and_season_makes_no_request(tmp_path):
    transport = _recorded_transport()
    client = EspnClient(str(tmp_path), transport=transport)
    first = client.get_player_stats(364, 2025, league_code="eng.1")
    calls_after_first = list(transport.calls)
    second = client.get_player_stats(364, 2025, league_code="eng.1")

    assert [s.player_id for s in second] == [s.player_id for s in first]
    assert transport.calls == calls_after_first


def test_two_seasons_get_two_sets_of_documents(tmp_path):
    # The season is in every key in this client, and these two answers differ, so
    # a key that dropped it would serve the 2024 record for the 2025 question and
    # report it as a success for 2025.
    transport = _soccer_transport(
        _soccer_leaders_body(1),
        {1: _athlete_stats_body(appearances=30.0, totalGoals=24.0)},
    )
    client = EspnClient(str(tmp_path), transport=transport)
    client.get_player_stats(364, 2025, league_code="eng.1")
    calls_2025 = list(transport.calls)
    client.get_player_stats(364, 2024, league_code="eng.1")

    assert calls_2025 == [SOCCER_LEADERS_URL, _soccer_stats_url(1, season=2025)]
    assert transport.calls[len(calls_2025) :] == [
        SOCCER_LEADERS_URL.replace("/seasons/2025/", "/seasons/2024/"),
        _soccer_stats_url(1, season=2024),
    ]


def test_the_cached_documents_name_the_league_the_team_and_the_season_on_disk(tmp_path):
    transport = _soccer_transport(
        _soccer_leaders_body(7),
        {7: _athlete_stats_body(appearances=30.0)},
    )
    client = EspnClient(str(tmp_path), transport=transport)
    client.get_player_stats(364, 2025, league_code="eng.1")

    assert (tmp_path / "espn_soccer_leaders_eng.1_364_2025.json").exists() is True
    assert (tmp_path / "espn_soccer_stats_eng.1_364_2025_7.json").exists() is True


def test_one_athlete_already_cached_is_not_requested_again(tmp_path):
    # Per athlete, not per team: a league fetch is ~500 requests and one that dies
    # partway must not throw away the work of the teams that finished.
    transport = _soccer_transport(
        _soccer_leaders_body(1, 2),
        {
            1: _athlete_stats_body(appearances=30.0, totalGoals=24.0),
            2: _athlete_stats_body(appearances=12.0, totalGoals=1.0),
        },
    )
    client = EspnClient(str(tmp_path), transport=transport)
    client._soccer_athlete_stats(364, "eng.1", 2025, 1)
    calls_before = list(transport.calls)
    stats = client.get_player_stats(364, 2025, league_code="eng.1")

    assert calls_before == [_soccer_stats_url(1)]
    assert [s.goals for s in stats] == [24, 1]
    assert transport.calls == calls_before + [SOCCER_LEADERS_URL, _soccer_stats_url(2)]


# --- resolving the league code from what `get_teams` cached ---


def test_a_squad_asked_for_without_a_league_code_resolves_it_from_the_cached_teams(tmp_path):
    # `WE2002Patcher.fetch` calls `get_teams` and then `get_squad`, and only the
    # first is told the league. This lookup is what carries the code across, and
    # it was reading `espn_teams_{id}` — a key nothing has written since the
    # season joined it — so it always answered `None`, and `get_squad` returned an
    # empty list without issuing a request or raising anything.
    def transport(url, headers, timeout):
        transport.calls.append(url)
        if url.endswith("/teams"):
            return _nhl_teams_body(("LIV", "Liverpool"), ("MUN", "Manchester United"))
        return _soccer_roster("Alpha One", "Alpha Two")

    transport.calls = []
    client = EspnClient(str(tmp_path), transport=transport)
    client.get_teams(2001, 2025)
    squad = client.get_squad(2, season=2025)

    assert [p.name for p in squad] == ["Alpha One", "Alpha Two"]
    assert transport.calls == [
        "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/teams",
        "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/teams/2/roster",
    ]


def test_the_teams_cached_for_one_season_do_not_resolve_another_seasons_squad(tmp_path):
    # The season is in the teams key for the same reason it is in every other
    # key, and a lookup that ignored it would answer a 2024 question from a 2025
    # cache — and resolve a team that changed competition to the wrong league.
    def transport(url, headers, timeout):
        transport.calls.append(url)
        return _nhl_teams_body(("LIV", "Liverpool"))

    transport.calls = []
    client = EspnClient(str(tmp_path), transport=transport)
    client.get_teams(2001, 2025)
    assert client.get_squad(1, season=2024) == []
    assert transport.calls == ["https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/teams"]


# --- the season on the league the caller is answered with ---


def test_a_league_carries_the_season_it_was_asked_about(tmp_path):
    # `WE2002Patcher.fetch` puts this object straight onto the `LeagueData` it
    # returns and `serde` writes it to the rosters file, so a `League` that
    # ignored the argument stamped every rosters file with the current calendar
    # year whatever season produced it.
    client = EspnClient(str(tmp_path))
    assert client.get_leagues(id=2001, season=2024)[0].season == 2024
    assert client.get_leagues(season=2024)[0].season == 2024


def test_a_league_asked_for_without_a_season_falls_back_to_the_calendar_year(tmp_path):
    # Which is what `get_featured_leagues` wants: a featured list has no season
    # in the question.
    client = EspnClient(str(tmp_path))
    assert client.get_leagues(id=2001)[0].season == datetime.now().year
    assert client.get_featured_leagues()[0].season == datetime.now().year


# --- the two soccer signatures against API-Football's ---


@pytest.mark.parametrize("method", ["get_squad", "get_player_stats"])
def test_a_soccer_method_is_a_positional_superset_of_api_footballs(method):
    # `WE2002Patcher.fetch` has one call site per method for both providers and
    # passes the season positionally. When `get_squad`'s second parameter was
    # `league_code`, the season landed in it, `"2024"` matched no league, and
    # every squad came back empty with nothing raised anywhere.
    #
    # Fixing the call site would have fixed that one line. Holding the ESPN
    # signature to API-Football's as a prefix makes the mistake unrepresentable,
    # and this is the assertion that keeps it true after `api_football` is
    # deleted and there is nothing left to compare against by hand.
    espn_params = list(inspect.signature(getattr(EspnClient, method)).parameters)
    football_params = list(inspect.signature(getattr(ApiFootballClient, method)).parameters)
    assert espn_params[: len(football_params)] == football_params
    assert espn_params[len(football_params) :] == ["league_code"]
