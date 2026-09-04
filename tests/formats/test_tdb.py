"""TDB: the bit-packed record database, and the CRC chain that validates it.

The instrument here is not a transcribed offset table, because there are no
offsets to transcribe: a game addresses a TDB by field name. So the claims are

- `parse` reads back the exact values `tests.fixtures.synthetic_tdb.pack_bits`
  wrote, field by field, from an independent bit packer;
- `serialize(parse(b)) == b` for every file in `TDB_CORPUS`, whose CRC chain the
  fixture computed with an independent bitwise CRC; and
- the chain is checked link by link against that independent CRC, including the
  two links `serialize` deliberately never rewrites.

The second of those is the one that would go vacuous quietly. `serialize` starts
from the bytes `parse` was given, so it would be byte-identical for a great many
broken implementations — one that recomputed no CRC at all, for instance. What
makes it a real claim is that the fixture builds the chain itself: a file from
`build_tdb` is only stable under `serialize` if the module's chain arithmetic
agrees with the fixture's, link for link. `test_serialize_rewrites_the_chain_
after_a_write` closes the rest, by changing a record and requiring the CRCs to
move.
"""

import struct
import zlib

import pytest

from retro_roster_patcher.core.errors import RetroRosterError, RomError
from retro_roster_patcher.formats.ea_tdb import (
    TDB_MAGIC,
    TDB_TYPE_BINARY,
    TDB_TYPE_FLOAT,
    TDB_TYPE_SINT,
    TDB_TYPE_STRING,
    TDB_TYPE_UINT,
    EaTdbError,
    TDBField,
    TDBFile,
    TDBTable,
    _build_crc_table,
    tdb_crc,
)
from tests.fixtures.synthetic_tdb import (
    DIRECTORY_ENTRY_SIZE,
    DIRECTORY_START,
    PLAYER_FIELDS,
    PLAYER_RECORD_SIZE,
    TABLE_HEADER_SIZE,
    TYPE_UINT,
    FieldSpec,
    TableSpec,
    empty_table,
    mpeg2_crc,
    pack_bits,
    player_table,
    player_values,
)

# ──────────────────────────────────────────────────────────────
# The corpus
# ──────────────────────────────────────────────────────────────

TDB_CORPUS: dict[str, list[TableSpec]] = {
    # One table: the chain degenerates to the trailing four bytes alone, and
    # nothing is written into any table header.
    "single": [player_table("SPBT", 1, 8)],
    # Two: exactly one header link plus the trailing one.
    "pair": [player_table("SPBT", 1, 8), player_table("SPAI", 2, 5)],
    # Three, with a live count below capacity and a table with none live.
    "three": [
        player_table("SPBT", 1, 40, 30),
        player_table("SPAI", 2, 1),
        player_table("ROST", 3, 64, 0),
    ],
    # No tables at all: the chain loop runs zero times and the four trailing
    # bytes are left as the fixture wrote them.
    "no_tables": [],
    # A table with fields and no allocation. Its CRC window is the field
    # definitions alone.
    "empty_capacity": [empty_table("NONE", 4)],
    "empty_then_full": [empty_table("NONE", 4), player_table("ROST", 5, 3)],
    # A single record, where every count that could collapse does.
    "one_record": [player_table("ONEE", 6, 1)],
    # 64 fields, which is the widest layout any of the three games reads.
    "many_fields": [
        TableSpec(
            name="WIDE",
            fields=[FieldSpec(f"F{i:03d}", TYPE_UINT, i * 8, 8) for i in range(64)],
            record_size=64,
            capacity=4,
            num_records=4,
            records=bytes((i * 3 + 1) & 0xFF for i in range(4 * 64)),
        )
    ],
    # No fields at all: the CRC window is the record data alone.
    "no_fields": [
        TableSpec(
            name="BARE",
            fields=[],
            record_size=4,
            capacity=6,
            num_records=6,
            records=bytes(range(24)),
        )
    ],
    # A record size of one byte, so every offset multiplication degenerates.
    "byte_records": [
        TableSpec(
            name="TINY",
            fields=[FieldSpec("BITS", TYPE_UINT, 0, 8)],
            record_size=1,
            capacity=9,
            num_records=9,
            records=bytes(range(10, 19)),
        )
    ],
}


