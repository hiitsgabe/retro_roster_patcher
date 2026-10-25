"""The ISS writer: encoders, the compressor, the font, and the machine-code patch.

Four things in it are ROM hacking rather than field writing -- a ten-byte 65816
patch, a compressor, three pointer encodings and a hand-rolled 2bpp font -- and
each is pinned against the *rule* the module documents rather than against its
own output.

Every read-back is of the *output* file, opened fresh: the writer holds the
output handle for its whole lifetime and `finalize` is what flushes it, so
reading before then can see a stale page. Every expected value is either
hand-derived from a documented format or computed by
`tests/fixtures/synthetic_iss_rom.py`, which retranscribes the formats
independently.
"""

from __future__ import annotations

import random
import struct
from itertools import permutations

import pytest

from retro_roster_patcher.core.errors import RomError
from retro_roster_patcher.games.iss_snes.konami import decompress
from retro_roster_patcher.games.iss_snes.models import (
    PLAYERS_PER_TEAM,
    TEAM_ENUM_ORDER,
    TOTAL_TEAMS,
    Flag,
    ISSPlayerAttributes,
    ISSPlayerRecord,
    ISSTeamRecord,
)
from retro_roster_patcher.games.iss_snes.rom_writer import (
    FLAG_ROOM_SIZE,
    MIN_PATCHABLE_SIZE,
    NAME_TILES_CAPACITY,
    ISSRomWriter,
    _encode_iss_name,
    _encode_p17000,
    _encode_p40000,
    _encode_p48000,
    _encode_team_name_text,
    _fewer_colours,
    _flag_blobs,
    _flag_half_tiles,
    _konami_compress_literal,
    _make_shades,
    _render_name_tiles,
    _rgb_to_bgr555,
    _rgb_to_predominant,
    _serialize_2bpp,
    _to_ascii,
    number_colour,
    number_first,
    two_band_flag,
)
from tests.fixtures import synthetic_iss_rom as fixture


@pytest.fixture
def rom(tmp_path):
    return fixture.write_iss_rom(tmp_path / "iss.sfc")


@pytest.fixture
def out(tmp_path):
    return tmp_path / "patched.sfc"


#: One value per field, all different, so a field written to the wrong bits
#: shows up as another field's value.
_DISTINCT = dict(
    dribble=5,
    dash=12,
    ball_dash=9,
    kick=3,
    shooting=6,
    technique=2,
    stamina=11,
    defense=13,
    offense=4,
)


def _player(index, **kwargs):
    attrs = ISSPlayerAttributes(**{f: kwargs.pop(f, v) for f, v in _DISTINCT.items()})
    return ISSPlayerRecord(
        name=kwargs.pop("name", f"WRITE{index:02d}"[:8]),
        shirt_number=kwargs.pop("shirt_number", index + 1),
        attributes=attrs,
        **kwargs,
    )


def _team(count=PLAYERS_PER_TEAM, **kwargs):
    return ISSTeamRecord(
        name=kwargs.pop("name", "Test United"),
        short_name=kwargs.pop("short_name", "TST"),
        players=[_player(i) for i in range(count)],
        **kwargs,
    )


def test_to_ascii_strips_diacritics_rather_than_the_letter():
    assert _to_ascii("José Ramírez") == "Jose Ramirez"


def test_to_ascii_drops_a_character_with_no_ascii_base():
    assert _to_ascii("Łukasz") == "ukasz"


def test_encoding_pads_a_short_name_to_eight_bytes_with_space():
    assert _encode_iss_name("PELE") == fixture.encode_name("PELE")
    assert len(_encode_iss_name("PELE")) == 8


def test_encoding_truncates_a_long_name_to_eight_bytes():
    assert _encode_iss_name("VANDERBEEK") == fixture.encode_name("VANDERBEE")


def test_an_unmappable_character_encodes_to_a_space_and_not_a_terminator():
    """0x00 is the space glyph, so "O&Neill" reaches the cartridge as "O Neill"."""
    assert _encode_iss_name("O&N")[:3] == bytes(
        [fixture.encode_char("O"), 0x00, fixture.encode_char("N")]
    )


def test_encoding_an_empty_name_gives_eight_spaces():
    assert _encode_iss_name("") == bytes(8)


def test_bgr555_puts_red_in_the_low_five_bits():
    assert _rgb_to_bgr555(255, 0, 0) == 0x001F


def test_bgr555_puts_green_in_the_middle_five_bits():
    assert _rgb_to_bgr555(0, 255, 0) == 0x03E0


def test_bgr555_puts_blue_in_the_high_five_bits():
    assert _rgb_to_bgr555(0, 0, 255) == 0x7C00


def test_bgr555_leaves_bit_fifteen_clear_for_white():
    assert _rgb_to_bgr555(255, 255, 255) == 0x7FFF


def test_bgr555_truncates_towards_zero():
    """`r * 31 // 255`, so 8 becomes 0 rather than 1."""
    assert _rgb_to_bgr555(8, 0, 0) == 0


def test_a_dark_colour_generates_shades_that_climb_towards_white():
    shades = _make_shades(0, 0, 0, 3)
    assert shades == [0x0000, 0x2108, 0x4210]


def test_a_bright_colour_generates_shades_that_climb_to_the_colour_itself():
    """Above the brightness threshold the base is the *lightest* shade."""
    shades = _make_shades(255, 255, 255, 3)
    assert shades[-1] == _rgb_to_bgr555(255, 255, 255)
    assert shades == [0x4210, 0x5EF7, 0x7FFF]


def test_a_single_shade_is_the_colour_itself():
    """The `count > 1` guard, which is what keeps the divisor out of zero."""
    assert _make_shades(200, 100, 50, 1) == [_rgb_to_bgr555(200, 100, 50)]


def test_the_shade_ramp_is_monotone_for_a_dark_colour():
    shades = _make_shades(10, 20, 30, 4)
    assert sorted(shades) == shades


@pytest.mark.parametrize(
    ("rgb", "expected"),
    [
        ((255, 255, 255), 0),
        ((0, 0, 255), 1),
        ((255, 0, 0), 2),
        ((255, 200, 0), 3),
        ((0, 255, 0), 4),
    ],
)
def test_the_predominant_colour_byte_names_the_five_ordinals(rgb, expected):
    assert _rgb_to_predominant(*rgb) == expected


@pytest.mark.parametrize(
    ("rgb", "expected"),
    [((0, 0, 0), 5), ((10, 10, 10), 5), ((190, 190, 190), 0), ((120, 120, 120), 6)],
)
def test_a_colour_without_hue_goes_by_brightness(rgb, expected):
    """Black is not red: a black shirt meets a red one without a change of kit."""
    assert _rgb_to_predominant(*rgb) == expected


def test_p40000_biases_the_high_byte_by_0x80():
    assert _encode_p40000(0x43000) == fixture.encode_p40000(0x43000)
    assert _encode_p40000(0x43000) == bytes([0x00, 0xB0])


def test_p48000_does_not_bias_the_high_byte():
    assert _encode_p48000(0x48400) == fixture.encode_p48000(0x48400)
    assert _encode_p48000(0x48400) == bytes([0x00, 0x84])


def test_p17000_is_measured_from_0x10000_and_biased():
    assert _encode_p17000(0x17680) == fixture.encode_p17000(0x17680)
    assert _encode_p17000(0x17680) == bytes([0x80, 0xF6])


def test_the_three_encodings_disagree_about_the_same_address():
    """They are not interchangeable, which is why each table has exactly one."""
    assert _encode_p40000(0x44000) != _encode_p48000(0x44000)


def test_p40000_raises_above_the_range_its_bias_can_express():
    """0x48000 gives a biased high byte of 0x100. Every address
    `write_team_name_texts` passes is below 0x44478, so the guard is the
    ceiling and not this."""
    with pytest.raises(ValueError):
        _encode_p40000(0x48000)


def test_the_compressed_stream_declares_its_own_total_length():
    blob = _konami_compress_literal(bytes(64))
    assert blob[0] | (blob[1] << 8) == len(blob)


def test_the_compressed_stream_matches_the_fixtures_transcription_of_the_format():
    payload = bytes(range(100))
    assert _konami_compress_literal(payload) == fixture.konami_literal(payload)


def test_a_run_never_exceeds_thirty_one_bytes():
    blob = _konami_compress_literal(bytes(96))
    controls = [blob[2], blob[2 + 32], blob[2 + 64]]
    assert controls == [0x80 | 31, 0x80 | 31, 0x80 | 31]


def test_the_final_short_run_carries_the_remainder():
    blob = _konami_compress_literal(bytes(96))
    assert blob[2 + 96] == 0x80 | 3


def test_the_output_is_always_larger_than_the_input():
    """Literal-only, no back references. This is why `write_name_tiles` has to
    relocate the blobs instead of writing them back where they were."""
    payload = bytes(64)
    assert len(_konami_compress_literal(payload)) == 2 + 3 + 64


def test_compressing_nothing_gives_a_bare_header():
    assert _konami_compress_literal(b"") == bytes([2, 0])


def test_the_payload_survives_the_wrapping_verbatim():
    payload = bytes(range(1, 32))
    assert _konami_compress_literal(payload)[3:] == payload


def test_a_rendered_grid_is_eight_rows_of_thirty_two():
    grid = _render_name_tiles("TEST")
    assert len(grid) == 8
    assert {len(row) for row in grid} == {32}


def test_a_rendered_grid_uses_only_the_three_declared_colour_codes():
    grid = _render_name_tiles("TEST")
    assert {value for row in grid for value in row} == {0, 1, 3}


