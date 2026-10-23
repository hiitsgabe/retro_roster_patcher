"""Kits and blank slots: `patch(kits=...)`, `kit_preview_png`, `kit_templates`
and `patch(blank_unused=True)`.

Every check reads the output image back at the offsets the synthetic ROM
fixture states, never what the writer was handed.
"""

from __future__ import annotations

import io
import struct

import pytest

from retro_roster_patcher.core import kits as core_kits
from retro_roster_patcher.core.errors import CapabilityError
from retro_roster_patcher.core.kits import KitSpec
from retro_roster_patcher.core.models import MappedRosters
from retro_roster_patcher.games.iss_snes import ISSPatcher, kits
from retro_roster_patcher.games.iss_snes.models import ISSPlayerRecord, ISSTeamRecord
from retro_roster_patcher.games.iss_snes.patcher import GK_KIT
from retro_roster_patcher.games.iss_snes.rom_writer import (
    Flag,
    _bgr555_to_rgb,
    _kit_offsets,
    _make_shades,
    _pinned,
    _rgb_to_bgr555,
    _rgb_to_predominant,
    number_colour,
    outfield_block,
    two_band_flag,
)
from tests.fixtures import synthetic_iss_rom as fixture
from tests.games.iss_snes.test_rom_writer import _colour_record, _pixels

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

RED = (255, 0, 0)
BLUE = (0, 0, 255)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
STRIPED = KitSpec(shirt=WHITE, secondary=BLACK, pattern="stripes", shorts=BLACK, socks=WHITE)
ARGENTINA = 18


@pytest.fixture
def rom(tmp_path):
    return fixture.write_iss_rom(tmp_path / "iss.sfc")


@pytest.fixture
def out(tmp_path):
    return tmp_path / "patched.sfc"


@pytest.fixture
def patcher(tmp_path):
    return ISSPatcher(cache_dir=tmp_path / "cache")


def _rosters(*slots, **extra):
    teams = {}
    for slot in slots:
        record = ISSTeamRecord(
            name=f"Club {slot}",
            short_name=f"C{slot:02d}",
            players=[ISSPlayerRecord(name=f"P{i:02d}", shirt_number=i + 1) for i in range(15)],
            flag_colors=[RED, BLUE],
            kit_home=(RED, WHITE, RED),
            kit_away=(BLUE, WHITE, BLUE),
        )
        for key, value in extra.items():
            setattr(record, key, value)
        teams[slot] = record
    return MappedRosters(game_id="iss-snes", teams=teams)


def _chest(image, slot):
    """`slot`'s eight pose pointers, as SNES addresses."""
    return [
        int.from_bytes(image[at : at + 3], "little")
        for at in (
            fixture.OFS_CHEST_PTRS + (pose * fixture.CHEST_SLOTS + slot) * 3
            for pose in range(fixture.CHEST_POSES)
        )
    ]


def _design(design):
    """The pointers of a design whose poses lie `POSE_STRIDE` apart."""
    return [design + pose * fixture.POSE_STRIDE for pose in range(fixture.CHEST_POSES)]


def _block(image, slot, kit=0):
    at = _kit_offsets(slot)[kit]
    return image[at : at + 16]


def _png(colour):
    image = Image.new("RGB", (60, 80), (0, 200, 0))
    image.paste(colour, (10, 10, 50, 70))
    data = io.BytesIO()
    image.save(data, "PNG")
    return data.getvalue()


def test_the_patcher_declares_kits_and_blank_slots():
    assert ISSPatcher.supports_kits is True
    assert ISSPatcher.supports_blank_unused is True


# -- kits ----------------------------------------------------------------------


def test_a_mapped_club_starts_from_its_catalogue_kits(patcher):
    from retro_roster_patcher.sports.models import Team

    club = next(c for c in core_kits.kit_catalog()["clubs"] if c.get("espn_id"))
    team = Team(id=club["espn_id"], name=club["name"], color="000000", alternate_color="ffffff")
    record = ISSTeamRecord(name="", short_name="")
    patcher._apply_colours(record, team)
    home, away = core_kits.catalog_kits(team) or (None, None)
    assert (record.kit_home_spec, record.kit_away_spec) == (home, away)
    assert record.kit_home == (home.shirt, home.shorts, home.socks)


