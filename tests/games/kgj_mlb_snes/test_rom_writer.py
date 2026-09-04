"""The ported KGJ MLB writer against synthetic images.

Every offset a test looks at is computed from `tests/fixtures/synthetic_kgj_rom`
rather than from `writer.reader`, so a test asserting that a field landed
somewhere is not using the writer's own arithmetic to decide where to look.

Two things this file exists to pin beyond the field layout:

  * `write_team_roster` does NOT touch the records it is given. Upstream set
    `roster_type` on them from the slot index, mutating a list the caller still
    held; `patcher.map_rosters` stamps it now.
  * `update_snes_checksum` is a real sum over the whole image, at offsets that
    shift by 512 for a headered file. The fixture's filler is pseudo-random
    precisely so that a routine which summed nothing, or summed from the wrong
    place, cannot produce the same number.
"""

import dataclasses

import pytest

from retro_roster_patcher.games.kgj_mlb_snes.models import (
    HAND_LEFT,
    HAND_SWITCH,
    PLAYERS_PER_TEAM,
    ROSTER_TYPE_BATTER,
    ROSTER_TYPE_RELIEVER,
    ROSTER_TYPE_STARTER,
    TEAM_COUNT,
    KGJBatterAppearance,
    KGJBatterAttributes,
    KGJPitcherAppearance,
    KGJPitcherAttributes,
    KGJPlayerRecord,
)
from retro_roster_patcher.games.kgj_mlb_snes.rom_writer import (
    KGJRomWriter,
    _encode_char,
    _encode_name,
    _encode_split_stat,
    _encode_stat_pair,
)
from tests.fixtures import synthetic_kgj_rom as fixture

#: Where team 0 player 0 begins in a default synthetic image, stated from the
#: fixture's own layout.
FIRST_TEAM_OFFSET = fixture.MARKER_OFFSET + len(fixture.FIRST_TEAM_MARKER)


def _offset(team, slot, *, with_header=False):
    base = fixture.player_offset(team, slot, first_team_offset=FIRST_TEAM_OFFSET)
    return base + (fixture.SMC_HEADER_SIZE if with_header else 0)


@pytest.fixture
def rom(tmp_path):
    return fixture.write_kgj_rom(tmp_path / "kgj.sfc")


@pytest.fixture
def headered_rom(tmp_path):
    return fixture.write_kgj_rom(tmp_path / "kgj.smc", with_header=True)


@pytest.fixture
def writer(rom, tmp_path):
    w = KGJRomWriter(str(rom), str(tmp_path / "out.sfc"))
    assert w.load() is True
    return w


def _batter(**overrides):
    """A batter whose every field differs from every default."""
    record = KGJPlayerRecord(
        first_initial="Q",
        last_name="GRIFFEY",
        position="RF",
        jersey_number=24,
        is_pitcher=False,
        bat_hand=HAND_LEFT,
        batter_attrs=KGJBatterAttributes(batting=9, power=8, speed=6, defense=4),
        batter_appearance=KGJBatterAppearance(
            skin=2, head=3, hair_color=5, body=6, legs_size=1, legs_stance=4, arms_stance=2
        ),
        batting_avg=323,
        home_runs=56,
        rbi=147,
        roster_type=ROSTER_TYPE_BATTER,
    )
    return dataclasses.replace(record, **overrides)


def _pitcher(**overrides):
    """A pitcher whose every field differs from every default."""
    record = KGJPlayerRecord(
        first_initial="R",
        last_name="McGWIRE",
        position="P",
        jersey_number=51,
        is_pitcher=True,
        bat_hand=HAND_SWITCH,
        batter_appearance=KGJBatterAppearance(
            skin=1, head=2, hair_color=3, body=4, legs_size=1, legs_stance=2, arms_stance=1
        ),
        pitcher_attrs=KGJPitcherAttributes(speed=10, control=7, fatigue=3),
        pitcher_appearance=KGJPitcherAppearance(
            skin=5, head=4, hair_color=3, body=2, throwing_style=1
        ),
        pitch_hand=1,
        wins=21,
        losses=6,
        era=289,
        saves=44,
        roster_type=ROSTER_TYPE_STARTER,
    )
    return dataclasses.replace(record, **overrides)


