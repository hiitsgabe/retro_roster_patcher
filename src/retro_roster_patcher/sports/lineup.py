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
    shape; `code_line` gives a code's line when no formation is at hand.

Pure rules and tables, with no I/O and no import of a client, so any client can use them.
"""

from __future__ import annotations

import re
from collections import Counter

#: How many of a player's own matchday squads count toward his starts.
SQUAD_WINDOW = 5
#: How many of a team's matches count toward its shape.
SHAPE_WINDOW = 5

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
