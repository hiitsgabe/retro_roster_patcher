"""WE2002 TEX_nn.BIN: the per-team 3D kit texture, read, previewed and repainted.

Where it lives: TEX_nn is at disc LBA 8400 + nn*20 (a 20-sector slot), its true
size in the ISO9660 BIN directory. The executable's file table loads every TEX
at 0x800f0000 and reads 18 sectors of it (`MAX_BYTES`), so a rebuilt file must
stay under that, not under the slot. 0-62 are the national and all-star teams,
63-94 the Master League (`JERSEY_TEMPLATES` names them).

Container: the 2D data file layout `flag_atlas.parse_data_file` reads. Twelve
u32 pointers (slot 6 empty), each to a one-record list whose payload sits just
before it; kind 0x000a is an LZSS stream of an 8bpp 128x128 image, kind 0xff09
a raw 256-entry BGR555 CLUT. The order is the same in every file:

  slot  name          content
   0    home_top      home kit page, rows 0-127        (VRAM 576,256)
   9    home_bottom   home kit page, rows 128-255      (VRAM 576,384)
   1    home_clut     home outfield palette            (VRAM 0,486)
   4    home_gk_clut  home goalkeeper palette          (VRAM 0,488)
   2/10/3/5           the same four for the away kit
   7/8  flag          eight 32x32 frames of the waving flag and its palette
  11    shared        identical in every file

The top and bottom images stack into one 8bpp 128x256 page. The outfield model
draws x 0..63 through the outfield CLUT, the goalkeeper draws x 64..127 (and
a few patches below x 64, see `_GK_BELOW_64`) through the GK CLUT; both draw
the number font at (64..127, 68..80), whose pixels are indices `FONT_INDEX` and
up in every file. `REGIONS` lists the outfield model's UV rectangles, from the
quads of MODEL.BIN; the shirt back rectangle is transparent in every shipped
file (the model mirrors the front) and is listed for completeness.

Repainting keeps the file's own records and sizes: only the pixels of the
outfield regions and the outfield CLUT of one kit change. The art of a *base*
file (the slot's own, or one of the 95 as a template) gives the shading: each
pixel's luminance, relative to its part's bright reference, scales the colour
the spec or picture asks for, so folds and seams survive a flat colour. The
repainted pixels are quantised to at most `COLOURS` colours at indices
1..239; index 0 stays 0x0000 (GPU transparent, the neck hole) and entries
240-255 keep the file's number colours. Needs Pillow.
"""

from __future__ import annotations

import io
import struct
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...core.kits import KitSpec
from .flag_atlas import lz_compress, lz_decompress, parse_data_file, rebuild_data_file

if TYPE_CHECKING:
    from PIL import Image

RGB = tuple[int, int, int]

BASE = 0x800F0000
#: What the executable loads of a TEX: 18 sectors.
MAX_BYTES = 18 * 2048
PAGE_W, PAGE_H = 128, 256
OUTFIELD_W = 64  # the outfield model's half of the page
PAGE_BYTES = PAGE_W * PAGE_H // 2  # one 128x128 image
#: First CLUT entry the number font uses; this and the entries above it are kept.
FONT_INDEX = 240
#: Palette budget of a repainted kit: indices 1..239.
COLOURS = FONT_INDEX - 1

SLOTS = {
    0: "home_top",
    1: "home_clut",
    2: "away_top",
    3: "away_clut",
    4: "home_gk_clut",
    5: "away_gk_clut",
    7: "flag",
    8: "flag_clut",
    9: "home_bottom",
    10: "away_bottom",
    11: "shared",
}
KITS = ("home", "away")
#: The plain white template a spec is painted on unless another is named:
#: Classic England, a white shirt with no trim, navy shorts, grey socks.
DEFAULT_TEMPLATE = 56