# -- character encoding ------------------------------------------------------


def test_a_letter_encodes_to_its_table_byte():
    assert _encode_char("K") == 0x15


def test_a_digit_encodes_to_its_table_byte():
    assert _encode_char("7") == 0x08


def test_a_space_encodes_to_zero():
    assert _encode_char(" ") == 0x00


def test_the_lone_lowercase_letter_encodes_to_its_own_byte():
    # `c` is the only lowercase letter the table names, so "McGWIRE" renders.
    assert _encode_char("c") == 0x36


def test_a_lowercase_letter_the_table_does_not_name_falls_back_to_its_capital():
    assert _encode_char("k") == _encode_char("K")


def test_an_accented_letter_encodes_to_a_space():
    # DEFECT, carried over: every accent in a modern MLB roster becomes a blank,
    # silently. `é`.upper() is `É`, which the table does not name either.
    assert _encode_char("é") == 0x00


def test_an_apostrophe_encodes_to_a_space():
    assert _encode_char("'") == 0x00


def test_a_name_shorter_than_the_field_is_padded_with_the_space_byte():
    assert _encode_name("KO", 8) == [0x15, 0x19, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]


def test_a_name_longer_than_the_field_is_truncated_without_an_ellipsis():
    assert _encode_name("YASTRZEMSKI", 8) == [_encode_char(ch) for ch in "YASTRZEM"]


def test_a_name_exactly_the_field_length_is_neither_padded_nor_cut():
    assert len(_encode_name("ABCDEFGH", 8)) == 8


# -- attribute packing -------------------------------------------------------


def test_a_rating_pair_stores_each_value_minus_one():
    # 1-10 becomes 0x0-0x9, high nibble first.
    assert _encode_stat_pair(10, 1) == 0x90


def test_a_rating_pair_puts_the_second_argument_in_the_low_nibble():
    assert _encode_stat_pair(1, 10) == 0x09


def test_a_rating_above_ten_clamps_to_nine():
    assert _encode_stat_pair(99, 5) == 0x94


def test_a_rating_below_one_clamps_to_zero():
    assert _encode_stat_pair(-7, 5) == 0x04


# -- the split stat ----------------------------------------------------------


def test_a_three_digit_stat_splits_into_a_byte_and_a_nibble():
    # 325 is 0x145: the low byte is 0x45 and the high nibble is 1. Plain binary,
    # not BCD -- BCD would be 0x325 -- which is why the function is not called
    # `_encode_bcd_stat` any more.
    assert _encode_split_stat(325) == (0x45, 0x1)


def test_a_stat_below_256_leaves_the_nibble_at_zero():
    assert _encode_split_stat(250) == (0xFA, 0x0)


def test_a_stat_above_999_clamps():
    assert _encode_split_stat(1500) == _encode_split_stat(999)


def test_the_clamp_ceiling_is_999_and_not_1023():
    assert _encode_split_stat(1500) == (0xE7, 0x3)


def test_a_negative_stat_floors_at_zero():
    assert _encode_split_stat(-40) == (0x00, 0x0)


# -- load --------------------------------------------------------------------


def test_load_answers_false_for_a_file_that_is_not_there(tmp_path):
    w = KGJRomWriter(str(tmp_path / "absent.sfc"), str(tmp_path / "out.sfc"))
    assert w.load() is False


def test_load_answers_false_for_an_image_with_no_marker(tmp_path):
    source = fixture.write_kgj_rom(tmp_path / "nomarker.sfc", place_marker=False)
    w = KGJRomWriter(str(source), str(tmp_path / "out.sfc"))
    assert w.load() is False


