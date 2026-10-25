"""Team identity: names, divisions, the team-select order, logos and uniforms.

The synthetic cartridge is the KGJ fixture plus `synthetic_kgj_identity`: the
five text tables, the menus and grid script, the resources. Each assertion
reads the ROM back the way the game does (pointers resolved by the fixture's
own transcription), not through `identity`'s readers.
"""

from __future__ import annotations

import io
import itertools
import math
import struct

import pytest

from retro_roster_patcher.core.models import MappedRosters
from retro_roster_patcher.formats import snes
from retro_roster_patcher.games.iss_snes.screens import to_rgb
from retro_roster_patcher.games.kgj_mlb_snes import identity, resources
from retro_roster_patcher.games.kgj_mlb_snes.models import MODERN_MLB_TO_KGJ, KGJTeamRecord
from retro_roster_patcher.games.kgj_mlb_snes.patcher import KGJMLBPatcher
from retro_roster_patcher.games.kgj_mlb_snes.rom_reader import KGJRomReader
from retro_roster_patcher.sports.models import Team
from tests.fixtures import synthetic_kgj_identity as fx
from tests.fixtures import synthetic_kgj_rom as fixture

FOUR_MIB = 0x400000


def _cartridge() -> bytearray:
    return fx.add_identity(fixture.build_kgj_rom())


def _record(slot: int, city: str, nickname: str, code: str, colors=()) -> KGJTeamRecord:
    return KGJTeamRecord(
        index=slot, name="", city=city, nickname=nickname, code=code, colors=colors
    )


# --- text --------------------------------------------------------------------


def test_the_cartridge_is_recognised_by_its_code():
    assert identity.known(_cartridge())
    assert not identity.known(fixture.build_kgj_rom())


def test_slots_are_game_teams_nl_first():
    assert [identity.team_of(s) for s in (0, 13, 14, 27)] == [14, 27, 0, 13]


@pytest.mark.parametrize(
    ("text", "printed"),
    [("St. Louis", "ST LOUIS"), ("Montréal  Expos", "MONTREAL EXPOS"), ("A's", "AS")],
)
def test_names_are_cleaned_for_the_team_font(text, printed):
    assert identity.clean(text) == printed


def test_a_full_name_past_18_characters_is_the_nickname():
    assert identity.full_name("BOSTON", "RED SOX") == "BOSTON RED SOX"
    assert identity.full_name("LOS ANGELES", "ANGELS") == "LOS ANGELES ANGELS"  # 18
    assert identity.full_name("LOS ANGELES", "DODGERS") == "DODGERS"
    assert [identity.singular(n) for n in ("BRAVES", "RED SOX")] == ["BRAVE", "RED SOX"]


def test_names_go_into_all_five_tables():
    rom = _cartridge()
    identity.write_names(
        rom, {3: ("MILWAUKEE", "BREWERS", "MIL"), 20: ("KANSAS CITY", "ROYALS", "KC")}
    )
    assert bytes(rom[fx.ABBREVIATIONS + 9 : fx.ABBREVIATIONS + 12]) == fx.encode("MIL")
    assert bytes(rom[fx.ABBREVIATIONS + 60 : fx.ABBREVIATIONS + 63]) == fx.encode("KC ")
    assert fx.read_string(rom, fx.FULL, 3) == "MILWAUKEE BREWERS"
    assert fx.read_string(rom, fx.FULL, 20) == "KANSAS CITY ROYALS"
    at = fx.string_at(rom, fx.CITY_NICK, 20)
    assert rom[at : rom.index(0xFF, rom.index(0xFF, at) + 1)] == (
        fx.encode("KANSAS CITY") + b"\xff" + fx.encode("ROYALS")
    )
    assert fx.read_string(rom, fx.CITY, 3, base=fx.CITY_BASE) == "MILWAUKEE"
    assert fx.read_string(rom, fx.NICK, 3, term=0xD0) == "BREWERS"
    assert fx.read_string(rom, fx.NICK, 30 + 3, term=0xD0) == "BREWER"
    # Everyone else, the All-Stars and the league names keep theirs.
    assert fx.read_string(rom, fx.FULL, 4) == "CITY04 NICK04"
    assert fx.read_string(rom, fx.FULL, 29) == "AMERICAN ALL STARS"
    assert fx.read_string(rom, fx.NICK, 28, term=0xD0) == "ALL STARS"
    assert fx.read_string(rom, fx.NICK, 59, term=0xD0) == "LEAGUE A"
    assert identity.names(rom)[20] == ("KANSAS CITY", "ROYALS")


