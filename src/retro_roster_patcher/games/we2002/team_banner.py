"""The team names on the pre-match VS banner: `T_NAME.BIN` (issue #13).

The banner does not print the name tables the patcher writes; it shows a
picture of each team's name, in katakana. `T_NAME.BIN` is a 2D data file of 28
image records, 128x128 pixels at 4 bits, in team order: record `r` holds teams
`4r .. 4r+3`, one 32-row band each, the name drawn in its top 16 rows (0..62
the national slots, 63..94 the Master League clubs, then stadium names).

A patched team gets its own name drawn into its band: white letters with a
dark shadow below right, slanted like the game's lettering, squeezed to fit
the band's width.
"""

from __future__ import annotations

from ...core.errors import RomError
from .flag_atlas import (
    KIND_IMAGE,
    AtlasReport,
    BlockEdit,
    lz_compress,
    read_data_file,
    rewrite_blocks,
)

FILE = "T_NAME.BIN"
WIDTH, BAND, ROWS = 128, 32, 16  # pixels; a band per team, its name in the top ROWS
TEAMS = 95  # 63 national slots + 32 Master League clubs; the bands after are stadiums
_TEXT, _SHADOW = 15, 3  # palette entries the disc's own lettering uses for face and shadow
_FIT = WIDTH - 6  # the disc's names end a few pixels short of the band's edge
_SHEAR = 0.25  # the game's italic slant


def render_name(text: str) -> list[list[int]]:
    """`text` as a `ROWS` x `WIDTH` grid of 4-bit palette indices (0 = clear)."""
    from PIL import Image, ImageDraw, ImageFont

    scale = 4
    face = ImageFont.load_default(size=12 * scale)
    probe = Image.new("L", (1, 1))
    left, top, right, _bottom = ImageDraw.Draw(probe).textbbox(
        (0, 0), text, font=face, stroke_width=scale // 2
    )
    big = Image.new("L", (int(right - left) + 4 * scale, 14 * scale), 0)
    ImageDraw.Draw(big).text(
        (2 * scale - left, scale - top),
        text,
        font=face,
        fill=255,
        stroke_width=scale // 2,
        stroke_fill=255,
    )
    big = big.transform(
        (big.width + int(big.height * _SHEAR), big.height),
        Image.Transform.AFFINE,
        (1, _SHEAR, -big.height * _SHEAR, 0, 1, 0),
        resample=Image.Resampling.BILINEAR,
    )
    ink = big.getbbox()
    if ink is not None:
        big = big.crop(ink)
    tall = ROWS - 4  # the disc's letters are 12 rows, from row 1
    width = min(_FIT - 1, max(1, round(big.width * tall / big.height)))
    small = big.resize((width, tall), Image.Resampling.BOX)
    data = small.tobytes()
    mask = [[data[y * width + x] >= 110 for x in range(width)] for y in range(tall)]
    grid = [[0] * WIDTH for _ in range(ROWS)]
    for y, row in enumerate(mask):
        for x, on in enumerate(row):
            if on:
                grid[y + 2][x + 1] = _SHADOW
    for y, row in enumerate(mask):
        for x, on in enumerate(row):
            if on:
                grid[y + 1][x] = _TEXT
    return grid


def _band(block: bytes, row: int, grid: list[list[int]] | None) -> bytes:
    """`block` (a 128x128 4-bit record) with band `row` replaced by `grid`, or cleared."""
    out = bytearray(block)
    pitch = WIDTH // 2
    for y in range(BAND):
        line = grid[y] if grid is not None and y < len(grid) else [0] * WIDTH
        packed = bytes(line[x] | line[x + 1] << 4 for x in range(0, WIDTH, 2))
        start = (row * BAND + y) * pitch
        out[start : start + pitch] = packed
    return bytes(out)


def banner_edits(image_path: str, names: dict[int, str]) -> list[BlockEdit]:
    """The block edits that draw `names` (team index -> text; "" clears) into `T_NAME.BIN`."""
    from .flag_atlas import lz_decompress

    parsed, _entry = read_data_file(image_path, FILE)
    records = [r for r in parsed.records() if r.kind & 0xFF == KIND_IMAGE]
    edits = []
    for index, rec in enumerate(records):
        teams = {i: names[i] for i in range(4 * index, 4 * index + 4) if i in names and i < TEAMS}
        if not teams:
            continue
        original, _ = lz_decompress(parsed.data, rec.ptr - parsed.base)
        block = original
        for team, text in teams.items():
            block = _band(block, team - 4 * index, render_name(text) if text else None)
        edits.append(BlockEdit(original, lz_compress(block), (FILE,), at=(rec.x, rec.y)))
    return edits


def write_banner_names(image_path: str, names: dict[int, str]) -> AtlasReport | None:
    """Draw `names` (team index -> text) onto the VS banner's name pictures.

    None, with nothing written, for an image without a readable `T_NAME.BIN` (no ISO file
    system, say): there is no banner to draw on.
    """
    try:
        edits = banner_edits(image_path, names)
    except RomError:
        return None
    return rewrite_blocks(image_path, edits)
