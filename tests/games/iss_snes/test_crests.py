"""Crests as flags: `logo.logo_flag`, and how `patch` gets, draws and falls back.

Every flag is read back by decoding the output image through its pointer
table (`test_rom_writer._pixels`), never from what the writer was handed.
"""

from __future__ import annotations

import io
import random
import struct
import sys

import pytest

from retro_roster_patcher.core.models import MappedRosters
from retro_roster_patcher.games.iss_snes import ISSPatcher
from retro_roster_patcher.games.iss_snes.logo import logo_flag
from retro_roster_patcher.games.iss_snes.models import ISSPlayerRecord, ISSTeamRecord
from retro_roster_patcher.games.iss_snes.rom_writer import _kit_offsets
from tests.fixtures import synthetic_iss_rom as fixture
from tests.games.iss_snes.test_rom_writer import (
    _as_shown,
    _blobs,
    _entry12,
    _pixels,
    _rgb,
    _shown,
    _stands_out,
)

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

RED = (255, 0, 0)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
BLUE = (0, 0, 255)
YELLOW = (255, 255, 0)
GOLD = (254, 215, 12)
#: Entry 12 is the shirt-number colour; the bands take the two entries after
#: it that compress smallest.
TWO_BAND = [[13] * 24] * 8 + [[15] * 24] * 8


def _png(size=(64, 64), fill=(*RED, 255), box=None, box_fill=None) -> bytes:
    image = Image.new("RGBA", size, fill)
    if box is not None:
        ImageDraw.Draw(image).rectangle(box, fill=box_fill)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _noise_png(seed) -> bytes:
    """A 24x16 field of four colours in 10x10 blocks: one block per flag pixel."""
    rng = random.Random(seed)
    colours = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]
    image = Image.new("RGB", (24, 16))
    for y in range(16):
        for x in range(24):
            image.putpixel((x, y), rng.choice(colours))
    out = io.BytesIO()
    image.resize((240, 160), Image.Resampling.NEAREST).save(out, "PNG")
    return out.getvalue()


def test_a_square_crest_fills_the_height_and_centres_on_white():
    flag = logo_flag(_png())
    red = flag.pixels[0][12]
    assert flag.colours[red] == RED
    for row in flag.pixels:
        assert row[4:20] == [red] * 16
        assert flag.colours[row[0]] == WHITE and flag.colours[row[23]] == WHITE


def test_transparent_margins_are_cropped_away():
    crest = _png((100, 100), (0, 0, 0, 0), box=(70, 70, 89, 89), box_fill=(*RED, 255))
    assert logo_flag(crest).pixels == logo_flag(_png()).pixels


def test_a_crest_comes_out_in_at_most_four_colours():
    rainbow = Image.new("RGB", (60, 10))
    for x in range(60):
        rainbow.paste((x * 4, 255 - x * 4, (x * 37) % 256), (x, 0, x + 1, 10))
    out = io.BytesIO()
    rainbow.save(out, "PNG")
    flag = logo_flag(out.getvalue())
    assert len(flag.colours) == 4
    assert {v for row in flag.pixels for v in row} <= {0, 1, 2, 3}
    assert len(flag.pixels) == 16 and all(len(row) == 24 for row in flag.pixels)


def _six(image, slot):
    """Every shade of `slot`'s home and away shirts, as `image` holds them."""
    return [
        _rgb(word) for at in _kit_offsets(slot) for word in struct.unpack("<3H", image[at : at + 6])
    ]