def _build(label: str) -> bytes:
    from tests.fixtures.synthetic_tdb import build_tdb

    return build_tdb(TDB_CORPUS[label])


def _block_bounds(specs: list[TableSpec]) -> list[tuple[int, int]]:
    """Where each table's block starts and ends, computed from the specs.

    Deliberately not from `TDBFile._header_offset`: the CRC assertions below
    would then be checking the module's arithmetic against itself.
    """
    start = DIRECTORY_START + DIRECTORY_ENTRY_SIZE * len(specs)
    bounds = []
    for spec in specs:
        bounds.append((start, start + spec.block_size()))
        start += spec.block_size()
    return bounds


def test_the_corpus_holds_ten_files_covering_the_degenerate_shapes():
    # Every parametrised test below runs once per entry, so an emptied corpus
    # would collect nothing and report green.
    assert len(TDB_CORPUS) == 10
    # And the shapes that collapse an arithmetic step are each present by name,
    # because a corpus of ten similar three-table files would satisfy the count.
    assert len(TDB_CORPUS["no_tables"]) == 0
    assert len(TDB_CORPUS["single"]) == 1
    assert len(TDB_CORPUS["three"]) == 3
    assert TDB_CORPUS["empty_capacity"][0].capacity == 0
    assert TDB_CORPUS["one_record"][0].capacity == 1
    assert TDB_CORPUS["no_fields"][0].fields == []
    assert len(TDB_CORPUS["many_fields"][0].fields) == 64
    assert TDB_CORPUS["byte_records"][0].record_size == 1
    assert TDB_CORPUS["three"][2].num_records == 0


def test_the_corpus_files_are_all_different():
    # Ten identical files would satisfy the count above and prove one case.
    assert len({_build(label) for label in TDB_CORPUS}) == 10


# ──────────────────────────────────────────────────────────────
# The CRC
# ──────────────────────────────────────────────────────────────


def test_the_crc_matches_the_published_check_value_for_crc32_mpeg2():
    # The one anchor in this file that comes from outside the project. CRC
    # catalogues give 0x0376E6E7 as CRC-32/MPEG-2 of b"123456789", so this says
    # the algorithm is that one and not merely self-consistent.
    assert tdb_crc(b"123456789") == 0x0376E6E7


def test_the_crc_of_nothing_is_the_initial_accumulator():
    assert tdb_crc(b"") == 0xFFFFFFFF


def test_it_is_not_the_zlib_crc32_someone_would_reach_for():
    # Reflected input and output and a final XOR: the same polynomial and a
    # different function. Worth pinning, because `zlib.crc32` is the obvious
    # substitution and it would leave every round-trip test in this file green
    # while producing a file the game rejects.
    assert tdb_crc(b"123456789") != zlib.crc32(b"123456789")


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x00",
        b"\xff",
        b"\x00" * 16,
        bytes(range(256)),
        b"SPBT" * 40,
        bytes(range(255, -1, -1)),
    ],
)
def test_the_table_driven_crc_agrees_with_the_bitwise_one(data):
    # Two implementations of the same function, one four bits at a time off a
    # 16-entry table and one a bit at a time off the polynomial.
    assert tdb_crc(data) == mpeg2_crc(data)


def test_the_crc_table_is_sixteen_nibble_entries():
    table = _build_crc_table()
    assert len(table) == 16
    assert table[0] == 0
    # Entry 1 is the polynomial itself: the accumulator starts at 0x80000000,
    # so the first shift XORs it in and nothing else has happened yet.
    assert table[1] == 0x04C11DB7
    # And the table is not a run of one value, which is what an XOR loop that
    # never fired would produce.
    assert len(set(table)) == 16


def test_the_crc_distinguishes_inputs_that_differ_in_one_bit():
    # Guards against an implementation that returns a constant, which would
    # satisfy the round trip for a file whose chain was built with it too.
    assert tdb_crc(b"\x00\x00\x00\x00") != tdb_crc(b"\x00\x00\x00\x01")


# ──────────────────────────────────────────────────────────────
# parse
# ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("data", [b"", b"DB", TDB_MAGIC, TDB_MAGIC + b"\x00" * 15])
def test_a_file_shorter_than_its_header_is_refused(data):
    with pytest.raises(EaTdbError, match="Not a TDB file"):
        TDBFile.parse(data)


