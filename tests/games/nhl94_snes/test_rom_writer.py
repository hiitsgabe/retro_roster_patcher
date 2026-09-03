"""Writer coverage against `tests/fixtures/synthetic_snes_rom.py`.

The writer patches in place: it measures the region the ROM's existing records
occupy and writes new ones inside it, truncating names and dropping whatever
does not fit. So every test here builds an image whose region size it knows, and
reads the result back with the reader rather than with a private decoder --
except for the stat bytes, which are decoded by
`synthetic_snes_rom.decode_player_stats`, transcribed from the writer's own
comments rather than from its code.

`test_the_number_written_is_not_the_number_asked_for` is the one this file
exists for. Upstream returned `bool` here and its caller reported
`len(players)`, and the two coincide for any roster that happens to fit -- which
is exactly the fixture size a test would reach for first.
"""

import pytest

from retro_roster_patcher.games.nhl94_snes.models import (
    TEAM_COUNT,
    NHL94PlayerAttributes,
    NHL94PlayerRecord,
)
from retro_roster_patcher.games.nhl94_snes.rom_reader import NHL94SNESRomReader
from retro_roster_patcher.games.nhl94_snes.rom_writer import (
    LINE_COUNT,
    LINE_SLOTS,
    NHL94SNESRomWriter,
    encode_nibble,
    encode_weight_nibble,
)
from tests.fixtures import synthetic_snes_rom as fixture

# Both read from the fixture, which transcribes the team block layout
# independently, and never from `rom_writer`. Importing the writer's own
# `LINE_ASSIGN_OFFSET` to say where the writer put the line table is a
# tautology: move the constant and the assertion moves with it.
PLAYER_COUNT_BYTE = fixture.PLAYER_COUNT_OFFSET
LINE_TABLE_BYTE = fixture.LINE_ASSIGN_OFFSET

#: Bytes one `_records()` entry occupies: a 2-byte length word, a 15-character
#: name and 8 stat bytes.
RECORD_SIZE = 2 + 15 + 8


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


# -- nibble encoding -------------------------------------------------------


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


# -- loading ---------------------------------------------------------------


def test_a_writer_over_a_missing_file_fails_to_load(tmp_path):
    writer = NHL94SNESRomWriter(str(tmp_path / "absent.sfc"), str(tmp_path / "out.sfc"))
    assert writer.load() is False
    assert writer.data is None


def test_loading_copies_the_image_rather_than_aliasing_the_readers(tmp_path):
    writer = _writer(tmp_path)
    assert writer.data is not writer.reader.data
    assert writer.data == writer.reader.data


# -- the region ------------------------------------------------------------


def test_the_region_runs_from_the_first_record_to_the_end_of_the_terminator(tmp_path):
    writer = _writer(tmp_path)
    start, size = writer._get_team_player_region(6)
    assert start == fixture.team_base(6) + fixture.TEAM_HEADER_SIZE
    assert size == fixture.roster_region_size(fixture.ROSTER_PLAYERS)


def test_a_shorter_existing_roster_gives_a_smaller_region(tmp_path):
    writer = _writer(tmp_path, players_per_team=10)
    _, size = writer._get_team_player_region(6)
    assert size == fixture.roster_region_size(10)


def test_a_region_past_the_last_team_is_empty(tmp_path):
    assert _writer(tmp_path)._get_team_player_region(TEAM_COUNT) == (0, 0)


# -- writing a roster ------------------------------------------------------


def test_a_roster_that_fits_is_written_whole(tmp_path):
    writer = _writer(tmp_path)
    written = writer.write_team_roster(5, _records(10))
    assert written == 10


def test_the_written_names_read_back_in_order(tmp_path):
    writer = _writer(tmp_path)
    writer.write_team_roster(5, _records(10))
    assert writer.finalize() is True
    names, _ = _finalized_reader(writer).read_team_roster(5)
    assert names == [f"Fourteen Char{i:02d}" for i in range(10)]


def test_the_written_stat_bytes_decode_to_the_records_attributes(tmp_path):
    writer = _writer(tmp_path)
    records = _records(10)
    writer.write_team_roster(5, records)
    assert writer.finalize() is True
    _, stats = _finalized_reader(writer).read_team_roster(5)
    decoded = fixture.decode_player_stats(stats[7])
    source = records[7]
    assert decoded["jersey_number"] == source.jersey_number
    assert decoded["weight_class"] == source.weight_class
    assert decoded["handedness"] == source.handedness
    assert decoded["speed"] == source.attributes.speed
    assert decoded["agility"] == source.attributes.agility
    assert decoded["off_awareness"] == source.attributes.off_awareness
    assert decoded["def_awareness"] == source.attributes.def_awareness
    assert decoded["shot_power"] == source.attributes.shot_power
    assert decoded["shot_accuracy"] == source.attributes.shot_accuracy
    assert decoded["stick_handling"] == source.attributes.stick_handling
    assert decoded["checking"] == source.attributes.checking
    assert decoded["endurance"] == source.attributes.endurance
    assert decoded["roughness"] == source.attributes.roughness
    assert decoded["pass_accuracy"] == source.attributes.pass_accuracy
    assert decoded["aggression"] == source.attributes.aggression


