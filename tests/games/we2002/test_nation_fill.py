"""The nation fill (#5): today's top national teams in the national slots a league leaves.

A nation goes into the national table only (the Master League club of the slot's number is left
alone), wears its own flag from the disc (`nations`, packaged) wherever it lands, and keeps its
cartridge look untouched when it is on its own slot and nothing has rewritten it. The real-disc
tests read RETRO_ROSTER_TEST_ROMS/we2002.bin (Track 1).
"""

from __future__ import annotations

import io
import os
import unicodedata
from pathlib import Path

import pytest

from retro_roster_patcher.core.models import MappedRosters, SlotMapping
from retro_roster_patcher.games.we2002 import nations
from retro_roster_patcher.games.we2002 import patcher as patcher_module
from retro_roster_patcher.games.we2002 import rom_writer as W
from retro_roster_patcher.games.we2002.flag_atlas import read_styles
from retro_roster_patcher.games.we2002.models import WEPlayerRecord, WETeamRecord
from retro_roster_patcher.games.we2002.patcher import WE2002Patcher
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster
from retro_roster_patcher.sports.nationals import NATIONAL_LEAGUE


def _nation(team_id, name, code, players=23):
    squad = [
        Player(id=team_id * 100 + i, name=f"P{i}", last_name=f"N{team_id}x{i}", position=pos)
        for i, pos in enumerate(
            ["Goalkeeper"] + ["Defender"] * 8 + ["Midfielder"] * 8 + ["Forward"] * 6
        )
    ][:players]
    return TeamRoster(team=Team(id=team_id, name=name, code=code), players=squad)


def _nations(*rosters):
    return LeagueData(league=League(id=NATIONAL_LEAGUE, name="Friendlies"), teams=list(rosters))


def test_the_fill_takes_the_54_nations_and_keeps_four_slots_for_their_own():
    """36, 39 and 47 have no colour entry the writer knows, and 35's is Master League club 5's:
    a moved nation's flag would draw wrong there, so only their own nation goes home to them."""
    patcher = WE2002Patcher(cache_dir="unused")
    assert patcher.supports_nation_fill
    assert nations.NO_COLOURS == (36, 39, 47)
    assert W._NAT_COLOR_OFFSETS[35] == W._ML_COLOR_OFFSETS[5]
    assert patcher.fill_slots(Path("unused")) == list(range(54))
    assert patcher.home_only_slots(Path("unused")) == [35, 36, 39, 47]


def test_a_moved_nation_on_a_home_only_slot_is_skipped_and_its_own_nation_kept(tmp_path):
    lines = []
    patcher = WE2002Patcher(cache_dir=tmp_path, on_status=lines.append)
    data = _nations(
        _nation(1, "Spain", "ESP"), _nation(2, "Senegal", "SEN"), _nation(3, "South Africa", "RSA")
    )
    mapped = patcher.map_rosters(
        data, [SlotMapping(35, 1, "Spain"), SlotMapping(36, 2, "SEN"), SlotMapping(39, 3, "RSA")]
    )
    assert sorted(mapped.teams) == [36]
    assert any("Spain" in line and "35" in line for line in lines)
    assert any("South Africa" in line and "39" in line for line in lines)
    home = patcher.map_rosters(
        _nations(_nation(3, "South Africa", "RSA")), [SlotMapping(35, 3, "")]
    )
    assert sorted(home.teams) == [35]


@pytest.mark.parametrize(
    ("name", "slot"),
    [("Denmark", 13), ("Republic of Ireland", 0), ("Türkiye", 27), ("United States", 37),
     ("South Korea", 49), ("Czechia", 11), ("Serbia", 23), ("Australia", 53), ("Canada", None)],
)  # fmt: skip
def test_a_nation_s_own_slot_is_its_name_on_the_disc(name, slot):
    patcher = WE2002Patcher(cache_dir="unused")
    assert patcher.own_national_slot(Path("unused"), Team(id=1, name=name)) == slot


