"""API-Football client behaviour, offline. Never touches the network.

Unlike the ESPN and NHL suites there is no recorded fixture here: API-Football
authenticates every request with a real key, so nothing can be recorded without
committing one. The tests drive hand-written synthetic bodies instead, the same
way `test_nhl.py` drives club-stats.
"""

import json
import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import pytest

from retro_roster_patcher.sports.api_football import (
    ApiFootballClient,
    DailyLimitError,
    RateLimitError,
    SeasonNotAvailableError,
)
from retro_roster_patcher.sports.models import League, Player, PlayerStats, Team

BASE = "https://v3.football.api-sports.io"
LEAGUES_URL = f"{BASE}/leagues"
TEAMS_URL = f"{BASE}/teams?league=39&season=2024"
SQUAD_URL = f"{BASE}/players/squads?team=33"
PLAYER_STATS_URL = f"{BASE}/players?team=33&season=2024"

# Truthy enough that the client writes it to the cache; empty enough that the
# parsers have nothing to build. The cache tests only care about the key.
EMPTY_RESPONSE: dict[str, Any] = {"response": []}


class _Transport:
    """Yields each payload in turn, repeating the last, and logs the URLs asked for.

    `calls` is the whole point — pinning the URL list is what catches a wrong path
    or a collapsed cache key. A class rather than a function carrying the list as
    a bolted-on attribute, which is what `conftest.replay` does: that pattern is
    fine while the function stays unannotated, but mypy rejects
    `transport.calls = calls` once the function has a signature, and this one
    needs one to accept `*payloads`. CI's mypy would not notice either way —
    `pyproject.toml` sets `files = ["src"]`, so it never reads the tests — but the
    `mypy src tests` run locally does.
    """

    def __init__(self, *payloads: Any) -> None:
        self._bodies = [json.dumps(payload).encode() for payload in payloads]
        self.calls: list[str] = []

    def __call__(self, url: str, headers: Mapping[str, str], timeout: float) -> bytes:
        self.calls.append(url)
        return self._bodies[min(len(self.calls) - 1, len(self._bodies) - 1)]


@pytest.fixture
def slept(monkeypatch):
    """Replace the retry sleep and record the intervals it was asked for.

    Without this a single rate-limited request blocks for RATE_LIMIT_WAIT seconds
    per attempt, three attempts deep. Recording rather than discarding the
    intervals costs nothing and lets one test pin the window itself.
    """
    recorded: list[float] = []
    monkeypatch.setattr(ApiFootballClient, "_sleep", staticmethod(recorded.append))
    return recorded


def test_the_api_key_is_sent_as_a_header(tmp_path):
    # Header, not query param: _http.get_json quotes the full URL into ApiError
    # messages, so a key that moved into the query string would land in any log.
    seen = {}

    def transport(url, headers, timeout):
        seen.update(headers)
        return b'{"response": []}'

    client = ApiFootballClient(api_key="secret", cache_dir=str(tmp_path), transport=transport)
    client._request("/teams", {"league": 39, "season": 2024})

    assert seen["x-apisports-key"] == "secret"


def test_params_reach_the_url(tmp_path):
    transport = _Transport(EMPTY_RESPONSE)
    client = ApiFootballClient(api_key="k", cache_dir=str(tmp_path), transport=transport)
    client._request("/teams", {"league": 39, "season": 2024})

    assert transport.calls == [TEAMS_URL]


def test_a_rate_limit_is_retried_and_then_succeeds(tmp_path, slept):
    transport = _Transport(
        {"errors": {"rateLimit": "too many"}},
        {"errors": {}, "response": [{"id": 1}]},
    )
    client = ApiFootballClient(api_key="k", cache_dir=str(tmp_path), transport=transport)

    data = client._request("/teams", {})

    assert data["response"] == [{"id": 1}]
    assert len(transport.calls) == 2


def test_the_retry_waits_the_whole_window_in_steps_and_reports_it(tmp_path, slept):
    transport = _Transport({"errors": {"rateLimit": "too many"}}, EMPTY_RESPONSE)
    seen: list[str] = []
    client = ApiFootballClient(
        api_key="k", cache_dir=str(tmp_path), on_status=seen.append, transport=transport
    )

    client._request("/teams", {})

    # 13 five-second steps == RATE_LIMIT_WAIT == 65s. Nothing else pins the wait:
    # the fixture makes sleeping free, so a window shortened to a value that would
    # not clear the provider's quota would otherwise pass silently.
    assert slept == [5] * 13
    assert sum(slept) == ApiFootballClient.RATE_LIMIT_WAIT
    # Counts down rather than up, and the first message quotes the full window.
    assert seen[0] == "Rate limited — retrying in 65s..."
    assert seen[-1] == "Rate limited — retrying in 5s..."