@pytest.mark.parametrize("magic", [b"DB\x00\x07", b"db\x00\x08", b"BIGF"])
def test_a_file_without_the_magic_is_refused(magic):
    with pytest.raises(EaTdbError, match="Not a TDB file"):
        TDBFile.parse(magic + b"\x00" * 40)


def test_the_refusal_is_a_rom_error():
    assert issubclass(EaTdbError, RomError) is True
    assert issubclass(EaTdbError, RetroRosterError) is True


def test_parse_finds_every_table_in_directory_order():
    tdb = TDBFile.parse(_build("three"))
    assert list(tdb.tables) == ["SPBT", "SPAI", "ROST"]


def test_parse_reads_each_tables_counts_and_record_size():
    tdb = TDBFile.parse(_build("three"))
    assert [tdb.tables[n].capacity for n in ("SPBT", "SPAI", "ROST")] == [40, 1, 64]
    assert [tdb.tables[n].num_records for n in ("SPBT", "SPAI", "ROST")] == [30, 1, 0]
    assert tdb.tables["SPBT"].record_size == PLAYER_RECORD_SIZE


def test_parse_reads_every_field_definition():
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    assert [f.name for f in table.fields] == ["FNME", "INDX", "SACC", "TEAM", "WGHT"]
    assert [f.bit_offset for f in table.fields] == [0, 96, 112, 119, 123]
    assert [f.bit_width for f in table.fields] == [96, 16, 7, 4, 5]
    assert [f.field_type for f in table.fields] == [0, 3, 3, 3, 3]


def test_parse_preserves_the_header_words_nothing_reads():
    # `_header_unk` and `_padding` are stored and never consulted, which is
    # exactly why they need pinning: they are part of what `serialize` has to
    # carry across untouched.
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    assert table._header_unk == 0x11110001
    assert table._padding == 0x22220001


def test_the_first_tables_stored_crc_is_the_directorys_and_is_not_recomputed():
    table = TDBFile.parse(_build("three")).tables["SPBT"]
    assert table._header_crc == 0x1234ABCD


def test_parse_holds_records_as_a_mutable_buffer():
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    assert type(table._raw_data) is bytearray
    assert len(table._raw_data) == 8 * PLAYER_RECORD_SIZE


def test_a_table_whose_block_runs_off_the_end_is_skipped_and_the_rest_survive():
    # `_parse_table` answers None rather than raising, so a file cut after the
    # second table still yields the first two.
    raw = _build("three")
    bounds = _block_bounds(TDB_CORPUS["three"])
    truncated = raw[: bounds[2][0] + 10]
    assert list(TDBFile.parse(truncated).tables) == ["SPBT", "SPAI"]


def test_a_directory_entry_pointing_past_the_end_is_skipped():
    raw = bytearray(_build("pair"))
    # Point the second entry's relative offset a long way past the file.
    struct.pack_into("<I", raw, DIRECTORY_START + DIRECTORY_ENTRY_SIZE + 4, 0x00FFFFFF)
    assert list(TDBFile.parse(bytes(raw)).tables) == ["SPBT"]


def test_get_table_is_case_sensitive_and_answers_none_for_a_name_it_lacks():
    tdb = TDBFile.parse(_build("three"))
    assert tdb.get_table("SPBT") is not None
    assert tdb.get_table("spbt") is None
    assert tdb.get_table("ZZZZ") is None


# ──────────────────────────────────────────────────────────────
# read_record
# ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("index", range(8))
def test_read_record_returns_the_values_the_fixture_packed(index):
    # The strongest single claim in this file. `pack_bits` wrote these numbers
    # LSB-first the long way; `read_record` reads them back through a completely
    # different code path, and every value encodes both the table and the record
    # so a swap of either lands somewhere else.
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    assert table.read_record(index) == player_values(1, index)


def test_the_expected_values_are_not_all_the_same():
    # Without this, the eight assertions above could all be comparing zero with
    # zero. Five fields, eight records, and no two records alike.
    rows = [player_values(1, i) for i in range(8)]
    assert len({tuple(sorted(row.items())) for row in rows}) == 8
    assert len({row["SACC"] for row in rows}) == 8
    assert 0 not in {row["INDX"] for row in rows}


