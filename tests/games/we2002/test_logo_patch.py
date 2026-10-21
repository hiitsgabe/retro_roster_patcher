"""The logo path through `WE2002Patcher.patch` and `RomWriter`.

`patch` is driven with a recording writer, as the rest of this package's tests
do, so what is checked is the contract the atlas code is handed: which style and
palette each record carries, which logos are queued, and when the flush runs.
"""

import io
import os
import struct

import pytest

from retro_roster_patcher.core.models import SlotMapping
from retro_roster_patcher.games.we2002 import patcher as patcher_module
from retro_roster_patcher.games.we2002.flag_atlas import VALID_STYLES, AtlasReport, LogoImages
from retro_roster_patcher.games.we2002.models import WETeamRecord
from retro_roster_patcher.games.we2002.patcher import WE2002Patcher
from retro_roster_patcher.games.we2002.rom_writer import (
    _ML_COLOR_OFFSETS,
    _NAT_COLOR_OFFSETS,
    _OFS_BANDIERE_FORMA1,
    _build_flag_data,
    _write_transparent_index,
)
from retro_roster_patcher.sports.models import League, LeagueData, Team, TeamRoster

_IMAGES = LogoImages(
    palette=[(10 * i, 0, 0) for i in range(15)],
    big=[[1] * 64] * 28,
    medium=[[2] * 24] * 16,
    mini=[[3] * 24] * 8,
)


class RecordingWriter:
    """Records every call; `flush_logo_patches` answers with a canned report."""

    def __init__(self, rom_path, output_path):
        self.output_path = output_path
        self.log = []
        with open(output_path, "wb") as f:
            f.write(b"")

    def write_team(self, slot_index, team, players=None, include_flag=True):
        self.log.append(("ml", slot_index, team.flag_style, team.flag_palette, team.logo_png))
        return len(players or [])

    def write_nat_team(self, nat_index, team, players=None, include_flag=True):
        self.log.append(("nat", nat_index, team.flag_style, team.flag_palette, team.logo_png))
        return len(players or [])

    def flush_tex_patches(self):
        self.log.append(("flush_tex",))

    def queue_logo(self, style, images):
        self.log.append(("queue", style, images))

    def flush_logo_patches(self):
        self.log.append(("flush_logos",))
        return AtlasReport(files=["DATSEL.BIN"])

    def finalize(self):
        self.log.append(("finalize",))


@pytest.fixture
def valid_rom(tmp_path):
    """A sparse image: big enough for `validate_rom`, raw-sector sync bytes, a
    style table full of 7s so style 7 is 'in use' by every untouched entry."""
    path = tmp_path / "rom.bin"
    with open(path, "wb") as f:
        f.truncate(101 * 1024 * 1024)
        f.write(b"\x00" + b"\xff" * 10 + b"\x00")
        f.seek(_OFS_BANDIERE_FORMA1)
        f.write(bytes([7] * 95))
    return path


@pytest.fixture
def writers(monkeypatch):
    made = []

    def factory(rom_path, output_path):
        w = RecordingWriter(rom_path, output_path)
        made.append(w)
        return w

    monkeypatch.setattr(patcher_module, "RomWriter", factory)
    monkeypatch.setattr(patcher_module, "ensure_ppf", lambda *a, **kw: "unused.ppf")
    monkeypatch.setattr(patcher_module, "apply_ppf", lambda *a, **kw: None)
    return made


def _patcher(tmp_path, transport=None, assets_dir=None):
    return WE2002Patcher(tmp_path / "cache", transport=transport, assets_dir=assets_dir)


def _rosters(patcher, slots, logo_url="https://a.espncdn.com/{id}.png"):
    teams = [
        TeamRoster(
            team=Team(
                id=100 + i, name=f"Team {i}", code=f"T{i}", logo_url=logo_url.format(id=100 + i)
            )
        )
        for i in slots
    ]
    data = LeagueData(league=League(id=1, name="L"), teams=teams)
    mapping = [SlotMapping(slot_index=s, team_id=100 + s, team_name=f"Team {s}") for s in slots]
    return patcher.map_rosters(data, mapping)


