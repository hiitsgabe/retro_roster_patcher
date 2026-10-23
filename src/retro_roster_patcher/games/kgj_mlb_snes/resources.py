"""The cartridge's resource archive: a directory of 2608 entries and the codecs.

Every picture, palette and tile map the game loads goes through one loader
($80:A4AA). Entry `i` (the code passes Y = 2i) has three directory fields in
bank $86: the codec, a word at $86:8000+2i; the stream length, a word at
$86:BD20+2i; and the stream's offset, a dword at $86:9460+4i counted from file
0x78000 ($8F:8000), so the stream may sit anywhere from bank $8F to the end of
a 4 MiB image. Codecs: 0 bitmap byte-RLE, 2 escape byte-RLE, 4 the tile codec,
6 stored, 8 escape word-RLE, 10 a word LZ this module does not decode (25
entries, none of them art the library touches).

A writer stores its data as codec 6 and repoints the entry (`set_entry`): no
encoder, and the old stream stays where it was. Every function takes the image
without a copier header.

Decoded against Mesen dumps of the retail cartridge: all 30 team logos, the
menu palettes (entry 2296) and a uniform palette match byte for byte.
"""

from __future__ import annotations

import struct

_DIR = 0x30000  # $86:8000
_LENGTHS = _DIR + 0x3D20
_OFFSETS = _DIR + 0x1460
_BASE = 0x78000  # $8F:8000
COUNT = 2608
STORED = 6

# Bit-reversed bytes: the game's table at $80:A69E.
_FLIP = bytes(int(f"{b:08b}"[::-1], 2) for b in range(256))


def entry(rom: bytes | bytearray, i: int) -> tuple[int, int, int]:
    """(codec, stream length, file offset) of entry `i`."""
    (codec,) = struct.unpack_from("<H", rom, _DIR + 2 * i)
    (length,) = struct.unpack_from("<H", rom, _LENGTHS + 2 * i)
    (offset,) = struct.unpack_from("<I", rom, _OFFSETS + 4 * i)
    return codec, length, _BASE + offset


def set_entry(rom: bytearray, i: int, codec: int, length: int, offset: int) -> None:
    """Point entry `i` at `length` bytes of `codec` at file `offset`."""
    struct.pack_into("<H", rom, _DIR + 2 * i, codec)
    struct.pack_into("<H", rom, _LENGTHS + 2 * i, length)
    struct.pack_into("<I", rom, _OFFSETS + 4 * i, offset - _BASE)


def store(rom: bytearray, i: int, data: bytes, offset: int) -> int:
    """Write `data` at file `offset` as entry `i`'s stored stream; the offset
    just past it."""
    rom[offset : offset + len(data)] = data
    set_entry(rom, i, STORED, len(data), offset)
    return offset + len(data)


def _rotate(unit: bytes | bytearray) -> bytearray:
    """$80:966E: a 2bpp unit turned a quarter: row 7-k, bit 0x80>>r of the
    result is row r, bit 0x80>>k of the source, plane by plane."""
    out = bytearray(16)
    for r in range(8):
        for p in range(2):
            v = unit[2 * r + p]
            for k in range(8):
                if v & (0x80 >> k):
                    out[2 * (7 - k) + p] |= 0x80 >> r
    return out


