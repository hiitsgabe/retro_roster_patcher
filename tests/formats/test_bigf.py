"""BIGF: the archive `db.viv` is, read against a directory this module wrote.

Every archive here comes from `tests.fixtures.synthetic_tdb.build_bigf`, which
lays one out by hand rather than calling `ea_tdb.bigf_build`. That is the whole
reason it exists: a parser checked only against its own writer agrees with any
layout the pair happen to share, including a wrong one. `bigf_build` is checked
separately, at the bottom, and then the two are checked against each other.

The three files in `ARCHIVE` have deliberately different, non-zero, mutually
distinguishable contents. Uniform filler would let a reader that returned the
wrong entry, or the right entry at the wrong offset, satisfy every equality.
"""

import struct

import pytest

from retro_roster_patcher.core.errors import RetroRosterError, RomError
from retro_roster_patcher.formats.ea_tdb import (
    BigfEntry,
    EaTdbError,
    bigf_build,
    bigf_extract,
    bigf_parse,
    bigf_replace,
    bigf_replace_inplace,
)
from tests.fixtures.synthetic_tdb import BigfSpec, build_bigf

# Three files, three different lengths, three different byte patterns, and one
# name spelled in upper case so the case-folding claims have something to bite
# on. 300 bytes forces the second file past a 128-byte boundary.
FILES = [
    ("nhl2007.tdb", bytes((i * 7 + 1) & 0xFF for i in range(300))),
    ("NHLROST.TDB", bytes((i * 11 + 5) & 0xFF for i in range(17))),
    ("nhlbioatt.tdb", bytes((i * 13 + 3) & 0xFF for i in range(129))),
]
ARCHIVE = build_bigf(BigfSpec(files=FILES))


def test_the_fixture_archive_is_not_degenerate():
    # Everything below is a claim about telling three files apart. One file, or
    # three identical ones, would make most of them vacuous.
    assert len(FILES) == 3
    assert len({name for name, _ in FILES}) == 3
    assert len({data for _, data in FILES}) == 3
    assert [len(data) for _, data in FILES] == [300, 17, 129]


def test_parse_returns_one_entry_per_file_in_directory_order():
    entries = bigf_parse(ARCHIVE)
    assert [entry.name for entry in entries] == ["nhl2007.tdb", "NHLROST.TDB", "nhlbioatt.tdb"]


def test_parse_reports_each_files_real_size():
    entries = bigf_parse(ARCHIVE)
    assert [entry.size for entry in entries] == [300, 17, 129]


def test_each_reported_offset_is_where_that_files_bytes_actually_are():
    # The offsets are what everything else in this module indexes with, so they
    # are checked against the content rather than against a number typed twice.
    entries = bigf_parse(ARCHIVE)
    for entry, (_, data) in zip(entries, FILES, strict=True):
        assert ARCHIVE[entry.offset : entry.offset + entry.size] == data


def test_every_file_but_the_last_starts_on_a_128_byte_boundary():
    entries = bigf_parse(ARCHIVE)
    assert [entry.offset % 128 for entry in entries] == [0, 0, 0]


def test_parse_returns_dataclass_entries():
    assert type(bigf_parse(ARCHIVE)[0]) is BigfEntry


@pytest.mark.parametrize("data", [b"", b"BIGF", b"BIGF" + b"\x00" * 11])
def test_an_archive_shorter_than_its_header_is_refused(data):
    with pytest.raises(EaTdbError, match="Not a BIGF archive"):
        bigf_parse(data)


@pytest.mark.parametrize("magic", [b"BIGH", b"bigf", b"\x10\xfb\x00\x00"])
def test_an_archive_without_the_magic_is_refused(magic):
    with pytest.raises(EaTdbError, match="Not a BIGF archive"):
        bigf_parse(magic + b"\x00" * 20)


def test_the_refusal_is_a_rom_error():
    assert issubclass(EaTdbError, RomError) is True
    assert issubclass(EaTdbError, RetroRosterError) is True