def test_a_kit_is_its_shades_and_the_clash_byte_its_shirts(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_rosters(0), logos=False)
    image = out.read_bytes()
    assert _block(image, 0, 0) == outfield_block((RED, WHITE, RED))
    assert _block(image, 0, 1) == outfield_block((BLUE, WHITE, BLUE))
    assert image[fixture.OFS_PREDOMINANT_COLOR] == _rgb_to_predominant(*RED)


def test_a_patched_team_loses_the_old_nations_chest_design(patcher, rom, out):
    """Germany's band, drawn in a club's crest colours, is no club's kit."""
    assert _chest(rom.read_bytes(), 0) == _design(fixture.BAND_DESIGN)
    patcher.patch(rom_path=rom, output_path=out, rosters=_rosters(0), logos=False)
    assert _chest(out.read_bytes(), 0) == [fixture.BLANK_DESIGN] * 8


def test_kits_false_keeps_the_cartridges_kits_chest_and_clash_byte(patcher, rom, out):
    before = rom.read_bytes()
    patcher.patch(rom_path=rom, output_path=out, rosters=_rosters(0), kits=False, logos=False)
    image = out.read_bytes()
    assert _block(image, 0, 0) == _block(before, 0, 0)
    assert _block(image, 0, 1) == _block(before, 0, 1)
    assert _chest(image, 0) == _chest(before, 0)
    assert image[fixture.OFS_PREDOMINANT_COLOR] == before[fixture.OFS_PREDOMINANT_COLOR]


def test_stripes_borrow_argentinas_design(patcher, rom, out):
    kits = {0: {"home": STRIPED.to_dict()}}
    patcher.patch(rom_path=rom, output_path=out, rosters=_rosters(0), kits=kits, logos=False)
    image = out.read_bytes()
    assert _chest(image, 0) == _design(fixture.STRIPES_DESIGN)
    assert _block(image, 0, 0) == outfield_block((WHITE, BLACK, WHITE))
    assert image[fixture.OFS_PREDOMINANT_COLOR] == _rgb_to_predominant(*WHITE)
    # The away kit is the record's own.
    assert _block(image, 0, 1) == outfield_block((BLUE, WHITE, BLUE))


@pytest.mark.parametrize("pattern", ["hoops", "sash", "chevron"])
def test_a_band_pattern_borrows_germanys(patcher, rom, out, pattern):
    spec = KitSpec(shirt=RED, secondary=BLUE, pattern=pattern, shorts=WHITE, socks=RED)
    kits = {3: {"home": spec}}
    patcher.patch(rom_path=rom, output_path=out, rosters=_rosters(3), kits=kits, logos=False)
    assert _chest(out.read_bytes(), 3) == _design(fixture.BAND_DESIGN)


GREEN = (0, 255, 0)
YELLOW = (255, 255, 0)


@pytest.mark.parametrize("secondary", [GREEN, YELLOW])
def test_the_stripes_take_the_flag_colour_nearest_the_secondary(patcher, rom, out, secondary):
    """Argentina's stripes draw in flag entry 13: the two-band flag's green
    and yellow are ordered so entry 13 holds the one the spec asks for, the
    flag looking the same and entry 12 still the numbers' colour."""
    spec = KitSpec(shirt=WHITE, secondary=secondary, pattern="stripes", shorts=WHITE, socks=WHITE)
    rosters = _rosters(0, flag_colors=[GREEN, YELLOW], kit_away=(BLACK, WHITE, BLACK))
    patcher.patch(
        rom_path=rom, output_path=out, rosters=rosters, kits={0: {"home": spec}}, logos=False
    )
    image = out.read_bytes()
    words = struct.unpack("<4H", _colour_record(image, "Germany"))
    pixels = _pixels(image, 0)
    assert words[1] == _rgb_to_bgr555(*secondary)
    assert [words[row[0] & 3] for row in pixels] == [_rgb_to_bgr555(*GREEN)] * 8 + [
        _rgb_to_bgr555(*YELLOW)
    ] * 8


