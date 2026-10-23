"""EA's Huffman-with-runs compression (magic `30 FB`), decode only.

NHL '94 (SNES) keeps some of its art this way and decodes it at $80:BBB3.
Checked byte for byte against 29 streams the game decompressed in Mesen 2.

Layout: `30 FB`, the decoded size (3 bytes, big-endian), the clue byte, then
an MSB-first bit stream: the number of codes of each length, 1 bit up, until
the code space is full; each symbol as a skip over the byte values not yet
used; then the codes, canonical. The clue's code is followed by a number: a
run repeating the last byte, or 0 and one more bit, 1 to end the stream and 0
for a raw 8-bit byte (the clue's own value, say).
"""

from __future__ import annotations

from .ea_tdb import EaTdbError

MAGIC = b"\x30\xfb"


class _Bits:
    def __init__(self, data: bytes, at: int) -> None:
        self.data, self.at, self.acc, self.n = data, at, 0, 0

    def get(self, k: int) -> int:
        while self.n < k:
            byte = self.data[self.at] if self.at < len(self.data) else 0
            self.acc = (self.acc << 8 | byte) & 0xFFFFFFFF
            self.at += 1
            self.n += 8
        self.n -= k
        return self.acc >> self.n & ((1 << k) - 1)

    def number(self) -> int:
        """m zero bits, a one, then m+2 bits: 0-3, 4-11, 12-27, ..."""
        m = 0
        while not self.get(1):
            m += 1
            if m > 20:
                raise EaTdbError("Corrupt EA Huffman stream: a number runs on")
        return (1 << (m + 2)) - 4 + self.get(m + 2)


def decompress(data: bytes) -> bytes:
    """The bytes the `30 FB` stream at the start of `data` decodes to.

    Raises:
        EaTdbError: not a `30 FB` stream, or one that does not decode.
    """
    if len(data) < 6 or data[:2] != MAGIC:
        raise EaTdbError("Not EA Huffman data (missing 0x30 0xFB header)")
    size = int.from_bytes(data[2:5], "big")
    clue = data[5]
    bits = _Bits(data, 6)
    counts: list[int] = []
    code = 0
    while True:
        n = bits.number()
        counts.append(n)
        code = (code << 1) + n
        if n and code >= 1 << len(counts):
            break
        if len(counts) > 24:
            raise EaTdbError("Corrupt EA Huffman stream: no complete code")
    if sum(counts) > 256:  # the skip loop below would never find a free byte
        raise EaTdbError("Corrupt EA Huffman stream: more symbols than byte values")
    used = [False] * 256
    x = 0xFF
    symbols = []
    for _ in range(sum(counts)):
        skip = bits.number() + 1
        while skip:
            x = (x + 1) & 0xFF
            if not used[x]:
                skip -= 1
        used[x] = True
        symbols.append(x)
    table: dict[tuple[int, int], int] = {}
    code = k = 0
    for length, n in enumerate(counts, 1):
        for i in range(n):
            table[(length, code + i)] = symbols[k + i]
        code = (code + n) << 1
        k += n
    out = bytearray()
    last = 0
    while len(out) < size:
        length = code = 0
        while (length, code) not in table:
            code = code << 1 | bits.get(1)
            length += 1
            if length > 24:
                raise EaTdbError(f"Corrupt EA Huffman stream: no code at byte {len(out)}")
        symbol = table[(length, code)]
        if symbol != clue:
            out.append(symbol)
            last = symbol
            continue
        run = bits.number()
        if run:
            out += bytes([last]) * run
        elif bits.get(1):
            break
        else:
            last = bits.get(8)
            out.append(last)
    return bytes(out[:size])