def test_the_sleep_seam_defaults_to_really_sleeping():
    # Every other rate-limit test replaces _sleep, so all of them stay green if the
    # production default is neutered. The seam exists to make the wait testable,
    # not to remove it: without a real sleep the "retry" is three requests fired
    # back to back at a provider that has just said stop.
    assert ApiFootballClient._sleep is time.sleep


def test_a_persistent_rate_limit_raises_after_the_retries_are_spent(tmp_path, slept):
    transport = _Transport({"errors": {"rateLimit": "too many"}})
    client = ApiFootballClient(api_key="k", cache_dir=str(tmp_path), transport=transport)

    with pytest.raises(RateLimitError):
        client._request("/teams", {})

    # The initial request plus three retries. Asserted because `raises` alone
    # passes just as happily on a client that gave up without retrying at all.
    assert len(transport.calls) == 4


def test_the_daily_limit_raises_immediately(tmp_path, slept):
    transport = _Transport({"errors": {"requests": "daily limit reached"}})
    client = ApiFootballClient(api_key="k", cache_dir=str(tmp_path), transport=transport)

    with pytest.raises(DailyLimitError):
        client._request("/teams", {})

    # A daily limit is not retried: the quota does not come back within the window.
    assert len(transport.calls) == 1
    assert slept == []


def test_a_rate_limit_beats_a_daily_limit_when_a_body_carries_both(tmp_path, slept):
    # The two checks are separate blocks in _request and the order between them is
    # a real decision: hoisting the daily-limit block would turn every combined
    # body into an immediate hard failure instead of a retry. Nothing about
    # raising DailyLimitError here would look wrong on its own.
    transport = _Transport({"errors": {"rateLimit": "too many", "requests": "daily limit"}})
    client = ApiFootballClient(api_key="k", cache_dir=str(tmp_path), transport=transport)

    with pytest.raises(RateLimitError):
        client._request("/teams", {})

    assert len(transport.calls) == 4


def test_a_free_plan_restriction_raises_season_not_available(tmp_path):
    client = ApiFootballClient(api_key="k", cache_dir=str(tmp_path))
    with pytest.raises(SeasonNotAvailableError) as excinfo:
        client._check_plan_error(
            {"errors": {"plan": "Free plans do not have access to this season"}}, 1998
        )

    # The season is carried on the exception so the caller can say which one failed.
    assert excinfo.value.season == 1998


# A plan restriction is what a free key hitting a historical season actually gets
# back. The body below is the whole response — API-Football answers 200 with the
# error in the envelope, so nothing raises on its own.
PLAN_RESTRICTED: dict[str, Any] = {
    "errors": {"plan": "Free plans do not have access to this season"}
}


def test_get_leagues_surfaces_a_plan_restriction(tmp_path):
    # Drives the public path, not _check_plan_error directly: the call site in
    # get_leagues can be deleted outright without the direct test noticing, and
    # the caller would then see an empty league list instead of being told the
    # key cannot reach 1998.
    client = ApiFootballClient(
        api_key="k", cache_dir=str(tmp_path), transport=_Transport(PLAN_RESTRICTED)
    )

    with pytest.raises(SeasonNotAvailableError) as excinfo:
        client.get_leagues(season=1998)

    assert excinfo.value.season == 1998


def test_get_teams_surfaces_a_plan_restriction(tmp_path):
    client = ApiFootballClient(
        api_key="k", cache_dir=str(tmp_path), transport=_Transport(PLAN_RESTRICTED)
    )

    with pytest.raises(SeasonNotAvailableError) as excinfo:
        client.get_teams(39, 1998)

    assert excinfo.value.season == 1998


def test_a_plan_restricted_squad_comes_back_empty_rather_than_raising(tmp_path):
    # Pinning a known defect in the ported source, not endorsing it: get_squad and
    # get_player_stats never call _check_plan_error, so the same body that raises
    # for leagues and teams is indistinguishable here from a team with no players.
    # Reported upstream; deliberately not fixed as part of a mechanical port.
    client = ApiFootballClient(
        api_key="k", cache_dir=str(tmp_path), transport=_Transport(PLAN_RESTRICTED)
    )

    assert client.get_squad(33) == []
    assert client.get_player_stats(33, 1998) == []


def test_a_transport_failure_yields_an_empty_dict(tmp_path):
    def failing(url, headers, timeout):
        raise OSError("no network")

    client = ApiFootballClient(api_key="k", cache_dir=str(tmp_path), transport=failing)
    assert client._request("/teams", {}) == {}


