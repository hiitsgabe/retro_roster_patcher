"""Read a real formation as three lines, and each of its places as a role, a side and a line.

ESPN gives every match one formation ("4-2-3-1") and, per starter, a position code. The retro
games draw three lines, and the codes are not roles: a code is the label of one place in one
formation, so `LM` is a holding midfielder in a 4-2-3-1, a side central midfielder in a 4-3-3,
a wide midfielder in a 4-4-2 and a wing-back in a 3-4-2-1.

  * `fold_formation` cuts a formation to three lines, and `choose_shape` takes the shape a team
    plays most;
  * `slot_role` reads a code inside its formation as a role in FotMob's vocabulary (GK RB CB LB
    RWB LWB CDM CM CAM RM LM RW LW ST), and `slot_side` gives its side across the pitch;
  * `slot_lines` puts each place of a template on a line, so the lines add up to the folded
    shape; `code_line` gives a code's line when no formation is at hand;
  * `pick_lineup` chooses a club's eleven from its matchday squads and seats each one in a
    place of its usual formation.

Pure rules and tables, with no I/O and no import of a client, so any client can use them.
"""

from __future__ import annotations

import re
from collections import Counter
from itertools import permutations
from typing import Any, NamedTuple

#: How many of a player's own matchday squads count toward his starts.
SQUAD_WINDOW = 5
#: How many of a team's matches count toward its shape.
SHAPE_WINDOW = 5
#: A role with at most this many seats has its players seated by trying every order. A code never
#: seen reads as CM, so a role can have ten seats, and every order of ten is too many to try.
EXACT_SEATS = 4

#: A code's role when its formation does not read it differently.
ROLE_BY_CODE = {
    "G": "GK", "CD": "CB", "CD-L": "CB", "CD-R": "CB", "SW": "CB", "LB": "LB", "RB": "RB",
    "LWB": "LWB", "RWB": "RWB", "DM": "CDM", "CM": "CM", "CM-L": "CM", "CM-R": "CM",
    "LM": "LM", "RM": "RM", "AM": "CAM", "AM-L": "LW", "AM-R": "RW", "F": "ST", "CF": "ST",
    "CF-L": "ST", "CF-R": "ST", "LCF": "ST", "RCF": "ST", "LW": "LW", "LF": "LW", "RW": "RW",
    "RF": "RW",
}  # fmt: skip
#: Codes that mean something else inside one formation. ESPN labels the places 1-11 of a 4-2-3-1
#: `G RB LB LM CD-R CD-L AM-R RM F AM AM-L`, so there `LM` and `RM` are the two holding
#: midfielders. A formation not listed here reads every code by `ROLE_BY_CODE`.
ROLE_OVERRIDES = {
    "4-2-3-1": {"LM": "CDM", "RM": "CDM"},
    "4-3-3": {"CM": "CDM", "LM": "CM", "RM": "CM"},
    "3-4-2-1": {"LM": "LWB", "RM": "RWB", "CF-L": "LW", "CF-R": "RW"},
    "3-4-3": {"LM": "LWB", "RM": "RWB", "CF-L": "LW", "CF-R": "RW"},
    "4-3-2-1": {"CF-L": "LW", "CF-R": "RW"},
    "4-1-2-1-2": {"LM": "CM", "RM": "CM"},
}
#: How far up the pitch a role plays. Places are cut into lines in this order, and a role on its
#: own falls on the line of its depth: 1-2 defence, 3-5 midfield, 6-7 attack.
DEPTH = {"GK": 0, "CB": 1, "RB": 1, "LB": 1, "RWB": 2, "LWB": 2, "CDM": 3, "CM": 4, "RM": 4,
         "LM": 4, "CAM": 5, "RW": 6, "LW": 6, "ST": 7}  # fmt: skip
#: The squad group (ESPN's roster grouping) of the players who belong on each line.
LINE_GROUP = {"GK": "Goalkeeper", "DF": "Defender", "MF": "Midfielder", "FW": "Attacker"}


