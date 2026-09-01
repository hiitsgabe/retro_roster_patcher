"""Characterisation tests for the PPF applier.

The plan for this port expected `get_ppf_info` to raise `PPFError` on a bad
magic. It does not: it returns `{"version": 0, ...}` and quotes the magic it saw
in the description, and only `apply_ppf` raises. These tests pin the behaviour
that is actually there rather than the behaviour that was assumed.
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

    patch = tmp_path / "good.ppf"
    patch.write_bytes(_ppf1_patch("synthetic", struct.pack("<I", 4) + b"\x03\xaa\xbb\xcc"))

    assert get_ppf_info(str(patch))["version"] == 1
    assert apply_ppf(str(target), str(patch)) == "synthetic"

    patched = target.read_bytes()
    assert patched[:4] == original[:4]
    assert patched[4:7] == b"\xaa\xbb\xcc"
    assert patched[7:] == original[7:]
