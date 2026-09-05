"""Both games fetched through `main` with no credential of any kind.

The argv carries no credential because there is no longer any way to spell one.

The bodies come from `tests/fixtures/api`, recorded from the real endpoints by
`record.py`, and are served by a transport that routes on URL, so the clients
build their real request paths and parse their real payloads. Two are
synthesised rather than recorded and are marked where they are built: ESPN's
soccer team list and the NHL club-stats document.
"""

import json

import pytest

from retro_roster_patcher.cli.__main__ import main
from retro_roster_patcher.sports import _http
from tests.sports.conftest import FIXTURES

from .conftest import events


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# ESPN publishes no soccer team list in the recorded set, so this is the
# `sports[].leagues[].teams[]` envelope its teams endpoint really answers with,
# carrying the one club the recorded roster and statistics documents belong to:
# Liverpool, id 364 in `eng.1`. One team is enough — `fetch` loops over whatever
# the list holds, and a second would need a second recorded roster to be
# anything other than a copy.
_SOCCER_TEAMS = json.dumps(
    {
        "sports": [
            {
                "leagues": [
                    {
                        "teams": [
                            {
                                "team": {
                                    "id": 364,
                                    "displayName": "Liverpool",
                                    "abbreviation": "LIV",
                                }
                            }
                        ]
                    }
                ]
            }
        ]
    }
).encode()

# The NHL club-stats document, which `get_hockey_team_leaders` reads and which
# is not in the fixture set. Empty of skaters: leaders are optional to the
# fetch, they reach `TeamRoster.extra` rather than any player field, and an
# empty one still exercises the request and the parse.
_CLUB_STATS = json.dumps({"skaters": []}).encode()


def _route(monkeypatch, table):
    """Serve recorded bodies by URL fragment and record what was asked for.

    Replaces `_http.default_transport`, which the suite-wide guard has already
    replaced with a raising sentinel. First matching fragment wins, so the table
    is ordered longest-path-first where two would both match.
    """
    asked: list[str] = []

    def transport(url, headers, timeout):
        asked.append(url)
        for fragment, body in table:
            if fragment in url:
                return body
        raise AssertionError(f"no fixture for {url}")

    monkeypatch.setattr(_http, "default_transport", transport)
    return asked


@pytest.fixture
def soccer_wire(monkeypatch):
    return _route(
        monkeypatch,
        [
            ("/athletes/", _fixture("espn_soccer_athlete_stats.json")),
            ("/leaders", _fixture("espn_soccer_leaders.json")),
            ("/teams/364/roster", _fixture("espn_soccer_roster.json")),
            ("/soccer/eng.1/teams", _SOCCER_TEAMS),
        ],
    )


@pytest.fixture
def hockey_wire(monkeypatch):
    return _route(
        monkeypatch,
        [
            ("/club-stats/", _CLUB_STATS),
            ("/roster/", _fixture("nhl_roster.json")),
            ("/standings/", _fixture("nhl_standings.json")),
        ],
    )


def _we2002_argv(tmp_path):
    """The whole command. No credential appears because none can be spelled."""
    return [
        "--json",
        "fetch",
        "--game",
        "we2002",
        "--season",
        "2025",
        "--league-id",
        "2001",
        "--cache-dir",
        str(tmp_path / "cache"),
        "--out",
        str(tmp_path / "rosters.json"),
    ]


def _nhl94_argv(tmp_path):
    return [
        "--json",
        "fetch",
        "--game",
        "nhl94-genesis",
        "--provider",
        "nhl",
        "--season",
        "2025",
        "--cache-dir",
        str(tmp_path / "cache"),
        "--out",
        str(tmp_path / "rosters.json"),
    ]


def test_we2002_fetch_exits_zero_with_no_credential(tmp_path, soccer_wire, capsys):
    assert main(_we2002_argv(tmp_path)) == 0


def test_we2002_fetch_ends_the_stream_with_a_result(tmp_path, soccer_wire, capsys):
    # The last event and not merely "no exception": a fetch that failed per team
    # still exits 0 and still writes a file, so the terminal event type is what
    # separates a working fetch from one that degraded all the way down.
    main(_we2002_argv(tmp_path))
    evts = events(capsys)
    assert evts[-1]["event"] == "result"
    assert evts[-1]["kind"] == "rosters"


def test_we2002_fetch_reports_the_league_the_season_and_a_real_squad(tmp_path, soccer_wire, capsys):
    # The player count is the recorded roster's, so a fetch that silently
    # returned an empty squad — which is what a missing credential used to
    # produce — cannot pass this.
    main(_we2002_argv(tmp_path))
    result = events(capsys)[-1]
    assert result["league"] == "Premier League"
    assert result["season"] == 2025
    assert result["teams"] == 1
    assert result["players"] == 29


def test_we2002_fetch_records_no_error_against_the_team(tmp_path, soccer_wire, capsys):
    # `fetch` degrades per team rather than raising, so a squad that failed
    # outright would still reach the `result` above. This reads the written file
    # and asserts the team carries no reason-it-failed string.
    main(_we2002_argv(tmp_path))
    written = json.loads((tmp_path / "rosters.json").read_text(encoding="utf-8"))
    assert written["teams"][0]["error"] == ""


def test_we2002_fetch_sends_no_authorization_header(tmp_path, monkeypatch, capsys):
    # The deleted client sent `x-apisports-key`. Nothing should now send any
    # credential header at all, and the transport is the only place that could.
    seen: list[dict] = []

    def transport(url, headers, timeout):
        seen.append(headers)
        for fragment, body in [
            ("/athletes/", _fixture("espn_soccer_athlete_stats.json")),
            ("/leaders", _fixture("espn_soccer_leaders.json")),
            ("/teams/364/roster", _fixture("espn_soccer_roster.json")),
            ("/soccer/eng.1/teams", _SOCCER_TEAMS),
        ]:
            if fragment in url:
                return body
        raise AssertionError(f"no fixture for {url}")

    monkeypatch.setattr(_http, "default_transport", transport)
    main(_we2002_argv(tmp_path))
    credentials = {
        key
        for headers in seen
        for key in headers
        if "key" in key.lower() or "auth" in key.lower() or "token" in key.lower()
    }
    assert sorted(credentials) == []


def test_nhl94_fetch_exits_zero_with_no_credential(tmp_path, hockey_wire, capsys):
    assert main(_nhl94_argv(tmp_path)) == 0


def test_nhl94_fetch_ends_the_stream_with_a_result(tmp_path, hockey_wire, capsys):
    main(_nhl94_argv(tmp_path))
    evts = events(capsys)
    assert evts[-1]["event"] == "result"
    assert evts[-1]["kind"] == "rosters"


def test_nhl94_fetch_reports_the_season_and_the_rom_mapped_teams(tmp_path, hockey_wire, capsys):
    # 26 is the slot-mapped subset of the 32 teams in the recorded standings:
    # `fetch` drops any team with no slot in the 1994 ROM before requesting a
    # squad, which is six of them.
    main(_nhl94_argv(tmp_path))
    result = events(capsys)[-1]
    assert result["season"] == 2025
    assert result["teams"] == 26


def test_nhl94_fetch_writes_a_squad_for_every_mapped_team(tmp_path, hockey_wire, capsys):
    # Every team is served the same recorded roster, so the count is uniform and
    # non-zero; zero would mean the squad request never landed.
    main(_nhl94_argv(tmp_path))
    written = json.loads((tmp_path / "rosters.json").read_text(encoding="utf-8"))
    assert {len(team["players"]) for team in written["teams"]} == {22}