#: Home kit of each TEX, for `kit_templates`: real 2001-02 kits for 0-62, the
#: fictional Master League clubs (and the club each stands in for) for 63-94.
JERSEY_TEMPLATES: dict[int, str] = {
    0: "Ireland (green)",
    1: "Scotland (navy)",
    2: "Wales (red)",
    3: "England (white)",
    4: "Portugal (dark red)",
    5: "Spain (red)",
    6: "France (blue)",
    7: "Belgium (red)",
    8: "Netherlands (orange)",
    9: "Switzerland (red)",
    10: "Italy (azzurri blue)",
    11: "Czech Republic (dark red)",
    12: "Germany (white)",
    13: "Denmark (red)",
    14: "Norway (red)",
    15: "Sweden (yellow)",
    16: "Iceland (blue)",
    17: "Poland (white)",
    18: "Slovakia (blue)",
    19: "Austria (white)",
    20: "Hungary (red)",
    21: "Albania (red)",
    22: "Croatia (navy, red/white checks)",
    23: "Serbia (blue)",
    24: "Romania (yellow)",
    25: "Bosnia (blue)",
    26: "Greece (blue, white sleeves)",
    27: "Turkey (red)",
    28: "Ukraine (yellow)",
    29: "Russia (white)",
    30: "Morocco (red)",
    31: "Ivory Coast (orange)",
    32: "Egypt (red)",
    33: "Nigeria (green)",
    34: "Cameroon (green)",
    35: "Algeria (green)",
    36: "Ghana (white)",
    37: "U.S.A. (white)",
    38: "Mexico (green)",
    39: "Venezuela (vinotinto)",
    40: "Colombia (yellow)",
    41: "Brazil (yellow)",
    42: "Peru (white, red sash)",
    43: "Chile (red)",
    44: "Paraguay (red/white stripes)",
    45: "Uruguay (celeste)",
    46: "Argentina (sky/white stripes)",
    47: "Ecuador (yellow)",
    48: "Japan (blue)",
    49: "South Korea (red)",
    50: "China (red)",
    51: "India (blue)",
    52: "New Zealand (white)",
    53: "Australia (gold)",
    54: "Euro All Stars (white)",
    55: "World All Stars (white)",
    56: "Clas. England (white)",
    57: "Clas. France (blue)",
    58: "Clas. Netherlands (orange)",
    59: "Clas. Italy (azzurri blue)",
    60: "Clas. Germany (white)",
    61: "Clas. Brazil (yellow)",
    62: "Clas. Argentina (sky/white stripes)",
    63: "Aragon (≈Man Utd, red)",
    64: "London (≈Arsenal, red)",
    65: "Europort (≈Chelsea, blue)",
    66: "Liguria (≈Liverpool, red)",
    67: "Yorkshire (≈Man City, white)",
    68: "Highlands (≈Tottenham, dark)",
    69: "Vascongadas (≈Atl. Madrid, red)",
    70: "Cataluna (≈Barcelona, red/blue)",
    71: "Navarra (≈Real Madrid, white)",
    72: "Andalucia (≈Valencia, white)",
    73: "Cantabria (≈Sevilla, blue)",
    74: "Provence (≈Monaco, red)",
    75: "Languedoc (≈Porto, white)",
    76: "Normandie (≈PSG, blue)",
    77: "Medoc (≈Benfica, white)",
    78: "Rijnkanaal (≈Ajax, white/red)",
    79: "Noordzeekanaal (≈CSKA, dark red)",
    80: "Flandre (≈Zenit, white/red)",
    81: "Marche (≈Inter, black/blue stripes)",
    82: "Piemonte (≈Juventus, black/white stripes)",
    83: "Lombardia (≈Milan, red/black stripes)",
    84: "Umbria (≈Lazio, sky blue)",
    85: "Emilia (≈Napoli, blue)",
    86: "Toscana (≈Fiorentina, purple)",
    87: "Abruzzi (≈Roma, dark red)",
    88: "Westfalen (≈Dortmund, dark)",
    89: "Anhalt (≈Bayern, dark)",
    90: "Ruhr (≈Leverkusen, red)",
    91: "Peloponnisos (≈Wolfsburg, white)",
    92: "Byzantinobul (≈Galatasaray, yellow/dark red)",
    93: "Marmara (≈Shakhtar, white)",
    94: "Patagonia (≈Basel, dark blue)",
}


@dataclass(frozen=True)
class Region:
    """A rectangle of the page (pixels, inclusive) the outfield model draws as `part`."""

    part: str
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0 + 1

    @property
    def height(self) -> int:
        return self.y1 - self.y0 + 1