def _counts(text: str) -> list[int]:
    """The players per line of "4-2-3-1": three lines or more, ten outfield players. Else `[]`."""
    if not re.fullmatch(r"\d+(?:-\d+){2,}", text):
        return []
    counts = [int(n) for n in text.split("-")]
    return counts if sum(counts) == 10 else []


def _line_of(role: str) -> str:
    """The line a role sits on by its depth alone."""
    depth = DEPTH[role]
    if depth == 0:
        return "GK"
    if depth <= 2:
        return "DF"
    return "MF" if depth <= 5 else "FW"


def fold_formation(text: str) -> str:
    """`text` cut to three lines: the back line, one midfield and the front line.

    Everything between the first and the last line is midfield, except that a lone striker is
    joined by the line right behind him: one or two players there all join the attack, three
    send their two wide ones, four or more stay. Text that is not a formation (at least three
    lines of ten outfield players) comes back unchanged, and so does a three-line formation.
    """
    counts = _counts(text)
    if len(counts) < 4:
        return text
    back, *middle, front = counts
    if front == 1:
        behind = middle[-1]
        if behind <= 2:
            front += behind
            middle.pop()
        elif behind == 3:
            front += 2
            middle[-1] = 1
    return f"{back}-{sum(middle)}-{front}"


def choose_shape(formations: list[str]) -> str | None:
    """The shape a team plays most, from its formations, newest first.

    Each formation is folded and the ones that are not three lines of ten are skipped. The first
    `SHAPE_WINDOW` left are counted, and a tie goes to the shape seen first, the most recent.
    `None` when no formation is usable.
    """
    shapes = [s for s in map(fold_formation, formations) if len(_counts(s)) == 3]
    return Counter(shapes[:SHAPE_WINDOW]).most_common(1)[0][0] if shapes else None


def slot_role(formation: str, code: str) -> str:
    """The role of the place ESPN labels `code` in `formation`.

    The formation's own reading comes first, then the code's, then a central midfielder for a
    code never seen.
    """
    return ROLE_OVERRIDES.get(formation, {}).get(code) or ROLE_BY_CODE.get(code, "CM")


def slot_side(code: str) -> str:
    """`"L"`, `"C"` or `"R"`: the side of the pitch the code's place is on."""
    if code.startswith("L") or code.endswith("-L"):
        return "L"
    if code.startswith("R") or code.endswith("-R"):
        return "R"
    return "C"


def slot_lines(shape: str, roles: list[str]) -> list[str]:
    """The line (`GK`, `DF`, `MF` or `FW`) of each place, for `roles` in place order.

    The keeper is `GK`. The other places are put in order from the back, by depth and then by
    place, and cut into the shape's counts, so the lines always add up to the shape. When the
    shape is not three counts that fill the outfield places, each place falls on the line of its
    own depth instead.
    """
    outfield = [i for i, role in enumerate(roles) if role != "GK"]
    outfield.sort(key=lambda i: (DEPTH[roles[i]], i))
    counts = _counts(shape)
    if len(counts) == 3 and sum(counts) == len(outfield):
        names = ["DF"] * counts[0] + ["MF"] * counts[1] + ["FW"] * counts[2]
    else:
        names = [_line_of(roles[i]) for i in outfield]
    placed = dict(zip(outfield, names, strict=True))
    return [placed.get(i, "GK") for i in range(len(roles))]


def code_line(code: str) -> str | None:
    """The line an ESPN code sits on by itself; `None` for one with no role, such as `SUB`."""
    role = ROLE_BY_CODE.get(code)
    return _line_of(role) if role else None


class _Seat(NamedTuple):
    """One place of the template match: its ESPN code, and the role, side and line it asks for."""

    place: int
    code: str
    role: str
    side: str
    line: str


def _formation(match: dict[str, Any]) -> str:
    """A match's formation, `""` when it has none."""
    return match.get("formation") or ""


def _usable(match: dict[str, Any]) -> bool:
    """A match to learn from: a formation `choose_shape` accepts and all eleven starters."""
    started = sum(1 for row in match["roster"] if row[1])
    return started == 11 and choose_shape([_formation(match)]) is not None