def _png():
    pytest.importorskip("PIL")
    from PIL import Image

    im = Image.new("RGBA", (20, 20), (200, 30, 30, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_map_rosters_carries_the_team_id_and_logo_url(tmp_path):
    rosters = _rosters(_patcher(tmp_path), [0, 1])
    assert rosters.teams[0].team_id == 100 and rosters.teams[0].logo_url.endswith("100.png")


def test_patch_gives_each_logo_team_a_free_style_and_queues_it(tmp_path, valid_rom, writers):
    png = _png()
    calls = []

    def transport(url, headers, timeout):
        calls.append(url)
        return png

    patcher = _patcher(tmp_path, transport=transport)
    rosters = _rosters(patcher, [0, 1])
    patcher.patch(rom_path=valid_rom, output_path=tmp_path / "out.bin", rosters=rosters)
    log = writers[0].log
    queued = [e for e in log if e[0] == "queue"]
    assert [e[1] for e in queued] == [0, 1]  # 7 is taken by every untouched entry
    assert all(e[1] in VALID_STYLES for e in queued)
    for kind in ("ml", "nat"):
        writes = [e for e in log if e[0] == kind]
        assert [e[2] for e in writes] == [0, 1]
        assert all(len(e[3]) == 16 and e[3][15] == (0, 0, 0) for e in writes)
        assert all(e[4] == png for e in writes)
    assert log.index(("flush_tex",)) < log.index(("flush_logos",)) < log.index(("finalize",))
    assert len(calls) == 2  # one download per team
    # The second patch reads the cache and downloads nothing.
    patcher.patch(rom_path=valid_rom, output_path=tmp_path / "out2.bin", rosters=rosters)
    assert len(calls) == 2


def test_an_override_in_assets_dir_wins_over_the_download(tmp_path, valid_rom, writers):
    png = _png()
    (tmp_path / "assets" / "logos").mkdir(parents=True)
    (tmp_path / "assets" / "logos" / "100.png").write_bytes(png)

    def transport(url, headers, timeout):
        raise AssertionError("must not download")

    patcher = _patcher(tmp_path, transport=transport, assets_dir=tmp_path / "assets")
    rosters = _rosters(patcher, [0], logo_url="")
    patcher.patch(rom_path=valid_rom, output_path=tmp_path / "out.bin", rosters=rosters)
    assert any(e[0] == "queue" for e in writers[0].log)


def test_a_failed_download_costs_that_team_its_logo_only(tmp_path, valid_rom, writers):
    png = _png()

    def transport(url, headers, timeout):
        if "101" in url:
            raise OSError("boom")
        return png

    status = []
    patcher = _patcher(tmp_path, transport=transport)
    patcher.on_status = status.append
    rosters = _rosters(patcher, [0, 1], logo_url="https://a/{id}.png")
    patcher.patch(rom_path=valid_rom, output_path=tmp_path / "out.bin", rosters=rosters)
    queued = [e for e in writers[0].log if e[0] == "queue"]
    assert len(queued) == 1
    assert rosters.teams[1].logo_png is None and rosters.teams[1].flag_style is None
    assert any("Team 1: logo skipped" in line for line in status)


def test_master_league_slots_without_a_palette_keep_the_striped_flag(tmp_path, valid_rom, writers):
    patcher = _patcher(tmp_path, transport=lambda *a: _png())
    status = []
    patcher.on_status = status.append
    assert 22 not in _ML_COLOR_OFFSETS and 5 in _ML_COLOR_OFFSETS
    rosters = _rosters(patcher, [22])
    patcher.patch(rom_path=valid_rom, output_path=tmp_path / "out.bin", rosters=rosters)
    assert not any(e[0] == "queue" for e in writers[0].log)
    assert any("slot 22 has no palette of its own" in line for line in status)


def test_without_pillow_logos_are_skipped_with_one_status_line(
    tmp_path, valid_rom, writers, monkeypatch
):
    def no_pillow(png):
        raise ImportError("No module named PIL")

    monkeypatch.setattr(patcher_module, "render_logo", no_pillow)
    status = []
    patcher = _patcher(tmp_path, transport=lambda *a: b"png")
    patcher.on_status = status.append
    rosters = _rosters(patcher, [0, 1])
    result = patcher.patch(rom_path=valid_rom, output_path=tmp_path / "out.bin", rosters=rosters)
    assert result.teams_patched == 2
    assert not any(e[0] in ("queue", "flush_logos") for e in writers[0].log)
    assert status.count("Pillow is not installed; team logos skipped") == 1


def test_logos_can_be_switched_off(tmp_path, valid_rom, writers):
    patcher = _patcher(tmp_path, transport=lambda *a: _png())
    rosters = _rosters(patcher, [0])
    patcher.patch(
        rom_path=valid_rom, output_path=tmp_path / "out.bin", rosters=rosters, logos=False
    )
    assert not any(e[0] in ("queue", "flush_logos") for e in writers[0].log)


def test_an_atlas_failure_is_reported_and_the_roster_patch_still_succeeds(
    tmp_path, valid_rom, writers, monkeypatch
):
    from retro_roster_patcher.core.errors import RomError

    def failing_flush(self):
        raise RomError("no DATSEL.BIN")

    monkeypatch.setattr(RecordingWriter, "flush_logo_patches", failing_flush)
    status = []
    patcher = _patcher(tmp_path, transport=lambda *a: _png())
    patcher.on_status = status.append
    rosters = _rosters(patcher, [0])
    result = patcher.patch(rom_path=valid_rom, output_path=tmp_path / "out.bin", rosters=rosters)
    assert result.teams_patched == 1
    assert any("Team logos skipped: no DATSEL.BIN" in line for line in status)


def test_blank_unused_empties_every_unmapped_slot_in_both_tables(tmp_path, valid_rom, writers):
    patcher = _patcher(tmp_path, transport=lambda *a: _png())
    rosters = _rosters(patcher, [0, 1])
    patcher.patch(
        rom_path=valid_rom, output_path=tmp_path / "out.bin", rosters=rosters, blank_unused=True
    )
    log = writers[0].log
    ml = {e[1]: e for e in log if e[0] == "ml"}
    nat = {e[1]: e for e in log if e[0] == "nat"}
    assert sorted(ml) == list(range(32)) and sorted(nat) == list(range(63))
    logo_styles = {ml[0][2], ml[1][2]}
    blank_style = ml[2][2]
    assert blank_style not in logo_styles and blank_style != 1
    for slot, entry in ml.items():
        if slot >= 2:
            assert entry[2] == blank_style and entry[3] == [(0, 0, 0)] * 16 and entry[4] == b""
    for slot, entry in nat.items():
        if slot >= 2:
            assert entry[2] == blank_style
    queued = {e[1]: e[2] for e in log if e[0] == "queue"}
    assert set(queued) == logo_styles | {blank_style}
    assert all(v == 0 for row in queued[blank_style].big for v in row)
    assert ("flush_logos",) in log


def test_blank_unused_without_logos_still_queues_the_blank_style(tmp_path, valid_rom, writers):
    patcher = _patcher(tmp_path)
    rosters = _rosters(patcher, [0], logo_url="")
    patcher.patch(
        rom_path=valid_rom, output_path=tmp_path / "out.bin", rosters=rosters, blank_unused=True
    )
    log = writers[0].log
    queued = [e for e in log if e[0] == "queue"]
    assert len(queued) == 1 and queued[0][1] != 1
    assert [e for e in log if e[0] == "nat" and e[1] == 0][0][2] is None  # the mapped team: default
    assert ("flush_logos",) in log


def test_without_blank_unused_the_other_slots_are_not_touched(tmp_path, valid_rom, writers):
    patcher = _patcher(tmp_path, transport=lambda *a: _png())
    rosters = _rosters(patcher, [0])
    patcher.patch(rom_path=valid_rom, output_path=tmp_path / "out.bin", rosters=rosters)
    assert {e[1] for e in writers[0].log if e[0] in ("ml", "nat")} == {0}


# --- the writer's side ------------------------------------------------------


def test_a_logo_palette_is_opaque_with_a_transparent_last_entry():
    team = WETeamRecord(name="X", short_name="X", flag_style=3, logo_png=b"png")
    team.flag_palette = [(0, 0, 0)] * 15 + [(0, 0, 0)]
    style, colours = _build_flag_data(team)
    values = struct.unpack("<16H", colours)
    assert style == 3
    assert all(v == 0x8000 for v in values[:15])  # black stays opaque
    assert values[15] == 0


def test_a_striped_palette_is_unchanged_by_the_logo_rule():
    team = WETeamRecord(name="X", short_name="X", kit_home=(255, 0, 0), kit_away=(0, 0, 255))
    _style, colours = _build_flag_data(team)
    values = struct.unpack("<16H", colours)
    assert not any(v & 0x8000 for v in values)


def test_the_transparent_index_is_written_one_halfword_before_the_slot(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"\xaa" * 64)
    chunks = _NAT_COLOR_OFFSETS[0]
    base = chunks[0][0]
    with open(path, "r+b") as f:
        f.truncate(base + 64)
        f.seek(base - 2)
        f.write(b"\xbb\xbb")
        _write_transparent_index(f, WETeamRecord(name="X", short_name="X", logo_png=b"p"), chunks)
        f.seek(base - 2)
        assert f.read(2) == b"\x00\x00"
        # A team without a logo leaves the halfword alone.
        f.seek(base - 2)
        f.write(b"\xbb\xbb")
        _write_transparent_index(f, WETeamRecord(name="X", short_name="X"), chunks)
        f.seek(base - 2)
        assert f.read(2) == b"\xbb\xbb"
    assert os.path.getsize(path) == base + 64
