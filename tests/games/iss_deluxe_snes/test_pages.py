"""ISS Deluxe team select's pages: a league of any size fits the label tiles.

The synthetic cartridge lays the labels out as the cartridge does: EUROPE 1-4
share ten tiles and add a digit's two, ASIA-AFRICA and N.S.AMERICA take 16
each and ALLSTAR 12, the 62 that VRAM holds.
"""

from __future__ import annotations

import struct

import pytest

from retro_roster_patcher.core.models import SlotMapping
from retro_roster_patcher.core.registry import get_patcher
from retro_roster_patcher.games.iss_deluxe_snes import pages
from retro_roster_patcher.games.iss_deluxe_snes import rom as R
from retro_roster_patcher.games.iss_snes.konami import compress_large
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster

LEAGUE = "Brasileirao Serie A"


def _cartridge() -> bytearray:
    image = bytearray(R.ROM_SIZE)
    image[pages.ORDER : pages.ORDER + pages.NATIONS] = bytes(2 * t for t in R.PAGE_ORDER)
    # Tile k is white from its top row to its bottom one, which no drawn label has.
    stream = compress_large(b"".join(bytes([k, 0] * 8) for k in range(1, 63)))
    image[0xF6C78 : 0xF6C78 + len(stream)] = stream  # $9E:EC78, as on the cartridge
    image[pages.TEMPLATE_SOURCE : pages.TEMPLATE_SOURCE + 3] = (0x9EEC78).to_bytes(3, "little")
    europe = [(0, 1, 2, 3, 4, 5, 11 + 2 * p, 0, 0, 6, 7, 8, 9, 10, 12 + 2 * p, 0) for p in range(4)]
    allstar = (0, *range(51, 57), 0, 0, *range(57, 63), 0)
    for p, tiles in enumerate([*europe, range(19, 35), range(35, 51), allstar]):
        struct.pack_into("<H", image, pages.LABELS + 2 * p, 0xC55D + 16 * p)
        at = pages._string_at(image, p)
        image[at : at + 16] = bytes(k and pages.FIRST_BYTE + k - 1 for k in tiles)
    return image


def _labels(image: bytes | bytearray) -> list[list[bytes]]:
    return [pages._tiles_of(image, p) for p in range(pages.PAGES)]


@pytest.mark.parametrize("count", [1, 18])
def test_fewer_than_19_clubs_label_the_nation_pages_nations(count):
    image = _cartridge()
    allstar = _labels(image)[6]
    pages.write(image, list(R.SLOT_ORDER[:count]), LEAGUE)
    held = -(-count // pages.PER_PAGE)
    league, nations = pages._drawn("BRASILEIRAO"), pages._drawn("NATIONS")
    assert _labels(image) == [league] * held + [nations] * (6 - held) + [allstar]


def test_19_clubs_or_more_leave_the_nation_pages_their_own_labels():
    image = _cartridge()
    before = _labels(image)
    pages.write(image, list(R.SLOT_ORDER[:20]), LEAGUE)
    assert _labels(image) == [pages._drawn("BRASILEIRAO")] * 4 + before[4:]


def test_a_page_the_league_no_longer_holds_loses_its_label():
    image = _cartridge()
    pages.write(image, list(R.SLOT_ORDER[:20]), LEAGUE)
    pages.write(image, list(R.SLOT_ORDER[:18]), LEAGUE)
    league, nations = pages._drawn("BRASILEIRAO"), pages._drawn("NATIONS")
    assert _labels(image)[:4] == [league] * 3 + [nations]


@pytest.mark.real_rom
@pytest.mark.parametrize("count", [1, 18])
def test_a_league_of_fewer_than_19_clubs_patches_the_cartridge(tmp_path, deluxe_rom, count):
    rom = deluxe_rom
    groups = ["Goalkeeper"] * 2 + ["Defender", "Midfielder", "Attacker"] * 6
    teams = [
        TeamRoster(
            team=Team(id=t, name=f"Club {t}"),
            players=[
                Player(id=100 * t + i, name=f"P{i}", position=g) for i, g in enumerate(groups)
            ],
        )
        for t in range(1, count + 1)
    ]
    data = LeagueData(league=League(id=2001, name=LEAGUE), teams=teams)
    patcher = get_patcher("iss-deluxe-snes")(tmp_path / "cache")
    slots = [SlotMapping(slot_index=R.SLOT_ORDER[t - 1], team_id=t) for t in range(1, count + 1)]
    rosters = patcher.map_rosters(data, slots)
    out = tmp_path / "out.sfc"
    patcher.patch(rom_path=rom, output_path=out, rosters=rosters, looks=False, logos=False)
    labels = _labels(out.read_bytes())
    assert labels[0] == pages._drawn("BRASILEIRAO") and labels[5] == pages._drawn("NATIONS")