def test_names_past_a_tables_area_go_to_the_end_of_its_bank():
    rom = _cartridge()
    # 28 cities of 9 letters: 280 bytes for an area of 264.
    identity.write_names(rom, {t: (f"CITYNAM{t:02d}", "X", "X") for t in range(28)})
    spill = [t for t in range(28) if fx.string_at(rom, fx.CITY, t, base=fx.CITY_BASE) >= 0x1FFA0]
    assert spill
    assert all(
        fx.read_string(rom, fx.CITY, t, base=fx.CITY_BASE) == f"CITYNAM{t:02d}" for t in range(28)
    )
    with pytest.raises(ValueError, match="do not fit"):
        identity.write_names(rom, {t: ("C" * 20, "X", "X") for t in range(28)})


def test_divisions_are_todays_and_the_league_the_slots():
    rom = _cartridge()
    identity.write_divisions(rom, {3: "MIL", 21: "HOU", 0: "ATL", 5: "XYZ"})
    word = lambda k: struct.unpack_from("<H", rom, fx.DIVISIONS + 2 * k)[0]  # noqa: E731
    assert (word(3), word(21), word(0)) == (2, 0x8004, 0)
    assert word(5) == 0x0006  # not a club of today: left alone
    assert word(28 + 3) == 0x0006  # the 1993 format is not touched


# --- team select -------------------------------------------------------------