def _record(slot, **extra):
    record = ISSTeamRecord(
        name=f"Club {slot}",
        short_name=f"C{slot:02d}",
        players=[ISSPlayerRecord(name=f"P{i:02d}", shirt_number=i + 1) for i in range(15)],
        flag_colors=[BLUE, YELLOW],
        kit_home=(BLUE, WHITE, BLUE),
        kit_away=(YELLOW, WHITE, YELLOW),
        team_id=100 + slot,
        logo_url=f"https://example.invalid/{slot}.png",
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def _rosters(*slots, **extra):
    return MappedRosters(game_id="iss-snes", teams={s: _record(s, **extra) for s in slots})


class Transport:
    def __init__(self, body=None, fail=False):
        self.calls = []
        self.body = body if body is not None else _png()
        self.fail = fail

    def __call__(self, url, headers, timeout):
        self.calls.append(url)
        if self.fail:
            raise OSError("no route to host")
        return self.body


@pytest.fixture
def rom(tmp_path):
    return fixture.write_iss_rom(tmp_path / "iss.sfc")


@pytest.fixture
def out(tmp_path):
    return tmp_path / "patched.sfc"


def _patcher(tmp_path, transport=None, messages=None, **kwargs):
    return ISSPatcher(
        cache_dir=tmp_path / "cache",
        transport=transport or Transport(),
        on_status=None if messages is None else messages.append,
        **kwargs,
    )


def test_the_patcher_says_it_draws_crests():
    assert ISSPatcher.supports_logos is True


def test_a_crest_is_drawn_from_the_logo_url(tmp_path, rom, out):
    transport = Transport()
    _patcher(tmp_path, transport).patch(rom_path=rom, output_path=out, rosters=_rosters(0))
    assert transport.calls == ["https://example.invalid/0.png"]
    assert _shown(out.read_bytes(), 0) == _as_shown(logo_flag(_png()))


def test_a_downloaded_logo_is_cached(tmp_path, rom, out):
    transport = Transport()
    patcher = _patcher(tmp_path, transport)
    patcher.patch(rom_path=rom, output_path=out, rosters=_rosters(0))
    patcher.patch(rom_path=rom, output_path=out, rosters=_rosters(0))
    assert len(transport.calls) == 1


def test_logos_false_keeps_the_two_band_flag(tmp_path, rom, out):
    transport = Transport()
    patcher = _patcher(tmp_path, transport)
    patcher.patch(rom_path=rom, output_path=out, rosters=_rosters(0), logos=False)
    assert transport.calls == []
    assert _pixels(out.read_bytes(), 0) == TWO_BAND


def test_an_assets_dir_logo_wins_over_the_download(tmp_path, rom, out):
    assets = tmp_path / "assets"
    (assets / "logos").mkdir(parents=True)
    green = _png(fill=(0, 255, 0, 255))
    (assets / "logos" / "100.png").write_bytes(green)
    transport = Transport(fail=True)
    _patcher(tmp_path, transport, assets_dir=assets).patch(
        rom_path=rom, output_path=out, rosters=_rosters(0)
    )
    assert transport.calls == []
    assert _shown(out.read_bytes(), 0) == _as_shown(logo_flag(green))


def test_a_team_without_a_logo_gets_the_two_band_flag(tmp_path, rom, out):
    transport = Transport()
    rosters = _rosters(0, logo_url="")
    _patcher(tmp_path, transport).patch(rom_path=rom, output_path=out, rosters=rosters)
    assert transport.calls == []
    assert _pixels(out.read_bytes(), 0) == TWO_BAND


def test_a_logo_that_will_not_download_costs_the_crest_only(tmp_path, rom, out):
    messages = []
    patcher = _patcher(tmp_path, Transport(fail=True), messages)
    result = patcher.patch(rom_path=rom, output_path=out, rosters=_rosters(0))
    assert result.players_patched == 15
    assert _pixels(out.read_bytes(), 0) == TWO_BAND
    assert any(m.startswith("Club 0: crest skipped") for m in messages)


def test_without_pillow_every_crest_is_skipped_with_one_line(tmp_path, rom, out, monkeypatch):
    transport = Transport()
    monkeypatch.setitem(sys.modules, "PIL", None)
    messages = []
    _patcher(tmp_path, transport, messages).patch(
        rom_path=rom, output_path=out, rosters=_rosters(0, 1)
    )
    assert messages.count("Pillow is not installed; team crests skipped") == 1
    assert not any("crest skipped" in m for m in messages)
    assert _pixels(out.read_bytes(), 0) == TWO_BAND


def test_a_team_with_neither_colours_nor_logo_keeps_the_cartridge_flag(tmp_path, rom, out):
    rosters = _rosters(0, logo_url="", flag_colors=[], kit_home=(), kit_away=())
    _patcher(tmp_path).patch(rom_path=rom, output_path=out, rosters=rosters)
    image, before = out.read_bytes(), rom.read_bytes()
    assert _blobs(image, 0) == [fixture.flag_half_blob(0, h) for h in (0, 1)]
    record = slice(fixture.OFS_FLAG_COLORS_RANGE1, fixture.OFS_FLAG_COLORS_RANGE1 + 8)
    assert image[record] == before[record]


def test_a_new_kit_redraws_the_cartridge_flag_around_the_numbers(tmp_path, rom, out):
    """No primary colour, so no home kit and no flag, but a new away kit: the
    cartridge's flag stays, with entry 12 set against the shirts."""
    rosters = _rosters(0, logo_url="", flag_colors=[], kit_home=(), kit_away=(BLACK, WHITE, BLACK))
    _patcher(tmp_path).patch(rom_path=rom, output_path=out, rosters=rosters)
    image = out.read_bytes()
    assert _stands_out(_entry12(image, 0), _six(image, 0))
    assert _shown(image, 0) == _shown(rom.read_bytes(), 0)


@pytest.mark.parametrize("path", ["crest", "two-band", "logos=False"])
@pytest.mark.parametrize(
    "home, away", [(WHITE, BLACK), (BLACK, WHITE), (WHITE, RED), (RED, RED), (BLUE, YELLOW)]
)
def test_the_shirt_numbers_stand_out_from_both_kits(tmp_path, rom, out, path, home, away):
    """Entry 12 colours the numbers on the home and the away shirt alike, so
    it stands out from every shade the game draws either shirt in."""
    extra = {"logo_url": ""} if path == "two-band" else {}
    rosters = _rosters(
        0,
        kit_home=(home, WHITE, home),
        kit_away=(away, WHITE, away),
        flag_colors=[home, away],
        **extra,
    )
    _patcher(tmp_path).patch(
        rom_path=rom, output_path=out, rosters=rosters, logos=path != "logos=False"
    )
    image = out.read_bytes()
    assert _stands_out(_entry12(image, 0), _six(image, 0))
    if path == "crest":
        # A red square on white keeps both its colours whatever the numbers take.
        shown = {v for row in _shown(image, 0) for v in row}
        assert shown == {v for row in _as_shown(logo_flag(_png())) for v in row}
    else:
        assert _pixels(image, 0) == TWO_BAND


@pytest.mark.parametrize(
    "crest", [(BLACK, WHITE), (BLACK, GOLD)], ids=["Botafogo-like", "Atletico-MG-like"]
)
def test_a_black_and_white_team_gets_numbers_that_read_on_both_shirts(tmp_path, rom, out, crest):
    """Black home, white away, as ESPN has Botafogo and Atletico-MG. Mid-grey
    vanished on the black shirt; gold is far from white in RGB, but nearly all
    of it in blue, and reads poorly on it. Red reads on every shade, and the
    crest keeps its colours."""
    png = _png(fill=(*crest[0], 255), box=(16, 16, 47, 47), box_fill=(*crest[1], 255))
    assets = tmp_path / "assets"
    (assets / "logos").mkdir(parents=True)
    (assets / "logos" / "100.png").write_bytes(png)
    near_white = (250, 250, 252)
    rosters = _rosters(0, kit_home=(BLACK, BLACK, BLACK), kit_away=(near_white, BLACK, near_white))
    _patcher(tmp_path, Transport(fail=True), assets_dir=assets).patch(
        rom_path=rom, output_path=out, rosters=rosters
    )
    image = out.read_bytes()
    assert _entry12(image, 0) == RED
    assert _stands_out(RED, _six(image, 0))
    shown = {v for row in _shown(image, 0) for v in row}
    assert shown == {v for row in _as_shown(logo_flag(png)) for v in row}


def test_crests_that_do_not_fit_are_reported(tmp_path, rom, out):
    assets = tmp_path / "assets"
    (assets / "logos").mkdir(parents=True)
    for slot in range(27):
        (assets / "logos" / f"{100 + slot}.png").write_bytes(_noise_png(slot))
    messages = []
    rosters = _rosters(*range(27))
    _patcher(tmp_path, Transport(fail=True), messages, assets_dir=assets).patch(
        rom_path=rom, output_path=out, rosters=rosters
    )
    fell_back = [m for m in messages if "crest did not fit" in m]
    assert any("crest simplified to" in m for m in messages)
    # Counted once the room is settled: only the crests that reached the ROM.
    assert messages[-2] == f"Team crests drawn: {27 - len(fell_back)}"
    assert all(messages.index(m) < len(messages) - 2 for m in fell_back)


def test_a_second_patch_keeps_the_first_ones_crest(tmp_path, rom):
    first, second = tmp_path / "first.sfc", tmp_path / "second.sfc"
    patcher = _patcher(tmp_path)
    patcher.patch(rom_path=rom, output_path=first, rosters=_rosters(0))
    patcher.patch(rom_path=first, output_path=second, rosters=_rosters(1))
    assert _pixels(second.read_bytes(), 0) == _pixels(first.read_bytes(), 0)
    assert _pixels(second.read_bytes(), 1) == _pixels(first.read_bytes(), 0)


def test_map_rosters_carries_the_logo_source(tmp_path):
    from tests.games.iss_snes.test_patcher import FakeApi

    patcher = _patcher(tmp_path)
    patcher.api = FakeApi()
    data = patcher.fetch(season=2025, league_id=2001)
    data.teams[0].team.logo_url = "https://example.invalid/crest.png"
    mapped = patcher.map_rosters(data, patcher.default_slot_mapping(data))
    assert mapped.teams[0].team_id == data.teams[0].team.id
    assert mapped.teams[0].logo_url == "https://example.invalid/crest.png"
