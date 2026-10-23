"""EA Huffman (`30 FB`) streams written from the format's description.

Four symbols with 2-bit codes, so `formats.ea_huff` is checked against an
independent encoder, not against itself.
"""

from __future__ import annotations


def huff_number(v: int) -> str:
    """The bits of `v` as the format's numbers: m zero bits, a one, then v in
    m+2 bits past (1 << m+2) - 4."""
    m = 0
    while v >= (1 << (m + 3)) - 4:
        m += 1
    return "0" * m + "1" + format(v - ((1 << (m + 2)) - 4), f"0{m + 2}b")


def huff(tokens: list[tuple[str, int]], size: int, symbols: bytes = b"\x11\x22\x41\x99") -> bytes:
    """A `30 FB` stream of `tokens`: ("byte", b), ("run", n), ("raw", b), ("end", 0).
    The last of the four `symbols` (ascending) is the clue."""
    code = {s: format(i, "02b") for i, s in enumerate(symbols)}
    bits = huff_number(0) + huff_number(4)  # no 1-bit codes, four 2-bit ones
    last = 0xFF
    for s in symbols:
        bits += huff_number((s - last) % 256 - 1)
        last = s
    clue = code[symbols[-1]]
    for kind, value in tokens:
        if kind == "byte":
            bits += code[value]
        elif kind == "run":
            bits += clue + huff_number(value)
        elif kind == "raw":
            bits += clue + huff_number(0) + "0" + format(value, "08b")
        else:
            bits += clue + huff_number(0) + "1"
    bits += "0" * (-len(bits) % 8)
    body = int(bits, 2).to_bytes(len(bits) // 8, "big")
    return b"\x30\xfb" + size.to_bytes(3, "big") + bytes([symbols[-1]]) + body