def test_a_field_straddling_a_byte_boundary_reads_correctly():
    # TEAM is four bits at offset 119, so it spans bits 7 of byte 14 and 0-2 of
    # byte 15. A reader that assumed byte alignment gets this one wrong and the
    # four byte-aligned fields right.
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    assert [table.read_record(i)["TEAM"] for i in range(8)] == [3, 4, 5, 6, 7, 8, 9, 10]


def test_a_field_narrower_than_a_byte_reads_only_its_own_bits():
    # SACC is 7 bits at offset 112 and TEAM's low bit is the eighth, so a reader
    # that took the whole byte would return SACC + 128 for half the records.
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    assert [table.read_record(i)["SACC"] for i in range(4)] == [13, 18, 23, 28]


def test_a_field_running_to_the_last_bit_of_the_record_reads_correctly():
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    assert [table.read_record(i)["WGHT"] for i in range(4)] == [1, 8, 15, 22]


def test_a_string_field_stops_at_its_first_nul():
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    assert table.read_record(3)["FNME"] == "T1R3"


def test_a_string_field_whose_first_byte_is_nul_reads_as_empty():
    # The boundary in `raw.find(b"\\x00")`: a field that begins with a NUL gives
    # index 0, and `>= 0` rather than `> 0` is what turns that into an empty
    # string instead of twelve bytes of NUL. Mutation testing found the
    # distinction unguarded.
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    table.write_record(0, {"FNME": ""})
    assert table.read_record(0)["FNME"] == ""


def test_a_string_field_that_fills_its_width_has_no_nul_to_stop_at():
    spec = TableSpec(
        name="STRS",
        fields=[FieldSpec("NAME", TDB_TYPE_STRING, 0, 32)],
        record_size=4,
        capacity=1,
        num_records=1,
        records=b"ABCD",
    )
    from tests.fixtures.synthetic_tdb import build_tdb

    table = TDBFile.parse(build_tdb([spec])).tables["STRS"]
    assert table.read_record(0)["NAME"] == "ABCD"


def test_a_string_field_holding_a_byte_outside_ascii_is_replaced_not_raised():
    spec = TableSpec(
        name="STRS",
        fields=[FieldSpec("NAME", TDB_TYPE_STRING, 0, 32)],
        record_size=4,
        capacity=1,
        num_records=1,
        records=b"A\xffB\x00",
    )
    from tests.fixtures.synthetic_tdb import build_tdb

    table = TDBFile.parse(build_tdb([spec])).tables["STRS"]
    assert table.read_record(0)["NAME"] == "A�B"


def test_records_past_the_live_count_are_still_readable():
    # `capacity` bounds reads, `num_records` bounds only the two search helpers.
    # A game that walked the allocation would see the previous roster's data.
    table = TDBFile.parse(_build("three")).tables["ROST"]
    assert table.num_records == 0
    assert table.read_record(5) == player_values(3, 5)


@pytest.mark.parametrize("index", [-1, 8, 9, 1000])
def test_reading_outside_the_allocation_raises_index_error(index):
    # A builtin on purpose: this is the caller asking for a record nobody
    # allocated, which says nothing about the user's disc.
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    with pytest.raises(IndexError, match="out of range"):
        table.read_record(index)


def test_the_index_error_is_not_a_library_error():
    # The other half of the error decision. `EaTdbError` means "the bytes are
    # not this format"; a bad index is not that, and typing it as a `RomError`
    # would make a caller's bug read as a complaint about the ROM.
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    with pytest.raises(IndexError) as caught:
        table.read_record(99)
    assert isinstance(caught.value, RetroRosterError) is False


def test_a_table_with_no_allocation_can_be_read_from_at_no_index():
    table = TDBFile.parse(_build("empty_capacity")).tables["NONE"]
    assert table.capacity == 0
    with pytest.raises(IndexError):
        table.read_record(0)


# ──────────────────────────────────────────────────────────────
# write_record
# ──────────────────────────────────────────────────────────────


def test_write_record_changes_only_the_fields_it_was_given():
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    before = table.read_record(2)
    table.write_record(2, {"SACC": 99})
    after = table.read_record(2)
    assert after["SACC"] == 99
    assert before["SACC"] != 99
    assert {k: v for k, v in after.items() if k != "SACC"} == {
        k: v for k, v in before.items() if k != "SACC"
    }


