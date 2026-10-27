"""ESPN public API client — no API key required.

Two hosts. `site.api.espn.com` serves team lists and rosters for all four sports;
`sports.core.api.espn.com` serves per-player statistics, as team "leaders" for
hockey, baseball and basketball and as a per-athlete statistics document for
soccer. Both are keyless and neither rate-limits.
"""

import json
import os
from collections.abc import Callable
from datetime import date
from typing import Any

from ..core.concurrency import emit_status, parallel_map
from ..core.errors import ApiError, ensure_cache_dir
from . import _http
from .models import League, Player, PlayerStats, Team

# Maps our internal league IDs to ESPN league codes. Never renumber these: they
# are what `--league-id` takes, so a change silently repoints every saved
# command line and rosters file.
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
    {"id": 2017, "code": "bra.2", "name": "Brasileirao Serie B", "country": "Brazil"},
    {
        "id": 2018,
        "code": "conmebol.sudamericana",
        "name": "Copa Sudamericana",
        "country": "South America",
    },
    {"id": 2019, "code": "fifa.world", "name": "FIFA World Cup", "country": "World"},
    {
        "id": 2020,
        "code": "fifa.wwc",
        "name": "FIFA Women's World Cup",
        "country": "World",
    },
    # Every men's national side ESPN tracks, with current squads: the source for
    # a game's national-team slots.
    {
        "id": 2021,
        "code": "fifa.friendly",
        "name": "International Friendlies",
        "country": "World",
    },
]

#: League code prefixes whose season runs inside one calendar year. ESPN names
#: those by that year (bra.1 2026 is 2026) and every other league by the year
#: its August-to-May season starts in (eng.1 2026 is 2026-27).
CALENDAR_YEAR_LEAGUES = ("bra.", "usa.", "arg.", "jpn.", "col.", "chi.", "conmebol.", "fifa.")


def default_season(code: str, today: date | None = None) -> int:
    """The season to fetch for ESPN league `code` on `today`: the current one
    once it has games (from March for a calendar-year league, from August for
    the others), else the one before, which has a full season of numbers."""
    today = today or date.today()
    start = 3 if code.lower().startswith(CALENDAR_YEAR_LEAGUES) else 8
    return today.year if today.month >= start else today.year - 1


#: ESPN lineup position codes (`get_lineup` starters), by squad group.
LINEUP_GROUPS = {
    "G": "Goalkeeper",
    "CD": "Defender",
    "CD-L": "Defender",
    "CD-R": "Defender",
    "LB": "Defender",
    "RB": "Defender",
    "LWB": "Defender",
    "RWB": "Defender",
    "SW": "Defender",
    "DM": "Midfielder",
    "CM": "Midfielder",
    "CM-L": "Midfielder",
    "CM-R": "Midfielder",
    "LM": "Midfielder",
    "RM": "Midfielder",
    "AM": "Midfielder",
    "AM-L": "Midfielder",
    "AM-R": "Midfielder",
    "F": "Attacker",
    "CF": "Attacker",
    "CF-L": "Attacker",
    "CF-R": "Attacker",
    "LW": "Attacker",
    "RW": "Attacker",
    "LF": "Attacker",
    "RF": "Attacker",
    "LCF": "Attacker",
    "RCF": "Attacker",
}

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

#: Completed matches a lineup is read from: the eleven who started most often.
LINEUP_MATCHES = 5

#: Completed MLB games a batting order is read from (`get_baseball_lineup`).
LINEUP_GAMES = 10

_ID_TO_LEAGUE = {item["id"]: item for item in ESPN_LEAGUES}
_CODE_TO_LEAGUE = {item["code"]: item for item in ESPN_LEAGUES}

# The `PlayerStats` fields ESPN's soccer statistics document has no counterpart
# for. Every record built here declares these in `PlayerStats.unsupplied`.
SOCCER_UNSUPPLIED_STATS = (
    "duels_total",
    "duels_won",
    "dribbles_attempts",
    "dribbles_success",
)

# ESPN publishes four average-match-rating fields and, for soccer, leaves all
# four at 0.0. The first populated one wins; `PlayerStats.rating` is `None` when
# none of them is.
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


