"""Licensed kit textures of PES 2013 (PS2): the encrypted 3D shirt packages.

A licensed team (`kits.py`: `t[31] = 0`) is not drawn from its colours but
from texture packages in `0_TEXT.AFS`. They sit in one run from entry 7243,
twelve entries per team: four kits in the order goalkeeper home, goalkeeper
away, player home, player away (`PACKAGE_INDEX`; the kit records of entry 54
order them differently), three entries each: the encrypted texture package,
then the unencrypted number and name-font textures. A team's rank in that run
is its position among the licensed teams, national rows first (in row order),
then club slots (`licensed_slots`). Read off the disc: ranks 0..16 are the 17
licensed nations (Croatia, Czech Republic, England ... Japan), rank 17 is
Manchester United at slot 7; the home kit of rank 0 is Croatia's checkerboard,
its away kit blue, which is what fixes the kit order.

A package entry is a block (`afs.py`) whose 16 trailing header bytes are four
key words; four dwords of the zlib stream (+0x20, +0x24, +0x2c, +0x34 of the
entry) are XORed with masks made from the keys, the PES 6 kit cipher, so the
same function encrypts. The stream inflates to 167328 bytes:

    u32 3, u32 8, u32 0x20, u32 0x204a0, pad to 0x20
    0x20     LOD0: 128-byte texture header, 1024-byte CLUT, 512x256 indices
    0x204a0  LOD1: header, 256x128 indices (through LOD0's CLUT)
    0x28520  LOD2: header, 64x32 indices

The header is the one `textures.py` reads (magic, w/h at +0x14, PSMT8) and
the CLUT is GS-swizzled like the UI ones, but the pixels are linear, not
swizzled, in all three levels (checked against Croatia's checkerboard shirt).
"""

from __future__ import annotations

import io
import struct
import zlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...core.errors import RomError
from .afs import BLOCK_HEADER
from .kits import AWAY, GK_AWAY, GK_HOME, HOME
from .models import NATIONAL_SLOT_BASE, TEXT_AFS
from .textures import PALETTE_BYTES, PSMT8, TEXTURE_HEADER, TEXTURE_MAGIC, Texture, clut_swizzle

if TYPE_CHECKING:
    from .rom_reader import RomReader
    from .rom_writer import RomWriter

FIRST_PACKAGE = 7243
PACKAGE_STRIDE = 12
#: Where a kit's package sits inside the team's twelve entries, keyed by the
#: kit-record order of `kits.py` (goalkeeper home, home, goalkeeper away, away).
PACKAGE_INDEX = {GK_HOME: 0, GK_AWAY: 3, HOME: 6, AWAY: 9}
PACKAGE_SIZE = 167328
LOD0_SIZE = (512, 256)
#: Palettes tried, largest first, until the rebuilt entry fits its room.
PALETTES = (255, 128, 64, 32, 16)
_M = 0xFFFFFFFF

#: Where the parts of the kit land on the 512x256 texture, (x, y, w, h).
#: ponytail: empty. The emulator run (a labelled hue grid written over
#: Cruzeiro's rank-17 home and GK packages) only ever showed the grid on
#: the goalkeeper at the far goal, too small to read; map it from a close
#: replay or a kit-select screen and fill this in.
REGIONS: dict[str, tuple[int, int, int, int]] = {}


# -- cipher ----------------------------------------------------------------


def _masks(entry: bytes) -> list[tuple[int, int]]:
    k1, k2, k3, k4 = struct.unpack_from("<4I", entry, 0x10)
    if not (k1 and k2 and k3 and k4):
        raise RomError("Kit entry carries no cipher keys")
    return [
        (0x20, ((k4 + k3) & _M) & 0x2DD795AC),
        (0x24, ((k4 - k2) & _M) & 0x5D54FC78),
        (0x2C, ((k2 & 0xF7B5B641) + k1) & _M),
        (0x34, (k1 - (k3 & 0x78D50165)) & _M),
    ]


def _crypt(entry: bytes) -> bytes:
    """The entry with its four masked dwords flipped (its own inverse)."""
    out = bytearray(entry)
    for off, mask in _masks(entry):
        struct.pack_into("<I", out, off, struct.unpack_from("<I", out, off)[0] ^ mask)
    return bytes(out)


def decrypt_entry(entry: bytes) -> bytes:
    """The inflated package of an encrypted kit entry."""
    if len(entry) < BLOCK_HEADER + 0x18:
        raise RomError("Kit entry shorter than its header")
    csize, usize = struct.unpack_from("<II", entry, 4)
    try:
        payload = zlib.decompress(_crypt(entry)[BLOCK_HEADER : BLOCK_HEADER + csize])
    except zlib.error as exc:
        raise RomError(f"Kit entry did not decrypt: {exc}") from exc
    if len(payload) != usize:
        raise RomError(f"Kit package inflated to {len(payload)} bytes, header says {usize}")
    return payload


