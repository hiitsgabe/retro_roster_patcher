"""Build `src/retro_roster_patcher/assets/uefa_club_ranking.json`: UEFA's club coefficient
ranking with each club's ESPN team id, the snapshot `sports.clubs` pairs the live ranking by.

    python build.py [season_year] [top]

Fetches the ranking from comp.uefa.com and the ESPN team lists of the European leagues and
competitions (`sports.clubs.COUNTRY_LEAGUES`, `UEFA_LEAGUES`), pairs each club with the
library's own `pair_club` (same country, shared name words), applies `overrides.json` (UEFA
id -> ESPN id, "" for none) and prints a "check:" line for every pairing that is not one
name for one name, and every club left without an id, to look through by hand.
"""

import json
import os
import sys
import urllib.request
from datetime import date

from retro_roster_patcher.sports.clubs import (
    COUNTRY_LEAGUES,
    UEFA_LEAGUES,
    RankedClub,
    club_words,
    pair_club,
    season_year,
)
from retro_roster_patcher.sports.espn import ESPN_LEAGUES
from retro_roster_patcher.sports.models import Team

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(
    HERE, "..", "..", "src", "retro_roster_patcher", "assets", "uefa_club_ranking.json"
)
URL = (
    "https://comp.uefa.com/v2/coefficients?coefficientRange=OVERALL&coefficientType=MEN_CLUB"
    "&language=EN&page=1&pagesize=500&seasonYear={}"
)
ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer/{}/teams"
CODES = {item["id"]: item["code"] for item in ESPN_LEAGUES}


def get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def teams(league: int) -> list[Team]:
    try:
        raw = get(ESPN.format(CODES[league]))["sports"][0]["leagues"][0]["teams"]
    except Exception as exc:  # a league ESPN has nothing for this season
        print(f"no teams for {CODES[league]}: {exc}")
        return []
    return [
        Team(
            id=int(t["team"]["id"]),
            name=t["team"]["displayName"],
            short_name=t["team"].get("shortDisplayName", ""),
            code=t["team"].get("abbreviation", ""),
        )
        for t in raw
    ]


def main() -> None:
    season = int(sys.argv[1]) if len(sys.argv) > 1 else season_year(date.today())
    top = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    data = get(URL.format(season))["data"]
    pool: dict[int, Team] = {}
    country: dict[int, str] = {}
    for code, leagues in COUNTRY_LEAGUES.items():
        for league in leagues:
            for team in teams(league):
                pool.setdefault(team.id, team)
                country[team.id] = code
    for league in UEFA_LEAGUES:
        for team in teams(league):
            pool.setdefault(team.id, team)
    overrides = json.load(open(os.path.join(HERE, "overrides.json")))
    ranking = []
    for m in data["members"][:top]:
        member, rank = m["member"], m["overallRanking"]["position"]
        names = {member.get(k) for k in ("displayName", "displayOfficialName", "displayNameShort")}
        names = sorted(str(n) for n in names - {None, member["internationalName"]})
        club = RankedClub(
            rank,
            member["internationalName"],
            member["countryCode"],
            member["id"],
            names=tuple(names),
        )
        team = pair_club(club, pool.values(), country)
        espn = str(team.id) if team else ""
        if member["id"] in overrides:
            espn = overrides[member["id"]]
            team = pool.get(int(espn)) if espn else None
        else:
            exact = team and any(
                club_words(n) == club_words(team.name) for n in (club.name, *club.names)
            )
            if not exact:
                print(
                    f"check: {rank} {club.name} ({club.country}) -> "
                    f"{espn or 'NONE'} {team.name if team else ''}"
                )
        ranking.append(
            {
                "rank": rank,
                "uefa_id": member["id"],
                "name": club.name,
                "names": names,
                "country": club.country,
                "points": m["overallRanking"]["totalValue"],
                "espn": espn,
            }
        )
    head = {
        "source": URL.format(season),
        "season_year": season,
        "date": data["lastUpdateDate"][:10],
        "fetched": date.today().isoformat(),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(json.dumps(head)[:-1] + ',"ranking":[\n')
        f.write(",\n".join(json.dumps(r, ensure_ascii=False) for r in ranking) + "\n]}\n")
    print(f"{len(ranking)} clubs, {sum(1 for r in ranking if r['espn'])} with an ESPN id -> {OUT}")


if __name__ == "__main__":
    main()
