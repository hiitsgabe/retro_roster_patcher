"""What every game's `looks` module shares: a stable weighted pick, and the
nationalities a game draws with its East Asian face."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

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
