"""`formats.ea_huff`: EA's Huffman-with-runs streams (`30 FB`), checked against
the fixture's encoder."""

from __future__ import annotations

import pytest

from retro_roster_patcher.formats.ea_huff import decompress
from retro_roster_patcher.formats.ea_tdb import EaTdbError
from tests.fixtures.synthetic_ea_huff import huff, huff_number


def test_bytes_runs_and_a_raw_byte_decode_and_the_end_marker_stops():
    tokens = [("byte", 0x11), ("run", 5), ("byte", 0x41), ("raw", 0x99), ("byte", 0x22)]
    stream = huff([*tokens, ("end", 0)], 64)
    assert decompress(stream + b"\xff" * 4) == bytes([0x11] * 6 + [0x41, 0x99, 0x22])


def test_the_size_ends_a_stream_without_an_end_marker():
    stream = huff([("byte", 0x22), ("run", 30)], 10)
    assert decompress(stream) == bytes([0x22] * 10)


def test_long_runs_take_longer_numbers():
    assert decompress(huff([("byte", 0x41), ("run", 300), ("end", 0)], 400)) == b"\x41" * 301


def test_anything_else_is_refused():
    with pytest.raises(EaTdbError):
        decompress(b"\x10\xfb\x00\x00\x01\x00")


def test_more_symbols_than_byte_values_is_refused_not_looped_on():
    """512 codes of 9 bits fill the code space, but there are only 256 bytes."""
    bits = huff_number(0) * 8 + huff_number(512) + huff_number(0) * 600
    bits += "0" * (-len(bits) % 8)
    body = int(bits, 2).to_bytes(len(bits) // 8, "big")
    with pytest.raises(EaTdbError, match="more symbols than byte values"):
        decompress(b"\x30\xfb" + (100).to_bytes(3, "big") + b"\x00" + body)
