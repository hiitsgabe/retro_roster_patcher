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


PAD = 4


class Space:
    """Hands out word-aligned pieces of `FREE`, first fit, and of what
    `release` gives back."""

    def __init__(self) -> None:
        self._holes = [list(hole) for hole in FREE]

    def release(self, start: int, size: int) -> None:
        """Make `size` bytes at `start`, no longer read by anything, free,
        joined to any free run it touches or is `PAD` bytes from: the
        cartridge word-aligns its frames, so a freed frame and the next are a
        filler byte or two apart."""
        begin, end = (start + 1) & ~1, start + size
        if end - begin < 2:
            return
        for hole in self._holes:
            if hole[0] <= end + PAD and begin <= hole[1] + PAD:
                begin, end = min(begin, hole[0]), max(end, hole[1])
        self._holes = [h for h in self._holes if not (h[0] <= end and begin <= h[1])]
        self._holes.append([begin, end])
        self._holes.sort()

    def alloc(self, size: int) -> int | None:
        """The start of `size` free bytes, or None when no run has them."""
        for hole in self._holes:
            if hole[1] - hole[0] >= size:
                start = hole[0]
                hole[0] += (size + 1) & ~1
                return start
        return None
