"""Konami's LZ format: the decoder against hand-built streams, the encoder by round trip.

The real-ROM test is the one that matters most: the 38 flag halves the cartridge
ships must decode, re-encode to the same bytes, and never cost more in total
than Konami's own encoder spent on them.
"""

from __future__ import annotations

import os
import random
import struct
from pathlib import Path

import pytest

from retro_roster_patcher.games.iss_snes.konami import MAX_RAW, compress, decompress
from retro_roster_patcher.games.iss_snes.rom_writer import _konami_compress_literal


def _stream(*ops: bytes) -> bytes:
    body = b"".join(ops)
    return struct.pack("<H", len(body) + 2) + body


def test_every_command_decodes_as_the_format_says():
    stream = _stream(
        bytes([0x83, 1, 2, 3]),  # three literals
        bytes([0xC1, 9]),  # three 9s
        bytes([0xE0]),  # two zeros
        bytes([0xFF, 1]),  # three zeros
        bytes([0xA0, 7, 8]),  # 00 07 00 08
        # copy 4 bytes from ring position 0: 0x3DF biased, length (c >> 2) + 2
        bytes([(4 - 2) << 2 | 0x3DF >> 8, 0x3DF & 0xFF]),
    )
    assert decompress(stream) == bytes([1, 2, 3, 9, 9, 9, 0, 0, 0, 0, 0, 0, 7, 0, 8, 1, 2, 3, 9])


def test_a_copy_may_overlap_the_bytes_it_writes():
    stream = _stream(bytes([0x81, 5]), bytes([(6 - 2) << 2 | 0x3DF >> 8, 0x3DF & 0xFF]))
    assert decompress(stream) == bytes([5] * 7)


def test_a_copy_from_the_rings_tail_reads_zeros():
    """The ring starts zeroed, so position 0x3FF holds a zero before anything
    is written there."""
    src = (0x3FF + 0x3DF) & 0x3FF
    stream = _stream(bytes([(3 - 2) << 2 | src >> 8, src & 0xFF]))
    assert decompress(stream) == bytes(3)


def test_the_stream_is_read_at_its_offset_and_bit_15_is_not_length():
    stream = _stream(bytes([0x82, 4, 5]))
    flagged = bytes([stream[0], stream[1] | 0x80]) + stream[2:]
    assert decompress(b"junk" + flagged, 4) == bytes([4, 5])


_rng = random.Random(7)
SAMPLES = {
    "zeros": bytes(96),
    "ramp": bytes(range(96)),
    "pairs": b"\x00\xff" * 48,
    "zero-pairs": bytes([0, 1, 0, 2, 0, 3] * 16),
    "noise": bytes(_rng.randrange(256) for _ in range(96)),
    "runs": b"\x12" * 40 + b"\x00" * 40 + b"\x34\x56" * 8,
    "long zero run": bytes(300) + b"\x01",
    "empty": b"",
}


@pytest.mark.parametrize("raw", SAMPLES.values(), ids=SAMPLES.keys())
def test_compress_round_trips(raw):
    assert decompress(compress(raw)) == raw


@pytest.mark.parametrize("raw", SAMPLES.values(), ids=SAMPLES.keys())
def test_compress_is_never_longer_than_literals(raw):
    assert len(compress(raw)) <= len(_konami_compress_literal(raw))


def test_compress_refuses_more_than_one_ring_can_carry():
    with pytest.raises(ValueError):
        compress(bytes(MAX_RAW + 1))


@pytest.mark.real_rom
@pytest.mark.skipif(
    not os.environ.get("RETRO_ROSTER_TEST_ROMS"),
    reason="set RETRO_ROSTER_TEST_ROMS to a directory holding iss.sfc",
)
def test_the_cartridges_flag_halves_round_trip_in_fewer_bytes():
    rom = Path(os.environ["RETRO_ROSTER_TEST_ROMS"]) / "iss.sfc"
    if not rom.exists():
        pytest.fail(f"RETRO_ROSTER_TEST_ROMS is set but {rom} is missing")
    data = rom.read_bytes()
    halves = {
        0x40000 + struct.unpack_from("<H", data, 0x941A + 4 * team + k)[0]
        for team in range(27)
        for k in (0, 2)
    }
    assert len(halves) == 38
    theirs = ours = 0
    for address in halves:
        raw = decompress(data, address)
        assert len(raw) == 96
        mine = compress(raw)
        assert decompress(mine) == raw
        theirs += struct.unpack_from("<H", data, address)[0]
        ours += len(mine)
    assert theirs == 1022
    assert ours <= theirs
