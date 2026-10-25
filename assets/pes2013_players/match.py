"""Pair the PES 2013 (PS2, SLUS_219.55) disc's players with ESPN ids, so a
patch can give a player who is on the disc already his own look wherever he
plays now. Writes `src/retro_roster_patcher/assets/pes2013_disc_players.json`,
which the patcher only reads: this runs offline, before a release.

    python assets/pes2013_players/match.py <pes2013.iso> <cache_dir> [season]

Reads the 4941 records' display names, nationality and age from the disc
(`RomReader`), fetches every men's ESPN league the library knows for the
season (squads are cached by the client), and pairs a disc record with an
ESPN player when the disc's name equals the ESPN display name, the ESPN
"F. Surname" or the ESPN surname (folded to ASCII upper case), the disc's age
is the player's age in 2012 give or take a year, and the nationalities agree
(a disc nationality without a national team on the disc agrees with any
citizenship that is not one of the disc's 60 nations). Then ESPN's team
history for the player (the athlete bio, cached in `cache_dir`) is read
against the disc club whose squad holds the record: a record at a real club
the player never played for around 2012 is a different person and is dropped
(the disc's made-up English names are translated first; its fantasy clubs
and records only a national squad holds cannot be checked). A pairing is kept
when it is the one match on both sides, or the one match the history
confirms. Prints what it paired, per league, and what it dropped. Review the
output before committing the JSON.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from retro_roster_patcher.games.pes2013_ps2.models import (  # noqa: E402
    AGE_BASE,
    AGE_BYTE,
    AGE_MASK,
    AGE_SHIFT,
    NATIONALITY_BYTE,
)
from retro_roster_patcher.games.pes2013_ps2.rom_reader import RomReader  # noqa: E402
from retro_roster_patcher.sports.espn import ESPN_LEAGUES, EspnClient  # noqa: E402

OUT = (
    Path(__file__).resolve().parents[2]
    / "src/retro_roster_patcher/assets/pes2013_disc_players.json"
)
DISC_YEAR = 2012  # the ages on the disc are the 2012-13 season's
AGE_SLACK = 1  # a birth year alone leaves the age one off either way
WOMEN = ("fifa.wwc",)
CUPS = (2008, 2009)  # the club cups double the clubs
BIO_URL = "https://site.web.api.espn.com/apis/common/v3/sports/soccer/athletes/{id}/bio"
HISTORY_YEARS = (2010, 2014)  # around the disc's 2012: loans and stale squads included
#: ESPN citizenship -> the disc's national-team name, where they differ.
NATION_ALIASES = {
    "CZECHIA": "CZECH REPUBLIC",
    "IVORY COAST": "COTE D'IVOIRE",
    "REPUBLIC OF IRELAND": "IRELAND",
    "TURKIYE": "TURKEY",
    "USA": "UNITED STATES",
    "CHINA PR": "CHINA",
    "UNITED ARAB EMIRATES": "UAE",
}
#: The disc's made-up club names -> the club, as ESPN names it.
FAKE_CLUBS = {
    "NORTH LONDON": "ARSENAL",
    "WEST MIDLANDS VILLAGE": "ASTON VILLA",
    "LONDON FC": "CHELSEA",
    "MERSEYSIDE BLUE": "EVERTON",
    "WEST LONDON WHITE": "FULHAM",
    "MERSEYSIDE RED": "LIVERPOOL",
    "MAN BLUE": "MANCHESTER CITY",
    "TYNESIDE": "NEWCASTLE UNITED",
    "NORTHLUCK C": "NORWICH CITY",
    "NORTH WEST LONDON": "QUEENS PARK RANGERS",
    "BERKSHIRE BLUES": "READING",
    "SOTON": "SOUTHAMPTON",
    "THE POTTERIES": "STOKE CITY",
    "WEARSIDE": "SUNDERLAND",
    "SWEARCLE": "SWANSEA CITY",
    "NORTH EAST LONDON": "TOTTENHAM HOTSPUR",
    "WEST MIDLANDS STRIPES": "WEST BROMWICH ALBION",
    "EAST LONDON": "WEST HAM UNITED",
    "LANCASHIRE ATHLETIC": "WIGAN ATHLETIC",
    "INTER": "INTERNAZIONALE",
    "AT. MADRID": "ATLETICO MADRID",
    "OLYMPIQUE LYONNAIS": "LYON",
    "STADE BRESTOIS 29": "BREST",
    "SPARTA PRAHA": "SPARTA PRAGUE",
    "SCHALKE04": "SCHALKE 04",
    "N.E.C. NIJMEGEN": "NEC NIJMEGEN",
    "ATHLETIC CLUB": "ATHLETIC BILBAO",
    "SPORTING": "SPORTING CP",
    "PFC CSKA MOSKVA": "CSKA MOSCOW",
}
FANTASY_CLUBS = {"PES UNITED", "WE UNITED", "BABILAYNA CF", "ATHLETIC CLUB SALSABIE"}
#: Words that say "a club" rather than which club.
CLUB_WORDS = {
    *"FC SC AC CF CFC KV SA JK SK UD CA RC RCD AS SL SS SSC USC UC ACF BC NK PFC RSC LOSC".split(),
    *"CALCIO CLUB FOOTBALL DE DI DU DEN THE STADE OLYMPIQUE ESTAC OGC AJ".split(),
}
LEAGUES = [item for item in ESPN_LEAGUES if item["id"] not in CUPS and item["code"] not in WOMEN]


def norm(text: str) -> str:
    for odd, plain in (
        ("Ł", "L"),
        ("ł", "l"),
        ("Æ", "AE"),
        ("æ", "ae"),
        ("Ø", "O"),
        ("ø", "o"),
        ("ß", "ss"),
    ):
        text = text.replace(odd, plain)
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", folded.upper().replace("-", " ")).strip()


class Disc:
    """The disc's records by name, with the nationality, age and club of each."""

    def __init__(self, iso: str) -> None:
        reader = RomReader(iso)
        assert reader.load(), iso
        tables = reader.player_tables()
        overlay = reader.overlay_teams()
        self.by_name: dict[str, list[int]] = defaultdict(list)
        self.nationality: dict[int, int] = {}
        self.age: dict[int, int] = {}
        for i in range(1, tables.player_count):
            record = tables.record(i)
            name = norm(record[:32].decode("utf-16-le", "replace").split("\0")[0])
            if not name:
                continue
            self.by_name[name].append(i)
            self.nationality[i] = record[NATIONALITY_BYTE]
            self.age[i] = ((record[AGE_BYTE] >> AGE_SHIFT) & AGE_MASK) + AGE_BASE
        self.nations: dict[int, str] = {}
        for row, (nation, _code) in overlay.nationals.items():
            if nation.text.startswith("CLASSIC"):
                continue
            squad = [i for i in tables.national_squad(row) if i]
            if squad:
                self.nations[self.nationality[squad[0]]] = norm(nation.text)
        self.nation_names = set(self.nations.values())
        self.clubs: dict[int, list[str]] = defaultdict(list)
        for club, (club_name, _code) in overlay.clubs.items():
            name = norm(club_name.text)
            if name in FANTASY_CLUBS:
                continue
            for i in tables.club_squad(club):
                if i:
                    self.clubs[i].append(FAKE_CLUBS.get(name, name))

    def agrees(self, index: int, citizenship: str, birth_year: int) -> bool:
        if abs(self.age[index] - (DISC_YEAR - birth_year)) > AGE_SLACK:
            return False
        nation = norm(citizenship)
        nation = NATION_ALIASES.get(nation, nation)
        disc_nation = self.nations.get(self.nationality[index])
        if disc_nation is None:
            return nation not in self.nation_names
        return nation == disc_nation