def test_a_template_copies_the_cartridge_kit_and_its_design(patcher, rom, out):
    before = rom.read_bytes()
    kits = {0: {"home": {"template": ARGENTINA}, "away": {"template": ARGENTINA}}}
    patcher.patch(rom_path=rom, output_path=out, rosters=_rosters(0), kits=kits, logos=False)
    image = out.read_bytes()
    assert _block(image, 0, 0) == _block(before, ARGENTINA, 0)
    assert _block(image, 0, 1) == _block(before, ARGENTINA, 1)
    assert _chest(image, 0) == _design(fixture.STRIPES_DESIGN)
    assert image[fixture.OFS_PREDOMINANT_COLOR] == before[fixture.OFS_PREDOMINANT_COLOR + ARGENTINA]


def test_a_template_is_read_before_its_own_slot_is_patched(patcher, rom, out):
    """Slot 0 takes kit 18 while slot 18 gets a club: the cartridge's kit."""
    before = rom.read_bytes()
    kits = {0: {"home": {"template": ARGENTINA}}}
    patcher.patch(rom_path=rom, output_path=out, rosters=_rosters(0, 18), kits=kits, logos=False)
    assert _block(out.read_bytes(), 0, 0) == _block(before, ARGENTINA, 0)


def test_a_kit_photo_gives_its_colours(patcher, rom, out):
    png = _png(RED)
    kits = {0: {"home": {"image": png}}}
    patcher.patch(rom_path=rom, output_path=out, rosters=_rosters(0), kits=kits, logos=False)
    spec = core_kits.kit_from_image(png)
    assert _block(out.read_bytes(), 0, 0) == outfield_block((spec.shirt, spec.shorts, spec.socks))


@pytest.mark.parametrize(
    ("choice", "message"),
    [
        ({"template": 26}, "slot 0 home kit: Unknown kit template 26"),
        ({"image": b"not a picture"}, "slot 0 home kit"),
        ({"shirt": "#zz0000"}, "slot 0 home kit"),
    ],
)
def test_a_bad_kit_is_refused_before_anything_is_written(patcher, rom, out, choice, message):
    with pytest.raises(CapabilityError, match=message):
        patcher.patch(
            rom_path=rom, output_path=out, rosters=_rosters(0), kits={0: {"home": choice}}
        )
    assert not out.exists()


def test_the_numbers_are_set_against_a_templates_shades(patcher, rom, out):
    """Entry 12 is chosen against the shirts as written, a template's too."""
    kits = {0: {"home": {"template": ARGENTINA}}}
    patcher.patch(rom_path=rom, output_path=out, rosters=_rosters(0), kits=kits, logos=False)
    image = out.read_bytes()
    shades = [
        _bgr555_to_rgb(w)
        for at in _kit_offsets(0)
        for w in struct.unpack("<3H", image[at : at + 6])
    ]
    number = number_colour(two_band_flag(RED, BLUE), shades)
    assert struct.unpack("<4H", _colour_record(image, "Germany"))[0] == _rgb_to_bgr555(*number)


def test_pinning_reorders_entries_13_to_15_and_keeps_the_picture():
    flag = Flag([[0, 1, 2, 3] * 6] * 16, [WHITE, RED, (0, 255, 0), BLUE])
    pinned = _pinned(flag, {13: (BLUE, 5)})
    assert pinned.colours[0] == WHITE
    assert pinned.colours[1] == BLUE
    shows = [[c.colours[v] for v in row] for c in (flag, pinned) for row in c.pixels]
    assert shows[:16] == shows[16:]


def test_pinning_with_nothing_to_ask_keeps_the_flag():
    flag = Flag([[0, 1, 2, 3] * 6] * 16, [WHITE, RED, (0, 255, 0), BLUE])
    assert _pinned(flag, {}) == flag


def test_the_entry_more_of_the_design_uses_gets_the_nearer_colour():
    flag = Flag([[1, 2, 3] * 8] * 16, [WHITE, RED, GREEN, BLUE])
    assert _pinned(flag, {13: (BLUE, 5), 14: (BLUE, 1)}).colours[1] == BLUE
    assert _pinned(flag, {13: (BLUE, 1), 14: (BLUE, 5)}).colours[2] == BLUE


def test_an_entry_no_flag_pixel_uses_takes_the_colour_asked_for():
    """Botafogo's crest uses entries 12 and 14 only: the stripes in 13 come
    out in the secondary, not in a crest colour."""
    flag = Flag([[0, 2] * 12] * 16, [BLACK, RED, WHITE, BLUE])
    pinned = _pinned(flag, {13: (GREEN, 9)})
    assert pinned.colours[1] == GREEN
    shows = [[c.colours[v] for v in row] for c in (flag, pinned) for row in c.pixels]
    assert shows[:16] == shows[16:]


