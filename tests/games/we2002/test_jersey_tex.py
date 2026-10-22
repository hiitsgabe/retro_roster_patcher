"""The 3D kit textures: reading a TEX file, painting a kit into it, previewing
one, and the patcher's `kits` option end to end.

No TEX file from the disc is here: `build_tex` lays one out from the format's
own rules (twelve slots, LZSS 8bpp 128x128 pages, raw 256-entry CLUTs) with
flat index planes, so a repainted pixel can be read back exactly. The disc
images are the sparse synthetic ones `test_tex_patching` builds, with these
files in their TEX slots. Everything that paints needs Pillow.
"""

import io
import struct

import pytest

from retro_roster_patcher.core.errors import CapabilityError, RomError
from retro_roster_patcher.core.kits import KitSpec, KitTemplate, render_kit
from retro_roster_patcher.core.models import MappedRosters, SlotMapping
from retro_roster_patcher.games.we2002 import jersey_tex as jt
from retro_roster_patcher.games.we2002 import patcher as patcher_module
from retro_roster_patcher.games.we2002.flag_atlas import lz_compress
from retro_roster_patcher.games.we2002.models import WETeamRecord
from retro_roster_patcher.games.we2002.patcher import WE2002Patcher
from retro_roster_patcher.games.we2002.rom_writer import (
    _OFS_ANT_MAGLIE2,
    RomWriter,
    _read_cd_file_from_handle,
)
from tests.games.we2002.test_patcher import _league_data, _make_patcher, _roster
from tests.games.we2002.test_tex_patching import _build_iso, _tex_lba

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

RED, WHITE, BLUE, BLACK = (255, 0, 0), (255, 255, 255), (0, 0, 255), (0, 0, 0)
#: Indices the synthetic pages are painted with.
OUTFIELD, GK, FONT = 1, 5, 240
NECK = (18, 0, 25, 3)  # transparent pixels inside the collar region
GLOVE = (49, 49, 54, 53)  # goalkeeper art below x 64

#: Payload order in every file on the disc, by slot.
FILE_ORDER = (0, 9, 1, 4, 2, 10, 3, 5, 7, 8, 11)
IMAGE_RECTS = {
    0: (576, 256),
    9: (576, 384),
    2: (576, 256),
    10: (576, 384),
    7: (704, 256),
    11: (768, 384),
}
CLUT_RECTS = {1: (0, 486), 3: (0, 486), 4: (0, 488), 5: (0, 488), 8: (256, 480)}


def _bgr555(c):
    v = (c[0] >> 3) | ((c[1] >> 3) << 5) | ((c[2] >> 3) << 10)
    return v or 0x8000


def clut(colours):
    """A 512-byte CLUT: index 0 transparent, `colours` (index -> RGB) set, the
    rest opaque black."""
    entries = [0] + [0x8000] * 255
    for k, c in colours.items():
        entries[k] = _bgr555(c)
    return struct.pack("<256H", *entries)


def page(outfield=OUTFIELD, gk=GK):
    """A 128x256 index page: `outfield` below x 64, `gk` from x 64, the neck
    hole transparent, the glove patch index 7 and the number font 240-243."""
    rows = []
    for y in range(256):
        row = bytearray(outfield if x < 64 else gk for x in range(128))
        if NECK[1] <= y <= NECK[3]:
            row[NECK[0] : NECK[2] + 1] = bytes(NECK[2] - NECK[0] + 1)
        if GLOVE[1] <= y <= GLOVE[3]:
            row[GLOVE[0] : GLOVE[2] + 1] = bytes([7]) * (GLOVE[2] - GLOVE[0] + 1)
        if 68 <= y <= 80:
            row[64:128] = bytes(FONT + (x % 4) for x in range(64, 128))
        rows.append(bytes(row))
    return b"".join(rows)


