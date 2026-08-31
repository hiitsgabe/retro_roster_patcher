"""ESPN client against a recorded response. Never touches the network."""

import pathlib

import pytest

from retro_roster_patcher.sports.espn import EspnClient

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "api"


@pytest.fixture
def replay():
    """Build a transport that always returns one recorded body."""

    def _replay(filename):
        body = (FIXTURES / filename).read_bytes()

        def transport(url, headers, timeout):
            return body

        return transport

    return _replay


def test_get_nhl_teams_parses_the_recorded_response(tmp_path, replay):
    client = EspnClient(str(tmp_path), transport=replay("espn_nhl_teams.json"))
    teams = client.get_nhl_teams()

    assert len(teams) >= 30
    codes = {t.code for t in teams}
    assert "BOS" in codes
    assert all(t.id for t in teams)
    assert all(t.name for t in teams)


def test_results_are_cached_so_the_second_call_skips_the_transport(tmp_path, replay):
    calls = []
    body = (FIXTURES / "espn_nhl_teams.json").read_bytes()

    def counting_transport(url, headers, timeout):
        calls.append(url)
        return body

    client = EspnClient(str(tmp_path), transport=counting_transport)
    client.get_nhl_teams()
    client.get_nhl_teams()

    assert len(calls) == 1


def test_a_transport_failure_yields_no_teams_rather_than_crashing(tmp_path):
    def failing(url, headers, timeout):
        raise OSError("no network")

    client = EspnClient(str(tmp_path), transport=failing)
    assert client.get_nhl_teams() == []
