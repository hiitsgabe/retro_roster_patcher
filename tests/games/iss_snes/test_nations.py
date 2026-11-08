"""ISS (SNES) filled with today's national teams (`--fill-nations`).

A nation on its own cartridge slot keeps the cartridge's name, flag and kit; one
written over another nation's slot takes its own name, its own retail flag and kit
when the cartridge has it, else ESPN's flag and colours. Every image is the
synthetic one (`tests.fixtures.synthetic_iss_rom`) and every flag is read back
through the image's pointer table.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from retro_roster_patcher.core.kits import default_kits
from retro_roster_patcher.core.models import SlotMapping
from retro_roster_patcher.games.iss_snes import ISSPatcher, kits
from retro_roster_patcher.games.iss_snes.logo import logo_flag
from retro_roster_patcher.games.iss_snes.patcher import GK_KIT
from retro_roster_patcher.games.iss_snes.rom_writer import (
    _DISPLACEMENT_PATCH_BYTE,
    _DISPLACEMENT_PATCH_POINTS,
    _encode_team_name_text,
    _gk_offset,
    _make_shades,
    outfield_block,
)
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster
from retro_roster_patcher.sports.nationals import fetch_top_nations
from tests.fixtures import synthetic_iss_rom as fixture
from tests.games.iss_snes.test_kits import _block, _chest, _name_text, _name_tiles, _player_names
from tests.games.iss_snes.test_rom_writer import _as_shown, _shown
from tests.sports.test_nationals import FakeApi

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

GERMANY, SPAIN, ENGLAND, FRANCE, ARGENTINA, BRAZIL, COLOMBIA, MEXICO, USA = (
    0, 3, 4, 7, 18, 19, 20, 21, 22,
)  # fmt: skip
LEAGUE = League(id=2001, name="English Premier League")
NATIONS = League(id=2021, name="International Friendlies")
GROUPS = ["Goalkeeper"] * 2 + ["Defender", "Midfielder", "Forward"] * 6


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (64, 64), (0, 160, 0, 255)).save(out, "PNG")
    return out.getvalue()


PNG = _png()


def transport(url, headers, timeout):
    """FotMob's ranking for the ranking, a green square for every logo."""
    if "fotmob" in url:
        return json.dumps(RANKING).encode()
    return PNG


def roster(team_id: int, name: str, code: str = "", colours=("FF0000", "FFFFFF")) -> TeamRoster:
    team = Team(
        id=team_id, name=name, short_name=name, code=code,
        logo_url=f"https://example.invalid/{team_id}.png",
        color=colours[0], alternate_color=colours[1],
    )  # fmt: skip
    players = [
        Player(id=team_id * 100 + i, name=f"{name[:3]} Player{i:02d}", position=g)
        for i, g in enumerate(GROUPS)
    ]
    return TeamRoster(team=team, players=players)


CLUB = roster(900, "Club Spain Lost", "CSL")
NATION = {
    "Spain": roster(1, "Spain", "ESP"),
    "Colombia": roster(2, "Colombia", "COL"),
    "Japan": roster(3, "Japan", "JPN", ("0000FF", "FFFFFF")),
}


@pytest.fixture
def rom(tmp_path):
    return fixture.write_iss_rom(tmp_path / "iss.sfc")


@pytest.fixture
def patcher(tmp_path):
    return ISSPatcher(cache_dir=tmp_path / "cache", transport=transport)


def _mapped(patcher, nations: dict[int, str]):
    """A club in Spain's slot, and `nations` {slot: name}."""
    clubs = patcher.map_rosters(
        LeagueData(league=LEAGUE, teams=[CLUB]), [SlotMapping(slot_index=SPAIN, team_id=900)]
    )
    if not nations:
        return clubs
    data = LeagueData(league=NATIONS, teams=[NATION[n] for n in nations.values()])
    mapping = [SlotMapping(slot_index=s, team_id=NATION[n].team.id) for s, n in nations.items()]
    return clubs.merge(patcher.map_rosters(data, mapping))


