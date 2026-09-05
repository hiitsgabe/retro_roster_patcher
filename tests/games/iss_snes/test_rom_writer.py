"""The ISS writer: encoders, the compressor, the font, and the machine-code patch.

This is the file where the port earns its keep, because none of the writer is
reviewable by reading. Four things in it are ROM hacking rather than field
writing -- a ten-byte 65816 patch, a compressor, three pointer encodings and a
hand-rolled 2bpp font -- and each is pinned here against the *rule* the module
documents rather than against its own output.

Two habits run through the file:

  * every read-back is of the *output* file, opened fresh. The writer holds the
    output handle for its whole lifetime and `finalize` is what flushes it, so
    reading before then can see a stale page;
  * every expected value is either hand-derived from a documented format or
    computed by `tests/fixtures/synthetic_iss_rom.py`, which retranscribes the
    formats independently. Nothing below calls the function it is checking to
    work out what that function should have returned.
"""

from __future__ import annotations

import pytest

from retro_roster_patcher.core.errors import RomError
from retro_roster_patcher.games.iss_snes.models import (
    HAIR_STYLES,
    PLAYERS_PER_TEAM,
    TOTAL_TEAMS,
    ISSPlayerAttributes,
    ISSPlayerRecord,
    ISSTeamRecord,
)
from retro_roster_patcher.games.iss_snes.rom_writer import (
    MIN_PATCHABLE_SIZE,
    NAME_TILES_CAPACITY,
    ISSRomWriter,
    _encode_iss_name,
    _encode_p17000,
    _encode_p40000,
    _encode_p48000,
    _encode_team_name_text,
    _konami_compress_literal,
    _make_shades,
    _make_solid_4bpp_tile,
    _render_name_tiles,
    _rgb_to_bgr555,
    _rgb_to_predominant,
    _serialize_2bpp,
    _shooting_to_rom,
    _speed_to_rom,
    _to_ascii,
)
from tests.fixtures import synthetic_iss_rom as fixture


@pytest.fixture
def rom(tmp_path):
    return fixture.write_iss_rom(tmp_path / "iss.sfc")


@pytest.fixture
def out(tmp_path):
    return tmp_path / "patched.sfc"


def _player(index, **kwargs):
    attrs = ISSPlayerAttributes(
        speed=kwargs.pop("speed", 9),
        shooting=kwargs.pop("shooting", 11),
        stamina=kwargs.pop("stamina", 13),
        technique=kwargs.pop("technique", 5),
    )
    return ISSPlayerRecord(
        name=kwargs.pop("name", f"WRITE{index:02d}"[:8]),
        shirt_number=kwargs.pop("shirt_number", index + 1),
        hair_style=kwargs.pop("hair_style", index % len(HAIR_STYLES)),
        is_special=kwargs.pop("is_special", False),
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


# -- text -------------------------------------------------------------------


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


# -- colour -----------------------------------------------------------------


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


def test_near_black_is_classified_red():
    """INHERITED ODDITY, pinned rather than fixed. The white arm needs a channel
    above 200, and with all three equal and low the `r >= g and r >= b` arm wins,
    so a black kit sets the predominant colour to Red."""
    assert _rgb_to_predominant(10, 10, 10) == 2


# -- the two attribute scales -----------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1, 0), (3, 1), (5, 2), (7, 3), (9, 4), (11, 5), (13, 6), (15, 7)],
)
def test_every_odd_shooting_value_maps_to_its_own_three_bit_index(value, expected):
    assert _shooting_to_rom(value) == expected


def test_an_even_shooting_value_rounds_down_on_a_tie():
    """`d < best_dist`, strictly, so 4 is equidistant from 3 and 5 and takes 3."""
    assert _shooting_to_rom(4) == _shooting_to_rom(3)


def test_a_shooting_value_above_the_scale_saturates():
    assert _shooting_to_rom(99) == 7


def test_a_shooting_value_below_the_scale_saturates():
    assert _shooting_to_rom(-5) == 0


@pytest.mark.parametrize("value", [1, 2, 3, 4, 5, 6, 7, 8])
def test_the_low_half_of_the_speed_scale_encodes_to_multiples_of_0x20(value):
    """To 8, not to 7. 0xE0 is the largest multiple of 0x20 a byte holds, so the
    decoder's exact branch covers exactly 1-8 and upstream stopped it one short.
    """
    assert _speed_to_rom(value) == (value - 1) * 0x20


