"""Konami's LZ format as ISS decodes it at $80:A530: `decompress` and an optimal `compress`.

A stream is a little-endian u16 header -- the stream's whole length, header
included; bit 15 is a flag the caller at $80:A27D handles, not part of the
length -- then commands, each a control byte `c`:

  00-7F  copy `(c >> 2) + 2` bytes (2..33) from ring position
         `(((c & 3) << 8) | next) - 0x3DF`, of a 1 KiB ring that starts zeroed
         and that the output fills from position 0
  80-9F  `c & 0x1F` literal bytes (0x80 alone means 256)
  A0-BF  `(c & 0x1F) + 2` pairs of a zero and the next literal byte
  C0-DF  `(c & 0x1F) + 2` copies of the next byte
  E0-FE  `(c & 0x1F) + 2` zero bytes
  FF     `next + 2` zero bytes (2..257)

`compress` finds the shortest stream over every one of those commands. On the
38 flag halves the cartridge ships it is never longer than Konami's own encoder
(1002 bytes against 1022 in all).
"""

from __future__ import annotations

from functools import lru_cache

_RING = 0x400
_BIAS = 0x3DF
_MAX_COPY = 33
#: Longest input `compress` takes: the output must never wrap the ring onto the
#: still-zero tail its copies may read from.
MAX_RAW = _RING - 64


def decompress(data: bytes, offset: int = 0) -> bytes:
    """The bytes the stream at `data[offset:]` decodes to."""
    end = (data[offset] | data[offset + 1] << 8) & 0x7FFF
    ring = bytearray(_RING)
    out = bytearray()
    y = offset + 2
    end += offset

    def put(value: int) -> None:
        ring[len(out) % _RING] = value
        out.append(value)

    while y < end:
        c = data[y]
        y += 1
        if c < 0x80:
            src = ((((c & 3) << 8) | data[y]) - _BIAS) & (_RING - 1)
            y += 1
            for _ in range((c >> 2) + 2):
                put(ring[src])
                src = (src + 1) & (_RING - 1)
        elif c < 0xA0:
            for _ in range(c & 0x1F or 256):
                put(data[y])
                y += 1
        elif c < 0xC0:
            for _ in range((c & 0x1F) + 2):
                put(0)
                put(data[y])
                y += 1
        elif c < 0xE0:
            for _ in range((c & 0x1F) + 2):
                put(data[y])
            y += 1
        else:
            count = (c & 0x1F) + 2 if c < 0xFF else data[y] + 2
            y += c == 0xFF
            for _ in range(count):
                put(0)
    return bytes(out)


@lru_cache(maxsize=512)
def compress(raw: bytes) -> bytes:
    """The shortest stream that decodes to `raw` (at most `MAX_RAW` bytes).

    Dynamic programming from the end: `cost[i]` is the fewest bytes that encode
    `raw[i:]`. Copies may read any byte already written, overlapping the bytes
    they write, or the ring's still-zero tail, whose copies run on into
    position 0 -- a trick Konami's own encoder uses.
    """
    n = len(raw)
    if n > MAX_RAW:
        raise ValueError(f"{n} bytes is more than one stream can carry ({MAX_RAW})")
    inf = 1 << 30
    cost = [inf] * (n + 1)
    pick: list[tuple[int, bytes]] = [(0, b"")] * (n + 1)
    cost[n] = 0

    def ring_at(q: int, written: int) -> int:
        """The ring byte at `q` once `written` bytes are out (zero until written)."""
        q %= _RING
        return raw[q] if q < written else 0

    def offer(i: int, length: int, op: bytes) -> None:
        if cost[i + length] + len(op) < cost[i]:
            cost[i] = cost[i + length] + len(op)
            pick[i] = (length, op)

    for i in range(n - 1, -1, -1):
        for k in range(1, min(31, n - i) + 1):
            offer(i, k, bytes([0x80 | k]) + raw[i : i + k])
        run = 1
        while i + run < n and raw[i + run] == raw[i] and run < 257:
            run += 1
        for length in range(2, run + 1):
            if raw[i] == 0:
                offer(
                    i, length, bytes([0xE0 | (length - 2)] if length <= 32 else [0xFF, length - 2])
                )
            elif length <= _MAX_COPY:
                offer(i, length, bytes([0xC0 | (length - 2), raw[i]]))
        pairs = 0
        while i + 2 * pairs + 1 < n and raw[i + 2 * pairs] == 0 and pairs < _MAX_COPY:
            pairs += 1
            if pairs >= 2:
                offer(i, 2 * pairs, bytes([0xA0 | (pairs - 2)]) + raw[i + 1 : i + 2 * pairs : 2])
        # The longest copy from each source; every shorter one is its prefix.
        sources = range(i) if raw[i] else [*range(i), *range(_RING - _MAX_COPY, _RING)]
        best: dict[int, int] = {}
        for p in sources:
            if ring_at(p, i) != raw[i]:
                continue
            length = 1
            while (
                length < _MAX_COPY
                and i + length < n
                and ring_at(p + length, i + length) == raw[i + length]
            ):
                length += 1
            for size in range(2, length + 1):
                best.setdefault(size, p)
        for size, p in best.items():
            r10 = (p + _BIAS) & (_RING - 1)
            offer(i, size, bytes([((size - 2) << 2) | (r10 >> 8), r10 & 0xFF]))

    ops = bytearray()
    i = 0
    while i < n:
        length, op = pick[i]
        ops += op
        i += length
    total = len(ops) + 2
    return bytes([total & 0xFF, total >> 8]) + bytes(ops)


