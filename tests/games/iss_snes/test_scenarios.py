"""ISS (SNES) scenario mode: nine matches in progress, each a pair of slots and a blurb.

`patch` keeps a scenario's pair while both its slots hold a patched team, else points it at
the first pair of patched slots no scenario plays yet, and rewrites the blurb of every
scenario whose two slots it patched, in place: same length, same 0xFF. A scenario that still
plays a slot the patch left keeps the cartridge's blurb.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from retro_roster_patcher.core.models import SlotMapping
from retro_roster_patcher.games.iss_snes import ISSPatcher, scenarios
from retro_roster_patcher.games.iss_snes.models import TEAM_ENUM_ORDER
from retro_roster_patcher.games.iss_snes.rom_writer import ISSRomWriter
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster
from tests.fixtures import synthetic_iss_rom as fixture

LEAGUE = League(id=2001, name="English Premier League")
GROUPS = ["Goalkeeper"] * 2 + ["Defender", "Midfielder", "Forward"] * 6
RETAIL = list(fixture.SCENARIO_PAIRS)
#: Words of the cartridge's nations a rewritten blurb must not hold.
NATION_WORDS = [n for n in TEAM_ENUM_ORDER[:26]] + ["Brazilians", "Italians", "Dutch", "Korea"]


# --- the pairing ---------------------------------------------------------------------------


def test_a_pair_both_of_whose_slots_are_mapped_stays():
    assert scenarios.repoint([(19, 22), (1, 23)], {1, 19, 22, 23}) == [(19, 22), (1, 23)]


def test_a_pair_with_a_slot_left_takes_the_first_pair_of_mapped_slots_no_scenario_plays():
    # (19, 22) stays; 1-23 lost Nigeria and takes 0 and 1, the first two slots no one plays.
    assert scenarios.repoint([(19, 22), (1, 23)], {0, 1, 2, 19, 22}) == [(19, 22), (0, 1)]


def test_with_no_slot_left_free_a_pair_takes_the_first_pair_no_scenario_plays():
    assert scenarios.repoint([(19, 22), (1, 23), (5, 2)], {0, 19, 22}) == [
        (19, 22),
        (0, 19),
        (0, 22),
    ]


def test_a_fallback_pair_takes_the_least_used_teams_first():
    # 0 and 1 play scenario 1: the unplayed (0, 2) and (1, 2) tie on use, slot order picks
    # (0, 2), and (1, 2) is the one unplayed pair left.
    assert scenarios.repoint([(0, 1), (5, 6), (5, 7)], {0, 1, 2}) == [(0, 1), (0, 2), (1, 2)]
    # (1, 2): 1 and 2 each play once, while 0 plays twice.
    assert scenarios.repoint([(0, 1), (0, 2), (5, 6)], {0, 1, 2}) == [(0, 1), (0, 2), (1, 2)]
    # With slots 0-3: (0, 1) and (0, 2) taken, (1, 3) uses 1 once and 3 never, (2, 3) the same,
    # (1, 2) twice: the least used in slot order is (1, 3).
    assert scenarios.repoint([(0, 1), (0, 2), (5, 6)], {0, 1, 2, 3}) == [(0, 1), (0, 2), (1, 3)]


def test_a_pair_on_a_blanked_slot_reuses_a_played_pair_rather_than_stay():
    assert scenarios.repoint([(0, 1), (5, 6)], {0, 1}, blank={5, 6, 7}) == [(0, 1), (0, 1)]
    # Without blanking, a slot the patch left is the cartridge's team: the pair stays.
    assert scenarios.repoint([(0, 1), (5, 6)], {0, 1}) == [(0, 1), (5, 6)]


def test_with_fewer_than_two_patched_teams_a_blanked_pair_stays():
    assert scenarios.repoint([(5, 6)], {0}, blank={5, 6}) == [(5, 6)]


def test_a_pair_on_kept_slots_stays_but_no_pair_moves_onto_them():
    # 30 and 31 still hold the cartridge's teams: their pair stays, and the pair that lost
    # slot 5 plays two patched teams, never a kept one.
    assert scenarios.repoint([(30, 31), (5, 0)], {0, 1, 2}, kept={30, 31}) == [(30, 31), (0, 1)]
    # A pair with one kept and one patched slot stays too (a nation the fill moved).
    assert scenarios.repoint([(30, 2)], {0, 2}, kept={30}) == [(30, 2)]


@pytest.mark.parametrize("mapped", [set(), {4}, {19}])
def test_fewer_than_two_mapped_slots_leave_every_pair(mapped):
    assert scenarios.repoint(RETAIL, mapped) == RETAIL


def test_a_team_never_plays_itself_and_no_pair_is_played_twice():
    pairs = scenarios.repoint(RETAIL, set(range(20)))
    assert all(a != b and a in range(20) and b in range(20) for a, b in pairs)
    assert len({frozenset(p) for p in pairs}) == len(pairs)


# --- the blurb ------------------------------------------------------------------------------


@pytest.mark.parametrize("length", fixture.SCENARIO_LENGTHS)
@pytest.mark.parametrize(
    ("a", "b"), [("Arsenal", "Spurs"), ("Wolverhampton Wanderers", "Brighton & Hove Albion")]
)
def test_a_blurb_fills_the_cartridge_s_length_in_23_character_lines(length, a, b):
    text = scenarios.blurb(a, b, length)
    assert len(text) == length
    assert 0xFF not in text and 0xFD not in text
    assert b"&" not in text  # the font has none: "and"
    assert b"'" not in text  # the font's apostrophe is the backtick
    assert a.split()[0].encode() in text


def test_a_blurb_writes_the_apostrophe_as_the_font_s_backtick():
    assert b"Nott`m Forest" in scenarios.blurb("Nott'm Forest", "Spurs", 79)


# --- the patch ------------------------------------------------------------------------------


def roster(team_id: int, name: str) -> TeamRoster:
    team = Team(id=team_id, name=name, short_name=name, color="FF0000", alternate_color="FFFFFF")
    players = [Player(id=team_id * 100 + i, name=f"P{i}", position=g) for i, g in enumerate(GROUPS)]
    return TeamRoster(team=team, players=players)


@pytest.fixture
def rom(tmp_path):
    return fixture.write_iss_rom(tmp_path / "iss.sfc")


@pytest.fixture
def patcher(tmp_path):
    return ISSPatcher(cache_dir=tmp_path / "cache")


def _rosters(patcher, slots):
    clubs = [roster(100 + s, f"Club {chr(65 + k)}") for k, s in enumerate(slots)]
    data = LeagueData(league=LEAGUE, teams=clubs)
    return patcher.map_rosters(data, [SlotMapping(slot_index=s, team_id=100 + s) for s in slots])


def _patch(patcher, rom, out, rosters, **options):
    patcher.patch(rom_path=rom, output_path=out, rosters=rosters, looks=False, **options)
    return out.read_bytes()


def _pairs(image: bytes) -> list[tuple[int, int]]:
    raw = image[scenarios.PAIRS : scenarios.PAIRS + 2 * scenarios.COUNT]
    return [(raw[2 * k] // 2, raw[2 * k + 1] // 2) for k in range(scenarios.COUNT)]


def _text(image: bytes, k: int) -> bytes:
    at = fixture.scenario_offset(k)
    return image[at : at + fixture.SCENARIO_LENGTHS[k] + 1]


def _region(image: bytes) -> bytes:
    end = fixture.scenario_offset(8) + fixture.SCENARIO_LENGTHS[8] + 1
    pairs = image[scenarios.PAIRS : scenarios.PAIRS + 2 * scenarios.COUNT]
    return pairs + image[fixture.SCENARIO_TEXT_BASE : end]


def _rewritten(image: bytes, k: int, names: dict[int, str]) -> None:
    """Scenario k's blurb names its pair's patched teams, keeps its length and 0xFF, and
    holds no nation of the cartridge."""
    text = _text(image, k)
    assert text[-1] == 0xFF and 0xFF not in text[:-1], k
    words = " ".join(text[:-1].decode("ascii").split())
    a, b = _pairs(image)[k]
    assert names[a] in words and names[b] in words, (k, words)
    assert not [w for w in NATION_WORDS if w in words], (k, words)


def test_the_sequential_map_points_every_scenario_at_two_clubs_and_rewrites_its_blurb(
    patcher, rom, tmp_path
):
    """The app's map: clubs in 0-19, 20-25 left as the cartridge has them."""
    rosters = _rosters(patcher, range(20))
    image = _patch(patcher, rom, tmp_path / "out.sfc", rosters)
    names = {s: t.name for s, t in rosters.teams.items()}
    pairs = _pairs(image)
    # Kept where both slots are clubs: scenarios 4-7 and 9.
    assert [pairs[k] for k in (3, 4, 5, 6, 8)] == [RETAIL[k] for k in (3, 4, 5, 6, 8)]
    assert all(a in names and b in names and a != b for a, b in pairs)
    for k in range(scenarios.COUNT):
        _rewritten(image, k, names)


