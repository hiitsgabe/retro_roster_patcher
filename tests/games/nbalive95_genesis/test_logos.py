"""NBA Live 95 team-select logos and season totals, without a cartridge."""

import io
import struct

from PIL import Image

from retro_roster_patcher.games.nbalive95_genesis import logos
from retro_roster_patcher.games.nbalive95_genesis.space import FREE, Space
from retro_roster_patcher.games.nbalive95_genesis.stat_mapper import (
    SEASON_TOTALS,
    NBALive95StatMapper,
)
from retro_roster_patcher.sports.models import Player


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


def test_a_head_shot_becomes_a_refpack_head_frame():
    from retro_roster_patcher.formats.ea_tdb import refpack_decompress
    from retro_roster_patcher.games.nbalive95_genesis import portraits

    picture = Image.new("RGBA", (64, 96), (0, 0, 0, 0))
    picture.paste((20, 20, 20, 255), (16, 8, 48, 90))
    buf = io.BytesIO()
    picture.save(buf, "PNG")
    data = portraits.tiles(portraits.crop(buf.getvalue()))
    assert len(data) == 24 * 32
    image = _image()
    index = portraits.entry(17, 1)
    struct.pack_into(">I", image, logos.LOGO_TABLE + 4 * index, 0x100000 - logos.LOGO_BASE)
    struct.pack_into(">7h", image, 0x100000, 27, 47, 14, 64, 3, 23, 2)
    blob = portraits.frame(image, index, data)
    assert struct.unpack_from(">7h", blob) == (32, 48, 14, 64, 2, 24, 2)
    assert refpack_decompress(blob[14 + 16 :]) == data