def test_a_nation_is_mapped_national_only_with_a_name_its_slot_can_show(tmp_path):
    lines = []
    patcher = WE2002Patcher(cache_dir=tmp_path, on_status=lines.append)
    data = _nations(
        _nation(1, "Spain", "ESP"),
        _nation(2, "Netherlands", "NED"),
        _nation(3, "Saudi Arabia", "KSA", players=0),
        _nation(4, "Canada", "CAN"),
    )
    mapped = patcher.map_rosters(
        data,
        [SlotMapping(5, 1, "Spain"), SlotMapping(20, 2, "Netherlands"), SlotMapping(21, 3, "KSA"),
         SlotMapping(46, 4, "Canada")],
    )  # fmt: skip
    assert sorted(mapped.teams) == [5, 20, 46]
    spain, dutch, canada = mapped.teams[5], mapped.teams[20], mapped.teams[46]
    assert spain.national and dutch.national and canada.national
    assert (spain.nation, dutch.nation, canada.nation) == (5, 8, None)
    # The grid shows 5 characters at slots 5 and 20, 7 at slot 46.
    assert (spain.name, dutch.name, canada.name) == ("Spain", "NED", "Canada")
    assert dutch.short_name == "NED"
    # The grid's kanji table titlecases a name, but not a code.
    assert W._encode_kanji_name("NED", 6)[:6] == "ＮＥＤ".encode("shift_jis")
    assert any("Saudi Arabia" in line for line in lines)


def test_a_club_mapping_is_not_national(tmp_path):
    patcher = WE2002Patcher(cache_dir=tmp_path)
    data = LeagueData(league=League(id=2001, name="PL"), teams=[_nation(7, "Liverpool", "LIV")])
    record = patcher.map_rosters(data, [SlotMapping(13, 7, "Liverpool")]).teams[13]
    assert not record.national and record.nation is None and record.name == "Liverpool"


def _recording_writer(calls):
    """A `RomWriter` stand-in that logs the calls `patch` makes and the records it hands over."""

    class Writer:
        write_kits = True

        def __init__(self, rom_path, output_path):
            Path(output_path).write_bytes(b"")

        def relink_clubs(self, rewritten, mapped):
            calls.append(("relink", rewritten, mapped))

        def write_team(self, slot, team, players=None, include_flag=True):
            calls.append(("ml", slot))

        def write_nat_team(self, slot, team, players=None, include_flag=True, kits=None):
            calls.append(("nat", slot, include_flag, kits))
            calls.append(("record", slot, team))
            return len(players or [])

        def write_nat_names(self, slot, team):
            calls.append(("names", slot, team.name))

        def flush_tex_patches(self):
            pass

        def queue_logo(self, style, images, stadium=None):
            calls.append(("queue", style))

        def flush_logo_patches(self):
            from retro_roster_patcher.games.we2002.flag_atlas import AtlasReport

            return AtlasReport()

        def finalize(self):
            pass

    return Writer


def _sparse_rom(tmp_path, styles=None):
    """A 100 MB sparse raw image; `styles` sets national style bytes ({slot: style})."""
    rom = tmp_path / "rom.bin"
    with rom.open("wb") as handle:
        handle.write(b"\x00" + b"\xff" * 10 + b"\x00")
        handle.truncate(100 * 1024 * 1024)
        for slot, style in (styles or {}).items():
            handle.seek(W._OFS_BANDIERE_FORMA1 + slot)
            handle.write(bytes([style]))
    return rom


def test_a_nation_below_32_leaves_the_master_league_club_of_its_number(tmp_path, monkeypatch):
    calls = []
    Writer = _recording_writer(calls)
    monkeypatch.setattr(patcher_module, "RomWriter", Writer)
    monkeypatch.setattr(patcher_module, "apply_ppf", lambda *args, **kwargs: "")
    rom = _sparse_rom(tmp_path)
    club = WETeamRecord(name="Club", short_name="CLB", players=[WEPlayerRecord("A", "B", 2, 1)])
    nation = WETeamRecord(name="DEN", short_name="DEN", national=True, nation=13, players=[])
    WE2002Patcher(cache_dir=tmp_path / "cache").patch(
        rom_path=rom,
        output_path=tmp_path / "out.bin",
        rosters=MappedRosters(game_id="we2002", teams={0: club, 20: nation}),
        kits=False,
        logos=False,
        looks=False,
    )
    assert ("relink", {0, 20}, {0}) in calls
    assert ("ml", 0) in calls and ("ml", 20) not in calls
    # Denmark away from home wears its disc flag: style 9, whose bitmaps are queued.
    assert ("nat", 20, True, True) in calls and ("queue", nations.flag(13).style) in calls