def build_tex(home=None, home_clut=None, away=None, away_clut=None, gk_clut=None):
    """A TEX file in the disc's layout around the given pages and CLUTs."""
    home = page() if home is None else home
    away = page(outfield=2) if away is None else away
    home_clut = (
        clut({OUTFIELD: WHITE, 2: BLUE, 7: BLACK, 240: BLACK, 241: RED, 242: RED, 243: RED})
        if home_clut is None
        else home_clut
    )
    away_clut = (
        clut({OUTFIELD: WHITE, 2: BLUE, 7: BLACK, 240: BLACK, 241: RED, 242: RED, 243: RED})
        if away_clut is None
        else away_clut
    )
    gk_clut = (
        clut({GK: RED, 7: BLACK, 240: BLACK, 241: WHITE, 242: WHITE, 243: WHITE})
        if gk_clut is None
        else gk_clut
    )
    payloads = {
        0: lz_compress(home[:16384]),
        9: lz_compress(home[16384:]),
        1: home_clut,
        4: gk_clut,
        2: lz_compress(away[:16384]),
        10: lz_compress(away[16384:]),
        3: away_clut,
        5: gk_clut,
        7: lz_compress(bytes(16384)),
        8: clut({}),
        11: lz_compress(bytes(16384)),
    }
    header = [0] * 12
    body = bytearray()
    for slot in FILE_ORDER:
        data = payloads[slot]
        data_off = 48 + len(body)
        body += data + bytes(-len(data) % 4)
        header[slot] = jt.BASE + 48 + len(body)
        if slot in IMAGE_RECTS:
            x, y = IMAGE_RECTS[slot]
            record = struct.pack(
                "<HHHHHHI", 0x000A, x, y, 64, 64 if slot == 7 else 128, 0, jt.BASE + data_off
            )
        else:
            x, y = CLUT_RECTS[slot]
            record = struct.pack("<HHHHHHI", 0xFF09, x, y, 256, 1, 0, jt.BASE + data_off)
        body += record + struct.pack("<HHHHHHI", 0xFF, 0, 0, 0, 0, 0, 0)
    return struct.pack("<12I", *header) + bytes(body)


def pixel(tex, which, x, y, gk=False):
    """The RGB of page pixel (x, y) of kit `which`, through its CLUT, None if transparent."""
    blocks = jt.parse(tex)
    data = blocks[f"{which}_top"].data + blocks[f"{which}_bottom"].data
    v = data[y * 128 + x]
    pal = jt.palette(blocks[f"{which}_gk_clut" if gk else f"{which}_clut"].data)
    return None if v == 0 else pal[v]


def nearest(colour, *candidates):
    return min(candidates, key=lambda c: sum((a - b) ** 2 for a, b in zip(colour, c, strict=True)))


def _indices(tex, which):
    blocks = jt.parse(tex)
    return blocks[f"{which}_top"].data + blocks[f"{which}_bottom"].data


STRIPED = KitSpec(shirt=RED, secondary=WHITE, pattern="stripes", shorts=WHITE, socks=RED)
PLAIN = KitSpec(shirt=BLUE, secondary=WHITE, pattern="plain", shorts=WHITE, socks=BLUE)


# ---------------------------------------------------------------------------
# Reading.
# ---------------------------------------------------------------------------


def test_parse_returns_every_slot_with_the_pages_decoded():
    tex = build_tex()
    blocks = jt.parse(tex)
    assert set(blocks) == set(jt.SLOTS.values())
    assert blocks["home_top"].data + blocks["home_bottom"].data == page()
    assert blocks["away_top"].data + blocks["away_bottom"].data == page(outfield=2)
    assert len(blocks["home_clut"].data) == 512


def test_parse_refuses_a_file_without_the_twelve_slots():
    with pytest.raises(Exception, match="lacks|record"):
        jt.parse(bytes(64))


def test_render_page_draws_the_outfield_and_goalkeeper_through_their_own_cluts():
    tex = build_tex()
    outfield = jt.render_page(tex, "home")
    keeper = jt.render_page(tex, "home", gk=True)
    assert outfield.size == (128, 256)
    assert outfield.getpixel((20, 20)) == WHITE
    assert keeper.getpixel((90, 20)) == RED


# ---------------------------------------------------------------------------
# Painting a spec.
# ---------------------------------------------------------------------------


def test_stripes_put_the_secondary_colour_on_alternate_four_pixel_bands_of_the_front():
    out = jt.apply_spec(build_tex(), "home", STRIPED)
    y = 15
    bands = [nearest(pixel(out, "home", x, y), RED, WHITE) for x in range(12, 32)]
    assert bands == [RED] * 4 + [WHITE] * 4 + [RED] * 4 + [WHITE] * 4 + [RED] * 4


def test_stripes_run_on_across_the_sleeves_and_sides():
    out = jt.apply_spec(build_tex(), "home", STRIPED)
    sleeve = [nearest(pixel(out, "home", x, 70), RED, WHITE) for x in range(0, 16)]
    assert sleeve == [WHITE] * 4 + [RED] * 4 + [WHITE] * 4 + [RED] * 4
    assert nearest(pixel(out, "home", 5, 20), RED, WHITE) == RED  # side, band 1
    assert nearest(pixel(out, "home", 9, 20), RED, WHITE) == WHITE  # side, band 2