# --- parsers ---
#
# Driven through the public methods rather than by poking the _parse_* helpers, so
# each test covers the wiring as well as the transform. Assertions are whole-object
# equalities: a truthiness check like `all(p.name for p in players)` passes just as
# happily on a blanked field that fell through to a non-empty default, or on a
# first/last name swap, which is the failure mode that produces plausible-looking
# wrong data rather than an obvious crash.


# Two seasons, requested-first. The ordering is load-bearing in both directions:
# the season-filter test below asks for 2024, which is *not* `seasons[-1]`, so a
# parser that ignored the filter and took the last entry fails; and the no-season
# test asks for nothing, where the fallback deliberately does take `seasons[-1]`
# and so must yield 2023. Swapping these two dicts makes both tests pass against
# parsers they exist to reject.
LEAGUES_BODY: dict[str, Any] = {
    "response": [
        {
            "league": {"id": 39, "name": "Premier League", "logo": "pl.png"},
            "country": {"name": "England", "code": "GB"},
            "seasons": [
                {"year": 2024, "statistics": {"teams": 20}},
                {"year": 2023, "statistics": {"teams": 18}},
            ],
        }
    ]
}


def test_get_leagues_takes_the_statistics_of_the_requested_season(tmp_path):
    client = ApiFootballClient(
        api_key="k", cache_dir=str(tmp_path), transport=_Transport(LEAGUES_BODY)
    )

    assert client.get_leagues(season=2024) == [
        League(
            id=39,
            name="Premier League",
            country="England",
            country_code="GB",
            logo_url="pl.png",
            season=2024,
            teams_count=20,
        )
    ]


def test_get_leagues_without_a_season_falls_back_to_the_last_one_listed(tmp_path):
    # The only caller that reaches `if not used_season and seasons`. Without it the
    # season silently comes back 0, which is not a season any endpoint accepts, and
    # teams_count stays 0 because no filter matched.
    client = ApiFootballClient(
        api_key="k", cache_dir=str(tmp_path), transport=_Transport(LEAGUES_BODY)
    )

    assert client.get_leagues() == [
        League(
            id=39,
            name="Premier League",
            country="England",
            country_code="GB",
            logo_url="pl.png",
            season=2023,
            teams_count=0,
        )
    ]


def test_get_teams_truncates_the_short_name_and_code_to_the_rom_field_widths(tmp_path):
    # The name overruns 12 characters and the code overruns 3 on purpose. Both cuts
    # are ROM field widths, and a widened cut is invisible until the patcher writes
    # past the field — the same hazard test_nhl.py pins with "Golden Knigh".
    body = {
        "response": [
            {
                "team": {"id": 33, "name": "Manchester United", "code": "MANU", "logo": "mu.png"},
                "venue": {"city": "Manchester"},
            }
        ]
    }
    client = ApiFootballClient(api_key="k", cache_dir=str(tmp_path), transport=_Transport(body))

    assert client.get_teams(39, 2024) == [
        Team(
            id=33,
            name="Manchester United",
            short_name="Manchester U",
            code="MAN",
            logo_url="mu.png",
            country="Manchester",
        )
    ]


def test_get_squad_maps_positions_and_keeps_the_name_halves_apart(tmp_path):
    body = {
        "response": [
            {
                "players": [
                    {
                        "id": 1,
                        "name": "David de Gea",
                        "firstname": "David",
                        "lastname": "de Gea",
                        "age": 33,
                        "nationality": "Spain",
                        "position": "Goalkeeper",
                        "number": 1,
                        "photo": "1.png",
                    },
                    {
                        # "Winger" is not one of the three names the parser matches,
                        # so it exercises the Attacker fallback rather than a branch.
                        "id": 2,
                        "name": "Marcus Rashford",
                        "firstname": "Marcus",
                        "lastname": "Rashford",
                        "age": 26,
                        "nationality": "England",
                        "position": "Winger",
                        "number": 10,
                        "photo": "10.png",
                    },
                    {
                        # A sparse entry, which is what a fringe player actually
                        # looks like: no age, no squad number, and a JSON null for
                        # firstname rather than a missing key.
                        "id": 3,
                        "name": "Kobbie Mainoo",
                        "firstname": None,
                        "lastname": "Mainoo",
                        "nationality": "England",
                        "position": "Midfielder",
                        "photo": "3.png",
                    },
                ]
            }
        ]
    }
    client = ApiFootballClient(api_key="k", cache_dir=str(tmp_path), transport=_Transport(body))

    # Both players carry distinct first and last names, so a swap fails rather than
    # producing something that still reads like a name.
    assert client.get_squad(33) == [
        Player(
            id=1,
            name="David de Gea",
            first_name="David",
            last_name="de Gea",
            age=33,
            nationality="Spain",
            position="Goalkeeper",
            number=1,
            photo_url="1.png",
        ),
        Player(
            id=2,
            name="Marcus Rashford",
            first_name="Marcus",
            last_name="Rashford",
            age=26,
            nationality="England",
            position="Attacker",
            number=10,
            photo_url="10.png",
        ),
        # age falls through to the parser's 25 default, number to None, and
        # first_name to "" — the `or ""` is what stops a JSON null landing in a
        # str field, where it would travel unnoticed until the patcher formats it.
        Player(
            id=3,
            name="Kobbie Mainoo",
            first_name="",
            last_name="Mainoo",
            age=25,
            nationality="England",
            position="Midfielder",
            number=None,
            photo_url="3.png",
        ),
    ]