def test_load_takes_its_own_copy_of_the_image(writer):
    # The writer's internal reader keeps the unmodified image for its whole
    # lifetime; a test that read it back would assert nothing.
    writer.data[0] = (writer.data[0] + 1) % 256
    assert writer.data[0] != writer.reader.data[0]


def test_load_copies_every_byte(writer, rom):
    assert bytes(writer.data) == rom.read_bytes()


# -- write_player, batter ----------------------------------------------------


def _written_batter(writer, team=4, slot=6, **overrides):
    assert writer.write_player(team, slot, _batter(**overrides)) is True
    return fixture.decode_player_record(writer.data, _offset(team, slot))


def test_a_batters_first_initial_lands_at_byte_zero(writer):
    assert _written_batter(writer)["first_initial"] == "Q"


def test_a_batters_last_name_lands_in_the_eight_bytes_after_it(writer):
    assert _written_batter(writer)["last_name"] == "GRIFFEY"


def test_a_batters_position_lands_at_byte_nine(writer):
    assert _written_batter(writer)["position"] == "RF"


def test_a_position_the_table_does_not_name_falls_back_to_centre_field(writer):
    assert _written_batter(writer, position="ZZ")["position"] == "CF"


def test_a_batters_jersey_lands_at_byte_ten(writer):
    assert _written_batter(writer)["jersey"] == 24


def test_a_jersey_above_99_clamps(writer):
    assert _written_batter(writer, jersey_number=250)["jersey"] == 99


def test_a_negative_jersey_floors_at_zero(writer):
    assert _written_batter(writer, jersey_number=-3)["jersey"] == 0


def test_a_batters_batting_lands_in_the_high_nibble_of_byte_0x0b(writer):
    assert _written_batter(writer)["attr_high"] == 9


def test_a_batters_power_lands_in_the_low_nibble_of_byte_0x0b(writer):
    assert _written_batter(writer)["attr_low"] == 8


def test_a_batters_speed_lands_in_the_high_nibble_of_byte_0x0c(writer):
    assert _written_batter(writer)["attr2_high"] == 6


def test_a_batters_defense_lands_in_the_low_nibble_of_byte_0x0c(writer):
    assert _written_batter(writer)["attr2_low"] == 4


def test_a_batters_hand_lands_at_byte_0x0d(writer):
    assert _written_batter(writer)["bat_hand"] == HAND_LEFT


def test_a_batters_skin_and_head_share_byte_0x0e(writer):
    assert _written_batter(writer)["skin_head"] == 0x23


def test_a_batters_hair_and_body_share_byte_0x0f(writer):
    assert _written_batter(writer)["hair_body"] == 0x56


def test_a_batters_leg_fields_share_byte_0x10(writer):
    assert _written_batter(writer)["legs"] == 0x14


def test_the_arms_nibble_leaves_the_high_half_of_byte_0x11_alone(writer):
    # The fixture puts a non-zero value in that half, so a write that clobbered
    # the whole byte is visible. This is the one field the writer merges.
    before = writer.data[_offset(4, 6) + 0x11] & 0xF0
    assert _written_batter(writer)["arms"] == before | 0x2


def test_a_batter_zeroes_the_three_pitcher_appearance_bytes(writer):
    written = _written_batter(writer)
    assert (
        written["pitch_hand_skin"],
        written["pitch_head_hair"],
        written["pitch_body_style"],
    ) == (0, 0, 0)


def test_a_batting_average_above_255_reaches_the_nibble_it_shares_with_the_roster_type(writer):
    assert _written_batter(writer)["batting_avg"] == 323


def test_a_batters_roster_type_lands_in_the_high_nibble_of_the_same_byte(writer):
    assert _written_batter(writer)["roster_type"] == ROSTER_TYPE_BATTER >> 4


def test_a_batters_home_runs_land_at_byte_0x1a(writer):
    assert _written_batter(writer)["home_runs"] == 56