def test_a_plain_spec_colours_the_whole_front_and_its_shorts_and_socks():
    out = jt.apply_spec(build_tex(), "home", PLAIN)
    front = {
        nearest(pixel(out, "home", x, y), BLUE, WHITE) for x in range(12, 32) for y in range(6, 30)
    }
    assert front == {BLUE}
    assert nearest(pixel(out, "home", 30, 40), BLUE, WHITE) == WHITE  # shorts
    assert nearest(pixel(out, "home", 10, 60), BLUE, WHITE) == BLUE  # socks
    assert nearest(pixel(out, "home", 14, 2), BLUE, WHITE) == WHITE  # collar: secondary


def test_hoops_run_across_rows_halves_split_the_front_and_sleeves_take_their_colour():
    hoops = jt.apply_spec(build_tex(), "home", KitSpec(RED, WHITE, "hoops", WHITE, RED))
    assert nearest(pixel(hoops, "home", 20, 7), RED, WHITE) == RED
    assert nearest(pixel(hoops, "home", 20, 11), RED, WHITE) == WHITE
    halves = jt.apply_spec(build_tex(), "home", KitSpec(RED, WHITE, "halves", WHITE, RED))
    assert nearest(pixel(halves, "home", 13, 20), RED, WHITE) == RED
    assert nearest(pixel(halves, "home", 30, 20), RED, WHITE) == WHITE
    assert nearest(pixel(halves, "home", 40, 70), RED, WHITE) == WHITE  # right sleeve
    sleeved = jt.apply_spec(
        build_tex(), "home", KitSpec(RED, WHITE, "plain", WHITE, RED, sleeves=BLUE)
    )
    assert nearest(pixel(sleeved, "home", 10, 70), RED, WHITE, BLUE) == BLUE
    assert nearest(pixel(sleeved, "home", 20, 20), RED, WHITE, BLUE) == RED


def test_painting_shades_by_the_base_arts_luminance():
    # The base shirt is a mid grey with a white band across its top third:
    # the band sets the bright reference, so blue painted over the grey comes
    # out at about half intensity and over the band at full.
    grey = clut({OUTFIELD: (128, 128, 128), 2: WHITE})
    base = page()
    band = bytes(2 if x < 64 else GK for x in range(128)) * 8
    base = base[: 6 * 128] + band + base[14 * 128 :]
    out = jt.apply_spec(build_tex(home=base, home_clut=grey), "home", PLAIN)
    r, g, b = pixel(out, "home", 20, 20)
    assert b < 160 and r == g == 0
    assert pixel(out, "home", 20, 10)[2] > 240


def test_painting_leaves_the_goalkeeper_half_the_glove_patch_and_the_font_alone():
    tex = build_tex()
    out = jt.apply_spec(tex, "home", STRIPED)
    before, after = _indices(tex, "home"), _indices(out, "home")
    assert all(
        before[y * 128 + x] == after[y * 128 + x] for y in range(256) for x in range(64, 128)
    )
    assert all(
        before[y * 128 + x] == after[y * 128 + x]
        for y in range(GLOVE[1], GLOVE[3] + 1)
        for x in range(GLOVE[0], GLOVE[2] + 1)
    )
    assert jt.parse(out)["home_clut"].data[480:] == jt.parse(tex)["home_clut"].data[480:]
    assert jt.parse(out)["home_gk_clut"].data == jt.parse(tex)["home_gk_clut"].data


def test_painting_keeps_the_neck_hole_transparent_and_index_zero_transparent():
    out = jt.apply_spec(build_tex(), "home", STRIPED)
    assert pixel(out, "home", 20, 2) is None
    assert jt.parse(out)["home_clut"].data[:2] == b"\x00\x00"


def test_painting_the_away_kit_leaves_the_home_kit_untouched():
    tex = build_tex()
    out = jt.apply_spec(tex, "away", STRIPED)
    assert _indices(out, "home") == _indices(tex, "home")
    assert jt.parse(out)["home_clut"].data == jt.parse(tex)["home_clut"].data
    assert nearest(pixel(out, "away", 13, 15), RED, WHITE, BLUE) == RED


def test_a_base_template_supplies_the_art_the_kit_is_painted_on():
    # The base's neck hole is wider than the target's own; the painted target
    # takes the base's transparency.
    wide = page()
    wide = (
        wide[: 2 * 128]
        + bytes(0 if 12 <= x <= 31 else wide[2 * 128 + x] for x in range(128))
        + wide[3 * 128 :]
    )
    base = build_tex(home=wide)
    out = jt.apply_spec(build_tex(), "home", PLAIN, base)
    assert pixel(out, "home", 13, 2) is None
    assert pixel(jt.apply_spec(build_tex(), "home", PLAIN), "home", 13, 2) is not None