def team_history(cache_dir: str, player_id: int) -> list[dict] | None:
    """ESPN's team history for a player, cached; None when it cannot be read."""
    path = Path(cache_dir) / f"espn_bio_{player_id}.json"
    if path.exists():
        return json.loads(path.read_text()).get("teamHistory", [])
    try:
        with urllib.request.urlopen(BIO_URL.format(id=player_id), timeout=20) as answer:
            data = json.loads(answer.read())
    except Exception as exc:  # noqa: BLE001 - a missing bio is "unknown", not a stop
        print(f"    no bio for {player_id}: {exc}", file=sys.stderr)
        return None
    path.write_text(json.dumps(data))
    return data.get("teamHistory", [])


def club_words(name: str) -> set[str]:
    return {w for w in re.split(r"[^A-Z0-9]+", norm(name)) if w and w not in CLUB_WORDS}


def same_club(a: str, b: str) -> bool:
    wa, wb = club_words(a), club_words(b)
    return bool(wa and wb) and (wa <= wb or wb <= wa)


def spans(seasons: str) -> list[tuple[int, int]]:
    out = []
    for part in seasons.split(","):
        bounds = part.strip().split("-")
        if len(bounds) == 2 and bounds[0].isdigit():
            end = int(bounds[1]) if bounds[1].isdigit() else 9999
            out.append((int(bounds[0]), end))
    return out