def test_write_record_changes_only_the_record_it_was_given():
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    table.write_record(2, {"SACC": 99, "FNME": "CHANGED"})
    assert table.read_record(1) == player_values(1, 1)
    assert table.read_record(3) == player_values(1, 3)


def test_a_key_naming_no_field_is_ignored():
    # The two NHL patchers hand one value dictionary to tables with different
    # layouts and rely on the extra keys being dropped rather than raising.
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    table.write_record(0, {"NOPE": 1, "SACC": 7})
    assert table.read_record(0)["SACC"] == 7
    assert "NOPE" not in table.read_record(0)


def test_a_value_too_wide_for_its_field_saturates_rather_than_wrapping():
    # SACC is 7 bits. Wrapping would give 200 & 127 == 72, which is a plausible
    # rating and therefore the worse failure; clamping gives the ceiling.
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    table.write_record(0, {"SACC": 200})
    assert table.read_record(0)["SACC"] == 127
    assert table.read_record(0)["SACC"] != 200 & 127


def test_a_negative_value_clamps_to_zero():
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    table.write_record(0, {"SACC": -5})
    assert table.read_record(0)["SACC"] == 0


def test_clamping_does_not_disturb_the_neighbouring_field():
    # The reason clamping is right rather than masking: SACC's 7 bits sit beside
    # TEAM's 4, and a write that spilled would silently move a player's team.
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    before_team = table.read_record(0)["TEAM"]
    table.write_record(0, {"SACC": 100000})
    assert table.read_record(0)["TEAM"] == before_team
    assert table.read_record(0)["SACC"] == 127


def test_a_string_longer_than_its_field_is_truncated():
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    table.write_record(0, {"FNME": "A" * 40})
    assert table.read_record(0)["FNME"] == "A" * 12


def test_an_over_long_string_does_not_run_into_the_records_that_follow():
    # Reading the field back is not enough, and mutation testing showed it:
    # dropping the `[:byte_len]` truncation still reads back twelve characters,
    # because the field is twelve bytes wide however many were written. What it
    # changes is everything after — 40 bytes from offset 0 of record 0 reach
    # into records 1 and 2 and silently rewrite two other players.
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    table.write_record(0, {"FNME": "A" * 40})
    assert table.read_record(1) == player_values(1, 1)
    assert table.read_record(2) == player_values(1, 2)
    assert table.read_record(0)["INDX"] == player_values(1, 0)["INDX"]


def test_a_string_shorter_than_its_field_is_nul_padded_over_what_was_there():
    # Not merely "reads back short": the bytes after it must be zeroed, or the
    # previous occupant's tail survives and the game renders it.
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    table.write_record(0, {"FNME": "LONGNAMEXXXX"})
    table.write_record(0, {"FNME": "AB"})
    assert table.read_record(0)["FNME"] == "AB"
    assert bytes(table._raw_data[0:12]) == b"AB" + b"\x00" * 10


def test_a_string_with_a_character_outside_ascii_is_replaced():
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    table.write_record(0, {"FNME": "Ovechkiná"})
    assert table.read_record(0)["FNME"] == "Ovechkin?"


def test_bytes_may_be_written_to_a_string_field():
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    table.write_record(0, {"FNME": b"RAW"})
    assert table.read_record(0)["FNME"] == "RAW"


@pytest.mark.parametrize("index", [-1, 8, 1000])
def test_writing_outside_the_allocation_raises_index_error(index):
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    with pytest.raises(IndexError, match="capacity="):
        table.write_record(index, {"SACC": 1})


def test_a_value_that_is_not_a_number_raises_type_error():
    # What `int()` would have raised, kept a builtin for the same reason
    # `IndexError` is: it is a bug in the caller, not a fact about the disc.
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    with pytest.raises(TypeError, match="takes an integer"):
        table.write_record(0, {"SACC": [1, 2]})


def test_a_float_is_truncated_toward_zero_the_way_int_would():
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    table.write_record(0, {"SACC": 7.9})
    assert table.read_record(0)["SACC"] == 7


# ──────────────────────────────────────────────────────────────
# find_record, find_records, allocate_record
# ──────────────────────────────────────────────────────────────