#: The outfield model's UV rectangles. `part` is what a spec colours: shirt
#: (the front), side, collar, shoulder, sleeve (short and long), shorts, socks,
#: back. Right-hand pieces start at x 32.
REGIONS: tuple[Region, ...] = (
    Region("shirt", 12, 6, 31, 29),
    Region("side", 1, 8, 10, 29),
    Region("side", 32, 8, 42, 29),
    Region("collar", 12, 0, 31, 5),
    Region("collar", 46, 0, 61, 4),
    Region("shoulder", 6, 0, 11, 7),
    Region("shoulder", 32, 0, 37, 7),
    Region("shorts", 0, 30, 63, 47),
    Region("shorts", 32, 48, 47, 65),
    Region("socks", 0, 48, 31, 65),
    Region("sleeve", 0, 66, 31, 79),
    Region("sleeve", 32, 66, 63, 79),
    Region("back", 0, 80, 19, 103),
    Region("sleeve", 32, 128, 63, 141),
    Region("sleeve", 32, 156, 63, 183),
    Region("sleeve", 32, 214, 63, 234),
)
#: Goalkeeper art below x 64 (glove patch, inner shorts strip, long sleeve):
#: never repainted. None overlaps a region above; kept as the rule's record.
_GK_BELOW_64 = ((49, 49, 54, 53), (56, 48, 63, 65), (32, 184, 63, 198))


def _is_goalkeeper(x: int, y: int) -> bool:
    return x >= OUTFIELD_W or any(
        x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in _GK_BELOW_64
    )


@dataclass
class Block:
    """One slot's payload: a decoded 128x128 image (`data` = 16384 indices) or
    a raw 512-byte CLUT. `offset`/`size` span the payload in the file."""

    slot: int
    kind: str
    x: int
    y: int
    w: int
    h: int
    offset: int
    size: int
    data: bytes


def parse(tex: bytes) -> dict[str, Block]:
    """The file's blocks by `SLOTS` name; images decompressed."""
    parsed = parse_data_file(tex, BASE)
    out: dict[str, Block] = {}
    for slot, ptr in enumerate(parsed.header):
        if not ptr or slot not in SLOTS:
            continue
        rec = next(r for r in parsed.records() if r.list_offset == ptr - BASE)
        off = rec.ptr - BASE
        if rec.kind & 0xFF == 0x0A:
            data, end = lz_decompress(tex, off)
            kind, w = "image", rec.w * 2
        else:
            end = off + rec.w * rec.h * 2
            data, kind, w = tex[off:end], "clut", rec.w
        out[SLOTS[slot]] = Block(slot, kind, rec.x, rec.y, w, rec.h, off, end - off, data)
    missing = set(SLOTS.values()) - set(out)
    if missing:
        raise ValueError(f"TEX file lacks {', '.join(sorted(missing))}")
    return out


def _rgb(v: int) -> RGB:
    r, g, b = v & 31, (v >> 5) & 31, (v >> 10) & 31
    return ((r << 3) | (r >> 2), (g << 3) | (g >> 2), (b << 3) | (b >> 2))


def palette(clut: bytes) -> list[RGB]:
    """256 RGB888 colours of a raw BGR555 CLUT."""
    return [_rgb(v) for v in struct.unpack("<256H", clut)]


def _bgr555(c: RGB) -> int:
    r, g, b = (v >> 3 for v in c)
    v = r | (g << 5) | (b << 10)
    return v if v else 0x8000  # 0x0000 is transparent; keep true black opaque


def _luminance(c: RGB) -> float:
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def _page(blocks: dict[str, Block], which: str) -> bytes:
    return blocks[f"{which}_top"].data + blocks[f"{which}_bottom"].data


def render_page(tex: bytes, which: str = "home", gk: bool = False) -> Image.Image:
    """The 128x256 kit page of `which` ("home" or "away") as an RGB image,
    through the outfield CLUT, or the goalkeeper's with `gk`."""
    from PIL import Image

    blocks = parse(tex)
    pal = palette(blocks[f"{which}_gk_clut" if gk else f"{which}_clut"].data)
    img = Image.frombytes("P", (PAGE_W, PAGE_H), _page(blocks, which))
    img.putpalette([c for rgb in pal for c in rgb])
    return img.convert("RGB")


