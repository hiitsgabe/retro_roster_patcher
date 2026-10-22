"""Pair each catalogue club with its ESPN team id: espn_ids.json + a report."""

import json
import os
import re
import sys
import unicodedata
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
LEAGUES = {
    "England": ["eng.1", "eng.2"],
    "Spain": ["esp.1", "esp.2"],
    "Germany": ["ger.1", "ger.2"],
    "Italy": ["ita.1", "ita.2"],
    "France": ["fra.1", "fra.2"],
    "Portugal": ["por.1"],
    "Netherlands": ["ned.1"],
    "Belgium": ["bel.1"],
    "Norway": ["nor.1"],
    "Saudi Arabia": ["ksa.1"],
    "Greece": ["gre.1"],
    "Turkey": ["tur.1"],
    "Serbia": ["srb.1"],
    "Ukraine": ["ukr.1"],
    "Ecuador": ["ecu.1"],
    "Brazil": ["bra.1", "bra.2"],
    "South Africa": ["rsa.1"],
    "Wales": ["wal.1"],
    "Russia": ["rus.1"],
    "Egypt": ["egy.1"],
    "Estonia": ["est.1"],
    "Cyprus": ["cyp.1"],
    "Czech Republic": ["cze.1"],
    "Argentina": ["arg.1"],
    "Croatia": ["cro.1"],
    "Denmark": ["den.1"],
    "Hungary": ["hun.1"],
    "Scotland": ["sco.1"],
    "Mexico": ["mex.1"],
}
EXTRA = [
    "uefa.champions",
    "uefa.europa",
    "uefa.europa.conf",
    "conmebol.libertadores",
    "conmebol.sudamericana",
    "afc.champions",
    "caf.champions",
]
DROP = {
    "fc",
    "cf",
    "sc",
    "ac",
    "as",
    "afc",
    "sk",
    "fk",
    "cd",
    "cr",
    "ss",
    "ssc",
    "rc",
    "the",
    "club",
    "de",
    "da",
    "do",
    "del",
    "futebol",
    "football",
    "calcio",
    "1907",
    "1909",
    "bc",
    "kv",
    "sfc",
    "ec",
    "ca",
    "se",
    "spc",
    "cp",
    "sl",
}
ALIAS = {
    "bayern munchen": "bayern munich",
    "inter milan": "internazionale",
    "sporting": "sporting cp",
    "bodo glimt": "bodo glimt",
    "union st gilloise": "union st gilloise",
    "olympiakos": "olympiacos",
    "fk red star belgrade": "crvena zvezda",
    "independiente": "independiente del valle",
    "zenit st petersburg": "zenit st petersburg",
    "ajax amsterdam": "ajax",
    "atletico pr": "athletico paranaense",
    "atletico mineiro": "atletico mg",
    "ldu de quito": "ldu quito",
    "celta vigo": "celta vigo",
    "rc strasbourg": "strasbourg",
    "red bull bragantino": "bragantino",
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(w for w in s.split() if w not in DROP)


def teams(code: str) -> list[dict]:
    from retro_roster_patcher.sports._http import DEFAULT_USER_AGENT

    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/teams"
    try:
        d = json.loads(
            urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT}), timeout=20
            ).read()
        )
    except Exception:
        return []
    out = []
    for lg in d.get("sports", [{}])[0].get("leagues", []):
        for t in lg.get("teams", []):
            out.append(t["team"])
    return out


def main():
    clubs = [
        line.rstrip("\n").split("\t")
        for line in open(os.path.join(HERE, "clubs.tsv"))
        if line.strip()
    ]
    pool: dict[str, dict] = {}
    for codes in LEAGUES.values():
        for c in codes:
            for t in teams(c):
                pool.setdefault(t["id"], t | {"_league": c})
    for c in EXTRA:
        for t in teams(c):
            pool.setdefault(t["id"], t | {"_league": c})
    print("espn teams in pool:", len(pool))
    known = {
        "Athletico-PR": "3458",
        "Atlético PR": "3458",
        "Atlético Mineiro": "7632",
        "Bahia": "9967",
        "Botafogo": "6086",
        "Chapecoense": "9318",
        "Corinthians": "874",
        "Coritiba": "3456",
        "Cruzeiro": "2022",
        "Flamengo": "819",
        "Fluminense FC": "3445",
        "Grêmio": "6273",
        "Internacional": "1936",
        "Mirassol": "9169",
        "Palmeiras": "2029",
        "Red Bull Bragantino": "6079",
        "Remo": "4936",
        "Santos FC": "2674",
        "São Paulo": "2026",
        "Vasco da Gama": "3454",
        "Vitória": "3457",
    }
    result, missing = {}, []
    for rank, name, country, title in clubs:
        if name in known:
            result[name] = {
                "espn": known[name],
                "espn_name": pool.get(known[name], {}).get("displayName", name),
                "how": "brasileirao rosters",
            }
            continue
        want = norm(ALIAS.get(norm(name), name))
        want_t = norm(title)
        best, score = None, 0
        for t in pool.values():
            names = {
                norm(t.get(k, ""))
                for k in ("displayName", "shortDisplayName", "name", "location", "nickname")
            }
            names.discard("")
            for n in names:
                s = 0
                if n == want or n == want_t:
                    s = 3
                elif want and (want in n or n in want) and min(len(n), len(want)) >= 4:
                    s = 2
                elif set(want.split()) & set(n.split()) and len(
                    set(want.split()) & set(n.split())
                ) >= max(1, len(want.split()) - 1):
                    s = 1
                if s > score:
                    best, score = t, s
        if best is not None and score >= 2:
            result[name] = {
                "espn": best["id"],
                "espn_name": best["displayName"],
                "league": best["_league"],
                "how": f"name score {score}",
            }
        else:
            missing.append((rank, name, country, best and best["displayName"], score))
    json.dump(result, open(os.path.join(HERE, "espn_ids.json"), "w"), indent=1, ensure_ascii=False)
    print("matched", len(result), "missing", len(missing))
    for m in missing:
        print("  MISSING", m)
    for name, r in result.items():
        if r.get("how", "").startswith("name score 2"):
            print("  check:", name, "->", r["espn_name"], r.get("league"))


if __name__ == "__main__":
    main()