def test_a_repainted_file_stays_within_what_the_game_loads():
    out = jt.apply_spec(build_tex(), "home", STRIPED)
    assert len(out) <= jt.MAX_BYTES
    assert jt.MAX_BYTES == 18 * 2048


def test_a_file_that_cannot_be_made_to_fit_is_refused(monkeypatch):
    monkeypatch.setattr(jt, "MAX_BYTES", 100)
    with pytest.raises(ValueError, match="over the 100"):
        jt.apply_spec(build_tex(), "home", STRIPED)


def test_the_colour_budget_is_cut_until_the_file_fits(monkeypatch):
    # A page of many colours compresses badly; a budget of 16 compresses well.
    # Set the cap between the two and the painter must take the smaller palette.
    calls = []
    real = jt._quantise

    def counting(colours, budget):
        calls.append(budget)
        return real(colours, budget)

    monkeypatch.setattr(jt, "_quantise", counting)
    noisy = KitSpec(RED, WHITE, "plain", WHITE, RED)
    gradient = clut({k: (k, 255 - k, 128) for k in range(1, 240)})
    base = b"".join(
        bytes(1 + (x * 7 + y * 3) % 239 if x < 64 else GK for x in range(128)) for y in range(256)
    )
    tex = build_tex(home=base, home_clut=gradient)
    full = jt.apply_spec(tex, "home", noisy)
    small = jt.apply_spec(tex, "home", KitSpec(WHITE, WHITE, "plain", WHITE, WHITE))
    assert len(full) > len(small)
    calls.clear()
    monkeypatch.setattr(jt, "MAX_BYTES", len(full) - 1)
    out = jt.apply_spec(tex, "home", noisy)
    assert len(out) < len(full)
    assert calls[0] == jt.COLOURS and len(calls) > 1


# ---------------------------------------------------------------------------
# Painting a picture, copying a template, previewing.
# ---------------------------------------------------------------------------


def test_a_picture_of_a_kit_is_mapped_onto_the_regions():
    png = render_kit(
        KitSpec(shirt=BLUE, secondary=WHITE, pattern="plain", shorts=WHITE, socks=BLUE)
    )
    out = jt.apply_image(build_tex(), "home", png)
    assert nearest(pixel(out, "home", 20, 18), BLUE, WHITE, RED) == BLUE  # chest
    assert nearest(pixel(out, "home", 30, 40), BLUE, WHITE, RED) == WHITE  # shorts
    assert nearest(pixel(out, "home", 10, 60), BLUE, WHITE, RED) == BLUE  # socks


def test_a_shirt_alone_gets_white_shorts():
    shirt = Image.new("RGBA", (64, 40), (*RED, 255))
    buf = io.BytesIO()
    shirt.save(buf, "PNG")
    out = jt.apply_image(build_tex(), "home", buf.getvalue())
    assert nearest(pixel(out, "home", 20, 18), RED, WHITE) == RED
    assert nearest(pixel(out, "home", 30, 40), RED, WHITE) == WHITE


def test_copying_a_template_takes_its_home_pages_and_both_palettes():
    template = build_tex(
        home=page(outfield=3), home_clut=clut({3: BLUE}), gk_clut=clut({GK: BLACK})
    )
    out = jt.copy_kit(build_tex(), "away", template)
    assert _indices(out, "away") == page(outfield=3)
    assert jt.parse(out)["away_clut"].data == clut({3: BLUE})
    assert jt.parse(out)["away_gk_clut"].data == clut({GK: BLACK})
    assert _indices(out, "home") == page()


def test_the_preview_is_a_kit_shaped_png():
    png = jt.template_preview(build_tex())
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG" and img.size == (96, 128)
    assert img.getpixel((48, 36))[:3] == WHITE  # chest
    assert img.getpixel((0, 127))[3] == 0  # background


def test_every_template_has_a_name():
    assert sorted(jt.JERSEY_TEMPLATES) == list(range(95))
    assert jt.JERSEY_TEMPLATES[jt.DEFAULT_TEMPLATE].startswith("Clas. England")


# ---------------------------------------------------------------------------
# Through the patcher.
# ---------------------------------------------------------------------------

SLOT, TEMPLATE = 0, 66


def _disc(tmp_path, more=None):
    """A patchable synthetic image holding TEX 63 (slot 0) and 66, plus `more`."""
    files = {63: build_tex(), TEMPLATE: build_tex(home=page(outfield=3), home_clut=clut({3: BLUE}))}
    files.update(more or {})
    path = _build_iso(tmp_path, files, sectors=6)
    with open(path, "r+b") as f:
        f.truncate(101 * 1024 * 1024)
    return path


