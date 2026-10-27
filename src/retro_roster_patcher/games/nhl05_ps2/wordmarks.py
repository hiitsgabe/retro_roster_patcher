"""The wordmarks of `FE/TLOGOAPT.VIV`: a club's city in the front end's lettering.

Each is a 256x32 plain shape (`T_<abbr>.big` holding `T_<abbr>.ssh`), the
city in small capitals, light grey, centred. A new one is set from letters cut
out of the wordmarks the disc has (`SOURCES`): the glyphs are the runs of
opaque columns of each, read off against its text. The first letter of a
wordmark is a full capital; one the disc has no full capital for is the small
capital scaled up to that height.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from ...formats import ssh
from ...formats.ea_tdb import EaTdbError, bigf_parse, refpack_decompress

if TYPE_CHECKING:
    from PIL import Image

SIZE = (256, 32)
GAP = 3
#: (member, its text, whether its first two letters touch and must be cut apart).
SOURCES = (
    ("T_PHO", "PHOENIX", False),
    ("T_PHI", "PHILADELPHIA", False),
    ("T_DET", "DETROIT", False),
    ("T_WSH", "WASHINGTON", True),
    ("T_STL", "ST. LOUIS", False),
)

Atlas = Mapping[tuple[str, bool], "Image.Image"]


def picture(archive: bytes, member: str) -> Image.Image | None:
    """The shape inside `<member>.big`, or None when the archive lacks it."""
    entry = next(
        (e for e in bigf_parse(archive) if e.name.lower() == f"{member}.big".lower()), None
    )
    if entry is None:
        return None
    try:
        inner = refpack_decompress(archive[entry.offset : entry.offset + entry.size])
        shape = next(e for e in bigf_parse(inner) if e.name.lower() == f"{member}.ssh".lower())
        return ssh.decode(inner[shape.offset : shape.offset + shape.size])
    except (EaTdbError, ssh.SshError, StopIteration, ValueError):
        return None


def _runs(image: Image.Image) -> tuple[list[int], list[tuple[int, int]]]:
    """The count of opaque pixels per column, and the runs of columns with any."""
    alpha = image.getchannel("A").tobytes()
    w, h = image.size
    profile = [sum(1 for y in range(h) if alpha[y * w + x] >= 128) for x in range(w)]
    runs, start = [], None
    for x, count in enumerate(profile + [0]):
        if count and start is None:
            start = x
        elif not count and start is not None:
            runs.append((start, x))
            start = None
    return profile, runs


def letters(archive: bytes) -> dict[tuple[str, bool], Image.Image]:
    """`{(letter, full capital): glyph}` cut from the disc's wordmarks."""
    atlas: dict[tuple[str, bool], Image.Image] = {}
    for member, text, joined in SOURCES:
        image = picture(archive, member)
        if image is None:
            continue
        profile, runs = _runs(image)
        if joined and runs:
            # Cut the first run at its thinnest column away from either edge.
            start, end = runs[0]
            cut = min(range(start + 10, end - 10), key=lambda x: (profile[x], x))
            runs = [(start, cut), (cut, end), *runs[1:]]
        wanted = [c for c in text if c != " "]
        if len(runs) != len(wanted):
            continue
        for i, (letter, (start, end)) in enumerate(zip(wanted, runs, strict=True)):
            atlas.setdefault((letter, i == 0), image.crop((start, 0, end, image.height)))
    return atlas


def _rows(glyph: Image.Image) -> tuple[int, int]:
    box = glyph.getchannel("A").point(lambda v: 255 if v >= 128 else 0).getbbox()
    return (box[1], box[3]) if box else (0, glyph.height)


def render(text: str, atlas: Atlas) -> Image.Image | None:
    """`text` as a wordmark, or None when the atlas lacks one of its letters."""
    from PIL import Image

    glyphs = []
    for i, letter in enumerate(text.upper()):
        glyph = atlas.get((letter, i == 0))
        if glyph is None and i == 0 and (letter, False) in atlas:
            small = atlas[(letter, False)]
            capital = next((g for (_, full), g in atlas.items() if full), None)
            if capital is None:
                return None
            top, bottom = _rows(capital)
            s_top, s_bottom = _rows(small)
            scale = (bottom - top) / max(1, s_bottom - s_top)
            grown = small.resize(
                (round(small.width * scale), round(small.height * scale)), Image.Resampling.LANCZOS
            )
            glyph = Image.new("RGBA", (grown.width, SIZE[1]), (0, 0, 0, 0))
            glyph.alpha_composite(grown, (0, top - _rows(grown)[0]))
        if glyph is None:
            return None
        glyphs.append(glyph)
    width = sum(g.width for g in glyphs) + GAP * (len(glyphs) - 1)
    if width > SIZE[0]:
        return None
    out = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    x = (SIZE[0] - width) // 2
    for glyph in glyphs:
        out.alpha_composite(glyph, (x, 0))
        x += glyph.width + GAP
    return out
