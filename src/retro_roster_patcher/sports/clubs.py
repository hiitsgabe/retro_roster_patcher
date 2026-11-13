"""Today's best European clubs, by UEFA club coefficient, for the club slots the leagues leave.

A soccer disc has more club slots than the leagues a patch writes fill, and the rest keep
their 2012 clubs. `fetch_top_clubs` fills them with ESPN squads of the best-ranked clubs:
`uefa_ranking` gives the order, `nationals.place_ranked` the slots.

The ranking is UEFA's own (`comp.uefa.com/v2/coefficients`, keyless). It names clubs by UEFA
id, so each club's ESPN team id comes from the packaged snapshot
`assets/uefa_club_ranking.json` (the 2026-11-05 ranking with ESPN ids, built and checked by
`assets/uefa_ranking/build.py`), which is also the fallback when UEFA cannot be reached. A
club the snapshot has no id for is paired by name among the ESPN teams of its own country
(`pair_club`): without the country rule, Dinamo Zagreb pairs with Dinamo Moscow.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

from ..core.assets import package_bytes
from ..core.concurrency import emit_status
from ..core.errors import ApiError, ensure_cache_dir
from ..core.models import StatusFn
from ..core.text import to_ascii
from . import _http
from .models import Team

RANKING_URL = "https://comp.uefa.com/v2/coefficients"
#: The `LeagueData.league` of a club fill: no provider league, and a patch names no slot
#: group after it (a fill never renames a league group).
CLUB_FILL_LEAGUE = 0
#: ESPN's league ids of each country's clubs, by UEFA country code. A country missing here
#: has no league on ESPN (Czechia, Croatia, Ukraine, ...): its clubs are on ESPN only while
#: they play a UEFA competition (`UEFA_LEAGUES`).
COUNTRY_LEAGUES = {
    "ENG": (2001, 2032),
    "ESP": (2002,),
    "GER": (2003,),
    "ITA": (2004,),
    "FRA": (2005,),
    "POR": (2012,),
    "NED": (2013,),
    "BEL": (2024,),
    "TUR": (2025,),
    "GRE": (2026,),
    "SCO": (2027,),
    "AUT": (2028,),
    "DEN": (2029,),
    "NOR": (2030,),
    "SUI": (2031,),
}
#: The Champions League, the Europa League and the Conference League.
UEFA_LEAGUES = (2008, 2022, 2023)
#: Words of a club's name that say what it is, not which one it is.
GENERIC_WORDS = frozenset(
    "fc cf sc ac as afc club de da do del the calcio football futebol nk hnk gnk fk pfc ks mks "
    "wks sk if bk vv kv krc kaa rsc rc rcd ss ssc us ud cd sl cp sad jk".split()
)
#: A word of a club's name as UEFA and ESPN spell it differently.
WORD_ALIASES = {
    "kobenhavn": "copenhagen",
    "praha": "prague",
    "munchen": "munich",
    "wien": "vienna",
    "beograd": "belgrade",
    "crvena": "red",
    "zvezda": "star",
    "warszawa": "warsaw",
}


@dataclass(frozen=True)
class RankedClub:
    rank: int
    name: str  # UEFA's international name ("FC Bayern München")
    country: str  # UEFA's country code ("GER", "ENG", "CZE")
    uefa_id: str
    espn_id: str = ""  # from the snapshot; "" when it has none
    points: float = 0.0
    names: tuple[str, ...] = ()  # UEFA's other spellings ("Bayern München", "Atleti")


def season_year(today: date) -> int:
    """The season UEFA's ranking is asked for: its end year, 2027 for 2026-27 (from July)."""
    return today.year + 1 if today.month >= 7 else today.year


def club_words(name: str) -> frozenset[str]:
    """The words that tell a club apart, folded to ASCII lower case (`GENERIC_WORDS` left out,
    `WORD_ALIASES` applied)."""
    words = re.split(r"[^a-z0-9]+", to_ascii(name).lower())
    return frozenset(WORD_ALIASES.get(w, w) for w in words if len(w) > 1 and w not in GENERIC_WORDS)


@cache
def _snapshot() -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(
        package_bytes("retro_roster_patcher.assets", "uefa_club_ranking.json")
    )
    return doc


@cache
def _snapshot_ids() -> dict[str, str]:
    return {str(e["uefa_id"]): str(e["espn"]) for e in _snapshot()["ranking"]}