# A season with fewer games than this is too young to rate a player on; the
# previous one stands in (see `EspnClient._season_line`).
MIN_GAMES = 10

# ESPN statistic `name` -> the abbreviation the game stat mappers read. Basketball
# lines are per game, hockey lines are season totals, as the mappers scale them.
# Hockey's `SOG` is shots on goal here: ESPN's own `SOG` abbreviation is shootout
# goals.
_MAPPER_KEYS = {
    "basketball": {
        "gamesPlayed": "GP",
        "avgMinutes": "MPG",
        "avgPoints": "PTS",
        "avgRebounds": "REB",
        "avgAssists": "AST",
        "avgSteals": "STL",
        "avgBlocks": "BLK",
        "avgOffensiveRebounds": "ORPG",
        "avgDefensiveRebounds": "DRPG",
        "avgTurnovers": "TO",
        "fieldGoalPct": "FG%",
        "threePointFieldGoalPct": "3P%",
        "freeThrowPct": "FT%",
        "PER": "PER",
    },
    "hockey": {
        "games": "GP",
        "goals": "G",
        "assists": "A",
        "points": "PTS",
        "plusMinus": "+/-",
        "penaltyMinutes": "PIM",
        "shotsTotal": "SOG",
        "faceoffPercent": "FO%",
        "savePct": "SV%",
        "avgGoalsAgainst": "GAA",
        "wins": "W",
        "shutouts": "SO",
    },
}


