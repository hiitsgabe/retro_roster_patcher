"""RefPack/QFS, checked three ways: against the source compressor, by round
trip, and by decoding what the encoder emitted back into commands.

The three are not redundant. A round trip alone would pass for any encoder that
emits something this module's own decompressor understands, including one that
picks a worse encoding than the game's tools do — and the output of
`refpack_compress` is what the console loads, so "decompresses correctly" is
necessary and not sufficient. `REFERENCE_COMPRESSED` closes that: it is the
exact output of the compressor inside `console_utilities` that this module was
ported from, captured over the thirteen inputs in `vector_inputs.py`, so a port
that re-derived a different-but-valid encoding fails here rather than shipping.

Nothing in this file imports `console_utilities`. The references are literals.
"""

import random

import pytest

from retro_roster_patcher.formats.ea_tdb import (
    EaTdbError,
    _emit_copy,
    _is_encodable,
    refpack_compress,
    refpack_decompress,
)
from tests.formats.vector_inputs import VECTOR_INPUTS

# Captured by running the source module's `refpack_compress` over
# `VECTOR_INPUTS`. Each was verified to decompress back to its input by the
# source module's own `refpack_decompress` at capture time.
REFERENCE_COMPRESSED = {
    # 112 bytes in, 119 out; commands ['end0', 'literal']
    "chunk_cap": bytes.fromhex(
        "10fb000070fb3d51a3333e88a2ea23ce9622686525985d86364c818f705a610b788679f420ac515d9df0e766"
        "ee1c683ea277a4e4edf4596c67f524905407e87bc5ccc22d93055d54485dd7ac6475900660fb60070dbeda85"
        "d83e9ba80b3e80461530a88ed10584fc8dba4fe81e8018d04d8fa7622562fc"
    ),
    # 686 bytes in, 421 out; commands ['copy2', 'copy3', 'end0', 'literal']
    "csv": bytes.fromhex(
        "10fb0002ae110030e22c3020302c3120504c415945050f52e13b0d0a3965333737031b39623187401b31051b"
        "31e13363366566333632001b87401b32051b32e16461613636643133001b87401b33051b33e1373864646536"
        "6334001b87401b34051b34e13137313536303735001b87401b35051b35e16235346364613236001b87401b36"
        "051b36e13533383435336437001b87401b37051b37e16631626263643838001b87401b38051b38e138666633"
        "34373339001b87401b39051b39e1326532616330656104fb870118291831e16363363233613962041c870119"
        "291931e13661393962343463041c87011a291a31e13038643132646664041c87011b291b31e1613730386137"
        "6165041c87011c291c31e13435343032313566041c87011d291d3130a824c587411e31291e31e0383161662b"
        "c631346387011f291f31e03166653627c738653787412031292031e06265316527c830383287412131292131"
        "e03563353527c938316487412132292132e06661386327ca66623887412132292132e03938633427cb373533"
        "87412132292132e03336666227cc65656587412132292132fc"
    ),
    # 0 bytes in, 6 out; commands ['end0']
    "empty": bytes.fromhex("10fb000000fc"),
    # 20016 bytes in, 100 out; commands ['copy4', 'end0', 'literal']
    "far_repeat": bytes.fromhex(
        "10fb004e30e14d41524b45525859cd0000ff00cc0000ffcc0000ffcc0000ffcc0000ffcc0000ffcc0000ffcc"
        "0000ffcc0000ffcc0000ffcc0000ffcc0000ffcc0000ffcc0000ffcc0000ffcc0000ffcc0000ffcc0000ffcc"
        "0000ffc40000cec04e2703fc"
    ),
    # 4 bytes in, 11 out; commands ['end0', 'literal']
    "four_bytes": bytes.fromhex("10fb000004e07778797afc"),
    # 28 bytes in, 26 out; commands ['copy2', 'end1', 'literal']
    "lazy": bytes.fromhex("10fb00001ce05758595a050451e05253545502005651180cfd58"),
    # 1200 bytes in, 15 out; commands ['copy4', 'end0']
    "long_run": bytes.fromhex("10fb0004b0cd0000ffc3c00000a6fc"),
    # 60 bytes in, 30 out; commands ['copy3', 'end0', 'literal']
    "medium_repeat": bytes.fromhex("10fb00003ce4303132333435363738394142434445464748494aa40013fc"),
    # 1 bytes in, 7 out; commands ['end1']
    "one_byte": bytes.fromhex("10fb000001fd51"),
    # 300 bytes in, 309 out; commands ['end0', 'literal']
    "random": bytes.fromhex(
        "10fb00012cfb3d65cb8f7f7efa43048abaf388728c1d442db89dfb1b64d1880e5b82f3882d3ec5e106be19dd"
        "5b43decf800e8d4231ea8dada4ed1fae5b6edde0b665937ccf3e254394c6e80e6c46475844f2e21099fdb65f"
        "a5e02aec54291ad65f0975783acab9044196784db8573ffc0091fd6c12bdfbb267a316bdf50658b7cbaad4c3"
        "460e7f3c81c711da093e28b244d537dffb50e8f6ce5dd6c2c01fd0c14a97084928d748efbdb62a1960d9a2d0"
        "261981572e28c50f27058aa1f5d1343d98687c87a60d8e4eef727392bca4c7c968be9a103a260e86f3c6874f"
        "5f6a81e7ded76f5bc8cd50f221601098ce3f9af4cd108c45c33d44da7a0253a3d10a4c8229651caa806e7972"
        "a6e5ad6268f7bc06164037539ab495d10664a56da8ed01f20d43d6941a410f46e6d67d580b0cab095ad387e1"
        "fc"
    ),
    # 40 bytes in, 10 out; commands ['copy3', 'end0']
    "run_of_one": bytes.fromhex("10fb000028a340005afc"),
    # 16 bytes in, 17 out; commands ['copy2', 'end0', 'literal']
    "short_repeat": bytes.fromhex("10fb000010e141424344454647481407fc"),
    # 3 bytes in, 9 out; commands ['end3']
    "three_bytes": bytes.fromhex("10fb000003ff616263"),
}


