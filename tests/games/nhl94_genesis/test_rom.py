"""Checks against the retail cartridge, `NHL '94 (USA, Europe).md`, saved as
`nhl94.md` in the directory RETRO_ROSTER_TEST_ROMS names. Skipped without it."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from retro_roster_patcher.games.nhl94_genesis.patcher import NHL94GenesisPatcher
from retro_roster_patcher.games.nhl94_genesis.rom_reader import NHL94GenesisRomReader
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster

LA = 10

real_rom = pytest.mark.skipif(
    not os.environ.get("RETRO_ROSTER_TEST_ROMS"),
    reason="set RETRO_ROSTER_TEST_ROMS to a directory holding nhl94.md",
)


def _rom() -> Path:
    rom = Path(os.environ["RETRO_ROSTER_TEST_ROMS"]) / "nhl94.md"
    if not rom.is_file():
        pytest.fail(f"RETRO_ROSTER_TEST_ROMS is set but {rom} is missing")
    return rom


def _squad(tag: str) -> list[Player]:
    """Two goalies, four of each forward position and six defencemen, named by
    position: "LAK G1", "LAK C1", ... in the order a coach would dress them."""
    players = []
    for pos, count in (("G", 2), ("C", 4), ("LW", 4), ("RW", 4), ("D", 6)):
        for k in range(count):
            players.append(
                Player(id=len(players) + 1, name=f"{tag} {pos}{k + 1}", position=pos, number=k + 1)
            )
    return players


def _patch(tmp_path: Path, rosters: dict[str, list[Player]], **options) -> Path:
    patcher = NHL94GenesisPatcher(tmp_path / "cache", provider="nhl")
    data = LeagueData(
        league=League(id=0, name="NHL", country="USA", country_code="US", logo_url="", season=2025),
        teams=[
            TeamRoster(team=Team(id=i, name=code, code=code), players=players)
            for i, (code, players) in enumerate(rosters.items())
        ],
    )
    out = tmp_path / "patched.md"
    patcher.patch(rom_path=_rom(), output_path=out, rosters=patcher.map_rosters(data), **options)
    return out


@pytest.mark.real_rom
@real_rom
def test_every_record_of_a_patched_team_starts_on_an_even_byte(tmp_path):
    # Odd names ("LAK LW1") get a NUL; without it the game's word-read roster walk
    # took an address error and hung on Matchups.
    out = _patch(tmp_path, {"LAK": _squad("LAK")})
    reader = NHL94GenesisRomReader(str(out))
    assert reader.load()
    at, _ = reader.get_team_player_region(LA)
    count = 0
    while (length := reader._read_u16_be(at)) >= 3:
        assert at % 2 == 0 and length % 2 == 0
        at += length + 8
        count += 1
    assert count == 20


@pytest.mark.real_rom
@real_rom
def test_line_zero_names_the_starting_six(tmp_path):
    # The cartridge counts line-table players from 1: its own LA line 0 is
    # [1, 17, 18, 8, 4, 13, 5, 0], Hrudey, Blake, McSorley, Robitaille, Gretzky,
    # Sandstrom.
    out = _patch(tmp_path, {"LAK": _squad("LAK")})
    reader = NHL94GenesisRomReader(str(out))
    assert reader.load()
    names, _ = reader.read_team_roster(LA)
    lines = reader.get_team_section_offsets(LA)["lines"]
    line0 = reader.data[lines : lines + 8]
    assert [names[i - 1] for i in line0[:6]] == [
        "LAK G1",
        "LAK D1",
        "LAK D2",
        "LAK LW1",
        "LAK C1",
        "LAK RW1",
    ]
    assert line0[7] == 0
