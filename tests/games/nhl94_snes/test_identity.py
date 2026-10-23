"""`games.nhl94_snes.identity`: team names, banners, colours, logos, brackets.

`add_identity` gives the shared synthetic ROM the cartridge's identity tables,
every value made up: a banner list, banner, logo and uniform palettes, a
center-ice table, a RefPack logo archive behind the three loaders, the NY
strings and 32 seed tables. The team blocks are packed together first, so the
seed tables can sit where the cartridge has them.
"""

from __future__ import annotations

import importlib.util
import io
import struct

import pytest

from retro_roster_patcher.core.errors import RomError
from retro_roster_patcher.core.models import MappedRosters
from retro_roster_patcher.formats import snes
from retro_roster_patcher.formats.ea_tdb import refpack_compress, refpack_decompress
from retro_roster_patcher.games.nhl94_snes import identity, portraits
from retro_roster_patcher.games.nhl94_snes.logo import big_logo, center_ice
from retro_roster_patcher.games.nhl94_snes.models import (
    MODERN_NHL_TO_NHL94,
    NHL94PlayerRecord,
    NHL94TeamRecord,
)
from retro_roster_patcher.games.nhl94_snes.patcher import NHL94SNESPatcher
from retro_roster_patcher.sports.models import Team
from tests.fixtures import synthetic_snes_rom as fixture
from tests.formats.test_ea_huff import huff
from tests.games.nhl94_snes.test_portraits import _colour, _head_shot, with_portraits

WINDOW_END = fixture.BANK_WINDOW_START + fixture.BANK_WINDOW_SIZE
BANNER_PALETTE_AT = 0xD7000
LOGO_PALETTE_AT = 0xD7400
ICE_TILES_AT = 0xD8000
ICE_MAP_AT = 0xD9C00
ARCHIVE_AT = 0xABDE
LOADER = bytes.fromhex("a98100850ea9deab")


def _u16(rom, at):
    return rom[at] | rom[at + 1] << 8


def _base(rom, slot):
    return fixture.BANK_WINDOW_START + (_u16(rom, fixture.team_pointer_offset(slot)) & 0x7FFF)


def _strings(rom, slot):
    base = _base(rom, slot)
    at = fixture.BANK_WINDOW_START + ((0x8000 + base - 0xE0000 + _u16(rom, base + 4)) & 0x7FFF)
    out = []
    for _ in range(4):
        out.append(rom[at + 2 : at + _u16(rom, at)].decode())
        at += _u16(rom, at)
    return out


def _entries():
    return [bytes([k + 1]) * (40 + k) for k in range(identity.ARCHIVE_ENTRIES)]


def add_identity(rom: bytearray) -> bytearray:
    blocks = []
    for slot in range(fixture.TEAM_COUNT):
        base = _base(rom, slot)
        end = at = base + _u16(rom, base + fixture.STRINGS_OFFSET)
        for _ in range(4):
            end += _u16(rom, end)
        blocks.append(bytes(rom[base:end]))
    rom[fixture.TEAM_BLOCK_BASE : WINDOW_END] = b"\xff" * (WINDOW_END - fixture.TEAM_BLOCK_BASE)
    at = fixture.TEAM_BLOCK_BASE
    for slot, block in enumerate(blocks):
        rom[at : at + len(block)] = block
        pointer = 0x8000 + at - fixture.BANK_WINDOW_START
        struct.pack_into("<H", rom, fixture.team_pointer_offset(slot), pointer)
        at += len(block)
    assert at < identity.SEEDS
    rom[identity.SEEDS : identity.SEEDS + 512] = bytes(
        (k + j) % 26 for k in range(32) for j in range(16)
    )

    names = b""
    for slot in range(28):
        struct.pack_into("<H", rom, identity.BANNER_POINTERS + 2 * slot, 0x9681 + len(names))
        names += f"Old {slot}".encode() + b"\0"
    rom[identity.BANNER_LIST.start : identity.BANNER_LIST.start + len(names)] = names

    for slot in range(28):
        at = BANNER_PALETTE_AT + 32 * slot
        rom[at : at + 32] = bytes([slot]) * 32
        struct.pack_into("<I", rom, identity.BANNER_PALETTES + 4 * slot, identity._cpu(at))
        tiles = refpack_compress(bytes(32) + bytes([slot + 1]) * 32)
        rom[ICE_TILES_AT + 0x100 * slot : ICE_TILES_AT + 0x100 * slot + len(tiles)] = tiles
        layout = struct.pack("<HHH", 8, 4, 0x0308) + struct.pack("<32H", *([0x401] + [0] * 31))
        rom[ICE_MAP_AT + 70 * slot : ICE_MAP_AT + 70 * slot + 70] = layout
        entry = identity.CENTER_ICE + 8 * slot
        struct.pack_into("<I", rom, entry, identity._cpu(ICE_TILES_AT + 0x100 * slot))
        struct.pack_into("<I", rom, entry + 4, identity._cpu(ICE_MAP_AT + 70 * slot))
        for table in (identity.AWAY_UNIFORMS, identity.HOME_UNIFORMS):
            words = [(slot * 16 + i) & 0x7FFF for i in range(16)]
            rom[table + 32 * slot : table + 32 * slot + 32] = struct.pack("<16H", *words)
    for k in range(30):
        at = LOGO_PALETTE_AT + 32 * k
        rom[at : at + 32] = bytes([0x40 + k]) * 32
        struct.pack_into("<I", rom, identity.LOGO_PALETTES + 4 * k, identity._cpu(at))

    archive = refpack_compress(identity._build_archive(_entries()))
    rom[ARCHIVE_AT : ARCHIVE_AT + len(archive)] = archive
    for site in identity.LOGO_LOADERS:
        rom[site : site + 8] = LOADER
    rom[0xDC5E7 : 0xDC5E7 + 26] = b"\x0e\x00NY Islanders\x0c\x00NY Rangers"
    return rom


