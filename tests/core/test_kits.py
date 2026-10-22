"""`render_kit` and `kit_from_image` need Pillow, the optional `images` extra."""

import io

import pytest

from retro_roster_patcher.core.kits import (
    PATTERNS,
    KitSpec,
    contrast,
    default_kits,
    kit_from_image,
    render_kit,
)
from retro_roster_patcher.sports.models import Team

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

RED = (200, 16, 46)
WHITE = (255, 255, 255)


def _spec(pattern: str, **overrides) -> KitSpec:
    fields = dict(shirt=RED, secondary=WHITE, pattern=pattern, shorts=WHITE, socks=RED)
    fields.update(overrides)
    return KitSpec(**fields)


def _image(spec: KitSpec, **size) -> Image.Image:
    return Image.open(io.BytesIO(render_kit(spec, **size))).convert("RGBA")


def _rgb(pixel: tuple[int, int, int, int]) -> tuple[int, int, int]:
    return pixel[:3]


def test_spec_round_trips_through_dict_as_hex_strings():
    spec = _spec("sash", sleeves=(0, 0, 0))
    data = spec.to_dict()
    assert data == {
        "shirt": "#c8102e",
        "secondary": "#ffffff",
        "pattern": "sash",
        "shorts": "#ffffff",
        "socks": "#c8102e",
        "sleeves": "#000000",
    }
    assert KitSpec.from_dict(data) == spec


def test_from_dict_keeps_absent_sleeves_absent_and_refuses_unknown_patterns():
    assert KitSpec.from_dict(_spec("plain").to_dict()).sleeves is None
    with pytest.raises(ValueError, match="hoops"):
        KitSpec.from_dict({**_spec("plain").to_dict(), "pattern": "argyle"})


@pytest.mark.parametrize("pattern", PATTERNS)
def test_every_pattern_renders_a_png_of_the_requested_size(pattern):
    image = _image(_spec(pattern), width=60, height=80)
    assert image.size == (60, 80)
    for corner in ((0, 0), (59, 0), (0, 79), (59, 79)):
        assert image.getpixel(corner)[3] == 0
    chest = {_rgb(image.getpixel((x, 20))) for x in range(20, 40)}
    assert RED in chest


def test_stripes_alternate_along_a_horizontal_line_through_the_chest():
    image = _image(_spec("stripes"))
    colours = {_rgb(image.getpixel((x, 40))) for x in range(24, 72)}
    assert {RED, WHITE} <= colours


def test_hoops_alternate_along_a_vertical_line_through_the_chest():
    image = _image(_spec("hoops"))
    colours = {_rgb(image.getpixel((48, y))) for y in range(14, 62)}
    assert {RED, WHITE} <= colours


def test_sleeves_take_their_own_colour_when_given():
    image = _image(_spec("plain", sleeves=(0, 0, 0)))
    assert _rgb(image.getpixel((10, 26))) == (0, 0, 0)
    assert _rgb(image.getpixel((48, 40))) == RED


def test_contrast_picks_white_on_navy_and_black_on_yellow():
    assert contrast((0, 0, 128)) == (255, 255, 255)
    assert contrast((255, 255, 0)) == (0, 0, 0)


GREEN, BLUE, NAVY = (0, 128, 0), (0, 0, 200), (0, 0, 80)


def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_default_kits_are_plain_in_the_providers_two_colours():
    home, away = default_kits(Team(id=1, name="X", color="#C8102E", alternate_color="FFFFFF"))
    assert home == KitSpec(RED, WHITE, "plain", WHITE, RED)
    assert away == KitSpec(WHITE, RED, "plain", RED, WHITE)  # white shirt, trim shorts
    assert default_kits(Team(id=1, name="X", color="red")) == (None, None)


def test_a_shirt_alone_reads_as_a_plain_kit_with_white_shorts():
    image = Image.new("RGBA", (120, 100), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((10, 10, 109, 89), fill=(*RED, 255))
    assert kit_from_image(_png(image)) == KitSpec(RED, WHITE, "plain", WHITE, RED)


def test_a_photo_background_is_read_off_the_corners():
    image = Image.new("RGB", (120, 100), (240, 240, 236))  # an off-white backdrop
    ImageDraw.Draw(image).rectangle((10, 10, 109, 89), fill=RED)
    assert kit_from_image(_png(image)).shirt == RED
    with pytest.raises(ValueError, match="background"):
        kit_from_image(_png(Image.new("RGB", (8, 8), (240, 240, 236))))


@pytest.mark.parametrize(
    "spec",
    [
        KitSpec(RED, WHITE, "plain", BLUE, NAVY),
        KitSpec(RED, WHITE, "stripes", WHITE, RED),
        KitSpec(RED, WHITE, "pinstripes", WHITE, RED),
        KitSpec(GREEN, WHITE, "hoops", WHITE, GREEN),
        KitSpec(WHITE, RED, "sash", RED, WHITE),
        KitSpec(RED, WHITE, "halves", WHITE, RED),
        KitSpec(RED, WHITE, "sleeves", WHITE, RED),
    ],
    ids=lambda spec: spec.pattern,
)
def test_a_rendered_full_kit_reads_back_its_pattern_and_colours(spec):
    read = kit_from_image(render_kit(spec, 192, 256))
    assert read.pattern == spec.pattern
    # Which of two colours is "the shirt" on a striped or halved kit is a toss-up.
    assert {read.shirt, read.secondary} == {spec.shirt, spec.secondary}
    assert (read.shorts, read.socks) == (spec.shorts, spec.socks)