def test_with_every_slot_filled_the_pairs_stay_and_every_blurb_is_rewritten(patcher, rom, tmp_path):
    rosters = _rosters(patcher, range(26))
    image = _patch(patcher, rom, tmp_path / "out.sfc", rosters)
    assert _pairs(image) == RETAIL
    names = {s: t.name for s, t in rosters.teams.items()}
    for k in range(scenarios.COUNT):
        _rewritten(image, k, names)


def test_blanked_slots_are_never_played(patcher, rom, tmp_path):
    rosters = _rosters(patcher, range(20))
    image = _patch(patcher, rom, tmp_path / "out.sfc", rosters, blank_unused=True)
    assert all(a < 20 and b < 20 for a, b in _pairs(image))


def test_with_blanked_slots_and_few_clubs_every_scenario_plays_two_clubs(patcher, rom, tmp_path):
    rosters = _rosters(patcher, [0, 1, 2])
    image = _patch(patcher, rom, tmp_path / "out.sfc", rosters, blank_unused=True)
    names = {s: t.name for s, t in rosters.teams.items()}
    assert all(a in names and b in names and a != b for a, b in _pairs(image))
    for k in range(scenarios.COUNT):
        _rewritten(image, k, names)


def test_a_scenario_too_few_patched_slots_reach_keeps_its_pair_and_blurb(patcher, rom, tmp_path):
    """Brazil, the U.S.A. and Germany patched: scenario 1 stays, 2 and 3 take Germany with
    each of the others, and 4-9 keep the cartridge's pair and blurb."""
    rosters = _rosters(patcher, [0, 19, 22])
    before = rom.read_bytes()
    image = _patch(patcher, rom, tmp_path / "out.sfc", rosters)
    assert _pairs(image) == [(19, 22), (0, 19), (0, 22), *RETAIL[3:]]
    names = {s: t.name for s, t in rosters.teams.items()}
    for k in range(3):
        _rewritten(image, k, names)
    for k in range(3, scenarios.COUNT):
        assert _text(image, k) == _text(before, k)