def _from_snapshot() -> list[RankedClub]:
    return [
        RankedClub(
            int(e["rank"]),
            str(e["name"]),
            str(e["country"]),
            str(e["uefa_id"]),
            str(e["espn"]),
            float(e["points"]),
            tuple(e.get("names") or ()),
        )
        for e in _snapshot()["ranking"]
    ]


def _parse(members: Any) -> list[RankedClub]:
    """The clubs of UEFA's member list, best first, each with its snapshot ESPN id."""
    clubs = []
    for m in members:
        member, ranking = m["member"], m["overallRanking"]
        uefa_id = str(member["id"])
        names = {member.get(k) for k in ("displayName", "displayOfficialName", "displayNameShort")}
        clubs.append(
            RankedClub(
                int(ranking["position"]),
                str(member["internationalName"]),
                str(member["countryCode"]),
                uefa_id,
                _snapshot_ids().get(uefa_id, ""),
                float(ranking.get("totalValue") or 0),
                tuple(sorted(str(n) for n in names - {None, member["internationalName"]})),
            )
        )
    return sorted(clubs, key=lambda c: c.rank)


def uefa_ranking(
    cache_dir: Path | str,
    on_status: StatusFn | None = None,
    transport: _http.Transport | None = None,
    *,
    today: date | None = None,
) -> list[RankedClub]:
    """UEFA's men's club coefficient ranking, best first: fetched once a day into `cache_dir`
    (the season `season_year` gives, else the one before, which UEFA serves until the new one
    has points), or the packaged snapshot, with a status line, when that fails."""
    ensure_cache_dir(cache_dir)
    today = today or date.today()
    path = Path(cache_dir) / f"uefa_ranking_{today.isoformat()}.json"
    try:
        return _parse(json.loads(path.read_text(encoding="utf-8"))["members"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    error: Exception = ApiError("no season answered")
    for year in (season_year(today), season_year(today) - 1):
        params = {
            "coefficientRange": "OVERALL",
            "coefficientType": "MEN_CLUB",
            "language": "EN",
            "page": 1,
            "pagesize": 500,
            "seasonYear": year,
        }
        try:
            members = _http.get_json(RANKING_URL, params=params, transport=transport)["data"][
                "members"
            ]
            clubs = _parse(members)
        except (ApiError, KeyError, TypeError, ValueError, AttributeError) as exc:
            error = exc
            continue
        if clubs:
            path.write_text(json.dumps({"members": members}), encoding="utf-8")
            return clubs
    emit_status(
        on_status,
        f"UEFA club ranking unavailable ({error}); using the packaged snapshot of the "
        f"{_snapshot()['date']} ranking",
    )
    return _from_snapshot()


def pair_club(
    club: RankedClub, espn_teams: Iterable[Team], team_country: Mapping[int, str]
) -> Team | None:
    """The ESPN team of a ranked club: the one with its snapshot id, else the one of its own
    country whose name shares the most words with one of the club's (`club_words`).

    `team_country` gives each team's country (a `COUNTRY_LEAGUES` code), or "" for a team
    found only in a UEFA competition: a club of a country ESPN has a league for is paired
    among that league's teams, any other among those. Two shared words pair, or one when it
    is all of the ESPN name ("Lens" is RC Lens) or no other team of the country has it
    (Celta Vigo is Real Club Celta, though Real Madrid shares "Real"). Among the UEFA-only
    teams one word is never enough, as they come from many countries (Dinamo-Minsk is not
    Dinamo Zagreb).
    """
    teams = list(espn_teams)
    if club.espn_id:
        return next((t for t in teams if str(t.id) == club.espn_id), None)
    home = club.country if club.country in COUNTRY_LEAGUES else ""
    want = frozenset().union(*(club_words(n) for n in (club.name, *club.names)))
    scored = []
    for team in teams:
        if team_country.get(team.id, "") != home:
            continue
        have = club_words(team.name) | club_words(team.short_name)
        if want & have:
            scored.append((len(want & have), have <= want, have, team))
    if not scored:
        return None
    shared, whole, have, team = max(scored, key=lambda s: (s[0], s[1]))
    rivals = any(other is not team and want & have & h for _, _, h, other in scored)
    if shared >= 2 or whole or (home and not rivals):
        return team
    return None