def compress_large(raw: bytes) -> bytes:
    """The shortest stream for `raw` of any length the 15-bit header allows.

    `compress` for screen art: the same commands, with copies read from the
    ring as it stands -- the last 1 KiB written, or its still-zero tail early
    on. A copy costs two bytes whatever its length, so the longest one at each
    position (found through chains of byte pairs) stands for all its prefixes
    and the parse stays optimal.

    Raises:
        ValueError: the stream would not fit the header's 15 bits.
    """
    n = len(raw)
    # The ring's zeros, then the output: index `h` is ring position `h % _RING`.
    hist = bytes(_RING) + raw
    chains: dict[bytes, list[int]] = {}
    for s in range(_RING):
        chains.setdefault(hist[s : s + 2], []).append(s)
    longest = [(0, 0)] * n
    for pos in range(n):
        h = _RING + pos
        best, source = 0, 0
        if pos + 1 < n:
            for s in reversed(chains.get(hist[h : h + 2], ())):
                if h - s > _RING:
                    break
                length = 2
                while (
                    length < _MAX_COPY
                    and pos + length < n
                    and hist[s + length] == raw[pos + length]
                ):
                    length += 1
                if length > best:
                    best, source = length, s
                    if length == _MAX_COPY:
                        break
        longest[pos] = (best, source)
        chains.setdefault(hist[h : h + 2], []).append(h)

    inf = 1 << 30
    cost = [inf] * (n + 1)
    step: list[tuple[int, bytes]] = [(0, b"")] * (n + 1)
    cost[0] = 0

    def relax(i: int, length: int, op: bytes) -> None:
        if cost[i] + len(op) < cost[i + length]:
            cost[i + length] = cost[i] + len(op)
            step[i + length] = (length, op)

    for i in range(n):
        for k in range(1, min(31, n - i) + 1):
            relax(i, k, bytes([0x80 | k]) + raw[i : i + k])
        best, source = longest[i]
        r10 = (source + _BIAS) & (_RING - 1)
        for length in range(2, best + 1):
            relax(i, length, bytes([((length - 2) << 2) | (r10 >> 8), r10 & 0xFF]))
        run = 1
        while i + run < n and raw[i + run] == raw[i] and run < 257:
            run += 1
        for length in range(2, run + 1):
            if raw[i] == 0:
                relax(
                    i, length, bytes([0xE0 | (length - 2)] if length <= 32 else [0xFF, length - 2])
                )
            elif length <= _MAX_COPY:
                relax(i, length, bytes([0xC0 | (length - 2), raw[i]]))
        pairs = 0
        while i + 2 * pairs + 1 < n and raw[i + 2 * pairs] == 0 and pairs < _MAX_COPY:
            pairs += 1
            if pairs >= 2:
                relax(i, 2 * pairs, bytes([0xA0 | (pairs - 2)]) + raw[i + 1 : i + 2 * pairs : 2])

    ops: list[bytes] = []
    j = n
    while j:
        length, op = step[j]
        ops.append(op)
        j -= length
    body = b"".join(reversed(ops))
    total = len(body) + 2
    if total > 0x7FFF:
        raise ValueError(f"{total} bytes is more than a stream's 15-bit header can say")
    return bytes([total & 0xFF, total >> 8]) + body