def test_a_patch_that_reaches_no_scenario_leaves_scenario_mode_as_it_was(patcher, rom, tmp_path):
    image = _patch(patcher, rom, tmp_path / "out.sfc", _rosters(patcher, [4]))
    assert _region(image) == _region(rom.read_bytes())


def test_with_no_team_written_scenario_mode_stays_as_it_was(rom, tmp_path):
    with ISSRomWriter(str(rom), str(tmp_path / "out.sfc")) as writer:
        writer.write_scenarios({})
        writer.finalize()
    assert (tmp_path / "out.sfc").read_bytes() == rom.read_bytes()


def test_re_patching_gives_the_same_scenarios(patcher, rom, tmp_path):
    rosters = _rosters(patcher, range(20))
    first = _patch(patcher, rom, tmp_path / "a.sfc", rosters)
    assert _patch(patcher, tmp_path / "a.sfc", tmp_path / "b.sfc", rosters) == first


def test_a_fill_over_a_plain_patch_gives_the_scenarios_of_a_direct_fill(patcher, rom, tmp_path):
    """The plain patch re-points scenarios onto clubs; the fill over it starts again from the
    cartridge's pairs, as a fill on the cartridge does."""
    full = _rosters(patcher, range(26))
    direct = _patch(patcher, rom, tmp_path / "direct.sfc", full)
    _patch(patcher, rom, tmp_path / "plain.sfc", _rosters(patcher, range(20)))
    again = _patch(patcher, tmp_path / "plain.sfc", tmp_path / "again.sfc", full)
    assert _pairs(again) == _pairs(direct) == RETAIL
    assert _region(again) == _region(direct)


