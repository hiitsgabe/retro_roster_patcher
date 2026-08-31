"""NHL official API client against a recorded response. Never touches the network."""

from typing import Any

from retro_roster_patcher.sports.nhl import NhlApiClient

STANDINGS_URL = "https://api-web.nhle.com/v1/standings/now"


def test_get_nhl_teams_parses_the_recorded_standings(tmp_path, replay):
    client = NhlApiClient(str(tmp_path), transport=replay("nhl_standings.json"))
    teams = client.get_nhl_teams()

    assert len(teams) >= 30
    assert "BOS" in {t.code for t in teams}


def test_the_requested_url_is_the_standings_endpoint(tmp_path, replay):
    # The replay transport ignores the URL, so without this nothing in the suite
    # would notice BASE_URL or the path changing.
    transport = replay("nhl_standings.json")
    NhlApiClient(str(tmp_path), transport=transport).get_nhl_teams()

    assert transport.calls == [STANDINGS_URL]


def test_status_callback_fires_for_each_request(tmp_path, replay):
    seen = []
    client = NhlApiClient(
        str(tmp_path), on_status=seen.append, transport=replay("nhl_standings.json")
    )
    client.get_nhl_teams()

    assert seen == ["Fetching /standings/now..."]


def test_a_transport_failure_yields_no_teams(tmp_path):
    def failing(url, headers, timeout):
        raise OSError("no network")

    assert NhlApiClient(str(tmp_path), transport=failing).get_nhl_teams() == []


# --- the transport seam (helpers live in conftest.py) ---

# Every public method that issues a request, with arguments that reach the wire.
# Annotated because every entry here takes positional arguments only: the empty
# kwargs dicts leave mypy nothing to infer a value type from.
NETWORK_CALLS: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {
    "get_nhl_teams": ((), {}),
    "get_hockey_squad": (("BOS",), {}),
    "get_hockey_team_leaders": (("BOS",), {}),
}

# This client answers nothing from module constants: every public method calls out.
OFFLINE_MEMBERS: set[str] = set()


def test_the_leak_guard_covers_every_public_member(assert_public_members_are_classified):
    assert_public_members_are_classified(NhlApiClient, NETWORK_CALLS, OFFLINE_MEMBERS)


def test_no_call_site_falls_back_to_the_default_transport(tmp_path, assert_no_transport_leak):
    assert_no_transport_leak(NhlApiClient, NETWORK_CALLS, str(tmp_path))
