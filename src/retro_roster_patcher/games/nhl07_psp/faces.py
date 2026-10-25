"""Player faces: the 3D head's face textures in `GAMEDATA/FACES.VIV`.

The archive is a BIGF of RefPack-compressed SHPM files named `FACE<ART_>.msh`,
one 64x64 8-bit face photo, unwrapped, per bio `ART_` (the same id that names
the bio's portrait), 1,528 of them on the retail disc; a bio whose `ART_` has
none is drawn with `FACE9999.msh`, a pale generic face. The bio's `SKIN` draws
nothing (checked in PPSSPP on 2026-10-24 and 2026-10-25: the Player Editor's
head followed a face written over `FACE9999.msh`, not `SKIN`). So a patched
player keeps the 2006 occupant's face unless his `FACE<ART_>.msh` is
rewritten: `repack_faces` writes there the generic face, toned (`toned_face`)
to the cheek colour the disc's own faces have for his `SKIN` (`FACE_TONES`).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import TYPE_CHECKING

from ...formats import shpm
from ...formats.ea_tdb import (
    EaTdbError,
    bigf_build,
    bigf_parse,
    refpack_compress,
    refpack_decompress,
)

if TYPE_CHECKING:
    from PIL import Image

FACES_PATH = "PSP_GAME/USRDIR/GAMEDATA/FACES.VIV"
FACES_BH_NAME = "faces.bh"
GENERIC_FACE = 9999
#: The cheeks of a 64x64 face texture: columns 22-41, rows 26-39.
CHEEKS = (22, 26, 42, 40)
#: The cheek colour of the disc's own faces by `SKIN` (the mean over up to 40
#: faces each): 1 and 2 pale, 3 olive, 6 brown, 5 the darkest.
FACE_TONES: dict[int, tuple[int, int, int]] = {
    1: (191, 147, 123),
    2: (184, 141, 120),
    3: (167, 118, 96),
    6: (145, 102, 82),
    5: (102, 71, 56),
}
SILHOUETTE_ART = 0


def face_name(art: int) -> str:
    return f"FACE{art}.msh"


def face_targets(art_of: Mapping[int, object], skins: Mapping[int, int]) -> dict[int, int]:
    """Which faces to draw: `ART_` -> `SKIN`, for each written bio (`skins`, by
    bio record) whose `ART_` (`art_of`, every live bio's) no other bio shares,
    as for portraits: the disc gives one id to dozens of European players.
    0 is never drawn."""
    uses = Counter(art_of.values())
    targets = {}
    for bio, skin in skins.items():
        art = art_of.get(bio)
        if isinstance(art, int) and art != SILHOUETTE_ART and uses[art] == 1:
            targets[art] = skin
    return targets


def read_face(archive: bytes, art: int) -> Image.Image | None:
    """The face texture of `ART_` as RGBA, or None when there is none to read."""
    entry = next((e for e in bigf_parse(archive) if e.name == face_name(art)), None)
    if entry is None:
        return None
    try:
        parsed = shpm.parse(refpack_decompress(archive[entry.offset : entry.offset + entry.size]))
        return shpm.decode_shape(parsed.shapes[0])[0].convert("RGBA")
    except (EaTdbError, shpm.ShpmError, IndexError, ValueError):
        return None


def toned_face(generic: Image.Image, tone: tuple[int, int, int]) -> Image.Image:
    """`generic` with each channel scaled so its cheeks average `tone`: the
    shading, eyes and hair keep their contrast."""
    from PIL import Image as PILImage
    from PIL import ImageStat

    cheeks = ImageStat.Stat(generic.convert("RGB").crop(CHEEKS)).mean
    gains = [tone[i] / max(1.0, cheeks[i]) for i in range(3)]
    channels = generic.convert("RGBA").split()
    scaled = [
        band.point(lambda v, g=gain: min(255, round(v * g)))
        for band, gain in zip(channels[:3], gains, strict=True)
    ]
    return PILImage.merge("RGBA", (*scaled, channels[3]))


def _packed(compressed: bytes, image: Image.Image) -> bytes | None:
    """One `FACE<n>.msh` with its shape replaced, recompressed; None to skip."""
    try:
        parsed = shpm.parse(refpack_decompress(compressed))
        shape = parsed.shapes[0]
        if shape.blocks[0][0] != shpm.PIX8:
            return None
        shpm.encode_shape(shape, image)
    except (EaTdbError, shpm.ShpmError, IndexError, ValueError):
        return None
    return refpack_compress(shpm.build(parsed))


def repack_faces(archive: bytes, images: Mapping[int, Image.Image]) -> tuple[bytes, int]:
    """`archive` with the face of each `ART_` in `images` replaced.

    Returns the new archive and how many faces went in. A face that fits its
    entry's slot is written where it sits, so the directory, and every cached
    copy of it, stays valid; otherwise the archive is rebuilt compactly. An
    `ART_` without a face of its own is skipped: it is drawn with the generic
    face, which every such bio shares.
    """
    entries = bigf_parse(archive)
    by_name = {e.name: e for e in entries}
    replaced: dict[str, bytes] = {}
    for art, image in images.items():
        entry = by_name.get(face_name(art))
        if entry is None:
            continue
        data = _packed(archive[entry.offset : entry.offset + entry.size], image)
        if data is not None:
            replaced[entry.name] = data
    if not replaced:
        return archive, 0
    if all(len(data) <= by_name[name].size for name, data in replaced.items()):
        out = bytearray(archive)
        for name, data in replaced.items():
            entry = by_name[name]
            out[entry.offset : entry.offset + entry.size] = data.ljust(entry.size, b"\0")
        return bytes(out), len(replaced)
    contents = {e.name: archive[e.offset : e.offset + e.size] for e in entries}
    contents.update(replaced)
    return bigf_build(entries, contents), len(replaced)
