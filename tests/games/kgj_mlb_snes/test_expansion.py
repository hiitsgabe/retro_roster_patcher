"""Arizona and Tampa Bay in the All-Star teams' places.

The synthetic cartridge is the KGJ fixture, `synthetic_kgj_identity`, and the
code and tables `expansion` edits, transcribed below from the retail
cartridge (headerless file offsets): the two All-Star lookups of the player
accessor, the lineup builder's branch, the season carry-over's branch, the
All-Star menu's option, and the (club, position, slot) tables.
"""

from __future__ import annotations

import struct

from retro_roster_patcher.core.models import MappedRosters
from retro_roster_patcher.games.kgj_mlb_snes import identity, resources
from retro_roster_patcher.games.kgj_mlb_snes.models import KGJPlayerRecord, KGJTeamRecord
from retro_roster_patcher.games.kgj_mlb_snes.patcher import KGJMLBPatcher, _is_al, _lineup
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster
from tests.fixtures import synthetic_kgj_identity as fx
from tests.fixtures import synthetic_kgj_rom as fixture

NL_LOOKUP, AL_LOOKUP = 0x2D7B4, 0x2D802
LINEUP_BRANCH = 0x2D6C3
CARRY_OVER_BRANCH = 0x2C86A
MENU_TEXT, MENU_CELL, MENU_POINTER, MENU_STRING = 0x361A6, 0x273A2, 0x37F61, 0x37FE0
NL_TABLES, AL_TABLES = 0x1D660, 0x1D6F6
BLOCKS = 0x208000


def _all_star(league: int, i: int) -> tuple[int, int, int]:
    """(game team, position byte, roster slot) of All-Star `i` of a league
    (0 NL, 1 AL) in the synthetic tables."""
    return 14 * league + (3 * i) % 14, 2 * (i % 10), (7 * i) % 25


def _cartridge() -> bytearray:
    body = fx.add_identity(fixture.build_kgj_rom())
    body[NL_LOOKUP : NL_LOOKUP + 13] = bytes.fromhex("ae071bac091bc00900d006bf92")
    body[AL_LOOKUP : AL_LOOKUP + 13] = bytes.fromhex("ae071bac091bc00900d006bf28")
    body[LINEUP_BRANCH - 4 : LINEUP_BRANCH + 1] = bytes.fromhex("c93800b005")
    body[CARRY_OVER_BRANCH - 6 : CARRY_OVER_BRANCH + 1] = bytes.fromhex("ad2618c91a00d0")
    body[MENU_TEXT : MENU_TEXT + 13] = _text("ALL-STAR GAME")
    body[MENU_CELL : MENU_CELL + 2] = b"\x02\x00"
    body[MENU_POINTER : MENU_POINTER + 2] = b"\xff\xff"
    body[MENU_STRING : MENU_STRING + 16] = b"\xff" * 16
    for league, tables in enumerate((NL_TABLES, AL_TABLES)):
        for i in range(25):
            team, position, slot = _all_star(league, i)
            for k, word in enumerate((2 * team, position, 2 * slot)):
                struct.pack_into("<H", body, tables + 50 * k + 2 * i, word)
    return body


def _text(text: str) -> bytes:
    """The menu font: the team font's, with "-" at 0x25."""
    return b"\x25".join(fx.encode(part) for part in text.split("-"))


def _player(name: str, pitcher: bool = False) -> KGJPlayerRecord:
    return KGJPlayerRecord(first_initial="A", last_name=name, position="P" if pitcher else "CF")


def _team(slot: int, n: int = 25) -> KGJTeamRecord:
    names = [f"{'AZ' if slot == 28 else 'TB'}{i:02d}" for i in range(n)]
    return KGJTeamRecord(
        index=slot, name="", players=[_player(x, i >= 15) for i, x in enumerate(names)]
    )


def _patch(tmp_path, rom: bytearray, teams: dict, **options) -> tuple[bytes, list[str]]:
    src, out = tmp_path / "in.sfc", tmp_path / "out.sfc"
    src.write_bytes(bytes(rom))
    messages: list[str] = []
    patcher = KGJMLBPatcher(tmp_path / "cache", on_status=messages.append)
    result = patcher.patch(
        rom_path=src,
        output_path=out,
        rosters=MappedRosters(game_id="kgj-mlb-snes", teams=teams),
        looks=False,
        **options,
    )
    messages.append(f"{result.teams_patched} {result.players_patched}")
    return out.read_bytes(), messages


