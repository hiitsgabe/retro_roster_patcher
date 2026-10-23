"""Writer coverage against `tests/fixtures/synthetic_snes_rom.py`.

The writer rebuilds every team block in bank $9C's room for them (the blocks
themselves and the window's 0xFF padding) and repoints the table, so tests read
the result back through the table with the reader, never at the fixture's own
offsets once a roster has been written. Stat bytes are decoded by
`synthetic_snes_rom.decode_player_stats`, transcribed from the writer's own
comments rather than from its code.
"""

import struct

import pytest

from retro_roster_patcher.core.errors import RomError
from retro_roster_patcher.games.nhl94_snes.models import (
    TEAM_COUNT,
    NHL94GoalieAttributes,
    NHL94PlayerAttributes,
    NHL94PlayerRecord,
)
from retro_roster_patcher.games.nhl94_snes.rom_reader import NHL94SNESRomReader
from retro_roster_patcher.games.nhl94_snes.rom_writer import (
    HEADER_GOALIE_SLOTS,
    LINE_COUNT,
    LINE_SLOTS,
    NHL94SNESRomWriter,
    abbreviate,
    encode_nibble,
    encode_records,
    encode_weight_nibble,
    game_overall,
    header_counts,
    player_stats,
)
from tests.fixtures import synthetic_snes_rom as fixture

# Both read from the fixture, which transcribes the team block layout
# independently, and never from `rom_writer`. Importing the writer's own
# `LINE_ASSIGN_OFFSET` to say where the writer put the line table is a
# tautology: move the constant and the assertion moves with it.
PLAYER_COUNT_BYTE = fixture.PLAYER_COUNT_OFFSET
LINE_TABLE_BYTE = fixture.LINE_ASSIGN_OFFSET


def _writer(tmp_path, **kwargs):
    source = fixture.write_nhl94_snes_rom(tmp_path / "in.sfc", **kwargs)
    writer = NHL94SNESRomWriter(str(source), str(tmp_path / "out.sfc"))
    assert writer.load() is True
    return writer


def _finalized_reader(writer):
    reader = NHL94SNESRomReader(writer.output_path)
    assert reader.load() is True
    return reader


def _records(count, *, name="Fourteen Char", goalies=2):
    """`count` records with distinct names, jerseys and attribute values.

    Every field varies with the index, so a writer that wrote one record `count`
    times, or wrote them in reverse, fails on a value rather than on a length.

    Jerseys step by four rather than by one, so the run reaches 17. Below 16 a
    BCD byte and a plain integer decode to the same number -- 10 is 0x10 packed
    and 0x0A plain, and `decode_player_stats` reads both back as 10 -- so a
    consecutive 1..n run cannot tell the writer's BCD from no encoding at all.
    """
    made = []
    for i in range(count):
        made.append(
            NHL94PlayerRecord(
                name=f"{name}{i:02d}",
                jersey_number=i * 4 + 1,
                weight_class=i % 15,
                handedness=i % 2,
                is_goalie=i < goalies,
                attributes=NHL94PlayerAttributes(
                    speed=i % 7,
                    agility=(i + 1) % 7,
                    shot_power=(i + 2) % 7,
                    shot_accuracy=(i + 3) % 7,
                    stick_handling=(i + 4) % 7,
                    pass_accuracy=(i + 5) % 7,
                    off_awareness=(i + 6) % 7,
                    def_awareness=i % 7,
                    checking=(i + 1) % 7,
                    endurance=(i + 2) % 7,
                    roughness=(i + 3) % 7,
                    aggression=(i + 4) % 7,
                ),
            )
        )
    return made


def test_two_nibbles_pack_high_first():
    assert encode_nibble(6, 1) == 0x61


@pytest.mark.parametrize(("high", "low", "expected"), [(9, 9, 0x66), (-1, -3, 0x00)])
def test_a_stat_nibble_is_clamped_to_the_zero_to_six_scale(high, low, expected):
    assert encode_nibble(high, low) == expected


def test_a_weight_nibble_uses_the_full_four_bit_range_minus_one():
    assert encode_weight_nibble(14, 6) == 0xE6


def test_a_weight_above_fourteen_is_clamped_to_fourteen():
    # 15 is representable in four bits and is still refused, which is what
    # distinguishes this from `encode_nibble`'s clamp.
    assert encode_weight_nibble(15, 0) == 0xE0