def _tex(path, index):
    writer = RomWriter(str(path), str(path))
    writer._ensure_tex_cache()
    with open(path, "rb") as f:
        return _read_cd_file_from_handle(f, _tex_lba(index), writer._tex_sizes[index])


def _record(**kwargs):
    return WETeamRecord(name="Synthetic", short_name="SYN", **kwargs)


def _patch(path, rosters, **options):
    patcher = WE2002Patcher(cache_dir=path.parent / "cache")
    return patcher.patch(rom_path=path, output_path=path, rosters=rosters, logos=False, **options)


def test_a_template_choice_copies_that_design_into_the_slot(tmp_path):
    path = _disc(tmp_path)
    rosters = MappedRosters(game_id="we2002", teams={SLOT: _record()})
    _patch(path, rosters, kits={SLOT: {"home": {"template": TEMPLATE}}})
    out = _tex(path, 63)
    assert _indices(out, "home") == page(outfield=3)
    assert jt.parse(out)["home_clut"].data == clut({3: BLUE})
    assert rosters.teams[SLOT].kit_home_template == TEMPLATE


def test_a_spec_choice_paints_the_slots_own_file(tmp_path):
    path = _disc(tmp_path)
    rosters = MappedRosters(game_id="we2002", teams={SLOT: _record()})
    _patch(path, rosters, kits={SLOT: {"away": STRIPED.to_dict()}})
    out = _tex(path, 63)
    assert nearest(pixel(out, "away", 13, 15), RED, WHITE, BLUE) == RED
    assert nearest(pixel(out, "away", 17, 15), RED, WHITE, BLUE) == WHITE
    assert _indices(out, "home") == page()
    assert rosters.teams[SLOT].kit_away == RED  # the preview and flag follow


def test_the_records_default_specs_are_painted_without_an_option(tmp_path):
    path = _disc(tmp_path)
    rosters = MappedRosters(
        game_id="we2002", teams={SLOT: _record(kit_home_spec=PLAIN, kit_home=BLUE)}
    )
    _patch(path, rosters)
    out = _tex(path, 63)
    assert nearest(pixel(out, "home", 20, 20), BLUE, WHITE, RED) == BLUE


def test_kits_false_leaves_the_texture_and_the_preview_as_the_disc_has_them(tmp_path):
    path = _disc(tmp_path)
    with open(path, "r+b") as f:
        f.seek(_OFS_ANT_MAGLIE2 + SLOT * 64)
        f.write(bytes(range(64)))
    before_tex = _tex(path, 63)
    rosters = MappedRosters(
        game_id="we2002", teams={SLOT: _record(kit_home_spec=STRIPED, kit_home=RED)}
    )
    _patch(path, rosters, kits=False)
    assert _tex(path, 63) == before_tex
    with open(path, "rb") as f:
        f.seek(_OFS_ANT_MAGLIE2 + SLOT * 64)
        assert f.read(64) == bytes(range(64))


def test_the_preview_palette_follows_the_spec(tmp_path):
    path = _disc(tmp_path)
    rosters = MappedRosters(
        game_id="we2002", teams={SLOT: _record(kit_home_spec=STRIPED, kit_home=RED)}
    )
    _patch(path, rosters)
    with open(path, "rb") as f:
        f.seek(_OFS_ANT_MAGLIE2 + SLOT * 64)
        home = struct.unpack("<16H", f.read(32))
    assert home[2] == _bgr555(RED) and home[3] == _bgr555(WHITE) and home[10] == _bgr555(WHITE)
    assert home[1] == _bgr555((16, 16, 16)) and home[9] == _bgr555((152, 152, 152))


def test_an_unknown_template_is_refused():
    with pytest.raises(CapabilityError, match="95|0..94"):
        WE2002Patcher._kit_choice({"template": 95})


def test_kit_templates_lists_one_preview_per_tex_file(tmp_path):
    path = _disc(tmp_path, {i: build_tex() for i in range(95) if i not in (63, TEMPLATE)})
    templates = WE2002Patcher(cache_dir=tmp_path / "cache").kit_templates(path)
    assert len(templates) == 95
    assert all(isinstance(t, KitTemplate) and t.png.startswith(b"\x89PNG") for t in templates)
    assert [t.index for t in templates] == list(range(95))
    assert templates[TEMPLATE].name == jt.JERSEY_TEMPLATES[TEMPLATE]