def test_a_directory_claiming_more_files_than_it_holds_invents_them():
    # An inherited defect, pinned rather than fixed, because fixing it is a
    # change of behaviour that belongs in its own commit with the three games
    # present to be re-run against it.
    #
    # The file count is trusted absolutely: nothing stops the scan at the end of
    # the real directory, so it walks straight into the padding and then into the
    # first file's data and manufactures entries out of it. The three real ones
    # come back correctly, so an over-count costs nothing to a caller that
    # searches by name — which is what all four other functions here do — and
    # would cost a caller that iterated the whole list.
    lying = build_bigf(BigfSpec(files=FILES, stated_num_files=9))
    entries = bigf_parse(lying)
    assert len(entries) == 9
    assert [e.name for e in entries[:3]] == ["nhl2007.tdb", "NHLROST.TDB", "nhlbioatt.tdb"]
    # Five of the six phantoms come out of the zero padding, so they are empty
    # names at offset zero; the sixth reads into the first file and reports a
    # size of 67599 in a 769-byte archive.
    assert [(e.name, e.offset, e.size) for e in entries[3:8]] == [("", 0, 0)] * 5
    assert entries[8].size == 67599
    assert len(lying) == 769


def test_the_real_entries_still_extract_from_an_over_counted_directory():
    # The reason the defect above has never bitten: every caller in the source
    # looks a name up rather than walking the list.
    lying = build_bigf(BigfSpec(files=FILES, stated_num_files=9))
    assert bigf_extract(lying, "nhl2007.tdb") == FILES[0][1]
    assert bigf_extract(lying, "nhlbioatt.tdb") == FILES[2][1]


def test_a_directory_truncated_mid_name_keeps_the_partial_name():
    # The other half of the same defect. The name scan stops at a NUL or at the
    # end of the buffer, so a cut inside the third name yields three entries, the
    # last of them called `nhl` with a size read from bytes that were there.
    entries = bigf_parse(ARCHIVE)
    third_name_start = 16 + sum(8 + len(entries[i].name) + 1 for i in range(2)) + 8
    truncated = ARCHIVE[: third_name_start + 3]
    assert [e.name for e in bigf_parse(truncated)] == ["nhl2007.tdb", "NHLROST.TDB", "nhl"]
    # And that third entry is unusable, which is the point: it names a file whose
    # bytes are not in the buffer at all.
    assert bigf_extract(truncated, "nhl") == b""


def test_a_directory_truncated_before_an_entry_yields_only_the_whole_ones():
    # The bounds check that does fire: with fewer than eight bytes left there is
    # no offset and size to read, and the scan stops.
    entries = bigf_parse(ARCHIVE)
    third_entry_start = 16 + sum(8 + len(entries[i].name) + 1 for i in range(2))
    truncated = ARCHIVE[: third_entry_start + 7]
    assert [e.name for e in bigf_parse(truncated)] == ["nhl2007.tdb", "NHLROST.TDB"]


def test_neither_the_total_size_nor_the_header_size_is_read():
    # Both are written, neither is consulted, and `bigf_build` writes the total
    # size little-endian while writing the other two big-endian. A reader that
    # started trusting either would break on one of these two archives.
    wrong_endian = build_bigf(BigfSpec(files=FILES, total_size_endianness=">"))
    lying_header = build_bigf(BigfSpec(files=FILES, stated_header_size=999999))
    expected = [(e.name, e.offset, e.size) for e in bigf_parse(ARCHIVE)]
    assert [(e.name, e.offset, e.size) for e in bigf_parse(wrong_endian)] == expected
    assert [(e.name, e.offset, e.size) for e in bigf_parse(lying_header)] == expected


def test_an_archive_with_no_files_parses_to_no_entries():
    assert bigf_parse(build_bigf(BigfSpec(files=[]))) == []


@pytest.mark.parametrize(("name", "index"), [("nhl2007.tdb", 0), ("NHLROST.TDB", 1)])
def test_extract_returns_that_files_exact_bytes(name, index):
    assert bigf_extract(ARCHIVE, name) == FILES[index][1]


@pytest.mark.parametrize("spelling", ["nhl2007.tdb", "NHL2007.TDB", "NhL2007.TdB", "nhl2007.TDB"])
def test_extract_folds_case_in_both_directions(spelling):
    # A disc spells it `db.viv` or `DB.VIV` depending on the title, and the
    # patchers hold the name as a constant. The archive here holds one lower-case
    # name and one upper-case one, so neither direction is the trivial one.
    assert bigf_extract(ARCHIVE, spelling) == FILES[0][1]


