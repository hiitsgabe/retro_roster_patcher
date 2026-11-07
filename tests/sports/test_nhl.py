"""NHL official API client against a recorded response. Never touches the network."""

import json
from datetime import date
from typing import Any

from retro_roster_patcher.sports.nhl import NhlApiClient, current_season

# The URLs the client builds, which are not the URLs the fixtures were recorded
# from: see the `SOURCES` table in tests/fixtures/api/record.py, which pins
# `/standings/<date>` so a re-record cannot swap the team set out mid-season.
STANDINGS_URL = "https://api-web.nhle.com/v1/standings/now"
ROSTER_URL = "https://api-web.nhle.com/v1/roster/BOS/20252026"
CLUB_STATS_URL = "https://api-web.nhle.com/v1/club-stats/BOS/20252026/2"
SEARCH_URL = (
    "https://search.d3.nhle.com/api/v1/search/player?culture=en-us&limit=50000&q=%2A&active=false"
)


def test_get_nhl_teams_parses_the_recorded_standings(tmp_path, replay):
    client = NhlApiClient(str(tmp_path), transport=replay("nhl_standings.json"))
    teams = client.get_nhl_teams()

    # Exactly the league, not "at least most of it": the dated fixture URL exists to
    # keep this set stable, so a loose bound would tolerate the drift it prevents.
    # Kept beside the table below because the table is keyed by `code`: two teams
    # parsed with the same code would collapse into one entry and still match it.
    assert len(teams) == 32
    # Every value spelled out rather than "every name is non-empty". Non-emptiness is
    # satisfied by the full name leaking into the short field, by the wrong nested
    # `default` key, and by a team's name landing on its neighbour — all of which this
    # table fails on. Two entries are load-bearing on their own: `UTA` and `VGK` are
    # the only teams whose common name overruns the 12-character cut, which is the
    # width the ROM's team-name field allows, and nothing else in the suite sees it.
    # `id` is deliberately absent: the standings payload carries no team ID and the
    # parser hardcodes 0.
    assert {t.code: (t.name, t.short_name) for t in teams} == {
        "ANA": ("Anaheim Ducks", "Ducks"),
        "BOS": ("Boston Bruins", "Bruins"),
        "BUF": ("Buffalo Sabres", "Sabres"),
        "CAR": ("Carolina Hurricanes", "Hurricanes"),
        "CBJ": ("Columbus Blue Jackets", "Blue Jackets"),
        "CGY": ("Calgary Flames", "Flames"),
        "CHI": ("Chicago Blackhawks", "Blackhawks"),
        "COL": ("Colorado Avalanche", "Avalanche"),
        "DAL": ("Dallas Stars", "Stars"),
        "DET": ("Detroit Red Wings", "Red Wings"),
        "EDM": ("Edmonton Oilers", "Oilers"),
        "FLA": ("Florida Panthers", "Panthers"),
        "LAK": ("Los Angeles Kings", "Kings"),
        "MIN": ("Minnesota Wild", "Wild"),
        "MTL": ("Montréal Canadiens", "Canadiens"),
        "NJD": ("New Jersey Devils", "Devils"),
        "NSH": ("Nashville Predators", "Predators"),
        "NYI": ("New York Islanders", "Islanders"),
        "NYR": ("New York Rangers", "Rangers"),
        "OTT": ("Ottawa Senators", "Senators"),
        "PHI": ("Philadelphia Flyers", "Flyers"),
        "PIT": ("Pittsburgh Penguins", "Penguins"),
        "SEA": ("Seattle Kraken", "Kraken"),
        "SJS": ("San Jose Sharks", "Sharks"),
        "STL": ("St. Louis Blues", "Blues"),
        "TBL": ("Tampa Bay Lightning", "Lightning"),
        "TOR": ("Toronto Maple Leafs", "Maple Leafs"),
        "UTA": ("Utah Hockey Club", "Utah Hockey "),
        "VAN": ("Vancouver Canucks", "Canucks"),
        "VGK": ("Vegas Golden Knights", "Golden Knigh"),
        "WPG": ("Winnipeg Jets", "Jets"),
        "WSH": ("Washington Capitals", "Capitals"),
    }


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
    # Every name, in order, rather than "every name is non-empty": the order is the
    # claim above (goalies last), and non-emptiness would survive the roster arriving
    # shifted by one, duplicated, or with a group parsed off the wrong key. `name` is
    # `f"{first} {last}".strip()`, so it also fails on half a name — which is what the
    # `first_name`/`last_name` spot check below exists to disambiguate.
    assert [p.name for p in players] == [
        "Michael Eyssimont",
        "Morgan Geekie",
        "James Hagens",
        "Tanner Jeannot",
        "Mark Kastelic",
        "Marat Khusnutdinov",
        "Sean Kuraly",
        "Elias Lindholm",
        "Fraser Minten",
        "Casey Mittelstadt",
        "David Pastrnak",
        "JJ Peterka",
        "Alex Steeves",
        "Pavel Zacha",
        "Jonathan Aspirot",
        "Henri Jokiharju",
        "Hampus Lindholm",
        "Mason Lohrei",
        "Charlie McAvoy",
        "Nikita Zadorov",
        "Joonas Korpisalo",
        "Jeremy Swayman",
    ]

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
    # Height and head shot, which NHL 07 writes to `HEIG` and the portrait.
    assert pastrnak.height == 72.0
    assert pastrnak.photo_url == "https://assets.nhle.com/mugs/nhl/20252026/BOS/8477956.png"
    # The birth date NHL 94 ranks youth on (the roster carries no age).
    assert pastrnak.birth_date == "1996-05-25"


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


