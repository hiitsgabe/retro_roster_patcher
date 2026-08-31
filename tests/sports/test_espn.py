"""ESPN client against a recorded response. Never touches the network."""

from retro_roster_patcher.sports import _http
from retro_roster_patcher.sports.espn import EspnClient

NHL_TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/teams"


def test_get_nhl_teams_parses_the_recorded_response(tmp_path, replay):
    client = EspnClient(str(tmp_path), transport=replay("espn_nhl_teams.json"))
    teams = client.get_nhl_teams()

    assert len(teams) >= 30
    codes = {t.code for t in teams}
    assert "BOS" in codes
    assert all(t.id for t in teams)
    assert all(t.name for t in teams)


def test_results_are_cached_so_the_second_call_skips_the_transport(tmp_path, replay):
    # Asserts the URL too: the replay transport ignores it, so without this nothing
    # in the suite would notice the base URL, sport segment, or path changing.
    calls = []
    recorded = replay("espn_nhl_teams.json")

    def counting_transport(url, headers, timeout):
        calls.append(url)
        return recorded(url, headers, timeout)

    client = EspnClient(str(tmp_path), transport=counting_transport)
    client.get_nhl_teams()
    client.get_nhl_teams()

    assert calls == [NHL_TEAMS_URL]


def test_a_transport_failure_yields_no_teams_rather_than_crashing(tmp_path):
    def failing(url, headers, timeout):
        raise OSError("no network")

    client = EspnClient(str(tmp_path), transport=failing)
    assert client.get_nhl_teams() == []


def test_the_status_callback_reports_the_fetch(tmp_path, replay):
    seen = []
    client = EspnClient(
        str(tmp_path), on_status=seen.append, transport=replay("espn_nhl_teams.json")
    )
    client.get_nhl_teams()

    # No space after "Fetching": the path supplies the separator.
    assert seen == ["Fetching/nhl/teams..."]


# --- the transport seam ---

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

# Public methods that answer from module constants and never make a request.
OFFLINE_METHODS = {"get_featured_leagues", "get_leagues", "get_player_stats"}


class _TransportLeak(BaseException):
    """Raised when a call site falls back to the real network transport.

    Deliberately not an `Exception`. Every call site wraps its request in
    `except Exception: return {}`, which would swallow an `AssertionError` and
    leave the guard below green while the leak it exists to catch went past.
    """


def test_the_leak_guard_covers_every_public_method():
    """Fails when a method is added to the client but to neither table above."""
    public = {
        name
        for name in dir(EspnClient)
        if not name.startswith("_") and callable(getattr(EspnClient, name))
    }
    assert public == set(NETWORK_CALLS) | OFFLINE_METHODS


def test_no_call_site_falls_back_to_the_default_transport(tmp_path, monkeypatch):
    def forbidden(url, headers, timeout):
        raise _TransportLeak(f"a call site did not pass self._transport: {url}")

    # `get_json` reads `default_transport` as a module global per call, so patching
    # the attribute reaches every call site.
    monkeypatch.setattr(_http, "default_transport", forbidden)

    def stub(url, headers, timeout):
        # Parses to an empty dict, so every method yields an empty result and — since
        # the client only caches a truthy body — every method really does call out.
        return b"{}"

    client = EspnClient(str(tmp_path), transport=stub)
    for name, (args, kwargs) in NETWORK_CALLS.items():
        getattr(client, name)(*args, **kwargs)
