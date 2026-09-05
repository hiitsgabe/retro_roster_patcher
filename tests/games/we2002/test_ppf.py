"""Characterisation tests for the PPF applier.

`get_ppf_info` does not raise `PPFError` on a bad magic: it returns
`{"version": 0, ...}` and quotes the magic it saw in the description. Only
`apply_ppf` raises. These tests pin the behaviour that is there.

Every patch and every target here is synthetic and built in-test. No community
PPF and no disc image is in this repository or reachable from these tests.

All three formats are covered and all three are reachable in product. Every patch
this project generates is PPF1 and is applied with validation on; a community
`w202-english.ppf` an operator drops into `assets_dir` is applied as it stands
with `skip_validation=True`, and those are typically PPF2 or PPF3. So a wrong
record stride in a supplied file writes chosen bytes at chosen offsets into the
output ISO with only the magic in the way.

`_apply_ppf2`'s `while len(data) >= 5` versus `>= 6` is an EQUIVALENT mutant and
is deliberately not covered: they differ only on a trailing remnant of exactly
five bytes, where a count of 0 writes an empty slice and any other count hits the
short-record break. The same is true of `_apply_ppf1`'s copy of that loop.
"""

import struct

import pytest

from retro_roster_patcher.games.we2002.ppf import PPFError, apply_ppf, get_ppf_info


def test_a_non_ppf_file_is_reported_as_version_zero(tmp_path):
    fake = tmp_path / "not.ppf"
    fake.write_bytes(b"this is not a ppf patch file")

    info = get_ppf_info(str(fake))

    assert info["version"] == 0
    # The magic is the first five bytes, quoted back so a caller can see what
    # was actually read; `expected_size` stays 0 because only PPF2 carries one.
    assert info["description"] == "Unknown format: b'this '"
    assert info["expected_size"] == 0


def test_an_empty_file_is_reported_as_version_zero(tmp_path):
    empty = tmp_path / "empty.ppf"
    empty.write_bytes(b"")

    info = get_ppf_info(str(empty))

    assert info["version"] == 0
    assert info["description"] == "Unknown format: b''"
    assert info["expected_size"] == 0


def test_applying_a_bad_patch_raises_rather_than_corrupting_the_target(tmp_path):
    target = tmp_path / "rom.bin"
    original = bytes(range(256)) * 16
    target.write_bytes(original)
    bad = tmp_path / "bad.ppf"
    bad.write_bytes(b"nonsense")

    with pytest.raises(PPFError):
        apply_ppf(str(target), str(bad))

    assert target.read_bytes() == original


def _ppf1_patch(description: str, records: bytes) -> bytes:
    """Build a synthetic PPF 1.0 patch.

    Layout the applier reads: magic `PPF10` (5 bytes), an encoding byte, then a
    50-byte description, so patch records start at offset 56. Each record is a
    little-endian uint32 offset, a one-byte length, then that many bytes.
    """
    header = b"PPF10" + b"\x00" + description.encode("ascii").ljust(50, b"\x00")
    return header + records


def test_a_ppf1_patch_overwrites_only_the_bytes_its_records_name(tmp_path):
    target = tmp_path / "rom.bin"
    original = bytes(range(256)) * 16
    target.write_bytes(original)

    # Two records, so the stride the applier uses to step from one to the next is
    # actually exercised: with a single record the tail is empty whatever the
    # stride is. The bundled translation patches carry 96 records each.
    two_records = (
        struct.pack("<I", 4) + b"\x03\xaa\xbb\xcc" + struct.pack("<I", 100) + b"\x02\xde\xad"
    )
    # Exactly 50 characters, which fills the description field to its width and
    # so pins the slice the applier reads it back with. No bundled patch is this
    # long, so `test_a_short_description_comes_back_without_its_null_padding`
    # covers the padded shape they all actually have.
    full_desc = "s" * 49 + "c"

    patch = tmp_path / "good.ppf"
    patch.write_bytes(_ppf1_patch(full_desc, two_records))

    assert get_ppf_info(str(patch)) == {
        "version": 1,
        "description": full_desc,
        "expected_size": 0,
    }
    assert apply_ppf(str(target), str(patch)) == full_desc

    patched = target.read_bytes()
    assert patched[:4] == original[:4]
    assert patched[4:7] == b"\xaa\xbb\xcc"
    assert patched[7:100] == original[7:100]
    assert patched[100:102] == b"\xde\xad"
    assert patched[102:] == original[102:]


