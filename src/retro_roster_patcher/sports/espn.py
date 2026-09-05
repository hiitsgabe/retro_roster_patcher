"""ESPN public API client — no API key required.

Two hosts. `site.api.espn.com` serves team lists and rosters for all four sports;
`sports.core.api.espn.com` serves per-player statistics, as team "leaders" for
hockey, baseball and basketball and as a per-athlete statistics document for
soccer. Both are keyless and neither rate-limits.
"""

import json
import os
from collections.abc import Callable
from typing import Any

from ..core.errors import ensure_cache_dir
from . import _http
from .models import League, Player, PlayerStats, Team

# Maps our internal league IDs to ESPN league codes.
# IDs start at 2000 because they once had to not clash with a second
# provider's. That provider is gone; the numbering stays, because these ids
# are what `--league-id` takes and renumbering them would silently
# repoint every saved command line and rosters file.
ESPN_LEAGUES = [
    {"id": 2001, "code": "eng.1", "name": "Premier League", "country": "England"},
    {"id": 2002, "code": "esp.1", "name": "La Liga", "country": "Spain"},
    {"id": 2003, "code": "ger.1", "name": "Bundesliga", "country": "Germany"},
    {"id": 2004, "code": "ita.1", "name": "Serie A", "country": "Italy"},
    {"id": 2005, "code": "fra.1", "name": "Ligue 1", "country": "France"},
    {"id": 2006, "code": "bra.1", "name": "Brasileirao Serie A", "country": "Brazil"},
    {"id": 2007, "code": "usa.1", "name": "MLS", "country": "USA"},
    {
        "id": 2008,
        "code": "UEFA.CHAMPIONS",
        "name": "UEFA Champions League",
        "country": "World",
    },
    {
        "id": 2009,
        "code": "conmebol.libertadores",
        "name": "Copa Libertadores",
        "country": "South America",
    },
    {"id": 2010, "code": "arg.1", "name": "Liga Profesional", "country": "Argentina"},
    {"id": 2011, "code": "mex.1", "name": "Liga BBVA MX", "country": "Mexico"},
    {"id": 2012, "code": "por.1", "name": "Primeira Liga", "country": "Portugal"},
    {"id": 2013, "code": "ned.1", "name": "Eredivisie", "country": "Netherlands"},
    {"id": 2014, "code": "jpn.1", "name": "J.League", "country": "Japan"},
    {"id": 2015, "code": "col.1", "name": "Primera A", "country": "Colombia"},
    {"id": 2016, "code": "chi.1", "name": "Primera División", "country": "Chile"},
]

# NHL team abbreviations mapping to ROM slots (28 teams: 26 NHL + 2 All-Star)
# Based on NHL 94 SNES team order (1993-94 season)
NHL_TEAM_MAP = {
    "ANA": 0,  # Mighty Ducks (expansion - will use San Jose)
    "BOS": 1,  # Boston Bruins
    "BUF": 2,  # Buffalo Sabres
    "CGY": 3,  # Calgary Flames
    "CHI": 4,  # Chicago Blackhawks
    "DAL": 5,  # Dallas Stars
    "DET": 6,  # Detroit Red Wings
    "EDM": 7,  # Edmonton Oilers
    "FLA": 8,  # Florida Panthers
    "CAR": 9,  # Carolina Hurricanes (was Hartford Whalers)
    "LAK": 10,  # Los Angeles Kings
    "LA": 10,  # ESPN abbreviation
    "MTL": 11,  # Montreal Canadiens
    "NJD": 12,  # New Jersey Devils
    "NJ": 12,  # ESPN abbreviation
    "NYI": 13,  # New York Islanders
    "NYR": 14,  # New York Rangers
    "OTT": 15,  # Ottawa Senators
    "PHI": 16,  # Philadelphia Flyers
    "PIT": 17,  # Pittsburgh Penguins
    "COL": 18,  # Colorado Avalanche (was Quebec Nordiques)
    "SJS": 19,  # San Jose Sharks
    "SJ": 19,  # ESPN abbreviation
    "STL": 20,  # St. Louis Blues
    "TBL": 21,  # Tampa Bay Lightning
    "TB": 21,  # ESPN abbreviation
    "TOR": 22,  # Toronto Maple Leafs
    "VAN": 23,  # Vancouver Canucks
    "WSH": 24,  # Washington Capitals
    "WPG": 25,  # Winnipeg Jets
    "NHL.EAST": 26,  # All-Star East
    "NHL.WEST": 27,  # All-Star West
}

_ID_TO_LEAGUE = {item["id"]: item for item in ESPN_LEAGUES}
_CODE_TO_LEAGUE = {item["code"]: item for item in ESPN_LEAGUES}