def tiles(rom: bytes | bytearray, at: int, length: int) -> bytes:
    """Codec 4 ($80:9295): 16-byte units (a 2bpp tile, or half a 4bpp one), each
    a command byte and its data. 0x40: one plane of two colours, with masks of
    rows to clear (0x20) or set (0x10). 0x30 bits otherwise: decode the unit at
    a stream offset, then patch bytes (0x20), flip it vertically (1) or
    horizontally (2). Plain: masks of bytes that are 0 (4), 0xFF (2) or one
    value (1), then literals. 0x80 rotates the unit."""
    start, end, pos = at, at + length, at
    out = bytearray()

    def read() -> int:
        nonlocal pos
        pos += 1
        return rom[pos - 1]

    while pos < end:
        cmd = read()
        buf = bytearray(16)
        stack: list[tuple[int, int]] = []
        while True:
            if cmd & 0x40:
                plane = bytearray(8)
                fixed = 0
                if cmd & 0x20:
                    m = read()
                    fixed |= m
                if cmd & 0x10:
                    m = read()
                    fixed |= m
                    for x in range(8):
                        if m >> x & 1:
                            plane[x] = 0xFF
                for x in range(8):
                    if not fixed >> x & 1:
                        plane[x] = read()
                clear, colour = cmd & 3, cmd >> 2 & 3
                for y in range(8):
                    lo = hi = 0
                    for x in range(8):
                        c = colour if plane[y] >> x & 1 else clear
                        lo |= (c & 1) << x
                        hi |= (c >> 1 & 1) << x
                    buf[2 * y], buf[2 * y + 1] = lo, hi
                break
            if cmd & 0x30:
                ref = read() | read() << 8
                stack.append((cmd, pos))
                pos = start + ref
                cmd = read()
                continue
            zero = ff = fill = value = 0
            if cmd & 4:
                zero = read() | read() << 8
            if cmd & 2:
                ff = read() | read() << 8
            if cmd & 1:
                value = read()
                fill = read() | read() << 8
            for x in range(16):
                if zero >> x & 1:
                    buf[x] = 0
                elif ff >> x & 1:
                    buf[x] = 0xFF
                elif fill >> x & 1:
                    buf[x] = value
                else:
                    buf[x] = read()
            break
        if cmd & 0x80:
            buf = _rotate(buf)
        while stack:
            cmd, pos = stack.pop()
            if cmd & 0x20:
                m = read() | read() << 8
                for x in range(16):
                    if m >> x & 1:
                        buf[x] = read()
            if cmd & 1:
                buf = bytearray(b"".join(bytes(buf[2 * r : 2 * r + 2]) for r in range(7, -1, -1)))
            if cmd & 2:
                buf = bytearray(_FLIP[v] for v in buf)
            if cmd & 0x80:
                buf = _rotate(buf)
        out += buf
    return bytes(out)


def _word_rle(rom: bytes | bytearray, at: int, length: int) -> bytes:
    """Codec 8 ($9727): escape pair e1 e2; `e1 e2 lo hi n` is the word n times
    (0 = 256), other words are literal."""
    e1, e2 = rom[at], rom[at + 1]
    pos, end, out = at + 2, at + length, bytearray()
    while pos < end:
        if rom[pos] == e1 and rom[pos + 1] == e2:
            out += bytes(rom[pos + 2 : pos + 4]) * (rom[pos + 4] or 256)
            pos += 5
        else:
            out += rom[pos : pos + 2]
            pos += 2
    return bytes(out)


def _byte_rle(rom: bytes | bytearray, at: int, length: int) -> bytes:
    """Codec 2 ($91F6): escape byte e; `e v n` is v n times (0 = 256)."""
    e = rom[at]
    pos, end, out = at + 1, at + length, bytearray()
    while pos < end:
        if rom[pos] == e:
            out += bytes([rom[pos + 1]]) * (rom[pos + 2] or 256)
            pos += 3
        else:
            out.append(rom[pos])
            pos += 1
    return bytes(out)


def _set_rle(rom: bytes | bytearray, at: int, length: int) -> bytes:
    """Codec 0 ($915A): a 32-byte bitmap of run values; a run value is followed
    by its count (0 = 256)."""
    runs = rom[at : at + 32]
    pos, end, out = at + 32, at + length, bytearray()
    while pos < end:
        v = rom[pos]
        if runs[v >> 3] >> (v & 7) & 1:
            out += bytes([v]) * (rom[pos + 1] or 256)
            pos += 2
        else:
            out.append(v)
            pos += 1
    return bytes(out)


_CODECS = {0: _set_rle, 2: _byte_rle, 4: tiles, 8: _word_rle}


def load(rom: bytes | bytearray, i: int) -> bytes:
    """Entry `i` decoded, as the game loads it."""
    codec, length, at = entry(rom, i)
    if codec == STORED:
        return bytes(rom[at : at + length])
    if codec not in _CODECS:
        raise ValueError(f"resource {i} uses codec {codec}, which is not decoded")
    return _CODECS[codec](rom, at, length)