def _grid(rom: bytes | bytearray) -> dict[tuple[int, int], tuple[int, int, int | None]]:
    """(x, y) -> (team in menu 1, team in menu 2, team the script draws)."""
    out = {}
    script, at = {}, fx.SCRIPT + 4
    for _ in range(28):
        x, y = rom[at + 1], rom[at + 2]
        short = rom[at + 3] == 0xDE
        script[x, y] = 0 if short else struct.unpack_from("<H", rom, at + 4)[0] // 25
        at += 4 if short else 6
        if len(script) == 14:
            at += 6
    for i in range(28):
        cells = [m + 16 * i for m in fx.MENUS]
        x, y = rom[cells[0]], rom[cells[0] + 1]
        out[x, y] = (rom[cells[0] + 12] // 2, rom[cells[1] + 12] // 2, script[x, y])
    return out


def test_each_page_is_sorted_and_the_logos_move_with_the_teams():
    rom = _cartridge()
    # NL: ATL stays first (the `DE` cell); CITY13 must now come second.
    identity.write_names(rom, {13: ("BBB", "X", "BB"), 27: ("AAA", "X", "AA")})
    identity.sort_grid(rom)
    grid = _grid(rom)
    assert all(a == b == c for a, b, c in grid.values())
    nl = [grid[xy][0] for xy in fx.NL_CELLS]
    al = [grid[xy][0] for xy in fx.AL_CELLS]
    assert nl == [0, 13, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    assert al == [27, *range(14, 27)]


def test_the_atlanta_cell_keeps_team_0_even_when_it_does_not_sort_first():
    rom = _cartridge()
    identity.write_names(rom, {0: ("ZZZ", "X", "ZZ")})
    identity.sort_grid(rom)
    grid = _grid(rom)
    assert grid[fx.NL_CELLS[0]] == (0, 0, 0)
    assert [grid[xy][0] for xy in fx.NL_CELLS[1:]] == list(range(1, 14))


# --- logos -------------------------------------------------------------------

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def _flat(colour) -> Image.Image:
    image = Image.new("RGBA", (100, 60), (0, 0, 0, 0))
    image.paste((*colour, 255), (10, 10, 90, 50))
    return image


def _logo_colours(rom, t) -> set[tuple[int, ...]]:
    """The colours game team t's logo draws, from the menu palettes' row 3."""
    codec, length, at = resources.entry(rom, fx.LOGO + t)
    data = resources.load(rom, fx.LOGO + t)
    row = resources.load(rom, fx.MENU_PALETTES)[96:128]
    pal = [row[i] | row[i + 1] << 8 for i in range(0, 32, 2)]
    used = set()
    for tile in range(25):
        for y in range(8):
            for x in range(8):
                v = sum(
                    (data[32 * tile + (p // 2) * 16 + 2 * y + p % 2] >> (7 - x) & 1) << p
                    for p in range(4)
                )
                if v:
                    used.add(tuple((pal[v] >> s & 31) * 255 // 31 for s in (0, 5, 10)))
    return used


def test_new_logos_share_one_new_palette_and_the_others_are_redrawn_in_it():
    rom = _cartridge()
    before = {t: _logo_colours(rom, t) for t in (5, 28)}
    snes.grow(rom, FOUR_MIB)
    identity.write_logos(rom, {0: _flat((255, 0, 0)), 14: _flat((0, 0, 255))})
    for t in range(30):
        assert resources.entry(rom, fx.LOGO + t) == (6, 800, 0x200000 + 800 * t)
    assert _logo_colours(rom, 0) == {(255, 0, 0)}
    assert _logo_colours(rom, 14) == {(0, 0, 255)}
    # A kept logo draws the colours it drew (the palette has room for them all).
    assert _logo_colours(rom, 5) == before[5]
    assert _logo_colours(rom, 28) == before[28]
    menus = resources.load(rom, fx.MENU_PALETTES)
    assert menus[:96] + menus[128:] == fx.menu_palettes()[:96] + fx.menu_palettes()[128:]
    assert menus[96:98] == fx.menu_palettes()[96:98]  # colour 0, the transparent one
    codec, length, at = resources.entry(rom, fx.LOGO_PALETTE)
    assert rom[at + 2 : at + 32] == menus[98:128]
    # The season schedule's copy: row 3 the same, row 4 it at 0.7, the rest kept.
    assert resources.entry(rom, fx.CALENDAR_PALETTES)[0] == 6
    calendar, retail = resources.load(rom, fx.CALENDAR_PALETTES), fx.calendar_palettes()
    assert calendar[:96] == retail[:96] and calendar[96:98] + calendar[128:130] == (
        retail[96:98] + retail[128:130]
    )
    assert calendar[98:128] == menus[98:128]
    row = struct.unpack_from("<15H", menus, 98)
    dim = struct.unpack_from("<15H", calendar, 130)
    assert dim == tuple(sum(round((w >> s & 31) * 0.7) << s for s in (0, 5, 10)) for w in row)


def test_a_repatch_draws_the_logos_it_keeps_the_same(monkeypatch):
    rom = _cartridge()
    # The synthetic cartridge's own entries stand in for the real one's.
    monkeypatch.setattr(
        identity, "_CARTRIDGE", {i: resources.entry(rom, i) for i in identity._CARTRIDGE}
    )
    snes.grow(rom, FOUR_MIB)
    pictures = {t: _flat((40 * (t % 6), 255 - 9 * t, 7 * t)) for t in range(28)}
    identity.write_logos(rom, pictures)
    once = bytes(rom)
    identity.write_logos(rom, pictures)
    assert bytes(rom) == once


def test_a_moved_club_takes_its_cartridge_logo_along():
    rom = _cartridge()
    washington = resources.entry(rom, fx.LOGO + 5)
    identity.keep_logos(rom, {3: "MIL", 21: "HOU", 5: "WSH"})
    # The cartridge's Brewers logo (game team 21) and Astros logo (team 3).
    assert resources.entry(rom, fx.LOGO + 3) == (4, 0x1CC, 0x1B5A27)
    assert resources.entry(rom, fx.LOGO + 21) == (4, 0x1FC, 0x1B3B6B)
    assert resources.entry(rom, fx.LOGO + 5) == washington
    # Logos a patch has redrawn stay with their slots.
    redrawn = _cartridge()
    snes.grow(redrawn, FOUR_MIB)
    resources.store(redrawn, fx.MENU_PALETTES, fx.menu_palettes(), 0x205DC0)
    before = [resources.entry(redrawn, fx.LOGO + t) for t in range(30)]
    identity.keep_logos(redrawn, {3: "MIL", 21: "HOU"})
    assert [resources.entry(redrawn, fx.LOGO + t) for t in range(30)] == before


def test_a_logo_is_fitted_into_40_by_40():
    logo = identity.fit_logo(_flat((0, 255, 0)))
    assert logo.size == (40, 40)
    assert logo.getchannel("A").getbbox() == (1, 10, 39, 29)  # 80x40 at 38/80


# --- uniforms ----------------------------------------------------------------


#: The synthetic batter's colours a club's colours replace: the navy cap,
#: sleeves and piping, the red bill, stripes and piping, the gold accent. The
#: greys (jersey, pants, the other runs) and the white trim stay.
_RECOLOURED = {5, 6, 7, 8, 25, 26, 27, 28, 29, 31, 37, 38, 39, 40, 45, 47}


def test_uniforms_change_the_team_colours_and_nothing_else():
    rom = _cartridge()
    snes.grow(rom, FOUR_MIB)
    identity.write_uniforms(rom, {15: ((255, 0, 255), (0, 255, 0))})
    for entry, original, changed in (
        (fx.BATTER_HOME, fx.batter(15, False), _RECOLOURED),
        (fx.BATTER_ROAD, fx.batter(15, True), _RECOLOURED),
        (fx.FIELDER_HOME, fx.fielder(15, False), {10, 11, 14}),
        (fx.FIELDER_ROAD, fx.fielder(15, True), {10, 11, 14}),
    ):
        codec, length, at = resources.entry(rom, entry + 15)
        assert codec == 6 and 0x200000 <= at < 0x300000
        new = resources.load(rom, entry + 15)
        words = [w for (w,) in struct.iter_unpack("<H", new)]
        old = [w for (w,) in struct.iter_unpack("<H", original)]
        assert {i for i in range(len(old)) if words[i] != old[i]} == changed
    bat = struct.unpack("<48H", resources.load(rom, fx.BATTER_HOME + 15))
    magenta, green = 0x7C1F, 0x03E0
    # The cap (7) and the navy piping (29, the cap's middle shade) are magenta,
    # the darkest piping (47) the cap's darkest shade; the red bill (5), the
    # stripes' second shade (38) and the gold accent (31) are green.
    assert (bat[7], bat[29], bat[47]) == (magenta, magenta, bat[8])
    assert (bat[5], bat[38], bat[31]) == (green, green, green)
    trim = struct.unpack_from("<H", resources.load(rom, fx.FIELDER_ROAD + 15), 28)[0]
    assert trim == green
    assert resources.load(rom, fx.BATTER_HOME + 16) == fx.batter(16, False)


def test_the_all_stars_fielders_jersey_takes_the_new_colour():
    # The All-Stars' fielders (the pitcher on the mound and on the Bullpen
    # cards too) wear a jersey of their cap's colour: it was left blue / red.
    rom = _cartridge()
    snes.grow(rom, FOUR_MIB)
    identity.write_uniforms(rom, {28: ((255, 0, 255), (0, 255, 0))})
    words = struct.unpack("<16H", resources.load(rom, fx.FIELDER_HOME + 28))
    assert words[1:4] == tuple(identity._shades((255, 0, 255), 4)[:3])
    old = struct.unpack("<16H", fx.fielder(28, False))
    assert {i for i in range(16) if words[i] != old[i]} == {1, 2, 3, 10, 11, 14}


def test_a_black_cap_ramps_toward_grey():
    # As the cartridge's black caps do: (8, 8, 8), (4, 4, 4), (0, 0, 0).
    assert identity._shades((0, 0, 0), 3) == [0x2529, 0x14A5, 0x0842]  # 9, 5, 2


def test_a_repatch_writes_the_same_uniforms():
    rom = _cartridge()
    snes.grow(rom, FOUR_MIB)
    # Seattle's navy trim lands near a shade of its teal cap: it stays the trim.
    colours = {
        15: ((0, 0, 0), (255, 199, 44)),
        16: ((0, 92, 92), (12, 44, 86)),
        28: ((170, 24, 44), (0, 0, 0)),
        29: ((9, 44, 92), (143, 188, 230)),
    }
    identity.write_uniforms(rom, colours)
    once = bytes(rom)
    identity.write_uniforms(rom, colours)
    assert bytes(rom) == once


@pytest.mark.parametrize(
    ("alternate", "trim"),
    [((255, 255, 255), 0x0018), ((196, 206, 212), 0x0018), ((255, 199, 44), 0x171F)],
)
def test_white_or_silver_trim_takes_the_primary_but_gold_stays(alternate, trim):
    # Fielder colours 10 and 11 are the cap (the primary, (200, 0, 0) = 0x0018,
    # then darker), 14 the trim.
    _, fielder = identity.uniform(bytes(96), bytes(32), (200, 0, 0), alternate)
    assert struct.unpack_from("<3H", fielder, 20) == (0x0018, 0x0012, 0)
    assert struct.unpack_from("<H", fielder, 28)[0] == trim


#: Batter colours 29 and 30 as a slot may have them: the cap's colour (its
#: middle shade), a second colour, white.
_NAVY, _RED, _WHITE = (0, 0, 11), (19, 0, 0), (31, 31, 31)


def _lettered(name: tuple[int, int, int], bar: tuple[int, int, int]) -> bytes:
    """The synthetic batter with colours 29 (the card's name) and 30 (its bar)."""
    words = list(struct.unpack("<48H", fx.batter(0, False)))
    words[29], words[30] = (r | g << 5 | b << 10 for r, g, b in (name, bar))
    return struct.pack("<48H", *words)


def _card_apart(batter: bytes) -> float:
    """How far apart, as the SNES draws them, the Line Up / Bullpen card's
    name (batter colour 29) and its bar (30) are. The cartridge keeps every
    club's at least 105 apart (Seattle's navy on teal)."""
    name, bar = struct.unpack_from("<2H", batter, 2 * 29)
    return math.dist(to_rgb(name), to_rgb(bar))


def test_the_angels_name_stands_out_on_its_card_bar():
    # California's lettering is red on navy. The Angels' silver alternate gives
    # way to their red, so both came out red: the name was lost on the bar.
    batter, _ = identity.uniform(_lettered(_RED, _NAVY), bytes(32), (186, 0, 33), (196, 206, 212))
    assert _card_apart(batter) >= 100


#: Every club's ESPN colour and alternate colour (2026).
_MLB_COLOURS = {
    "ARI": ("aa182c", "000000"), "ATH": ("003831", "efb21e"), "ATL": ("0c2340", "ba0c2f"),
    "BAL": ("df4601", "000000"), "BOS": ("0d2b56", "bd3039"), "CHC": ("0e3386", "cc3433"),
    "CHW": ("000000", "c4ced4"), "CIN": ("c6011f", "ffffff"), "CLE": ("002b5c", "e31937"),
    "COL": ("33006f", "000000"), "DET": ("0a2240", "ff4713"), "HOU": ("002d62", "eb6e1f"),
    "KC": ("004687", "7ab2dd"), "LAA": ("ba0021", "c4ced4"), "LAD": ("005a9c", "ffffff"),
    "MIA": ("00a3e0", "000000"), "MIL": ("13294b", "ffc72c"), "MIN": ("031f40", "e20e32"),
    "NYM": ("002d72", "ff5910"), "NYY": ("132448", "c4ced4"), "PHI": ("e81828", "003278"),
    "PIT": ("000000", "fdb827"), "SD": ("2f241d", "ffc425"), "SF": ("000000", "fd5a1e"),
    "SEA": ("005c5c", "0c2c56"), "STL": ("be0a14", "001541"), "TB": ("092c5c", "8fbce6"),
    "TEX": ("003278", "c0111f"), "TOR": ("134a8e", "6cace5"), "WSH": ("ab0003", "11225b"),
}  # fmt: skip


@pytest.mark.parametrize(("primary", "alternate"), _MLB_COLOURS.values(), ids=_MLB_COLOURS)
def test_every_clubs_card_name_stands_out_in_any_slot(primary, alternate):
    colours = tuple(bytes.fromhex(primary)), tuple(bytes.fromhex(alternate))
    for name, bar in itertools.product((_NAVY, _RED, _WHITE), repeat=2):
        batter, fielder = identity.uniform(_lettered(name, bar), bytes(32), *colours)
        assert _card_apart(batter) >= 100, (name, bar)
        assert identity.uniform(batter, fielder, *colours) == (batter, fielder)  # a re-patch


# --- the patch ----------------------------------------------------------------


def _team(code: str, name: str, short: str, tid: int, color="0d2b56", alt="bd3039") -> Team:
    return Team(id=tid, name=name, short_name=short, code=code, color=color, alternate_color=alt)


def test_stamp_takes_the_providers_names_and_colours():
    record = KGJTeamRecord(index=10, name="")
    identity.stamp(record, _team("ATH", "Athletics", "Athletics", 11, "003831", "efb21e"))
    assert (record.city, record.nickname, record.code) == ("SACRAMENTO", "ATHLETICS", "ATH")
    assert record.colors == ((0, 0x38, 0x31), (0xEF, 0xB2, 0x1E)) and record.team_id == 11
    identity.stamp(record, _team("STL", "St. Louis Cardinals", "Cardinals", 24, "", "x"))
    assert (record.city, record.nickname, record.colors) == ("ST LOUIS", "CARDINALS", ())


def test_houston_plays_in_the_al_and_milwaukee_in_the_nl():
    assert MODERN_MLB_TO_KGJ["HOU"] == 7 and MODERN_MLB_TO_KGJ["MIL"] == 17
    # Each keeps its own name, not the slot's 1994 club.
    houston = KGJTeamRecord(index=7, name="Milwaukee Brewers")
    identity.stamp(houston, _team("HOU", "Houston Astros", "Astros", 18))
    milwaukee = KGJTeamRecord(index=17, name="Houston Astros")
    identity.stamp(milwaukee, _team("MIL", "Milwaukee Brewers", "Brewers", 8))
    assert (houston.name, milwaukee.name) == ("Houston Astros", "Milwaukee Brewers")


def _patch(tmp_path, rom: bytearray, teams: dict, **options):
    src, out = tmp_path / "in.sfc", tmp_path / "out.sfc"
    src.write_bytes(bytes(rom))
    assets = tmp_path / "assets"
    (assets / "logos").mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    _flat((255, 0, 0)).save(buffer, "PNG")
    (assets / "logos" / "77.png").write_bytes(buffer.getvalue())
    messages: list[str] = []
    patcher = KGJMLBPatcher(tmp_path / "cache", assets_dir=assets, on_status=messages.append)
    patcher.patch(
        rom_path=src,
        output_path=out,
        rosters=MappedRosters(game_id="kgj-mlb-snes", teams=teams),
        **options,
    )
    return out.read_bytes(), messages


def test_patch_writes_the_identity_and_grows_the_rom(tmp_path):
    boston = _record(1, "BOSTON", "RED SOX", "BOS", ((13, 43, 86), (189, 48, 57)))
    boston.team_id = 77
    data, messages = _patch(tmp_path, _cartridge(), {1: boston})
    assert len(data) == FOUR_MIB
    assert fx.read_string(data, fx.FULL, 15) == "BOSTON RED SOX"
    assert _logo_colours(data, 15) == {(255, 0, 0)}
    assert resources.entry(data, fx.BATTER_ROAD + 15)[0] == 6
    assert data[0x7FDE] | data[0x7FDF] << 8 == sum(snes.fix_checksum(data)) & 0xFFFF
    reader = KGJRomReader(str(tmp_path / "out.sfc"))
    assert reader.load() and reader.validate()
    assert messages == ["Validating ROM...", "Initializing ROM writer...", "Fetching BOS logo...", "Saving patched ROM..."]


def test_patch_moves_houston_and_milwaukee_and_sorts_the_grid(tmp_path):
    teams = {
        7: _record(7, "HOUSTON", "ASTROS", "HOU", ((0, 45, 98), (235, 110, 31))),
        17: _record(17, "MILWAUKEE", "BREWERS", "MIL"),
        14: _record(14, "ATLANTA", "BRAVES", "ATL"),
    }
    data, _ = _patch(tmp_path, _cartridge(), teams, logos=False)
    word = lambda k: struct.unpack_from("<H", data, fx.DIVISIONS + 2 * k)[0]  # noqa: E731
    # Game team 21 is slot 7, AL West; 3 is slot 17, NL Central.
    assert (word(21), word(3), word(0)) == (0x8004, 0x0002, 0x0000)
    grid = _grid(data)
    assert all(a == b == c for a, b, c in grid.values())
    nl = [grid[xy][0] for xy in fx.NL_CELLS]
    al = [grid[xy][0] for xy in fx.AL_CELLS]
    assert nl == [0, 1, 2, *range(4, 14), 3]  # ATLANTA, CITY01 ... CITY13, MILWAUKEE
    assert al == [*range(14, 21), *range(22, 28), 21]  # CITY14 ... CITY27, HOUSTON
    # Each takes the cartridge's logo of its own club along.
    assert resources.entry(data, fx.LOGO + 21) == (4, 0x1FC, 0x1B3B6B)
    assert resources.entry(data, fx.LOGO + 3) == (4, 0x1CC, 0x1B5A27)


def test_saint_louis_sorts_before_san_diego_as_on_the_cartridge():
    rom = _cartridge()
    identity.write_names(rom, {1: ("SAN DIEGO", "PADRES", "SD"), 2: ("ST LOUIS", "X", "STL")})
    identity.sort_grid(rom)
    grid = _grid(rom)
    assert [grid[xy][0] for xy in fx.NL_CELLS][-2:] == [2, 1]


def test_logos_false_keeps_the_cartridges(tmp_path):
    boston = _record(1, "BOSTON", "RED SOX", "BOS")
    boston.team_id = 77
    rom = _cartridge()
    data, _ = _patch(tmp_path, rom, {1: boston}, logos=False)
    assert len(data) == len(rom)  # no colours, no logos: nothing to store
    assert all(
        resources.entry(data, fx.LOGO + t) == resources.entry(rom, fx.LOGO + t) for t in range(30)
    )
    assert fx.read_string(data, fx.FULL, 15) == "BOSTON RED SOX"


def test_a_headered_image_is_written_behind_its_header(tmp_path):
    boston = _record(1, "BOSTON", "RED SOX", "BOS", ((13, 43, 86), (189, 48, 57)))
    header = bytes(range(256)) * 2
    data, _ = _patch(tmp_path, bytearray(header) + _cartridge(), {1: boston}, logos=False)
    assert data[:512] == header and len(data) == FOUR_MIB + 512
    assert fx.read_string(data[512:], fx.FULL, 15) == "BOSTON RED SOX"


def test_an_unknown_cartridge_keeps_its_identity_with_a_note(tmp_path):
    data, messages = _patch(tmp_path, fixture.build_kgj_rom(), {1: _record(1, "B", "R", "BOS")})
    assert len(data) == fixture.ROM_SIZE
    assert "not the cartridge this patcher knows" in messages[2]
