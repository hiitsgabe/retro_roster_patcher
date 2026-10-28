"""ISS 2000 (N64): the pieces that need no cartridge."""

import os
import random

import pytest

from retro_roster_patcher.core.registry import get_patcher
from retro_roster_patcher.games.iss2000_n64 import rom as R
from retro_roster_patcher.games.iss2000_n64 import screens
from retro_roster_patcher.games.iss2000_n64.mapper import nearest_formation, parse_formation


def test_lz_round_trips_runs_copies_and_literals():
    rng = random.Random(7)
    noise = bytes(rng.randrange(4) for _ in range(5000))
    data = noise + bytes(600) + b"\x55" * 90 + os.urandom(300)
    packed = R.lz_compress(data)
    assert R.lz_decompress(packed) == data
    assert len(packed) < len(data)


def test_names_use_the_cartridge_code():
    assert R.encode_name("Gahn") == bytes([0x11, 0x25, 0x2C, 0x32]) + bytes(4)
    assert R.decode_name(R.encode_name("Ødegaard")) == "Odegaard"
    assert R.decode_name(R.encode_name("Lewis-Skelly")) == "Lewis-Sk"


def test_formations_fall_back_to_ones_the_cartridge_has():
    assert parse_formation("4-2-3-1") == (4, 5, 1)
    assert nearest_formation((4, 5, 1)) == (4, 5, 1)
    assert nearest_formation((5, 4, 1)) == (4, 4, 2)


def test_wallpaper_texels_are_distinct_and_inside_the_asset():
    seen = {screens.texel_offset(x, y) for y in range(2, 238) for x in range(4, 316)}
    assert len(seen) == 312 * 236
    assert max(seen) < 159552


def test_registered_with_its_capabilities():
    cls = get_patcher("iss2000-n64")
    assert cls.requires_slot_mapping and cls.supports_looks and cls.image_slots == ("menu", "title")


def test_a_given_squad_starts_one_keeper_in_goal():
    from retro_roster_patcher.games.iss2000_n64.mapper import N64StatMapper
    from retro_roster_patcher.sports.models import Player

    def p(i, pos):
        return Player(id=i, name=f"P{i}", position=pos)

    # ESPN's raw order: two keepers first, then outfielders.
    squad = [p(1, "Goalkeeper"), p(2, "Goalkeeper")] + [
        p(i, pos)
        for i, pos in enumerate(["Defender"] * 5 + ["Midfielder"] * 6 + ["Attacker"] * 4, start=3)
    ]
    xi, shape = N64StatMapper().given_22(squad)
    assert [x.id for x in xi[:2]] == [1, 3]  # one keeper, then the defence
    assert shape == (4, 5, 1)  # 5 DF and 5 MF made it: nearest shape


def test_a_formation_comes_from_the_donor_as_shipped():
    from retro_roster_patcher.games.iss2000_n64 import writer as W
    from retro_roster_patcher.games.iss2000_n64.mapper import FORMATION_DONORS

    image = bytearray(R.METADATA + 120 * 6)
    for donor in FORMATION_DONORS.values():
        image[R.FORMATIONS + donor * R.FORMATION_ROW] = donor + 1
    donors = W.donor_rows(image)
    # The 4-5-1 donor (slot 20) is itself patched with a 4-3-3 first ...
    W.write_formation(image, FORMATION_DONORS[(4, 5, 1)], (4, 3, 3), donors)
    # ... and a later 4-5-1 still gets the shipped 4-5-1 row.
    W.write_formation(image, 69, (4, 5, 1), donors)
    assert image[R.FORMATIONS + 69 * R.FORMATION_ROW] == FORMATION_DONORS[(4, 5, 1)] + 1


def test_a_kit_recolours_the_shading_ramps_and_keeps_the_skin():
    import struct

    from retro_roster_patcher.core.kits import KitSpec
    from retro_roster_patcher.games.iss2000_n64 import kits as K

    def word(r, g, b):
        return (r >> 3) << 11 | (g >> 3) << 6 | (b >> 3) << 1 | 1

    yellow = [word(240 - 30 * i, 210 - 26 * i, 40) for i in range(5)]
    record = bytearray(512)
    struct.pack_into(">16H", record, K.SHIRT, *yellow, *[word(255, 255, 255)] * 11)
    skin = word(230, 180, 140)
    shorts = [word(40, 60, 200 - 30 * i) for i in range(6)] + [word(255, 255, 255), skin] + [0] * 8
    struct.pack_into(">16H", record, K.SHORTS, *shorts)
    kit = KitSpec(
        shirt=(0, 0, 0),
        secondary=(255, 255, 255),
        pattern="plain",
        shorts=(255, 0, 0),
        socks=(0, 0, 255),
    )
    K.paint(record, kit)
    out = struct.unpack_from(">16H", record, K.SHORTS)
    assert K._split(out[0]) == (31, 0, 0)  # the lightest shade is the new colour
    assert out[7] == skin  # past the ramp nothing changes
    shirt = struct.unpack_from(">16H", record, K.SHIRT)
    assert all(K._split(c)[1] == 0 for c in shirt[:5])  # black, shaded
    assert K.KIT_INDEX[69] == 23 and len(set(K.KIT_INDEX)) == 100


