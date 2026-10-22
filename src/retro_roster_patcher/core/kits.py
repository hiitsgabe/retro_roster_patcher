"""Kit descriptions and a small front-view preview of them.

A `KitSpec` is the vocabulary the app and every patcher share for a team's
colours: a shirt colour, the contrast colour its pattern is drawn in, the
pattern's name, shorts and socks. `render_kit` turns one into a PNG so the app
can show a team's home and away kits; the patchers write the same spec into
whatever colour slots their game has.

Rendering needs Pillow, the optional `images` extra, imported inside the
function so the rest of the library stays importable without it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RGB = tuple[int, int, int]

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