def _save(image):
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _png(colour=(200, 0, 0), size=(100, 80)):
    """An anti-aliased logo; a test drawing one needs Pillow."""
    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((5, 5, size[0] - 5, size[1] - 5), fill=(*colour, 255))
    draw.rectangle((40, 30, 60, 50), fill=(0, 0, 160, 255))
    return _save(image)


#: Eight bands across the top half of `_flat_png`, then its bottom-left quarter.
FLAT = [
    (248, 0, 0), (0, 248, 0), (248, 248, 0), (0, 248, 248),
    (248, 0, 248), (248, 248, 248), (128, 128, 128), (248, 128, 0), (0, 0, 248),
]  # fmt: skip


def _flat_colour(x, y):
    return FLAT[x // 6] if y < 16 else FLAT[8] if x < 24 else None


def _flat_png():
    """48x32 in nine flat colours (indices 1-9, so all four bitplanes), its
    bottom-right quarter clear."""
    pytest.importorskip("PIL")
    from PIL import Image

    image = Image.new("RGBA", (48, 32), (0, 0, 0, 0))
    for y in range(32):
        for x in range(48):
            if (rgb := _flat_colour(x, y)) is not None:
                image.putpixel((x, y), (*rgb, 255))
    return _save(image)


def _record(slot, team):
    players = [
        NHL94PlayerRecord(name=f"Player {slot:02d}{i:02d}", jersey_number=i + 1, is_goalie=i < 2)
        for i in range(10)
    ]
    return NHL94TeamRecord(
        index=slot,
        name="",
        city="",
        acronym="",
        players=players,
        num_forwards=5,
        num_defensemen=3,
        team=team,
    )


def _rosters(teams):
    return MappedRosters(
        game_id="nhl94-snes",
        teams={MODERN_NHL_TO_NHL94[t.code]: _record(MODERN_NHL_TO_NHL94[t.code], t) for t in teams},
    )


CAR = Team(id=7, name="Carolina Hurricanes", code="CAR", color="e30426", alternate_color="000000")
COL = Team(id=17, name="Colorado Avalanche", code="COL", color="860038", alternate_color="005ea3")
SEA = Team(id=124292, name="Seattle Kraken", code="SEA", color="000d33", alternate_color="a3dce4")
VGK = Team(id=37, name="Vegas Golden Knights", code="VGK", color="344043", alternate_color="b4975a")
DAL = Team(id=9, name="Dallas Stars", code="DAL", color="20864c", alternate_color="000000")


@pytest.fixture
def rom_path(tmp_path):
    path = tmp_path / "nhl94.sfc"
    path.write_bytes(add_identity(fixture.build_nhl94_snes_rom(players_per_team=10)))
    return path


@pytest.fixture
def assets(tmp_path):
    logos = tmp_path / "assets" / "logos"
    logos.mkdir(parents=True)
    for team, colour in ((CAR, (220, 0, 30)), (COL, (130, 0, 50)), (SEA, (0, 20, 50))):
        (logos / f"{team.id}.png").write_bytes(_png(colour))
    return tmp_path / "assets"


def _no_network(url, headers, timeout):
    raise AssertionError(f"unexpected download of {url}")


def _patch(tmp_path, rom_path, rosters, *, assets=None, transport=_no_network, **options):
    messages = []
    patcher = NHL94SNESPatcher(
        tmp_path / "cache", on_status=messages.append, assets_dir=assets, transport=transport
    )
    out = tmp_path / f"out{len(list(tmp_path.glob('out*')))}.sfc"
    patcher.patch(rom_path=rom_path, output_path=out, rosters=rosters, **options)
    return bytearray(out.read_bytes()), messages


def _archive(rom):
    site = identity.LOGO_LOADERS[0]
    address = rom[site + 1] << 16 | _u16(rom, site + 6)
    return identity._archive_entries(refpack_decompress(bytes(rom[identity._file(address) :])))


def _pointed(rom, table, slot, size=32):
    at = identity._file(int.from_bytes(rom[table + 4 * slot : table + 4 * slot + 3], "little"))
    return at, bytes(rom[at : at + size])


def _banners(rom):
    out = []
    for slot in range(28):
        at = 0xE0000 + _u16(rom, identity.BANNER_POINTERS + 2 * slot) - 0x8000
        out.append(rom[at : rom.index(0, at)].decode())
    return out


def _assert_todays_brackets(rom):
    east = {s for c, s in MODERN_NHL_TO_NHL94.items() if s < 26 and identity._east(c)}
    seen = set()
    for k in range(32):
        table = rom[identity.SEEDS + 16 * k : identity.SEEDS + 16 * k + 16]
        assert set(table[:8]) <= east and not set(table[8:]) & east
        assert len(set(table)) == 16
        seen |= set(table)
    assert seen == set(range(26))
    assert {18, 6, 22} & east == {6, 22}  # Colorado West, Detroit and Toronto East


def _planar(tile):
    """A 4bpp planar SNES tile as 8 rows of 8 colour indices."""
    planes = (0, 1, 16, 17)
    return [
        [sum((tile[p + 2 * y] >> (7 - x) & 1) << k for k, p in enumerate(planes)) for x in range(8)]
        for y in range(8)
    ]


# -- the club table ---------------------------------------------------------------


def test_every_mapped_code_is_a_club_and_every_club_fits_the_game():
    assert len(identity.CLUBS) == 32
    assert sum(club.east for club in identity.CLUBS.values()) == 16
    for code in MODERN_NHL_TO_NHL94:
        assert identity.club_code(code) is not None, code
    for code, club in identity.CLUBS.items():
        assert len(code) <= 3 and code.isupper()
        assert identity.banner_width(club.city) <= identity.BANNER_MAX_WIDTH, club.city
        assert len(club.city) <= 12 and club.city.isascii() and club.arena.isascii()
        assert identity._hex(club.color) and identity._hex(club.alternate)


def test_the_nhl_apis_codes_are_espns_clubs():
    assert identity.club_code("LAK") == "LA"
    assert identity.club_code("tbl") == "TB"
    assert identity.club_code("ZZZ") is None


def test_the_banner_font_is_measured_as_the_cartridge_measures_it():
    assert identity.banner_width("Los Angeles") == 66  # the widest on the cartridge
    assert identity.banner_width("GOLDEN KNIGHTS") > identity.BANNER_MAX_WIDTH


def test_either_codec_the_game_reads_is_decoded():
    assert identity._decode(huff([("byte", 0x11), ("run", 3), ("end", 0)], 8)) == b"\x11" * 4
    assert identity._decode(refpack_compress(b"abc" * 9)) == b"abc" * 9


# -- a patch ------------------------------------------------------------------------


def test_without_pillow_a_mapped_team_still_gets_its_name_and_colours(
    tmp_path, rom_path, monkeypatch
):
    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name, *a: None if name == "PIL" else real(name, *a)
    )
    original = bytes(rom_path.read_bytes())
    rom, messages = _patch(tmp_path, rom_path, _rosters([CAR, COL, SEA]))  # nothing downloaded

    assert "Pillow is not installed; team logos skipped" in messages
    assert "Team identity written: 3" in messages
    assert len(rom) == identity.TWO_MIB and rom[snes.SIZE_BYTE] == 0x0B
    assert snes.fix_checksum(rom) == rom
    assert _strings(rom, 9) == ["Carolina", "CAR", "Hurricanes", "Lenovo Center"]
    assert _strings(rom, 26) == ["Seattle", "SEA", "Kraken", "Climate Pledge Arena"]
    assert _strings(rom, 0) == _strings(original, 0)  # Anaheim was not mapped
    _assert_todays_brackets(rom)
    for start, size in ((identity.LOGO_LOADERS[0], 8), (identity.CENTER_ICE, 224)):
        assert rom[start : start + size] == original[start : start + size]

    banners = _banners(rom)
    assert banners[9] == "Carolina" and banners[18] == "Colorado" and banners[26] == "Seattle"
    assert banners[0] == "Old 0" and banners[27] == "Old 27"

    at, palette = _pointed(rom, identity.BANNER_PALETTES, 9)
    assert at in identity.REGION
    words = struct.unpack("<16H", palette)
    assert words[:2] == (0, 0x6F7B) and words[15] == 0x031A
    assert _pointed(rom, identity.BANNER_PALETTES, 0) == _pointed(
        original, identity.BANNER_PALETTES, 0
    )

    home = struct.unpack_from("<16H", rom, identity.HOME_UNIFORMS + 32 * 9)
    away = struct.unpack_from("<16H", rom, identity.AWAY_UNIFORMS + 32 * 9)
    assert home[12] == identity.bgr555(identity.WHITE)
    assert away[12] == identity.bgr555((0xE3, 0x04, 0x26))
    assert home[7] == 9 * 16 + 7  # skin stays
    unmapped = identity.HOME_UNIFORMS + 32 * 1
    assert rom[unmapped : unmapped + 32] == original[unmapped : unmapped + 32]


