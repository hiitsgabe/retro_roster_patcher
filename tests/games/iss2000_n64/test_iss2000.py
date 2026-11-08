"""ISS 2000 (N64): the pieces that need no cartridge."""

import os
import random

import pytest

from retro_roster_patcher.core.registry import get_patcher
from retro_roster_patcher.games.iss2000_n64 import rom as R
from retro_roster_patcher.games.iss2000_n64 import screens
from retro_roster_patcher.games.iss2000_n64.mapper import (
    N64StatMapper,
    nearest_formation,
    parse_formation,
)
from retro_roster_patcher.sports.models import Player, Team, TeamRoster
from tests.fixtures.older_roster import older_roster

# The eleven places of a 4-2-3-1 as ESPN codes them, the squad group of the player at each, and
# the line each place is on once the formation is folded to 4-3-3 (the lone striker is joined by
# the two wide attacking midfielders; the one behind him stays in midfield).
C4231 = ["G", "RB", "LB", "LM", "CD-R", "CD-L", "AM-R", "RM", "F", "AM", "AM-L"]
SQUAD_GROUPS = [
    "Goalkeeper", "Defender", "Defender", "Midfielder", "Defender", "Defender",
    "Midfielder", "Midfielder", "Attacker", "Midfielder", "Midfielder",
]  # fmt: skip
LINES = ["GK", "DF", "DF", "MF", "DF", "DF", "FW", "MF", "FW", "MF", "FW"]


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
    # A tie in the line counts goes to the nearest number of defenders, as in the other games: a
    # back five stays a back five.
    assert nearest_formation((5, 4, 1)) == (5, 3, 2)
    assert nearest_formation((5, 2, 3)) == (5, 3, 2)


def _club_in_a_4231(starters, **lineup):
    """A club of ids 1-11 whose last lineup was a 4-2-3-1, `starters` in place order; `lineup`
    adds keys to it."""
    players = [Player(id=i, name=f"P{i}", position=g) for i, g in enumerate(SQUAD_GROUPS, start=1)]
    extra = {"lineup": {"formation": "4-2-3-1", "starters": starters, **lineup}}
    return TeamRoster(team=Team(id=1, name="Club"), players=players, extra=extra)


def _starters_in_a_4231():
    """The eleven of `_club_in_a_4231`, each on the line the lineup gives him."""
    return [
        {"id": i, "position": code, "line": line}
        for i, (code, line) in enumerate(zip(C4231, LINES, strict=True), start=1)
    ]


def test_a_four_two_three_one_lineup_plays_four_three_three():
    starters = [
        {"id": i, "position": code, "line": line}
        for i, (code, line) in enumerate(zip(C4231, LINES, strict=True), start=1)
    ]
    m = N64StatMapper()
    _, shape = m.select_22(_club_in_a_4231(starters, shape="4-3-3"))
    assert shape == (4, 3, 3)
    assert m._lines[7] == m._lines[11] == 3  # the wide attacking midfielders play up front
    assert m._lines[10] == 2  # the one behind the striker stays in midfield


def test_an_old_lineup_without_shape_still_folds():
    starters = [{"id": i, "position": code} for i, code in enumerate(C4231, start=1)]
    _, shape = N64StatMapper().select_22(_club_in_a_4231(starters))
    assert shape == (4, 3, 3)


def test_wallpaper_texels_are_distinct_and_inside_the_asset():
    seen = {screens.texel_offset(x, y) for y in range(2, 238) for x in range(4, 316)}
    assert len(seen) == 312 * 236
    assert max(seen) < 159552


def test_registered_with_its_capabilities():
    cls = get_patcher("iss2000-n64")
    assert cls.requires_slot_mapping and cls.supports_looks and cls.image_slots == ("menu", "title")


def test_iss2000_writes_its_seven_formations():
    cls = get_patcher("iss2000-n64")
    assert cls.writes_formation is True
    assert cls.formations == ("4-4-2", "4-5-1", "4-3-3", "3-5-2", "3-6-1", "3-4-3", "5-3-2")


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


def _squad_in_a_433(order=range(1, 23), **extra):
    """A club of ids 1-22 whose natural order is a 4-3-3's keeper, 4 defenders, 3 midfielders and
    3 forwards, then a bench of a second keeper, 3 defenders, 4 midfielders and 3 forwards; the
    squad is given in `order`."""
    groups = (
        ["Goalkeeper"] + ["Defender"] * 4 + ["Midfielder"] * 3 + ["Attacker"] * 3
        + ["Goalkeeper"] + ["Defender"] * 3 + ["Midfielder"] * 4 + ["Attacker"] * 3
    )  # fmt: skip
    by_id = {i: Player(id=i, name=f"P{i}", position=g) for i, g in enumerate(groups, start=1)}
    return TeamRoster(team=Team(id=1, name="Club"), players=[by_id[i] for i in order], extra=extra)


