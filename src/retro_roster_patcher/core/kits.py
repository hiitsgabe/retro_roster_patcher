"""Kit descriptions and a small front-view preview of them.

A `KitSpec` is the vocabulary the app and every patcher share for a team's
colours: a shirt colour, the contrast colour its pattern is drawn in, the
pattern's name, shorts and socks. `render_kit` turns one into a PNG so the app
can show a team's home and away kits; the patchers write the same spec into
whatever colour slots their game has.

`default_kits` is the spec every game starts from, plain kits in a provider's
two team colours, and `kit_from_image` reads one off a picture of the kit for
games that cannot take the picture itself.

Rendering and reading pictures need Pillow, the optional `images` extra,
imported inside the functions so the rest of the library stays importable
without it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..sports.models import Team

RGB = tuple[int, int, int]
WHITE: RGB = (255, 255, 255)

#: The kit designs a spec can name, the common real-world ones.
PATTERNS: tuple[str, ...] = (
    "plain",  # one colour
    "stripes",  # vertical bands (Barcelona, Milan, Newcastle)
    "hoops",  # horizontal bands (Celtic, QPR)
    "halves",  # left/right split (Blackburn)
    "sash",  # diagonal band from the wearer's right shoulder (River Plate, Rayo)
    "pinstripes",  # thin vertical lines
    "chevron",  # a V across the chest
    "sleeves",  # plain shirt, contrasting sleeves
)

OUTLINE = (24, 24, 24, 255)


def _hex(colour: RGB) -> str:
    return "#{:02x}{:02x}{:02x}".format(*colour)


def _rgb(text: str) -> RGB:
    text = text.strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"Not an #rrggbb colour: {text!r}")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


@dataclass(frozen=True)
class KitSpec:
    """One kit: a shirt with a pattern in a contrast colour, shorts and socks.

    `sleeves` is None for sleeves in the shirt colour (or in `secondary` on the
    "sleeves" pattern).
    """

    shirt: RGB
    secondary: RGB
    pattern: str
    shorts: RGB
    socks: RGB
    sleeves: RGB | None = None

    def to_dict(self) -> dict[str, Any]:
        """Colours as "#rrggbb", the pattern by name: the app's JSON shape."""
        return {
            "shirt": _hex(self.shirt),
            "secondary": _hex(self.secondary),
            "pattern": self.pattern,
            "shorts": _hex(self.shorts),
            "socks": _hex(self.socks),
            "sleeves": None if self.sleeves is None else _hex(self.sleeves),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KitSpec:
        pattern = str(data.get("pattern", "plain"))
        if pattern not in PATTERNS:
            raise ValueError(f"Unknown kit pattern {pattern!r}; one of {', '.join(PATTERNS)}")
        sleeves = data.get("sleeves")
        return cls(
            shirt=_rgb(data["shirt"]),
            secondary=_rgb(data["secondary"]),
            pattern=pattern,
            shorts=_rgb(data["shorts"]),
            socks=_rgb(data["socks"]),
            sleeves=None if sleeves is None else _rgb(sleeves),
        )


@dataclass(frozen=True)
class KitTemplate:
    """One of a game's own jersey designs, for `patch(kits={slot: {"home": {"template": index}}})`.

    `png` is a preview of it, for a picker.
    """

    index: int
    name: str
    png: bytes


def _hex_colour(value: str) -> RGB | None:
    """A `RRGGBB` / `#RRGGBB` provider colour, else None."""
    text = (value or "").lstrip("#")
    if len(text) != 6:
        return None
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return None


CATALOG_PACKAGE = "retro_roster_patcher.data"
CATALOG_FILE = "kit_catalog.json"
_catalog: dict[str, Any] | None = None


def kit_catalog() -> dict[str, Any]:
    """The packaged kit catalogue (`data/kit_catalog.json`): real clubs' home
    and away kits as `KitSpec` dicts. A plain JSON file so any soccer patcher,
    or another program, can read it: `clubs` is a list of entries with `name`,
    `country`, `espn_id` (int or null), `names` (other spellings), `wikipedia`,
    `home` and `away`. Read once; an install without it reads as empty."""
    global _catalog
    if _catalog is None:
        import json

        from .assets import MissingAssetError, package_bytes

        try:
            _catalog = json.loads(package_bytes(CATALOG_PACKAGE, CATALOG_FILE))
        except MissingAssetError:
            _catalog = {"clubs": []}
    return _catalog


def _name_key(name: str) -> str:
    """A club name folded for matching: ASCII, lower case, no club-type words."""
    import unicodedata

    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    words = "".join(c if c.isalnum() else " " for c in folded).split()
    noise = {
        "fc",
        "cf",
        "sc",
        "ac",
        "afc",
        "sk",
        "fk",
        "cd",
        "cr",
        "ssc",
        "rc",
        "the",
        "club",
        "de",
        "da",
        "do",
        "del",
    }
    return " ".join(w for w in words if w not in noise)


def catalog_kits(team: Team) -> tuple[KitSpec, KitSpec] | None:
    """The catalogue's home and away kits for `team`, if it is there.

    An entry with an ESPN id answers only to that id, so a club that shares a
    name with a catalogued one elsewhere never takes its kit; an entry
    without one (a league ESPN does not carry) answers to its names.
    """
    clubs = kit_catalog().get("clubs", [])
    hit = next((c for c in clubs if c.get("espn_id") is not None and c["espn_id"] == team.id), None)
    if hit is None:
        keys = {_name_key(n) for n in (team.name, team.short_name) if n}
        hit = next(
            (
                c
                for c in clubs
                if c.get("espn_id") is None
                and keys & {_name_key(n) for n in [c["name"], *c.get("names", [])]}
            ),
            None,
        )
    if hit is None or not hit.get("home") or not hit.get("away"):
        return None
    return KitSpec.from_dict(hit["home"]), KitSpec.from_dict(hit["away"])


def default_kits(team: Team) -> tuple[KitSpec | None, KitSpec | None]:
    """The kits a team starts with: the catalogue's (`catalog_kits`) for a
    club it knows, else plain home and away kits from the provider's two team
    colours: the first colour at home, the second away, each with the other
    as its trim, white shorts unless the shirt is white, socks like the
    shirt. None when neither knows the team's colours."""
    known = catalog_kits(team)
    if known is not None:
        return known
    first = _hex_colour(team.color)
    second = _hex_colour(team.alternate_color) or (contrast(first) if first else None)
    if first is None or second is None:
        return None, None

    def plain(shirt: RGB, trim: RGB) -> KitSpec:
        shorts = trim if shirt == WHITE else WHITE
        return KitSpec(shirt=shirt, secondary=trim, pattern="plain", shorts=shorts, socks=shirt)

    return plain(first, second), plain(second, first)


def contrast(colour: RGB) -> RGB:
    """Black or white, whichever reads better on `colour`: a shirt's number colour.

    Relative luminance (sRGB linearised). The split sits above the mathematical
    tie point (0.18) because kit makers put white on reds and mid tones.
    """

    def linear(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in colour)
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return (0, 0, 0) if luminance > 0.3 else (255, 255, 255)


def render_kit(spec: KitSpec, width: int = 96, height: int = 128) -> bytes:
    """Draw `spec` from the front as an RGBA PNG on a transparent background.

    Shirt with short sleeves and a round neck, shorts, two socks, each with a
    1 px dark outline. The pattern is drawn on its own layer and clipped to the
    shirt through a mask, so every pattern is a few plain polygons.
    """
    import io

    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    def x(f: float) -> float:
        return f * width

    def y(f: float) -> float:
        return f * height

    # Torso box; the sleeves hang off its top corners.
    x0, x1, y0, y1 = x(0.22), x(0.78), y(0.08), y(0.50)
    cx = width / 2
    torso = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    left_sleeve = [(x0 - 1, y0), (x(0.04), y(0.18)), (x(0.10), y(0.30)), (x0 - 1, y(0.26))]
    right_sleeve = [(x1 + 1, y0), (x(0.96), y(0.18)), (x(0.90), y(0.30)), (x1 + 1, y(0.26))]
    neck = (cx - x(0.09), y0 - y(0.045), cx + x(0.09), y0 + y(0.045))
    shorts = [
        (x(0.24), y(0.48)),
        (x(0.76), y(0.48)),
        (x(0.80), y(0.66)),
        (cx + x(0.05), y(0.66)),
        (cx, y(0.60)),
        (cx - x(0.05), y(0.66)),
        (x(0.20), y(0.66)),
    ]
    socks = [
        [(x(0.28), y(0.74)), (x(0.42), y(0.74)), (x(0.41), y(0.95)), (x(0.29), y(0.95))],
        [(x(0.58), y(0.74)), (x(0.72), y(0.74)), (x(0.71), y(0.95)), (x(0.59), y(0.95))],
    ]

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    def place(layer: Image.Image, mask: Image.Image) -> None:
        """Composite `layer` through `mask`, then outline the mask's silhouette."""
        canvas.paste(layer, (0, 0), mask)
        edge = ImageChops.subtract(mask, mask.filter(ImageFilter.MinFilter(3)))
        canvas.paste(OUTLINE, (0, 0), edge)

    def garment(polygons: list[list[tuple[float, float]]], colour: RGB) -> None:
        mask = Image.new("L", (width, height), 0)
        for poly in polygons:
            ImageDraw.Draw(mask).polygon(poly, fill=255)
        place(Image.new("RGBA", (width, height), (*colour, 255)), mask)

    # Back to front: socks, shorts, then the shirt whose hem covers the shorts.
    for sock in socks:
        garment([sock], spec.socks)
    garment([shorts], spec.shorts)

    mask = Image.new("L", (width, height), 0)
    md = ImageDraw.Draw(mask)
    for poly in (torso, left_sleeve, right_sleeve):
        md.polygon(poly, fill=255)
    md.ellipse(neck, fill=0)

    layer = Image.new("RGBA", (width, height), (*spec.shirt, 255))
    draw = ImageDraw.Draw(layer)
    fill = (*spec.secondary, 255)
    pattern = spec.pattern
    if pattern == "stripes":
        band = (x1 - x0) / 9
        for k in range(0, 9, 2):
            draw.rectangle((x0 + k * band, 0, x0 + (k + 1) * band - 1, height), fill=fill)
    elif pattern == "hoops":
        band = (y1 - y0) / 10
        for k in range(1, 10, 2):
            draw.rectangle((0, y0 + k * band, width, y0 + (k + 1) * band - 1), fill=fill)
    elif pattern == "halves":
        draw.rectangle((cx, 0, width, height), fill=fill)
    elif pattern == "sash":
        # Centreline from the wearer's right shoulder (viewer's left) to the
        # opposite hip, extended past the shirt; the mask clips it.
        slope = (x1 - x0) / (y1 - y0)
        hw = (x1 - x0) / 7
        top, bottom = x0 - y0 * slope, x0 + (height - y0) * slope
        draw.polygon(
            [(top - hw, 0), (top + hw, 0), (bottom + hw, height), (bottom - hw, height)],
            fill=fill,
        )
    elif pattern == "pinstripes":
        for px in range(int(x0) + 3, int(x1), 6):
            draw.line((px, 0, px, height), fill=fill, width=1)
    elif pattern == "chevron":
        top, apex, thick = y(0.10), y(0.30), y(0.08)
        draw.polygon(
            [
                (0, top),
                (cx, apex),
                (width, top),
                (width, top + thick),
                (cx, apex + thick),
                (0, top + thick),
            ],
            fill=fill,
        )
    sleeves = spec.sleeves or (spec.secondary if pattern == "sleeves" else None)
    if sleeves is not None:
        for poly in (left_sleeve, right_sleeve):
            draw.polygon(poly, fill=(*sleeves, 255))
    place(layer, mask)

    out = io.BytesIO()
    canvas.save(out, "PNG")
    return out.getvalue()


#: RGB distance within which a pixel is the background colour of the corners.
_BACKGROUND = 40
#: RGB distance two kit colours are apart; anything closer is shading of one.
_CLEAR = 80
#: Share of the shirt's pixels a colour needs to be its second colour rather
#: than a crest, a sponsor or the shadow along an edge.
_SECONDARY_SHARE = 0.10
#: The picture is scaled to this many pixels along its longer side first.
_READ_SIZE = 256


def kit_from_image(png: bytes) -> KitSpec:
    """Read a `KitSpec` off a picture of a kit: a shirt alone, a full kit or a photo.

    A heuristic for games that cannot take the picture itself. The picture is
    scaled to at most 256 px and its colours quantised to 24 levels per channel
    so JPEG noise collapses. Transparent pixels and pixels near the colour of
    the four corners are background. In the bounding box of what is left, the
    upper 55% is the shirt: its most frequent colour is `shirt`, the next one a
    clear distance away with a tenth of the pixels is `secondary` (else black
    or white, by `contrast`). The pattern is read by classing each pixel as one
    of the two: four or more alternations along horizontal lines through the
    chest are stripes (pinstripes when the second colour's runs are 1-2 px),
    along vertical lines hoops; the second colour covering one diagonal of the
    chest but not the other is a sash; the two halves having different
    dominant colours is halves; the second colour dominating the sleeve
    corners (outer fifth of the columns, top quarter of the box) but not the
    chest is sleeves; anything else is plain. A box taller than 1.1x its width
    is a full kit, and rows 55-80% give the shorts, 80-100% the socks;
    otherwise the shorts are white (the second colour on a white shirt) and
    the socks match the shirt.

    Limits: a kit the colour of its background is lost with it; a chevron
    reads as plain; a sash or sleeves are found only near the angle and place
    looked at; a mannequin's legs become the socks; thin pinstripes vanish in
    the scaling; a shirt lit unevenly can read its shadow as a second colour.
    Raises `ValueError` when nothing but background is found. Needs Pillow.
    """
    import io

    from PIL import Image

    image = Image.open(io.BytesIO(png)).convert("RGBA")
    image.thumbnail((_READ_SIZE, _READ_SIZE))
    width, height = image.size
    raw = image.tobytes()
    pix: list[tuple[int, ...]] = [tuple(raw[i : i + 4]) for i in range(0, len(raw), 4)]

    def at(x: int, y: int) -> tuple[int, ...]:
        return pix[y * width + x]

    corners = [
        at(x, y)[:3]
        for x, y in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1))
        if at(x, y)[3] >= 128
    ]
    mask = [
        [
            at(x, y)[3] >= 128 and all(math.dist(at(x, y)[:3], c) > _BACKGROUND for c in corners)
            for x in range(width)
        ]
        for y in range(height)
    ]
    rows = [y for y in range(height) if any(mask[y])]
    if not rows:
        raise ValueError("No kit in the picture: every pixel is transparent or background")
    cols = [x for x in range(width) if any(mask[y][x] for y in rows)]
    top, bottom, left, right = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
    box_w, box_h = right - left, bottom - top

    def row(f: float) -> int:
        return top + round(box_h * f)

    def col(f: float) -> int:
        return left + round(box_w * f)

    def palette(x0: int, x1: int, y0: int, y1: int) -> list[tuple[int, RGB]]:
        """(count, colour) of the kit pixels in the box, most frequent first,
        each colour the mean of its 24-level bin."""
        bins: dict[tuple[int, int, int], list[int]] = {}
        for y in range(y0, y1):
            for x in range(x0, x1):
                if mask[y][x]:
                    r, g, b, _ = at(x, y)
                    acc = bins.setdefault((r // 11, g // 11, b // 11), [0, 0, 0, 0])
                    acc[0] += 1
                    acc[1] += r
                    acc[2] += g
                    acc[3] += b
        return sorted(((n, (r // n, g // n, b // n)) for n, r, g, b in bins.values()), reverse=True)

    def dominant(x0: int, x1: int, y0: int, y1: int, default: RGB) -> RGB:
        found = palette(x0, x1, y0, y1)
        return found[0][1] if found else default

    shirt_bottom = row(0.55)
    colours = palette(left, right, top, shirt_bottom)
    total = sum(n for n, _ in colours)
    shirt = colours[0][1]
    secondary = next(
        (
            c
            for n, c in colours[1:]
            if n >= total * _SECONDARY_SHARE and math.dist(c, shirt) > _CLEAR
        ),
        contrast(shirt),
    )

    def which(x: int, y: int) -> int | None:
        """0 for a pixel of the shirt colour, 1 for one of the second, else None."""
        if not mask[y][x]:
            return None
        rgb = at(x, y)[:3]
        to_shirt, to_secondary = math.dist(rgb, shirt), math.dist(rgb, secondary)
        if min(to_shirt, to_secondary) > _CLEAR:
            return None
        return 0 if to_shirt <= to_secondary else 1

    def line(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
        n = max(abs(x1 - x0), abs(y1 - y0), 1)
        return [(x0 + (x1 - x0) * i // n, y0 + (y1 - y0) * i // n) for i in range(n + 1)]

    def runs(points: list[tuple[int, int]]) -> list[list[int]]:
        """[colour, length] of each run of one colour along the points."""
        out: list[list[int]] = []
        for x, y in points:
            label = which(x, y)
            if label is None:
                continue
            if out and out[-1][0] == label:
                out[-1][1] += 1
            else:
                out.append([label, 1])
        return out

    def share(points: list[tuple[int, int]]) -> float:
        """The second colour's share of the classed pixels along the points."""
        labels = [label for label in (which(x, y) for x, y in points) if label is not None]
        return sum(labels) / len(labels) if labels else 0.0

    def nearer(colour: RGB, target: RGB, other: RGB) -> bool:
        return math.dist(colour, target) < math.dist(colour, other)

    def alternations(lines: list[list[list[int]]]) -> int:
        return sorted(len(r) - 1 for r in lines)[len(lines) // 2]  # the median line

    shirt_h = shirt_bottom - top
    chest_rows = [top + round(shirt_h * f) for f in (0.15, 0.22, 0.30)]
    across = [runs(line(left, y, right - 1, y)) for y in chest_rows]
    down = [
        runs(line(min(right - 1, col(f)), top, min(right - 1, col(f)), shirt_bottom - 1))
        for f in (0.4, 0.5, 0.6)
    ]
    # The chest's diagonals, with the sleeves and shoulders left out.
    cx0, cx1, cy0, cy1 = col(0.2), col(0.8) - 1, top + round(shirt_h * 0.1), shirt_bottom - 1
    diagonals = sorted((share(line(cx0, cy0, cx1, cy1)), share(line(cx1, cy0, cx0, cy1))))
    sleeves = [
        dominant(left, col(0.2), top, row(0.25), shirt),
        dominant(col(0.8), right, top, row(0.25), shirt),
    ]
    chest = dominant(col(0.2), col(0.8), top, shirt_bottom, shirt)
    halves = (
        dominant(left, col(0.5), top, shirt_bottom, shirt),
        dominant(col(0.5), right, top, shirt_bottom, shirt),
    )

    if alternations(across) >= 4:
        narrow = all(n <= 2 for r in across for label, n in r if label == 1)
        pattern = "pinstripes" if narrow else "stripes"
    elif alternations(down) >= 4:
        pattern = "hoops"
    elif diagonals[1] > 0.6 and diagonals[0] < 0.4:
        pattern = "sash"
    elif math.dist(*halves) > _CLEAR:
        pattern = "halves"
    elif all(nearer(s, secondary, shirt) for s in sleeves) and nearer(chest, shirt, secondary):
        pattern = "sleeves"
    else:
        pattern = "plain"

    if box_h > 1.1 * box_w:
        shorts = dominant(left, right, shirt_bottom, row(0.8), WHITE)
        socks = dominant(left, right, row(0.8), bottom, shirt)
    else:
        shorts = secondary if min(shirt) > 230 else WHITE
        socks = shirt
    return KitSpec(shirt=shirt, secondary=secondary, pattern=pattern, shorts=shorts, socks=socks)