def test_map_rosters_gives_every_record_its_default_kits(tmp_path):
    patcher = _make_patcher(tmp_path)
    data = _league_data([_roster(100, color="C60000", alternate_color="FFFFFF"), _roster(101)])
    mapped = patcher.map_rosters(
        data,
        slot_mapping=[
            SlotMapping(slot_index=0, team_id=100),
            SlotMapping(slot_index=1, team_id=101),
        ],
    )
    home, away = mapped.teams[0].kit_home_spec, mapped.teams[0].kit_away_spec
    assert home == KitSpec((198, 0, 0), WHITE, "plain", WHITE, (198, 0, 0))
    assert away == KitSpec(WHITE, (198, 0, 0), "plain", (198, 0, 0), WHITE)
    assert mapped.teams[0].kit_home == home.shirt
    assert mapped.teams[1].kit_home_spec is None and mapped.teams[1].kit_away_spec is None


# ---------------------------------------------------------------------------
# The page itself: `page_png` out, `apply_page` back.
# ---------------------------------------------------------------------------


def _png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_page_png_is_the_page_with_the_neck_hole_transparent_and_both_halves_coloured():
    img = Image.open(io.BytesIO(jt.page_png(build_tex(), "home", guides=False)))
    assert img.size == (128, 256) and img.mode == "RGBA"
    assert img.getpixel((20, 20)) == (*WHITE, 255)  # outfield through its CLUT
    assert img.getpixel((90, 20)) == (*RED, 255)  # goalkeeper through its own
    assert img.getpixel((20, 2))[3] == 0  # neck hole


def test_guides_outline_every_region_in_pure_red():
    img = Image.open(io.BytesIO(jt.page_png(build_tex(), "home")))
    for region in jt.REGIONS:
        assert img.getpixel((region.x0, region.y0)) == (255, 0, 0, 255)
        assert img.getpixel((region.x1, region.y1)) == (255, 0, 0, 255)
    assert img.getpixel((20, 20)) == (*WHITE, 255)  # the inside is left alone


def test_a_page_round_trips_through_apply_page():
    tex = build_tex()
    out = jt.apply_page(tex, "home", jt.page_png(tex, "home", guides=False))
    assert jt.render_page(out, "home").tobytes() == jt.render_page(tex, "home").tobytes()
    assert _indices(out, "home")[: 128 * 256 : 128] == _indices(tex, "home")[: 128 * 256 : 128]
    assert jt.parse(out)["home_clut"].data[480:] == jt.parse(tex)["home_clut"].data[480:]
    assert jt.parse(out)["home_gk_clut"].data == jt.parse(tex)["home_gk_clut"].data


def test_apply_page_paints_opaque_pixels_and_keeps_the_art_under_transparent_ones():
    tex = build_tex()
    art = Image.new("RGBA", (128, 256), (0, 0, 0, 0))
    for y in range(6, 30):
        for x in range(12, 32):
            art.putpixel((x, y), (*BLUE, 255))
    art.putpixel((20, 2), (*RED, 255))  # painted into the neck hole
    art.putpixel((90, 20), (*BLUE, 255))  # the goalkeeper's half: ignored
    art.putpixel((50, 50), (*BLUE, 255))  # the glove patch: ignored
    out = jt.apply_page(tex, "home", _png(art))
    assert nearest(pixel(out, "home", 20, 20), BLUE, WHITE, RED) == BLUE
    assert nearest(pixel(out, "home", 5, 20), BLUE, WHITE, RED) == WHITE  # side kept
    assert nearest(pixel(out, "home", 20, 2), BLUE, WHITE, RED) == RED
    assert pixel(out, "home", 21, 2) is None  # the rest of the hole stays transparent
    before, after = _indices(tex, "home"), _indices(out, "home")
    assert all(
        before[y * 128 + x] == after[y * 128 + x] for y in range(256) for x in range(64, 128)
    )
    assert after[50 * 128 + 50] == before[50 * 128 + 50]


def test_apply_page_refuses_a_picture_of_another_size():
    with pytest.raises(ValueError, match="128x256"):
        jt.apply_page(build_tex(), "home", _png(Image.new("RGBA", (96, 128))))


def test_is_page_png_reads_the_size_off_the_header():
    assert jt.is_page_png(_png(Image.new("RGBA", (128, 256))))
    assert not jt.is_page_png(_png(Image.new("RGBA", (96, 128))))
    assert not jt.is_page_png(b"not a png")


def test_a_page_picture_in_the_kits_option_is_applied_as_the_page(tmp_path):
    path = _disc(tmp_path)
    art = Image.new("RGBA", (128, 256), (0, 0, 0, 0))
    for y in range(6, 30):
        for x in range(12, 32):
            art.putpixel((x, y), (*BLUE, 255))
    rosters = MappedRosters(game_id="we2002", teams={SLOT: _record(kit_home=RED)})
    _patch(path, rosters, kits={SLOT: {"home": {"image": _png(art)}}})
    out = _tex(path, 63)
    assert nearest(pixel(out, "home", 20, 20), BLUE, WHITE, RED) == BLUE
    assert nearest(pixel(out, "home", 5, 20), BLUE, WHITE, RED) == WHITE
    assert rosters.teams[SLOT].kit_home == RED  # no colour guessed off a page