def _first_eleven_lineup(shape):
    """The lineup of a club whose players 1-11 started, in a formation folded to `shape`."""
    return {"formation": shape, "shape": shape, "starters": [{"id": i} for i in range(1, 12)]}


def test_an_edited_formation_reads_the_order_by_its_lines():
    m = N64StatMapper()
    team = m.map_team(_squad_in_a_433(formation="4-4-2"), {}, order_as_given=True)
    assert team.formation == (4, 4, 2)
    assert [p.player_id for p in team.players[:11]] == list(range(1, 12))  # the order, as given
    assert m._lines[9] == 2  # a forward by position, ninth in the order: the fourth midfielder
    assert team.players[8].position_bits & R.MF
    assert m._lines[10] == m._lines[11] == 3


def test_an_edited_formation_beats_the_suggested_xi_shortcut():
    # The lineup says 4-3-3 and the squad comes in `suggest_squad_order`'s order, so the suggested
    # XI is the given one; the formation picked since is the one to write.
    m = N64StatMapper()
    roster = _squad_in_a_433(lineup=_first_eleven_lineup("4-3-3"), formation="4-4-2")
    roster.players = m.select_22(roster)[0]
    assert {p.id for p in roster.players[:11]} == set(range(1, 12))
    assert m.map_team(roster, {}, order_as_given=True).formation == (4, 4, 2)


def test_a_formation_the_cartridge_lacks_takes_its_nearest_for_lines_and_shape():
    m = N64StatMapper()
    team = m.map_team(_squad_in_a_433(formation="5-4-1"), {}, order_as_given=True)
    nearest = nearest_formation((5, 4, 1))
    assert team.formation == nearest
    lines = list(m._lines.values())
    assert tuple(lines.count(ln) for ln in (1, 2, 3)) == nearest


def test_the_first_keeper_in_the_list_starts_whatever_slot_one_holds():
    # A defender first and the only keeper third.
    groups = ["Defender", "Midfielder", "Goalkeeper"] + ["Defender"] * 3 + ["Midfielder"] * 4
    groups += ["Attacker"] * 2
    squad = [Player(id=i, name=f"P{i}", position=g) for i, g in enumerate(groups, start=1)]
    m = N64StatMapper()
    xi, shape = m.given_22(squad, "4-4-2")
    assert [x.id for x in xi] == [3, 1, 2, *range(4, 13)]  # the keeper in goal, the rest as given
    assert shape == (4, 4, 2)
    assert list(m._lines.values()).count(0) == 1
    assert m._lines[3] == 0


def test_the_goal_is_slot_one_even_without_a_keeper_among_the_first_eleven():
    m = N64StatMapper()
    # The only keeper below the eleventh place: he starts and the eleventh outfielder makes way.
    groups = ["Defender"] * 4 + ["Midfielder"] * 4 + ["Attacker"] * 3 + ["Goalkeeper"]
    squad = [Player(id=i, name=f"P{i}", position=g) for i, g in enumerate(groups, start=1)]
    xi, _ = m.given_22(squad, "4-4-2")
    assert [x.id for x in xi] == [12, *range(1, 12)]
    assert m._lines[12] == 0 and len(m._lines) == 11
    # No keeper at all: the first player stands in.
    xi, _ = m.given_22(squad[:11], "4-4-2")
    assert [x.id for x in xi] == list(range(1, 12))
    assert m._lines[1] == 0 and len(m._lines) == 11


def test_the_given_eleven_never_grows_past_eleven():
    # Eleven outfielders first and the only keeper twelfth, no formation: he starts, the eleventh
    # outfielder goes to the bench and nobody is listed twice.
    groups = ["Defender"] * 4 + ["Midfielder"] * 4 + ["Attacker"] * 3 + ["Goalkeeper"]
    groups += ["Defender"] * 3 + ["Midfielder"] * 4 + ["Attacker"] * 3
    squad = [Player(id=i, name=f"P{i}", position=g) for i, g in enumerate(groups, start=1)]
    m = N64StatMapper()
    xi, shape = m.given_22(squad)
    assert [x.id for x in xi] == [12, *range(1, 12), *range(13, 23)]
    assert len(m._lines) == 11 and list(m._lines.values()).count(0) == 1
    assert m._lines[12] == 0
    assert shape == (4, 4, 2) and sum(shape) == 10