def _decode_commands(stream: bytes) -> list[str]:
    """Walk a compressed stream and name each command it contains.

    Written out here rather than shared with `ea_tdb`, so that a bug in the
    module's own command dispatch cannot make this agree with it. It parses only
    what it needs to step forward: the family, and how many literals ride along.
    """
    kinds: list[str] = []
    pos = 5
    while pos < len(stream):
        b0 = stream[pos]
        if b0 < 0x80:
            kinds.append("copy2")
            pos += 2 + (b0 & 0x03)
        elif b0 < 0xC0:
            kinds.append("copy3")
            pos += 3 + ((stream[pos + 1] & 0xC0) >> 6)
        elif b0 < 0xE0:
            kinds.append("copy4")
            pos += 4 + (b0 & 0x03)
        elif b0 < 0xFC:
            kinds.append("literal")
            pos += 1 + ((b0 & 0x1F) << 2) + 4
        else:
            kinds.append(f"end{b0 & 0x03}")
            break
    return kinds


# ──────────────────────────────────────────────────────────────
# The corpus itself, before anything is asserted about its members
# ──────────────────────────────────────────────────────────────


def test_the_reference_corpus_holds_thirteen_cases():
    # A parametrised suite over an empty or shrunken dict passes by collecting
    # nothing. This is the assertion that makes the count deliberate: every test
    # below that says `@pytest.mark.parametrize(..., VECTOR_INPUTS)` runs
    # thirteen times or this fails first.
    assert len(VECTOR_INPUTS) == 13


def test_every_input_has_a_reference_and_no_reference_is_orphaned():
    # Equality, not a subset either way. An input without a reference would be
    # silently unpinned; a reference without an input would be a vector nothing
    # checks.
    assert set(VECTOR_INPUTS) == set(REFERENCE_COMPRESSED)


def test_the_corpus_between_them_uses_every_command_family():
    # The point of this file is byte-fidelity across all five encodings. If the
    # inputs drifted until, say, no 4-byte copy command were emitted anywhere,
    # every equality below would still pass and the 4-byte encoder would be
    # unpinned. This is what stops that.
    families = set()
    for stream in REFERENCE_COMPRESSED.values():
        families.update(_decode_commands(stream))
    assert families == {"copy2", "copy3", "copy4", "literal", "end0", "end1", "end3"}