def test_kit_page_png_reads_the_slots_file_or_a_template(tmp_path):
    path = _disc(tmp_path)
    patcher = WE2002Patcher(cache_dir=tmp_path / "cache")
    own = Image.open(io.BytesIO(patcher.kit_page_png(path, 63, "home", guides=False)))
    assert own.getpixel((20, 20)) == (*WHITE, 255)
    design = Image.open(io.BytesIO(patcher.kit_page_png(path, 63, template=TEMPLATE, guides=False)))
    assert design.getpixel((20, 20)) == (*BLUE, 255)
    with pytest.raises(CapabilityError, match="0..94"):
        patcher.kit_page_png(path, 95)


# ---------------------------------------------------------------------------
# The mounted preview of a choice, nothing written.
# ---------------------------------------------------------------------------


def _preview_pixel(png, x, y):
    return Image.open(io.BytesIO(png)).getpixel((x, y))


def test_the_preview_of_a_stripes_choice_alternates_across_the_chest(tmp_path):
    path = _disc(tmp_path)
    before = path.read_bytes()
    png = WE2002Patcher(cache_dir=tmp_path / "cache").kit_preview_png(
        path, 63, "home", STRIPED.to_dict()
    )
    x0, y0 = jt.PREVIEW_FRONT_AT
    y = y0 + 24
    # The chest is scaled 2x, so the 4 px bands of the texture are 8 px wide.
    bands = [nearest(_preview_pixel(png, x0 + 8 * k + 4, y)[:3], RED, WHITE) for k in range(5)]
    assert bands == [RED, WHITE, RED, WHITE, RED]
    assert _preview_pixel(png, 0, 127)[3] == 0  # transparent background
    assert path.read_bytes() == before  # nothing written


def test_the_preview_of_no_choice_is_the_current_kits_photo(tmp_path):
    path = _disc(tmp_path)
    patcher = WE2002Patcher(cache_dir=tmp_path / "cache")
    assert patcher.kit_preview_png(path, 63, "home") == jt.template_preview(_tex(path, 63), "home")
    assert patcher.kit_preview_png(path, 63, "away") == jt.template_preview(_tex(path, 63), "away")


def test_the_preview_of_a_template_choice_shows_that_design(tmp_path):
    path = _disc(tmp_path)
    patcher = WE2002Patcher(cache_dir=tmp_path / "cache")
    png = patcher.kit_preview_png(path, 63, "home", {"template": TEMPLATE})
    x0, y0 = jt.PREVIEW_FRONT_AT
    assert nearest(_preview_pixel(png, x0 + 20, y0 + 24)[:3], BLUE, WHITE) == BLUE
    assert png == jt.template_preview(
        jt.copy_kit(_tex(path, 63), "home", _tex(path, TEMPLATE)), "home"
    )


def test_the_preview_refuses_a_slot_off_the_disc(tmp_path):
    path = _disc(tmp_path)
    with pytest.raises(CapabilityError, match="0..94"):
        WE2002Patcher(cache_dir=tmp_path / "cache").kit_preview_png(path, 95)


# ---------------------------------------------------------------------------
# Review findings.
# ---------------------------------------------------------------------------

from retro_roster_patcher.games.we2002 import rom_writer as rw  # noqa: E402
from tests.games.we2002.test_patcher import _fake_writer_class, _silence_translation  # noqa: E402


def test_black_cloth_in_the_preview_palette_keeps_its_stp_bit():
    # 0x0000 is the CLUT's transparent entry; true black must pack to 0x8000.
    black = KitSpec(BLACK, WHITE, "stripes", BLACK, BLACK)
    home = struct.unpack("<16H", rw._maglia_half(black, BLACK, BLACK))
    assert home[0] == 0
    assert all(v == 0x8000 for k, v in enumerate(home) if k in (2, 5, 7, 8, 10, 11, 12, 13, 14, 15))
    legacy = struct.unpack("<16H", rw._maglia_half(None, BLACK, BLACK))
    assert legacy[0] == 0 and all(v == 0x8000 for v in legacy[2:])