def test_an_old_roster_without_formation_maps_as_before():
    # The XI given in no order at all: grouped by position, its counts name the formation.
    order = [1, 9, 10, 11, 6, 7, 8, 2, 3, 4, 5, *range(12, 23)]
    team = N64StatMapper().map_team(_squad_in_a_433(order), {}, order_as_given=True)
    assert [p.player_id for p in team.players] == list(range(1, 23))
    assert team.formation == (4, 3, 3)


def test_shirt_numbers_are_unique_in_1_to_63_and_espns_where_they_can_be():
    # ESPN's number stays when it is in 1-63 (the 6-bit field) and no earlier player has it; the
    # repeats, the missing (None, 0), the 64 and the 99 take the lowest free numbers in squad order.
    wanted = [1, 1, None, 99, 7, 0, 63, 7, 64, 2, 5, 5, 9, 9, 9, 40, 41, 42, 43, 44, 45, 46]
    roster = _squad_in_a_433()
    for player, number in zip(roster.players, wanted, strict=True):
        player.number = number
    team = N64StatMapper().map_team(roster, {}, order_as_given=True)
    assert [p.player_id for p in team.players] == list(range(1, 23))
    assert [p.number for p in team.players] == [
        1, 3, 4, 6, 7, 8, 63, 10, 11, 2, 5, 12, 9, 13, 14, 40, 41, 42, 43, 44, 45, 46,
    ]  # fmt: skip


def test_a_squad_of_nobodys_is_numbered_1_to_22():
    team = N64StatMapper().map_team(_squad_in_a_433(), {}, order_as_given=True)
    assert [p.number for p in team.players] == list(range(1, 23))


def test_an_unreadable_formation_counts_as_none():
    # The 4-2-3-1 club's suggested XI is the given one, so the shortcut keeps its lineup shape.
    m = N64StatMapper()
    roster = _club_in_a_4231(_starters_in_a_4231(), shape="4-3-3")
    roster.players = m.select_22(roster)[0]
    plain = m.map_team(roster, {}, order_as_given=True)
    roster.extra["formation"] = "x"
    assert m.map_team(roster, {}, order_as_given=True) == plain
    assert plain.formation == (4, 3, 3)


@pytest.mark.parametrize(
    "roster",
    [
        _club_in_a_4231(_starters_in_a_4231(), shape="4-3-3"),
        _squad_in_a_433(lineup=_first_eleven_lineup("4-3-3")),
    ],
    ids=["4-2-3-1 club", "4-3-3 squad"],
)
def test_an_unedited_roster_maps_as_the_suggested_one_does(roster):
    # A fetch leaves the lineup's own shape as the roster's formation. Given back in the order
    # `suggest_squad_order` showed, the roster writes what the suggested mapping writes.
    m = N64StatMapper()
    roster.extra["formation"] = roster.extra["lineup"]["shape"]
    roster.players = m.select_22(roster)[0]
    suggested = m.map_team(roster, {}, order_as_given=False)
    suggested_lines = dict(m._lines)
    given = m.map_team(roster, {}, order_as_given=True)
    assert [p.player_id for p in given.players] == [p.player_id for p in suggested.players]
    assert given.formation == suggested.formation == (4, 3, 3)
    assert m._lines == suggested_lines


def _back_five():
    """A 5-4-1 club whose lineup (ESPN's codes) is ids 1-11: a keeper, five defenders, four
    midfielders and a striker."""
    codes = "G LB CD-L CD CD-R RB LM CM-L CM-R RM F".split()
    roles = "GK LB CB CB CB RB LM CM CM RM ST".split()
    return older_roster(codes, roles, formation="5-4-1", shape="5-4-1")


def test_a_five_four_one_club_plays_five_three_two_with_all_five_defenders_in_defence():
    # 5-3-2 is as near a 5-4-1 as 4-4-2 and 4-5-1 are, and the back five stays: the fifth
    # defender used to be spilled to the attack of a 4-4-2.
    m = N64StatMapper()
    xi, shape = m.select_22(_back_five())
    assert shape == (5, 3, 2)
    assert {p.id for p in xi[1:6]} == {2, 3, 4, 5, 6}
    assert [m._lines[p.id] for p in xi[:11]] == [0] + [1] * 5 + [2] * 3 + [3] * 2
    assert m.map_team(_back_five(), {}).formation == (5, 3, 2)


def test_an_edited_formation_fills_and_writes_the_eleven_without_order_as_given():
    # The lineup says 4-3-3; the formation picked since, a 3-5-2, is the one the XI is chosen for.
    m = N64StatMapper()
    roster = _squad_in_a_433(lineup=_first_eleven_lineup("4-3-3"), formation="3-5-2")
    team = m.map_team(roster, {})
    assert team.formation == (3, 5, 2)
    played = [m._lines[p.player_id] for p in team.players[:11]]
    assert [played.count(line) for line in (1, 2, 3)] == [3, 5, 2]


