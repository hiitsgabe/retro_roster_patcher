"""JSON round-trip for `LeagueData`.

`fetch` and `patch` are separate commands, run as separate processes by both
consumers. This module is the contract for the file that passes between them.

Reading is lenient about *optional* fields: unknown keys are dropped and absent
ones fall back to dataclass defaults, so a rosters file written by a newer
version of the library still loads in an older one -- which matters when a
Flutter app bundles a pinned CPython and the desktop tool does not. It is not
lenient about required ones: `League` and `Team` declare `id` and `name` with no
defaults, so a payload missing either raises `TypeError`.
"""

from __future__ import annotations

from dataclasses import asdict, fields
from typing import Any

from .models import League, LeagueData, Player, PlayerStats, Team, TeamRoster


def league_data_to_dict(data: LeagueData) -> dict[str, Any]:
    """Convert to plain dicts.

    Every value the models *declare* is JSON-serialisable, but `player_stats`
    keeps its `int` keys. `json.dumps` stringifies those on the way out, which is
    the whole reason the read side has to convert them back.

    `extra` is the deliberate exception. It is `dict[str, Any]`, an escape hatch
    holding whatever the provider put there, so this function hands back a `set`
    or a `datetime` as readily as a `str` and `json.dumps` then raises. Keeping
    what goes into `extra` dumpable is the caller's responsibility, not this
    module's.
    """
    return asdict(data)


def _only_declared(cls: Any, raw: dict[str, Any]) -> dict[str, Any]:
    """The subset of `raw` that `cls` actually declares as fields.

    `cls` is `Any` rather than `type[T]` because `dataclasses.fields` is typed
    against `DataclassInstance`, which a bare type variable does not satisfy --
    the generic form needs a `# type: ignore`, and this package does not carry
    those. Returning the keyword arguments instead of the built instance also
    keeps each construction site's own type precise.
    """
    declared = {f.name for f in fields(cls)}
    return {k: v for k, v in raw.items() if k in declared}


def _team_roster_from_dict(raw: dict[str, Any]) -> TeamRoster:
    return TeamRoster(
        team=Team(**_only_declared(Team, raw.get("team") or {})),
        players=[Player(**_only_declared(Player, p)) for p in (raw.get("players") or [])],
        # JSON object keys are always strings, so this one comes back keyed by
        # `"18"`. `extra` below needs no equivalent conversion: both producers of
        # the leaders blob already key it by `str`, and its inner shape is
        # provider-defined, so re-keying it would be a guess.
        player_stats={
            int(pid): PlayerStats(**_only_declared(PlayerStats, s))
            for pid, s in (raw.get("player_stats") or {}).items()
        },
        loading=bool(raw.get("loading", False)),
        # `or ""` rather than a `get` default, because an empty `error` is how a
        # healthy roster says "no error" and every falsy JSON value means the
        # same thing. `str()` alone would render `null` as `"None"`, `false` as
        # `"False"` and `0` as `"0"` -- all non-empty, so all truthy to a
        # consumer's `if roster.error:`, reporting a failure on a roster that
        # had none. `loading` above needs no such guard: `bool(None)` is already
        # `False`, so its `get` default and an `or` are indistinguishable.
        error=str(raw.get("error") or ""),
        extra=dict(raw.get("extra") or {}),
    )


def league_data_from_dict(raw: dict[str, Any]) -> LeagueData:
    """Rebuild a `LeagueData` from `league_data_to_dict` output."""
    return LeagueData(
        league=League(**_only_declared(League, raw.get("league") or {})),
        teams=[_team_roster_from_dict(t) for t in (raw.get("teams") or [])],
    )