# -- previews and templates ------------------------------------------------------


def _size(png):
    return Image.open(io.BytesIO(png)).size


def test_the_preview_is_the_team_colors_player_at_three_times(patcher, rom):
    assert _size(patcher.kit_preview_png(rom, 0)) == (96, 144)


def test_a_choice_changes_the_preview_and_writes_nothing(patcher, rom):
    before = rom.read_bytes()
    plain = patcher.kit_preview_png(rom, 0, "home")
    striped = patcher.kit_preview_png(rom, 0, "home", STRIPED.to_dict())
    assert plain != striped
    assert patcher.kit_preview_png(rom, 0, "home", {"template": 0}) == plain
    assert rom.read_bytes() == before


def test_the_preview_draws_a_specs_shirt(patcher, rom):
    shirt = Image.open(io.BytesIO(patcher.kit_preview_png(rom, 4, "away", STRIPED))).convert("RGB")
    assert (255, 255, 255) in {shirt.getpixel((x, y)) for x in range(96) for y in range(144)}


@pytest.mark.parametrize(
    ("slot", "which", "choice"),
    [(27, "home", None), (-1, "home", None), (0, "third", None), (0, "home", {"template": 99})],
)
def test_a_preview_of_nothing_is_a_capability_error(patcher, rom, slot, which, choice):
    with pytest.raises(CapabilityError):
        patcher.kit_preview_png(rom, slot, which, choice)


def test_the_templates_are_the_26_national_kits(patcher, rom):
    templates = patcher.kit_templates(rom)
    assert [t.index for t in templates] == list(range(26))
    assert templates[ARGENTINA].name == "Argentina"
    assert templates[3].png == patcher.kit_preview_png(rom, 3)


def test_the_preview_draws_the_frame_mirrored_and_its_shadow(patcher, tmp_path):
    """Every frame tile solid in entry 15, the shadow, which is magenta: the
    chest's stripes (tile columns 0, 3 and 6) land mirrored, on top of it."""
    image = fixture.build_iss_rom()
    for start, _, count in kits._FRAME_TILES:
        image[start : start + 32 * count] = fixture.tile_4bpp([[15] * 8] * 8) * count
    home = _kit_offsets(ARGENTINA)[0]
    image[home + 16 : home + 18] = struct.pack("<H", 0x7C1F)
    rom = tmp_path / "frame.sfc"
    rom.write_bytes(image)
    png = Image.open(io.BytesIO(patcher.kit_preview_png(rom, ARGENTINA))).convert("RGB")
    stripe = kits.flag_colours(bytes(image), ARGENTINA)[1]
    for y in range(9, 17):
        row = [png.getpixel((3 * x, 3 * y)) for x in range(12, 20)]
        assert [c == stripe for c in row] == [False, True, False, False, True, False, False, True]
        assert row[0] == (255, 0, 255)


def test_the_away_preview_wears_the_home_kits_chest_design(patcher, rom):
    """The design is the team's: the away kit's own pattern never shows."""
    away = KitSpec(shirt=BLACK, secondary=WHITE, pattern="stripes", shorts=BLACK, socks=BLACK)
    plain_away = KitSpec(shirt=BLACK, secondary=WHITE, pattern="plain", shorts=BLACK, socks=BLACK)
    plain_home = KitSpec(shirt=RED, secondary=WHITE, pattern="plain", shorts=RED, socks=RED)
    drawn = patcher.kit_preview_png(rom, 4, "away", away, home=plain_home)
    assert drawn == patcher.kit_preview_png(rom, 4, "away", plain_away, home=plain_home)
    assert drawn != patcher.kit_preview_png(rom, 4, "away", plain_away, home=STRIPED)


