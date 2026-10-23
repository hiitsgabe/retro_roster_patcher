"""FotMob client, soccer only, no API key.

Three document types, all JSON, all on `www.fotmob.com/api/data`:

  * `leagues?id=` carries the league table, which is the cheapest list of a
    league's teams;
  * `teams?id=` carries the squad: position, shirt number, height, birth date
    and the season's goals, assists and cards for each player;
  * `search/suggest?term=` finds a team or player by name.

Player photos are plain PNGs on `images.fotmob.com`, keyed by player id.

FotMob has no public API and its terms do not offer one. Nothing here is a
contract: the client takes the same defensive stance as `EspnClient` (a failed
request is an empty answer, never an exception) because the shapes can change
without notice. It is meant as a second source behind ESPN, not a replacement.

League ids are this package's own (the same as `ESPN_LEAGUES`), so `--league-id`
means the same league for both providers.
"""

from __future__ import annotations

import json
import os
import urllib.parse
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ..core.concurrency import emit_status
from ..core.errors import ensure_cache_dir
from . import _http
from .models import League, Player, PlayerStats, Team

API_URL = "https://www.fotmob.com/api/data"
IMAGE_URL = "https://images.fotmob.com/image_resources"

# Our league id -> FotMob league id. Never renumber the first column.
FOTMOB_LEAGUES = [
    {"id": 2001, "fotmob_id": 47, "name": "Premier League", "country": "England"},
    {"id": 2002, "fotmob_id": 87, "name": "La Liga", "country": "Spain"},
    {"id": 2003, "fotmob_id": 54, "name": "Bundesliga", "country": "Germany"},
    {"id": 2004, "fotmob_id": 55, "name": "Serie A", "country": "Italy"},
    {"id": 2005, "fotmob_id": 53, "name": "Ligue 1", "country": "France"},
    {"id": 2006, "fotmob_id": 268, "name": "Brasileirao Serie A", "country": "Brazil"},
    {"id": 2007, "fotmob_id": 130, "name": "MLS", "country": "USA"},
    {"id": 2008, "fotmob_id": 42, "name": "UEFA Champions League", "country": "World"},
    {"id": 2009, "fotmob_id": 45, "name": "Copa Libertadores", "country": "South America"},
    {"id": 2010, "fotmob_id": 112, "name": "Liga Profesional", "country": "Argentina"},
    {"id": 2011, "fotmob_id": 230, "name": "Liga BBVA MX", "country": "Mexico"},
    {"id": 2012, "fotmob_id": 61, "name": "Primeira Liga", "country": "Portugal"},
    {"id": 2013, "fotmob_id": 57, "name": "Eredivisie", "country": "Netherlands"},
    {"id": 2014, "fotmob_id": 223, "name": "J.League", "country": "Japan"},
    {"id": 2015, "fotmob_id": 274, "name": "Primera A", "country": "Colombia"},
    {"id": 2016, "fotmob_id": 273, "name": "Primera División", "country": "Chile"},
    {"id": 2017, "fotmob_id": 8814, "name": "Brasileirao Serie B", "country": "Brazil"},
]
_BY_ID = {item["id"]: item for item in FOTMOB_LEAGUES}

# FotMob's own squad group -> the position names `EspnClient.get_squad` uses.
_GROUP_POSITIONS = {
    "keepers": "Goalkeeper",
    "defenders": "Defender",
    "midfielders": "Midfielder",
    "attackers": "Attacker",
}


def photo_url(player_id: int) -> str:
    """The player's photo: a 192 px PNG with a transparent background.

    Answers 403 for a player FotMob has no picture of, so a caller that wants to
    know has to request it; `get_squad` only builds the URL.
    """
    return f"{IMAGE_URL}/playerimages/{player_id}.png"


def team_logo_url(team_id: int) -> str:
    return f"{IMAGE_URL}/logo/teamlogo/{team_id}.png"


def _as_int(value: Any) -> int:
    """`value` as an int; search answers carry ids as strings, tables as numbers."""
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return int(value)


