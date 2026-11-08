"""Today's top national teams, by FIFA ranking, for the slots a club league leaves.

A soccer cartridge holds more slots than a club league fills, and the rest keep
their 1990s nations. `fetch_top_nations` fills them with ESPN's national squads
(league 2021) of the best-ranked sides that have one: `fifa_ranking` gives the
order, `ranked_slot_map` the slots.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.assets import package_bytes
from ..core.concurrency import emit_status
from ..core.errors import ApiError, CapabilityError, ensure_cache_dir
from ..core.models import ProgressFn, SlotMapping, StatusFn
from ..core.text import to_ascii
from . import _http
from .lineup import apply_lineup
from .models import LeagueData, Team, TeamRoster

if TYPE_CHECKING:
    from ..core.patcher import Patcher

RANKING_PAGE = "https://inside.fifa.com/fifa-world-ranking/men"
RANKING_API = "https://inside.fifa.com/api/ranking-overview"
#: ESPN's league of every men's national side it tracks, with current squads.
NATIONAL_LEAGUE = 2021


@dataclass(frozen=True)
class RankedNation:
    rank: int
    name: str
    code: str  # FIFA's three-letter code, which ESPN mostly uses as abbreviation


def _parse(document: Any) -> list[RankedNation]:
    """The ranked sides of a `ranking-overview` answer, best first; an unranked one is left out."""
    items = (entry["rankingItem"] for entry in document["rankings"])
    nations = [
        RankedNation(int(i["rank"]), str(i["name"]), str(i["countryCode"]))
        for i in items
        if i.get("rank") is not None
    ]
    return sorted(nations, key=lambda n: n.rank)


def _fetch(transport: _http.Transport | None) -> dict[str, Any]:
    """Today's `ranking-overview` document: the latest `id1NNNN` date the page lists."""
    tx = transport or _http.default_transport
    try:
        page = tx(RANKING_PAGE, {}, _http.DEFAULT_TIMEOUT).decode("utf-8", "replace")
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(f"GET {RANKING_PAGE} failed: {exc}") from exc
    # ponytail: newer releases list `FRS_Male_Football_<date>` ids, which this
    # endpoint answers empty; switch when FIFA serves them here.
    ids = re.findall(r'"id(1\d{4})"', page)
    if not ids:
        raise ApiError(f"No ranking date in {RANKING_PAGE}")
    date_id = f"id{max(ids, key=int)}"
    document = _http.get_json(
        RANKING_API, params={"locale": "en", "dateId": date_id}, transport=transport
    )
    if not isinstance(document, dict):
        raise ApiError(f"{RANKING_API} answered a {type(document).__name__}")
    return document