def test_home_runs_above_255_clamp(writer):
    assert _written_batter(writer, home_runs=400)["home_runs"] == 255


def test_a_batter_zeroes_byte_0x1b(writer):
    assert _written_batter(writer)["always_zero"] == 0


def test_a_batters_rbi_lands_at_byte_0x1c(writer):
    assert _written_batter(writer)["rbi"] == 147


def test_a_batter_writes_the_batter_flag_at_byte_0x1d(writer):
    assert _written_batter(writer)["kind_flag"] == 0x10


def test_a_batter_zeroes_byte_0x1e(writer):
    assert _written_batter(writer)["stat_fourth"] == 0


def test_a_batter_leaves_the_bytes_no_field_owns_alone(writer):
    before = {offset: writer.data[_offset(4, 6) + offset] for offset in fixture.UNTOUCHED_OFFSETS}
    assert _written_batter(writer)["untouched"] == before


# -- write_player, pitcher ---------------------------------------------------


def _written_pitcher(writer, team=4, slot=17, **overrides):
    assert writer.write_player(team, slot, _pitcher(**overrides)) is True
    return fixture.decode_player_record(writer.data, _offset(team, slot))


def test_a_pitchers_name_uses_the_lowercase_c(writer):
    assert _written_pitcher(writer)["last_name"] == "McGWIRE"


def test_a_pitchers_speed_lands_in_the_high_nibble_of_byte_0x0b(writer):
    assert _written_pitcher(writer)["attr_high"] == 10


def test_a_pitchers_control_lands_in_the_low_nibble_of_byte_0x0b(writer):
    assert _written_pitcher(writer)["attr_low"] == 7


def test_a_pitchers_fatigue_takes_the_whole_of_byte_0x0c(writer):
    # The high nibble is zeroed rather than merged, unlike a batter's, so a
    # pitcher written over a batter does not keep the batter's speed.
    _written_pitcher(writer)
    assert writer.data[_offset(4, 17) + 0x0C] == 0x02


def test_a_pitcher_still_carries_a_batting_hand(writer):
    assert _written_pitcher(writer)["bat_hand"] == HAND_SWITCH


def test_a_pitcher_writes_the_batter_appearance_too(writer):
    # Pitchers bat, so bytes 0x0E-0x11 come from `batter_appearance` and not
    # from `pitcher_appearance`.
    assert _written_pitcher(writer)["skin_head"] == 0x12


def test_a_pitchers_throwing_hand_and_skin_share_byte_0x15(writer):
    assert _written_pitcher(writer)["pitch_hand_skin"] == 0x15


def test_a_pitchers_head_and_hair_share_byte_0x16(writer):
    assert _written_pitcher(writer)["pitch_head_hair"] == 0x43


def test_a_pitchers_body_and_throwing_style_share_byte_0x17(writer):
    assert _written_pitcher(writer)["pitch_body_style"] == 0x21


def test_a_pitchers_wins_land_at_byte_0x18(writer):
    assert _written_pitcher(writer)["wins"] == 21


def test_a_pitchers_roster_type_takes_the_whole_high_nibble_of_byte_0x19(writer):
    _written_pitcher(writer)
    assert writer.data[_offset(4, 17) + 0x19] == ROSTER_TYPE_STARTER


def test_a_pitchers_losses_land_at_byte_0x1a(writer):
    assert _written_pitcher(writer)["losses"] == 6


def test_a_pitcher_zeroes_byte_0x1b(writer):
    assert _written_pitcher(writer)["always_zero"] == 0


def test_an_era_above_255_reaches_the_nibble_it_shares_with_the_pitcher_flag(writer):
    assert _written_pitcher(writer)["era"] == 289


def test_a_pitcher_writes_the_pitcher_flag_in_the_high_nibble_of_byte_0x1d(writer):
    _written_pitcher(writer)
    assert writer.data[_offset(4, 17) + 0x1D] >> 4 == 0x2