def test_a_weight_nibble_still_clamps_its_stat_half():
    assert encode_weight_nibble(0, 9) == 0x06


def test_a_writer_over_a_missing_file_fails_to_load(tmp_path):
    writer = NHL94SNESRomWriter(str(tmp_path / "absent.sfc"), str(tmp_path / "out.sfc"))
    assert writer.load() is False
    assert writer.data is None


def test_loading_shares_one_buffer_with_the_reader(tmp_path):
    """Once `write_rosters` moves a block, the reader's pointers must be the
    new ones for whatever writes after it (headers, portraits, identity)."""
    writer = _writer(tmp_path)
    assert writer.data is writer.reader.data


def _block_base(data, slot, header=0):
    """A block's file offset, read from the table the way the game reads it."""
    entry = header + fixture.team_pointer_offset(slot)
    return header + fixture.BANK_WINDOW_START + ((data[entry] | data[entry + 1] << 8) & 0x7FFF)


def _strings(data, slot, header=0):
    """The block's four strings, found through bytes 4-5."""
    base = _block_base(data, slot, header)
    at = base + (data[base + fixture.STRINGS_OFFSET] | data[base + fixture.STRINGS_OFFSET + 1] << 8)
    found = []
    for _ in range(4):
        length = data[at] | data[at + 1] << 8
        found.append(bytes(data[at + 2 : at + length]).decode("ascii"))
        at += length
    return found


def test_a_roster_is_written_and_reads_back_in_order(tmp_path):
    writer = _writer(tmp_path)
    assert writer.write_rosters({5: _records(10)}) == {5}
    assert writer.finalize() is True
    names, _ = _finalized_reader(writer).read_team_roster(5)
    assert names == [f"Fourteen Char{i:02d}" for i in range(10)]


def test_the_written_stat_bytes_decode_to_the_records_attributes(tmp_path):
    writer = _writer(tmp_path)
    records = _records(10)
    writer.write_rosters({5: records})
    assert writer.finalize() is True
    _, stats = _finalized_reader(writer).read_team_roster(5)
    decoded = fixture.decode_player_stats(stats[7])
    source = records[7]
    attributes = source.attributes
    assert isinstance(attributes, NHL94PlayerAttributes)
    assert decoded["jersey_number"] == source.jersey_number
    assert decoded["weight_class"] == source.weight_class
    assert decoded["handedness"] == source.handedness
    assert decoded["speed"] == attributes.speed
    assert decoded["agility"] == attributes.agility
    assert decoded["off_awareness"] == attributes.off_awareness
    assert decoded["def_awareness"] == attributes.def_awareness
    assert decoded["shot_power"] == attributes.shot_power
    assert decoded["shot_accuracy"] == attributes.shot_accuracy
    assert decoded["stick_handling"] == attributes.stick_handling
    assert decoded["checking"] == attributes.checking
    assert decoded["endurance"] == attributes.endurance
    assert decoded["roughness"] == attributes.roughness
    assert decoded["pass_accuracy"] == attributes.pass_accuracy
    assert decoded["aggression"] == attributes.aggression


def test_a_goalie_is_written_in_the_games_goalie_fields():
    """Agility, speed, awareness (twice, as every cartridge goalie has it), puck
    control, the glove hand, zeros where no goalie is read, then the four save
    zones: stick right/left, glove right/left."""
    goalie = NHL94PlayerRecord(
        name="Keeper",
        jersey_number=35,
        weight_class=6,
        handedness=1,
        is_goalie=True,
        attributes=NHL94GoalieAttributes(
            agility=6,
            speed=4,
            def_awareness=5,
            puck_control=3,
            stick_right=2,
            stick_left=1,
            glove_right=4,
            glove_left=6,
        ),
    )
    assert player_stats(goalie) == bytes([0x35, 0x66, 0x45, 0x53, 0x01, 0x00, 0x21, 0x46])


def test_every_records_stat_bytes_are_its_own(tmp_path):
    """Rules out a writer that wrote one record's stats into every slot."""
    writer = _writer(tmp_path)
    records = _records(10)
    writer.write_rosters({5: records})
    assert writer.finalize() is True
    _, stats = _finalized_reader(writer).read_team_roster(5)
    jerseys = [fixture.decode_player_stats(block)["jersey_number"] for block in stats]
    assert jerseys == [record.jersey_number for record in records]