def _patch(patcher, rom, out, rosters):
    patcher.patch(rom_path=rom, output_path=out, rosters=rosters, looks=False)
    return out.read_bytes()


def test_the_fill_takes_every_slot_on_team_select_and_never_the_super_star(tmp_path, rom):
    assert ISSPatcher.supports_nation_fill
    assert ISSPatcher(cache_dir=tmp_path).fill_slots(rom) == list(range(26))


@pytest.mark.parametrize(
    ("name", "slot"),
    [
        ("Spain", SPAIN),
        ("Netherlands", 2),
        ("Switzerland", 14),
        ("Republic of Ireland", 11),
        ("United States", USA),
        ("South Korea", 25),
        ("Japan", None),
    ],
)
def test_a_nation_s_own_slot_comes_from_the_cartridge_s_names(tmp_path, name, slot):
    patcher = ISSPatcher(cache_dir=tmp_path)
    assert patcher.own_national_slot(tmp_path / "iss.sfc", Team(id=1, name=name)) == slot


def test_a_nation_whose_slot_a_club_took_lands_with_its_own_name_flag_and_kit(
    patcher, rom, tmp_path
):
    before = rom.read_bytes()
    image = _patch(patcher, rom, tmp_path / "out.sfc", _mapped(patcher, {MEXICO: "Spain"}))
    # Spain, in Mexico's slot: its name, Spain's flag and kit, never Mexico's.
    assert _name_text(image, MEXICO) == _encode_team_name_text("Spain")
    assert _name_tiles(image, MEXICO) != _name_tiles(before, MEXICO)
    assert b"Spain" in image[fixture.desc_offset(MEXICO) : fixture.desc_offset(MEXICO + 1)]
    assert _shown(image, MEXICO) == _shown(before, SPAIN) != _shown(before, MEXICO)
    assert _block(image, MEXICO, 0) == _block(before, SPAIN, 0)
    assert _block(image, MEXICO, 1) == _block(before, SPAIN, 1)
    assert _chest(image, MEXICO) == _chest(before, SPAIN)
    # The club in Spain's slot wears its own crest.
    assert _shown(image, SPAIN) == _as_shown(logo_flag(PNG))


def test_a_nation_on_its_own_slot_keeps_the_cartridge_s_name_flag_and_kit(patcher, rom, tmp_path):
    before = rom.read_bytes()
    image = _patch(patcher, rom, tmp_path / "out.sfc", _mapped(patcher, {COLOMBIA: "Colombia"}))
    assert _name_text(image, COLOMBIA) == _name_text(before, COLOMBIA)
    # The first patch moves the name tiles out of bank $89, each blob as it was.
    at = fixture.decode_p48000(before[fixture.OFS_NAME_TILES_PTRS + 2 * COLOMBIA :][:2])
    assert _name_tiles(image, COLOMBIA) == before[at : at + len(_name_tiles(image, COLOMBIA))]
    assert _shown(image, COLOMBIA) == _shown(before, COLOMBIA)
    assert _block(image, COLOMBIA, 0) == _block(before, COLOMBIA, 0)
    assert _chest(image, COLOMBIA) == _chest(before, COLOMBIA)
    # Today's squad all the same, and no 1994 blurb.
    assert _player_names(image, COLOMBIA) != _player_names(before, COLOMBIA)
    desc = image[fixture.desc_offset(COLOMBIA) : fixture.desc_offset(COLOMBIA + 1)]
    assert b"Colombia" in desc


def test_a_nation_the_cartridge_lacks_gets_espn_s_flag_and_colours(patcher, rom, tmp_path):
    image = _patch(patcher, rom, tmp_path / "out.sfc", _mapped(patcher, {USA: "Japan"}))
    assert _name_text(image, USA) == _encode_team_name_text("Japan")
    assert _shown(image, USA) == _as_shown(logo_flag(PNG))
    home, _ = default_kits(NATION["Japan"].team)
    assert home is not None
    assert _block(image, USA, 0) == outfield_block((home.shirt, home.shorts, home.socks))


