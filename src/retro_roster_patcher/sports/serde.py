"""JSON round-trip for `LeagueData`, the file that passes between `fetch` and
`patch`.

Reading is lenient about *optional* fields: unknown keys are dropped and absent
ones fall back to dataclass defaults, so a rosters file written by a newer
version of the library still loads in an older one. It is not lenient about
required ones: a payload missing `id` or `name` raises `TypeError`.
"""

from __future__ import annotations

from dataclasses import asdict, fields
from typing import Any

from .models import League, LeagueData, Player, PlayerStats, Team, TeamRoster


def league_data_to_dict(data: LeagueData) -> dict[str, Any]:
    """Convert to plain dicts.

    `player_stats` keeps its `int` keys here; `json.dumps` stringifies them,
    which is why the read side converts them back. Keeping `extra` dumpable is
    the caller's responsibility.
    """
    return asdict(data)


def _only_declared(cls: Any, raw: dict[str, Any]) -> dict[str, Any]:
    """The subset of `raw` that `cls` actually declares as fields.

    `cls` is `Any`, not `type[T]`: `dataclasses.fields` is typed against
    `DataclassInstance | type[DataclassInstance]`, which a type variable does
    not satisfy.
    """
    declared = {f.name for f in fields(cls)}
    return {k: v for k, v in raw.items() if k in declared}


def _team_roster_from_dict(raw: dict[str, Any]) -> TeamRoster:
    return TeamRoster(
        team=Team(**_only_declared(Team, raw.get("team") or {})),
        players=[Player(**_only_declared(Player, p)) for p in (raw.get("players") or [])],
        # JSON object keys are always strings, so this one comes back keyed by
        # `"18"`. `extra` needs no equivalent conversion: it is already `str`-keyed.
        player_stats={
            int(pid): PlayerStats(**_only_declared(PlayerStats, s))
            for pid, s in (raw.get("player_stats") or {}).items()
        },
        loading=bool(raw.get("loading", False)),
        # `or ""` and not a `get` default: bare `str()` renders `null` as
        # `"None"`, which is truthy to a consumer's `if roster.error:`.
        error=str(raw.get("error") or ""),
        extra=dict(raw.get("extra") or {}),
    )


def league_data_from_dict(raw: dict[str, Any]) -> LeagueData:
    """Rebuild a `LeagueData` from `league_data_to_dict` output."""
    return LeagueData(
        league=League(**_only_declared(League, raw.get("league") or {})),
        teams=[_team_roster_from_dict(t) for t in (raw.get("teams") or [])],
    )