#: The kit thumbnail (96x128): the page box each piece is cut from, with
#: exclusive ends, and where it goes. Pieces are scaled 2x tall with nearest
#: neighbour so the texture stays crisp; the sides, shorts and socks wrap
#: around the body on the model, so they keep 1x width.
PREVIEW_FRONT = (12, 6, 32, 30)  # the chest on the page
PREVIEW_FRONT_AT = (28, 10)  # where it lands, scaled 2x: x 28..67, y 10..57
_PREVIEW_PIECES: tuple[tuple[tuple[int, int, int, int], tuple[int, int], tuple[int, int]], ...] = (
    # (page box, (x scale, y scale), position)
    ((0, 48, 16, 66), (1, 2), (27, 92)),  # left sock
    ((16, 48, 32, 66), (1, 2), (53, 92)),  # right sock
    ((0, 30, 64, 48), (1, 2), (16, 56)),  # shorts
    ((1, 8, 11, 30), (1, 2), (18, 12)),  # left side
    ((32, 8, 42, 30), (1, 2), (68, 12)),  # right side
    (PREVIEW_FRONT, (2, 2), PREVIEW_FRONT_AT),  # chest
    ((12, 0, 32, 6), (2, 2), (28, 0)),  # collar, its hole transparent
)
_PREVIEW_SLEEVE = ((8, 66, 20, 80), (40, 66, 52, 80))  # a strip of each short sleeve
_PREVIEW_SHADOW = (0, 0, 0, 90)


def template_preview(tex: bytes, which: str = "home") -> bytes:
    """A 96x128 RGBA PNG "photo" of the kit, how a picker shows it: the shirt
    front with its sides, collar and both sleeves hanging off the shoulders,
    the shorts below, the socks below those, each cut from the page and scaled
    with nearest neighbour, over a light drop shadow on a transparent
    background."""
    from PIL import Image, ImageFilter

    blocks = parse(tex)
    pal = palette(blocks[f"{which}_clut"].data)
    page = Image.frombytes("P", (PAGE_W, PAGE_H), _page(blocks, which))
    page.putpalette([c for rgb in pal for c in rgb])
    rgba = page.convert("RGBA")
    rgba.putalpha(
        Image.frombytes("L", (PAGE_W, PAGE_H), bytes(255 if v else 0 for v in page.tobytes()))
    )
    nearest = Image.Resampling.NEAREST

    out = Image.new("RGBA", (96, 128), (0, 0, 0, 0))
    for n, box in enumerate(_PREVIEW_SLEEVE):
        # A tall strip of sleeve, tilted so its lower end hangs outwards.
        sleeve = rgba.crop(box).resize((22, 26), nearest)
        sleeve = sleeve.rotate(-22 if n == 0 else 22, nearest, expand=True)
        out.paste(sleeve, (2 if n == 0 else 94 - sleeve.width, 12), sleeve)
    for box, (sx, sy), at in _PREVIEW_PIECES:
        piece = rgba.crop(box).resize(((box[2] - box[0]) * sx, (box[3] - box[1]) * sy), nearest)
        out.paste(piece, at, piece)

    shadow = Image.new("RGBA", out.size, (0, 0, 0, 0))
    shadow.paste(_PREVIEW_SHADOW, (2, 2), out.getchannel("A"))
    shadow = shadow.filter(ImageFilter.GaussianBlur(1.2))
    shadow.alpha_composite(out)
    buf = io.BytesIO()
    shadow.save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Repainting.
# ---------------------------------------------------------------------------

#: The flat colour wanted at a region pixel, before shading.
ColourAt = Callable[[Region, int, int], RGB]


def _repaint(tex: bytes, which: str, base: bytes | None, colour_at: ColourAt) -> bytes:
    """`tex` with kit `which` painted: the outfield regions take `colour_at`,
    shaded by the base's home art, the rest of the page and the GK palette
    stay. Quantises down until the file fits `MAX_BYTES`."""
    target = parse(tex)
    source = target if base is None else parse(base)
    art = _page(source, "home")
    art_pal = palette(source["home_clut"].data)
    page = bytearray(_page(target, which))

    # Each part's bright reference: the 85th percentile luminance of its opaque
    # base pixels, so a highlight brightens the colour a little and a fold
    # darkens it, whatever colour the base was painted.
    lums: dict[str, list[float]] = {}
    for region in REGIONS:
        for y in range(region.y0, region.y1 + 1):
            for x in range(region.x0, region.x1 + 1):
                v = art[y * PAGE_W + x]
                if v:
                    lums.setdefault(region.part, []).append(_luminance(art_pal[v]))
    reference = {part: sorted(ls)[int(len(ls) * 0.85)] or 1.0 for part, ls in lums.items()}

    positions: list[int] = []
    colours: list[RGB] = []
    for region in REGIONS:
        for y in range(region.y0, region.y1 + 1):
            for x in range(region.x0, region.x1 + 1):
                pos = y * PAGE_W + x
                v = art[pos]
                if not v:
                    page[pos] = 0
                    continue
                ratio = _luminance(art_pal[v]) / reference[region.part]
                r, g, b = colour_at(region, x, y)
                positions.append(pos)
                colours.append(
                    (
                        min(255, round(r * ratio)),
                        min(255, round(g * ratio)),
                        min(255, round(b * ratio)),
                    )
                )

    return _rebuild(tex, target, which, page, positions, colours)


