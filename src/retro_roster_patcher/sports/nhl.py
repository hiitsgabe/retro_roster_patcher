"""NHL official API client (api-web.nhle.com) — no API key required.

Provides historical rosters and per-player stats back to 1993-94.
Used as an alternative to ESPN for the NHL94 SNES patcher.
"""

import json
import os
from collections.abc import Callable, Iterable
from dataclasses import asdict
from datetime import date
from typing import Any

from ..core.concurrency import emit_status, parallel_map
from ..core.errors import ensure_cache_dir
from . import _http
from .models import Player, Team
from .serde import _only_declared

BASE_URL = "https://api-web.nhle.com/v1"
#: The NHL's player search: every player it knows, with his current club.
SEARCH_URL = "https://search.d3.nhle.com/api/v1/search/player"
#: Clubs a career names that today's standings do not, by full name.
FORMER_CLUBS = {
    "Arizona Coyotes": "ARI",
    "Phoenix Coyotes": "PHX",
    "Atlanta Thrashers": "ATL",
    "Utah Hockey Club": "UTA",
    "Mighty Ducks of Anaheim": "ANA",
}


def current_season(today: date) -> int:
    """The start year of the NHL season `today` belongs to: the league year
    turns over on July 1, when free agency opens."""
    return today.year if today.month >= 7 else today.year - 1