def _as_float(value: Any) -> float:
    """A statistic's numeric value, or `0.0` for anything that is not one.

    A `null` in one ESPN field must not cost the rest of the document.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)


class EspnClient:
    """Client for ESPN's public API — no key, no rate limits."""

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

        `season` is carried onto the `League` and nothing else, but it must be:
        it ends up on the `LeagueData` that `serde` writes to the rosters file.
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
        season parameter and answers with the current table.
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
        # Cache what parsed, not what arrived: a body carrying zero teams is
        # still a truthy dict, and caching it freezes the empty answer on disk.
        teams = self._parse_teams(data)
        if teams:
            self._save_cache(cache_key, data)
        return teams

    def get_squad(
        self, team_id: int, season: int | None = None, league_code: str | None = None
    ) -> list[Player]:
        """Fetch current squad for a team.

        `season` reaches the cache key only; see `get_hockey_squad`. Keep
        `season` second and `league_code` third: callers pass the season
        positionally, and swapping them silently empties every squad.
        """
        # The endpoint needs the league code, so resolve it before the cache
        # lookup: team ids are league-scoped, and a key of the id alone would
        # serve one competition's roster for another's request.
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

        Two steps, because there is no bulk endpoint — `/athletes` on a team is a
        404:

          * the team's `leaders` document enumerates the athletes who have any
            statistic this season, as `$ref` links, twelve categories deep;
          * each athlete's own `statistics` document carries the fields
            `_parse_athlete_stats` reads.

        That is one request plus one per athlete, so the per-athlete cache is
        load-bearing rather than an optimisation.

        Only the athletes the leaders document names get a record; the rest of a
        squad reaches `StatMapper.map_player` with no stats.
        """
        code = league_code or self._find_league_code_for_team(team_id, season)
        if not code:
            return []
        athlete_ids = self._soccer_stat_athletes(team_id, code, season)
        # One request per athlete and no bulk endpoint, so overlap them; the input
        # order is kept, which keeps the result identical to a serial run.
        parsed = parallel_map(
            lambda athlete_id: self._soccer_athlete_stats(team_id, code, season, athlete_id),
            athlete_ids,
        )
        return [ps for ps in parsed if ps is not None]

    def _soccer_stat_athletes(self, team_id: int, code: str, season: int) -> list[int]:
        """Ids of the athletes the team's leaders document names, in first-seen order.

        The categories name the same athletes repeatedly, so this deduplicates;
        the order is stable so the result list is the same whether the athlete
        documents came from cache or from the network. It does not order the
        per-athlete requests, which are issued concurrently.
        """
        cache_key = f"espn_soccer_leaders_{code}_{team_id}_{season}"
        document = self._load_cache(cache_key)
        if document is None:
            url = f"{SOCCER_CORE_URL}/{code}/seasons/{season}/types/1/teams/{team_id}/leaders"
            emit_status(self.on_status, f"Fetching stats for team {team_id}...")
            try:
                document = _http.get_json(url, transport=self._transport)
            except Exception:
                return []
            if not isinstance(document, dict):
                return []
            # Cache only what named at least one athlete: an empty body is a
            # truthy response and caching it freezes the empty answer on disk.
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
                # `_extract_pid` answers with whatever sat after `/athletes/`,
                # so an unrecognised link arrives non-numeric, not as `None`.
                if pid is None or not pid.isdigit() or pid in seen:
                    continue
                seen.add(pid)
                athlete_ids.append(int(pid))
        return athlete_ids

    def _soccer_athlete_stats(
        self, team_id: int, code: str, season: int, athlete_id: int
    ) -> PlayerStats | None:
        """One athlete's statistics document, parsed, or `None` if there is none.

        Keep the athlete in the cache key: this is the request a league fetch
        makes hundreds of times, and caching per team would mean one interrupted
        run costs every athlete in it. A failed request costs only this athlete.
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
        the current squad whatever the caller wants. Keep it in the *cache key*
        anyway: without a time coordinate the first fetch ever run freezes that
        squad on disk, and every later `--season` replays it as a success.
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
        """Every squad player's season line, `{player id (str): {stat: value}}`.

        Each line is the player's own statistics document (see
        `_season_line`), laid over what the team leaders endpoint reported for
        him; the leaders alone cover only the few players who top a category.
        """
        leaders = self._leaders(HOCKEY_CORE_URL, "hockey", team_id, season)
        squad = self.get_hockey_squad(team_id, season)
        lines = parallel_map(lambda p: self._season_line("hockey", p, season), squad)
        return leaders | {
            str(p.id): {**leaders.get(str(p.id), {}), **line}
            for p, line in zip(squad, lines, strict=True)
            if line
        }

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
        """Every squad player's season line, `{player id (str): {stat: value}}`.

        Each line is the player's own statistics document (see
        `_season_line`), laid over what the team leaders endpoint reported for
        him; the leaders alone cover only the few players who top a category.
        """
        leaders = self._leaders(BASEBALL_CORE_URL, "baseball", team_id, season)
        squad = self.get_baseball_squad(team_id, season)
        lines = parallel_map(lambda p: self._season_line("baseball", p, season), squad)
        return leaders | {
            str(p.id): {**leaders.get(str(p.id), {}), **line}
            for p, line in zip(squad, lines, strict=True)
            if line
        }

    def get_baseball_athlete_stats(self, athlete_id: int, season: int) -> dict[str, dict]:
        """One MLB player's regular-season line by category, `{"batting": {"AVG":
        0.284, "HR": 25.0, ...}, "pitching": {...}, "fielding": {...}}`, keyed by
        ESPN's abbreviations (the first of a repeated one wins). `{}` when ESPN
        has none.

        One request per player, so the line is cached; an empty answer is not,
        so a failed request is retried by the next fetch.
        """
        cache_key = f"espn_baseball_stats_{athlete_id}_{season}"
        cached = self._load_cache(cache_key)
        if cached:
            return cached
        url = f"{BASEBALL_CORE_URL}/seasons/{season}/types/2/athletes/{athlete_id}/statistics"
        try:
            data = _http.get_json(url, transport=self._transport)
        except Exception:
            return {}
        lines: dict[str, dict] = {}
        for category in (data.get("splits") or {}).get("categories") or []:
            line = lines.setdefault(str(category.get("name", "")), {})
            for stat in category.get("stats") or []:
                line.setdefault(str(stat.get("abbreviation", "")), _as_float(stat.get("value")))
        if lines:
            self._save_cache(cache_key, lines)
        return lines

    def get_baseball_lineup(
        self, team_id: int, season: int, today: date | None = None
    ) -> list[dict[str, Any]]:
        """Where a club's batters have batted lately: `[{"id", "order",
        "starts"}, ...]`, `order` being the average lineup spot (1-9) of each
        player who started any of its last `LINEUP_GAMES` regular-season games
        of `season` up to `today`, and `starts` how many of them he started;
        sorted by `order`. `[]` when ESPN has none.

        One request for the game list, then one per game, cached per game since
        a played game does not change.
        """
        last = min(today or date.today(), date(season, 12, 31))
        if last.year < season:
            return []
        url = (
            f"{BASEBALL_CORE_URL}/seasons/{season}/types/2/teams/{team_id}/events"
            f"?limit=200&dates={season}0101-{last:%Y%m%d}"
        )
        try:
            items = _http.get_json(url, transport=self._transport).get("items") or []
        except Exception:
            return []
        refs = [str(item.get("$ref", "")) for item in items]
        events = [ref.split("/events/")[1].split("?")[0] for ref in refs if "/events/" in ref]
        games = parallel_map(
            lambda event: self._baseball_starters(team_id, event), events[-LINEUP_GAMES:]
        )
        orders: dict[int, list[int]] = {}
        for game in games:
            for pid, order in game:
                orders.setdefault(pid, []).append(order)
        lineup = [
            {"id": pid, "order": sum(spots) / len(spots), "starts": len(spots)}
            for pid, spots in orders.items()
        ]
        return sorted(lineup, key=lambda starter: starter["order"])

    def get_basketball_starters(
        self, team_id: int, season: int, today: date | None = None
    ) -> dict[int, int]:
        """How many of the club's last `LINEUP_GAMES` played regular-season
        games each player started, `{player id: starts}`; the previous season's
        last games make up a season with fewer than `LINEUP_GAMES / 2` so far.
        `{}` when ESPN has none.

        One request per season's game list, then one per game, cached per game
        since a played game does not change.
        """
        last = today or date.today()
        events: list[str] = []
        for year in (season, season - 1):
            stop = min(last, date(year, 6, 30))
            if stop < date(year - 1, 10, 1):
                continue
            url = (
                f"{BASKETBALL_CORE_URL}/seasons/{year}/types/2/teams/{team_id}/events"
                f"?limit=200&dates={year - 1}1001-{stop:%Y%m%d}"
            )
            try:
                items = _http.get_json(url, transport=self._transport).get("items") or []
            except Exception:
                items = []
            refs = [str(item.get("$ref", "")) for item in items]
            ids = [r.split("/events/")[1].split("?")[0] for r in refs if "/events/" in r]
            events = ids + events
            if len(events) >= LINEUP_GAMES // 2:
                break
        games = parallel_map(
            lambda event: self._basketball_starters(team_id, event), events[-LINEUP_GAMES:]
        )
        starts: dict[int, int] = {}
        for game in games:
            for pid in game:
                starts[pid] = starts.get(pid, 0) + 1
        return starts

    def _basketball_starters(self, team_id: int, event_id: str) -> list[int]:
        """One game's five starters for `team_id`, `[]` for a game not played.
        Cached once it has any."""
        cache_key = f"espn_basketball_starters_{team_id}_{event_id}"
        cached = self._load_cache(cache_key)
        if cached:
            return [int(pid) for pid in cached["starters"]]
        url = (
            f"{BASKETBALL_CORE_URL}/events/{event_id}/competitions/{event_id}"
            f"/competitors/{team_id}/roster"
        )
        try:
            entries = _http.get_json(url, transport=self._transport).get("entries") or []
        except Exception:
            return []
        starters = [int(e.get("playerId") or 0) for e in entries if e.get("starter")]
        if starters:
            self._save_cache(cache_key, {"starters": starters})
        return starters

    def _baseball_starters(self, team_id: int, event_id: str) -> list[tuple[int, int]]:
        """One game's starting batters for `team_id` as (player id, lineup spot),
        `[]` for a game not played. Cached once it has any."""
        cache_key = f"espn_baseball_starters_{team_id}_{event_id}"
        cached = self._load_cache(cache_key)
        if cached:
            return [(int(pid), int(order)) for pid, order in cached["starters"]]
        url = (
            f"{BASEBALL_CORE_URL}/events/{event_id}/competitions/{event_id}"
            f"/competitors/{team_id}/roster"
        )
        try:
            entries = _http.get_json(url, transport=self._transport).get("entries") or []
        except Exception:
            return []
        starters = [
            (int(entry.get("playerId") or 0), entry["batOrder"])
            for entry in entries
            if entry.get("starter") and entry.get("batOrder") in range(1, 10)
        ]
        if starters:
            self._save_cache(cache_key, {"starters": starters})
        return starters

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

                # Pounds and inches, as `_parse_hockey_squad` reads them. Zero
                # means "not reported"; a consumer must treat it that way.
                weight = athlete.get("weight", 0) or 0
                height = athlete.get("height", 0) or 0
                headshot = athlete.get("headshot")
                photo = headshot.get("href", "") if isinstance(headshot, dict) else ""

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
                        photo_url=photo,
                        weight=float(weight),
                        height=float(height),
                        handedness=throw_hand,
                        bats=bat_hand,
                    )
                )
        return players

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
        """Every squad player's season line, `{player id (str): {stat: value}}`.

        Each line is the player's own statistics document (see
        `_season_line`), laid over what the team leaders endpoint reported for
        him; the leaders alone cover only the few players who top a category.
        """
        leaders = self._leaders(BASKETBALL_CORE_URL, "basketball", team_id, season)
        squad = self.get_basketball_squad(team_id, season)
        lines = parallel_map(lambda p: self._season_line("basketball", p, season), squad)
        return leaders | {
            str(p.id): {**leaders.get(str(p.id), {}), **line}
            for p, line in zip(squad, lines, strict=True)
            if line
        }

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
                    photo_url=(athlete.get("headshot") or {}).get("href", "")
                    if isinstance(athlete.get("headshot"), dict)
                    else "",
                    weight=float(wt),
                    height=float(athlete.get("height", 0) or 0),
                )
            )
        return players

    def _leaders(self, core_url: str, sport: str, team_id: int, season: int) -> dict:
        """The team leaders endpoint as `{player id (str): {abbreviation: value}}`."""
        cache_key = f"espn_{sport}_leaders_{team_id}_{season}"
        cached = self._load_cache(cache_key)
        if cached:
            return cached
        url = f"{core_url}/seasons/{season}/types/2/teams/{team_id}/leaders"
        try:
            data = _http.get_json(url, transport=self._transport)
        except Exception:
            return {}
        stats: dict[str, dict[str, Any]] = {}
        for cat in data.get("categories", []):
            abbrev = cat.get("abbreviation", "")
            for entry in cat.get("leaders", []):
                pid = self._extract_pid(entry.get("athlete", {}))
                if pid:
                    stats.setdefault(pid, {})[abbrev] = entry.get("value", 0)
        if stats:
            self._save_cache(cache_key, stats)
        return stats

    def _season_line(self, sport: str, player: Player, season: int) -> dict[str, float]:
        """One player's regular-season line for the game mappers: every ESPN stat
        under its `name` (season totals, `avg...` per game) plus the
        abbreviations the mappers read (`_MAPPER_KEYS`). For baseball, the
        pitching line of a pitcher and the batting line of anyone else.

        A season under `MIN_GAMES` games old is a handful of games, so the
        previous season stands in when it has more. `{}` when ESPN has neither.
        """

        def line(year: int) -> dict[str, float]:
            if sport == "baseball":
                cats = self.get_baseball_athlete_stats(player.id, year)
                kind = "pitching" if player.position in ("P", "SP", "RP") else "batting"
                return dict(cats.get(kind, {}))
            named = self._athlete_named_stats(sport, player.id, year)
            keys = _MAPPER_KEYS[sport]
            return {**named, **{keys[n]: v for n, v in named.items() if n in keys}}

        def games(stats: dict[str, float]) -> float:
            return stats.get("GP", 0.0)

        now = line(season)
        if games(now) < MIN_GAMES:
            before = line(season - 1)
            if games(before) > games(now):
                return before
        return now

    def _athlete_named_stats(self, sport: str, athlete_id: int, season: int) -> dict[str, float]:
        """An NBA or NHL player's statistics document as `{stat name: value}`.

        Keyed by `name`, not abbreviation: the abbreviation repeats for a total,
        a per-game average and a per-48 rate (`PTS` three times). Cached; an
        empty answer is not, so a failed request is retried by the next fetch.
        """
        cache_key = f"espn_{sport}_named_{athlete_id}_{season}"
        cached = self._load_cache(cache_key)
        if cached:
            return cached
        core = {"basketball": BASKETBALL_CORE_URL, "hockey": HOCKEY_CORE_URL}[sport]
        url = f"{core}/seasons/{season}/types/2/athletes/{athlete_id}/statistics"
        try:
            data = _http.get_json(url, transport=self._transport)
        except Exception:
            return {}
        named: dict[str, float] = {}
        for category in (data.get("splits") or {}).get("categories") or []:
            for stat in category.get("stats") or []:
                named.setdefault(str(stat.get("name", "")), _as_float(stat.get("value")))
        if named:
            self._save_cache(cache_key, named)
        return named

    def _extract_pid(self, athlete) -> str | None:
        """Extract player ID from athlete obj or $ref link."""
        if isinstance(athlete, dict):
            if "id" in athlete:
                return str(athlete["id"])
            ref = athlete.get("$ref", "")
            if "/athletes/" in ref:
                return ref.split("/athletes/")[-1].split("?")[0]
        return None

    def get_lineup(self, team_id: int, league_id: int) -> dict[str, Any] | None:
        """The team's starting eleven from its last few completed matches, or None.

        One request for the team schedule, then one per match summary (up to
        `LINEUP_MATCHES`), whose `rosters` carry each side's formation and who
        started where. The eleven who started most often make the lineup, so a
        side rested for one match does not become the team; ties go to the
        more recent starter, and each keeps the position of his latest start.
        The formation is the latest match's. Returned as plain data
        (`{"formation": "4-2-3-1", "event": id, "starters": [{"id",
        "position", "number", "place"}, ...]}`) so it rides along in
        `TeamRoster.extra` through the JSON boundary. Cached per match.
        """
        item = _ID_TO_LEAGUE.get(league_id)
        if item is None:
            return None
        code = str(item["code"])
        try:
            schedule = self._request(f"/{code}/teams/{team_id}/schedule", sport="soccer")
        except ApiError:
            return None
        done = [
            e
            for e in schedule.get("events") or []
            if (e.get("competitions") or [{}])[0].get("status", {}).get("type", {}).get("completed")
        ]
        recent = sorted(done, key=lambda e: str(e.get("date", "")), reverse=True)[:LINEUP_MATCHES]
        lineups = [lu for e in recent if (lu := self._match_lineup(code, team_id, e)) is not None]
        if not lineups:
            return None
        latest = lineups[0]
        if len(lineups) == 1:
            return latest
        starts: dict[int, int] = {}
        seen: dict[int, dict[str, Any]] = {}
        for lineup in lineups:  # newest first, so the first sighting is the latest
            for starter in lineup["starters"]:
                starts[starter["id"]] = starts.get(starter["id"], 0) + 1
                seen.setdefault(starter["id"], starter)
        order = sorted(starts, key=lambda pid: (-starts[pid], list(seen).index(pid)))
        return {
            "formation": latest["formation"],
            "event": latest["event"],
            "starters": [seen[pid] for pid in order[:11]],
        }

    def _match_lineup(self, code: str, team_id: int, event: dict) -> dict[str, Any] | None:
        """One match's formation and starters for `team_id`, cached per match."""
        cache_key = f"espn_lineup_{code}_{team_id}_{event.get('id')}"
        cached = self._load_cache(cache_key)
        if cached:
            return dict(cached)
        try:
            summary = self._request(f"/{code}/summary?event={event['id']}", sport="soccer")
        except ApiError:
            return None
        for side in summary.get("rosters") or []:
            if str(side.get("team", {}).get("id")) != str(team_id):
                continue
            starters = [
                {
                    "id": int(p["athlete"]["id"]),
                    "position": str(p.get("position", {}).get("abbreviation", "")),
                    "number": int(p["jersey"]) if str(p.get("jersey", "")).isdigit() else None,
                    "place": int(p.get("formationPlace") or 0),
                }
                for p in side.get("roster") or []
                if p.get("starter") and p.get("athlete", {}).get("id")
            ]
            lineup = {
                "formation": str(side.get("formation", "")),
                "event": event.get("id"),
                "starters": starters,
            }
            self._save_cache(cache_key, lineup)
            return lineup
        return None

    def get_league_logo(self, league_id: int) -> str:
        """The league's logo URL from its scoreboard, "" when ESPN has none.

        The leagues catalog carries no artwork, so this is one extra request,
        cached under its own key; a failed request is an empty string, not an
        error, because a logo is decoration.
        """
        item = _ID_TO_LEAGUE.get(league_id)
        if item is None:
            return ""
        cache_key = f"espn_league_logo_{league_id}"
        cached = self._load_cache(cache_key)
        if cached is not None and "logo" in cached:
            return str(cached["logo"])
        try:
            data = self._request(f"/{item['code']}/scoreboard", sport="soccer")
        except ApiError:
            return ""
        leagues = data.get("leagues") or []
        logos = (leagues[0].get("logos") or []) if leagues else []
        logo = str(logos[0].get("href", "")) if logos else ""
        self._save_cache(cache_key, {"logo": logo})
        return logo

    def _league_from_item(self, item: dict, season: int | None = None) -> League:
        """One `ESPN_LEAGUES` entry as a `League`.

        The current calendar year is the fallback for a caller that named no
        season, not the answer.
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
        # Keep outside the `try`: only the request is meant to be guarded, and a
        # raising status callback is a caller bug, not a failed fetch.
        emit_status(self.on_status, f"Fetching{path}...")
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

        The key must stay identical to the one `get_teams` writes, or this
        silently answers `None` and `get_squad` returns empty without a request.

        Consult only the season's own key: falling back to a neighbouring
        season's team list resolves a team that changed competition to the wrong
        league code.
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
        displayValue}`. Every value arrives as a float, counts included.

        `general.passPct` is a *fraction* (`0.768`), so it is scaled by 100 into
        `passes_accuracy`, which is declared a percentage.

        Returns `None` for a document with no categories at all, so an empty
        record does not become a player with twenty zeroes.
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
            # `starts`, not `appearances`: consumers order a squad by times in
            # the starting XI.
            lineups=int(general.get("starts", 0)),
            # Marks the four zeroes above as filler, not as a measurement.
            unsupplied=SOCCER_UNSUPPLIED_STATS,
        )

    def _parse_teams(self, data: dict) -> list[Team]:
        if not isinstance(data, dict):
            return []
        # `or [{}]`, not a `.get` default: ESPN sends an empty list rather than
        # omitting the key, and indexing [0] on it raises IndexError.
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

            # ESPN often leaves lastName empty for mononym players (Hulk) and
            # compound-name players (Felipe Anderson), so split displayName.
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
                    photo_url=(
                        athlete["headshot"].get("href", "")
                        if isinstance(athlete.get("headshot"), dict)
                        else ""
                    ),
                    birth_date=str(athlete.get("dateOfBirth") or "")[:10],
                    weight=float(athlete.get("weight", 0) or 0),
                    height=float(athlete.get("height", 0) or 0),
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

        groups = []
        for group in data.get("athletes", []):
            items = group.get("items", [])
            # Experience years descending, so starters come first.
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
                # Rare variants ESPN emits for defence and forward.
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

                wt = athlete.get("weight", 0) or 0
                ht = athlete.get("height", 0) or 0
                headshot = athlete.get("headshot")
                photo = headshot.get("href", "") if isinstance(headshot, dict) else ""
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
                        photo_url=photo,
                        weight=float(wt),
                        height=float(ht),
                        handedness=hand,
                    )
                )
        return players