def test_find_record_returns_the_first_match():
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    table.write_record(2, {"TEAM": 15})
    table.write_record(5, {"TEAM": 15})
    assert table.find_record("TEAM", 15) == 2


def test_find_record_answers_minus_one_when_nothing_matches():
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    assert table.find_record("TEAM", 99) == -1


def test_find_records_returns_every_match_in_order():
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    for i in (1, 4, 6):
        table.write_record(i, {"TEAM": 15})
    assert table.find_records("TEAM", 15) == [1, 4, 6]


def test_find_records_is_empty_when_nothing_matches():
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    assert table.find_records("TEAM", 99) == []


def test_both_searches_stop_at_the_live_count_not_the_allocation():
    # 40 allocated, 30 live. A match written at 35 is in the buffer, is readable
    # by index, and must not be found: it belongs to whatever roster was there
    # before, and a patcher that assigned a player to it would write past the
    # end of what the game reads.
    table = TDBFile.parse(_build("three")).tables["SPBT"]
    assert table.capacity == 40
    assert table.num_records == 30
    # INDX rather than TEAM: TEAM is four bits wide, so all sixteen of its
    # values already occur naturally among 40 records and the search would find
    # them too. 60000 occurs nowhere.
    table.write_record(35, {"INDX": 60000})
    table.write_record(12, {"INDX": 60000})
    assert table.read_record(35)["INDX"] == 60000
    assert table.find_records("INDX", 60000) == [12]
    assert table.find_record("INDX", 60000) == 12


def test_a_search_on_a_field_the_table_does_not_have_finds_nothing():
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    assert table.find_record("ZZZZ", 0) == -1
    assert table.find_records("ZZZZ", 0) == []


def test_allocate_record_hands_out_the_next_index_and_moves_the_live_count():
    table = TDBFile.parse(_build("three")).tables["SPBT"]
    assert table.allocate_record() == 30
    assert table.num_records == 31
    assert table.allocate_record() == 31
    assert table.num_records == 32


def test_allocate_record_refuses_once_the_allocation_is_full():
    table = TDBFile.parse(_build("single")).tables["SPBT"]
    assert table.num_records == 8
    assert table.capacity == 8
    assert table.allocate_record() == -1
    assert table.num_records == 8


def test_allocate_record_refuses_on_a_table_with_no_allocation():
    table = TDBFile.parse(_build("empty_capacity")).tables["NONE"]
    assert table.allocate_record() == -1


# ──────────────────────────────────────────────────────────────
# serialize
# ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("label", sorted(TDB_CORPUS))
def test_serialize_of_a_freshly_parsed_file_is_the_same_bytes(label):
    raw = _build(label)
    assert TDBFile.parse(raw).serialize() == raw


@pytest.mark.parametrize("label", sorted(TDB_CORPUS))
def test_parsing_the_serialized_file_gives_the_same_tables(label):
    raw = _build(label)
    first = TDBFile.parse(raw)
    second = TDBFile.parse(first.serialize())
    assert list(second.tables) == list(first.tables)
    assert [second.tables[n] for n in second.tables] == [first.tables[n] for n in first.tables]


def test_the_chain_link_in_each_header_is_the_previous_tables_crc():
    # Computed with the fixture's bitwise CRC over bounds derived from the
    # specs, so neither side of this comparison comes from the module.
    specs = TDB_CORPUS["three"]
    raw = _build("three")
    bounds = _block_bounds(specs)
    tables = list(TDBFile.parse(raw).tables.values())
    for i in (1, 2):
        start, end = bounds[i - 1]
        expected = mpeg2_crc(raw[start + TABLE_HEADER_SIZE : end])
        assert tables[i]._header_crc == expected


def test_the_last_four_bytes_hold_the_last_tables_crc():
    specs = TDB_CORPUS["three"]
    raw = _build("three")
    start, end = _block_bounds(specs)[-1]
    expected = mpeg2_crc(raw[start + TABLE_HEADER_SIZE : end])
    assert struct.unpack_from("<I", raw, len(raw) - 4)[0] == expected


