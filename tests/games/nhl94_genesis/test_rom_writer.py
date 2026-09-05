"""Writer coverage against the synthetic ROM in `tests/fixtures/synthetic_rom.py`.

Every read-back goes through a *fresh* `NHL94GenesisRomReader` opened on the
output path. `NHL94GenesisRomWriter.__init__` builds its own reader over the
*input* file, so `writer.reader.data` is the pre-write image for the writer's
whole lifetime and asserting against it would assert nothing about the write.

The stat bytes are checked with `synthetic_rom.decode_player_stats`, transcribed
from `_write_player_stats`'s documented byte layout rather than from its code.

Every span this file needs to watch *become* zero, or *stay* untouched, is
filled with `POISON` first: against an image that was already zero there, "these
bytes are zero" passes whether or not the writer ran.
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

# 25 records of 18 bytes plus the 2-byte sentinel, as the fixture lays them out.
ROSTER_REGION = 452

# A byte the fixture never writes, planted where a test needs to tell "the writer
# put a zero here" apart from "this was already zero".
POISON = 0xA5


@pytest.fixture
def rom_paths(tmp_path):
    """A synthetic input ROM and a path to write the output to."""
    source = synthetic_rom.write_nhl94_genesis_rom(tmp_path / "in.bin")
    return source, tmp_path / "out.bin"


def _poisoned_rom(*spans):
    """The fixture image with `(offset, length)` spans filled with `POISON`.

    Each caller names only the spans its own assertions look at, because poison
    is not free: the bytes at 0x00 and 0x02 are the first two section pointers of
    the bogus team block a negative index resolves to, and filling those changes
    where that write lands.
    """
    rom = synthetic_rom.build_nhl94_genesis_rom()
    for offset, length in spans:
        rom[offset : offset + length] = bytes([POISON]) * length
    return rom


def _write_rom(tmp_path, name, rom):
    path = tmp_path / name
    path.write_bytes(bytes(rom))
    return path


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


def _line_data(output, team_index=0):
    """The 64 bytes of line assignments a written ROM holds for one team."""
    lines_off = synthetic_rom.team_base(team_index) + synthetic_rom.SEC_LINES
    return output.read_bytes()[lines_off : lines_off + 64]


def _line_bytes(rows):
    """Flatten eight 8-byte line records into the 64 bytes they occupy."""
    return bytes(value for row in rows for value in row)


def test_encode_nibble_packs_high_and_low():
    assert encode_nibble(6, 3) == 0x63
    assert encode_nibble(0, 0) == 0x00
    assert encode_nibble(3, 6) == 0x36
    # Both arguments at the top of the legal range, so the maximum byte the
    # function can emit is pinned as 0x66 and not as some wider value.
    assert encode_nibble(6, 6) == 0x66


def test_encode_nibble_clamps_both_nibbles_to_zero_through_six():
    # The ROM stat nibbles are a 0-6 scale, so a caller handing over a 0-15 rating
    # must not write 0xFF into a stat byte. Both arguments and both ends: a clamp
    # applied to only one of the two is the mistake this is here to catch.
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
    # check. Every fixture record is re-encoded through the writer's own functions,
    # which is only satisfiable because the fixture keeps all 650 records inside the
    # ranges `encode_nibble`/`encode_weight_nibble` can emit.
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


def test_finalize_reports_false_when_the_output_directory_cannot_be_made(tmp_path):
    # `os.makedirs` raising NotADirectoryError, because a component of the path is
    # a regular file. This and the test below are the only two things that execute
    # `finalize`'s `except Exception: return False`.
    source = synthetic_rom.write_nhl94_genesis_rom(tmp_path / "in.bin")
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"a regular file, not a directory")
    writer = NHL94GenesisRomWriter(str(source), str(blocker / "sub" / "out.bin"))
    assert writer.load() is True
    assert writer.finalize() is False
    assert blocker.read_bytes() == b"a regular file, not a directory"


def test_finalize_reports_false_when_the_output_path_is_a_directory(tmp_path):
    # `open(dir, "wb")` raising IsADirectoryError. `os.makedirs` is happy here —
    # the parent already exists and `exist_ok=True` — so this reaches the `open`.
    source = synthetic_rom.write_nhl94_genesis_rom(tmp_path / "in.bin")
    output = tmp_path / "outdir"
    output.mkdir()
    writer = NHL94GenesisRomWriter(str(source), str(output))
    assert writer.load() is True
    assert writer.finalize() is False
    assert list(output.iterdir()) == []


def test_a_file_far_too_small_to_be_a_rom_loads_and_survives_every_operation(tmp_path):
    # `load` never calls `validate`, so a 16-byte file loads like any other and the
    # size guards inside the individual operations are all that stand between that
    # and an IndexError: `update_header_checksum` would store at 0x18E and
    # `disable_checksum` at 0x0FFACA, both far past the end. Every one of them must
    # decline and leave the image alone.
    tiny = _write_rom(tmp_path, "tiny.bin", bytes([POISON]) * 16)
    output = tmp_path / "tiny_out.bin"
    writer = NHL94GenesisRomWriter(str(tiny), str(output))
    assert writer.load() is True
    assert len(writer.data) == 16
    writer.update_header_checksum()
    writer.disable_checksum()
    assert writer.write_team_roster(0, _squad()) == -1
    assert writer.write_team_header(0, _squad()) is False
    assert writer.finalize() is True
    assert output.read_bytes() == bytes([POISON]) * 16


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
    # `encode_nibble` clamps every attribute to 0-6, so twelve attributes cannot all
    # take distinct values. The repeats straddle the packing: the six attributes
    # stored in a high nibble all differ, the six in a low nibble all differ, and no
    # byte holds two equal nibbles. A swap of two equal-valued attributes that also
    # crosses halves — def_awareness against agility — stays invisible, and no
    # assignment of 0-6 to twelve fields can make it visible.
    writer, output = _loaded_writer(rom_paths)
    player = NHL94GenPlayerRecord(
        name="AA",
        jersey_number=87,
        position="C",
        weight_class=11,
        handedness=1,
        attributes=NHL94GenPlayerAttributes(
            speed=0,
            agility=1,
            shot_power=5,
            shot_accuracy=4,
            stick_handling=3,
            pass_accuracy=5,
            off_awareness=6,
            def_awareness=1,
            checking=2,
            endurance=4,
            roughness=3,
            aggression=2,
        ),
    )
    assert writer.write_team_roster(0, [player]) == 1
    assert writer.finalize() is True

    _, stats = _read_back(output).read_team_roster(0)
    assert stats[0] == b"\x87\xb1\x06\x15\x21\x34\x43\x52"
    assert synthetic_rom.decode_player_stats(stats[0]) == {
        "jersey_number": 87,
        "weight_class": 11,
        "agility": 1,
        "speed": 0,
        "off_awareness": 6,
        "def_awareness": 1,
        "shot_power": 5,
        "checking": 2,
        "handedness": 1,
        "stick_handling": 3,
        "shot_accuracy": 4,
        "endurance": 4,
        "roughness": 3,
        "pass_accuracy": 5,
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
    # Ratings arriving from a sports API are on a wider scale than the ROM's 0-6;
    # this is the path that keeps one from spilling into the neighbouring nibble.
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


def test_the_roster_ends_with_a_sentinel_and_the_rest_is_zero_filled(tmp_path):
    # The region already holds 25 fixture records, so without the zero fill the
    # reader runs past a short new roster into whatever the previous team left. The
    # 16 bytes after the region are poisoned because the fixture leaves them zero.
    #
    # The reader ends a region on any length word below 3, not on 0x0000 alone, so
    # the fixture's terminator is 0x0002: the region stays 452 bytes long while its
    # final byte is non-zero, and a fill that stopped one byte short shows up.
    start = synthetic_rom.team_base(0) + synthetic_rom.SEC_PLAYERS
    rom = _poisoned_rom((start + ROSTER_REGION, 16))
    rom[start + ROSTER_REGION - 2 : start + ROSTER_REGION] = b"\x00\x02"
    source = _write_rom(tmp_path, "gap.bin", rom)
    output = tmp_path / "out.bin"
    writer = NHL94GenesisRomWriter(str(source), str(output))
    assert writer.load() is True
    assert writer.write_team_roster(0, _squad()) == 15
    assert writer.finalize() is True

    written_bytes = 2 * (2 + 7 + 8) + 13 * (2 + 8 + 8)
    data = output.read_bytes()
    assert data[start + written_bytes : start + written_bytes + 2] == b"\x00\x00"
    assert data[start + written_bytes : start + ROSTER_REGION] == (
        b"\x00" * (ROSTER_REGION - written_bytes)
    )
    assert data[start + ROSTER_REGION : start + ROSTER_REGION + 16] == bytes([POISON]) * 16
    # The region shrinks to the sentinel, which is what a later re-read sees.
    assert _read_back(output).get_team_player_region(0) == (start, 270)


def test_writing_one_team_leaves_the_others_byte_identical(rom_paths):
    source, output = rom_paths
    writer, _ = _loaded_writer(rom_paths)
    assert writer.write_team_roster(0, _squad()) == 15
    assert writer.finalize() is True

    before = source.read_bytes()
    after = output.read_bytes()
    team_0_end = synthetic_rom.team_base(0) + synthetic_rom.SEC_PLAYERS + ROSTER_REGION
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
    # 11 * 40 = 440 bytes of records; the 12 that remain of the 452-byte region are
    # the 2-byte sentinel followed by 10 bytes of zero fill.
    data = output.read_bytes()
    region_end = synthetic_rom.team_base(0) + synthetic_rom.SEC_PLAYERS + ROSTER_REGION
    assert data[region_end - 12 : region_end] == b"\x00" * 12
    assert _read_back(output).read_team_roster(1) == _fixture_roster(1)


def test_a_non_ascii_name_becomes_one_question_mark_per_character(rom_paths):
    # `errors="replace"` on the ascii codec emits exactly one "?" per unencodable
    # character. Dropping them ("ignore") would shorten the name, and latin-1 would
    # emit a high byte the ROM's font cannot draw and the reader decodes as U+FFFD.
    writer, output = _loaded_writer(rom_paths)
    assert (
        writer.write_team_roster(
            0,
            [
                NHL94GenPlayerRecord(name="NÄSLUND", jersey_number=1),
                NHL94GenPlayerRecord(name="MÜLLER", jersey_number=2),
            ],
        )
        == 2
    )
    assert writer.finalize() is True

    names, _ = _read_back(output).read_team_roster(0)
    assert names == ["N?SLUND", "M?LLER"]
    # One byte per character, so the length words still describe the records.
    start = synthetic_rom.team_base(0) + synthetic_rom.SEC_PLAYERS
    data = output.read_bytes()
    assert struct.unpack_from(">H", data, start)[0] == 9
    assert struct.unpack_from(">H", data, start + 9 + 8)[0] == 8


def test_an_empty_name_is_written_as_a_length_word_the_reader_reads_as_the_end(rom_paths):
    # PINS UPSTREAM FIDELITY DELIBERATELY. Known to be wrong, kept because these are
    # the only bytes this game has ever been fed by a released build of the patcher.
    #
    # `read_team_roster` and `get_team_player_region` both stop at a length word
    # below 3, and an empty name encodes a length word of exactly 2. Written
    # mid-roster it buries the end-of-roster sentinel inside the roster: the region
    # re-measures at 56 bytes instead of 355, so a later patch of the same team
    # truncates against the short region. Both providers can hand over "".
    #
    # Do not "fix" this by writing a placeholder byte.
    writer, output = _loaded_writer(rom_paths)
    roster = [NHL94GenPlayerRecord(name=f"NAME{i:04d}", jersey_number=i + 1) for i in range(20)]
    roster[3] = NHL94GenPlayerRecord(name="", jersey_number=4)
    # The writer still counts all twenty, which is the half of the defect that
    # reaches the caller: `PatchResult.players_patched` overstates the roster.
    assert writer.write_team_roster(0, roster) == 20
    assert writer.finalize() is True

    data = output.read_bytes()
    start = synthetic_rom.team_base(0) + synthetic_rom.SEC_PLAYERS
    # Three 18-byte records ahead of it, then the length word the reader stops on.
    assert struct.unpack_from(">H", data, start + 54)[0] == 2

    reader = _read_back(output)
    names, stats = reader.read_team_roster(0)
    # Three, not twenty: the sixteen records after the empty name are unreachable.
    assert len(names) == 3
    assert len(stats) == 3
    assert names == ["NAME0000", "NAME0001", "NAME0002"]
    # And the region re-measures short, so a second patch of this team would fit
    # far fewer players than the first one did.
    assert reader.get_team_player_region(0)[1] == 56


def test_a_one_character_name_survives_the_round_trip(rom_paths):
    # Length word 3 is the low edge of the reader's `length < 3` sentinel test, so a
    # one-character name is the shortest record that must not read as end-of-roster.
    writer, output = _loaded_writer(rom_paths)
    roster = [
        NHL94GenPlayerRecord(name=name, jersey_number=i + 1)
        for i, name in enumerate(("AAAA", "X", "BBBB", "CCCC"))
    ]
    assert writer.write_team_roster(0, roster) == 4
    assert writer.finalize() is True

    reader = _read_back(output)
    names, stats = reader.read_team_roster(0)
    assert names == ["AAAA", "X", "BBBB", "CCCC"]
    assert stats[2][0] == 0x03
    # Three 14-byte records, one 11-byte record, and the 2-byte sentinel.
    assert reader.get_team_player_region(0)[1] == 55


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
    # team block is unreachable. The `region_size == 0` guard buys the documented -1
    # return and nothing more: without it `start` and `end` are both 0, so no byte
    # is written either way and only the reported result changes.
    rom = synthetic_rom.build_nhl94_genesis_rom()
    entry = synthetic_rom.POINTER_TABLE_OFFSET + 5 * 4
    rom[entry : entry + 4] = b"\xff\xff\xff\xff"
    source = _write_rom(tmp_path, "bad_pointer.bin", rom)
    writer = NHL94GenesisRomWriter(str(source), str(tmp_path / "out.bin"))
    assert writer.load() is True
    assert writer.write_team_roster(5, _squad()) == -1
    assert writer.write_team_header(5, _squad()) is False


def test_a_team_index_at_the_count_is_refused(rom_paths):
    # Both `team_index >= TEAM_COUNT` checks in the writer are masked by the
    # reader's own, so this pins the behaviour rather than either line — deleting
    # both leaves it green.
    writer, _ = _loaded_writer(rom_paths)
    assert writer.write_team_roster(TEAM_COUNT, _squad()) == -1
    assert writer.write_team_header(TEAM_COUNT, _squad()) is False


def test_the_team_header_records_forward_and_defence_counts(tmp_path):
    # The three ratings bytes ahead of the count byte are per-team values the writer
    # must preserve, so they are poisoned: against the fixture's zeros "still zero"
    # would also hold for a writer that scribbled zeros over all four.
    base = synthetic_rom.team_base(0)
    ratings = base + synthetic_rom.SEC_RATINGS
    source = _write_rom(tmp_path, "ratings.bin", _poisoned_rom((ratings, 3)))
    output = tmp_path / "out.bin"
    writer = NHL94GenesisRomWriter(str(source), str(output))
    assert writer.load() is True
    squad = _squad()
    written = writer.write_team_roster(0, squad)
    assert writer.write_team_header(0, squad, actual_count=written) is True
    assert writer.finalize() is True

    data = output.read_bytes()
    count_byte = data[ratings + 3]
    assert count_byte == 0x94
    assert count_byte >> 4 == 9  # forwards
    assert count_byte & 0x0F == 4  # defence
    assert data[ratings : ratings + 3] == bytes([POISON]) * 3


def test_the_goalie_count_comes_from_the_flag_and_the_defence_count_from_the_position(rom_paths):
    # `write_team_header` mixes conventions: `goalie_count` reads `is_goalie` while
    # `defense_count` reads `position`, and forwards are whatever is left over. Two
    # players flagged as goalies while listed at centre, and nobody at position "G",
    # is what tells them apart; reading the position would report 0x31.
    writer, output = _loaded_writer(rom_paths)
    roster = [
        NHL94GenPlayerRecord(name="FLAGGED0", position="C", jersey_number=1, is_goalie=True),
        NHL94GenPlayerRecord(name="FLAGGED1", position="C", jersey_number=2, is_goalie=True),
        NHL94GenPlayerRecord(name="BLUELINE", position="D", jersey_number=3),
        NHL94GenPlayerRecord(name="CENTREIC", position="C", jersey_number=4),
    ]
    assert writer.write_team_roster(0, roster) == 4
    assert writer.write_team_header(0, roster) is True
    assert writer.finalize() is True

    base = synthetic_rom.team_base(0)
    assert output.read_bytes()[base + synthetic_rom.SEC_RATINGS + 3] == 0x11
    # One forward at slot 2 and one defenceman at slot 3, so the two flagged
    # goalies did occupy slots 0 and 1 of the roster the lines index into.
    assert _line_data(output)[:8] == bytes([0x01, 3, 3, 2, 2, 2, 2, 0])


def test_a_roster_of_twenty_five_at_one_position_saturates_its_count_nibble(rom_paths):
    # `min(15, ...)` on both nibbles. Twenty-five 6-character records cost 400 of
    # the 452 bytes, so a whole one-position squad fits and the count reaches the
    # 0xF the nibble tops out at; a cap of 14 would report 0xE0 and 0x0E instead.
    source, _ = rom_paths
    for position, expected in (("C", 0xF0), ("D", 0x0F)):
        output = source.parent / f"out_{position}.bin"
        writer = NHL94GenesisRomWriter(str(source), str(output))
        assert writer.load() is True
        roster = [
            NHL94GenPlayerRecord(name=f"P{i:05d}", position=position, jersey_number=1)
            for i in range(25)
        ]
        assert writer.write_team_roster(0, roster) == 25
        assert writer.write_team_header(0, roster, actual_count=25) is True
        assert writer.finalize() is True
        base = synthetic_rom.team_base(0)
        assert output.read_bytes()[base + synthetic_rom.SEC_RATINGS + 3] == expected


def test_the_header_counts_only_the_players_that_actually_fit(rom_paths):
    # `actual_count` exists because `write_team_roster` may have dropped the tail of
    # the list; a header describing players not in the ROM would point the game's
    # line data at roster slots that do not exist.
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
    # goalies, then forwards 2-10, then defence 11-14.
    writer, output = _loaded_writer(rom_paths)
    squad = _squad()
    assert writer.write_team_roster(0, squad) == 15
    assert writer.write_team_header(0, squad, actual_count=len(squad)) is True
    assert writer.finalize() is True

    assert _line_data(output) == _line_bytes(
        [
            [0x01, 11, 12, 3, 2, 4, 5, 0],
            [0x01, 13, 14, 6, 5, 7, 8, 0],
            [0x01, 11, 12, 9, 8, 10, 10, 0],
            [0x01, 13, 14, 10, 10, 10, 2, 0],
            [0x01, 11, 12, 3, 2, 4, 6, 0],
            [0x01, 13, 14, 6, 5, 7, 9, 0],
            [0x01, 11, 12, 9, 8, 10, 2, 0],
            [0x01, 13, 14, 10, 10, 10, 5, 0],
        ]
    )


def test_a_roster_with_no_forwards_or_defence_points_every_slot_at_the_starter(rom_paths):
    # The `forward_count == 0` and `defense_count == 0` arms of `_generate_lines`.
    # They do not prevent an overrun — `min(i, forward_count - 1)` would return the
    # last goalie, still a real slot. What the arms decide is *which* slot: every
    # position on every line names the starting goalie at 0 rather than goalie 2.
    writer, output = _loaded_writer(rom_paths)
    goalies = [
        NHL94GenPlayerRecord(name=f"GOALIE{i}", position="G", jersey_number=30 + i, is_goalie=True)
        for i in range(3)
    ]
    assert writer.write_team_roster(0, goalies) == 3
    assert writer.write_team_header(0, goalies) is True
    assert writer.finalize() is True

    assert _line_data(output) == _line_bytes([[0x01, 0, 0, 0, 0, 0, 0, 0]] * 8)
    base = synthetic_rom.team_base(0)
    assert output.read_bytes()[base + synthetic_rom.SEC_RATINGS + 3] == 0x00


def test_a_single_defenceman_is_used_as_both_halves_of_every_pair(rom_paths):
    # The `min(i, defense_count - 1)` clamp inside `d()`. Every line asks for two
    # defence slots, so with one defenceman the second ask is out of range and only
    # the clamp keeps it from naming slot 6 on a six-player team. Four defencemen —
    # what `_squad` has — never reach it: the largest index requested is 3.
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

    # Slot 5 is the lone defenceman and appears as both LD and RD on all eight
    # lines; no byte names a slot past the end of a six-player roster.
    assert _line_data(output) == _line_bytes(
        [
            [0x01, 5, 5, 3, 2, 4, 4, 0],
            [0x01, 5, 5, 4, 4, 4, 4, 0],
            [0x01, 5, 5, 4, 4, 4, 4, 0],
            [0x01, 5, 5, 4, 4, 4, 2, 0],
            [0x01, 5, 5, 3, 2, 4, 4, 0],
            [0x01, 5, 5, 4, 4, 4, 4, 0],
            [0x01, 5, 5, 4, 4, 4, 2, 0],
            [0x01, 5, 5, 4, 4, 4, 4, 0],
        ]
    )


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


def test_disable_checksum_writes_an_rts_at_the_bypass_offset(tmp_path):
    # The bytes either side of the patch are poisoned: the image is already zero
    # there, so "the neighbours are still zero" would hold just as well for a writer
    # that had scribbled zeros over them.
    source = _write_rom(
        tmp_path,
        "bypass.bin",
        _poisoned_rom((CHECKSUM_BYPASS_OFFSET - 4, 4), (CHECKSUM_BYPASS_OFFSET + 2, 4)),
    )
    output = tmp_path / "out.bin"
    writer = NHL94GenesisRomWriter(str(source), str(output))
    assert writer.load() is True
    writer.disable_checksum()
    assert writer.finalize() is True

    data = output.read_bytes()
    assert data[CHECKSUM_BYPASS_OFFSET : CHECKSUM_BYPASS_OFFSET + 2] == b"\x4e\x75"
    # A word-aligned two-byte patch and nothing more.
    assert CHECKSUM_BYPASS_OFFSET % 2 == 0
    assert data[CHECKSUM_BYPASS_OFFSET - 4 : CHECKSUM_BYPASS_OFFSET] == bytes([POISON]) * 4
    assert data[CHECKSUM_BYPASS_OFFSET + 2 : CHECKSUM_BYPASS_OFFSET + 6] == bytes([POISON]) * 4


def test_the_bypass_is_written_only_when_both_its_bytes_fit(tmp_path):
    # Both sides of `CHECKSUM_BYPASS_OFFSET + 2 <= len(data)`. The offset sits
    # 1334 bytes from the end of a 1 MB image, so against the plain fixture the
    # guard never decides anything. At exactly `offset + 2` bytes the patch is the
    # last word in the file; one byte shorter and nothing may be written at all.
    rom = synthetic_rom.build_nhl94_genesis_rom()
    fits = _write_rom(tmp_path, "fits.bin", rom[: CHECKSUM_BYPASS_OFFSET + 2])
    short = _write_rom(tmp_path, "short.bin", rom[: CHECKSUM_BYPASS_OFFSET + 1])

    for source, name in ((fits, "fits_out.bin"), (short, "short_out.bin")):
        writer = NHL94GenesisRomWriter(str(source), str(tmp_path / name))
        assert writer.load() is True
        writer.disable_checksum()
        assert writer.finalize() is True

    assert (tmp_path / "fits_out.bin").read_bytes()[-2:] == b"\x4e\x75"
    # Compared against the input rather than against zeros: the image is unchanged
    # byte for byte, not merely still zero where the patch would have gone.
    assert (tmp_path / "short_out.bin").read_bytes() == short.read_bytes()


def test_an_odd_length_rom_folds_its_last_byte_in_as_a_high_byte(tmp_path):
    # The `else` arm of the checksum loop, which no whole-word image can reach:
    # every ROM in this file is an even number of bytes, so the final iteration
    # always has a second byte to read. 0x0203 bytes gives one whole word at
    # 0x200 and a lone byte at 0x202, which the sum takes as the high half of a
    # word: 0x1234 + 0xAB00 = 0xBD34. Dropping the arm leaves 0x1234 and reading
    # the byte unshifted leaves 0x12DF, so all three are distinguishable.
    rom = bytearray(0x203)
    rom[0x200], rom[0x201], rom[0x202] = 0x12, 0x34, 0xAB
    source = _write_rom(tmp_path, "odd.bin", rom)
    output = tmp_path / "odd_out.bin"
    writer = NHL94GenesisRomWriter(str(source), str(output))
    assert writer.load() is True
    writer.update_header_checksum()
    assert writer.finalize() is True

    assert output.read_bytes()[0x18E:0x190] == b"\xbd\x34"


def test_disable_checksum_before_loading_does_nothing(tmp_path):
    writer = NHL94GenesisRomWriter(str(tmp_path / "nope.bin"), str(tmp_path / "out.bin"))
    writer.disable_checksum()
    assert writer.data is None


def test_update_header_checksum_sums_every_word_from_0x200(tmp_path):
    # Both ends of the summed range are poisoned. The fixture leaves the word at
    # 0x200 and the last word of the image zero, and a zero word contributes nothing
    # to a sum, so a range starting two bytes late or stopping two bytes early would
    # produce an identical checksum.
    source = _write_rom(
        tmp_path,
        "edges.bin",
        _poisoned_rom((0x200, 2), (synthetic_rom.ROM_SIZE - 2, 2)),
    )
    output = tmp_path / "out.bin"
    writer = NHL94GenesisRomWriter(str(source), str(output))
    assert writer.load() is True
    writer.disable_checksum()
    writer.update_header_checksum()
    assert writer.finalize() is True

    # The fixture never writes the checksum field, so this is a value the writer
    # must have produced rather than one that was already there.
    assert struct.unpack_from(">H", source.read_bytes(), 0x18E)[0] == 0x0000
    data = output.read_bytes()
    expected = (
        sum(struct.unpack_from(">H", data, i)[0] for i in range(0x200, len(data), 2)) & 0xFFFF
    )
    assert struct.unpack_from(">H", data, 0x18E)[0] == expected
    # The field itself sits below 0x200 and so is not part of its own sum: running
    # the calculation twice is stable.
    writer.update_header_checksum()
    assert struct.unpack_from(">H", writer.data, 0x18E)[0] == expected


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


def test_a_negative_team_index_writes_a_roster_wherever_word_zero_points(tmp_path):
    # DEFECT: the guard is `team_index >= TEAM_COUNT` only, exactly as in the
    # reader. A negative index reads the word below the pointer table, which is zero
    # here, so the "team block" resolves to file offset 0 and its "player records"
    # pointer comes out of the first word of the image — on a real cartridge, the top
    # half of the initial supervisor stack pointer.
    #
    # That word is set to 8 and a length-2 terminator planted there, so the write
    # lands somewhere that was not already zero: the call reports 0 rather than the
    # documented -1, and the terminator is overwritten.
    rom = synthetic_rom.build_nhl94_genesis_rom()
    rom[0:2] = b"\x00\x08"
    rom[8:10] = b"\x00\x02"
    source = _write_rom(tmp_path, "negative.bin", rom)
    output = tmp_path / "out.bin"
    writer = NHL94GenesisRomWriter(str(source), str(output))
    assert writer.load() is True
    assert writer.reader.get_team_player_region(-1) == (8, 2)
    assert writer.write_team_roster(-1, _squad()) == 0
    assert writer.finalize() is True

    data = output.read_bytes()
    assert data[8:10] == b"\x00\x00"
    assert data[0:8] == b"\x00\x08\x00\x00\x00\x00\x00\x00"


def test_a_roster_region_that_runs_past_the_end_of_the_file_raises(tmp_path):
    # DEFECT: `get_team_player_region` reports a region two bytes past the image for
    # an unterminated roster, and `write_team_roster` trusts the number. The records
    # and the sentinel all land inside the file; it is the zero fill that walks off
    # the end, so the call raises IndexError rather than returning the documented -1.
    #
    # `_write_player_stats`'s own `offset + STATS_SIZE > len(self.data)` guard cannot
    # fire on an image shaped like this one: a record is written only while
    # `offset <= end - 13` and the name is truncated to `end - offset - 12`, so the
    # stats finish at or before `end - 2`. Only a corrupt pointer table can put
    # `start` within one record of the end, which is the test below.
    rom = synthetic_rom.build_nhl94_genesis_rom()
    start = synthetic_rom.team_base(0) + synthetic_rom.SEC_PLAYERS
    record = synthetic_rom.player_record(0, 0)
    repeats = (len(rom) - start) // len(record) + 1
    rom[start:] = (record * repeats)[: len(rom) - start]
    source = _write_rom(tmp_path, "unterminated.bin", rom)
    writer = NHL94GenesisRomWriter(str(source), str(tmp_path / "out.bin"))
    assert writer.load() is True
    assert writer.reader.get_team_player_region(0) == (start, 982530)
    assert start + 982530 == synthetic_rom.ROM_SIZE + 2
    with pytest.raises(IndexError):
        writer.write_team_roster(0, _squad())


def test_a_team_block_at_the_end_of_the_file_lets_stats_scribble_past_it(tmp_path):
    # DEFECT: `write_team_roster`'s second IndexError route, and the one that shows
    # `_write_player_stats`'s bounds guard is load-bearing. Team 0's block is moved to
    # 20 bytes from EOF with its players pointer at +10, so the region *starts* in
    # range, and the length word planted there says 100, so the reader reports a
    # 108-byte region finishing 98 bytes past the image.
    #
    # The first record's stats then begin at `len(data) - 2`, the guard fires, and
    # the *second* record's name write runs off the end. Drop the guard and the
    # jersey BCD and weight nibble land on the last two bytes of the ROM instead.
    # The partial write is already in `self.data`, so a caller that catches and
    # finalizes anyway ships the damaged image.
    rom = synthetic_rom.build_nhl94_genesis_rom()
    end_of_file = len(rom)
    base = end_of_file - 20
    struct.pack_into(">I", rom, synthetic_rom.POINTER_TABLE_OFFSET, base)
    struct.pack_into(">H", rom, base, 10)
    struct.pack_into(">H", rom, end_of_file - 10, 100)
    source = _write_rom(tmp_path, "edge.bin", rom)
    output = tmp_path / "out.bin"
    writer = NHL94GenesisRomWriter(str(source), str(output))
    assert writer.load() is True
    assert writer.reader.get_team_player_region(0) == (end_of_file - 10, 108)

    players = [
        NHL94GenPlayerRecord(name=name, jersey_number=7)
        for name in ("ABCDEF", "GHIJKL", "MNOPQR", "STUVWX")
    ]
    with pytest.raises(IndexError):
        writer.write_team_roster(0, players)
    assert writer.finalize() is True

    # Two zeros from before the region, the first record's length word, its six name
    # bytes, then the second record's length word in the last two bytes. The eight
    # stat bytes the first record should have had are absent: the guard returned the
    # offset unchanged, so the file ends on a length word rather than on a jersey and
    # a weight nibble.
    assert output.read_bytes()[-12:] == b"\x00\x00\x00\x08ABCDEF\x00\x08"


def test_a_negative_team_index_overwrites_the_start_of_the_rom_with_line_data(rom_paths):
    # DEFECT: the same missing guard in `write_team_header`. All six section offsets
    # resolve to 0, so the call reports success and writes the count byte, the goalie
    # byte and 64 bytes of line assignments over the vector table at the head of the
    # file.
    writer, output = _loaded_writer(rom_paths)
    assert writer.write_team_header(-1, _squad()) is True
    assert writer.finalize() is True
    assert output.read_bytes()[:8] == bytes([0x01, 11, 12, 3, 2, 4, 5, 0])


def test_the_writers_reader_never_observes_the_writers_own_patches(rom_paths):
    # DEFECT: `load` takes a *copy* of the reader's image, and every region lookup
    # goes back through that reader, so within one writer session the region for a
    # team is always the one in the input file. Writing the same team twice both
    # start from the original 452 bytes, which is accidental: dropping the
    # `bytearray(...)` copy so the two share one buffer would make the second call
    # see only what the first left, silently truncating the roster.
    writer, output = _loaded_writer(rom_paths)
    start = synthetic_rom.team_base(0) + synthetic_rom.SEC_PLAYERS
    full = [NHL94GenPlayerRecord(name=f"NAME{i:04d}", jersey_number=1) for i in range(25)]

    assert writer.reader.get_team_player_region(0) == (start, ROSTER_REGION)
    assert writer.write_team_roster(0, _squad()) == 15
    # After a write that shrank the roster to 270 bytes, the reader still says 452.
    assert writer.reader.get_team_player_region(0) == (start, ROSTER_REGION)
    assert writer.write_team_roster(0, full) == 25
    assert writer.finalize() is True

    names, _ = _read_back(output).read_team_roster(0)
    assert names == [f"NAME{i:04d}" for i in range(25)]


def test_patching_a_rom_that_was_already_patched_loses_roster_space(rom_paths):
    # DEFECT: the roster region is defined by where the sentinel currently sits, and
    # `write_team_roster` moves the sentinel down to the end of whatever it just
    # wrote. A short roster shrinks the region permanently, so re-patching the output
    # can no longer fit a roster the original ROM had room for. The remaining tail is
    # zero-filled and unreachable, not reclaimed.
    writer, output = _loaded_writer(rom_paths)
    start = synthetic_rom.team_base(0) + synthetic_rom.SEC_PLAYERS
    full = [NHL94GenPlayerRecord(name=f"NAME{i:04d}", jersey_number=1) for i in range(25)]

    # A fresh writer on the untouched ROM fits all twenty-five.
    assert writer.reader.get_team_player_region(0) == (start, ROSTER_REGION)
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
