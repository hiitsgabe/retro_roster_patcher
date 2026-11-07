"""Shirt numbers a game's squad table can hold, shared by the game writers."""

from __future__ import annotations

from collections.abc import Iterable


def shirt_numbers(wanted: Iterable[int], top: int) -> list[int]:
    """Different shirt numbers in 1..`top`, one for each of `wanted`: a squad in order, with the
    number the provider gives each player (0 for none).

    A player keeps the number he wants when it is in range and no player before him has it. The
    rest take the lowest free numbers, in order, once every keeper has his: a player with no
    number never takes one that a later player wants. More players than numbers is a `ValueError`.
    """
    wanted = list(wanted)
    if len(wanted) > top:
        raise ValueError(f"{len(wanted)} players cannot all wear a different number in 1-{top}")
    kept: list[int] = []
    taken: set[int] = set()
    for number in wanted:
        if 1 <= number <= top and number not in taken:
            taken.add(number)
            kept.append(number)
        else:
            kept.append(0)
    free = (n for n in range(1, top + 1) if n not in taken)
    return [number or next(free) for number in kept]