# The `PlayerStats` fields ESPN's soccer statistics document has no counterpart
# for, out of the twenty. The other sixteen plus `lineups` are mapped in
# `_parse_athlete_stats`; the document carries 96 fields and none of them counts
# duels contested, duels won, dribbles attempted or dribbles completed.
#
# This is what every record built here declares in `PlayerStats.unsupplied`.
#
# Declaring it is all this client does; whether a consumer acts on it is the
# consumer's decision, and the two soccer consumers decide differently.
# `games/iss_snes/stat_mapper.py` gates on it, and it costs nothing there --
# the audit measured its optional inputs and this tuple to be disjoint.
# `games/we2002/stat_mapper.py` deliberately ignores it, and says why: acting on
# it changes bytes away from what the original patcher wrote, and three
# permanently floored attributes are the price of that fidelity.
SOCCER_UNSUPPLIED_STATS = (
    "duels_total",
    "duels_won",
    "dribbles_attempts",
    "dribbles_success",
)

# ESPN publishes four average-match-rating fields and, for soccer, leaves all
# four at 0.0 — measured across the recorded document's `general` category. The
# first that is populated wins, so a feed that starts filling one is not thrown
# away; `PlayerStats.rating` is `None` when none of them is, which is the
# representation `PlayerStats` has always used for an absent rating.
_RATING_FIELDS = (
    "avgRatingFromCorrespondent",
    "avgRatingFromDataFeed",
    "avgRatingFromEditor",
    "avgRatingFromUser",
)

SOCCER_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"
SOCCER_CORE_URL = "https://sports.core.api.espn.com/v2/sports/soccer/leagues"
HOCKEY_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/hockey"
HOCKEY_CORE_URL = "https://sports.core.api.espn.com/v2/sports/hockey/leagues/nhl"
BASEBALL_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/baseball"
BASEBALL_CORE_URL = "https://sports.core.api.espn.com/v2/sports/baseball/leagues/mlb"
BASKETBALL_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball"
BASKETBALL_CORE_URL = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"