def test_the_logo_archive_takes_each_logo_and_keeps_the_rest(tmp_path, rom_path, assets):
    rom, messages = _patch(tmp_path, rom_path, _rosters([CAR, SEA]), assets=assets)
    assert "Team logos drawn: 2" in messages and _strings(rom, 9)[0] == "Carolina"

    site = identity.LOGO_LOADERS[0]
    assert identity._file(rom[site + 1] << 16) in identity.REGION
    assert len({bytes(rom[s : s + 8]) for s in identity.LOGO_LOADERS}) == 1
    entries = _archive(rom)
    old = _entries()
    for slot in range(30):
        if slot in (9, 26):
            assert entries[slot][:4] == bytes([6, 0x80, 24, 0])  # six pieces, 24 tiles
        else:
            assert entries[slot].rstrip(b"\0") == old[slot]
    at, palette = _pointed(rom, identity.LOGO_PALETTES, 9)
    assert at in identity.REGION and palette[:2] == b"\0\0"

    entry = identity.CENTER_ICE + 8 * 26
    tiles = refpack_decompress(
        bytes(rom[identity._file(int.from_bytes(rom[entry : entry + 3], "little")) :])
    )
    layout_at = identity._file(int.from_bytes(rom[entry + 4 : entry + 7], "little"))
    width, height, flags = struct.unpack_from("<HHH", rom, layout_at)
    words = struct.unpack_from("<32H", rom, layout_at + 6)
    assert (width, height, flags) == (8, 4, 0x0308)
    assert tiles[:32] == bytes(32) and len(tiles) // 32 <= 30
    assert {w & 0x3FF for w in words if w} == set(range(1, len(tiles) // 32))
    assert all(w in (0,) or w & 0x0400 for w in words)


def test_logos_false_keeps_the_cartridges_logos_but_not_its_names(tmp_path, rom_path):
    original = bytes(rom_path.read_bytes())
    rom, _ = _patch(tmp_path, rom_path, _rosters([CAR]), logos=False)
    for start, size in ((identity.LOGO_LOADERS[0], 8), (identity.CENTER_ICE, 224)):
        assert rom[start : start + size] == original[start : start + size]
    assert _strings(rom, 9)[0] == "Carolina"


def test_a_provider_without_logos_or_colours_gets_espns(tmp_path, rom_path):
    asked, png = [], _png()

    def transport(url, headers, timeout):
        asked.append(url)
        return png

    team = Team(id=0, name="Los Angeles Kings", code="LAK")  # the NHL API's
    rom, _ = _patch(tmp_path, rom_path, _rosters([team]), transport=transport)
    assert asked == ["https://a.espncdn.com/i/teamlogos/nhl/500/la.png"]
    away = struct.unpack_from("<16H", rom, identity.AWAY_UNIFORMS + 32 * 10)
    assert away[12] == identity.bgr555(identity._visible((0x12, 0x12, 0x12)))
    assert _strings(rom, 10)[1] == "LA"
    _patch(tmp_path, rom_path, _rosters([team]), transport=transport)
    assert len(asked) == 1  # cached


def test_a_logo_that_cannot_be_had_costs_the_logo_only(tmp_path, rom_path):
    pytest.importorskip("PIL")  # without it no logo is fetched at all

    def transport(url, headers, timeout):
        raise OSError("offline")

    rom, messages = _patch(tmp_path, rom_path, _rosters([CAR]), transport=transport)
    assert any("logo skipped" in m for m in messages)
    assert _strings(rom, 9)[0] == "Carolina"
    site = identity.LOGO_LOADERS[0]
    assert rom[site : site + 8] == LOADER


def test_strings_too_long_for_their_block_move_to_the_padding(rom_path):
    original = rom_path.read_bytes()
    rom = bytearray(original)
    identity.write(rom, {26: identity.identity_of(_record(26, SEA))}, print)
    assert _strings(rom, 26) == ["Seattle", "SEA", "Kraken", "Climate Pledge Arena"]
    base = _base(original, 26)
    old = base + _u16(original, base + 4)
    assert _u16(rom, base + 4) != _u16(original, base + 4)
    assert rom[old : old + 4] == b"\xff" * 4  # free for the roster writer


def test_the_ny_strings_change_only_when_another_club_takes_the_slot(rom_path):
    rom = bytearray(rom_path.read_bytes())
    nyi = Team(id=12, name="New York Islanders", code="NYI")
    identity.write(rom, {13: identity.identity_of(_record(13, nyi))}, print)
    assert rom[0xDC5E7 : 0xDC5E7 + 14] == b"\x0e\x00NY Islanders"
    identity.write(rom, {14: identity.identity_of(_record(14, SEA))}, print)
    assert rom[0xDC5F5 : 0xDC5F5 + 9] == b"\x09\x00Seattle"


def test_the_playoff_brackets_hold_todays_conferences(tmp_path, rom_path):
    rom, _ = _patch(tmp_path, rom_path, _rosters([CAR]), logos=False)
    _assert_todays_brackets(rom)


@pytest.fixture
def faces_path(tmp_path):
    """`rom_path` with the cartridge's portrait table, every team on one palette."""
    path = tmp_path / "faces.sfc"
    rom = add_identity(fixture.build_nhl94_snes_rom(players_per_team=10))
    path.write_bytes(with_portraits(rom))
    return path


def _jersey(rom, slot):
    """Where the slot's portrait palette is, and its 16 colours."""
    at, palette = _pointed(rom, portraits.PALETTES, slot)
    return at, struct.unpack("<16H", palette)


def test_a_mapped_slots_portraits_wear_its_colours_on_a_palette_of_its_own(tmp_path, faces_path):
    original = faces_path.read_bytes()
    rom, _ = _patch(tmp_path, faces_path, _rosters([CAR, SEA, VGK]), logos=False)
    white = identity.bgr555(identity.WHITE)
    jerseys = {
        9: ((0xE3, 0x04, 0x26), (0, 0, 0)),
        26: ((0x00, 0x0D, 0x33), (0xA3, 0xDC, 0xE4)),
        27: ((0x34, 0x40, 0x43), (0xB4, 0x97, 0x5A)),
    }
    for slot, (colour, alternate) in jerseys.items():
        at, words = _jersey(rom, slot)
        assert at in identity.REGION
        assert words[:13] == _jersey(original, slot)[1][:13]  # see-through and the skin ramp
        assert words[13:] == (white, identity.bgr555(colour), identity.bgr555(alternate))
    assert _jersey(original, 26)[0] == _jersey(original, 27)[0]
    assert _jersey(rom, 26)[0] != _jersey(rom, 27)[0]  # the All-Stars' palette, split
    assert _jersey(rom, 0) == _jersey(original, 0)  # Anaheim was not mapped

    again = tmp_path / "again.sfc"
    again.write_bytes(rom)
    second, _ = _patch(tmp_path, again, _rosters([COL]), logos=False)
    assert _jersey(second, 26)[1] == _jersey(rom, 26)[1]  # carried over
    assert _jersey(second, 18)[1][14] == identity.bgr555((0x86, 0x00, 0x38))


def test_head_shots_are_drawn_in_the_jersey_colours_identity_wrote(tmp_path, faces_path):
    """A Dallas-green head shot takes colour 14, red on the cartridge's palette."""
    pytest.importorskip("PIL")
    shot = _head_shot((0x20, 0x86, 0x4C))
    rosters = _rosters([DAL])
    for player in rosters.teams[5].players:
        player.photo_url = "https://example.test/dallas.png"
    rom, _ = _patch(
        tmp_path, faces_path, rosters, logos=False, transport=lambda url, headers, timeout: shot
    )
    assert [_colour(rom, 5, r) for r in range(10)] == [14] * 10


def test_a_second_patch_carries_what_it_leaves_alone(tmp_path, rom_path, assets):
    first, _ = _patch(tmp_path, rom_path, _rosters([CAR, SEA]), assets=assets)
    again = tmp_path / "again.sfc"
    again.write_bytes(first)
    second, _ = _patch(tmp_path, again, _rosters([COL]), assets=assets)

    for table in (identity.BANNER_PALETTES, identity.LOGO_PALETTES):
        assert _pointed(second, table, 26)[1] == _pointed(first, table, 26)[1]
    for rom in (first, second):
        assert _archive(rom)[26][:4] == bytes([6, 0x80, 24, 0])
    entry = identity.CENTER_ICE + 8 * 9

    def ice(rom):
        tiles = int.from_bytes(rom[entry : entry + 3], "little")
        return refpack_decompress(bytes(rom[identity._file(tiles) :]))

    assert ice(second) == ice(first)
    assert _strings(second, 9)[0] == "Carolina" and _strings(second, 18)[0] == "Colorado"


def test_a_second_patch_without_logos_keeps_the_first_ones(tmp_path, rom_path, assets):
    first, _ = _patch(tmp_path, rom_path, _rosters([CAR]), assets=assets)
    again = tmp_path / "again.sfc"
    again.write_bytes(first)
    second, _ = _patch(tmp_path, again, _rosters([CAR]), logos=False)
    assert _archive(second) == _archive(first)
    assert (
        _pointed(second, identity.LOGO_PALETTES, 9)[1]
        == _pointed(first, identity.LOGO_PALETTES, 9)[1]
    )


def test_a_copier_header_is_kept_in_front_of_the_grown_image(tmp_path, rom_path):
    headered = tmp_path / "headered.smc"
    headered.write_bytes(bytes(512) + rom_path.read_bytes())
    rom, _ = _patch(tmp_path, headered, _rosters([CAR]), logos=False)
    assert len(rom) == 512 + identity.TWO_MIB and rom[:512] == bytes(512)
    assert _strings(rom[512:], 9)[0] == "Carolina"
    assert snes.fix_checksum(rom[512:]) == rom[512:]


def test_an_image_without_the_cartridges_tables_keeps_its_identity(tmp_path):
    path = fixture.write_nhl94_snes_rom(tmp_path / "plain.sfc", players_per_team=10)
    original = path.read_bytes()
    rom, messages = _patch(tmp_path, path, _rosters([CAR]))  # no download either
    assert any("not the retail cartridge's layout" in m for m in messages)
    assert _strings(rom, 9) == _strings(original, 9)


# -- the pictures ---------------------------------------------------------------------


def test_a_logo_fills_six_sprites_in_fifteen_colours():
    entry, palette = big_logo(_png())
    assert len(entry) == 22 + 6 * 7 + 24 * 32 and len(palette) == 32
    header = struct.unpack_from("<BBHhHhHHHH", entry)
    assert header == (6, 0x80, 24, 0, 48, 0, 32, 0x180, 0x180, 64)


def test_a_logo_filling_the_whole_box_is_shrunk_to_thirty_tiles():
    image = pytest.importorskip("PIL.Image")
    tiles, layout = center_ice(
        _save(image.new("RGBA", (64, 64), (130, 0, 50, 255))), identity.RINK_PALETTE
    )
    assert len(tiles) // 32 <= 30 and tiles[:32] == bytes(32)
    assert len(layout) == 6 + 64


def test_a_flat_logo_decodes_back_to_its_pixels():
    entry, palette = big_logo(_flat_png())  # Pillow keeps only the 9 colours used
    colours = struct.unpack("<16H", palette)
    tiles = entry[64:]
    rows = [[tiles[r * 384 + 32 * i : r * 384 + 32 * i + 32] for i in range(12)] for r in (0, 1)]
    seen = {}
    for k in range(6):
        x, y, first, size = struct.unpack_from("<hhHB", entry, 22 + 7 * k)
        assert size == 0xFF
        for dx, dy, tile in (
            (0, 0, rows[0][first]),
            (8, 0, rows[0][first + 1]),
            (0, 8, rows[1][first]),
            (8, 8, rows[1][first + 1]),
        ):
            for ty, line in enumerate(_planar(tile)):
                for tx, index in enumerate(line):
                    seen[(x + dx + tx, y + dy + ty)] = index
    assert len(seen) == 48 * 32
    assert set(seen.values()) == set(range(10))
    for (x, y), index in seen.items():
        if (want := _flat_colour(x, y)) is None:
            assert index == 0, (x, y)
        else:
            assert index and colours[index] == identity.bgr555(want), (x, y)


def test_center_ice_draws_the_left_pixel_high_and_leaves_white_to_the_ice():
    image = pytest.importorskip("PIL.Image")
    # 64x49 squashes to exactly the 64x32 box, so the columns survive as drawn:
    # dark red on even columns of the left 48, white everywhere else.
    logo = image.new("RGBA", (64, 49), (255, 255, 255, 255))
    for x in range(0, 48, 2):
        for y in range(49):
            logo.putpixel((x, y), (130, 0, 40, 255))
    tiles, layout = center_ice(_save(logo), identity.RINK_PALETTE)
    words = struct.unpack_from("<32H", layout, 6)
    for k, word in enumerate(words):
        tile = tiles[32 * (word & 0x3FF) :][:32] if word else bytes(32)
        for y in range(8):
            for x in range(8):
                index = tile[4 * y + x // 2] >> (0 if x % 2 else 4) & 15
                coloured = (k % 8) < 6 and x % 2 == 0
                assert index == (13 if coloured else 0), (k, x, y)  # 13: the rink's dark red


def test_a_flat_override_logo_of_few_colours_is_drawn(tmp_path, rom_path):
    logos = tmp_path / "assets" / "logos"
    logos.mkdir(parents=True)
    (logos / f"{CAR.id}.png").write_bytes(_flat_png())
    rom, messages = _patch(tmp_path, rom_path, _rosters([CAR]), assets=tmp_path / "assets")
    assert "Team logos drawn: 1" in messages
    assert _archive(rom)[9][:4] == bytes([6, 0x80, 24, 0])


def test_a_logo_pillow_refuses_costs_that_logo_only(tmp_path, rom_path, assets, monkeypatch):
    image = pytest.importorskip("PIL.Image")
    monkeypatch.setattr(image, "MAX_IMAGE_PIXELS", 1000)  # every logo a decompression bomb
    rom, messages = _patch(tmp_path, rom_path, _rosters([CAR]), assets=assets)
    assert any(m.startswith("Slot 9: logo skipped") for m in messages)
    assert _strings(rom, 9)[0] == "Carolina"


def test_a_logo_the_archive_has_no_room_for_is_skipped(tmp_path, rom_path, assets, monkeypatch):
    monkeypatch.setattr(identity, "ARCHIVE_SIZE", len(identity._build_archive(_entries())) + 100)
    original = bytes(rom_path.read_bytes())
    rom, messages = _patch(tmp_path, rom_path, _rosters([CAR]), assets=assets)
    assert "Slot 9: logo skipped: the logo archive is full" in messages
    assert _archive(rom)[9].rstrip(b"\0") == _entries()[9]
    assert _pointed(rom, identity.LOGO_PALETTES, 9) == _pointed(original, identity.LOGO_PALETTES, 9)
    ice = identity.CENTER_ICE + 8 * 9
    assert rom[ice : ice + 8] == original[ice : ice + 8]


def test_a_stream_never_crosses_a_bank():
    rom = bytearray(identity.TWO_MIB)
    region = identity._Region(rom)
    region.at = 0x108000 - 10
    assert region.put(b"\1" * 10) == identity._cpu(0x107FF6)  # fits the bank exactly
    region.at = 0x108000 - 10
    assert region.put(b"\2" * 20) == identity._cpu(0x108000)  # would straddle: next bank
    assert rom[0x108000:0x108014] == b"\2" * 20
    with pytest.raises(RomError):
        region.put(bytes(identity.BANK + 1))