class FotmobClient:
    """Client for FotMob's unofficial API. Same shape as `EspnClient` for soccer."""

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

    def get_leagues(
        self, country: str | None = None, season: int | None = None, id: int | None = None
    ) -> list[League]:
        """The leagues FotMob is mapped for, optionally one by `id` or by country."""
        if id is not None:
            item = _BY_ID.get(id)
            return [self._league(item, season)] if item else []
        leagues = [self._league(item, season) for item in FOTMOB_LEAGUES]
        if country:
            leagues = [x for x in leagues if x.country.lower() == country.lower()]
        return leagues

    def get_teams(self, league_id: int, season: int | None = None) -> list[Team]:
        """The league table's teams.

        `season` reaches the cache key only: the answer is the current table.
        """
        item = _BY_ID.get(league_id)
        if not item:
            return []
        key = f"fotmob_teams_{league_id}_{season or 'any'}"
        data = self._load_cache(key)
        if data is None:
            data = self._request(f"/leagues?id={item['fotmob_id']}")
        teams = self._parse_teams(data, str(item["country"]))
        # Cache what parsed, not what arrived: an empty answer frozen on disk
        # would outlive the outage that caused it.
        if teams:
            self._save_cache(key, data)
        return teams

    def get_squad(self, team_id: int, season: int | None = None) -> list[Player]:
        """The team's players, coaches left out, each with a photo URL."""
        return self._parse_squad(self._squad_document(team_id, season))

    def get_player_stats(self, team_id: int, season: int | None = None) -> list[PlayerStats]:
        """What the squad document counts for each player this season.

        Only goals, assists, cards and FotMob's own rating are there. Everything
        else is filler, named in `unsupplied` so a caller can tell it from a
        measured zero.
        """
        stats = []
        for member in self._members(self._squad_document(team_id, season)):
            rating = member.get("rating")
            rating = rating.get("num") if isinstance(rating, dict) else rating
            try:
                rating_value = float(rating) if rating is not None else None
            except (TypeError, ValueError):
                rating_value = None
            stats.append(
                PlayerStats(
                    player_id=_as_int(member.get("id")),
                    appearances=0,
                    minutes=0,
                    goals=_as_int(member.get("goals")),
                    assists=_as_int(member.get("assists")),
                    shots_total=0,
                    shots_on=0,
                    passes_total=0,
                    passes_accuracy=0.0,
                    tackles_total=0,
                    interceptions=0,
                    blocks=0,
                    duels_total=0,
                    duels_won=0,
                    dribbles_attempts=0,
                    dribbles_success=0,
                    fouls_committed=0,
                    fouls_drawn=0,
                    cards_yellow=_as_int(member.get("ycards")),
                    cards_red=_as_int(member.get("rcards")),
                    rating=rating_value,
                    unsupplied=_UNSUPPLIED,
                )
            )
        return stats

    def search_teams(self, term: str) -> list[Team]:
        """Teams matching `term`, best first. Names only, no league scoping."""
        data = self._request(f"/search/suggest?term={urllib.parse.quote(term)}&lang=en")
        found = []
        for group in data if isinstance(data, list) else []:
            for hit in group.get("suggestions") or []:
                if isinstance(hit, dict) and hit.get("type") == "team":
                    team_id = _as_int(hit.get("id"))
                    found.append(
                        Team(
                            id=team_id,
                            name=str(hit.get("name", "")),
                            logo_url=team_logo_url(team_id),
                        )
                    )
        # "All" repeats what the per-type groups carry.
        return list({t.id: t for t in found}.values())

    # -- internals ---------------------------------------------------------

    def _league(self, item: dict[str, Any], season: int | None = None) -> League:
        return League(
            id=item["id"],
            name=item["name"],
            country=item["country"],
            season=season or datetime.now().year,
        )

    def _squad_document(self, team_id: int, season: int | None) -> dict:
        key = f"fotmob_squad_{team_id}_{season or 'any'}"
        data = self._load_cache(key)
        if data is None:
            data = self._request(f"/teams?id={team_id}")
            if self._members(data):
                self._save_cache(key, data)
        return data

    @staticmethod
    def _groups(data: dict) -> list[dict]:
        squad = data.get("squad") if isinstance(data, dict) else None
        groups = squad.get("squad") if isinstance(squad, dict) else None
        return [g for g in groups or [] if isinstance(g, dict)]

    def _members(self, data: dict) -> list[dict]:
        return [
            m
            for g in self._groups(data)
            if g.get("title") in _GROUP_POSITIONS
            for m in g.get("members") or []
            if isinstance(m, dict)
        ]

    @staticmethod
    def _parse_teams(data: dict, country: str) -> list[Team]:
        """Teams from the league table. A cup has no `all` table and gives none."""
        try:
            rows = data["table"][0]["data"]["table"]["all"]
        except (KeyError, IndexError, TypeError):
            return []
        return [
            Team(
                id=_as_int(row.get("id")),
                name=str(row.get("name", "")),
                short_name=str(row.get("shortName", "")),
                logo_url=team_logo_url(_as_int(row.get("id"))),
                country=country,
            )
            for row in rows
            if isinstance(row, dict) and row.get("id")
        ]

    def _parse_squad(self, data: dict) -> list[Player]:
        players = []
        for group in self._groups(data):
            position = _GROUP_POSITIONS.get(group.get("title", ""))
            if position is None:
                continue
            for member in group.get("members") or []:
                if not isinstance(member, dict) or not member.get("id"):
                    continue
                name = str(member.get("name", ""))
                first, _, last = name.rpartition(" ")
                shirt = member.get("shirtNumber")
                players.append(
                    Player(
                        id=_as_int(member["id"]),
                        name=name,
                        first_name=first,
                        last_name=last,
                        age=_as_int(member.get("age")),
                        nationality=str(member.get("cname", "")),
                        position=position,
                        number=shirt
                        if isinstance(shirt, int) and not isinstance(shirt, bool)
                        else None,
                        photo_url=photo_url(_as_int(member["id"])),
                        birth_date=str(member.get("dateOfBirth") or "")[:10],
                    )
                )
        return players

    def _request(self, path: str) -> Any:
        emit_status(self.on_status, f"Fetching FotMob{path}...")
        try:
            return _http.get_json(API_URL + path, transport=self._transport)
        except Exception:
            return {}

    def _load_cache(self, key: str) -> dict | None:
        try:
            with open(os.path.join(self.cache_dir, f"{key}.json")) as f:
                data = json.load(f)
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def _save_cache(self, key: str, data: dict) -> None:
        with open(os.path.join(self.cache_dir, f"{key}.json"), "w") as f:
            json.dump(data, f)


_UNSUPPLIED = (
    "appearances",
    "minutes",
    "shots_total",
    "shots_on",
    "passes_total",
    "passes_accuracy",
    "tackles_total",
    "interceptions",
    "blocks",
    "duels_total",
    "duels_won",
    "dribbles_attempts",
    "dribbles_success",
    "fouls_committed",
    "fouls_drawn",
)