def test_every_records_stat_bytes_are_its_own(tmp_path):
    """Rules out a writer that wrote one record's stats into every slot."""
    writer = _writer(tmp_path)
    records = _records(10)
    writer.write_team_roster(5, records)
    assert writer.finalize() is True
    _, stats = _finalized_reader(writer).read_team_roster(5)
    jerseys = [fixture.decode_player_stats(block)["jersey_number"] for block in stats]
    assert jerseys == [record.jersey_number for record in records]


def test_the_length_word_is_little_endian(tmp_path):
    """The single byte that tells this format from its Genesis sibling's.

    A 260-byte name is unreachable, so the two encodings differ only in which
    byte holds the low half. `0x10 0x00` is 16 little-endian and 4096
    big-endian, and 4096 is past the reader's ceiling of 40, so a big-endian
    writer would produce a roster the reader cannot see at all.
    """
    writer = _writer(tmp_path)
    start, _ = writer._get_team_player_region(5)
    writer.write_team_roster(5, _records(1, name="Fourteen Char"))
    data = writer.data
    assert data is not None
    assert data[start] == 17  # 15 name bytes + 2
    assert data[start + 1] == 0


def test_a_roster_is_terminated_with_an_empty_string(tmp_path):
    writer = _writer(tmp_path)
    start, _ = writer._get_team_player_region(5)
    written = writer.write_team_roster(5, _records(10))
    data = writer.data
    assert data is not None
    end_of_records = start + written * RECORD_SIZE
    assert data[end_of_records] == 0x02
    assert data[end_of_records + 1] == 0x00


def test_the_rest_of_the_region_is_zero_filled(tmp_path):
    writer = _writer(tmp_path)
    start, size = writer._get_team_player_region(5)
    written = writer.write_team_roster(5, _records(10))
    data = writer.data
    assert data is not None
    tail_start = start + written * RECORD_SIZE + 2
    assert data[tail_start : start + size] == bytes(start + size - tail_start)
    # 416 - (10 * 25 + 2). The tail is not empty, so the assertion above is not
    # vacuous -- a writer that zero-filled nothing would pass an empty slice.
    assert start + size - tail_start == 164


def test_writing_one_team_leaves_its_neighbours_untouched(tmp_path):
    writer = _writer(tmp_path)
    before = bytes(writer.reader.data or b"")
    # Measured before the write: afterwards the scan walks the records this
    # writer just laid down, which are a different length.
    start, size = writer._get_team_player_region(5)
    writer.write_team_roster(5, _records(10))
    after = bytes(writer.data or b"")
    assert after[:start] == before[:start]
    assert after[start + size :] == before[start + size :]


# -- the count, which is the point ----------------------------------------


def test_the_number_written_is_not_the_number_asked_for(tmp_path):
    """The regression this port exists to fix, on a fixture where they differ.

    The image holds 23 records of an 8-byte name, so each team's region is
    23 * 18 + 2 = 416 bytes. A record with a 13-byte name costs 23, and the
    writer stops while fewer than 13 bytes remain (2 length + 1 name + 8 stats +
    2 terminator), so 18 fit and 5 of the 23 are dropped. Upstream returned True
    here and its caller counted 23.
    """
    writer = _writer(tmp_path)
    written = writer.write_team_roster(5, _records(23, name="Fourteen Ch"))
    assert written == 18
    assert writer.finalize() is True
    names, _ = _finalized_reader(writer).read_team_roster(5)
    assert len(names) == 18
    assert names[-1] == "Fourteen Ch17"


def test_a_region_too_small_for_even_one_record_writes_none(tmp_path):
    # A ROM whose teams hold no players at all: the region is the terminator
    # and nothing else, so the first record has 2 - 12 bytes of room for a name.
    writer = _writer(tmp_path, players_per_team=0)
    _, size = writer._get_team_player_region(5)
    assert size == 2
    assert writer.write_team_roster(5, _records(5)) == 0


def test_a_team_index_past_the_last_slot_is_an_error(tmp_path):
    assert _writer(tmp_path).write_team_roster(TEAM_COUNT, _records(1)) == -1


def test_a_team_whose_region_cannot_be_measured_is_an_error(tmp_path):
    """`_get_team_player_region` answers `(0, 0)` when the pointer will not read.

    Built by truncating the image below the pointer table rather than by
    poisoning a pointer, because the pointer's own bank is hardcoded and cannot
    be made to miss.
    """
    rom = fixture.build_nhl94_snes_rom()
    short = tmp_path / "short.sfc"
    short.write_bytes(bytes(rom[: fixture.POINTER_TABLE_OFFSET]))
    writer = NHL94SNESRomWriter(str(short), str(tmp_path / "out.sfc"))
    assert writer.load() is True
    assert writer.write_team_roster(0, _records(1)) == -1


def test_an_empty_roster_erases_the_region_and_reports_nothing_written(tmp_path):
    """Pins the erasure `patch` exists to keep an empty list away from."""
    writer = _writer(tmp_path)
    assert writer.write_team_roster(5, []) == 0
    assert writer.finalize() is True
    assert _finalized_reader(writer).read_team_roster(5) == ([], [])