def test_a_record_is_a_little_endian_length_the_name_and_eight_stat_bytes():
    record = NHL94PlayerRecord(name="Ab")
    encoded = encode_records([record])
    assert encoded == b"\x04\x00Ab" + player_stats(record) + b"\x02\x00"


def test_an_empty_name_is_written_as_a_length_word_the_reader_reads_as_the_end():
    # Upstream's behaviour, kept: the mapper never makes an empty name.
    assert encode_records([NHL94PlayerRecord(name="")])[:2] == b"\x02\x00"


def test_a_non_ascii_name_is_replaced_rather_than_raising():
    # The mapper folds names to ASCII first; this is the writer's own guard.
    assert encode_records([NHL94PlayerRecord(name="é")])[2:3] == b"?"


def test_a_full_length_name_is_written_whole_when_the_old_block_was_smaller(tmp_path):
    """The slot's 1993 block held ten 8-character names; twenty-five of 19 move
    the block to room that fits them, and the table follows it."""
    writer = _writer(tmp_path, players_per_team=10)
    data = writer.data
    assert data is not None
    before = _block_base(data, 5)
    long_names = _records(25, name="Nineteen Characte")
    writer.write_rosters({5: long_names})
    assert writer.finalize() is True
    names, _ = _finalized_reader(writer).read_team_roster(5)
    assert names == [record.name for record in long_names]
    assert len(names[0]) == 19
    assert _block_base(data, 5) != before


def test_the_table_holds_the_games_rom_address(tmp_path):
    # $9C:8000 and up: bit 15 set, as the cartridge stores it.
    writer = _writer(tmp_path)
    writer.write_rosters({5: _records(10)})
    data = writer.data
    assert data is not None
    entry = fixture.team_pointer_offset(5)
    assert data[entry + 1] & 0x80


def test_every_other_team_keeps_its_roster_header_and_strings(tmp_path):
    writer = _writer(tmp_path)
    data = writer.data
    assert data is not None
    reader = writer.reader
    others = [slot for slot in range(TEAM_COUNT) if slot != 5]
    before = {
        slot: (
            reader.read_team_roster(slot),
            bytes(data[_block_base(data, slot) : _block_base(data, slot) + 85]),
            _strings(data, slot),
        )
        for slot in others
    }
    writer.write_rosters({5: _records(25, name="Nineteen Characte")})
    after = {
        slot: (
            reader.read_team_roster(slot),
            bytes(data[_block_base(data, slot) : _block_base(data, slot) + 85]),
            _strings(data, slot),
        )
        for slot in others
    }
    # Bytes 4-5 move with the block's own layout; everything else is kept.
    strip = [(roster, head[:4] + head[6:], strings) for roster, head, strings in before.values()]
    assert [(r, h[:4] + h[6:], s) for r, h, s in after.values()] == strip


def test_the_strings_move_with_their_block(tmp_path):
    writer = _writer(tmp_path, players_per_team=10)
    data = writer.data
    assert data is not None
    writer.write_rosters({5: _records(25, name="Nineteen Characte")})
    assert _strings(data, 5) == ["Dallas", "DAL", fixture.nickname(5), fixture.arena(5)]
    assert writer.finalize() is True
    assert _finalized_reader(writer).get_info().team_slots[5].current_name == "Dallas"


def test_the_room_no_block_uses_is_left_as_0xff_padding(tmp_path):
    writer = _writer(tmp_path)
    data = writer.data
    assert data is not None
    writer.write_rosters({slot: _records(12) for slot in range(TEAM_COUNT)})
    used = set()
    for slot in range(TEAM_COUNT):
        base = _block_base(data, slot)
        strings = _strings(data, slot)
        end = base + (data[base + 4] | data[base + 5] << 8) + sum(2 + len(s) for s in strings)
        used.update(range(base, end))
    seeds = range(fixture.SEED_TABLES, fixture.SEED_TABLES + fixture.SEED_TABLES_SIZE)
    free = [
        i
        for i in range(
            fixture.POINTER_TABLE_END, fixture.BANK_WINDOW_START + fixture.BANK_WINDOW_SIZE
        )
        if i not in used and i not in seeds
    ]
    assert free
    assert all(data[i] == 0xFF for i in free)
    # The tables between the blocks and the padding are no block's: untouched.
    assert bytes(data[seeds.start : seeds.stop]) == bytes(len(seeds))