def test_the_three_chain_links_are_three_different_numbers():
    # A chain of one repeated value would satisfy both tests above if the CRC
    # ignored its input.
    raw = _build("three")
    tables = list(TDBFile.parse(raw).tables.values())
    eof = struct.unpack_from("<I", raw, len(raw) - 4)[0]
    assert len({tables[1]._header_crc, tables[2]._header_crc, eof}) == 3


def test_serialize_rewrites_the_chain_after_a_write():
    # The assertion that stops `serialize(parse(b)) == b` from being vacuous. If
    # `serialize` recomputed nothing, it would still return the original bytes
    # for an unmodified file and would leave a stale CRC here.
    raw = _build("three")
    tdb = TDBFile.parse(raw)
    tdb.get_table("SPBT").write_record(0, {"SACC": 111})
    out = tdb.serialize()
    assert out != raw
    assert len(out) == len(raw)
    specs = TDB_CORPUS["three"]
    start, end = _block_bounds(specs)[0]
    expected = mpeg2_crc(out[start + TABLE_HEADER_SIZE : end])
    # The first table's CRC lives in the SECOND table's header.
    second_start = _block_bounds(specs)[1][0]
    assert struct.unpack_from("<I", out, second_start)[0] == expected
    assert (
        struct.unpack_from("<I", out, second_start)[0]
        != struct.unpack_from("<I", raw, second_start)[0]
    )


def test_a_write_to_the_last_table_moves_the_trailing_crc():
    raw = _build("three")
    tdb = TDBFile.parse(raw)
    tdb.get_table("ROST").write_record(0, {"SACC": 5})
    out = tdb.serialize()
    start, end = _block_bounds(TDB_CORPUS["three"])[-1]
    assert struct.unpack_from("<I", out, len(out) - 4)[0] == mpeg2_crc(
        out[start + TABLE_HEADER_SIZE : end]
    )
    assert out[-4:] != raw[-4:]


def test_a_single_table_file_writes_only_the_trailing_crc():
    # The degenerate chain. With one table there is no next header to write, and
    # the one header there is keeps the directory's CRC untouched.
    raw = _build("single")
    tdb = TDBFile.parse(raw)
    tdb.get_table("SPBT").write_record(0, {"SACC": 3})
    out = tdb.serialize()
    header_start = _block_bounds(TDB_CORPUS["single"])[0][0]
    assert struct.unpack_from("<I", out, header_start)[0] == 0x1234ABCD
    assert out[-4:] != raw[-4:]


def test_the_directory_hash_and_the_first_link_survive_serialization():
    raw = _build("three")
    tdb = TDBFile.parse(raw)
    tdb.get_table("SPAI").write_record(0, {"SACC": 60})
    out = tdb.serialize()
    assert struct.unpack_from("<I", out, 20)[0] == 0xDEADBEEF
    first_header = _block_bounds(TDB_CORPUS["three"])[0][0]
    assert struct.unpack_from("<I", out, first_header)[0] == 0x1234ABCD


def test_serialize_writes_the_live_count_back_into_the_header():
    raw = _build("three")
    tdb = TDBFile.parse(raw)
    tdb.get_table("SPBT").allocate_record()
    out = tdb.serialize()
    header_start = _block_bounds(TDB_CORPUS["three"])[0][0]
    assert struct.unpack_from("<H", out, header_start + 22)[0] == 31
    assert struct.unpack_from("<H", raw, header_start + 22)[0] == 30
    # The allocation itself is written back unchanged beside it.
    assert struct.unpack_from("<H", out, header_start + 20)[0] == 40


def test_serialize_carries_the_record_bytes_of_every_table_across():
    raw = _build("three")
    tdb = TDBFile.parse(raw)
    tdb.get_table("SPAI").write_record(0, {"FNME": "MOVED"})
    out = TDBFile.parse(tdb.serialize())
    assert out.get_table("SPAI").read_record(0)["FNME"] == "MOVED"
    assert out.get_table("SPBT").read_record(7) == player_values(1, 7)
    assert out.get_table("ROST").read_record(63) == player_values(3, 63)


def test_a_file_with_no_tables_serializes_unchanged():
    raw = _build("no_tables")
    assert TDBFile.parse(raw).serialize() == raw
    assert len(raw) == DIRECTORY_START + 4


# ──────────────────────────────────────────────────────────────
# Inherited hazards, pinned rather than fixed
# ──────────────────────────────────────────────────────────────