def test_get_player_stats_parses_the_first_statistics_entry(tmp_path):
    body = {
        "response": [
            {
                "player": {"id": 1},
                "statistics": [
                    {
                        # "appearences" is API-Football's own misspelling and it is
                        # load-bearing: correcting it to "appearances" here or in
                        # the parser silently zeroes every appearance count.
                        "games": {
                            "appearences": 35,
                            "minutes": 3050,
                            "rating": "7.4",
                            "lineups": 34,
                        },
                        "goals": {"total": 18, "assists": 7},
                        "shots": {"total": 60, "on": 30},
                        # accuracy arrives as a string, like rating: an int here
                        # would compare equal to the expected float either way and
                        # the coercion would be invisible.
                        "passes": {"total": 900, "accuracy": "84"},
                        "tackles": {"total": 20, "interceptions": 8, "blocks": 3},
                        "duels": {"total": 200, "won": 110},
                        "dribbles": {"attempts": 90, "success": 45},
                        "fouls": {"committed": 15, "drawn": 25},
                        "cards": {"yellow": 4, "red": 1},
                    },
                    # A second entry, which the parser must ignore: players loaned
                    # mid-season carry one per competition and the client documents
                    # taking the primary league only.
                    {"games": {"appearences": 99}, "goals": {"total": 99}},
                ],
            },
            # A player with no statistics at all — signed but never registered.
            # The parser skips the whole entry rather than emitting an all-zero
            # record, so an id=2 row appearing below means the skip is gone.
            {"player": {"id": 2}, "statistics": []},
            # A player registered but never played: every stat group missing.
            # rating is the one field that must come back None rather than 0.0,
            # because PlayerStats.rating is float | None and 0.0 would read as a
            # rated performance of zero rather than no performance at all.
            {"player": {"id": 3}, "statistics": [{"games": {}}]},
        ]
    }
    client = ApiFootballClient(api_key="k", cache_dir=str(tmp_path), transport=_Transport(body))

    # goals and assists differ, so the swap that would read fine either way fails.
    assert client.get_player_stats(33, 2024) == [
        PlayerStats(
            player_id=1,
            appearances=35,
            minutes=3050,
            goals=18,
            assists=7,
            shots_total=60,
            shots_on=30,
            passes_total=900,
            passes_accuracy=84.0,
            tackles_total=20,
            interceptions=8,
            blocks=3,
            duels_total=200,
            duels_won=110,
            dribbles_attempts=90,
            dribbles_success=45,
            fouls_committed=15,
            fouls_drawn=25,
            cards_yellow=4,
            cards_red=1,
            rating=7.4,
            lineups=34,
        ),
        PlayerStats(
            player_id=3,
            appearances=0,
            minutes=0,
            goals=0,
            assists=0,
            shots_total=0,
            shots_on=0,
            passes_total=0,
            passes_accuracy=0.0,
            tackles_total=0,
            interceptions=0,
            blocks=0,
            duels_total=0,
            duels_won=0,
            dribbles_attempts=0,
            dribbles_success=0,
            fouls_committed=0,
            fouls_drawn=0,
            cards_yellow=0,
            cards_red=0,
            rating=None,
            lineups=0,
        ),
    ]


# --- members that answer offline ---


