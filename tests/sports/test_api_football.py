"""API-Football rate-limit, cache-key, and URL behaviour. Never touches the network.

Unlike the ESPN and NHL suites there is no recorded fixture here: API-Football
authenticates every request with a real key, so nothing can be recorded without
committing one. The tests drive synthetic bodies instead, and target what makes
this client different from its siblings — the retry loop, the plan restriction,
and the four argument-derived cache keys.
"""

import json
from collections.abc import Mapping
from typing import Any

import pytest

from retro_roster_patcher.sports.api_football import (
    ApiFootballClient,
    DailyLimitError,
    RateLimitError,
    SeasonNotAvailableError,
)

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

    A class rather than a function with an attribute bolted on: mypy rejects
    `transport.calls = calls` on a function object, and `calls` is the whole point
    — pinning the URL list is what catches a wrong path or a collapsed cache key.
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


def test_a_free_plan_restriction_raises_season_not_available(tmp_path):
    client = ApiFootballClient(api_key="k", cache_dir=str(tmp_path))
    with pytest.raises(SeasonNotAvailableError) as excinfo:
        client._check_plan_error(
            {"errors": {"plan": "Free plans do not have access to this season"}}, 1998
        )

    # The season is carried on the exception so the caller can say which one failed.
    assert excinfo.value.season == 1998


def test_a_transport_failure_yields_an_empty_dict(tmp_path):
    def failing(url, headers, timeout):
        raise OSError("no network")

    client = ApiFootballClient(api_key="k", cache_dir=str(tmp_path), transport=failing)
    assert client._request("/teams", {}) == {}


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