def _as_float(value: Any) -> float:
    """A statistic's numeric value, or `0.0` for anything that is not one.

    ESPN sends every statistic as a JSON number, but a `null` in one field must
    not cost the other ninety-five, and `int(None)` raises.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)


class EspnClient:
    """Client for ESPN's public API — no key, no rate limits.

    Four sports: soccer for WE2002, hockey for NHL94, and baseball and
    basketball for the games the plan migrates next.
    """

    def __init__(
        self,
        cache_dir: str,
        on_status: Callable[[str], None] | None = None,
        transport: _http.Transport | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.on_status = on_status
        self._transport = transport
        ensure_cache_dir(cache_dir)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_featured_leagues(self) -> list[League]:
        """Return featured leagues from the ESPN league list."""
        featured_ids = [2008, 2009, 2006, 2007]  # CL, Libertadores, Brasileirao, MLS
        result = []
        for league_id in featured_ids:
            item = _ID_TO_LEAGUE.get(league_id)
            if item:
                result.append(self._league_from_item(item))
        return result

    def get_leagues(
        self, country: str | None = None, season: int | None = None, id: int | None = None
    ) -> list[League]:
        """Return ESPN leagues, optionally filtered by id.

        `season` is carried onto the `League` it answers with. It reaches nothing
        else — this list is a module constant — but `WE2002Patcher.fetch` puts
        that object straight onto the `LeagueData` it returns and `serde` writes
        it to the rosters file, so a `League` that ignored the argument reported
        the current calendar year for every season the caller asked about.
        """
        if id is not None:
            item = _ID_TO_LEAGUE.get(id)
            return [self._league_from_item(item, season)] if item else []
        leagues = [self._league_from_item(item, season) for item in ESPN_LEAGUES]
        if country:
            leagues = [x for x in leagues if x.country.lower() == country.lower()]
        return leagues

    def get_teams(self, league_id: int, season: int | None = None) -> list[Team]:
        """Fetch all teams in a league.

        `season` reaches the cache key and not the request: the endpoint has no
        season parameter and answers with the current table. See
        `get_hockey_squad` for why the key carries it anyway; this method is the
        blunter case, because it was already *given* a season and dropped it.
        """
        item = _ID_TO_LEAGUE.get(league_id)
        if not item:
            return []
        code = item["code"]
        cache_key = f"espn_teams_{league_id}_{season or 'any'}"
        cached = self._load_cache(cache_key)
        if cached:
            return self._parse_teams(cached)
        data = self._request(f"/{code}/teams", sport="soccer")
        # Cache what parsed, not what arrived. A body carrying zero teams is still a
        # truthy dict, so caching on the body persisted the empty result across runs
        # and no later call could recover. The same guard is on every teams method.
        teams = self._parse_teams(data)
        if teams:
            self._save_cache(cache_key, data)
        return teams

    def get_squad(
        self, team_id: int, season: int | None = None, league_code: str | None = None
    ) -> list[Player]:
        """Fetch current squad for a team.

        `season` reaches the cache key only; see `get_hockey_squad`.

        `season` is second and `league_code` third, and that order is not
        cosmetic. WE2002's `fetch` passes the season positionally; with
        `league_code` second — where it used to be — the season landed in it, a
        league code of `2024` matched nothing, and every squad came back empty
        with no error anywhere. The order was originally fixed by making this
        signature a positional superset of `ApiFootballClient.get_squad`'s. That
        client is gone, so `test_espn.py` now pins the parameter list of both
        soccer methods directly — the same guard, without a second class to hold
        it against.
        """
        # ESPN roster endpoint requires the league code; find it via the cached
        # team lists if unknown. Resolved before the cache lookup because the code
        # varies the response — team ids are league-scoped, so a key of the id
        # alone would serve one competition's roster for another's request.
        code = league_code or self._find_league_code_for_team(team_id, season)
        if not code:
            return []
        cache_key = f"espn_squad_{code}_{team_id}_{season or 'any'}"
        cached = self._load_cache(cache_key)
        if cached:
            return self._parse_squad(cached)
        data = self._request(f"/{code}/teams/{team_id}/roster", sport="soccer")
        if data:
            self._save_cache(cache_key, data)
        return self._parse_squad(data)

    def get_player_stats(
        self, team_id: int, season: int, league_code: str | None = None
    ) -> list[PlayerStats]:
        """Fetch per-player season statistics for a soccer team.

        ESPN's core API serves soccer through the same shape the hockey, baseball
        and basketball leaders calls above already use, and it needs no key. Two
        steps, because there is no bulk endpoint — `/athletes` on a team is a 404:

          * the team's `leaders` document enumerates the athletes who have any
            statistic this season, as `$ref` links, twelve categories deep;
          * each athlete's own `statistics` document carries the 96 fields
            `_parse_athlete_stats` reads 17 of.

        That is one request plus one per athlete, about 25 a team and 500 for a
        20-team league. The cache is therefore
        load-bearing rather than an optimisation, and it is per athlete: a league
        fetch interrupted at the twelfth team keeps everything the first eleven
        cost. Every key carries the season, for the reason `get_hockey_squad`
        gives at length.

        Only the athletes the leaders document names get a record. A squad is
        larger than that — the recorded fixtures are 29 and 25 — so the rest
        reach `StatMapper.map_player` with no stats and are rated from position
        and age, which is what that path is for.

        This method used to `return []` under the docstring "ESPN doesn't provide
        historical stats". That was measured and is false: seasons 2024 and 2025
        both answer 200, and it is the sentence that kept WE2002 tied to a paid
        provider.
        """
        code = league_code or self._find_league_code_for_team(team_id, season)
        if not code:
            return []
        stats = []
        for athlete_id in self._soccer_stat_athletes(team_id, code, season):
            parsed = self._soccer_athlete_stats(team_id, code, season, athlete_id)
            if parsed is not None:
                stats.append(parsed)
        return stats

    def _soccer_stat_athletes(self, team_id: int, code: str, season: int) -> list[int]:
        """Ids of the athletes the team's leaders document names, in first-seen order.

        Twelve categories of twenty-five entries each name the same athletes over
        and over, so this deduplicates; the order is stable so that a cached run
        and a live one issue their per-athlete requests in the same sequence.
        """
        cache_key = f"espn_soccer_leaders_{code}_{team_id}_{season}"
        document = self._load_cache(cache_key)
        if document is None:
            url = f"{SOCCER_CORE_URL}/{code}/seasons/{season}/types/1/teams/{team_id}/leaders"
            if self.on_status:
                self.on_status(f"Fetching stats for team {team_id}...")
            try:
                document = _http.get_json(url, transport=self._transport)
            except Exception:
                return []
            if not isinstance(document, dict):
                return []
            # Cache what named at least one athlete, as the teams methods cache
            # only what parsed to at least one team: an empty body is a truthy
            # response and caching it would freeze the empty answer on disk.
            if document:
                self._save_cache(cache_key, document)

        athlete_ids: list[int] = []
        seen = set()
        for category in document.get("categories") or []:
            if not isinstance(category, dict):
                continue
            for entry in category.get("leaders") or []:
                if not isinstance(entry, dict):
                    continue
                pid = self._extract_pid(entry.get("athlete"))
                # `_extract_pid` answers with whatever sat after `/athletes/`, so
                # a link this client does not recognise reaches here as a
                # non-numeric string rather than as `None`.
                if pid is None or not pid.isdigit() or pid in seen:
                    continue
                seen.add(pid)
                athlete_ids.append(int(pid))
        return athlete_ids

    def _soccer_athlete_stats(
        self, team_id: int, code: str, season: int, athlete_id: int
    ) -> PlayerStats | None:
        """One athlete's statistics document, parsed, or `None` if there is none.

        The athlete is in the key as well as the team and the season: this is the
        request a league fetch makes five hundred times, and caching per team
        would mean one interrupted run costs every athlete in it.

        A failed request costs this athlete and not the other twenty-four, which
        is the same bargain `WE2002Patcher.fetch` makes per team.
        """
        cache_key = f"espn_soccer_stats_{code}_{team_id}_{season}_{athlete_id}"
        data = self._load_cache(cache_key)
        if data is None:
            url = (
                f"{SOCCER_CORE_URL}/{code}/seasons/{season}/types/1"
                f"/teams/{team_id}/athletes/{athlete_id}/statistics"
            )
            try:
                data = _http.get_json(url, transport=self._transport)
            except Exception:
                return None
            if not isinstance(data, dict) or not data:
                return None
            self._save_cache(cache_key, data)
        return self._parse_athlete_stats(athlete_id, data)

    # ------------------------------------------------------------------
    # Hockey-specific methods (NHL)
    # ------------------------------------------------------------------

    def get_nhl_teams(self) -> list[Team]:
        """Fetch all current NHL teams."""
        cache_key = "espn_nhl_teams"
        cached = self._load_cache(cache_key)
        if cached:
            return self._parse_teams(cached)
        data = self._request("/nhl/teams", sport="hockey")
        teams = self._parse_teams(data)
        if teams:
            self._save_cache(cache_key, data)
        return teams

    def get_hockey_squad(self, team_id: int, season: int | None = None) -> list[Player]:
        """Fetch current roster for an NHL team.

        `season` does not reach the request — this endpoint has none, and serves
        the current squad whatever the caller wants. It reaches the *cache key*,
        and that is the whole point of the parameter.

        Without it the key is `espn_hockey_squad_{team_id}` and identifies a
        resource whose meaning is "now". A key with no time coordinate can never
        be invalidated by anything the caller can vary, so the first fetch a user
        ever runs freezes that squad on disk for the life of the cache directory:
        every later `--season` replays it, is served with zero network calls, and
        is reported as a success for the season that was asked for. Measured
        before this change, through `NHL94GenesisPatcher.fetch` against one cache
        directory: season 2024 then season 2026, the second run issued no roster
        request at all, returned a player list equal to the first's in id, name,
        position and number, and reported `League.season == 2026`. That is the
        library answering with data it can see was fetched for a different
        season, which is worse than either staleness or an empty result: both of
        those are visible.

        Deliberately not a TTL. A TTL is a divergence from the upstream this
        client is a port of, it needs a wall clock, and it would still hand a
        2024 squad to a 2026 request inside the window. The season is the one
        coordinate the caller already supplies and the only one that makes the
        key mean what the answer means.
        """
        cache_key = f"espn_hockey_squad_{team_id}_{season or 'any'}"
        cached = self._load_cache(cache_key)
        if cached:
            return self._parse_hockey_squad(cached)
        data = self._request(f"/nhl/teams/{team_id}/roster", sport="hockey")
        if data:
            self._save_cache(cache_key, data)
        return self._parse_hockey_squad(data)

    def get_hockey_team_leaders(self, team_id: int, season: int = 2026) -> dict:
        """Fetch per-player stats via team leaders endpoint.

        Returns dict mapping ESPN player ID (str) to stat dict,
        e.g. {"4024123": {"G": 26, "A": 22, "PTS": 48, ...}}.
        One API call per team — covers all rostered players.
        """
        cache_key = f"espn_hockey_leaders_{team_id}_{season}"
        cached = self._load_cache(cache_key)
        if cached:
            return cached

        url = f"{HOCKEY_CORE_URL}/seasons/{season}/types/2/teams/{team_id}/leaders"
        try:
            data = _http.get_json(url, transport=self._transport)
        except Exception:
            return {}

        # Parse: categories[].leaders[] → {player_id: {stat: val}}
        stats: dict[str, dict[str, Any]] = {}
        for cat in data.get("categories", []):
            abbrev = cat.get("abbreviation", "")
            for entry in cat.get("leaders", []):
                athlete = entry.get("athlete", {})
                pid = self._extract_pid(athlete)
                if not pid:
                    continue
                if pid not in stats:
                    stats[pid] = {}
                val = entry.get("value", 0)
                stats[pid][abbrev] = val

        if stats:
            self._save_cache(cache_key, stats)
        return stats

    # ------------------------------------------------------------------
    # Baseball / MLB
    # ------------------------------------------------------------------

    def get_mlb_teams(self) -> list[Team]:
        """Fetch all current MLB teams."""
        cache_key = "espn_mlb_teams"
        cached = self._load_cache(cache_key)
        if cached:
            return self._parse_teams(cached)
        data = self._request("/mlb/teams", sport="baseball")
        teams = self._parse_teams(data)
        if teams:
            self._save_cache(cache_key, data)
        return teams

    def get_baseball_squad(self, team_id: int, season: int | None = None) -> list[Player]:
        """Fetch current roster for an MLB team.

        `season` reaches the cache key only; see `get_hockey_squad`.
        """
        cache_key = f"espn_baseball_squad_{team_id}_{season or 'any'}"
        cached = self._load_cache(cache_key)
        if cached:
            return self._parse_baseball_squad(cached)
        data = self._request(f"/mlb/teams/{team_id}/roster", sport="baseball")
        if data:
            self._save_cache(cache_key, data)
        return self._parse_baseball_squad(data)

    def get_baseball_team_leaders(self, team_id: int, season: int = 2025) -> dict:
        """Fetch per-player stats via team leaders endpoint.

        Returns dict mapping ESPN player ID (str) to stat dict.
        """
        cache_key = f"espn_baseball_leaders_{team_id}_{season}"
        cached = self._load_cache(cache_key)
        if cached:
            return cached

        url = f"{BASEBALL_CORE_URL}/seasons/{season}/types/2/teams/{team_id}/leaders"
        try:
            data = _http.get_json(url, transport=self._transport)
        except Exception:
            return {}

        stats: dict[str, dict[str, Any]] = {}
        for cat in data.get("categories", []):
            abbrev = cat.get("abbreviation", "")
            for entry in cat.get("leaders", []):
                athlete = entry.get("athlete", {})
                pid = self._extract_pid(athlete)
                if not pid:
                    continue
                if pid not in stats:
                    stats[pid] = {}
                val = entry.get("value", 0)
                stats[pid][abbrev] = val

        if stats:
            self._save_cache(cache_key, stats)
        return stats

    def _parse_baseball_squad(self, data: dict) -> list[Player]:
        """Parse MLB team roster with baseball-specific positions.

        ESPN baseball roster groups athletes by role:
        athletes: [{position: "Pitchers", items: [...]}, ...]
        """
        if not isinstance(data, dict):
            return []

        players = []
        for group in data.get("athletes", []):
            items = group.get("items", [])
            for athlete in items:
                pos_info = athlete.get("position", {})
                pos_abbrev = (
                    pos_info.get("abbreviation", "OF") if isinstance(pos_info, dict) else "OF"
                ).upper()

                jersey = athlete.get("jersey")
                number = int(jersey) if jersey and str(jersey).isdigit() else None

                display_name = athlete.get("displayName", athlete.get("fullName", ""))
                first_name = athlete.get("firstName", "")
                last_name = athlete.get("lastName", "")

                if not last_name and display_name:
                    parts = display_name.split()
                    if len(parts) == 1:
                        last_name = parts[0]
                        first_name = ""
                    else:
                        last_name = parts[-1]
                        first_name = " ".join(parts[:-1])

                # Bats/throws from roster endpoint
                bats_info = athlete.get("bats", {})
                bat_hand = bats_info.get("abbreviation", "") if isinstance(bats_info, dict) else ""
                throws_info = athlete.get("throws", {})
                throw_hand = (
                    throws_info.get("abbreviation", "") if isinstance(throws_info, dict) else ""
                )
                # Fallback to generic hand field
                if not throw_hand:
                    hand_info = athlete.get("hand", {})
                    throw_hand = (
                        hand_info.get("abbreviation", "") if isinstance(hand_info, dict) else ""
                    )

                # `weight` is reported in pounds, the same unit and the same
                # key `_parse_hockey_squad` reads. The source did not parse it
                # here, and **the one consumer of a baseball squad still does
                # not read it**: `games/mvp_psp` writes every patched player at
                # `MVPPlayerRecord.weight`'s default of 190 lb, which is
                # upstream's behaviour and preserved deliberately -- see the
                # label on `mvp_psp.patcher._build_attrib_fields`. Filling the
                # field changes no byte on any disc today; it is kept because
                # this is the provider layer, the figure is right there, and a
                # parser that silently drops a value the endpoint reports is a
                # worse default than one that carries it. Zero still means "not
                # reported" and a consumer must treat it that way.
                weight = athlete.get("weight", 0) or 0

                players.append(
                    Player(
                        id=int(athlete.get("id", 0)),
                        name=display_name,
                        first_name=first_name,
                        last_name=last_name,
                        age=athlete.get("age", 25) or 25,
                        nationality=athlete.get("citizenship", ""),
                        position=pos_abbrev,
                        number=number,
                        photo_url="",
                        weight=float(weight),
                        handedness=throw_hand,
                        bats=bat_hand,
                    )
                )
        return players

    # ------------------------------------------------------------------
    # Basketball / NBA
    # ------------------------------------------------------------------

    def get_nba_teams(self) -> list[Team]:
        """Fetch all current NBA teams."""
        cache_key = "espn_nba_teams"
        cached = self._load_cache(cache_key)
        if cached:
            return self._parse_teams(cached)
        data = self._request("/nba/teams", sport="basketball")
        teams = self._parse_teams(data)
        if teams:
            self._save_cache(cache_key, data)
        return teams

    def get_basketball_squad(self, team_id: int, season: int | None = None) -> list[Player]:
        """Fetch current roster for an NBA team.

        `season` reaches the cache key only; see `get_hockey_squad`.
        """
        cache_key = f"espn_basketball_squad_{team_id}_{season or 'any'}"
        cached = self._load_cache(cache_key)
        if cached:
            return self._parse_basketball_squad(cached)
        data = self._request(f"/nba/teams/{team_id}/roster", sport="basketball")
        if data:
            self._save_cache(cache_key, data)
        return self._parse_basketball_squad(data)

    def get_basketball_team_leaders(self, team_id: int, season: int = 2026) -> dict:
        """Fetch per-player stats via team leaders endpoint.

        Returns dict mapping ESPN player ID (str) to stat dict,
        e.g. {"4024123": {"PTS": 26.2, "REB": 8.1, "AST": 5.3, ...}}.
        """
        cache_key = f"espn_basketball_leaders_{team_id}_{season}"
        cached = self._load_cache(cache_key)
        if cached:
            return cached

        url = f"{BASKETBALL_CORE_URL}/seasons/{season}/types/2/teams/{team_id}/leaders"
        try:
            data = _http.get_json(url, transport=self._transport)
        except Exception:
            return {}

        stats: dict[str, dict[str, Any]] = {}
        for cat in data.get("categories", []):
            abbrev = cat.get("abbreviation", "")
            for entry in cat.get("leaders", []):
                athlete = entry.get("athlete", {})
                pid = self._extract_pid(athlete)
                if not pid:
                    continue
                if pid not in stats:
                    stats[pid] = {}
                val = entry.get("value", 0)
                stats[pid][abbrev] = val

        if stats:
            self._save_cache(cache_key, stats)
        return stats

    def _parse_basketball_squad(self, data: dict) -> list[Player]:
        """Parse NBA team roster.

        ESPN basketball roster returns athletes as a flat list (not grouped):
        athletes: [{id, displayName, position: {abbreviation: "PG"}, ...}, ...]
        """
        if not isinstance(data, dict):
            return []

        players = []
        for athlete in data.get("athletes", []):
            pos_info = athlete.get("position", {})
            pos_abbrev = (
                pos_info.get("abbreviation", "SF") if isinstance(pos_info, dict) else "SF"
            ).upper()

            jersey = athlete.get("jersey")
            number = int(jersey) if jersey and str(jersey).isdigit() else None

            display_name = athlete.get("displayName", athlete.get("fullName", ""))
            first_name = athlete.get("firstName", "")
            last_name = athlete.get("lastName", "")

            if not last_name and display_name:
                parts = display_name.split()
                if len(parts) == 1:
                    last_name = parts[0]
                    first_name = ""
                else:
                    last_name = parts[-1]
                    first_name = " ".join(parts[:-1])

            wt = athlete.get("weight", 0) or 0

            players.append(
                Player(
                    id=int(athlete.get("id", 0)),
                    name=display_name,
                    first_name=first_name,
                    last_name=last_name,
                    age=athlete.get("age", 25) or 25,
                    nationality=athlete.get("citizenship", ""),
                    position=pos_abbrev,
                    number=number,
                    photo_url="",
                    weight=float(wt),
                )
            )
        return players

    def _extract_pid(self, athlete) -> str | None:
        """Extract player ID from athlete obj or $ref link."""
        if isinstance(athlete, dict):
            if "id" in athlete:
                return str(athlete["id"])
            ref = athlete.get("$ref", "")
            if "/athletes/" in ref:
                return ref.split("/athletes/")[-1].split("?")[0]
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _league_from_item(self, item: dict, season: int | None = None) -> League:
        """One `ESPN_LEAGUES` entry as a `League`.

        The current calendar year is the fallback and not the answer: it is what
        `get_featured_leagues` wants, since a featured list has no season in the
        question, and it is what a caller who named no season gets.
        """
        from datetime import datetime

        return League(
            id=item["id"],
            name=item["name"],
            country=item["country"],
            country_code="",
            logo_url="",
            season=season or datetime.now().year,
            teams_count=0,
        )

    def _request(self, path: str, sport: str = "soccer") -> dict:
        if sport == "hockey":
            base = HOCKEY_BASE_URL
        elif sport == "baseball":
            base = BASEBALL_BASE_URL
        elif sport == "basketball":
            base = BASKETBALL_BASE_URL
        else:
            base = SOCCER_BASE_URL
        # Outside the `try`: only the request is meant to be guarded. A raising
        # status callback is a caller bug, not a failed fetch, and swallowing it
        # here turned it into an empty payload.
        if self.on_status:
            self.on_status(f"Fetching{path}...")
        try:
            data = _http.get_json(base + path, transport=self._transport)
        except Exception:
            return {}
        return data

    def _load_cache(self, cache_key: str) -> dict | None:
        path = os.path.join(self.cache_dir, f"{cache_key}.json")
        if os.path.exists(path):
            try:
                with open(path) as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return None

    def _save_cache(self, cache_key: str, data: dict):
        path = os.path.join(self.cache_dir, f"{cache_key}.json")
        with open(path, "w") as f:
            json.dump(data, f)

    def _find_league_code_for_team(self, team_id: int, season: int | None = None) -> str | None:
        """Find which league a team belongs to by checking cached team lists.

        The key here has to be the one `get_teams` writes, which since the season
        joined it is `espn_teams_{id}_{season or 'any'}`. This method was still
        reading `espn_teams_{id}`, a name nothing writes any more, so it found
        nothing whatever was cached and always answered `None` — and a `None` here
        makes `get_squad` return an empty list without issuing a request, which
        is silent. Both callers now hand their season down.

        Only the season's own key is consulted, and deliberately: the season is in
        every key in this client precisely so that one season's answer is never
        served for another's question, and a fallback to a neighbouring season's
        team list would resolve a team that changed competition to the wrong
        league code. A caller that has not fetched the league's teams for the
        season it is asking about gets `None`, which is the honest answer.
        """
        for item in ESPN_LEAGUES:
            cached = self._load_cache(f"espn_teams_{item['id']}_{season or 'any'}")
            if not cached:
                continue
            if any(t.id == team_id for t in self._parse_teams(cached)):
                return str(item["code"])
        return None

    def _parse_athlete_stats(self, athlete_id: int, data: dict) -> PlayerStats | None:
        """Build a `PlayerStats` from one athlete's ESPN statistics document.

        `splits.categories[]` is a list of named groups — `defensive`, `general`,
        `goalKeeping`, `offensive` — each holding `stats[]` of `{name, value,
        displayValue}`. Every value arrives as a float, including the counts, so
        the integer fields are converted rather than assigned.

        `general.passPct` is a *fraction*: the recorded document reads `0.768`
        where `displayValue` says `"0.8"`. `PlayerStats.passes_accuracy` is
        declared a percentage, so it is scaled by 100 here. Getting this wrong is
        invisible — `pass_accuracy` is percentiled league-wide, and scaling every
        player's value by the same constant leaves the ranking, and so every
        rating, completely unchanged. It only shows up against a concrete number,
        which is why `test_espn.py` asserts 76.8 and not a band.

        Returns `None` for a document with no categories at all, so that a
        goalkeeper-only or empty record does not become a player with twenty
        zeroes, which `map_player` would read as a real season.
        """
        splits = data.get("splits")
        categories = splits.get("categories") if isinstance(splits, dict) else None
        if not categories:
            return None
        groups: dict[str, dict[str, float]] = {}
        for category in categories:
            if not isinstance(category, dict):
                continue
            groups[str(category.get("name", ""))] = {
                str(stat.get("name", "")): _as_float(stat.get("value"))
                for stat in category.get("stats") or []
                if isinstance(stat, dict)
            }
        general = groups.get("general", {})
        offensive = groups.get("offensive", {})
        defensive = groups.get("defensive", {})

        rating = None
        for name in _RATING_FIELDS:
            if general.get(name, 0.0) > 0:
                rating = general[name]
                break

        return PlayerStats(
            player_id=athlete_id,
            appearances=int(general.get("appearances", 0)),
            minutes=int(general.get("minutes", 0)),
            goals=int(offensive.get("totalGoals", 0)),
            assists=int(offensive.get("goalAssists", 0)),
            shots_total=int(offensive.get("totalShots", 0)),
            shots_on=int(offensive.get("shotsOnTarget", 0)),
            passes_total=int(offensive.get("totalPasses", 0)),
            passes_accuracy=general.get("passPct", 0.0) * 100,
            tackles_total=int(defensive.get("totalTackles", 0)),
            interceptions=int(defensive.get("interceptions", 0)),
            blocks=int(defensive.get("blockedShots", 0)),
            duels_total=0,
            duels_won=0,
            dribbles_attempts=0,
            dribbles_success=0,
            fouls_committed=int(general.get("foulsCommitted", 0)),
            fouls_drawn=int(general.get("foulsSuffered", 0)),
            cards_yellow=int(general.get("yellowCards", 0)),
            cards_red=int(general.get("redCards", 0)),
            rating=rating,
            # `starts`, not `appearances`: this drives `_select_best_22`, which
            # orders a squad by times in the starting XI.
            lineups=int(general.get("starts", 0)),
            # The four zeroes above are filler and this is what says so. Without
            # it they are indistinguishable from a measurement, and the three
            # attributes computed from them collapse to the league floor.
            unsupplied=SOCCER_UNSUPPLIED_STATS,
        )

    def _parse_teams(self, data: dict) -> list[Team]:
        if not isinstance(data, dict):
            return []
        # `or [{}]` rather than a `.get` default: ESPN sends an empty list — not a
        # missing key — when it has nothing to report, and the default only covers
        # the missing-key case, so indexing [0] raised IndexError.
        sports = data.get("sports") or [{}]
        leagues = sports[0].get("leagues") or [{}]
        teams_raw = leagues[0].get("teams") or []
        teams = []
        for entry in teams_raw:
            t = entry.get("team", {})
            teams.append(
                Team(
                    id=int(t.get("id", 0)),
                    name=t.get("displayName", t.get("name", "")),
                    short_name=t.get("shortDisplayName", t.get("name", ""))[:12],
                    code=(t.get("abbreviation", "") or "")[:3],
                    logo_url=(t.get("logos", [{}])[0].get("href", "") if t.get("logos") else ""),
                    country="",
                    color=t.get("color", ""),
                    alternate_color=t.get("alternateColor", ""),
                )
            )
        return teams

    def _parse_squad(self, data: dict) -> list[Player]:
        if not isinstance(data, dict):
            return []
        players = []
        for athlete in data.get("athletes", []):
            pos_info = athlete.get("position", {})
            pos_name = (
                pos_info.get("name", "Midfielder") if isinstance(pos_info, dict) else "Midfielder"
            )
            if "Goalkeeper" in pos_name:
                position = "Goalkeeper"
            elif "Defender" in pos_name or "Back" in pos_name:
                position = "Defender"
            elif "Forward" in pos_name or "Striker" in pos_name or "Winger" in pos_name:
                position = "Attacker"
            else:
                position = "Midfielder"

            jersey = athlete.get("jersey")
            number = int(jersey) if jersey and str(jersey).isdigit() else None

            display_name = athlete.get("displayName", athlete.get("fullName", ""))
            first_name = athlete.get("firstName", "")
            last_name = athlete.get("lastName", "")

            # ESPN often leaves lastName empty for mononym players (Hulk,
            # Paulinho) and compound-name players (Carlos Miguel, Felipe
            # Anderson).  Split displayName so last_name gets the surname
            # and first_name gets the given name.
            if not last_name and display_name:
                parts = display_name.split()
                if len(parts) == 1:
                    last_name = parts[0]
                    first_name = ""
                else:
                    last_name = parts[-1]
                    first_name = " ".join(parts[:-1])

            players.append(
                Player(
                    id=int(athlete.get("id", 0)),
                    name=display_name,
                    first_name=first_name,
                    last_name=last_name,
                    age=athlete.get("age", 25) or 25,
                    nationality=athlete.get("citizenship", ""),
                    position=position,
                    number=number,
                    photo_url="",
                )
            )
        return players

    def _parse_hockey_squad(self, data: dict) -> list[Player]:
        """Parse NHL team roster with hockey-specific positions.

        ESPN hockey roster groups athletes by position:
        athletes: [{position: "Centers", items: [...]}, ...]

        Preserves exact position abbreviation (C, LW, RW, D, G)
        and sorts each group by experience (descending) so starters
        come first.
        """
        if not isinstance(data, dict):
            return []

        # Collect players per group, sort by experience desc
        groups = []
        for group in data.get("athletes", []):
            items = group.get("items", [])
            # Sort by experience years descending (starters first)
            items.sort(
                key=lambda a: (
                    a.get("experience", {}).get("years", 0)
                    if isinstance(a.get("experience"), dict)
                    else 0
                ),
                reverse=True,
            )
            groups.append(items)

        players = []
        for items in groups:
            for athlete in items:
                pos_info = athlete.get("position", {})
                pos_abbrev = (
                    pos_info.get("abbreviation", "C") if isinstance(pos_info, dict) else "C"
                ).upper()
                # Normalize rare variants
                if pos_abbrev in ("LD", "RD"):
                    pos_abbrev = "D"
                elif pos_abbrev == "F":
                    pos_abbrev = "C"

                jersey = athlete.get("jersey")
                number = int(jersey) if jersey and str(jersey).isdigit() else None

                display_name = athlete.get("displayName", athlete.get("fullName", ""))
                first_name = athlete.get("firstName", "")
                last_name = athlete.get("lastName", "")

                if not last_name and display_name:
                    parts = display_name.split()
                    if len(parts) == 1:
                        last_name = parts[0]
                        first_name = ""
                    else:
                        last_name = parts[-1]
                        first_name = " ".join(parts[:-1])

                # Weight and handedness
                wt = athlete.get("weight", 0) or 0
                hand_info = athlete.get("hand", {})
                hand = hand_info.get("abbreviation", "") if isinstance(hand_info, dict) else ""

                players.append(
                    Player(
                        id=int(athlete.get("id", 0)),
                        name=display_name,
                        first_name=first_name,
                        last_name=last_name,
                        age=athlete.get("age", 25) or 25,
                        nationality=athlete.get("citizenship", ""),
                        position=pos_abbrev,
                        number=number,
                        photo_url="",
                        weight=float(wt),
                        handedness=hand,
                    )
                )
        return players