def _name(rom: bytes, at: int) -> str:
    return fixture.decode_name(rom[at + 1 : at + 9])


# --- the blocks --------------------------------------------------------------


def test_arizona_and_tampa_bay_get_blocks_of_their_own(tmp_path):
    data, messages = _patch(tmp_path, _cartridge(), {28: _team(28), 29: _team(29)})
    assert len(data) == 0x400000
    # LDA #$8000 / STA $CE / LDA #$00C1 / STA $D0 / JMP $D889 (and #$8320).
    assert data[NL_LOOKUP : NL_LOOKUP + 13].hex() == "a9008085cea9c10085d04c89d8"
    assert data[AL_LOOKUP : AL_LOOKUP + 13].hex() == "a9208385cea9c10085d04c89d8"
    assert [_name(data, BLOCKS + 32 * i) for i in (0, 24)] == ["AZ00", "AZ24"]
    assert [_name(data, BLOCKS + 800 + 32 * i) for i in (0, 24)] == ["TB00", "TB24"]
    assert messages[-1] == "2 50"


def test_a_block_starts_as_its_all_star_team(tmp_path):
    squad = _team(29)
    squad.players[3] = None
    data, _ = _patch(tmp_path, _cartridge(), {29: squad})
    team, position, slot = _all_star(1, 3)
    expected = bytearray(fixture.build_player_record(identity.team_of(team), slot))
    expected[9] = position
    assert data[BLOCKS + 800 + 96 : BLOCKS + 800 + 128] == expected
    # Only Tampa Bay: the NL All-Stars keep the cartridge's lookup.
    assert data[NL_LOOKUP : NL_LOOKUP + 13] == _cartridge()[NL_LOOKUP : NL_LOOKUP + 13]


def test_the_game_bats_nine_from_the_block_and_copies_no_season_lines(tmp_path):
    data, _ = _patch(tmp_path, _cartridge(), {28: _team(28)})
    assert data[LINEUP_BRANCH] == 0x0C  # BCS to the AL path
    assert data[CARRY_OVER_BRANCH] == 0x80  # BNE made BRA, to its RTS


def test_the_menu_option_reads_arizona_tampa_with_both_clubs(tmp_path):
    one, _ = _patch(tmp_path, _cartridge(), {28: _team(28)})
    assert one[MENU_TEXT : MENU_TEXT + 13] == _cartridge()[MENU_TEXT : MENU_TEXT + 13]
    both, _ = _patch(tmp_path, _cartridge(), {28: _team(28), 29: _team(29)})
    assert both[MENU_TEXT : MENU_TEXT + 13] == _text("ARIZONA-TAMPA")
    (cell,) = struct.unpack_from("<H", both, MENU_CELL)
    (pointer,) = struct.unpack_from("<H", both, 0xF36D - 0x8000 + 0x30000 + cell)
    assert 0x30000 + pointer - 0x8000 == MENU_STRING
    assert both[MENU_STRING : MENU_STRING + 16] == _text(" ARIZONA-TAMPA ") + b"\xd0"


def test_without_arizona_and_tampa_bay_the_all_stars_stay(tmp_path):
    cartridge = _cartridge()
    sites = [
        (NL_LOOKUP, 13),
        (AL_LOOKUP, 13),
        (LINEUP_BRANCH, 1),
        (CARRY_OVER_BRANCH, 1),
        (MENU_TEXT, 13),
        (MENU_CELL, 2),
        (MENU_POINTER, 2),
        (MENU_STRING, 16),
    ]
    both = {28: _team(28), 29: _team(29)}
    for teams, options in (({1: _team(1)}, {}), (both, {"all_stars": True})):
        data, messages = _patch(tmp_path, cartridge, teams, **options)
        assert [data[at : at + n] for at, n in sites] == [cartridge[at : at + n] for at, n in sites]
        assert len(data) == len(cartridge)
        assert messages[-1] == ("1 25" if 1 in teams else "0 0")