def _decode_speed(byte):
    """The decoder `_speed_to_rom`'s docstring quotes from SkillData.java."""
    if byte % 0x20 == 0:
        return byte // 0x20 + 1
    return (byte + 1) // 0x20 + 8


@pytest.mark.parametrize("value", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16])
def test_every_speed_round_trips_through_the_games_own_decoder(value):
    """DELIBERATE DIVERGENCE: 8 is in this list, and used to be the exception."""
    assert _decode_speed(_speed_to_rom(value)) == value


def test_the_scales_midpoint_encodes_to_the_last_multiple_of_0x20():
    """`(8 - 8) * 0x20 - 1` was -1, `max(0, -1)` was 0, and 0 decodes to 1: the
    midpoint of the scale, and `ISSPlayerAttributes.speed`'s own default, was
    written as the slowest speed in the game. 0xE0, not 0x00, and not merely
    "something that decodes to 8" -- 31 bytes do."""
    assert _speed_to_rom(8) == 0xE0
    assert _decode_speed(0x00) == 1


def test_the_default_speed_no_provider_measured_is_the_one_that_used_to_break():
    """Why the single broken value was the expensive one."""
    assert ISSPlayerAttributes().speed == 8


def test_the_two_speed_branches_meet_without_a_gap_or_an_overlap():
    """Sixteen values, sixteen distinct bytes, and the join is 8 -> 9."""
    encoded = [_speed_to_rom(value) for value in range(1, 17)]
    assert encoded == [
        0x00,
        0x20,
        0x40,
        0x60,
        0x80,
        0xA0,
        0xC0,
        0xE0,
        31,
        63,
        95,
        127,
        159,
        191,
        223,
        255,
    ]
    assert len(set(encoded)) == 16


@pytest.mark.parametrize("value", list(range(1, 17)))
def test_every_encoded_speed_fits_in_the_byte_it_is_written_to(value):
    """`& 0xFF` was removed with the `max(0, ...)`; neither could ever fire."""
    assert 0 <= _speed_to_rom(value) <= 0xFF


def test_speed_is_clamped_at_both_ends():
    assert _speed_to_rom(0) == _speed_to_rom(1)
    assert _speed_to_rom(99) == _speed_to_rom(16)


# -- the three pointer encodings --------------------------------------------


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


# -- Konami literal-run compression -----------------------------------------


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


# -- the 2bpp font ----------------------------------------------------------


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


def test_a_name_with_no_renderable_character_falls_back_to_an_A():
    assert _render_name_tiles("!!!") == _render_name_tiles("A")


def test_an_empty_name_falls_back_to_an_A():
    assert _render_name_tiles("") == _render_name_tiles("A")


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


def test_a_solid_4bpp_tile_sets_the_bitplanes_its_colour_code_names():
    """0x0C is bitplanes 2 and 3, which is palette index 12."""
    tile = _make_solid_4bpp_tile(0x0C)
    assert set(tile[:16]) == {0x00}
    assert set(tile[16:]) == {0xFF}


def test_a_solid_4bpp_tile_is_thirty_two_bytes():
    assert len(_make_solid_4bpp_tile(0x0D)) == 32


# -- the selection-screen name display list ---------------------------------


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


# -- the size bound ---------------------------------------------------------


def test_the_minimum_is_the_flag_tile_write_and_nothing_higher():
    """Derived in the module from a `max` over its own offsets; retranscribed in
    the fixture as the flag-tile address plus two compressed 96-byte halves."""
    assert MIN_PATCHABLE_SIZE == fixture.OFS_FLAG_TILE_NEW + 204
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
        fixture.OFS_FLAG_TILE_NEW + 204,
    ]
    assert MIN_PATCHABLE_SIZE == max(ends)


def test_the_displaced_name_tile_region_holds_2432_bytes():
    assert NAME_TILES_CAPACITY == fixture.NAME_TILES_CAPACITY
    assert NAME_TILES_CAPACITY == 0x18000 - 0x17680


# -- write_player_names -----------------------------------------------------


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


# -- write_player_data ------------------------------------------------------


