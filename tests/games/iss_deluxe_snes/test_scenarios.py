"""ISS Deluxe scenario mode: twelve matches under way, each a record, a title and a description.

`patch` keeps a scenario's pair while both its slots hold a patched team, else points it at
the first pair of patched slots no scenario plays yet (`iss_snes.scenarios.repoint`), and
rewrites the title ("NO.7  MAN CITY 0-1 LIVERPOOL", "1:43 MAN CITY'S F.K.") and the
description of every scenario whose two slots it patched, at their fixed widths. A scenario
that still plays a slot the patch left keeps the cartridge's texts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from retro_roster_patcher.core.models import SlotMapping
from retro_roster_patcher.games.iss_deluxe_snes import rom as R
from retro_roster_patcher.games.iss_deluxe_snes import scenarios
from retro_roster_patcher.games.iss_deluxe_snes.patcher import ISSDeluxePatcher
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster

#: The cartridge's twelve records, as the USA cartridge holds them.
RECORDS = bytes.fromhex(
    "34011401002201020104100009400022010201101a02030301908005200174015401043c0103010410000980"
    "0176005600100000000305908006000269004900341601010202100005ffff5b004200140801010302900006"
    "800058014301182e00010305904005c00034012401281c0000010110ffff0002580038000c0a0001030390c0"
    "064000690049003e3c02020106100009400050013801022c0103030390000680012200020004300101010410"
    "00090002"
)
RETAIL = [
    (0, 17), (8, 13), (2, 30), (8, 0), (26, 11), (10, 4),
    (12, 23), (20, 14), (6, 5), (31, 30), (1, 22), (2, 24),
]  # fmt: skip
SCORES = ["1-2", "2-3", "1-3", "0-0", "1-1", "1-1", "0-1", "0-0", "0-1", "2-2", "1-3", "1-1"]
CLOCKS = "1:14 1:02 1:54 0:56 0:49 0:42 1:43 1:24 0:38 0:49 1:38 0:02".split()
#: Words of the cartridge's nations a rewritten title or description must not hold.
NATION_WORDS = [n.upper() for n in R.TEAMS] + ["ITALIANS", "ASIA", "CZECH"]
_TEXT_CHARS = {v: k for k, v in scenarios.TEXT_FONT.items()}


def _synthetic() -> bytearray:
    image = bytearray(R.ROM_SIZE)
    image[scenarios.RECORDS : scenarios.RECORDS + len(RECORDS)] = RECORDS
    return image


def _pairs(image) -> list[tuple[int, int]]:
    return scenarios.pairs(image)


def _title(image, k: int) -> tuple[str, str]:
    at = scenarios.TITLES + scenarios.TITLE * k
    raw = image[at : at + scenarios.TITLE]
    return R.decode_name(raw[: scenarios.MATCHUP]), R.decode_name(raw[scenarios.MATCHUP :])


def _text(image, k: int) -> str:
    at = scenarios.TEXTS + scenarios.TEXT * k
    return "".join(_TEXT_CHARS.get(b, "?") for b in image[at : at + scenarios.TEXT])


def _outside(image) -> bytes:
    """The image with the records' team bytes, the titles and the descriptions zeroed."""
    out = bytearray(image)
    for k in range(scenarios.COUNT):
        at = scenarios.RECORDS + scenarios.RECORD * k
        out[at + 4 : at + 6] = bytes(2)
    out[scenarios.TITLES : scenarios.TITLES + scenarios.TITLE * scenarios.COUNT] = bytes(
        scenarios.TITLE * scenarios.COUNT
    )
    out[scenarios.TEXTS : scenarios.TEXTS + scenarios.TEXT * scenarios.COUNT] = bytes(
        scenarios.TEXT * scenarios.COUNT
    )
    return bytes(out)


def _rewritten(image, k: int, names: dict[int, str]) -> None:
    a, b = (names[s].upper() for s in _pairs(image)[k])
    matchup, situation = _title(image, k)
    assert matchup == f"NO.{k + 1}".ljust(5) + f" {a} {SCORES[k]} {b}", k
    assert situation.startswith(CLOCKS[k] + " ") and situation.endswith(scenarios.PIECES[k])
    text = " ".join(_text(image, k).split())
    assert a in text and b in text and SCORES[k] in text, (k, text)
    words = f"{matchup} {situation} {text}"
    assert not [w for w in NATION_WORDS if w in words], (k, words)


def test_the_twelve_records_are_the_cartridge_s_pairs():
    assert _pairs(_synthetic()) == RETAIL


