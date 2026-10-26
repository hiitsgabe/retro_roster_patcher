"""Team names: the 64x8 pictures the menus show a team's name with.

Asset 19 is a 128-pixel-wide I4 sheet. Team k's name is rows 169+8k to
176+8k (100 nations, then the all-star sides), drawn twice: in the left and
the right 64 pixels. The game's italic lettering is not a font anywhere on
the cartridge, so a new name is set in the 8x8 font of asset 0 (glyphs
trimmed to their ink, one pixel apart, sheared like the originals) and
squeezed when it would run past 62 pixels.
"""

from __future__ import annotations

from functools import lru_cache

from . import rom as R

NAMES_ASSET = 19
FONT_ASSET = 0
SHEET_W = 128
FIRST_ROW = 169
CELL_W, CELL_H = 64, 8
_FONT_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _pixels(data: bytes, width: int) -> list[list[int]]:
    rows = []
    for r in range(len(data) * 2 // width):
        line = data[r * width // 2 : (r + 1) * width // 2]
        rows.append([v for b in line for v in (b >> 4, b & 15)])
    return rows


@lru_cache(maxsize=4)
def _glyphs(font: bytes) -> dict[str, list[list[int]]]:
    px = _pixels(font, SHEET_W)
    out: dict[str, list[list[int]]] = {}
    for i, ch in enumerate(_FONT_CHARS):
        gx, gy = (i % 16) * 8, (i // 16) * 8
        cell = [px[gy + y][gx : gx + 8] if gy + y < len(px) else [0] * 8 for y in range(8)]
        cols = [x for x in range(8) if any(cell[y][x] for y in range(8))]
        if cols:
            cell = [row[cols[0] : cols[-1] + 1] for row in cell]
        out[ch] = cell
    out[" "] = [[0, 0, 0]] * 8
    return out


def render(text: str, font: bytes) -> list[list[int]]:
    """`text` as 8 rows of 64 I4 values: Pillow's built-in face, bold and
    sheared like the game's italics; the 8x8 font of asset 0 where Pillow
    has no scalable face."""
    from ...core.text import to_ascii

    text = to_ascii(text)
    try:
        return _render_face(text)
    except (ImportError, OSError, TypeError, AttributeError):
        return _render_pixels(text, font)


def _render_face(text: str, cell_w: int = CELL_W, cell_h: int = CELL_H) -> list[list[int]]:
    from PIL import Image, ImageDraw, ImageFont

    CELL_W, CELL_H = cell_w, cell_h  # noqa: N806 - the cell this call fills
    scale = 4
    face = ImageFont.load_default(size=7 * scale * CELL_H // 8)
    cap_top = face.getbbox("H")[1]
    canvas = Image.new("L", (CELL_W * scale * 3, CELL_H * scale), 0)
    draw = ImageDraw.Draw(canvas)
    x = 0
    for ch in text:
        # Cap height on rows 1-5 of the cell, as the originals sit.
        draw.text(
            (x, scale * 1 - cap_top), ch, font=face, fill=255, stroke_width=1, stroke_fill=255
        )
        x += round(face.getlength(ch)) + scale  # one pixel of tracking
    width = max(1, x - scale + scale * 2)
    sheared = canvas.transform(
        canvas.size,
        Image.Transform.AFFINE,
        (1, 0.3, -0.3 * canvas.height, 0, 1, 0),
        resample=Image.Resampling.BILINEAR,
    ).crop((0, 0, min(width, canvas.width), canvas.height))
    target = min(CELL_W - 2, round(sheared.width / scale))
    small = sheared.resize((max(1, target), CELL_H), Image.Resampling.BOX)
    out = [[0] * CELL_W for _ in range(CELL_H)]
    x0 = (CELL_W - small.width) // 2
    for y in range(CELL_H):
        for x in range(small.width):
            v = int(small.getpixel((x, y)))  # type: ignore[arg-type]
            out[y][x0 + x] = min(15, round(v * 1.4 / 17))
    return out


def _render_pixels(text: str, font: bytes) -> list[list[int]]:
    glyphs = _glyphs(font)
    rows: list[list[int]] = [[] for _ in range(8)]
    for ch in text:
        g = glyphs.get(ch) or glyphs.get(ch.upper())
        if g is None:
            continue
        for y in range(8):
            rows[y] += g[y] + [0]
    width = max(len(r) for r in rows) if rows[0] else 0
    usable = CELL_W - 4
    if width > usable:  # squeeze to fit
        rows = [[r[int(x * width / usable)] for x in range(usable)] for r in rows]
        width = usable
    left = (CELL_W - width) // 2
    out = [[0] * CELL_W for _ in range(CELL_H)]
    for y in range(7):
        for x, v in enumerate(rows[y]):
            tx = left + x + (6 - y) // 3
            if v and 0 <= tx < CELL_W:
                out[y + 1][tx] = 15
    return out


#: The region labels team select heads each page with, 16 rows tall:
#: (first row, first column, width). Europe's and Asia's page letters sit
#: right of their word and stay.
REGIONS = {
    "Europe": (1010, 0, 46),
    "Africa": (1026, 0, 64),
    "CONCACAF": (1026, 64, 64),
    "South America": (1042, 0, 64),
    "Asia": (1042, 64, 36),
    "Oceania": (1058, 0, 64),
}


VISIBLE = 52


def write_regions(image: bytearray, space: R.AssetSpace, labels: dict[str, str]) -> None:
    """Rename team select's region labels, {region: text} (`REGIONS` keys)."""
    if not labels:
        return
    asset = R.assets(image)[NAMES_ASSET]
    sheet = bytearray(R.read_asset(image, asset))
    for region, text in labels.items():
        row0, col0, width = REGIONS[region]
        # Team select shows a 64-pixel cell's first 52 or so: squeeze the
        # name into that, centred there.
        cell = _render_face(text.upper(), min(width, VISIBLE) - min(width, VISIBLE) % 2, 16)
        cell = [row + [0] * (width - len(row)) for row in cell]
        for y in range(16):
            for x in range(0, width - width % 2, 2):
                sheet[(row0 + y) * SHEET_W // 2 + (col0 + x) // 2] = (
                    cell[y][x] << 4 | cell[y][x + 1]
                )
    space.put(asset, bytes(sheet))


def write(image: bytearray, space: R.AssetSpace, names: dict[int, str]) -> None:
    """Set the name of each team in `names` ({team id: text}); "" blanks it."""
    if not names:
        return
    assets = R.assets(image)
    font = R.read_asset(image, assets[FONT_ASSET])
    sheet = bytearray(R.read_asset(image, assets[NAMES_ASSET]))
    for team, text in names.items():
        cell = render(text, font) if text else [[0] * CELL_W for _ in range(CELL_H)]
        for y in range(CELL_H):
            row = FIRST_ROW + team * CELL_H + y
            packed = bytes(cell[y][x] << 4 | cell[y][x + 1] for x in range(0, CELL_W, 2))
            o = row * SHEET_W // 2
            sheet[o : o + 32] = packed
            sheet[o + 32 : o + 64] = packed
    space.put(assets[NAMES_ASSET], bytes(sheet))
