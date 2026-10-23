"""Fill the head shots one provider lacks from another.

ESPN's soccer rosters carry a photo for almost no one, FotMob's for almost
everyone, and the two share no id. A player is matched across them by what both
publish: the birth date first, then the name.

  * a birth date that picks out one player on the other squad is a match;
  * a birth date shared by several is settled by name;
  * a name closer than `NAME_THRESHOLD`, and clearly closer than the runner-up,
    is a match when the birth date is missing or disagrees (a typo in either
    source's date still finds its player).

Nothing is overwritten: a player who already has a photo keeps it, and one with
no match stays without, which every consumer must tolerate anyway.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from difflib import SequenceMatcher

from ..core.concurrency import emit_status
from ..core.text import to_ascii
from .fotmob import FotmobClient
from .models import LeagueData, Player, Team, TeamRoster

NAME_THRESHOLD = 0.8
# How much closer the best name must be than the second best. Two "Samuel"s on
# one squad would otherwise take each other's photo.
NAME_MARGIN = 0.05
TEAM_THRESHOLD = 0.75


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z ]", " ", to_ascii(text).lower()).split())


def _similarity(a: str, b: str) -> float:
    """0..1. One name's words all inside the other's ("Gutte Silva", "Gutte")
    count as 0.9: providers drop and add surnames freely."""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    words_a, words_b = set(a.split()), set(b.split())
    contained = 0.9 if words_a <= words_b or words_b <= words_a else 0.0
    return max(SequenceMatcher(None, a, b).ratio(), contained)


def match_squad(players: list[Player], candidates: list[Player]) -> dict[int, Player]:
    """`player.id -> the candidate who is the same person`, for those found."""
    by_birth: dict[str, list[Player]] = {}
    for c in candidates:
        if c.birth_date:
            by_birth.setdefault(c.birth_date, []).append(c)

    matched: dict[int, Player] = {}
    taken: set[int] = set()
    for p in players:
        same_day = [c for c in by_birth.get(p.birth_date, []) if c.id not in taken]
        if len(same_day) == 1:
            matched[p.id] = same_day[0]
            taken.add(same_day[0].id)
        elif same_day:
            best = max(same_day, key=lambda c: _similarity(p.name, c.name))
            if _similarity(p.name, best.name) >= 0.5:
                matched[p.id] = best
                taken.add(best.id)

    for p in players:
        if p.id in matched:
            continue
        scored = sorted(
            ((_similarity(p.name, c.name), c) for c in candidates if c.id not in taken),
            key=lambda pair: pair[0],
            reverse=True,
        )
        if not scored or scored[0][0] < NAME_THRESHOLD:
            continue
        if len(scored) > 1 and scored[0][0] - scored[1][0] < NAME_MARGIN:
            continue
        matched[p.id] = scored[0][1]
        taken.add(scored[0][1].id)
    return matched


def _find_team(team: Team, others: list[Team]) -> Team | None:
    def score(other: Team) -> float:
        return max(
            _similarity(a, b)
            for a in (team.name, team.short_name)
            for b in (other.name, other.short_name)
            if a and b
        )

    best = max(others, key=score, default=None)
    return best if best is not None and score(best) >= TEAM_THRESHOLD else None


def fill_headshots(
    data: LeagueData,
    client: FotmobClient,
    on_status: Callable[[str], None] | None = None,
) -> int:
    """Give every player of `data` who has no photo the one FotMob has.

    Returns how many were filled. A team FotMob does not list, or a request that
    fails, costs that team its photos and nothing else.
    """
    needy = [r for r in data.teams if any(not p.photo_url for p in r.players)]
    if not needy:
        return 0
    others = client.get_teams(data.league.id, data.league.season)
    if not others:
        emit_status(on_status, "FotMob has no teams for this league; photos not filled")
        return 0

    filled = 0
    for roster in needy:
        filled += _fill_roster(roster, others, client, on_status)
    return filled


def _fill_roster(
    roster: TeamRoster,
    others: list[Team],
    client: FotmobClient,
    on_status: Callable[[str], None] | None,
) -> int:
    other = _find_team(roster.team, others)
    if other is None:
        emit_status(on_status, f"{roster.team.name}: not found on FotMob, photos not filled")
        return 0
    # Match the whole roster, not just who lacks a photo: a FotMob player who is
    # already someone else's must not be offered to a stranger.
    found = match_squad(roster.players, client.get_squad(other.id))
    filled = 0
    for player in roster.players:
        if not player.photo_url and player.id in found:
            player.photo_url = found[player.id].photo_url
            filled += 1
    return filled
