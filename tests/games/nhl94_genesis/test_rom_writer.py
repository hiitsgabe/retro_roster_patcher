"""Writer coverage against the synthetic ROM in `tests/fixtures/synthetic_rom.py`.

The writer had no test at all in the codebase it was ported from, so this file is
the first thing that has ever executed its 357 lines. As with the reader tests,
nothing here touches a real image.

Every read-back goes through a *fresh* `NHL94GenesisRomReader` opened on the
output path. `NHL94GenesisRomWriter.__init__` builds its own reader over the
*input* file and `write_team_roster` sizes its patch from that reader, so
`writer.reader.data` is the pre-write image for the writer's whole lifetime;
asserting against it would assert nothing about what was written.

The stat bytes are checked with `synthetic_rom.decode_player_stats`, which is
transcribed from `_write_player_stats`'s documented byte layout rather than from
its code. `src/` contains no nibble decoder, so without an independently written
one the only available assertion about the packing would be that some byte came
out non-zero.

The last section pins defects rather than intended behaviour, matching the policy
in the reader tests: the port is faithful and stays that way, so each of these
asserts what the writer *does* under a comment saying why that is wrong.
"""

import struct

import pytest

from retro_roster_patcher.games.nhl94_genesis.models import (
    TEAM_COUNT,
    NHL94GenPlayerAttributes,
    NHL94GenPlayerRecord,
)
from retro_roster_patcher.games.nhl94_genesis.rom_reader import (
    CHECKSUM_BYPASS_OFFSET,
    NHL94GenesisRomReader,
)
from retro_roster_patcher.games.nhl94_genesis.rom_writer import (
    NHL94GenesisRomWriter,
    encode_nibble,
    encode_weight_nibble,
)
from tests.fixtures import synthetic_rom


@pytest.fixture
def rom_paths(tmp_path):
    """A synthetic input ROM and a path to write the output to."""
    source = synthetic_rom.write_nhl94_genesis_rom(tmp_path / "in.bin")
    return source, tmp_path / "out.bin"


def _squad():
    """Two goalies, nine forwards, four defencemen — a legal small roster."""
    players = [
        NHL94GenPlayerRecord(name=f"GOALIE{i}", position="G", jersey_number=30 + i, is_goalie=True)
        for i in range(2)
    ]
    players += [
        NHL94GenPlayerRecord(name=f"FORWARD{i}", position="C", jersey_number=10 + i)
        for i in range(9)
    ]
    players += [
        NHL94GenPlayerRecord(name=f"DEFENCE{i}", position="D", jersey_number=2 + i)
        for i in range(4)
    ]
    return players


def _loaded_writer(rom_paths):
    source, output = rom_paths
    writer = NHL94GenesisRomWriter(str(source), str(output))
    assert writer.load() is True
    return writer, output


def _read_back(output):
    """Open the written file with a reader that has never seen the input."""
    reader = NHL94GenesisRomReader(str(output))
    assert reader.load() is True
    return reader


def _fixture_roster(team_index):
    """The names and stat bytes the fixture put in one team's roster region."""
    return (
        [synthetic_rom.player_name(team_index, s) for s in range(synthetic_rom.ROSTER_PLAYERS)],
        [synthetic_rom.player_stats(team_index, s) for s in range(synthetic_rom.ROSTER_PLAYERS)],
    )


# ── Nibble packing ───────────────────────────────────────────────────────


def test_encode_nibble_packs_high_and_low():
    assert encode_nibble(6, 3) == 0x63
    assert encode_nibble(0, 0) == 0x00
    assert encode_nibble(3, 6) == 0x36
    # Both arguments at the top of the legal range, so the maximum byte the
    # function can emit is pinned as 0x66 and not as some wider value.
    assert encode_nibble(6, 6) == 0x66


def test_encode_nibble_clamps_both_nibbles_to_zero_through_six():
    # The clamp is load-bearing: the ROM stat nibbles are a 0-6 scale, and a
    # caller handing over a 0-15 rating must not be allowed to write 0xFF into a
    # stat byte. Both arguments and both ends, because a clamp applied to only one
    # of the two is the mistake this is here to catch.
    assert encode_nibble(15, 15) == 0x66
    assert encode_nibble(7, 0) == 0x60
    assert encode_nibble(0, 7) == 0x06
    assert encode_nibble(-1, 3) == 0x03
    assert encode_nibble(3, -1) == 0x30