def test_extract_of_the_upper_case_entry_folds_the_other_way_too():
    assert bigf_extract(ARCHIVE, "nhlrost.tdb") == FILES[1][1]


def test_extract_of_an_absent_file_is_none_rather_than_an_error():
    assert bigf_extract(ARCHIVE, "nhl2005.tdb") is None


def test_extract_of_an_empty_file_is_empty_bytes_and_not_none():
    # The distinction matters: `if not data` would treat a zero-length member the
    # same as a missing one, and `bigf_build` writes an empty file for any name
    # it has no content for.
    archive = build_bigf(BigfSpec(files=[("a.tdb", b""), ("b.tdb", b"xy")]))
    assert bigf_extract(archive, "a.tdb") == b""


def test_replace_puts_the_new_bytes_in_and_leaves_the_others_alone():
    rebuilt = bigf_replace(ARCHIVE, "nhl2007.tdb", b"REPLACED" * 9)
    assert bigf_extract(rebuilt, "nhl2007.tdb") == b"REPLACED" * 9
    assert bigf_extract(rebuilt, "NHLROST.TDB") == FILES[1][1]
    assert bigf_extract(rebuilt, "nhlbioatt.tdb") == FILES[2][1]


def test_replace_keeps_the_directory_order():
    rebuilt = bigf_replace(ARCHIVE, "nhl2007.tdb", b"x")
    assert [e.name for e in bigf_parse(rebuilt)] == [e.name for e in bigf_parse(ARCHIVE)]


def test_replace_with_something_longer_moves_the_later_files():
    # The reason `bigf_replace_inplace` exists. Growing the first member shifts
    # every offset after it, which is fine for a standalone archive and fatal for
    # one written back into an ISO at a fixed LBA.
    rebuilt = bigf_replace(ARCHIVE, "nhl2007.tdb", b"L" * 5000)
    assert [e.offset for e in bigf_parse(ARCHIVE)] == [128, 512, 640]
    assert [e.offset for e in bigf_parse(rebuilt)] == [128, 5248, 5376]


def test_replace_with_something_shorter_also_moves_them():
    rebuilt = bigf_replace(ARCHIVE, "nhl2007.tdb", b"S")
    assert [e.offset for e in bigf_parse(rebuilt)] == [128, 256, 384]
    assert [e.offset for e in bigf_parse(ARCHIVE)] == [128, 512, 640]


def test_replace_of_an_absent_file_raises():
    with pytest.raises(EaTdbError, match="not found in BIGF archive"):
        bigf_replace(ARCHIVE, "nhl2005.tdb", b"x")


def test_replace_matches_case_insensitively_but_checks_case_sensitively():
    # Carried over from the source unchanged. `bigf_replace` folds case when
    # deciding which member to swap out and then checks membership with the
    # caller's exact spelling, so asking for `NHL2007.TDB` in an archive that
    # spells it lower case raises after the swap has already been made. The two
    # NHL patchers work around it by reading the archive's own spelling out of
    # `bigf_parse` first, which is why this is pinned rather than fixed.
    with pytest.raises(EaTdbError, match="not found in BIGF archive"):
        bigf_replace(ARCHIVE, "NHL2007.TDB", b"x")
    # And the same call with the archive's own spelling succeeds, so the failure
    # above is about the spelling and not about the file.
    assert bigf_extract(bigf_replace(ARCHIVE, "nhl2007.tdb", b"x"), "nhl2007.tdb") == b"x"


def test_replace_inplace_writes_at_the_original_offset():
    archive = bytearray(ARCHIVE)
    entry = bigf_parse(ARCHIVE)[0]
    assert bigf_replace_inplace(archive, "nhl2007.tdb", b"NEW" * 10) is True
    assert bytes(archive[entry.offset : entry.offset + 30]) == b"NEW" * 10


def test_replace_inplace_leaves_the_archive_exactly_as_long():
    archive = bytearray(ARCHIVE)
    bigf_replace_inplace(archive, "nhl2007.tdb", b"NEW" * 10)
    assert len(archive) == len(ARCHIVE)


