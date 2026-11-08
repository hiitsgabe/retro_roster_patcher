"""ISS 2000 (N64) filled with today's national teams (`--fill-nations`), its region labels and
its scenarios after a patch.

A nation on its own slot, while that slot still shows its cartridge flag and kits, keeps the
cartridge's name, flag and kits; one written anywhere else takes its name, the cartridge's flag
and kits when the image still shows them somewhere, else ESPN's flag and colours. The tests that
patch need the cartridge and are gated on RETRO_ROSTER_TEST_ROMS (a directory holding
iss2000.z64).
"""

from __future__ import annotations

import io
import json
import struct

import pytest

from retro_roster_patcher.core.kits import default_kits
from retro_roster_patcher.core.models import SlotMapping
from retro_roster_patcher.games.iss2000_n64 import flags, kits, names, scenarios
from retro_roster_patcher.games.iss2000_n64 import rom as R
from retro_roster_patcher.games.iss2000_n64.patcher import ISS2000Patcher
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster
from retro_roster_patcher.sports.nationals import fetch_top_nations
from tests.games.iss2000_n64.test_formation import real_rom, retail  # noqa: F401
from tests.sports.test_nationals import FakeApi

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

GERMANY, HOLLAND, ENGLAND, SCOTLAND, NORWAY, FINLAND, ICELAND, QATAR = 0, 4, 14, 15, 20, 21, 22, 95
LEAGUE = League(id=2001, name="English Premier League")
NATIONS = League(id=2021, name="International Friendlies")
GROUPS = ["Goalkeeper"] * 2 + ["Defender", "Midfielder", "Forward"] * 6


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (64, 64), (0, 160, 0, 255)).save(out, "PNG")
    return out.getvalue()


PNG = _png()
RANKING = [
    {"name": n, "rank": r}
    for r, n in enumerate(
        ["Spain", "Argentina", "Saudi Arabia", "France", "England", "Senegal", "Japan"], start=1
    )
]


def transport(url, headers, timeout):
    """FotMob's ranking for the ranking, a green square for every logo."""
    if "fotmob" in url:
        return json.dumps(RANKING).encode()
    return PNG


def roster(team_id: int, name: str, code: str = "", colours=("FF0000", "FFFFFF")) -> TeamRoster:
    team = Team(
        id=team_id, name=name, short_name=name, code=code,
        logo_url=f"https://example.invalid/{team_id}.png",
        color=colours[0], alternate_color=colours[1],
    )  # fmt: skip
    players = [
        Player(id=team_id * 100 + i, name=f"{name[:3]} Player{i:02d}", position=g)
        for i, g in enumerate(GROUPS)
    ]
    return TeamRoster(team=team, players=players)


NATION = {
    n: roster(1 + i, n, c, ("0000FF", "FFFFFF"))
    for i, (n, c) in enumerate(
        [("England", "ENG"), ("Netherlands", "NED"), ("Senegal", "SEN"), ("Scotland", "SCO"),
         ("Spain", "ESP"), ("Argentina", "ARG"), ("Saudi Arabia", "KSA"), ("France", "FRA"),
         ("Japan", "JPN"), ("Qatar", "QAT")]
    )
}  # fmt: skip

# England plays a 4-3-3 over Norway's slot, which is the cartridge's 4-5-1 donor, and Scotland the
# 4-5-1: a re-patch must still copy the cartridge's 4-5-1 row, not England's.
NATION["England"].extra["formation"] = "4-3-3"
NATION["Scotland"].extra["formation"] = "4-5-1"


@pytest.fixture
def patcher(tmp_path):
    return ISS2000Patcher(cache_dir=tmp_path / "cache", transport=transport)


def _clubs(patcher, slots):
    teams = [roster(900 + s, f"Club {s}", f"C{s:02d}") for s in slots]
    return patcher.map_rosters(
        LeagueData(league=LEAGUE, teams=teams),
        [SlotMapping(slot_index=s, team_id=900 + s) for s in slots],
    )