def test_a_pitchers_saves_land_at_byte_0x1e(writer):
    assert _written_pitcher(writer)["stat_fourth"] == 44


def test_a_pitcher_leaves_the_bytes_no_field_owns_alone(writer):
    before = {offset: writer.data[_offset(4, 17) + offset] for offset in fixture.UNTOUCHED_OFFSETS}
    assert _written_pitcher(writer)["untouched"] == before


# -- write_player, refusals --------------------------------------------------


def test_a_team_index_past_the_league_is_refused(writer):
    assert writer.write_player(TEAM_COUNT, 0, _batter()) is False


def test_a_slot_past_the_roster_is_refused(writer):
    assert writer.write_player(0, PLAYERS_PER_TEAM, _batter()) is False


def test_a_refused_write_leaves_the_image_alone(writer):
    before = bytes(writer.data)
    writer.write_player(TEAM_COUNT, 0, _batter())
    assert bytes(writer.data) == before


def test_a_record_that_would_run_off_the_end_is_refused(tmp_path):
    source = fixture.write_kgj_rom(
        tmp_path / "tight.sfc",
        marker_offset=fixture.ROM_SIZE - fixture.TEAM_DATA_SPAN + 0x400,
    )
    w = KGJRomWriter(str(source), str(tmp_path / "out.sfc"))
    assert w.load() is True
    assert w.write_player(27, 24, _batter()) is False


def test_write_player_before_load_is_refused(tmp_path, rom):
    w = KGJRomWriter(str(rom), str(tmp_path / "out.sfc"))
    assert w.write_player(0, 0, _batter()) is False


# -- write_team_roster -------------------------------------------------------


def test_a_full_roster_reports_every_record_written(writer):
    records = [_batter() for _ in range(PLAYERS_PER_TEAM)]
    assert writer.write_team_roster(2, records) == PLAYERS_PER_TEAM


def test_a_short_roster_reports_only_what_it_held(writer):
    assert writer.write_team_roster(2, [_batter(), _pitcher()]) == 2


def test_an_over_long_roster_is_cut_at_the_roster_size(writer):
    records = [_batter() for _ in range(PLAYERS_PER_TEAM + 9)]
    assert writer.write_team_roster(2, records) == PLAYERS_PER_TEAM


def test_an_over_long_roster_does_not_reach_the_next_team(writer):
    # Slot 25 would be team 3's slot 0 if the cut were missing.
    before = writer.data[_offset(3, 0)]
    writer.write_team_roster(2, [_batter(first_initial="Z") for _ in range(30)])
    assert writer.data[_offset(3, 0)] == before


def test_an_empty_roster_writes_nothing(writer):
    assert writer.write_team_roster(2, []) == 0


def test_a_team_index_past_the_league_reports_the_error_return(writer):
    assert writer.write_team_roster(TEAM_COUNT, [_batter()]) == -1


def test_write_team_roster_before_load_reports_the_error_return(tmp_path, rom):
    w = KGJRomWriter(str(rom), str(tmp_path / "out.sfc"))
    assert w.write_team_roster(0, [_batter()]) == -1


def test_the_writer_does_not_stamp_the_roster_type_it_is_given(writer):
    # DELIBERATE DIVERGENCE. Upstream set this field from the slot index, on the
    # caller's own object. Slot 20 is a reliever slot, so upstream would have
    # overwritten this record's 0x30 with 0x00.
    record = _batter(roster_type=ROSTER_TYPE_BATTER)
    writer.write_team_roster(2, [_batter() for _ in range(20)] + [record])
    assert record.roster_type == ROSTER_TYPE_BATTER


def test_the_writer_writes_the_roster_type_it_was_given_and_not_the_slots(writer):
    record = _batter(roster_type=ROSTER_TYPE_BATTER)
    writer.write_team_roster(2, [_batter() for _ in range(20)] + [record])
    written = fixture.decode_player_record(writer.data, _offset(2, 20))
    assert written["roster_type"] == ROSTER_TYPE_BATTER >> 4