def test_a_single_letter_is_centred():
    """Five pixels wide plus a one-pixel shadow either side, in 32 columns."""
    grid = _render_name_tiles("A")
    lit = [col for col, value in enumerate(grid[0]) if value != 0]
    assert lit == [12, 13, 14, 15, 16, 17, 18]


def test_the_top_row_of_a_letter_is_shadow_and_not_stroke():
    """Row 0 is a border: the glyph's own six rows start at row 1."""
    assert set(_render_name_tiles("A")[0]) == {0, 3}


def test_the_glyphs_first_row_lands_on_grid_row_one():
    grid = _render_name_tiles("A")
    stroke = [col for col, value in enumerate(grid[1]) if value == 1]
    assert stroke == [14, 15, 16]


def test_a_name_with_no_renderable_character_is_left_transparent():
    assert _render_name_tiles("!!!") == [[0] * 32] * 8


def test_an_empty_name_is_left_transparent():
    """A blank slot's (`blank_unused`): no letters and no shadow behind them."""
    assert _render_name_tiles("") == [[0] * 32] * 8


def test_a_name_too_wide_for_the_grid_is_truncated_at_the_boundary():
    """The layout drops the inter-letter gap first and then stops placing
    letters that would cross column 32, rather than overflowing the grid."""
    grid = _render_name_tiles("ABCDEFGHIJKLMNOP")
    assert {len(row) for row in grid} == {32}


def test_serialising_a_blank_grid_gives_sixty_four_zero_bytes():
    assert _serialize_2bpp([[0] * 32 for _ in range(8)]) == bytes(64)


def test_serialising_an_all_ones_grid_fills_only_bitplane_zero():
    data = _serialize_2bpp([[1] * 32 for _ in range(8)])
    assert set(data[0::2]) == {0xFF}
    assert set(data[1::2]) == {0x00}


def test_serialising_an_all_threes_grid_fills_both_bitplanes():
    data = _serialize_2bpp([[3] * 32 for _ in range(8)])
    assert set(data) == {0xFF}


def test_the_leftmost_pixel_of_a_row_is_the_high_bit():
    grid = [[0] * 32 for _ in range(8)]
    grid[0][0] = 1
    assert _serialize_2bpp(grid)[0] == 0x80


def test_the_second_tile_starts_sixteen_bytes_in():
    """Four 8x8 tiles side by side, so column 8 is the first tile's neighbour."""
    grid = [[0] * 32 for _ in range(8)]
    grid[0][8] = 1
    data = _serialize_2bpp(grid)
    assert data[16] == 0x80
    assert data[0] == 0x00


def test_flag_colour_zero_is_palette_entry_twelve_in_every_tile():
    """12 is bitplanes 2 and 3, the second sixteen bytes of each 32-byte tile."""
    half = _flag_half_tiles([[0] * 24 for _ in range(8)])
    assert len(half) == 96
    for tile in range(3):
        assert set(half[32 * tile : 32 * tile + 16]) == {0x00}
        assert set(half[32 * tile + 16 : 32 * tile + 32]) == {0xFF}


def test_a_flag_pixel_lands_on_its_tile_row_and_bit():
    """Pixel (row 2, column 9) is tile 1, row 2, bit 6; colour 3 is entry 15,
    all four planes."""
    rows = [[0] * 24 for _ in range(8)]
    rows[2][9] = 3
    half = _flag_half_tiles(rows)
    assert half[32 + 4] == 0x40 and half[32 + 5] == 0x40
    assert half[4] == 0x00 and half[32 + 6] == 0x00


def test_a_two_letter_name_encodes_right_to_left_bottom_before_top():
    """Hand-derived from the documented format.

    `A` is 9 pixels wide, so `AB` is 18 and the centre is 9. `B` sits at x 9,
    which centres to 0; `A` at x 0 centres to -9, which is 0xF7 as a byte. The
    entries come out in reverse order, and within a character the bottom half
    (0xF9) precedes the top (0xF1). Tile ids run 0xC0 and 0xD0 upwards for the
    top and bottom halves of A-P.
    """
    assert _encode_team_name_text("AB") == bytes(
        [
            0x04,
            0xF9, 0x00, 0xD1, 0x06,
            0xF1, 0x00, 0xC1, 0x06,
            0xF9, 0xF7, 0xD0, 0x06,
            0xF1, 0xF7, 0xC0, 0x06,
        ]
    )  # fmt: skip


def test_a_period_contributes_a_bottom_half_only():
    """Three entries for two characters, not four: `.` has no top tile."""
    blob = _encode_team_name_text("I.")
    assert blob[0] == 3
    assert len(blob) == 1 + 3 * 4


def test_the_count_byte_is_the_number_of_four_byte_entries():
    blob = _encode_team_name_text("ABCDE")
    assert len(blob) == 1 + blob[0] * 4


def test_a_space_advances_the_layout_without_producing_an_entry():
    assert _encode_team_name_text("A B")[0] == _encode_team_name_text("AB")[0]
    assert _encode_team_name_text("A B") != _encode_team_name_text("AB")


def test_an_unrenderable_character_is_skipped_entirely():
    assert _encode_team_name_text("A&B") == _encode_team_name_text("AB")


def test_a_name_wider_than_the_budget_is_scaled_rather_than_dropped():
    """Ten default-width letters are 90 pixels against a 70-pixel budget, so the
    x positions are squeezed and every letter keeps its entry."""
    wide = _encode_team_name_text("ABCDEFGHIJ")
    assert wide[0] == 20


def test_an_empty_name_encodes_to_a_count_of_zero():
    assert _encode_team_name_text("") == bytes([0])


def test_the_minimum_is_the_end_of_the_flag_room_and_nothing_higher():
    """Derived in the module from a `max` over its own offsets; retranscribed in
    the fixture as the end of bank $89's flag room."""
    assert MIN_PATCHABLE_SIZE == fixture.FLAG_ROOM_END
    assert MIN_PATCHABLE_SIZE == fixture.SIZE_ARITHMETIC_MINIMUM


def test_the_bound_is_at_least_the_end_of_every_region_the_writer_touches():
    """Each region end computed from the fixture's own transcribed offsets. A
    term dropped from the `max` that was not already dominated fails here."""
    ends = [
        fixture.OFS_PLAYER_NAMES + TOTAL_TEAMS * PLAYERS_PER_TEAM * 8,
        fixture.OFS_PLAYER_DATA + TOTAL_TEAMS * PLAYERS_PER_TEAM * 6,
        fixture.OFS_KIT1_RANGE1 + 19 * fixture.OUTFIELD_KIT_STRIDE,
        fixture.OFS_KIT2_RANGE2 + 8 * fixture.OUTFIELD_KIT_STRIDE,
        fixture.OFS_GK_RANGE2 + 8 * fixture.GK_KIT_STRIDE,
        fixture.OFS_FLAG_COLORS_RANGE2 + 8 * fixture.FLAG_COLORS_STEP + 8,
        fixture.OFS_PREDOMINANT_COLOR + TOTAL_TEAMS,
        fixture.OFS_NAME_TILES_PTRS + TOTAL_TEAMS * 2,
        fixture.OFS_FLAG_TILE_PTRS + TOTAL_TEAMS * 4,
        fixture.NAME_TILES_DISPLACED_END,
        fixture.OFS_DESC_PTRS + TOTAL_TEAMS * 2,
        fixture.OFS_TEAM_NAME_TEXT_PTRS + TOTAL_TEAMS * 2,
        fixture.MAX_NAME_TEXT_ADDR,
        max(fixture.DISPLACEMENT_PATCH_POINTS) + 1,
        fixture.OFS_FLAG_CELLS + TOTAL_TEAMS * 2,
        fixture.FLAG_ROOM_END,
    ]
    assert MIN_PATCHABLE_SIZE == max(ends)


def test_the_displaced_name_tile_region_holds_2432_bytes():
    assert NAME_TILES_CAPACITY == fixture.NAME_TILES_CAPACITY
    assert NAME_TILES_CAPACITY == 0x18000 - 0x17680


def _read(path, offset, length):
    return path.read_bytes()[offset : offset + length]