def test_a_short_description_comes_back_without_its_null_padding(tmp_path):
    # The full-width description above pins the field's slice, but it leaves no
    # padding to strip, so on its own it stops covering `.rstrip("\x00")`. This
    # is the shape that actually ships: generating all four bundled translation
    # patches gives descriptions of the form "WE2002 English - Console Utilities"
    # with 16, 16, 17 and 13 trailing NULs (en, es, fr, pt). None is 50 wide.
    target = tmp_path / "rom.bin"
    original = bytes(range(256))
    target.write_bytes(original)

    patch = tmp_path / "short.ppf"
    patch.write_bytes(_ppf1_patch("synthetic", struct.pack("<I", 0) + b"\x01\xaa"))

    assert get_ppf_info(str(patch))["description"] == "synthetic"
    assert apply_ppf(str(target), str(patch)) == "synthetic"


def test_a_ppf1_record_that_overruns_the_end_of_the_patch_is_dropped(tmp_path):
    # A truncated download ends mid-record: the length byte promises two bytes
    # and only one follows. The loop stops there rather than writing a short
    # record, so the earlier record stands and the target keeps its own bytes.
    #
    # Short by exactly one byte, on purpose. The guard is `len(data) < 5 + count`
    # and an off-by-one — `4 + count` — only shows on a tail of exactly that
    # length: at any larger shortfall both forms break and the test proves
    # nothing. Here the off-by-one form would write `data[5:7]`, which is the
    # single byte `\x33`, into the target at offset 20.
    target = tmp_path / "rom.bin"
    original = bytes(range(256))
    target.write_bytes(original)

    good = struct.pack("<I", 8) + b"\x02\x11\x22"
    one_byte_short = struct.pack("<I", 20) + b"\x02\x33"
    assert len(one_byte_short) == 6
    patch = tmp_path / "truncated.ppf"
    patch.write_bytes(_ppf1_patch("truncated", good + one_byte_short))

    apply_ppf(str(target), str(patch))

    patched = target.read_bytes()
    assert patched[8:10] == b"\x11\x22"
    assert patched[20:22] == original[20:22]
    assert patched == original[:8] + b"\x11\x22" + original[10:]


@pytest.mark.parametrize("magic", [b"PPF11", b"PPF1\x00", b"PPF1A", b"PPF40", b"PPF0 "])
def test_a_magic_that_only_starts_like_ppf1_is_refused(tmp_path, magic):
    # PPF1 is matched on all five bytes where PPF2 and PPF3 are matched on four,
    # and that asymmetry is load-bearing: the only production call passes
    # `skip_validation=True`, so the magic is the sole thing between a wrong file
    # and an in-place write into the output ISO. A prefix test here would send
    # `PPF11` down `_apply_ppf1` and write its bytes at whatever offsets it names.
    target = tmp_path / "rom.bin"
    original = bytes(range(256))
    target.write_bytes(original)
    patch = tmp_path / "wrong.ppf"
    patch.write_bytes(magic + b"\x00" + b"x" * 50 + struct.pack("<I", 0) + b"\x01\xff")

    with pytest.raises(PPFError, match="Unsupported PPF format"):
        apply_ppf(str(target), str(patch))

    assert target.read_bytes() == original


@pytest.mark.parametrize("magic", [b"PPF11", b"PPF1\x00"])
def test_get_ppf_info_reports_a_near_miss_magic_as_version_zero(tmp_path, magic):
    # The same exact-versus-prefix rule on the reading side.
    patch = tmp_path / "wrong.ppf"
    patch.write_bytes(magic + b"\x00" + b"x" * 50 + struct.pack("<I", 7))

    info = get_ppf_info(str(patch))

    assert info["version"] == 0
    assert info["expected_size"] == 0


def _ppf2_patch(description: str, records: bytes, *, expected_size: int, block: bytes) -> bytes:
    """Build a synthetic PPF 2.0 patch.

    Layout the applier reads: magic `PPF20` (5 bytes), an encoding byte, a
    50-byte description, a little-endian uint32 expected file size at offset 56,
    a 1024-byte validation block at offset 60, and then records from offset
    1084. Each record is a little-endian uint32 offset, a one-byte length, then
    that many bytes — the same record shape PPF1 uses.
    """
    header = b"PPF20" + b"\x00" + description.encode("ascii").ljust(50, b"\x00")
    header += struct.pack("<I", expected_size)
    header += block.ljust(1024, b"\x00")
    return header + records


