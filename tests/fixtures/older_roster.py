"""A club's roster as an older rosters file has it, for the soccer games' tests.

Fetched before roles and formations were kept: no role on any player and no
`extra["formation"]`, only `extra["lineup"]` (what `EspnClient.get_lineup` returned), with its shape
and ESPN's code for each starter. Every game reads such a roster's formation from the lineup's
shape and a player's role from his code (`sports.lineup.team_formation`, `lineup_role`).
"""

from __future__ import annotations

from retro_roster_patcher.sports.models import Player, Team, TeamRoster

#: The places of a 4-3-3 as ESPN labels them (one to eleven), and the role each reads as there.
CODES_433 = "G RB LB CM CD-R CD-L RM LM F RF LF".split()
ROLES_433 = "GK RB LB CDM CB CB CM CM ST RW LW".split()
GROUP = {
    "GK": "Goalkeeper",
    **dict.fromkeys(("CB", "RB", "LB", "RWB", "LWB"), "Defender"),
    **dict.fromkeys(("CDM", "CM", "CAM", "RM", "LM"), "Midfielder"),
    **dict.fromkeys(("ST", "RW", "LW"), "Attacker"),
}
#: The bench: a keeper, a defender, a midfielder and a forward.
BENCH = ("GK", "CB", "CM", "ST")


def older_roster(
    codes: list[str] | None = None,
    roles: list[str] | None = None,
    *,
    formation: str = "4-3-3",
    shape: str | None = "4-3-3",
    bench: tuple[str, ...] = BENCH,
    team_id: int = 1,
) -> TeamRoster:
    """A club whose player i (from 1) started at `codes[i - 1]` (by default a 4-3-3's places), in
    the squad group of `roles[i - 1]`, named by that role and his id: `RB2`, `LB3`, ... Then one
    bench player for each of `bench`, named the same way. No player has a role. The lineup is of
    `formation` and holds `shape` (none when `shape` is None), and the roster has no
    `extra["formation"]`."""
    codes = codes or CODES_433
    roles = roles or ROLES_433
    players = [
        Player(id=i, name=f"{role}{i}", position=GROUP[role])
        for i, role in enumerate([*roles, *bench], start=1)
    ]
    lineup: dict = {
        "formation": formation,
        "event": 1,
        "starters": [{"id": i, "position": code} for i, code in enumerate(codes, start=1)],
    }
    if shape:
        lineup["shape"] = shape
    return TeamRoster(
        team=Team(id=team_id, name="Older FC", code="OLD"),
        players=players,
        extra={"lineup": lineup},
    )
