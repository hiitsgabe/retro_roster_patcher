"""The cartridge's free space: runs of 0xFF filler no code or data reads."""

from __future__ import annotations

#: (start, end) of each run, start even.
FREE = (
    (0x1F92C0, 0x200000),
    (0x1660C2, 0x168000),
    (0x1967D4, 0x198000),
    (0x1C6A02, 0x1C8000),
    (0x1D6A16, 0x1D8000),
    (0x15EE38, 0x160000),
)


class Space:
    """Hands out word-aligned pieces of `FREE`, first fit, and of what
    `release` gives back."""

    def __init__(self) -> None:
        self._holes = [list(hole) for hole in FREE]

    def release(self, start: int, size: int) -> None:
        """Make `size` bytes at `start`, no longer read by anything, free."""
        begin = (start + 1) & ~1
        if start + size - begin >= 2:
            self._holes.append([begin, start + size])

    def alloc(self, size: int) -> int | None:
        """The start of `size` free bytes, or None when no run has them."""
        for hole in self._holes:
            if hole[1] - hole[0] >= size:
                start = hole[0]
                hole[0] += (size + 1) & ~1
                return start
        return None