def _seats(template: dict[str, Any], shape: str) -> list[_Seat]:
    """The places of the template match in place order, each read as a role, a side and a line."""
    starters = sorted((row for row in template["roster"] if row[1]), key=lambda row: row[3])
    roles = [slot_role(_formation(template), row[2]) for row in starters]
    lines = slot_lines(shape, roles)
    return [
        _Seat(row[3], row[2], role, slot_side(row[2]), line)
        for row, role, line in zip(starters, roles, lines, strict=True)
    ]


def _propose(
    cap: Counter[str],
    asks: dict[int, list[str]],
    recent: dict[int, int],
    season: dict[int, int],
    latest: dict[int, int],
) -> dict[str, list[int]]:
    """Deferred acceptance of players into roles: the players each role holds, best first.

    `cap` is the seats of each role and `asks` the roles each player asks for, best first (his
    FotMob positions, most natural first, or the roles he started in, latest start first). A
    player asks for his roles in turn, and a role keeps its best up to its seats: the player in
    form (`recent` starts), then the one the role suits better (earlier in his list), then the
    one with more `season` starts, then the one whose latest start is more recent (`latest`, its
    index in the matches), then the lower id. A player turned away asks for the next role on his
    list, and one turned away from all of them is held by none.
    """

    def rank(p: int, role: str) -> tuple[int, int, int, int, int]:
        return (-recent[p], asks[p].index(role), -season[p], latest[p], p)

    held: dict[str, list[int]] = {role: [] for role in cap}
    nxt = dict.fromkeys(asks, 0)
    free = [p for p in asks if asks[p]]
    while free:
        p = free.pop()
        if nxt[p] == len(asks[p]):
            continue
        role = asks[p][nxt[p]]
        nxt[p] += 1
        held[role].append(p)
        held[role].sort(key=lambda q: rank(q, role))
        if len(held[role]) > cap[role]:
            free.append(held[role].pop())
    return held


def _sit(
    idx: list[int], players: list[int], seats: list[_Seat], at_code: Counter[tuple[int, str]]
) -> dict[int, int]:
    """Seat index -> player, for the `players` one role holds (best first) in its seats `idx`.

    The players sit where their starts at each seat's code (`at_code`) add up most, and a tie
    keeps the first order tried. Every order is tried for up to `EXACT_SEATS` seats. A role with
    more takes the seats one by one in place order, each taking the held player with the most
    starts at its code, a tie going to the better ranked; the seats left over stay empty.
    """
    if len(idx) <= EXACT_SEATS:
        order = max(
            permutations(idx, len(players)),
            key=lambda o: sum(at_code[p, seats[i].code] for p, i in zip(players, o, strict=True)),
        )
        return dict(zip(order, players, strict=True))
    free, sat = list(players), {}
    for i in idx:
        if free:
            sat[i] = max(free, key=lambda p: at_code[p, seats[i].code])
            free.remove(sat[i])
    return sat