def test_full_names_reach_the_padding_that_ends_the_bank(tmp_path):
    """23 names of 19 characters in every slot need 22 052 bytes; the blocks and
    the 0xFF between them hold 21 673, so some block goes past the seed tables
    into the padding that ends the bank, and no name is shortened."""
    writer = _writer(tmp_path)
    data = writer.data
    assert data is not None
    rosters = {slot: _records(23, name="Nineteen Characte") for slot in range(TEAM_COUNT)}
    assert writer.write_rosters(rosters) == set(range(TEAM_COUNT))
    tail = fixture.SEED_TABLES + fixture.SEED_TABLES_SIZE
    assert any(_block_base(data, slot) >= tail for slot in range(TEAM_COUNT))
    assert writer.finalize() is True
    reader = _finalized_reader(writer)
    for slot in range(TEAM_COUNT):
        assert reader.read_team_roster(slot)[0] == [r.name for r in rosters[slot]]


def test_strings_before_their_block_are_found_through_the_16_bit_wrap(tmp_path):
    """Bytes 4-5 are added to the block's address in 16 bits, as the game adds
    them: 0xFF60 reaches 0xA0 bytes before the block."""
    writer = _writer(tmp_path)
    data = writer.data
    assert data is not None
    base = fixture.team_base(0)
    strings = _strings(data, 0)
    moved = b"".join(struct.pack("<H", len(t) + 2) + t.encode() for t in strings)
    data[base - 0xA0 : base - 0xA0 + len(moved)] = moved
    struct.pack_into("<H", data, base + fixture.STRINGS_OFFSET, 0xFF60)
    assert writer.reader.get_info().team_slots[0].current_name == "Anaheim"
    assert writer.write_rosters({0: _records(10)}) == {0}
    assert _strings(data, 0) == strings


def test_a_patched_image_has_the_same_room_again(tmp_path):
    """Patching the output reuses the padding the first patch left: a roster
    that fit once fits again, at full length."""
    writer = _writer(tmp_path)
    rosters = {slot: _records(22, name="Nineteen Characte") for slot in range(TEAM_COUNT)}
    writer.write_rosters(rosters)
    assert writer.finalize() is True
    again = NHL94SNESRomWriter(writer.output_path, str(tmp_path / "again.sfc"))
    assert again.load() is True
    again.write_rosters(rosters)
    assert again.finalize() is True
    reader = NHL94SNESRomReader(str(tmp_path / "again.sfc"))
    assert reader.load() is True
    for slot in range(TEAM_COUNT):
        assert reader.read_team_roster(slot)[0] == [r.name for r in rosters[slot]]


def test_when_the_bank_is_full_the_longest_names_lose_their_first_names(tmp_path):
    writer = _writer(tmp_path)
    rosters = {
        slot: _records(25, name="Firstname Lastnam") + _records(3, name="Al Bo", goalies=0)
        for slot in range(TEAM_COUNT)
    }
    writer.write_rosters(rosters)
    assert writer.finalize() is True
    reader = _finalized_reader(writer)
    written = [name for slot in range(TEAM_COUNT) for name in reader.read_team_roster(slot)[0]]
    wanted = [r.name for slot in range(TEAM_COUNT) for r in rosters[slot]]
    assert len(written) == len(wanted)
    # Some long names lost their first names to an initial; no surname was cut,
    # and the short names (nothing to gain) were left alone.
    shortened = [w for w, n in zip(written, wanted, strict=True) if w != n]
    assert shortened
    assert all(w == abbreviate(n) for w, n in zip(written, wanted, strict=True) if w != n)
    assert all(w.startswith("F. Lastnam") for w in shortened)


def test_the_longest_names_are_the_first_to_lose_their_first_names(tmp_path):
    """Four 19-character names and 22 of 17 in every slot overflow the bank by
    about 560 bytes: some 19s become 'L. Abc00', and not one 17."""
    writer = _writer(tmp_path)
    rosters = {
        slot: _records(4, name="Longfirstname Abc") + _records(22, name="Firstname Lastn")
        for slot in range(TEAM_COUNT)
    }
    writer.write_rosters(rosters)
    assert writer.finalize() is True
    reader = _finalized_reader(writer)
    written = [name for slot in range(TEAM_COUNT) for name in reader.read_team_roster(slot)[0]]
    wanted = [r.name for slot in range(TEAM_COUNT) for r in rosters[slot]]
    shortened = [(w, n) for w, n in zip(written, wanted, strict=True) if w != n]
    assert shortened
    assert all(len(n) == 19 and w == abbreviate(n) for w, n in shortened)