def test_a_template_on_an_image_patched_before_is_the_retail_nations(patcher, rom, out, tmp_path):
    """Slot 18 lost Argentina's kit to a club; template 18 is still Argentina's
    retail kit, stripes and clash byte, so re-patching gives the same image."""
    patcher.patch(rom_path=rom, output_path=out, rosters=_rosters(ARGENTINA), logos=False)
    assert _chest(out.read_bytes(), ARGENTINA) != _design(fixture.STRIPES_DESIGN)
    options = {
        "rosters": _rosters(0),
        "kits": {0: {"home": {"template": ARGENTINA}}},
        "logos": False,
    }
    patcher.patch(rom_path=out, output_path=tmp_path / "a.sfc", **options)
    patcher.patch(rom_path=tmp_path / "a.sfc", output_path=tmp_path / "b.sfc", **options)
    image = (tmp_path / "a.sfc").read_bytes()
    assert _block(image, 0, 0) == bytes.fromhex("105eb56e7b7b630cc6184a29ef59397b")
    assert _chest(image, 0) == _design(fixture.STRIPES_DESIGN)
    assert image[fixture.OFS_PREDOMINANT_COLOR] == 0
    assert (tmp_path / "b.sfc").read_bytes() == image
    assert (
        patcher.kit_templates(out)[ARGENTINA].png
        == patcher.kit_templates(tmp_path / "b.sfc")[ARGENTINA].png
    )


@pytest.mark.parametrize("on", [True, False])
def test_the_goalkeeper_kit_is_written_with_the_kits(patcher, rom, out, on):
    before = rom.read_bytes()
    patcher.patch(
        rom_path=rom, output_path=out, rosters=_rosters(0, kit_gk=GK_KIT), kits=on, logos=False
    )
    shirt = out.read_bytes()[fixture.OFS_GK_RANGE1 + 2 : fixture.OFS_GK_RANGE1 + 10]
    if on:
        assert shirt == b"".join(w.to_bytes(2, "little") for w in _make_shades(*GK_KIT[0], 4))
    else:
        assert shirt == before[fixture.OFS_GK_RANGE1 + 2 : fixture.OFS_GK_RANGE1 + 10]


# -- blank_unused --------------------------------------------------------------


def _patch_blank(patcher, rom, out, **options):
    patcher.patch(rom_path=rom, output_path=out, rosters=_rosters(0), logos=False, **options)
    return out.read_bytes()


def _name_text(image, slot):
    at = fixture.OFS_TEAM_NAME_TEXT_PTRS + 2 * slot
    blob = fixture.decode_p40000(image[at : at + 2])
    return image[blob : blob + 1 + 4 * image[blob]]


def _name_tiles(image, slot):
    at = fixture.OFS_NAME_TILES_PTRS + 2 * slot
    blob = fixture.decode_p17000(image[at : at + 2])
    return image[blob : blob + (image[blob] | image[blob + 1] << 8)]


def _player_names(image, slot):
    base = fixture.OFS_PLAYER_NAMES + fixture.name_storage_index(slot) * 15 * 8
    return image[base : base + 15 * 8]


def test_blank_unused_empties_every_unmapped_slot(patcher, rom, out):
    image = _patch_blank(patcher, rom, out, blank_unused=True)
    for slot in range(1, 27):
        assert _name_text(image, slot) == b"\x00"
        assert _name_tiles(image, slot) == fixture.konami_literal(bytes(64))
        assert _player_names(image, slot) == bytes(15 * 8)
        assert _pixels(image, slot) == [[0] * 24] * 16
        start = fixture.desc_text_start(slot)
        assert image[start : start + fixture.DESC_TEXT_LENGTH] == b" " * fixture.DESC_TEXT_LENGTH


def test_blank_unused_leaves_the_mapped_slot_and_the_blank_slots_kits(patcher, rom, out):
    before = rom.read_bytes()
    image = _patch_blank(patcher, rom, out, blank_unused=True)
    assert _player_names(image, 0)[:8] == fixture.encode_name("P00")
    assert _pixels(image, 0) != [[0] * 24] * 16
    assert _block(image, 7, 0) == _block(before, 7, 0)
    assert _chest(image, 7) == _chest(before, 7)


def test_without_blank_unused_an_unmapped_slot_keeps_its_team(patcher, rom, out):
    before = rom.read_bytes()
    image = _patch_blank(patcher, rom, out)
    assert _player_names(image, 7) == _player_names(before, 7)
    assert _name_text(image, 7) == _name_text(before, 7)
    assert _pixels(image, 7) == _pixels(before, 7)