def test_a_home_nation_without_a_colour_entry_gets_back_its_style_byte_alone(tmp_path, monkeypatch):
    """Senegal home on 36 after an earlier patch changed the slot's style: its own style byte,
    never a logo or stripes through colours nobody can write."""
    calls = []
    monkeypatch.setattr(patcher_module, "RomWriter", _recording_writer(calls))
    monkeypatch.setattr(patcher_module, "apply_ppf", lambda *args, **kwargs: "")
    rom = _sparse_rom(tmp_path, {36: 41})
    senegal = WETeamRecord(
        name="SEN", short_name="SEN", national=True, nation=36, logo_url="http://x/sen.png"
    )
    WE2002Patcher(cache_dir=tmp_path / "cache").patch(
        rom_path=rom,
        output_path=tmp_path / "out.bin",
        rosters=MappedRosters(game_id="we2002", teams={36: senegal}),
        kits=False,
        looks=False,
    )
    record = next(c[2] for c in calls if c[:2] == ("record", 36))
    assert (record.flag_style, record.flag_clut) == (nations.flag(36).style, b"")
    assert ("nat", 36, True, True) in calls
    assert [c for c in calls if c[0] == "queue"] == [("queue", nations.flag(36).style)]


def test_the_style_byte_alone_leaves_every_colour_as_it_is(tmp_path):
    from retro_roster_patcher.games.we2002.rom_writer import RomWriter

    path = tmp_path / "noise.bin"
    path.write_bytes(bytes(range(256)) * 50_000)
    before = path.read_bytes()
    writer = RomWriter(str(path), str(path))
    for slot in (36, 20):
        writer.write_nat_team(
            slot, WETeamRecord(name="X", short_name="X", flag_style=17, flag_clut=b"")
        )
    after = path.read_bytes()
    for slot in (36, 20):
        assert after[W._OFS_BANDIERE_FORMA1 + slot] == 17
    colours = [c for chunks in W._NAT_COLOR_OFFSETS.values() for c in chunks]
    region = (min(o for o, _ in colours) - 2, max(o + n for o, n in colours))
    assert after[region[0] : region[1]] == before[region[0] : region[1]]


# --- the retail disc -----------------------------------------------------------------------


def _disc():
    root = os.environ.get("RETRO_ROSTER_TEST_ROMS")
    if not root:
        pytest.skip("set RETRO_ROSTER_TEST_ROMS to a directory holding we2002.bin")
    return Path(root) / "we2002.bin"


@pytest.mark.real_rom
def test_the_package_is_the_disc_s_flags():
    import json
    import zlib

    built = json.loads(zlib.decompress(nations.build(str(_disc()))))
    assert built == nations._doc()


