"""The Genesis picture encoder every Genesis game's screen art goes through."""

import pytest

from retro_roster_patcher.formats import genesis

Image = pytest.importorskip("PIL.Image")


def _palette64(encoded):
    """The 64 CRAM words an encoded picture draws with, 0 where it set none."""
    words = [0] * 64
    for line, sixteen in encoded.palettes.items():
        for i, w in enumerate(sixteen):
            if w is not None:
                words[line * 16 + i] = w
    return words


def test_rgb_and_word_are_inverse_on_the_eight_levels():
    for level in genesis.LEVELS:
        colour = (level, genesis.LEVELS[2], 255)
        assert genesis.rgb(genesis.word(colour)) == colour
    assert genesis.word((255, 0, 0)) == 0x00E
    assert genesis.word((0, 0, 255)) == 0xE00


def test_a_two_colour_picture_round_trips_through_its_tiles():
    picture = Image.new("RGB", (16, 8), (255, 0, 0))
    picture.paste((0, 0, 255), (8, 0, 16, 8))
    encoded = genesis.encode(picture, (16, 8), (0,), {0: tuple(range(1, 16))}, 4)
    assert len(encoded.tiles) == 2 and all(len(t) == 32 for t in encoded.tiles)
    assert [e >> 13 for e in encoded.entries] == [0, 0]
    back = genesis.render_tiles(b"".join(encoded.tiles), encoded.entries, 2, _palette64(encoded))
    assert back.tobytes() == picture.tobytes()


def test_a_tile_budget_merges_tiles_alike_when_shrunk():
    # Eight red tiles, each with one blue pixel somewhere else: eight distinct
    # tiles, alike at 4x4, so a budget of one keeps the first. (Tiles that differ
    # even shrunk to one pixel are kept: the budget is a target, not a promise.)
    picture = Image.new("RGB", (64, 8), (255, 0, 0))
    for k in range(8):
        picture.putpixel((k * 8 + k, k), (0, 0, 255))
    encoded = genesis.encode(picture, (64, 8), (0,), {0: tuple(range(1, 16))}, 1)
    assert len(encoded.tiles) == 1
    assert [e & 0x7FF for e in encoded.entries] == [0] * 8


def test_a_colour_number_the_picture_may_not_set_stays_none():
    picture = Image.new("RGB", (8, 8), (0, 255, 0))
    encoded = genesis.encode(picture, (8, 8), (2,), {2: (1, 2, 3)}, 4)
    assert set(encoded.palettes) == {2}
    words = encoded.palettes[2]
    assert words[0] is None and words[4:] == [None] * 12
    assert genesis.word((0, 255, 0)) in words[1:4]
    assert {p for t in encoded.tiles for b in t for p in (b >> 4, b & 15)} <= {1, 2, 3}