def test_the_corpus_holds_both_a_case_that_shrinks_and_one_that_grows():
    # A corpus of nothing but incompressible inputs would never exercise a copy
    # command; one of nothing but runs would never exercise the literal path.
    shrank = [k for k, v in REFERENCE_COMPRESSED.items() if len(v) < len(VECTOR_INPUTS[k])]
    grew = [k for k, v in REFERENCE_COMPRESSED.items() if len(v) > len(VECTOR_INPUTS[k])]
    assert len(shrank) == 6
    assert len(grew) == 7


# ──────────────────────────────────────────────────────────────
# Byte fidelity against the source compressor
# ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("label", sorted(VECTOR_INPUTS))
def test_compress_reproduces_the_source_compressor_byte_for_byte(label):
    # The claim the round-trip tests cannot make. A different-but-valid encoding
    # would satisfy every other test in this file.
    assert refpack_compress(VECTOR_INPUTS[label]) == REFERENCE_COMPRESSED[label]


@pytest.mark.parametrize("label", sorted(VECTOR_INPUTS))
def test_decompress_reads_the_source_compressors_output(label):
    # The other direction: this module's decompressor against streams it did not
    # write. Without it, an encoder and a decoder that were wrong in matching
    # ways would round-trip happily.
    assert refpack_decompress(REFERENCE_COMPRESSED[label]) == VECTOR_INPUTS[label]


def test_a_compressor_that_returned_its_input_would_fail_the_references():
    # Asked of every property in this file: would it still pass if `compress`
    # were the identity? Here it demonstrably would not, for a case whose output
    # is 15 bytes against 1200 in.
    assert refpack_compress(VECTOR_INPUTS["long_run"]) != VECTOR_INPUTS["long_run"]
    assert len(REFERENCE_COMPRESSED["long_run"]) == 15
    assert len(VECTOR_INPUTS["long_run"]) == 1200


# ──────────────────────────────────────────────────────────────
# Round trip over a wider corpus
# ──────────────────────────────────────────────────────────────


