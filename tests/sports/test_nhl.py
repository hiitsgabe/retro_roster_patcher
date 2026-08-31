"""NHL official API client against a recorded response. Never touches the network."""

from typing import Any

from retro_roster_patcher.sports.nhl import NhlApiClient

# The URLs the client builds, which are not the URLs the fixtures were recorded
# from: see the `SOURCES` table in tests/fixtures/api/record.py, which pins
# `/standings/<date>` so a re-record cannot swap the team set out mid-season.
STANDINGS_URL = "https://api-web.nhle.com/v1/standings/now"
ROSTER_URL = "https://api-web.nhle.com/v1/roster/BOS/20252026"
CLUB_STATS_URL = "https://api-web.nhle.com/v1/club-stats/BOS/20252026/2"


def test_get_nhl_teams_parses_the_recorded_standings(tmp_path, replay):
    client = NhlApiClient(str(tmp_path), transport=replay("nhl_standings.json"))
    teams = client.get_nhl_teams()

    # Exactly the league, not "at least most of it": the dated fixture URL exists to
    # keep this set stable, so a loose bound would tolerate the drift it prevents.
    assert len(teams) == 32
    assert all(t.name for t in teams)
    assert all(t.short_name for t in teams)
    # `id` is deliberately not checked: the standings payload carries no team ID
    # and the parser hardcodes 0.

    # One team spelled out, because "non-empty" passes just as happily on the full
    # name leaking into the short field, or on the wrong nested `default` key.
    bruins = next(t for t in teams if t.code == "BOS")
    assert bruins.name == "Boston Bruins"
    assert bruins.short_name == "Bruins"

    # And one team whose common name overruns the 12-character cut, which is the
    # width the ROM's team-name field allows. Nothing else in the suite sees it.
    knights = next(t for t in teams if t.code == "VGK")
    assert knights.short_name == "Golden Knigh"


def test_the_standings_url_is_pinned_and_results_are_cached(tmp_path, replay):
    # One list-equality covers both. The replay transport ignores the URL it is
    # handed, so nothing else would notice BASE_URL or the path changing; and the
    # second call must be served from the on-disk cache, so nothing else would
    # notice the write, the read, or the cache key breaking.
    transport = replay("nhl_standings.json")
    client = NhlApiClient(str(tmp_path), transport=transport)
    client.get_nhl_teams()
    client.get_nhl_teams()

    assert transport.calls == [STANDINGS_URL]


def test_get_hockey_squad_parses_the_recorded_roster(tmp_path, replay):
    client = NhlApiClient(str(tmp_path), transport=replay("nhl_roster.json"))
    players = client.get_hockey_squad("BOS")

    # forwards + defensemen + goalies, flattened into one list. The goalies group is
    # parsed last and is what a truncated group tuple drops silently.
    assert len(players) == 22
    assert sum(p.position == "G" for p in players) == 2
    assert all(p.name for p in players)

    # The NHL-specific transform this client exists for: the payload codes wings as
    # bare L and R, and the ROM roster format wants LW and RW. Set equality rather
    # than a membership check, so an unmapped L or R fails rather than passing
    # alongside the mapped ones.
    assert {p.position for p in players} == {"C", "LW", "RW", "D", "G"}

    # One player spelled out. `name` is `f"{first} {last}".strip()`, so blanking
    # either half leaves it truthy and the check above passes on half a name.
    # Selected by sweater number, which is independent of both.
    pastrnak = next(p for p in players if p.number == 88)
    assert (pastrnak.first_name, pastrnak.last_name) == ("David", "Pastrnak")
    assert pastrnak.name == "David Pastrnak"
    assert pastrnak.position == "RW"  # the R -> RW mapping, on a named player


def test_each_squad_team_and_season_caches_separately(tmp_path, replay):
    # The standings cache key is a bare constant, but this one is built from the
    # arguments. A key that drops one serves Boston's roster for Toronto, or last
    # season's for this one — wrong data, no error. Asserting the URL list rather
    # than a call count pins the season string the key and the path share.
    transport = replay("nhl_roster.json")
    client = NhlApiClient(str(tmp_path), transport=transport)
    client.get_hockey_squad("BOS")
    client.get_hockey_squad("TOR")
    client.get_hockey_squad("BOS", season=2024)
    client.get_hockey_squad("BOS")

    assert transport.calls == [
        ROSTER_URL,
        "https://api-web.nhle.com/v1/roster/TOR/20252026",
        "https://api-web.nhle.com/v1/roster/BOS/20242025",
    ]


def test_each_leaders_team_and_season_caches_separately(tmp_path):
    # Same hazard, same shape, on the third endpoint. Driven by a synthetic body
    # rather than a recorded one on purpose: what is under test is the cache key
    # and the URL, not the stat extraction, so this needs no club-stats fixture —
    # only a payload truthy enough that the client caches it.
    calls = []

    def transport(url, headers, timeout):
        calls.append(url)
        return b'{"skaters": [{"playerId": 1, "goals": 3}], "goalies": []}'

    client = NhlApiClient(str(tmp_path), transport=transport)
    client.get_hockey_team_leaders("BOS")
    client.get_hockey_team_leaders("TOR")
    client.get_hockey_team_leaders("BOS", season=2024)
    client.get_hockey_team_leaders("BOS")

    assert calls == [
        CLUB_STATS_URL,
        "https://api-web.nhle.com/v1/club-stats/TOR/20252026/2",
        "https://api-web.nhle.com/v1/club-stats/BOS/20242025/2",
    ]


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
    urls = assert_no_transport_leak(NhlApiClient, NETWORK_CALLS, str(tmp_path))

    # The guard already walks every endpoint, so pin what it requested. These two
    # URLs have no fixture-backed test of their own, and everything they encode
    # fails silently rather than loudly: the season string both derive from the
    # default season, and the trailing /2 that means regular season, not playoffs.
    assert urls == [STANDINGS_URL, ROSTER_URL, CLUB_STATS_URL]