def _mapped(patcher, clubs, nations: dict[int, str]):
    """Clubs on `clubs`, and `nations` {slot: name}."""
    data = LeagueData(league=NATIONS, teams=[NATION[n] for n in nations.values()])
    mapping = [SlotMapping(slot_index=s, team_id=NATION[n].team.id) for s, n in nations.items()]
    out = patcher.map_rosters(data, mapping)
    return _clubs(patcher, clubs).merge(out) if clubs else out


# --- no cartridge needed ----------------------------------------------------------------------


def test_the_fill_takes_the_hundred_nations_and_never_the_all_stars(tmp_path):
    assert ISS2000Patcher.supports_nation_fill
    assert ISS2000Patcher(cache_dir=tmp_path).fill_slots(tmp_path / "x.z64") == list(range(100))


@pytest.mark.parametrize(
    ("name", "slot"),
    [
        ("Germany", GERMANY), ("Netherlands", HOLLAND), ("Republic of Ireland", 18),
        ("United States", 61), ("Turkiye", 38), ("South Korea", 80), ("Senegal", None),
    ],
)  # fmt: skip
def test_a_nation_s_own_slot_comes_from_the_cartridge_s_names(tmp_path, name, slot):
    patcher = ISS2000Patcher(cache_dir=tmp_path)
    assert patcher.own_national_slot(tmp_path / "x.z64", Team(id=1, name=name)) == slot


def test_a_nation_knows_its_cartridge_slot_and_an_empty_squad_is_skipped(tmp_path):
    lines: list[str] = []
    patcher = ISS2000Patcher(cache_dir=tmp_path, on_status=lines.append)
    empty = TeamRoster(team=Team(id=50, name="Saudi Arabia"))
    data = LeagueData(league=NATIONS, teams=[NATION["England"], NATION["Senegal"], empty])
    rosters = patcher.map_rosters(
        data,
        [
            SlotMapping(slot_index=NORWAY, team_id=NATION["England"].team.id),
            SlotMapping(slot_index=FINLAND, team_id=NATION["Senegal"].team.id),
            SlotMapping(slot_index=86, team_id=50),
        ],
    )
    england, senegal = rosters.teams[NORWAY], rosters.teams[FINLAND]
    assert england.national and england.nation == ENGLAND and england.league == ""
    assert senegal.national and senegal.nation is None
    assert 86 not in rosters.teams
    assert any("Saudi Arabia" in line and "Skipped" in line for line in lines)


@pytest.mark.parametrize(
    ("clubs", "labelled"),
    [
        (range(20), set()),  # Europe B holds Spurs and the Nordic nations: not the league's
        (range(51), {"Europe"}),
        (range(51, 61), {"Africa"}),
        ([*range(51, 61), 0], {"Africa"}),
    ],
)
def test_a_region_is_named_after_the_league_only_when_its_pages_are_all_clubs(
    patcher, clubs, labelled
):
    teams = {int(s): t for s, t in _clubs(patcher, clubs).teams.items()}
    labels = patcher._region_labels(teams)
    assert set(labels) == {region for region, _, _ in patcher.slot_groups}
    assert {r for r, text in labels.items() if text} == labelled
    assert all(labels[r] == "English" for r in labelled)


def test_a_kept_flag_s_texture_is_never_handed_to_a_crest():
    image = bytearray(flags.TABLE + 6 * R.TEAMS)
    textures = sorted({first + k for _, first, _, n in flags._BUNDLES for k in range(n)})
    for t in range(R.TEAMS):
        flags.set_entry(image, t, textures[t % len(textures)], 0x48E, 0)
    # Every texture is some team's: the crests take those of the teams left alone, last first.
    taken = set(flags.allocate(image, [0, 1, 2], {0, 1, 2}).values())
    keep = {t for t in range(R.TEAMS) if flags.entry(image, t)[0] in taken and t > 2}
    assert keep
    again = flags.allocate(image, [0, 1, 2], {0, 1, 2}, keep)
    assert not set(again.values()) & {flags.entry(image, t)[0] for t in keep}