def _rebuild(
    tex: bytes,
    target: dict[str, Block],
    which: str,
    page: bytearray,
    positions: list[int],
    colours: list[RGB],
) -> bytes:
    """`tex` with kit `which` holding `page`, whose pixels at `positions` take
    `colours` quantised into CLUT entries 1..239 (the file's number colours at
    240-255 kept), cutting the colour budget until the file fits `MAX_BYTES`."""
    clut = target[f"{which}_clut"].data
    parsed = parse_data_file(tex, BASE)
    for budget in (COLOURS, 128, 64, 32, 16):
        pal, indices = _quantise(colours, budget)
        for pos, k in zip(positions, indices, strict=True):
            page[pos] = k + 1
        new_clut = (
            b"\x00\x00"
            + b"".join(_bgr555(c).to_bytes(2, "little") for c in pal)
            + bytes(2 * (FONT_INDEX - 1 - budget))
            + clut[2 * FONT_INDEX :]
        )
        out = rebuild_data_file(
            parsed,
            {
                target[f"{which}_top"].offset: lz_compress(bytes(page[:PAGE_BYTES])),
                target[f"{which}_bottom"].offset: lz_compress(bytes(page[PAGE_BYTES:])),
                target[f"{which}_clut"].offset: new_clut,
            },
        )
        if len(out) <= MAX_BYTES:
            return out
    raise ValueError(f"repainted TEX is {len(out)} bytes, over the {MAX_BYTES} the game loads")


def _quantise(colours: list[RGB], budget: int) -> tuple[list[RGB], list[int]]:
    """`budget` palette entries (median cut, padded with black) and each
    colour's index among them."""
    from PIL import Image

    strip = Image.new("RGB", (max(len(colours), 1), 1))
    strip.putdata(colours)
    q = strip.quantize(colors=budget, method=Image.Quantize.MEDIANCUT)
    flat = (q.getpalette() or []) + [0] * (3 * budget)
    pal = [(flat[i], flat[i + 1], flat[i + 2]) for i in range(0, 3 * budget, 3)]
    return pal, list(q.tobytes())[: len(colours)]


