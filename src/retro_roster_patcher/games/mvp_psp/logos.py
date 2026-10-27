"""Team logos: `FRONTEND/LOGOS.BIG` (and its twin `LOGOS_BE.BIG`).

Both files are one `C0 FB` archive: the two magic bytes, the header's length
(less 4) and the file count (big-endian words), then per file a 3-byte offset, a 3-byte
size and a NUL-terminated name. The files are `lNNN.msh`, RefPack-compressed
128x96 8-bit SHPM pictures; a club shows `l{team_artid - 1:03d}` (`team`
column 6).

`edits` draws each club's crest over its picture and rebuilds the archive, in
place and padded to the file's own length so the disc's directory stays as it
is; when the rebuilt archive would not fit, the largest new pictures give way
to the old ones until it does.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Mapping
from typing import BinaryIO

from ...formats import iso9660, shpm
from ...formats.ea_tdb import refpack_compress, refpack_decompress

PATHS = (
    ("PSP_GAME", "USRDIR", "DATA", "FRONTEND", "LOGOS.BIG"),
    ("PSP_GAME", "USRDIR", "DATA", "FRONTEND", "LOGOS_BE.BIG"),
)
MAGIC = b"\xc0\xfb"
WIDTH, HEIGHT = 128, 96


def parse(archive: bytes) -> list[tuple[str, bytes]]:
    """The archive's files as (name, stored bytes), in order."""
    count = int.from_bytes(archive[4:6], "big")
    at, out = 6, []
    for _ in range(count):
        offset = int.from_bytes(archive[at : at + 3], "big")
        size = int.from_bytes(archive[at + 3 : at + 6], "big")
        end = archive.index(b"\0", at + 6)
        out.append((archive[at + 6 : end].decode("ascii"), archive[offset : offset + size]))
        at = end + 1
    return out


def build(files: list[tuple[str, bytes]]) -> bytes:
    """An archive of (name, stored bytes), each file word-aligned."""
    header_len = 6 + sum(6 + len(name) + 1 for name, _ in files)
    at = (header_len + 3) & ~3  # the first file, word-aligned
    table, body = bytearray(), bytearray()
    for n, (name, data) in enumerate(files):
        table += (at + len(body)).to_bytes(3, "big") + len(data).to_bytes(3, "big")
        table += name.encode("ascii") + b"\0"
        body += data + (bytes(-len(data) % 4) if n + 1 < len(files) else b"")
    # The length word counts the header after the magic and itself.
    head = MAGIC + (header_len - 4).to_bytes(2, "big") + len(files).to_bytes(2, "big") + table
    return bytes(head + bytes(at - len(head)) + body)


def picture(png: bytes, old: bytes) -> bytes:
    """`png` (a crest) centred on the old picture's 128x96 SHPM, compressed."""
    from PIL import Image

    art = shpm.parse(refpack_decompress(old))
    crest = Image.open(io.BytesIO(png)).convert("RGBA")
    crest.thumbnail((WIDTH - 8, HEIGHT - 8), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    canvas.alpha_composite(crest, ((WIDTH - crest.width) // 2, (HEIGHT - crest.height) // 2))
    shpm.encode_shape(art.shapes[0], canvas)
    return refpack_compress(shpm.build(art))


def _files(f: BinaryIO) -> list[tuple[int, int]]:
    """(byte offset, length) of each copy on the disc."""
    root = iso9660.read_root(f)
    out = []
    for path in PATHS:
        folder = iso9660.walk(f, root, path[:-1]) if root else None
        entry = iso9660.find_entry(f, folder, path[-1]) if folder else None
        if entry is not None:
            out.append((entry.extent.lba * iso9660.SECTOR_SIZE, entry.extent.size))
    return out


def edits(
    f: BinaryIO, crests: Mapping[int, bytes], status: Callable[[str], None]
) -> list[tuple[int, bytes]]:
    """(disc offset, bytes) writes drawing each `{team_artid: crest png}`."""
    out: list[tuple[int, bytes]] = []
    for offset, length in _files(f):
        f.seek(offset)
        old = f.read(length)
        if old[:2] != MAGIC:
            continue
        files = parse(old)
        new = {}
        for artid, png in crests.items():
            name = f"l{artid - 1:03d}.msh"
            for i, (n, data) in enumerate(files):
                if n == name:
                    try:
                        new[i] = picture(png, data)
                    except Exception:  # noqa: BLE001 - a bad crest keeps the old logo
                        pass
        while True:
            archive = build([(n, new.get(i, d)) for i, (n, d) in enumerate(files)])
            if len(archive) <= length or not new:
                break
            del new[max(new, key=lambda i: len(new[i]))]
        if len(archive) <= length:
            out.append((offset, archive + bytes(length - len(archive))))
            drawn = len(new)
    if out:
        status(f"Team logos drawn: {drawn} of {len(crests)}")
    return out