def test_a_crest_short_of_a_texture_takes_one_from_a_team_that_can_do_without():
    image = bytearray(flags.TABLE + 6 * R.TEAMS)
    textures = sorted({first + k for _, first, _, n in flags._BUNDLES for k in range(n)})
    assert textures[0] == flags.GENERIC
    for t in range(R.TEAMS):  # teams 1-81 on a texture of their own, the rest on the generic one
        flags.set_entry(image, t, textures[t] if t < len(textures) else flags.GENERIC, 0x48E, 0)
    every = list(range(R.TEAMS))
    # Every team patched and crested: 0 has no texture of its own and nothing is spare ...
    assert 0 not in flags.allocate(image, every, set(every))
    # ... unless a team that can do without one (a nation, drawn in stripes) gives up its own.
    out = flags.allocate(image, every, set(every), low=[50])
    assert out[0] == flags.entry(image, 50)[0] and 50 not in out


def test_stripes_stand_in_for_a_flag_with_no_texture_left():
    from PIL import Image as Img

    # Generic textures as the cartridge's: horizontal bands, vertical bands, a cross (1, 6, 11).
    bands = bytes(
        (11 << 4 | 11) if r >= 22 else (6 << 4 | 6) if r >= 11 else 0x11
        for r in range(32)
        for _ in range(24)
    )
    cols = bytes(
        ((1 if x < 16 else 6 if x < 32 else 11) << 4)
        | (1 if x + 1 < 16 else 6 if x + 1 < 32 else 11)
        for _ in range(32)
        for x in range(0, 48, 2)
    )
    cross = bytes(0x11 for _ in range(768))
    generic = {flags.GENERIC: bands, flags.GENERIC + 1: cols, flags.GENERIC + 2: cross}
    flag = Img.new("RGB", (48, 32), (0, 133, 63))
    flag.paste((253, 239, 66), (16, 0, 32, 32))
    flag.paste((227, 27, 35), (32, 0, 48, 32))
    out = io.BytesIO()
    flag.save(out, "PNG")
    tex, pal = flags.draw(out.getvalue(), (255, 255, 255))
    gid, stripes = flags.stripes(generic, tex, pal)
    assert gid == flags.GENERIC + 1  # Senegal's three vertical bands
    colours = [flags._rgb(struct.unpack_from(">H", stripes, 2 * i)[0]) for i in (1, 6, 11)]
    assert colours[0][1] > 100 > colours[0][0] and colours[2][0] > 180 > colours[2][1]


def test_the_top_nations_fill_the_slots_a_league_leaves(tmp_path):
    lines: list[str] = []
    patcher = ISS2000Patcher(cache_dir=tmp_path / "c", transport=transport, on_status=lines.append)
    squads = {r.team.id: list(r.players) for r in NATION.values()}
    squads[NATION["Saudi Arabia"].team.id] = []
    patcher.api = FakeApi(squads, teams=[r.team for r in NATION.values()])
    clubs = _clubs(patcher, [s for s in range(100) if s not in (98, 99, 70)])  # Argentina's free
    nations, mapping = fetch_top_nations(patcher, tmp_path / "x.z64", 2026, clubs.teams)
    # Argentina on its own slot; Spain, whose slot 3 a club took, on the first slot left.
    assert {m.slot_index: m.team_name for m in mapping} == {
        70: "Argentina",
        98: "Spain",
        99: "France",
    }
    assert any("Saudi Arabia" in line and "Skipped" in line for line in lines)
    rosters = clubs.merge(patcher.map_rosters(nations, mapping))
    assert rosters.teams[70].nation == 70 and rosters.teams[98].nation == 3


def test_more_free_slots_than_nations_maps_each_nation_once(tmp_path):
    patcher = ISS2000Patcher(cache_dir=tmp_path / "c", transport=transport)
    squads = {r.team.id: list(r.players) for r in NATION.values()}
    squads[NATION["Saudi Arabia"].team.id] = []
    patcher.api = FakeApi(squads, teams=[r.team for r in NATION.values()])
    clubs = _clubs(patcher, range(20))
    nations, mapping = fetch_top_nations(patcher, tmp_path / "x.z64", 2026, clubs.teams)
    # Seven ranked sides, Saudi Arabia without a squad: six nations for 80 free slots.
    assert sorted(m.team_name for m in mapping) == sorted(
        ["Spain", "Argentina", "France", "England", "Senegal", "Japan"]
    )
    slots = [m.slot_index for m in mapping]
    assert len(set(slots)) == len(slots) and all(20 <= s < 100 for s in slots)
    rosters = clubs.merge(patcher.map_rosters(nations, mapping))
    assert len(rosters.teams) == 26