def test_names_that_do_not_fit_even_abbreviated_are_refused_and_nothing_is_written(tmp_path):
    writer = _writer(tmp_path)
    data = writer.data
    assert data is not None
    before = bytes(data)
    rosters = {slot: _records(30, name="Onewordnameonly") for slot in range(TEAM_COUNT)}
    with pytest.raises(RomError, match="bank \\$9C"):
        writer.write_rosters(rosters)
    assert bytes(data) == before


def test_a_block_that_does_not_parse_stays_where_it_is_and_is_not_written(tmp_path):
    writer = _writer(tmp_path)
    data = writer.data
    assert data is not None
    base = fixture.team_base(13)
    data[base] = 74  # a header too short for the line table
    block = bytes(data[base : base + fixture.TEAM_BLOCK_STRIDE])
    assert writer.write_rosters({5: _records(10), 13: _records(10)}) == {5}
    assert bytes(data[base : base + fixture.TEAM_BLOCK_STRIDE]) == block


def test_no_rosters_change_nothing(tmp_path):
    writer = _writer(tmp_path)
    before = bytes(writer.data or b"")
    assert writer.write_rosters({}) == set()
    assert bytes(writer.data or b"") == before


def test_a_copier_headered_image_is_rebuilt_512_bytes_in(tmp_path):
    writer = _writer(tmp_path, with_smc_header=True)
    writer.write_rosters({5: _records(25, name="Nineteen Characte")})
    data = writer.data
    assert data is not None
    assert _strings(data, 5, header=fixture.SMC_HEADER_SIZE)[0] == "Dallas"
    assert writer.finalize() is True
    names, _ = _finalized_reader(writer).read_team_roster(5)
    assert len(names) == 25


@pytest.mark.parametrize(
    ("stats", "goalie", "overall"),
    [
        (bytes([0x01, 0x03, 0x33, 0x33, 0x30, 0x33, 0x33, 0x33]), False, 60),  # 20 x 3
        (bytes([0x01, 0x02, 0x22, 0x22, 0x20, 0x22, 0x22, 0x22]), False, 45),  # 40, squashed
        (bytes([0x01, 0x06, 0x66, 0x66, 0x60, 0x66, 0x66, 0x66]), False, 100),  # capped
        (bytes([0x01, 0x04, 0x44, 0x44, 0x00, 0x00, 0x44, 0x44]), True, 70),  # (108 + 32) / 2
        (bytes([0x01, 0x06, 0x66, 0x66, 0x00, 0x00, 0x66, 0x66]), True, 100),
        # Every weighted nibble its own value, so no two weights can trade
        # places unseen: agility 1 x 2, speed 5 x 3, off. 2 x 3, def. 4 x 2,
        # shot power 0, checking 6 x 2, stick 3 x 3, accuracy 1 x 2, endurance
        # 2, pass 4.
        (bytes([0x01, 0x71, 0x52, 0x40, 0x61, 0x31, 0x26, 0x45]), False, 60),
        # Agility 5, awareness 2 and puck control 4 at 9 each, zones 6, 1, 3, 0
        # at 2 each: 119, halved.
        (bytes([0x01, 0x75, 0x32, 0x24, 0x01, 0x00, 0x61, 0x30]), True, 59),
    ],
)
def test_the_overall_is_the_games_own_weighting(stats, goalie, overall):
    assert game_overall(stats, goalie) == overall


def test_the_team_ratings_bytes(tmp_path):
    """14 the given strength, 15 and 16 zeroed, 18 the mean of line record 0's
    six game overalls minus 2."""
    writer = _writer(tmp_path)
    data = writer.data
    assert data is not None
    players = _records(23)
    writer.write_team_header(5, 14, 7)
    assert writer.write_team_ratings(5, players, 0x52) is True
    base = fixture.team_base(5)
    assert data[base + 14] == 0x52
    assert data[base + 15 : base + 17] == b"\x00\x00"
    six = data[base + LINE_TABLE_BYTE : base + LINE_TABLE_BYTE + 6]
    overalls = [game_overall(player_stats(players[i - 1]), n == 0) for n, i in enumerate(six)]
    assert data[base + 18] == round(sum(overalls) / 6) - 2


