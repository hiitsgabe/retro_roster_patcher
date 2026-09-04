"""The inputs whose exact compressed form `test_refpack.py` pins.

Kept apart from the test that uses them because the reference outputs beside
them in `test_refpack.py` were produced by running the *source* compressor —
the one inside `console_utilities` this module was ported from — over exactly
these thirteen inputs. Anything that edits an input here invalidates the
corresponding reference, and having the two in separate files makes that hard
to do by accident.

Each entry exists to force a particular command out of the encoder, and
`test_refpack.py::test_the_corpus_between_them_uses_every_command_family`
decodes the references and fails if any family stops appearing. Between them
they cover all four copy encodings, the literal-only command, and end markers
carrying zero, one and three trailing literals.

The inputs are built here rather than written out as literals because two of
them are 20 KB, and a repr that long is not something a reader can check.
"""

import random

# Seeded once, at import. The two incompressible cases must be byte-identical
# every run or the references below them are meaningless; `random.Random` with
# an explicit seed is reproducible across CPython versions for `randrange`.
_rng = random.Random(20260904)


def _hash_chain_input() -> bytes:
    """A repeat the compressor finds only because its hash spreads three bytes.

    `refpack_compress` buckets a position by `(b0 << 8) ^ (b1 << 4) ^ b2` and
    walks at most 128 entries of a bucket's chain looking for the longest match.
    This builds a file where 129 *different* three-byte groups would share a
    bucket if the first shift were 7 instead of 8, so under that one-character
    change the chain overflows and the only real match in the file is never
    reached.

    Built rather than written out because it has to be derived from the hash:
    a literal would be a magic blob that nobody could check or regenerate.
    """
    block = bytes((0x51, 0x52, 0x53))
    target = ((0x51 << 7) ^ (0x52 << 4) ^ 0x53) & 0xFFFF
    groups = []
    for b0 in range(1, 256):
        for b1 in range(1, 256):
            b2 = target ^ ((b0 << 7) & 0xFFFF) ^ ((b1 << 4) & 0xFFFF)
            if 1 <= b2 <= 255 and (b0, b1, b2) != (0x51, 0x52, 0x53):
                groups.append(bytes((b0, b1, b2)))
            if len(groups) == 129:
                break
        if len(groups) == 129:
            break
    payload = block + b"TUVWXY"
    return payload + b"".join(groups) + payload


VECTOR_INPUTS: dict[str, bytes] = {
    # Nothing at all: a header and a bare end marker, six bytes.
    "empty": b"",
    # One byte, which has to ride out on the end marker as a trailing literal.
    "one_byte": b"Q",
    # Three, the most an end marker can carry.
    "three_bytes": b"abc",
    # Four, which is one literal-only command's minimum, then an empty end marker.
    "four_bytes": b"wxyz",
    # An eight-byte repeat eight bytes back: length 8, offset 8, the 2-byte copy.
    "short_repeat": b"ABCDEFGH" + b"ABCDEFGH",
    # A 40-byte run of one byte. The match overlaps its own output at offset 1,
    # which is the case a slice-based decompressor gets wrong.
    "run_of_one": b"\x5a" * 40,
    # A 20-byte cycle three times over: matches longer than the 2-byte command's
    # 10-byte ceiling, so the 3-byte copy.
    "medium_repeat": b"0123456789ABCDEFGHIJ" * 3,
    # The same eight bytes 20008 apart, with a compressible gap so the reference
    # stays short. Offset past 16384 forces the 4-byte copy.
    "far_repeat": b"MARKERXY" + b"\x00" * 20000 + b"MARKERXY",
    # A 1200-byte run: longer than the 1028-byte match ceiling, so it cannot be
    # one command however far the encoder looks.
    "long_run": b"\xc3" * 1200,
    # The record shape MVP Baseball's `database.big` actually holds.
    "csv": b"".join(
        b"%08x,0 %d,1 PLAYER%03d,;\r\n" % (i * 2654435761 & 0xFFFFFFFF, i, i) for i in range(24)
    ),
    # Incompressible: literal commands and nothing else, and 3% larger out than in.
    "random": bytes(_rng.randrange(256) for _ in range(300)),
    # Exactly 112 literals, the largest chunk `flush_literals` will emit at once.
    "chunk_cap": bytes(_rng.randrange(256) for _ in range(112)),
    # A 4-byte match at one position with a 9-byte match one byte later, which is
    # what makes the lazy-match branch take a different path from the greedy one.
    "lazy": b"WXYZ" + b"QWXYZRSTUV" + b"QQQQ" + b"WXYZRSTUVX",
    # Three distinct byte values over 400 bytes. Every hash bucket holds dozens
    # of positions and almost every position has several candidate matches of
    # nearly equal length, which is what makes the encoder's two search
    # parameters observable: mutation testing showed that relaxing the lazy
    # rule from `next_length > length + 1` to `> length`, or cutting the chain
    # depth from 128 to 4, changes no output in any other vector here and
    # changes this one.
    "low_alphabet": bytes(_rng.randrange(3) for _ in range(400)),
    # A nine-byte block, then 129 three-byte groups chosen so that the mutant
    # hash `(b0 << 7) ^ (b1 << 4) ^ b2` maps every one of them onto the same
    # bucket as the block's first three bytes, then the block again. Under the
    # real hash the chain for that bucket holds one entry and the repeat is
    # found; under the mutant it holds 130 and the chain-depth limit of 128
    # discards the only real one. Nothing else in this corpus can see the
    # difference — 129 is the smallest count that does.
    "hash_chain": _hash_chain_input(),
}