def test_with_three_clubs_a_scenario_that_lost_a_nation_never_plays_one_beside_a_club(patcher):
    from tests.games.iss2000_n64.test_scenarios import cartridge

    image = cartridge()
    teams = {int(s): t for s, t in _clubs(patcher, range(3)).teams.items()}
    patcher._scenarios(image, teams, {s: s for s in range(3, 100)}, set())
    for k, (a, b) in enumerate(scenarios.read(image)):
        assert {a, b} <= {0, 1, 2} or not {a, b} & {0, 1, 2}, k
        assert (a, b) == scenarios.RETAIL[k] or {a, b} <= {0, 1, 2}, k


# --- on the cartridge -----------------------------------------------------------------------
#
# A patch of the cartridge takes about half a minute (the assets are packed again), so each
# image is made once for the module: FILLED is a league that leaves the Netherlands their slot,
# with England, Scotland and Senegal written over Norway, Finland and Iceland.

FILL = {HOLLAND: "Netherlands", NORWAY: "England", FINLAND: "Scotland", ICELAND: "Senegal"}
CLUBS = [*range(4), *range(5, 20)]


def _patch(patcher, rom, out, rosters, **options):
    patcher.patch(rom_path=rom, output_path=out, rosters=rosters, looks=False, **options)
    return out.read_bytes()


@pytest.fixture(scope="module")
def cartridge_rom(tmp_path_factory):
    import os
    from pathlib import Path

    if not os.environ.get("RETRO_ROSTER_TEST_ROMS"):
        pytest.skip("set RETRO_ROSTER_TEST_ROMS to a directory holding iss2000.z64")
    rom = Path(os.environ["RETRO_ROSTER_TEST_ROMS"]) / "iss2000.z64"
    if not rom.exists():
        pytest.fail(f"RETRO_ROSTER_TEST_ROMS is set but {rom} is missing")
    path = tmp_path_factory.mktemp("iss2000") / "iss2000.z64"
    path.write_bytes(rom.read_bytes())
    return path


@pytest.fixture(scope="module")
def made(cartridge_rom, tmp_path_factory):
    """{name: (path, image)} of the patches the tests read, each made once."""
    patcher = ISS2000Patcher(cache_dir=tmp_path_factory.mktemp("cache"), transport=transport)
    out = tmp_path_factory.mktemp("out")
    found: dict[str, tuple] = {}

    def make(name, rom, rosters, **options):
        if name not in found:
            path = out / f"{name}.z64"
            found[name] = (path, _patch(patcher, rom, path, rosters, **options))
            assert len(found[name][1]) == R.ROM_SIZE, name
        return found[name]

    def get(name):
        if name == "filled":
            return make(name, cartridge_rom, _mapped(patcher, CLUBS, FILL))
        if name == "refilled":
            return make(name, get("filled")[0], _mapped(patcher, CLUBS, FILL))
        if name == "moved again":  # England from Norway's slot to the Faroes'
            again = {**FILL, 23: "England"}
            del again[NORWAY]
            return make(name, get("filled")[0], _mapped(patcher, CLUBS, again))
        if name == "plain then fill":  # kits=False: a nation's own kits still come with it
            fill = _mapped(patcher, range(20), {NORWAY: "England", QATAR: "Qatar"})
            return make(name, get("plain")[0], fill, kits=False)
        if name == "arsenal":  # a club on England's slot, its name only
            return make(name, cartridge_rom, _clubs(patcher, [ENGLAND]), logos=False, kits=False)
        if name == "england home":
            return make(name, get("arsenal")[0], _mapped(patcher, [], {ENGLAND: "England"}))
        assert name == "plain"
        # An earlier patch labelled Europe after the league (as every patch did before).
        stale = bytearray(cartridge_rom.read_bytes())
        names.write_regions(stale, R.AssetSpace(stale), {"Europe": "PREMIER"})
        assert names.region_cells(stale) != names.RETAIL_REGIONS
        old = out / "stale.z64"
        old.write_bytes(stale)
        return make(name, old, _clubs(patcher, range(20)))

    return get