def test_replace_inplace_moves_no_other_file():
    # The whole point: the caller writes this back into an ISO at a fixed LBA.
    archive = bytearray(ARCHIVE)
    bigf_replace_inplace(archive, "nhl2007.tdb", b"NEW" * 10)
    assert [e.offset for e in bigf_parse(bytes(archive))] == [e.offset for e in bigf_parse(ARCHIVE)]
    assert bigf_extract(bytes(archive), "NHLROST.TDB") == FILES[1][1]
    assert bigf_extract(bytes(archive), "nhlbioatt.tdb") == FILES[2][1]


def test_replace_inplace_zero_fills_the_space_it_did_not_use():
    archive = bytearray(ARCHIVE)
    entry = bigf_parse(ARCHIVE)[0]
    bigf_replace_inplace(archive, "nhl2007.tdb", b"NEW" * 10)
    assert bytes(archive[entry.offset + 30 : entry.offset + entry.size]) == b"\x00" * 270


def test_replace_inplace_leaves_the_directory_size_at_the_original_allocation():
    # Deliberate, and documented in the module: the game reads the full original
    # size and RefPack stops at its own end marker, so the padding is never seen.
    # A reader that trusted the directory size would otherwise get a short file.
    archive = bytearray(ARCHIVE)
    bigf_replace_inplace(archive, "nhl2007.tdb", b"NEW" * 10)
    assert bigf_parse(bytes(archive))[0].size == 300


def test_replace_inplace_zero_fills_a_single_leftover_byte():
    # One byte short of the allocation is the boundary the padding branch turns on at:
    # `if remaining > 1` passes every other test in this file and leaves one stale byte
    # behind.
    archive = bytearray(ARCHIVE)
    entry = bigf_parse(ARCHIVE)[0]
    assert bigf_replace_inplace(archive, "nhl2007.tdb", b"\x77" * 299) is True
    assert archive[entry.offset + 299] == 0
    assert archive[entry.offset + 298] == 0x77


def test_replace_inplace_fills_the_allocation_exactly_without_padding():
    archive = bytearray(ARCHIVE)
    entry = bigf_parse(ARCHIVE)[0]
    exact = bytes((i * 3 + 200) & 0xFF for i in range(300))
    assert bigf_replace_inplace(archive, "nhl2007.tdb", exact) is True
    assert bytes(archive[entry.offset : entry.offset + 300]) == exact


def test_replace_inplace_refuses_data_larger_than_the_allocation():
    archive = bytearray(ARCHIVE)
    assert bigf_replace_inplace(archive, "nhl2007.tdb", b"\x01" * 301) is False


def test_a_refused_replace_inplace_changes_nothing():
    # The failure mode this return value guards. The source's two callers ignore
    # it, which turned an over-large TDB into a silently skipped write reported
    # as a successful patch; the byte-level claim here is what lets a game raise.
    archive = bytearray(ARCHIVE)
    bigf_replace_inplace(archive, "nhl2007.tdb", b"\x01" * 301)
    assert bytes(archive) == ARCHIVE


def test_replace_inplace_of_an_absent_file_is_false_and_changes_nothing():
    archive = bytearray(ARCHIVE)
    assert bigf_replace_inplace(archive, "nhl2005.tdb", b"x") is False
    assert bytes(archive) == ARCHIVE


def test_replace_inplace_folds_case():
    archive = bytearray(ARCHIVE)
    assert bigf_replace_inplace(archive, "NHL2007.TDB", b"ok") is True
    assert bigf_extract(bytes(archive), "nhl2007.tdb")[:2] == b"ok"


def test_replace_inplace_takes_the_last_of_two_entries_sharing_a_name():
    # An archive with the same name twice is not something EA ships, but the
    # loop keeps overwriting `target_entry` rather than breaking, so the last
    # wins. Pinned so a later `break` is a deliberate change and not a silent one.
    archive = bytearray(build_bigf(BigfSpec(files=[("dup.tdb", b"A" * 8), ("dup.tdb", b"B" * 8)])))
    entries = bigf_parse(bytes(archive))
    assert bigf_replace_inplace(archive, "dup.tdb", b"ZZ") is True
    assert bytes(archive[entries[1].offset : entries[1].offset + 2]) == b"ZZ"
    assert bytes(archive[entries[0].offset : entries[0].offset + 2]) == b"AA"


