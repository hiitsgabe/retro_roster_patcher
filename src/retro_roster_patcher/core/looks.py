"""What every game's `looks` module shares: a stable weighted pick, the
nationalities a game draws with its East Asian face, and the choice of a
team's two shared looks (`two_looks`)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from itertools import product

#: Nationalities as ESPN's `citizenship` and FotMob's `cname` spell them.
#: WE2002 and PES 2013 give these players their East Asian skin tone, which
#: is a face of its own on both discs (Japan, Korea and China are 100% of it)
#: and not a step on the light-to-dark ladder.
EAST_ASIAN = frozenset(
    {
        "Japan",
        "South Korea",
        "Korea Republic",
        "North Korea",
        "Korea DPR",
        "China",
        "China PR",
        "Chinese Taipei",
        "Taiwan",
        "Hong Kong",
        "Macau",
        "Mongolia",
    }
)


def pick(weights: Sequence[int], key: str) -> int:
    """A weighted choice among `range(len(weights))` that the same key always
    makes: the pick is the SHA-256 of the key against the running sum."""
    h = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") % sum(weights)
    for value, weight in enumerate(weights):
        if h < weight:
            return value
        h -= weight
    raise AssertionError("unreachable")


#: A player as a look chooser sees him: a skin step (an index into the game's
#: ladder) and a hair colour name (`core.hair`'s).
Person = tuple[int, str]

#: Skin outweighs hair: one step of the ladder costs three hair mismatches,
#: and the cost grows with the square of the steps. A photo measures the
#: ends of the ladder surely and its middle loosely (studio lighting evens
#: out brown and pale faces), so a face two steps off is a worse miss than
#: two faces one step off.
SKIN_WEIGHT = 3


def look_cost(person: Person, look: Person, position: Mapping[int, float] | None = None) -> float:
    """How badly `look` draws `person`. `position` places skins that are not
    a step of the ladder (an East Asian face) on it."""
    where = position or {}
    a, b = where.get(person[0], person[0]), where.get(look[0], look[0])
    return SKIN_WEIGHT * (a - b) ** 2 + (person[1] != look[1])


def two_looks(
    people: Sequence[Person],
    skins: Iterable[int],
    hairs: Iterable[str],
    alt_skins: Iterable[int],
    alt_hairs: Iterable[str],
    position: Mapping[int, float] | None = None,
) -> tuple[Person, Person | None]:
    """A team's own look and its alternate (None: one look for all), for
    games that draw a squad in two shared palettes (ISS, ISS Deluxe): the
    pair with the lowest total `look_cost`; of equal ones, without an
    alternate, then with fewer players in it. The alternate never takes more
    than half the players: ISS draws the captain in the team's own palette at
    the coin toss whatever his flag, so the own look is the majority's."""
    hairs, alt_skins, alt_hairs = list(hairs), list(alt_skins), list(alt_hairs)
    best: tuple[tuple[float, bool, int], Person, Person | None] | None = None
    for own in product(skins, hairs):
        base = [look_cost(p, own, position) for p in people]
        alternates: list[Person | None] = [None]
        alternates += [
            alt for alt in product((*alt_skins, own[0]), (*alt_hairs, own[1])) if alt != own
        ]
        for alt in alternates:
            if alt is None:
                key = (sum(base), False, 0)
            else:
                other = [look_cost(p, alt, position) for p in people]
                moved = sum(o < b for o, b in zip(other, base, strict=True))
                if 2 * moved > len(people):
                    continue
                key = (sum(map(min, base, other)), True, moved)
            if best is None or key < best[0]:
                best = (key, own, alt)
    assert best is not None
    return best[1], best[2]