def test_no_strength_keeps_the_cartridges_byte(tmp_path):
    writer = _writer(tmp_path)
    data = writer.data
    assert data is not None
    base = fixture.team_base(5)
    data[base + 14] = 0x41
    writer.write_team_header(5, 14, 7)
    writer.write_team_ratings(5, _records(23), None)
    assert data[base + 14] == 0x41


@pytest.mark.parametrize("header", [0, fixture.SMC_HEADER_SIZE])
def test_the_hot_cold_fix_sign_extends_the_modifier_and_can_be_undone(tmp_path, header):
    writer = _writer(tmp_path, with_smc_header=bool(header))
    data = writer.data
    assert data is not None
    at = header + fixture.HOT_COLD_OFFSET
    assert bytes(data[at : at + 14]) == fixture.HOT_COLD_ROUTINE
    assert writer.set_hot_cold_fix(True) is True
    assert (data[at + 8], data[at + 12]) == (0x20, 0xC0)  # BIT #$20 / ORA #$C0
    assert writer.set_hot_cold_fix(False) is True
    assert bytes(data[at : at + 14]) == fixture.HOT_COLD_ROUTINE


def test_the_hot_cold_fix_touches_nothing_where_the_routine_is_not(tmp_path):
    writer = _writer(tmp_path)
    data = writer.data
    assert data is not None
    data[fixture.HOT_COLD_OFFSET] = 0xEA
    before = bytes(data)
    assert writer.set_hot_cold_fix(True) is False
    assert bytes(data) == before


def test_a_roster_that_lost_nothing_keeps_the_counts_it_asked_for():
    # 2 + 14 + 7 = 23 records written, so nothing is clamped and the header
    # bytes are the ones upstream wrote. This is the byte-identity case.
    assert header_counts(23, 14, 7) == (14, 7)


def test_records_the_writer_dropped_come_off_the_defencemen():
    # The filed defect. 21 of 23 reached the image, so the header may claim 5
    # defencemen, not 7 -- `_build_lines` would otherwise resolve `di(5)` to
    # record 21, which was never written.
    assert header_counts(21, 14, 7) == (14, 5)


def test_a_roster_short_of_forwards_claims_no_defencemen_at_all():
    # A provider that returned nine players. Seven forwards and no defenceman,
    # rather than the fourteen and seven that were asked for.
    assert header_counts(9, 14, 7) == (7, 0)


def test_more_records_than_the_request_accounts_for_do_not_become_defencemen():
    """The upper clamp, which is reachable and not decoration.

    `patcher._resolve_roster_counts` accepts any non-negative goalie count from
    `RomInfo.extra`, so a slot cut to 3/14/7 yields 24 records where the line
    table's `HEADER_GOALIE_SLOTS` assumes 23. Without the clamp the header would
    claim 8 defencemen and `_build_lines` would index record 24, which the
    24-record roster does not have either.
    """
    assert header_counts(24, 14, 7) == (14, 7)


def test_a_roster_of_goalies_alone_claims_no_skaters():
    assert header_counts(HEADER_GOALIE_SLOTS, 14, 7) == (0, 0)


def test_a_roster_below_the_goalie_slots_claims_no_skaters():
    # Not a fix -- `_build_lines` still names record 1 here, and one record is
    # not a hockey team. Stated so the remaining edge is on the record.
    assert header_counts(1, 14, 7) == (0, 0)


def test_the_counts_a_short_roster_claims_add_up_to_what_was_written():
    # The property the whole clamp exists for, on the boundary case: goalie
    # slots plus forwards plus defencemen is the number of records written, so
    # the highest index `_build_lines` can produce is the last one written.
    forwards, defencemen = header_counts(21, 14, 7)
    assert HEADER_GOALIE_SLOTS + forwards + defencemen == 21