def test_a_repatch_writes_the_same_bytes(tmp_path):
    teams = {28: _team(28, 20), 29: _team(29)}
    once, _ = _patch(tmp_path, _cartridge(), teams)
    twice, _ = _patch(tmp_path, bytearray(once), teams)
    assert once == twice


def test_another_image_keeps_its_all_stars(tmp_path):
    data, messages = _patch(tmp_path, fixture.build_kgj_rom(), {28: _team(28)})
    assert "Arizona and Tampa Bay left out" in messages[2]
    assert len(data) == fixture.ROM_SIZE


# --- identity in the All-Stars' places -----------------------------------------


def test_the_all_star_slots_are_their_game_teams():
    assert [identity.team_of(s) for s in (28, 29)] == [28, 29]


def test_the_clubs_names_go_where_the_all_stars_have_entries():
    rom = fx.add_identity(fixture.build_kgj_rom())
    identity.write_names(
        rom, {28: ("ARIZONA", "DIAMONDBACKS", "ARI"), 29: ("TAMPA BAY", "RAYS", "TB")}
    )
    assert fx.read_string(rom, fx.FULL, 28) == "DIAMONDBACKS"  # past 18 characters
    assert fx.read_string(rom, fx.FULL, 29) == "TAMPA BAY RAYS"
    assert fx.decode(rom[fx.ABBREVIATIONS + 84 : fx.ABBREVIATIONS + 90]) == "ARITB "
    assert fx.read_string(rom, fx.NICK, 29, term=0xD0) == "RAYS"
    # No city or singular of their own: the league names stay.
    assert fx.read_string(rom, fx.NICK, 58, term=0xD0) == "LEAGUE N"
    assert fx.read_string(rom, fx.CITY, 27, base=fx.CITY_BASE) == fx.city(27)


def test_the_all_star_teams_have_no_division():
    rom = fx.add_identity(fixture.build_kgj_rom())
    before = bytes(rom[fx.DIVISIONS + 56 : fx.DIVISIONS + 60])
    identity.write_divisions(rom, {28: "ARI", 29: "TB"})
    assert rom[fx.DIVISIONS + 56 : fx.DIVISIONS + 60] == before


def test_the_all_star_teams_get_home_uniforms_only():
    # Their road entries would be other teams' resources: 2333 + 28 is 2361,
    # Atlanta's home batter.
    rom = fx.add_identity(fixture.build_kgj_rom())
    rom += b"\xff" * (0x400000 - len(rom))
    others = [resources.entry(rom, fx.BATTER_HOME), resources.entry(rom, fx.BATTER_ROAD + 28)]
    identity.write_uniforms(rom, {28: ((170, 24, 44), (0, 0, 0))})
    assert resources.entry(rom, fx.BATTER_HOME + 28)[0] == resources.STORED
    assert resources.entry(rom, fx.FIELDER_HOME + 28)[0] == resources.STORED
    assert [
        resources.entry(rom, fx.BATTER_HOME),
        resources.entry(rom, fx.BATTER_ROAD + 28),
    ] == others


# --- mapping -------------------------------------------------------------------


def test_both_clubs_bat_nine_and_play_in_their_league():
    assert len(_lineup(28)) == len(_lineup(29)) == 9
    assert (_is_al(28), _is_al(29)) == (False, True)


def test_map_rosters_puts_the_clubs_in_the_all_star_slots(tmp_path):
    rosters = [
        TeamRoster(
            team=Team(id=k, name=name, code=code, short_name=name.split()[-1]),
            players=[
                Player(id=100 * k + n, name=f"Some Body{n}", position="RP" if n >= 15 else "CF")
                for n in range(25)
            ],
        )
        for k, (name, code) in enumerate(
            (("Arizona Diamondbacks", "ARI"), ("Tampa Bay Rays", "TB"))
        )
    ]
    data = LeagueData(league=League(id=0, name="MLB", season=2026), teams=rosters)
    teams = KGJMLBPatcher(tmp_path / "cache").map_rosters(data).teams
    assert sorted(teams) == [28, 29]
    assert (teams[28].name, teams[28].city, teams[28].nickname) == (
        "Arizona Diamondbacks",
        "ARIZONA",
        "DIAMONDBACKS",
    )
    assert sorted(r.position for r in teams[29].players[:9] if r) == sorted(
        ("C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH")
    )