def test_team_select_opens_on_the_clubs_page():
    import struct

    from retro_roster_patcher.games.iss2000_n64 import writer as W

    assert W.start_page([69, 70, 71]) == 6  # South America starts at 69
    assert W.start_page([3, 4, 61]) == 5  # CONCACAF's first team is a club
    assert W.start_page([40, 41]) == 3  # no page starts on a club: the lowest's page
    image = bytearray(W.TEAM_SELECT_PAGE_LOAD + 4)
    struct.pack_into(">I", image, W.TEAM_SELECT_PAGE_LOAD, 0x90840B4F)
    assert W.write_start_page(image, [69])
    assert struct.unpack_from(">I", image, W.TEAM_SELECT_PAGE_LOAD)[0] == 0x24040006
    assert W.write_start_page(image, [0])  # a patched image patches again
    assert not W.write_start_page(bytearray(W.TEAM_SELECT_PAGE_LOAD + 4), [0])


def test_rewritten_assets_stay_inside_16_mib():
    image = bytearray(R.ROM_SIZE)
    # One asset at the table, the 0xFF padding after it.
    image[R.ASSET_TABLE : R.ASSET_TABLE + 8] = (0x100000 | 0x80000000).to_bytes(4, "big") + (
        0x100100
    ).to_bytes(4, "big")
    image[0x100100:] = b"\xff" * (R.ROM_SIZE - 0x100100)
    space = R.AssetSpace(image)
    asset = R.assets(image)[0]
    space.put(asset, os.urandom(4096))  # too big for its 256 bytes
    moved = R.assets(image)[0]
    assert len(image) == R.ROM_SIZE and not space.grew
    assert 0x100100 <= moved.start < R.ROM_SIZE


def test_title_texels_cover_the_asset_once():
    seen = {screens.title_texel(x, y) for y in range(240) for x in range(320)}
    assert seen == set(range(320 * 240))


def _cartridge(title_room: int) -> bytearray:
    """A blank image with the asset table filled up to the title, the title
    last and only `title_room` bytes of padding after it."""
    image = bytearray(R.ROM_SIZE)
    blank = R.lz_compress(bytes(160000))
    title = R.lz_compress(bytes(153600))
    image[0x100000 : 0x100000 + len(blank)] = blank
    at = R.ROM_SIZE - title_room - len(title)
    image[at : at + len(title)] = title
    for k in range(screens.TITLE_ASSET + 1):
        start, end = (
            (at, at + len(title)) if k == screens.TITLE_ASSET else (0x100000, 0x100000 + len(blank))
        )
        image[R.ASSET_TABLE + 8 * k : R.ASSET_TABLE + 8 * k + 8] = (start | 0x80000000).to_bytes(
            4, "big"
        ) + end.to_bytes(4, "big")
    image[at + len(title) :] = b"\xff" * (R.ROM_SIZE - at - len(title))
    return image


def test_title_round_trips_through_the_cartridge(tmp_path):
    from io import BytesIO

    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw

    pic = Image.new("RGB", (320, 240), (0, 96, 40))
    draw = ImageDraw.Draw(pic)
    draw.ellipse((110, 20, 210, 120), fill=(248, 216, 0))
    draw.rectangle((0, 225, 319, 239), fill=(200, 0, 0))  # the bottom strip
    png = BytesIO()
    pic.save(png, "PNG")
    image = _cartridge(200000)
    space = R.AssetSpace(image)
    screens.write(image, space, {"title": png.getvalue()})
    assert len(image) == R.ROM_SIZE and not space.grew
    out = Image.open(screens.export(image, tmp_path)[1])
    assert list(out.getdata()) == [  # 5 bits a channel
        tuple((c >> 3) * 255 // 31 for c in p) for p in pic.getdata()
    ]


def test_a_busy_title_drops_colours_rather_than_grow_the_image(tmp_path):
    import math
    from io import BytesIO

    pytest.importorskip("PIL")
    from PIL import Image

    rng = random.Random(3)
    pic = Image.new("RGB", (320, 240))
    pic.putdata(
        [
            (
                int(128 + 100 * math.sin(x / 17) * math.cos(y / 23)) + rng.randrange(-6, 7),
                int(128 + 90 * math.sin((x + y) / 29)),
                x * y % 256,
            )
            for y in range(240)
            for x in range(320)
        ]
    )  # about 150 KB packed in full colour, 64 KB in 256 colours
    png = BytesIO()
    pic.save(png, "PNG")
    image = _cartridge(70000)
    space = R.AssetSpace(image)
    screens.write(image, space, {"title": png.getvalue()})
    assert len(image) == R.ROM_SIZE and not space.grew
    out = Image.open(screens.export(image, tmp_path)[1])
    assert len(set(out.getdata())) <= 256