def test_the_clubs_the_app_maps_first_play_every_scenario_and_name_it():
    image = _synthetic()
    names = {s: f"Club {chr(65 + k)}" for k, s in enumerate(R.SLOT_ORDER[:20])}
    before = bytes(image)
    scenarios.write(image, names)
    pairs = _pairs(image)
    assert all(a in names and b in names and a != b for a, b in pairs)
    # Romania-Poland, Scotland-Russia, Portugal-Ireland and Holland-Czech Rep. are clubs' slots.
    assert [pairs[k] for k in (6, 7, 8, 10)] == [RETAIL[k] for k in (6, 7, 8, 10)]
    for k in range(scenarios.COUNT):
        _rewritten(image, k, names)
    assert _outside(image) == _outside(before)


def test_with_every_slot_filled_the_pairs_stay():
    image = _synthetic()
    names = {s: f"Team {s}" for s in range(len(R.TEAMS))}
    scenarios.write(image, names)
    assert _pairs(image) == RETAIL
    for k in range(scenarios.COUNT):
        _rewritten(image, k, names)


def test_a_scenario_the_patch_cannot_reach_keeps_its_pair_and_texts():
    """Italy and Croatia patched: scenario 1 is theirs; no other pair is left."""
    image = _synthetic()
    before = bytes(image)
    scenarios.write(image, {0: "Club A", 17: "Club B"})
    assert _pairs(image) == RETAIL
    _rewritten(image, 0, {0: "Club A", 17: "Club B"})
    for k in range(1, scenarios.COUNT):
        assert _title(image, k) == _title(before, k) and _text(image, k) == _text(before, k)


@pytest.mark.parametrize("names", [{}, {3: "Club A"}])
def test_a_patch_that_reaches_no_scenario_leaves_scenario_mode_as_it_was(names):
    image = _synthetic()
    scenarios.write(image, names)
    assert image == _synthetic()


def test_a_long_name_is_cut_to_the_title_and_the_situation_drops_it():
    image = _synthetic()
    names = {0: "Wolverhampton Wanderers", 17: "Brighton and Hove Albion"}
    scenarios.write(image, names)
    matchup, situation = _title(image, 0)
    assert len(matchup) <= scenarios.MATCHUP and matchup.startswith("NO.1  WOLVERHAMP")
    assert situation == "1:14 C.K."


def test_writing_twice_gives_the_same_image():
    names = {s: f"Club {chr(65 + k)}" for k, s in enumerate(R.SLOT_ORDER[:20])}
    once = _synthetic()
    scenarios.write(once, names)
    twice = bytearray(once)
    scenarios.write(twice, names)
    assert twice == once


# --- the cartridge ------------------------------------------------------------------------

LEAGUE = League(id=2001, name="English Premier League")
GROUPS = ["Goalkeeper"] * 2 + ["Defender", "Midfielder", "Attacker"] * 6


def roster(team_id: int, name: str) -> TeamRoster:
    team = Team(id=team_id, name=name, short_name=name, color="00A000", alternate_color="FFFFFF")
    players = [Player(id=team_id * 100 + i, name=f"P{i}", position=g) for i, g in enumerate(GROUPS)]
    return TeamRoster(team=team, players=players)


@pytest.mark.real_rom
def test_the_cartridge_s_scenarios_are_the_1996_matches(deluxe_rom: Path):
    image = deluxe_rom.read_bytes()
    assert image[scenarios.RECORDS : scenarios.RECORDS + len(RECORDS)] == RECORDS
    assert _title(image, 6) == ("NO.7  ROMANIA 0-1 POLAND", "1:43 ROMANIA'S F.K.")
    assert _title(image, 4) == ("NO.5  TURKEY 1-1 SWEDEN", "0:49 TURKEY'S THROW IN")
    assert _text(image, 0).startswith("It seems like a defeat forITALY against")
    assert _text(image, 11).rstrip().endswith("game ends in a tie.")


@pytest.mark.real_rom
def test_the_cartridge_s_scenarios_name_the_league_s_clubs(deluxe_rom: Path, tmp_path):
    patcher = ISSDeluxePatcher(tmp_path / "cache")
    clubs = [roster(100 + k, f"Club {chr(65 + k)}") for k in range(20)]
    rosters = patcher.map_rosters(
        LeagueData(league=LEAGUE, teams=clubs),
        [SlotMapping(slot_index=s, team_id=100 + k) for k, s in enumerate(R.SLOT_ORDER[:20])],
    )
    out = tmp_path / "a.sfc"
    patcher.patch(rom_path=deluxe_rom, output_path=out, rosters=rosters, looks=False, logos=False)
    image = out.read_bytes()
    names = {s: t.name for s, t in rosters.teams.items()}
    for k in range(scenarios.COUNT):
        _rewritten(image, k, names)
    again = tmp_path / "b.sfc"
    patcher.patch(rom_path=out, output_path=again, rosters=rosters, looks=False, logos=False)
    assert again.read_bytes() == image
