"""NHL official API client (api-web.nhle.com) — no API key required.

Provides historical rosters and per-player stats back to 1993-94.
Used as an alternative to ESPN for the NHL94 SNES patcher.
"""

import json
import os
from collections.abc import Callable
from typing import Any

from . import _http
from .models import Player, Team

BASE_URL = "https://api-web.nhle.com/v1"


class NhlApiClient:
    """Client for the NHL official API."""

    def __init__(
        self,
        cache_dir: str,
        on_status: Callable[[str], None] | None = None,
        transport: _http.Transport | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.on_status = on_status
        self._transport = transport
        os.makedirs(cache_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Public interface (mirrors EspnClient hockey methods)
    # ------------------------------------------------------------------

    def get_nhl_teams(self) -> list[Team]:
        """Fetch all current NHL teams from standings."""
        cache_key = "nhl_api_teams"
        cached = self._load_cache(cache_key)
        if cached:
            return self._parse_standings_teams(cached)
        data = self._request("/standings/now")
        if data:
            self._save_cache(cache_key, data)
        return self._parse_standings_teams(data)

    def get_hockey_squad(self, team_abbrev: str, season: int = 2025) -> list[Player]:
        """Fetch roster for an NHL team for a given season.

        Args:
            team_abbrev: 3-letter NHL abbreviation (TOR, BOS, etc.)
            season: Start year of season (2024 = 2024-2025 season)
        """
        season_str = f"{season}{season + 1}"
        cache_key = f"nhl_api_squad_{team_abbrev}_{season_str}"
        cached = self._load_cache(cache_key)
        if cached:
            return self._parse_roster(cached)
        data = self._request(f"/roster/{team_abbrev}/{season_str}")
        if data:
            self._save_cache(cache_key, data)
        return self._parse_roster(data)

    def get_hockey_team_leaders(self, team_abbrev: str, season: int = 2025) -> dict[str, dict]:
        """Fetch per-player season stats via club-stats endpoint.

        Returns dict mapping player ID (str) to stat dict:
        {"12345": {"G": 26, "A": 22, "PTS": 48, ...}}

        One API call per team — covers all rostered players.
        """
        season_str = f"{season}{season + 1}"
        cache_key = f"nhl_api_stats_{team_abbrev}_{season_str}"
        cached = self._load_cache(cache_key)
        if cached:
            return cached

        data = self._request(f"/club-stats/{team_abbrev}/{season_str}/2")
        if not data:
            return {}

        stats: dict[str, dict[str, Any]] = {}

        for sk in data.get("skaters", []):
            pid = str(sk.get("playerId", ""))
            if not pid:
                continue
            stats[pid] = {
                "G": sk.get("goals", 0),
                "A": sk.get("assists", 0),
                "PTS": sk.get("points", 0),
                "+/-": sk.get("plusMinus", 0),
                "PIM": sk.get("penaltyMinutes", 0),
                "GP": sk.get("gamesPlayed", 0),
                "PPG": sk.get("powerPlayGoals", 0),
                "SHG": sk.get("shorthandedGoals", 0),
                "GWG": sk.get("gameWinningGoals", 0),
                "S": sk.get("shots", 0),
                "SPCT": sk.get("shootingPctg", 0),
                "TOI": sk.get("avgTimeOnIcePerGame", 0),
                "FO%": sk.get("faceoffWinPctg", 0),
            }

        for gl in data.get("goalies", []):
            pid = str(gl.get("playerId", ""))
            if not pid:
                continue
            stats[pid] = {
                "GP": gl.get("gamesPlayed", 0),
                "W": gl.get("wins", 0),
                "L": gl.get("losses", 0),
                "GAA": gl.get("goalsAgainstAverage", 0),
                "SV%": gl.get("savePercentage", 0) or 0,
                "SO": gl.get("shutouts", 0),
                "SA": gl.get("shotsAgainst", 0),
                "SV": gl.get("saves", 0),
                "GA": gl.get("goalsAgainst", 0),
            }

        if stats:
            self._save_cache(cache_key, stats)
        return stats

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    def _parse_standings_teams(self, data: dict) -> list[Team]:
        if not isinstance(data, dict):
            return []
        teams = []
        seen = set()
        for entry in data.get("standings", []):
            abbrev = entry.get("teamAbbrev", {}).get("default", "")
            if not abbrev or abbrev in seen:
                continue
            seen.add(abbrev)
            name = entry.get("teamName", {}).get("default", "")
            common = entry.get("teamCommonName", {}).get("default", "")
            teams.append(
                Team(
                    id=0,
                    name=f"{name}",
                    short_name=common[:12] if common else name[:12],
                    code=abbrev,
                    logo_url="",
                    country="",
                )
            )
        return teams

    def _parse_roster(self, data: dict) -> list[Player]:
        """Parse NHL API roster response.

        Response format:
        {forwards: [...], defensemen: [...], goalies: [...]}
        Each player has: id, firstName, lastName, sweaterNumber,
        positionCode, shootsCatches, weightInPounds, etc.
        """
        if not isinstance(data, dict):
            return []

        players = []
        for group_key in ("forwards", "defensemen", "goalies"):
            for athlete in data.get(group_key, []):
                first = athlete.get("firstName", {}).get("default", "")
                last = athlete.get("lastName", {}).get("default", "")
                display = f"{first} {last}".strip()

                pos = athlete.get("positionCode", "C")
                # NHL API uses L/R for wing, map to LW/RW
                if pos == "L":
                    pos = "LW"
                elif pos == "R":
                    pos = "RW"

                number = athlete.get("sweaterNumber")
                weight = athlete.get("weightInPounds", 0) or 0
                hand = athlete.get("shootsCatches", "")

                players.append(
                    Player(
                        id=int(athlete.get("id", 0)),
                        name=display,
                        first_name=first,
                        last_name=last,
                        age=0,
                        nationality="",
                        position=pos,
                        number=number,
                        photo_url="",
                        weight=float(weight),
                        handedness=hand,
                    )
                )

        return players

    # ------------------------------------------------------------------
    # HTTP / Cache
    # ------------------------------------------------------------------

    def _request(self, path: str) -> dict:
        try:
            if self.on_status:
                self.on_status(f"Fetching {path}...")
            return _http.get_json(BASE_URL + path, transport=self._transport)
        except Exception:
            return {}

    def _load_cache(self, key: str) -> dict | None:
        path = os.path.join(self.cache_dir, f"{key}.json")
        if os.path.exists(path):
            try:
                with open(path) as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return None

    def _save_cache(self, key: str, data: dict) -> None:
        path = os.path.join(self.cache_dir, f"{key}.json")
        with open(path, "w") as f:
            json.dump(data, f)