def test_re_patching_with_the_fill_gives_the_same_image(patcher, rom, tmp_path):
    """Spain's own slot holds a club's crest after the first patch: the second
    still finds Spain's retail flag and kit, so a third changes nothing."""
    rosters = _mapped(patcher, {MEXICO: "Spain", COLOMBIA: "Colombia", USA: "Japan"})
    a, b, c = (tmp_path / f"{x}.sfc" for x in "abc")
    _patch(patcher, rom, a, rosters)
    second = _patch(patcher, a, b, rosters)
    assert _patch(patcher, b, c, rosters) == second
    assert _shown(second, MEXICO) == _as_shown(kits.retail_flag(second, SPAIN))
    assert _block(second, MEXICO, 0) == kits.resolve(second, SPAIN, 0).block


def test_retail_flags_are_the_cartridge_s_until_it_is_patched(rom, patcher, tmp_path):
    before = rom.read_bytes()
    assert _as_shown(kits.retail_flag(before, SPAIN)) == _shown(before, SPAIN)
    image = _patch(patcher, rom, tmp_path / "out.sfc", _mapped(patcher, {}))
    flag = kits.retail_flag(image, GERMANY)
    # Germany's black, red and gold bands, from the retail cartridge.
    assert [flag.colours[row[0]] for row in (flag.pixels[2], flag.pixels[8], flag.pixels[13])] == [
        (9, 9, 9),
        (247, 0, 0),
        (255, 181, 0),
    ]


# --- the whole fill: ranking, squads, slots, patch ------------------------------------------

RANKING = [
    {"name": n, "rank": r}
    for r, n in enumerate(
        ["Spain", "Argentina", "Saudi Arabia", "France", "England", "Brazil", "Portugal", "Japan"],
        start=1,
    )
]
ESPN = [
    roster(10 + i, n, c)
    for i, (n, c) in enumerate(
        [("Spain", "ESP"), ("Argentina", "ARG"), ("Saudi Arabia", "KSA"), ("France", "FRA"),
         ("England", "ENG"), ("Brazil", "BRA"), ("Portugal", "POR"), ("Japan", "JPN")]
    )
]  # fmt: skip


def test_the_top_nations_fill_the_six_slots_a_league_leaves(rom, tmp_path):
    lines: list[str] = []
    patcher = ISSPatcher(cache_dir=tmp_path / "cache", transport=transport, on_status=lines.append)
    squads = {r.team.id: list(r.players) for r in ESPN}
    squads[12] = []  # ESPN has no squad for Saudi Arabia
    patcher.api = FakeApi(squads, teams=[r.team for r in ESPN])
    league = LeagueData(
        league=LEAGUE, teams=[roster(900 + s, f"Club {s}", f"C{s:02d}") for s in range(20)]
    )
    clubs = patcher.map_rosters(
        league, [SlotMapping(slot_index=s, team_id=900 + s) for s in range(20)]
    )
    nations, mapping = fetch_top_nations(patcher, rom, 2026, clubs.teams)
    # Spain, Argentina, France, England and Brazil lost their slots to clubs; nobody takes 26.
    assert {m.slot_index: m.team_name for m in mapping} == {
        20: "Spain", 21: "Argentina", 22: "France", 23: "England", 24: "Brazil", 25: "Portugal",
    }  # fmt: skip
    assert any("Saudi Arabia" in line and "Skipped" in line for line in lines)
    assert 17 not in patcher.api.asked  # Japan: the slots ran out first
    before = rom.read_bytes()
    image = _patch(
        patcher, rom, tmp_path / "out.sfc", clubs.merge(patcher.map_rosters(nations, mapping))
    )
    for slot, own in ((20, SPAIN), (21, ARGENTINA), (22, FRANCE), (23, ENGLAND), (24, BRAZIL)):
        assert _shown(image, slot) == _shown(before, own)
        assert _block(image, slot, 0) == _block(before, own, 0)
    assert _name_text(image, 25) == _encode_team_name_text("Portugal")
    assert _shown(image, 25) == _as_shown(logo_flag(PNG))
    assert _player_names(image, 26) == _player_names(before, 26)