def test_a_reliever_slot_can_still_hold_a_reliever_roster_type(writer):
    # The value 0 is what a reliever carries, and the previous test's 0x30 would
    # also pass an implementation that always wrote 0x30. This is its opposite.
    record = _pitcher(roster_type=ROSTER_TYPE_RELIEVER)
    writer.write_team_roster(2, [_batter() for _ in range(20)] + [record])
    written = fixture.decode_player_record(writer.data, _offset(2, 20))
    assert written["roster_type"] == ROSTER_TYPE_RELIEVER


def test_records_land_in_the_slot_order_they_were_given(writer):
    records = [_batter(jersey_number=index + 1) for index in range(PLAYERS_PER_TEAM)]
    writer.write_team_roster(2, records)
    jerseys = [
        fixture.decode_player_record(writer.data, _offset(2, slot))["jersey"]
        for slot in range(PLAYERS_PER_TEAM)
    ]
    assert jerseys == list(range(1, PLAYERS_PER_TEAM + 1))


def test_writing_one_team_leaves_the_next_teams_records_alone(writer):
    before = fixture.decode_player_record(writer.data, _offset(3, 0))["raw"]
    writer.write_team_roster(2, [_batter() for _ in range(PLAYERS_PER_TEAM)])
    assert fixture.decode_player_record(writer.data, _offset(3, 0))["raw"] == before


def test_writing_an_al_team_leaves_the_nl_half_alone(writer):
    before = fixture.decode_player_record(writer.data, _offset(14, 0))["raw"]
    writer.write_team_roster(13, [_batter() for _ in range(PLAYERS_PER_TEAM)])
    assert fixture.decode_player_record(writer.data, _offset(14, 0))["raw"] == before


# -- the SNES checksum -------------------------------------------------------


def _checksum_words(data, *, with_header=False):
    shift = fixture.SMC_HEADER_SIZE if with_header else 0
    cksum = fixture.CHECKSUM_OFFSET + shift
    comp = fixture.COMPLEMENT_OFFSET + shift
    return (
        data[cksum] | (data[cksum + 1] << 8),
        data[comp] | (data[comp + 1] << 8),
    )


def _expected_checksum(data, *, with_header=False):
    """Recompute the checksum independently of the code under test.

    Zero the checksum word, fill the complement word with 0xFF, sum every byte,
    take the low 16 bits. That is the SNES convention the writer's docstring
    describes, restated here rather than imported.
    """
    shift = fixture.SMC_HEADER_SIZE if with_header else 0
    scratch = bytearray(data)
    scratch[fixture.CHECKSUM_OFFSET + shift] = 0x00
    scratch[fixture.CHECKSUM_OFFSET + shift + 1] = 0x00
    scratch[fixture.COMPLEMENT_OFFSET + shift] = 0xFF
    scratch[fixture.COMPLEMENT_OFFSET + shift + 1] = 0xFF
    return sum(scratch) & 0xFFFF


def test_the_checksum_is_the_sum_of_every_byte(writer):
    writer.write_team_roster(0, [_batter() for _ in range(PLAYERS_PER_TEAM)])
    expected = _expected_checksum(writer.data)
    writer.update_snes_checksum()
    assert _checksum_words(writer.data)[0] == expected


def test_the_checksum_is_not_the_value_the_image_arrived_with(writer):
    # The fixture's filler is deliberately wrong, so "recomputed" and "left
    # alone" are different answers. Without this the previous test would pass
    # against a routine that wrote nothing, if the filler happened to be right.
    before = _checksum_words(writer.data)[0]
    writer.update_snes_checksum()
    assert _checksum_words(writer.data)[0] != before


def test_the_complement_is_the_checksums(writer):
    writer.update_snes_checksum()
    checksum, complement = _checksum_words(writer.data)
    assert checksum + complement == 0xFFFF