def encrypt_entry(entry_header: bytes, payload_zlib: bytes) -> bytes:
    """A kit entry: the original 32-byte header (keys and inflated size kept,
    the stream size updated) over `payload_zlib`, masked with the keys."""
    head = bytearray(entry_header[:BLOCK_HEADER])
    struct.pack_into("<I", head, 4, len(payload_zlib))
    return _crypt(bytes(head) + payload_zlib)


def _stored(data: bytes, final: bool = False) -> bytes:
    """One stored deflate block."""
    return bytes([final]) + struct.pack("<HH", len(data), len(data) ^ 0xFFFF) + data


def deflate_to(payload: bytes, size: int) -> bytes:
    """A zlib stream of exactly `size` bytes: `payload` deflated, then padded
    with empty stored blocks (five bytes each) ahead of the final block, the
    last few payload bytes held back as a stored block to make the count work.

    A kit entry on the disc is exactly its stream plus the 32-byte header, so a
    rebuilt one keeps the stream size the game read along with everything else.
    """
    least = None
    for held in range(64):
        head, tail = (payload[:-held], payload[-held:]) if held else (payload, b"")
        for strategy in (zlib.Z_DEFAULT_STRATEGY, zlib.Z_FILTERED):
            c = zlib.compressobj(9, zlib.DEFLATED, 15, 9, strategy)
            body = c.compress(head) + c.flush(zlib.Z_SYNC_FLUSH)  # byte-aligned
            if tail:
                body += _stored(tail)
            end = _stored(b"", final=True) + struct.pack(">I", zlib.adler32(payload))
            pad = size - len(body) - len(end)
            if pad >= 0 and pad % 5 == 0:
                return body + _stored(b"") * (pad // 5) + end
            least = min(least or len(body) + len(end), len(body) + len(end))
    raise RomError(f"needs {least} bytes, {size} available")


# -- package ---------------------------------------------------------------


@dataclass
class Lod:
    header: bytes
    pixels: bytes  # linear indices, w*h

    @property
    def width(self) -> int:
        return struct.unpack_from("<H", self.header, 0x14)[0]

    @property
    def height(self) -> int:
        return struct.unpack_from("<H", self.header, 0x16)[0]


@dataclass
class KitPackage:
    head: bytes  # the part table before the first texture
    palette: bytes  # linear RGBA, 256 entries, shared by the three levels
    lods: list[Lod]

    @classmethod
    def parse(cls, payload: bytes) -> KitPackage:
        if len(payload) < 0x20 or struct.unpack_from("<II", payload) != (3, 8):
            raise RomError("Not a kit texture package")
        first = struct.unpack_from("<I", payload, 8)[0]
        lods: list[Lod] = []
        palette = b""
        pos = first
        while pos + TEXTURE_HEADER <= len(payload):
            header = payload[pos : pos + TEXTURE_HEADER]
            if struct.unpack_from("<I", header)[0] != TEXTURE_MAGIC:
                break
            if header[0x19] != PSMT8:
                raise RomError(f"Kit texture format {header[0x19]:#x} is not 8-bit indexed")
            size = struct.unpack_from("<I", header, 8)[0]
            w, h = struct.unpack_from("<HH", header, 0x14)
            body = payload[pos + TEXTURE_HEADER : pos + size]
            if not lods:
                palette, body = clut_swizzle(body[:PALETTE_BYTES]), body[PALETTE_BYTES:]
            if len(body) != w * h:
                raise RomError("Kit texture shorter than its size says")
            lods.append(Lod(header, body))
            pos += size
        if len(lods) != 3 or (lods[0].width, lods[0].height) != LOD0_SIZE:
            raise RomError(f"Kit package has {len(lods)} textures, not three")
        return cls(payload[:first], palette, lods)

    def to_bytes(self) -> bytes:
        out = bytearray(self.head)
        for k, lod in enumerate(self.lods):
            out += lod.header
            if k == 0:
                out += clut_swizzle(self.palette)
            out += lod.pixels
        return bytes(out)

    def to_png(self) -> bytes:
        """LOD0 as RGBA PNG."""
        lod = self.lods[0]
        return Texture(lod.header, self.palette, lod.pixels).to_png()

    def paint(self, png: bytes, colours: int = 255) -> KitPackage:
        """Draw `png` over LOD0 (fitted to 512x256, `colours` opaque colours,
        index 0 for transparency) and the two smaller levels scaled from it."""
        from PIL import Image

        w, h = LOD0_SIZE
        img = Image.open(io.BytesIO(png)).convert("RGBA")
        if img.size != (w, h):
            img = img.resize((w, h), Image.Resampling.LANCZOS)
        quant = img.convert("RGB").quantize(colors=colours, method=Image.Quantize.MEDIANCUT)
        flat = quant.getpalette() or []
        pal = bytearray(PALETTE_BYTES)
        for i in range(min(colours, 255)):
            r, g, b = flat[3 * i : 3 * i + 3] if 3 * i + 3 <= len(flat) else (0, 0, 0)
            pal[4 * (i + 1) : 4 * (i + 2)] = bytes((r, g, b, 0x80))
        self.palette = bytes(pal)
        for lod in self.lods:
            level = img.resize((lod.width, lod.height), Image.Resampling.LANCZOS)
            idx = level.convert("RGB").quantize(palette=quant, dither=Image.Dither.NONE).tobytes()
            alpha = level.getchannel("A").tobytes()
            lod.pixels = bytes(0 if alpha[k] < 128 else idx[k] + 1 for k in range(len(idx)))
        return self


# -- on the disc -----------------------------------------------------------


def kit_entry(rank: int, which: int) -> int:
    """`0_TEXT.AFS` entry of the texture package of kit `which` (`kits.GK_HOME`,
    `HOME`, `GK_AWAY` or `AWAY`) of the team at `rank`."""
    return FIRST_PACKAGE + PACKAGE_STRIDE * rank + PACKAGE_INDEX[which]


def licensed_slots(reader: RomReader) -> dict[int, int]:
    """Slot (national rows as `NATIONAL_SLOT_BASE` + row) -> rank of its package:
    the team's place among the teams with a licensed kit, nations first.

    A team counts as licensed while any of its four kits is: a patch that
    draws the player kits from colours leaves the goalkeepers' licensed.
    """
    table = reader.kits()
    teams = [(NATIONAL_SLOT_BASE + row, kits) for row, kits in enumerate(table.nationals)]
    teams += list(enumerate(table.clubs))
    ranks: dict[int, int] = {}
    for slot, kits in teams:
        if not all(kit.parametric for kit in kits):
            ranks[slot] = len(ranks)
    return ranks


def _package_entry(reader: RomReader, slot: int, which: int) -> int:
    ranks = licensed_slots(reader)
    if slot not in ranks:
        raise RomError(
            f"Slot {slot} has no kit texture: its kits are parametric, drawn from colours"
        )
    return kit_entry(ranks[slot], which)


def is_texture_png(png: bytes) -> bool:
    """True for a PNG whose header says 512x256: the kit texture's own size."""
    return (
        len(png) >= 24
        and png[:8] == b"\x89PNG\r\n\x1a\n"
        and struct.unpack(">II", png[16:24]) == (512, 256)
    )


def read_kit_png(reader: RomReader, slot: int, which: int) -> bytes:
    """The 512x256 texture of one licensed kit, as PNG."""
    index = _package_entry(reader, slot, which)
    return KitPackage.parse(decrypt_entry(reader.read(TEXT_AFS, index))).to_png()


def write_kit_png(writer: RomWriter, slot: int, which: int, png: bytes) -> int:
    """Paint `png` into one licensed kit's package and put it back, re-encrypted
    with the entry's own keys, its stream padded to the original's size
    (`deflate_to`); returns the colours it took.

    The entry's size is never changed (`RomWriter._write_entry`), so the
    rebuilt stream has to fit the original's: the palette is cut down until
    it does, or `RomError` is raised. Checked in PCSX2: a goalkeeper kit so
    written is what the keeper wears.
    """
    index = _package_entry(writer.reader, slot, which)
    entry = writer.reader.read(TEXT_AFS, index)
    package = KitPackage.parse(decrypt_entry(entry))
    size = struct.unpack_from("<I", entry, 4)[0]
    why = ""
    for colours in PALETTES:
        try:
            stream = deflate_to(package.paint(png, colours).to_bytes(), size)
        except RomError as exc:
            why = str(exc)
            continue
        writer._write_entry(TEXT_AFS, index, encrypt_entry(entry, stream))
        return colours
    raise RomError(
        f"Kit texture for slot {slot} {why} even with {PALETTES[-1]} colours; use a flatter picture"
    )


def template_png() -> bytes:
    """The 512x256 layout, each region outlined in red and named, to paint over."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", LOD0_SIZE, (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    for name, (x, y, w, h) in REGIONS.items():
        draw.rectangle((x, y, x + w - 1, y + h - 1), outline=(255, 0, 0, 255))
        draw.text((x + 3, y + 2), name, fill=(255, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