def test_player_names_land_in_the_name_order_block_for_slot_zero(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_names(0, [_player(0, name="KLINSMAN")])
    writer.finalize()
    assert _read(out, fixture.OFS_PLAYER_NAMES, 8) == fixture.encode_name("KLINSMAN")


def test_slot_five_writes_scotlands_names_at_storage_index_twenty_four(rom, out):
    """The translation. Writing slot 5 must not touch storage index 5, which is
    Wales, and must land at 24."""
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_names(5, [_player(0, name="MCSTAY")])
    writer.finalize()
    scotland = fixture.OFS_PLAYER_NAMES + 24 * PLAYERS_PER_TEAM * 8
    wales = fixture.OFS_PLAYER_NAMES + 5 * PLAYERS_PER_TEAM * 8
    assert _read(out, scotland, 8) == fixture.encode_name("MCSTAY")
    assert _read(out, wales, 8) == fixture.encode_name(fixture.player_name(5, 0))


def test_slot_six_writes_wales_names_at_storage_index_five(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_names(6, [_player(0, name="GIGGS")])
    writer.finalize()
    wales = fixture.OFS_PLAYER_NAMES + 5 * PLAYERS_PER_TEAM * 8
    assert _read(out, wales, 8) == fixture.encode_name("GIGGS")


@pytest.mark.parametrize("slot", range(TOTAL_TEAMS))
def test_every_slot_writes_to_its_own_storage_block(rom, out, slot):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_names(slot, [_player(0, name="MARKER")])
    writer.finalize()
    base = fixture.OFS_PLAYER_NAMES + fixture.name_storage_index(slot) * PLAYERS_PER_TEAM * 8
    assert _read(out, base, 8) == fixture.encode_name("MARKER")


def test_writing_names_returns_the_count_written(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    written = writer.write_player_names(0, [_player(i) for i in range(4)])
    writer.finalize()
    assert written == 4


def test_a_squad_longer_than_fifteen_is_cut_at_fifteen(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    written = writer.write_player_names(0, [_player(i) for i in range(22)])
    writer.finalize()
    assert written == PLAYERS_PER_TEAM


def test_the_sixteenth_players_name_never_reaches_the_image(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_names(0, [_player(i, name=f"NEW{i:02d}") for i in range(22)])
    writer.finalize()
    sixteenth = fixture.OFS_PLAYER_NAMES + 15 * 8
    assert _read(out, sixteenth, 8) == fixture.encode_name(fixture.player_name(1, 0))


def test_an_empty_squad_writes_no_name_at_all(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    written = writer.write_player_names(0, [])
    writer.finalize()
    assert written == 0
    assert _read(out, fixture.OFS_PLAYER_NAMES, 8) == fixture.encode_name(fixture.player_name(0, 0))


def test_the_copier_header_shifts_the_name_block(tmp_path, out):
    rom = fixture.write_iss_rom(tmp_path / "iss.smc", with_header=True)
    writer = ISSRomWriter(str(rom), str(out), 512)
    writer.write_player_names(0, [_player(0, name="SHIFTED")])
    writer.finalize()
    assert _read(out, fixture.OFS_PLAYER_NAMES + 512, 8) == fixture.encode_name("SHIFTED")
    assert _read(out, fixture.OFS_PLAYER_NAMES, 8) != fixture.encode_name("SHIFTED")


def _record(path, slot, player, header=0):
    base = fixture.OFS_PLAYER_DATA + (slot * PLAYERS_PER_TEAM + player) * 6
    return path.read_bytes()[base + header : base + header + 6]


def _decode(record):
    """The verified record layout, transcribed independently of the writer."""
    b0, b1, b2, b3, b4, b5 = record
    return dict(
        dribble=b0 & 0x0F,
        dash=b0 >> 4,
        kick=b1 & 0x07,
        shooting=(b1 >> 4) & 0x07,
        technique=b2 & 0x07,
        stamina=b2 >> 4,
        ball_dash=b3 >> 4,
        defense=b4 & 0x0F,
        offense=b4 >> 4,
    )


#: The largest value each field's bits hold.
_TOPS = dict(
    dribble=15,
    dash=15,
    ball_dash=15,
    kick=7,
    shooting=7,
    technique=7,
    stamina=15,
    defense=15,
    offense=15,
)


def test_player_data_lands_in_the_enum_order_block_for_slot_five(rom, out):
    """Player data uses the *other* order, so slot 5 writes at index 5."""
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_data(5, [_player(0)])
    writer.finalize()
    assert _decode(_record(out, 5, 0)) == _DISTINCT
    assert _record(out, 24, 0) == fixture.player_data_record(24, 0)


@pytest.mark.parametrize(("field", "top"), list(_TOPS.items()))
def test_every_value_of_each_field_round_trips_through_its_own_bits(rom, out, field, top):
    """Every value of one field, with the other eight held at distinct values,
    so a field written into a neighbour's bits fails here."""
    values = list(range(top + 1))
    writer = ISSRomWriter(str(rom), str(out))
    for slot in (0, 1):
        chunk = values[slot * PLAYERS_PER_TEAM : (slot + 1) * PLAYERS_PER_TEAM]
        if chunk:
            writer.write_player_data(slot, [_player(i, **{field: v}) for i, v in enumerate(chunk)])
    writer.finalize()
    for value in values:
        decoded = _decode(_record(out, *divmod(value, PLAYERS_PER_TEAM)))
        assert decoded == {**_DISTINCT, field: value}


def test_every_field_at_its_top_writes_retails_super_star_record(rom, out):
    """Retail's Super Star records read ff 77 f7 fN ff: every field at its top."""
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_data(0, [_player(0, shirt_number=1, **_TOPS)])
    writer.finalize()
    assert _record(out, 0, 0)[:5] == bytes([0xFF, 0x77, 0xF7, 0xF0, 0xFF])


def test_a_field_past_its_bits_is_clamped_and_not_wrapped(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_data(0, [_player(0, kick=99, stamina=16, dash=-3, shooting=8, technique=9)])
    writer.finalize()
    record = _record(out, 0, 0)
    decoded = _decode(record)
    assert (decoded["kick"], decoded["stamina"], decoded["dash"]) == (7, 15, 0)
    assert (decoded["shooting"], decoded["technique"]) == (7, 7)
    assert record[1] & 0x88 == 0 and record[2] & 0x08 == 0  # no spill into the unknown bits


def test_the_unknown_bits_are_written_zero(rom, out):
    """b1 bits 3 and 7 and b2 bit 3 are 0 in every retail record. The fixture
    sets them, so this is a write and not a byte left alone."""
    original = fixture.player_data_record(0, 0)
    assert original[1] & 0x88 == 0x88 and original[2] & 0x08 == 0x08
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_data(0, [_player(0, **_TOPS)])
    writer.finalize()
    record = _record(out, 0, 0)
    assert record[1] & 0x88 == 0
    assert record[2] & 0x08 == 0


def test_the_shirt_number_is_stored_as_one_less_in_the_low_nibble(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_data(0, [_player(0, shirt_number=10, ball_dash=6)])
    writer.finalize()
    assert _record(out, 0, 0)[3] == 0x69


def test_the_shirt_number_is_clamped_to_one_through_fifteen(rom, out):
    """Retail numbers run 1-15; 16 fits the nibble but the game never uses it."""
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_data(0, [_player(0, shirt_number=99), _player(1, shirt_number=0)])
    writer.finalize()
    assert _record(out, 0, 0)[3] & 0x0F == 14
    assert _record(out, 0, 1)[3] & 0x0F == 0


def test_the_hair_byte_is_zero_but_for_the_discs_palette_bit(rom, out):
    """Hair 0 for everyone, bits 4, 5 and 7 cleared, bit 6 kept. The fixture
    sets all four high bits and a non-zero hair style."""
    assert fixture.player_data_record(0, 0)[5] & 0xF0 == 0xF0
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_data(0, [_player(0)])
    writer.finalize()
    assert _record(out, 0, 0)[5] == 0x40


def test_a_clear_palette_bit_stays_clear(rom, out):
    """The other half: bit 6 comes from the disc, never from the player."""
    image = bytearray(rom.read_bytes())
    image[fixture.OFS_PLAYER_DATA + 5] &= ~0x40
    rom.write_bytes(image)
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_data(0, [_player(0)])
    writer.finalize()
    assert _record(out, 0, 0)[5] == 0x00


def test_writing_data_returns_the_count_written(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    written = writer.write_player_data(0, [_player(i) for i in range(7)])
    writer.finalize()
    assert written == 7


def test_a_squad_longer_than_fifteen_reports_only_the_fifteen_written(rom, out):
    """`PatchResult.players_patched` counts records that reached the image."""
    writer = ISSRomWriter(str(rom), str(out))
    written = writer.write_player_data(0, [_player(i) for i in range(22)])
    writer.finalize()
    assert written == PLAYERS_PER_TEAM


def test_the_sixteenth_players_data_never_reaches_the_image(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_data(0, [_player(i) for i in range(22)])
    writer.finalize()
    assert _record(out, 1, 0) == fixture.player_data_record(1, 0)


def test_an_untouched_slot_keeps_every_byte_of_its_records(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_data(0, [_player(i) for i in range(15)])
    writer.finalize()
    assert _record(out, 26, 14) == fixture.player_data_record(26, 14)


def test_the_home_kit_writes_three_shirt_shades_at_the_range_one_offset(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_kit_colors(0, _team(kit_home=((200, 0, 0), (255, 255, 255), (200, 0, 0))))
    writer.finalize()
    data = _read(out, fixture.OFS_KIT1_RANGE1, 6)
    expected = b"".join(shade.to_bytes(2, "little") for shade in _make_shades(200, 0, 0, 3))
    assert data == expected


def test_scotland_writes_into_the_second_kit_range(rom, out):
    """Slot 5 is at position 1 of the eight-team range, not position 5 of the
    nineteen-team one."""
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_kit_colors(5, _team(kit_home=((0, 0, 200),)))
    writer.finalize()
    at_range2 = _read(out, fixture.OFS_KIT1_RANGE2 + 1 * 32, 6)
    expected = b"".join(shade.to_bytes(2, "little") for shade in _make_shades(0, 0, 200, 3))
    assert at_range2 == expected


def test_the_kit_write_leaves_the_hair_and_skin_words_alone(rom, out):
    """Sixteen bytes of a thirty-two byte block; the other sixteen are palette
    entries no method here owns."""
    before = rom.read_bytes()[fixture.OFS_KIT1_RANGE1 + 16 : fixture.OFS_KIT1_RANGE1 + 32]
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_kit_colors(0, _team(kit_home=((200, 0, 0),)))
    writer.finalize()
    assert _read(out, fixture.OFS_KIT1_RANGE1 + 16, 16) == before


def test_a_kit_with_one_colour_uses_it_for_shorts_and_socks_too(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_kit_colors(0, _team(kit_home=((10, 20, 30),)))
    writer.finalize()
    data = _read(out, fixture.OFS_KIT1_RANGE1, 16)
    shirt = b"".join(s.to_bytes(2, "little") for s in _make_shades(10, 20, 30, 3))
    assert data[0:6] == shirt
    assert data[6:12] == shirt


def test_the_goalkeeper_kit_opens_with_a_near_white_specular_word(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_kit_colors(0, _team(kit_gk=((0, 128, 0), (0, 0, 0))))
    writer.finalize()
    assert _read(out, fixture.OFS_GK_RANGE1, 2) == (0x7FFE).to_bytes(2, "little")


def test_the_goalkeeper_kit_writes_four_shirt_shades(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_kit_colors(0, _team(kit_gk=((0, 128, 0), (0, 0, 0))))
    writer.finalize()
    expected = b"".join(s.to_bytes(2, "little") for s in _make_shades(0, 128, 0, 4))
    assert _read(out, fixture.OFS_GK_RANGE1 + 2, 8) == expected


def test_super_star_has_an_outfield_kit_but_no_goalkeeper_slot(rom, out):
    """`_GK_RANGE1_TEAMS` is the first eighteen of nineteen, so slot 26 falls
    through the goalkeeper branch without writing."""
    before = rom.read_bytes()
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_kit_colors(26, _team(kit_gk=((0, 128, 0),)))
    writer.finalize()
    assert out.read_bytes() == before


def test_a_record_with_no_kit_colours_writes_nothing(rom, out):
    before = rom.read_bytes()
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_kit_colors(0, _team())
    writer.finalize()
    assert out.read_bytes() == before


def test_the_predominant_colour_byte_lands_at_the_slots_own_offset(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_predominant_color(7, (0, 0, 255))
    writer.finalize()
    assert out.read_bytes()[fixture.OFS_PREDOMINANT_COLOR + 7] == 1


def test_the_predominant_colour_touches_no_neighbouring_slot(rom, out):
    before = rom.read_bytes()
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_predominant_color(7, (0, 0, 255))
    writer.finalize()
    after = out.read_bytes()
    assert after[fixture.OFS_PREDOMINANT_COLOR + 6] == before[fixture.OFS_PREDOMINANT_COLOR + 6]
    assert after[fixture.OFS_PREDOMINANT_COLOR + 8] == before[fixture.OFS_PREDOMINANT_COLOR + 8]


def _write_flags(rom, out, flags, fallbacks=None, shirts=None):
    """Move the name tiles out, as `patch` does first, then write `flags`."""
    with ISSRomWriter(str(rom), str(out)) as writer:
        writer.write_name_tiles({})
        notes = writer.write_flags(flags, fallbacks, shirts)
        writer.finalize()
    return notes


def _blobs(image, team):
    """Team `team`'s two compressed halves, through the flag pointer table."""
    at = fixture.OFS_FLAG_TILE_PTRS + 4 * team
    addresses = [fixture.decode_p48000(image[at + k : at + k + 2]) for k in (0, 2)]
    return [image[a : a + (image[a] | image[a + 1] << 8)] for a in addresses]


def _pixels(image, team):
    """Team `team`'s flag as 16 rows of 24 palette entries, decoded from the image."""
    rows = []
    for blob in _blobs(image, team):
        half = decompress(blob)
        for r in range(8):
            row = []
            for t in range(3):
                planes = [half[32 * t + o + 2 * r] for o in (0, 1, 16, 17)]
                for c in range(8):
                    row.append(sum(1 << p for p, b in enumerate(planes) if b & (0x80 >> c)))
            rows.append(row)
    return rows


def _noise(seed, colours=(0, 1, 2, 3)):
    rng = random.Random(seed)
    return Flag(
        [[rng.choice(colours) for _ in range(24)] for _ in range(16)],
        [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)],
    )


def test_a_flag_decodes_back_to_its_pixels(rom, out):
    flag = _noise(1)
    _write_flags(rom, out, {0: flag})
    assert _pixels(out.read_bytes(), 0) == [[12 + v for v in row] for row in flag.pixels]


def test_the_four_flag_colours_land_in_the_teams_colour_record(rom, out):
    flag = _noise(1)
    _write_flags(rom, out, {0: flag})
    # Germany is position 0 of the first flag-colour range.
    words = struct.unpack("<4H", _read(out, fixture.OFS_FLAG_COLORS_RANGE1, 8))
    assert list(words) == [_rgb_to_bgr555(*rgb) for rgb in flag.colours]


def test_scotland_takes_the_second_flag_colour_range(rom, out):
    _write_flags(rom, out, {5: two_band_flag((255, 0, 0), (0, 0, 255))})
    assert _read(out, fixture.OFS_FLAG_COLORS_RANGE2, 2) == _rgb_to_bgr555(255, 0, 0).to_bytes(
        2, "little"
    )


def test_every_unpatched_team_keeps_its_flag_byte_for_byte(rom, out):
    """The room is repacked, so the blobs move, but each is the cartridge's own."""
    before = rom.read_bytes()
    _write_flags(rom, out, {0: _noise(1), 9: _noise(2)})
    after = out.read_bytes()
    for team in range(TOTAL_TEAMS):
        if team in (0, 9):
            continue
        assert _blobs(after, team) == [fixture.flag_half_blob(team, h) for h in (0, 1)], team
        name = TEAM_ENUM_ORDER[team]
        assert _colour_record(after, name) == _colour_record(before, name), team


def _colour_record(image, name):
    from retro_roster_patcher.games.iss_snes import rom_writer

    if name in rom_writer._FLAG_COLORS_RANGE1_TEAMS:
        at = fixture.OFS_FLAG_COLORS_RANGE1 + 10 * rom_writer._FLAG_COLORS_RANGE1_TEAMS.index(name)
    else:
        at = fixture.OFS_FLAG_COLORS_RANGE2 + 10 * rom_writer._FLAG_COLORS_RANGE2_TEAMS.index(name)
    return image[at : at + 8]


def test_one_design_shared_by_two_teams_is_stored_once(rom, out):
    band = two_band_flag((255, 0, 0), (0, 0, 255))
    _write_flags(rom, out, {0: band, 9: band})
    table = _read(out, fixture.OFS_FLAG_TILE_PTRS, 4 * TOTAL_TEAMS)
    assert table[0:4] == table[36:40]


def test_a_two_band_flag_is_the_primary_over_the_alternate(rom, out):
    _write_flags(rom, out, {0: two_band_flag((255, 0, 0), (0, 0, 255))})
    assert _pixels(out.read_bytes(), 0) == [[12] * 24] * 8 + [[13] * 24] * 8


def test_the_flags_stay_inside_the_room(rom, out):
    before = rom.read_bytes()
    _write_flags(rom, out, {team: _noise(team) for team in range(5)})
    after = out.read_bytes()
    assert after[fixture.FLAG_ROOM_END :] == before[fixture.FLAG_ROOM_END :]
    for team in range(TOTAL_TEAMS):
        at = fixture.OFS_FLAG_TILE_PTRS + 4 * team
        for k in (0, 2):
            address = fixture.decode_p48000(after[at + k : at + k + 2])
            assert fixture.FLAG_ROOM_START <= address < fixture.FLAG_ROOM_END


def test_the_flags_refuse_to_go_before_the_name_tiles_move(rom, out):
    """On the cartridge the name tiles sit inside the flag room."""
    with ISSRomWriter(str(rom), str(out)) as writer, pytest.raises(RomError, match="name tiles"):
        writer.write_flags({0: _noise(1)})


def _speckled(seed):
    """Three colours, two of them noise: about 110 bytes, and 78 once the rarest
    (dark red) joins the red it is nearest."""
    rng = random.Random(seed)
    return Flag(
        [
            [rng.choices((0, 1, 3), (40, 40, 20))[0] if x < 18 else 0 for x in range(24)]
            for _ in range(16)
        ],
        [(255, 255, 255), (200, 0, 0), (0, 0, 255), (190, 0, 0)],
    )


def test_crests_that_do_not_fit_lose_colours_first(rom, out):
    """27 speckled crests need more than the room: the largest give up a colour
    at a time, and each decodes to what its note says."""
    crests = {team: _speckled(team) for team in range(TOTAL_TEAMS)}
    bands = {team: two_band_flag((255, 0, 0), (0, 0, 255)) for team in range(TOTAL_TEAMS)}
    notes = _write_flags(rom, out, crests, bands)
    image = out.read_bytes()
    assert any("simplified" in note for note in notes.values())
    assert not any("did not fit" in note for note in notes.values())
    for team in range(TOTAL_TEAMS):
        decoded = _pixels(image, team)
        note = notes.get(team, "")
        if "two-band" in note:
            assert decoded == [[12] * 24] * 8 + [[13] * 24] * 8
        elif "simplified" in note:
            kept = int(note.split("simplified to ")[1][0])
            assert len({v for row in decoded for v in row}) == kept
        else:
            assert decoded == [[12 + v for v in row] for row in crests[team].pixels]


def test_crests_that_cannot_lose_colours_fall_back(rom, out):
    """Two-colour noise cannot be simplified: the largest crests take their
    fallback, the two-band flag or (None) the cartridge's own."""
    crests = {team: _noise(team, colours=(0, 3)) for team in range(TOTAL_TEAMS)}
    fallbacks = {
        team: (None if team % 2 else two_band_flag((255, 0, 0), (0, 0, 255)))
        for team in range(TOTAL_TEAMS)
    }
    notes = _write_flags(rom, out, crests, fallbacks)
    image = out.read_bytes()
    assert notes
    assert not any("simplified" in note for note in notes.values())
    for team, note in notes.items():
        if team % 2:
            assert note == "did not fit; the cartridge's flag stays"
            assert _blobs(image, team) == [fixture.flag_half_blob(team, h) for h in (0, 1)]
        else:
            assert note == "did not fit; two-band flag instead"
    kept = [team for team in range(TOTAL_TEAMS) if team not in notes]
    assert kept
    for team in kept:
        assert _pixels(image, team) == [[12 + v for v in row] for row in crests[team].pixels]


def test_the_flag_room_holds_2688_bytes():
    assert FLAG_ROOM_SIZE == fixture.FLAG_ROOM_END - fixture.FLAG_ROOM_START == 2688


def test_the_eight_borrowing_teams_get_cells_of_their_own(rom, out):
    before = rom.read_bytes()
    _write_flags(rom, out, {})
    after = out.read_bytes()
    for team, name in enumerate(TEAM_ENUM_ORDER):
        cell = fixture.OFS_FLAG_CELLS + 2 * team
        record = fixture.FLAG_SPRITE_BASE + 9 * team
        if name not in fixture.OWN_FLAG_CELLS:
            assert after[cell : cell + 2] == before[cell : cell + 2], name
            assert after[record : record + 9] == before[record : record + 9], name
            continue
        tile = fixture.OWN_FLAG_CELLS[name]
        assert struct.unpack("<H", after[cell : cell + 2])[0] == 0x1000 + (tile - 0x100) * 16
        expected = bytes([2, 0, 8, (tile + 1) & 0xFF, 0x13, 0, 0, tile & 0xFF, 0x13])
        assert after[record : record + 9] == expected, name


def test_a_sprite_record_that_is_not_two_sprites_is_refused(tmp_path, out):
    image = fixture.build_iss_rom()
    image[fixture.FLAG_SPRITE_BASE + 9 * TEAM_ENUM_ORDER.index("Holland")] = 3
    rom = tmp_path / "odd.sfc"
    rom.write_bytes(bytes(image))
    with pytest.raises(RomError, match="Holland"):
        _write_flags(rom, out, {})


WHITE, BLACK, RED, BLUE = (255, 255, 255), (0, 0, 0), (200, 0, 0), (0, 0, 200)
#: White as `map_rosters` builds it from ESPN's colours (Botafogo's away kit).
NEAR_WHITE = (250, 250, 252)


@pytest.mark.parametrize(
    "shirts",
    [None, (WHITE, BLACK), ((0, 0, 255), (255, 255, 0))],
    ids=["no shirts", "white and black shirts", "blue and yellow shirts"],
)
def test_an_incompressible_crest_does_not_cost_the_others_their_place(rom, out, shirts):
    """Two-colour noise cannot lose a colour, so it keeps its place and the
    speckled crests shrink instead: nothing falls back. With shirts, red
    numbers take entry 12 and the crests' white background leaves it; their
    reds are near enough to take entry 12 once down to two colours, and still
    nothing falls back."""
    noise = Flag(_noise(0, colours=(0, 3)).pixels, [WHITE, (20, 20, 20), (235, 235, 235), BLACK])
    crests = {0: noise} | {team: _speckled(team) for team in range(1, 26)}
    fallbacks = {team: two_band_flag((255, 0, 0), (0, 0, 255)) for team in crests}
    total = sum(len(b) for f in crests.values() for b in _flag_blobs(f)) + 12  # + team 26
    assert total > FLAG_ROOM_SIZE
    notes = _write_flags(rom, out, crests, fallbacks, shirts and {team: shirts for team in crests})
    assert not any("did not fit" in note for note in notes.values())
    assert any("simplified" in note for note in notes.values())
    assert 0 not in notes
    image = out.read_bytes()
    assert _shown(image, 0) == _as_shown(noise)
    if shirts is not None:
        assert all(_entry12(image, team) == (255, 0, 0) for team in crests)


def test_a_crest_loses_colours_until_that_saves_bytes(rom, out):
    """Four colours of noise compress no better as three, but far better as
    two. The others cannot lose a colour, so the noise must go down two steps
    for everything to fit; given up after one, the largest crest would fall
    back."""
    noise = _noise(4)
    three = _fewer_colours(noise)
    sizes = [sum(map(len, _flag_blobs(f))) for f in (noise, three, _fewer_colours(three))]
    assert sizes[0] == sizes[1] > sizes[2]
    crests = {0: noise} | {team: _noise(team, colours=(0, 3)) for team in range(1, 24)}
    total = sum(len(b) for f in crests.values() for b in _flag_blobs(f)) + 12 * (27 - 24)
    assert FLAG_ROOM_SIZE < total <= FLAG_ROOM_SIZE + sizes[0] - sizes[2]
    fallbacks = {team: two_band_flag((255, 0, 0), (0, 0, 255)) for team in crests}
    notes = _write_flags(rom, out, crests, fallbacks)
    assert notes == {0: "simplified to 2 colours to fit"}
    assert len({v for row in _pixels(out.read_bytes(), 0) for v in row}) == 2


def test_a_colour_near_the_number_colour_may_take_entry_12():
    """Two colours, neither on entry 12: red numbers take the crest's dark red,
    which then compresses far better; black numbers are too far from it, and
    a crest cannot go below two colours."""
    crest = Flag([[1] * 14 + [2] * 10 for _ in range(16)], [(255, 0, 0), WHITE, RED, RED])
    simpler = _fewer_colours(crest)
    assert simpler is not None
    assert _shows(simpler) == [[WHITE] * 14 + [(255, 0, 0)] * 10] * 16
    assert sum(map(len, _flag_blobs(simpler))) < sum(map(len, _flag_blobs(crest)))
    assert _fewer_colours(Flag(crest.pixels, [BLACK, WHITE, RED, RED])) is None


def _distance(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b, strict=True)) ** 0.5


def _rgb(word):
    return tuple((word >> shift & 31) * 255 // 31 for shift in (0, 5, 10))


def _six(home, away):
    """Every shade the shirt numbers sit on: three per shirt, as the game draws them."""
    return [_rgb(word) for kit in (home, away) for word in _make_shades(*kit, 3)]


def _stands_out(colour, shades):
    """The six-shade rule: 150 or more in RGB from every shade of both shirts."""
    return min(_distance(colour, shade) for shade in shades) >= 150


def _entry12(image, team):
    """Team `team`'s palette entry 12, the shirt-number colour, as RGB."""
    return _rgb(struct.unpack("<H", _colour_record(image, TEAM_ENUM_ORDER[team])[:2])[0])


def _shown(image, team):
    """Team `team`'s flag as the BGR555 colours its pixels show."""
    words = struct.unpack("<4H", _colour_record(image, TEAM_ENUM_ORDER[team]))
    return [[words[v & 3] for v in row] for row in _pixels(image, team)]


def _as_shown(flag):
    """What `_shown` reads back once `flag` is written as it is."""
    return [[_rgb_to_bgr555(*flag.colours[v]) for v in row] for row in flag.pixels]


def _shows(flag):
    """`flag` as the colours its pixels show."""
    return [[flag.colours[v] for v in row] for row in flag.pixels]


def test_the_number_colour_is_the_crests_own_when_that_stands_out():
    crest = Flag([[0] * 12 + [1] * 12] * 16, [RED, WHITE, RED, RED])
    numbered = number_first(crest, _six(RED, BLUE))
    assert numbered.colours[0] == WHITE
    # Nothing is lost: every pixel shows the colour it did.
    assert _shows(numbered) == _shows(crest)


def test_a_number_colour_the_crest_lacks_takes_its_least_missed_colour():
    """Black and white shirts: no crest colour stands out from all six shades,
    so red does, and the crest's dark red, the colour nearest it, gives way."""
    grey = (120, 120, 120)
    crest = Flag([[0] * 6 + [1] * 6 + [2] * 6 + [3] * 6] * 16, [WHITE, BLACK, grey, RED])
    assert number_colour(crest, _six(WHITE, BLACK)) == (255, 0, 0)
    numbered = number_first(crest, _six(WHITE, BLACK))
    assert numbered.colours[0] == (255, 0, 0)
    assert _shows(numbered)[0] == [WHITE] * 6 + [BLACK] * 6 + [grey] * 6 + [(255, 0, 0)] * 6


@pytest.mark.parametrize(
    "crest",
    [[BLACK, WHITE], [BLACK, WHITE, (254, 215, 12)], [WHITE, (255, 243, 19), (8, 108, 57)]],
    ids=["black and white crest", "gold in the crest", "yellow in the crest"],
)
@pytest.mark.parametrize(
    "home, away", [(BLACK, NEAR_WHITE), (NEAR_WHITE, BLACK)], ids=["black home", "white home"]
)
def test_black_and_white_shirts_get_numbers_that_read_on_every_shade(home, away, crest):
    """Botafogo and Atletico-MG. Mid-grey is the white shirt's dark shade and
    next to the black one's light shade; gold and yellow are far from white
    in RGB, but nearly all of it in blue, and read poorly on it. Red stands
    out from all six shades."""
    flag = Flag([[i % len(crest) for i in range(24)]] * 16, crest + [crest[-1]] * (4 - len(crest)))
    shades = _six(home, away)
    assert number_colour(flag, shades) == (255, 0, 0)
    assert _stands_out((255, 0, 0), shades)


def test_a_two_band_flag_keeps_its_bands_off_entry_12():
    numbered = number_first(two_band_flag(RED, BLUE), _six(RED, BLUE))
    assert _shows(numbered) == [[RED] * 24] * 8 + [[BLUE] * 24] * 8
    assert 0 not in {v for row in numbered.pixels for v in row}
    assert _stands_out(numbered.colours[0], _six(RED, BLUE))


def test_the_colours_after_entry_12_take_their_smallest_order():
    """Red numbers on white and black shirts: a speckled crest keeps its three
    colours, its white off entry 12, in whichever order compresses best."""
    crest = _speckled(1)
    numbered = number_first(crest, _six(WHITE, BLACK))
    assert numbered.colours[0] == (255, 0, 0)
    assert _shows(numbered) == _shows(crest)
    size = sum(map(len, _flag_blobs(numbered)))
    for order in permutations((1, 2, 3)):
        moved = Flag(
            [[order[v - 1] if v else 0 for v in row] for row in numbered.pixels],
            [numbered.colours[0]] + [numbered.colours[order.index(k) + 1] for k in (1, 2, 3)],
        )
        assert _shows(moved) == _shows(numbered)
        assert size <= sum(map(len, _flag_blobs(moved)))


def test_entry_12_stands_out_from_both_shirts(rom, out):
    """Crest and two-band alike, against every shade of a white home and a
    black away shirt."""
    flags = {0: _noise(1), 9: two_band_flag(WHITE, BLACK)}
    _write_flags(rom, out, flags, shirts={0: (WHITE, BLACK), 9: (WHITE, BLACK)})
    image = out.read_bytes()
    for team in flags:
        assert _stands_out(_entry12(image, team), _six(WHITE, BLACK)), team
    assert _shown(image, 9) == _as_shown(two_band_flag(WHITE, BLACK))


def test_a_kit_left_alone_is_read_from_the_rom(rom, out):
    """No away kit given: the cartridge's away shirt counts, all three of its
    shades read from the ROM."""
    image = bytearray(rom.read_bytes())
    away = fixture.OFS_KIT2_RANGE1  # Germany's away kit: three shades
    cartridge = (0x7FFF, 0x5294, 0x294A)  # white, light grey, dark grey
    image[away : away + 6] = struct.pack("<3H", *cartridge)
    rom.write_bytes(bytes(image))
    _write_flags(rom, out, {0: two_band_flag(BLACK, RED)}, shirts={0: (BLACK, None)})
    shades = _six(BLACK, BLACK)[:3] + [_rgb(word) for word in cartridge]
    assert _stands_out(_entry12(out.read_bytes(), 0), shades)


def test_new_kits_redraw_the_cartridge_flag_around_the_numbers(rom, out):
    """The fixture's flag uses two colours, so it loses none to the numbers."""
    _write_flags(rom, out, {}, shirts={3: (WHITE, BLACK)})
    image = out.read_bytes()
    assert _stands_out(_entry12(image, 3), _six(WHITE, BLACK))
    assert _shown(image, 3) == _shown(rom.read_bytes(), 3)


def _name_text_pointers(path, header=0):
    data = path.read_bytes()
    base = fixture.OFS_TEAM_NAME_TEXT_PTRS + header
    return [
        fixture.decode_p40000(data[base + i * 2 : base + i * 2 + 2]) for i in range(TOTAL_TEAMS)
    ]


def test_the_name_text_pointers_stay_in_ascending_order(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_team_name_texts({0: "ARSENAL"})
    writer.finalize()
    pointers = _name_text_pointers(out)
    assert sorted(pointers) == pointers


def test_the_first_name_text_pointer_is_the_lowest_original_address(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_team_name_texts({0: "ARSENAL"})
    writer.finalize()
    assert _name_text_pointers(out)[0] == fixture.NAME_TEXT_BASE


def test_a_patched_team_gets_the_new_encoding_at_its_pointer(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_team_name_texts({0: "ARSENAL"})
    writer.finalize()
    expected = _encode_team_name_text("ARSENAL")
    assert _read(out, _name_text_pointers(out)[0], len(expected)) == expected


def test_an_unpatched_teams_blob_is_copied_through_verbatim(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_team_name_texts({0: "ARSENAL"})
    writer.finalize()
    blob = fixture.name_text_blob(3)
    assert _read(out, _name_text_pointers(out)[3], len(blob)) == blob


def test_each_pointer_is_the_previous_one_plus_that_teams_blob_length(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_team_name_texts({0: "ARSENAL"})
    writer.finalize()
    pointers = _name_text_pointers(out)
    lengths = [len(_encode_team_name_text("ARSENAL"))] + [
        len(fixture.name_text_blob(i)) for i in range(1, TOTAL_TEAMS - 1)
    ]
    assert [b - a for a, b in zip(pointers[:-1], pointers[1:], strict=True)] == lengths


def test_a_budget_too_small_for_the_patched_names_shortens_them(tmp_path, out):
    """The truncation loop: names are cut a character at a time, longest first,
    until the whole table fits."""
    rom = fixture.write_iss_rom(
        tmp_path / "tight.sfc", name_text_base=fixture.MAX_NAME_TEXT_ADDR - 700
    )
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_team_name_texts({i: "WOLVERHAMPTON WANDERERS" for i in range(TOTAL_TEAMS)})
    writer.finalize()
    pointers = _name_text_pointers(out)
    assert pointers[-1] + 1 <= fixture.MAX_NAME_TEXT_ADDR


def test_the_shortened_names_are_shorter_than_the_ones_asked_for(tmp_path, out):
    rom = fixture.write_iss_rom(
        tmp_path / "tight.sfc", name_text_base=fixture.MAX_NAME_TEXT_ADDR - 700
    )
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_team_name_texts({i: "WOLVERHAMPTON WANDERERS" for i in range(TOTAL_TEAMS)})
    writer.finalize()
    full = len(_encode_team_name_text("WOLVERHAMPTON WANDERERS"))
    pointers = _name_text_pointers(out)
    assert pointers[1] - pointers[0] < full


def test_a_generous_budget_leaves_the_names_alone(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_team_name_texts({0: "WOLVERHAMPTON WANDERERS"})
    writer.finalize()
    expected = _encode_team_name_text("WOLVERHAMPTON WANDERERS")
    assert _read(out, _name_text_pointers(out)[0], len(expected)) == expected


# The mid-blob cut. 27 patched names shrink to three characters each and encode
# to 25 bytes apiece -- 675 in all -- so a budget under that reaches the final
# slice with the shrink loop already exhausted.
#
# 411 rather than a round number, and the choice is load-bearing twice. 411 is
# not a multiple of 25, so the cut lands 11 bytes inside team 16's blob and the
# case really is a *mid-blob* cut rather than a short write that stopped on a
# boundary. And at 411 the last byte the writer puts down differs from the byte
# the image already held there, so a cap one byte tighter or looser moves an
# assertion below -- at 410 and at 412 the two coincide and the boundary is
# invisible.
_STARVED_BUDGET = 411
_SHRUNK_BLOB = 25
_CUT_TEAM = 16
_CUT_WITHIN_BLOB = 11


@pytest.fixture
def starved(tmp_path):
    return fixture.write_iss_rom(
        tmp_path / "starved.sfc",
        name_text_base=fixture.MAX_NAME_TEXT_ADDR - _STARVED_BUDGET,
    )


def _starve(starved, out):
    writer = ISSRomWriter(str(starved), str(out))
    writer.write_team_name_texts({i: "WOLVERHAMPTON WANDERERS" for i in range(TOTAL_TEAMS)})
    writer.finalize()
    return _name_text_pointers(out)


def test_the_shrink_loop_really_does_run_out_at_three_characters():
    """Sizes the fixture: the floor is 675 bytes and the budget is 411, so the
    starved case below is starved and not merely tight."""
    assert len(_encode_team_name_text("WOL")) == _SHRUNK_BLOB
    assert _SHRUNK_BLOB * TOTAL_TEAMS == 675
    assert _STARVED_BUDGET < 675
    assert _STARVED_BUDGET % _SHRUNK_BLOB == _CUT_WITHIN_BLOB


def test_a_budget_below_the_shrink_floor_still_reports_success(starved, out):
    """INHERITED DEFECT, PRESERVED and pinned. Nothing signals the overflow.

    `write_name_tiles` raises `RomError` in the same situation; this method
    returns, and the argument for the asymmetry is at the line in `rom_writer`.
    """
    writer = ISSRomWriter(str(starved), str(out))
    result = writer.write_team_name_texts(
        {i: "WOLVERHAMPTON WANDERERS" for i in range(TOTAL_TEAMS)}
    )
    writer.close()
    assert result is None


def test_the_pointer_table_names_ten_teams_the_writer_never_wrote(starved, out):
    """The corruption, counted. The table is written before the slice and is
    not shortened with it."""
    pointers = _starve(starved, out)
    cut = fixture.MAX_NAME_TEXT_ADDR
    assert len([p for p in pointers if p >= cut]) == 10
    assert pointers[-1] == cut + 239


def test_the_write_stops_at_exactly_the_ceiling(starved, out):
    """The boundary, to the byte, on an image where the two sides differ.

    The whole written region is compared against the 675 bytes that were built,
    cut at the budget; then the first byte past the ceiling is asserted to still
    hold the image's own value, and to be a value the intended data would have
    changed. A cap one byte short or one byte long moves one of the three.
    """
    _starve(starved, out)
    before = starved.read_bytes()
    after = out.read_bytes()
    cut = fixture.MAX_NAME_TEXT_ADDR
    start = cut - _STARVED_BUDGET
    intended = _encode_team_name_text("WOL") * TOTAL_TEAMS
    assert after[start:cut] == intended[:_STARVED_BUDGET]
    assert after[cut] == before[cut]
    assert intended[_STARVED_BUDGET] != before[cut]


def test_the_blob_at_the_cut_is_severed_in_the_middle_of_itself(starved, out):
    """Eleven bytes of team 16's display list reach the image and fourteen do not.

    Both halves asserted: a short write that stopped on a blob boundary would
    satisfy the first and fail the second.
    """
    pointers = _starve(starved, out)
    intended = _encode_team_name_text("WOL")
    written = _read(out, pointers[_CUT_TEAM], _SHRUNK_BLOB)
    assert pointers[_CUT_TEAM] + _CUT_WITHIN_BLOB == fixture.MAX_NAME_TEXT_ADDR
    assert written[:_CUT_WITHIN_BLOB] == intended[:_CUT_WITHIN_BLOB]
    assert written[_CUT_WITHIN_BLOB:] != intended[_CUT_WITHIN_BLOB:]


def test_the_teams_before_the_cut_are_written_correctly(starved, out):
    """The other side of it: this is a tail overflow and not a general failure,
    which is why nothing downstream notices."""
    pointers = _starve(starved, out)
    intended = _encode_team_name_text("WOL")
    before = [_read(out, pointers[i], _SHRUNK_BLOB) for i in range(_CUT_TEAM)]
    assert before == [intended] * _CUT_TEAM


def test_a_non_positive_budget_raises_rather_than_writing_past_the_ceiling(tmp_path, out):
    """DELIBERATE DIVERGENCE. `all_data[:budget]` with a negative budget is not
    a truncation: it drops bytes from the end and writes the rest at `min_addr`,
    which is by definition at or past the ceiling."""
    rom = fixture.write_iss_rom(
        tmp_path / "over.sfc", name_text_base=fixture.MAX_NAME_TEXT_ADDR + 8
    )
    writer = ISSRomWriter(str(rom), str(out))
    with pytest.raises(RomError, match="ceiling"):
        writer.write_team_name_texts({0: "ARSENAL"})
    writer.close()


def test_a_budget_of_exactly_zero_also_raises(tmp_path, out):
    rom = fixture.write_iss_rom(tmp_path / "zero.sfc", name_text_base=fixture.MAX_NAME_TEXT_ADDR)
    writer = ISSRomWriter(str(rom), str(out))
    with pytest.raises(RomError):
        writer.write_team_name_texts({0: "ARSENAL"})
    writer.close()


def test_all_ten_code_bytes_are_redirected_to_the_new_bank(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_name_tiles({0: "TST"})
    writer.finalize()
    data = out.read_bytes()
    assert [data[p] for p in fixture.DISPLACEMENT_PATCH_POINTS] == [fixture.PATCHED_BANK_BYTE] * 10


def test_those_ten_bytes_held_the_old_bank_before_the_patch(rom):
    """Without this the assertion above passes against a writer that patched
    nothing, if the fixture happened to hold 0x82 already."""
    data = rom.read_bytes()
    assert [data[p] for p in fixture.DISPLACEMENT_PATCH_POINTS] == [
        fixture.UNPATCHED_BANK_BYTE
    ] * 10


def test_the_ten_patch_points_are_ten_distinct_addresses(rom, out):
    assert len(set(fixture.DISPLACEMENT_PATCH_POINTS)) == 10


def test_the_code_patch_is_shifted_by_a_copier_header(tmp_path, out):
    rom = fixture.write_iss_rom(tmp_path / "iss.smc", with_header=True)
    writer = ISSRomWriter(str(rom), str(out), 512)
    writer.write_name_tiles({0: "TST"})
    writer.finalize()
    data = out.read_bytes()
    point = fixture.DISPLACEMENT_PATCH_POINTS[0]
    assert data[point + 512] == fixture.PATCHED_BANK_BYTE
    # The headerless address holds the copier header's own filler here, and the
    # write must not have reached it.
    assert data[point] == rom.read_bytes()[point]


def test_the_blobs_are_relocated_to_the_free_region(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_name_tiles({0: "TST"})
    writer.finalize()
    data = out.read_bytes()
    base = fixture.OFS_NAME_TILES_PTRS
    assert fixture.decode_p17000(data[base : base + 2]) == fixture.NAME_TILES_DISPLACED_BASE


def test_the_pointer_table_switches_to_the_p17000_encoding(rom, out):
    """The old table was P48000, unbiased; the new one is biased, which is what
    the ten redirected code bytes make correct."""
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_name_tiles({0: "TST"})
    writer.finalize()
    data = out.read_bytes()
    assert data[fixture.OFS_NAME_TILES_PTRS : fixture.OFS_NAME_TILES_PTRS + 2] == _encode_p17000(
        fixture.NAME_TILES_DISPLACED_BASE
    )


def test_a_patched_team_gets_a_freshly_compressed_sixty_nine_byte_blob(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_name_tiles({0: "TST"})
    writer.finalize()
    expected = fixture.konami_literal(_serialize_2bpp(_render_name_tiles("TST")))
    assert len(expected) == 69
    assert _read(out, fixture.NAME_TILES_DISPLACED_BASE, 69) == expected


def test_an_unpatched_teams_blob_is_relocated_unchanged(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_name_tiles({0: "TST"})
    writer.finalize()
    data = out.read_bytes()
    base = fixture.OFS_NAME_TILES_PTRS + 2
    second = fixture.decode_p17000(data[base : base + 2])
    assert data[second : second + fixture.NAME_TILE_BLOB_SIZE] == fixture.name_tile_blob(1)


def test_the_blobs_are_laid_out_end_to_end(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_name_tiles({0: "TST"})
    writer.finalize()
    data = out.read_bytes()
    base = fixture.OFS_NAME_TILES_PTRS
    pointers = [
        fixture.decode_p17000(data[base + i * 2 : base + i * 2 + 2]) for i in range(TOTAL_TEAMS)
    ]
    lengths = [69] + [fixture.NAME_TILE_BLOB_SIZE] * (TOTAL_TEAMS - 2)
    assert [b - a for a, b in zip(pointers[:-1], pointers[1:], strict=True)] == lengths


def test_the_whole_relocated_table_stays_inside_the_free_region(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_name_tiles({i: "TST" for i in range(TOTAL_TEAMS)})
    writer.finalize()
    data = out.read_bytes()
    base = fixture.OFS_NAME_TILES_PTRS + (TOTAL_TEAMS - 1) * 2
    last = fixture.decode_p17000(data[base : base + 2])
    assert last + 69 <= fixture.NAME_TILES_DISPLACED_END


def test_blobs_that_do_not_fit_the_free_region_raise(tmp_path, out):
    """DELIBERATE DIVERGENCE: upstream raised a bare `ValueError`, outside this
    library's hierarchy. Same condition, correct type."""
    rom = fixture.write_iss_rom(tmp_path / "fat.sfc", name_tile_blob_size=91)
    writer = ISSRomWriter(str(rom), str(out))
    with pytest.raises(RomError, match="Name tiles too large"):
        writer.write_name_tiles({})
    writer.close()


def test_the_capacity_check_fires_before_any_code_byte_is_patched(tmp_path, out):
    """Step 3 precedes step 4, so a refused patch leaves the image alone."""
    rom = fixture.write_iss_rom(tmp_path / "fat.sfc", name_tile_blob_size=91)
    before = rom.read_bytes()
    writer = ISSRomWriter(str(rom), str(out))
    with pytest.raises(RomError):
        writer.write_name_tiles({})
    writer.close()
    assert out.read_bytes() == before


def test_one_byte_under_the_capacity_is_accepted(tmp_path, out):
    """27 blobs of 90 is 2 430, two under the 2 432 the region holds."""
    rom = fixture.write_iss_rom(tmp_path / "ok.sfc", name_tile_blob_size=90)
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_name_tiles({})
    writer.finalize()
    assert 27 * 90 <= NAME_TILES_CAPACITY


def test_a_patched_description_is_the_team_name_centred_in_fifteen_column_lines(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_team_descriptions({0: "Arsenal"})
    writer.finalize()
    expected = fixture.centred_description("Arsenal")
    assert _read(out, fixture.desc_text_start(0), len(expected)) == expected


def test_a_long_name_wraps_across_lines(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_team_descriptions({0: "Wolverhampton Wanderers"})
    writer.finalize()
    expected = fixture.centred_description("Wolverhampton Wanderers")
    assert _read(out, fixture.desc_text_start(0), len(expected)) == expected


def test_the_description_write_stops_at_the_blocks_terminator(rom, out):
    """The 0xFF the scan found is the end, and it is not overwritten."""
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_team_descriptions({0: "Arsenal"})
    writer.finalize()
    end = fixture.desc_text_start(0) + fixture.DESC_TEXT_LENGTH
    assert out.read_bytes()[end] == fixture.DESC_TERMINATOR


def test_an_unpatched_team_keeps_its_original_description(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_team_descriptions({0: "Arsenal"})
    writer.finalize()
    assert _read(out, fixture.desc_text_start(4), fixture.DESC_TEXT_LENGTH) == fixture.desc_text(4)


def test_a_pointer_outside_bank_two_is_skipped_rather_than_seeking_backwards(tmp_path, out):
    """DELIBERATE DIVERGENCE. `0x10000 + (snes_addr - 0x8000)` is negative for a
    small enough pointer and `_seek` cannot take one; upstream computed it
    anyway. The slot keeps its 1994 description, which is what an unpatched team
    gets."""
    rom = fixture.write_iss_rom(tmp_path / "bank.sfc", break_desc_bank=True)
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_team_descriptions({7: "Arsenal", 8: "Chelsea"})
    writer.finalize()
    assert _read(out, fixture.desc_text_start(7), fixture.DESC_TEXT_LENGTH) == fixture.desc_text(7)
    assert _read(out, fixture.desc_text_start(8), 15) == fixture.centred_description("Chelsea")[:15]


def test_a_pointer_one_bank_low_does_not_overwrite_the_predominant_colour_table(tmp_path, out):
    """The consequence the bank guard actually prevents.

    `0x10000 + (snes_addr - 0x8000)` reduces to `0x8000 + snes_addr`, so it is
    never negative -- it lands one bank low. This fixture plants a well-formed
    description block at 0x8DA0, whose text would start at 0x8DB4, which is
    inside the 27-byte predominant-colour table at 0x8DB2.
    """
    rom = fixture.write_iss_rom(tmp_path / "low.sfc", desc_pointer_one_bank_low=True)
    before = rom.read_bytes()
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_team_descriptions({7: "Arsenal"})
    writer.finalize()
    table = slice(fixture.OFS_PREDOMINANT_COLOR, fixture.OFS_PREDOMINANT_COLOR + TOTAL_TEAMS)
    assert out.read_bytes()[table] == before[table]


def test_the_low_bank_block_is_well_formed_enough_to_be_written_to(tmp_path, out):
    """Without this the test above passes against any writer that failed to find
    a description start there for some unrelated reason."""
    rom = fixture.write_iss_rom(tmp_path / "low.sfc", desc_pointer_one_bank_low=True)
    planted = rom.read_bytes()[
        fixture.LOW_BANK_DESC_OFFSET : fixture.LOW_BANK_DESC_OFFSET + len(fixture.DESC_HEADER)
    ]
    assert planted == fixture.DESC_HEADER
    assert fixture.LOW_BANK_DESC_OFFSET + len(fixture.DESC_HEADER) > fixture.OFS_PREDOMINANT_COLOR


def test_a_name_with_no_ascii_at_all_pads_the_block_with_spaces(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_team_descriptions({0: "日本"})
    writer.finalize()
    assert _read(out, fixture.desc_text_start(0), fixture.DESC_TEXT_LENGTH) == b" " * 60


def test_the_constructor_copies_the_input_to_the_output(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.close()
    assert out.read_bytes() == rom.read_bytes()


def test_close_releases_the_handle_and_is_idempotent(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.close()
    writer.close()
    assert writer._f is None


def test_finalize_leaves_nothing_for_close_to_do(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.finalize()
    writer.close()
    assert writer._f is None


def test_the_context_manager_releases_the_handle_on_an_exception(rom, out):
    with pytest.raises(ZeroDivisionError):
        with ISSRomWriter(str(rom), str(out)) as writer:
            raise ZeroDivisionError("something the caller did")
    assert writer._f is None


def test_the_context_manager_releases_the_handle_on_success(rom, out):
    with ISSRomWriter(str(rom), str(out)) as writer:
        writer.write_predominant_color(0, (255, 0, 0))
    assert writer._f is None


def test_writing_after_close_raises_a_library_error_and_not_an_attribute_error(rom, out):
    """DELIBERATE DIVERGENCE: upstream's methods reached `self._f` directly, and
    `finalize` sets it to `None`, so a second write raised `AttributeError` from
    inside the writer -- outside this library's hierarchy."""
    writer = ISSRomWriter(str(rom), str(out))
    writer.finalize()
    with pytest.raises(RomError, match="already closed"):
        writer.write_predominant_color(0, (255, 0, 0))


@pytest.mark.parametrize(
    ("home", "away"),
    [
        ((0xC6, 0x00, 0x00), (0x00, 0x93, 0xEC)),  # Bahia: blue was a point ahead of black
        ((0x26, 0x58, 0x91), (0xFA, 0xFA, 0xFA)),  # Remo: red was a point ahead of black
    ],
)
def test_a_near_tie_goes_to_the_brighter_number_colour(home, away):
    """When a hue scores a hair above black, black wins: digits a pixel wide
    read by brightness, and black on these shirts is the crisper of the two."""
    from retro_roster_patcher.games.iss_snes.rom_writer import _kit_shades, number_colour

    flag = Flag(pixels=[[0] * 24 for _ in range(16)], colours=[home, away, home, away])
    assert number_colour(flag, _kit_shades(home) + _kit_shades(away)) == (0, 0, 0)


# --- Looks -----------------------------------------------------------------
#
# A team's hair and five skin shades sit 12 bytes before each of its kits;
# its alternate look is a pointer per team into bank $85 at $81:8D3C (skin)
# and $81:8D74 (hair); its keeper's hair and three shades sit 8 bytes before
# the keeper's gloves. Traced in the code at $80:ADDA-$80:AE7A.

from retro_roster_patcher.games.iss_snes import looks as iss_looks  # noqa: E402
from retro_roster_patcher.games.iss_snes.models import ISSLook  # noqa: E402
from retro_roster_patcher.games.iss_snes.rom_writer import (  # noqa: E402
    ALT_HAIR_RECORDS,
    ALT_SKIN_RECORDS,
)


def _words(path, offset, count):
    return list(struct.unpack(f"<{count}H", _read(path, offset, 2 * count)))


def _bank85(address):
    return 0x28000 + address - 0x8000


def _colours(*rgbs):
    """Exact BGR555: the cartridge's colours are 5-bit values times 8."""
    return [(r >> 3) | (g >> 3) << 5 | (b >> 3) << 10 for r, g, b in rgbs]


def test_a_look_puts_the_hair_and_five_shades_before_both_kits(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_look(0, ISSLook(skin=iss_looks.DARK, hair="blond"))
    writer.finalize()
    expected = _colours(iss_looks.HAIR["blond"], *iss_looks.SKIN_RAMPS[iss_looks.DARK])
    assert _words(out, fixture.OFS_KIT1_RANGE1 - 12, 6) == expected
    assert _words(out, fixture.OFS_KIT2_RANGE1 - 12, 6) == expected


def test_a_second_range_team_gets_its_look_before_its_own_kits(rom, out):
    scotland = TEAM_ENUM_ORDER.index("Scotland")  # second of the eight in range 2
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_look(scotland, ISSLook(skin=iss_looks.TAN, hair="red"))
    writer.finalize()
    expected = _colours(iss_looks.HAIR["red"], *iss_looks.SKIN_RAMPS[iss_looks.TAN])
    assert _words(out, fixture.OFS_KIT1_RANGE2 + 32 - 12, 6) == expected
    assert _words(out, fixture.OFS_KIT2_RANGE2 + 32 - 12, 6) == expected


def test_a_look_leaves_the_kits_alone(rom, out):
    before = _read(rom, fixture.OFS_KIT1_RANGE1, 16) + _read(rom, fixture.OFS_KIT2_RANGE1, 16)
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_look(0, ISSLook(skin=iss_looks.DARK, hair="blond"))
    writer.finalize()
    after = _read(out, fixture.OFS_KIT1_RANGE1, 16) + _read(out, fixture.OFS_KIT2_RANGE1, 16)
    assert after == before


def test_the_alternate_points_at_the_shared_records(rom, out):
    slot = TEAM_ENUM_ORDER.index("Brazil")
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_look(slot, ISSLook(skin=iss_looks.TAN, alt_skin=iss_looks.DARK, alt_hair="blond"))
    writer.write_look(0, ISSLook())
    writer.finalize()
    assert _words(out, 0x8D3C + 2 * slot, 1) == [0xEA05]
    assert _words(out, 0x8D74 + 2 * slot, 1) == [0xEA1D]
    # No alternate: 0, the team's own look.
    assert _words(out, 0x8D3C, 1) == [0] and _words(out, 0x8D74, 1) == [0]


def test_the_shared_records_hold_the_ramps_and_hairs_they_are_named_for(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_look_records()
    writer.finalize()
    for skin, address in ALT_SKIN_RECORDS.items():
        assert _words(out, _bank85(address), 6) == [0x0009, *_colours(*iss_looks.SKIN_RAMPS[skin])]
    for hair, address in ALT_HAIR_RECORDS.items():
        assert _words(out, _bank85(address), 2) == [0x0001, *_colours(iss_looks.HAIR[hair])]
    assert set(ALT_SKIN_RECORDS) == set(iss_looks.ALT_SKINS)
    assert set(ALT_HAIR_RECORDS) == set(iss_looks.ALT_HAIRS)


def test_the_shared_records_the_retail_cartridge_points_at_keep_their_colours(rom, out):
    """Unpatched teams point at four skin and three hair records: rewriting
    them must not change how those teams look."""
    retail = {
        0xE9E1: [(128, 64, 24), (184, 88, 64), (200, 136, 112), (208, 184, 168), (224, 224, 216)],
        0xE9F9: [(88, 40, 16), (112, 56, 24), (168, 88, 64), (192, 136, 88), (224, 208, 176)],
        0xEA05: [(56, 32, 0), (72, 48, 0), (104, 56, 0), (168, 96, 0), (224, 168, 128)],
        0xEA11: [(128, 96, 0)],
        0xEA15: [(104, 24, 0)],
        0xEA19: [(32, 16, 0)],
    }
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_look_records()
    writer.finalize()
    for address, colours in retail.items():
        assert _words(out, _bank85(address) + 2, len(colours)) == _colours(*colours)


def test_the_keeper_gets_his_hair_and_three_shades(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_look(0, ISSLook(keeper_skin=iss_looks.DARKEST, keeper_hair="grey"))
    writer.finalize()
    ramp = iss_looks.SKIN_RAMPS[iss_looks.DARKEST]
    expected = _colours(iss_looks.HAIR["grey"], ramp[1], ramp[3], ramp[4])
    assert _words(out, fixture.OFS_GK_RANGE1 - 8, 4) == expected


def test_super_star_has_no_keeper_record_to_dress(rom, out):
    before = rom.read_bytes()
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_look(TEAM_ENUM_ORDER.index("Super Star"), ISSLook(keeper_skin=iss_looks.DARK))
    writer.finalize()
    gk_area = slice(fixture.OFS_GK_RANGE1 - 8, fixture.OFS_GK_RANGE1 + 18 * 24)
    assert out.read_bytes()[gk_area] == before[gk_area]


@pytest.mark.parametrize("alternate, expected", [(True, 0x40), (False, 0x00)])
def test_a_players_alternate_look_sets_bit_six_whatever_the_disc_had(rom, out, alternate, expected):
    for disc in (0x00, 0x40):
        image = bytearray(rom.read_bytes())
        image[fixture.OFS_PLAYER_DATA + 5] = (image[fixture.OFS_PLAYER_DATA + 5] & ~0x40) | disc
        rom.write_bytes(image)
        writer = ISSRomWriter(str(rom), str(out))
        writer.write_player_data(0, [_player(0, alternate=alternate)])
        writer.finalize()
        assert _record(out, 0, 0)[5] == expected
