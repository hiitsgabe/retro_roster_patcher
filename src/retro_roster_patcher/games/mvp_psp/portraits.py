"""Player portraits: the photo the menus show of each player.

`PORTRAIT.BIG` is on the disc twice (`PATHS`), a BIGF archive of `NNNN.msh`
pictures, each a 78x96 8-bit SHPM: a head-and-shoulders photo, cap on, on a
white ground. The picture a player shows is named by his `attrib` column 7
(`playerattrib_audioid`, which also names his speech), four digits, when his
column 8 (`playerattrib_photo`) is 1; with 2 the game draws his 3D head
instead, as for its own players without a photo. `0000` is the silhouette.

A patched player takes an audio id no recorded name answers to (`speech`), so
`edits` renames the picture of his record's old id to the new one in the
archive's directory, which stays in name order, and draws his ESPN head shot over it, in
place and the same size, in both archives; without a head shot he gets the
silhouette, not the old player's face. The silhouette itself is never drawn
over.
"""

from __future__ import annotations

import io
import struct
from collections.abc import Callable, Mapping
from typing import BinaryIO

from ...formats import iso9660, shpm
from ...formats.ea_tdb import bigf_extract, bigf_parse

PATHS = (
    ("PSP_GAME", "USRDIR", "DATA", "PORTRAIT.BIG"),
    ("PSP_GAME", "USRDIR", "DATA", "FRONTEND", "PORTRAIT.BIG"),
)
SILHOUETTE = "0000.msh"
WIDTH, HEIGHT = 78, 96
#: The share of the head shot's height a portrait takes, from its top: ESPN's
#: MLB photos sit lower and smaller than the disc's, cap at the top edge.
CROP = 0.82


def name(audio_id: str) -> str:
    """The archive entry of an `attrib` column 7 value."""
    return f"{int(audio_id):04d}.msh"


def crop(png: bytes):  # -> PIL.Image.Image
    """A head shot as a portrait: 78x96 from the top centre, on white."""
    from PIL import Image

    picture = Image.open(io.BytesIO(png)).convert("RGBA")
    w, h = picture.size
    tall = round(h * CROP)
    wide = round(tall * WIDTH / HEIGHT)
    x0 = (w - wide) // 2
    piece = picture.crop((x0, 0, x0 + wide, tall)).resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    ground = Image.new("RGBA", (WIDTH, HEIGHT), (255, 255, 255, 255))
    ground.alpha_composite(piece)
    return ground


def _archives(f: BinaryIO) -> list[tuple[int, bytes]]:
    """(byte offset, contents) of each copy of `PORTRAIT.BIG` on the disc."""
    root = iso9660.read_root(f)
    out = []
    for path in PATHS:
        if root is None:
            break
        folder = iso9660.walk(f, root, path[:-1])
        entry = iso9660.find_entry(f, folder, path[-1]) if folder else None
        if entry is None:
            continue
        f.seek(entry.extent.lba * iso9660.SECTOR_SIZE)
        out.append((entry.extent.lba * iso9660.SECTOR_SIZE, f.read(entry.extent.size)))
    return out


def ids(f: BinaryIO) -> set[int]:
    """The audio ids the disc has a portrait for; the silhouette is none."""
    archives = _archives(f)
    names = [e.name for e in bigf_parse(archives[0][1])] if archives else []
    return {int(n[:4]) for n in names if n[:4].isdigit() and n == name(n[:4])} - {0}


def _directory(archive: bytes, renames: Mapping[str, str]) -> bytes:
    """The archive's directory from its first entry, `renames` applied and in
    name order: the game finds a picture only where that order puts it, so a
    renamed entry left in its old place shows no portrait."""
    entries = sorted((renames.get(e.name, e.name), e) for e in bigf_parse(archive))
    return b"".join(
        struct.pack(">II", e.offset, e.size) + n.encode("ascii") + b"\0" for n, e in entries
    )


def edits(
    f: BinaryIO,
    pictures: Mapping[str, bytes | None],
    status: Callable[[str], None],
    moves: Mapping[int, int] | None = None,
) -> list[tuple[int, bytes]]:
    """(disc offset, bytes) writes giving each `{attrib column 7: head shot or
    None}` its picture, None the silhouette, in every copy of the archive. An
    id in `moves` takes over the picture of the id it maps to, renamed to it."""
    out: list[tuple[int, bytes]] = []
    drawn = 0
    moves = moves or {}
    for start, archive in _archives(f):
        entries = {e.name: e for e in bigf_parse(archive)}
        renames: dict[str, str] = {}
        blank = bigf_extract(archive, SILHOUETTE)
        for audio_id, png in pictures.items():
            source = name(str(moves.get(int(audio_id), audio_id)))
            entry = entries.get(source)
            if entry is None or source == SILHOUETTE:
                continue
            if source != name(audio_id):
                renames[source] = name(audio_id)
            data = blank
            if png is not None:
                try:
                    art = shpm.parse(archive[entry.offset : entry.offset + entry.size])
                    shpm.encode_shape(art.shapes[0], crop(png))
                    data = shpm.build(art)
                    drawn += 1
                except Exception:  # noqa: BLE001 - a bad photo keeps the silhouette
                    data = blank
            if data is not None and len(data) == entry.size:
                out.append((start + entry.offset, data))
        if renames:
            out.append((start + 16, _directory(archive, renames)))
    status(f"Portraits drawn from head shots: {drawn // max(1, len(PATHS))}")
    return out