def test_a_header_written_from_the_clamped_counts_stops_at_the_last_record(tmp_path):
    """End to end at the writer: no line slot names record 22.

    Twenty-one records written under a 14/7 request, numbered 1..21 as the line
    table counts them. Written from the requested counts the table reaches 22;
    written from the clamped ones it stops at 21, the last record that exists.
    """
    writer = _writer(tmp_path)
    forwards, defencemen = header_counts(21, 14, 7)
    assert writer.write_team_header(5, forwards, defencemen) is True
    data = writer.data
    assert data is not None
    base = fixture.team_base(5) + LINE_TABLE_BYTE
    assert max(data[base : base + LINE_COUNT * LINE_SLOTS]) == 21


def test_a_header_written_from_the_requested_counts_names_an_absent_record(tmp_path):
    # The behaviour being diverged from, kept as an assertion so the fix is a
    # difference and not a claim. `_build_lines` reaches `di(5)`, which under 7
    # defencemen from record 17 is record 22 -- one past the 21 records, 1..21,
    # that were written.
    writer = _writer(tmp_path)
    assert writer.write_team_header(5, 14, 7) is True
    data = writer.data
    assert data is not None
    base = fixture.team_base(5) + LINE_TABLE_BYTE
    assert max(data[base : base + LINE_COUNT * LINE_SLOTS]) == 22


def test_the_count_byte_packs_forwards_high_and_defencemen_low(tmp_path):
    writer = _writer(tmp_path)
    assert writer.write_team_header(5, 13, 8) is True
    data = writer.data
    assert data is not None
    assert data[fixture.team_base(5) + PLAYER_COUNT_BYTE] == 0xD8


def test_the_count_byte_is_read_back_by_the_reader(tmp_path):
    writer = _writer(tmp_path)
    writer.write_team_header(5, 12, 9)
    assert writer.finalize() is True
    assert _finalized_reader(writer).read_team_player_counts(5) == (2, 12, 9)


@pytest.mark.parametrize(
    ("forwards", "defencemen", "expected"),
    [(99, 99, 0xFF), (-1, -1, 0x00)],
)
def test_the_count_nibbles_are_clamped_to_four_bits(tmp_path, forwards, defencemen, expected):
    writer = _writer(tmp_path)
    writer.write_team_header(5, forwards, defencemen)
    data = writer.data
    assert data is not None
    assert data[fixture.team_base(5) + PLAYER_COUNT_BYTE] == expected


def test_the_header_write_fills_exactly_sixty_four_line_bytes(tmp_path):
    writer = _writer(tmp_path)
    before = bytes(writer.reader.data or b"")
    writer.write_team_header(5, 14, 7)
    after = bytes(writer.data or b"")
    base = fixture.team_base(5)
    changed = [i for i in range(base, base + fixture.TEAM_HEADER_SIZE) if before[i] != after[i]]
    lines = list(range(base + LINE_TABLE_BYTE, base + LINE_TABLE_BYTE + 64))
    goalies = [base + fixture.GOALIE_OFFSET, base + fixture.GOALIE_OFFSET + 1]
    # The count byte, the goalie nibbles and the line table, and nothing else in
    # the header -- byte 18's team overall is left alone.
    assert changed == [base + PLAYER_COUNT_BYTE, *goalies, *lines]
    assert LINE_COUNT * LINE_SLOTS == 64
    assert LINE_TABLE_BYTE + 64 == fixture.TEAM_HEADER_SIZE


@pytest.mark.parametrize(
    ("cartridge", "written"),
    [
        # Retail patterns. EDM's one goalie keeps his 13 and a backup's 5 joins him.
        (b"\xd0\x00", b"\xd5\x00"),
        # BUF's three, [11, 2, 5]: the two largest, Fuhr's 11 then Hasek's 5.
        (b"\xb2\x50", b"\xb5\x00"),
        # TB's [6, 8, 4]: Jablonski's 8 outweighed G1's 6, so it moves to G1.
        (b"\x68\x40", b"\x86\x00"),
        # Nothing to keep: Montreal's 12 and 5.
        (b"\x00\x00", b"\xc5\x00"),
    ],
)
def test_the_goalie_nibbles_count_the_two_goalies_written_largest_first(
    tmp_path, cartridge, written
):
    """The game counts its goalies from the nonzero nibbles at bytes 19-20 (28 of
    28 retail teams agree), and the patch always writes 2, so 2 must be left
    there, the larger weight on the editor's G1."""
    writer = _writer(tmp_path)
    data = writer.data
    assert data is not None
    goalies = fixture.team_base(5) + fixture.GOALIE_OFFSET
    data[goalies : goalies + 2] = cartridge
    assert writer.write_team_header(5, 14, 7) is True
    assert bytes(data[goalies : goalies + 2]) == written


