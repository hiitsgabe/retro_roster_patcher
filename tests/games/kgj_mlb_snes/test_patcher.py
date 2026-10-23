"""The KGJ MLB patcher against the unified interface.

`map_rosters` keeps pitchers from slot 15 on whatever the squad's shape: the
game itself decides who pitches by the slot.

Every read-back of a patched ROM goes through a *fresh* reader on the output
path. `KGJRomWriter.__init__` builds its own reader over the *input* file, so
`writer.reader.data` is the pre-write image for the writer's whole lifetime and
asserting against it would assert nothing.
"""

import copy
import io
import subprocess
import sys
import textwrap

import pytest

from retro_roster_patcher.core.errors import ApiError, CapabilityError, MappingError, RomError
from retro_roster_patcher.core.models import MappedRosters, PatchResult, RomSlot, SlotMapping
from retro_roster_patcher.games.kgj_mlb_snes import expansion, looks
from retro_roster_patcher.games.kgj_mlb_snes.models import (
    BATTERS_PER_TEAM,
    KGJ_TEAM_ORDER,
    PLAYERS_PER_TEAM,
    TEAM_COUNT,
    KGJPlayerRecord,
    KGJTeamRecord,
)
from retro_roster_patcher.games.kgj_mlb_snes.patcher import (
    KGJMLBPatcher,
    _is_al,
    _team_data_fits,
)
from retro_roster_patcher.games.kgj_mlb_snes.rom_reader import TEAM_DATA_SPAN, KGJRomReader
from retro_roster_patcher.games.kgj_mlb_snes.stat_mapper import KGJStatMapper
from retro_roster_patcher.sports.espn import EspnClient
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster
from tests.fixtures import synthetic_kgj_rom as fixture
from tests.fixtures import synthetic_snes_rom as nhl94_snes_fixture

#: Slots from `MODERN_MLB_TO_KGJ`. Seattle and Chicago's two are the ones the
#: fake API covers; the White Sox slot is one of the two that two codes reach.
SEA_SLOT = 11

#: Where the fixture's team data starts, the same derivation
#: `test_rom_writer.py` uses: the marker offset plus the marker's own length.
FIRST_TEAM_OFFSET = fixture.MARKER_OFFSET + len(fixture.FIRST_TEAM_MARKER)
CWS_SLOT = 3
OAK_SLOT = 10


class FakeApi:
    """Stands in for `EspnClient`.

    Records what it was asked for, because `season` reaches the endpoints
    differently -- a cache key on the squad call, a path segment on the stats
    and lineup calls -- and upstream passed it to only one of them.
    """

    def __init__(self, teams, squad_size=25, lines=None, lineup=None):
        self._teams = teams
        self._squad_size = squad_size
        # Player 0 bats, player 15 pitches; both have a line in each category.
        both = {"batting": {"AVG": 0.311}, "pitching": {"ERA": 2.5}}
        self._lines = {0: both, 15: both} if lines is None else lines
        self._lineup = lineup or []
        self.squad_calls = []
        self.stat_calls = []
        self.lineup_calls = []

    def get_mlb_teams(self):
        return list(self._teams)

    def get_baseball_squad(self, team_id, season=None):
        self.squad_calls.append((team_id, season))
        return [
            Player(
                id=index,
                name=f"First{index} Last{index:02d}",
                position="SP" if index >= BATTERS_PER_TEAM else "CF",
                number=index + 1,
            )
            for index in range(self._squad_size)
        ]

    def get_baseball_athlete_stats(self, athlete_id, season):
        self.stat_calls.append((athlete_id, season))
        return dict(self._lines.get(athlete_id, {}))

    def get_baseball_lineup(self, team_id, season):
        self.lineup_calls.append((team_id, season))
        return list(self._lineup)


def _teams():
    return [
        Team(id=1, name="Seattle Mariners", code="SEA"),
        Team(id=2, name="Chicago White Sox", code="CWS"),
        Team(id=3, name="Montreal Expos", code="MON"),
        Team(id=4, name="Not A Real Team", code="ZZZ"),
    ]


@pytest.fixture
def patcher(tmp_path):
    """A patcher wired to a fake API covering two real KGJ MLB slots."""
    p = KGJMLBPatcher(cache_dir=tmp_path / "cache")
    p.api = FakeApi(_teams())
    return p


@pytest.fixture
def rom(tmp_path):
    return fixture.write_kgj_rom(tmp_path / "kgj.sfc")


@pytest.fixture
def out(tmp_path):
    return tmp_path / "patched.sfc"


def _league_data(*entries, season=2025):
    """`LeagueData` with one `TeamRoster` per `(code, squad size)`, in order.

    The order is what decides which of two colliding aliases lands in the shared
    slot, so it is a parameter rather than an accident of a dict.
    """
    return LeagueData(
        league=League(id=0, name="MLB", country="USA", country_code="US", season=season),
        teams=[
            TeamRoster(
                team=Team(id=index + 1, name=code, code=code),
                players=[
                    Player(
                        id=index * 100 + n,
                        name=f"{code}First{n:02d} {code}Last{n:02d}",
                        position="SP" if n >= BATTERS_PER_TEAM else "CF",
                        number=n + 1,
                    )
                    for n in range(size)
                ],
                extra={"stats": {}},
            )
            for index, (code, size) in enumerate(entries)
        ],
    )


def _uneven_league(batters, starters, relievers, code="SEA"):
    """One team whose three groups are the sizes given, and nothing else.

    Sized to make the wrong answer and the right answer differ. Twelve batters
    is the case the follow-up names: slots 12-14 are batter slots holding
    starting pitchers, and a fixture of fifteen or more would not reach the
    disagreement at all.
    """
    players = [
        Player(id=n, name=f"Bat{n:02d} Last{n:02d}", position="CF", number=n + 1)
        for n in range(batters)
    ]
    players += [
        Player(id=100 + n, name=f"Sta{n:02d} Last{n:02d}", position="SP", number=n + 1)
        for n in range(starters)
    ]
    players += [
        Player(id=200 + n, name=f"Rel{n:02d} Last{n:02d}", position="RP", number=n + 1)
        for n in range(relievers)
    ]
    return LeagueData(
        league=League(id=0, name="MLB", country="USA", country_code="US", season=2025),
        teams=[
            TeamRoster(
                team=Team(id=1, name=code, code=code),
                players=players,
                extra={"stats": {}},
            )
        ],
    )


def _records(count):
    return [
        KGJPlayerRecord(
            first_initial="W",
            last_name=f"WRITE{index:02d}",
            position="CF",
            jersey_number=index + 1,
            is_pitcher=index >= BATTERS_PER_TEAM,
        )
        for index in range(count)
    ]


def _mapped(*pairs):
    return MappedRosters(
        game_id="kgj-mlb-snes",
        teams={
            slot: KGJTeamRecord(index=slot, name=KGJ_TEAM_ORDER[slot], players=_records(count))
            for slot, count in pairs
        },
    )


def _read_back(path, slot):
    reader = KGJRomReader(str(path))
    assert reader.load() is True
    assert reader.validate() is True
    return reader.read_team_roster(slot)


def test_the_patcher_is_registered_with_its_id():
    from retro_roster_patcher import get_patcher

    assert get_patcher("kgj-mlb-snes") is KGJMLBPatcher


def test_the_patcher_declares_its_platform():
    assert KGJMLBPatcher.platform == "snes"


