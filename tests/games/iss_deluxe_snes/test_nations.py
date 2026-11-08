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
from pathlib import Path

import pytest

from retro_roster_patcher.core.models import SlotMapping
from retro_roster_patcher.games.iss_deluxe_snes import bignames, flags, kits, names, pages
from retro_roster_patcher.games.iss_deluxe_snes import rom as R
from retro_roster_patcher.games.iss_deluxe_snes.patcher import ISSDeluxePatcher
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster
from tests.games.iss_deluxe_snes.test_pages import _cartridge, _labels

ITALY, ENGLAND, SPAIN, FRANCE, CROATIA, TURKEY, CAMEROON, MEXICO = 0, 2, 4, 9, 17, 26, 28, 33
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
def rom(deluxe_rom: Path) -> Path:
    return deluxe_rom


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


def _league_pages_hold_only(image, clubs) -> None:
    """Every page labelled with the league holds only `clubs`."""
    league = pages._drawn(pages.label(LEAGUE.name))
    order, labels = pages.order(image), _labels(image)
    for p in range(6):
        if labels[p] == league:
            assert set(order[6 * p : 6 * p + 6]) <= set(clubs), p


def _league(patcher, count):
    clubs = [roster(100 + k, f"Club {k}") for k in range(count)]
    return patcher.map_rosters(
        LeagueData(league=LEAGUE, teams=clubs),
        [SlotMapping(slot_index=s, team_id=100 + k) for k, s in enumerate(R.SLOT_ORDER[:count])],
    )


@pytest.mark.real_rom
def test_no_nation_stands_on_a_page_labelled_with_the_league(patcher, rom, tmp_path):
    image = _patch(patcher, rom, tmp_path / "out.sfc", _rosters(patcher))
    _league_pages_hold_only(image, R.SLOT_ORDER[:20])
    assert _labels(image)[:3] == [pages._drawn(pages.label(LEAGUE.name))] * 3


@pytest.mark.real_rom
def test_a_page_an_earlier_patch_labelled_with_the_league_loses_the_label(patcher, rom, tmp_path):
    """The league alone labels page 4 (two clubs and four nations); the fill on that
    image leaves page 4 to the nations, so the label goes."""
    plain = tmp_path / "plain.sfc"
    _patch(patcher, rom, plain, _league(patcher, 20))
    assert _labels(plain.read_bytes())[3] == pages._drawn(pages.label(LEAGUE.name))
    image = _patch(patcher, plain, tmp_path / "fill.sfc", _rosters(patcher))
    _league_pages_hold_only(image, R.SLOT_ORDER[:20])
    assert _labels(image)[3] == pages._drawn(pages.OTHERS)


@pytest.mark.real_rom
def test_a_short_fill_leaves_no_cartridge_nation_on_a_league_page(patcher, rom, tmp_path):
    """One nation (Brazil, page 6) for 16 free slots: page 4's two clubs share it
    with England, Germany, Italy and Spain, still the cartridge's, so it is not the
    league's."""
    nations = LeagueData(league=NATIONS, teams=[roster(1, "Brazil")])
    rosters = _league(patcher, 20).merge(
        patcher.map_rosters(nations, [SlotMapping(slot_index=30, team_id=1)])
    )
    image = _patch(patcher, rom, tmp_path / "out.sfc", rosters)
    _league_pages_hold_only(image, R.SLOT_ORDER[:20])


@pytest.mark.real_rom
def test_a_nation_back_on_a_slot_an_earlier_patch_gave_a_club_writes_its_own(
    patcher, rom, tmp_path
):
    """20 clubs, then 18 and the fill: Croatia's own slot 17 still holds a club from
    the first patch, so Croatia writes its own name, big name, flag and kits there."""
    retail = rom.read_bytes()
    first = tmp_path / "first.sfc"
    _patch(patcher, rom, first, _league(patcher, 20))
    nations = LeagueData(league=NATIONS, teams=[roster(1, "Croatia")])
    rosters = _league(patcher, 18).merge(
        patcher.map_rosters(nations, [SlotMapping(slot_index=CROATIA, team_id=1)])
    )
    image = _patch(patcher, first, tmp_path / "second.sfc", rosters)
    assert names.read(image, CROATIA) == names.render(names.fit("Croatia", "Croatia"))
    assert bignames.read(image)[CROATIA] == bignames.sprites("CROATIA")
    assert _shown(image, CROATIA) == _shown(retail, CROATIA)
    for kit in (0, 1):
        assert kits.resolve(image, CROATIA, kit).words == kits.resolve(retail, CROATIA, kit).words


@pytest.mark.real_rom
def test_the_packaged_kits_and_flags_are_the_cartridge_s(patcher, rom, tmp_path):
    retail = rom.read_bytes()
    restored = kits.retail(_patch(patcher, rom, tmp_path / "out.sfc", _rosters(patcher)))
    for team in range(kits.TEMPLATES):
        for at, n in kits._fields(retail, team):
            assert restored[at : at + n] == retail[at : at + n], (team, at)
        assert flags.read(restored, team) == flags.read(retail, team), team


@pytest.mark.real_rom
def test_re_patching_with_the_fill_gives_the_same_image(patcher, rom, tmp_path):
    """France's own slot holds a club after the first patch: the second still
    finds France's retail flag and kits, and changes nothing."""
    retail = rom.read_bytes()
    rosters = _rosters(patcher)
    a, b = tmp_path / "a.sfc", tmp_path / "b.sfc"
    first = _patch(patcher, rom, a, rosters)
    second = _patch(patcher, a, b, rosters)
    assert second == first
    assert _shown(second, TURKEY) == _shown(retail, FRANCE)
    assert kits.resolve(second, TURKEY, 0).words == kits.resolve(retail, FRANCE, 0).words


@pytest.mark.real_rom
@pytest.mark.parametrize(
    ("given", "numbers"),
    [(True, CROATIA), (False, MEXICO), ({MEXICO: {"home": {"template": ENGLAND}}}, MEXICO)],
)
def test_a_moved_nation_numbers_the_shirts_only_of_its_own_kit(
    patcher, rom, tmp_path, given, numbers
):
    """Croatia on Mexico's slot: flag entry 12, the colour of the numbers of a kit whose number
    byte is 0 (Croatia's and Mexico's both are), is Croatia's only while the slot wears Croatia's
    kit; with `kits=False` or a template of another team the slot keeps Mexico's."""
    retail = rom.read_bytes()
    rosters = patcher.map_rosters(
        LeagueData(league=NATIONS, teams=[roster(1, "Croatia")]),
        [SlotMapping(slot_index=MEXICO, team_id=1)],
    )
    out = tmp_path / "out.sfc"
    patcher.patch(rom_path=rom, output_path=out, rosters=rosters, looks=False, kits=given)
    image = out.read_bytes()
    assert flags.colours(image, MEXICO)[0] == flags.colours(retail, numbers)[0]