def test_the_offline_members_answer_without_touching_the_transport(tmp_path):
    # Constructed with no transport at all, under the suite-wide autouse guard in
    # `tests/conftest.py`, which makes the default transport raise:
    # OFFLINE_MEMBERS is otherwise only ever compared as a set of names, so a
    # member listed there could start issuing requests with the suite still green.
    client = ApiFootballClient(api_key="k", cache_dir=str(tmp_path))

    assert client.get_team_logo_url(33) == "https://media.api-sports.io/football/teams/33.png"

    # Recomputed rather than hardcoded: the source reads datetime.now().year, so a
    # literal here would rot at the new year. The cross-year rule is the point —
    # the Champions League season is labelled by the year it started, and getting
    # it wrong asks the API for a season that has not finished.
    year = datetime.now().year
    assert client.get_featured_leagues() == [
        League(id=2, name="UEFA Champions League", country="World", season=year - 1),
        League(id=13, name="Copa Libertadores", country="South America", season=year),
        League(id=71, name="Brasileirao Serie A", country="Brazil", season=year),
        League(id=253, name="MLS", country="USA", season=year),
    ]


# --- cache keys ---
#
# All four cached endpoints build their key from their arguments. A key that drops
# one serves Arsenal's squad for Chelsea, or last season's teams for this one —
# wrong data, no error. Each test calls across distinct argument sets plus a
# repeat and asserts the exact URL list, which pins the key, the path, and the
# query string at once.


def test_each_leagues_filter_caches_separately(tmp_path):
    transport = _Transport(EMPTY_RESPONSE)
    client = ApiFootballClient(api_key="k", cache_dir=str(tmp_path), transport=transport)
    client.get_leagues()
    client.get_leagues(country="England")
    client.get_leagues(season=2024)
    client.get_leagues(id=39)
    client.get_leagues()

    assert transport.calls == [
        LEAGUES_URL,
        f"{LEAGUES_URL}?country=England",
        f"{LEAGUES_URL}?season=2024",
        f"{LEAGUES_URL}?id=39",
    ]


def test_each_teams_league_and_season_caches_separately(tmp_path):
    transport = _Transport(EMPTY_RESPONSE)
    client = ApiFootballClient(api_key="k", cache_dir=str(tmp_path), transport=transport)
    client.get_teams(39, 2024)
    client.get_teams(140, 2024)
    client.get_teams(39, 2023)
    client.get_teams(39, 2024)

    assert transport.calls == [
        TEAMS_URL,
        f"{BASE}/teams?league=140&season=2024",
        f"{BASE}/teams?league=39&season=2023",
    ]


def test_each_squad_team_caches_separately(tmp_path):
    transport = _Transport(EMPTY_RESPONSE)
    client = ApiFootballClient(api_key="k", cache_dir=str(tmp_path), transport=transport)
    client.get_squad(33)
    client.get_squad(34)
    client.get_squad(33)

    assert transport.calls == [SQUAD_URL, f"{BASE}/players/squads?team=34"]


def test_each_player_stats_team_and_season_caches_separately(tmp_path):
    transport = _Transport(EMPTY_RESPONSE)
    client = ApiFootballClient(api_key="k", cache_dir=str(tmp_path), transport=transport)
    client.get_player_stats(33, 2024)
    client.get_player_stats(34, 2024)
    client.get_player_stats(33, 2023)
    client.get_player_stats(33, 2024)

    assert transport.calls == [
        PLAYER_STATS_URL,
        f"{BASE}/players?team=34&season=2024",
        f"{BASE}/players?team=33&season=2023",
    ]


# --- the transport seam (helpers live in conftest.py) ---

# Every public method that issues a request, with arguments that reach the wire.
NETWORK_CALLS: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {
    "get_leagues": ((), {}),
    "get_teams": ((39, 2024), {}),
    "get_squad": ((33,), {}),
    "get_player_stats": ((33, 2024), {}),
}

# Public members that answer from class constants and never make a request.
# `_check_plan_error` and the three exception classes are not here: the helper
# scans `dir(ApiFootballClient)` for names without a leading underscore, so the
# former is out by name and the latter are module-level, not class members.
OFFLINE_MEMBERS = {
    "BASE_URL",
    "FEATURED_LEAGUES",
    "RATE_LIMIT_WAIT",
    "get_featured_leagues",
    "get_team_logo_url",
}


def test_the_leak_guard_covers_every_public_member(assert_public_members_are_classified):
    assert_public_members_are_classified(ApiFootballClient, NETWORK_CALLS, OFFLINE_MEMBERS)


def test_no_call_site_falls_back_to_the_default_transport(tmp_path, assert_no_transport_leak):
    urls = assert_no_transport_leak(ApiFootballClient, NETWORK_CALLS, "k", str(tmp_path))

    assert urls == [LEAGUES_URL, TEAMS_URL, SQUAD_URL, PLAYER_STATS_URL]