def _spec_colour(spec: KitSpec) -> ColourAt:
    """`colour_at` for a spec: the pattern drawn in `secondary` over the shirt.

    Vertical stripes are 4 px bands by page column, so they run on across the
    front, sides and sleeves; pinstripes a 1 px line every 4; hoops 4 px bands
    from each piece's top; halves the right half of the front and back and
    every right-hand piece; a sash a diagonal band across the front; a chevron
    a V across the chest. Sleeves take `sleeves` when given, `secondary` on the
    sleeves pattern, and otherwise follow the pattern like the rest of the
    shirt; the collar always takes `secondary`.
    """
    pattern = spec.pattern
    sleeves = spec.sleeves or spec.secondary

    def contrast(region: Region, x: int, y: int) -> bool:
        if pattern == "stripes":
            return (x // 4) % 2 == 0
        if pattern == "pinstripes":
            return x % 4 == 2
        if pattern == "hoops":
            return ((y - region.y0) // 4) % 2 == 1
        if pattern == "halves":
            if region.part in ("shirt", "back"):
                return x >= (region.x0 + region.x1 + 1) // 2
            return region.x0 >= 32
        if region.part != "shirt":
            return False
        u, v = x - region.x0 + 0.5, y - region.y0 + 0.5
        if pattern == "sash":  # band about the diagonal from the top-left corner
            w, h = region.width, region.height
            return abs(u * h - v * w) / (w * w + h * h) ** 0.5 < 2.5
        if pattern == "chevron":  # a V whose apex sits at mid-chest
            return 0 <= v - (11 - abs(u - region.width / 2) * 0.6) < 4
        return False

    def colour_at(region: Region, x: int, y: int) -> RGB:
        part = region.part
        if part == "shorts":
            return spec.shorts
        if part == "socks":
            return spec.socks
        if part == "collar":
            return spec.secondary
        if part == "sleeve" and (spec.sleeves is not None or pattern == "sleeves"):
            return sleeves
        return spec.secondary if contrast(region, x, y) else spec.shirt

    return colour_at


def apply_spec(tex: bytes, which: str, spec: KitSpec, base: bytes | None = None) -> bytes:
    """`tex` with kit `which` ("home" or "away") drawn from `spec`, shaded by
    the home art of `base` (another TEX, a template) or of `tex` itself."""
    return _repaint(tex, which, base, _spec_colour(spec))


#: Where each part of the kit sits on a 96x128 picture laid out like
#: `core.kits.render_kit`: fractions (x0, y0, x1, y1); right-hand pieces mirror.
_IMAGE_BOXES: dict[str, tuple[float, float, float, float]] = {
    "shirt": (0.28, 0.12, 0.72, 0.48),
    "back": (0.28, 0.12, 0.72, 0.48),
    "side": (0.22, 0.14, 0.30, 0.48),
    "collar": (0.40, 0.08, 0.60, 0.12),
    "shoulder": (0.22, 0.08, 0.32, 0.14),
    "sleeve": (0.04, 0.10, 0.22, 0.30),
    "shorts": (0.24, 0.50, 0.76, 0.64),
    "socks": (0.28, 0.76, 0.42, 0.94),
}


def _image_colour(png: bytes) -> ColourAt:
    """`colour_at` sampling a picture of the kit: the chest from its upper
    middle, the sleeves from its sides, shorts and socks from its lower part.
    Where the picture is transparent the shirt's mean colour stands in, white
    for the shorts (a shirt alone gets white shorts and matching socks)."""
    from PIL import Image

    w, h = 96, 128
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    img.thumbnail((w, h))
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.paste(img, ((w - img.width) // 2, 0))
    raw = canvas.tobytes()

    def pix(x: int, y: int) -> tuple[int, int, int, int]:
        o = (y * w + x) * 4
        return raw[o], raw[o + 1], raw[o + 2], raw[o + 3]

    x0, y0, x1, y1 = _IMAGE_BOXES["shirt"]
    torso = [
        pix(x, y)[:3]
        for y in range(int(y0 * h), int(y1 * h))
        for x in range(int(x0 * w), int(x1 * w))
        if pix(x, y)[3] >= 128
    ]
    shirt: RGB = (255, 255, 255)
    if torso:
        n = len(torso)
        shirt = (
            sum(c[0] for c in torso) // n,
            sum(c[1] for c in torso) // n,
            sum(c[2] for c in torso) // n,
        )

    def colour_at(region: Region, x: int, y: int) -> RGB:
        bx0, by0, bx1, by1 = _IMAGE_BOXES[region.part]
        if region.x0 >= 32 and region.part in ("side", "shoulder", "sleeve"):
            bx0, bx1 = 1 - bx1, 1 - bx0
        u = (x - region.x0 + 0.5) / region.width
        v = (y - region.y0 + 0.5) / region.height
        sx = min(w - 1, int((bx0 + u * (bx1 - bx0)) * w))
        sy = min(h - 1, int((by0 + v * (by1 - by0)) * h))
        r, g, b, a = pix(sx, sy)
        if a >= 128:
            return (r, g, b)
        return (255, 255, 255) if region.part == "shorts" else shirt

    return colour_at


def apply_image(tex: bytes, which: str, png: bytes, base: bytes | None = None) -> bytes:
    """`tex` with kit `which` painted from a picture of the kit (a shirt or a
    full kit on a transparent or plain background), shaded like `apply_spec`."""
    return _repaint(tex, which, base, _image_colour(png))


def copy_kit(tex: bytes, which: str, base: bytes) -> bytes:
    """`tex` with kit `which` replaced by the home kit of `base` as drawn: its
    two page images, outfield palette and goalkeeper palette."""
    target, source = parse(tex), parse(base)
    replacements = {}
    for part in ("top", "bottom", "clut", "gk_clut"):
        src = source[f"home_{part}"]
        replacements[target[f"{which}_{part}"].offset] = base[src.offset : src.offset + src.size]
    return rebuild_data_file(parse_data_file(tex, BASE), replacements)


def apply_choice(
    tex: bytes,
    which: str,
    spec: KitSpec | None,
    image: bytes | None,
    template: int | None,
    files: Mapping[int, bytes],
) -> bytes:
    """One kit choice onto `tex`, as the writer and the previews apply it: a
    128x256 picture is the page itself (`apply_page`); another picture or a
    spec is painted over the art of `files[template]` (`DEFAULT_TEMPLATE`
    without one, `tex` itself when that is not in `files`); a template alone
    is copied in; nothing at all leaves `tex` as it is."""
    base = files.get(DEFAULT_TEMPLATE if template is None else template)
    if image is not None and is_page_png(image):
        return apply_page(tex, which, image)
    if image is not None:
        return apply_image(tex, which, image, base)
    if spec is not None:
        return apply_spec(tex, which, spec, base)
    if template is not None and base is not None:
        return copy_kit(tex, which, base)
    return tex


def is_page_png(png: bytes) -> bool:
    """True for a PNG of exactly the page's size, 128x256: `apply_page` art
    rather than a picture of a kit. Read off the IHDR chunk, no Pillow needed."""
    return png[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack_from(">II", png, 16) == (
        PAGE_W,
        PAGE_H,
    )


def apply_page(tex: bytes, which: str, png: bytes) -> bytes:
    """`tex` with the outfield half of kit `which`'s page painted from `png`,
    a 128x256 RGBA picture of the page itself (`page_png`, painted over): an
    opaque pixel below x 64 takes the picture's colour, a transparent one keeps
    the art; the goalkeeper's half, its patches below x 64 and the number
    colours stay. Quantised and rebuilt like `apply_image`. Guides left in the
    picture are painted too."""
    from PIL import Image

    img = Image.open(io.BytesIO(png)).convert("RGBA")
    if img.size != (PAGE_W, PAGE_H):
        raise ValueError(f"a page picture is {PAGE_W}x{PAGE_H}, not {img.width}x{img.height}")
    raw = img.tobytes()
    target = parse(tex)
    page = bytearray(_page(target, which))
    pal = palette(target[f"{which}_clut"].data)
    positions: list[int] = []
    colours: list[RGB] = []
    for y in range(PAGE_H):
        for x in range(OUTFIELD_W):
            if _is_goalkeeper(x, y):
                continue
            pos = y * PAGE_W + x
            o = pos * 4
            if raw[o + 3] >= 128:
                colours.append((raw[o], raw[o + 1], raw[o + 2]))
            elif page[pos]:
                colours.append(pal[page[pos]])
            else:
                continue
            positions.append(pos)
    return _rebuild(tex, target, which, page, positions, colours)


#: Guide labels, short enough to sit inside most regions.
_LABELS = {
    "shirt": "front",
    "side": "side",
    "collar": "col",
    "shoulder": "sh",
    "shorts": "shorts",
    "socks": "socks",
    "sleeve": "slv",
    "back": "back",
}


def page_png(tex: bytes, which: str = "home", guides: bool = True) -> bytes:
    """Kit `which`'s page as a 128x256 RGBA PNG to paint over and hand back to
    `apply_page`: the outfield half through its CLUT, the goalkeeper's through
    its own, index 0 transparent. With `guides`, every `REGIONS` rectangle is
    outlined in 1 px pure red (#ff0000) with a small red label."""
    from PIL import Image, ImageDraw, ImageFont

    blocks = parse(tex)
    outfield = palette(blocks[f"{which}_clut"].data)
    keeper = palette(blocks[f"{which}_gk_clut"].data)
    data = _page(blocks, which)
    img = Image.new("RGBA", (PAGE_W, PAGE_H), (0, 0, 0, 0))
    img.putdata(
        [
            (*(outfield if i % PAGE_W < OUTFIELD_W else keeper)[v], 255) if v else (0, 0, 0, 0)
            for i, v in enumerate(data)
        ]
    )
    if guides:
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.load_default(size=7)
        except TypeError:  # Pillow before 10.1
            font = ImageFont.load_default()
        for region in REGIONS:
            draw.rectangle((region.x0, region.y0, region.x1, region.y1), outline=(255, 0, 0, 255))
            draw.text(
                (region.x0 + 1, region.y0), _LABELS[region.part], fill=(255, 0, 0, 255), font=font
            )
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