class NhlApiClient:
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
        """Fetch per-player season stats via the club-stats endpoint.

        Returns a dict mapping player ID (str) to a stat dict:
        {"12345": {"G": 26, "A": 22, "PTS": 48, ...}}
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
                "A": gl.get("assists", 0),
                "PIM": gl.get("penaltyMinutes", 0),
            }

        if stats:
            self._save_cache(cache_key, stats)
        return stats

    def get_hockey_free_agents(self, season: int) -> tuple[list[Player], dict[str, dict]]:
        """The players without a club now, best first, and each one's latest
        NHL regular-season line in `get_hockey_team_leaders`' keys.

        A free agent is a player the NHL's search lists with no team whose
        last season is `season` (start year) or the one before: an unsigned
        player, or one who retired, which the search does not tell apart.
        Best first is NHL games over those two seasons; one with none comes
        last with the line `{"GP": 0}`, so he rates from zeros and not from
        the position defaults, which rank above most real players' lines.
        Cached per season, as the squads are, so the free agents stay the ones
        fetched with them.
        """
        cache_key = f"nhl_api_free_agents_{season}"
        cached = self._load_cache(cache_key)
        if cached is None:
            cached = self._find_free_agents(season)
            # Not when a landing did not answer: a dropped connection would
            # otherwise freeze a short list for the season.
            if cached["players"] and not cached.pop("missing"):
                self._save_cache(cache_key, cached)
        # Leniently, as `serde` reads a rosters file: the cache outlives versions.
        return [Player(**_only_declared(Player, p)) for p in cached["players"]], cached["stats"]

    def _find_free_agents(self, season: int) -> dict[str, Any]:
        recent = (season - 1) * 10000 + season, season * 10000 + season + 1
        try:
            emit_status(self.on_status, "Fetching the NHL's free agents...")
            found = _http.get_json(
                SEARCH_URL,
                params={"culture": "en-us", "limit": 50000, "q": "*", "active": "false"},
                transport=self._transport,
            )
        except Exception:
            found = []
        candidates = [
            p
            for p in (found if isinstance(found, list) else [])
            if isinstance(p, dict)
            and p.get("playerId")
            and not p.get("teamAbbrev")
            and str(p.get("lastSeasonId")) in {str(s) for s in recent}
        ]
        landings = parallel_map(
            lambda p: self._request(f"/player/{p['playerId']}/landing"), candidates
        )
        missing = sum(1 for landing in landings if not landing)
        if missing:
            emit_status(
                self.on_status,
                f"Free agents: {missing} of {len(candidates)} did not answer; "
                "asked again next time",
            )
        ranked = []
        for found_player, landing in zip(candidates, landings, strict=True):
            if not landing:
                continue  # did not answer: counted in `missing`
            try:
                ranked.append(_free_agent(found_player, landing, season, recent))
            except (AttributeError, KeyError, TypeError, ValueError):
                continue  # a page not shaped as expected: skip the player, keep the rest
        ranked.sort(key=lambda entry: -entry[2])
        return {
            "players": [asdict(player) for player, _, _ in ranked],
            "stats": {str(player.id): line for player, line, _ in ranked if line},
            "missing": missing,
        }

    def get_hockey_careers(self, player_ids: Iterable[int]) -> dict[str, dict[str, Any]]:
        """Each player's NHL career from his landing page, by player id (str):
        `{"draft_year", "draft_pick", "seasons"}`. The draft is 0 and 0 for an
        undrafted player. A season is one regular season or playoffs, clubs
        added up: `{"season" (start year), "playoffs", "team" ("ANA-BOS" when
        traded), "GP", ...}` with a skater's G, A, PTS, +/-, PIM, S, PPG, SHG
        and GWG or a goalie's W, L, T, SO, GA, SA, MIN, G, A and PIM.

        One request a player, cached once it answers; a player whose page
        does not answer is left out and asked again next time.
        """
        ids = [int(i) for i in player_ids if i]
        cached = {i: self._load_cache(f"nhl_api_career_{i}") for i in ids}
        missing = [i for i in ids if cached[i] is None]
        if missing:
            codes = {team.name: team.code for team in self.get_nhl_teams()} | FORMER_CLUBS
            landings = parallel_map(lambda i: self._request(f"/player/{i}/landing"), missing)
            for i, landing in zip(missing, landings, strict=True):
                if not landing:
                    continue
                try:
                    career = _career(landing, codes)
                except (AttributeError, KeyError, TypeError, ValueError):
                    continue  # a page not shaped as expected: skip the player
                cached[i] = career
                self._save_cache(f"nhl_api_career_{i}", career)
        return {str(i): career for i, career in cached.items() if career is not None}

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
                height = athlete.get("heightInInches", 0) or 0
                hand = athlete.get("shootsCatches", "")

                players.append(
                    Player(
                        id=int(athlete.get("id", 0)),
                        name=display,
                        first_name=first,
                        last_name=last,
                        age=0,
                        nationality=str(athlete.get("birthCountry") or ""),
                        position=pos,
                        number=number,
                        photo_url=athlete.get("headshot", "") or "",
                        birth_date=str(athlete.get("birthDate") or "")[:10],
                        birth_city=str((athlete.get("birthCity") or {}).get("default") or ""),
                        birth_state=str(
                            (athlete.get("birthStateProvince") or {}).get("default") or ""
                        ),
                        weight=float(weight),
                        height=float(height),
                        handedness=hand,
                    )
                )

        return players

    def _request(self, path: str) -> dict:
        try:
            emit_status(self.on_status, f"Fetching {path}...")
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


#: A season's counts, landing key -> this library's stat key.
_SKATER_COUNTS = {
    "gamesPlayed": "GP",
    "goals": "G",
    "assists": "A",
    "points": "PTS",
    "plusMinus": "+/-",
    "pim": "PIM",
    "shots": "S",
    "powerPlayGoals": "PPG",
    "shorthandedGoals": "SHG",
    "gameWinningGoals": "GWG",
}
_GOALIE_COUNTS = {
    "gamesPlayed": "GP",
    "wins": "W",
    "losses": "L",
    "ties": "T",
    "shutouts": "SO",
    "goalsAgainst": "GA",
    "shotsAgainst": "SA",
    "goals": "G",
    "assists": "A",
    "pim": "PIM",
}


def _career(landing: dict[str, Any], codes: dict[str, str]) -> dict[str, Any]:
    """`get_hockey_careers`' entry for one landing page."""
    goalie = landing.get("position") == "G"
    counts = _GOALIE_COUNTS if goalie else _SKATER_COUNTS
    seasons: dict[tuple[int, bool], dict[str, Any]] = {}
    for row in landing.get("seasonTotals") or []:
        if row.get("leagueAbbrev") != "NHL" or row.get("gameTypeId") not in (2, 3):
            continue
        key = int(row["season"]) // 10000, row["gameTypeId"] == 3
        name = (row.get("teamName") or {}).get("default", "")
        code = codes.get(name) or name[:3].upper()
        season = seasons.setdefault(key, {"season": key[0], "playoffs": key[1], "team": ""})
        if code not in season["team"].split("-"):
            season["team"] = f"{season['team']}-{code}".strip("-")
        for theirs, ours in counts.items():
            season[ours] = season.get(ours, 0) + int(row.get(theirs) or 0)
        if goalie:
            minutes, _, seconds = str(row.get("timeOnIce") or "0:00").partition(":")
            season["MIN"] = season.get("MIN", 0) + int(minutes) + round(int(seconds or 0) / 60)
    draft = landing.get("draftDetails") or {}
    return {
        "draft_year": int(draft.get("year") or 0),
        "draft_pick": int(draft.get("overallPick") or 0),
        "seasons": [seasons[key] for key in sorted(seasons)],
    }