def fifa_ranking(
    cache_dir: Path | str,
    on_status: StatusFn | None = None,
    transport: _http.Transport | None = None,
) -> list[RankedNation]:
    """The FIFA men's ranking, best first: fetched once a day into `cache_dir`, or
    the packaged snapshot (with a status line) when the fetch fails."""
    ensure_cache_dir(cache_dir)
    cache = Path(cache_dir) / f"fifa_ranking_{date.today().isoformat()}.json"
    try:
        return _parse(json.loads(cache.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        document = _fetch(transport)
        nations = _parse(document)
        if not nations:
            raise ApiError("the ranking came back empty")
    except (ApiError, KeyError, TypeError, ValueError) as exc:
        snapshot = json.loads(package_bytes("retro_roster_patcher.assets", "fifa_ranking.json"))
        emit_status(
            on_status,
            f"FIFA ranking unavailable ({exc}); using the packaged snapshot of {snapshot['date']}",
        )
        return _parse(snapshot)
    cache.write_text(json.dumps(document), encoding="utf-8")
    return nations


def _key(name: str) -> str:
    return to_ascii(name).upper()


def _ranked_teams(
    ranking: Iterable[RankedNation], espn_teams: Iterable[Team], aliases: Mapping[str, str]
) -> list[Team]:
    """The ESPN teams the ranking names, in ranking order: by FIFA code against the
    ESPN abbreviation, else by name, else by the name `aliases` gives it."""
    teams = list(espn_teams)
    by_code = {t.code.upper(): t for t in teams if t.code}
    by_name = {_key(t.name): t for t in teams}
    alias = {_key(k): _key(v) for k, v in aliases.items()}
    out: list[Team] = []
    for nation in ranking:
        name = _key(nation.name)
        team = (
            by_code.get(nation.code.upper())
            or by_name.get(name)
            or by_name.get(alias.get(name, ""))
        )
        if team is not None and team not in out:
            out.append(team)
    return out


def ranked_slot_map(
    ranking: Iterable[RankedNation],
    espn_teams: Iterable[Team],
    free_slots: Sequence[int],
    own_slot: Callable[[Team], int | None],
    *,
    aliases: Mapping[str, str] | None = None,
) -> list[SlotMapping]:
    """Map the best-ranked ESPN teams onto `free_slots`, one each, until the slots run out.

    A nation whose `own_slot` (its slot on the cartridge) is free takes it; the
    others fill the slots left, in `free_slots`' order. Ranked sides ESPN does
    not have are skipped. `aliases` maps another spelling of a name to ESPN's.
    """
    chosen = _ranked_teams(ranking, espn_teams, aliases or {})[: len(free_slots)]
    placed: dict[int, Team] = {}
    moved: list[Team] = []
    for team in chosen:
        slot = own_slot(team)
        if slot is not None and slot in free_slots and slot not in placed:
            placed[slot] = team
        else:
            moved.append(team)
    rest = [s for s in free_slots if s not in placed]
    placed.update(zip(rest, moved, strict=False))
    return [
        SlotMapping(slot_index=slot, team_id=team.id, team_name=team.name)
        for slot, team in placed.items()
    ]


def fetch_top_nations(
    patcher: Patcher,
    rom: Path,
    season: int,
    taken: Iterable[int],
    on_progress: ProgressFn | None = None,
) -> tuple[LeagueData, list[SlotMapping]]:
    """(LeagueData, slot mapping) of the best-ranked national squads for the slots of
    `patcher.fill_slots(rom)` not in `taken`, for `map_rosters` and a `merge`.

    Squads and lineups are fetched in ranking order for as many nations as there
    are slots. A nation whose squad fails or comes back empty is skipped with a
    status line, and the next one takes its place. Each squad is in the game's
    fielding order (`suggest_squad_order`), as a league fetch leaves it.
    """
    if not patcher.supports_nation_fill:
        raise CapabilityError(f"{patcher.game_id or type(patcher).__name__} has no nation fill")
    blocked = set(taken)
    free = [s for s in patcher.fill_slots(rom) if s not in blocked]
    api: Any = getattr(patcher, "api")  # noqa: B009 -- the base Patcher declares no client
    league = next(iter(api.get_leagues(id=NATIONAL_LEAGUE, season=season)))
    if not free:
        return LeagueData(league=league), []
    ranking = fifa_ranking(
        patcher.cache_dir, on_status=patcher.status, transport=getattr(patcher, "_transport", None)
    )
    # ponytail: the aliases live as a module table beside each game's `national_slot_map`.
    aliases = getattr(sys.modules[type(patcher).__module__], "NATIONAL_ALIASES", {})
    rosters: list[TeamRoster] = []
    for team in _ranked_teams(ranking, api.get_teams(NATIONAL_LEAGUE, season), aliases):
        if len(rosters) == len(free):
            break
        if on_progress is not None:
            on_progress(
                len(rosters) / len(free),
                f"National squads {len(rosters) + 1}/{len(free)}: {team.name}",
            )
        roster = TeamRoster(team=team)
        try:
            roster.players = api.get_squad(team.id, season)
        except ApiError as exc:
            patcher.status(f"Skipped {team.name}: its squad failed ({exc})")
            continue
        if not roster.players:
            patcher.status(f"Skipped {team.name}: ESPN has no squad for it")
            continue
        try:
            lineup = api.get_lineup(
                team.id, NATIONAL_LEAGUE, squad=roster.players, team=team, season=season
            )
        except ApiError as exc:
            patcher.status(f"No lineup for {team.name} ({exc})")
            lineup = None
        apply_lineup(roster, lineup)
        roster.players = patcher.suggest_squad_order(roster)
        rosters.append(roster)
    mapping = ranked_slot_map(
        ranking,
        [r.team for r in rosters],
        free,
        lambda t: patcher.own_national_slot(rom, t),
        aliases=aliases,
    )
    return LeagueData(league=league, teams=rosters), mapping
