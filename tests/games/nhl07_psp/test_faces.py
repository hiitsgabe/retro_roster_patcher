"""Face textures in `GAMEDATA/FACES.VIV`, on hand-built archives.

On the retail disc (2026-10-25, PPSSPP) the 3D head the Player Editor draws
followed a face texture written over `FACE9999.msh`, the face of every bio
without one of its own, while the bios' `SKIN` changed nothing.
"""

from __future__ import annotations

import random
import struct

import pytest

from retro_roster_patcher.formats.ea_tdb import BigfEntry, bigf_build, refpack_compress
from retro_roster_patcher.games.nhl07_psp import faces
from tests.fixtures import synthetic_shpm as synthetic

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

SIZE = 64


def _face_msh(seed: int) -> bytes:
    """A face as big as the disc's (about 6 KB compressed): photo-like noise."""
    rng = random.Random(seed)
    indices = bytes(rng.randrange(256) for _ in range(SIZE * SIZE))
    return refpack_compress(synthetic.shpm_file(synthetic.eight_bit_shape(SIZE, SIZE, indices)))


def _archive(*arts: int) -> bytes:
    contents = {faces.face_name(art): _face_msh(art) for art in arts}
    return bigf_build([BigfEntry(n, 0, 0) for n in contents], contents)


def _directory(archive: bytes) -> bytes:
    return archive[: struct.unpack_from(">I", archive, 12)[0]]


def _cheeks(image: Image.Image) -> tuple[float, ...]:
    from PIL import ImageStat

    return tuple(ImageStat.Stat(image.convert("RGB").crop(faces.CHEEKS)).mean)


def _generic() -> Image.Image:
    image = Image.new("RGBA", (SIZE, SIZE), (40, 30, 20, 255))  # hair and shadow
    x0, y0, x1, y1 = faces.CHEEKS
    image.paste((190, 150, 125, 255), (x0 - 4, y0 - 4, x1 + 4, y1 + 4))
    return image


def test_a_toned_face_has_the_tones_cheeks_and_keeps_its_shading():
    generic = _generic()
    for target in faces.FACE_TONES.values():
        toned = faces.toned_face(generic, target)
        assert all(abs(a - b) <= 2 for a, b in zip(_cheeks(toned), target, strict=True))
        # The dark parts stay darker than the cheeks: shading is scaled, not flattened.
        assert sum(toned.getpixel((0, 0))[:3]) < sum(toned.getpixel((32, 32))[:3])
        assert toned.getpixel((0, 0))[3] == 255


def test_the_tones_run_from_the_lightest_to_the_darkest_shade_the_disc_draws():
    """SKIN 5 is the darkest on the disc (Laraque, Brashear), 6 next (Iginla)."""
    lightness = [sum(faces.FACE_TONES[s]) for s in (1, 2, 3, 6, 5)]
    assert lightness == sorted(lightness, reverse=True)


def test_repack_replaces_the_named_faces_and_leaves_the_rest_byte_for_byte():
    archive = _archive(1, 2, faces.GENERIC_FACE)
    toned = faces.toned_face(_generic(), faces.FACE_TONES[5])
    new, count = faces.repack_faces(archive, {1: toned})
    assert count == 1
    assert _directory(new) == _directory(archive)  # every face fits its slot
    image = faces.read_face(new, 1)
    assert image is not None
    assert all(abs(a - b) <= 6 for a, b in zip(_cheeks(image), faces.FACE_TONES[5], strict=True))
    for art in (2, faces.GENERIC_FACE):
        assert faces.read_face(new, art).tobytes() == faces.read_face(archive, art).tobytes()


def test_a_face_the_archive_lacks_is_skipped():
    archive = _archive(1)
    new, count = faces.repack_faces(archive, {7: _generic()})
    assert (new, count) == (archive, 0)


def test_only_a_bios_own_art_id_gets_a_face():
    """Like portraits: an `ART_` several bios share is left alone."""
    art_of = {10: 100, 11: 101, 12: 101, 13: 0}
    tones = {10: 5, 11: 6, 13: 1}
    assert faces.face_targets(art_of, tones) == {100: 5}