# Every public method that issues a request, with arguments that reach the wire.
# Annotated because every entry here takes positional arguments only: the empty
# kwargs dicts leave mypy nothing to infer a value type from.
NETWORK_CALLS: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {
    "get_nhl_teams": ((), {}),
    "get_hockey_squad": (("BOS",), {}),
    "get_hockey_team_leaders": (("BOS",), {}),
    "get_hockey_free_agents": ((2026,), {}),
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
    assert urls == [STANDINGS_URL, ROSTER_URL, CLUB_STATS_URL, SEARCH_URL]


# The NHL's player search, as `get_hockey_free_agents` reads it: the fields it
# uses, for a player who is a free agent and for each reason one is not.
SEARCH = [
    {"playerId": "1", "teamAbbrev": None, "lastSeasonId": "20262027", "active": False,
     "birthCity": "Eden Prairie", "birthStateProvince": "MN", "birthCountry": "USA",
     "heightInInches": 72, "weightInPounds": 205},
    {"playerId": "2", "teamAbbrev": None, "lastSeasonId": "20252026", "active": False,
     "birthCity": "Kazan", "birthStateProvince": None, "birthCountry": "RUS",
     "heightInInches": 74, "weightInPounds": 176},
    # Gone since 2024-25; signed; never played; on a roster last season, no NHL game.
    {"playerId": "3", "teamAbbrev": None, "lastSeasonId": "20242025", "active": False},
    {"playerId": "4", "teamAbbrev": "TOR", "lastSeasonId": "20262027", "active": False},
    {"playerId": "5", "teamAbbrev": None, "lastSeasonId": None, "active": False},
    {"playerId": "6", "teamAbbrev": None, "lastSeasonId": "20252026", "active": False},
]  # fmt: skip


#: A candidate whose landing page is not what the client expects.
JUNK = {"playerId": "7", "teamAbbrev": None, "lastSeasonId": "20252026", "active": False}


def season_line(season, games, **fields):
    return {
        "leagueAbbrev": "NHL",
        "gameTypeId": 2,
        "season": season,
        "gamesPlayed": games,
        **fields,
    }


LANDINGS = {
    "1": {
        "firstName": {"default": "Nick"}, "lastName": {"default": "Leddy"}, "position": "D",
        "shootsCatches": "L", "sweaterNumber": 4, "birthDate": "1991-03-20",
        "headshot": "https://assets.nhle.com/mugs/nhl/latest/1.png",
        "seasonTotals": [
            season_line(20242025, 31, goals=2),
            season_line(20252026, 32, goals=1, assists=6, points=7, plusMinus=-9, pim=6, shots=16,
                        faceoffWinningPctg=0.0),
            {**season_line(20252026, 40), "leagueAbbrev": "AHL"},
        ],
    },
    "6": {
        "firstName": {"default": "Brett"}, "lastName": {"default": "Harrison"}, "position": "C",
        "seasonTotals": [{**season_line(20252026, 40), "leagueAbbrev": "AHL"}],
    },
    "7": {"firstName": "Plain", "seasonTotals": [None]},
    "2": {
        "firstName": {"default": "Artur"}, "lastName": {"default": "Akhtyamov"}, "position": "G",
        "shootsCatches": "L", "sweaterNumber": 70, "birthDate": "2001-10-31",
        "headshot": "https://assets.nhle.com/mugs/nhl/latest/2.png",
        "seasonTotals": [
            season_line(20262027, 80, wins=40, goalsAgainstAvg=2.5, savePctg=0.915),
            season_line(20262027, 70, gameTypeId=3),
        ],
    },
}  # fmt: skip


def free_agent_transport(search=SEARCH):
    def transport(url, headers, timeout):
        transport.calls.append(url)
        if url.startswith("https://search.d3.nhle.com/"):
            return json.dumps(search).encode()
        pid = url.split("/player/")[1].split("/")[0]
        return json.dumps(LANDINGS.get(pid, {})).encode()

    transport.calls = []
    return transport


def free_agents(tmp_path, transport=None):
    client = NhlApiClient(str(tmp_path), transport=transport or free_agent_transport())
    return client.get_hockey_free_agents(2026)


def test_free_agents_are_unsigned_players_of_the_last_two_seasons(tmp_path):
    players, _ = free_agents(tmp_path)
    assert sorted(p.id for p in players) == [1, 2, 6]


def test_a_player_without_an_nhl_game_comes_last_with_an_empty_line(tmp_path):
    # With no line he would take the position defaults, which rate above most
    # real players' lines; an empty line rates him from zeros instead.
    players, lines = free_agents(tmp_path)
    assert ([p.id for p in players][-1], lines["6"]) == (6, {"GP": 0})


def test_free_agents_come_best_first_by_games_played(tmp_path):
    # Leddy played 63 NHL games over the two seasons, Akhtyamov 80.
    players, _ = free_agents(tmp_path)
    assert [p.id for p in players] == [2, 1, 6]


def test_a_free_agent_carries_his_birth_hand_and_head_shot(tmp_path):
    leddy = next(p for p in free_agents(tmp_path)[0] if p.id == 1)
    assert (
        leddy.name, leddy.position, leddy.number, leddy.handedness, leddy.photo_url,
        leddy.birth_date, leddy.age, leddy.nationality, leddy.birth_city, leddy.birth_state,
        leddy.height, leddy.weight,
    ) == (
        "Nick Leddy", "D", 4, "L", "https://assets.nhle.com/mugs/nhl/latest/1.png",
        "1991-03-20", 35, "USA", "Eden Prairie", "MN", 72.0, 205.0,
    )  # fmt: skip


def test_a_free_agents_age_counts_from_the_first_of_october(tmp_path):
    # Born on October 31: still 24 on October 1, 2026.
    akhtyamov = next(p for p in free_agents(tmp_path)[0] if p.id == 2)
    assert akhtyamov.age == 24


def test_a_free_agents_line_is_his_latest_nhl_regular_season(tmp_path):
    _, lines = free_agents(tmp_path)
    assert lines["1"] == {
        "G": 1, "A": 6, "PTS": 7, "+/-": -9, "PIM": 6, "S": 16, "FO%": 0.0, "GP": 32,
    }  # fmt: skip


def test_a_free_agent_goalies_line_has_the_goalie_stats(tmp_path):
    _, lines = free_agents(tmp_path)
    assert lines["2"] == {"GP": 80, "W": 40, "GAA": 2.5, "SV%": 0.915}


def test_free_agents_are_skipped_when_the_search_fails(tmp_path):
    def failing(url, headers, timeout):
        raise OSError("no network")

    assert free_agents(tmp_path, failing) == ([], {})


def test_free_agents_are_cached_by_season(tmp_path):
    transport = free_agent_transport()
    free_agents(tmp_path, transport)
    first = len(transport.calls)
    again = free_agents(tmp_path, transport)
    assert (first, len(transport.calls), sorted(p.id for p in again[0])) == (4, 4, [1, 2, 6])


def test_the_nhl_season_turns_over_on_the_first_of_july():
    assert [current_season(date(2026, 6, 30)), current_season(date(2026, 7, 1))] == [2025, 2026]


def test_a_malformed_landing_is_skipped(tmp_path):
    players, _ = free_agents(tmp_path, free_agent_transport(SEARCH + [JUNK]))
    assert sorted(p.id for p in players) == [1, 2, 6]


def test_a_landing_that_did_not_answer_is_fetched_again_next_time(tmp_path):
    # A dropped connection must not freeze a short list in the season's cache.
    flaky = free_agent_transport()
    calls = {"n": 0}

    def transport(url, headers, timeout):
        if url.endswith("/player/1/landing") and not calls["n"]:
            calls["n"] += 1
            raise OSError("wifi dropped")
        return flaky(url, headers, timeout)

    first, _ = free_agents(tmp_path, transport)
    again, _ = free_agents(tmp_path, transport)
    assert (sorted(p.id for p in first), sorted(p.id for p in again)) == ([2, 6], [1, 2, 6])


def test_a_landing_that_did_not_answer_is_counted_in_a_status_line(tmp_path):
    def transport(url, headers, timeout):
        if url.endswith("/player/1/landing"):
            raise OSError("wifi dropped")
        return free_agent_transport()(url, headers, timeout)

    lines = []
    NhlApiClient(str(tmp_path), on_status=lines.append, transport=transport).get_hockey_free_agents(
        2026
    )
    assert "Free agents: 1 of 3 did not answer; asked again next time" in lines
