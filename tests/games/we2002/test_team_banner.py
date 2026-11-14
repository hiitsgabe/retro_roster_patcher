"""The VS banner's name pictures (`team_banner`) and the team-select grid's short names."""

import pytest

from retro_roster_patcher.games.we2002 import team_banner
from retro_roster_patcher.games.we2002.models import WETeamRecord
from retro_roster_patcher.games.we2002.patcher import _banner_text
from retro_roster_patcher.games.we2002.rom_writer import _kanji_text

pytest.importorskip("PIL")


def test_a_name_is_drawn_in_the_top_rows_of_its_band_with_a_shadow():
    grid = team_banner.render_name("LIVERPOOL")

    assert len(grid) == team_banner.ROWS and all(len(row) == team_banner.WIDTH for row in grid)
    used = {v for row in grid for v in row}
    assert used == {0, team_banner._TEXT, team_banner._SHADOW}
    # starts at the band's left like the disc's names, and stays inside its width
    assert min(x for row in grid for x, v in enumerate(row) if v) <= 4
    assert not any(
        row[team_banner._FIT :] != [0] * (team_banner.WIDTH - team_banner._FIT) for row in grid
    )


def test_a_long_name_is_squeezed_into_the_band():
    grid = team_banner.render_name("NOTTINGHAM FOREST AND MORE")

    assert max(x for row in grid for x, v in enumerate(row) if v) < team_banner._FIT


def test_a_band_is_packed_two_pixels_a_byte_low_nibble_first():
    block = bytes(64 * 128)
    grid = [[0] * 128 for _ in range(team_banner.ROWS)]
    grid[0][0], grid[0][1] = 15, 3

    out = team_banner._band(block, 2, grid)

    start = 2 * team_banner.BAND * 64
    assert out[start] == 0x3F
    assert out[:start] == block[:start] and out[start + 1 :] == block[start + 1 :]


def test_an_empty_name_clears_the_band():
    block = bytes([0x11]) * (64 * 128)

    out = team_banner._band(block, 1, None)

    band = slice(team_banner.BAND * 64, 2 * team_banner.BAND * 64)
    assert out[band] == bytes(team_banner.BAND * 64)
    assert out[: band.start] == block[: band.start]


def test_the_banner_shows_the_full_name_unless_it_is_too_long():
    assert _banner_text(WETeamRecord("AFC Bournemouth", "BOU", banner_name="AFC Bournemouth")) == (
        "AFC BOURNEMOUTH"
    )
    long = WETeamRecord(
        "Brighton", "BHA", kanji_name="Brighton", banner_name="Brighton & Hove Albion"
    )
    assert _banner_text(long) == "BRIGHTON"


def test_the_grid_takes_the_name_then_the_short_name_then_the_code():
    # a budget of 6 shows 5 characters
    assert _kanji_text(WETeamRecord("Hull", "HUL", kanji_name="Hull"), 6) == "Hull"
    assert _kanji_text(WETeamRecord("Tottenham Hotspur", "TOT", kanji_name="Spurs"), 6) == "Spurs"
    assert _kanji_text(WETeamRecord("Liverpool", "LIV", kanji_name="Liverpool"), 6) == "LIV"


def test_on_the_disc_a_slot_s_name_lands_in_its_own_band(disc_path):
    edits = team_banner.banner_edits(disc_path, {13: "LIVERPOOL", 76: "LIVERPOOL"})

    # team 13 is the 4th record's 2nd band; club 13 (team 76) the 20th record's 1st
    parsed, _ = team_banner.read_data_file(disc_path, team_banner.FILE)
    records = [r for r in parsed.records() if r.kind & 0xFF == team_banner.KIND_IMAGE]
    assert {e.at for e in edits} == {(records[3].x, records[3].y), (records[19].x, records[19].y)}
