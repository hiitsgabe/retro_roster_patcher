"""The AFS container handler, against archives built in-test.

`AfsHandler` has no consumer in `src/` outside its own module, and no test
before this one, so the negative claim `replace_entry`'s docstring now makes —
that it changes memory and never the file it was read from — had nothing
holding it. That is the claim most worth pinning: a caller who believed
"in-place" meant the file would lose every edit.

Every archive here is a handful of bytes assembled by `_archive`. Nothing from
the game is involved.
"""

import struct

import pytest

from retro_roster_patcher.games.we2002.afs_handler import AfsHandler
from retro_roster_patcher.games.we2002.models import AfsEntry


def _archive(payloads, *, first_offset=2048, stride=2048):
    """An AFS holding `payloads`, each at its own sector-aligned offset.

    Header is the four-byte magic, a little-endian uint32 file count, and then
    an eight-byte (offset, size) pair per entry.
    """
    header = bytearray(b"AFS\x00" + struct.pack("<I", len(payloads)))
    offsets = [first_offset + i * stride for i in range(len(payloads))]
    for offset, payload in zip(offsets, payloads, strict=True):
        header += struct.pack("<II", offset, len(payload))
    raw = bytearray(header)
    for offset, payload in zip(offsets, payloads, strict=True):
        raw.extend(bytes(offset - len(raw)))
        raw.extend(payload)
    return bytes(raw)


def _written(tmp_path, payloads, name="game.afs"):
    path = tmp_path / name
    path.write_bytes(_archive(payloads))
    return path


def test_the_table_of_contents_is_read_as_one_entry_per_file(tmp_path):
    path = _written(tmp_path, [b"first", b"second-longer", b"3rd"])

    entries = AfsHandler(str(path)).list_entries()

    assert entries == [
        AfsEntry(index=0, offset=2048, size=5),
        AfsEntry(index=1, offset=4096, size=13),
        AfsEntry(index=2, offset=6144, size=3),
    ]


def test_each_entry_extracts_the_bytes_its_toc_row_points_at(tmp_path):
    path = _written(tmp_path, [b"first", b"second-longer", b"3rd"])
    handler = AfsHandler(str(path))

    assert handler.extract_entry(0) == b"first"
    assert handler.extract_entry(1) == b"second-longer"
    assert handler.extract_entry(2) == b"3rd"


def test_an_index_outside_the_table_is_refused(tmp_path):
    path = _written(tmp_path, [b"only"])
    handler = AfsHandler(str(path))

    with pytest.raises(IndexError, match="1 out of range"):
        handler.extract_entry(1)
    with pytest.raises(IndexError, match="-1 out of range"):
        handler.extract_entry(-1)
    with pytest.raises(IndexError, match="1 out of range"):
        handler.replace_entry(1, b"x")


def test_a_file_that_is_not_an_afs_archive_is_refused(tmp_path):
    path = tmp_path / "not.afs"
    path.write_bytes(b"ZZZ\x00" + struct.pack("<I", 1) + bytes(16))

    with pytest.raises(ValueError, match="Not a valid AFS archive"):
        AfsHandler(str(path))


def test_replacing_an_entry_changes_memory_and_not_the_file_it_came_from(tmp_path):
    # The claim the docstring makes, and the one a caller is most likely to get
    # wrong: "in-place" is about the entry's extent, not about the file. There
    # is no save, flush or write method on this class at all — `rebuild` writes,
    # and it writes wherever it is told.
    path = _written(tmp_path, [b"first", b"second"])
    before = path.read_bytes()
    handler = AfsHandler(str(path))

    handler.replace_entry(0, b"NEW")

    assert handler.extract_entry(0) == b"NEW\x00\x00"
    assert path.read_bytes() == before


def test_a_replacement_shorter_than_the_entry_is_zero_padded_to_its_size(tmp_path):
    # The padding is what keeps every later offset in the TOC valid, so it is
    # the reason a short replacement is allowed at all.
    path = _written(tmp_path, [b"abcdefgh", b"second"])
    handler = AfsHandler(str(path))

    handler.replace_entry(0, b"XY")

    assert handler.extract_entry(0) == b"XY\x00\x00\x00\x00\x00\x00"
    assert handler.extract_entry(1) == b"second"
    assert handler.list_entries()[0].size == 8


def test_a_replacement_larger_than_the_entry_is_refused(tmp_path):
    # Growing an entry would move every offset after it, which this method
    # cannot do without rewriting the TOC — that is what `rebuild` is for, and
    # the message says so.
    path = _written(tmp_path, [b"abc"])
    handler = AfsHandler(str(path))

    with pytest.raises(ValueError, match="Use rebuild"):
        handler.replace_entry(0, b"abcd")

    assert handler.extract_entry(0) == b"abc"


def test_rebuild_writes_a_readable_archive_to_the_path_it_is_given(tmp_path):
    path = _written(tmp_path, [b"first", b"second"])
    before = path.read_bytes()
    handler = AfsHandler(str(path))
    out = tmp_path / "rebuilt.afs"

    handler.rebuild(str(out))

    assert path.read_bytes() == before, "rebuild writes to its argument, not the source"
    rebuilt = AfsHandler(str(out))
    assert rebuilt.extract_entry(0) == b"first"
    assert rebuilt.extract_entry(1) == b"second"


def test_rebuild_resizes_the_entry_that_replace_entry_could_not(tmp_path):
    # A replacement longer than the original: the TOC row grows and the entry
    # after it moves to the next sector, which is exactly what `replace_entry`
    # refuses to do.
    path = _written(tmp_path, [b"abc", b"second"])
    handler = AfsHandler(str(path))
    out = tmp_path / "rebuilt.afs"

    handler.rebuild(str(out), replacements={0: b"much longer than before"})

    rebuilt = AfsHandler(str(out))
    assert rebuilt.list_entries() == [
        AfsEntry(index=0, offset=2048, size=23),
        AfsEntry(index=1, offset=4096, size=6),
    ]
    assert rebuilt.extract_entry(0) == b"much longer than before"
    assert rebuilt.extract_entry(1) == b"second"


def test_a_replacement_made_in_memory_survives_a_rebuild(tmp_path):
    # The two methods compose: `replace_entry` is only useful because `rebuild`
    # reads the mutated buffer rather than the file.
    path = _written(tmp_path, [b"abcdefgh", b"second"])
    handler = AfsHandler(str(path))
    out = tmp_path / "rebuilt.afs"

    handler.replace_entry(0, b"XY")
    handler.rebuild(str(out))

    assert AfsHandler(str(out)).extract_entry(0) == b"XY\x00\x00\x00\x00\x00\x00"


def test_a_missing_archive_parses_as_no_entries_rather_than_raising(tmp_path):
    # The constructor tolerates an absent path, so nothing downstream may treat
    # a handler as proof that a file was there.
    handler = AfsHandler(str(tmp_path / "nowhere.afs"))

    assert handler.list_entries() == []
    with pytest.raises(IndexError):
        handler.extract_entry(0)