def test_build_then_parse_returns_what_went_in():
    contents = dict(FILES)
    entries = [BigfEntry(name=name, offset=0, size=0) for name, _ in FILES]
    built = bigf_build(entries, contents)
    for name, data in FILES:
        assert bigf_extract(built, name) == data


def test_build_ignores_the_offsets_and_sizes_it_is_handed():
    # `bigf_replace` passes the entries it parsed straight back in, so the
    # offsets on them are the OLD archive's. Anything that trusted them would
    # produce a directory pointing into the wrong file.
    entries = [BigfEntry(name=name, offset=999999, size=7) for name, _ in FILES]
    built = bigf_build(entries, dict(FILES))
    assert [e.size for e in bigf_parse(built)] == [300, 17, 129]
    assert bigf_extract(built, "nhl2007.tdb") == FILES[0][1]


def test_build_starts_the_first_file_on_a_128_byte_boundary():
    entries = [BigfEntry(name=name, offset=0, size=0) for name, _ in FILES]
    built = bigf_build(entries, dict(FILES))
    assert bigf_parse(built)[0].offset % 128 == 0


def test_build_pads_between_files_but_not_after_the_last():
    entries = [BigfEntry(name=name, offset=0, size=0) for name, _ in FILES]
    built = bigf_build(entries, dict(FILES))
    last = bigf_parse(built)[-1]
    assert len(built) == last.offset + last.size
    assert len(built) % 128 == 1


def test_build_writes_an_empty_file_for_a_name_it_has_no_content_for():
    # `bigf_replace` relies on this: the dictionary it builds is keyed by the
    # archive's own spelling, so a mismatch would silently drop a file instead of
    # raising, and the archive would still be structurally valid.
    entries = [BigfEntry(name="a.tdb", offset=0, size=0), BigfEntry(name="b.tdb", offset=0, size=0)]
    built = bigf_build(entries, {"a.tdb": b"kept"})
    assert bigf_extract(built, "b.tdb") == b""
    assert bigf_extract(built, "a.tdb") == b"kept"
    # The directory size and the bytes written come from two separate `.get()`
    # calls on the same dictionary, so they can disagree without `bigf_extract`
    # noticing: a default of one byte rather than none makes the archive a byte
    # longer than its last entry claims and shifts every later file.
    last = bigf_parse(built)[-1]
    assert len(built) == last.offset + last.size


def test_build_writes_the_file_count_and_header_size_big_endian():
    entries = [BigfEntry(name=name, offset=0, size=0) for name, _ in FILES]
    built = bigf_build(entries, dict(FILES))
    expected_header = 16 + sum(8 + len(name) + 1 for name, _ in FILES)
    assert struct.unpack_from(">I", built, 8)[0] == 3
    assert struct.unpack_from(">I", built, 12)[0] == expected_header


def test_build_writes_the_total_size_little_endian():
    # Not a typo in the test. The source writes this one little-endian and the
    # two beside it big-endian, and `bigf_parse` reads none of the three, so
    # nothing in this library can say which EA meant. Pinned as it is.
    entries = [BigfEntry(name=name, offset=0, size=0) for name, _ in FILES]
    built = bigf_build(entries, dict(FILES))
    assert struct.unpack_from("<I", built, 4)[0] == len(built)
    assert struct.unpack_from(">I", built, 4)[0] != len(built)


def test_build_of_nothing_is_a_header_padded_to_128():
    # The alignment pad runs before the first file rather than before each, so
    # an archive with no files still gets it: 16 bytes of header and 112 of zero.
    built = bigf_build([], {})
    assert built[:4] == b"BIGF"
    assert len(built) == 128
    assert bigf_parse(built) == []


def test_build_agrees_with_the_hand_written_fixture():
    # The two writers meet here. `build_bigf` in the fixture module lays an
    # archive out independently; if this equality ever fails, one of the two has
    # changed and every parse assertion above is checking the wrong format.
    entries = [BigfEntry(name=name, offset=0, size=0) for name, _ in FILES]
    assert bigf_build(entries, dict(FILES)) == ARCHIVE