def _png(rgb):
    from PIL import Image, ImageDraw

    im = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((5, 5, 95, 95), fill=(*rgb, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _clubs(count):
    return {
        slot: WETeamRecord(
            name=f"Club {slot}",
            short_name="CLB",
            team_id=900 + slot,
            players=[WEPlayerRecord(f"C{slot}x{i}", "F", 2, i + 1) for i in range(23)],
        )
        for slot in range(count)
    }


def _patch(src, out, teams, assets=None, logos=True):
    WE2002Patcher(cache_dir=out.parent / "cache", assets_dir=assets).patch(
        rom_path=src,
        output_path=out,
        rosters=MappedRosters(game_id="we2002", teams=teams),
        kits=False,
        logos=logos,
        looks=False,
    )


def _flag(image, slot):
    with open(image, "rb") as f:
        return nations.read_flag(f, slot)


def _read(image, chunks):
    with open(image, "rb") as f:
        out = b""
        for offset, size in chunks:
            f.seek(offset)
            out += f.read(size)
        return out


@pytest.mark.real_rom
def test_nations_wear_their_own_flags_over_a_plain_patch_and_a_repatch_changes_nothing(
    tmp_path, monkeypatch
):
    pytest.importorskip("PIL")
    monkeypatch.setattr(patcher_module, "apply_ppf", lambda *args, **kwargs: "")
    disc = _disc()
    assets = tmp_path / "assets"
    (assets / "logos").mkdir(parents=True)
    for slot in range(20):
        (assets / "logos" / f"{900 + slot}.png").write_bytes(_png((40 + 10 * slot, 0, 200)))

    # A plain league patch first: slots 0-19 get club logos, which take the styles nobody
    # untouched draws (Scotland's style 2 among them).
    plain = tmp_path / "plain.bin"
    _patch(disc, plain, _clubs(20), assets)
    assert _flag(plain, 1) != nations.flag(1)
    assert read_styles(str(plain), [2])[2].big != nations.bitmaps(2).big

    # Then 12 clubs and the fill. Scotland, back on its own slot 1, is no longer untouched;
    # Spain's slot 5 is a club's, so it moves to 20; Croatia is home and untouched; Canada, which
    # the disc lacks, draws its ESPN flag.
    patcher = WE2002Patcher(cache_dir=tmp_path / "cache", assets_dir=assets)
    clubs = {s: r for s, r in _clubs(12).items() if s != 1}
    data = _nations(
        _nation(1, "Scotland", "SCO"),
        _nation(2, "Spain", "ESP"),
        _nation(3, "Croatia", "CRO"),
        _nation(4, "Canada", "CAN"),
    )
    (assets / "logos" / "4.png").write_bytes(_png((255, 0, 0)))
    mapping = [SlotMapping(1, 1, "Scotland"), SlotMapping(20, 2, "Spain"),
               SlotMapping(22, 3, "Croatia"), SlotMapping(21, 4, "Canada")]  # fmt: skip
    mapped = MappedRosters(game_id="we2002", teams=clubs).merge(patcher.map_rosters(data, mapping))
    filled = tmp_path / "filled.bin"
    patcher.patch(rom_path=plain, output_path=filled, rosters=mapped, kits=False, looks=False)

    for slot, nation in ((1, 1), (20, 5), (22, 22)):
        assert _flag(filled, slot) == nations.flag(nation), slot
    assert read_styles(str(filled), [2])[2] == nations.bitmaps(2)
    assert read_styles(str(filled), [nations.flag(5).style])[nations.flag(5).style] == (
        nations.bitmaps(nations.flag(5).style)
    )
    # The match (stadium) copies too: an earlier logo patch left its crests on white there, which
    # a copy matched by DATSEL's content never found.
    from retro_roster_patcher.games.we2002.logo_render import render_logos

    spain = nations.flag(5).style
    for gdc in ("GDC_AD.BIN", "GDC_FN.BIN"):
        on_pitch = read_styles(str(filled), [spain, 2], gdc)
        assert (on_pitch[spain].big, on_pitch[spain].mini) == (
            nations.bitmaps(spain).big,
            nations.bitmaps(spain).mini,
        ), gdc
        assert on_pitch[2].big == nations.bitmaps(2).big, gdc
        club = _flag(filled, 0).style
        stadium = render_logos(_png((40, 0, 200)))[1]
        drawn = read_styles(str(filled), [club], gdc)[club]
        assert (drawn.big, drawn.mini) == (stadium.big, stadium.mini), gdc
    # Croatia, untouched at home, keeps its disc flag but gets its name: the translation the
    # patch applies renames some nations' slots (Iran's to "India").
    assert _read(filled, W._nat_kanji_chunks(22)).startswith("ＣＲＯ".encode("shift_jis"))
    # Spain at 20 is national only: Master League club 20 (LOMBARDIA) keeps its name.
    assert _read(filled, W._nat_kanji_chunks(20)) != _read(disc, W._nat_kanji_chunks(20))
    lombardia = [(W._ml_kanji_offset(20), W._LUN_NOMIK[63 + 20] * 2)]
    assert _read(filled, lombardia) == _read(disc, lombardia)
    # Canada drew its flag from the ESPN picture, through a style no nation here wears.
    assert _flag(filled, 21).style not in {nations.flag(n).style for n in (1, 5, 22)}
    # No club outside the league (Master League club 1 is one now) fields a player of a slot the
    # patch wrote.
    for club in [1, *range(12, 32)]:
        links = _read(filled, W._link_chunks(club))
        hits = [(links[i], links[i + 1]) for i in range(0, 46, 2)]
        assert not [h for h in hits if h[1] <= 22 and (h[0] < 12 or h[0] in (20, 21, 22))], club

    again = tmp_path / "again.bin"
    patcher.patch(rom_path=filled, output_path=again, rosters=mapped, kits=False, looks=False)
    for slot in (1, 20, 21, 22):
        assert _flag(again, slot) == _flag(filled, slot), slot
    styles = [_flag(filled, s).style for s in (1, 20, 21, 22)]
    assert read_styles(str(again), styles) == read_styles(str(filled), styles)
    for club in range(32):
        assert _read(again, W._link_chunks(club)) == _read(filled, W._link_chunks(club))


def test_a_nation_takes_its_kit_along_even_with_kits_off(tmp_path):
    """With `kits=False` a club keeps the disc's kit, but a nation away from home would wear its
    slot owner's: its own kit goes with it (2D preview and 3D texture)."""
    from retro_roster_patcher.games.we2002.rom_writer import RomWriter

    path = tmp_path / "noise.bin"
    path.write_bytes(bytes(range(256)) * 50_000)
    writer = RomWriter(str(path), str(path))
    writer.write_kits = False
    team = WETeamRecord(name="ESP", short_name="ESP", kit_home=(200, 0, 0), kit_away=(0, 0, 200))
    preview = slice(W._nat_jersey_offset(20), W._nat_jersey_offset(20) + 64)
    before = path.read_bytes()[preview]
    writer.write_nat_team(21, team)
    assert path.read_bytes()[preview] == before and writer._pending_tex_patches == []
    writer.write_nat_team(20, team, kits=True)
    assert path.read_bytes()[preview] == W._maglia(team)
    assert [index for index, _ in writer._pending_tex_patches] == [20]


def test_patch_forces_a_nation_s_kit_and_leaves_an_untouched_home_one(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(patcher_module, "RomWriter", _recording_writer(calls))
    monkeypatch.setattr(patcher_module, "apply_ppf", lambda *args, **kwargs: "")
    rom = _sparse_rom(tmp_path, {22: nations.flag(22).style})
    with rom.open("r+b") as handle:  # Croatia's slot still wears its disc flag
        for offset, size in W._NAT_COLOR_OFFSETS[22]:
            handle.seek(offset)
            handle.write(nations.flag(22).clut[:size] if size == 32 else b"")
    club = WETeamRecord(name="Club", short_name="CLB", players=[WEPlayerRecord("A", "B", 2, 1)])
    spain = WETeamRecord(name="ESP", short_name="ESP", national=True, nation=5)
    croatia = WETeamRecord(name="CRO", short_name="CRO", national=True, nation=22)
    WE2002Patcher(cache_dir=tmp_path / "cache").patch(
        rom_path=rom,
        output_path=tmp_path / "out.bin",
        rosters=MappedRosters(game_id="we2002", teams={0: club, 20: spain, 22: croatia}),
        kits=False,
        logos=False,
        looks=False,
    )
    kits = {c[1]: c[3] for c in calls if c[0] == "nat"}
    assert kits == {0: None, 20: True, 22: False}


def _grid_name(image, slot):
    """The name the exhibition grid shows for national slot `slot` (its kanji table entry)."""
    raw = _read(image, W._nat_kanji_chunks(slot))
    text = ""
    for i in range(0, len(raw), 2):
        if raw[i : i + 2] == b"\0\0":
            break
        text += raw[i : i + 2].decode("shift_jis", "replace")
    return unicodedata.normalize("NFKC", text)


@pytest.mark.real_rom
def test_after_the_fill_no_leftover_slot_shows_the_name_of_a_nation_placed_elsewhere(tmp_path):
    """The translation the patch applies labels slot 35 (South Africa) "Alger" and 39 (Costa
    Rica) "Venez": with today's Algeria in 52 the grid showed two Algerias. A home-only slot
    the fill leaves untouched gets its own nation's name (or FIFA code) back."""
    disc = _disc()
    patcher = WE2002Patcher(cache_dir=tmp_path / "cache")
    placed = {52: _nation(52, "Algeria", "ALG"), 36: _nation(36, "Senegal", "SEN")}
    for slot in range(20, 54):
        if slot not in placed and slot not in nations.HOME_ONLY:
            placed[slot] = _nation(100 + slot, f"Zz{slot}", f"Z{slot}")
    mapped = patcher.map_rosters(
        _nations(*placed.values()),
        [SlotMapping(slot, roster.team.id, "") for slot, roster in placed.items()],
    )
    out = tmp_path / "out.bin"
    patcher.patch(
        rom_path=disc, output_path=out, rosters=mapped, kits=False, logos=False, looks=False
    )
    names = {slot: _grid_name(out, slot) for slot in range(54)}
    assert (names[35], names[39]) == ("RSA", "CRC")
    for slot in set(range(54)) - set(placed):
        label = names[slot].lower()
        for roster in placed.values():
            nation = roster.team.name.lower()
            assert not (
                len(label) >= 3 and (nation.startswith(label) or label.startswith(nation))
            ), (slot, names[slot], roster.team.name)


def test_without_nations_no_home_only_slot_is_renamed(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(patcher_module, "RomWriter", _recording_writer(calls))
    monkeypatch.setattr(patcher_module, "apply_ppf", lambda *args, **kwargs: "")
    club = WETeamRecord(name="Club", short_name="CLB", players=[WEPlayerRecord("A", "B", 2, 1)])
    WE2002Patcher(cache_dir=tmp_path / "cache").patch(
        rom_path=_sparse_rom(tmp_path),
        output_path=tmp_path / "out.bin",
        rosters=MappedRosters(game_id="we2002", teams={0: club}),
        kits=False,
        logos=False,
        looks=False,
    )
    assert not [c for c in calls if c[0] == "names"]
