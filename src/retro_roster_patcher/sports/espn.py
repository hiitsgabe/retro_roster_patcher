"""ESPN public API client for soccer and hockey roster data — no API key required."""

import json
import os
from collections.abc import Callable
from typing import Any

from ..core.errors import ensure_cache_dir
from . import _http
from .models import League, Player, PlayerStats, Team

# Maps our internal league IDs to ESPN league codes.
# IDs start at 2000 to avoid clashing with API-Football IDs.
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

SOCCER_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"
HOCKEY_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/hockey"
HOCKEY_CORE_URL = "https://sports.core.api.espn.com/v2/sports/hockey/leagues/nhl"
BASEBALL_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/baseball"
BASEBALL_CORE_URL = "https://sports.core.api.espn.com/v2/sports/baseball/leagues/mlb"
BASKETBALL_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball"
BASKETBALL_CORE_URL = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"


class EspnClient:
    """Client for ESPN's public soccer and hockey API — no key, no rate limits."""

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
    # Public interface (mirrors ApiFootballClient)
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
        """Return ESPN leagues, optionally filtered by id."""
        if id is not None:
            item = _ID_TO_LEAGUE.get(id)
            return [self._league_from_item(item)] if item else []
        leagues = [self._league_from_item(item) for item in ESPN_LEAGUES]
        if country:
            leagues = [x for x in leagues if x.country.lower() == country.lower()]
        return leagues

    def get_teams(self, league_id: int, season: int | None = None) -> list[Team]:
        """Fetch all teams in a league."""
        item = _ID_TO_LEAGUE.get(league_id)
        if not item:
            return []
        code = item["code"]
        cache_key = f"espn_teams_{league_id}"
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

    def get_squad(self, team_id: int, league_code: str | None = None) -> list[Player]:
        """Fetch current squad for a team."""
        # ESPN roster endpoint requires the league code; find it via team detail if
        # unknown. Resolved before the cache lookup because the code varies the
        # response — team ids are league-scoped, so a key of the id alone would
        # serve one competition's roster for another's request.
        code = league_code or self._find_league_code_for_team(team_id)
        if not code:
            return []
        cache_key = f"espn_squad_{code}_{team_id}"
        cached = self._load_cache(cache_key)
        if cached:
            return self._parse_squad(cached)
        data = self._request(f"/{code}/teams/{team_id}/roster", sport="soccer")
        if data:
            self._save_cache(cache_key, data)
        return self._parse_squad(data)

    def get_player_stats(self, team_id: int, season: int) -> list[PlayerStats]:
        """ESPN doesn't provide historical stats — return empty list."""
        return []

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

    def get_hockey_squad(self, team_id: int) -> list[Player]:
        """Fetch current roster for an NHL team."""
        cache_key = f"espn_hockey_squad_{team_id}"
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

    def get_baseball_squad(self, team_id: int) -> list[Player]:
        """Fetch current roster for an MLB team."""
        cache_key = f"espn_baseball_squad_{team_id}"
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

    def get_basketball_squad(self, team_id: int) -> list[Player]:
        """Fetch current roster for an NBA team."""
        cache_key = f"espn_basketball_squad_{team_id}"
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

    def _league_from_item(self, item: dict) -> League:
        from datetime import datetime

        return League(
            id=item["id"],
            name=item["name"],
            country=item["country"],
            country_code="",
            logo_url="",
            season=datetime.now().year,
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

    def _find_league_code_for_team(self, team_id: int) -> str | None:
        """Find which league a team belongs to by checking cached team lists."""
        for item in ESPN_LEAGUES:
            cache_key = f"espn_teams_{item['id']}"
            cached = self._load_cache(cache_key)
            if cached:
                teams = self._parse_teams(cached)
                if any(t.id == team_id for t in teams):
                    return str(item["code"])
        return None

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
