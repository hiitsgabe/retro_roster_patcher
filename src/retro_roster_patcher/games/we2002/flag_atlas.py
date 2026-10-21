"""The WE2002 team-flag atlas: where every flag bitmap lives, and how to replace it.

The flags and Master League emblems the menus draw are 4bpp bitmaps, three per
*style* (the per-team "forma" byte the writer already sets): a 64x28 one for the
match and versus screens, a 24x16 one for the team-select grid and a 24x8 one the
game copies into a per-team strip for the smaller screens. Every team draws its
style's bitmaps through its own 16-colour CLUT, so a style is a shape and the CLUT
is the colouring. Styles 0..60 exist; 44..51 are gaps the game skips over.

The bitmaps sit in a handful of 4bpp texture blocks, stored LZSS-compressed
inside the 2D data files (`DATSEL.BIN` for the team select, `DATSEL3.BIN` for
the other select screens, `GDC_*.BIN` per stadium for the match HUD, and two of
the editor files) and uploaded to fixed VRAM rectangles. The same block bytes
are repeated file for file, which is what `patch_flag_atlas` relies on: it
decodes the atlas once from `DATSEL.BIN`, paints the new bitmaps into a VRAM
model, and re-encodes every block whose original content it recognises.

Geometry below was read out of SELECT.BIN's sprite code (the 24x16 and 24x8
cases) and confirmed against the uploaded VRAM for every style; the 64x28
layout is what the data holds, which differs from one of the overlay's copy
routines by one texture page. Trust the data.

Each 2D data file is `[list pointers][streams...][record lists...]`, all
pointers absolute RAM addresses for the file's load base. A record is 16 bytes:
`u16 kind, u16 x, u16 y, u16 w, u16 h, u16 pad, u32 ptr`, kind 0x0a an
LZSS-compressed image and the list ends at kind 0xff. The main executable holds
a table of `u32 lba, u32 load_address, u32 sector_count` per file, which is what
the game loads by, so a file that grows needs its sector count raised there as
well as in the ISO directory.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from ...core.errors import RomError

# ---------------------------------------------------------------------------
# LZSS, as implemented by the game (SLPM_870.56 at 0x8001cc9c).
#
# One flag byte per eight tokens, bit 0 first; a clear bit is a literal byte. A
# set bit introduces a control byte `c`:
#   0xxxxxxx dd   copy (c>>2)+3 bytes from distance ((c&3)<<8 | dd), 1..1023
#   10LLDDDD      copy (L+2) bytes from distance D+1, 1..16
#   11xxxxxx      a run of c-0xb8 literal bytes (8..70); 0xff ends the stream
# ---------------------------------------------------------------------------


def lz_decompress(src: bytes, start: int = 0) -> tuple[bytes, int]:
    """Decode one stream; return the bytes and the offset just past its 0xFF."""
    out = bytearray()
    i = start
    flags = 0
    n = len(src)
    while True:
        flags >>= 1
        if not flags & 0x100:
            if i >= n:
                raise ValueError("LZSS stream ends without a terminator")
            flags = src[i] | 0xFF00
            i += 1
        if i >= n:
            raise ValueError("LZSS stream ends without a terminator")
        c = src[i]
        if not flags & 1:
            out.append(c)
            i += 1
            continue
        if not c & 0x80:
            if i + 1 >= n:
                raise ValueError("truncated LZSS back-reference")
            dist = src[i + 1] | ((c & 3) << 8)
            count = (c >> 2) + 3
            i += 2
        elif not c & 0x40:
            dist = (c & 0xF) + 1
            count = (c >> 4) - 6
            i += 1
        else:
            if c == 0xFF:
                return bytes(out), i + 1
            count = c - 0xB8
            i += 1
            out += src[i : i + count]
            i += count
            continue
        if dist == 0 or dist > len(out):
            raise ValueError("LZSS back-reference before the start of the output")
        for _ in range(count):
            out.append(out[-dist])


def lz_compress(data: bytes) -> bytes:
    """Encode `data` so `lz_decompress` gives it back.

    Greedy longest-match over a 1023-byte window, with runs of eight or more
    literals packed as a literal-run token. On the game's own blocks this lands
    within a percent or two of Konami's encoder.
    """
    n = len(data)
    out = bytearray()
    flag_pos = -1
    flag_bits = 0
    nbits = 8
    pending: list[int] = []
    index: dict[bytes, list[int]] = {}

    def emit(bit: int, payload: bytes) -> None:
        nonlocal flag_pos, flag_bits, nbits
        if nbits == 8:
            if flag_pos >= 0:
                out[flag_pos] = flag_bits
            flag_pos = len(out)
            out.append(0)
            flag_bits = 0
            nbits = 0
        flag_bits |= bit << nbits
        nbits += 1
        out.extend(payload)

    def flush_literals() -> None:
        while pending:
            if len(pending) >= 8:
                run = pending[:70]
                del pending[:70]
                emit(1, bytes([len(run) + 0xB8]) + bytes(run))
            else:
                emit(0, bytes([pending.pop(0)]))

    def remember(pos: int) -> None:
        if pos + 3 <= n:
            index.setdefault(data[pos : pos + 3], []).append(pos)

    i = 0
    while i < n:
        best_len = 0
        best_dist = 0
        key = data[i : i + 3]
        if len(key) == 3:
            for cand in reversed(index.get(key, ())):
                dist = i - cand
                if dist > 1023:
                    break
                length = 3
                while length < 34 and i + length < n and data[cand + length] == data[i + length]:
                    length += 1
                if length > best_len:
                    best_len, best_dist = length, dist
                    if length == 34:
                        break
        if best_len < 2 and i >= 1 and i + 1 < n and data[i] == data[i - 1] == data[i + 1]:
            # A two-to-five byte repeat of the previous byte: the short token.
            best_len, best_dist = 2, 1
            while best_len < 5 and i + best_len < n and data[i + best_len] == data[i - 1]:
                best_len += 1
        if best_len >= 3 or (best_len == 2 and best_dist <= 16):
            flush_literals()
            if best_dist <= 16 and best_len <= 5:
                emit(1, bytes([0x80 | ((best_len + 6) << 4) | (best_dist - 1)]))
            else:
                emit(1, bytes([((best_len - 3) << 2) | (best_dist >> 8), best_dist & 0xFF]))
            for k in range(best_len):
                remember(i + k)
            i += best_len
        else:
            pending.append(data[i])
            remember(i)
            i += 1
    flush_literals()
    emit(1, b"\xff")
    out[flag_pos] = flag_bits
    return bytes(out)


# ---------------------------------------------------------------------------
# Geometry. Rectangles are (x, y, w, h): x in VRAM halfwords (four 4bpp pixels
# each), y in VRAM rows, w and h in pixels.
# ---------------------------------------------------------------------------

STYLE_COUNT = 61
#: Every style byte the game has art for. 44..51 do not exist.
VALID_STYLES: tuple[int, ...] = tuple(s for s in range(STYLE_COUNT) if not 44 <= s < 52)

Rect = tuple[int, int, int, int]


def big_rect(style: int) -> Rect:
    """The 64x28 flag: match scoreboard and versus screens."""
    if style < 36:
        return (832 + 16 * (style % 4), 256 + 28 * (style // 4), 64, 28)
    if style < 52:
        return (768 + 16 * ((style - 36) % 4), 256 + 28 * ((style - 36) // 4), 64, 28)
    return (448 + 16 * ((style - 52) % 4), 28 * ((style - 52) // 4), 64, 28)


def medium_rect(style: int) -> Rect:
    """The 24x16 flag: the team-select grid."""
    if style >= 58:
        return (482 + 6 * (style - 58), 72, 24, 16)
    e = style if style < 44 else style - 8
    return (768 + 6 * (e % 10), 432 + 16 * (e // 10), 24, 16)


def mini_rect(style: int) -> Rect:
    """The 24x8 flag the game copies into its per-team strip."""
    if style >= 60:
        return (464 + 6 * ((style - 60) % 8), 64 + 8 * ((style - 60) // 8), 24, 8)
    return (768 + 6 * (style % 10), 312 + 8 * (style // 10), 24, 8)


def style_rects(style: int) -> tuple[Rect, Rect, Rect]:
    if style not in VALID_STYLES:
        raise ValueError(f"style {style} is not one the game draws")
    return big_rect(style), medium_rect(style), mini_rect(style)


# ---------------------------------------------------------------------------
# A VRAM model: 1024 x 512 halfwords, read and written a 4bpp pixel at a time.
# ---------------------------------------------------------------------------

VRAM_W = 1024
VRAM_H = 512


class Vram:
    def __init__(self) -> None:
        self.data = bytearray(VRAM_W * VRAM_H * 2)

    def put_block(self, x: int, y: int, w_hw: int, rows: int, raw: bytes) -> None:
        for r in range(rows):
            o = ((y + r) * VRAM_W + x) * 2
            self.data[o : o + w_hw * 2] = raw[r * w_hw * 2 : (r + 1) * w_hw * 2]

    def get_block(self, x: int, y: int, w_hw: int, rows: int) -> bytes:
        out = bytearray()
        for r in range(rows):
            o = ((y + r) * VRAM_W + x) * 2
            out += self.data[o : o + w_hw * 2]
        return bytes(out)

    def paint(self, rect: Rect, pixels: list[list[int]]) -> None:
        """Write 4bpp indices (rows of `w` values 0..15) into `rect`."""
        x, y, w, h = rect
        if len(pixels) != h or any(len(row) != w for row in pixels):
            raise ValueError(f"image is not {w}x{h}")
        for r, row in enumerate(pixels):
            for c, v in enumerate(row):
                px = x * 4 + c
                o = ((y + r) * VRAM_W + px // 4) * 2
                shift = 4 * (px % 4)
                word = self.data[o] | (self.data[o + 1] << 8)
                word = (word & ~(0xF << shift)) | ((v & 0xF) << shift)
                self.data[o] = word & 0xFF
                self.data[o + 1] = word >> 8

    def pixels(self, rect: Rect) -> list[list[int]]:
        x, y, w, h = rect
        out = []
        for r in range(h):
            row = []
            for c in range(w):
                px = x * 4 + c
                o = ((y + r) * VRAM_W + px // 4) * 2
                word = self.data[o] | (self.data[o + 1] << 8)
                row.append((word >> (4 * (px % 4))) & 0xF)
            out.append(row)
        return out


def _overlaps(a: Rect, b: Rect) -> bool:
    """Two rects in the same units (halfwords wide)."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def _hw_rect(rect: Rect) -> Rect:
    """A pixel-wide rect as halfwords, rounded out to whole halfwords."""
    x, y, w, h = rect
    return (x, y, (w + 3) // 4, h)


# ---------------------------------------------------------------------------
# The 2D data file layout.
# ---------------------------------------------------------------------------

KIND_IMAGE = 0x0A
KIND_END = 0xFF
_KINDS = (0x00, 0x09, KIND_IMAGE)


@dataclass
class Record:
    kind: int
    x: int
    y: int
    w: int  # halfwords
    h: int  # rows
    pad: int
    ptr: int  # absolute RAM address
    list_offset: int = 0  # file offset of this record's 16 bytes


@dataclass
class DataFile:
    base: int
    header: list[int]  # list pointers, zero for an empty slot
    lists: list[list[Record]]
    data: bytes

    def records(self) -> Iterable[Record]:
        for lst in self.lists:
            yield from lst


def infer_base(data: bytes) -> int:
    """The RAM address a 2D data file is loaded at, read off its own pointers.

    The header is a run of list pointers; the first stream starts right after
    it; every record list is a run of 16-byte records ending in kind 0xff. So
    for every record list in the file and every plausible header length, the
    base that puts the list's lowest stream at the header's end must also make
    one header word point at the list. One base satisfies that; none means the
    file is not laid out this way.
    """
    size = len(data)
    words = [struct.unpack_from("<I", data, k)[0] for k in range(0, min(size, 64), 4)]
    for t in range(0, size - 16, 4):
        kind, _x, _y, w, h, _pad, ptr = struct.unpack_from("<HHHHHHI", data, t)
        if (kind & 0xFF) not in _KINDS or w == 0 or h == 0 or w > VRAM_W or h > VRAM_H:
            continue
        # Walk the list, collecting the lowest stream pointer.
        lowest = ptr
        k = t
        ok = False
        while k + 16 <= size:
            kind, _x, _y, w, h, _pad, ptr = struct.unpack_from("<HHHHHHI", data, k)
            if kind == KIND_END:
                ok = True
                break
            if (kind & 0xFF) not in _KINDS or w > VRAM_W or h > VRAM_H:
                break
            lowest = min(lowest, ptr)
            k += 16
        if not ok:
            continue
        for header_len in range(4, len(words) * 4 + 1, 4):
            base = lowest - header_len
            if base % 4 or not 0x80000000 <= base < 0x80200000:
                continue
            if base + t in words[: header_len // 4]:
                return base
    raise RomError("2D data file layout not recognised")


def parse_data_file(data: bytes, base: int) -> DataFile:
    """Read the list pointers and every record list of a 2D data file.

    The header's length is not stored: it runs until the first stream, which is
    the lowest address any record points at. Pointer-looking words are read
    greedily first, their lists parsed, and the header then cut back to that
    lowest stream. A word in the header that is neither zero nor the start of a
    well-formed list is a layout this module does not know, and is refused.
    """
    end = base + len(data)

    def is_ptr(v: int) -> bool:
        return base <= v < end and v % 4 == 0

    def read_list(off: int) -> list[Record] | None:
        recs: list[Record] = []
        while off + 16 <= len(data):
            kind, x, y, w, h, pad, ptr = struct.unpack_from("<HHHHHHI", data, off)
            if kind == KIND_END:
                return recs
            # CLUT records carry flags in the high byte (0xff09); the low byte
            # is the kind.
            if (kind & 0xFF) not in _KINDS or not is_ptr(ptr) or w > VRAM_W or h > VRAM_H:
                return None
            recs.append(Record(kind, x, y, w, h, pad, ptr, off))
            off += 16
        return None

    greedy: list[int] = []
    off = 0
    while off + 4 <= len(data):
        v = struct.unpack_from("<I", data, off)[0]
        if v == 0 or is_ptr(v):
            greedy.append(v)
            off += 4
        else:
            break
    lists = {}
    for v in greedy:
        if v and v not in lists:
            lst = read_list(v - base)
            if lst is not None:
                lists[v] = lst
    stream_ptrs = [r.ptr for v in lists for r in lists[v]]
    if not stream_ptrs:
        raise RomError("2D data file has no image records")
    header_len = min(stream_ptrs) - base
    if header_len % 4 or header_len > len(greedy) * 4:
        raise RomError("2D data file header does not end at its first stream")
    header = greedy[: header_len // 4]
    for v in header:
        if v and v not in lists:
            raise RomError(f"2D data file header points at {v:#x}, which is not a record list")
    return DataFile(base, header, [lists[v] for v in header if v], data)


def rebuild_data_file(parsed: DataFile, replacements: Mapping[int, bytes]) -> bytes:
    """`parsed.data` with the streams at `replacements` (file offsets) swapped.

    Every stream and list keeps its order and moves only as far as the streams
    before it grew or shrank; every pointer in the header and the lists is
    rewritten to match. Bytes between items that nothing points at travel with
    the item before them.
    """
    data = parsed.data
    base = parsed.base
    stream_offsets = {r.ptr - base for r in parsed.records()}
    list_offsets = {v - base for v in parsed.header if v}
    items = sorted(stream_offsets | list_offsets)
    header_len = min(items)
    for off in replacements:
        if off not in items:
            raise ValueError(f"offset {off:#x} is not the start of a stream")
    out = bytearray(data[:header_len])
    new_off: dict[int, int] = {}
    for n, off in enumerate(items):
        end = items[n + 1] if n + 1 < len(items) else len(data)
        blob = replacements.get(off)
        if blob is None:
            blob = data[off:end]
        else:
            blob = bytes(blob) + b"\x00" * (-len(blob) % 4)
        new_off[off] = len(out)
        out += blob
    # Lists were copied verbatim above; patch their pointer fields in place.
    for lst in parsed.lists:
        if not lst:
            continue
        list_old = lst[0].list_offset
        list_new = new_off[list_old]
        for k, rec in enumerate(lst):
            struct.pack_into("<I", out, list_new + 16 * k + 12, base + new_off[rec.ptr - base])
    # An empty list is just its terminator; it moved with the item it sits in.
    for k, v in enumerate(parsed.header):
        if v:
            struct.pack_into("<I", out, 4 * k, base + new_off[v - base])
    return bytes(out)


# ---------------------------------------------------------------------------
# Style assignment.
# ---------------------------------------------------------------------------


def assign_styles(forma: bytes, patched: Iterable[int], wanted: Iterable[int]) -> dict[int, int]:
    """Give each wanted team a style of its own to draw its logo through.

    `forma` is the 95-byte style table (63 national entries, then 32 Master
    League ones), `patched` every entry the patch rewrites, `wanted` the slots
    (0..62) that want a logo, each of which owns entries `slot` and, below 32,
    `63 + slot`. Styles nobody outside the patch uses go first, so a replaced
    bitmap is never seen under an untouched team's flag; when those run out, the
    styles with the fewest untouched users follow, and when every style is
    taken the remaining slots get none.
    """
    patched = set(patched)
    users: dict[int, set[int]] = {s: set() for s in VALID_STYLES}
    for entry, style in enumerate(forma[:95]):
        if style in users:
            users[style].add(entry)
    free = [s for s in VALID_STYLES if users[s] <= patched]
    shared = sorted(
        (s for s in VALID_STYLES if not users[s] <= patched),
        key=lambda s: (len(users[s] - patched), s),
    )
    order = free + shared
    out: dict[int, int] = {}
    for slot, style in zip(sorted(set(wanted)), order, strict=False):
        out[slot] = style
    return out


# ---------------------------------------------------------------------------
# The disc.
# ---------------------------------------------------------------------------

_SECTOR = 2352
_USER = 2048
_USER_OFF = 24
_PVD_LBA = 16

#: Files that carry copies of the atlas blocks. Any that is absent is skipped.
ATLAS_FILES = ("DATSEL.BIN", "DATSEL2.BIN", "DATSEL3.BIN", "EDT_2D.BIN", "EDTR_2D.BIN")
ATLAS_FILE_PREFIXES = ("GDC_",)
EXE_NAME = "SLPM_870.56"
SOURCE_FILE = "DATSEL.BIN"


@dataclass
class DiscFile:
    name: str
    lba: int
    size: int
    record_offset: int  # absolute byte offset of its directory record in the image


@dataclass
class LogoImages:
    """One logo rendered for every slot a style has, as 4bpp indices.

    `palette` is the fifteen colours behind indices 1..15; index 0 is the
    transparent background.
    """

    palette: list[tuple[int, int, int]]
    big: list[list[int]]
    medium: list[list[int]]
    mini: list[list[int]]


@dataclass
class AtlasReport:
    files: list[str] = field(default_factory=list)
    grown: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _read_user(f, lba: int, nbytes: int) -> bytes:
    out = bytearray()
    while nbytes > 0:
        f.seek(lba * _SECTOR + _USER_OFF)
        chunk = min(nbytes, _USER)
        out += f.read(chunk)
        nbytes -= chunk
        lba += 1
    return bytes(out)


def _edc(data: bytes) -> int:
    table = []
    for i in range(256):
        edc = i
        for _ in range(8):
            edc = (edc >> 1) ^ 0xD8018001 if edc & 1 else edc >> 1
        table.append(edc)
    crc = 0
    for b in data:
        crc = table[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc


def _write_user(f, lba: int, data: bytes) -> None:
    """Write whole sectors of user data, recomputing each sector's EDC."""
    data = data + b"\x00" * (-len(data) % _USER)
    for n in range(len(data) // _USER):
        sector = (lba + n) * _SECTOR
        f.seek(sector + _USER_OFF)
        f.write(data[n * _USER : (n + 1) * _USER])
        f.seek(sector + 16)
        body = f.read(2056)
        f.seek(sector + 2072)
        f.write(struct.pack("<I", _edc(body)))


def _set_record_size(f, record_offset: int, size: int) -> None:
    f.seek(record_offset + 10)
    f.write(struct.pack("<I", size))
    f.seek(record_offset + 14)
    f.write(struct.pack(">I", size))
    sector = (record_offset // _SECTOR) * _SECTOR
    f.seek(sector + 16)
    body = f.read(2056)
    f.seek(sector + 2072)
    f.write(struct.pack("<I", _edc(body)))


def _read_directory(f, lba: int, size: int) -> list[DiscFile]:
    out = []
    sectors = (size + _USER - 1) // _USER
    for s in range(sectors):
        sector = _read_user(f, lba + s, _USER)
        pos = 0
        while pos < _USER:
            length = sector[pos]
            if length == 0:
                break
            name_len = sector[pos + 32]
            name = sector[pos + 33 : pos + 33 + name_len].decode("latin1").split(";")[0]
            ext = struct.unpack_from("<I", sector, pos + 2)[0]
            data_len = struct.unpack_from("<I", sector, pos + 10)[0]
            if name not in ("", "\x00", "\x01"):
                out.append(DiscFile(name, ext, data_len, (lba + s) * _SECTOR + _USER_OFF + pos))
            pos += length
    return out


def _disc_files(f) -> dict[str, DiscFile]:
    """Every file in the root directory and in `/BIN`, by name without `;1`."""
    pvd = _read_user(f, _PVD_LBA, _USER)
    if pvd[1:6] != b"CD001":
        raise RomError("no ISO 9660 volume descriptor; is this a raw WE2002 image?")
    root_lba = struct.unpack_from("<I", pvd, 156 + 2)[0]
    root_size = struct.unpack_from("<I", pvd, 156 + 10)[0]
    files: dict[str, DiscFile] = {}
    for entry in _read_directory(f, root_lba, root_size):
        files[entry.name] = entry
    bin_dir = files.get("BIN")
    if bin_dir is not None:
        for entry in _read_directory(f, bin_dir.lba, bin_dir.size):
            files[entry.name] = entry
    return files


def _file_table_entries(exe: bytes, lba: int) -> list[tuple[int, int, int]]:
    """Every `(offset, load_address, sector_count)` entry for `lba` in the EXE table."""
    out = []
    key = struct.pack("<I", lba)
    off = exe.find(key)
    while off >= 0:
        if off % 4 == 0 and off + 12 <= len(exe):
            addr, sectors = struct.unpack_from("<II", exe, off + 4)
            if 0x80000000 <= addr < 0x80200000 and 0 < sectors < 0x10000:
                out.append((off, addr, sectors))
        off = exe.find(key, off + 1)
    return out


def _growth_budget(exe: bytes, entry: DiscFile, all_lbas: list[int]) -> int:
    """How many sectors `entry` may occupy once rewritten.

    The game reads each file by a sector count of its own, not by the ISO
    directory: the count in the executable's file table for files listed there
    (raised by the caller when exceeded, so those may grow to the next file),
    and for the per-stadium `GDC_*.BIN` files the one stadium-slot entry's
    count, which the loader applies to whichever stadium file it picks. A file
    with neither is read by a count this module cannot find, so it may not
    grow. Every budget stops at the next file on the disc.
    """
    following = [lba for lba in all_lbas if lba > entry.lba]
    free = (following[0] - entry.lba) if following else 0x7FFFFFFF
    if _file_table_entries(exe, entry.lba):
        return free
    if entry.name.startswith("GDC_"):
        counts = [sectors for _off, sectors in _entries_by_address(exe, _GDC_LOAD_ADDRESS)]
        if counts:
            return min(free, max(counts))
    return (entry.size + _USER - 1) // _USER


def _entries_by_address(exe: bytes, addr: int) -> list[tuple[int, int]]:
    """Every `(offset, sector_count)` file table entry that loads at `addr`."""
    out = []
    key = struct.pack("<I", addr)
    off = exe.find(key)
    while off >= 0:
        if off % 4 == 0 and off >= 4 and off + 8 <= len(exe):
            lba = struct.unpack_from("<I", exe, off - 4)[0]
            sectors = struct.unpack_from("<I", exe, off + 4)[0]
            if 0 < lba < 0x100000 and 0 < sectors < 0x10000:
                out.append((off - 4, sectors))
        off = exe.find(key, off + 1)
    return out


#: Every stadium file loads here; the table entry with this address is the slot.
_GDC_LOAD_ADDRESS = 0x800C8000


def _atlas_blocks(parsed: DataFile) -> list[Record]:
    """The image records that lie inside the two atlas regions."""
    region_a = (768, 256, 128, 256)
    region_b = (448, 0, 64, 256)
    out = []
    for rec in parsed.records():
        if rec.kind & 0xFF != KIND_IMAGE:
            continue
        r = (rec.x, rec.y, rec.w, rec.h)
        if _overlaps(r, region_a) or _overlaps(r, region_b):
            out.append(rec)
    return out


def patch_flag_atlas(image_path: str, logos: Mapping[int, LogoImages]) -> AtlasReport:
    """Paint `logos` (style -> images) into every copy of the flag atlas on the disc.

    Reads the atlas out of `DATSEL.BIN`, then for every 2D data file on the disc
    re-encodes each atlas block whose original content it recognises. A file
    whose re-encoded blocks no longer fit grows into the free sectors after it,
    with its ISO directory record and its sector count in the main executable's
    file table raised to match; a file that cannot grow raises `RomError` before
    anything is written.
    """
    with open(image_path, "r+b") as f:
        files = _disc_files(f)
        source = files.get(SOURCE_FILE)
        exe_entry = files.get(EXE_NAME)
        if source is None or exe_entry is None:
            raise RomError(f"{SOURCE_FILE} or {EXE_NAME} not found on the disc")
        exe = bytearray(_read_user(f, exe_entry.lba, exe_entry.size))

        source_data = _read_user(f, source.lba, source.size)
        source_parsed = parse_data_file(source_data, infer_base(source_data))

        original = Vram()
        for rec in _atlas_blocks(source_parsed):
            raw, _ = lz_decompress(source_parsed.data, rec.ptr - source_parsed.base)
            original.put_block(rec.x, rec.y, rec.w, rec.h, raw)
        painted = Vram()
        painted.data[:] = original.data

        touched: list[Rect] = []
        for style, images in logos.items():
            big, medium, mini = style_rects(style)
            painted.paint(big, images.big)
            painted.paint(medium, images.medium)
            painted.paint(mini, images.mini)
            touched += [_hw_rect(big), _hw_rect(medium), _hw_rect(mini)]

        # Which blocks change, keyed by their original decoded content so every
        # file's copy is recognised whatever offset it sits at.
        new_blocks: dict[bytes, bytes] = {}
        for rec in _atlas_blocks(source_parsed):
            r = (rec.x, rec.y, rec.w, rec.h)
            if not any(_overlaps(r, t) for t in touched):
                continue
            before = original.get_block(rec.x, rec.y, rec.w, rec.h)
            after = painted.get_block(rec.x, rec.y, rec.w, rec.h)
            if before != after:
                new_blocks[before] = lz_compress(after)

        report = AtlasReport()
        if not new_blocks:
            return report

        all_lbas = sorted(entry.lba for entry in files.values())
        candidates = [
            entry
            for name, entry in files.items()
            if name in ATLAS_FILES or name.startswith(ATLAS_FILE_PREFIXES)
        ]
        exe_changed = False
        for entry in sorted(candidates, key=lambda e: e.lba):
            data = _read_user(f, entry.lba, entry.size)
            try:
                base = infer_base(data)
                parsed = parse_data_file(data, base)
            except RomError as exc:
                report.skipped.append(f"{entry.name}: {exc}")
                continue
            replacements: dict[int, bytes] = {}
            for rec in _atlas_blocks(parsed):
                off = rec.ptr - base
                try:
                    raw, _ = lz_decompress(data, off)
                except ValueError:
                    continue
                new = new_blocks.get(raw)
                if new is not None:
                    replacements[off] = new
            if not replacements:
                continue
            rebuilt = rebuild_data_file(parsed, replacements)
            old_sectors = (entry.size + _USER - 1) // _USER
            new_sectors = (len(rebuilt) + _USER - 1) // _USER
            if new_sectors > old_sectors:
                budget = _growth_budget(bytes(exe), entry, all_lbas)
                if new_sectors > budget:
                    report.skipped.append(
                        f"{entry.name}: needs {new_sectors} sectors, {budget} available; "
                        f"its flags are left as they were"
                    )
                    continue
                for off, _addr, sectors in _file_table_entries(bytes(exe), entry.lba):
                    if sectors < new_sectors:
                        struct.pack_into("<I", exe, off + 8, new_sectors)
                        exe_changed = True
                report.grown.append(entry.name)
            _write_user(f, entry.lba, rebuilt)
            if len(rebuilt) != entry.size:
                _set_record_size(f, entry.record_offset, len(rebuilt))
            report.files.append(entry.name)
        if exe_changed:
            _write_user(f, exe_entry.lba, bytes(exe))
        f.flush()
    return report
