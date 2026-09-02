"""API-Football client with local JSON caching.

The three exception classes below are `ApiError` subclasses. All three describe
what `ApiError` already names — "an upstream sports API failed, rate-limited, or
returned nothing usable" — and all three are raised on paths a caller reaches
through `Patcher.fetch`, whose interface docstring promises `ApiError`. As bare
`Exception`s they walked straight out of the CLI: a free-plan season restriction
is the commonest failure a free-tier user meets, and it ended the NDJSON stream
after a single `progress` event with no terminal `error`.
"""

import json
import os
import time
from collections.abc import Callable
from typing import Any

from ..core.errors import ApiError, ensure_cache_dir
from . import _http
from .models import League, Player, PlayerStats, Team


class RateLimitError(ApiError):
    """Raised when the API rate limit is hit and all retries are exhausted."""

    pass


class DailyLimitError(ApiError):
    """Raised when the daily API request quota is exceeded."""

    pass


class SeasonNotAvailableError(ApiError):
    """Raised when the API-Football plan doesn't allow access to the requested season."""

    def __init__(self, season: int, api_message: str = ""):
        self.season = season
        self.api_message = api_message
        super().__init__(f"Season {season} not available on current plan")


class ApiFootballClient:
    """Client for API-Football with local JSON caching."""

    BASE_URL = "https://v3.football.api-sports.io"

    # Featured leagues shown immediately without an API call.
    # season=None  → use current calendar year
    # season=-1    → use current year - 1 (cross-year competitions like CL)
    # Annotated only so mypy reads the mixed int/str/None values as `Any` rather
    # than `object`; the entries are unchanged from the original.
    FEATURED_LEAGUES: list[dict[str, Any]] = [
        {"id": 2, "name": "UEFA Champions League", "country": "World", "season": -1},
        {
            "id": 13,
            "name": "Copa Libertadores",
            "country": "South America",
            "season": None,
        },
        {"id": 71, "name": "Brasileirao Serie A", "country": "Brazil", "season": None},
        {"id": 253, "name": "MLS", "country": "USA", "season": None},
    ]

    RATE_LIMIT_WAIT = 65  # seconds to wait after a rate limit hit

    # The rate-limit retry sleeps RATE_LIMIT_WAIT seconds per attempt, three
    # attempts deep. Held as a class attribute so a test can replace it; nothing
    # in the library overrides it.
    _sleep: Callable[[float], None] = staticmethod(time.sleep)

    def __init__(
        self,
        api_key: str,
        cache_dir: str,
        on_status: Callable[[str], None] | None = None,
        transport: _http.Transport | None = None,
    ):
        self.api_key = api_key
        self.cache_dir = cache_dir
        self.on_status = on_status  # Optional callable(message: str) for status updates
        self._transport = transport
        ensure_cache_dir(cache_dir)

    def get_featured_leagues(self) -> list[League]:
        """Return hardcoded featured leagues with dynamically computed seasons."""
        from datetime import datetime

        current_year = datetime.now().year
        results = []
        for item in self.FEATURED_LEAGUES:
            if item["season"] is None:
                season = current_year
            elif item["season"] == -1:
                season = current_year - 1
            else:
                season = item["season"]
            results.append(
                League(
                    id=item["id"],
                    name=item["name"],
                    country=item["country"],
                    country_code="",
                    logo_url="",
                    season=season,
                    teams_count=0,
                )
            )
        return results

    def get_leagues(
        self, country: str | None = None, season: int | None = None, id: int | None = None
    ) -> list[League]:
        """Fetch available leagues, optionally filtered by id/country/season."""
        params: dict[str, Any] = {}
        if id:
            params["id"] = id
        if country:
            params["country"] = country
        if season:
            params["season"] = season
        cache_key = f"leagues_{id or 'all'}_{country or 'all'}_{season or 'all'}"
        cached = self._load_cache(cache_key)
        data = cached or self._request("/leagues", params)
        if not cached:
            self._check_plan_error(data, season)
            if data:
                self._save_cache(cache_key, data)
        return self._parse_leagues(data, season)

    def get_teams(self, league_id: int, season: int) -> list[Team]:
        """Fetch all teams in a league for a given season."""
        params = {"league": league_id, "season": season}
        cache_key = f"teams_{league_id}_{season}"
        cached = self._load_cache(cache_key)
        data = cached or self._request("/teams", params)
        if not cached:
            self._check_plan_error(data, season)
            if data:
                self._save_cache(cache_key, data)
        return self._parse_teams(data)

    def get_squad(self, team_id: int, season: int | None = None) -> list[Player]:
        """Fetch current squad/roster for a team.

        `season` reaches the cache key and not the request. `/players/squads`
        takes no season and answers with the squad as it stands today, so a key
        of the team id alone identifies a resource whose meaning is "now" and can
        never be invalidated by anything the caller varies: the first fetch a
        user ever runs freezes that squad on disk for the life of the cache
        directory, and every later season replays it with no network call and
        reports success. The season is the one coordinate the caller already has,
        and putting it in the key is what makes the key mean what the answer
        means. `EspnClient.get_hockey_squad` carries the same fix and the long
        form of this argument, including why it is not a TTL.
        """
        params = {"team": team_id}
        cache_key = f"squad_{team_id}_{season or 'any'}"
        cached = self._load_cache(cache_key)
        data = cached or self._request("/players/squads", params)
        if not cached and data:
            self._save_cache(cache_key, data)
        return self._parse_squad(data)

    def get_player_stats(self, team_id: int, season: int) -> list[PlayerStats]:
        """Fetch detailed player statistics for a team in a season."""
        params = {"team": team_id, "season": season}
        cache_key = f"players_{team_id}_{season}"
        cached = self._load_cache(cache_key)
        data = cached or self._request("/players", params)
        if not cached and data:
            self._save_cache(cache_key, data)
        return self._parse_player_stats(data)

    def get_team_logo_url(self, team_id: int) -> str:
        """Get team logo image URL from API."""
        return f"https://media.api-sports.io/football/teams/{team_id}.png"

    def _check_plan_error(self, data: dict, season: int | None = None) -> None:
        """Raise SeasonNotAvailableError if the response contains a plan restriction."""
        if not data or not isinstance(data, dict):
            return
        errors = data.get("errors") or {}
        if not isinstance(errors, dict) or not errors:
            return
        plan_msg = errors.get("plan", "")
        if plan_msg and "Free plans" in plan_msg:
            raise SeasonNotAvailableError(season or 0, plan_msg)

    def _request(self, endpoint: str, params: dict, _retries: int = 3) -> dict:
        """Make authenticated request, retrying transparently on rate limit."""
        try:
            data = _http.get_json(
                self.BASE_URL + endpoint,
                params=params,
                headers={"x-apisports-key": self.api_key},
                transport=self._transport,
            )
        except Exception:
            return {}

        # Check for rate limit error and retry after waiting
        if not isinstance(data, dict):
            return {}
        errors = data.get("errors") or {}
        if not isinstance(errors, dict):
            errors = {}
        if errors.get("rateLimit"):
            if _retries > 0:
                wait = self.RATE_LIMIT_WAIT
                remaining = wait
                while remaining > 0:
                    step = min(5, remaining)
                    if self.on_status:
                        self.on_status(f"Rate limited — retrying in {remaining}s...")
                    self._sleep(step)
                    remaining -= step
                return self._request(endpoint, params, _retries - 1)
            else:
                raise RateLimitError(errors.get("rateLimit", "Rate limit exceeded"))

        if errors.get("requests"):
            raise DailyLimitError(errors.get("requests", "Daily request limit reached"))

        return data

    def _load_cache(self, cache_key: str) -> dict | None:
        """Load cached API response if available."""
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
        """Save API response to local cache."""
        path = os.path.join(self.cache_dir, f"{cache_key}.json")
        with open(path, "w") as f:
            json.dump(data, f)

    def _parse_leagues(self, data: dict, season: int | None = None) -> list[League]:
        """Parse API response into League objects."""
        leagues = []
        if not isinstance(data, dict):
            return leagues
        for item in data.get("response", []):
            league_info = item.get("league", {})
            country_info = item.get("country", {})
            seasons = item.get("seasons", [])
            # Determine teams count from the season data if available
            teams_count = 0
            used_season = season or 0
            for s in seasons:
                if season and s.get("year") == season:
                    teams_count = (
                        s.get("statistics", {}).get("teams", 0)
                        if isinstance(s.get("statistics"), dict)
                        else 0
                    )
                    used_season = season
                    break
            if not used_season and seasons:
                used_season = seasons[-1].get("year", 0)
            leagues.append(
                League(
                    id=league_info.get("id", 0),
                    name=league_info.get("name", ""),
                    country=country_info.get("name", ""),
                    country_code=country_info.get("code", ""),
                    logo_url=league_info.get("logo", ""),
                    season=used_season,
                    teams_count=teams_count,
                )
            )
        return leagues

    def _parse_teams(self, data: dict) -> list[Team]:
        """Parse API response into Team objects."""
        teams = []
        if not isinstance(data, dict):
            return teams
        for item in data.get("response", []):
            team_info = item.get("team", {})
            venue_info = item.get("venue", {})
            teams.append(
                Team(
                    id=team_info.get("id", 0),
                    name=team_info.get("name", ""),
                    short_name=team_info.get("name", "")[:12],
                    code=team_info.get("code", "")[:3] if team_info.get("code") else "",
                    logo_url=team_info.get("logo", ""),
                    country=venue_info.get("city", ""),
                )
            )
        return teams

    def _parse_squad(self, data: dict) -> list[Player]:
        """Parse squad API response into Player objects."""
        players = []
        if not isinstance(data, dict):
            return players
        for item in data.get("response", []):
            for p in item.get("players", []):
                pos = p.get("position", "Midfielder")
                # Normalize position names
                if pos == "Goalkeeper":
                    position = "Goalkeeper"
                elif pos == "Defender":
                    position = "Defender"
                elif pos == "Midfielder":
                    position = "Midfielder"
                else:
                    position = "Attacker"
                players.append(
                    Player(
                        id=p.get("id", 0),
                        name=p.get("name", ""),
                        first_name=p.get("firstname", "") or "",
                        last_name=p.get("lastname", "") or "",
                        age=p.get("age", 25),
                        nationality=p.get("nationality", ""),
                        position=position,
                        number=p.get("number"),
                        photo_url=p.get("photo", ""),
                    )
                )
        return players

    def _parse_player_stats(self, data: dict) -> list[PlayerStats]:
        """Parse player stats API response into PlayerStats objects."""
        stats_list = []
        if not isinstance(data, dict):
            return stats_list
        for item in data.get("response", []):
            player_info = item.get("player", {})
            statistics = item.get("statistics", [])
            if not statistics:
                continue
            # Use the first statistics entry (primary league)
            s = statistics[0]
            games = s.get("games", {})
            shots = s.get("shots", {})
            goals_data = s.get("goals", {})
            passes = s.get("passes", {})
            tackles = s.get("tackles", {})
            duels = s.get("duels", {})
            dribbles = s.get("dribbles", {})
            fouls = s.get("fouls", {})
            cards = s.get("cards", {})
            stats_list.append(
                PlayerStats(
                    player_id=player_info.get("id", 0),
                    appearances=games.get("appearences", 0) or 0,
                    minutes=games.get("minutes", 0) or 0,
                    goals=goals_data.get("total", 0) or 0,
                    assists=goals_data.get("assists", 0) or 0,
                    shots_total=shots.get("total", 0) or 0,
                    shots_on=shots.get("on", 0) or 0,
                    passes_total=passes.get("total", 0) or 0,
                    passes_accuracy=float(passes.get("accuracy", 0) or 0),
                    tackles_total=tackles.get("total", 0) or 0,
                    interceptions=tackles.get("interceptions", 0) or 0,
                    blocks=tackles.get("blocks", 0) or 0,
                    duels_total=duels.get("total", 0) or 0,
                    duels_won=duels.get("won", 0) or 0,
                    dribbles_attempts=dribbles.get("attempts", 0) or 0,
                    dribbles_success=dribbles.get("success", 0) or 0,
                    fouls_committed=fouls.get("committed", 0) or 0,
                    fouls_drawn=fouls.get("drawn", 0) or 0,
                    cards_yellow=cards.get("yellow", 0) or 0,
                    cards_red=cards.get("red", 0) or 0,
                    rating=float(games.get("rating", 0) or 0) or None,
                    lineups=games.get("lineups", 0) or 0,
                )
            )
        return stats_list
