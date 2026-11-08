"""Scenario mode: matches already under way, each a pair of slots and a blurb.

ISS's nine: the pairs at $81:9CAE (`PAIRS`), two slot ids doubled each, in
`TEAM_ENUM_ORDER`; the setup records behind them (score, time, who is sent off)
at $81:9CD2 belong to the scenario, not the teams. Each blurb is ASCII behind
description entries 27-35 (`FIRST_TEXT`), 23 characters a line, 0xFD a line
break and 0xFF the end. The title line is drawn from the team-name texts, so
it follows the slots by itself. ISS Deluxe's twelve (`iss_deluxe_snes.scenarios`)
pair their slots the same way, `repoint`.
"""

from __future__ import annotations

import textwrap
from collections import Counter
from collections.abc import Collection, Sequence

PAIRS = 0x9CAE
COUNT = 9
#: The description entry of scenario 1's blurb; scenario k's is `FIRST_TEXT + k`.
FIRST_TEXT = 27
LINE = 23


def repoint(
    pairs: Sequence[tuple[int, int]], mapped: Collection[int], blank: Collection[int] = ()
) -> list[tuple[int, int]]:
    """Each scenario's pair: its own while both slots are `mapped`, else the pair of mapped
    slots no scenario plays whose teams play least so far (ties in slot order). With none left
    it keeps its own, the cartridge's teams, unless that touches a `blank` slot: then it plays
    the least used pair again. Fewer than two mapped slots leave every pair."""
    order = sorted(set(mapped))
    every = [(a, b) for i, a in enumerate(order) for b in order[i + 1 :]]
    out: list[tuple[int, int] | None] = [
        (a, b) if a in mapped and b in mapped else None for a, b in pairs
    ]
    for k, pair in enumerate(out):
        if pair is not None:
            continue
        taken = [p for p in out if p]
        played = {frozenset(p) for p in taken}
        uses = Counter(s for p in taken for s in p)
        free = [p for p in every if frozenset(p) not in played]
        if not free and set(pairs[k]) & set(blank):
            free = every
        # ponytail: picks over every pair each time, O(n^2) per scenario; 36 slots at most
        out[k] = min(free, key=lambda p: uses[p[0]] + uses[p[1]], default=pairs[k])
    return [p for p in out if p]


def blurb(a: str, b: str, length: int) -> bytes:
    """A blurb for `a` against `b` in `length` bytes: 23-character lines, padded with spaces."""
    # The font has no "&", and its apostrophe is the backtick ("hasn`t").
    a, b = (name.replace("&", "and").replace("'", "`") for name in (a, b))
    texts = (
        f"{a} take on {b} with everything to play for. Can you lead them to the win?",
        f"{a} take on {b}. Can you win it?",
        f"{a} v {b}",
    )
    for text in texts:
        lines = textwrap.wrap(text, LINE)
        body = "".join(line.ljust(LINE) for line in lines[:-1]) + lines[-1]
        if len(body) <= length:
            break
    return body[:length].ljust(length).encode("ascii", errors="replace")