def test_the_suggested_order_follows_an_edited_formation(tmp_path):
    patcher = get_patcher("iss2000-n64")(tmp_path)
    roster = _squad_in_a_433(lineup=_first_eleven_lineup("4-3-3"), formation="3-5-2")
    patcher.suggest_squad_order(roster)
    played = list(patcher.mapper._lines.values())
    assert [played.count(line) for line in (1, 2, 3)] == [3, 5, 2]


@pytest.mark.parametrize(
    "roster_of",
    [
        lambda: _club_in_a_4231(_starters_in_a_4231(), shape="4-3-3"),
        lambda: _squad_in_a_433(lineup=_first_eleven_lineup("4-3-3")),
    ],
    ids=["4-2-3-1 club", "4-3-3 squad"],
)
def test_a_fresh_rosters_formation_changes_nothing_in_the_automatic_mapping(roster_of):
    # A fetch sets the formation to the lineup's shape: such a roster maps as the one that has
    # only the lineup (a rosters file from before formations).
    m = N64StatMapper()
    plain = m.map_team(roster_of(), {})
    plain_lines = dict(m._lines)
    fresh = roster_of()
    fresh.extra["formation"] = fresh.extra["lineup"]["shape"]
    assert m.map_team(fresh, {}) == plain
    assert m._lines == plain_lines
    assert plain.formation == (4, 3, 3)


@pytest.mark.parametrize("order_as_given", [False, True])
def test_a_four_line_formation_reads_as_its_three_lines_as_in_the_other_games(order_as_given):
    # "4-2-3-1" is a 4-3-3 once folded (`parse_shape`); `parse_formation` reads it as (4, 5, 1).
    roster = _squad_in_a_433(formation="4-2-3-1")
    team = N64StatMapper().map_team(roster, {}, order_as_given=order_as_given)
    assert team.formation == (4, 3, 3)


def test_a_formation_comes_from_the_donor_as_shipped():
    from retro_roster_patcher.games.iss2000_n64 import writer as W
    from retro_roster_patcher.games.iss2000_n64.mapper import FORMATION_DONORS

    # An image an earlier patch wrote over every donor slot (the nation fill does).
    image = bytearray(R.METADATA + 120 * 6)
    for donor in FORMATION_DONORS.values():
        image[R.FORMATIONS + donor * R.FORMATION_ROW] = donor + 1
    # The 4-5-1 donor (slot 20) is itself patched with a 4-3-3 first ...
    W.write_formation(image, FORMATION_DONORS[(4, 5, 1)], (4, 3, 3))
    # ... and a later 4-5-1 still gets the shipped 4-5-1 row, never what the image holds.
    W.write_formation(image, 69, (4, 5, 1))
    row, ident = W.DONORS[(4, 5, 1)]
    at = R.FORMATIONS + 69 * R.FORMATION_ROW
    assert image[at : at + R.FORMATION_ROW] == row and image[R.METADATA + 69 * 6 + 5] == ident
    assert row[0] != FORMATION_DONORS[(4, 5, 1)] + 1


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


def test_a_rewritten_asset_starts_on_an_8_byte_boundary():
    # The palette asset (669) starts at 0xF6E2F6 on the cartridge; rewritten there, a shorter
    # stream hung team select in Mupen64Plus-Next (Nov 8 2026), the same bytes from an
    # 8-byte boundary did not. In place when it still fits after the boundary, else moved.
    image = bytearray(R.ROM_SIZE)
    starts = (0x100006, 0x100406, 0x100800)
    for k, (start, end) in enumerate(zip(starts, starts[1:], strict=False)):
        image[R.ASSET_TABLE + 8 * k : R.ASSET_TABLE + 8 * k + 8] = (start | 0x80000000).to_bytes(
            4, "big"
        ) + end.to_bytes(4, "big")
    image[0x100800:] = b"\xff" * (R.ROM_SIZE - 0x100800)
    space = R.AssetSpace(image)
    first, second = R.assets(image)
    space.put(first, bytes(64))  # a few bytes: fits after 0x100008
    space.put(second, os.urandom(2000))  # too big for its room: moved
    one, two = R.assets(image)
    assert one.start == 0x100008 and R.read_asset(image, one) == bytes(64)
    assert two.start % 8 == 0 and two.start != 0x100406
    # The stream is padded to 8 too, so it also ends on a boundary.
    assert (one.end - one.start) % 8 == 0 and (two.end - two.start) % 8 == 0
    assert len(image) == R.ROM_SIZE
