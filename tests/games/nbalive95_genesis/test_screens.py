"""NBA Live 95 title art: the encoder, without a cartridge."""

from PIL import Image, ImageDraw

from retro_roster_patcher.games.nbalive95_genesis import screens


def test_a_picture_becomes_tiles_a_full_map_and_three_lines_that_spare_live():
    picture = Image.new("RGB", screens.SIZE, (10, 20, 80))
    draw = ImageDraw.Draw(picture)
    for x in range(0, screens.SIZE[0], 7):
        draw.line([(x, 0), (screens.SIZE[0] - x, screens.SIZE[1])], fill=(x % 255, 200, 40))
    tiles, entries, lines = screens.encode(picture)
    count = int.from_bytes(tiles[:2], "big")
    assert 1 <= count <= screens.MAX_TILES and len(tiles) == 2 + 32 * count
    assert len(entries) == screens.COLUMNS * screens.ROWS
    assert {e >> 13 for e in entries} <= {0, 1, 2}
    assert [lines[1][i] for i in screens.RESERVED[1]] == [None] * 4
    assert lines[0][0] == 0 and any(w is not None for w in lines[0][1:])