# -- names -----------------------------------------------------------------


def test_a_name_too_long_for_the_remaining_space_is_truncated(tmp_path):
    """The last record to fit gets whatever bytes are left, not a whole name."""
    writer = _writer(tmp_path, players_per_team=2)
    # 2 * 18 + 2 = 38 bytes. The first record takes 2 + 13 + 8 = 23, leaving 15,
    # so the second gets 15 - 12 = 3 name bytes and the other two do not fit.
    _, size = writer._get_team_player_region(5)
    assert size == 38
    assert writer.write_team_roster(5, _records(4, name="Fourteen Ch")) == 2
    assert writer.finalize() is True
    names, _ = _finalized_reader(writer).read_team_roster(5)
    assert names == ["Fourteen Ch00", "Fou"]


def test_an_empty_name_is_written_as_a_placeholder_byte(tmp_path):
    """DELIBERATE DIVERGENCE: upstream wrote a length word of 2 here.

    Both `read_team_roster` and `_get_team_player_region` stop below 3, so the
    record would have been the end of the roster and every player after it
    invisible -- while the writer still counted them. One `?` keeps the chain
    intact. Two records are written and two are read back; upstream's answer to
    the same input was two written and none read back.
    """
    writer = _writer(tmp_path)
    records = _records(2)
    records[0].name = ""
    assert writer.write_team_roster(5, records) == 2
    assert writer.finalize() is True
    names, _ = _finalized_reader(writer).read_team_roster(5)
    assert names == ["?", "Fourteen Char01"]


def test_a_non_ascii_name_is_replaced_rather_than_raising(tmp_path):
    writer = _writer(tmp_path)
    records = _records(1)
    records[0].name = "Grégoire"
    assert writer.write_team_roster(5, records) == 1
    assert writer.finalize() is True
    names, _ = _finalized_reader(writer).read_team_roster(5)
    assert names == ["Gr?goire"]


# -- the team header -------------------------------------------------------


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


def test_the_header_write_fills_exactly_fifty_six_line_bytes(tmp_path):
    writer = _writer(tmp_path)
    before = bytes(writer.reader.data or b"")
    writer.write_team_header(5, 14, 7)
    after = bytes(writer.data or b"")
    base = fixture.team_base(5)
    changed = [i for i in range(base, base + fixture.TEAM_HEADER_SIZE) if before[i] != after[i]]
    lines = list(range(base + LINE_TABLE_BYTE, base + LINE_TABLE_BYTE + 56))
    # The count byte and the line table, and nothing else in the header --
    # byte 18's team overall in particular is left alone.
    assert changed == [base + PLAYER_COUNT_BYTE, *lines]
    assert LINE_COUNT * LINE_SLOTS == 56


def test_the_eight_lines_index_goalies_forwards_and_defencemen_by_position(tmp_path):
    """The whole table, against the layout the writer's comments describe.

    Forwards start at 2 and defencemen at 2 + forwards, so a header written for
    a different forward count moves every defenceman index. That is what makes
    `patch` reading the count off the mapped record rather than off the ROM load
    bearing.
    """
    writer = _writer(tmp_path)
    writer.write_team_header(5, 9, 4)
    data = writer.data
    assert data is not None
    base = fixture.team_base(5) + LINE_TABLE_BYTE
    slots = fixture.LINE_SLOTS
    table = [list(data[base + i * slots : base + (i + 1) * slots]) for i in range(8)]
    # Forwards 2..10, defencemen 11..14, and `di`/`fi` clamp to the last one.
    assert table == [
        [0, 11, 12, 2, 3, 4, 2],  # SC1
        [0, 13, 14, 5, 6, 7, 5],  # SC2
        [0, 14, 14, 8, 9, 10, 8],  # CHK, both defence indices clamped
        [0, 11, 12, 2, 3, 4, 5],  # PP1
        [0, 13, 14, 5, 6, 7, 8],  # PP2
        [0, 11, 12, 8, 9, 10, 8],  # PK1
        [0, 13, 14, 5, 6, 7, 5],  # PK2
        [1, 11, 12, 2, 3, 4, 5],  # EA, the only line on the backup goalie
    ]


def test_a_header_write_past_the_last_team_is_refused(tmp_path):
    assert _writer(tmp_path).write_team_header(TEAM_COUNT, 14, 7) is False


def test_a_header_write_with_no_pointer_to_read_is_refused(tmp_path):
    rom = fixture.build_nhl94_snes_rom()
    short = tmp_path / "short.sfc"
    short.write_bytes(bytes(rom[: fixture.POINTER_TABLE_OFFSET]))
    writer = NHL94SNESRomWriter(str(short), str(tmp_path / "out.sfc"))
    assert writer.load() is True
    assert writer.write_team_header(0, 14, 7) is False


# -- finalize --------------------------------------------------------------


def test_finalize_writes_the_buffer_to_the_output_path(tmp_path):
    writer = _writer(tmp_path)
    writer.write_team_roster(5, _records(10))
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
