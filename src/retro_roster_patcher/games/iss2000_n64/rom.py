"""International Superstar Soccer 2000 (N64, USA): the cartridge image.

Everything here was checked on the USA cartridge (`I.S.S.2000`, game code
NISE, SHA-1 e7bd36c4...) in the Mupen64Plus-Next core.

Layout:

* Header CRC (0x10, 0x14): the CIC-6102 checksum over ROM 0x1000-0x101000.
  None of the data this package writes lies in that range, but `fix_crc`
  runs on every patch anyway.
* Player table 0x2F2EE0: 106 teams x 22 players x 56 bytes, team-major. Teams
  0-99 are the national teams, 100-105 the all-star sides. It sits inside the
  raw overlay ROM 0x2E0D90-0x3166C0, which the game DMAs as it is, so it is
  edited in place.
* Formation rows 0x313FD4: per team 11 slots x 6 bytes (`>hhBB`: depth,
  width, line | flags, role), slot k is player record k. Line 0 keeper,
  1 defence, 2 midfield, 3 attack.
* Team metadata 0x315BF0: per team 6 bytes, five record indices 0-10 (captain
  and set-piece takers) and the formation id.
* Asset table 0x99D04 ("Nisitenma-Ichigo"): (start, end) word pairs, bit 31 of
  start = compressed. A compressed asset is either LZ (`lz_decompress`) or,
  when bit 31 of its first word is set, zlib.

A player record (offsets in the record):

    0x00-0x03 special-ability bit set (bits 0-22)
    0x0B      low 6 bits: shirt number
    0x11      high nibble hair colour, low nibble skin tone (0 light - 8 dark)
    0x14      face (portrait and head model)
    0x18-0x1F name, `encode_name`
    0x20-0x29 determination, shoot (goal kick for a keeper), curve, speed, dash,
              stamina, jump, keep, defence, passing
    0x2A      foot: right-footed players 70-76, left 14-29, both 38-63
    0x2B      goalkeeping
    0x2C      second keeper skill (keepers 74-96, others 10-22)
    0x2D      height - 100 (cm)
    0x2E      build: the game shows the weight from this and the height
    0x33      position bits: 1 GK, 2 DF, 4 MF, 8 FW (combinations allowed)
    0x34      team id, 0x35 index in the team
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from ...core.errors import RomError

ROM_SIZE = 0x1000000
GAME_CODE = b"NISE"

PLAYER_TABLE = 0x2F2EE0
RECORD = 56
PLAYERS_PER_TEAM = 22
TEAMS = 106
NATIONAL_TEAMS = 100
FORMATIONS = 0x313FD4
FORMATION_ROW = 66
METADATA = 0x315BF0
ASSET_TABLE = 0x99D04
ASSET_TABLE_END = 0x9B42C
#: Where a grown image keeps a copy of the first 0x100 bytes: the game compares
#: ROM 0x1000000 with its header to tell a development board from a cartridge
#: (on a 16 MiB cartridge that address mirrors offset 0).
MIRROR = 0x1000000
#: First byte a relocated asset may take in a grown image.
GROWTH_START = 0x1000100

# Record offsets.
NUMBER = 0x0B
LOOK = 0x11
FACE = 0x14
NAME = 0x18
NAME_LEN = 8
ABILITIES = 0x20
FOOT = 0x2A
GOALKEEPING = 0x2B
KEEPER2 = 0x2C
HEIGHT = 0x2D
BUILD = 0x2E
POSITION = 0x33
TEAM = 0x34
INDEX = 0x35

ABILITY_NAMES = (
    "determination",
    "shoot",
    "curve",
    "speed",
    "dash",
    "stamina",
    "jump",
    "keep",
    "defence",
    "passing",
)

GK, DF, MF, FW = 1, 2, 4, 8


def is_iss2000(image: bytes | bytearray) -> bool:
    return len(image) >= ROM_SIZE and image[0x3B:0x3F] == GAME_CODE


# --- header CRC ---------------------------------------------------------------


def _rol(v: int, n: int) -> int:
    n &= 31
    return ((v << n) | (v >> (32 - n))) & 0xFFFFFFFF


def crc(image: bytes | bytearray) -> tuple[int, int]:
    """The CIC-6102 header checksum."""
    m = 0xFFFFFFFF
    t1 = t2 = t3 = t4 = t5 = t6 = 0xF8CA4DDC
    for d in struct.unpack_from(">262144I", image, 0x1000):
        if (t6 + d) & m < t6:
            t4 = (t4 + 1) & m
        t6 = (t6 + d) & m
        t3 ^= d
        r = _rol(d, d & 0x1F)
        t5 = (t5 + r) & m
        t2 = t2 ^ r if t2 > d else t2 ^ t6 ^ d
        t1 = (t1 + (t5 ^ d)) & m
    return (t6 ^ t4 ^ t3) & m, (t5 ^ t2 ^ t1) & m


def fix_crc(image: bytearray) -> None:
    struct.pack_into(">II", image, 0x10, *crc(bytes(image)))


# --- LZ -------------------------------------------------------------------------


class AssetError(RomError):
    """A compressed asset that does not decode."""


def lz_decompress(buf: bytes | bytearray, at: int = 0) -> bytes:
    """Konami's N64 LZ: a big-endian word with the stream's length (header
    included), then commands until that length:

    0x00-0x7F  copy (c >> 2) + 2 bytes from ((c & 3) << 8 | next) back
    0x80-0x9F  c & 0x1F literal bytes
    0xA0-0xDF  the next byte (c & 0x1F) + 2 times
    0xE0-0xFE  (c & 0x1F) + 2 zeros
    0xFF       next + 2 zeros
    """
    head = struct.unpack_from(">I", buf, at)[0]
    if head & 0x80000000:
        d = zlib.decompressobj()
        unpacked = d.decompress(buf[at + 4 : at + (head & 0x7FFFFFFF)])
        if not d.eof:
            raise AssetError("truncated zlib stream")
        return unpacked
    end = at + head
    if head < 4 or end > len(buf):
        raise AssetError(f"bad LZ length {head:#x} at {at:#x}")
    ip = at + 4
    out = bytearray()
    while ip < end:
        c = buf[ip]
        ip += 1
        if c < 0x80:
            dist = ((c & 3) << 8) | buf[ip]
            ip += 1
            if not 0 < dist <= len(out):
                raise AssetError(f"LZ copy from {dist} back at output {len(out):#x}")
            for _ in range((c >> 2) + 2):
                out.append(out[-dist])
        elif c < 0xA0:
            n = c & 0x1F
            out += buf[ip : ip + n]
            ip += n
        elif c < 0xE0:
            out += bytes([buf[ip]]) * ((c & 0x1F) + 2)
            ip += 1
        elif c < 0xFF:
            out += bytes((c & 0x1F) + 2)
        else:
            out += bytes(buf[ip] + 2)
            ip += 1
    if ip != end:
        raise AssetError("LZ stream overran its length")
    return bytes(out)


def lz_compress(data: bytes) -> bytes:
    """The smallest stream `lz_decompress` reads back as `data`.

    An optimal parse: the cheapest way to reach each position from back
    copies (2-33 bytes, up to 1023 back, 2 bytes), zero runs (2-32 in 1 byte,
    up to 257 in 2), byte runs (2-33, 2 bytes) and literal groups (1-31, one
    byte more). It beats the cartridge's own streams, so a rewritten asset
    fits back where it was more often.
    """
    n = len(data)
    # Longest back copy at each position, and from where.
    match_len = [0] * n
    match_dist = [0] * n
    heads: dict[bytes, list[int]] = {}
    for i in range(n):
        best, dist = 0, 0
        if i + 2 <= n:
            chain = heads.get(data[i : i + 2])
            if chain:
                for p in reversed(chain):
                    d = i - p
                    if d > 1023:
                        break
                    m = 2
                    while m < 33 and i + m < n and data[p + m] == data[i + m]:
                        m += 1
                    if m > best:
                        best, dist = m, d
                        if m == 33:
                            break
            heads.setdefault(data[i : i + 2], []).append(i)
        match_len[i], match_dist[i] = best, dist
    # Run of equal bytes from each position.
    run = [1] * n
    for i in range(n - 2, -1, -1):
        if data[i] == data[i + 1]:
            run[i] = min(run[i + 1] + 1, 257)
    inf = float("inf")
    cost = [inf] * (n + 1)
    step: list[tuple[int, int]] = [(0, 0)] * (n + 1)  # (kind, length) into position
    cost[0] = 0
    for i in range(n):
        c = cost[i]
        if c == inf:
            continue
        for k in range(1, min(31, n - i) + 1):  # literals
            if c + k + 1 < cost[i + k]:
                cost[i + k], step[i + k] = c + k + 1, (0, k)
        for k in range(2, match_len[i] + 1):  # back copy
            if c + 2 < cost[i + k]:
                cost[i + k], step[i + k] = c + 2, (1, k)
        r = run[i]
        if r >= 2:
            if data[i] == 0:
                for k in range(2, r + 1):
                    w = 1 if k <= 32 else 2
                    if c + w < cost[i + k]:
                        cost[i + k], step[i + k] = c + w, (2, k)
            else:
                for k in range(2, min(r, 33) + 1):
                    if c + 2 < cost[i + k]:
                        cost[i + k], step[i + k] = c + 2, (3, k)
    ops = []
    i = n
    while i > 0:
        kind, k = step[i]
        ops.append((i - k, kind, k))
        i -= k
    out = bytearray(4)
    for at, kind, k in reversed(ops):
        if kind == 0:
            out.append(0x80 | k)
            out += data[at : at + k]
        elif kind == 1:
            d = match_dist[at]
            out += bytes([((k - 2) << 2) | (d >> 8), d & 0xFF])
        elif kind == 2:
            out += bytes([0xE0 + k - 2]) if k <= 32 else bytes([0xFF, k - 2])
        else:
            out += bytes([0xA0 + k - 2, data[at]])
    struct.pack_into(">I", out, 0, len(out))
    return bytes(out)


# --- assets ---------------------------------------------------------------------


@dataclass(frozen=True)
class Asset:
    """One entry of the asset table. `entry` is the ROM offset of its start word."""

    index: int
    entry: int
    start: int
    end: int
    compressed: bool


def assets(image: bytes | bytearray) -> list[Asset]:
    """The asset table in order, skipping its zero-word separators.

    The game's asset id is `index + 1` for the first group; ids are not needed
    here, only the entries themselves.
    """
    out: list[Asset] = []
    o = ASSET_TABLE
    while o < ASSET_TABLE_END - 4:
        a, b = struct.unpack_from(">II", image, o)
        if a == 0:
            o += 4
            continue
        out.append(Asset(len(out), o, a & 0x7FFFFFFF, b & 0x7FFFFFFF, bool(a >> 31)))
        o += 8
    return out


def read_asset(image: bytes | bytearray, asset: Asset) -> bytes:
    if asset.compressed:
        return lz_decompress(image, asset.start)
    return bytes(image[asset.start : asset.end])


class AssetSpace:
    """Puts rewritten assets back inside the 16 MiB cartridge.

    In place when the new stream fits; else in room this patch freed (what a
    shrunken asset left after it, an asset's old place once it moved) or the
    0xFF padding after the last asset; only when none of that fits does the
    image grow past 16 MiB, which some emulators and flash carts do not read
    (the game then draws headless players and broken flags), so a patch
    should never need it. Growing keeps the 0x100-byte header mirror at
    `MIRROR` so the game still believes it runs from a cartridge.
    """

    ALIGN = 8

    def __init__(self, image: bytearray) -> None:
        self.image = image
        self.top = max(GROWTH_START, len(image)) if len(image) > ROM_SIZE else GROWTH_START
        last = max((a.end for a in assets(image) if a.end <= ROM_SIZE), default=ROM_SIZE)
        self.free: list[tuple[int, int]] = [(self._up(last), ROM_SIZE)]
        self.grew = False

    @classmethod
    def _up(cls, at: int) -> int:
        return -(-at // cls.ALIGN) * cls.ALIGN

    def _release(self, start: int, end: int) -> None:
        start = self._up(start)
        if end - start >= 64 and end <= ROM_SIZE:
            self.free.append((start, end))

    def _take(self, size: int) -> int | None:
        for k, (lo, hi) in enumerate(self.free):
            if hi - lo >= size:
                self.free[k] = (self._up(lo + size), hi)
                return lo
        return None

    def room(self, asset: Asset) -> int:
        """The most bytes `asset` can take without growing the image."""
        return max([asset.end - asset.start] + [hi - lo for lo, hi in self.free])

    def put(self, asset: Asset, data: bytes, compress: bool = True) -> None:
        self.place(asset, lz_compress(data) if compress else data, compress)

    def place(self, asset: Asset, blob: bytes, compressed: bool = True) -> None:
        """`put` for a stream already compressed (or raw, `compressed=False`)."""
        blob += bytes(-len(blob) % 4)
        start = asset.start
        if len(blob) <= asset.end - asset.start:
            self._release(start + len(blob), asset.end)
        else:
            self._release(asset.start, asset.end)
            taken = self._take(len(blob))
            start = taken if taken is not None else self._grow(len(blob))
        self.image[start : start + len(blob)] = blob
        flag = 0x80000000 if compressed else 0
        struct.pack_into(">II", self.image, asset.entry, start | flag, start + len(blob))

    def _grow(self, size: int) -> int:
        self.grew = True
        start = self.top
        self.top += size
        need = -(-self.top // 0x400000) * 0x400000
        if len(self.image) < need:
            self.image.extend(b"\xff" * (need - len(self.image)))
        self.image[MIRROR : MIRROR + 0x100] = self.image[:0x100]
        return start


# --- names ----------------------------------------------------------------------

#: Characters a name can hold and their codes. 0x00 is a space and pads.
_CODES = {" ": 0x00, ".": 0x3F, "-": 0x45, "'": 0x51}
_CODES.update({str(d): 0x01 + d for d in range(10)})
_CODES.update({chr(65 + k): 0x0B + k for k in range(26)})
_CODES.update({chr(97 + k): 0x25 + k for k in range(26)})
_CHARS = {v: k for k, v in _CODES.items()}


#: Letters Unicode does not decompose into a base letter and a mark.
_TRANSLIT = str.maketrans(
    {
        "Ø": "O",
        "ø": "o",
        "Æ": "AE",
        "æ": "ae",
        "Œ": "OE",
        "œ": "oe",
        "ß": "ss",
        "Ł": "L",
        "ł": "l",
        "Đ": "D",
        "đ": "d",
        "Þ": "Th",
        "þ": "th",
        "ı": "i",
    }
)


def encode_name(text: str) -> bytes:
    """`text` in the game's 8-byte name code; characters it lacks are dropped."""
    from ...core.text import to_ascii

    codes = [_CODES[c] for c in to_ascii(text.translate(_TRANSLIT)) if c in _CODES][:NAME_LEN]
    return bytes(codes).ljust(NAME_LEN, b"\0")


def decode_name(raw: bytes) -> str:
    return "".join(_CHARS.get(b, "?") for b in raw).rstrip()


# --- records --------------------------------------------------------------------


def record_offset(team: int, index: int) -> int:
    return PLAYER_TABLE + (team * PLAYERS_PER_TEAM + index) * RECORD


def formation_lines(image: bytes | bytearray, team: int) -> list[int]:
    """Line of each of the team's 11 starting slots: 0 GK, 1 DF, 2 MF, 3 FW."""
    o = FORMATIONS + team * FORMATION_ROW
    return [image[o + 6 * k + 4] & 3 for k in range(11)]


def formation_widths(image: bytes | bytearray, team: int) -> list[int]:
    """Signed width coordinate of each starting slot."""
    o = FORMATIONS + team * FORMATION_ROW
    return [struct.unpack_from(">h", image, o + 6 * k + 2)[0] for k in range(11)]
