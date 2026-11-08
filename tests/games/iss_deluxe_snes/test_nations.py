"""ISS Deluxe filled with today's national teams (`--fill-nations`).

`team.national` means a nation on its own slot: it keeps the cartridge's name,
flag and kits. Any other nation goes the clubs' way (scoreboard name, big name,
flag), wearing its own retail flag and kit when the cartridge has it, and no
nation stands on a page labelled with the league. The patches need the real
cartridge and are gated on RETRO_ROSTER_TEST_ROMS (a directory holding
iss-deluxe.sfc).
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from retro_roster_patcher.core.models import SlotMapping
from retro_roster_patcher.games.iss_deluxe_snes import bignames, flags, kits, names, pages
from retro_roster_patcher.games.iss_deluxe_snes import rom as R
from retro_roster_patcher.games.iss_deluxe_snes.patcher import ISSDeluxePatcher
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster
from tests.games.iss_deluxe_snes.test_pages import _cartridge, _labels

ITALY, ENGLAND, SPAIN, FRANCE, TURKEY, CAMEROON = 0, 2, 4, 9, 26, 28
LEAGUE = League(id=2001, name="English Premier League")
NATIONS = League(id=2021, name="International Friendlies")
GROUPS = ["Goalkeeper"] * 2 + ["Defender", "Midfielder", "Attacker"] * 6


def roster(team_id: int, name: str) -> TeamRoster:
    team = Team(
        id=team_id, name=name, short_name=name, color="00A000", alternate_color="FFFFFF",
        logo_url=f"https://example.invalid/{team_id}.png",
    )  # fmt: skip
    players = [Player(id=team_id * 100 + i, name=f"P{i}", position=g) for i, g in enumerate(GROUPS)]
    return TeamRoster(team=team, players=players)


def test_the_fill_takes_every_slot_in_the_order_clubs_take_them(tmp_path):
    assert ISSDeluxePatcher.supports_nation_fill
    assert ISSDeluxePatcher(tmp_path).fill_slots(tmp_path / "rom.sfc") == list(R.SLOT_ORDER)


@pytest.mark.parametrize(
    ("name", "slot"),
    [
        ("Spain", SPAIN),
        ("Netherlands", 1),
        ("Republic of Ireland", 5),
        ("Northern Ireland", 21),
        ("Czechia", 22),
        ("Turkiye", TURKEY),
        ("South Korea", 25),
        ("United States", 34),
        ("Senegal", None),
    ],
)
def test_a_nation_s_own_slot_comes_from_the_cartridge_s_names(tmp_path, name, slot):
    patcher = ISSDeluxePatcher(tmp_path)
    assert patcher.own_national_slot(tmp_path / "rom.sfc", Team(id=1, name=name)) == slot


def test_a_nation_is_national_only_on_its_own_slot(tmp_path):
    patcher = ISSDeluxePatcher(tmp_path)
    data = LeagueData(
        league=NATIONS, teams=[roster(1, "Spain"), roster(2, "France"), roster(3, "Senegal")]
    )
    mapping = [
        SlotMapping(slot_index=SPAIN, team_id=1),
        SlotMapping(slot_index=TURKEY, team_id=2),
        SlotMapping(slot_index=CAMEROON, team_id=3),
    ]
    teams = patcher.map_rosters(data, mapping).teams
    assert (teams[SPAIN].national, teams[SPAIN].nation) == (True, SPAIN)
    assert (teams[TURKEY].national, teams[TURKEY].nation) == (False, FRANCE)
    assert (teams[CAMEROON].national, teams[CAMEROON].nation) == (False, None)
    assert {t.league for t in teams.values()} == {""}


def test_a_page_holding_a_nation_is_never_the_league_s():
    image = _cartridge()
    pages.write(image, list(R.SLOT_ORDER[:20]), LEAGUE.name, R.SLOT_ORDER[20:])
    league = pages._drawn("PREMIER")
    # Pages 1-3 hold 18 clubs; page 4 holds two clubs and four nations.
    assert _labels(image)[:3] == [league] * 3
    assert league not in _labels(image)[3:]


# --- the cartridge ------------------------------------------------------------------------


def _png() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGBA", (64, 64), (0, 160, 0, 255)).save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def rom() -> Path:
    if not os.environ.get("RETRO_ROSTER_TEST_ROMS"):
        pytest.skip("set RETRO_ROSTER_TEST_ROMS to a directory holding iss-deluxe.sfc")
    path = Path(os.environ["RETRO_ROSTER_TEST_ROMS"]) / "iss-deluxe.sfc"
    if not path.exists():
        pytest.fail(f"RETRO_ROSTER_TEST_ROMS is set but {path} is missing")
    return path


@pytest.fixture
def patcher(tmp_path):
    pytest.importorskip("PIL")
    png = _png()
    return ISSDeluxePatcher(tmp_path / "cache", transport=lambda url, headers, timeout: png)


def _rosters(patcher):
    """20 clubs in the slots clubs take first (France's among them), then France on
    Turkey's slot, Spain on its own and Senegal, which the cartridge lacks, on
    Cameroon's."""
    clubs = [roster(100 + k, f"Club {k}") for k in range(20)]
    mapped = patcher.map_rosters(
        LeagueData(league=LEAGUE, teams=clubs),
        [SlotMapping(slot_index=s, team_id=100 + k) for k, s in enumerate(R.SLOT_ORDER[:20])],
    )
    nations = LeagueData(
        league=NATIONS, teams=[roster(1, "Spain"), roster(2, "France"), roster(3, "Senegal")]
    )
    mapping = [
        SlotMapping(slot_index=SPAIN, team_id=1),
        SlotMapping(slot_index=TURKEY, team_id=2),
        SlotMapping(slot_index=CAMEROON, team_id=3),
    ]
    return mapped.merge(patcher.map_rosters(nations, mapping))


def _patch(patcher, rom, out, rosters):
    patcher.patch(rom_path=rom, output_path=out, rosters=rosters, looks=False)
    return out.read_bytes()


def _shown(image, team):
    flag = flags.read(image, team)
    return [[flag.colours[v] for v in row] for row in flag.pixels]


@pytest.mark.real_rom
def test_a_nation_whose_slot_a_club_took_wears_its_own_name_flag_and_kit(patcher, rom, tmp_path):
    before = rom.read_bytes()
    image = _patch(patcher, rom, tmp_path / "out.sfc", _rosters(patcher))
    # France on Turkey's slot: its name, big name, flag and kits; never Turkey's.
    assert names.read(image, TURKEY) == names.render(names.fit("France", "France"))
    assert bignames.read(image)[TURKEY] == bignames.sprites("FRANCE")
    assert _shown(image, TURKEY) == _shown(before, FRANCE) != _shown(before, TURKEY)
    for kit in (0, 1):
        assert kits.resolve(image, TURKEY, kit).words == kits.resolve(before, FRANCE, kit).words
    assert kits.chest(image, TURKEY) == kits.chest(before, FRANCE)
    # Spain on its own slot keeps the cartridge's.
    assert names.read(image, SPAIN) == names.read(before, SPAIN)
    assert bignames.read(image)[SPAIN] == bignames.read(before)[SPAIN]
    assert _shown(image, SPAIN) == _shown(before, SPAIN)
    assert kits.resolve(image, SPAIN, 0) == kits.resolve(before, SPAIN, 0)
    # Senegal, which the cartridge lacks, gets ESPN's flag.
    assert _shown(image, CAMEROON) != _shown(before, CAMEROON)
    assert names.read(image, CAMEROON) == names.render(names.fit("Senegal", "Senegal"))


@pytest.mark.real_rom
def test_no_nation_stands_on_a_page_labelled_with_the_league(patcher, rom, tmp_path):
    image = _patch(patcher, rom, tmp_path / "out.sfc", _rosters(patcher))
    league = pages._drawn(pages.label(LEAGUE.name))
    order, labels = pages.order(image), _labels(image)
    for p in range(6):
        if labels[p] == league:
            assert all(t not in (SPAIN, TURKEY, CAMEROON) for t in order[6 * p : 6 * p + 6])
            assert all(t in R.SLOT_ORDER[:20] for t in order[6 * p : 6 * p + 6])
    assert labels[:3] == [league] * 3


@pytest.mark.real_rom
def test_re_patching_with_the_fill_gives_the_same_image(patcher, rom, tmp_path):
    """France's own slot holds a club after the first patch: the second still
    finds France's retail flag and kits, so a third changes nothing."""
    retail = rom.read_bytes()
    rosters = _rosters(patcher)
    a, b, c = (tmp_path / f"{x}.sfc" for x in "abc")
    _patch(patcher, rom, a, rosters)
    second = _patch(patcher, a, b, rosters)
    assert _patch(patcher, b, c, rosters) == second
    assert _shown(second, TURKEY) == _shown(retail, FRANCE)
    assert kits.resolve(second, TURKEY, 0).words == kits.resolve(retail, FRANCE, 0).words
    assert kits.retail(retail) == retail