def test_the_packaged_pairs_are_the_fixture_s():
    assert list(scenarios.RETAIL) == RETAIL


# --- the real cartridge ---------------------------------------------------------------------


@pytest.fixture
def retail_rom() -> Path:
    if not os.environ.get("RETRO_ROSTER_TEST_ROMS"):
        pytest.skip("set RETRO_ROSTER_TEST_ROMS to a directory holding iss.sfc")
    path = Path(os.environ["RETRO_ROSTER_TEST_ROMS"]) / "iss.sfc"
    if not path.exists():
        pytest.fail(f"RETRO_ROSTER_TEST_ROMS is set but {path} is missing")
    return path


def _blurbs(image: bytes) -> list[bytes]:
    out = []
    for k in range(scenarios.COUNT):
        pointer = int.from_bytes(image[0x38000 + 2 * (27 + k) :][:2], "little")
        at = 0x8000 + pointer
        out.append(image[at : image.index(0xFF, at) + 1])
    return out


def _lines(text: bytes) -> int:
    """The lines a blurb takes: 23 characters each, 0xFD a line break."""
    return sum(max(1, -(-len(part) // 23)) for part in text.rstrip(b"\xff").split(b"\xfd"))


@pytest.mark.real_rom
def test_the_cartridge_s_scenarios_are_the_1994_pairs_and_blurbs(retail_rom):
    image = retail_rom.read_bytes()
    named = [(TEAM_ENUM_ORDER[a], TEAM_ENUM_ORDER[b]) for a, b in _pairs(image)]
    assert named == [
        ("Brazil", "U.S.A."), ("Italy", "Nigeria"), ("S.Korea", "Spain"),
        ("Scotland", "Holland"), ("Bulgaria", "Germany"), ("Holland", "Brazil"),
        ("Argentina", "Romania"), ("U.S.A.", "Colombia"), ("Bulgaria", "France"),
    ]  # fmt: skip
    blurbs = _blurbs(image)
    assert [len(b) - 1 for b in blurbs] == list(fixture.SCENARIO_LENGTHS)
    assert blurbs[0].startswith(b"The Brazilians are down")


@pytest.mark.real_rom
def test_the_cartridge_s_blurbs_are_rewritten_in_place(patcher, retail_rom, tmp_path):
    before = retail_rom.read_bytes()
    rosters = _rosters(patcher, range(20))
    image = _patch(patcher, retail_rom, tmp_path / "a.sfc", rosters)
    names = {s: t.name for s, t in rosters.teams.items()}
    assert all(a in names and b in names for a, b in _pairs(image))
    for k, (old, new) in enumerate(zip(_blurbs(before), _blurbs(image), strict=True)):
        assert len(new) == len(old) and _lines(new) <= _lines(old), k
        words = new[:-1].decode("ascii")
        assert not [w for w in NATION_WORDS if w in words], (k, words)
    assert _patch(patcher, tmp_path / "a.sfc", tmp_path / "b.sfc", rosters) == image
