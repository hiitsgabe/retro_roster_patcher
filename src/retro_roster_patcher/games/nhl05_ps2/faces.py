"""Player faces: the 3D head's face texture in `GAMEDATA/FACES.VIV`.

The archive is a BIGF of RefPack-compressed members, two per face: `FACE<n>.ebo`,
the head mesh, and `FACE<n>.ssh`, its textures, both named by the bio's
`ART_` (the id that names its portrait too). The shape file holds `FAC0`, a
16x16 4-bit thumbnail, and `FAC1`, the face: a 128x128 picture of the head
unwrapped, in the tight packing (`formats.ssh`), 1,477 of them on the retail
disc, plus `FACE9999`, a pale generic face, and `FACE8999`, a woman's. The
game draws the face the bio's `ART_` names, so a patched player keeps the
2004 occupant's face unless his `FACE<n>.ssh` is rewritten: `repack` writes
there the generic face toned (`toned_face`) to the cheek colour the disc's
own faces have for his `SKIN` (`FACE_TONES`), and its thumbnail. The mesh
stays the occupant's. A face is written where its member sits, with the most
colours that let the member keep its size.
"""

from __future__ import annotations

import struct
from collections import Counter
from collections.abc import Mapping
from typing import TYPE_CHECKING

from ...formats import ssh
from ...formats.ea_tdb import (
    EaTdbError,
    bigf_parse,
    refpack_compress,
    refpack_decompress,
)

if TYPE_CHECKING:
    from PIL import Image

FACES_PATH = "GAMEDATA/FACES.VIV"
GENERIC_FACE = 9999
#: The cheekbones of a 128x128 face texture: columns 34-53 and 74-93, rows 60-75.
CHEEKS = ((34, 60, 54, 76), (74, 60, 94, 76))
#: The cheek colour of the disc's own faces by `SKIN` (the mean over its
#: faces): 1 and 2 pale, 3 olive, 4 East Asian, 6 brown, 5 the darkest.
FACE_TONES: dict[int, tuple[int, int, int]] = {
    1: (193, 150, 122),
    2: (177, 135, 112),
    3: (158, 113, 89),
    4: (177, 135, 107),
    6: (148, 103, 82),
    5: (101, 72, 57),
}
SILHOUETTE_ART = 0
COLOUR_STEPS = (256, 128, 64)
THUMBNAIL = "FAC0"
FACE = "FAC1"


def face_name(art: int) -> str:
    return f"FACE{art}.ssh"


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
        return ssh.decode(
            refpack_decompress(archive[entry.offset : entry.offset + entry.size]), FACE
        )
    except (EaTdbError, ssh.SshError, IndexError, ValueError):
        return None


def toned_face(generic: Image.Image, tone: tuple[int, int, int]) -> Image.Image:
    """`generic` with each channel scaled so its cheeks average `tone`: the
    shading, eyes and hair keep their contrast."""
    from PIL import Image as PILImage
    from PIL import ImageStat

    rgb = generic.convert("RGB")
    cheeks = [ImageStat.Stat(rgb.crop(box)).mean for box in CHEEKS]
    gains = [tone[i] / max(1.0, sum(c[i] for c in cheeks) / len(cheeks)) for i in range(3)]
    channels = generic.convert("RGBA").split()
    scaled = [
        band.point(lambda v, g=gain: min(255, round(v * g)))
        for band, gain in zip(channels[:3], gains, strict=True)
    ]
    return PILImage.merge("RGBA", (*scaled, channels[3]))


def _thumbnail(data: bytes, picture: Image.Image) -> bytes:
    """`data` with `picture` drawn into its `THUMBNAIL` shape (16x16, 4-bit,
    16 colours), when it has one."""
    from PIL import Image

    found = next((o for name, o, _ in ssh.shapes(data) if name == THUMBNAIL), None)
    if found is None:
        return data
    block = found
    width, height = struct.unpack_from("<II", data, block + 0x18)
    pal = block + (struct.unpack_from("<I", data, block + 4)[0] & 0xFFFFFF) + 0x20
    small = picture.convert("RGB").resize((width, height), Image.Resampling.BOX)
    reduced = small.quantize(16, method=Image.Quantize.MEDIANCUT)
    flat = reduced.getpalette() or []
    pixels = reduced.tobytes()
    out = bytearray(data)
    for i in range(0, width * height, 2):
        out[block + 0x20 + i // 2] = (pixels[i] & 15) | ((pixels[i + 1] & 15) << 4)
    for i in range(16):
        out[pal + 4 * i : pal + 4 * i + 4] = bytes([*flat[3 * i : 3 * i + 3], ssh.OPAQUE])
    return bytes(out)


def _member(data: bytes, image: Image.Image, colours: int) -> bytes:
    """`data`, a plain `FACE<n>.ssh`, with `image` as its face and thumbnail."""
    return ssh.encode(_thumbnail(data, image), image, colours, FACE)


def repack(
    archive: bytes,
    skins: Mapping[int, int],
    tones: Mapping[int, tuple[int, int, int]] = FACE_TONES,
    generic: int = GENERIC_FACE,
) -> tuple[bytes, int]:
    """`archive` with each `{ART_: SKIN}`'s face replaced, where its member
    sits, by the generic face toned to the shade, and how many went in.

    Every face of a shade is the same member, so each shade is encoded once,
    at each palette size in turn, and a face takes the richest that fits its
    member. An `ART_` without a face of its own, or whose member is too
    small for any, is skipped.
    """
    by_name = {e.name: e for e in bigf_parse(archive)}
    source = by_name.get(face_name(generic))
    if source is None:
        return archive, 0
    try:
        template = refpack_decompress(archive[source.offset : source.offset + source.size])
        plain = ssh.decode(template, FACE)
    except (EaTdbError, ssh.SshError, IndexError, ValueError):
        return archive, 0
    encoded: dict[tuple[int, int], bytes | None] = {}

    def candidate(skin: int, colours: int) -> bytes | None:
        key = (skin, colours)
        if key not in encoded:
            try:
                tone = tones.get(skin, tones[min(tones)])
                encoded[key] = refpack_compress(
                    _member(template, toned_face(plain, tone), colours), tight=True
                )
            except (EaTdbError, ssh.SshError, IndexError, ValueError):
                encoded[key] = None
        return encoded[key]

    out = bytearray(archive)
    drawn = 0
    for art, skin in skins.items():
        entry = by_name.get(face_name(art))
        if entry is None:
            continue
        for colours in COLOUR_STEPS:
            data = candidate(skin, colours)
            if data is not None and len(data) <= entry.size:
                out[entry.offset : entry.offset + entry.size] = data.ljust(entry.size, b"\0")
                drawn += 1
                break
    return bytes(out), drawn