def _bundles(image):
    a = R.assets(image)
    return {k: R.read_asset(image, a[k]) for k in (*range(670, 675), flags.PALETTE_ASSET)}


def _flag(image, team) -> bytes:
    """The flag team select shows for `team`: its texture and palette bytes."""
    b = _bundles(image)
    tex, block, idx = flags.entry(image, team)
    asset, off = flags.texture_place(tex)
    p = flags.palette_place(block, idx)
    return b[asset][off : off + flags.TEX_SIZE] + b[flags.PALETTE_ASSET][p : p + 32]


def _kit(image, team) -> bytes:
    a = R.assets(image)
    stream = b"".join(R.read_asset(image, a[k]) for k in kits.PACKS)
    return b"".join(
        stream[kits._record(team, side) : kits._record(team, side) + kits.SIZE] for side in (0, 1)
    )


def _name(image, team) -> bytes:
    sheet = R.read_asset(image, R.assets(image)[names.NAMES_ASSET])
    o = (names.FIRST_ROW + team * names.CELL_H) * names.SHEET_W // 2
    return sheet[o : o + names.CELL_H * names.SHEET_W // 2]


def _players(image, team) -> list[str]:
    return [
        R.decode_name(image[R.record_offset(team, i) + R.NAME :][: R.NAME_LEN]) for i in range(22)
    ]


@real_rom
def test_the_packaged_looks_are_the_cartridge_s(retail):  # noqa: F811
    from retro_roster_patcher.games.iss2000_n64 import retail as packaged

    for t in range(100):
        assert b"".join(packaged.flag(t)) == _flag(retail, t), t
        assert packaged.kit(t, 0) + packaged.kit(t, 1) == _kit(retail, t), t
        assert packaged.name(t) == _name(retail, t), t


@real_rom
def test_the_packaged_region_labels_are_the_cartridge_s(retail):  # noqa: F811
    assert names.region_cells(retail) == names.RETAIL_REGIONS


@real_rom
def test_a_nation_whose_slot_a_club_took_lands_with_its_own_name_flag_and_kit(made, retail):  # noqa: F811
    _, image = made("filled")
    # England, in Norway's slot: its name, England's flag and kits, never Norway's.
    assert _flag(image, NORWAY) == _flag(retail, ENGLAND)
    assert _kit(image, NORWAY) == _kit(retail, ENGLAND)
    assert _name(image, NORWAY) != _name(retail, NORWAY)
    assert _players(image, NORWAY) != _players(retail, NORWAY)
    # The club on England's slot wears its crest.
    assert _flag(image, ENGLAND) != _flag(retail, ENGLAND)
    # The Netherlands on their own slot keep the cartridge's name, flag and kits, with
    # today's squad.
    assert _flag(image, HOLLAND) == _flag(retail, HOLLAND)
    assert _kit(image, HOLLAND) == _kit(retail, HOLLAND)
    assert _name(image, HOLLAND) == _name(retail, HOLLAND)
    assert _players(image, HOLLAND) != _players(retail, HOLLAND)
    # Senegal, which the cartridge lacks: ESPN's flag (the green square) and colours.
    tex, pal = flags.draw(PNG, (255, 255, 255))
    assert _flag(image, ICELAND) == tex + pal
    home, _ = default_kits(NATION["Senegal"].team)
    record = bytearray(_kit(retail, ICELAND)[: kits.SIZE])
    kits.paint(record, home)
    assert _kit(image, ICELAND)[: kits.SIZE] == bytes(record)
    # Every nation no team was written over keeps its flag, or (when a crest took its
    # texture, as a league patch always did) waves the generic stripes in its own colours.
    for t in range(23, 100):
        tex = flags.entry(image, t)[0]
        assert _flag(image, t) == _flag(retail, t) or tex == flags.GENERIC, t
        assert _flag(image, t)[-32:] == _flag(retail, t)[-32:], t


@real_rom
def test_re_patching_with_the_fill_gives_the_same_image(made, retail):  # noqa: F811
    # Clubs' kits are painted from the cartridge's records, not over the last paint.
    _, first = made("filled")
    _, second = made("refilled")
    assert second == first
    assert _flag(second, NORWAY) == _flag(retail, ENGLAND)


@real_rom
def test_a_fill_over_a_plain_league_gives_the_nations_their_cartridge_looks_back(
    made,
    retail,  # noqa: F811
):
    _, plain = made("plain")
    # The plain league's crests took Qatar's texture; its palette, kits and name stayed.
    assert _flag(plain, QATAR) != _flag(retail, QATAR)
    _, image = made("plain then fill")
    # England, whose slot a club holds, on Norway's: England's flag and kits (with kits=False).
    assert _flag(image, NORWAY) == _flag(retail, ENGLAND)
    assert _kit(image, NORWAY) == _kit(retail, ENGLAND)
    # Qatar on its own slot: its flag back, its kits and name as the cartridge has them.
    assert _flag(image, QATAR) == _flag(retail, QATAR)
    assert _kit(image, QATAR) == _kit(retail, QATAR)
    assert _name(image, QATAR) == _name(retail, QATAR)


@real_rom
def test_a_nation_back_on_its_own_slot_reads_its_cartridge_name(made, retail):  # noqa: F811
    _, arsenal = made("arsenal")
    assert _name(arsenal, ENGLAND) != _name(retail, ENGLAND)
    _, image = made("england home")
    assert _name(image, ENGLAND) == _name(retail, ENGLAND)
    assert _flag(image, ENGLAND) == _flag(retail, ENGLAND)
    assert _kit(image, ENGLAND) == _kit(retail, ENGLAND)


@real_rom
def test_a_nation_moved_by_an_earlier_patch_is_found_where_it_went(made, retail):  # noqa: F811
    _, image = made("moved again")
    assert _flag(image, 23) == _flag(retail, ENGLAND)
    assert _kit(image, 23) == _kit(retail, ENGLAND)


@real_rom
def test_a_league_short_of_a_region_gives_it_back_the_cartridge_s_label(made):
    _, image = made("plain")
    assert names.region_cells(image) == names.RETAIL_REGIONS


@real_rom
def test_a_plain_league_puts_every_scenario_on_clubs_or_leaves_it_on_its_nations(made):
    _, image = made("plain")
    pairs = scenarios.read(image)
    for k, (pair, old) in enumerate(zip(pairs, scenarios.RETAIL, strict=True)):
        if min(old) >= 20:  # Croatia-Yugoslavia, Uruguay-Brazil, ...: still there
            assert pair == old, k
        else:
            assert max(pair) < 20 and pair[0] != pair[1], k
    assert len({frozenset(p) for p in pairs}) == len(pairs)
    at, room = scenarios.strings(image, 0)[scenarios.COUNT]
    text = scenarios.decode(image[at : at + room]).replace("|", " ")
    a, b = pairs[0]
    assert f"Club {a} take on Club {b}" in text


@real_rom
def test_scenario_nations_follow_the_fill_and_lost_ones_play_patched_teams(made):
    _, image = made("filled")
    pairs = scenarios.read(image)
    assert pairs[0] == (NORWAY, FINLAND)  # England-Scotland, where the fill put them
    assert pairs[5] == (25, 24)  # Croatia-Yugoslavia, never touched
    assert pairs[12] == (30, HOLLAND)  # the Czechs untouched, the Dutch home with today's squad
    patched = set(CLUBS) | set(FILL)
    for k, (a, b) in enumerate(pairs):
        assert (a, b) == scenarios.RETAIL[k] or {a, b} <= patched, k
    at, room = scenarios.strings(image, 0)[0]
    assert scenarios.decode(image[at : at + room]) == "3:41, Second Half  England to Free Kick|"


@real_rom
def test_a_patch_that_leaves_every_scenario_nation_in_place_leaves_the_scenarios(
    patcher,
    retail,  # noqa: F811
):
    # Fiji and Papua NG play no scenario; the Netherlands stay on their own slot.
    from retro_roster_patcher.games.iss2000_n64.patcher import _present

    rosters = _clubs(patcher, [98, 99]).merge(_mapped(patcher, [], {HOLLAND: "Netherlands"}))
    image = bytearray(retail)
    patcher._scenarios(image, {int(s): t for s, t in rosters.teams.items()}, _present(retail), {4})
    assert image == retail