def played_for(history: list[dict] | None, clubs: list[str]) -> bool | None:
    """True when the history puts the player at one of `clubs` in the disc's
    years, False when it does not, None when there is nothing to compare."""
    clubs = [club for club in clubs if club_words(club)]
    if not history or not clubs:
        return None
    first, last = HISTORY_YEARS
    for entry in history:
        if not any(same_club(entry.get("displayName", ""), club) for club in clubs):
            continue
        if any(a <= last and b >= first for a, b in spans(entry.get("seasons", ""))):
            return True
    return False


def main(iso: str, cache_dir: str, season: int) -> None:
    disc = Disc(iso)
    client = EspnClient(cache_dir, on_status=lambda line: print("   ", line, file=sys.stderr))
    hits: dict[tuple[int, int], tuple[str, str]] = {}  # (espn id, disc index) -> who, league
    by_player: dict[int, set[int]] = defaultdict(set)
    by_record: dict[int, set[int]] = defaultdict(set)
    for league in LEAGUES:
        code = str(league["code"])
        try:
            teams = client.get_teams(int(str(league["id"])), season)
        except Exception as exc:  # noqa: BLE001 - one league down must not stop the rest
            print(f"{code}: no teams ({exc})", file=sys.stderr)
            continue
        for team in teams:
            try:
                squad = client.get_squad(team.id, season, league_code=code)
            except Exception:  # noqa: BLE001
                continue
            for player in squad:
                if not player.birth_date[:4].isdigit() or not player.nationality:
                    continue
                year = int(player.birth_date[:4])
                full = norm(player.name)
                last = norm(player.last_name) if player.last_name else ""
                first = norm(player.first_name) if player.first_name else ""
                forms = {full, last}
                if first and last:
                    forms.add(f"{first[0]}. {last}")
                for form in forms:
                    for index in disc.by_name.get(form, ()):
                        if disc.agrees(index, player.nationality, year):
                            who = f"{player.name} ({team.name}, {player.nationality}, {year})"
                            hits[(player.id, index)] = (who, code)
                            by_player[player.id].add(index)
                            by_record[index].add(player.id)
    verdict: dict[tuple[int, int], bool | None] = {}
    for pid, index in hits:
        verdict[(pid, index)] = played_for(team_history(cache_dir, pid), disc.clubs.get(index, []))
    confirmed = {key for key, ok in verdict.items() if ok}
    pairs: dict[int, int] = {}
    by_league: Counter[str] = Counter()
    dropped: list[str] = []
    checked: Counter[str] = Counter()
    for (pid, index), (who, code) in sorted(hits.items(), key=lambda kv: kv[1][0]):
        ok = verdict[(pid, index)]
        clubs = disc.clubs.get(index, [])
        if ok is False:
            dropped.append(f"{who}: disc {index} plays for {clubs}, not in the history")
            continue
        unique = len(by_player[pid]) == 1 and len(by_record[index]) == 1
        # The history settles a shared name when it confirms this pairing alone.
        rivals = {(p, index) for p in by_record[index]} | {(pid, i) for i in by_player[pid]}
        resolved = ok is True and confirmed & rivals == {(pid, index)}
        if not unique and not resolved:
            records, players = sorted(by_player[pid]), sorted(by_record[index])
            dropped.append(f"{who}: disc records {records}, espn {players}")
            continue
        if pid in pairs:
            continue
        pairs[pid] = index
        by_league[code] += 1
        checked["history confirms" if ok else "nothing to check"] += 1
        tag = "confirmed" if ok else "unchecked"
        print(f"{who:60s} -> disc {index} age {disc.age[index]} {clubs} {tag}")
    for line in dropped:
        print("dropped:", line)
    OUT.write_text(
        json.dumps(
            {
                "disc": "SLUS_219.55",
                "season": season,
                "players": {str(k): v for k, v in sorted(pairs.items())},
            },
            indent=0,
        )
        + "\n"
    )
    print("history:", dict(checked))
    print(f"paired {len(pairs)} players: {dict(by_league)}")
    print(f"dropped {len(dropped)}; wrote {OUT}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 2026)