def test_the_patcher_declares_its_sport():
    assert KGJMLBPatcher.sport == "baseball"


def test_the_patcher_maps_teams_without_a_slot_mapping():
    assert KGJMLBPatcher.requires_slot_mapping is False


def test_the_patcher_declares_espn_as_its_only_provider():
    assert KGJMLBPatcher.providers == ("espn",)


def test_registering_this_game_makes_espns_mlb_methods_reachable():
    """The registration is what makes ESPN's three MLB methods reachable.

    Named for the claim rather than for the ordering: this was the library's
    first baseball game and asserted it was the only one, which `mvp-psp` then
    made false. Membership is what the claim actually needs -- that a baseball
    game is registered, so `get_mlb_teams` and its two siblings are reachable
    from the registry rather than only from a dotted path.
    """
    from retro_roster_patcher import list_patchers

    baseball = {info.game_id for info in list_patchers() if info.sport == "baseball"}
    assert "kgj-mlb-snes" in baseball


def test_importing_the_package_root_is_what_registers_the_game(tmp_path):
    # Registration is a side-effect import at the bottom of the package root, so
    # importing the game module directly must not be what a consumer relies on.
    script = textwrap.dedent(
        """
        import retro_roster_patcher as rrp
        print(",".join(sorted(info.game_id for info in rrp.list_patchers())))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert "kgj-mlb-snes" in result.stdout.strip().split(",")


def test_the_constructor_accepts_a_string_cache_directory(tmp_path):
    p = KGJMLBPatcher(cache_dir=str(tmp_path / "cache"))
    assert p.cache_dir == tmp_path / "cache"


def test_the_constructor_builds_its_client_eagerly(tmp_path):
    p = KGJMLBPatcher(cache_dir=tmp_path / "cache")
    assert type(p.api) is EspnClient


def test_the_constructor_creates_the_cache_directory(tmp_path):
    KGJMLBPatcher(cache_dir=tmp_path / "cache")
    assert (tmp_path / "cache").is_dir() is True


def test_the_constructor_refuses_an_api_key(tmp_path):
    # No provider here takes a credential. A parameter that silently did nothing
    # would let a caller believe one was in use.
    with pytest.raises(TypeError):
        KGJMLBPatcher(cache_dir=tmp_path / "cache", api_key="secret")


def test_the_constructor_refuses_a_provider_it_does_not_have(tmp_path):
    with pytest.raises(CapabilityError):
        KGJMLBPatcher(cache_dir=tmp_path / "cache", provider="nhl")


def test_the_constructor_accepts_its_own_provider(tmp_path):
    p = KGJMLBPatcher(cache_dir=tmp_path / "cache", provider="espn")
    assert p.provider == "espn"


def test_the_default_provider_is_the_first_declared(tmp_path):
    p = KGJMLBPatcher(cache_dir=tmp_path / "cache")
    assert p.provider == "espn"


def test_the_al_is_the_first_fourteen_slots():
    assert [_is_al(slot) for slot in (0, 13, 14, 27)] == [True, True, False, False]


def _reader_for(path):
    reader = KGJRomReader(str(path))
    reader.load()
    reader.validate()
    return reader


def test_a_marker_at_the_head_of_the_file_leaves_room(rom):
    assert _team_data_fits(_reader_for(rom)) is True


def test_a_marker_exactly_far_enough_from_the_end_leaves_room(tmp_path):
    # The last byte of team 27 lands on the last byte of the file.
    offset = fixture.ROM_SIZE - TEAM_DATA_SPAN - len(fixture.FIRST_TEAM_MARKER)
    path = fixture.write_kgj_rom(tmp_path / "exact.sfc", marker_offset=offset)
    assert _team_data_fits(_reader_for(path)) is True


def test_a_marker_one_byte_too_late_does_not(tmp_path):
    offset = fixture.ROM_SIZE - TEAM_DATA_SPAN - len(fixture.FIRST_TEAM_MARKER) + 1
    path = fixture.write_kgj_rom(tmp_path / "off-by-one.sfc", marker_offset=offset)
    assert _team_data_fits(_reader_for(path)) is False


def test_a_reader_that_never_loaded_does_not_fit(tmp_path):
    assert _team_data_fits(KGJRomReader(str(tmp_path / "absent.sfc"))) is False


def test_analyze_recognises_a_headerless_image(patcher, rom):
    assert patcher.analyze_rom(rom).is_valid is True


def test_analyze_recognises_a_headered_image(patcher, tmp_path):
    path = fixture.write_kgj_rom(tmp_path / "kgj.smc", with_header=True)
    assert patcher.analyze_rom(path).is_valid is True


def test_analyze_stamps_its_own_game_id(patcher, rom):
    assert patcher.analyze_rom(rom).game_id == "kgj-mlb-snes"


def test_analyze_reports_the_files_size(patcher, rom):
    assert patcher.analyze_rom(rom).size == fixture.ROM_SIZE


def test_analyze_reports_the_path_it_was_given(patcher, rom):
    assert patcher.analyze_rom(rom).path == str(rom)


def test_analyze_reports_one_slot_per_team(patcher, rom):
    # The 28 clubs and the two All-Star teams, Arizona's and Tampa Bay's.
    assert len(patcher.analyze_rom(rom).slots) == expansion.TEAMS


def test_the_last_two_slots_are_the_all_star_teams(patcher, rom):
    slots = patcher.analyze_rom(rom).slots[TEAM_COUNT:]
    assert [(s.index, s.display_name) for s in slots] == [
        (28, "National League All-Stars"),
        (29, "American League All-Stars"),
    ]


def test_a_slots_display_name_is_the_1994_team(patcher, rom):
    assert patcher.analyze_rom(rom).slots[10].display_name == "Oakland Athletics"


def test_every_slots_display_name_is_distinct(patcher, rom):
    # `RomSlot.display_name` is what a slot-picking UI lists, so a repeated
    # value leaves the user unable to tell two rows apart.
    names = [slot.display_name for slot in patcher.analyze_rom(rom).slots]
    assert len(set(names)) == expansion.TEAMS


def test_a_slots_current_name_says_it_is_a_player(patcher, rom):
    # The upstream slot record's only ROM-derived field is a *player* name. The
    # NBA Live 95 port faced the identical problem and labelled it the same way,
    # rather than filing a player under a field a UI renders as a team.
    name = f"{fixture.player_first_initial(10, 0)}. {fixture.player_last_name(10, 0)}"
    expected = f"First player: {name}"
    assert patcher.analyze_rom(rom).slots[10].current_name == expected


def test_two_slots_carry_different_current_names(patcher, rom):
    slots = patcher.analyze_rom(rom).slots
    assert slots[10].current_name != slots[11].current_name


def test_a_slot_is_a_rom_slot(patcher, rom):
    assert type(patcher.analyze_rom(rom).slots[0]) is RomSlot


def test_analyze_reports_whether_the_image_has_a_copier_header(patcher, rom):
    assert patcher.analyze_rom(rom).extra["has_header"] is False


def test_analyze_reports_the_copier_header_on_a_headered_image(patcher, tmp_path):
    path = fixture.write_kgj_rom(tmp_path / "kgj.smc", with_header=True)
    assert patcher.analyze_rom(path).extra["has_header"] is True


def test_analyze_reports_where_the_marker_matched(patcher, rom):
    expected = fixture.MARKER_OFFSET + len(fixture.FIRST_TEAM_MARKER)
    assert patcher.analyze_rom(rom).extra["first_team_offset"] == expected


def test_the_extra_dict_survives_a_json_round_trip(patcher, rom):
    import json

    info = patcher.analyze_rom(rom)
    assert json.loads(json.dumps(info.to_dict()))["extra"] == info.extra


def test_analyze_refuses_an_image_with_no_marker(patcher, tmp_path):
    path = fixture.write_kgj_rom(tmp_path / "nomarker.sfc", place_marker=False)
    assert patcher.analyze_rom(path).is_valid is False


def test_analyze_refuses_an_image_of_the_wrong_size(patcher, tmp_path):
    path = tmp_path / "wrongsize.sfc"
    path.write_bytes(bytes(fixture.build_kgj_rom())[: fixture.ROM_SIZE - 1])
    assert patcher.analyze_rom(path).is_valid is False


def test_analyze_refuses_an_image_whose_marker_leaves_no_room(patcher, tmp_path):
    # This is the file upstream accepted, patched nothing in, and reported as a
    # success.
    path = fixture.write_kgj_rom(tmp_path / "nofit.sfc", marker_offset=fixture.ROM_SIZE - 20)
    assert patcher.analyze_rom(path).is_valid is False


def test_analyze_still_reports_the_size_of_an_image_it_refuses(patcher, tmp_path):
    path = fixture.write_kgj_rom(tmp_path / "nomarker.sfc", place_marker=False)
    assert patcher.analyze_rom(path).size == fixture.ROM_SIZE


def test_analyze_does_not_claim_the_other_snes_game_in_this_library(patcher, tmp_path):
    # `retro-roster analyze` probes every registered patcher against one image,
    # and NHL 94 SNES is the other cartridge here. Its 1 MB dump fails the size
    # test outright; this pins that the sweep stays honest.
    path = nhl94_snes_fixture.write_nhl94_snes_rom(tmp_path / "nhl94.sfc")
    assert patcher.analyze_rom(path).is_valid is False


def test_analyze_does_not_claim_an_arbitrary_two_megabyte_file(patcher, tmp_path):
    path = tmp_path / "other.sfc"
    path.write_bytes(bytes(fixture.ROM_SIZE))
    assert patcher.analyze_rom(path).is_valid is False


def test_analyze_raises_for_a_file_that_is_not_there(patcher, tmp_path):
    # DELIBERATE DIVERGENCE: upstream returned `KGJRomInfo(path, size=0)` here,
    # which is the same answer it gave for a readable image of another game.
    with pytest.raises(RomError):
        patcher.analyze_rom(tmp_path / "absent.sfc")


def test_analyze_raises_for_a_path_that_cannot_be_read(patcher, tmp_path):
    directory = tmp_path / "adirectory"
    directory.mkdir()
    with pytest.raises(RomError):
        patcher.analyze_rom(directory)


def test_the_unreadable_error_names_the_file(patcher, tmp_path):
    missing = tmp_path / "absent.sfc"
    with pytest.raises(RomError, match="absent.sfc"):
        patcher.analyze_rom(missing)


def test_fetch_returns_one_roster_per_slot_mapped_team(patcher):
    # SEA and CWS have slots; MON is no team of today and ZZZ is not a team.
    assert len(patcher.fetch(season=2025).teams) == 2


def test_fetch_names_the_league(patcher):
    assert patcher.fetch(season=2025).league.name == "MLB"


def test_fetch_records_the_season_it_was_asked_for(patcher):
    assert patcher.fetch(season=1994).league.season == 1994


def test_fetch_counts_the_rosters_it_built_and_not_the_teams_it_saw(patcher):
    assert patcher.fetch(season=2025).league.teams_count == 2


def test_fetch_threads_the_season_into_the_squad_call(patcher):
    # DELIBERATE DIVERGENCE: upstream called `get_baseball_squad(team.id)` with
    # no season at all, and the season is part of that endpoint's cache key, so
    # the first season ever fetched was served forever.
    patcher.fetch(season=1994)
    assert patcher.api.squad_calls == [(1, 1994), (2, 1994)]


def test_fetch_asks_for_every_players_season_line(patcher):
    patcher.fetch(season=1994)
    assert sorted(patcher.api.stat_calls) == sorted((n, 1994) for n in range(25) for _ in range(2))


def test_fetch_threads_the_season_into_the_lineup_call(patcher):
    patcher.fetch(season=1994)
    assert patcher.api.lineup_calls == [(1, 1994), (2, 1994)]


def test_fetch_keeps_the_batting_line_of_a_batter_and_the_pitching_line_of_a_pitcher(patcher):
    data = patcher.fetch(season=2025)
    assert data.teams[0].extra["stats"] == {"0": {"AVG": 0.311}, "15": {"ERA": 2.5}}


def test_a_team_with_no_lines_still_gets_an_empty_dict(tmp_path):
    p = KGJMLBPatcher(cache_dir=tmp_path / "cache")
    p.api = FakeApi(_teams(), lines={})
    assert p.fetch(season=2025).teams[0].extra["stats"] == {}


def test_fetch_puts_the_lineup_where_the_squad_order_can_reach_it(tmp_path):
    p = KGJMLBPatcher(cache_dir=tmp_path / "cache")
    p.api = FakeApi(_teams(), lineup=[{"id": 3, "order": 1.0}])
    assert p.fetch(season=2025).teams[0].extra["lineup"] == [{"id": 3, "order": 1.0}]


def test_fetch_raises_when_the_provider_returns_no_teams(tmp_path):
    p = KGJMLBPatcher(cache_dir=tmp_path / "cache")
    p.api = FakeApi([])
    with pytest.raises(ApiError):
        p.fetch(season=2025)


def test_fetch_raises_when_no_team_maps_to_a_slot(tmp_path):
    p = KGJMLBPatcher(cache_dir=tmp_path / "cache")
    p.api = FakeApi([Team(id=1, name="Expos", code="MON"), Team(id=2, name="Pilots", code="SEP")])
    with pytest.raises(ApiError):
        p.fetch(season=2025)


def test_fetch_reports_progress_once_per_team_and_once_at_the_end(patcher):
    seen = []
    patcher.fetch(season=2025, on_progress=lambda f, m: seen.append(f))
    assert seen == [0.0, 0.5, 1.0]


def test_fetch_narrates_its_first_step(tmp_path):
    messages = []
    p = KGJMLBPatcher(cache_dir=tmp_path / "cache", on_status=messages.append)
    p.api = FakeApi(_teams())
    p.fetch(season=2025)
    assert messages == ["Fetching MLB teams..."]


def test_fetch_does_not_reach_the_provider_for_a_team_with_no_slot(patcher):
    patcher.fetch(season=2025)
    assert [call[0] for call in patcher.api.squad_calls] == [1, 2]


def test_map_rosters_refuses_a_slot_mapping(patcher):
    with pytest.raises(CapabilityError):
        patcher.map_rosters(_league_data(("SEA", 25)), [SlotMapping(slot_index=0, team_id=1)])


def test_map_rosters_stamps_its_own_game_id(patcher):
    assert patcher.map_rosters(_league_data(("SEA", 25))).game_id == "kgj-mlb-snes"


def test_map_rosters_keys_a_team_by_its_rom_slot(patcher):
    assert list(patcher.map_rosters(_league_data(("SEA", 25))).teams) == [SEA_SLOT]


def test_map_rosters_is_sparse(patcher):
    # Upstream built all 28 records and left the unmatched ones empty.
    assert len(patcher.map_rosters(_league_data(("SEA", 25))).teams) == 1


def test_a_mapped_team_carries_its_1994_name(patcher):
    mapped = patcher.map_rosters(_league_data(("SEA", 25)))
    assert mapped.teams[SEA_SLOT].name == "Seattle Mariners"


def test_a_mapped_team_holds_one_record_per_selected_player(patcher):
    mapped = patcher.map_rosters(_league_data(("SEA", 25)))
    assert len(mapped.teams[SEA_SLOT].players) == PLAYERS_PER_TEAM


def test_a_squad_of_more_than_25_is_cut_to_the_roster_size(patcher):
    mapped = patcher.map_rosters(_league_data(("SEA", 60)))
    assert len(mapped.teams[SEA_SLOT].players) == PLAYERS_PER_TEAM


def test_a_team_with_no_rom_slot_is_dropped(patcher):
    assert patcher.map_rosters(_league_data(("MON", 25))).teams == {}


def test_the_pitcher_slots_hold_pitcher_records(patcher):
    mapped = patcher.map_rosters(_league_data(("SEA", 25)))
    flags = [r.is_pitcher for r in mapped.teams[SEA_SLOT].players[BATTERS_PER_TEAM:]]
    assert flags == [True] * 10


def test_the_batter_slots_hold_batter_records(patcher):
    mapped = patcher.map_rosters(_league_data(("SEA", 25)))
    flags = [r.is_pitcher for r in mapped.teams[SEA_SLOT].players[:BATTERS_PER_TEAM]]
    assert flags == [False] * BATTERS_PER_TEAM


def test_a_starting_pitcher_gets_the_starter_defaults(patcher):
    # `is_starter` decides which default row a stat-less pitcher takes.
    mapped = patcher.map_rosters(_league_data(("SEA", 25)))
    assert mapped.teams[SEA_SLOT].players[BATTERS_PER_TEAM].pitcher_attrs.fatigue == 7


def test_a_relief_pitcher_gets_the_reliever_defaults(patcher):
    mapped = patcher.map_rosters(_league_data(("SEA", 25)))
    assert mapped.teams[SEA_SLOT].players[PLAYERS_PER_TEAM - 1].pitcher_attrs.fatigue == 3


def test_a_populated_slot_survives_an_empty_alias_arriving_after_it(patcher):
    # `MODERN_MLB_TO_KGJ` names slot 3 twice, CWS and CHW. Without the guard the
    # empty one would wipe the populated one, `patch` would skip the slot, and
    # the run would report success with the 1994 roster still in place.
    mapped = patcher.map_rosters(_league_data(("CWS", 25), ("CHW", 0)))
    assert len(mapped.teams[CWS_SLOT].players) == PLAYERS_PER_TEAM


def test_the_oakland_alias_pair_is_guarded_too(patcher):
    mapped = patcher.map_rosters(_league_data(("OAK", 25), ("ATH", 0)))
    assert len(mapped.teams[OAK_SLOT].players) == PLAYERS_PER_TEAM


def test_a_populated_alias_arriving_second_does_take_the_slot(patcher):
    # The guard is one-directional on purpose: it protects a populated roster
    # from an empty one, not the first arrival from the second.
    mapped = patcher.map_rosters(_league_data(("CHW", 0), ("CWS", 25)))
    assert len(mapped.teams[CWS_SLOT].players) == PLAYERS_PER_TEAM


def test_an_empty_roster_with_no_collision_still_takes_its_slot(patcher):
    mapped = patcher.map_rosters(_league_data(("SEA", 0)))
    assert mapped.teams[SEA_SLOT].players == []


def test_two_different_teams_take_two_different_slots(patcher):
    mapped = patcher.map_rosters(_league_data(("SEA", 25), ("CWS", 25)))
    assert sorted(mapped.teams) == sorted([SEA_SLOT, CWS_SLOT])


def test_patch_refuses_another_games_rosters(patcher, rom, out):
    wrong = MappedRosters(game_id="nhl94-snes", teams={})
    with pytest.raises(MappingError):
        patcher.patch(rom_path=rom, output_path=out, rosters=wrong)


def test_patch_returns_a_patch_result(patcher, rom, out):
    result = patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25)))
    assert type(result) is PatchResult


def test_patch_reports_the_output_path(patcher, rom, out):
    result = patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25)))
    assert result.output_path == str(out)


def test_patch_counts_the_slots_it_reached(patcher, rom, out):
    result = patcher.patch(
        rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25), (CWS_SLOT, 25))
    )
    assert result.teams_patched == 2


def test_patch_counts_the_records_that_reached_the_image(patcher, rom, out):
    result = patcher.patch(
        rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25), (CWS_SLOT, 12))
    )
    assert result.players_patched == 37


def test_patch_writes_the_output_file(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25)))
    assert out.exists() is True


def test_the_output_is_the_same_size_as_the_input(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25)))
    assert len(out.read_bytes()) == fixture.ROM_SIZE


def test_a_patched_slot_reads_back_the_names_that_were_written(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25)))
    names, _ = _read_back(out, SEA_SLOT)
    assert names[0] == "W. WRITE00"


def test_every_slot_of_a_patched_team_is_written(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25)))
    names, _ = _read_back(out, SEA_SLOT)
    assert names[-1] == "W. WRITE24"


def test_an_unpatched_slot_keeps_the_records_it_arrived_with(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25)))
    names, _ = _read_back(out, 0)
    expected = f"{fixture.player_first_initial(0, 0)}. {fixture.player_last_name(0, 0)}"
    assert names[0] == expected


def test_a_short_roster_leaves_the_slots_after_it_alone(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 3)))
    names, _ = _read_back(out, SEA_SLOT)
    initial = fixture.player_first_initial(SEA_SLOT, 4)
    expected = f"{initial}. {fixture.player_last_name(SEA_SLOT, 4)}"
    assert names[4] == expected


def test_patch_recomputes_the_snes_checksum(patcher, rom, out):
    # Upstream's orchestrator called `update_snes_checksum` explicitly, and so
    # does this one; the NBA Live 95 port does its equivalent inside `finalize`
    # instead. Both are deliberate.
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25)))
    written = out.read_bytes()
    checksum = written[fixture.CHECKSUM_OFFSET] | (written[fixture.CHECKSUM_OFFSET + 1] << 8)
    scratch = bytearray(written)
    scratch[fixture.CHECKSUM_OFFSET : fixture.CHECKSUM_OFFSET + 2] = b"\x00\x00"
    scratch[fixture.COMPLEMENT_OFFSET : fixture.COMPLEMENT_OFFSET + 2] = b"\xff\xff"
    assert checksum == sum(scratch) & 0xFFFF


def test_the_recomputed_checksum_is_not_the_one_the_image_arrived_with(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25)))
    written = out.read_bytes()
    before = rom.read_bytes()
    assert (
        written[fixture.CHECKSUM_OFFSET : fixture.CHECKSUM_OFFSET + 2]
        != before[fixture.CHECKSUM_OFFSET : fixture.CHECKSUM_OFFSET + 2]
    )


def test_patching_a_headered_image_shifts_the_checksum_and_leaves_the_header_out(
    patcher, tmp_path, out
):
    source = fixture.write_kgj_rom(tmp_path / "kgj.smc", with_header=True)
    patcher.patch(rom_path=source, output_path=out, rosters=_mapped((SEA_SLOT, 25)))
    written = out.read_bytes()
    at = fixture.CHECKSUM_OFFSET + fixture.SMC_HEADER_SIZE
    checksum = written[at] | (written[at + 1] << 8)
    scratch = bytearray(written)
    scratch[at : at + 2] = b"\x00\x00"
    comp = fixture.COMPLEMENT_OFFSET + fixture.SMC_HEADER_SIZE
    scratch[comp : comp + 2] = b"\xff\xff"
    assert checksum == sum(scratch[fixture.SMC_HEADER_SIZE :]) & 0xFFFF


def test_an_empty_roster_is_not_written_and_not_counted(patcher, rom, out):
    result = patcher.patch(
        rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25), (CWS_SLOT, 0))
    )
    assert result.teams_patched == 1


def test_a_negative_slot_index_is_dropped(patcher, rom, out):
    # These keys come from a plain dict that may have crossed a JSON boundary.
    # `write_team_roster` guards only the upper end, so a negative key would
    # compute an offset below the marker and overwrite whatever is there.
    rosters = _mapped((SEA_SLOT, 25))
    rosters.teams[-3] = KGJTeamRecord(index=-3, name="Bogus", players=_records(25))
    result = patcher.patch(rom_path=rom, output_path=out, rosters=rosters)
    assert result.teams_patched == 1


def test_a_negative_slot_index_does_not_reach_the_image(patcher, rom, out):
    rosters = _mapped((SEA_SLOT, 25))
    rosters.teams[-3] = KGJTeamRecord(index=-3, name="Bogus", players=_records(25))
    patcher.patch(rom_path=rom, output_path=out, rosters=rosters)
    below = fixture.MARKER_OFFSET - 3 * 0x320
    assert out.read_bytes()[below : below + 32] == rom.read_bytes()[below : below + 32]


def test_a_slot_index_past_the_league_is_dropped(patcher, rom, out):
    rosters = _mapped((SEA_SLOT, 25))
    rosters.teams[TEAM_COUNT] = KGJTeamRecord(index=TEAM_COUNT, name="Bogus", players=_records(25))
    result = patcher.patch(rom_path=rom, output_path=out, rosters=rosters)
    assert result.teams_patched == 1


def test_patch_raises_for_an_image_with_no_marker(patcher, tmp_path, out):
    path = fixture.write_kgj_rom(tmp_path / "nomarker.sfc", place_marker=False)
    with pytest.raises(RomError):
        patcher.patch(rom_path=path, output_path=out, rosters=_mapped((SEA_SLOT, 25)))


def test_patch_raises_for_a_file_that_is_not_there(patcher, tmp_path, out):
    with pytest.raises(RomError):
        patcher.patch(
            rom_path=tmp_path / "absent.sfc", output_path=out, rosters=_mapped((SEA_SLOT, 25))
        )


def test_patch_raises_when_the_marker_leaves_no_room(patcher, tmp_path, out):
    # DELIBERATE DIVERGENCE: upstream wrote an unmodified copy of the image and
    # returned success with zero teams patched.
    path = fixture.write_kgj_rom(tmp_path / "nofit.sfc", marker_offset=fixture.ROM_SIZE - 20)
    with pytest.raises(RomError):
        patcher.patch(rom_path=path, output_path=out, rosters=_mapped((SEA_SLOT, 25)))


def test_the_no_room_error_says_how_far_the_tables_run(patcher, tmp_path, out):
    path = fixture.write_kgj_rom(tmp_path / "nofit.sfc", marker_offset=fixture.ROM_SIZE - 20)
    with pytest.raises(RomError, match=str(TEAM_DATA_SPAN)):
        patcher.patch(rom_path=path, output_path=out, rosters=_mapped((SEA_SLOT, 25)))


def test_patch_writes_nothing_when_it_raises(patcher, tmp_path, out):
    path = fixture.write_kgj_rom(tmp_path / "nofit.sfc", marker_offset=fixture.ROM_SIZE - 20)
    with pytest.raises(RomError):
        patcher.patch(rom_path=path, output_path=out, rosters=_mapped((SEA_SLOT, 25)))
    assert out.exists() is False


def test_patch_raises_when_the_output_cannot_be_written(patcher, rom, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a directory")
    with pytest.raises(RomError):
        patcher.patch(
            rom_path=rom, output_path=blocker / "out.sfc", rosters=_mapped((SEA_SLOT, 25))
        )


def test_patch_reports_progress_once_per_target_and_once_at_the_end(patcher, rom, out):
    seen = []
    patcher.patch(
        rom_path=rom,
        output_path=out,
        rosters=_mapped((SEA_SLOT, 25), (CWS_SLOT, 25)),
        on_progress=lambda f, m: seen.append(f),
    )
    assert seen == [0.0, 0.5, 1.0]


@pytest.mark.parametrize("pillow", [True, False])
def test_patch_narrates_its_three_steps(tmp_path, rom, out, monkeypatch, pillow):
    """The same lines with or without Pillow: with no logo or head shot to
    draw there is nothing to skip."""
    if not pillow:
        monkeypatch.setitem(sys.modules, "PIL", None)
    messages = []
    p = KGJMLBPatcher(cache_dir=tmp_path / "cache", on_status=messages.append)
    p.api = FakeApi(_teams())
    p.patch(rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25)))
    assert messages == ["Validating ROM...", "Initializing ROM writer...", "Saving patched ROM..."]


def test_patch_does_not_mutate_the_rosters_it_was_given(patcher, rom, out, tmp_path):
    # A second patch from the same `MappedRosters` matches the first.
    rosters = _mapped((SEA_SLOT, 25))
    patcher.patch(rom_path=rom, output_path=out, rosters=rosters)
    again = tmp_path / "again.sfc"
    patcher.patch(rom_path=rom, output_path=again, rosters=rosters)
    assert again.read_bytes() == out.read_bytes()


def test_analyze_reports_a_readable_non_kgj_image_rather_than_raising(patcher, tmp_path):
    # `cmd_analyze` probes every registered patcher against one ROM and catches
    # `RomError` per patcher, so "not this game" must not be an exception --
    # otherwise a user's whole library reports errors instead of a clean miss.
    path = fixture.write_kgj_rom(tmp_path / "nomarker.sfc", place_marker=False)
    assert patcher.analyze_rom(path).is_valid is False


def test_patch_raises_on_the_very_image_analyze_merely_reported(patcher, tmp_path, out):
    # The asymmetry, pinned as a pair: the same file, two different contracts.
    # `analyze` costs the user nothing by declining, and `patch` must not write
    # a copy of an image it cannot modify.
    path = fixture.write_kgj_rom(tmp_path / "nomarker.sfc", place_marker=False)
    with pytest.raises(RomError):
        patcher.patch(rom_path=path, output_path=out, rosters=_mapped((SEA_SLOT, 25)))


def test_analyze_declines_and_patch_raises_on_a_marker_that_leaves_no_room(patcher, tmp_path, out):
    path = fixture.write_kgj_rom(tmp_path / "nofit.sfc", marker_offset=fixture.ROM_SIZE - 20)
    assert patcher.analyze_rom(path).is_valid is False
    with pytest.raises(RomError):
        patcher.patch(rom_path=path, output_path=out, rosters=_mapped((SEA_SLOT, 25)))


def test_fetch_map_and_patch_run_end_to_end(patcher, rom, out):
    data = patcher.fetch(season=2025)
    mapped = patcher.map_rosters(data)
    result = patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    assert result.teams_patched == 2


def test_the_pipeline_writes_the_fetched_names_into_the_image(patcher, rom, out):
    data = patcher.fetch(season=2025)
    mapped = patcher.map_rosters(data)
    patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    names, _ = _read_back(out, SEA_SLOT)
    # "First0 Last00" -> initial F, last name LAST00.
    assert names[0] == "F. LAST00"


def test_the_pipeline_leaves_an_unmatched_slot_alone(patcher, rom, out):
    data = patcher.fetch(season=2025)
    patcher.patch(rom_path=rom, output_path=out, rosters=patcher.map_rosters(data))
    names, _ = _read_back(out, 0)
    expected = f"{fixture.player_first_initial(0, 0)}. {fixture.player_last_name(0, 0)}"
    assert names[0] == expected


def test_the_pipeline_survives_a_json_round_trip_of_the_league_data(patcher, rom, out):
    from retro_roster_patcher.sports.serde import league_data_from_dict, league_data_to_dict

    data = patcher.fetch(season=2025)
    reloaded = league_data_from_dict(league_data_to_dict(data))
    mapped = patcher.map_rosters(reloaded)
    result = patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    assert result.players_patched == 50


class _StubMapper:
    """A mapper that answers one fixed slot, whatever team it is handed.

    `MODERN_MLB_TO_KGJ` holds no negative and no out-of-range value, so the two
    ends of `map_rosters`' bound cannot be reached through the real mapper. They
    are still a guard worth having -- `LeagueData` can arrive from a file -- and
    this is how a test reaches them.
    """

    def __init__(self, slot):
        self._slot = slot
        self.real = KGJStatMapper()

    def get_team_slot(self, code):
        return self._slot

    def select_roster(self, players, stats=None):
        return self.real.select_roster(players, stats)

    def select_roster_groups(self, players, stats=None, lineup=None, lineup_size=9):
        return self.real.select_roster_groups(players, stats, lineup, lineup_size)

    def is_pitcher(self, player):
        return self.real.is_pitcher(player)

    def map_batter(self, player, stats=None):
        return self.real.map_batter(player, stats)

    def map_pitcher(self, player, stats=None, is_starter=True):
        return self.real.map_pitcher(player, stats, is_starter=is_starter)


def test_map_rosters_drops_a_negative_slot(patcher):
    patcher.mapper = _StubMapper(-1)
    assert patcher.map_rosters(_league_data(("SEA", 25))).teams == {}


def test_map_rosters_drops_a_slot_past_the_league(patcher):
    patcher.mapper = _StubMapper(expansion.TEAMS)
    assert patcher.map_rosters(_league_data(("SEA", 25))).teams == {}


def test_map_rosters_keeps_the_last_slot_in_the_league(patcher):
    # The upper bound is exclusive, so slot 29 (Tampa Bay's) is in and 30 is
    # out. Without this the previous test also passes against `slot > 30`.
    patcher.mapper = _StubMapper(expansion.TEAMS - 1)
    assert list(patcher.map_rosters(_league_data(("SEA", 25))).teams) == [expansion.TEAMS - 1]


def test_map_rosters_keeps_slot_zero(patcher):
    patcher.mapper = _StubMapper(0)
    assert list(patcher.map_rosters(_league_data(("SEA", 25))).teams) == [0]


def test_pitchers_start_at_slot_fifteen_on_a_short_roster(patcher):
    """Twelve batters: the rotation still opens at slot 15.

    Replaces the pins of upstream's slot-index layout, which wrote the first
    three starting pitchers into batter slots 12-14 with the batter nibble over
    a pitcher-shaped record, pushed three relievers into the rotation, and left
    the last three bullpen slots to the cartridge.
    """
    players = patcher.map_rosters(_uneven_league(12, 5, 5)).teams[SEA_SLOT].players
    # Bat.., Sta.. and Rel.. players, by first initial.
    kinds = [r.first_initial if r else None for r in players[11:]]
    assert kinds == ["B"] + [None] * 3 + ["S"] * 5 + ["R"] * 5


def test_a_batter_slot_nobody_is_left_for_keeps_the_cartridges_record(patcher, rom, out):
    patcher.patch(
        rom_path=rom, output_path=out, rosters=patcher.map_rosters(_uneven_league(12, 5, 5))
    )
    names, _ = _read_back(out, SEA_SLOT)
    initial = fixture.player_first_initial(SEA_SLOT, 12)
    assert names[12] == f"{initial}. {fixture.player_last_name(SEA_SLOT, 12)}"


def test_the_rotation_is_rated_as_starters_on_a_short_roster(patcher):
    # A stat-less starter's fatigue default is 7 and a reliever's 3.
    players = patcher.map_rosters(_uneven_league(12, 5, 5)).teams[SEA_SLOT].players
    assert [r.pitcher_attrs.fatigue for r in players[15:25]] == [7] * 5 + [3] * 5


def test_a_promoted_reliever_is_rated_as_a_starter_too(patcher):
    promoted = patcher.map_rosters(_uneven_league(15, 2, 12)).teams[SEA_SLOT].players[19]
    starter = patcher.map_rosters(_uneven_league(15, 5, 5)).teams[SEA_SLOT].players[19]
    assert promoted.pitcher_attrs.fatigue == starter.pitcher_attrs.fatigue


def test_the_first_pitcher_is_written_pitcher_shaped_at_slot_fifteen(patcher, rom, out):
    mapped = patcher.map_rosters(_uneven_league(12, 5, 5))
    patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    offset = fixture.player_offset(SEA_SLOT, 15, first_team_offset=FIRST_TEAM_OFFSET)
    record = fixture.decode_player_record(out.read_bytes(), offset)
    # Pitcher-shaped: FAT fills byte 0x0C, ERA's digit nibble is 2.
    assert [record["raw"][0x0C] >> 4, record["kind_flag"] >> 4] == [0, 2]


def _ranked_league(players):
    """One SEA roster whose stats rank every player by id, highest first."""
    leaders = {str(p.id): {"GS": p.id, "OPS": p.id, "W": p.id, "SV": p.id} for p in players}
    return LeagueData(
        league=League(id=0, name="MLB", country="USA", country_code="US", season=2025),
        teams=[
            TeamRoster(
                team=Team(id=1, name="SEA", code="SEA"),
                players=players,
                extra={"stats": leaders},
            )
        ],
    )


def _ranked_squad():
    """Eighteen batters, six starters and six relievers; jersey = id + 1."""
    positions = ["CF"] * 18 + ["SP"] * 6 + ["RP"] * 6
    return [
        Player(id=n, name=f"First{n:02d} Last{n:02d}", position=pos, number=n + 1)
        for n, pos in enumerate(positions)
    ]


def test_order_as_given_keeps_the_blocks_and_the_callers_order_inside_them(patcher):
    squad = _ranked_squad()
    given = squad[::2] + squad[1::2]

    mapped = patcher.map_rosters(_ranked_league(given), order_as_given=True)

    batters = [p for p in given if p.position == "CF"][:BATTERS_PER_TEAM]
    pitchers = [p for p in given if p.position != "CF"][: PLAYERS_PER_TEAM - BATTERS_PER_TEAM]
    expected = [p.number for p in batters + pitchers]
    assert [r.jersey_number for r in mapped.teams[SEA_SLOT].players] == expected


def test_without_order_as_given_the_input_order_is_ignored(patcher):
    squad = _ranked_squad()
    forward = patcher.map_rosters(_ranked_league(squad)).teams[SEA_SLOT].players
    backward = patcher.map_rosters(_ranked_league(squad[::-1])).teams[SEA_SLOT].players
    assert [r.jersey_number for r in forward] == [r.jersey_number for r in backward]


def _squad_league(positions, code="SEA"):
    """One club whose players have these positions, in this order, and no stats."""
    players = [
        Player(id=n, name=f"First{n:02d} Last{n:02d}", position=pos, number=n + 1)
        for n, pos in enumerate(positions)
    ]
    return LeagueData(
        league=League(id=0, name="MLB", country="USA", country_code="US", season=2025),
        teams=[
            TeamRoster(team=Team(id=1, name=code, code=code), players=players, extra={"stats": {}})
        ],
    )


AL_LINEUP = ["C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH"]
STAFF = ["SP"] * 5 + ["RP"] * 5


def test_fetch_publishes_how_many_batters_each_club_starts(tmp_path):
    # Slots 0-13 are the AL, with a DH; 14-27 the NL, without.
    p = KGJMLBPatcher(cache_dir=tmp_path / "cache")
    p.api = FakeApi(
        [Team(id=1, name="Seattle Mariners", code="SEA"), Team(id=2, name="Braves", code="ATL")]
    )
    sizes = {r.team.code: r.extra["lineup_size"] for r in p.fetch(season=2025).teams}
    assert sizes == {"SEA": 9, "ATL": 8}


def test_a_bench_outfielder_swapped_in_for_the_catcher_catches(patcher):
    # The editor swapped the catcher (now 10th) with a bench outfielder (now 1st).
    given = ["OF", *AL_LINEUP[1:], "C", "C", "IF", *STAFF]
    players = patcher.map_rosters(_squad_league(given), order_as_given=True).teams[SEA_SLOT].players
    assert [r.position for r in players[:10]] == [*AL_LINEUP, "C"]


def test_an_outfielder_takes_the_outfield_gap_and_a_spare_catcher_the_rest(patcher):
    given = ["C", "1B", "2B", "3B", "SS", "OF", "CF", "RF", "C", *STAFF]
    players = patcher.map_rosters(_squad_league(given), order_as_given=True).teams[SEA_SLOT].players
    assert [players[5].position, players[8].position] == ["LF", "DH"]


def test_the_default_lineup_fields_every_position_without_a_listed_dh(patcher):
    # No DH and no LF listed: the first nine were C,1B,2B,3B,SS,CF,RF,OF,C.
    squad = ["C", "1B", "2B", "3B", "SS", "CF", "RF", "OF", "C", "OF", "IF", *STAFF]
    players = patcher.map_rosters(_squad_league(squad)).teams[SEA_SLOT].players
    assert sorted(r.position for r in players[:9]) == sorted(AL_LINEUP)


def test_an_nl_lineup_is_eight_without_a_dh(patcher):
    # The same order would be a full AL lineup; in the NL the DH bats nowhere.
    given = ["DH", *AL_LINEUP[1:8], "C", *STAFF]
    players = patcher.map_rosters(_squad_league(given, code="ATL"), order_as_given=True).teams[14]
    assert [r.position for r in players.players[:9]] == [*AL_LINEUP[:8], "C"]


@pytest.mark.parametrize("order_as_given", [False, True])
def test_spare_pitchers_fill_a_short_batter_block_as_batters(patcher, order_as_given):
    data = _uneven_league(13, 5, 8)
    players = patcher.map_rosters(data, order_as_given=order_as_given).teams[SEA_SLOT].players
    assert [r.first_initial for r in players[13:16]] == ["R", "R", "S"]
    assert [r.is_pitcher for r in players] == [False] * 15 + [True] * 10


@pytest.mark.parametrize("order_as_given", [False, True])
def test_spare_batters_fill_a_short_bullpen_so_no_1994_pitcher_stays(patcher, order_as_given):
    data = _uneven_league(17, 5, 3)
    players = patcher.map_rosters(data, order_as_given=order_as_given).teams[SEA_SLOT].players
    assert [(r.first_initial, r.is_pitcher) for r in players[23:]] == [("B", True)] * 2


def _season_league(code="SEA"):
    """A club with stat lines and a recent lineup that disagrees with position order."""
    positions = [*AL_LINEUP, "C", "1B", "OF", "IF", "SS", "LF", "CF", *STAFF, "SP", "RP"]
    players = [
        Player(id=n, name=f"First{n:02d} Last{n:02d}", position=pos, number=n + 1)
        for n, pos in enumerate(positions)
    ]
    stats = {
        str(p.id): {"GS": 100 - p.id, "PA": 500, "AVG": 0.250, "SLG": 0.400, "HR": p.id, "W": p.id}
        for p in players
    }
    # The DH leads off and the catcher bats ninth.
    lineup = [{"id": n, "order": 9.0 - n} for n in range(9)]
    roster = TeamRoster(
        team=Team(id=1, name=code, code=code),
        players=players,
        extra={"stats": stats, "lineup": lineup},
    )
    return LeagueData(
        league=League(id=0, name="MLB", country="USA", country_code="US", season=2025),
        teams=[roster],
    )


@pytest.mark.parametrize("code,slot", [("SEA", SEA_SLOT), ("ATL", 14)])
def test_the_editors_order_and_the_default_mapping_agree(patcher, code, slot):
    # The app shows `suggest_squad_order` as the batting order and patches it
    # with `order_as_given`; without an edit that must be what `map_rosters`
    # would write on its own.
    data = _season_league(code)
    default = patcher.map_rosters(data).teams[slot].players
    data.teams[0].players = patcher.suggest_squad_order(data.teams[0])
    edited = patcher.map_rosters(data, order_as_given=True).teams[slot].players
    assert edited == default


def test_a_rosters_file_from_before_full_stat_lines_still_maps_its_leaders(patcher):
    # Older fetches kept the season lines under `leaders`.
    data = _season_league()
    expected = patcher.map_rosters(data).teams[SEA_SLOT].players
    data.teams[0].extra["leaders"] = data.teams[0].extra.pop("stats")
    players = patcher.map_rosters(data).teams[SEA_SLOT].players
    assert players == expected
    assert any(r.home_runs for r in players if r)


def test_the_default_batting_order_follows_the_clubs_lineup(patcher):
    players = patcher.map_rosters(_season_league()).teams[SEA_SLOT].players
    assert [r.position for r in players[:9]] == AL_LINEUP[::-1]


def test_an_nl_club_sits_its_dh_and_bats_eight(patcher):
    players = patcher.map_rosters(_season_league("ATL")).teams[14].players
    assert [r.position for r in players[:8]] == AL_LINEUP[7::-1]


def test_the_derby_takes_each_leagues_home_run_leaders(patcher, rom, out):
    mapped = patcher.map_rosters(_league_data(("SEA", 25), ("ATL", 25)))
    for team in mapped.teams.values():
        for n, record in enumerate(team.players[:BATTERS_PER_TEAM]):
            record.home_runs = n
            record.last_name = f"{team.name[:2].upper()}{n:02d}"
    patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    written = out.read_bytes()
    names = [
        fixture.decode_player_record(written, fixture.derby_offset(i))["last_name"]
        for i in range(6)
    ]
    assert names == ["AT14", "AT13", "AT12", "SE14", "SE13", "SE12"]


def test_a_league_with_no_patched_team_keeps_its_derby_batters(patcher, rom, out):
    before = rom.read_bytes()[fixture.derby_offset(0) : fixture.derby_offset(3)]
    patcher.patch(
        rom_path=rom, output_path=out, rosters=patcher.map_rosters(_league_data(("SEA", 25)))
    )
    assert out.read_bytes()[fixture.derby_offset(0) : fixture.derby_offset(3)] == before


def test_of_two_starters_listed_at_one_position_the_regular_keeps_it(patcher):
    # Both list 3B; the one batting third started 10 games, the one batting
    # ninth 150. Without stats the later one would move.
    given = ["C", "1B", "3B", "SS", "LF", "CF", "RF", "2B", "3B", *STAFF]
    data = _squad_league(given)
    data.teams[0].extra["stats"] = {"2": {"GS": 10}, "8": {"GS": 150}}
    players = patcher.map_rosters(data, order_as_given=True).teams[SEA_SLOT].players
    assert [players[2].position, players[8].position] == ["DH", "3B"]


# -- looks ----------------------------------------------------------------------------


def _look_bytes(image, slot, index):
    off = fixture.player_offset(slot, index, first_team_offset=FIRST_TEAM_OFFSET)
    return bytes(image[off + 0x0E : off + 0x18])


def _photo_league():
    """Seattle's 25, each with a head shot URL."""
    data = _league_data(("SEA", 25))
    for player in data.teams[0].players:
        player.photo_url = f"https://example.test/{player.id}.png"
    return data


def _face(rgb):
    from PIL import Image, ImageDraw

    picture = Image.new("RGBA", (600, 436), (0, 0, 0, 0))
    draw = ImageDraw.Draw(picture)
    draw.ellipse((220, 110, 380, 300), fill=(*rgb, 255))
    draw.rectangle((215, 50, 385, 150), fill=(20, 30, 70, 255))
    draw.rectangle((60, 330, 540, 436), fill=(250, 250, 250, 255))
    out = io.BytesIO()
    picture.save(out, "PNG")
    return out.getvalue()


def test_map_rosters_gives_no_two_batters_or_pitchers_the_same_card(patcher):
    team = patcher.map_rosters(_league_data(("SEA", 25))).teams[SEA_SLOT]
    cards = [looks._card(r) for r in team.players]
    assert len(set(cards[:BATTERS_PER_TEAM])) == BATTERS_PER_TEAM
    assert len(set(cards[BATTERS_PER_TEAM:])) > 1


def test_patch_writes_each_players_own_look(patcher, rom, out):
    mapped = patcher.map_rosters(_league_data(("SEA", 25)))
    patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    written = out.read_bytes()
    for index, record in enumerate(mapped.teams[SEA_SLOT].players):
        bat = record.batter_appearance
        assert _look_bytes(written, SEA_SLOT, index)[0] == bat.skin << 4 | bat.head
    assert len({_look_bytes(written, SEA_SLOT, i)[:7] for i in range(BATTERS_PER_TEAM)}) > 10


def test_patch_takes_each_players_skin_from_his_head_shot(tmp_path, rom, out):
    pytest.importorskip("PIL")
    light, dark = _face((232, 190, 160)), _face((95, 60, 40))
    asked = []

    def transport(url, headers, timeout):
        asked.append(url)
        return dark if int(url.rsplit("/", 1)[1].split(".")[0]) % 2 else light

    messages = []
    p = KGJMLBPatcher(cache_dir=tmp_path / "cache", transport=transport, on_status=messages.append)
    mapped = p.map_rosters(_photo_league())
    before = copy.deepcopy(mapped)
    p.patch(rom_path=rom, output_path=out, rosters=mapped)

    written = out.read_bytes()
    for index, record in enumerate(mapped.teams[SEA_SLOT].players):
        tone = 5 if record.player_id % 2 else 0
        looks_ = _look_bytes(written, SEA_SLOT, index)
        assert looks_[0] >> 4 == tone
        if record.is_pitcher:
            assert looks_[7] & 0x0F == tone
    assert len(asked) == 25
    assert "Skin tones from head shots: 25 of 25 players" in messages
    assert mapped == before  # the caller's rosters keep the looks they were mapped with


def test_a_player_whose_head_shot_will_not_load_keeps_his_mapped_look(tmp_path, rom, out):
    def transport(url, headers, timeout):
        raise OSError("offline")

    p = KGJMLBPatcher(cache_dir=tmp_path / "cache", transport=transport)
    mapped = p.map_rosters(_photo_league())
    p.patch(rom_path=rom, output_path=out, rosters=mapped)
    first = mapped.teams[SEA_SLOT].players[0].batter_appearance
    assert _look_bytes(out.read_bytes(), SEA_SLOT, 0)[0] == first.skin << 4 | first.head


def test_without_pillow_the_looks_come_from_the_id_and_a_status_says_so(
    monkeypatch, tmp_path, rom, out
):
    monkeypatch.setitem(sys.modules, "PIL", None)
    messages = []

    def transport(url, headers, timeout):
        raise AssertionError("nothing to fetch without Pillow")

    p = KGJMLBPatcher(cache_dir=tmp_path / "cache", transport=transport, on_status=messages.append)
    mapped = p.map_rosters(_photo_league())
    p.patch(rom_path=rom, output_path=out, rosters=mapped)
    assert "Pillow is not installed; player skin tones not taken from head shots" in messages
    first = mapped.teams[SEA_SLOT].players[0].batter_appearance
    assert _look_bytes(out.read_bytes(), SEA_SLOT, 0)[0] == first.skin << 4 | first.head


def test_looks_false_keeps_the_cartridges_looks(patcher, rom, out):
    patcher.patch(
        rom_path=rom,
        output_path=out,
        rosters=patcher.map_rosters(_league_data(("SEA", 25))),
        looks=False,
    )
    source, written = rom.read_bytes(), out.read_bytes()
    for index in range(PLAYERS_PER_TEAM):
        was, now = _look_bytes(source, SEA_SLOT, index), _look_bytes(written, SEA_SLOT, index)
        assert now[:7] + now[8:] == was[:7] + was[8:]
        assert now[7] & 0x0F == was[7] & 0x0F


#: $85:D1B4 as the retail cartridge has it, at its retail file offset.
GRIFFEY_RULE = bytes.fromhex("ae4618bdd012c93a00f005c93200d016adf512c91800d00e")
GRIFFEY_OFFSET = 0x2D1B4
GRIFFEY_JERSEY = GRIFFEY_OFFSET + GRIFFEY_RULE.index(b"\xc9\x18") + 1


@pytest.fixture
def griffey_rom(rom):
    image = bytearray(rom.read_bytes())
    image[GRIFFEY_OFFSET : GRIFFEY_OFFSET + len(GRIFFEY_RULE)] = GRIFFEY_RULE
    rom.write_bytes(image)
    return rom


@pytest.mark.parametrize(
    ("codes", "options", "jersey"),
    [
        (("SEA",), {}, 0xFF),
        (("CWS",), {}, 0x18),
        (("SEA",), {"looks": False}, 0x18),
    ],
)
def test_a_patched_seattle_frees_number_24_from_griffeys_head(
    patcher, griffey_rom, out, codes, options, jersey
):
    rosters = patcher.map_rosters(_league_data(*((code, 25) for code in codes)))
    patcher.patch(rom_path=griffey_rom, output_path=out, rosters=rosters, **options)
    assert out.read_bytes()[GRIFFEY_JERSEY] == jersey