def _round_trip_corpus() -> dict[str, bytes]:
    """A deterministic corpus wider than the thirteen pinned vectors.

    The thirteen above are pinned byte for byte and so are expensive to add to;
    this one is free to grow and covers the shapes a byte-fidelity reference
    cannot afford to hold, above all the 200 KB incompressible case and the
    matches that sit exactly on an offset ceiling.
    """
    rng = random.Random(11235)
    cases: dict[str, bytes] = {"empty": b""}
    for n in range(1, 12):
        cases[f"tiny-{n}"] = bytes(range(n))
    # Around the literal chunk size (4, 112, 128) and the match ceilings.
    for n in (111, 112, 113, 115, 116, 127, 128, 129, 1027, 1028, 1029, 2048, 16385):
        cases[f"seq-{n}"] = bytes((i * 7 + 3) & 0xFF for i in range(n))
    for n in (1, 2, 3, 16, 255, 1000, 65536, 200000):
        cases[f"rand-{n}"] = bytes(rng.randrange(256) for _ in range(n))
    for n in (4, 5, 10, 11, 1028, 1029, 70000):
        cases[f"run-{n}"] = b"\xa5" * n
    # Repeats whose period sits either side of each offset ceiling, so at least
    # one case cannot be encoded by the command the previous one used.
    for period in (1, 2, 3, 4, 17, 1024, 1025, 16384, 16385, 131072):
        block = bytes((i * 31 + 11) & 0xFF for i in range(period))
        total = max(period * 3, 5000)
        cases[f"period-{period}"] = (block * (total // period + 1))[:total]
    # Random data punctuated by long runs: both families in one stream.
    mixed = bytearray()
    for _ in range(40):
        mixed += bytes(rng.randrange(256) for _ in range(rng.randrange(1, 60)))
        mixed += bytes([rng.randrange(256)]) * rng.randrange(1, 1500)
    cases["mixed"] = bytes(mixed)
    # The only match in the file is at the largest offset the format can express.
    cases["max_offset"] = (
        b"ABCDEFGH" + bytes(rng.randrange(256) for _ in range(131064)) + b"ABCDEFGH"
    )
    return cases


ROUND_TRIP_CORPUS = _round_trip_corpus()


def test_the_round_trip_corpus_is_the_size_it_claims():
    # Without this the parametrised round trip below could collect nothing and
    # report green. The byte total is pinned too: a corpus of 49 empty strings
    # would satisfy a count alone, and `decompress(compress(b"")) == b""` is
    # true of a great many wrong implementations.
    assert len(ROUND_TRIP_CORPUS) == 52
    assert sum(len(v) for v in ROUND_TRIP_CORPUS.values()) == 1048122


def test_the_round_trip_corpus_holds_more_than_one_distinct_value():
    # The other half of the zero-over-zero guard: 49 copies of one string would
    # pass the count and prove one case.
    assert len(set(ROUND_TRIP_CORPUS.values())) == 52


@pytest.mark.parametrize("label", sorted(ROUND_TRIP_CORPUS))
def test_decompress_undoes_compress(label):
    data = ROUND_TRIP_CORPUS[label]
    assert refpack_decompress(refpack_compress(data)) == data


@pytest.mark.parametrize("label", sorted(ROUND_TRIP_CORPUS))
def test_the_header_states_the_input_length(label):
    # The three size bytes are what the game allocates from. A compressor that
    # got everything else right and this wrong still round-trips through this
    # module, because `refpack_decompress` only ever truncates by it.
    data = ROUND_TRIP_CORPUS[label]
    stream = refpack_compress(data)
    assert stream[:2] == b"\x10\xfb"
    assert (stream[2] << 16) | (stream[3] << 8) | stream[4] == len(data)


def test_the_repetitive_cases_actually_compress():
    # Would every round-trip test above still pass if `compress` wrapped its
    # input in a header and emitted it all as literals? Yes. This is the
    # assertion that says the matcher runs at all, with exact lengths rather
    # than a ratio, so it fails on a change in either direction.
    assert len(refpack_compress(ROUND_TRIP_CORPUS["run-70000"])) == 283
    assert len(refpack_compress(ROUND_TRIP_CORPUS["period-17"])) == 44
    # And the converse, so the pair cannot both be satisfied by a constant.
    assert len(refpack_compress(ROUND_TRIP_CORPUS["rand-1000"])) == 1015


def test_an_overlapping_match_repeats_the_byte_it_points_at():
    # Offset 1 with a length of 40 means "copy the byte before you, 40 times",
    # and a decompressor that read its source buffer as a slice before the copy
    # would emit garbage for 39 of them. `run_of_one` is 10 compressed bytes.
    assert refpack_decompress(REFERENCE_COMPRESSED["run_of_one"]) == b"\x5a" * 40


# ──────────────────────────────────────────────────────────────
# The encoder and the decoder as inverses, independent of the matcher
# ──────────────────────────────────────────────────────────────

# (length, offset) pairs on and either side of every boundary the three copy
# encodings have. `_is_encodable` decides which are emittable, and the test below
# holds it to `_emit_copy` for the ones it accepts.
_COPY_GRID = [
    (3, 1),
    (3, 1024),
    (3, 1025),  # too far for the only encoding that takes a length of 3
    (4, 1),
    (4, 16384),
    (4, 16385),  # length 4 is below the 4-byte command's minimum of 5
    (5, 131072),
    (5, 131073),  # past every offset the format can express
    (10, 1024),
    (10, 16384),
    (10, 131072),
    (11, 1024),
    (67, 16384),
    (68, 16384),
    (68, 16385),
    (1028, 1),
    (1028, 131072),
    (1029, 1),  # past the longest match any command encodes
]


def _stream_with_one_copy(prefix: bytes, length: int, offset: int) -> bytes:
    """A hand-built stream: `prefix` as literals, then one copy, then the end.

    Built here rather than by `refpack_compress` on purpose. The compressor
    chooses which command to emit, so it can only ever reach the encodings its
    own matcher happens to pick; this reaches each of the three directly.
    """
    if len(prefix) % 4 != 0:
        raise ValueError("literal-only commands come in multiples of four")
    total = len(prefix) + length
    out = bytearray(b"\x10\xfb")
    out += bytes([(total >> 16) & 0xFF, (total >> 8) & 0xFF, total & 0xFF])
    pos = 0
    while pos < len(prefix):
        chunk = min(len(prefix) - pos, 112)
        out.append(0xE0 + ((chunk - 4) >> 2))
        out += prefix[pos : pos + chunk]
        pos += chunk
    _emit_copy(out, 0, b"", length, offset)
    out.append(0xFC)
    return bytes(out)


def test_the_copy_grid_covers_both_answers():
    # A grid that `_is_encodable` accepted or refused unanimously would make one
    # of the two tests below collect nothing meaningful.
    encodable = [pair for pair in _COPY_GRID if _is_encodable(*pair)]
    refused = [pair for pair in _COPY_GRID if not _is_encodable(*pair)]
    assert len(encodable) == 14
    assert len(refused) == 4
    assert refused == [(3, 1025), (4, 16385), (5, 131073), (1029, 1)]


@pytest.mark.parametrize(("length", "offset"), [p for p in _COPY_GRID if _is_encodable(*p)])
def test_every_encodable_copy_decodes_back_to_the_same_bytes(length, offset):
    # `_emit_copy`'s bit shuffling is the inverse of `refpack_decompress`'s, and
    # the two are 60 lines apart. A dropped mask in either shows up as the wrong
    # offset or the wrong length here, whatever the matcher would have chosen.
    # The literal-only command emits in multiples of four, so the prefix is
    # rounded up and the match still measured back from its end.
    prefix = bytes((i * 37 + 5) & 0xFF for i in range(-(-max(offset, 4) // 4) * 4))
    source = prefix[len(prefix) - offset :]
    expected = (source * (length // len(source) + 1))[:length]
    assert refpack_decompress(_stream_with_one_copy(prefix, length, offset)) == prefix + expected


@pytest.mark.parametrize(("length", "offset"), [p for p in _COPY_GRID if not _is_encodable(*p)])
def test_a_refused_pair_is_one_no_command_could_hold(length, offset):
    # The refusals are load-bearing: the compressor drops these matches and
    # writes literals instead. If `_is_encodable` accepted one, `_emit_copy`'s
    # `else` branch would silently truncate it into a valid command naming a
    # different length or a different offset.
    assert _is_encodable(length, offset) is False


# ──────────────────────────────────────────────────────────────
# Rejections, and the two things this module does NOT check
# ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "data",
    [b"", b"\x10", b"\x10\xfb", b"\x10\xfb\x00", b"\x10\xfb\x00\x00"],
)
def test_a_stream_shorter_than_its_header_is_refused(data):
    with pytest.raises(EaTdbError, match="Not RefPack data"):
        refpack_decompress(data)


@pytest.mark.parametrize("data", [b"\x11\xfb\x00\x00\x00", b"\x10\xfc\x00\x00\x00", b"BIGF\x00"])
def test_a_stream_without_the_magic_is_refused(data):
    with pytest.raises(EaTdbError, match="Not RefPack data"):
        refpack_decompress(data)


def test_the_refusal_is_a_rom_error_so_a_patcher_can_catch_it():
    # The reason it is not a `ValueError`: `analyze_rom` and `patch` promise
    # `RomError`, and a `db.viv` that is not RefPack is a fact about the user's
    # disc rather than a bug in this library.
    from retro_roster_patcher.core.errors import RetroRosterError, RomError

    assert issubclass(EaTdbError, RomError) is True
    assert issubclass(EaTdbError, RetroRosterError) is True


def test_a_truncated_stream_returns_short_without_complaint():
    # Pinned rather than endorsed. The source behaves this way and three games
    # are about to be ported onto it, so the behaviour is written down here; the
    # module's docstring says the same. A decompressor that raised would be the
    # better design and would be a divergence, not a port.
    full = REFERENCE_COMPRESSED["csv"]
    got = refpack_decompress(full[: len(full) // 2])
    assert len(got) == 312
    assert got == VECTOR_INPUTS["csv"][:312]


def test_a_header_claiming_more_than_the_commands_emit_is_not_enforced():
    # The size in the header is a ceiling, never a floor. Nothing pads up to it.
    stream = bytearray(refpack_compress(b"abcd"))
    stream[4] = 0xFF
    assert refpack_decompress(bytes(stream)) == b"abcd"


def test_a_header_claiming_less_than_the_commands_emit_truncates():
    # The one thing the size field does do. `b"abcd"` compresses to a literal
    # command plus an end marker; asking for two bytes yields two bytes.
    stream = bytearray(refpack_compress(b"abcd"))
    stream[4] = 2
    assert refpack_decompress(bytes(stream)) == b"ab"