def test_a_live_count_above_the_allocation_parses_and_then_raises_on_read():
    # Nothing validates `currentRecords <= maxRecords`. The file parses, and a
    # caller looping `range(table.num_records)` — which is what the source's
    # readers do — is handed an `IndexError` from the file's own contents.
    #
    # Not clamped here: clamping would make `serialize` write back a count it
    # did not read and break the round trip above. Not raised at parse either:
    # that refuses a whole disc over one header word. The game's reader is where
    # the loop gets bounded, and this test is what says so.
    raw = bytearray(_build("single"))
    header_start = _block_bounds(TDB_CORPUS["single"])[0][0]
    struct.pack_into("<H", raw, header_start + 22, 60000)
    table = TDBFile.parse(bytes(raw)).tables["SPBT"]
    assert table.num_records == 60000
    assert table.capacity == 8
    with pytest.raises(IndexError):
        table.find_record("TEAM", 999)


def test_two_tables_sharing_a_name_keep_the_later_and_walk_the_chain_twice():
    # `tables` is keyed by name so the second wins, while `_table_order` lists
    # the name twice, so `serialize` walks that one table twice and computes one
    # link of the chain from the wrong table. No EA file is known to do it;
    # pinned so that fixing it is a deliberate act.
    from tests.fixtures.synthetic_tdb import build_tdb

    raw = build_tdb([player_table("DUPE", 1, 4), player_table("DUPE", 2, 4)])
    tdb = TDBFile.parse(raw)
    assert list(tdb.tables) == ["DUPE"]
    assert tdb._table_order == ["DUPE", "DUPE"]
    assert tdb.tables["DUPE"].read_record(0) == player_values(2, 0)
    # And the round trip fails, which is how a caller would find out.
    assert tdb.serialize() != raw


def test_the_field_type_codes_are_the_numbers_the_format_uses():
    assert (TDB_TYPE_STRING, TDB_TYPE_BINARY, TDB_TYPE_SINT, TDB_TYPE_UINT, TDB_TYPE_FLOAT) == (
        0,
        1,
        2,
        3,
        4,
    )


@pytest.mark.parametrize(
    ("code", "is_string", "is_int"),
    [
        (TDB_TYPE_STRING, True, False),
        (TDB_TYPE_BINARY, False, False),
        (TDB_TYPE_SINT, False, True),
        (TDB_TYPE_UINT, False, True),
        (TDB_TYPE_FLOAT, False, False),
    ],
)
def test_the_field_type_predicates_answer_for_every_code(code, is_string, is_int):
    f = TDBField(name="TEST", field_type=code, bit_offset=0, bit_width=8)
    assert f.is_string is is_string
    assert f.is_int is is_int


def test_a_field_of_an_unknown_type_is_read_as_an_integer():
    # `read_record` branches on `is_string` alone, so binary and float fields —
    # neither of which any of the three games declares — come back as the
    # unsigned integer their bits spell.
    spec = TableSpec(
        name="ODDT",
        fields=[FieldSpec("BINF", TDB_TYPE_BINARY, 0, 16)],
        record_size=2,
        capacity=1,
        num_records=1,
        records=b"\x34\x12",
    )
    from tests.fixtures.synthetic_tdb import build_tdb

    table = TDBFile.parse(build_tdb([spec])).tables["ODDT"]
    assert table.read_record(0)["BINF"] == 0x1234


def test_a_hand_built_table_coerces_its_record_buffer_to_a_bytearray():
    # `__post_init__` is what makes the `bytearray` annotation true for a table
    # nobody has written to. Without it the first write on a table built from
    # `bytes` would raise.
    table = TDBTable(
        name="HAND",
        name_hash=0,
        fields=[
            TDBField(
                name=f.name,
                field_type=f.field_type,
                bit_offset=f.bit_offset,
                bit_width=f.bit_width,
            )
            for f in PLAYER_FIELDS
        ],
        record_size=PLAYER_RECORD_SIZE,
        capacity=1,
        num_records=1,
        _raw_data=pack_bits(PLAYER_FIELDS, player_values(9, 0), PLAYER_RECORD_SIZE),
    )
    assert type(table._raw_data) is bytearray
    table.write_record(0, {"SACC": 42})
    assert table.read_record(0)["SACC"] == 42