def test_the_checksum_changes_when_a_record_changes(writer):
    writer.update_snes_checksum()
    first = _checksum_words(writer.data)[0]
    writer.write_team_roster(0, [_batter(jersey_number=99) for _ in range(PLAYERS_PER_TEAM)])
    writer.update_snes_checksum()
    assert _checksum_words(writer.data)[0] != first


def test_recomputing_twice_gives_the_same_answer(writer):
    writer.update_snes_checksum()
    first = _checksum_words(writer.data)
    writer.update_snes_checksum()
    assert _checksum_words(writer.data) == first


def test_a_headered_image_writes_the_checksum_512_bytes_further_in(headered_rom, tmp_path):
    w = KGJRomWriter(str(headered_rom), str(tmp_path / "out.smc"))
    assert w.load() is True
    expected = _expected_checksum(w.data, with_header=True)
    w.update_snes_checksum()
    assert _checksum_words(w.data, with_header=True)[0] == expected


def test_a_headered_image_leaves_the_headerless_offsets_alone(headered_rom, tmp_path):
    # 0x7FDE in a headered file is ordinary ROM data. A writer that forgot the
    # shift would corrupt it, and the previous test alone would not notice.
    w = KGJRomWriter(str(headered_rom), str(tmp_path / "out.smc"))
    w.load()
    before = bytes(w.data[fixture.CHECKSUM_OFFSET : fixture.CHECKSUM_OFFSET + 2])
    w.update_snes_checksum()
    assert bytes(w.data[fixture.CHECKSUM_OFFSET : fixture.CHECKSUM_OFFSET + 2]) == before


def test_a_headered_and_a_headerless_image_get_different_checksums(rom, headered_rom, tmp_path):
    # The header is 512 more bytes in the sum, so the two must differ; a routine
    # that skipped the header would give them the same one.
    plain = KGJRomWriter(str(rom), str(tmp_path / "a.sfc"))
    plain.load()
    plain.update_snes_checksum()
    headered = KGJRomWriter(str(headered_rom), str(tmp_path / "b.smc"))
    headered.load()
    headered.update_snes_checksum()
    assert _checksum_words(plain.data)[0] != _checksum_words(headered.data, with_header=True)[0]


def test_update_snes_checksum_before_load_does_nothing(tmp_path, rom):
    w = KGJRomWriter(str(rom), str(tmp_path / "out.sfc"))
    w.update_snes_checksum()
    assert w.data is None


# -- finalize ----------------------------------------------------------------


def test_finalize_writes_the_image_it_holds(writer, tmp_path):
    writer.write_team_roster(0, [_batter() for _ in range(PLAYERS_PER_TEAM)])
    assert writer.finalize() is True


def test_the_written_file_is_byte_for_byte_what_the_writer_held(writer, tmp_path):
    writer.write_team_roster(0, [_batter() for _ in range(PLAYERS_PER_TEAM)])
    expected = bytes(writer.data)
    writer.finalize()
    assert (tmp_path / "out.sfc").read_bytes() == expected


def test_finalize_creates_the_output_directory(rom, tmp_path):
    out = tmp_path / "deep" / "deeper" / "out.sfc"
    w = KGJRomWriter(str(rom), str(out))
    w.load()
    assert w.finalize() is True


def test_finalize_before_load_answers_false(rom, tmp_path):
    w = KGJRomWriter(str(rom), str(tmp_path / "out.sfc"))
    assert w.finalize() is False


def test_finalize_answers_false_when_the_output_cannot_be_written(rom, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a directory")
    w = KGJRomWriter(str(rom), str(blocker / "out.sfc"))
    w.load()
    assert w.finalize() is False


def test_finalize_does_not_recompute_the_checksum(writer, tmp_path):
    # The orchestrator calls `update_snes_checksum` itself. This is the opposite
    # split from the NBA Live 95 port, and both are deliberate.
    before = _checksum_words(writer.data)
    writer.finalize()
    assert _checksum_words(bytearray((tmp_path / "out.sfc").read_bytes())) == before