def _position(code: str) -> str:
    """The NHL API's position code in this library's spelling: L/R are wings."""
    return {"L": "LW", "R": "RW"}.get(code, code or "C")


def _free_agent(
    found: dict[str, Any], landing: dict[str, Any], season: int, recent: tuple[int, int]
) -> tuple[Player, dict[str, Any], int]:
    """One free agent from his search record and his landing page: the
    player, his latest NHL regular-season line, and his NHL games in `recent`.

    Birth place from the search, which spells a state as its postal code;
    names, hand, head shot and birth date from the landing. Age as of
    October 1 of `season`.
    """
    first = (landing.get("firstName") or {}).get("default", "")
    last = (landing.get("lastName") or {}).get("default", "")
    born = str(landing.get("birthDate") or "")
    parts = born.split("-")
    age = 0
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        year, month, day = (int(p) for p in parts)
        age = season - year - ((month, day) > (10, 1))
    player = Player(
        id=int(found.get("playerId") or 0),
        name=f"{first} {last}".strip(),
        first_name=first,
        last_name=last,
        age=age,
        nationality=str(found.get("birthCountry") or ""),
        position=_position(str(landing.get("position") or "")),
        number=landing.get("sweaterNumber"),
        photo_url=str(landing.get("headshot") or ""),
        birth_date=born,
        birth_city=str(found.get("birthCity") or ""),
        birth_state=str(found.get("birthStateProvince") or ""),
        weight=float(found.get("weightInPounds") or 0),
        height=float(found.get("heightInInches") or 0),
        handedness=str(landing.get("shootsCatches") or ""),
    )
    nhl = [
        s
        for s in landing.get("seasonTotals") or []
        if s.get("leagueAbbrev") == "NHL" and s.get("gameTypeId") == 2 and s.get("season") in recent
    ]
    games = sum(int(s.get("gamesPlayed") or 0) for s in nhl)
    if not nhl:
        return player, {"GP": 0}, games
    # ponytail: a player traded mid-season has one line per club; the one with
    # the most games stands for his season.
    latest = max(nhl, key=lambda s: (s["season"], s.get("gamesPlayed") or 0))
    if player.position == "G":
        line = {
            "GP": latest.get("gamesPlayed", 0),
            "W": latest.get("wins", 0),
            "GAA": latest.get("goalsAgainstAvg", 0),
            "SV%": latest.get("savePctg", 0) or 0,
        }
    else:
        line = {
            "G": latest.get("goals", 0),
            "A": latest.get("assists", 0),
            "PTS": latest.get("points", 0),
            "+/-": latest.get("plusMinus", 0),
            "PIM": latest.get("pim", 0),
            "S": latest.get("shots", 0),
            "FO%": latest.get("faceoffWinningPctg", 0),
            "GP": latest.get("gamesPlayed", 0),
        }
    return player, line, games
