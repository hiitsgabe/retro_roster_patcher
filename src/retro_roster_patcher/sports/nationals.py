"""Today's top national teams, by FIFA ranking, for the slots a club league leaves.

A soccer cartridge holds more slots than a club league fills, and the rest keep
their 1990s nations. `fetch_top_nations` fills them with ESPN's national squads
(league 2021) of the best-ranked sides that have one: `fifa_ranking` gives the
order, `ranked_slot_map` the slots.

The ranking is FIFA's men's world ranking as FotMob serves it
(`https://www.fotmob.com/api/data/fifarankings/ranking?gender=men`): FIFA's own
`ranking-overview` API stops at September 2025. FotMob gives no country code, so
`RankedNation.code` comes from the packaged snapshot `assets/fifa_ranking.json`
(FotMob's list of the 2026-10-07 ranking, fetched 2026-11-08, with each side's
FIFA code added), which is also the fallback when FotMob cannot be reached.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.assets import package_bytes
from ..core.concurrency import emit_status
from ..core.errors import ApiError, CapabilityError, ensure_cache_dir
from ..core.models import ProgressFn, SlotMapping, StatusFn
from ..core.text import to_ascii
from . import _http
from .fotmob import API_URL
from .lineup import apply_lineup
from .models import League, LeagueData, Team, TeamRoster

if TYPE_CHECKING:
    from ..core.patcher import Patcher

RANKING_URL = f"{API_URL}/fifarankings/ranking"
#: ESPN's league of every men's national side it tracks, with current squads.
NATIONAL_LEAGUE = 2021


@dataclass(frozen=True)
class RankedNation:
    rank: int
    name: str
    code: str  # FIFA's three-letter code, which ESPN mostly uses as abbreviation; "" if unknown


def _key(name: str) -> str:
    return to_ascii(name).upper()


@cache
def _snapshot() -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(
        package_bytes("retro_roster_patcher.assets", "fifa_ranking.json")
    )
    return doc


@cache
def _codes() -> dict[str, str]:
    """FIFA code by FotMob's spelling of the name, from the snapshot."""
    return {_key(e["name"]): e["code"] for e in _snapshot()["ranking"]}


def _parse(entries: Any) -> list[RankedNation]:
    """The ranked sides of a FotMob ranking list, best first; an unranked one is left out."""
    nations = [
        RankedNation(int(e["rank"]), str(e["name"]), _codes().get(_key(str(e["name"])), ""))
        for e in entries
        if e.get("rank") is not None
    ]
    return sorted(nations, key=lambda n: n.rank)


def fifa_ranking(
    cache_dir: Path | str,
    on_status: StatusFn | None = None,
    transport: _http.Transport | None = None,
) -> list[RankedNation]:
    """The FIFA men's ranking, best first: fetched from FotMob once a day into
    `cache_dir`, or the packaged snapshot (with a status line) when that fails."""
    ensure_cache_dir(cache_dir)
    path = Path(cache_dir) / f"fifa_ranking_{date.today().isoformat()}.json"
    try:
        return _parse(json.loads(path.read_text(encoding="utf-8"))["ranking"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    try:
        entries = _http.get_json(RANKING_URL, params={"gender": "men"}, transport=transport)
        if not isinstance(entries, list):
            raise ApiError(f"{RANKING_URL} answered a {type(entries).__name__}")
        nations = _parse(entries)
        if not nations:
            raise ApiError("the ranking came back empty")
    except (ApiError, KeyError, TypeError, ValueError, AttributeError) as exc:
        emit_status(
            on_status,
            f"FIFA ranking unavailable ({exc}); using the packaged snapshot of the "
            f"{_snapshot()['date']} ranking",
        )
        return _parse(_snapshot()["ranking"])
    # A dict, as every cache file in the shared directory is.
    path.write_text(json.dumps({"ranking": entries}), encoding="utf-8")
    return nations


def _ranked_teams(
    ranking: Iterable[RankedNation], espn_teams: Iterable[Team], aliases: Mapping[str, str]
) -> list[Team]:
    """The ESPN teams the ranking names, in ranking order: by name, else by FIFA code
    against the ESPN abbreviation, else by the name `aliases` gives it. A code more
    than one ESPN team uses (Guam and Guatemala are both "GUA") never matches."""
    teams = list(espn_teams)
    codes = Counter(t.code.upper() for t in teams if t.code)
    by_code = {t.code.upper(): t for t in teams if t.code and codes[t.code.upper()] == 1}
    by_name = {_key(t.name): t for t in teams}
    alias = {_key(k): _key(v) for k, v in aliases.items()}
    out: list[Team] = []
    for nation in ranking:
        name = _key(nation.name)
        team = (
            by_name.get(name)
            or by_code.get(nation.code.upper())
            or by_name.get(alias.get(name, ""))
        )
        if team is not None and team not in out:
            out.append(team)
    return out


def cartridge_slot_map(
    names: Sequence[str], aliases: Mapping[str, str], data: LeagueData
) -> list[SlotMapping]:
    """A game's `national_slot_map`: each slot whose cartridge name (`names[slot]`,
    `aliases` turning the cartridge's spelling into ESPN's) is one of `data`'s
    national teams, paired with it, in slot order."""
    by_name = {_key(r.team.name): r.team for r in data.teams}
    out = []
    for slot, name in enumerate(names):
        team = by_name.get(_key(aliases.get(name, name)))
        if team is not None:
            out.append(SlotMapping(slot_index=slot, team_id=team.id, team_name=team.name))
    return out


def cartridge_slot(names: Sequence[str], aliases: Mapping[str, str], team: Team) -> int | None:
    """The slot `cartridge_slot_map` gives national team `team`, or None for a nation
    the cartridge lacks."""
    data = LeagueData(league=League(id=NATIONAL_LEAGUE, name=""), teams=[TeamRoster(team=team)])
    return next((m.slot_index for m in cartridge_slot_map(names, aliases, data)), None)


def ranked_slot_map(
    ranking: Iterable[RankedNation],
    espn_teams: Iterable[Team],
    free_slots: Sequence[int],
    own_slot: Callable[[Team], int | None],
    *,
    aliases: Mapping[str, str] | None = None,
    home_only: Collection[int] = (),
) -> list[SlotMapping]:
    """Map the best-ranked ESPN teams onto `free_slots`, one each, until the slots run out.

    A nation whose `own_slot` (its slot on the cartridge) is free takes it; the
    others fill the slots left, in `free_slots`' order. Ranked sides ESPN does
    not have are skipped. `aliases` maps another spelling of a name to ESPN's.
    A slot in `home_only` takes its own nation only, never a moved one (a slot
    where any other nation's flag would draw wrong).
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
    rest = [s for s in free_slots if s not in placed and s not in home_only]
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
        home_only=patcher.home_only_slots(rom),
    )
    return LeagueData(league=league, teams=rosters), mapping
