"""NBA Live 95 team-select logos and season totals, without a cartridge."""

import io
import struct

import pytest

from retro_roster_patcher.games.nbalive95_genesis import logos
from retro_roster_patcher.games.nbalive95_genesis.space import FREE, Space
from retro_roster_patcher.games.nbalive95_genesis.stat_mapper import (
    SEASON_TOTALS,
    NBALive95StatMapper,
)
from retro_roster_patcher.sports.models import Player

Image = pytest.importorskip("PIL.Image")


def _image():
    image = bytearray(0x200000)
    # The shared palette: colour 1 red, 2 white, the rest black.
    struct.pack_into(">16H", image, logos.PALETTE, 0, 0x00E, 0xEEE, *([0] * 13))
    for start, end in FREE:
        image[start:end] = b"\xff" * (end - start)
    return image


def test_a_logo_is_drawn_in_the_shared_palette_and_read_back():
    png = Image.new("RGBA", (48, 40), (255, 0, 0, 255))
    png.paste((255, 255, 255, 255), (0, 0, 24, 40))
    buf = io.BytesIO()
    png.save(buf, "PNG")
    image = _image()
    assert logos.write(image, {9: buf.getvalue()}, Space()) == [9]
    start = struct.unpack_from(">I", image, logos.LOGO_TABLE + 36)[0] + logos.LOGO_BASE
    assert FREE[0][0] == start
    rows = logos.read(image, 9)
    assert len(rows) == 40 and len(rows[0]) == 48
    assert rows[5][3] == 2 and rows[5][40] == 1


def test_the_season_line_is_the_players_totals():
    line = dict.fromkeys(SEASON_TOTALS, 0.0) | {"gamesPlayed": 72.0, "points": 1621.0}
    rec = NBALive95StatMapper().map_player(Player(id=1, name="A B", position="PG"), line)
    assert rec.season_stats[0] == 72 and rec.season_stats[14] == 1621
    assert NBALive95StatMapper().map_player(Player(id=1, name="A B")).season_stats == [0] * 17


def test_a_head_shot_becomes_a_whole_portrait_and_the_bodies_go_blank():
    from retro_roster_patcher.formats.ea_tdb import refpack_decompress
    from retro_roster_patcher.games.nbalive95_genesis import portraits

    picture = Image.new("RGBA", (120, 90), (0, 0, 0, 0))
    picture.paste((20, 20, 20, 255), (30, 8, 90, 76))
    buf = io.BytesIO()
    picture.save(buf, "PNG")
    data = portraits.tiles(portraits.crop(buf.getvalue()))
    assert len(data) == 64 * 32
    blob = portraits.frame(data)
    assert struct.unpack_from(">7h", blob) == (64, 64, 32, 66, 4, 64, 2)
    assert refpack_decompress(blob[14 + 32 :]) == data
    image = _image()
    for index in portraits.BODIES:
        struct.pack_into(">I", image, logos.LOGO_TABLE + 4 * index, 0x100000 - logos.LOGO_BASE)
    portraits.blank_bodies(image, Space())
    starts = {logos.frame_start(image, i) for i in portraits.BODIES}
    assert len(starts) == 1 and image[starts.pop() :][: len(portraits.EMPTY)] == portraits.EMPTY
