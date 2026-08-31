"""ESPN client against a recorded response. Never touches the network."""

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
    transport = replay("espn_nhl_teams.json")
    client = EspnClient(str(tmp_path), transport=transport)
    client.get_nhl_teams()
    client.get_nhl_teams()

    assert transport.calls == [NHL_TEAMS_URL]


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