def test_the_eight_lines_index_goalies_forwards_and_defencemen_by_position(tmp_path):
    """The whole table, in the retail cartridge's layout.

    Eight bytes per record, `[G, LD, RD, LW, C, RW, EA, 0x00]`, with 1-based
    indices: forwards from 3 and defencemen from 3 + forwards, so a header
    written for a different forward count moves every defenceman index. That is
    what makes `patch` reading the count off the mapped record rather than off
    the ROM load bearing. The records run Best, Scoring 1, Scoring 2, Checking,
    PP1, PP2, PK1, PK2, and the starting goalie, record 1, is in every one.
    """
    writer = _writer(tmp_path)
    writer.write_team_header(5, 9, 4)
    data = writer.data
    assert data is not None
    base = fixture.team_base(5) + LINE_TABLE_BYTE
    slots = fixture.LINE_SLOTS
    table = [list(data[base + i * slots : base + (i + 1) * slots]) for i in range(8)]
    # Forwards 3..11, defencemen 12..15, and `di`/`fi` clamp to the last one.
    assert table == [
        [1, 12, 13, 3, 4, 5, 6, 0],  # Best: trio 1, pair 1
        [1, 12, 13, 3, 4, 5, 6, 0],  # Scoring 1: the same unit
        [1, 14, 15, 6, 7, 8, 9, 0],  # Scoring 2
        [1, 15, 15, 9, 10, 11, 3, 0],  # Checking, both defence indices clamped
        [1, 12, 13, 3, 4, 5, 6, 0],  # PP1
        [1, 14, 15, 6, 7, 8, 9, 0],  # PP2
        [1, 12, 13, 9, 10, 11, 3, 0],  # PK1
        [1, 14, 15, 6, 7, 8, 9, 0],  # PK2
    ]


def test_no_record_names_its_extra_attacker_among_the_five_on_the_ice(tmp_path):
    # The retail cartridge does this in 206 of its 208 records.
    writer = _writer(tmp_path)
    writer.write_team_header(5, 14, 7)
    data = writer.data
    assert data is not None
    base = fixture.team_base(5) + LINE_TABLE_BYTE
    for i in range(LINE_COUNT):
        record = data[base + i * 8 : base + i * 8 + 8]
        assert record[6] not in record[1:6]


def test_a_header_write_past_the_last_team_is_refused(tmp_path):
    assert _writer(tmp_path).write_team_header(TEAM_COUNT, 14, 7) is False


def test_a_header_write_with_no_pointer_to_read_is_refused(tmp_path):
    rom = fixture.build_nhl94_snes_rom()
    short = tmp_path / "short.sfc"
    short.write_bytes(bytes(rom[: fixture.POINTER_TABLE_OFFSET]))
    writer = NHL94SNESRomWriter(str(short), str(tmp_path / "out.sfc"))
    assert writer.load() is True
    assert writer.write_team_header(0, 14, 7) is False


def test_finalize_writes_the_buffer_to_the_output_path(tmp_path):
    writer = _writer(tmp_path)
    writer.write_rosters({5: _records(10)})
    assert writer.finalize() is True
    assert (tmp_path / "out.sfc").read_bytes() == bytes(writer.data or b"")


def test_finalize_creates_the_output_directory(tmp_path):
    source = fixture.write_nhl94_snes_rom(tmp_path / "in.sfc")
    target = tmp_path / "nested" / "deeper" / "out.sfc"
    writer = NHL94SNESRomWriter(str(source), str(target))
    assert writer.load() is True
    assert writer.finalize() is True
    assert target.is_file() is True


def test_finalize_without_a_loaded_image_reports_failure(tmp_path):
    writer = NHL94SNESRomWriter(str(tmp_path / "absent.sfc"), str(tmp_path / "out.sfc"))
    assert writer.finalize() is False


def test_finalize_onto_a_path_that_is_a_directory_reports_failure(tmp_path):
    source = fixture.write_nhl94_snes_rom(tmp_path / "in.sfc")
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    writer = NHL94SNESRomWriter(str(source), str(blocked))
    assert writer.load() is True
    assert writer.finalize() is False