def _target(tmp_path, size=4096):
    """A synthetic patch target whose every byte is predictable."""
    path = tmp_path / "rom.bin"
    path.write_bytes(bytes(range(256)) * (size // 256))
    return path


def test_a_ppf2_patch_writes_every_one_of_its_records(tmp_path):
    # Three records rather than one, because the record stride -- `5 + count`, and
    # `count` differs per record -- is what steps from one to the next. With a single
    # record any stride at all leaves the loop with nothing to misread, and a wrong
    # stride writes attacker-chosen bytes at attacker-chosen offsets into the output
    # ISO.
    target = _target(tmp_path)
    original = target.read_bytes()

    records = (
        struct.pack("<I", 0)
        + b"\x01\xaa"
        + struct.pack("<I", 64)
        + b"\x03\xb0\xb1\xb2"
        + struct.pack("<I", 1000)
        + b"\x02\xc0\xc1"
    )
    patch = tmp_path / "two.ppf"
    patch.write_bytes(_ppf2_patch("synthetic ppf2", records, expected_size=0, block=b""))

    assert apply_ppf(str(target), str(patch), skip_validation=True) == "synthetic ppf2"

    expected = bytearray(original)
    expected[0:1] = b"\xaa"
    expected[64:67] = b"\xb0\xb1\xb2"
    expected[1000:1002] = b"\xc0\xc1"
    assert target.read_bytes() == bytes(expected)


def test_a_ppf2_record_that_overruns_the_end_of_the_patch_is_dropped(tmp_path):
    # Short by exactly one byte, for the same reason as the PPF1 case above: the
    # guard is `len(data) < 5 + count`, and an off-by-one form only diverges on a
    # tail of exactly `4 + count` bytes. Here it would write the lone `\x33` into
    # the output ISO at offset 32.
    target = _target(tmp_path)
    original = target.read_bytes()

    one_byte_short = struct.pack("<I", 32) + b"\x02\x33"
    assert len(one_byte_short) == 6
    records = struct.pack("<I", 16) + b"\x02\x11\x22" + one_byte_short
    patch = tmp_path / "truncated.ppf"
    patch.write_bytes(_ppf2_patch("truncated", records, expected_size=0, block=b""))

    apply_ppf(str(target), str(patch), skip_validation=True)

    expected = bytearray(original)
    expected[16:18] = b"\x11\x22"
    assert target.read_bytes() == bytes(expected)


def test_a_ppf2_patch_built_for_a_different_dump_is_refused_on_size(tmp_path):
    # `skip_validation` defaults off, and this is the first of the two checks it
    # turns off. The target is 4096 bytes and the patch declares 9999.
    target = _target(tmp_path)
    original = target.read_bytes()
    records = struct.pack("<I", 0) + b"\x01\xaa"
    patch = tmp_path / "wrongsize.ppf"
    patch.write_bytes(_ppf2_patch("wrong size", records, expected_size=9999, block=b""))

    with pytest.raises(PPFError, match="Size mismatch"):
        apply_ppf(str(target), str(patch))

    assert target.read_bytes() == original


def test_a_ppf2_patch_whose_validation_block_does_not_match_is_refused(tmp_path):
    # The second check: 1024 bytes read from the target at 0x9320 must equal the
    # block carried in the patch. The target is large enough to hold that offset
    # so the read returns real bytes rather than an empty string.
    target = _target(tmp_path, size=0x9320 + 2048)
    original = target.read_bytes()
    records = struct.pack("<I", 0) + b"\x01\xaa"
    patch = tmp_path / "wrongblock.ppf"
    patch.write_bytes(
        _ppf2_patch("wrong block", records, expected_size=len(original), block=b"\xff" * 1024)
    )

    with pytest.raises(PPFError, match="different ROM dump"):
        apply_ppf(str(target), str(patch))

    assert target.read_bytes() == original


def test_a_ppf2_patch_that_passes_both_checks_applies_without_skip_validation(tmp_path):
    # The other side of the two tests above: with a matching size and a matching
    # block, the records go in with `skip_validation` left at its default.
    target = _target(tmp_path, size=0x9320 + 2048)
    original = target.read_bytes()
    records = struct.pack("<I", 5) + b"\x02\xaa\xbb"
    patch = tmp_path / "matching.ppf"
    patch.write_bytes(
        _ppf2_patch(
            "matching",
            records,
            expected_size=len(original),
            block=original[0x9320 : 0x9320 + 1024],
        )
    )

    assert apply_ppf(str(target), str(patch)) == "matching"

    expected = bytearray(original)
    expected[5:7] = b"\xaa\xbb"
    assert target.read_bytes() == bytes(expected)


def _ppf3_patch(description: str, records: bytes, *, blockcheck=0, undo=0, block=b"") -> bytes:
    """Build a synthetic PPF 3.0 patch.

    Layout the applier reads: magic `PPF30`, an encoding method byte that must
    be 2, a 50-byte description, then image type, blockcheck and undo flags at
    offsets 56, 57 and 58. With blockcheck set there is a 1024-byte validation
    block at offset 60 and records start at 1084; without it records start at
    60. A PPF3 record is a little-endian uint64 offset, a one-byte length, that
    many patch bytes, and — when the undo flag is set — that many more bytes
    holding the original data.
    """
    header = b"PPF30" + bytes([2]) + description.encode("ascii").ljust(50, b"\x00")
    header += bytes([0, blockcheck, undo, 0])
    if blockcheck:
        header += block.ljust(1024, b"\x00")
    return header + records


def test_a_ppf3_patch_writes_its_records(tmp_path):
    target = _target(tmp_path)
    original = target.read_bytes()

    records = struct.pack("<Q", 10) + b"\x02\xaa\xbb" + struct.pack("<Q", 200) + b"\x01\xcc"
    patch = tmp_path / "plain.ppf3"
    patch.write_bytes(_ppf3_patch("plain ppf3", records))

    assert apply_ppf(str(target), str(patch)) == "plain ppf3"

    expected = bytearray(original)
    expected[10:12] = b"\xaa\xbb"
    expected[200:201] = b"\xcc"
    assert target.read_bytes() == bytes(expected)


def test_a_ppf3_patch_with_undo_data_steps_over_it_instead_of_writing_it(tmp_path):
    # With the undo flag set, every record carries the original bytes after the
    # replacement bytes so the patch can be reversed. They are not part of the
    # next record's header: without the extra skip the applier reads the first
    # undo byte as the start of a uint64 offset and writes the rest of the patch
    # to a garbage address. Two records, because the skip only shows on the step
    # from one record to the next.
    target = _target(tmp_path)
    original = target.read_bytes()

    first = struct.pack("<Q", 10) + b"\x02" + b"\xaa\xbb" + bytes(original[10:12])
    second = struct.pack("<Q", 200) + b"\x03" + b"\xc0\xc1\xc2" + bytes(original[200:203])
    patch = tmp_path / "undo.ppf3"
    patch.write_bytes(_ppf3_patch("undoable", first + second, undo=1))

    assert apply_ppf(str(target), str(patch)) == "undoable"

    expected = bytearray(original)
    expected[10:12] = b"\xaa\xbb"
    expected[200:203] = b"\xc0\xc1\xc2"
    assert target.read_bytes() == bytes(expected)


def test_a_ppf3_patch_with_blockcheck_reads_its_records_from_after_the_block(tmp_path):
    # The record start moves from 60 to 1084 when blockcheck is set, and the
    # block itself is checked against the target unless validation is skipped.
    target = _target(tmp_path, size=0x9320 + 2048)
    original = target.read_bytes()

    records = struct.pack("<Q", 7) + b"\x02\xaa\xbb"
    patch = tmp_path / "checked.ppf3"
    patch.write_bytes(
        _ppf3_patch("checked", records, blockcheck=1, block=original[0x9320 : 0x9320 + 1024])
    )

    assert apply_ppf(str(target), str(patch)) == "checked"

    expected = bytearray(original)
    expected[7:9] = b"\xaa\xbb"
    assert target.read_bytes() == bytes(expected)


def test_a_ppf3_blockcheck_against_a_different_dump_is_refused(tmp_path):
    target = _target(tmp_path, size=0x9320 + 2048)
    original = target.read_bytes()
    records = struct.pack("<Q", 7) + b"\x02\xaa\xbb"
    patch = tmp_path / "mismatched.ppf3"
    patch.write_bytes(_ppf3_patch("mismatched", records, blockcheck=1, block=b"\xff" * 1024))

    with pytest.raises(PPFError, match="different ROM dump"):
        apply_ppf(str(target), str(patch))

    assert target.read_bytes() == original


def test_a_ppf3_encoding_method_other_than_two_is_refused(tmp_path):
    target = _target(tmp_path)
    original = target.read_bytes()
    patch = tmp_path / "method.ppf3"
    body = _ppf3_patch("method", struct.pack("<Q", 0) + b"\x01\xaa")
    patch.write_bytes(body[:5] + bytes([1]) + body[6:])

    with pytest.raises(PPFError, match="Unsupported PPF3 encoding method: 1"):
        apply_ppf(str(target), str(patch))

    assert target.read_bytes() == original