def _record(path, slot, player, header=0):
    base = fixture.OFS_PLAYER_DATA + (slot * PLAYERS_PER_TEAM + player) * 6
    return path.read_bytes()[base + header : base + header + 6]


def test_player_data_lands_in_the_enum_order_block_for_slot_five(rom, out):
    """Player data uses the *other* order, so slot 5 writes at index 5."""
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_data(5, [_player(0, speed=16)])
    writer.finalize()
    assert _record(out, 5, 0)[0] == _speed_to_rom(16)
    assert _record(out, 24, 0) == fixture.player_data_record(24, 0)


def test_the_speed_byte_is_replaced_outright(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_data(0, [_player(0, speed=3)])
    writer.finalize()
    assert _record(out, 0, 0)[0] == 0x40


def test_the_shooting_index_replaces_only_the_low_three_bits(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_data(0, [_player(0, shooting=15)])
    writer.finalize()
    original = fixture.player_data_record(0, 0)[1]
    assert _record(out, 0, 0)[1] == (original & 0xF8) | 7
    assert original & 0xF8 == 0xC8


def test_the_shooting_write_preserves_low_bits_that_were_already_set(rom, out):
    """The record at (0, 0) has a low nibble of 0, so it cannot tell `& 0xF8`
    from `& 0xFF`. The eighth record of team 0 has all three low bits set, and a
    shooting value of 1 encodes to index 0, so the mask is the only thing that
    can clear them."""
    original = fixture.player_data_record(0, 7)[1]
    assert original & 0x07 == 0x07
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_data(0, [_player(i, shooting=1) for i in range(8)])
    writer.finalize()
    assert _record(out, 0, 7)[1] == original & 0xF8


def test_the_technique_write_preserves_low_bits_that_were_already_set(rom, out):
    """The same hole in byte 2. (0, 5) has a low nibble of 0b000 in byte 1 but
    byte 2 there is 0xA8; (0, 4) is 0xAF, which has all three set."""
    original = fixture.player_data_record(0, 4)[2]
    assert original & 0x07 == 0x07
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_data(0, [_player(i, technique=1) for i in range(5)])
    writer.finalize()
    assert _record(out, 0, 4)[2] == original & 0xF8


def test_the_technique_index_replaces_only_the_low_three_bits(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_data(0, [_player(0, technique=1)])
    writer.finalize()
    original = fixture.player_data_record(0, 0)[2]
    assert _record(out, 0, 0)[2] == original & 0xF8


def test_the_shirt_number_is_stored_as_one_less_in_the_low_nibble(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_data(0, [_player(0, shirt_number=10)])
    writer.finalize()
    original = fixture.player_data_record(0, 0)[3]
    assert _record(out, 0, 0)[3] == (original & 0xF0) | 9


def test_the_shirt_number_is_clamped_to_one_through_sixteen(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_data(0, [_player(0, shirt_number=99), _player(1, shirt_number=0)])
    writer.finalize()
    assert _record(out, 0, 0)[3] & 0x0F == 15
    assert _record(out, 0, 1)[3] & 0x0F == 0


def test_stamina_is_stored_as_one_less_in_the_low_nibble(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_data(0, [_player(0, stamina=16)])
    writer.finalize()
    original = fixture.player_data_record(0, 0)[4]
    assert _record(out, 0, 0)[4] == (original & 0xF0) | 15


def test_the_special_flag_sets_bit_six(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_data(0, [_player(0, is_special=True, hair_style=0)])
    writer.finalize()
    assert _record(out, 0, 0)[5] & 0x40 == 0x40


def test_an_ordinary_player_clears_bit_six(rom, out):
    """The fixture record has it set, so this is a write and not a no-op."""
    assert fixture.player_data_record(0, 0)[5] & 0x40 == 0x40
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_data(0, [_player(0, is_special=False)])
    writer.finalize()
    assert _record(out, 0, 0)[5] & 0x40 == 0x00


def test_bits_seven_five_and_four_of_the_hair_byte_are_preserved(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_data(0, [_player(0, hair_style=2)])
    writer.finalize()
    original = fixture.player_data_record(0, 0)[5]
    assert _record(out, 0, 0)[5] & 0xB0 == original & 0xB0
    assert original & 0xB0 == 0xB0


def test_the_hair_style_is_clamped_to_the_length_of_the_style_table(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_data(0, [_player(0, hair_style=99)])
    writer.finalize()
    assert _record(out, 0, 0)[5] & 0x0F == len(HAIR_STYLES) - 1


def test_a_negative_hair_style_is_clamped_to_zero(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_player_data(0, [_player(0, hair_style=-3)])
    writer.finalize()
    assert _record(out, 0, 0)[5] & 0x0F == 0


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


# -- kits -------------------------------------------------------------------


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


# -- flags and the predominant colour ---------------------------------------


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


def test_the_two_flag_halves_are_written_once_at_the_free_region(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_flag_tiles_and_colors({0: ((255, 0, 0), (0, 0, 255))})
    writer.finalize()
    half = fixture.konami_literal(_make_solid_4bpp_tile(0x0C) * 3)
    assert _read(out, fixture.OFS_FLAG_TILE_NEW, len(half)) == half


def test_every_patched_team_points_at_the_one_shared_flag(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_flag_tiles_and_colors({0: ((255, 0, 0), (0, 0, 255)), 9: ((0, 255, 0), (0, 0, 0))})
    writer.finalize()
    data = out.read_bytes()
    first = data[fixture.OFS_FLAG_TILE_PTRS : fixture.OFS_FLAG_TILE_PTRS + 4]
    ninth = data[fixture.OFS_FLAG_TILE_PTRS + 36 : fixture.OFS_FLAG_TILE_PTRS + 40]
    assert first == ninth
    assert fixture.decode_p48000(first[:2]) == fixture.OFS_FLAG_TILE_NEW


def test_the_bottom_half_pointer_follows_the_top_halfs_length(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_flag_tiles_and_colors({0: ((255, 0, 0), (0, 0, 255))})
    writer.finalize()
    entry = _read(out, fixture.OFS_FLAG_TILE_PTRS, 4)
    half = len(fixture.konami_literal(_make_solid_4bpp_tile(0x0C) * 3))
    assert fixture.decode_p48000(entry[2:]) == fixture.OFS_FLAG_TILE_NEW + half


def test_an_unpatched_team_keeps_its_original_flag_pointer(rom, out):
    before = rom.read_bytes()
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_flag_tiles_and_colors({0: ((255, 0, 0), (0, 0, 255))})
    writer.finalize()
    offset = fixture.OFS_FLAG_TILE_PTRS + 4
    assert out.read_bytes()[offset : offset + 4] == before[offset : offset + 4]


def test_the_flag_palette_alternates_primary_and_alternate(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_flag_tiles_and_colors({0: ((255, 0, 0), (0, 0, 255))})
    writer.finalize()
    # Germany is position 0 of the first flag-colour range.
    data = _read(out, fixture.OFS_FLAG_COLORS_RANGE1, 8)
    red = _rgb_to_bgr555(255, 0, 0).to_bytes(2, "little")
    blue = _rgb_to_bgr555(0, 0, 255).to_bytes(2, "little")
    assert data == red + blue + red + blue


def test_scotland_takes_the_second_flag_colour_range(rom, out):
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_flag_tiles_and_colors({5: ((255, 0, 0), (0, 0, 255))})
    writer.finalize()
    assert _read(out, fixture.OFS_FLAG_COLORS_RANGE2, 2) == _rgb_to_bgr555(255, 0, 0).to_bytes(
        2, "little"
    )


def test_no_patched_team_still_writes_the_shared_flag_tiles(rom, out):
    """The tiles go out unconditionally; only the pointers and palettes are
    per-team. An empty mapping therefore still changes the image."""
    before = rom.read_bytes()
    writer = ISSRomWriter(str(rom), str(out))
    writer.write_flag_tiles_and_colors({})
    writer.finalize()
    assert out.read_bytes() != before
    assert out.read_bytes()[: fixture.OFS_FLAG_TILE_NEW] == before[: fixture.OFS_FLAG_TILE_NEW]


# -- write_team_name_texts --------------------------------------------------


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


# -- write_name_tiles: the machine-code patch -------------------------------


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


# -- write_team_descriptions ------------------------------------------------


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


# -- the handle -------------------------------------------------------------


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