@pytest.mark.parametrize(
    "kit",
    [
        {"image": "/nonexistent/kit.png"},
        {"image": b"not a png"},
        {"shirt": "zzz", "secondary": "#ffffff", "shorts": "#ffffff", "socks": "#ffffff"},
        {"shirt": "#ff0000"},
        {"template": "ninety"},
        {"template": 95},
        "plain",
    ],
)
def test_a_bad_kit_option_is_refused_before_the_disc_is_copied(tmp_path, monkeypatch, kit):
    log = []
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class(log))
    _silence_translation(monkeypatch)
    rom = tmp_path / "we2002.bin"
    with rom.open("wb") as handle:
        handle.write(b"\x00" + b"\xff" * 10 + b"\x00")
        handle.truncate(100 * 1024 * 1024)
    patcher = WE2002Patcher(cache_dir=tmp_path / "cache")
    rosters = MappedRosters(game_id="we2002", teams={SLOT: _record()})
    with pytest.raises(CapabilityError, match=f"slot {SLOT} home kit"):
        patcher.patch(
            rom_path=rom,
            output_path=tmp_path / "out.bin",
            rosters=rosters,
            kits={SLOT: {"home": kit}},
        )
    assert log == []  # the writer was never built, so nothing was copied


def test_a_kit_that_cannot_be_painted_costs_that_kit_only(tmp_path, monkeypatch):
    path = _disc(tmp_path)
    real = rw.apply_choice

    def failing(tex, which, spec, image, template, files):
        if which == "away":
            raise ValueError("no room")
        return real(tex, which, spec, image, template, files)

    monkeypatch.setattr(rw, "apply_choice", failing)
    lines = []
    patcher = WE2002Patcher(cache_dir=tmp_path / "cache", on_status=lines.append)
    rosters = MappedRosters(
        game_id="we2002", teams={SLOT: _record(kit_home_spec=PLAIN, kit_away_spec=STRIPED)}
    )
    patcher.patch(rom_path=path, output_path=path, rosters=rosters, logos=False)
    out = _tex(path, 63)
    assert nearest(pixel(out, "home", 20, 20), BLUE, WHITE, RED) == BLUE  # home landed
    assert _indices(out, "away") == page(outfield=2)  # away left as it was
    assert any("TEX_63 away kit skipped: no room" in line for line in lines)


def test_a_kit_over_what_the_game_loads_is_skipped_with_a_line(tmp_path, monkeypatch):
    path = _disc(tmp_path)
    # Only the home kit has a choice; the away call must still hand `tex` back.
    monkeypatch.setattr(
        rw,
        "apply_choice",
        lambda tex, which, *a: bytes(rw._TEX_MAX_BYTES + 1) if which == "home" else tex,
    )
    writer = RomWriter(str(path), str(path))
    writer.write_team(SLOT, _record(kit_home_spec=PLAIN), players=None, include_flag=False)
    before = _tex(path, 63)
    writer.flush_tex_patches()
    assert _tex(path, 63) == before
    assert writer.kit_problems == [
        f"TEX_63 home kit skipped: {rw._TEX_MAX_BYTES + 1} bytes, over the 36864 the game loads"
    ]


def test_map_rosters_mirrors_the_specs_shirts_onto_the_rgb_fields(tmp_path):
    patcher = _make_patcher(tmp_path)
    data = _league_data([_roster(100, color="C60000")])
    record = patcher.map_rosters(data, slot_mapping=[SlotMapping(slot_index=0, team_id=100)]).teams[
        0
    ]
    assert record.kit_away_spec.shirt == WHITE  # the contrast of the one colour given
    assert record.kit_away == WHITE and record.kit_home == (198, 0, 0) == record.kit_third


@pytest.mark.parametrize("method", ["kit_templates", "kit_page_png", "kit_preview_png"])
def test_the_readers_refuse_a_file_that_is_not_a_raw_we2002_image(tmp_path, method):
    small = tmp_path / "small.bin"
    small.write_bytes(bytes(4096))
    cooked = tmp_path / "cooked.iso"
    with cooked.open("wb") as handle:
        handle.truncate(101 * 1024 * 1024)
    patcher = WE2002Patcher(cache_dir=tmp_path / "cache")
    for path in (small, cooked):
        with pytest.raises(RomError, match="Not a raw WE2002 image"):
            getattr(patcher, method)(path) if method == "kit_templates" else getattr(
                patcher, method
            )(path, 63)


def test_a_directory_cut_short_in_a_record_is_read_without_raising():
    from tests.games.we2002.test_tex_patching import _dir_record

    blob = bytearray(_dir_record(b"TEX_63.BIN;1", 4096)) + bytes([40]) + bytes(10)
    sizes, _ = rw._build_tex_dir_map_from_dir(blob)
    assert sizes == {63: 4096}