def pick_lineup(
    matches: list[dict[str, Any]],
    squad: dict[int, str] | None,
    positions: dict[int, list[str]],
) -> dict[str, Any] | None:
    """The club's starting eleven, each in a place of its usual formation, or `None`.

    `matches` are the club's matches, newest first, each `{"event", "formation", "roster":
    [[athlete id, started, ESPN code, place, jersey], ...]}` with the bench as code `SUB`, place 0.
    `squad` maps the ids still at the club to their group (`Goalkeeper`, `Defender`,
    `Midfielder`, `Attacker` or `""`); `None` or `{}` is unknown, so nobody is left out.
    `positions` maps an id to his FotMob positions, most natural first; `{}` is none known.

    A match is usable when `choose_shape` accepts its formation and eleven players started. The
    shape is the one the club plays most (`choose_shape`), the template is its latest usable
    match in that shape, and the template's eleven places are the seats.

    A player is in form by his starts among his own last `SQUAD_WINDOW` matchday squads, so a
    spell out of the squad (hurt, suspended, not yet signed) never counts against him; a tie goes
    to his season's starts, then to the more recent start. With `positions`, players ask for the
    seats of their roles (`_propose`), FotMob's or, for a player it has none for or none the
    formation has, the roles he started in, the one of his latest start first; the players a
    role holds sit on the sides they started on in the template's formation (`_sit`). Without,
    every seat first goes to the best player who started at its code. A seat still empty takes
    the best player who started on its line, else one of its squad group, else anyone, so there
    are eleven unless the squad has fewer.

    Plain data for the JSON boundary: `{"formation", "shape", "event", "starters": [{"id",
    "position", "number", "place", "line", "role", "side"}, ...]}`, starters by place.
    """
    usable = [m for m in matches if _usable(m)]
    shape = choose_shape([_formation(m) for m in usable])
    if shape is None:
        return None
    template = next(m for m in usable if fold_formation(_formation(m)) == shape)
    seats = _seats(template, shape)

    rows: dict[int, list[list[Any]]] = {}  # each player's roster rows, newest first, in any match
    played: dict[int, list[str]] = {}  # the role of each place he started at, newest first
    newest: dict[int, int] = {}  # the index in `matches` of his latest start
    for k, match in enumerate(matches):
        for row in match["roster"]:
            rows.setdefault(row[0], []).append(row)
            if row[1]:
                played.setdefault(row[0], []).append(slot_role(_formation(match), row[2]))
                newest.setdefault(row[0], k)
    squad = squad or {}
    eligible = [p for p in rows if p in squad or not squad]
    recent = {p: sum(1 for r in rows[p][:SQUAD_WINDOW] if r[1]) for p in eligible}
    season = {p: sum(1 for r in rows[p] if r[1]) for p in eligible}
    latest = {p: newest.get(p, len(matches)) for p in eligible}
    number = {
        p: next((int(r[4]) for r in rows[p] if str(r[4]).isdecimal()), None) for p in eligible
    }
    started = {p: {r[2] for r in rows[p] if r[1]} for p in eligible}  # the codes he started at
    # Best first, so is every pool below.
    eligible.sort(key=lambda p: (-recent[p], -season[p], latest[p], p))

    placed: dict[int, int] = {}  # seat index -> player id
    if positions:
        cap = Counter(seat.role for seat in seats)
        asks: dict[int, list[str]] = {}
        for p in eligible:
            # A player FotMob has no positions for, or none the formation has, asks for the roles
            # he started in, the one of his latest start first and each role once (`played` is
            # newest first).
            known = [r for r in positions.get(p, []) if r in cap]
            asks[p] = known or [r for r in dict.fromkeys(played.get(p, [])) if r in cap]
        at_code = Counter(  # starts of a player at a code, in the template's formation
            (row[0], row[2])
            for m in matches
            if _formation(m) == _formation(template)
            for row in m["roster"]
            if row[1]
        )
        for role, players in _propose(cap, asks, recent, season, latest).items():
            role_seats = [i for i, seat in enumerate(seats) if seat.role == role]
            placed.update(_sit(role_seats, players, seats, at_code))

    left = [p for p in eligible if p not in placed.values()]

    def give(i: int, pool: list[int]) -> None:
        """Seat `i` takes the best of `pool`, if it has anyone."""
        if pool:
            placed[i] = pool[0]
            left.remove(pool[0])

    if not positions:  # first every seat takes who started at its own code, in place order
        for i, seat in enumerate(seats):
            give(i, [p for p in left if seat.code in started[p]])
    for i, seat in enumerate(seats):  # then a seat still empty takes the nearest player
        if i not in placed:
            pools = (
                [p for p in left if any(code_line(c) == seat.line for c in started[p])],
                [p for p in left if squad.get(p) == LINE_GROUP[seat.line]],
                left,
            )
            give(i, next((pool for pool in pools if pool), []))

    return {
        "formation": _formation(template),
        "shape": shape,
        "event": template["event"],
        "starters": [
            {
                "id": placed[i],
                "position": seat.code,
                "number": number[placed[i]],
                "place": seat.place,
                "line": seat.line,
                "role": seat.role,
                "side": seat.side,
            }
            for i, seat in enumerate(seats)
            if i in placed
        ],
    }