def test_encode_weight_nibble_puts_the_weight_class_high():
    assert encode_weight_nibble(7, 4) == 0x74
    assert encode_weight_nibble(0, 0) == 0x00


def test_encode_weight_nibble_clamps_the_weight_to_fourteen_not_to_six():
    # The asymmetry with `encode_nibble`: weight class is a 0-14 scale (140 + 8 lb
    # per step, per `NHL94GenPlayerRecord`), so only the low nibble is capped at 6.
    # A weight of 7 surviving is what tells the two clamps apart.
    assert encode_weight_nibble(14, 6) == 0xE6
    assert encode_weight_nibble(15, 15) == 0xE6
    assert encode_weight_nibble(7, 7) == 0x76
    assert encode_weight_nibble(-1, -1) == 0x00


def test_the_fixture_decoder_inverts_the_writers_encoders():
    # `decode_player_stats` is transcribed from a docstring, so it needs its own
    # check before the tests below can lean on it. Every record the fixture plants
    # is re-encoded through the writer's own functions and compared byte for byte,
    # which is only satisfiable because the fixture keeps all 650 records inside
    # the ranges `encode_nibble`/`encode_weight_nibble` can actually emit.
    for team_index in range(synthetic_rom.TEAM_COUNT):
        for slot in range(synthetic_rom.ROSTER_PLAYERS):
            stats = synthetic_rom.player_stats(team_index, slot)
            d = synthetic_rom.decode_player_stats(stats)
            jersey = d["jersey_number"]
            reencoded = bytes(
                [
                    ((jersey // 10) << 4) | (jersey % 10),
                    encode_weight_nibble(d["weight_class"], d["agility"]),
                    encode_nibble(d["speed"], d["off_awareness"]),
                    encode_nibble(d["def_awareness"], d["shot_power"]),
                    encode_nibble(d["checking"], d["handedness"]),
                    encode_nibble(d["stick_handling"], d["shot_accuracy"]),
                    encode_nibble(d["endurance"], d["roughness"]),
                    encode_nibble(d["pass_accuracy"], d["aggression"]),
                ]
            )
            assert reencoded == stats


# ── Loading ──────────────────────────────────────────────────────────────


def test_loading_a_missing_file_fails(tmp_path):
    writer = NHL94GenesisRomWriter(str(tmp_path / "nope.bin"), str(tmp_path / "out.bin"))
    assert writer.load() is False
    assert writer.data is None


def test_loading_an_empty_file_fails(tmp_path):
    # The writer's `if self.reader.data` is what rejects this, not the reader:
    # `NHL94GenesisRomReader.load` returns True for a zero-byte file (pinned in
    # test_rom_reader.py). This is the `return False` after a successful read.
    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    writer = NHL94GenesisRomWriter(str(empty), str(tmp_path / "out.bin"))
    assert writer.load() is False
    assert writer.data is None


def test_finalize_without_loading_writes_nothing(tmp_path):
    output = tmp_path / "never.bin"
    writer = NHL94GenesisRomWriter(str(tmp_path / "nope.bin"), str(output))
    assert writer.finalize() is False
    assert output.exists() is False


def test_finalize_creates_the_output_directory(tmp_path):
    source = synthetic_rom.write_nhl94_genesis_rom(tmp_path / "in.bin")
    output = tmp_path / "nested" / "deeper" / "out.bin"
    writer = NHL94GenesisRomWriter(str(source), str(output))
    assert writer.load() is True
    assert writer.finalize() is True
    assert len(output.read_bytes()) == synthetic_rom.ROM_SIZE


# ── Roster writing ───────────────────────────────────────────────────────


def test_writing_a_roster_reports_how_many_players_fit(rom_paths):
    # 452 bytes of region against 2 * (2 + 7 + 8) + 13 * (2 + 8 + 8) = 268 bytes of
    # squad, so all fifteen fit with 184 bytes to spare and nothing is truncated.
    writer, _ = _loaded_writer(rom_paths)
    assert writer.write_team_roster(0, _squad()) == 15


def test_written_names_read_back_through_the_reader(rom_paths):
    writer, output = _loaded_writer(rom_paths)
    assert writer.write_team_roster(0, _squad()) == 15
    assert writer.finalize() is True

    names, stats = _read_back(output).read_team_roster(0)
    assert names == [
        "GOALIE0",
        "GOALIE1",
        "FORWARD0",
        "FORWARD1",
        "FORWARD2",
        "FORWARD3",
        "FORWARD4",
        "FORWARD5",
        "FORWARD6",
        "FORWARD7",
        "FORWARD8",
        "DEFENCE0",
        "DEFENCE1",
        "DEFENCE2",
        "DEFENCE3",
    ]
    assert [len(s) for s in stats] == [8] * 15


def test_the_jersey_number_is_written_as_bcd(rom_paths):
    writer, output = _loaded_writer(rom_paths)
    assert (
        writer.write_team_roster(
            0, [NHL94GenPlayerRecord(name="AA", jersey_number=42, position="C")]
        )
        == 1
    )
    assert writer.finalize() is True

    _, stats = _read_back(output).read_team_roster(0)
    # 0x42, not 42: the nibbles are the decimal digits, so a plain byte store
    # would put 0x2A here.
    assert stats[0][0] == 0x42
    assert synthetic_rom.decode_player_stats(stats[0])["jersey_number"] == 42


def test_the_jersey_number_is_clamped_to_one_through_ninety_nine(rom_paths):
    # Both ends, because the BCD packing has no room for a third digit and a zero
    # jersey is not a legal number in the game.
    writer, output = _loaded_writer(rom_paths)
    assert (
        writer.write_team_roster(
            0,
            [
                NHL94GenPlayerRecord(name="LO", jersey_number=0),
                NHL94GenPlayerRecord(name="HI", jersey_number=150),
            ],
        )
        == 2
    )
    assert writer.finalize() is True

    _, stats = _read_back(output).read_team_roster(0)
    assert [s[0] for s in stats] == [0x01, 0x99]


def test_attributes_land_in_the_seven_stat_bytes(rom_paths):
    # Fourteen distinct values across the fourteen nibbles, so a transposed pair or
    # a nibble read from the wrong half of a byte cannot survive. The literal bytes
    # pin the layout; the decoded dict names which value went where.
    writer, output = _loaded_writer(rom_paths)
    player = NHL94GenPlayerRecord(
        name="AA",
        jersey_number=87,
        position="C",
        weight_class=11,
        handedness=1,
        attributes=NHL94GenPlayerAttributes(
            speed=6,
            agility=5,
            shot_power=4,
            shot_accuracy=3,
            stick_handling=2,
            pass_accuracy=1,
            off_awareness=0,
            def_awareness=6,
            checking=5,
            endurance=4,
            roughness=3,
            aggression=2,
        ),
    )
    assert writer.write_team_roster(0, [player]) == 1
    assert writer.finalize() is True

    _, stats = _read_back(output).read_team_roster(0)
    assert stats[0] == b"\x87\xb5\x60\x64\x51\x23\x43\x12"
    assert synthetic_rom.decode_player_stats(stats[0]) == {
        "jersey_number": 87,
        "weight_class": 11,
        "agility": 5,
        "speed": 6,
        "off_awareness": 0,
        "def_awareness": 6,
        "shot_power": 4,
        "checking": 5,
        "handedness": 1,
        "stick_handling": 2,
        "shot_accuracy": 3,
        "endurance": 4,
        "roughness": 3,
        "pass_accuracy": 1,
        "aggression": 2,
    }


def test_the_record_defaults_are_written_as_the_middling_ratings(rom_paths):
    # The defaults are what every caller that does not supply attributes gets, so
    # they are worth pinning as bytes: weight 7, most attributes 3, roughness and
    # aggression 2, handedness left.
    writer, output = _loaded_writer(rom_paths)
    assert writer.write_team_roster(0, [NHL94GenPlayerRecord(name="AA")]) == 1
    assert writer.finalize() is True

    _, stats = _read_back(output).read_team_roster(0)
    assert stats[0] == b"\x01\x73\x33\x33\x30\x33\x32\x32"


def test_an_out_of_range_attribute_is_clamped_before_it_reaches_the_rom(rom_paths):
    # Ratings arriving from a sports API are on a wider scale than the ROM's 0-6.
    # This is the path that keeps such a value from spilling out of its nibble and
    # corrupting the neighbouring one.
    writer, output = _loaded_writer(rom_paths)
    player = NHL94GenPlayerRecord(
        name="AA",
        jersey_number=1,
        weight_class=99,
        attributes=NHL94GenPlayerAttributes(speed=99, off_awareness=99),
    )
    assert writer.write_team_roster(0, [player]) == 1
    assert writer.finalize() is True

    _, stats = _read_back(output).read_team_roster(0)
    decoded = synthetic_rom.decode_player_stats(stats[0])
    assert (decoded["weight_class"], decoded["speed"], decoded["off_awareness"]) == (14, 6, 6)


def test_the_roster_ends_with_a_sentinel_and_the_rest_is_zero_filled(rom_paths):
    # The stale tail matters: the region already holds 25 fixture records, so
    # without the zero fill the reader would run straight on past a short new
    # roster into whatever the previous team had there.
    writer, output = _loaded_writer(rom_paths)
    assert writer.write_team_roster(0, _squad()) == 15
    assert writer.finalize() is True

    start, size = _read_back(output).get_team_player_region(0)
    written_bytes = 2 * (2 + 7 + 8) + 13 * (2 + 8 + 8)
    data = output.read_bytes()
    assert data[start + written_bytes : start + written_bytes + 2] == b"\x00\x00"
    assert data[start + written_bytes : start + 452] == b"\x00" * (452 - written_bytes)
    # The region shrinks to the sentinel, which is what a later re-read sees.
    assert (start, size) == (synthetic_rom.team_base(0) + synthetic_rom.SEC_PLAYERS, 270)


def test_writing_one_team_leaves_the_others_byte_identical(rom_paths):
    source, output = rom_paths
    writer, _ = _loaded_writer(rom_paths)
    assert writer.write_team_roster(0, _squad()) == 15
    assert writer.finalize() is True

    before = source.read_bytes()
    after = output.read_bytes()
    team_0_end = synthetic_rom.team_base(0) + synthetic_rom.SEC_PLAYERS + 452
    assert after[team_0_end:] == before[team_0_end:]
    assert _read_back(output).read_team_roster(1) == _fixture_roster(1)


def test_a_roster_larger_than_the_region_is_truncated_not_overflowed(rom_paths):
    # 200 players of 30 characters against 452 bytes. Each record costs 40 bytes,
    # so after eleven of them 12 bytes remain, `max_name_len` is 0 and the loop
    # takes the `< 1` break. Nothing is truncated on this path — every name that
    # made it in is still 30 characters — and the region is never exceeded.
    writer, output = _loaded_writer(rom_paths)
    huge = [NHL94GenPlayerRecord(name="X" * 30, jersey_number=1, position="C") for _ in range(200)]
    assert writer.write_team_roster(0, huge) == 11
    assert writer.finalize() is True

    names, stats = _read_back(output).read_team_roster(0)
    assert [len(n) for n in names] == [30] * 11
    assert len(stats) == 11
    # 11 * 40 = 440, plus the 2-byte sentinel, is the whole 452-byte region.
    data = output.read_bytes()
    region_end = synthetic_rom.team_base(0) + synthetic_rom.SEC_PLAYERS + 452
    assert data[region_end - 12 : region_end] == b"\x00" * 12
    assert _read_back(output).read_team_roster(1) == _fixture_roster(1)


def test_a_name_is_truncated_when_only_part_of_it_fits(rom_paths):
    # The other exit from the same loop, and the one the 200-player case never
    # reaches. Twenty-four 8-character records consume 432 of the 452 bytes, so the
    # twenty-fifth player sees `max_name_len == 8` and keeps only "TRUNCATE"; the
    # twenty-sixth then finds 2 bytes left and breaks.
    writer, output = _loaded_writer(rom_paths)
    roster = [NHL94GenPlayerRecord(name=f"NAME{i:04d}", jersey_number=1) for i in range(24)]
    roster.append(NHL94GenPlayerRecord(name="TRUNCATED_LONG_NAME_HERE_XXXXX", jersey_number=2))
    roster.append(NHL94GenPlayerRecord(name="NEVERFITS", jersey_number=3))
    assert writer.write_team_roster(0, roster) == 25
    assert writer.finalize() is True

    names, stats = _read_back(output).read_team_roster(0)
    assert len(names) == 25
    assert names[23] == "NAME0023"
    assert names[24] == "TRUNCATE"
    # The truncated player still gets his full eight stat bytes, and the player who
    # did not fit contributed nothing at all.
    assert stats[24][0] == 0x02
    assert 3 not in [s[0] for s in stats]


def test_the_region_of_a_team_whose_pointer_is_out_of_range_reports_no_space(tmp_path):
    # `get_team_player_region` answering (0, 0) is the writer's only signal that a
    # team block is unreachable, and the `region_size == 0` guard is the only thing
    # between that and a write at file offset 0.
    rom = synthetic_rom.build_nhl94_genesis_rom()
    entry = synthetic_rom.POINTER_TABLE_OFFSET + 5 * 4
    rom[entry : entry + 4] = b"\xff\xff\xff\xff"
    source = tmp_path / "bad_pointer.bin"
    source.write_bytes(bytes(rom))
    writer = NHL94GenesisRomWriter(str(source), str(tmp_path / "out.bin"))
    assert writer.load() is True
    assert writer.write_team_roster(5, _squad()) == -1
    assert writer.write_team_header(5, _squad()) is False


def test_a_team_index_at_the_count_is_refused(rom_paths):
    # Both `team_index >= TEAM_COUNT` checks in the writer are masked by the
    # reader's own: `get_team_player_region` already answers (0, 0) and
    # `get_team_section_offsets` already answers None for an index this large, and
    # the guards behind those turn each into the same result. So this pins the
    # behaviour rather than either line — deleting both leaves it green.
    writer, _ = _loaded_writer(rom_paths)
    assert writer.write_team_roster(TEAM_COUNT, _squad()) == -1
    assert writer.write_team_header(TEAM_COUNT, _squad()) is False


# ── Team header ──────────────────────────────────────────────────────────


def test_the_team_header_records_forward_and_defence_counts(rom_paths):
    writer, output = _loaded_writer(rom_paths)
    squad = _squad()
    written = writer.write_team_roster(0, squad)
    assert writer.write_team_header(0, squad, actual_count=written) is True
    assert writer.finalize() is True

    base = synthetic_rom.team_base(0)
    data = output.read_bytes()
    count_byte = data[base + synthetic_rom.SEC_RATINGS + 3]
    assert count_byte == 0x94
    assert count_byte >> 4 == 9  # forwards
    assert count_byte & 0x0F == 4  # defence
    # The three bytes the count byte sits behind are not the writer's to touch.
    assert data[base + synthetic_rom.SEC_RATINGS : base + synthetic_rom.SEC_RATINGS + 3] == (
        b"\x00\x00\x00"
    )


def test_the_header_counts_only_the_players_that_actually_fit(rom_paths):
    # `actual_count` exists because `write_team_roster` may have dropped the tail of
    # the list; a header describing players that are not in the ROM would point the
    # game's line data at roster slots that do not exist.
    writer, output = _loaded_writer(rom_paths)
    squad = _squad()
    assert writer.write_team_roster(0, squad) == 15
    # First four: two goalies and two forwards, no defence at all.
    assert writer.write_team_header(0, squad, actual_count=4) is True
    assert writer.finalize() is True

    base = synthetic_rom.team_base(0)
    assert output.read_bytes()[base + synthetic_rom.SEC_RATINGS + 3] == 0x20


def test_a_header_for_zero_surviving_players_is_refused(rom_paths):
    # `actual_count=0` empties the slice after the `not players` guard has already
    # passed, which is the second of the two early returns.
    writer, _ = _loaded_writer(rom_paths)
    assert writer.write_team_header(0, _squad(), actual_count=0) is False


def test_writing_a_header_with_no_players_is_refused(rom_paths):
    writer, _ = _loaded_writer(rom_paths)
    assert writer.write_team_header(0, []) is False


def test_the_team_header_writes_sixty_four_bytes_of_line_data(rom_paths):
    # Eight lines of [01, LD, RD, LW, C, RW, EA, G] over a roster laid out as two
    # goalies, then forwards 2-10, then defence 11-14. Pinned whole because the
    # only other way to state it is to re-run `_generate_lines`, which would assert
    # nothing about where the bytes landed or in what order.
    writer, output = _loaded_writer(rom_paths)
    squad = _squad()
    assert writer.write_team_roster(0, squad) == 15
    assert writer.write_team_header(0, squad, actual_count=len(squad)) is True
    assert writer.finalize() is True

    base = synthetic_rom.team_base(0)
    lines = output.read_bytes()[
        base + synthetic_rom.SEC_LINES : base + synthetic_rom.SEC_LINES + 64
    ]
    assert lines == bytes(
        [
            0x01, 11, 12, 3, 2, 4, 5, 0,
            0x01, 13, 14, 6, 5, 7, 8, 0,
            0x01, 11, 12, 9, 8, 10, 10, 0,
            0x01, 13, 14, 10, 10, 10, 2, 0,
            0x01, 11, 12, 3, 2, 4, 6, 0,
            0x01, 13, 14, 6, 5, 7, 9, 0,
            0x01, 11, 12, 9, 8, 10, 2, 0,
            0x01, 13, 14, 10, 10, 10, 5, 0,
        ]
    )  # fmt: skip


def test_a_roster_with_no_forwards_or_defence_points_every_slot_at_the_starter(rom_paths):
    # The `forward_count == 0` and `defense_count == 0` arms of `_generate_lines`.
    # Without them `f()` and `d()` would index off the end of a goalies-only roster
    # and write slot numbers the game has no players for.
    writer, output = _loaded_writer(rom_paths)
    goalies = [
        NHL94GenPlayerRecord(name=f"GOALIE{i}", position="G", jersey_number=30 + i, is_goalie=True)
        for i in range(3)
    ]
    assert writer.write_team_roster(0, goalies) == 3
    assert writer.write_team_header(0, goalies) is True
    assert writer.finalize() is True

    base = synthetic_rom.team_base(0)
    data = output.read_bytes()
    assert data[base + synthetic_rom.SEC_LINES : base + synthetic_rom.SEC_LINES + 64] == (
        bytes([0x01, 0, 0, 0, 0, 0, 0, 0]) * 8
    )
    assert data[base + synthetic_rom.SEC_RATINGS + 3] == 0x00


def test_a_single_defenceman_is_used_as_both_halves_of_every_pair(rom_paths):
    # The `min(i, defense_count - 1)` clamp inside `d()`. Every line asks for two
    # defence slots, so with one defenceman on the roster the second ask is out of
    # range and only the clamp keeps it from naming slot 6 on a six-player team.
    # Four defencemen — what `_squad` has — never reach it: `d_pairs` is 2 there and
    # the largest index requested is 3, so the clamp is a no-op and removing it
    # changes nothing the other line-data test can see.
    writer, output = _loaded_writer(rom_paths)
    roster = [
        NHL94GenPlayerRecord(name=f"GOALIE{i}", position="G", jersey_number=30 + i, is_goalie=True)
        for i in range(2)
    ]
    roster += [
        NHL94GenPlayerRecord(name=f"FWD{i}", position="C", jersey_number=10 + i) for i in range(3)
    ]
    roster.append(NHL94GenPlayerRecord(name="SOLO_D", position="D", jersey_number=4))
    assert writer.write_team_roster(0, roster) == 6
    assert writer.write_team_header(0, roster) is True
    assert writer.finalize() is True

    base = synthetic_rom.team_base(0)
    lines = output.read_bytes()[
        base + synthetic_rom.SEC_LINES : base + synthetic_rom.SEC_LINES + 64
    ]
    # Slot 5 is the lone defenceman and appears as both LD and RD on all eight
    # lines; no byte names a slot past the end of a six-player roster.
    assert lines == bytes(
        [
            0x01, 5, 5, 3, 2, 4, 4, 0,
            0x01, 5, 5, 4, 4, 4, 4, 0,
            0x01, 5, 5, 4, 4, 4, 4, 0,
            0x01, 5, 5, 4, 4, 4, 2, 0,
            0x01, 5, 5, 3, 2, 4, 4, 0,
            0x01, 5, 5, 4, 4, 4, 4, 0,
            0x01, 5, 5, 4, 4, 4, 2, 0,
            0x01, 5, 5, 4, 4, 4, 4, 0,
        ]
    )  # fmt: skip


def test_the_goalie_byte_flags_a_third_goalie_and_preserves_the_byte_before_it(tmp_path):
    # Byte 0 of the goalies section is a per-team value the writer must not clobber,
    # so it is planted non-zero here; the fixture leaves it at zero, where a stray
    # write would be invisible. Byte 1 carries the "more than two goalies" flag.
    rom = synthetic_rom.build_nhl94_genesis_rom()
    goalies_off = synthetic_rom.team_base(0) + synthetic_rom.SEC_GOALIES
    rom[goalies_off] = 0x5A
    rom[goalies_off + 1] = 0xFF
    source = tmp_path / "planted.bin"
    source.write_bytes(bytes(rom))

    for count, expected in ((2, 0x00), (3, 0x10)):
        output = tmp_path / f"out{count}.bin"
        writer = NHL94GenesisRomWriter(str(source), str(output))
        assert writer.load() is True
        roster = [
            NHL94GenPlayerRecord(name=f"G{i}", position="G", jersey_number=30 + i, is_goalie=True)
            for i in range(count)
        ]
        assert writer.write_team_roster(0, roster) == count
        assert writer.write_team_header(0, roster) is True
        assert writer.finalize() is True
        assert output.read_bytes()[goalies_off : goalies_off + 2] == bytes([0x5A, expected])


# ── Checksums and finalize ───────────────────────────────────────────────


def test_disable_checksum_writes_an_rts_at_the_bypass_offset(rom_paths):
    writer, output = _loaded_writer(rom_paths)
    writer.disable_checksum()
    assert writer.finalize() is True

    data = output.read_bytes()
    assert data[CHECKSUM_BYPASS_OFFSET : CHECKSUM_BYPASS_OFFSET + 2] == b"\x4e\x75"
    # A word-aligned two-byte patch and nothing more.
    assert CHECKSUM_BYPASS_OFFSET % 2 == 0
    assert data[CHECKSUM_BYPASS_OFFSET - 2 : CHECKSUM_BYPASS_OFFSET] == b"\x00\x00"
    assert data[CHECKSUM_BYPASS_OFFSET + 2 : CHECKSUM_BYPASS_OFFSET + 4] == b"\x00\x00"


def test_disable_checksum_before_loading_does_nothing(tmp_path):
    writer = NHL94GenesisRomWriter(str(tmp_path / "nope.bin"), str(tmp_path / "out.bin"))
    writer.disable_checksum()
    assert writer.data is None


def test_update_header_checksum_sums_every_word_from_0x200(rom_paths):
    writer, output = _loaded_writer(rom_paths)
    writer.disable_checksum()
    writer.update_header_checksum()
    assert writer.finalize() is True

    data = output.read_bytes()
    expected = (
        sum(struct.unpack_from(">H", data, i)[0] for i in range(0x200, len(data), 2)) & 0xFFFF
    )
    assert struct.unpack_from(">H", data, 0x18E)[0] == expected
    # The RTS is the only non-zero content past 0x200 in an otherwise blank tail,
    # so the sum is a value only that patch produces.
    assert (
        expected
        == 0x4E75
        + sum(
            struct.unpack_from(">H", data, i)[0]
            for i in range(0x200, len(data), 2)
            if i != CHECKSUM_BYPASS_OFFSET
        )
        & 0xFFFF
    )


def test_update_header_checksum_before_loading_does_nothing(tmp_path):
    writer = NHL94GenesisRomWriter(str(tmp_path / "nope.bin"), str(tmp_path / "out.bin"))
    writer.update_header_checksum()
    assert writer.data is None


def test_finalize_leaves_the_input_rom_untouched(rom_paths):
    source, _ = rom_paths
    before = source.read_bytes()
    writer, _ = _loaded_writer(rom_paths)
    assert writer.write_team_roster(0, _squad()) == 15
    writer.disable_checksum()
    assert writer.finalize() is True
    assert source.read_bytes() == before


def test_the_output_is_still_a_valid_rom_after_a_full_patch(rom_paths):
    writer, output = _loaded_writer(rom_paths)
    squad = _squad()
    for slot in range(synthetic_rom.TEAM_COUNT):
        written = writer.write_team_roster(slot, squad)
        assert written == 15
        assert writer.write_team_header(slot, squad, actual_count=written) is True
    writer.disable_checksum()
    writer.update_header_checksum()
    assert writer.finalize() is True

    reader = _read_back(output)
    assert reader.validate() is True
    assert len(output.read_bytes()) == synthetic_rom.ROM_SIZE
    # Every slot reads back the new squad, and the team names the patch never
    # touched are still the ones the fixture wrote.
    for slot in range(synthetic_rom.TEAM_COUNT):
        names, stats = reader.read_team_roster(slot)
        assert names[0] == "GOALIE0"
        assert len(stats) == 15
    slots = reader.get_info().team_slots
    assert [s.current_name for s in slots] == synthetic_rom.CITIES


# ── Pinned defects ───────────────────────────────────────────────────────


def test_a_negative_team_index_writes_a_roster_at_file_offset_zero(rom_paths):
    # DEFECT: the guard is `team_index >= TEAM_COUNT` only, exactly as in the
    # reader. A negative index reads the word below the pointer table, which is
    # zero here, resolves the team block to file offset 0 and reports a 2-byte
    # "region" there — so the call returns 0 rather than the documented -1 and
    # writes a sentinel over the first two bytes of the 68000 reset vector.
    writer, output = _loaded_writer(rom_paths)
    assert writer.write_team_roster(-1, _squad()) == 0
    assert writer.reader.get_team_player_region(-1) == (0, 2)
    assert writer.finalize() is True
    assert output.read_bytes()[:2] == b"\x00\x00"


def test_a_negative_team_index_overwrites_the_start_of_the_rom_with_line_data(rom_paths):
    # DEFECT: the same missing guard in `write_team_header`, and far louder — all
    # six section offsets resolve to 0, so the call reports success and writes the
    # count byte, the goalie byte and 64 bytes of line assignments over the vector
    # table at the head of the file.
    writer, output = _loaded_writer(rom_paths)
    assert writer.write_team_header(-1, _squad()) is True
    assert writer.finalize() is True
    assert output.read_bytes()[:8] == bytes([0x01, 11, 12, 3, 2, 4, 5, 0])


def test_patching_a_rom_that_was_already_patched_loses_roster_space(rom_paths):
    # DEFECT: the roster region is defined by where the sentinel currently sits, and
    # `write_team_roster` moves the sentinel down to the end of whatever it just
    # wrote instead of leaving it at the end of the original space. A short roster
    # therefore shrinks the region permanently, and re-patching the output can no
    # longer fit a roster the original ROM had room for. The remaining tail is
    # zero-filled and unreachable, not reclaimed.
    writer, output = _loaded_writer(rom_paths)
    start = synthetic_rom.team_base(0) + synthetic_rom.SEC_PLAYERS
    full = [NHL94GenPlayerRecord(name=f"NAME{i:04d}", jersey_number=1) for i in range(25)]

    # A fresh writer on the untouched ROM fits all twenty-five.
    assert writer.reader.get_team_player_region(0) == (start, 452)
    assert writer.write_team_roster(0, _squad()) == 15
    assert writer.finalize() is True

    second = NHL94GenesisRomWriter(str(output), str(output.parent / "again.bin"))
    assert second.load() is True
    assert second.reader.get_team_player_region(0) == (start, 270)
    assert second.write_team_roster(0, full) == 15
    assert second.finalize() is True

    names, _ = _read_back(output.parent / "again.bin").read_team_roster(0)
    assert len(names) == 15
    assert names[14] == "NAME00"  # truncated to the six bytes that were left