# --- a slot an earlier patch wrote ----------------------------------------------------------


def _league(patcher, count: int):
    clubs = [roster(900 + s, f"Club {s}", f"C{s:02d}") for s in range(count)]
    return patcher.map_rosters(
        LeagueData(league=LEAGUE, teams=clubs),
        [SlotMapping(slot_index=s, team_id=900 + s) for s in range(count)],
    )


def test_a_nation_back_on_a_slot_an_earlier_patch_gave_a_club_writes_its_own_name_flag_and_kit(
    patcher, rom, tmp_path
):
    """20 clubs, then 18 and the fill: Argentina's own slot 18 still holds Club 18
    from the first patch, so Argentina writes its name, flag and kits there."""
    first = tmp_path / "first.sfc"
    _patch(patcher, rom, first, _league(patcher, 20))
    nations = LeagueData(league=NATIONS, teams=[roster(1, "Argentina", "ARG")])
    rosters = _league(patcher, 18).merge(
        patcher.map_rosters(nations, [SlotMapping(slot_index=ARGENTINA, team_id=1)])
    )
    image = _patch(patcher, first, tmp_path / "second.sfc", rosters)
    assert _name_text(image, ARGENTINA) == _encode_team_name_text("Argentina")
    assert _name_tiles(image, ARGENTINA) != _name_tiles(first.read_bytes(), ARGENTINA)
    assert _shown(image, ARGENTINA) == _as_shown(kits.retail_flag(image, ARGENTINA))
    assert _block(image, ARGENTINA, 0) == kits.resolve(image, ARGENTINA, 0).block
    assert _block(image, ARGENTINA, 1) == kits.resolve(image, ARGENTINA, 1).block
    at = _gk_offset(ARGENTINA)
    assert at is not None
    keeper = b"".join(w.to_bytes(2, "little") for w in _make_shades(*GK_KIT[0], 4))
    assert image[at + 2 : at + 10] == keeper


# --- the real cartridge ---------------------------------------------------------------------


@pytest.fixture
def retail_rom() -> Path:
    if not os.environ.get("RETRO_ROSTER_TEST_ROMS"):
        pytest.skip("set RETRO_ROSTER_TEST_ROMS to a directory holding iss.sfc")
    path = Path(os.environ["RETRO_ROSTER_TEST_ROMS"]) / "iss.sfc"
    if not path.exists():
        pytest.fail(f"RETRO_ROSTER_TEST_ROMS is set but {path} is missing")
    return path


@pytest.mark.real_rom
def test_the_packaged_flags_are_the_cartridge_s(retail_rom):
    retail = retail_rom.read_bytes()
    patched = bytearray(retail)
    patched[_DISPLACEMENT_PATCH_POINTS[0]] = _DISPLACEMENT_PATCH_BYTE
    for n in range(kits.TEMPLATES):
        assert kits.retail_flag(bytes(patched), n) == kits.retail_flag(retail, n), n


@pytest.mark.real_rom
def test_patching_the_cartridge_and_re_patching_give_the_same_image(patcher, retail_rom, tmp_path):
    """Spain moved, Colombia on its own slot, Japan from ESPN: a patch of the retail
    cartridge and a patch of that patch are the same image."""
    rosters = _mapped(patcher, {MEXICO: "Spain", COLOMBIA: "Colombia", USA: "Japan"})
    first = _patch(patcher, retail_rom, tmp_path / "a.sfc", rosters)
    assert _patch(patcher, tmp_path / "a.sfc", tmp_path / "b.sfc", rosters) == first
